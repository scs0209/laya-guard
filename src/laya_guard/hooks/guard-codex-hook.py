#!/usr/bin/env python3
"""laya-guard for Codex CLI — PreToolUse hook (command type).

Install: copy to ~/.codex/hooks/check_command.py, chmod +x, and add the
[[hooks.PreToolUse]] block from config-snippet.toml to ~/.codex/config.toml
(requires [features] hooks = true). Needs laya-guard on localhost:8787.
Override: LAYA_GUARD_URL env (default http://localhost:8787/guard).

Contract (per Codex docs): JSON event on stdin; denial as JSON on stdout
with hookSpecificOutput.permissionDecision = deny; silence + exit 0 lets
the call through. PreToolUse has no ask lane, so review passes with a
stderr note.
Guard unreachable or slower than 10s -> `laya-guard check --offline`
(regex-flagged blocks, safe commands pass). A call it can't judge, or input
that can't be read, has nobody to ask here, so it is blocked unless
LAYA_GUARD_FAIL=open.
"""
import json
import os
import subprocess
import sys
import urllib.request

GUARD_URL = os.environ.get("LAYA_GUARD_URL", "http://localhost:8787/guard")
GUARD_BIN = os.environ.get("LAYA_GUARD_BIN", "laya-guard")
FAIL_MODE = os.environ.get("LAYA_GUARD_FAIL", "ask")
NO_PROMPT = "this agent has no approval prompt, so it is blocked (LAYA_GUARD_FAIL=open lets it through)"


def not_judged(why: str) -> dict:
    return {"action": "allow" if FAIL_MODE == "open" else "block", "risk": 0.5,
            "policy_id": "offline", "reason": f"{why}; not judged" + ("" if FAIL_MODE == "open" else f", {NO_PROMPT}")}


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


def extract_cwd(event: object) -> str:
    # Where the command runs: the tool's own directory if given, else the event's cwd.
    base = os.getcwd()
    if not isinstance(event, dict):
        return base
    roots = event.get("workspace_roots") or event.get("workspaceRoots")
    for val in (event.get("cwd"), roots[0] if isinstance(roots, list) and roots else None):
        if isinstance(val, str) and os.path.isabs(val):
            base = val
            break
    for args in (event.get("tool_input"), event.get("tool_info")):
        if isinstance(args, dict):
            for key in ("cwd", "workdir", "directory"):
                val = args.get(key)
                if isinstance(val, str) and val:
                    return os.path.normpath(os.path.join(base, os.path.expanduser(val)))
    return base


def judge(text: str, cwd: str) -> dict | None:
    try:
        req = urllib.request.Request(
            GUARD_URL,
            data=json.dumps({"tool_call": text, "source": "tool", "cwd": cwd}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def judge_offline(text: str, cwd: str) -> dict:
    # Server down or too slow: regex + safe-command check without the model.
    try:
        r = subprocess.run([GUARD_BIN, "check", "--offline", "--json", "--cwd", cwd], input=text,
                           capture_output=True, text=True, timeout=10,
                           env=dict(os.environ, LAYA_GUARD_FAIL=FAIL_MODE))
        verdict = json.loads(r.stdout)
    except Exception:
        verdict = not_judged("guard server and CLI unavailable")
    if verdict.get("action") == "review":
        verdict = dict(verdict, action="block", reason=f"server offline, not judged by the model; {NO_PROMPT}")
    print(f"laya-guard: server unreachable, offline check -> {verdict.get('action')}",
          file=sys.stderr)
    return verdict


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        event = None
    if event is None:
        verdict = not_judged("could not read the hook input")
    else:
        text = extract_command(event)
        cwd = extract_cwd(event)
        verdict = judge(text, cwd)
        if verdict is None:
            verdict = judge_offline(text, cwd)
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
