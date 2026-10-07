from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

MAX_SCENES = 20


class ScriptRequest(BaseModel):
    script: str = Field(min_length=10, max_length=5000, description="쇼츠로 만들 대본")
    tone: str = Field(default="energetic", min_length=1, max_length=50, description="영상 분위기")

    @field_validator("script", "tone")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("공백만으로 이루어질 수 없습니다.")
        return value


class ScenePlan(BaseModel):
    scene_id: int
    narration: str
    visual_prompt: str
    subtitle_text: str


class ShortsPlan(BaseModel):
    """LLM 응답 스키마. Gemini의 response_schema로도 쓰이므로 단순하게 유지합니다."""

    title: str
    scenes: list[ScenePlan]


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    GENERATING_ASSETS = "GENERATING_ASSETS"
    RENDERING_FINAL = "RENDERING_FINAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED)


class CreateJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    status_url: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    title: str | None = None
    scene_count: int | None = None
    video_url: str | None = Field(default=None, description="서명된 영상 링크. 만료되면 상태를 다시 조회하세요.")
    video_url_expires_at: datetime | None = None
    duration_sec: float | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
