#!/usr/bin/env python3
"""laya-guard for Windsurf Cascade — pre_run_command hook.

Install (user): copy to a hooks dir, chmod +x, and add to
~/.codeium/windsurf/hooks.json (workspace: .devin/hooks.json):
  {"hooks": {"pre_run_command": [{"command": "<path>/guard.py",
                                  "show_output": true}]}}
Needs laya-guard on localhost:8787. Override: LAYA_GUARD_URL env.

Contract (per Cascade docs): JSON event on stdin (tool_info.command_line
for pre_run_command); exit 2 blocks (stderr shown as the message),
exit 0 proceeds. review passes with a stderr note. Unreadable input ->
fail open (exit 0).
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
        tool_info = event.get("tool_info")
        if isinstance(tool_info, dict):
            for key in ("command_line", "command", "cmd"):
                val = tool_info.get(key)
                if isinstance(val, str) and val:
                    return val
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
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0  # unreadable input -> fail open
    text = extract_command(event)
    verdict = judge(text)
    if verdict is None:
        verdict = judge_offline(text)
    action = verdict.get("action")
    if action == "block":
        print(f"laya-guard blocked [{verdict.get('policy_id')} risk={verdict.get('risk')}]: "
              f"{verdict.get('reason')}", file=sys.stderr)
        return 2
    if action == "review":
        print(f"laya-guard review [{verdict.get('policy_id')} risk={verdict.get('risk')}]: "
              f"{verdict.get('reason')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
