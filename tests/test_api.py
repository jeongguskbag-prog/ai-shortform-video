import asyncio

import httpx
import pytest

from app.main import create_app
from app.pipeline import Providers

from .conftest import FakeImages, FakePlanner, FakeTTS, requires_ffmpeg


async def _wait_done(client, job_id, timeout=60):
    async with asyncio.timeout(timeout):
        while True:
            body = (await client.get(f"/api/v1/shorts/status/{job_id}")).json()
            if body["status"] in ("COMPLETED", "FAILED"):
                return body
            await asyncio.sleep(0.1)


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@requires_ffmpeg
@pytest.mark.asyncio
async def test_end_to_end_with_image_fallback(settings):
    providers = Providers(planner=FakePlanner(3), tts=FakeTTS(), images=FakeImages(fail=True))
    app = create_app(settings, providers)
    async with app.router.lifespan_context(app), await _client(app) as client:
        r = await client.post("/api/v1/shorts/generate", json={"script": "테스트 대본입니다. 충분히 길어요."})
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        assert r.json()["status_url"] == f"/api/v1/shorts/status/{job_id}"

        body = await _wait_done(client, job_id)
        assert body["status"] == "COMPLETED", body["error"]
        assert body["progress"] == 100
        assert body["scene_count"] == 3
        # 같은 이유의 이미지 실패는 경고 하나로 묶임
        assert len(body["warnings"]) == 1
        assert body["warnings"][0].startswith("씬 1, 2, 3: ")
        assert body["duration_sec"] > 3

        video_resp = await client.get(body["video_url"])
        assert video_resp.status_code == 200
        assert video_resp.content[4:8] == b"ftyp"


@requires_ffmpeg
@pytest.mark.asyncio
async def test_failure_is_reported(settings):
    class BrokenTTS:
        async def synthesize(self, text, output_path):
            raise RuntimeError("tts down")

    app = create_app(settings, Providers(FakePlanner(), BrokenTTS(), FakeImages()))
    async with app.router.lifespan_context(app), await _client(app) as client:
        job_id = (await client.post("/api/v1/shorts/generate", json={"script": "대본 대본 대본 대본"})).json()["job_id"]
        body = await _wait_done(client, job_id)
        assert body["status"] == "FAILED"
        assert body["error"] == "RuntimeError: tts down"


@requires_ffmpeg
@pytest.mark.asyncio
async def test_validation_and_404(settings, providers):
    app = create_app(settings, providers)
    async with app.router.lifespan_context(app), await _client(app) as client:
        assert (await client.post("/api/v1/shorts/generate", json={"script": "짧음"})).status_code == 422
        assert (await client.post("/api/v1/shorts/generate", json={"script": " " * 20})).status_code == 422
        assert (await client.get("/api/v1/shorts/status/nope")).status_code == 404
        assert (await client.get("/health")).json()["status"] == "ok"


@requires_ffmpeg
@pytest.mark.asyncio
async def test_web_index_served(settings, providers):
    app = create_app(settings, providers)
    async with app.router.lifespan_context(app), await _client(app) as client:
        r = await client.get("/")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert "/api/v1/shorts" in r.text


@requires_ffmpeg
@pytest.mark.asyncio
async def test_stock_photo_credit_is_reported(settings):
    from io import BytesIO

    from PIL import Image

    class StockPhotos:
        async def generate(self, prompt, output_path, query="", exclude=()):
            buf = BytesIO()
            Image.new("RGB", (800, 1200), (30, 120, 60)).save(buf, format="JPEG")
            output_path.write_bytes(buf.getvalue())  # Pexels처럼 .png 경로에 JPEG 저장
            return "Kim / Pexels"

    app = create_app(settings, Providers(FakePlanner(2), FakeTTS(), StockPhotos()))
    async with app.router.lifespan_context(app), await _client(app) as client:
        job_id = (await client.post("/api/v1/shorts/generate", json={"script": "사진 출처 테스트 대본"})).json()["job_id"]
        body = await _wait_done(client, job_id)
        assert body["status"] == "COMPLETED", body["error"]
        assert body["image_credits"] == ["Kim / Pexels"]  # 씬 2개가 같은 촬영자면 한 번만
        assert body["warnings"] == []


class FakeMusic:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.tones = []

    async def fetch(self, tone, output_path):
        self.tones.append(tone)
        if self.fail:
            raise RuntimeError("openverse down")
        from app.services import video

        await video.run_command(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             "sine=frequency=220:duration=1.5", str(output_path)],
            timeout=30,
        )
        return "Sunny - Jane / Openverse (BY 3.0)"


@requires_ffmpeg
@pytest.mark.asyncio
async def test_background_music_is_mixed_and_credited(settings):
    music = FakeMusic()
    app = create_app(settings, Providers(FakePlanner(2), FakeTTS(), FakeImages(), music))
    async with app.router.lifespan_context(app), await _client(app) as client:
        r = await client.post("/api/v1/shorts/generate", json={"script": "배경음악 테스트 대본", "tone": "calm"})
        body = await _wait_done(client, r.json()["job_id"])
        assert body["status"] == "COMPLETED", body["error"]
        assert body["audio_credits"] == ["Sunny - Jane / Openverse (BY 3.0)"]
        assert body["warnings"] == []
        assert music.tones == ["calm"]
        assert (await client.get(body["video_url"])).content[4:8] == b"ftyp"
    job_dir = settings.output_dir / body["job_id"]
    assert not list(job_dir.glob("narration_*.mp4"))  # 섞기 전 영상은 정리됨


@requires_ffmpeg
@pytest.mark.asyncio
async def test_missing_music_only_warns(settings):
    app = create_app(settings, Providers(FakePlanner(2), FakeTTS(), FakeImages(), FakeMusic(fail=True)))
    async with app.router.lifespan_context(app), await _client(app) as client:
        r = await client.post("/api/v1/shorts/generate", json={"script": "배경음악 실패 테스트 대본"})
        body = await _wait_done(client, r.json()["job_id"])
        assert body["status"] == "COMPLETED", body["error"]
        assert body["audio_credits"] == []
        assert len(body["warnings"]) == 1 and body["warnings"][0].startswith("배경음악을 찾지 못해")
        assert body["duration_sec"] > 2


@requires_ffmpeg
@pytest.mark.asyncio
async def test_sound_can_be_turned_off(settings):
    settings = settings.model_copy(update={"background_music": False, "transition_sfx": False})
    music = FakeMusic()
    app = create_app(settings, Providers(FakePlanner(2), FakeTTS(), FakeImages(), music))
    async with app.router.lifespan_context(app), await _client(app) as client:
        r = await client.post("/api/v1/shorts/generate", json={"script": "소리 끄기 테스트 대본"})
        body = await _wait_done(client, r.json()["job_id"])
        assert body["status"] == "COMPLETED", body["error"]
        assert music.tones == [] and body["audio_credits"] == []
    assert not list((settings.output_dir / body["job_id"]).glob("whoosh.wav"))


class RecordingImages(FakeImages):
    def __init__(self, credit=None) -> None:
        super().__init__()
        self.prompts, self.queries, self.credit = [], [], credit

    async def generate(self, prompt, output_path, query="", exclude=()):
        self.prompts.append(prompt)
        self.queries.append(query)
        await super().generate(prompt, output_path, query, exclude)
        return self.credit


@requires_ffmpeg
@pytest.mark.asyncio
async def test_image_style_is_added_after_scene_prompt(settings):
    images = RecordingImages()
    app = create_app(settings, Providers(FakePlanner(2), FakeTTS(), images))
    async with app.router.lifespan_context(app), await _client(app) as client:
        r = await client.post("/api/v1/shorts/generate", json={"script": "그림 스타일 테스트 대본", "style": "watercolor"})
        body = await _wait_done(client, r.json()["job_id"])
        assert body["status"] == "COMPLETED", body["error"]
        assert body["warnings"] == []
    # 씬 묘사는 앞에 그대로 두고 스타일 문구를 뒤에 붙임 (사진 검색어에 섞이지 않도록)
    assert all(p.startswith("scene ") and p.endswith("visible paper texture, gentle washes of color") for p in images.prompts)


@requires_ffmpeg
@pytest.mark.asyncio
async def test_style_with_stock_photos_warns_once(settings):
    images = RecordingImages(credit="Kim / Pexels")
    app = create_app(settings, Providers(FakePlanner(2), FakeTTS(), images))
    async with app.router.lifespan_context(app), await _client(app) as client:
        r = await client.post("/api/v1/shorts/generate", json={"script": "그림 스타일 사진 테스트", "style": "anime"})
        body = await _wait_done(client, r.json()["job_id"])
        assert body["status"] == "COMPLETED", body["error"]
        assert len(body["warnings"]) == 1 and "그림 스타일이 적용되지 않아요" in body["warnings"][0]


@pytest.mark.asyncio
async def test_unknown_style_is_rejected(settings, providers):
    app = create_app(settings, providers)
    async with app.router.lifespan_context(app), await _client(app) as client:
        r = await client.post("/api/v1/shorts/generate", json={"script": "잘못된 스타일 테스트", "style": "oil"})
        assert r.status_code == 422
