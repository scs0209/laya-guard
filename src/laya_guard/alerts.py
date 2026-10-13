"""Tell a human when laya-guard is judging without its model (standard library only).

An outage is not a denial, and the log can only be read after the fact. If `LAYA_GUARD_ALERT_CMD` is set, the
first time a kind of outage shows up the command is started, once, in the background; the next alert of the
same kind waits out `LAYA_GUARD_ALERT_COOLDOWN` seconds (default 600). Two kinds exist:

  server-down   a hook couldn't reach the server and fell back to the offline check (regexes and the safe
                list only). This runs in the hook's own process, the only code that is up during the outage.
  model-error   the server is up but the model failed, so regexes and heuristics stood in.

The command runs through a shell with `LAYA_GUARD_ALERT_KIND` and `LAYA_GUARD_ALERT_MESSAGE` in its
environment (the text is never put into the command line itself). It is never waited for and any failure
is swallowed: a hook must not get slower or fail because a notification didn't go out.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

try:
    import fcntl
except ImportError:                      # Windows: no locking, still correct enough for a notification
    fcntl = None

KINDS = {
    "server-down": "The laya-guard server can't be reached, so tool calls are judged by the offline check only "
                   "(regexes and the safe list, no model).",
    "model-error": "The laya-guard model failed, so tool calls are judged by regexes and heuristics only.",
}
DEFAULT_COOLDOWN = 600.0


def alert_command() -> str:
    return os.environ.get("LAYA_GUARD_ALERT_CMD", "").strip()


def cooldown() -> float:
    try:
        return max(0.0, float(os.environ.get("LAYA_GUARD_ALERT_COOLDOWN", DEFAULT_COOLDOWN)))
    except ValueError:
        return DEFAULT_COOLDOWN


def state_path() -> Path:
    from .log import log_path
    return log_path().with_name("alerts.json")      # next to the verdict log, so it follows LAYA_GUARD_LOG_PATH


def _claim(kind: str, now: float) -> bool:
    """True if this kind of alert may run now (and record that it did). Safe against hooks racing each other."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        try:
            state = json.loads(fh.read() or "{}")
            state = state if isinstance(state, dict) else {}
        except ValueError:
            state = {}
        last = state.get(kind)
        if isinstance(last, (int, float)) and 0 <= now - last < cooldown():
            return False
        state[kind] = now
        fh.seek(0)
        fh.truncate()
        fh.write(json.dumps(state))
        fh.flush()
    return True


def maybe_alert(kind: str, message: str | None = None, now: float | None = None) -> bool:
    """Start LAYA_GUARD_ALERT_CMD for this kind of outage unless one already ran within the cooldown.
    Never raises and never waits. Returns True if the command was started."""
    command = alert_command()
    if not command or kind not in KINDS:
        return False
    try:
        if not _claim(kind, time.time() if now is None else now):
            return False
        proc = subprocess.Popen(command, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True,
                                env=dict(os.environ, LAYA_GUARD_ALERT_KIND=kind,
                                         LAYA_GUARD_ALERT_MESSAGE=message or KINDS[kind]))
        threading.Thread(target=proc.wait, daemon=True).start()     # reap it if this process lives on (the server)
        return True
    except Exception:
        return False
