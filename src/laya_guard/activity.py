"""File activity: which files an agent's tool calls named, kept apart from the verdict log.

Off by default. `laya-guard activity on` starts it. One JSON line per file an agent touched:

    {"ts", "agent", "tool_name", "op", "path", "abs", "cwd", "source", "action", "policy_id"}   (+ "would" for os-monitor)

cwd     the directory the call ran in (absolute), or null when the adapter sent none: what `laya-guard footprint` groups by.

op      read | write | delete
source  how the path was learned, which is how far to trust it:
        hook   a file tool (Read, Edit, Write, ...) named it. The agent's own report of what it is about to do.
        shell  guessed from a Bash command. Simple forms only (cat, rm, cp, mv, redirects, ...); anything that needs
               the shell to resolve (variables, globs, substitutions) is skipped, so this misses things.
        os-monitor  seen by the operating system (`laya-guard watch`, macOS, optional): a file a process of the agent
               really opened, whatever it was run from, so reads inside scripts and child processes are here. Reported
               after the fact, nothing was stopped: `action` is "observed" and `would` is what a protected-path rule
               would have answered (review | block | null). `tool_name` is the program that touched the file.
action  what the guard answered for the call (allow | review | block), so an attempt that was stopped is not
        mistaken for an access.

Hook and shell events are declared access: what a call said it would touch. A file read inside a script or a child
process is only here when the OS monitor is running.
File contents are never stored. Paths are, in clear, in a file only the owner can read; that is why it is opt-in and
why it lives next to, not inside, verdicts.jsonl (which never holds anything but hashes and lengths).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .fileaccess import accesses_for, config_dir
from .shellpaths import shell_accesses_moved

log = logging.getLogger("laya_guard")

MAX_BYTES = 50 * 1024 * 1024        # stop adding past this (about 250,000 events); delete or move the file to restart
MAX_PER_CALL = 50
OPS = ("read", "write", "delete")


# --- on / off, where it lives -------------------------------------------------------------------------------------------

def marker_path() -> Path:
    return config_dir() / "file-activity.on"


def activity_path() -> Path:
    override = os.environ.get("LAYA_GUARD_FILE_ACTIVITY_PATH", "")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "laya-guard" / "file-activity.jsonl"


def forced() -> bool | None:
    """True or False when LAYA_GUARD_FILE_ACTIVITY decides, None when the on/off switch does."""
    value = os.environ.get("LAYA_GUARD_FILE_ACTIVITY", "").strip().lower()
    if value in ("1", "true", "on", "yes"):
        return True
    if value in ("0", "false", "off", "no"):
        return False
    return None


def enabled() -> bool:
    f = forced()
    return marker_path().exists() if f is None else f


def turn_on() -> Path:
    marker_path().parent.mkdir(parents=True, exist_ok=True)
    marker_path().touch()
    return marker_path()


def turn_off() -> None:
    marker_path().unlink(missing_ok=True)


# --- events -------------------------------------------------------------------------------------------------------------

MAX_PATH_CHARS = 4096
MAX_LABEL_CHARS = 64                # agent, tool_name, policy_id: cut like the verdict log's tool_name


def _label(value) -> str | None:
    if value is None:
        return None
    return "".join(c for c in str(value) if c.isprintable())[:MAX_LABEL_CHARS] or None


def _absolute(path: str, cwd: str | None, moved: bool = False) -> str | None:
    """The path made absolute, or None when that can't be known: a relative path with no usable cwd, or after the
    command changed directory. Too long a result is left out rather than cut into a different path."""
    p = os.path.expanduser(path)
    if os.path.isabs(p):
        out = os.path.normpath(p)
    elif moved or not cwd or not os.path.isabs(cwd) or len(cwd) > MAX_PATH_CHARS:
        return None
    else:
        out = os.path.normpath(os.path.join(cwd, p))
    return out if len(out) <= MAX_PATH_CHARS else None


def build_events(*, agent, tool_name, accesses, source, cwd, action, policy_id) -> list[dict]:
    """One event per access: (op, path) or (op, path, moved)."""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    where = cwd if isinstance(cwd, str) and os.path.isabs(cwd) and len(cwd) <= MAX_PATH_CHARS else None
    events = []
    for access in accesses[:MAX_PER_CALL]:
        op, path, moved = (*access, False)[:3]
        events.append({"ts": ts, "agent": _label(agent), "tool_name": _label(tool_name), "op": op,
                       "path": path[:MAX_PATH_CHARS], "abs": _absolute(path, cwd, moved), "cwd": where, "source": source,
                       "action": action, "policy_id": _label(policy_id)})
    return events


def _append(events: list[dict]) -> bool:
    """Add events to the log. False when it is full (over MAX_BYTES) and nothing was written."""
    path = activity_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events).encode("utf-8")
    size = path.stat().st_size if path.exists() else 0
    if size + len(data) > MAX_BYTES:
        log.warning("file activity: %s would pass %d bytes, not recording more", path, MAX_BYTES)
        return False
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)    # owner only when we create it
    with os.fdopen(fd, "ab") as f:
        f.write(data)
    return True


def append_events(events: list[dict]) -> bool:
    """For another recorder (the OS monitor): add events that build_events made. False when the log is full."""
    return _append(events)


def record_call(*, agent: str | None, tool_name: str | None, cwd: str | None, verdict, text: str = "", paths=None,
                accesses=None, command: str | None = None) -> None:
    """Remember the files one tool call named, if recording is on. Never raises: a log that can't be written (or a
    config directory that can't even be looked at) must never stop a hook or change its verdict.

    Where the paths come from, in this order: `accesses` (an adapter says what the call touches, any agent) or the
    `paths` of a Claude Code file tool, which are source "hook"; else the shell `command` the adapter sent (or, for
    Claude Code's Bash tool, the call text), which is parsed and is source "shell"."""
    try:
        if not (tool_name or command or accesses) or not enabled():
            return
        found = accesses_for(tool_name, paths, accesses)
        source = "hook"
        if not found and (command or tool_name == "Bash"):
            found, source = shell_accesses_moved(command or text), "shell"
        if found:
            _append(build_events(agent=agent, tool_name=tool_name, accesses=found, source=source, cwd=cwd,
                                 action=verdict.action, policy_id=verdict.policy_id))
    except Exception as exc:                                         # noqa: BLE001
        log.warning("file activity: not recorded: %s", exc)


def load_events(path: Path | str | None = None) -> list[dict]:
    p = Path(path).expanduser() if path else activity_path()
    if not p.exists():
        return []
    events = []
    for line in p.read_text(encoding="utf-8", errors="replace").split("\n"):    # not splitlines: a path may hold U+2028
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events
