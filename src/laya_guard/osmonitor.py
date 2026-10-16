"""OS monitor (macOS, optional): the files an agent's processes really opened, from Apple's `eslogger`.

The hooks see what a tool call says it will touch. A script the agent runs, a child process, a build tool: none of that
reaches them. The operating system sees every open, create, delete and rename, so this reads that stream, keeps only
the activity of the agent's own processes, and adds it to the file activity log with `source: "os-monitor"`.

    sudo eslogger exec fork exit open unlink rename create | laya-guard watch

or simply `laya-guard watch`, which starts that `sudo eslogger` for you. Only Apple's `eslogger` runs as root; this code
reads its output through a pipe and runs as you.

What it can and can't do:

* It reports after the fact. Nothing is blocked (Endpoint Security notifications can't); blocking stays with the hooks.
* It needs sudo and Full Disk Access for the terminal app that runs it. `eslogger` is Apple's tool and its output is
  documented as "NOT API", so the shape can change with a macOS release. Anything unrecognised is skipped, never
  guessed at, and a stream that never matches the shape it expects is reported.
* The stream covers every process on the machine. Everything outside the agent's process tree is dropped as soon as it
  is read; the environment variables an `exec` message carries (they hold secrets) are never read, stored or logged.
  Only a path, what was done to it, which agent and which program are kept. Never file contents.

How a process is attributed: an `exec` of a program with an agent's name (`claude`, `codex`, ...; `--match` adds more)
starts a tree, and `fork` carries it to every child. `exit` ends it. Agents already running when this starts are found
from `ps`.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import activity
from .fileaccess import judge_accesses, load_rules, matches_glob

EVENTS = ("exec", "fork", "exit", "open", "unlink", "rename", "create")     # not close/write: they are 3/4 of the volume
ESLOGGER = "/usr/bin/eslogger"
SUDO = "/usr/bin/sudo"
PIPE_COMMAND = f"sudo {ESLOGGER} {' '.join(EVENTS)} | laya-guard watch"
FWRITE = 0x2                                                                # open flags: FREAD=1, FWRITE=2

# the program name that starts an agent's tree -> the agent's id (the same ids `laya-guard install` uses)
AGENT_PROGRAMS = {"claude": "claude-code", "codex": "codex", "cursor-agent": "cursor", "gemini": "gemini-cli",
                  "qwen": "qwen-code", "copilot": "copilot-cli", "cline": "cline", "opencode": "opencode",
                  "kilo": "kilo", "amp": "amp"}
_INTERPRETERS = ("node", "bun", "deno", "python")                           # prefix: python3.12 is one too
_SCRIPT_SUFFIXES = (".js", ".mjs", ".cjs", ".ts", ".py")

READS = ("protected", "outside", "all")
COALESCE_SECONDS = 5.0              # the same program doing the same thing to the same path again inside this is one event
RULES_RELOAD_SECONDS = 30.0
FLUSH_EVENTS = 200
FLUSH_SECONDS = 1.0
NOISE_PREFIXES = ("/dev/", "/private/var/folders/")    # terminals, /dev/null and the per-user temp and cache area
FORMAT_CHECK_AFTER = 500            # lines read with nothing recognised: say the format may have changed


# --- reading one eslogger message --------------------------------------------------------------------------------------
# Apple documents this output as "NOT API". Every accessor takes what it is given loosely and answers None for a shape
# it doesn't know, so a different macOS release makes events go missing (and is reported), never wrong.

def _pid(proc) -> int | None:
    if not isinstance(proc, dict):
        return None
    token = proc.get("audit_token")
    pid = token.get("pid") if isinstance(token, dict) else proc.get("pid")
    return pid if isinstance(pid, int) and not isinstance(pid, bool) else None


def _path(file) -> str | None:
    p = file.get("path") if isinstance(file, dict) else None
    return p if isinstance(p, str) and p.startswith("/") else None


def _exe(proc) -> str:
    return _path(proc.get("executable")) or "" if isinstance(proc, dict) else ""


def _destination(dest) -> str | None:
    """Where a create or rename put a file: an existing file's path, or a new name inside a directory."""
    if not isinstance(dest, dict):
        return None
    if isinstance(dest.get("existing_file"), dict):
        return _path(dest["existing_file"])
    new = dest.get("new_path")
    if isinstance(new, dict) and isinstance(new.get("filename"), str):
        directory = _path(new.get("dir"))
        return os.path.join(directory, new["filename"]) if directory else None
    return None


def touches(name: str, body: dict) -> list[tuple[str, str]]:
    """The (op, path) pairs one message says a process did to files. op is read, write or delete. There is no read
    event: a read is an `open` without the write flag."""
    if name == "open":
        path = _path(body.get("file"))
        flags = body.get("fflag")
        flags = flags if isinstance(flags, int) and not isinstance(flags, bool) else 0
        return [("write" if flags & FWRITE else "read", path)] if path else []
    if name == "create":
        path = _destination(body.get("destination"))
        return [("write", path)] if path else []
    if name == "unlink":
        path = _path(body.get("target"))
        return [("delete", path)] if path else []
    if name == "rename":
        out = []
        if _path(body.get("source")):
            out.append(("delete", _path(body.get("source"))))           # the old name is gone ...
        if _destination(body.get("destination")):
            out.append(("write", _destination(body.get("destination"))))    # ... and the new one exists
        return out
    return []


# --- which program is an agent -----------------------------------------------------------------------------------------

def _is_interpreter(name: str) -> bool:
    return name.startswith(_INTERPRETERS)


def _program_names(exe: str, args: list[str]) -> list[str]:
    """The names a process answers to: its executable, its argv[0] and, for a script run by an interpreter
    (`node /usr/local/bin/claude`), the script's name."""
    first = [n for n in (os.path.basename(exe or ""), os.path.basename(args[0]) if args else "") if n]
    names = list(first)
    if len(args) > 1 and any(_is_interpreter(n) for n in first) and not args[1].startswith("-"):
        script = os.path.basename(args[1])
        for suffix in _SCRIPT_SUFFIXES:
            if script.endswith(suffix):
                script = script[: -len(suffix)]
        names.append(script)
    return names


def match_agent(exe: str, args: list[str], programs: dict[str, str]) -> str | None:
    """The agent a program is, or None. Names are compared exactly (case and all): `claude` is an agent, `Claude` (the
    desktop app) is not."""
    for name in _program_names(exe, args):
        if name in programs:
            return programs[name]
    return None


def program_name(exe: str, args: list[str]) -> str:
    """What to call a process in the log: its executable's name, or argv[0] when that is a version number (a native
    install keeps its binaries as .../versions/2.1.5)."""
    base = os.path.basename(exe or "")
    if (not base or base[:1].isdigit()) and args:
        base = os.path.basename(args[0]) or base
    return (base or "?")[:64]


# --- the process tree --------------------------------------------------------------------------------------------------

@dataclass
class Proc:
    agent: str
    exe: str = "?"
    cwd: str | None = None
    project: str | None = None      # where the agent was started: the directory its tree is "inside"


def parse_ps(comm_text: str, args_text: str = "") -> list[tuple[int, int, str, list[str]]]:
    """(pid, ppid, executable, argv) per process, from `ps -axo pid=,ppid=,comm=` (the executable's full path, which may
    hold spaces: ".../Application Support/Claude/.../claude") and `ps -axo pid=,args=` (the command line as one string,
    split on spaces, with the executable kept whole when the line starts with it)."""
    args_by_pid = {}
    for line in args_text.splitlines():
        m = re.match(r"\s*(\d+)\s+(\S.*)$", line)
        if m:
            args_by_pid[int(m.group(1))] = m.group(2)
    rows = []
    for line in comm_text.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S.*)$", line)
        if not m:
            continue
        pid, exe = int(m.group(1)), m.group(3).strip()
        line_args = args_by_pid.get(pid, "")
        argv = [exe] + line_args[len(exe):].split() if line_args.startswith(exe) else (line_args.split() or [exe])
        rows.append((pid, int(m.group(2)), exe, argv))
    return rows


def _ps(fields: str) -> str:
    out = subprocess.run(["ps", "-axo", fields], capture_output=True, text=True, timeout=10)
    return out.stdout if out.returncode == 0 else ""


def ps_rows() -> list[tuple[int, int, str, list[str]]]:
    try:
        return parse_ps(_ps("pid=,ppid=,comm="), _ps("pid=,args="))
    except (OSError, subprocess.SubprocessError):
        return []


def lsof_cwd(pid: int) -> str | None:
    """Where a running process is, for the agents already running when the watch starts (best effort)."""
    try:
        out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True, text=True,
                             timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines():
        if line.startswith("n/"):
            return line[1:]
    return None


class Tracker:
    """Which processes belong to an agent. Feed it eslogger messages; it answers with the file accesses of the ones
    that do. Never stores a message, and never reads an `exec`'s environment."""

    def __init__(self, programs: dict[str, str] | None = None, ignore_pids=()):
        self.programs = dict(AGENT_PROGRAMS if programs is None else programs)
        self.procs: dict[int, Proc] = {}
        self.ignored: set[int] = set(ignore_pids)       # this process and what it starts (sudo, eslogger): never tracked
        self.new_roots: list[tuple[int, Proc]] = []      # for the caller to announce

    def seed(self, rows, cwd_lookup=lsof_cwd) -> None:
        """Start from the processes already running: every agent among them and everything below it."""
        by_pid = {pid: (ppid, exe, argv) for pid, ppid, exe, argv in rows}
        matched = {pid: a for pid, (_, exe, argv) in by_pid.items() if (a := match_agent(exe, argv, self.programs))}
        nearest: dict[int, int | None] = {}              # pid -> the pid of the closest agent at or above it

        def agent_above(pid: int) -> int | None:
            chain, cur, found = [], pid, None
            while cur in by_pid and len(chain) < 64:
                if cur in nearest:
                    found = nearest[cur]
                    break
                chain.append(cur)
                if cur in matched:
                    found = cur
                    break
                cur = by_pid[cur][0]
            for c in chain:
                nearest[c] = found
            return found

        cwds: dict[int, str | None] = {}
        for pid in by_pid:
            top = agent_above(pid)
            if top is None or pid in self.ignored:
                continue
            if top not in cwds:
                cwds[top] = cwd_lookup(top) if cwd_lookup else None
            _, exe, argv = by_pid[pid]
            proc = Proc(matched[top], program_name(exe, argv), cwds[top], cwds[top])
            self.procs[pid] = proc
            if pid == top:
                self.new_roots.append((pid, proc))

    # -- messages --

    def _member(self, pid: int, proc: dict) -> Proc | None:
        """The tracked process for pid. One we missed the fork of is still ours if its parent is."""
        if pid in self.ignored:
            return None
        tracked = self.procs.get(pid)
        if tracked is None:
            ppid = proc.get("ppid") if isinstance(proc, dict) else None
            parent = self.procs.get(ppid) if isinstance(ppid, int) else None
            if parent is not None:
                tracked = self.procs[pid] = Proc(parent.agent, program_name(_exe(proc), []), parent.cwd, parent.project)
        return tracked

    def _fork(self, pid: int, proc: dict, body: dict) -> None:
        child = _pid(body.get("child"))
        if child is None:
            return
        parent = self._member(pid, proc)
        if pid in self.ignored:
            self.ignored.add(child)
            self.procs.pop(child, None)
        elif parent is not None:
            self.ignored.discard(child)
            self.procs[child] = Proc(parent.agent, parent.exe, parent.cwd, parent.project)
        else:
            self.ignored.discard(child)
            self.procs.pop(child, None)                  # a reused pid must not keep an old owner

    def _exec(self, pid: int, proc: dict, body: dict) -> None:
        if pid in self.ignored:
            return
        target = body.get("target")
        exe = _exe(target) or _exe(proc)
        raw = body.get("args")
        args = [a for a in raw if isinstance(a, str)] if isinstance(raw, list) else []
        cwd = _path(body.get("cwd"))
        tracked = self._member(pid, proc)
        if tracked is not None:
            tracked.exe = program_name(exe, args)
            if cwd:
                tracked.cwd = cwd
            return
        agent = match_agent(exe, args, self.programs)
        if agent:
            tracked = self.procs[pid] = Proc(agent, program_name(exe, args), cwd, cwd)
            self.new_roots.append((pid, tracked))

    def feed(self, msg: dict) -> list[tuple[str, str, Proc]]:
        """(op, path, process) for each file access in this message that belongs to an agent's process."""
        ev = msg.get("event")
        if not isinstance(ev, dict) or not ev:
            return []
        name = next(iter(ev))
        body = ev[name]
        proc = msg.get("process")
        pid = _pid(proc)
        if not isinstance(body, dict) or pid is None:
            return []
        if name == "fork":
            self._fork(pid, proc, body)
        elif name == "exec":
            self._exec(pid, proc, body)
        elif name == "exit":
            self.procs.pop(pid, None)
            self.ignored.discard(pid)
        else:
            found = touches(name, body)
            if found:
                tracked = self._member(pid, proc)
                if tracked is not None:
                    return [(op, path, tracked) for op, path in found]
        return []


# --- what to keep ------------------------------------------------------------------------------------------------------

def outside_project(path: str, project: str | None, home: str) -> bool:
    """A file in one of your own folders (Documents, other projects, ...) that is not where the agent was started.
    System directories, hidden directories (~/.nvm, ~/.zshrc, ...) and ~/Library are the machine's own business: a
    shell reads dozens of them every time it starts, so they only count when a protected-path rule names them."""
    if project is None:
        return False
    root = project.rstrip("/")
    if path == root or path.startswith(root + "/"):
        return False
    if not path.startswith(home + "/"):
        return False
    first = path[len(home) + 1:].split("/", 1)[0]
    return not first.startswith(".") and first != "Library"


class Monitor:
    """Turns an agent's file accesses into activity events: judges each against the protected paths, keeps what is worth
    keeping, merges repeats, and hands batches to `sink` (the activity log, or stdout)."""

    def __init__(self, tracker: Tracker, sink, *, reads: str = "outside", ignore=(), home: str | None = None,
                 clock=time.monotonic, rules_loader=load_rules, announce=None):
        if reads not in READS:
            raise ValueError(f"reads must be one of {', '.join(READS)}")
        self.tracker, self.sink, self.reads, self.ignore = tracker, sink, reads, tuple(ignore)
        self.home = (home or os.path.expanduser("~")).rstrip("/")
        self.clock, self.announce = clock, announce or (lambda text: None)
        self._rules_loader, self._rules, self._rules_at = rules_loader, [], float("-inf")
        self._judge_cached = lru_cache(maxsize=8192)(self._judge_uncached)
        self.stats: Counter = Counter()
        self.buffer: list[dict] = []
        self._flushed_at = clock()
        self._recent: dict[tuple, float] = {}
        self._full = False
        self._warned_format = False

    # -- rules --

    def _fresh_rules(self) -> list:
        now = self.clock()
        if now - self._rules_at >= RULES_RELOAD_SECONDS:
            rules = list(self._rules_loader())
            if rules != self._rules:
                self._rules = rules
                self._judge_cached.cache_clear()
            self._rules_at = now
        return self._rules

    def _judge_uncached(self, op: str, path: str, anchor: str):
        """(the protected-path verdict or None, whether the user asked to ignore this path)."""
        return (judge_accesses([(op, path)], anchor, self._rules),
                any(matches_glob(g, path, anchor) for g in self.ignore))

    def _judge(self, op: str, path: str, anchor: str):
        self._fresh_rules()
        return self._judge_cached(op, path, anchor)

    # -- one access --

    def _access(self, op: str, path: str, proc: Proc) -> None:
        self.stats["accesses"] += 1
        if path.startswith(NOISE_PREFIXES):
            self.stats["skipped_noise"] += 1
            return
        key = (proc.agent, proc.exe, op, path)
        now = self.clock()
        last = self._recent.get(key)
        if last is not None and now - last < COALESCE_SECONDS:
            self.stats["skipped_repeat"] += 1
            return
        verdict, ignored = self._judge(op, path, proc.project or proc.cwd or "/")
        if ignored and verdict is None:                  # a protected path is never ignored
            self.stats["skipped_ignored"] += 1
            return
        if op == "read" and verdict is None and not (
                self.reads == "all" or (self.reads == "outside" and outside_project(path, proc.project, self.home))):
            self.stats["skipped_read"] += 1
            return
        if len(self._recent) > 20_000:
            self._recent = {k: t for k, t in self._recent.items() if now - t < COALESCE_SECONDS}
        self._recent[key] = now
        event = activity.build_events(agent=proc.agent, tool_name=proc.exe, accesses=[(op, path)], source="os-monitor",
                                      cwd=None, action="observed", policy_id=verdict.policy_id if verdict else None)[0]
        event["would"] = verdict.action if verdict else None
        self.buffer.append(event)
        self.stats["recorded"] += 1
        if verdict:
            self.stats["flagged"] += 1

    # -- the stream --

    def handle_line(self, line: str) -> None:
        self.stats["messages"] += 1
        try:
            msg = json.loads(line)
        except ValueError:
            msg = None
        if not isinstance(msg, dict):
            self.stats["unreadable"] += 1
        else:
            if isinstance(msg.get("event"), dict) and isinstance(msg.get("process"), dict):
                self.stats["recognised"] += 1
            for op, path, proc in self.tracker.feed(msg):
                self.stats["from_agents"] += 1
                self._access(op, path, proc)
            while self.tracker.new_roots:
                pid, proc = self.tracker.new_roots.pop(0)
                self.announce(f"tracking {proc.agent} (pid {pid})" + (f" started in {proc.project}" if proc.project else ""))
        if (self.stats["messages"] == FORMAT_CHECK_AFTER and not self.stats["recognised"]
                and not self._warned_format):
            self._warned_format = True
            self.announce("warning: none of the first %d lines looked like eslogger output (an `event` and a `process`). "
                          "Is this `eslogger` JSON? Its format can change with a macOS release." % FORMAT_CHECK_AFTER)

    def flush(self) -> None:
        if self.buffer:
            written = self.sink(self.buffer)
            if written is False:
                self.stats["lost"] += len(self.buffer)
                if not self._full:
                    self._full = True
                    self.announce("the activity log is full, so nothing more is recorded (see `laya-guard activity`)")
            self.buffer = []
        self._flushed_at = self.clock()

    def run(self, lines) -> Counter:
        try:
            for line in lines:
                self.handle_line(line)
                if len(self.buffer) >= FLUSH_EVENTS or self.clock() - self._flushed_at >= FLUSH_SECONDS:
                    self.flush()
        finally:
            self.flush()
        return self.stats

    def summary(self) -> str:
        s = self.stats
        return (f"{s['messages']} messages read, {s['from_agents']} file accesses by agent processes, "
                f"{s['recorded']} recorded ({s['flagged']} on protected paths); "
                f"left out: {s['skipped_read']} ordinary reads, {s['skipped_repeat']} repeats, "
                f"{s['skipped_noise'] + s['skipped_ignored']} noise or ignored"
                + (f"; {s['unreadable']} unreadable lines" if s["unreadable"] else "")
                + (f"; {s['lost']} not saved" if s["lost"] else ""))


# --- running it --------------------------------------------------------------------------------------------------------

def _parse_matches(items) -> dict[str, str]:
    """--match PROGRAM[=AGENT] -> {program: agent}."""
    programs = dict(AGENT_PROGRAMS)
    for item in items or []:
        program, _, agent = item.partition("=")
        program = program.strip()
        if not program or "/" in program:
            raise ValueError(f"--match takes a program name like `aider` or `aider=aider`, not {item!r}")
        programs[program] = agent.strip() or program
    return programs


def _print_sink(events: list[dict]) -> bool:
    sys.stdout.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
    sys.stdout.flush()
    return True


def _say(text: str) -> None:
    print(f"watch: {text}", file=sys.stderr, flush=True)


def _spawn_eslogger() -> subprocess.Popen:
    # Both by absolute path: a folder on PATH that the user (or the agent being watched) can write to, like Homebrew's,
    # could otherwise hold a fake `sudo` or `eslogger`, and the user's password would run it as root.
    return subprocess.Popen([SUDO, ESLOGGER, *EVENTS], stdout=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", bufsize=1)


class RecordingOff(Exception):
    """File activity recording was turned off while watching."""


def _record(events: list[dict]) -> bool:
    """The sink of a watch that records: `laya-guard activity off` stops it at the next flush, and nothing more is added."""
    try:
        on = activity.enabled()
    except OSError:
        on = False
    if not on:
        raise RecordingOff
    return activity.append_events(events)


def watch(*, matches=None, reads: str = "outside", ignore=(), to_stdout: bool = False, seed: bool = True,
          stdin=None) -> int:
    """laya-guard watch. Reads eslogger JSON from stdin when it is piped in, and starts `sudo eslogger` itself when it
    is a terminal. Returns the exit code."""
    try:
        programs = _parse_matches(matches)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    stdin = stdin if stdin is not None else sys.stdin
    if hasattr(stdin, "reconfigure"):
        stdin.reconfigure(errors="replace")             # a path that isn't valid UTF-8 must not end the watch
    spawn = stdin.isatty()
    if spawn and sys.platform != "darwin":
        print("error: the OS monitor reads macOS's eslogger. Pipe an eslogger capture in to try it elsewhere.",
              file=sys.stderr)
        return 1
    if not to_stdout:
        if activity.forced() is False:
            print("error: LAYA_GUARD_FILE_ACTIVITY is off in this environment, so nothing would be recorded.",
                  file=sys.stderr)
            return 1
        if not activity.enabled():
            print("error: file activity recording is off. Turn it on first: laya-guard activity on\n"
                  "       (or add --stdout to print the events instead of recording them)", file=sys.stderr)
            return 1
    tracker = Tracker(programs, ignore_pids={os.getpid()})
    monitor = Monitor(tracker, _print_sink if to_stdout else _record, reads=reads, ignore=ignore,
                      announce=_say)
    if seed:
        tracker.seed(ps_rows())
    where = "stdout" if to_stdout else str(activity.activity_path())
    _say(f"events go to {where}; reads kept: {reads}. Ctrl+C to stop.")
    child = None

    def _term(*_):
        raise KeyboardInterrupt

    try:
        previous = signal.signal(signal.SIGTERM, _term)         # a stop from kill or launchd still saves what is buffered
    except ValueError:                                          # not the main thread
        previous = None
    try:
        if spawn:
            _say("starting `sudo eslogger` (it asks for your password; only eslogger runs as root)")
            child = _spawn_eslogger()
            lines = child.stdout
        else:
            lines = stdin
        monitor.run(lines)
    except KeyboardInterrupt:
        pass
    except RecordingOff:
        monitor.buffer = []
        _say("file activity recording was turned off, so the watch stops; what it saw since the last save is not kept")
    except BrokenPipeError:                                     # `laya-guard watch --stdout | head`: the reader went away
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())     # so the exit flush has nothing to fail on
        except (OSError, ValueError):
            pass
    finally:
        if child is not None:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
        _say(monitor.summary())
    if child is not None and monitor.stats["messages"] == 0:
        print("eslogger produced nothing. Most likely the terminal app needs Full Disk Access (System Settings > "
              "Privacy & Security > Full Disk Access), then quit and reopen it.", file=sys.stderr)
        return 1
    return 0
