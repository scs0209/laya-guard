"""laya-guard audit: reads synthetic Claude Code and Codex transcripts in a temporary HOME, never the real ones."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from laya_guard import audit, cli, guard


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("LAYA_GUARD_PROTECT", raising=False)
    monkeypatch.setattr(guard, "_MOCK", True)         # verdicts from the rules alone, the same on every machine
    return h


def _ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat().replace("+00:00", "Z")


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def claude_session(home, name, commands, cwd="/work/app", when=None):
    rows = []
    for i, cmd in enumerate(commands):
        rows.append({"type": "assistant", "timestamp": when or _ago(minutes=5), "cwd": cwd,
                     "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash",
                                              "input": {"command": cmd}}]}})
        rows.append({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                                              "is_error": i % 2 == 1}]}})
    rows.append({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}})
    _write(home / ".claude" / "projects" / "-work-app" / f"{name}.jsonl", rows)


def codex_session(root, name, commands, cwd="/work/svc"):
    rows = [{"type": "session_meta", "timestamp": _ago(minutes=9), "payload": {"cwd": cwd}}]
    for i, cmd in enumerate(commands):
        rows.append({"type": "response_item", "timestamp": _ago(minutes=8),
                     "payload": {"type": "function_call", "name": "exec_command", "call_id": f"c{i}",
                                 "arguments": json.dumps({"cmd": cmd})}})
        rows.append({"type": "response_item", "payload": {"type": "function_call_output", "call_id": f"c{i}"}})
    _write(root / "sessions" / "2026" / "10" / "23" / f"rollout-{name}.jsonl", rows)


def test_it_reads_each_agents_shell_calls_with_where_and_how_they_ended(home):
    claude_session(home, "s1", ["ls -la", "git status"])
    codex_session(home / ".codex", "r1", ["npm test"])
    report = audit.run(since=None, home=home)
    assert [(c.agent, c.command, c.cwd, c.status) for c in report.calls] == [
        ("claude-code", "ls -la", "/work/app", "ok"), ("claude-code", "git status", "/work/app", "error"),
        ("codex", "npm test", "/work/svc", "ok")]
    assert report.sessions == {"claude-code": 1, "codex": 1}


def test_codex_home_is_honoured(home, tmp_path, monkeypatch):
    elsewhere = tmp_path / "codex-home"
    codex_session(elsewhere, "r1", ["git log"])
    monkeypatch.setenv("CODEX_HOME", str(elsewhere))
    assert [c.command for c in audit.run(("codex",), home=home).calls] == ["git log"]


def test_what_the_hook_would_hold_is_reported_worst_first(home):
    claude_session(home, "s1", ["ls", "git push --force origin main", "rm -rf ~", "rm -rf ~"])
    report = audit.run(since=None, home=home)
    assert report.counts == {"allow": 1, "review": 1, "block": 2}
    assert [f.verdict.action for f in report.findings] == ["block", "block", "review"]
    text = audit.render(report, home=home)
    assert "block 2   review 1   allow 1" in text
    assert "×2" in text                                   # the same command held twice is one line
    assert "rm -rf ~" in text and "git push --force" in text


def test_redact_leaves_the_commands_out(home):
    claude_session(home, "s1", ["rm -rf ~"])
    report = audit.run(since=None, home=home)
    assert "rm -rf ~" not in audit.render(report, redact=True, home=home)
    assert "command" not in audit.to_json(report, redact=True)["findings"][0]
    assert audit.to_json(report)["findings"][0]["command"] == "rm -rf ~"


def test_since_leaves_older_calls_out(home):
    claude_session(home, "old", ["rm -rf ~"], when=_ago(days=3))
    claude_session(home, "new", ["ls"])
    assert [c.command for c in audit.run(since=audit.parse_since("24h"), home=home).calls] == ["ls"]
    assert len(audit.run(since=None, home=home).calls) == 2


@pytest.mark.parametrize("text", ["7", "d", "-1d", "1w", "abc"])
def test_since_rejects_what_it_cannot_read(text):
    with pytest.raises(ValueError):
        audit.parse_since(text)


def test_each_agent_is_judged_the_way_its_hook_judges(home, monkeypatch):
    seen = []
    monkeypatch.setattr(audit, "judge", lambda cmd, source, cwd, ask: seen.append((source, ask)) or
                        guard.judge(cmd, source=source, cwd=cwd, ask=ask))
    claude_session(home, "s1", ["ls"])
    codex_session(home / ".codex", "r1", ["pwd"])
    audit.run(since=None, home=home)
    # Claude Code's hook sends no source and asks about a review; Codex's says "tool" and lets a review through
    assert seen == [("user", True), ("tool", False)]


def test_a_broken_transcript_line_is_skipped(home):
    claude_session(home, "s1", ["ls"])
    path = home / ".claude" / "projects" / "-work-app" / "s1.jsonl"
    path.write_text("{not json\n" + path.read_text())
    assert [c.command for c in audit.run(since=None, home=home).calls] == ["ls"]


def test_no_transcripts_is_said_plainly(home):
    text = audit.render(audit.run(since=None, home=home), home=home)
    assert "No shell commands found" in text


def test_the_cli_prints_the_report_and_writes_nothing(home, capsys, tmp_path, monkeypatch):
    log = tmp_path / "v.jsonl"
    monkeypatch.setenv("LAYA_GUARD_LOG_PATH", str(log))
    claude_session(home, "s1", ["rm -rf ~"])
    assert cli.main(["audit", "--since", "all"]) == 0
    assert "BLOCK" in capsys.readouterr().out
    assert cli.main(["audit", "--since", "all", "--json", "--redact"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"] == {"block": 1}
    assert not log.exists()
    assert cli.main(["audit", "--since", "soon"]) == 1
