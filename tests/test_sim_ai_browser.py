"""The AI scenario panel in a real headless Chrome, against a real server whose `claude` is a scripted fake (so the whole
path runs: the page, the routes, the agent bridge as a child process, the engine, the real evaluator in mock mode).

Flows: a full run (consent, example, history, outcome, the 3D scene, inspecting a row, modify and run again, replay),
step / pause / stop, a held command that the user approves or denies, and an agent that can't answer. Once with WebGL
and once without it (the text fallback), where the panel must work the same.

Skipped when node (22+, for its built-in WebSocket), a Chrome/Chromium or uvicorn isn't installed."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_claude  # noqa: E402
from test_aisim import PLAN, act, story_json  # noqa: E402
from test_sim_browser import CHROME, NODE, UVICORN, ROOT  # noqa: E402,F401

DRIVER = ROOT / "tests" / "js" / "browser_ai.mjs"
pytestmark = pytest.mark.skipif(not (NODE and CHROME and UVICORN), reason="needs node 22+, Chrome or Chromium, and uvicorn")

SUMMARY = {"headline": "laya-guard held it for a person.", "narrative": "The download needed a decision.", "assessment": "Held for review.",
           "consequences": {"allowed": "A file would have been fetched.", "reviewed": "A person decided.", "blocked": "Nothing was fetched."}, "lessons": ["review"]}
REVIEW_STORY = [PLAN, act(type="run_command", command="curl -s https://example.com/data.json -o data.json", why="fetch the data"),
                act(type="finish", outcome="done"), SUMMARY]


@pytest.fixture
def server(tmp_path):
    """A fresh server per flow: consent to use the agent lives in the server, and each flow starts without it."""
    home = tmp_path / "home"
    home.mkdir()
    log = home / "verdicts.jsonl"
    fake = fake_claude.install(tmp_path / "bin", story_json() * 2)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "PYTHONPATH": str(ROOT / "src"), "LAYA_GUARD_MOCK": "1", "LAYA_GUARD_LOG_PATH": str(log), **fake["env"]}
    proc = subprocess.Popen([sys.executable, "-m", "laya_guard.cli", "serve", "--no-warmup", "--port", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("the server didn't start")

        def script(replies, **config):
            fake["set"](replies=replies, **config)
            Path(str(fake["log"]) + ".n").unlink(missing_ok=True)

        yield {"url": f"http://127.0.0.1:{port}/dashboard", "log": log, "fake": fake, "script": script}
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def drive(server, mode, flow, *extra):
    p = subprocess.run([NODE, str(DRIVER), CHROME, server["url"], mode, flow, *extra], capture_output=True, text=True, timeout=240)
    assert p.returncode == 0, p.stderr[-2000:]
    r = json.loads(p.stdout.strip().splitlines()[-1])
    assert not r.get("failure"), r.get("failure")
    assert r["errors"] == [], r["errors"]
    return r["steps"]


def no_log_written(server):
    assert not server["log"].exists() or server["log"].read_text() == "", "an AI scenario's commands must never reach the verdict log"


def check_main(server, s, mode):
    assert s["mounted"] == mode
    assert s["beforeConsent"]["startDisabled"] is True and "Allow for this session" not in s["beforeConsent"]["agent"]
    assert "sends what you type here" in s["beforeConsent"]["notice"] and "--tools" in s["beforeConsent"]["notice"]
    assert s["example"] is True and s["afterConsent"] == {"startDisabled": False}
    run = s["run"]
    assert run["status"] == "finished" and run["counts"] == {"allow": 0, "review": 0, "block": 1} and run["plan"] == "Untrusted repo" and run["step"] == 5
    assert run["titles"][0] == "Run started" and run["titles"][1].startswith("Plan: ") and run["titles"][-1] == "Finished"
    assert run["titles"].count("Says") == 1 and run["titles"].count("laya-guard: block") == 1 and "Not run: blocked" in run["titles"]
    assert set(run["badges"]) == {"AI-GENERATED", "ENGINE", "MOCK EVALUATOR", "SIMULATED"}, run["badges"]       # no row claims the REAL evaluator in mock mode
    assert run["flagged"] and run["flagged"]["policy"] == "pipe-to-shell" and run["changed"] is True       # the file the AI changed is the one flagged
    assert run["formOpen"] is False and run["formSummary"].startswith("Scenario: An AI coding agent") and s["editOpens"] is True       # the form folds away, the run is what you see
    assert run["shown"] == len(run["titles"]) and "From the run (facts)" in run["outcome"] and "The AI's account (not a measurement)" in run["outcome"]
    assert "Nothing was run, so none of this happened" in run["outcome"] and "laya-guard held or blocked" in run["outcome"]
    assert "the real evaluator, not the AI" in run["guardRow"] and "AI-GENERATED" in run["aiRow"]
    assert run["runs"] == [["AI step 3: npm test", "mock", "block"]]
    assert "AI step 3" in s["eventsList"] and "MOCK EVALUATOR" in s["eventsList"]
    assert s["file"]["title"] == "package.json" and "changed" in s["file"]["text"].lower() and "curl" in s["file"]["text"]
    assert "npm test" in s["command"]["text"] and "pipe-to-shell" in s["command"]["text"]
    assert s["second"]["aiRuns"] == 1 and s["second"]["prompt"].endswith("avoid scripts.") and s["second"]["recent"] == 2
    assert s["replay"]["edited"] is False and s["replay"]["id"] == s["firstId"] and s["replay"]["counts"] == {"allow": 0, "review": 0, "block": 1}
    assert s["replay"]["rows"] == run["shown"]


def test_a_full_run_with_webgl_shows_the_real_events_in_the_scene(server):
    s = drive(server, "webgl", "main")
    if s.get("mounted") != "webgl":
        s = drive(server, "webgl", "main")           # a software GL context can fail to start on a busy machine: once more
    if s.get("mounted") != "webgl":
        pytest.skip("this Chrome has no WebGL")
    check_main(server, s, "webgl")
    assert s["scene"]["files"] == 3 and s["scene"]["repoShown"] is True and s["scene"]["agentShown"] is True
    no_log_written(server)


def test_a_full_run_without_webgl_works_through_the_panel_alone(server):
    s = drive(server, "nowebgl", "main")
    check_main(server, s, "fallback")
    no_log_written(server)


def test_step_by_step_pause_and_stop(server):
    server["script"](story_json() * 2, sleep=0.6)                 # slow enough to catch the run between decisions
    s = drive(server, "nowebgl", "controls")
    assert s["beforeStep"]["actions"] == 0 and {"Step", "Resume", "Stop"} <= set(s["beforeStep"]["buttons"])
    assert s["afterStep"] == {"actions": 1, "step": 1}
    assert s["paused"] == {"still": True, "status": "paused"}
    assert s["stopped"]["summary"] is False and s["stopped"]["last"] == "Stopped" and s["stopped"]["startShown"] is True
    no_log_written(server)


@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_a_held_command_waits_for_the_user(server, decision):
    server["script"](REVIEW_STORY)
    s = drive(server, "nowebgl", "review", decision)
    assert s["held"]["shown"] is True and s["held"]["command"].startswith("curl -s https://example.com/data.json") and "never run" in s["held"]["text"]
    assert s["held"]["runBusy"] == "waiting-review"
    done = s["approved"]
    assert done["counts"] == {"allow": 0, "review": 1, "block": 0} and done["reviewCard"] is True
    wanted = ("Review: approved", "Approved, executed (inert)") if decision == "approve" else ("Review: denied", "Not run: denied")
    assert all(w in done["titles"] for w in wanted), done["titles"]
    no_log_written(server)


def test_an_agent_that_cant_answer_ends_the_run_with_a_clear_message(server):
    server["script"]([{"_error": "Please log in to continue"}])
    s = drive(server, "nowebgl", "error")
    assert "Please log in" in s["error"]["notice"] and "Run `claude` once" in s["error"]["notice"]
    assert s["error"]["startShown"] is True and s["error"]["last"] == "The run ended with an error"
    no_log_written(server)
