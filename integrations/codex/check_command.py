#!/usr/bin/env python3
"""laya-guard for Codex CLI — PreToolUse hook (command type).

Install: copy to ~/.codex/hooks/check_command.py, chmod +x, and add the
[[hooks.PreToolUse]] block from config-snippet.toml to ~/.codex/config.toml
(requires [features] hooks = true). Needs laya-guard on localhost:8787.
Override: LAYA_GUARD_URL env (default http://localhost:8787/guard).

Contract (per Codex docs): JSON event on stdin; denial as JSON on stdout
with hookSpecificOutput.permissionDecision = deny; silence + exit 0 lets
the call through. PreToolUse has no ask lane, so review passes with a
stderr note. Unreadable input -> fail open (silence, exit 0).
Guard unreachable or slower than 10s -> `laya-guard check --offline`
(regex-flagged blocks, safe commands pass, the rest passes unless
LAYA_GUARD_FAIL=closed).
"""
import json
import os
import subprocess
import sys
import urllib.request

GUARD_URL = os.environ.get("LAYA_GUARD_URL", "http://localhost:8787/guard")
GUARD_BIN = os.environ.get("LAYA_GUARD_BIN", "laya-guard")
FAIL_CLOSED = os.environ.get("LAYA_GUARD_FAIL", "open") == "closed"


def extract_command(event: object) -> str:
    if isinstance(event, dict):
        tool_input = event.get("tool_input")
        if isinstance(tool_input, dict):
            cmd = tool_input.get("command")
            if isinstance(cmd, str) and cmd:
                return cmd
        # schema-agnostic fallback: judge the whole payload text
        try:
            return json.dumps(event, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(event)
    return str(event)


def judge(text: str) -> dict | None:
    try:
        req = urllib.request.Request(
            GUARD_URL,
            data=json.dumps({"tool_call": text, "source": "tool"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def judge_offline(text: str) -> dict:
    # Server down or too slow: regex + safe-command check without the model.
    try:
        r = subprocess.run([GUARD_BIN, "check", "--offline", "--json"], input=text,
                           capture_output=True, text=True, timeout=10)
        verdict = json.loads(r.stdout)
    except Exception:
        verdict = {"action": "block" if FAIL_CLOSED else "allow", "risk": 0.5,
                   "policy_id": "offline", "reason": "guard server and CLI unavailable; not judged"}
    print(f"laya-guard: server unreachable, offline check -> {verdict.get('action')}",
          file=sys.stderr)
    return verdict


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0  # unreadable input -> fail open
    text = extract_command(event)
    verdict = judge(text)
    if verdict is None:
        verdict = judge_offline(text)
    if verdict.get("action") == "block":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason":
                    f"laya-guard blocked [{verdict.get('policy_id')} risk={verdict.get('risk')}]: {verdict.get('reason')}",
            }
        }))
    elif verdict.get("action") == "review":
        print(f"laya-guard review [{verdict.get('policy_id')} risk={verdict.get('risk')}]: "
              f"{verdict.get('reason')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
