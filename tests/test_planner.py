import pytest

from app.schemas import MAX_SCENES, ScenePlan, ShortsPlan
from app.services.planner import PlanningError, normalize_plan


def scene(i, narration="말"):
    return ScenePlan(scene_id=i, narration=narration, visual_prompt=" ", subtitle_text="")


def test_normalize_renumbers_and_fills_blanks():
    plan = normalize_plan(ShortsPlan(title=" t ", scenes=[scene(7), scene(7, "  "), scene(3)]))
    assert [s.scene_id for s in plan.scenes] == [1, 2]
    assert plan.title == "t"
    assert plan.scenes[0].subtitle_text == "말"
    assert plan.scenes[0].visual_prompt == "말"


def test_normalize_caps_scene_count():
    plan = normalize_plan(ShortsPlan(title="t", scenes=[scene(i) for i in range(50)]))
    assert len(plan.scenes) == MAX_SCENES


def test_normalize_rejects_empty():
    with pytest.raises(PlanningError):
        normalize_plan(ShortsPlan(title="t", scenes=[scene(1, "")]))


def test_env_values_that_are_only_comments_count_as_empty(tmp_path, monkeypatch):
    from app.config import Settings

    # 실행 환경에 실제 키가 있으면 .env보다 우선하므로 지운다
    for name in ("GEMINI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=   # 여기에 키\nPEXELS_API_KEY=     # https://www.pexels.com/api/\nPIXABAY_API_KEY=abc\n")
    s = Settings(_env_file=env)
    assert (s.gemini_api_key, s.pexels_api_key, s.pixabay_api_key) == (None, "", "abc")


def test_normalize_cleans_stock_excludes():
    s = ScenePlan(
        scene_id=1, narration="말", visual_prompt="v", subtitle_text="s",
        stock_query="carom billiards table",
        stock_exclude=[" Pool ", "pool", "snooker", "billiards hall", ""],
    )
    plan = normalize_plan(ShortsPlan(title="t", scenes=[s]))
    # 중복·빈 값 제거, 검색어와 겹치는 단어("billiards")가 든 제외어는 빼서 모든 사진이 걸러지지 않게 함
    assert plan.scenes[0].stock_exclude == ["pool", "snooker"]


def test_plan_schema_converts_to_gemini_schema():
    from google.genai import Client, _transformers

    schema = _transformers.t_schema(Client(api_key="x")._api_client, ShortsPlan)
    scene_props = schema.properties["scenes"].items.properties
    assert scene_props["stock_exclude"].items.type == "STRING"


class _HangingModels:
    """정해진 모델은 응답 없이 멈추고, 나머지는 씬 하나짜리 기획을 돌려주는 가짜 Gemini."""

    def __init__(self, hang: set[str]) -> None:
        self.hang, self.calls = hang, []

    async def generate_content(self, model, contents, config):
        import asyncio
        from types import SimpleNamespace

        self.calls.append(model)
        if model in self.hang:
            await asyncio.sleep(3600)
        plan = ShortsPlan(title="t", scenes=[scene(1, "물 한 잔")])
        return SimpleNamespace(parsed=plan, text=plan.model_dump_json())


def _planner(hang):
    from types import SimpleNamespace

    from app.config import Settings
    from app.services.planner import GeminiPlanner

    models = _HangingModels(hang)
    settings = Settings(_env_file=None, llm_timeout_sec=0.2, max_retries=1, llm_model="main", llm_fallback_model="backup")
    return GeminiPlanner(SimpleNamespace(aio=SimpleNamespace(models=models)), settings), models


@pytest.mark.asyncio
async def test_planner_switches_to_fallback_when_main_model_hangs():
    planner, models = _planner(hang={"main"})
    plan = await planner.plan("대본", "calm")
    assert plan.scenes[0].narration == "물 한 잔"
    assert models.calls == ["main", "backup"]


@pytest.mark.asyncio
async def test_planner_gives_up_with_clear_error_when_every_model_hangs():
    import asyncio

    planner, models = _planner(hang={"main", "backup"})
    async with asyncio.timeout(5):  # 무한 대기하지 않음
        with pytest.raises(PlanningError, match="0.2초 안에"):
            await planner.plan("대본", "calm")
    assert models.calls == ["main", "backup"]
