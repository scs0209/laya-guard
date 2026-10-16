#!/bin/bash
# Cursor beforeShellExecution hook → laya-guard judge.
# Cursor sends {"command": ..., "cwd": ..., ...} on stdin and reads {"permission": "allow|deny|ask",
# "user_message": ..., "agent_message": ...} from stdout (https://cursor.com/docs/agent/hooks).
# A permission hook whose output isn't valid JSON blocks the command, so every path below prints
# exactly one JSON object and exits 0. block -> deny, review -> ask (LAYA_GUARD_REVIEW=pass lets
# it through unasked), everything else -> allow. Server: $LAYA_GUARD_URL (default localhost:8787).
# If the server is down or silent for 10 seconds it falls back to `laya-guard check --offline`:
# a regex hit is denied, a safe command is allowed, and anything it can't judge is asked about
# (LAYA_GUARD_FAIL=closed denies it, =open allows it). Input without a readable command follows the
# same rule. An offline ask is denied when LAYA_GUARD_REVIEW=pass, since nobody would be asked.
url="${LAYA_GUARD_URL:-http://localhost:8787/guard}"
fail="${LAYA_GUARD_FAIL:-ask}"
case "$fail" in open) unjudged=allow ;; closed) unjudged=block ;; *) unjudged=review ;; esac
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
    policy, reason = "offline", res or "no reason given"
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

offline=0
action=""
if [ -z "$cmd" ]; then          # not a command we can read: the fail mode decides
  offline=1
  action=$unjudged
  res="the hook input had no command; not judged"
else
  # Every call this hook sees is a shell command: say so (and which agent), so the opt-in file activity log can read paths off it.
  payload=$(CWD="$cwd" python3 -c 'import json,os,sys
c = sys.stdin.read().strip()
print(json.dumps({"tool_call": c, "cwd": os.environ["CWD"], "agent": "cursor", "tool_name": "beforeShellExecution", "command": c}))' <<< "$cmd")
  if ! res=$(printf '%s' "$payload" | curl -sf -m 10 -X POST "$url" -H 'Content-Type: application/json' --data-binary @-); then
    offline=1
    res=$(printf '%s' "$cmd" | LAYA_GUARD_FAIL="$fail" "${LAYA_GUARD_BIN:-laya-guard}" check --offline --json --cwd "$cwd" --agent cursor --tool-name beforeShellExecution --shell 2>/dev/null)
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
answer "$action" "$res"
exit 0
