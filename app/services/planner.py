"""대본 → 씬 기획 (Gemini)."""

import logging

from google import genai
from google.genai import types

from ..config import Settings
from ..errors import api_error_code, is_retryable
from ..retry import with_retry
from ..schemas import MAX_SCENES, ScenePlan, ShortsPlan

SYSTEM_INSTRUCTION = """\
당신은 조회수가 높은 유튜브 쇼츠를 만드는 전문 기획자입니다.
사용자의 대본을 9:16 세로 쇼츠 영상의 씬(Scene) 목록으로 분해합니다.

규칙:
- 대본의 내용과 순서를 지키되, 첫 씬은 시청자를 붙잡는 강한 훅으로 만드세요.
- 각 씬의 narration은 한국어로 소리 내어 읽었을 때 약 3~5초(대략 15~35자) 분량입니다.
- subtitle_text는 화면에 띄울 짧은 한국어 자막입니다. narration의 핵심만 25자 이내로 요약하세요.
- visual_prompt는 이미지 생성 AI용 영어 묘사입니다. 피사체, 배경, 구도, 조명, 색감을 구체적으로 쓰고,
  이미지 안에 글자·자막·로고가 들어가지 않게 하세요. 모든 씬의 화풍은 일관되게 유지하세요.
- scene_id는 1부터 시작하는 연속된 정수입니다.
- 씬은 최소 1개, 최대 {max_scenes}개입니다.
"""


logger = logging.getLogger(__name__)


class PlanningError(RuntimeError):
    pass


def normalize_plan(plan: ShortsPlan) -> ShortsPlan:
    """LLM 출력을 정리합니다. 빈 씬 제거, scene_id 재부여, 씬 수 제한."""
    scenes: list[ScenePlan] = []
    for scene in plan.scenes:
        narration = scene.narration.strip()
        if not narration:
            continue
        scenes.append(
            ScenePlan(
                scene_id=len(scenes) + 1,
                narration=narration,
                visual_prompt=scene.visual_prompt.strip() or narration,
                subtitle_text=scene.subtitle_text.strip() or narration,
            )
        )
        if len(scenes) == MAX_SCENES:
            break
    if not scenes:
        raise PlanningError("기획 결과에 유효한 씬이 없습니다.")
    return ShortsPlan(title=plan.title.strip() or "Untitled", scenes=scenes)


class GeminiPlanner:
    def __init__(self, client: genai.Client, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def plan(self, script: str, tone: str) -> ShortsPlan:
        prompt = f"영상 분위기: {tone}\n\n[대본]\n{script}"
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION.format(max_scenes=MAX_SCENES),
            response_mime_type="application/json",
            response_schema=ShortsPlan,
            temperature=0.7,
        )

        async def call(model: str) -> ShortsPlan:
            response = await self._client.aio.models.generate_content(
                model=model, contents=prompt, config=config
            )
            if isinstance(response.parsed, ShortsPlan):
                return response.parsed
            if not response.text:
                raise PlanningError("LLM이 빈 응답을 반환했습니다.")
            return ShortsPlan.model_validate_json(response.text)

        models = [self._settings.llm_model]
        if self._settings.llm_fallback_model and self._settings.llm_fallback_model not in models:
            models.append(self._settings.llm_fallback_model)

        for i, model in enumerate(models):
            try:
                plan = await with_retry(
                    lambda m=model: call(m), attempts=self._settings.max_retries, what=f"씬 기획({model})"
                )
                return normalize_plan(plan)
            except Exception as exc:
                # 과부하·일시 오류이거나 모델이 내려간 경우(404)에만 예비 모델로 넘어갑니다.
                # 키 오류 같은 문제는 예비 모델로도 해결되지 않으므로 바로 알립니다.
                if i == len(models) - 1 or not (is_retryable(exc) or api_error_code(exc) == 404):
                    raise
                logger.warning("씬 기획 모델 %s 실패, 예비 모델 %s로 전환: %s", model, models[i + 1], exc)
        raise AssertionError("unreachable")
