"""The demo script adapter: always ready, no model or login, and its answers fit the engine's schemas."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from laya_guard import agentbridge, aisim  # noqa: E402


def demo():
    agentbridge._cache.clear()
    return agentbridge.get("demo")


def test_demo_is_always_ready_without_login_key_or_cost():
    info = demo().detect()
    assert (info.id, info.found, info.logged_in) == ("demo", True, True)
    assert [a.id for a in agentbridge.detect_all(fresh=True)][-1] == "demo"


def test_demo_plan_names_the_run_after_the_situation_and_validates():
    prompt = "PHASE: interpret\n\nThe user describes a situation:\n<<<\nPoisoned PR\n>>>\n\nTurn it into a plan."
    text = demo().complete("sys", prompt).text
    plan, errors = aisim.validate_plan(json.loads(text))
    assert errors == [] and plan["title"].startswith("Demo: Poisoned PR")
    assert "package.json" in plan["files"] and len(plan["files"]) <= 14


def test_demo_steps_read_then_test_then_finish_and_validate():
    world = aisim.World(files=dict(agentbridge.DemoScript._FILES))
    s1 = json.loads(demo().complete("sys", "PHASE: act  STEP: 1 of 6\n\nNothing has happened yet.").text)
    s2 = json.loads(demo().complete("sys", "PHASE: act  STEP: 2 of 6\n\n1. read_file package.json").text)
    s3 = json.loads(
        demo().complete("sys", "PHASE: act  STEP: 3 of 6\n\n1. read_file package.json\n2. run_command npm test -> blocked").text)
    assert (s1["type"], s2["type"], s3["type"]) == ("read_file", "run_command", "finish")
    for step in (s1, s2, s3):
        _, errors = aisim.validate_action(step, world)
        assert errors == [], step
    assert "stopped" in s3["outcome"]


def test_demo_summary_has_the_required_shape():
    closing = json.loads(demo().complete("sys", "PHASE: summary\n\nThe simulation has ended.").text)
    assert closing["headline"] and closing["narrative"]
