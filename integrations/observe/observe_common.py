"""Shared paths, masking and JSONL helpers for the observe-* scripts (standard library only).

Files (all created with mode 0600, local only, nothing is sent anywhere):
  observe.log          one line per PreToolUse verdict          (observe-hook.py)
  observe-outcome.log  ids of tool calls that actually ran      (observe-post-hook.py)
  labels.jsonl         ground-truth labels typed by a human     (observe-label.py)

Environment: LAYA_GUARD_HOME (data dir; default $XDG_DATA_HOME/laya-guard or
~/.local/share/laya-guard), LAYA_GUARD_URL, LAYA_GUARD_BIN, LAYA_GUARD_OBSERVE_LOG,
LAYA_GUARD_OUTCOME_LOG, LAYA_GUARD_LABELS.
"""
import json
import os
import re
import subprocess
import urllib.request


def _data_dir() -> str:
    override = os.environ.get("LAYA_GUARD_HOME")
    if override:
        return os.path.expanduser(override)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = os.path.expanduser(xdg) if xdg else os.path.expanduser("~/.local/share")
    return os.path.join(base, "laya-guard")


BASE = _data_dir()
OBSERVE_LOG = os.environ.get("LAYA_GUARD_OBSERVE_LOG") or os.path.join(BASE, "observe.log")
OUTCOME_LOG = os.environ.get("LAYA_GUARD_OUTCOME_LOG") or os.path.join(BASE, "observe-outcome.log")
LABELS = os.environ.get("LAYA_GUARD_LABELS") or os.path.join(BASE, "labels.jsonl")
URL = os.environ.get("LAYA_GUARD_URL") or "http://127.0.0.1:8787/guard"
BIN = os.environ.get("LAYA_GUARD_BIN") or "laya-guard"

FULL_MAX = 4000  # characters of the masked command kept as command_full
TIMEOUT = 10     # seconds for the server call and for the offline CLI fallback

# Common secret shapes. Not exhaustive: a free-form password or token is NOT caught.
# The KEY=value rule covers names containing PASSWORD / SECRET / TOKEN / API_KEY / ACCESS_KEY.
_TOKENS = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{10,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")
_KV = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|ACCESS[_-]?KEY)[A-Z0-9_]*)"
    r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)")
_BEARER = re.compile(r"(?i)(bearer\s+)\S+")
_PRIVKEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)


def mask(text: str) -> str:
    text = _PRIVKEY.sub("***PRIVATE KEY***", text)
    text = _TOKENS.sub("***", text)
    text = _BEARER.sub(r"\1***", text)
    return _KV.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


def read_jsonl(path: str) -> list:
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def append_jsonl(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def server_verdict(text: str, cwd: str = "/tmp"):
    """Ask the running guard server. Returns the verdict dict or None if it is unreachable."""
    try:
        req = urllib.request.Request(URL, data=json.dumps({"tool_call": text, "cwd": cwd}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except Exception:
        return None


def judge(text: str, cwd: str):
    """(verdict dict, offline?, error name). Falls back to `laya-guard check --offline`."""
    verdict = server_verdict(text, cwd)
    if verdict is not None:
        return verdict, False, None
    try:
        p = subprocess.run([BIN, "check", "--offline", "--json", "--cwd", cwd], input=text,
                           capture_output=True, text=True, timeout=TIMEOUT)
        return json.loads(p.stdout.strip().splitlines()[-1]), True, None
    except Exception as exc:
        return {}, True, type(exc).__name__
