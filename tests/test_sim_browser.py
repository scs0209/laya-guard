"""The simulation view in a real headless Chrome: the page mounts, the tour runs, a scenario is judged and labelled,
the stations and replay controls work, a recorded call opens in Calls, picking works in the 3D scene, and nothing
logs a console error. Once with WebGL and once without it (the text fallback).

Skipped when node (22+, for its built-in WebSocket), a Chrome/Chromium or uvicorn isn't installed."""
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "tests" / "js" / "browser_smoke.mjs"


def _chrome():
    for candidate in (os.environ.get("LAYA_TEST_CHROME"), shutil.which("google-chrome"), shutil.which("google-chrome-stable"),
                      shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("chrome"),
                      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _node22():
    node = shutil.which("node")
    if not node:
        return None
    out = subprocess.run([node, "-e", "console.log(typeof WebSocket)"], capture_output=True, text=True).stdout.strip()
    return node if out == "function" else None


NODE, CHROME = _node22(), _chrome()
UVICORN = importlib.util.find_spec("uvicorn") is not None          # `laya-guard serve` runs on it
pytestmark = pytest.mark.skipif(not (NODE and CHROME and UVICORN), reason="needs node 22+, Chrome or Chromium, and uvicorn")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    home = tmp_path_factory.mktemp("sim-home")
    log = home / "verdicts.jsonl"
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(30):
        action = ("allow", "allow", "allow", "review", "block")[i % 5]
        via = {"allow": "model", "review": "model", "block": "regex"}[action]
        rows.append({"ts": (now - timedelta(minutes=40 - i)).isoformat(timespec="seconds"), "action": action, "risk": 0.1 if action == "allow" else 0.9,
                     "policy_id": {"allow": "general", "review": "mass-delete", "block": "pipe-to-shell"}[action], "reason": "r", "mock": False,
                     "source": "tool", "latency_ms": 3.0 + i, "tool_len": 20 + i, "tool_sha": hashlib.sha256(str(i).encode()).hexdigest()[:16], "via": via})
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "PYTHONPATH": str(ROOT / "src"), "LAYA_GUARD_MOCK": "1", "LAYA_GUARD_LOG_PATH": str(log)}
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
        yield {"url": f"http://127.0.0.1:{port}/dashboard", "log": log, "before": log.read_text(), "port": port}
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def drive(server, mode):
    p = subprocess.run([NODE, str(SMOKE), CHROME, server["url"], mode], capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout.strip().splitlines()[-1])


def check_common(server, r):
    assert not r.get("failure"), r.get("failure")
    assert r["errors"] == [], r["errors"]
    s = r["steps"]
    assert s["tour"] == {"shown": True, "hiddenAfter": True, "remembered": "done"}
    kpis = dict(k.split(":") for k in s["mounted"]["kpis"])
    assert kpis["allow"] == "18" and kpis["review"] == "6" and kpis["block"] == "6"       # the seeded log, nothing else
    sc = s["scenario"]
    assert sc["badge"] == "MOCK EVALUATOR" and sc["verdict"] == "Blocked" and "pipe-to-shell" in sc["facts"] and "the evaluator returns none" in sc["facts"]
    assert "Remote script piped into a shell" in sc["firstRow"] and "MOCK EVALUATOR" in sc["firstRow"] and sc["chip"] == "Showing: MOCK EVALUATOR"
    assert sc["inspector"]["waiting"] is False and "Remote script piped into a shell" in sc["inspector"]["text"] and "Decided by" in sc["inspector"]["text"]
    assert "not measured" in s["simulation"]["facts"] and "Nothing is evaluated" in s["simulation"]["note"]
    assert s["policyGate"] == {"title": "Policy gate", "policies": 12} and s["policy"] in ("destructive-fs", "pipe-to-shell", "exfiltration", "secrets-leak", "mass-delete",
        "prompt-injection", "payment-action", "remote-exec", "priv-escalation", "persistence", "auth-bypass", "credential-read")
    assert s["pending"]["controlsOff"] is True and s["pending"]["chip"] == "JUDGING…" and s["pending"]["held"] is True
    assert s["pending"]["stuckAt"] == "policy" and s["pending"]["busy"] is True
    assert s["afterPending"] == {"source": "mock", "action": "allow", "controlsOn": True}
    assert s["down"]["error"].startswith("The guard service didn't answer (connection refused)") and "not an evaluation" in s["down"]["error"]
    assert s["answeredWithError"].startswith("The scenario couldn't be evaluated (bad token)") and "didn't answer" not in s["answeredWithError"]
    assert s["down"]["source"] == "simulation" and s["down"]["badge"] == "SIMULATION DATA" and "Typically" in s["down"]["decided"] and s["down"]["busy"] is False
    assert s["retryNotice"].startswith("The guard service didn't answer, so the pipeline can't be loaded")
    assert s["retry"]["before"] != s["retry"]["after"] and s["errorsAfterRetry"] == []          # the retried view really runs
    assert s["replay"] == {"playing": True, "frozen": True, "stepped": True, "restarted": True}
    assert s["recorded"]["title"].startswith("Recorded call #") and s["recorded"]["badge"] == "RECORDED"
    assert s["calls"]["view"] == "calls" and s["calls"]["detail"]
    assert all(s["views"].values()), s["views"]
    assert server["log"].read_text() == server["before"], "a scenario run must not be written to the verdict log"


def test_the_view_works_in_a_real_browser_with_webgl(server):
    r = drive(server, "webgl")
    if r["steps"].get("mounted", {}).get("mode") != "webgl":
        r = drive(server, "webgl")                       # a software GL context can fail to start on a busy machine: once more
    if r["steps"].get("mounted", {}).get("mode") != "webgl":
        pytest.skip("this Chrome has no WebGL")
    check_common(server, r)
    assert r["steps"]["camera"] == {"zoomed": True, "panned": True, "rotated": True}
    assert r["steps"]["keyboard"] == {"view": "simulation"}
    pick = r["steps"]["picking"]
    assert pick["n"] > 50 and pick["tokens"] >= 24
    assert pick["afterTokenClick"].startswith("Recorded call #") and pick["ring"] is True


def test_the_view_works_without_webgl_through_the_text_fallback(server):
    r = drive(server, "nowebgl")
    assert r["steps"]["mounted"]["mode"] == "fallback", r["steps"]["mounted"]
    check_common(server, r)
