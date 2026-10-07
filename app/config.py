"""애플리케이션 설정. 모든 값은 환경변수 또는 .env 파일로 덮어쓸 수 있습니다."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- 인증 ---
    api_keys: str = Field(
        default="",
        description="쉼표로 구분한 API 키 목록 (API_KEYS). 비우면 인증을 끕니다.",
    )

    # --- 외부 서비스 ---
    gemini_api_key: str | None = Field(default=None, description="Gemini API 키 (GEMINI_API_KEY)")
    llm_model: str = "gemini-2.5-flash"
    image_model: str = "imagen-4.0-generate-001"
    tts_voice: str = "ko-KR-SunHiNeural"
    tts_rate: str = "+15%"

    # --- 출력 영상 ---
    output_dir: Path = Path("output")
    video_width: int = 720
    video_height: int = 1280
    video_fps: int = 30
    scene_padding_sec: float = Field(default=0.35, ge=0, description="씬 끝에 붙는 여백(초)")
    max_zoom: float = Field(default=1.15, ge=1.0, le=2.0, description="켄 번스 효과 최대 줌 배율")
    font_path: Path | None = Field(default=None, description="자막 폰트 경로. 비우면 한글 폰트를 자동 탐색")
    subtitle_font_size: int = 54

    # --- 동시성 / 안정성 ---
    max_concurrent_jobs: int = Field(default=2, ge=1)
    max_concurrent_scenes: int = Field(default=3, ge=1)
    max_retries: int = Field(default=3, ge=1)
    ffmpeg_timeout_sec: float = 300.0
    job_ttl_hours: float = Field(default=24.0, gt=0, description="완료된 작업과 파일을 보관하는 시간")

    @property
    def api_key_list(self) -> tuple[str, ...]:
        return tuple(k.strip() for k in self.api_keys.split(",") if k.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
