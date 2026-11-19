"""배경음악과 장면 전환 효과음.

배경음악은 Openverse 오디오 검색(키 불필요, 출처 표기 라이선스만)에서 영상 분위기에 맞춰 찾고,
전환 효과음("휙")은 외부 서비스 없이 FFmpeg로 직접 합성합니다. 둘 다 실패해도 영상은 소리 없이 완성됩니다.
"""

import logging
import random
from collections import deque
from pathlib import Path

import httpx

from ..config import Settings
from ..errors import UserFacingError
from ..retry import with_retry
from .images import StockPhotoError
from .video import run_command

logger = logging.getLogger(__name__)

# 웹 화면의 분위기 선택지 → 음악 검색어(앞에서부터 시도). 직접 입력한 분위기는 기본 검색어를 씁니다.
# Openverse 음악은 곡 수가 적어 검색어에 따라 결과가 0곡이 되기도 하므로(예: "corporate background"),
# 분위기에 맞는 좁은 검색어 뒤에 결과가 많은 넓은 검색어(piano·acoustic 등 각 20곡 이상, 2026-11 확인)를 둡니다.
TONE_MUSIC_QUERIES = {
    "energetic": ("upbeat", "acoustic"),
    "calm": ("calm piano", "piano"),
    "humorous": ("funny", "upbeat"),
    "emotional": ("emotional piano", "piano"),
    "informative": ("acoustic", "piano"),
    "mysterious": ("mysterious ambient", "ambient"),
}
DEFAULT_MUSIC_QUERIES = ("piano", "acoustic")


class MusicNotFoundError(UserFacingError):
    pass


def music_queries_for(tone: str) -> tuple[str, ...]:
    return TONE_MUSIC_QUERIES.get(tone.strip().lower(), DEFAULT_MUSIC_QUERIES)


class OpenverseMusicSource:
    """Openverse 공개 라이선스 음악 (키 불필요). https://api.openverse.org/

    영상 길이보다 짧은 곡은 반복 재생하므로 너무 짧은 곡(효과음 등)만 거릅니다.
    """

    SEARCH_URL = "https://api.openverse.org/v1/audio/"
    LICENSES = "cc0,pdm,by"
    MIN_DURATION_MS = 30_000
    MAX_DOWNLOAD_TRIES = 3

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport  # 테스트에서 가짜 응답을 넣을 때 사용
        self._recent: deque[str] = deque(maxlen=50)  # 연달아 만든 영상에 같은 곡이 반복되지 않도록

    async def fetch(self, tone: str, output_path: Path) -> str:
        """분위기에 맞는 곡을 output_path에 저장하고 출처 표기 문구를 돌려줍니다."""
        queries = music_queries_for(tone)

        async def call() -> str | None:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(30.0),
                headers={"User-Agent": "ai-shortform-video/1.0"},
                follow_redirects=True,
            ) as client:
                for query in queries:  # 앞 검색어에 쓸 만한 곡이 없으면 다음 검색어로
                    credit = await self._search_and_download(client, query, output_path)
                    if credit:
                        return credit
            return None

        credit = await with_retry(call, attempts=self._settings.max_retries, what="배경음악")
        if credit is None:
            raise MusicNotFoundError(f"'{' / '.join(queries)}'에 맞는 배경음악을 찾지 못함")
        return credit

    async def _search_and_download(self, client: httpx.AsyncClient, query: str, output_path: Path) -> str | None:
        response = await client.get(
            self.SEARCH_URL,
            params={
                "q": query,
                "category": "music",
                "license": self.LICENSES,
                "mature": "false",
                "page_size": 20,
            },
        )
        if response.status_code != 200:
            raise StockPhotoError("Openverse 음악", response.status_code, response.text[:200])
        tracks = [
            t for t in response.json().get("results") or []
            if t.get("url") and (t.get("duration") or 0) >= self.MIN_DURATION_MS
        ]
        fresh = [t for t in tracks if t.get("id") not in self._recent] or tracks
        random.shuffle(fresh)
        for track in fresh[: self.MAX_DOWNLOAD_TRIES]:
            try:
                audio = await client.get(track["url"])
            except httpx.HTTPError:
                continue
            if audio.status_code == 200 and audio.content:
                output_path.write_bytes(audio.content)
                self._recent.append(track.get("id"))
                license_name = f"{track.get('license', '')} {track.get('license_version') or ''}"
                title = track.get("title") or "제목 없음"
                who = track.get("creator") or "Unknown"
                return f"{title} - {who} / Openverse ({license_name.strip().upper()})"
        return None


async def make_whoosh(output_path: Path, *, duration: float = 0.45, timeout: float = 30) -> None:
    """장면 전환용 '휙' 소리: 분홍 잡음을 대역 필터로 다듬고 부드럽게 커졌다 작아지게 합니다."""
    fade_out = duration * 0.7
    await run_command(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.6:duration={duration:.3f}:seed=7",
            "-af",
            (
        "highpass=f=400,lowpass=f=5000,"
        f"afade=t=in:st=0:d={duration - fade_out:.3f}:curve=qsin,"
        f"afade=t=out:st={duration - fade_out:.3f}:d={fade_out:.3f}:curve=exp"
            ),
            "-ar", "44100", "-ac", "2",
            str(output_path),
        ],
        timeout=timeout,
    )
