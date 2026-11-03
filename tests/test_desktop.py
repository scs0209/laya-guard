"""Desktop computer-use calls: judged by what is on the screen where they click (desktop.py), with the screen lookup
faked (a function, or a stand-in helper script for the end-to-end hook). No model, no real screen. Secrets are fake."""
import json
import os
os.environ["LAYA_GUARD_MOCK"] = "1"
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from laya_guard import cli, desktop, guard, server  # noqa: E402
from laya_guard.desktop import judge_desktop_call  # noqa: E402
from test_file_calls import _hook  # noqa: E402

CU = "mcp__computer-use__computer"
TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temporary HOME (no user rules, no helper built), a log of its own, mock mode."""
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "verdicts.jsonl"
    for k, v in {"HOME": str(home), "LAYA_GUARD_MOCK": "1", "LAYA_GUARD_LOG_PATH": str(log)}.items():
        monkeypatch.setenv(k, v)
    for k in ("XDG_CONFIG_HOME", "LAYA_GUARD_PROTECT", "LAYA_GUARD_AX", "LAYA_GUARD_AX_SCALE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(guard, "_MOCK", True)
    return home, log


def screen(paths=(), labels=()):
    """A fake lookup that sees one element everywhere, and records what it was asked."""
    asked = []

    def look(*args):
        asked.append(args)
        return {"trusted": True, "paths": list(paths), "labels": list(labels)}
    look.asked = asked
    return look


def _a(inp, look=None, tool=CU):
    v = judge_desktop_call(tool, inp, "/tmp", look)
    return v.action, v.policy_id


def test_what_counts_as_a_desktop_call(env):
    assert desktop.parse(CU, {"action": "left_click", "coordinate": [1, 2]}) == ("left_click", {"action": "left_click", "coordinate": [1, 2]})
    assert desktop.parse("mcp__computer__double_click", {"x": 1, "y": 2})[0] == "double_click"
    assert desktop.parse(CU, {"action": "warp", "coordinate": [1, 2]})[0] == "warp"
    assert desktop.parse("mcp__slack__send", {"text": "hi"}) is None
    assert desktop.parse("mcp__Claude_Browser__computer", {"action": "left_click", "coordinate": [1, 2]}) is None
    assert judge_desktop_call("Bash", {"command": "ls"}) is None


def test_a_click_on_a_protected_file_follows_protect_yaml(env):
    home, _ = env
    look = screen(paths=[f"{home}/.ssh/id_rsa"])
    assert _a({"action": "double_click", "coordinate": [100, 200]}, look) == ("review", "protected-path")
    assert look.asked == [("at", "100", "200")]
    assert _a({"action": "left_click_drag", "start_coordinate": [5, 6], "coordinate": [9, 9]}, look)[1] == "protected-path"
    assert look.asked[-1] == ("at", "5", "6")
    assert _a({"action": "double_click", "coordinate": [1, 2]}, screen(paths=["/tmp/notes.txt"])) == ("allow", "desktop")


def test_a_click_without_coordinates_looks_under_the_pointer(env):
    home, _ = env
    look = screen(paths=[f"{home}/.aws/credentials"])
    assert _a({"action": "left_click"}, look)[1] == "protected-path"
    assert look.asked == [("pointer",)]


def test_a_click_on_a_risky_button(env):
    assert _a({"action": "left_click", "coordinate": [1, 2]}, screen(labels=["Open"]))[0] == "allow"
    assert _a({"action": "left_click", "coordinate": [1, 2]}, screen(labels=["Move to Trash"]))[0] == "review"
    assert _a({"action": "left_click", "coordinate": [1, 2]}, screen(labels=["Delete account"]))[0] == "review"
    assert _a({"action": "left_click", "coordinate": [1, 2]}, screen(labels=["결제하기"]))[0] == "review"


def test_when_the_screen_cant_be_read_a_click_is_asked_about(env):
    assert _a({"action": "left_click", "coordinate": [1, 2]}, lambda *a: None) == ("review", "desktop")
    assert _a({"action": "left_click", "coordinate": [1, 2]}) == ("review", "desktop")        # no helper built
    assert _a({"action": "screenshot"}) == ("allow", "desktop")


def test_keys_that_open_the_selection(env):
    home, _ = env
    look = screen(paths=[f"{home}/.ssh/config"])
    assert _a({"action": "key", "text": "cmd+o"}, look)[1] == "protected-path"
    assert look.asked == [("selection",)]
    assert _a({"action": "key", "text": "Return"}, look)[1] == "protected-path"
    assert _a({"action": "key", "text": "cmd+c"}, look) == ("allow", "desktop")
    assert _a({"action": "key", "text": "Return"}, screen(paths=["/tmp/a"])) == ("allow", "desktop")
    assert _a({"action": "key", "text": "Return"}, lambda *a: None)[0] == "review"


def test_typing(env):
    assert _a({"action": "type", "text": "hello"}) == ("allow", "desktop")
    assert _a({"action": "type", "text": TOKEN}) == ("block", "secrets-leak")
    assert _a({"action": "type", "text": "4111 1111 1111 1111"}) == ("review", "desktop")
    assert _a({"action": "type", "text": "~/.ssh/"}) == ("review", "protected-path")       # Go to Folder


def test_unknown_actions_and_scale(env, monkeypatch):
    assert _a({"action": "warp", "coordinate": [1, 2]}) == ("review", "desktop")
    monkeypatch.setenv("LAYA_GUARD_AX_SCALE", "2")
    look = screen()
    _a({"action": "left_click", "coordinate": [10, 20]}, look)
    assert look.asked == [("at", "20", "40")]


def _fake_helper(tmp_path, paths):
    exe = tmp_path / "laya-guard-ax"
    exe.write_text("#!/bin/sh\necho '" + json.dumps({"trusted": True, "paths": paths, "labels": []}) + "'\n")
    exe.chmod(0o755)
    return exe


def test_the_route_and_the_offline_check(env, tmp_path, monkeypatch, capsys):
    import io
    home, _ = env
    monkeypatch.setenv("LAYA_GUARD_AX", str(_fake_helper(tmp_path, [f"{home}/.ssh/id_rsa"])))
    inp = {"action": "double_click", "coordinate": [1, 2]}
    v = server.guard(server.GuardReq(tool_call="x", tool_name=CU, tool_input=inp, agent="claude-code"))
    assert (v.action, v.policy_id) == ("review", "protected-path")
    sys.stdin = io.StringIO(json.dumps(inp))
    try:
        code = cli.main(["check", "--offline", "--json", "--tool-name", CU, "--input-json"])
    finally:
        sys.stdin = sys.__stdin__
    assert code == 0 and json.loads(capsys.readouterr().out)["policy_id"] == "protected-path"


def test_the_offline_hook_asks_before_a_protected_file_is_opened(env, tmp_path):
    home, log = env
    helper = _fake_helper(tmp_path, [f"{home}/.ssh/id_rsa"])
    p = _hook({"tool_name": CU, "tool_input": {"action": "double_click", "coordinate": [1, 2]}},
              home=home, LAYA_GUARD_LOG_PATH=str(log), LAYA_GUARD_AX=str(helper))
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert p.returncode == 0 and out["permissionDecision"] == "ask" and "protected-path" in out["permissionDecisionReason"]
    blocked = _hook({"tool_name": CU, "tool_input": {"action": "type", "text": TOKEN}}, home=home, LAYA_GUARD_LOG_PATH=str(log))
    assert blocked.returncode == 2
    plain = _hook({"tool_name": CU, "tool_input": {"action": "screenshot"}}, home=home, LAYA_GUARD_LOG_PATH=str(log))
    assert plain.returncode == 0 and plain.stdout.strip() == ""


def test_the_desktop_check_command(env, tmp_path, monkeypatch, capsys):
    assert cli.main(["desktop", "check", "pointer"]) == 1                    # no helper
    monkeypatch.setenv("LAYA_GUARD_AX", str(_fake_helper(tmp_path, ["/tmp/a"])))
    assert cli.main(["desktop", "check", "10", "20"]) == 0
    assert json.loads(capsys.readouterr().out)["paths"] == ["/tmp/a"]


def test_a_blocked_call_tells_the_person_at_the_screen(env, monkeypatch):
    from laya_guard import alerts
    started = []

    class P:
        def __init__(self, cmd, **kw):
            started.append((cmd, kw.get("env")))

        def wait(self):
            return 0
    monkeypatch.setattr(alerts.subprocess, "Popen", P)
    monkeypatch.setattr(alerts.sys, "platform", "darwin")
    monkeypatch.delenv("LAYA_GUARD_ALERT_CMD", raising=False)
    monkeypatch.setenv("LAYA_GUARD_NOTIFY", "1")
    server.guard(server.GuardReq(tool_call="x", tool_name=CU, tool_input={"action": "type", "text": TOKEN}))
    (cmd, _), = started
    assert cmd[0] == "osascript" and cmd[-2].startswith("Blocked: ") and "desktop action" in cmd[-1]
    assert TOKEN not in " ".join(cmd)                                    # the reason, never what was typed
    server.guard(server.GuardReq(tool_call="x", tool_name=CU, tool_input={"action": "screenshot"}))
    assert len(started) == 1                                             # nothing for an allow
    monkeypatch.setenv("LAYA_GUARD_ALERT_CMD", "notify-me")
    server.guard(server.GuardReq(tool_call="x", tool_name="mcp__Claude_Browser__computer",
                                 tool_input={"action": "type", "text": TOKEN}))
    cmd, environ = started[-1]
    assert cmd == "notify-me" and environ["LAYA_GUARD_ALERT_KIND"] == "blocked" and "browser action" in environ["LAYA_GUARD_ALERT_MESSAGE"]
    monkeypatch.setenv("LAYA_GUARD_NOTIFY", "0")
    server.guard(server.GuardReq(tool_call="x", tool_name=CU, tool_input={"action": "type", "text": TOKEN}))
    assert len(started) == 2
