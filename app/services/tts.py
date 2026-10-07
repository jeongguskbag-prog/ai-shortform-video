"""나레이션 음성 합성 (Edge TTS)."""

from pathlib import Path

import edge_tts

from ..config import Settings
from ..retry import with_retry


class EdgeTTS:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def synthesize(self, text: str, output_path: Path) -> None:
        async def call() -> None:
            communicate = edge_tts.Communicate(
                text, voice=self._settings.tts_voice, rate=self._settings.tts_rate
            )
            await communicate.save(str(output_path))
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError("TTS 결과 파일이 비어 있습니다.")

        await with_retry(call, attempts=self._settings.max_retries, what="TTS")
