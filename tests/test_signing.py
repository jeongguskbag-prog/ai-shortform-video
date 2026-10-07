import time
from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio

from app.main import create_app
from app.pipeline import Providers
from app.signing import URLSigner

from .conftest import FakeImages, FakePlanner, FakeTTS, requires_ffmpeg
from .test_api import _client, _wait_done

SECRET = b"0123456789abcdef0123456789abcdef"


def _parts(url):
    q = parse_qs(urlsplit(url).query)
    return urlsplit(url).path, int(q["expires"][0]), q["sig"][0]


def test_sign_and_verify_roundtrip():
    signer = URLSigner(SECRET, ttl_sec=60)
    path, expires, sig = _parts(signer.sign("/videos/a/b.mp4").url)
    assert path == "/videos/a/b.mp4"
    assert signer.verify(path, expires, sig)
    assert not signer.verify("/videos/a/c.mp4", expires, sig)          # 다른 파일
    assert not signer.verify(path, expires + 1, sig)                    # 만료 시각 조작
    assert not signer.verify(path, expires, sig[:-1] + ("B" if sig[-1] == "A" else "A"))  # 서명 조작
    assert not signer.verify(path, expires, sig, now=expires + 1)       # 만료
    assert not URLSigner(b"x" * 32, 60).verify(path, expires, sig)      # 다른 비밀키


def test_rejects_short_secret():
    with pytest.raises(ValueError):
        URLSigner(b"short", 60)


@pytest_asyncio.fixture
async def finished(settings):
    """키와 서명 비밀키를 설정한 서버에서 작업 하나를 끝까지 실행합니다."""
    secured = settings.model_copy(update={"api_keys": "k", "url_signing_secret": SECRET.decode()})
    app = create_app(secured, Providers(FakePlanner(1), FakeTTS(), FakeImages()))
    async with app.router.lifespan_context(app), await _client(app) as client:
        client.headers["X-API-Key"] = "k"
        job_id = (await client.post("/api/v1/shorts/generate", json={"script": "서명 테스트 대본입니다."})).json()["job_id"]
        body = await _wait_done(client, job_id)
        assert body["status"] == "COMPLETED", body["error"]
        del client.headers["X-API-Key"]  # 영상은 키 없이 서명만으로 열려야 함
        yield client, body


@requires_ffmpeg
@pytest.mark.asyncio
async def test_signed_video_url(finished):
    client, body = finished
    assert body["video_url"].startswith(f"/videos/{body['job_id']}/")
    assert body["video_url_expires_at"]

    r = await client.get(body["video_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["cache-control"].startswith("private, max-age=")

    r = await client.get(body["video_url"], headers={"Range": "bytes=0-99"})
    assert r.status_code == 206  # 영상 탐색(seek)에 필요
    assert len(r.content) == 100


@requires_ffmpeg
@pytest.mark.asyncio
async def test_rejects_bad_video_links(finished):
    client, body = finished
    path, expires, sig = _parts(body["video_url"])

    assert (await client.get(path)).status_code == 403                                     # 서명 없음
    assert (await client.get(path, params={"expires": expires, "sig": "x"})).status_code == 403
    assert (await client.get(path, params={"expires": expires + 1, "sig": sig})).status_code == 403

    signer = URLSigner(SECRET, ttl_sec=60)
    expired = signer.sign(path, now=time.time() - 3600).url
    assert (await client.get(expired)).status_code == 403

    # 서명이 맞아도 작업에 기록된 파일이 아니면 열 수 없음
    other = signer.sign(f"/videos/{body['job_id']}/plan.json").url
    assert (await client.get(other)).status_code == 404
    traversal = signer.sign(f"/videos/{body['job_id']}/..%2F..%2Fetc%2Fpasswd").url
    assert (await client.get(traversal)).status_code in (403, 404)
