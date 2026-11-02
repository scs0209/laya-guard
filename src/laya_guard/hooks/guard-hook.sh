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
# One python3 reads the hook input and prints, NUL-separated: whether the input could be read, the text to judge,
# the directory, the tool name, the request for the server, then the paths a file tool names. (python3 costs ~20 ms to
# start, so the hook starts it twice in all, not once per field.)
#   text to judge: for Bash, only the command that actually runs (description never runs); a file tool call, its name
#     and the paths it names (never the file's content: that is all that is sent); any other tool, its name and its
#     flattened input values.
#   directory: where to find the package.json scripts that npm test etc. run.
fields=()
while IFS= read -r -d '' f; do fields+=("$f"); done < <(python3 -c '
import json, os, sys
raw = sys.stdin.read()
ok, tool, cwd, name, paths = "1", "", os.getcwd(), None, []
try:
    d = json.loads(raw)
    a = d.get("tool_input", {})
    c = a.get("command") if isinstance(a, dict) else None
    flat = " ".join([str(v) for v in (a.values() if isinstance(a, dict) else [a])])
    tool = c if isinstance(c, str) and c else d.get("tool_name", "") + ": " + flat
except Exception:
    ok = "0"
try:
    c = d.get("cwd")
    cwd = c if isinstance(c, str) and os.path.isabs(c) else cwd
    n = d.get("tool_name")
    name = n if isinstance(n, str) else None
    a = d.get("tool_input")
    if name in {"Read", "Edit", "MultiEdit", "Write", "NotebookEdit", "NotebookRead", "Glob", "Grep"} and isinstance(a, dict):
        paths = [a[k] for k in ("file_path", "path", "notebook_path") if isinstance(a.get(k), str) and a[k].strip()]
except Exception:
    pass
if paths:
    tool = name + ": " + " ".join(paths)
tool = tool.rstrip("\n")
p = {"tool_call": tool.strip(), "cwd": cwd, "agent": "claude-code"}
if name:
    p["tool_name"] = name
if paths:
    p["paths"] = paths
if name == "Bash":
    p["command"] = p["tool_call"]
sys.stdout.write("\0".join([ok, tool, cwd, name or "", json.dumps(p)] + paths) + "\0")
' <<< "$input" 2>/dev/null)
if [ "${#fields[@]}" -lt 5 ]; then
  fields=(0 "" "$(pwd)" "" "")          # no python3: nothing to send, so the server can't answer and the CLI decides
fi
tool=${fields[1]}
cwd=${fields[2]}
name=${fields[3]}
payload=${fields[4]}
paths=("${fields[@]:5}")
if [ "${fields[0]}" != 1 ]; then
  # the input wasn't JSON we can read: judging an empty command would pass it, so the fail mode decides
  tool=""
  if [ "$unjudged" != allow ]; then
    offline=1
    action=$unjudged
    res="could not parse the hook input; not judged"
  fi
fi
review_json=""
if [ -z "$action" ]; then
  if ! res=$(printf '%s' "$payload" | curl -sf -m 10 -X POST "$url" -H 'Content-Type: application/json' --data-binary @-); then
    offline=1
    # The offline check applies the same protected paths: pass the tool name and the paths along.
    name_args=()
    path_args=()
    [ -n "$name" ] && name_args=(--tool-name "$name")
    [ "$name" = Bash ] && name_args+=(--shell)
    for p in "${paths[@]}"; do path_args+=(--path "$p"); done
    res=$(printf '%s' "$tool" | LAYA_GUARD_FAIL="$fail" "${LAYA_GUARD_BIN:-laya-guard}" check --offline --json --cwd "$cwd" --agent claude-code "${name_args[@]}" "${path_args[@]}" 2>/dev/null)
  fi
  # The second python3: the action, and for a review the answer that asks a human through Claude Code's prompt.
  answer=$(RES="$res" python3 -c '
import json, os
d = json.loads(os.environ["RES"])
action = d.get("action", "allow")
print(action)
if action == "review":
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": "laya-guard: review (%s) - %s" % (d.get("policy_id") or "general",
                                                                        d.get("reason") or "needs review")}}))
' 2>/dev/null)
  action=${answer%%$'\n'*}
  [ "$answer" != "$action" ] && review_json=${answer#*$'\n'}
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
  # res is one of the fixed messages above when it isn't the server's or the CLI's JSON
  [ -n "$review_json" ] || review_json="{\"hookSpecificOutput\": {\"hookEventName\": \"PreToolUse\", \"permissionDecision\": \"ask\", \"permissionDecisionReason\": \"laya-guard: review (offline) - ${res:-needs review}\"}}"
  printf '%s\n' "$review_json"
fi
exit 0
