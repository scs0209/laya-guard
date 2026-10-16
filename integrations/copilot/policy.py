#!/usr/bin/env python3
"""laya-guard for GitHub Copilot CLI — preToolUse hook (command type).

Install (user): copy to ~/.copilot/hooks/laya-guard.py, chmod +x, and add
copilot/laya-guard.json to ~/.copilot/hooks/ (see that file).
Install (repo): .github/hooks/laya-guard.json instead.
Needs laya-guard on localhost:8787. Override: LAYA_GUARD_URL env.

Contract (per Copilot hooks reference): tool-call payload on stdin, decision
JSON on stdout with permissionDecision allow/deny/ask (reason required for
deny; ask prompts interactively, counts as deny headless). Crashes/nonzero
exits fail closed, timeouts fail open. review maps to ask.
Guard unreachable or slower than 10s -> `laya-guard check --offline`
(regex-flagged blocks, safe commands pass). A call it can't judge, or input
that can't be read, is asked about (LAYA_GUARD_FAIL=closed blocks it, =open
passes it).
"""
import json
import os
import subprocess
import sys
import urllib.request

GUARD_URL = os.environ.get("LAYA_GUARD_URL", "http://localhost:8787/guard")
GUARD_BIN = os.environ.get("LAYA_GUARD_BIN", "laya-guard")
FAIL_MODE = os.environ.get("LAYA_GUARD_FAIL", "ask")

AGENT = "copilot-cli"


def call_flags(call: dict | None) -> list[str]:
    """What the guard's file activity log needs to know about this call, as `laya-guard check` flags."""
    call = call or {}
    return (["--tool-name", call["tool_name"]] if call.get("tool_name") else []) + (["--shell"] if call.get("command") else [])


def shell_command(event: object) -> str | None:
    # The bare command of a shell tool call, or None when the event doesn't carry one.
    if isinstance(event, dict):
        for key in ("toolArgs", "tool_input"):
            args = event.get(key)
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = None
            if isinstance(args, dict):
                for name in ("command", "cmd", "script"):
                    if isinstance(args.get(name), str) and args[name]:
                        return args[name]
        if isinstance(event.get("command"), str) and event["command"]:
            return event["command"]
    return None


def make_call(event: object) -> dict:
    """The call as the activity log wants it: which tool, and the bare shell command when it is one."""
    if not isinstance(event, dict):
        return {}
    call = {"tool_name": event.get("toolName") or event.get("tool_name") or "bash", "command": shell_command(event)}
    return {k: v for k, v in call.items() if isinstance(v, str) and v}


def not_judged(why: str) -> dict:
    return {"action": {"open": "allow", "closed": "block"}.get(FAIL_MODE, "review"), "risk": 0.5,
            "policy_id": "offline", "reason": f"{why}; not judged"}


def extract_text(event: object) -> str:
    if isinstance(event, dict):
        # Copilot CLI sends {"toolName": "bash", "toolArgs": ...} for a camelCase `preToolUse` entry and
        # {"tool_name": "Bash", "tool_input": {...}} for a PascalCase `PreToolUse` one. The args can be a JSON string.
        for key in ("toolArgs", "tool_input"):
            args = event.get(key)
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = None
            if isinstance(args, dict):
                for name in ("command", "cmd", "script"):
                    if isinstance(args.get(name), str) and args[name]:
                        return args[name]
        for key in ("command", "tool_input", "input", "parameters"):
            val = event.get(key)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, dict):
                try:
                    return json.dumps(val, ensure_ascii=False)
                except (TypeError, ValueError):
                    pass
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


def judge(text: str, cwd: str, call: dict | None = None) -> dict | None:
    try:
        req = urllib.request.Request(
            GUARD_URL,
            data=json.dumps({"tool_call": text, "source": "tool", "cwd": cwd, "agent": AGENT, **(call or {})}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def judge_offline(text: str, cwd: str, call: dict | None = None) -> dict:
    # Server down or too slow: regex + safe-command check without the model.
    try:
        r = subprocess.run([GUARD_BIN, "check", "--offline", "--json", "--cwd", cwd, "--agent", AGENT, *call_flags(call)], input=text,
                           capture_output=True, text=True, timeout=10,
                           env=dict(os.environ, LAYA_GUARD_FAIL=FAIL_MODE))
        verdict = json.loads(r.stdout)
    except Exception:
        verdict = not_judged("guard server and CLI unavailable")
    print(f"laya-guard: server unreachable, offline check -> {verdict.get('action')}",
          file=sys.stderr)
    return verdict


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        event = None
    if event is None:
        verdict = not_judged("could not read the hook input")
    else:
        text = extract_text(event)
        cwd = extract_cwd(event)
        call = make_call(event)
        verdict = judge(text, cwd, call)
        if verdict is None:
            verdict = judge_offline(text, cwd, call)
    action = verdict.get("action")
    if action == "block":
        print(json.dumps({
            "permissionDecision": "deny",
            "permissionDecisionReason":
                f"laya-guard blocked [{verdict.get('policy_id')} risk={verdict.get('risk')}]: {verdict.get('reason')}",
        }))
    elif action == "review":
        print(json.dumps({
            "permissionDecision": "ask",
            "permissionDecisionReason":
                f"laya-guard review [{verdict.get('policy_id')} risk={verdict.get('risk')}]: {verdict.get('reason')}",
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
