import asyncio
import logging
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .jobs import JobStore
from .pipeline import Providers, ShortsPipeline
from .schemas import CreateJobResponse, JobStatusResponse, ScriptRequest
from .services import video

logger = logging.getLogger(__name__)


def build_default_providers(settings: Settings) -> Providers:
    from google import genai

    from .services.images import ImagenGenerator
    from .services.planner import GeminiPlanner
    from .services.tts import EdgeTTS

    # api_key가 None이면 SDK가 GEMINI_API_KEY / GOOGLE_API_KEY 환경변수를 사용합니다.
    client = genai.Client(api_key=settings.gemini_api_key)
    return Providers(
        planner=GeminiPlanner(client, settings),
        tts=EdgeTTS(settings),
        images=ImagenGenerator(client, settings),
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
        title="AI 쇼폼 비디오",
        version="2.0.0",
        description="대본 한 편으로 자막·나레이션·이미지가 들어간 9:16 쇼츠 영상을 생성합니다.",
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=settings.output_dir), name="static")

    @app.get("/health", tags=["system"])
    async def health(request: Request) -> dict:
        return {"status": "ok", "jobs": len(request.app.state.jobs)}

    @app.post(
        "/api/v1/shorts/generate",
        response_model=CreateJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["shorts"],
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
        "/api/v1/shorts/status/{job_id}", response_model=JobStatusResponse, tags=["shorts"]
    )
    async def get_job_status(job_id: str, request: Request) -> JobStatusResponse:
        job = request.app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
        return job.to_response()

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
