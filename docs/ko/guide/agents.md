# 에이전트 연결

[← README로 돌아가기](../../../README.ko.md) · [English](../../guide/agents.md) · **한국어**

`laya-guard install`이 에이전트마다 무엇을 연결하고 쓰는지, 그리고 다른 에이전트 루프에서 가드를 부르는 방법입니다.

## 지원 에이전트

| 에이전트 | 설치 | 연동 | `review` 처리 |
|---|---|---|---|
| Claude Code | `laya-guard install claude-code` | [PreToolUse 훅](../../../guard-hook.sh) | `ask` (권한 프롬프트, `LAYA_GUARD_REVIEW=pass`면 통과) |
| Codex CLI | `laya-guard install codex` | [`PreToolUse` 훅](../../../integrations/codex/) | allow + stderr 안내 (ask 불가) |
| Cursor | `laya-guard install cursor` | [`beforeShellExecution` 훅](../../../integrations/cursor/) | `ask` (사람 승인) |
| Gemini CLI | `laya-guard install gemini-cli` | [`BeforeTool` 훅](../../../integrations/gemini/check.py) | exit 0 + stderr 안내 |
| Qwen Code | `laya-guard install qwen-code` | [`PreToolUse` 훅](../../../integrations/qwen/check.py) | `ask` (headless에선 deny) |
| Windsurf | `laya-guard install windsurf` | [`pre_run_command` 훅](../../../integrations/windsurf/guard.py) | exit 0 + stderr 안내 |
| Copilot CLI | `laya-guard install copilot-cli` | [`preToolUse` 훅](../../../integrations/copilot/policy.py) | `ask` (headless에선 deny) |
| Cline | `laya-guard install cline` | [`PreToolUse` 실행파일](../../../integrations/cline/PreToolUse) | 통과 (`cancel: false`) |
| OpenCode | `laya-guard install opencode` | [`tool.execute.before` 플러그인](../../../integrations/opencode/laya-guard.ts) | allow + stderr 경고 |
| Kilo Code | `laya-guard install kilo` | [`tool.execute.before` 플러그인](../../../integrations/kilo/laya-guard.ts) | allow + stderr 경고 |
| Amp | `laya-guard install amp` | [`tool.call` 플러그인](../../../integrations/amp/laya-guard.ts) | allow + stderr 안내 |

훅 메커니즘이 없어 어댑터 불가: Aider, Roo Code(2026-05 종료), Continue(단계제만,
저장소 아카이브), Junie(allow/ask만). 자세히는 [integrations/](../../../integrations/).

`laya-guard install <에이전트>`가 설정을 대신 해줍니다([무엇을 쓰고 무엇이 남는지](#1c-나머지-에이전트-전부)). 에이전트별
수동 설치법은 [integrations/](../../../integrations/)에 있습니다. `review`를 사람에게 넘기는 것은 Claude Code, Cursor, Qwen Code,
Copilot CLI이고(답할 사람이 없으면 거부), 나머지는 경고 후 통과하니 강제 승인이 필요하면 각 도구의
`permission`/`allowlist` 규칙을 함께 쓰세요.

## 사용처

### 1. Claude Code 가드 (PreToolUse 훅)

```bash
laya-guard install claude-code             # ~/.claude/settings.json. diff를 보여주고 먼저 묻습니다
laya-guard install claude-code --scope local   # ./.claude/settings.local.json (project도 가능)
laya-guard status --check                  # 어디에 설치됐는지, 서버는 떠 있는지, 훅이 동작하는지
laya-guard uninstall claude-code           # `install`이 추가한 것만 제거
```

`install`은 `Bash` 도구용으로 훅을 등록하고(`--matcher`로 변경, `--files`로 파일 도구 추가: [파일 가이드](file-protection.md#에이전트의-파일-도구로부터-파일-보호하기-선택)), 절대 경로를 씁니다(훅은 가상환경의
`PATH`를 물려받지 못하는 경우가 많습니다). 변경을 diff로 보여주고, 파일을 백업하고, 쓰기 전에
확인합니다(`--dry-run`은 보기만, `--yes`는 확인 생략). 기존 훅에 병합하고 덮어쓰지 않으며, 손으로
쓴 `guard-hook.sh` 항목은 `--force` 없이는 건드리지 않습니다. 끝나면 훅에 시험 호출 두 개(무해한 것
하나, 다운로드를 셸로 넘기는 것 하나)를 보내 종료 코드를 확인합니다. 훅이 bash 스크립트라 Windows는
아직 지원하지 않습니다. 직접 하려면:

[guard-hook.sh](../../../guard-hook.sh)를 PreToolUse 훅으로 등록하세요. tool 페이로드를
stdin으로 읽어 로컬 서버에 POST하고, `block`이면 exit 2로 해당 실행을 취소합니다.
`review`면 `permissionDecision: "ask"` JSON을 출력해 Claude Code가 정책과 사유가 담긴 권한 프롬프트로 사람에게 묻습니다(`LAYA_GUARD_REVIEW=pass`면 예전처럼 통과). `allow`는 조용히 통과합니다. 응답할 사람이 없는 비대화형(`claude -p`)에서는 ask가 거부됩니다. Bash 도구는 에이전트가 쓴 설명이 아니라 명령만 심판합니다. 서버에 닿지 않거나 10초 안에 답이 없으면 오프라인 검사로 대체합니다([서버 장애](reliability.md#서버-장애) 참조).
[한계](reference.md#한계-신뢰하기-전에-읽어주세요)를 먼저 읽어주세요. 서버 주소는 `LAYA_GUARD_URL`로 바꿀 수 있습니다.

### 1b. Cursor 가드 (beforeShellExecution 훅)

```bash
laya-guard install cursor              # ~/.cursor/hooks.json. diff를 보여주고 먼저 묻습니다
laya-guard install cursor --scope project   # ./.cursor/hooks.json (Cursor에는 local 범위가 없음)
laya-guard status --agent cursor --check
laya-guard uninstall cursor
```

안전장치는 위와 같습니다(diff, 백업, 테스트 호출, `--dry-run`, `--yes`, 자기가 넣은 항목만 제거). Cursor에서
다른 점은 이렇습니다. Cursor는 조건에 맞는 훅을 전부 실행하므로, laya-guard 항목은 다른 도구가
`beforeShellExecution`에 등록한 항목 옆에 들어가고 Cursor는 가장 엄격한 답을 따릅니다(`deny`가 `ask`보다,
`ask`가 `allow`보다 우선). 훅 스크립트는 Cursor 형식으로 답합니다. 차단은 사용자용과 에이전트용 메시지가 담긴
`deny`, `review`는 `ask`, 나머지는 `allow`이고, 권한 훅의 출력이 유효한 JSON이 아니면 Cursor가 명령을 막기
때문에 항상 유효한 JSON 하나만 출력합니다. Cursor의 `hooks.json`은 엄격한 JSON이 아닌 경우가 많은데(다른
도구가 끝에 쉼표를 남김) `install`은 그래도 읽고, 쉼표를 없애고 다시 쓴다는 점을 먼저 알려줍니다.
[integrations/cursor/](../../../integrations/cursor/)의 어댑터를 직접 등록해 둔 경우는 알려주기만 하고
`--force` 없이는 건드리지 않아 판정이 두 번 돌지 않습니다. 테스트 호출은 종료 코드가 아니라 답(`allow` /
`deny`)을 확인합니다. 이 훅은 Cursor 앱에 적용되며, Cursor CLI가 `beforeShellExecution` 훅을 실행하는지는
Cursor 문서에 명시돼 있지 않아 거기서는 확인하지 않았습니다.

### 1c. 나머지 에이전트 전부

```bash
laya-guard install gemini-cli     # 그 밖에: codex, qwen-code, windsurf, copilot-cli, cline, opencode, kilo, amp
laya-guard status --check         # 이 컴퓨터에 뭐가 설치돼 있고 각 훅이 제대로 답하는지
```

에이전트마다 명령 하나이고 안전장치는 위와 같습니다. diff를 먼저 보여주고, 바꾸는 파일은 백업하고, 절대 경로를
쓰고, 다른 도구의 항목 옆에 병합하며, `uninstall`은 자기가 넣은 것만 지웁니다. 무엇을 쓰는지, 그리고 직접 해야 하는
한 가지는 다음과 같습니다.

| 에이전트 | `laya-guard install`이 쓰는 것 | 직접 할 일 |
|---|---|---|
| Claude Code | `~/.claude/settings.json`의 훅 항목 | 열려 있으면 재시작 |
| Codex CLI | `~/.codex/hooks.json` 항목(훅을 `config.toml`에 두는 경우는 표시된 블록) | `/hooks`로 신뢰 처리(새 훅은 Codex가 먼저 물음) |
| Cursor | `~/.cursor/hooks.json` 항목 | — |
| Gemini CLI | `~/.gemini/settings.json` 항목과 `tools.enableHooks`(옛 버전은 훅이 꺼져 있음) | 재시작 |
| Qwen Code | `~/.qwen/settings.json` 항목 | 재시작 또는 `/hooks` |
| Windsurf | `~/.codeium/windsurf/hooks.json` 항목 | 창 다시 불러오기(제한 모드에서는 훅이 안 돎) |
| Copilot CLI | 전용 파일 `~/.copilot/hooks/laya-guard.json` | 새 세션 시작 |
| Cline | 실행 파일 `~/Documents/Cline/Hooks/PreToolUse` | Cline의 Hooks 탭에서 켜기 |
| OpenCode | `~/.config/opencode/plugins/laya-guard.ts` | 재시작 |
| Kilo Code | `~/.config/kilo/plugin/laya-guard.ts` | 재시작 |
| Amp | `~/.config/amp/plugins/laya-guard.ts` | "plugins: reload" 실행 |

`--scope project`는 프로젝트 쪽 위치(`.codex/`, `.gemini/`, `.github/hooks/`, `.amp/plugins/` 등)에 씁니다. 훅은 설치
직후 실제 어댑터로 테스트 호출 두 건을 보내 확인하고, 플러그인(OpenCode, Kilo, Amp)은 에이전트만 실행할 수
있어서 `install`이 그렇다고 알려줍니다. 패키지에 들어 있는 어댑터는 [integrations/](../../../integrations/)의 것과
같습니다(테스트가 보장). **테스트에서 에이전트를 실제로 실행하지는 않습니다.** 형식은 각 에이전트의 현재 문서와, 다른
도구가 실제 컴퓨터에 남긴 파일에서 가져왔고, Codex, Qwen Code, Windsurf, Cline, Kilo는 앱에서 직접 돌려보지 않았다고
`install`이 알려줍니다. 특히 Cline의 훅은 문서가 플러그인 쪽으로 옮겨가는 중입니다.

### 2. 아무 에이전트 루프 가드 (HTTP)

매 스텝 HTTP 호출이 되는 프레임워크(OpenClaw, OpenCode, LangChain/LlamaIndex
루프, 직접 만든 하네스)는 tool 실행을 이렇게 막을 수 있습니다:

```python
import requests

def guard(tool_call: str, source: str = "tool") -> None:
    v = requests.post("http://localhost:8787/guard",
                      json={"tool_call": tool_call, "source": source},
                      timeout=60).json()
    if v["action"] == "block":
        raise RuntimeError(f"blocked by {v['policy_id']}: {v['reason']}")
    if v["action"] == "review":
        ask_human(tool_call)  # 승인 단계 연결
```

도구 결과물에서 나온 호출은 `source="email"`/`"web"`/`"tool"`로 보내면 임계값이
강화됩니다. 간접 인젝션을 막는 장치가 바로 이것입니다.

### 3. 명령 묶음 감사 (CLI)

```bash
while IFS= read -r cmd; do
  laya-guard check "$cmd" || echo "BLOCKED: $cmd"
done < commands.txt
```

(`check`는 block이면 exit 2라 `||`/`&&`·`xargs`와 조합됩니다.)

### 4. 자동화 게이트 (한국어 셋)

CI가 이미 이렇게 돕니다. `eval/run_eval.py --min-acc 1.0`은 판정 정확도가 깨지면
빌드를 실패시킵니다. 정책 수정 때도 로컬에서 같은 명령으로 검증하세요.
