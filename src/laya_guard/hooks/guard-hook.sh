#!/bin/bash
# Claude Code PreToolUse hook → laya-guard verdict.
# block: exit 2 (the call is cancelled). review: ask a human through Claude Code's permission
# prompt (stdout JSON permissionDecision "ask", exit 0). LAYA_GUARD_REVIEW=pass lets it through
# without asking, as before. allow: let it through. Server: $LAYA_GUARD_URL (default localhost:8787)
# Note: when nobody can answer (non-interactive `claude -p`), ask is treated as a deny.
# If the server is missing or doesn't answer within 10s, fall back to `laya-guard check --offline`:
# regex hits are blocked, safe commands pass, and everything else it can't judge asks a human
# (LAYA_GUARD_FAIL=closed blocks, =open passes). Unreadable input follows the same rule.
# A call the offline check would ask about is blocked, not passed, even with LAYA_GUARD_REVIEW=pass.
url="${LAYA_GUARD_URL:-http://localhost:8787/guard}"
fail="${LAYA_GUARD_FAIL:-ask}"
case "$fail" in open) unjudged=allow ;; closed) unjudged=block ;; *) unjudged=review ;; esac
input=$(cat)
offline=0
action=""
# For Bash, judge only the command that actually runs (description never runs). Other tools: name + flattened input values.
if ! tool=$(echo "$input" | python3 -c "import json,sys; d=json.load(sys.stdin); a=d.get('tool_input',{}); c=a.get('command') if isinstance(a,dict) else None; flat=' '.join([str(v) for v in (a.values() if isinstance(a,dict) else [a])]); print(c if isinstance(c,str) and c else d.get('tool_name','')+': '+flat)"); then
  # the input wasn't JSON we can read: judging an empty command would pass it, so the fail mode decides
  tool=""
  if [ "$unjudged" != allow ]; then
    offline=1
    action=$unjudged
    res="could not parse the hook input; not judged"
  fi
fi
if [ -z "$action" ]; then
  # Directory the command runs in: where to find the package.json scripts that npm test etc. run.
  cwd=$(echo "$input" | python3 -c "import json,os,sys; c=json.load(sys.stdin).get('cwd'); print(c if isinstance(c,str) and os.path.isabs(c) else os.getcwd())" 2>/dev/null || pwd)
  # The tool's name and, for the file tools, the paths it names (never their content): the server checks them against
  # the protected paths.
  meta=$(echo "$input" | python3 -c '
import json, sys
d = json.load(sys.stdin)
a = d.get("tool_input")
name = d.get("tool_name")
files = {"Read", "Edit", "MultiEdit", "Write", "NotebookEdit", "NotebookRead", "Glob", "Grep"}
paths = []
if name in files and isinstance(a, dict):
    paths = [a[k] for k in ("file_path", "path", "notebook_path") if isinstance(a.get(k), str) and a[k].strip()]
print(json.dumps({"tool_name": name if isinstance(name, str) else None, "paths": paths}))
' 2>/dev/null) || meta='{}'
  # A file tool call is judged by the paths it names, not by the file's content: that is all that is sent.
  only=$(META="$meta" python3 -c 'import json,os
m = json.loads(os.environ.get("META") or "{}")
if m.get("paths"):
    print(m["tool_name"] + ": " + " ".join(m["paths"]))' 2>/dev/null)
  [ -n "$only" ] && tool="$only"
  payload=$(CWD="$cwd" META="$meta" python3 -c 'import json,os,sys
m = json.loads(os.environ.get("META") or "{}")
p = {"tool_call": sys.stdin.read().strip(), "cwd": os.environ["CWD"]}
if m.get("tool_name"):
    p["tool_name"] = m["tool_name"]
if m.get("paths"):
    p["paths"] = m["paths"]
print(json.dumps(p))' <<< "$tool")
  if ! res=$(printf '%s' "$payload" | curl -sf -m 10 -X POST "$url" -H 'Content-Type: application/json' --data-binary @-); then
    offline=1
    # The offline check applies the same protected paths: pass the tool name and the paths along.
    name_args=()
    path_args=()
    name=$(META="$meta" python3 -c 'import json,os; print(json.loads(os.environ.get("META") or "{}").get("tool_name") or "")' 2>/dev/null)
    [ -n "$name" ] && name_args=(--tool-name "$name")
    while IFS= read -r -d '' p; do path_args+=(--path "$p"); done < <(META="$meta" python3 -c 'import json,os,sys
for x in json.loads(os.environ.get("META") or "{}").get("paths", []):
    sys.stdout.write(x + "\0")' 2>/dev/null)
    res=$(printf '%s' "$tool" | LAYA_GUARD_FAIL="$fail" "${LAYA_GUARD_BIN:-laya-guard}" check --offline --json --cwd "$cwd" "${name_args[@]}" "${path_args[@]}" 2>/dev/null)
  fi
  action=$(printf '%s' "$res" | python3 -c "import json,sys; print(json.load(sys.stdin).get('action','allow'))" 2>/dev/null)
  if [ -z "$action" ]; then
    action=$unjudged
    res="guard server and CLI unavailable; not judged"
  fi
  if [ "$offline" = 1 ]; then
    echo "laya-guard: server unreachable, offline check -> $action" >&2
  fi
fi
if [ "$offline" = 1 ] && [ "$action" = "review" ] && [ "${LAYA_GUARD_REVIEW:-ask}" = "pass" ]; then
  action=block
  case "$res" in "{"*) res="server offline, not judged by the model" ;; esac
  res="$res; LAYA_GUARD_REVIEW=pass means nobody is asked"
fi
if [ "$action" = "block" ]; then
  echo "laya-guard: blocked ($res)" >&2
  exit 2
fi
if [ "$action" = "review" ] && [ "${LAYA_GUARD_REVIEW:-ask}" != "pass" ]; then
  RES="$res" python3 -c '
import json, os
try:
    d = json.loads(os.environ["RES"])
    policy, reason = d.get("policy_id") or "general", d.get("reason") or "needs review"
except Exception:
    policy, reason = "offline", os.environ["RES"] or "needs review"
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "laya-guard: review (%s) - %s" % (policy, reason)}}))
'
fi
exit 0
