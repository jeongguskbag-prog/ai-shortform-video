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
