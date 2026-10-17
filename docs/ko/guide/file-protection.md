# 파일: 보호, 기록, 검토

[← README로 돌아가기](../../../README.ko.md) · [English](../../guide/file-protection.md) · **한국어**

Claude Code 파일 도구용 보호 경로, 에이전트가 건드린 파일 기록(선택), macOS OS 모니터(선택), `laya-guard footprint`를 다룹니다.

## 에이전트의 파일 도구로부터 파일 보호하기 (선택)

Claude Code 훅은 기본적으로 `Bash`만 지키므로 Claude Code 자체의 `Read`, `Edit`, `Write`는 가드에 닿지 않습니다.
`~/.ssh/id_rsa`를 여는 것을 막을 방법이 없다는 뜻입니다. `--files`로 이 도구들을 추가합니다:

```bash
laya-guard install claude-code --files     # Bash + Read, Edit, MultiEdit, Write, NotebookEdit, Glob, Grep
```

그러면 파일 도구 호출은 그 호출이 지목한 경로를 **보호 경로**와 대조해 판정합니다. 모델은 돌지 않고, 읽거나 쓰는
내용은 판정하지도 전송하지도 않습니다. 기본값은 자격 증명 저장소(`~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube` 등), `.env*`
파일, 개인 키를 읽을 때 *검토*(Claude Code가 묻습니다), 자격 증명 저장소를 바꿀 때 *차단*, 에이전트 자신의 사용자
수준 설정과 훅, 셸 시작 파일(`~/.bashrc`, `~/.zshrc` 등), `.git/hooks/**`에 쓸 때 *검토*입니다([`protect.yaml`](../../../src/laya_guard/protect.yaml)).
경로가 50개를 넘거나 한 경로가 4096자를 넘는 호출도 건너뛰지 않고 *검토*합니다. 내 규칙은
`~/.config/laya-guard/protect.yaml`에 추가하면 다음 호출부터 적용됩니다. 형식은 이렇습니다:

```yaml
version: 1
protect:
  - path: "~/work/app/prisma/migrations/**"   # 절대 경로: 이 프로젝트에만
    ops: [write]                               # read, write, 또는 둘 다(기본값)
    action: block                              # review(묻기) 또는 block
    reason: schema history                     # 판정과 함께 표시됨. 파일 이름은 넣지 마세요
  - path: "**/secrets.yaml"                    # **/는 어느 깊이에서나 일치
    except: ["**/secrets.example.yaml"]
```

상대 경로 글롭은 에이전트가 실행 중인 디렉터리 기준이라 모든 프로젝트에 적용됩니다. 한 프로젝트에만 적용하려면
절대 경로를 쓰세요. 경로는 지목된 그대로, 그리고 심볼릭 링크와 `..`을 풀어서, 대소문자를 무시하고 검사합니다.
경로는 판정 로그에 쓰지 않습니다(로그에는 도구 이름, 해시, 길이만 남음). 에이전트 없이 시험하려면
`laya-guard check --offline --tool-name Read --path ~/.ssh/id_rsa x`. 서버가 꺼져 있어도 오프라인 검사가 같은 규칙을
적용하고, 어느 규칙에도 해당하지 않는 파일 호출은 묻지 않고 허용합니다.

### 셸 명령

모든 에이전트의 셸 명령도 텍스트가 지목한 경로로 같은 규칙을 적용합니다. 서버가 꺼져 있어도 마찬가지입니다. 명령이
그대로 읽거나 바꾸는 파일(`cat`, `grep`, `source`, `<`, `rm`, `mv`, `cp`의 대상, `>` 등)은 그 동작대로 보고, 상대
경로는 단순한 `cd`를 따라가므로 `cd /etc && cat shadow`는 `/etc/shadow` 읽기입니다. 명령 안의 다른 경로 형태
문자열(`python -c "open('/home/me/.ssh/id_rsa')"`의 코드 등)은 읽기로 봅니다. 프로그램이 그걸로 무엇을 할지 모르니
쓰기 규칙을 추측으로 적용하지 않습니다. 판정을 더 엄격하게만 만듭니다. 명령은 여전히 정규식과 모델이 심판하고, 더
엄격한 답을 따릅니다. 경로 형태 문자열이 200개를 넘는 명령은 검토합니다. 시험해 보려면
`laya-guard check --offline --shell "cd /etc && cat shadow"`.

스크립트나 자식 프로세스가 실행 중에 만들어 내는 경로(`open(os.environ["KEY_FILE"])`, 빌드 도구가 읽는 파일)는
훅에 닿지 않습니다. macOS에서는 [OS 모니터](#에이전트가-말한-것이-아니라-os가-본-것-보기-macos-선택)가 기록합니다.
텍스트로 알 수 없는 `cd`(`cd -`, `cd $DIR`, `pushd`)는 따라가지 않습니다.

## 에이전트가 건드린 파일 보기 (선택)

에이전트의 도구 호출이 지목한 파일을 기록해 두면 에이전트가 실제로 무엇에 손을 댔는지 볼 수 있습니다. 디스크에
**파일 경로**(내용은 절대 아님)를 쓰기 때문에 기본은 꺼져 있습니다:

```bash
laya-guard activity on        # 기록 시작
laya-guard activity           # 상태와 최근 이벤트
laya-guard activity off       # 중지 (기록은 남음)
```

`~/.local/share/laya-guard/file-activity.jsonl`(본인만 읽을 수 있음, `LAYA_GUARD_FILE_ACTIVITY_PATH`로 위치 변경)의
한 줄이 호출이 지목한 파일 하나입니다: `{ts, agent, tool_name, op, path, abs, cwd, source, action, policy_id}`(`cwd`: 호출이
실행된 디렉터리, OS 모니터 줄에는 `would`가 더해짐).
`op`는 `read`·`write`·`delete`, `source`는 경로를 어디서 알았는지(`hook`: 파일 도구가 지목, `shell`: Bash 명령에서
단순한 형태만 추정, 셸이 풀어야 하는 `$VAR`·글롭·`$(…)`은 건너뛰므로 놓치는 것이 있음), `action`은 가드의 답이라
막힌 시도를 접근으로 오해하지 않습니다. `verdicts.jsonl`에는 여전히 경로가 들어가지 않고, 환경변수
`LAYA_GUARD_FILE_ACTIVITY=1|0`이 있으면 그것이 우선합니다. 50 MB에서 기록을 멈춥니다. Bash 호출은 켜는 즉시,
파일 도구 호출은 `install --files`가 필요하고 지금은 Claude Code만 지원합니다. 다른 에이전트의 훅은 셸 명령에만
등록돼 있기 때문입니다. 셸 명령은 지원하는 **모든 에이전트**에서 기록합니다(각 어댑터가 에이전트 이름과 맨 명령어를
보냄). 선언된 접근만 기록하며, 셸이 아닌 도구가 무엇을 건드리는지 아는 어댑터는 `accesses`로 보고할 수 있습니다
(`POST /guard` 또는 `laya-guard check --access read PATH`).

## 에이전트가 말한 것이 아니라 OS가 본 것 보기 (macOS, 선택)

훅은 도구 호출이 건드리겠다고 *말한* 것만 압니다. 에이전트가 실행한 스크립트, 자식 프로세스, 빌드 도구가 하는 일은
닿지 않습니다. macOS에서는 `laya-guard watch`가 운영체제에 직접 묻습니다. Apple의 `eslogger`(Endpoint Security)를 읽어
**에이전트 자신의 프로세스**가 실제로 열고, 만들고, 지우고, 이름을 바꾼 파일만 남기고, 같은 파일 활동 기록에
`source: "os-monitor"`로 추가합니다.

```bash
laya-guard activity on        # 기록이 쌓일 곳
laya-guard watch              # `sudo eslogger`를 대신 시작합니다(비밀번호를 묻습니다). Ctrl+C로 중지
# 권한이 필요한 부분을 직접 실행하고 파이프로 넘겨도 됩니다(root로 도는 것은 eslogger뿐, laya-guard는 본인 권한):
sudo eslogger exec fork exit open unlink rename create | laya-guard watch
```

`sudo`와, 실행하는 터미널 앱의 Full Disk Access(시스템 설정 > 개인정보 보호 및 보안 > 전체 디스크 접근 권한, 앱을 완전히
종료했다 다시 열기)가 필요합니다. 이후 `laya-guard activity`가 훅 이벤트 옆에 이 이벤트를 함께 보여줍니다.

- **어떤 프로세스인가.** 에이전트 이름의 프로그램(`claude`, `codex`, `cursor-agent`, `gemini`, `qwen`, `copilot`,
  `cline`, `opencode`, `kilo`, `amp`, `--match 이름[=에이전트]`로 추가)이 트리의 뿌리가 되고, 그 아래 시작된 모든 것(스크립트·
  자식 프로세스 포함)이 트리에 속합니다. 시작할 때 이미 실행 중인 에이전트는 `ps`로 찾습니다. 컴퓨터의 나머지 활동은
  읽자마자 버립니다.
- **무엇을 남기나.** 쓰기·삭제·이름 변경은 항상. 읽기는 보호 경로를 지목했을 때와(기본값) 에이전트를 시작한 프로젝트 밖의
  본인 폴더 파일일 때. 시스템 디렉터리, 숨김 디렉터리(`~/.nvm`, `~/.zshrc` …), `~/Library`는 규칙이 지목하지 않는 한 늘 있는
  잡음으로 봅니다. `--reads protected|outside|all`로 바꾸고(`all`은 매우 큼) `--ignore GLOB`으로 본인의 잡음을 줄입니다
  (보호 경로는 절대 무시되지 않음). 같은 프로그램이 같은 경로에 같은 일을 5초 안에 반복하면 한 건입니다.
- **기록 형식.** 훅 이벤트와 같고, `action: "observed"`(이미 일어난 일이고 막은 것이 없음)와 `would`(
  [보호 경로](#에이전트의-파일-도구로부터-파일-보호하기-선택) 규칙이었다면 답했을 것: `review`, `block`, `null`)가 더해집니다.
  `tool_name`은 파일을 건드린 프로그램(`cat`, `python3` …)입니다.
- **개인정보.** `exec` 메시지에는 프로세스의 환경변수 전체(비밀이 들어 있음)가 실려 오는데, 읽지도 저장하지도 기록하지도
  않습니다. 명령줄과 파일 내용도 마찬가지입니다. 경로는 본인만 읽을 수 있는 같은 파일에 평문으로 씁니다.

알려줄 뿐 막을 수는 없으므로 차단은 계속 훅이 맡습니다. Apple은 `eslogger` 출력을 "NOT API"라고 밝히고 있어 macOS 버전에
따라 모양이 바뀔 수 있습니다. 알 수 없는 메시지는 추측하지 않고 건너뛰며, 입력이 기대한 모양과 전혀 맞지 않으면
`watch`가 알려줍니다. 컴퓨터가 과부하면 이벤트가 유실될 수 있고, macOS는 열린 파일을 보고하므로 에이전트가 그 파일에
도달한 이름(심볼릭 링크)과 다를 수 있어 규칙은 실제 경로로 쓰는 것이 안전합니다. Mac 한 대(macOS 26)에서 시험했을 때
대역 에이전트의 읽기·쓰기·삭제·이름 변경을 모두 잡았고 Python·Node 자식 프로세스 안의 읽기도 포함됩니다. 초당 약 700건,
`eslogger`가 코어 하나의 8% 수준이었습니다. 실제 에이전트의 긴 세션으로는 아직 돌려보지 않았습니다.

## 에이전트가 어디까지 닿았는지, 무엇을 보호할지 보기 (footprint)

파일 활동 기록을 켜 두면 `laya-guard footprint`가 "에이전트가 무엇을 건드렸나"를 프로젝트·세션별로, "아직 보호하지 않는데
보호할 만한 것은 무엇인가"를 알려줍니다:

```bash
laya-guard footprint                     # 최근 7일, 프로젝트·세션별
laya-guard footprint --since 30d --project ~/work/app --agent claude-code
laya-guard footprint --suggest           # 살펴볼 만한 protect.yaml 항목. 아무것도 적용하지 않음
laya-guard footprint --json              # 스크립트용
```

프로젝트마다 읽기·쓰기·삭제 수, 에이전트, 세션, 보호 경로에 닿은 접근, 프로젝트 밖 파일, 가장 많이 건드린 파일을 보여줍니다.
대시보드 개요에도 같은 내용이 파일 발자국 카드로 있습니다(기록에 내용이 있을 때만, 프로젝트를 누르면 자세히).

- **프로젝트**는 호출 디렉터리가 속한 git 저장소입니다(없으면 그 디렉터리). 기록이 `cwd`를 남기기 전의 이벤트와 어댑터가
  디렉터리를 보내지 않은 호출은 `(unknown)`에 들어갑니다.
- **세션**은 한 에이전트가 한 프로젝트에서 30분 넘는 공백 없이 이어간 활동입니다. 훅은 세션 id를 보내지 않으므로
  타임스탬프로 추정한 값입니다.
- **막힌 것은 건드린 것이 아닙니다.** 가드가 차단한 호출은 일어나지 않았으므로 막힌 시도로 셉니다. 검토로 보낸 호출은
  건드린 것으로 셉니다. 기록만으로는 승인했는지 알 수 없기 때문입니다.
- **추천**은 두 종류이며 절대 적용되지 않습니다. *민감*: 기본 규칙이 다루지 않는 널리 알려진 비밀 위치(클라우드 CLI 자격증명,
  브라우저 프로필, 셸 기록, `*.pem`, `*.tfstate` …)를 에이전트가 건드렸을 때. *드묾*: 홈의 본인 폴더이면서 프로젝트 밖이고
  에이전트가 몇 번(몇 세션)만 건드린 폴더. 보호 비용이 낮고 에이전트가 있으리라 기대하지 않던 곳일 수 있습니다. 에이전트가
  늘 쓰는 곳은 보호하면 검토 요청이 계속 뜨므로 추천하지 않습니다. 각 항목은 action `review`의 `protect.yaml` 항목으로
  나오며, 원하는 것만 `~/.config/laya-guard/protect.yaml`에 붙여 넣으세요. **접근 하나만으로는 나쁜 의도의 증거가 아닙니다.**
  `~/Documents/taxes/2025.pdf`를 한 번 읽은 에이전트는 그렇게 요청받았을 수도 있습니다.
