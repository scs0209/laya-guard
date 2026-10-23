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
  → 검토를 사람에게 묻는 에이전트(Claude Code, Cursor, Qwen Code, Copilot CLI)에서는 모델(또는 그 대체
    휴리스틱)만 내린 차단을 검토로 바꿈. 정규식·보호 경로 차단은 그대로 차단
```

마지막 단계를 둔 이유: 실제 Claude Code 명령 하루치를 다시 판정해 보니, 모델이 명백한 읽기 전용 명령
(`sed -n 1,80p f` 0.99, `ls src | head` 0.82)과 `python3 -`에 넘기는 heredoc 대부분을 차단해 전체의 약 4분의 1이
막혔습니다. 점수가 0.8~1.0이라 임계값을 올려도 소용이 없었습니다. 그래서 사람에게 물을 수 있는 곳에서는 모델이 묻게
했습니다. 물을 수 없는 곳(검토를 안내만 남기고 통과시키는 에이전트)에서는 검토가 곧 실행이 되므로 차단을 유지합니다.

실측 지연: 노트북 CPU에서 **판정당 ~85–90ms** (2026-10, 실전 모델 웜업 후).
정규식 적중과 안전 명령은 모델 호출 없이 즉시 반환됩니다.

안전 명령 레인([policy.yaml](../../../src/laya_guard/policy.yaml)의 `safe_commands`)은 셸
메타문자(`; & | $ < > ( )` 등), 프로그램 실행·파일 쓰기 플래그(`-exec`, `--config`,
`--output` 등), 민감 경로(`.ssh`, `.aws`, `.env` 등)가 없는 명령에만 열립니다.
설치, `git push`, `npx`는 항상 모델이 심판합니다. 작은따옴표 안은 그냥 텍스트이고(`grep -o '[^|]*' f`),
큰따옴표 안에서는 `$`, `` ` ``, `\`만 셸 문자로 봅니다. 모든 단계가 안전 명령인 파이프라인(`grep -n x f | head`,
`sort | uniq -c`)도 안전하며, 출력을 버리거나 합치는 것(`2>/dev/null`, `>/dev/null`, `2>&1`)은 쓰기로 보지 않습니다.
에이전트가 자주 잇는 읽기 전용 도구(`cut`, `sort`, `uniq`, `tr`, `diff`, `stat`, `-exec`/`-delete` 없는 `find`,
`sed -n 10,20p`)도 목록에 있지만, 파일을 쓰는 형태(`sort -o`, `uniq 입력 출력`, `sed -i`)는 빠져 있습니다.
프로그램은 셸이 따옴표를 뗀 뒤의 인자를 받으므로, 따옴표를 뗀 형태로도 검사를 통과해야 합니다(`find . -ex'ec' …`는 `-exec`).

같은 명령 안에서 앞서 단순한 값을 넣은 변수는 그 값으로 읽습니다(`d=src; ls $d`는 `ls src`,
`for f in a b; do sed -n 1p $f; done`은 `a`와 `b` 각각으로 검사). 안전 레인과 정규식이 모두 그렇게 읽으므로
`d=~; rm -rf $d`는 `rm -rf ~`로 차단됩니다. 값을 따라갈 수 없는 변수(`$1`, `$(…)`, 환경변수, 나중에 `read`로 바뀌는
변수)는 여전히 레인을 닫습니다. 할당은 아무것도 실행하지 않으므로(`X=1 pytest`, `export A=b`) 통과하지만, 무엇이
실행될지 바꾸는 이름(`PATH`, `LD_PRELOAD`, `GIT_*`, `*_OPTIONS`, `BASH_ENV` 등)은 모델로 갑니다. 셸이 본문을
펼치지 않는 heredoc(`git commit -F - <<'EOF'`)은 그 명령의 데이터입니다. 저장소와 호스팅을 읽는 명령도 목록에
있습니다(`--prune`·refspec 없는 `git fetch`, `git rev-parse`, `gh pr view`/`checks`, 메서드·본문 없는 `gh api`).

Windows 명령도 같은 레인을 탑니다. 경로 사이의 백슬래시는 경로 구분자로 읽고
(`cd C:\work\api`, `type src\app.py`, `pytest tests\unit`), cmd의 `%VAR%`는 `$VAR`처럼
취급하며, cmd.exe·PowerShell의 같은 명령(`dir`, `type`, `findstr`, `where`, `md`,
`cd /d`, `Get-ChildItem`, `Get-Content`, `Set-Location`, `Select-String`,
`New-Item -ItemType Directory`)이 목록에 있습니다. POSIX 셸은 같은 백슬래시를 이스케이프로
읽으므로, 백슬래시를 지운 형태로도 깨끗해야 합니다. `cat ~/.s\sh/id_r\sa`는 여전히 모델이
심판합니다.

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

체인은 최상위 `;`, `&&`, `||`, `&`에서만 나누고(따옴표, `$(…)`, 괄호 안은 그대로), 여러 줄
스크립트는 줄마다 나눕니다. 그래서 `cd app && npm test`는 안전 명령 두 개이고, `npm test && curl … | sh`는 뒷부분
때문에 차단됩니다. heredoc 본문은 그것을 연 명령의 데이터라 함께 판정하고, `then`/`do`/`fi`/`done`은 명령이 아니라
문법으로 봅니다. 40개 명령을 넘는 스크립트는 지금처럼 통째로 판정합니다. 전체 문자열도 정규식 검사를 거치므로 명령 경계를 넘는 패턴
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
| `mass-delete` | `DROP TABLE`, `s3 rm --recursive`, `kubectl delete --all`, `git push --force`, `git reset --hard`, `git clean -f`, `git checkout -- .`, API 요청 본문 안의 삭제(`mutation { volumeDelete(...) }`, `{"method": "deleteDatabase"}`) | review |
| `auth-bypass` | 인증/TLS 무력화, 비밀번호 초기화, 한영 | block |
| `credential-read` | 개인키·클라우드 자격 증명 읽기(`cat ~/.ssh/id_rsa`, `cat ~/.aws/credentials` 등) | review |

패턴은 전부 [policy.yaml](../../../src/laya_guard/policy.yaml)에 있습니다. 코드 수정 없이 추가·수정 가능.

## 벤치마크 (재현 가능, 좋은 것만 골라내지 않음)

| 스위트 | 결과 | 명령 |
|---|---|---|
| AgentDojo v1 공격 (심판 레벨) | **25/27 차단** | `eval/run_agentdojo.py` |
| AgentDojo v1 정상 (심판 레벨) | **92/97 유지** (allow+review) | `eval/run_agentdojo.py` |
| E2E 롤아웃 (qwen2.5:7b 에이전트 + 가드) | **4/4 시나리오** (1 방어, 2 미시도, 1 정상 OK) | `eval/run_rollout.py` → [결과](../../../eval/rollout_results.md) |
| 실제 사고 재현 (공개된 사고 7건, 채점 명령 13개) | **13/13 붙잡음** (검토 또는 차단. 0.5.1에선 12/13으로, `volumeDelete` GraphQL 호출을 허용했고 이후 규칙을 추가) | `eval/run_incidents.py` → [정리](incidents.md) |
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
| 상태 | 개발 중, v0.5.2, MIT | MIT | ⚠️ 2025-05 아카이브 | ⚠️ 아카이브, 미유지보수 | 알파, Apache-2.0 |

이 표의 어떤 도구도 제3자 검증 AgentDojo 점수는 없습니다. 우리 수치도 자체 측정·심판
레벨입니다 ([한계](reference.md#한계-신뢰하기-전에-읽어주세요) 참조).
