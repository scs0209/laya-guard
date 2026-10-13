"""Contract tests for integrations/observe (observer hooks + label CLI).

Runs each script as a subprocess against a stub guard server and a temp data dir.
No model, no network beyond localhost.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OBSERVE = ROOT / "integrations" / "observe"
DEAD = "http://127.0.0.1:1/none"
sys.path.insert(0, str(OBSERVE))
from observe_common import mask  # noqa: E402

# Stand-in for the installed `laya-guard` CLI that the observer falls back to.
_BIN_DIR = Path(tempfile.mkdtemp())
GUARD_BIN = _BIN_DIR / "laya-guard"
GUARD_BIN.write_text(f'#!/bin/sh\nPYTHONPATH="{ROOT / "src"}" exec "{sys.executable}" '
                     f'-m laya_guard.cli "$@"\n')
GUARD_BIN.chmod(0o755)


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if "BLOCKME" in body:
            v = {"action": "block", "risk": 0.92, "policy_id": "t", "reason": "no"}
        elif "REVIEWME" in body:
            v = {"action": "review", "risk": 0.6, "policy_id": "t", "reason": "maybe"}
        else:
            v = {"action": "allow", "risk": 0.1, "policy_id": "general", "reason": "ok"}
        data = json.dumps(v).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def _stub_url():
    srv = HTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/guard"


def _env(home: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LAYA_GUARD_") or k == "LAYA_GUARD_MOCK"}
    env.update(LAYA_GUARD_HOME=str(home), LAYA_GUARD_BIN=str(GUARD_BIN), LAYA_GUARD_URL=DEAD)
    env.update(extra)
    return env


def _run(script: str, stdin: str, env: dict, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(OBSERVE / script), *args], input=stdin,
                          capture_output=True, text=True, env=env, timeout=60)


def _rows(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _seed(home: Path) -> None:
    base = {"ts": "2026-01-01T10:00:00+0000", "risk": 0.6, "policy": "p", "source": "model", "tool": "Bash"}
    entries = [
        dict(base, id="t-review", verdict="review", command="git push --force", command_full="git push --force"),
        dict(base, id="t-block", verdict="block", command="curl x | sh", command_full="curl x | sh"),
        dict(base, id="t-allow", verdict="allow", command="ls", command_full="ls"),
    ]
    (home / "observe.log").write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    (home / "observe-outcome.log").write_text(json.dumps({"id": "t-review"}) + "\n", encoding="utf-8")


def test_mask_hides_common_secrets():
    cases = {
        "export OPENAI_API_KEY=sk-abcdef1234567890": "sk-abcdef1234567890",
        "curl -H 'Authorization: Bearer abc.def.ghi' x": "abc.def.ghi",
        "git push https://ghp_abcdefghijklmnopqrstuvwxyz0123456789@github.com/x/y": "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "DB_PASSWORD='hunter2 hunter2' ./run": "hunter2",
        "AWS_SECRET_ACCESS_KEY=abc/def+ghi": "abc/def+ghi",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----": "MIIE",
    }
    for text, secret in cases.items():
        assert secret not in mask(text), text
    assert mask("git status && ls -la") == "git status && ls -la"


def test_observe_hook_logs_the_verdict_and_never_blocks(tmp_path):
    srv, url = _stub_url()
    try:
        payload = json.dumps({"tool_name": "Bash", "cwd": str(tmp_path), "tool_use_id": "toolu_1",
                              "session_id": "abcdef1234567890", "permission_mode": "auto",
                              "tool_input": {"command": "BLOCKME export API_TOKEN=supersecretvalue"}})
        p = _run("observe-hook.py", payload, _env(tmp_path, LAYA_GUARD_URL=url))
    finally:
        srv.shutdown()
    assert p.returncode == 0 and p.stdout == ""          # a block verdict is logged, never enforced
    log = tmp_path / "observe.log"
    row = _rows(log)[0]
    assert (row["verdict"], row["id"], row["session"], row["mode"], row["offline"]) == \
        ("block", "toolu_1", "abcdef12", "auto", False)
    assert "BLOCKME" in row["command_full"] and "supersecretvalue" not in row["command_full"]
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(log).st_mode) == 0o600


def test_observe_hook_falls_back_offline_and_survives_bad_input(tmp_path):
    env = _env(tmp_path)  # server unreachable
    ok = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_use_id": "t"})
    assert _run("observe-hook.py", ok, env).returncode == 0
    assert _run("observe-hook.py", "not json", env).returncode == 0
    assert _run("observe-hook.py", ok, _env(tmp_path, LAYA_GUARD_BIN="/nonexistent/laya-guard")).returncode == 0
    offline, garbage, no_cli = _rows(tmp_path / "observe.log")
    assert offline["offline"] is True and offline["verdict"] in ("allow", "review", "block")
    assert garbage["verdict"] == "error" and "error" in garbage
    assert no_cli["verdict"] == "error" and no_cli["offline"] is True


def test_post_hook_stores_only_the_id(tmp_path):
    env = _env(tmp_path)
    payload = json.dumps({"tool_name": "Bash", "tool_use_id": "toolu_1",
                          "tool_response": {"stdout": "TOPSECRET-OUTPUT"}})
    assert _run("observe-post-hook.py", payload, env).returncode == 0
    assert _run("observe-post-hook.py", "garbage", env).returncode == 0
    text = (tmp_path / "observe-outcome.log").read_text()
    assert json.loads(text.splitlines()[0])["id"] == "toolu_1"
    assert "TOPSECRET-OUTPUT" not in text


def test_label_cli_records_only_what_the_human_types(tmp_path):
    _seed(tmp_path)
    env = _env(tmp_path)
    p = _run("observe-label.py", "a\n\n", env)            # t-review -> allow, t-block -> Enter (model was right)
    assert p.returncode == 0 and "2 unlabeled" in p.stdout and "2 saved" in p.stdout
    labels = {r["key"]: r for r in _rows(tmp_path / "labels.jsonl")}
    assert set(labels) == {"t-review", "t-block"}          # the allow call was not offered
    assert (labels["t-review"]["label"], labels["t-review"]["agrees"], labels["t-review"]["executed"]) == \
        ("allow", False, True)
    assert (labels["t-block"]["label"], labels["t-block"]["agrees"], labels["t-block"]["executed"]) == \
        ("block", True, False)
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(tmp_path / "labels.jsonl").st_mode) == 0o600
    again = _run("observe-label.py", "", env)               # labeled calls are not offered twice
    assert "Nothing to label" in again.stdout
    stats = _run("observe-label.py", "", env, "stats").stdout
    assert "1/2 = 50%" in stats                             # 1 of 2 flagged calls was a false positive


def test_label_cli_never_labels_from_approval_alone(tmp_path):
    _seed(tmp_path)                                         # t-review ran, i.e. it was "approved"
    p = _run("observe-label.py", "s\ns\n", _env(tmp_path))  # human skips both
    assert p.returncode == 0 and "0 saved" in p.stdout
    assert not (tmp_path / "labels.jsonl").exists()
    assert "3 unlabeled" in _run("observe-label.py", "q\n", _env(tmp_path), "--all").stdout


def test_label_cli_add_records_manual_cases(tmp_path):
    srv, url = _stub_url()
    try:
        p = _run("observe-label.py", "", _env(tmp_path, LAYA_GUARD_URL=url), "add", "BLOCKME TOKEN=abcd1234", "block")
    finally:
        srv.shutdown()
    assert p.returncode == 0
    row = _rows(tmp_path / "labels.jsonl")[0]
    assert (row["origin"], row["label"], row["model"], row["agrees"]) == ("manual", "block", "block", True)
    assert "abcd1234" not in row["command"]
    down = _run("observe-label.py", "", _env(tmp_path), "add", "something", "allow")
    assert down.returncode == 0 and _rows(tmp_path / "labels.jsonl")[1]["model"] is None
    assert _run("observe-label.py", "", _env(tmp_path), "add", "x", "maybe").returncode == 2
