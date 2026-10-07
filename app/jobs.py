"""인메모리 작업 저장소.

단일 프로세스 배포를 전제로 합니다. 여러 워커/서버로 확장할 때는
Redis 같은 외부 저장소와 작업 큐(Celery, arq 등)로 교체하세요.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .schemas import JobStatus, JobStatusResponse


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Job:
    job_id: str
    script: str
    tone: str
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    title: str | None = None
    scene_count: int | None = None
    video_url: str | None = None
    duration_sec: float | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def update(self, **changes) -> None:
        for key, value in changes.items():
            setattr(self, key, value)
        # 진행률은 뒤로 가지 않도록 보장
        self.progress = max(0, min(100, self.progress))
        self.updated_at = _now()

    def to_response(self) -> JobStatusResponse:
        return JobStatusResponse(
            job_id=self.job_id,
            status=self.status,
            progress=self.progress,
            title=self.title,
            scene_count=self.scene_count,
            video_url=self.video_url,
            duration_sec=self.duration_sec,
            error=self.error,
            warnings=list(self.warnings),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, script: str, tone: str) -> Job:
        job = Job(job_id=uuid.uuid4().hex, script=script, tone=tone)
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def pop_expired(self, ttl: timedelta) -> list[Job]:
        """TTL이 지난 '종료된' 작업을 저장소에서 제거하고 반환합니다."""
        cutoff = _now() - ttl
        expired = [j for j in self._jobs.values() if j.status.is_terminal and j.updated_at < cutoff]
        for job in expired:
            del self._jobs[job.job_id]
        return expired

    def __len__(self) -> int:
        return len(self._jobs)
