"""The web trial (space/): only the judge is exposed, input is bounded, visitors are rate-limited.

trial.py holds the logic and is tested here without Gradio; app.py, the gr.Server wiring, only when Gradio is installed.
"""
import sys
from pathlib import Path

import pytest

from laya_guard import guard

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "space"))
import trial  # noqa: E402


@pytest.fixture(autouse=True)
def mock(monkeypatch):
    monkeypatch.setattr(guard, "_MOCK", True)


def test_a_command_is_judged_with_its_steps():
    r = trial.judge_command("d=~; rm -rf $d", "codex")
    assert (r["action"], r["policy_id"], r["verdict_source"]) == ("block", "destructive-fs", "regex")
    assert [s["text"] for s in r["steps"] if s["stage"] == "segment"] == ["d=~", "rm -rf $d"]
    assert trial.judge_command("ls -la")["action"] == "allow"


def test_each_agent_is_judged_the_way_its_hook_judges(monkeypatch):
    seen = []
    real = trial.judge_traced
    monkeypatch.setattr(trial, "judge_traced", lambda cmd, source, emit, ask: seen.append((source, ask)) or real(
        cmd, source=source, emit=emit, ask=ask))
    for agent in ("claude-code", "cursor", "codex", "gemini-cli"):
        trial.judge_command("ls", agent)
    assert seen == [("user", True), ("tool", True), ("tool", False), ("tool", False)]


@pytest.mark.parametrize("command, agent, status", [("x" * 4001, "claude-code", 422), ("  ", "claude-code", 422),
                                                    (None, "claude-code", 422), ("ls", "other", 400)])
def test_bad_input_is_refused(command, agent, status):
    with pytest.raises(trial.Refused) as e:
        trial.judge_command(command, agent)
    assert e.value.status == status


def test_visitors_are_rate_limited_separately():
    limits = trial.RateLimit(per_minute=3)
    assert [limits.allow("a") for _ in range(4)] == [True, True, True, False]
    assert limits.allow("b")
    assert trial.client_of({"x-forwarded-for": "203.0.113.7, 10.0.0.1"}, "10.0.0.2") == "203.0.113.7"
    assert trial.client_of({}, "10.0.0.2") == "10.0.0.2"


def test_the_app_serves_the_page_and_the_judge_only(monkeypatch):
    pytest.importorskip("gradio")
    pytest.importorskip("spaces")
    from fastapi.testclient import TestClient
    monkeypatch.setattr(guard, "warmup", lambda: False)
    import app
    c = TestClient(app.app)
    assert c.get("/").status_code == 200 and "laya-guard trial" in c.get("/").text
    r = c.post("/api/judge", json={"command": "d=~; rm -rf $d", "agent": "codex"}).json()
    assert r["action"] == "block"
    assert c.post("/api/judge", json={"command": "ls", "agent": "other"}).status_code == 400
    assert c.post("/api/judge", content=b"[1]", headers={"content-type": "application/json"}).status_code == 400
