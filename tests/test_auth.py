import pytest

from app.main import create_app

from .conftest import requires_ffmpeg
from .test_api import _client

SCRIPT = {"script": "인증 테스트용 대본입니다."}


@pytest.fixture
def secured(settings):
    return settings.model_copy(update={"api_keys": "key-one, key-two"})


@requires_ffmpeg
@pytest.mark.asyncio
async def test_rejects_missing_or_wrong_key(secured, providers):
    app = create_app(secured, providers)
    async with app.router.lifespan_context(app), await _client(app) as client:
        r = await client.post("/api/v1/shorts/generate", json=SCRIPT)
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"
        r = await client.post("/api/v1/shorts/generate", json=SCRIPT, headers={"X-API-Key": "nope"})
        assert r.status_code == 401
        assert (await client.get("/api/v1/shorts/status/x")).status_code == 401


@requires_ffmpeg
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [{"X-API-Key": "key-one"}, {"X-API-Key": "key-two"}, {"Authorization": "Bearer key-two"}],
)
async def test_accepts_any_configured_key(secured, providers, headers):
    app = create_app(secured, providers)
    async with app.router.lifespan_context(app), await _client(app) as client:
        r = await client.post("/api/v1/shorts/generate", json=SCRIPT, headers=headers)
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        assert len(job_id) == 32
        assert (await client.get(f"/api/v1/shorts/status/{job_id}", headers=headers)).status_code == 200


@requires_ffmpeg
@pytest.mark.asyncio
async def test_public_routes_and_auth_flag(secured, settings, providers):
    app = create_app(secured, providers)
    async with app.router.lifespan_context(app), await _client(app) as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/health")).json() == {"status": "ok", "auth_required": True}

    open_app = create_app(settings, providers)
    async with open_app.router.lifespan_context(open_app), await _client(open_app) as client:
        assert (await client.get("/health")).json()["auth_required"] is False
        assert (await client.post("/api/v1/shorts/generate", json=SCRIPT)).status_code == 202
