"""LAYA_GUARD_ALERT_CMD: a human hears about an outage once, and a hook never waits for it.

The command is a real shell command that writes into a temp file; nothing here talks to a notifier.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from laya_guard import alerts  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A private state file (it sits next to the log) and an alert command that appends to `out`."""
    monkeypatch.setenv("LAYA_GUARD_LOG_PATH", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.delenv("LAYA_GUARD_ALERT_COOLDOWN", raising=False)
    out = tmp_path / "alerts.out"
    monkeypatch.setenv("LAYA_GUARD_ALERT_CMD", f'printf "%s|%s\\n" "$LAYA_GUARD_ALERT_KIND" "$LAYA_GUARD_ALERT_MESSAGE" >> "{out}"')
    return out


def _lines(out: Path, count: int, timeout: float = 10.0) -> list[str]:
    """The command runs detached, so wait (briefly) for it to have written."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = out.read_text().splitlines() if out.exists() else []
        if len(lines) >= count:
            return lines
        time.sleep(0.05)
    return out.read_text().splitlines() if out.exists() else []


def test_nothing_happens_without_a_command(tmp_path, monkeypatch):
    monkeypatch.setenv("LAYA_GUARD_LOG_PATH", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.delenv("LAYA_GUARD_ALERT_CMD", raising=False)
    assert alerts.maybe_alert("server-down") is False
    monkeypatch.setenv("LAYA_GUARD_ALERT_CMD", "   ")
    assert alerts.maybe_alert("server-down") is False
    assert not (tmp_path / "alerts.json").exists()


def test_the_command_gets_the_kind_and_message_in_its_environment(env):
    assert alerts.maybe_alert("server-down") is True
    kind, message = _lines(env, 1)[0].split("|", 1)
    assert kind == "server-down"
    assert "can't be reached" in message and "offline check" in message


def test_the_message_is_data_and_never_part_of_the_command_line(env, tmp_path):
    pwned = tmp_path / "pwned"
    assert alerts.maybe_alert("model-error", message=f"$(touch {pwned}) `touch {pwned}` ; touch {pwned}") is True
    line = _lines(env, 1)[0]
    assert line.startswith("model-error|$(touch ") and "`touch " in line      # printed verbatim, not executed
    assert not pwned.exists()


def test_one_alert_per_kind_until_the_cooldown_is_over(env, monkeypatch):
    monkeypatch.setenv("LAYA_GUARD_ALERT_COOLDOWN", "600")
    t = 1_000_000.0
    assert alerts.maybe_alert("server-down", now=t) is True
    assert alerts.maybe_alert("server-down", now=t + 1) is False
    assert alerts.maybe_alert("server-down", now=t + 599) is False
    assert alerts.maybe_alert("server-down", now=t + 600) is True
    assert [l.split("|")[0] for l in _lines(env, 2)] == ["server-down", "server-down"]


def test_kinds_have_their_own_cooldown(env):
    t = 1_000_000.0
    assert alerts.maybe_alert("server-down", now=t) is True
    assert alerts.maybe_alert("model-error", now=t) is True
    assert alerts.maybe_alert("server-down", now=t + 1) is False
    assert sorted(l.split("|")[0] for l in _lines(env, 2)) == ["model-error", "server-down"]


def test_the_cooldown_is_configurable_and_a_bad_value_falls_back(env, monkeypatch):
    monkeypatch.setenv("LAYA_GUARD_ALERT_COOLDOWN", "0")
    assert alerts.maybe_alert("server-down", now=5.0) is True
    assert alerts.maybe_alert("server-down", now=5.0) is True                 # no cooldown at all
    monkeypatch.setenv("LAYA_GUARD_ALERT_COOLDOWN", "soon")
    assert alerts.cooldown() == alerts.DEFAULT_COOLDOWN
    monkeypatch.setenv("LAYA_GUARD_ALERT_COOLDOWN", "-3")
    assert alerts.cooldown() == 0.0


def test_a_clock_that_went_backwards_does_not_silence_alerts_forever(env):
    assert alerts.maybe_alert("server-down", now=2_000_000.0) is True
    assert alerts.maybe_alert("server-down", now=1_000_000.0) is True


def test_an_unknown_kind_is_ignored(env):
    assert alerts.maybe_alert("disk-full") is False
    assert not env.exists()


def test_a_corrupt_state_file_is_replaced_not_fatal(env, tmp_path):
    (tmp_path / "alerts.json").write_text("{ not json")
    assert alerts.maybe_alert("server-down") is True
    assert "server-down" in json.loads((tmp_path / "alerts.json").read_text())


def test_a_slow_command_does_not_slow_the_caller(tmp_path, monkeypatch):
    monkeypatch.setenv("LAYA_GUARD_LOG_PATH", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.setenv("LAYA_GUARD_ALERT_CMD", "sleep 3")
    t0 = time.perf_counter()
    assert alerts.maybe_alert("server-down") is True
    assert time.perf_counter() - t0 < 1.0


def test_a_failing_command_or_unwritable_state_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("LAYA_GUARD_LOG_PATH", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.setenv("LAYA_GUARD_ALERT_CMD", "/no/such/notifier --now")
    assert alerts.maybe_alert("server-down") is True                          # started; the shell reports the failure
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("LAYA_GUARD_LOG_PATH", str(blocker / "sub" / "verdicts.jsonl"))
    assert alerts.maybe_alert("server-down") is False                         # no state directory: skipped quietly


def _check_offline(tmp_path, command: str, **extra):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), LAYA_GUARD_LOG_PATH=str(tmp_path / "verdicts.jsonl"),
               LAYA_GUARD_ALERT_CMD=command, **extra)
    env.pop("LAYA_GUARD_ALERT_COOLDOWN", None)
    return subprocess.run([sys.executable, "-m", "laya_guard.cli", "check", "--offline", "--json", "ls -la"],
                          capture_output=True, text=True, env=env, timeout=60)


def test_the_offline_fallback_alerts_once_and_keeps_its_verdict(tmp_path):
    out = tmp_path / "alerts.out"
    cmd = f'printf "%s\\n" "$LAYA_GUARD_ALERT_KIND" >> "{out}"'
    first = _check_offline(tmp_path, cmd)
    second = _check_offline(tmp_path, cmd)
    assert first.returncode == 0 and json.loads(first.stdout)["action"] == "allow"
    assert second.returncode == 0
    assert _lines(out, 1) == ["server-down"]
    time.sleep(0.5)
    assert out.read_text().splitlines() == ["server-down"]                    # the second hook run was within the cooldown


def test_a_broken_alert_command_does_not_change_what_a_hook_decides(tmp_path):
    for command in ("/no/such/notifier", "exit 7"):
        p = _check_offline(tmp_path, command, LAYA_GUARD_FAIL="closed")
        assert p.returncode == 0 and json.loads(p.stdout)["action"] == "allow"      # `ls -la` is on the safe list


def test_the_server_alerts_when_the_model_failed_but_not_in_mock_mode(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    monkeypatch.setenv("LAYA_GUARD_LOG_PATH", str(tmp_path / "verdicts.jsonl"))
    from laya_guard import server
    from laya_guard.guard import Verdict
    sent = []
    monkeypatch.setattr(alerts, "maybe_alert", lambda kind, *a, **k: sent.append(kind) or True)

    def serve(verdict):
        monkeypatch.setattr(server, "judge", lambda *a, **k: verdict)
        server._judge_and_log("ls", source="tool")

    serve(Verdict("allow", 0.1, "general", "ok", verdict_source="model"))
    serve(Verdict("block", 0.9, "rm", "no", verdict_source="regex"))
    serve(Verdict("allow", 0.1, "general", "ok", mock=True, verdict_source="heuristic"))
    assert sent == []
    serve(Verdict("allow", 0.1, "general", "model raised", verdict_source="heuristic"))
    assert sent == ["model-error"]
