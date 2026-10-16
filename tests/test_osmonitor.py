"""The OS monitor: which processes are an agent's, what their file accesses mean, what is kept and how it is recorded.

No eslogger, no sudo, no macOS needed: the messages are written here in the shape eslogger prints them (a probe of a
real macOS 26 run showed `event`, `process.audit_token.pid`, `process.ppid`, `process.executable.path`, and for exec
`args`, `cwd`, `env` and `target`). Everything runs with HOME and both logs in a temporary place.
"""
import io
import os
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from laya_guard import activity, cli, osmonitor  # noqa: E402
from laya_guard.fileaccess import Rule  # noqa: E402
from laya_guard.osmonitor import Monitor, Tracker  # noqa: E402

REAL_SPAWN = osmonitor._spawn_eslogger          # the fixture replaces it; one test looks at what it would run

CLAUDE = "/Users/u/.local/share/claude/versions/2.1.5"      # a native install: the binary is named by its version
PROJECT = "/work/proj"
SECRET = "ANTHROPIC_API_KEY=sk-ant-very-secret-value"


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = Path("/Users/tester")        # never created: pytest's own temp dir is under /private/var/folders, which is noise
    log = tmp_path / "data" / "file-activity.jsonl"
    for k, v in {"HOME": str(home), "XDG_CONFIG_HOME": str(tmp_path / "config"), "LAYA_GUARD_MOCK": "1",
                 "LAYA_GUARD_FILE_ACTIVITY_PATH": str(log), "LAYA_GUARD_LOG_PATH": str(tmp_path / "verdicts.jsonl")}.items():
        monkeypatch.setenv(k, v)
    for k in ("XDG_DATA_HOME", "LAYA_GUARD_PROTECT", "LAYA_GUARD_FILE_ACTIVITY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(osmonitor, "ps_rows", lambda: [])                  # nothing here looks at the real machine
    monkeypatch.setattr(osmonitor, "lsof_cwd", lambda pid: None)

    def no_spawn():
        raise AssertionError("a test tried to start the real eslogger")

    monkeypatch.setattr(osmonitor, "_spawn_eslogger", no_spawn)
    return home, log


# --- messages in eslogger's shape --------------------------------------------------------------------------------------

def _proc(pid, ppid=1, exe="/bin/zsh"):
    return {"audit_token": {"pid": pid, "pidversion": 1}, "ppid": ppid, "executable": {"path": exe}}


def _line(name, body, pid, ppid=1, exe="/bin/zsh"):
    return json.dumps({"event": {name: body}, "process": _proc(pid, ppid, exe), "time": "2026-10-16T08:00:00.000000000Z"})


def exec_(pid, ppid, exe, args, cwd=PROJECT, env=None):
    body = {"target": _proc(pid, ppid, exe), "args": args, "cwd": {"path": cwd}, "env": env or [SECRET, "HOME=/Users/u"]}
    return _line("exec", body, pid, ppid, "/bin/zsh")


def fork(parent, child, ppid=1, exe="/bin/zsh"):
    return _line("fork", {"child": _proc(child, parent, exe)}, parent, ppid, exe)


def exit_(pid, ppid=1, exe="/bin/zsh"):
    return _line("exit", {}, pid, ppid, exe)


def opened(pid, path, flags=1, ppid=1, exe="/bin/cat"):
    return _line("open", {"file": {"path": path}, "fflag": flags}, pid, ppid, exe)


def unlinked(pid, path, ppid=1, exe="/bin/rm"):
    return _line("unlink", {"target": {"path": path}, "parent_dir": {"path": str(Path(path).parent)}}, pid, ppid, exe)


def renamed(pid, old, new, ppid=1, exe="/bin/mv"):
    return _line("rename", {"source": {"path": old}, "destination_type": 0, "destination": {"existing_file": {"path": new}}},
                 pid, ppid, exe)


def created(pid, directory, name, ppid=1, exe="/usr/bin/touch"):
    return _line("create", {"destination_type": 1, "destination": {"new_path": {"dir": {"path": directory}, "filename": name}}},
                 pid, ppid, exe)


def agent_tree():
    """An agent (pid 100) started in PROJECT with a shell (101) below it."""
    return [exec_(100, 50, CLAUDE, ["claude", "--resume"]), fork(100, 101), exec_(101, 100, "/bin/zsh", ["zsh", "-c", "x"])]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def run(lines, *, home, reads="outside", programs=None, clock=None, rules=None, ignore=()):
    got = []
    clock = clock or Clock()
    tracker = Tracker(programs, ignore_pids=ignore)
    mon = Monitor(tracker, lambda events: got.extend(events) or True, reads=reads, home=str(home), clock=clock,
                  rules_loader=(lambda: rules) if rules is not None else osmonitor.load_rules, announce=lambda t: None)
    mon.run(lines)
    return got, mon


def digest(events):
    return [(e["op"], e["path"], e["tool_name"], e["agent"]) for e in events]


# --- which processes are an agent --------------------------------------------------------------------------------------

def test_a_program_named_for_an_agent_starts_a_tree_and_forks_carry_it(env):
    home, _ = env
    secret = str(home / ".ssh" / "id_rsa")
    lines = agent_tree() + [fork(101, 102, 100), exec_(102, 101, "/bin/cat", ["cat", secret]), opened(102, secret)]
    events, _ = run(lines, home=home)
    assert digest(events) == [("read", secret, "cat", "claude-code")]
    assert events[0]["source"] == "os-monitor"


def test_a_native_install_is_known_by_argv0_not_its_version_named_binary(env):
    tracker = Tracker()
    tracker.feed(json.loads(exec_(100, 50, CLAUDE, ["claude"])))
    assert tracker.procs[100].agent == "claude-code" and tracker.procs[100].exe == "claude"
    assert tracker.procs[100].project == PROJECT


def test_a_script_run_by_node_is_known_by_the_scripts_name(env):
    tracker = Tracker()
    tracker.feed(json.loads(exec_(100, 50, "/opt/homebrew/bin/node", ["node", "/opt/homebrew/bin/codex", "exec"])))
    tracker.feed(json.loads(exec_(200, 50, "/usr/bin/python3", ["python3", "/x/gemini.py"])))
    assert (tracker.procs[100].agent, tracker.procs[200].agent) == ("codex", "gemini-cli")


@pytest.mark.parametrize("exe,args", [("/Applications/Claude.app/Contents/MacOS/Claude", ["Claude"]),
                                      ("/usr/bin/vim", ["vim", "claude.md"]),
                                      ("/usr/bin/node", ["node", "server.js", "claude"]),
                                      ("/bin/zsh", ["zsh", "-c", "claude"])])
def test_things_that_only_mention_an_agent_are_not_one(env, exe, args):
    tracker = Tracker()
    tracker.feed(json.loads(exec_(100, 50, exe, args)))
    assert tracker.procs == {}


def test_match_adds_a_program_and_an_agent_label(env):
    home, _ = env
    programs = osmonitor._parse_matches(["aider", "my-bot=mybot"])
    events, _ = run([exec_(100, 50, "/usr/local/bin/my-bot", ["my-bot"]), unlinked(100, "/work/proj/x")], home=home,
                    programs=programs)
    assert digest(events) == [("delete", "/work/proj/x", "my-bot", "mybot")]
    assert osmonitor._parse_matches(None)["claude"] == "claude-code"
    with pytest.raises(ValueError):
        osmonitor._parse_matches(["/usr/bin/aider"])


def test_a_process_outside_the_tree_is_dropped(env):
    home, _ = env
    secret = str(home / ".ssh" / "id_rsa")
    events, mon = run(agent_tree() + [opened(900, secret, ppid=1), opened(901, secret, ppid=900)], home=home)
    assert events == [] and mon.stats["from_agents"] == 0


def test_a_missed_fork_is_made_up_from_the_parent_pid(env):
    home, _ = env
    secret = str(home / ".ssh" / "id_rsa")
    events, _ = run(agent_tree() + [opened(300, secret, ppid=101)], home=home)         # pid 300 was never forked in view
    assert digest(events) == [("read", secret, "cat", "claude-code")]


def test_a_reused_pid_does_not_inherit_the_old_owner(env):
    home, _ = env
    secret = str(home / ".ssh" / "id_rsa")
    lines = agent_tree() + [fork(101, 102, 100),                         # 102 exits without us seeing it ...
                            fork(900, 102, 1, "/bin/zsh"),              # ... and the number comes back under an unrelated parent
                            opened(102, secret, ppid=900)]
    events, _ = run(lines, home=home)
    assert events == []


def test_exit_ends_the_tracking(env):
    home, _ = env
    secret = str(home / ".ssh" / "id_rsa")
    events, _ = run(agent_tree() + [exit_(101, 100), opened(101, secret, ppid=1)], home=home)
    assert events == []


def test_our_own_process_and_what_it_starts_are_never_tracked(env):
    """`laya-guard watch` can run inside an agent's own shell: writing the log must not feed back into the log."""
    home, _ = env
    log = str(home / ".local" / "share" / "laya-guard" / "file-activity.jsonl")      # (the real one is not a noise path)
    lines = agent_tree() + [opened(777, log, flags=3, ppid=101, exe="/usr/bin/python3"),    # us: running before the watch began
                            fork(777, 778, 101), opened(778, log, flags=3, ppid=777, exe="/usr/bin/eslogger"),
                            opened(779, log, flags=3, ppid=778),
                            exec_(778, 777, CLAUDE, ["claude"]), opened(778, "/work/proj/x", flags=2, ppid=777)]   # even an agent below us
    events, _ = run(lines, home=home, ignore={777})
    assert events == []


# --- what a message means ----------------------------------------------------------------------------------------------

def test_reads_writes_deletes_creates_and_renames(env):
    home, _ = env
    lines = agent_tree() + [opened(101, "/work/proj/a.txt", flags=1), opened(101, "/work/proj/b.txt", flags=2),
                            opened(101, "/work/proj/c.txt", flags=3), unlinked(101, "/work/proj/d.txt"),
                            created(101, "/work/proj", "e.txt"), renamed(101, "/work/proj/f.txt", "/work/proj/g.txt")]
    events, _ = run(lines, home=home, reads="all")
    assert [(e["op"], e["path"]) for e in events] == [
        ("read", "/work/proj/a.txt"), ("write", "/work/proj/b.txt"), ("write", "/work/proj/c.txt"),
        ("delete", "/work/proj/d.txt"), ("write", "/work/proj/e.txt"), ("delete", "/work/proj/f.txt"),
        ("write", "/work/proj/g.txt")]


def test_an_event_has_the_activity_log_shape(env):
    home, _ = env
    secret = str(home / ".aws" / "credentials")
    events, _ = run(agent_tree() + [opened(101, secret, flags=2)], home=home)
    e = events[0]
    assert set(e) == {"ts", "agent", "tool_name", "op", "path", "abs", "source", "action", "policy_id", "would"}
    assert (e["agent"], e["tool_name"], e["op"], e["source"], e["action"]) == ("claude-code", "zsh", "write", "os-monitor", "observed")
    assert (e["abs"], e["policy_id"], e["would"]) == (secret, "protected-path", "block")      # writing a credential store


def test_an_access_nothing_protects_has_no_flag(env):
    home, _ = env
    events, _ = run(agent_tree() + [opened(101, "/work/proj/src/app.py", flags=2)], home=home)
    assert (events[0]["policy_id"], events[0]["would"], events[0]["action"]) == (None, None, "observed")


def test_the_environment_in_an_exec_message_is_never_kept(env, capsys):
    home, _ = env
    events, mon = run(agent_tree() + [opened(101, "/work/proj/a.py", flags=2)], home=home)
    assert "sk-ant" not in json.dumps(events) and "sk-ant" not in repr(vars(mon.tracker))
    assert all("env" not in p.__dict__ for p in mon.tracker.procs.values())


@pytest.mark.parametrize("junk", [
    '{"event": {"open": {"file": "not-a-dict", "fflag": "x"}}, "process": []}', '{"event": "x"}', "{}", "[1,2]", "null",
    "not json at all", '{"event": {"open": {"file": {"path": "relative.txt"}, "fflag": 1}}, "process": {"audit_token": {"pid": true}}}',
    '{"event": {"fork": {"child": 7}}, "process": {"audit_token": {"pid": 1}}}', ""])
def test_a_shape_nobody_planned_for_is_skipped_not_fatal(env, junk):
    home, _ = env
    events, mon = run(agent_tree() + [junk, opened(101, "/work/proj/z.txt", flags=2)], home=home)
    assert digest(events) == [("write", "/work/proj/z.txt", "zsh", "claude-code")]


def test_a_stream_that_never_looks_like_eslogger_is_reported(env):
    home, _ = env
    said = []
    mon = Monitor(Tracker(), lambda e: True, home=str(home), announce=said.append)
    mon.run(["hello"] * (osmonitor.FORMAT_CHECK_AFTER + 10))
    assert len(said) == 1 and "eslogger" in said[0] and mon.stats["unreadable"] == osmonitor.FORMAT_CHECK_AFTER + 10


# --- what is kept ------------------------------------------------------------------------------------------------------

def _reads(home, paths, reads, project=PROJECT):
    lines = [exec_(100, 50, CLAUDE, ["claude"], cwd=project)] + [opened(100, p, exe="/usr/bin/python3") for p in paths]
    events, _ = run(lines, home=home, reads=reads)
    return [e["path"] for e in events]


def test_reads_outside_the_project_keep_your_own_folders_only(env):
    home, _ = env
    h = str(home)
    paths = [PROJECT + "/src/a.py", PROJECT, "/usr/lib/libSystem.dylib", "/System/Library/x", "/private/var/db/x",
             h + "/Documents/notes.txt", h + "/other-repo/readme.md", h + "/.zshrc", h + "/.nvm/versions/node/x.js",
             h + "/Library/Caches/y", h + "/.ssh/id_rsa", "/tmp/scratch"]
    assert _reads(home, paths, "outside") == [h + "/Documents/notes.txt", h + "/other-repo/readme.md", h + "/.ssh/id_rsa"]


def test_reads_protected_keeps_only_protected_paths(env):
    home, _ = env
    h = str(home)
    got = _reads(home, [h + "/Documents/notes.txt", h + "/.ssh/id_rsa", PROJECT + "/.env"], "protected")
    assert got == [h + "/.ssh/id_rsa", PROJECT + "/.env"]


def test_reads_all_keeps_everything(env):
    home, _ = env
    paths = ["/usr/lib/libSystem.dylib", PROJECT + "/a.py"]
    assert _reads(home, paths, "all") == paths


def test_an_agent_with_no_known_project_keeps_only_protected_reads(env):
    """A seeded agent whose directory couldn't be read: "outside the project" can't be told, so it isn't guessed."""
    home, _ = env
    tracker = Tracker()
    tracker.seed([(100, 1, "/x/claude", ["claude"])], cwd_lookup=lambda pid: None)
    got = []
    mon = Monitor(tracker, lambda e: got.extend(e) or True, home=str(home), clock=Clock(), announce=lambda t: None)
    mon.run([opened(100, str(home / "Documents" / "n.txt"), exe="/x/claude"),
             opened(100, str(home / ".ssh" / "id_rsa"), exe="/x/claude")])
    assert [e["path"] for e in got] == [str(home / ".ssh" / "id_rsa")]


def test_writes_and_deletes_are_always_kept_wherever_they_are(env):
    home, _ = env
    lines = agent_tree() + [opened(101, "/usr/local/x", flags=2), unlinked(101, str(home / ".zshrc"))]
    events, _ = run(lines, home=home, reads="protected")
    assert [(e["op"], e["path"]) for e in events] == [("write", "/usr/local/x"), ("delete", str(home / ".zshrc"))]


def test_the_same_program_doing_the_same_thing_again_is_one_event_for_a_while(env):
    home, _ = env
    clock = Clock()
    p = "/work/proj/out.log"
    got = []
    mon = Monitor(Tracker(), lambda e: got.extend(e) or True, home=str(home), clock=clock, announce=lambda t: None)
    for line in agent_tree() + [opened(101, p, flags=2)] * 3:
        mon.handle_line(line)
    clock.t += osmonitor.COALESCE_SECONDS + 1
    mon.handle_line(opened(101, p, flags=2))                                       # long enough later: a new event
    mon.handle_line(exec_(101, 100, "/usr/bin/python3", ["python3"]))
    mon.handle_line(opened(101, p, flags=2))                                       # another program: its own event
    mon.flush()
    assert [e["tool_name"] for e in got] == ["zsh", "zsh", "python3"] and mon.stats["skipped_repeat"] == 2


def test_terminals_and_dev_null_are_noise(env):
    home, _ = env
    events, mon = run(agent_tree() + [opened(101, "/dev/null", flags=2), opened(101, "/dev/ttys004", flags=3)], home=home)
    assert events == [] and mon.stats["skipped_noise"] == 2


def test_protect_rules_are_used_as_the_user_wrote_them(env):
    home, _ = env
    yaml_path = activity.config_dir() / "protect.yaml"
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text("protect:\n  - path: /work/proj/secrets/**\n    ops: [read, write]\n    action: block\n")
    events, _ = run(agent_tree() + [opened(101, "/work/proj/secrets/k.pem", flags=1)], home=home)
    assert (events[0]["would"], events[0]["policy_id"]) == ("block", "protected-path")


def test_an_edited_rules_file_applies_to_a_watch_that_is_already_running(env):
    home, _ = env
    clock = Clock()
    state = {"rules": []}
    got = []
    mon = Monitor(Tracker(), lambda e: got.extend(e) or True, home=str(home), clock=clock, announce=lambda t: None,
                  rules_loader=lambda: state["rules"])
    for line in agent_tree():
        mon.handle_line(line)
    mon.handle_line(opened(101, "/work/proj/k.pem", flags=2))
    clock.t += osmonitor.COALESCE_SECONDS + 1
    state["rules"] = [Rule("/work/proj/*.pem", frozenset({"write"}), "block", "pem")]      # the user adds a rule
    clock.t += osmonitor.RULES_RELOAD_SECONDS
    mon.handle_line(opened(101, "/work/proj/k.pem", flags=2))
    mon.flush()
    assert [e["would"] for e in got] == [None, "block"]


# --- agents that are already running -----------------------------------------------------------------------------------

def test_ps_output_is_read_into_rows_and_a_path_with_spaces_stays_whole():
    comm = ("  100     1 /Users/u/Library/Application Support/Claude/claude-code/2.1.295/abc/claude.app/Contents/MacOS/claude\n"
            "  101   100 /bin/zsh\n  102   101 node\nnonsense\n  103   101 /usr/bin/gone\n")
    args = ("  100 /Users/u/Library/Application Support/Claude/claude-code/2.1.295/abc/claude.app/Contents/MacOS/claude --output-format x\n"
            "  101 /bin/zsh -l\n  102 node /opt/bin/codex exec\n")
    exe = "/Users/u/Library/Application Support/Claude/claude-code/2.1.295/abc/claude.app/Contents/MacOS/claude"
    assert osmonitor.parse_ps(comm, args) == [
        (100, 1, exe, [exe, "--output-format", "x"]), (101, 100, "/bin/zsh", ["/bin/zsh", "-l"]),
        (102, 101, "node", ["node", "/opt/bin/codex", "exec"]), (103, 101, "/usr/bin/gone", ["/usr/bin/gone"])]
    tracker = Tracker()
    tracker.seed(osmonitor.parse_ps(comm, args), cwd_lookup=lambda pid: "/work")
    assert {pid: p.agent for pid, p in tracker.procs.items()} == {   # node running codex below claude is its own agent
        100: "claude-code", 101: "claude-code", 102: "codex", 103: "claude-code"}


def test_agents_already_running_are_found_with_everything_below_them(env):
    home, _ = env
    rows = [(1, 0, "/sbin/launchd", ["launchd"]), (100, 1, "/usr/local/bin/claude", ["claude"]),
            (101, 100, "/bin/zsh", ["zsh"]), (102, 101, "/usr/bin/python3", ["python3"]),
            (200, 1, "/bin/zsh", ["zsh"]), (201, 200, "/usr/bin/vim", ["vim"]),
            (300, 100, "/opt/codex", ["codex"]), (301, 300, "/bin/sh", ["sh"])]
    tracker = Tracker()
    tracker.seed(rows, cwd_lookup=lambda pid: {100: "/work/a", 300: "/work/b"}.get(pid))
    assert {pid: (p.agent, p.project) for pid, p in tracker.procs.items()} == {
        100: ("claude-code", "/work/a"), 101: ("claude-code", "/work/a"), 102: ("claude-code", "/work/a"),
        300: ("codex", "/work/b"), 301: ("codex", "/work/b")}
    assert [pid for pid, _ in tracker.new_roots] == [100, 300]
    secret = str(home / ".ssh" / "id_rsa")
    got = []
    mon = Monitor(tracker, lambda e: got.extend(e) or True, home=str(home), clock=Clock(), announce=lambda t: None)
    mon.run([opened(102, secret, ppid=101, exe="/usr/bin/python3"), opened(201, secret, ppid=200)])
    assert digest(got) == [("read", secret, "python3", "claude-code")]


def test_the_watch_itself_is_not_seeded(env):
    tracker = Tracker(ignore_pids={101})
    tracker.seed([(100, 1, "/usr/local/bin/claude", ["claude"]), (101, 100, "/usr/bin/python3", ["laya-guard", "watch"])],
                 cwd_lookup=lambda pid: "/w")
    assert list(tracker.procs) == [100]


# --- running it --------------------------------------------------------------------------------------------------------

def _stdin(lines):
    return io.StringIO("\n".join(lines) + "\n")


def test_watch_records_into_the_activity_log_when_it_is_on(env):
    home, log = env
    activity.turn_on()
    secret = str(home / ".ssh" / "id_rsa")
    lines = agent_tree() + [opened(101, secret), opened(101, "/work/proj/a.py", flags=2)]
    assert osmonitor.watch(stdin=_stdin(lines)) == 0
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(e["op"], e["path"], e["source"], e["would"]) for e in events] == [
        ("read", secret, "os-monitor", "review"), ("write", "/work/proj/a.py", "os-monitor", None)]
    assert (log.stat().st_mode & 0o777) == 0o600
    assert "sk-ant" not in log.read_text()


def test_watch_will_not_record_while_the_log_is_off(env, capsys):
    _, log = env
    assert osmonitor.watch(stdin=_stdin(agent_tree())) == 1
    assert "laya-guard activity on" in capsys.readouterr().err and not log.exists()


def test_watch_respects_an_environment_that_forces_the_log_off(env, capsys, monkeypatch):
    activity.turn_on()
    monkeypatch.setenv("LAYA_GUARD_FILE_ACTIVITY", "0")
    assert osmonitor.watch(stdin=_stdin(agent_tree())) == 1
    assert "off in this environment" in capsys.readouterr().err


def test_watch_stdout_prints_events_and_writes_nothing(env, capsys):
    home, log = env
    lines = agent_tree() + [opened(101, "/work/proj/a.py", flags=2)]
    assert osmonitor.watch(stdin=_stdin(lines), to_stdout=True) == 0
    out = capsys.readouterr()
    printed = [json.loads(line) for line in out.out.splitlines()]
    assert [(e["op"], e["path"]) for e in printed] == [("write", "/work/proj/a.py")] and not log.exists()
    assert "tracking claude-code (pid 100)" in out.err and "1 recorded" in out.err and "sk-ant" not in out.out + out.err


def test_the_cli_runs_watch(env, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _stdin(agent_tree() + [opened(101, "/work/proj/a.py", flags=2)]))
    assert cli.main(["watch", "--stdout", "--no-seed", "--reads", "protected"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[0])["path"] == "/work/proj/a.py"


def test_the_cli_rejects_a_bad_match(env, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _stdin([]))
    assert cli.main(["watch", "--stdout", "--match", "/usr/bin/x"]) == 1
    assert "--match" in capsys.readouterr().err


def test_a_full_log_stops_recording_and_says_so_once(env, monkeypatch):
    home, log = env
    activity.turn_on()
    monkeypatch.setattr(activity, "MAX_BYTES", 10)
    log.parent.mkdir(parents=True)
    log.write_text("x" * 100)
    said = []
    mon = Monitor(Tracker(), activity.append_events, home=str(home), clock=Clock(), announce=said.append)
    mon.run(agent_tree() + [opened(101, "/work/proj/a.py", flags=2)])
    mon.handle_line(opened(101, "/work/proj/b.py", flags=2))
    mon.flush()
    assert mon.stats["lost"] == 2 and sum("full" in s for s in said) == 1


def test_a_stopped_stream_still_saves_what_was_buffered(env):
    home, _ = env
    got = []

    def lines():
        yield from agent_tree()
        yield opened(101, "/work/proj/a.py", flags=2)
        raise KeyboardInterrupt

    mon = Monitor(Tracker(), lambda e: got.extend(e) or True, home=str(home), clock=Clock(), announce=lambda t: None)
    with pytest.raises(KeyboardInterrupt):
        mon.run(lines())
    assert len(got) == 1


class FakeEslogger:
    """Stands in for the `sudo eslogger` child: a stream of lines, and the calls watch makes to stop it."""

    def __init__(self, lines):
        self.stdout = iter(lines)
        self.stopped = False

    def poll(self):
        return None if not self.stopped else 0

    def terminate(self):
        self.stopped = True

    def kill(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 0


class FakeTty(io.StringIO):
    def isatty(self):
        return True


def test_on_a_terminal_watch_starts_eslogger_and_stops_it(env, monkeypatch):
    home, _ = env
    child = FakeEslogger(agent_tree() + [opened(101, "/work/proj/a.py", flags=2)])
    monkeypatch.setattr(osmonitor, "_spawn_eslogger", lambda: child)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert osmonitor.watch(stdin=FakeTty(), to_stdout=True) == 0
    assert child.stopped


def test_an_eslogger_that_says_nothing_points_at_full_disk_access(env, monkeypatch, capsys):
    monkeypatch.setattr(osmonitor, "_spawn_eslogger", lambda: FakeEslogger([]))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert osmonitor.watch(stdin=FakeTty(), to_stdout=True) == 1
    assert "Full Disk Access" in capsys.readouterr().err


def test_a_terminal_off_macos_is_told_to_pipe_a_capture_in(env, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    assert osmonitor.watch(stdin=FakeTty(), to_stdout=True) == 1
    assert "Pipe an eslogger capture" in capsys.readouterr().err


def test_the_spawned_command_is_exactly_sudo_eslogger_with_the_events(env, monkeypatch):
    seen = {}

    class Popen:
        def __init__(self, argv, **kw):
            seen["argv"], seen["kw"] = argv, kw

    monkeypatch.setattr(osmonitor.subprocess, "Popen", Popen)
    REAL_SPAWN()
    # absolute paths: a writable folder early on PATH (Homebrew's) must not get to supply what runs as root
    assert seen["argv"][:2] == ["/usr/bin/sudo", "/usr/bin/eslogger"]
    assert seen["argv"][2:] == list(osmonitor.EVENTS) and "shell" not in seen["kw"]


def test_the_spawned_command_ignores_an_eslogger_or_sudo_on_path(env, monkeypatch, tmp_path):
    for name in ("sudo", "eslogger"):
        fake = tmp_path / name
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    seen = {}

    class Popen:
        def __init__(self, argv, **kw):
            seen["argv"] = argv

    monkeypatch.setattr(osmonitor.subprocess, "Popen", Popen)
    REAL_SPAWN()
    assert not any(str(tmp_path) in a for a in seen["argv"])


def test_turning_recording_off_stops_a_running_watch(env, capsys, monkeypatch):
    home, log = env
    activity.turn_on()
    monkeypatch.setattr(osmonitor, "FLUSH_EVENTS", 1)

    class Piped:
        def isatty(self):
            return False

        def __iter__(self):
            yield from agent_tree()
            yield opened(101, str(home / ".ssh" / "id_rsa"))
            activity.turn_off()                                   # `laya-guard activity off` from another terminal
            yield opened(101, str(home / ".aws" / "credentials"))
            yield opened(101, str(home / ".kube" / "config"))

    assert osmonitor.watch(stdin=Piped()) == 0
    paths = [json.loads(line)["path"] for line in log.read_text().splitlines()]
    assert paths == [str(home / ".ssh" / "id_rsa")]
    assert "turned off" in capsys.readouterr().err


def test_the_activity_command_shows_what_the_os_saw(env, capsys):
    home, _ = env
    activity.turn_on()
    osmonitor.watch(stdin=_stdin(agent_tree() + [opened(101, str(home / ".ssh" / "id_rsa"))]))
    capsys.readouterr()
    assert cli.main(["activity"]) == 0
    out = capsys.readouterr().out
    assert "os-monitor" in out and "observed!review" in out and "claude-code" in out and "1 event" in out


def test_pipe_command_names_every_event_it_needs():
    assert all(e in osmonitor.PIPE_COMMAND for e in ("exec", "fork", "exit", "open", "unlink", "rename", "create"))
    assert "close" not in osmonitor.EVENTS and "write" not in osmonitor.EVENTS


def test_ignore_drops_matching_paths_but_never_a_protected_one(env):
    home, _ = env
    secret = str(home / ".ssh" / "id_rsa")
    lines = agent_tree() + [opened(101, str(home / ".claude" / "projects" / "t.jsonl"), flags=2),
                            opened(101, "/work/proj/keep.py", flags=2), opened(101, secret, flags=2)]
    got = []
    mon = Monitor(Tracker(), lambda e: got.extend(e) or True, home=str(home), clock=Clock(), announce=lambda t: None,
                  ignore=["~/.claude/**", "~/.ssh/**"])
    mon.run(lines)
    assert [e["path"] for e in got] == ["/work/proj/keep.py", secret] and mon.stats["skipped_ignored"] == 1


def test_the_cli_passes_ignore_through(env, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _stdin(agent_tree() + [opened(101, "/work/proj/a.log", flags=2),
                                                             opened(101, "/work/proj/b.py", flags=2)]))
    assert cli.main(["watch", "--stdout", "--no-seed", "--ignore", "**/*.log"]) == 0
    assert [json.loads(l)["path"] for l in capsys.readouterr().out.splitlines()] == ["/work/proj/b.py"]


def test_a_reader_that_goes_away_ends_the_watch_quietly(env, monkeypatch, capsys):
    def gone(events):
        raise BrokenPipeError

    monkeypatch.setattr(osmonitor, "_print_sink", gone)
    assert osmonitor.watch(stdin=_stdin(agent_tree() + [opened(101, "/work/proj/a.py", flags=2)]), to_stdout=True) == 0
    assert "Traceback" not in capsys.readouterr().err
