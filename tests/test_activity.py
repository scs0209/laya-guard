"""The opt-in file activity log: the shell path parser, recording, the server route, `check`, the hook and `activity`.

Everything runs with HOME, the config directory and both logs in a temporary place. No model, no network.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from laya_guard import activity, cli, server  # noqa: E402
from laya_guard.activity import shell_accesses  # noqa: E402
from laya_guard.guard import Verdict  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "data" / "file-activity.jsonl"
    for k, v in {"HOME": str(home), "LAYA_GUARD_MOCK": "1", "LAYA_GUARD_FILE_ACTIVITY_PATH": str(log),
                 "LAYA_GUARD_LOG_PATH": str(tmp_path / "verdicts.jsonl")}.items():
        monkeypatch.setenv(k, v)
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "LAYA_GUARD_PROTECT", "LAYA_GUARD_FILE_ACTIVITY"):
        monkeypatch.delenv(k, raising=False)
    return home, log


def events(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


ALLOW = Verdict(action="allow", risk=0.0, policy_id="general", reason="ok")


# --- the shell parser --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("command, expected", [
    ("cat ~/.ssh/id_rsa", [("read", "~/.ssh/id_rsa")]),
    ("cat a.txt b.txt | grep foo > out.txt", [("read", "a.txt"), ("read", "b.txt"), ("write", "out.txt")]),
    ("rm -rf build dist", [("delete", "build"), ("delete", "dist")]),
    ("cp -r src/ backup/ && mv old.txt new.txt",
     [("read", "src/"), ("write", "backup/"), ("delete", "old.txt"), ("write", "new.txt")]),
    ("echo hi >> notes.md 2>/dev/null", [("write", "notes.md")]),
    ("echo hi 2>&1 | tee -a log.txt", [("write", "log.txt")]),
    ("head -n 5 file.py; tail -20 other.py", [("read", "file.py"), ("read", "other.py")]),
    ("grep -rn TODO src/ README.md", [("read", "src/"), ("read", "README.md")]),
    ("grep -e foo -e bar a.txt", [("read", "a.txt")]),
    ("grep -f pats.txt data.log", [("read", "pats.txt"), ("read", "data.log")]),
    ("rg --regexp=x file.py", [("read", "file.py")]),
    ("FOO=1 sudo -E rm secret.txt", [("delete", "secret.txt")]),
    ("chmod 600 ~/.ssh/id_rsa", [("write", "~/.ssh/id_rsa")]),
    ("source .env && ./run.sh", [("read", ".env")]),
    ("(cd sub && rm junk.txt)", [("delete", "junk.txt")]),
    ("cat < input.txt", [("read", "input.txt")]),
    ("cat <<EOF > conf.yml\nrm -rf /\nEOF", [("write", "conf.yml")]),        # a heredoc body is text, not commands
])
def test_the_parser_reads_plain_forms(command, expected):
    assert shell_accesses(command) == expected


@pytest.mark.parametrize("command", [
    "cat $HOME/.ssh/id_rsa",            # the shell would still have to expand these: guessing would be wrong
    "cat *.pem",
    'rm -rf "$DIR"',
    "cat `which python`",
    'cat "unclosed',
    "git status && npm test",
    "python3 -c 'open(\"x\").read()'",
    "sed -i s/a/b/ file.txt",
    "curl -o out.bin https://example.com/x",
    "grep foo",                         # only a pattern, reading stdin
    "",
    "   ",
])
def test_the_parser_leaves_out_what_it_cannot_read(command):
    assert shell_accesses(command) == []


@pytest.mark.parametrize("command, expected", [
    ("grep -A 3 password .env", [("read", ".env")]),                    # 3 is the option's, password the pattern
    ("tail -n +5 f.log", [("read", "f.log")]),
    ("mkdir -m 700 d", [("write", "d")]),
    ("bat -l py main.py", [("read", "main.py")]),
    ("stat -f %z a.bin", [("read", "a.bin")]),
    ("xxd -l 16 a.bin", [("read", "a.bin")]),
    ("rg -g '*.py' TODO src", [("read", "src")]),
    ("cat <<< hi; cat <<EOF\nrm -rf /\nEOF", []),                       # a here-string and a heredoc on one line
])
def test_an_options_value_is_never_recorded_as_a_path(command, expected):
    assert shell_accesses(command) == expected


def test_a_path_after_cd_has_no_absolute_form(env):
    _, log = env
    activity.turn_on()
    _guard(tool_call="rm a.txt && cd sub && rm junk.txt && rm /tmp/x", tool_name="Bash", cwd="/work", agent="claude-code")
    got = [(e["path"], e["abs"]) for e in events(log)]
    assert got == [("a.txt", "/work/a.txt"), ("junk.txt", None), ("/tmp/x", "/tmp/x")]


def test_the_parser_never_raises_on_odd_input():
    for command in ["\x00\x01", "((((", "))))", "a;;;;b", "> ", "< ", "cat >", "rm", "cp onlyone", "x" * 50_000,
                    "cat " + "a " * 5000, "\\", "'", '"', "$(", "cat ${"]:
        assert isinstance(shell_accesses(command), list)
    assert shell_accesses(None) == []


# --- off until you turn it on ------------------------------------------------------------------------------------------

def test_nothing_is_recorded_until_it_is_turned_on(env):
    _, log = env
    activity.record_call(agent="claude-code", tool_name="Read", text="", paths=["a.txt"], cwd="/p", verdict=ALLOW)
    assert not log.exists() and not activity.enabled()
    activity.turn_on()
    assert activity.enabled()
    activity.record_call(agent="claude-code", tool_name="Read", text="", paths=["a.txt"], cwd="/p", verdict=ALLOW)
    assert len(events(log)) == 1
    activity.turn_off()
    activity.record_call(agent="claude-code", tool_name="Read", text="", paths=["b.txt"], cwd="/p", verdict=ALLOW)
    assert len(events(log)) == 1 and not activity.enabled()


def test_the_environment_can_force_it_either_way(env, monkeypatch):
    _, log = env
    monkeypatch.setenv("LAYA_GUARD_FILE_ACTIVITY", "1")
    assert activity.enabled()                                                     # no marker needed
    monkeypatch.setenv("LAYA_GUARD_FILE_ACTIVITY", "0")
    activity.turn_on()
    assert not activity.enabled()                                                 # the marker loses to an explicit off
    activity.record_call(agent="x", tool_name="Read", text="", paths=["a"], cwd="/p", verdict=ALLOW)
    assert not log.exists()


# --- what is recorded --------------------------------------------------------------------------------------------------

def _guard(**kw):
    return server.guard(server.GuardReq(**kw))


def test_a_file_tool_call_is_recorded_with_the_guards_answer(env):
    home, log = env
    activity.turn_on()
    _guard(tool_call="Read: ~/.ssh/id_rsa", tool_name="Read", paths=["~/.ssh/id_rsa"], cwd="/tmp", agent="claude-code")
    _guard(tool_call="Write: ~/.aws/credentials", tool_name="Write", paths=["~/.aws/credentials"], cwd="/tmp", agent="claude-code")
    _guard(tool_call="Edit: src/app.py", tool_name="Edit", paths=["src/app.py"], cwd="/work", agent="claude-code")
    read, blocked, edit = events(log)
    assert (read["agent"], read["tool_name"], read["op"], read["path"], read["source"], read["action"], read["policy_id"]) == (
        "claude-code", "Read", "read", "~/.ssh/id_rsa", "hook", "review", "protected-path")
    assert read["abs"] == str(home / ".ssh" / "id_rsa")
    assert (blocked["op"], blocked["action"]) == ("write", "block")                # an attempt that was stopped says so
    assert (edit["op"], edit["abs"], edit["action"]) == ("write", "/work/src/app.py", "allow")


def test_a_bash_call_is_recorded_from_the_paths_its_command_names(env):
    _, log = env
    activity.turn_on()
    _guard(tool_call="cat notes.txt > out.txt", tool_name="Bash", cwd="/work", agent="claude-code")
    got = [(e["op"], e["path"], e["abs"], e["source"]) for e in events(log)]
    assert got == [("read", "notes.txt", "/work/notes.txt", "shell"), ("write", "out.txt", "/work/out.txt", "shell")]


def test_other_tools_and_calls_without_a_tool_name_are_not_recorded(env):
    _, log = env
    activity.turn_on()
    _guard(tool_call="cat notes.txt")                                              # no tool_name: not an agent's tool call
    _guard(tool_call="mcp__x__tool notes.txt", tool_name="mcp__x__tool", paths=["notes.txt"])
    _guard(tool_call="Read: x", tool_name="Read")                                  # a file tool that named no path
    assert not log.exists()


def test_the_log_is_for_the_owner_only_and_holds_no_content(env):
    _, log = env
    activity.turn_on()
    _guard(tool_call="Write: a.txt SECRET-CONTENT", tool_name="Write", paths=["a.txt"], cwd="/p")
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert stat.S_IMODE(log.parent.stat().st_mode) == 0o700                         # the directory is new, so ours to restrict
    assert "SECRET-CONTENT" not in log.read_text()


def test_a_log_that_cannot_be_written_never_breaks_the_call(env, tmp_path, monkeypatch):
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    monkeypatch.setenv("LAYA_GUARD_FILE_ACTIVITY_PATH", str(blocker / "sub" / "log.jsonl"))   # the parent is a file
    activity.turn_on()
    v = _guard(tool_call="Read: src/a.py", tool_name="Read", paths=["src/a.py"], cwd="/p")
    assert v.action == "allow"


def test_a_config_directory_that_cannot_be_looked_at_never_changes_the_verdict(env, monkeypatch, capsys):
    def denied():
        raise PermissionError("config directory not readable")
    monkeypatch.setattr(activity, "enabled", denied)
    assert cli.main(["check", "--offline", "--json", "--tool-name", "Bash", "--agent", "claude-code", "rm -rf /"]) == 2
    assert json.loads(capsys.readouterr().out)["action"] == "block"
    assert _guard(tool_call="rm -rf /", tool_name="Bash", agent="claude-code").action == "block"


def test_long_labels_and_paths_are_cut_so_one_call_stays_small(env):
    _, log = env
    activity.turn_on()
    huge = "x" * 1_000_000
    long_path = "p" * (activity.MAX_PATH_CHARS - 10)
    activity.record_call(agent=huge, tool_name="Read", text="", paths=[long_path] * 50, cwd="/" + huge, verdict=ALLOW)
    got = events(log)
    assert len(got) == 50 and log.stat().st_size < 50 * (activity.MAX_PATH_CHARS + 1000)
    assert len(got[0]["agent"]) == activity.MAX_LABEL_CHARS and got[0]["abs"] is None


def test_a_call_that_would_pass_the_cap_is_not_written(env, monkeypatch):
    _, log = env
    activity.turn_on()
    monkeypatch.setattr(activity, "MAX_BYTES", 1000)
    activity.record_call(agent="a", tool_name="Read", text="", paths=[f"f{i}" for i in range(50)], cwd="/p", verdict=ALLOW)
    assert not log.exists() or log.stat().st_size <= 1000


def test_a_path_with_a_line_separator_is_read_back(env):
    _, log = env
    activity.turn_on()
    activity.record_call(agent="a", tool_name="Read", text="", paths=["odd\u2028name.txt", "b.txt"], cwd="/p", verdict=ALLOW)
    assert [e["path"] for e in activity.load_events()] == ["odd\u2028name.txt", "b.txt"]


def test_the_activity_command_never_prints_an_escape_sequence(env, capsys):
    activity.turn_on()
    activity.record_call(agent="evil\x1b[2J", tool_name="Read", text="", paths=["a\x1b]0;x\x07.txt"], cwd="/p", verdict=ALLOW)
    assert cli.main(["activity"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "\\u001b" in out


def test_recording_stops_past_the_size_cap(env, monkeypatch):
    _, log = env
    activity.turn_on()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * 100)
    monkeypatch.setattr(activity, "MAX_BYTES", 10)
    activity.record_call(agent="a", tool_name="Read", text="", paths=["a"], cwd="/p", verdict=ALLOW)
    assert log.read_text() == "x" * 100


def test_one_call_never_records_more_than_the_cap(env):
    _, log = env
    activity.turn_on()
    paths = [f"f{i}" for i in range(500)]
    activity.record_call(agent="a", tool_name="Read", text="", paths=paths, cwd="/p", verdict=ALLOW)
    assert len(events(log)) == activity.MAX_PER_CALL


# --- any agent: accesses and an explicit command ------------------------------------------------------------------------

def test_an_adapters_accesses_are_recorded_and_judged_for_any_agent(env):
    home, log = env
    activity.turn_on()
    v = _guard(tool_call="read_file({...})", tool_name="read_file", agent="gemini-cli", cwd="/work",
               accesses=[{"op": "read", "path": "~/.ssh/id_rsa"}])
    assert (v.action, v.policy_id) == ("review", "protected-path")
    _guard(tool_call="write_file({...})", tool_name="write_file", agent="gemini-cli", cwd="/work",
           accesses=[{"op": "write", "path": "src/a.py"}, {"op": "delete", "path": "old.py"}])
    got = [(e["agent"], e["tool_name"], e["op"], e["abs"], e["source"], e["action"]) for e in events(log)]
    assert got == [
        ("gemini-cli", "read_file", "read", str(home / ".ssh" / "id_rsa"), "hook", "review"),
        ("gemini-cli", "write_file", "write", "/work/src/a.py", "hook", "allow"),
        ("gemini-cli", "write_file", "delete", "/work/old.py", "hook", "allow")]


def test_the_shell_command_is_read_from_command_not_from_a_wrapper(env):
    _, log = env
    activity.turn_on()
    # the tool_call is the wrapper some agents send; parsing it would invent paths
    _guard(tool_call='run_shell_command({"command": "rm old.log"})', tool_name="run_shell_command", agent="gemini-cli",
           command="rm old.log", cwd="/work")
    _guard(tool_call="bash", tool_name="bash", agent="opencode", command="cat a.txt > b.txt", cwd="/work")
    got = [(e["agent"], e["op"], e["path"], e["source"]) for e in events(log)]
    assert got == [("gemini-cli", "delete", "old.log", "shell"), ("opencode", "read", "a.txt", "shell"),
                   ("opencode", "write", "b.txt", "shell")]


def test_a_wrapper_without_a_command_records_nothing(env):
    _, log = env
    activity.turn_on()
    _guard(tool_call='run_shell_command({"command": "rm old.log"})', tool_name="run_shell_command", agent="gemini-cli")
    assert not log.exists()


def test_check_takes_a_shell_flag_and_accesses_for_any_agent(env, capsys):
    _, log = env
    activity.turn_on()
    cli.main(["check", "--offline", "--json", "--agent", "opencode", "--tool-name", "bash", "--shell", "cat notes.txt"])
    cli.main(["check", "--offline", "--json", "--agent", "gemini-cli", "--tool-name", "read_file",
              "--access", "read", "~/.ssh/id_rsa", "ignored"])
    capsys.readouterr()
    got = [(e["agent"], e["tool_name"], e["op"], e["path"], e["source"]) for e in events(log)]
    assert got == [("opencode", "bash", "read", "notes.txt", "shell"),
                   ("gemini-cli", "read_file", "read", "~/.ssh/id_rsa", "hook")]
    assert events(log)[1]["action"] == "review"


# --- laya-guard check and the hook ------------------------------------------------------------------------------------

def test_the_offline_check_records_a_hook_call_but_not_a_command_someone_typed(env, capsys):
    _, log = env
    activity.turn_on()
    cli.main(["check", "--offline", "--json", "--tool-name", "Write", "--path", "~/.ssh/authorized_keys",
              "--agent", "claude-code", "x"])
    capsys.readouterr()
    (e,) = events(log)
    assert (e["agent"], e["tool_name"], e["op"], e["action"], e["source"]) == ("claude-code", "Write", "write", "block", "hook")
    cli.main(["check", "--offline", "--json", "cat secrets.txt"])                   # no --tool-name: someone typing
    capsys.readouterr()
    assert len(events(log)) == 1


def test_the_hook_records_through_the_offline_fallback(env, tmp_path):
    home, log = env
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "laya-guard"
    stub.write_text(f'#!/bin/sh\nPYTHONPATH="{ROOT / "src"}" exec "{sys.executable}" -m laya_guard.cli "$@"\n')
    stub.chmod(0o755)
    environ = dict(os.environ, HOME=str(home), LAYA_GUARD_URL="http://127.0.0.1:1/none", LAYA_GUARD_BIN=str(stub),
                   LAYA_GUARD_FAIL="open", LAYA_GUARD_MOCK="1", LAYA_GUARD_FILE_ACTIVITY="1")
    environ.pop("XDG_CONFIG_HOME", None)
    for payload in ({"tool_name": "Read", "tool_input": {"file_path": "~/.ssh/id_rsa"}, "cwd": "/work"},
                    {"tool_name": "Bash", "tool_input": {"command": "rm old.log"}, "cwd": "/work"}):
        subprocess.run(["bash", str(ROOT / "guard-hook.sh")], input=json.dumps(payload), capture_output=True, text=True,
                       env=environ, timeout=60)
    got = [(e["agent"], e["tool_name"], e["op"], e["path"], e["source"]) for e in events(log)]
    assert got == [("claude-code", "Read", "read", "~/.ssh/id_rsa", "hook"), ("claude-code", "Bash", "delete", "old.log", "shell")]


# --- laya-guard activity -----------------------------------------------------------------------------------------------

def test_the_activity_command_turns_it_on_and_off_and_shows_what_it_holds(env, capsys):
    _, log = env
    assert cli.main(["activity"]) == 0
    out = capsys.readouterr().out
    assert "recording: off" in out and "laya-guard activity on" in out

    assert cli.main(["activity", "on"]) == 0
    out = capsys.readouterr().out
    assert "is on" in out and "Never the contents" in out and "only you can read" in out and activity.enabled()

    _guard(tool_call="Read: ~/.ssh/id_rsa", tool_name="Read", paths=["~/.ssh/id_rsa"], cwd="/tmp", agent="claude-code")
    assert cli.main(["activity"]) == 0
    out = capsys.readouterr().out
    assert "recording: on" in out and "1 event" in out and "claude-code" in out and "~/.ssh/id_rsa" in out

    assert cli.main(["activity", "--json", "--last", "5"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["enabled"] is True and data["total"] == 1 and data["events"][0]["op"] == "read"

    assert cli.main(["activity", "off"]) == 0
    assert "is off" in capsys.readouterr().out and not activity.enabled() and log.exists()      # off keeps what was recorded


def test_the_activity_command_says_when_the_environment_decides(env, capsys, monkeypatch):
    monkeypatch.setenv("LAYA_GUARD_FILE_ACTIVITY", "0")
    assert cli.main(["activity", "on"]) == 0
    assert "stays off here" in capsys.readouterr().out
    assert cli.main(["activity"]) == 0
    assert "set by LAYA_GUARD_FILE_ACTIVITY" in capsys.readouterr().out
