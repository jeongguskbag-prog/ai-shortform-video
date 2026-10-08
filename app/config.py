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

    url_signing_secret: str = Field(
        default="",
        description="영상 링크 서명용 비밀키. 비우면 시작할 때마다 새로 만들어져 재시작 시 기존 링크가 무효가 됩니다.",
    )
    video_url_ttl_sec: int = Field(default=3600, ge=60, description="영상 링크 유효 시간(초)")

    # --- 외부 서비스 ---
    gemini_api_key: str | None = Field(default=None, description="Gemini API 키 (GEMINI_API_KEY)")
    # "-latest" 별칭은 Google이 최신 안정 모델로 자동 연결해 줘서, 특정 버전이 내려가도 404가 나지 않습니다.
    llm_model: str = "gemini-flash-latest"
    # 기본 모델이 과부하(503) 등으로 계속 실패하면 이 모델로 한 번 더 시도합니다. 비우면 사용하지 않습니다.
    llm_fallback_model: str = "gemini-flash-lite-latest"
    # Gemini 이미지 모델(generate_content)과 Imagen 모델(imagen-*, generate_images)을 모두 지원합니다.
    image_model: str = "gemini-3.1-flash-image"
    # 무료 사진(Pexels) API 키. https://www.pexels.com/api/ 에서 무료로 발급받습니다.
    pexels_api_key: str = ""
    # 이미지를 시도할 순서. 앞의 것이 실패하면 다음 것을 쓰고, 모두 실패하면 그라디언트 배경을 씁니다.
    image_sources: str = "gemini,pexels"
    # 한도 초과·키 오류처럼 다시 해도 안 되는 오류가 난 이미지 소스는 이 시간(초) 동안 건너뜁니다.
    image_source_cooldown_sec: int = Field(default=3600, ge=0)
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
