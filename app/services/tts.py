"""나레이션 음성 합성.

Edge TTS → Gemini 음성 → Google 번역 음성(gTTS) 순서로 시도합니다.
Edge TTS는 무료지만 Microsoft가 접속을 막으면(403) 쓸 수 없어서, 예비 수단을 둡니다.
결과는 항상 output_path에 mp3로 저장합니다.
"""

import io
import re
import wave
from pathlib import Path

import edge_tts
from google import genai
from google.genai import types

from ..config import Settings
from ..errors import UserFacingError
from ..fallback import FallbackChain
from ..retry import with_retry
from . import video


class TTSError(UserFacingError):
    pass


def rate_to_tempo(rate: str) -> float:
    """Edge TTS 형식의 속도("+15%")를 재생 배율(1.15)로 바꿉니다."""
    match = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*%\s*", rate or "")
    if not match:
        return 1.0
    return min(2.0, max(0.5, 1 + float(match.group(1)) / 100))


async def _to_mp3(source: Path, output_path: Path, tempo: float, timeout: float) -> None:
    """임시 오디오를 mp3로 바꾸면서 말하기 속도를 맞춥니다."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
    if abs(tempo - 1.0) > 0.01:
        args += ["-filter:a", f"atempo={tempo:.3f}"]
    args += ["-c:a", "libmp3lame", "-q:a", "3", str(output_path)]
    await video.run_command(args, timeout=timeout)


def _ensure_written(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise TTSError("음성 파일이 비어 있습니다.")


class EdgeTTS:
    """Microsoft Edge 온라인 음성 (무료, 키 불필요, 가장 자연스러움)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def synthesize(self, text: str, output_path: Path) -> None:
        async def call() -> None:
            communicate = edge_tts.Communicate(
                text, voice=self._settings.tts_voice, rate=self._settings.tts_rate
            )
            await communicate.save(str(output_path))
            _ensure_written(output_path)

        await with_retry(call, attempts=self._settings.max_retries, what="Edge 음성")


class GeminiTTS:
    """Gemini 음성 모델. 24kHz 16비트 PCM을 받아 mp3로 바꿉니다."""

    SAMPLE_RATE = 24000

    def __init__(self, client: genai.Client, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def synthesize(self, text: str, output_path: Path) -> None:
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self._settings.gemini_tts_voice
                    )
                )
            ),
        )

        async def call() -> bytes:
            response = await self._client.aio.models.generate_content(
                model=self._settings.gemini_tts_model,
                contents=f"Read this Korean narration naturally and energetically: {text}",
                config=config,
            )
            for candidate in response.candidates or []:
                for part in (candidate.content.parts if candidate.content else None) or []:
                    if part.inline_data and part.inline_data.data:
                        return part.inline_data.data
            raise TTSError("Gemini가 음성을 돌려주지 않았습니다.")

        pcm = await with_retry(call, attempts=self._settings.max_retries, what="Gemini 음성")
        wav_path = output_path.with_suffix(".gemini.wav")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.SAMPLE_RATE)
            wav.writeframes(pcm)
        wav_path.write_bytes(buf.getvalue())
        try:
            await _to_mp3(wav_path, output_path, 1.0, self._settings.ffmpeg_timeout_sec)
        finally:
            wav_path.unlink(missing_ok=True)
        _ensure_written(output_path)


class GoogleTranslateTTS:
    """Google 번역 음성 (gTTS, 무료, 키 불필요). 기계음에 가깝지만 가장 잘 막히지 않습니다."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def synthesize(self, text: str, output_path: Path) -> None:
        import asyncio

        from gtts import gTTS

        raw_path = output_path.with_suffix(".gtts.mp3")

        async def call() -> None:
            await asyncio.to_thread(gTTS(text=text, lang="ko").save, str(raw_path))
            _ensure_written(raw_path)

        await with_retry(call, attempts=self._settings.max_retries, what="Google 번역 음성")
        try:
            # gTTS는 말이 느린 편이라 Edge와 같은 속도로 맞춥니다.
            tempo = rate_to_tempo(self._settings.tts_rate)
            await _to_mp3(raw_path, output_path, tempo, self._settings.ffmpeg_timeout_sec)
        finally:
            raw_path.unlink(missing_ok=True)
        _ensure_written(output_path)


class TTSChain(FallbackChain):
    def __init__(self, sources: list[tuple[str, object]], cooldown_sec: float) -> None:
        super().__init__(sources, cooldown_sec, what="나레이션 음성", error_cls=TTSError)

    async def synthesize(self, text: str, output_path: Path) -> None:
        try:
            await self.run(lambda source: source.synthesize(text, output_path))
        except TTSError as exc:
            raise TTSError(f"나레이션 음성을 만들지 못했어요 — {exc}") from exc


def build_tts_chain(settings: Settings, client: genai.Client | None) -> TTSChain:
    sources: list[tuple[str, object]] = []
    for name in (n.strip().lower() for n in settings.tts_sources.split(",")):
        if name == "edge":
            sources.append(("Edge", EdgeTTS(settings)))
        elif name == "gemini" and client is not None:
            sources.append(("Gemini", GeminiTTS(client, settings)))
        elif name == "gtts":
            sources.append(("Google 번역", GoogleTranslateTTS(settings)))
    return TTSChain(sources, settings.image_source_cooldown_sec)
