# 서버가 꺼졌을 때

[← README로 돌아가기](../../../README.ko.md) · [English](../../guide/reliability.md) · **한국어**

서버 없이 훅이 어떻게 동작하는지, 서버를 계속 실행해 두는 방법, 장애 알림을 받는 방법입니다.

## 서버 장애

모든 어댑터는 서버를 10초 기다립니다. 서버가 꺼졌거나, 오류를 내거나, 그보다 느리면
대신 `laya-guard check --offline`(정규식 + 안전 명령 레인, 모델 없음)을 실행합니다.

| 호출 | 오프라인 판정 |
|---|---|
| 정규식 정책에 적중 (block·review 등급 모두) | block |
| 안전 명령이 실행할 package.json 스크립트가 정규식 정책에 적중 | block |
| 안전 명령, 또는 안전 명령만으로 된 체인 | allow |
| 그 외 (모델 없이는 판정 불가) | review (사람에게 묻거나, 물을 수 없는 에이전트는 차단. 아래 참고). `LAYA_GUARD_FAIL=closed`면 block, `LAYA_GUARD_FAIL=open`이면 stderr 경고 후 allow |

어댑터가 읽을 수 없는 훅 입력, 그리고 에이전트의 `PATH`에 CLI도 없을 때의 호출도 마지막 행과 같이 처리합니다
(`LAYA_GUARD_BIN`에 CLI 전체 경로를 지정하세요. 예: `~/.venv/bin/laya-guard`).

기본값 **`ask`**는 판정하지 못한 호출을 사람에게 넘깁니다. Claude Code, Cursor, Qwen Code, Copilot CLI, Amp의 레거시
permissions helper는 사용자에게 묻고, 답할 사람이 없으면(헤드리스 실행) ask가 거부됩니다. 승인 레인이 없는
에이전트(Gemini CLI, Codex, Windsurf, Cline, OpenCode, Kilo, Amp 플러그인)는 물어볼 사람이 없으므로 차단하고,
`LAYA_GUARD_FAIL=open`이면 통과된다고 알려줍니다. `LAYA_GUARD_REVIEW=pass`도 아무에게도 묻지 않는다는 뜻이라, 이때
오프라인 `review`는 차단됩니다. **`closed`**는 판정 못 한 호출을 모두 차단하며 무인 실행(CI, 예약된 에이전트)용입니다.
**`open`**은 경고와 함께 통과시키며, 장애가 작업을 멈추면 안 되고 그 호출들이 확인 없이 실행되는 것을 감수할 때 씁니다.
(0.3.0까지는 `open`이 기본값이었습니다.)

장애는 판정이 아니므로 따로 기록합니다. 오프라인 검사는 모두 판정 로그에 `via: "offline"`으로 남고(다른 항목처럼 해시와
길이만), 서버는 어떻게 판정했는지(`via: "model"`, `"regex"`, `"allowlist"`, 모델 오류 뒤의 `"heuristic"`, `"limit"`)를
기록하며, `laya-guard stats`는 서버가 꺼져 있거나 모델이 오류를 낸 상태에서 모델 없이 판정한 호출 수를 보여줍니다. MOCK
모드는 장애가 아니라 선택이므로 세지 않습니다.

### 계속 실행해 두기와 장애 알림

서버를 직접 기억해서 켜야 하는 가드는 그 자체가 단일 장애점이므로, **원하시면** laya-guard가 서버를 대신 켜 줍니다.
`laya-guard service install`은 macOS LaunchAgent(`~/Library/LaunchAgents/com.laya-guard.server.plist`)나 Linux systemd
사용자 유닛(`~/.config/systemd/user/laya-guard.service`)을 파일 내용을 먼저 보여준 뒤 작성하고, 서버를 지금과 로그인할
때마다 시작하며(비정상 종료 뒤에는 30초 간격으로 다시 시작), 모델은 미리 올립니다(시작마다 약 1.4 GB, 5초).
`--no-warmup`이면 첫 호출 때 올리고 대기 중에는 약 90 MB만 씁니다. 직접 켜 둔 서버가 포트를 쥐고 있으면 그대로 두고,
그 서버가 멈추면 서비스가 이어받습니다. `laya-guard service status`로 상태를, `laya-guard service uninstall`로 제거를
할 수 있고, 대시보드 Overview의 "Start at login"도 같은 스위치입니다. 훅은 이와 무관하게, 서버가 없으면 지금처럼 오프라인
검사를 씁니다. Windows는 아직 지원하지 않습니다. 서버가 읽는 환경변수(`LAYA_GUARD_ALERT_CMD`, `LAYA_GUARD_LOG_PATH` 등)는
설치할 때 파일로 복사되고, 파일은 본인만 읽을 수 있습니다.

최근 1시간 안에 모델 없이 판정한 호출이 있으면 대시보드가 **Degraded**(주황색, 안내 문구와 "Judged without the model"
카드)로 바뀌고, 각 호출에는 누가 판정했는지가 표시됩니다. 알림을 받으려면 `LAYA_GUARD_ALERT_CMD`에 아무 셸 명령이나
지정하세요. 장애 종류(`server-down`: 서버가 꺼졌을 때도 살아 있는 유일한 코드인 훅의 오프라인 대체 경로에서, `model-error`:
서버에서)마다 `LAYA_GUARD_ALERT_COOLDOWN`초에 한 번만 백그라운드로 실행됩니다. 내용은 명령줄이 아니라
`LAYA_GUARD_ALERT_KIND`와 `LAYA_GUARD_ALERT_MESSAGE` 환경변수로 전달되고, 훅은 이 명령을 기다리지도, 실패 때문에 실패하지도
않습니다. macOS 예시:

```bash
export LAYA_GUARD_ALERT_CMD='osascript -e "display notification \"$LAYA_GUARD_ALERT_MESSAGE\" with title \"laya-guard\""'
```
