# OS 샌드박스

훅은 명령 텍스트에 적힌 것을 심판합니다. 스크립트, 자식 프로세스, 실행 중에 만들어지는 경로는 훅에 닿지 않습니다
([실제 사고 재현](incidents.md)). OS 샌드박스는 다릅니다. 누가 요청했든 커널이 읽기나 쓰기를 거부합니다. 코딩
에이전트 대부분이 샌드박스를 갖고 있지만, 기본값은 프로젝트 밖 쓰기만 막고 읽기는 그대로 둡니다. 그래서 스크립트가
`~/.ssh`나 `~/.aws/credentials`를 여전히 읽을 수 있습니다.

`laya-guard sandbox <에이전트>`는 그 에이전트의 샌드박스를 켜고, [protect.yaml](../../../src/laya_guard/protect.yaml)
(그리고 사용자의 `~/.config/laya-guard/protect.yaml`)의 경로를 거부하게 합니다. 훅은 계속 모든 호출을 먼저
심판하고, 샌드박스는 훅이 보지 못하는 것을 막는 마지막 방어선입니다.

```bash
laya-guard sandbox --list               # 에이전트별로 샌드박스가 받아들이는 것
laya-guard sandbox claude-code          # 변경을 보여 주고, 묻고, 파일을 백업한 뒤 씀
laya-guard sandbox codex --dry-run      # 보여 주기만 함
laya-guard sandbox claude-code --remove # 추가한 것만 정확히 빼냄
laya-guard run -- opencode              # 자체 샌드박스가 없는 에이전트를 sandbox-runtime 안에서 실행
```

샌드박스는 거부만 할 수 있고 묻지 못합니다. 훅이 `review`할 보호 경로도 샌드박스에서는 거부됩니다. 꼭 필요한
명령이면(SSH를 쓰는 git은 `~/.ssh`가 필요) 에이전트가 샌드박스 밖에서 실행할지 사용자에게 묻습니다.

## 에이전트별로 쓰는 것

| 에이전트 | 위치 | laya-guard가 쓰는 것 | 이 컴퓨터에서 확인한 것 |
|---|---|---|---|
| Claude Code | `~/.claude/settings.json` (또는 `--scope project/local`) | `sandbox.enabled`, `sandbox.filesystem.denyRead` / `denyWrite` | 실제 세션(2.1.280): 보호 경로 읽기 거부, 프로젝트 파일은 읽힘, 훅도 그대로 실행 |
| Codex | `~/.codex/config.toml` | 권한 프로필(`default_permissions = "laya-guard"`, `:workspace` 확장): 보호 경로 `"deny"`, 쓰기 전용 규칙은 `"read"` | `codex sandbox` (0.163.0) |
| Copilot CLI | `~/.copilot/settings.json` | `sandbox.enabled`, `sandbox.userPolicy.filesystem.deniedPaths` / `readonlyPaths` | CLI(1.0.98)가 설정을 읽음. 실제 차단은 로그인 세션이 필요해 실행하지 않음 |
| Gemini CLI | `<프로젝트>/.gemini/` | 설치된 CLI의 기본 Seatbelt 프로필에 거부 규칙을 더한 프로필, `tools.sandbox`, `.gemini/.env`의 `SEATBELT_PROFILE` | `sandbox-exec`로 프로필 실행 (0.20.0) |
| Qwen Code | `<프로젝트>/.qwen/` | 같은 방식, Qwen 자체 기본 프로필 기반 | `sandbox-exec`로 프로필 실행 (0.25.2) |
| Cursor | `~/.cursor/cli-config.json` | `sandbox.mode = "enabled"`만 | 실행하지 않음 |
| 터미널의 OpenCode, Amp, Kilo 등 | 쓰는 파일 없음 | `laya-guard run -- <에이전트>`가 [sandbox-runtime](https://github.com/anthropics/sandbox-runtime)(`srt`) 안에서 보호 경로를 거부한 채 실행 | `srt` 0.0.80에서 `opencode`와 실측 명령 |
| Windsurf, Cline 등 IDE 안의 에이전트 | — | 지원 안 함: IDE를 바깥에서 감쌀 수 없음 | — |

확인했다고 적은 행은 모두 같은 실측을 거쳤습니다. 테스트용 비밀 파일과 `~/.ssh` 읽기는 거부됐고, 프로젝트 안에 숨긴
개인 키(`**/id_rsa`)도 거부됐으며, 프로젝트 파일은 읽혔습니다.

## 각 샌드박스가 받아들이지 못하는 것

다음은 훅이 계속 맡습니다. `laya-guard sandbox`가 쓰기 전에 목록으로 보여 줍니다.

- **예외가 붙은 규칙.** `**/.env.*`에는 `except: [**/.env.example, ...]`이 있는데, 여기 있는 어떤 샌드박스도 거부
  안에서 예외를 뺄 수 없습니다. `**/.env`는 들어갑니다(예외가 이 경로와 겹치지 않음).
- **Codex:** 글롭에는 `"deny"`만 쓸 수 있어서 쓰기 전용 `**/` 규칙(`**/.git/hooks`)은 표현할 수 없습니다. `**/`
  글롭은 작업 루트 아래에만 적용되고, Linux와 Windows에서는 Codex가 시작할 때 한 번 풀어 둡니다. 어디에든
  `sandbox_mode`가 있으면(config.toml이나 `-s/--sandbox`) Codex는 프로필을 무시하므로, 그때는 laya-guard가 쓰지
  않고 멈춥니다.
- **Copilot CLI:** 글롭 없이 일반 경로만 받습니다. Copilot은 없는 거부 경로를 명령마다 폴더로 만들어 버리므로, 지금
  있는 경로만 넣습니다. 이 샌드박스는 laya-guard 훅도 안에서 실행하는데, macOS에서는 localhost에 닿지 못해 훅이
  오프라인 검사로 넘어갈 수 있습니다.
- **Gemini CLI / Qwen Code:** macOS 전용(Seatbelt)입니다. 설치된 버전은 시작한 폴더의 `.gemini/`나 `.qwen/`에서만
  프로필을 찾으므로 프로젝트마다 따로 있어야 합니다(이 둘은 `--scope project`가 기본). CLI 전체가 훅까지 포함해 이
  프로필 안에서 돕니다. CLI를 업그레이드하면 다시 실행하세요. 프로필이 기본 프로필에서 다시 만들어집니다.
  docker/podman 모드에서는 프로젝트만 마운트되므로 `~/.ssh`가 애초에 없습니다.
- **Cursor:** git 저장소 밖 경로의 읽기를 막는 목록이 없어서, 샌드박스를 켜기만 합니다(프로젝트 밖 쓰기, 네트워크).
  Cursor 앱에서는 Settings > Agents > Approvals & Execution에 있습니다.
- **`laya-guard run`:** `srt`가 필요합니다(`npm install -g @anthropic-ai/sandbox-runtime`). 네트워크를 전부 열어 둘 수
  없어서 주요 모델 API와 패키지 저장소만 허용합니다. 호스트는 `--allow-domain`, 쓰기 가능한 폴더는 `--allow-write`로
  추가합니다. 자체 샌드박스가 있는 에이전트는 이 안에서 실행하면 안 됩니다. Seatbelt는 중첩이 안 돼서 에이전트의
  샌드박스가 시작되지 못합니다.

## 되돌리기

모든 변경은 쓰기 전에 보여 주고, 원래 파일은 옆에 백업합니다(`*.laya-guard-<시각>.bak`).
`~/.config/laya-guard/sandbox.json`에 무엇을 추가했는지 기록하므로, `--remove`는 그 항목만 빼고, 켰던 스위치를
되돌리고, 직접 만든 파일은 다른 내용이 없으면 지웁니다. Codex 프로필과 Seatbelt 파일은 표시된 블록·파일이라
`--remove`가 지웁니다.
