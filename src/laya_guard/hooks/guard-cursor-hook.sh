#!/bin/bash
# Cursor beforeShellExecution hook → laya-guard judge.
# Cursor sends {"command": ..., "cwd": ..., ...} on stdin and reads {"permission": "allow|deny|ask",
# "user_message": ..., "agent_message": ...} from stdout (https://cursor.com/docs/agent/hooks).
# A permission hook whose output isn't valid JSON blocks the command, so every path below prints
# exactly one JSON object and exits 0. block -> deny, review -> ask (LAYA_GUARD_REVIEW=pass lets
# it through unasked), everything else -> allow. Server: $LAYA_GUARD_URL (default localhost:8787).
# If the server is down or silent for 10 seconds it falls back to `laya-guard check --offline`:
# a regex hit is denied, a safe command is allowed, the rest is allowed (LAYA_GUARD_FAIL=closed denies).
url="${LAYA_GUARD_URL:-http://localhost:8787/guard}"
input=$(cat)
cmd=$(printf '%s' "$input" | python3 -c "import json,sys; c=json.load(sys.stdin).get('command'); print(c if isinstance(c,str) else '')" 2>/dev/null)
cwd=$(printf '%s' "$input" | python3 -c "import json,os,sys; c=json.load(sys.stdin).get('cwd'); print(c if isinstance(c,str) and os.path.isabs(c) else os.getcwd())" 2>/dev/null || pwd)

# one place that prints the answer: ACTION is allow | block | review, RES the verdict (JSON) or a note
answer() {
  ACTION="$1" RES="$2" python3 -c '
import json, os
action, res = os.environ["ACTION"], os.environ["RES"]
try:
    d = json.loads(res)
    policy, reason = d.get("policy_id") or "general", d.get("reason") or "no reason given"
except Exception:
    policy, reason = "general", res or "no reason given"
out = {"permission": "allow"}
if action == "block":
    out = {"permission": "deny",
           "user_message": "laya-guard blocked this command (%s): %s" % (policy, reason),
           "agent_message": "laya-guard blocked this command (%s): %s. Do not run it unchanged; "
                            "tell the user what you wanted to do and why." % (policy, reason)}
elif action == "review" and os.environ.get("LAYA_GUARD_REVIEW", "ask") != "pass":
    out = {"permission": "ask",
           "user_message": "laya-guard: review (%s) - %s" % (policy, reason),
           "agent_message": "laya-guard asked the user to confirm this command (%s): %s" % (policy, reason)}
print(json.dumps(out))
'
}

if [ -z "$cmd" ]; then          # not a command we can read: nothing to judge
  if [ "${LAYA_GUARD_FAIL:-open}" = closed ]; then answer block "the hook input had no command"; else answer allow ""; fi
  exit 0
fi

payload=$(CWD="$cwd" python3 -c 'import json,os,sys; print(json.dumps({"tool_call": sys.stdin.read().strip(), "cwd": os.environ["CWD"]}))' <<< "$cmd")
offline=0
if ! res=$(printf '%s' "$payload" | curl -sf -m 10 -X POST "$url" -H 'Content-Type: application/json' --data-binary @-); then
  offline=1
  res=$(printf '%s' "$cmd" | "${LAYA_GUARD_BIN:-laya-guard}" check --offline --json --cwd "$cwd" 2>/dev/null)
fi
action=$(printf '%s' "$res" | python3 -c "import json,sys; print(json.load(sys.stdin).get('action','allow'))" 2>/dev/null)
if [ -z "$action" ]; then
  if [ "${LAYA_GUARD_FAIL:-open}" = closed ]; then action=block; else action=allow; fi
  res="guard server and CLI unavailable; not judged"
fi
if [ "$offline" = 1 ]; then
  echo "laya-guard: server unreachable, offline check -> $action" >&2
fi
answer "$action" "$res"
exit 0
