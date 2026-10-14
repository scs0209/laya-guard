"""The AI scenario routes, driven through the real agent bridge (a fake `claude` child process), end to end."""
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("LAYA_GUARD_MOCK", "1")
import fake_claude  # noqa: E402
from laya_guard import agentbridge, aisim, guard  # noqa: E402
from test_aisim import CLEAN_PKG, EVIL_PKG, PLAN, act  # noqa: E402

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

SUMMARY = {"headline": "laya-guard stopped it.", "narrative": "The script was blocked.", "assessment": "Caught.", "consequences": {}, "lessons": []}
STORY = [PLAN, act(type="read_file", path="package.json"), act(type="write_file", path="package.json", content=EVIL_PKG),
         act(type="run_command", command="npm test"), act(type="finish", outcome="blocked"), SUMMARY]


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from laya_guard.server import app
    log = tmp_path / "verdicts.jsonl"
    monkeypatch.setenv("LAYA_GUARD_LOG_PATH", str(log))
    monkeypatch.setattr(guard, "_MOCK", True)
    monkeypatch.setattr(aisim, "MANAGER", aisim.Manager())
    fake = fake_claude.install(tmp_path / "bin", STORY)
    for k, v in fake["env"].items():
        monkeypatch.setenv(k, v)
    agentbridge._cache.clear()
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    token = re.search(r'name="laya-token" content="([^"]+)"', client.get("/dashboard").text).group(1)

    def post(path, body=None, headers=None):
        h = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:8787", "X-Laya-Token": token, **(headers or {})}
        return client.post(path, content=json.dumps(body if body is not None else {}), headers={k: v for k, v in h.items() if v})

    def events(run_id, after=0):
        r = client.get(f"/api/sim/ai/runs/{run_id}/events?after={after}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
        return [json.loads(line) for line in r.text.splitlines() if line and json.loads(line)["type"] != "ping"]

    return type("A", (), {"get": staticmethod(client.get), "post": staticmethod(post), "events": staticmethod(events), "fake": fake, "log": log,
                          "client": client})


def start(api, **kw):
    assert api.post("/api/sim/ai/consent", {"agent": "claude-code"}).status_code == 200
    r = api.post("/api/sim/ai/runs", {"prompt": "An agent edits package.json so npm test runs a suspicious command.", "agent": "claude-code", **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_status_lists_the_agent_and_what_a_run_is_held_to(api):
    s = api.get("/api/sim/ai/status").json()
    a = s["agents"][0]
    assert (a["id"], a["found"], a["logged_in"], a["consent"]) == ("claude-code", True, True, False) and a["version"].startswith("9.9.9")
    assert "--tools" in a["inert"] and "someone@example.com" not in json.dumps(s)
    assert s["limits"]["max_steps"] == 20 and s["limits"]["run_seconds"] == 600 and len(s["examples"]) == 3 and s["review_policies"] == ["ask", "deny", "approve"]
    assert api.get("/api/sim/ai/status", headers={"host": "evil.example:8787"}).status_code == 403


def test_nothing_starts_without_the_users_consent(api):
    r = api.post("/api/sim/ai/runs", {"prompt": "x", "agent": "claude-code"})
    assert r.status_code == 409 and "consent" in r.json()["detail"]
    assert api.fake["calls"]() == []                                      # the agent was never even started
    api.post("/api/sim/ai/consent", {"agent": "claude-code"})
    assert api.get("/api/sim/ai/status").json()["agents"][0]["consent"] is True
    api.post("/api/sim/ai/consent", {"agent": "claude-code", "allow": False})
    assert api.get("/api/sim/ai/status").json()["agents"][0]["consent"] is False
    assert api.post("/api/sim/ai/consent", {"agent": "nobody"}).status_code == 404


def test_a_full_run_through_the_real_bridge_streams_the_story(api):
    run_id = start(api)
    events = api.events(run_id)
    t = [e["type"] for e in events]
    assert t[0] == "run.started" and t[-1] == "run.finished" and events[0]["agent"]["name"] == "Claude Code"
    assert [e["action"]["type"] for e in events if e["type"] == "ai.action"] == ["read_file", "write_file", "run_command", "finish"]
    verdict = next(e for e in events if e["type"] == "guard.result")["result"]
    assert verdict["verdict"]["action"] == "block" and verdict["verdict"]["policy_id"] == "pipe-to-shell" and verdict["mode"] == "mock"
    assert next(e for e in events if e["type"] == "summary")["facts"]["verdicts"]["block"] == 1
    assert api.events(run_id, after=3)[0]["seq"] == 4                       # resumable
    # the agent was started the inert way, every time, and sent only the scenario and the virtual state
    calls = api.fake["calls"]()
    assert len(calls) == 6 and all(c["argv"][c["argv"].index("--tools") + 1] == "" for c in calls)
    assert all("someone@example.com" not in c["prompt"] and str(api.fake["bin"].parent) not in c["prompt"] for c in calls)
    assert not api.log.exists()                                             # not a call: nothing was logged
    assert api.get("/api/stats?window=all").json()["totals"]["total"] == 0


def test_runs_can_be_listed_and_replayed_after_they_end(api):
    run_id = start(api)
    api.events(run_id)
    listed = api.get("/api/sim/ai/runs").json()["runs"]
    assert listed[0]["id"] == run_id and listed[0]["finished"] is True and listed[0]["agent"] == "Claude Code"
    assert [e["type"] for e in api.events(run_id)][-1] == "run.finished"


@pytest.mark.parametrize("body,detail", [({"prompt": ""}, "describe"), ({"prompt": "x" * 2001}, "under 2000"), ({"prompt": "x", "agent": "nobody"}, "unknown agent"),
                                         ({"prompt": "x", "mode": "turbo"}, "mode"), ({"prompt": "x", "review_policy": "maybe"}, "review_policy")])
def test_bad_requests_are_refused_with_a_reason(api, body, detail):
    api.post("/api/sim/ai/consent", {"agent": "claude-code"})
    r = api.post("/api/sim/ai/runs", {"agent": "claude-code", **body})
    assert r.status_code == 400 and detail in r.json()["detail"]


def test_an_agent_that_isnt_logged_in_or_installed_is_reported_before_a_run(api, monkeypatch, tmp_path):
    api.fake["set"](logged_in=False)
    agentbridge._cache.clear()
    api.post("/api/sim/ai/consent", {"agent": "claude-code"})
    r = api.post("/api/sim/ai/runs", {"prompt": "x"})
    assert r.status_code == 400 and "isn't logged in" in r.json()["detail"]
    monkeypatch.setenv("LAYA_GUARD_CLAUDE_BIN", str(tmp_path / "gone"))
    monkeypatch.setenv("PATH", str(tmp_path))
    agentbridge._cache.clear()
    r = api.post("/api/sim/ai/runs", {"prompt": "x"})
    assert r.status_code == 400 and "isn't on PATH" in r.json()["detail"]
    assert api.fake["calls"]() == []


def test_an_agent_failure_reaches_the_page_as_an_error_event(api):
    api.fake["set"](replies=[{"_error": "Credit balance is too low"}])
    events = api.events(start(api))
    assert events[-1]["type"] == "run.error" and "Credit balance is too low" in events[-1]["message"]


def test_the_control_routes_pause_step_and_stop_a_run(api):
    api.fake["set"](replies=STORY, sleep=0.4)
    run_id = start(api, mode="step")
    deadline = time.time() + 15
    while time.time() < deadline and "run.paused" not in [e["type"] for e in aisim.MANAGER.get(run_id).events]:
        time.sleep(0.05)
    assert api.post(f"/api/sim/ai/runs/{run_id}/control", {"action": "step"}).status_code == 200
    while time.time() < deadline and [e["type"] for e in aisim.MANAGER.get(run_id).events].count("step.end") < 1:
        time.sleep(0.05)
    assert [e["type"] for e in aisim.MANAGER.get(run_id).events].count("ai.action") == 1
    assert api.post(f"/api/sim/ai/runs/{run_id}/control", {"action": "stop"}).status_code == 200
    assert api.events(run_id)[-1]["type"] == "run.stopped"
    assert api.post(f"/api/sim/ai/runs/{run_id}/control", {"action": "explode"}).status_code == 400
    assert api.post("/api/sim/ai/runs/nope/control", {"action": "stop"}).status_code == 404
    assert api.get("/api/sim/ai/runs/nope/events").status_code == 404


def test_the_post_routes_need_every_write_check_and_the_streams_a_loopback_host(api):
    api.post("/api/sim/ai/consent", {"agent": "claude-code"})
    body = {"prompt": "x"}
    for path in ("/api/sim/ai/runs", "/api/sim/ai/consent", "/api/sim/ai/runs/x/control"):
        b = {"agent": "claude-code"} if "consent" in path else {"action": "stop"} if "control" in path else body
        assert api.post(path, b, headers={"X-Laya-Token": "wrong"}).status_code == 403, path
        assert api.post(path, b, headers={"Origin": "http://evil.example"}).status_code == 403, path
        assert api.post(path, b, headers={"Content-Type": "text/plain"}).status_code == 415, path
        assert api.post(path, b, headers={"host": "evil.example:8787"}).status_code == 403, path
    run_id = start(api)
    api.events(run_id)
    assert api.get(f"/api/sim/ai/runs/{run_id}/events", headers={"host": "evil.example:8787"}).status_code == 403
    assert api.get("/api/sim/ai/runs", headers={"host": "evil.example:8787"}).status_code == 403
    assert api.fake["calls"]() and all(c["prompt"] for c in api.fake["calls"]())


def test_a_model_the_agent_does_not_offer_is_refused(api):
    api.post("/api/sim/ai/consent", {"agent": "claude-code"})
    r = api.post("/api/sim/ai/runs", {"prompt": "x", "agent": "claude-code", "model": "--dangerously-skip-permissions"})
    assert r.status_code == 400 and "model" in r.json()["detail"]
    assert api.fake["calls"]() == []


def test_a_review_answer_names_the_step_it_answers(api, monkeypatch):
    from laya_guard import simulation
    real = simulation.judge_command

    def held(command, **kw):
        r = real(command, **kw)
        r["verdict"] = {**r["verdict"], "action": "review", "policy_id": "mass-delete", "reason": "held", "risk": 0.55}
        return r
    monkeypatch.setattr(simulation, "judge_command", held)
    api.fake["set"](replies=[PLAN, act(type="run_command", command="npm test"), act(type="finish"), SUMMARY])
    run_id = start(api)
    for _ in range(100):
        if api.get("/api/sim/ai/runs").json()["runs"][0]["state"] == "waiting-review":
            break
        time.sleep(0.1)
    assert api.post(f"/api/sim/ai/runs/{run_id}/control", {"action": "review", "decision": "approve", "step": 9}).status_code == 200
    time.sleep(0.8)
    assert api.get("/api/sim/ai/runs").json()["runs"][0]["state"] == "waiting-review"       # the answer to another step was ignored
    api.post(f"/api/sim/ai/runs/{run_id}/control", {"action": "review", "decision": "deny", "step": 1})
    evs = api.events(run_id)
    assert next(e for e in evs if e["type"] == "review.decision")["decision"] == "deny"


def test_a_server_that_exits_takes_the_agent_process_with_it(tmp_path):
    """The run's thread dies with the server, but the agent is its own process: stopping the server must end it too."""
    pytest.importorskip("uvicorn")
    import signal
    import socket
    import subprocess
    import urllib.request
    fake = fake_claude.install(tmp_path / "bin", [PLAN], sleep=60)
    home = tmp_path / "home"
    home.mkdir()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "PYTHONPATH": str(ROOT / "src"), "LAYA_GUARD_MOCK": "1",
           "LAYA_GUARD_LOG_PATH": str(home / "v.jsonl"), **fake["env"]}
    proc = subprocess.Popen([sys.executable, "-m", "laya_guard.cli", "serve", "--no-warmup", "--port", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pids = Path(str(fake["log"]) + ".pid")
    try:
        for _ in range(100):
            try:
                page = urllib.request.urlopen(f"http://127.0.0.1:{port}/dashboard", timeout=1).read().decode()
                break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("the server didn't start")
        token = re.search(r'name="laya-token" content="([^"]+)"', page).group(1)

        def post(path, body):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", "X-Laya-Token": token})
            return urllib.request.urlopen(req, timeout=5).read()
        post("/api/sim/ai/consent", {"agent": "claude-code"})
        post("/api/sim/ai/runs", {"prompt": "an agent in a repo", "agent": "claude-code"})
        for _ in range(100):
            if pids.exists() and pids.read_text().split():
                break
            time.sleep(0.1)
        child = int(pids.read_text().split()[-1])
        os.kill(child, 0)                                      # the agent is alive and busy
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
        for _ in range(50):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            pytest.fail("the agent process outlived the server")
    finally:
        if proc.poll() is None:
            proc.kill()
        for line in (pids.read_text().split() if pids.exists() else []):
            try:
                os.kill(int(line), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
