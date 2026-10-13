#!/usr/bin/env python3
"""laya-guard for Cline — PreToolUse hook (VS Code extension and CLI).

Install (VS Code workspace): copy to .clinerules/hooks/PreToolUse, chmod +x.
Install (VS Code global): ~/Documents/Cline/Hooks/PreToolUse. Enable hooks in
Cline Features settings. (CLI paths differ: .cline/hooks/, ~/.cline/hooks/.)
Needs laya-guard on localhost:8787. Override: LAYA_GUARD_URL env.

Contract (per Cline docs): JSON event on stdin (preToolUse.toolName and
preToolUse.parameters); print {"cancel": true/false} (+ errorMessage) and
exit 0. No ask lane exists, so review passes.
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


def extract_text(event: object) -> str:
    if isinstance(event, dict):
        pre = event.get("preToolUse")
        if isinstance(pre, dict):
            tool = pre.get("toolName", "")
            params = pre.get("parameters", {})
            try:
                return f"{tool}({json.dumps(params, ensure_ascii=False)})"
            except (TypeError, ValueError):
                return f"{tool}({params})"
        try:
            return json.dumps(event, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(event)
    return str(event)


def shell_command(event: object) -> str | None:
    # The offline safe-command list matches a bare command, not the tool-call wrapper the model sees.
    pre = event.get("preToolUse") if isinstance(event, dict) else None
    if isinstance(pre, dict) and pre.get("toolName") == "execute_command":
        params = pre.get("parameters")
        if isinstance(params, dict) and isinstance(params.get("command"), str):
            return params["command"] or None
    return None


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
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        event = None
    if event is None:
        verdict = not_judged("could not read the hook input")
    else:
        text = extract_text(event)
        cwd = extract_cwd(event)
        verdict = judge(text, cwd)
        if verdict is None:
            verdict = judge_offline(shell_command(event) or text, cwd)
    if verdict.get("action") == "block":
        print(json.dumps({
            "cancel": True,
            "errorMessage": f"laya-guard blocked [{verdict.get('policy_id')} risk={verdict.get('risk')}]: {verdict.get('reason')}",
        }))
    else:
        print(json.dumps({"cancel": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
