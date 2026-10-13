#!/usr/bin/env python3
"""laya-guard observer (Claude Code PreToolUse hook): log the verdict, never block.

Asks the guard server (or `laya-guard check --offline` when it is down) for allow / review /
block and appends it to observe.log. It always exits 0 and prints nothing, so it can sit
next to the enforcing guard-hook.sh in the same matcher (hooks run in parallel) or run alone
as a "dry run" before you turn enforcement on.

Each log line (mode 0600, local only): ts, id (tool_use_id), session, mode (permission_mode),
verdict, risk, policy, source, reason, tool, command (first 200 chars, for reports),
command_full (masked, up to 4000 chars, for labeling), ms, offline. Secrets are masked with
observe_common.mask, which is best-effort: free-form passwords are not caught.
Whether the call actually ran is recorded by observe-post-hook.py; labels by observe-label.py.
"""
import json
import os
import sys
import time

from observe_common import FULL_MAX, OBSERVE_LOG, append_jsonl, judge, mask


def flatten(tool: str, tool_input) -> str:
    """Same rule as guard-hook.sh: Bash judges the command only, other tools name + input values."""
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if tool == "Bash" and isinstance(command, str):
            return command.strip()
        return " ".join([tool] + [str(v) for v in tool_input.values()]).strip()
    return f"{tool} {tool_input}".strip()


def main() -> int:
    raw = sys.stdin.read()
    started = time.time()
    payload, tool, command, verdict = {}, None, "", {}
    offline, failure = False, None
    try:
        payload = json.loads(raw)
        tool = payload.get("tool_name") or "?"
        command = flatten(tool, payload.get("tool_input") or {})
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) and os.path.isabs(payload["cwd"]) \
            else os.getcwd()
        verdict, offline, failure = judge(command, cwd)
    except Exception as exc:  # an observer must never get in the way
        failure = type(exc).__name__
    if not isinstance(payload, dict):
        payload = {}

    masked = mask(command)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "id": payload.get("tool_use_id"),
        "session": str(payload.get("session_id") or "")[:8],
        "mode": payload.get("permission_mode"),
        "verdict": verdict.get("action") or "error",
        "risk": verdict.get("risk"),
        "policy": verdict.get("policy_id"),
        "source": verdict.get("verdict_source"),
        "reason": verdict.get("reason"),
        "tool": tool,
        "command": masked[:200],
        "command_full": masked[:FULL_MAX],
        "ms": int((time.time() - started) * 1000),
        "offline": offline,
    }
    if failure:
        entry["error"] = failure
    try:
        append_jsonl(OBSERVE_LOG, entry)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
