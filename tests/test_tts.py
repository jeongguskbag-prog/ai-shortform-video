import math
import struct

import pytest

from app.config import Settings
from app.jobs import JobStore
from app.pipeline import Providers, ShortsPipeline
from app.services import tts, video

from .conftest import FakeImages, FakePlanner, requires_ffmpeg

SETTINGS = Settings(max_retries=1, _env_file=None)


class EdgeBlocked(Exception):
    """aiohttp가 Edge TTS 접속 거부 때 내는 WSServerHandshakeError 모양."""

    code = 403


class Recorder:
    def __init__(self, error=None):
        self.error, self.calls = error, 0

    async def synthesize(self, text, output_path):
        self.calls += 1
        if self.error:
            raise self.error
        output_path.write_bytes(b"audio")


@pytest.mark.parametrize(("rate", "tempo"), [("+15%", 1.15), ("-10%", 0.9), ("0%", 1.0), ("", 1.0), ("x", 1.0)])
def test_rate_to_tempo(rate, tempo):
    assert tts.rate_to_tempo(rate) == pytest.approx(tempo)


@pytest.mark.asyncio
async def test_chain_falls_back_when_edge_is_blocked_and_then_skips_it(tmp_path):
    edge, backup = Recorder(EdgeBlocked("Invalid response status")), Recorder()
    chain = tts.TTSChain([("Edge", edge), ("Google 번역", backup)], cooldown_sec=3600)
    for i in range(3):
        await chain.synthesize("안녕", tmp_path / f"{i}.mp3")
    assert edge.calls == 1  # 403 뒤로는 Edge를 건너뜀
    assert backup.calls == 3


@pytest.mark.asyncio
async def test_chain_reports_every_source_in_korean(tmp_path):
    chain = tts.TTSChain(
        [("Edge", Recorder(EdgeBlocked("x"))), ("Google 번역", Recorder(RuntimeError("network down")))],
        cooldown_sec=3600,
    )
    with pytest.raises(tts.TTSError) as info:
        await chain.synthesize("안녕", tmp_path / "a.mp3")
    msg = str(info.value)
    assert msg.startswith("나레이션 음성을 만들지 못했어요 — Edge: 접근 거부")
    assert "Google 번역: network down" in msg
    assert "Gemini" not in msg


def test_build_chain_order_and_gemini_needs_client():
    assert tts.build_tts_chain(SETTINGS, client=None).names == ["Edge", "Google 번역"]
    assert tts.build_tts_chain(SETTINGS, client=object()).names == ["Edge", "Gemini", "Google 번역"]


@requires_ffmpeg
@pytest.mark.asyncio
async def test_google_translate_tts_writes_sped_up_mp3(tmp_path, monkeypatch):
    class FakeGTTS:
        def __init__(self, text, lang):
            assert lang == "ko"

        def save(self, path):
            import subprocess

            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2.3", path],
                check=True,
            )

    import gtts

    monkeypatch.setattr(gtts, "gTTS", FakeGTTS)
    out = tmp_path / "audio_01.mp3"
    await tts.GoogleTranslateTTS(SETTINGS).synthesize("안녕하세요", out)
    assert await video.probe_duration(out) == pytest.approx(2.3 / 1.15, abs=0.1)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["audio_01.mp3"]  # 임시 파일 정리


@requires_ffmpeg
@pytest.mark.asyncio
async def test_gemini_tts_turns_pcm_into_mp3(tmp_path):
    pcm = b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) for i in range(24000))  # 1초

    class Part:
        class inline_data:
            data = pcm

    class Response:
        class candidate:
            class content:
                parts = [Part]

        candidates = [candidate]

    class Models:
        async def generate_content(self, *, model, contents, config):
            assert model == SETTINGS.gemini_tts_model
            assert config.response_modalities == ["AUDIO"]
            return Response

    class Client:
        class aio:
            models = Models()

    out = tmp_path / "audio_01.mp3"
    await tts.GeminiTTS(Client, SETTINGS).synthesize("안녕하세요", out)
    assert await video.probe_duration(out) == pytest.approx(1.0, abs=0.1)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["audio_01.mp3"]


@requires_ffmpeg
@pytest.mark.asyncio
async def test_failed_job_remembers_stage_and_blames_the_right_service(settings, spec):
    chain = tts.TTSChain([("Edge", Recorder(EdgeBlocked("Invalid response status")))], cooldown_sec=0)
    pipeline = ShortsPipeline(Providers(FakePlanner(1), chain, FakeImages()), settings, spec)
    job = JobStore().create("대본", "tone")
    await pipeline.run(job)
    assert job.status == "FAILED"
    assert job.failed_stage == "GENERATING_ASSETS"  # 기획은 성공, 나레이션 단계에서 실패
    assert job.error.startswith("나레이션 음성을 만들지 못했어요 — Edge: 접근 거부")
    assert "Gemini API 키" not in job.error
