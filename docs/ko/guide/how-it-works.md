# 내부 구조

[← README로 돌아가기](../../../README.ko.md) · [English](../../guide/how-it-works.md) · **한국어**

판정 파이프라인, 정책 12종, 벤치마크, 다른 가드레일과의 비교입니다.

## 동작 구조

```
에이전트 tool_call
  → 에이전트의 훅, `laya-guard install`이 설치 (Claude Code는 guard-hook.sh, PreToolUse 훅;
    block이면 exit 2로 실행 취소, review면 권한 프롬프트)
  → POST /guard (localhost:8787)
  → 체인(`a && b`, `a; b`, `a || b`)은 명령별로 나눠 아래 단계를 거치고, 가장 나쁜 판정을 씀
  → policy.yaml: 정규식 프리필터 12종 (block 정책이 review 정책보다 우선)
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

안전 명령 레인([policy.yaml](../../../src/laya_guard/policy.yaml)의 `safe_commands`)은 셸
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

## 긴 입력과 너무 큰 입력

전체 256K자 또는 한 줄 16K자를 넘는 입력은 읽지 않고 차단합니다. 일부 정규식은 한 줄
길이에 대해 제곱 시간이라, 200KB 한 줄이 48초 걸려 훅 타임아웃을 넘길 수 있었습니다.

## 정책 (12종)

| id | 잡는 것 | 정규식 적중 시 |
|---|---|---|
| `destructive-fs` | `rm -rf /`, `rm -rf ~`/`$HOME` (플래그 순서 무관: `-fr`, `-r -f`, `--recursive --force`), `mkfs`, 디바이스 `dd`, 포크밤 | block |
| `pipe-to-shell` | `curl … \| sh`, `wget … \| sudo bash`, `bash -c "$(curl …)"`, `bash <(curl …)` | block |
| `exfiltration` | 터널, `nc`로 파이프, `curl -d @파일` 업로드 | review |
| `prompt-injection` | 이전 지시 무시, 탈옥, 한영 | block |
| `secrets-leak` | API 키, 개인키, 토큰, 키 파일(`~/.ssh`, `~/.aws` 등)을 `curl`/`wget`/`nc`로 파이프 | block |
| `payment-action` | 결제/환불/청구, 한영 | review |
| `remote-exec` | 수상한 패키지 인덱스, 원격 스크립트, 로컬 내용을 셸로 파이프, 인코딩된 PS | review |
| `priv-escalation` | `sudo sh`, `chmod 777`, setuid, `/etc/`로 리다이렉트 | review |
| `persistence` | cron, 서비스 등록, 셸rc 후킹, git `core.hooksPath`/`core.pager` | review |
| `mass-delete` | `DROP TABLE`, `s3 rm --recursive`, `kubectl delete --all`, `git push --force`, `git reset --hard`, `git clean -f`, `git checkout -- .` | review |
| `auth-bypass` | 인증/TLS 무력화, 비밀번호 초기화, 한영 | block |
| `credential-read` | 개인키·클라우드 자격 증명 읽기(`cat ~/.ssh/id_rsa`, `cat ~/.aws/credentials` 등) | review |

패턴은 전부 [policy.yaml](../../../src/laya_guard/policy.yaml)에 있습니다. 코드 수정 없이 추가·수정 가능.

## 벤치마크 (재현 가능, 좋은 것만 골라내지 않음)

| 스위트 | 결과 | 명령 |
|---|---|---|
| AgentDojo v1 공격 (심판 레벨) | **25/27 차단** | `eval/run_agentdojo.py` |
| AgentDojo v1 정상 (심판 레벨) | **92/97 유지** (allow+review) | `eval/run_agentdojo.py` |
| E2E 롤아웃 (qwen2.5:7b 에이전트 + 가드) | **4/4 시나리오** (1 방어, 2 미시도, 1 정상 OK) | `eval/run_rollout.py` → [결과](../../../eval/rollout_results.md) |
| 셸 명령 (자체: 일상 35 + 위험/우회 36, 그중 체인 10) | **69/71** (실전, `source=user`·`tool` 모두. 앞 50개는 안전 명령 레인 전 32/50·29/50) | `eval/run_eval.py --set shell` |
| 한국어 30문항 (자체) | **30/30** (MOCK·실전 모두) | `eval/run_eval.py` |

두 자체 셋은 검증 대상 규칙과 함께 만들어졌습니다(한국어 셋은 MOCK 휴리스틱과, 셸 셋은
안전 명령 레인·셸 정규식과). 정확도 주장이 아니라 회귀 게이트로 봐주세요. 외부 기준
수치는 AgentDojo입니다. 셸 셋 실패: `npx tsc --noEmit`이 검토/차단되고,
`cargo build --config` 러너 주입을 모델이 허용합니다.

놓친 2건은 문장만으로 정상 요청과 구분 불가한 캘린더 인젝션입니다 (출처 태깅이
정식 대응). 남은 오탐 5건은 대개 영어 프롬프트로, 모델이 한국어 위주로 튜닝된 탓입니다
(차단에 risk 합의를 요구한 뒤 24건에서 감소).
자세히는 [eval/](../../../eval/) 참조.

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
| 상태 | 개발 중, v0.5.0, MIT | MIT | ⚠️ 2025-05 아카이브 | ⚠️ 아카이브, 미유지보수 | 알파, Apache-2.0 |

이 표의 어떤 도구도 제3자 검증 AgentDojo 점수는 없습니다. 우리 수치도 자체 측정·심판
레벨입니다 ([한계](reference.md#한계-신뢰하기-전에-읽어주세요) 참조).
