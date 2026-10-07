import asyncio
import logging
import secrets
import shutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse

from .auth import require_api_key
from .config import Settings, get_settings
from .jobs import JobStore
from .pipeline import Providers, ShortsPipeline
from .schemas import CreateJobResponse, JobStatusResponse, ScriptRequest
from .services import video
from .signing import VIDEO_ROUTE_PREFIX, URLSigner

logger = logging.getLogger(__name__)

WEB_INDEX = Path(__file__).parent / "web" / "index.html"


def build_default_providers(settings: Settings) -> Providers:
    from google import genai

    from .services.images import GeminiImageGenerator
    from .services.planner import GeminiPlanner
    from .services.tts import EdgeTTS

    # api_key가 None이면 SDK가 GEMINI_API_KEY / GOOGLE_API_KEY 환경변수를 사용합니다.
    client = genai.Client(api_key=settings.gemini_api_key)
    return Providers(
        planner=GeminiPlanner(client, settings),
        tts=EdgeTTS(settings),
        images=GeminiImageGenerator(client, settings),
    )


def create_app(settings: Settings | None = None, providers: Providers | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.output_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        video.ensure_ffmpeg()
        spec = video.VideoSpec(
            width=settings.video_width,
            height=settings.video_height,
            fps=settings.video_fps,
            max_zoom=settings.max_zoom,
            padding_sec=settings.scene_padding_sec,
            font_path=video.find_korean_font(settings.font_path),
            font_size=settings.subtitle_font_size,
            timeout_sec=settings.ffmpeg_timeout_sec,
        )
        logger.info("자막 폰트: %s", spec.font_path)

        app.state.api_keys = settings.api_key_list
        if not app.state.api_keys:
            logger.warning("API_KEYS가 비어 있어 인증 없이 실행합니다. 외부에 공개하지 마세요.")

        if settings.url_signing_secret:
            secret = settings.url_signing_secret.encode()
        else:
            secret = secrets.token_bytes(32)
            logger.info("URL_SIGNING_SECRET이 없어 임시 비밀키를 만들었습니다. 재시작하면 기존 영상 링크는 무효가 됩니다.")
        app.state.signer = URLSigner(secret, settings.video_url_ttl_sec)

        app.state.jobs = JobStore()
        app.state.pipeline = ShortsPipeline(
            providers or build_default_providers(settings), settings, spec
        )
        app.state.tasks = set()
        cleaner = asyncio.create_task(_cleanup_loop(app, settings))
        try:
            yield
        finally:
            cleaner.cancel()
            for task in list(app.state.tasks):
                task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(cleaner, *app.state.tasks, return_exceptions=True)

    app = FastAPI(
        title="ai-shortform-video",
        version="2.0.0",
        description="대본 한 편으로 자막·나레이션·이미지가 들어간 9:16 쇼츠 영상을 생성합니다.",
        lifespan=lifespan,
    )

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_INDEX)

    @app.get("/health", tags=["system"])
    async def health(request: Request) -> dict:
        # 웹 화면이 키 입력란을 띄울지 판단하는 데 씁니다. 작업 수 같은 내부 정보는 노출하지 않습니다.
        return {"status": "ok", "auth_required": bool(request.app.state.api_keys)}

    @app.post(
        "/api/v1/shorts/generate",
        response_model=CreateJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["shorts"],
        dependencies=[Depends(require_api_key)],
    )
    async def create_shorts_job(req: ScriptRequest, request: Request) -> CreateJobResponse:
        state = request.app.state
        job = state.jobs.create(req.script, req.tone)
        # BackgroundTasks 대신 직접 태스크를 관리해 종료 시 깔끔하게 취소합니다.
        task = asyncio.create_task(state.pipeline.run(job), name=f"shorts-{job.job_id}")
        state.tasks.add(task)
        task.add_done_callback(state.tasks.discard)
        return CreateJobResponse(
            job_id=job.job_id,
            status=job.status,
            status_url=str(request.url_for("get_job_status", job_id=job.job_id).path),
            message="작업이 대기열에 등록되었습니다.",
        )

    @app.get(
        "/api/v1/shorts/status/{job_id}",
        response_model=JobStatusResponse,
        tags=["shorts"],
        dependencies=[Depends(require_api_key)],
    )
    async def get_job_status(job_id: str, request: Request) -> JobStatusResponse:
        job = request.app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        return job.to_response(request.app.state.signer)

    @app.get(f"{VIDEO_ROUTE_PREFIX}/{{job_id}}/{{filename}}", include_in_schema=False)
    async def get_video(
        job_id: str,
        filename: str,
        request: Request,
        expires: int | None = None,
        sig: str | None = None,
    ) -> FileResponse:
        # API 키 대신 서명으로 접근을 허용합니다 (<video> 태그는 헤더를 붙일 수 없음).
        path = f"{VIDEO_ROUTE_PREFIX}/{job_id}/{filename}"
        if expires is None or not sig or not request.app.state.signer.verify(path, expires, sig):
            raise HTTPException(status_code=403, detail="링크가 만료되었거나 올바르지 않습니다.")
        job = request.app.state.jobs.get(job_id)
        # 파일 경로는 요청 값이 아니라 작업에 기록된 이름으로 만들어 경로 조작을 막습니다.
        if job is None or job.video_file != filename:
            raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
        file_path = settings.output_dir / job.job_id / job.video_file
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
        max_age = max(0, expires - int(time.time()))
        return FileResponse(
            file_path,
            media_type="video/mp4",
            headers={"Cache-Control": f"private, max-age={max_age}", "Referrer-Policy": "no-referrer"},
        )

    return app


async def _cleanup_loop(app: FastAPI, settings: Settings, interval_sec: float = 600) -> None:
    """보관 기간이 지난 작업과 출력 파일을 주기적으로 삭제합니다."""
    ttl = timedelta(hours=settings.job_ttl_hours)
    while True:
        await asyncio.sleep(interval_sec)
        for job in app.state.jobs.pop_expired(ttl):
            await asyncio.to_thread(shutil.rmtree, settings.output_dir / job.job_id, True)
            logger.info("만료된 작업 삭제: %s", job.job_id)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
