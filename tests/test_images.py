import httpx
import pytest

from app.config import Settings
from app.services.images import (
    ImageGenerationError,
    ImageSourceChain,
    NotConfiguredSource,
    PexelsError,
    PexelsImageSource,
)

from .test_errors import QUOTA

SETTINGS = Settings(max_retries=1, _env_file=None)
JPEG = b"\xff\xd8\xff fake jpeg"


def pexels_transport(photos_by_query: dict[str, list[dict]], seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.pexels.com":
            if request.headers.get("Authorization") != "good-key":
                return httpx.Response(401, text="Unauthorized")
            query = request.url.params["query"]
            assert request.url.params["orientation"] == "portrait"
            return httpx.Response(200, json={"photos": photos_by_query.get(query, [])})
        return httpx.Response(200, content=JPEG)  # 사진 다운로드

    return httpx.MockTransport(handler)


def photo(pid: int, who: str) -> dict:
    return {"id": pid, "photographer": who, "src": {"portrait": f"https://images.pexels.com/{pid}.jpg"}}


@pytest.mark.asyncio
async def test_pexels_downloads_photo_and_returns_credit(tmp_path):
    seen = []
    transport = pexels_transport({"glass of water": [photo(1, "Kim"), photo(2, "Lee")]}, seen)
    source = PexelsImageSource("good-key", SETTINGS, transport=transport)

    out = tmp_path / "a.png"
    assert await source.generate("ignored prompt", out, query="glass of water") == "Kim / Pexels"
    assert out.read_bytes() == JPEG

    # 같은 검색어라도 방금 쓴 사진은 건너뛰어 씬마다 다른 사진이 나옴
    out2 = tmp_path / "b.png"
    assert await source.generate("ignored", out2, query="glass of water") == "Lee / Pexels"


@pytest.mark.asyncio
async def test_pexels_retries_with_shorter_query_then_reports_no_result(tmp_path):
    seen = []
    transport = pexels_transport({"sunny kitchen": [photo(7, "Park")]}, seen)
    source = PexelsImageSource("good-key", SETTINGS, transport=transport)

    credit = await source.generate("x", tmp_path / "a.png", query="sunny kitchen morning table")
    assert credit == "Park / Pexels"
    queries = [r.url.params["query"] for r in seen if r.url.host == "api.pexels.com"]
    assert queries == ["sunny kitchen morning table", "sunny kitchen"]

    with pytest.raises(ImageGenerationError, match="찾지 못함"):
        await source.generate("x", tmp_path / "b.png", query="nothing matches here")


@pytest.mark.asyncio
async def test_pexels_bad_key_is_a_permanent_error(tmp_path):
    source = PexelsImageSource("bad-key", SETTINGS, transport=pexels_transport({}, []))
    with pytest.raises(PexelsError) as info:
        await source.generate("x", tmp_path / "a.png", query="water")
    assert info.value.code == 401


class Recorder:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    async def generate(self, prompt, output_path, query=""):
        self.calls += 1
        if self.error:
            raise self.error
        output_path.write_bytes(b"img")
        return self.result


@pytest.mark.asyncio
async def test_chain_falls_back_and_skips_blocked_source(tmp_path):
    gemini = Recorder(error=QUOTA)
    pexels = Recorder(result="Kim / Pexels")
    chain = ImageSourceChain([("Gemini", gemini), ("Pexels", pexels)], cooldown_sec=3600)

    for i in range(3):
        assert await chain.generate("p", tmp_path / f"{i}.png", "q") == "Kim / Pexels"
    assert gemini.calls == 1  # 한도 0 오류 뒤에는 Gemini를 건너뜀
    assert pexels.calls == 3


@pytest.mark.asyncio
async def test_chain_keeps_trying_after_temporary_error(tmp_path):
    gemini = Recorder(error=RuntimeError("timeout"))
    pexels = Recorder(result="Kim / Pexels")
    chain = ImageSourceChain([("Gemini", gemini), ("Pexels", pexels)], cooldown_sec=3600)
    for i in range(2):
        await chain.generate("p", tmp_path / f"{i}.png")
    assert gemini.calls == 2  # 일시 오류는 막지 않음


@pytest.mark.asyncio
async def test_chain_reports_short_korean_reasons_when_all_fail(tmp_path):
    chain = ImageSourceChain(
        [("Gemini", Recorder(error=QUOTA)), ("Pexels", NotConfiguredSource("PEXELS_API_KEY를 설정하세요"))],
        cooldown_sec=3600,
    )
    with pytest.raises(ImageGenerationError) as info:
        await chain.generate("p", tmp_path / "a.png")
    message = str(info.value)
    assert message == "Gemini: 사용량 한도 초과 (무료 등급이거나 결제 필요) / Pexels: PEXELS_API_KEY를 설정하세요"
    assert "quota" not in message  # 영어 원문은 화면에 보이지 않음
