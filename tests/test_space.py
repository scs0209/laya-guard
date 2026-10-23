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


def test_forged_addresses_still_meet_the_overall_limit_and_idle_visitors_are_forgotten(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(trial.time, "monotonic", lambda: clock[0])
    limits = trial.RateLimit(per_minute=3, all_per_minute=5, max_clients=100)
    assert [limits.allow(f"forged-{i}") for i in range(7)] == [True] * 5 + [False] * 2
    clock[0] += 61                                           # a minute later the idle visitors are gone
    assert limits.allow("new") and list(limits.hits) == ["new"]
    small = trial.RateLimit(per_minute=3, all_per_minute=1000, max_clients=3)
    for i in range(10):
        small.allow(f"c{i}")
    assert list(small.hits) == ["c7", "c8", "c9"]           # never more than max_clients, oldest dropped first


@pytest.mark.parametrize("agent", [[], {}, 1, None])
def test_an_agent_that_isnt_a_string_is_refused(agent):
    with pytest.raises(trial.Refused) as e:
        trial.judge_command("ls", agent)
    assert e.value.status == 400


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
    big = b'{"command": "' + b"x" * (trial.MAX_BODY + 10) + b'"}'
    assert c.post("/api/judge", content=big, headers={"content-type": "application/json"}).status_code == 413
    assert c.post("/api/judge", json={"command": "ls", "agent": []}).status_code == 400
