#!/bin/bash
# Claude Code PreToolUse hook → laya-guard 심판.
# block이면 exit 2(실행 취소), 그 외는 통과. 서버: $LAYA_GUARD_URL (기본 localhost:8787)
# 서버가 없거나 10초 안에 답이 없으면 `laya-guard check --offline`으로 대체:
# 정규식 적중은 차단, 안전 명령은 통과, 나머지는 통과 (LAYA_GUARD_FAIL=closed면 차단).
url="${LAYA_GUARD_URL:-http://localhost:8787/guard}"
input=$(cat)
# Bash는 실제 실행될 command만 심판 (description은 실행되지 않음). 그 외 도구는 이름+입력값 평탄화.
tool=$(echo "$input" | python3 -c "import json,sys; d=json.load(sys.stdin); a=d.get('tool_input',{}); c=a.get('command') if isinstance(a,dict) else None; flat=' '.join([str(v) for v in (a.values() if isinstance(a,dict) else [a])]); print(c if isinstance(c,str) and c else d.get('tool_name','')+': '+flat)")
payload=$(python3 -c 'import json,sys; print(json.dumps({"tool_call": sys.stdin.read().strip()}))' <<< "$tool")
offline=0
if ! res=$(printf '%s' "$payload" | curl -sf -m 10 -X POST "$url" -H 'Content-Type: application/json' --data-binary @-); then
  offline=1
  res=$(printf '%s' "$tool" | "${LAYA_GUARD_BIN:-laya-guard}" check --offline --json 2>/dev/null)
fi
action=$(printf '%s' "$res" | python3 -c "import json,sys; print(json.load(sys.stdin).get('action','allow'))" 2>/dev/null)
if [ -z "$action" ]; then
  if [ "${LAYA_GUARD_FAIL:-open}" = closed ]; then action=block; else action=allow; fi
  res="guard server and CLI unavailable; not judged"
fi
if [ "$offline" = 1 ]; then
  echo "laya-guard: server unreachable, offline check -> $action" >&2
fi
if [ "$action" = "block" ]; then
  echo "laya-guard: blocked ($res)" >&2
  exit 2
fi
exit 0
