"""쇼츠 생성 파이프라인: 기획 → (씬별 TTS + 이미지 + 렌더링, 병렬) → 이어 붙이기."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Settings
from .errors import describe_error, is_retryable, short_reason
from .jobs import Job
from .schemas import JobStatus, ScenePlan, ShortsPlan
from .services import audio, video
from .services.images import make_placeholder

logger = logging.getLogger(__name__)

# 진행률 구간
PLAN_START, ASSETS_START, ASSETS_END, FINAL_DONE = 5, 15, 90, 100


class Planner(Protocol):
    async def plan(self, script: str, tone: str) -> ShortsPlan: ...


class TTSEngine(Protocol):
    async def synthesize(self, text: str, output_path: Path) -> None: ...


class ImageGenerator(Protocol):
    async def generate(
        self, prompt: str, output_path: Path, query: str = "", exclude: Sequence[str] = ()
    ) -> str | None:
        """이미지를 output_path에 저장하고, 출처 표기가 필요하면 그 문구를 돌려줍니다."""
        ...


class MusicSource(Protocol):
    async def fetch(self, tone: str, output_path: Path) -> str:
        """분위기에 맞는 배경음악을 output_path에 저장하고 출처 표기 문구를 돌려줍니다."""
        ...


@dataclass
class Providers:
    planner: Planner
    tts: TTSEngine
    images: ImageGenerator
    music: MusicSource | None = None  # 없으면 배경음악 없이 만듭니다


class ShortsPipeline:
    def __init__(self, providers: Providers, settings: Settings, spec: video.VideoSpec) -> None:
        self._providers = providers
        self._settings = settings
        self._spec = spec
        self._job_slots = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._scene_slots = asyncio.Semaphore(settings.max_concurrent_scenes)

    async def run(self, job: Job) -> None:
        """작업을 끝까지 실행합니다. 예외는 삼키지 않고 job 상태에 기록합니다."""
        job_dir = self._settings.output_dir / job.job_id
        try:
            async with self._job_slots:
                job_dir.mkdir(parents=True, exist_ok=True)
                await self._run(job, job_dir)
        except asyncio.CancelledError:
            job.update(failed_stage=job.status, status=JobStatus.FAILED, error="서버 종료로 작업이 취소되었습니다.")
            raise
        except Exception as exc:
            logger.exception("작업 %s 실패", job.job_id)
            root: BaseException = exc
            while isinstance(root, BaseExceptionGroup) and root.exceptions:
                root = root.exceptions[0]  # TaskGroup이 감싼 실제 원인을 보여줌
            job.update(failed_stage=job.status, status=JobStatus.FAILED, error=describe_error(root))

    async def _run(self, job: Job, job_dir: Path) -> None:
        job.update(status=JobStatus.PLANNING, progress=PLAN_START)
        plan = await self._providers.planner.plan(job.script, job.tone)
        (job_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        job.update(
            status=JobStatus.GENERATING_ASSETS,
            progress=ASSETS_START,
            title=plan.title,
            scene_count=len(plan.scenes),
        )

        done = 0
        step = (ASSETS_END - ASSETS_START) / len(plan.scenes)
        images = _ImageFailures()

        async def build(index: int, scene: ScenePlan) -> tuple[Path, float]:
            nonlocal done
            async with self._scene_slots:
                result = await self._build_scene(job, job_dir, index, scene, images)
            done += 1
            job.update(progress=ASSETS_START + int(step * done))
            return result

        # 배경음악은 씬을 만드는 동안 함께 찾습니다.
        music_task = asyncio.create_task(self._fetch_music(job, job_dir))
        try:
            # TaskGroup: 하나라도 실패하면 나머지 씬 작업을 즉시 취소합니다.
            async with asyncio.TaskGroup() as tg:
                tasks = [tg.create_task(build(i, s)) for i, s in enumerate(plan.scenes)]
            scene_paths = [t.result()[0] for t in tasks]
            scene_durations = [t.result()[1] for t in tasks]
            job.warnings.extend(images.summary())

            job.update(status=JobStatus.RENDERING_FINAL, progress=ASSETS_END)
            final_name = f"final_{job.job_id}.mp4"
            narration_path = job_dir / f"narration_{job.job_id}.mp4"
            duration = await video.concat_scenes(
                scene_paths, narration_path, timeout=self._settings.ffmpeg_timeout_sec
            )
            for path in scene_paths:  # 중간 산출물 정리 (이미지·음성·기획은 디버깅용으로 남김)
                path.unlink(missing_ok=True)
            await self._add_soundtrack(
                job, job_dir, narration_path, job_dir / final_name, scene_durations, duration, await music_task
            )
        finally:
            music_task.cancel()  # 중간에 실패했을 때 음악 다운로드가 남지 않도록 (끝난 작업이면 아무 일 없음)

        job.update(
            status=JobStatus.COMPLETED,
            progress=FINAL_DONE,
            duration_sec=round(duration, 2),
            video_file=final_name,
        )
        logger.info("작업 %s 완료: %d개 씬, %.1f초", job.job_id, len(plan.scenes), duration)

    async def _fetch_music(self, job: Job, job_dir: Path) -> Path | None:
        """배경음악을 받아 둡니다. 실패해도 작업은 계속하고 경고만 남깁니다."""
        if not (self._settings.background_music and self._providers.music):
            return None
        path = job_dir / "music.mp3"
        try:
            credit = await self._providers.music.fetch(job.tone, path)
        except Exception as exc:
            reason = short_reason(exc)
            logger.warning("작업 %s 배경음악 실패, 음악 없이 진행: %s", job.job_id, reason)
            job.warnings.append(f"배경음악을 찾지 못해 음악 없이 만들었어요 ({reason})")
            return None
        job.audio_credits.append(credit)
        return path

    async def _add_soundtrack(
        self,
        job: Job,
        job_dir: Path,
        narration_path: Path,
        final_path: Path,
        scene_durations: list[float],
        duration: float,
        music_path: Path | None,
    ) -> None:
        """배경음악·전환 효과음을 섞어 최종 영상을 만듭니다. 섞지 못하면 나레이션만 있는 영상을 씁니다."""
        sfx_path, sfx_times = None, []
        if self._settings.transition_sfx and len(scene_durations) > 1:
            sfx_path = job_dir / "whoosh.wav"
            try:
                await audio.make_whoosh(sfx_path, timeout=self._settings.ffmpeg_timeout_sec)
                # 소리가 가장 큰 순간이 장면이 바뀌는 시점에 오도록 조금 앞당깁니다.
                boundary = 0.0
                for d in scene_durations[:-1]:
                    boundary += d
                    sfx_times.append(max(0.0, boundary - 0.15))
            except Exception as exc:
                logger.warning("작업 %s 전환 효과음 생성 실패: %s", job.job_id, exc)
                sfx_path = None
        if music_path is None and not sfx_times:
            narration_path.replace(final_path)
            return
        try:
            await video.mix_soundtrack(
                narration_path,
                final_path,
                duration=duration,
                music_path=music_path,
                music_volume=self._settings.music_volume,
                sfx_path=sfx_path,
                sfx_times=sfx_times,
                sfx_volume=self._settings.sfx_volume,
                timeout=self._settings.ffmpeg_timeout_sec,
            )
            narration_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("작업 %s 배경음·효과음 합성 실패, 나레이션만 사용: %s", job.job_id, exc)
            job.warnings.append("배경음악·효과음을 넣지 못해 나레이션만 있는 영상으로 만들었어요")
            job.audio_credits.clear()
            final_path.unlink(missing_ok=True)
            narration_path.replace(final_path)

    async def _build_scene(
        self, job: Job, job_dir: Path, index: int, scene: ScenePlan, images: "_ImageFailures"
    ) -> tuple[Path, float]:
        n = index + 1
        audio_path = job_dir / f"audio_{n:02d}.mp3"
        image_path = job_dir / f"image_{n:02d}.png"
        subtitle_path = job_dir / f"subtitle_{n:02d}.png"
        scene_path = job_dir / f"scene_{n:02d}.mp4"

        async def image_with_fallback() -> None:
            if images.blocked:
                # 한도 0·키 오류처럼 다시 해도 안 되는 오류가 이미 났다면 요청을 아낍니다.
                images.add(n, images.blocked)
            else:
                try:
                    credit = await self._providers.images.generate(
                        scene.visual_prompt, image_path, scene.stock_query, exclude=scene.stock_exclude
                    )
                    if credit and credit not in job.image_credits:
                        job.image_credits.append(credit)
                    return
                except Exception as exc:
                    reason = short_reason(exc)
                    logger.warning("작업 %s 씬 %d 이미지 생성 실패, 대체 배경 사용: %s", job.job_id, n, reason)
                    images.add(n, reason)
                    if not is_retryable(exc):
                        images.blocked = reason
            await make_placeholder(scene.visual_prompt, self._spec.size, image_path)

        # 음성은 필수, 이미지는 실패해도 대체 배경으로 진행
        burn = self._settings.burn_subtitles
        jobs = [
            self._providers.tts.synthesize(scene.narration, audio_path),
            image_with_fallback(),
        ]
        if burn:
            jobs.append(video.render_subtitle_png(scene.subtitle_text, self._spec, subtitle_path))
        await asyncio.gather(*jobs)
        scene_duration = await video.render_scene(
            image_path=image_path,
            audio_path=audio_path,
            subtitle_png=subtitle_path if burn else None,
            output_path=scene_path,
            spec=self._spec,
            zoom_in=index % 2 == 0,  # 씬마다 줌 인/아웃을 번갈아 단조로움을 줄임
        )
        return scene_path, scene_duration


class _ImageFailures:
    """작업 하나의 이미지 실패를 모아 같은 이유끼리 경고 하나로 묶습니다."""

    def __init__(self) -> None:
        self.blocked: str | None = None
        self._scenes_by_reason: dict[str, list[int]] = {}

    def add(self, scene_no: int, reason: str) -> None:
        self._scenes_by_reason.setdefault(reason, []).append(scene_no)

    def summary(self) -> list[str]:
        return [
            f"씬 {', '.join(map(str, sorted(scenes)))}: 이미지를 만들지 못해 기본 배경을 사용했어요 ({reason})"
            for reason, scenes in self._scenes_by_reason.items()
        ]
