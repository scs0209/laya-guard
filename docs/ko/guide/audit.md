# 에이전트가 이미 실행한 명령 감사

훅을 설치하기 전에, 훅이 있었다면 무엇을 했을지 볼 수 있습니다. Claude Code와 Codex는 모든 세션을 디스크에
남기고, 거기에는 에이전트가 실행한 셸 명령과 실행한 위치, 시각이 들어 있습니다. `laya-guard audit`은 이 대화 기록을
읽어 모든 명령을 그 에이전트의 훅이 했을 방식대로 판정하고, 검토되거나 차단됐을 명령을 보여 줍니다.

```bash
laya-guard audit                        # 최근 24시간, Claude Code와 Codex
laya-guard audit --since 7d             # 일주일 (7d, 24h, 30d, all)
laya-guard audit --agent codex          # 에이전트 하나만
laya-guard audit --redact               # 명령 대신 판정 이유 (스크린샷용)
laya-guard audit --json                 # 전부, 걸린 명령마다 한 항목
```

```
laya-guard audit — since 2026-10-22 09:14

  Claude Code  12 sessions, 418 shell commands
  Codex        3 sessions, 61 shell commands

  block 2   review 37   allow 440   (with the model, 95s)

What the hook would have held (worst first, 20 of 31 distinct commands):

  BLOCK  10-22 17:41  Claude Code destructive-fs           ~/work/api
         rm -rf ~/work/api/../..
  REVIEW 10-23 08:02  Codex       mass-delete      ×3    ~/work/svc
         git reset --hard origin/main
  …
```

(위 명령은 지어낸 것이고, 출력 형태는 실제와 같습니다.)

## 읽는 곳

| 에이전트 | 대화 기록 | 셸 호출 |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` (서브에이전트 포함) | `Bash` 도구 호출: `input.command`, 세션의 `cwd`, `timestamp`, 결과가 오류였는지 |
| Codex | `~/.codex/sessions/**/*.jsonl` (또는 `CODEX_HOME` 아래) | `exec_command` / `shell` 함수 호출: `cmd`, `workdir`(없으면 세션의 `cwd`), `timestamp` |

`--since` 안에 바뀐 파일만 열고, 그 기간 안의 호출만 판정합니다.

## 판정 방식

모든 명령은 서버와 같은 판정기를 거치고, 에이전트마다 설정이 다릅니다.

- **Claude Code**는 출처 `user`, `ask` 켜짐으로 판정합니다. 이 훅은 검토를 권한 확인 창으로 바꾸므로, 모델만
  내린 차단은 검토가 됩니다([내부 동작](how-it-works.md)).
- **Codex**는 출처 `tool`, `ask` 꺼짐으로 판정합니다. 이 훅은 검토를 메모와 함께 통과시키므로, 모델 차단은
  차단으로 남습니다.
- 둘 다 훅처럼 [보호 경로](file-protection.md)에도 걸립니다.

같은 디렉터리의 같은 명령은 한 번만 판정합니다. 안전 레인이나 패턴이 정하는 명령은 비용이 없고, 나머지는 모델로
갑니다. 노트북 CPU 실측(2026년 10월): 명령이 많았던 하루치 Claude Code 명령 1,961개에 11분, 모델 로딩을 포함해
명령당 약 0.33초가 걸렸습니다(긴 heredoc은 여러 구간으로 나눠 읽습니다). 그래서 기본 기간이 하루입니다. 터미널에서는 stderr에 진행
상황이 표시됩니다. 모델이 설치돼 있지 않으면 요약 줄에 그렇다고 나옵니다(`rules and heuristics only (mock mode)`).

## 하지 않는 것

- **아무것도 쓰거나 보내지 않습니다.** 판정 로그, 대시보드, 알림은 이 판정을 보지 못하고, 대화 기록은 읽기만 합니다.
- **훅이 뭐라고 답했을지를 보여 줄 뿐, 실제로 무슨 일이 있었는지는 아닙니다.** 여기서 걸린 명령이 실제로는 문제없었을
  수 있고, 허용된 명령도 텍스트에 드러나지 않는 스크립트로 해를 끼쳤을 수 있습니다. 그 부분은
  [OS 샌드박스](sandbox.md)가 맡습니다.
- **다른 에이전트**(Gemini CLI, Cursor, Copilot CLI 등)는 아직 읽지 않습니다. 대화 기록 형식이 다르거나 디스크에
  남지 않습니다.
- **셸 외의 도구**(파일 편집, 웹 요청, MCP 호출)는 판정하지 않습니다.
