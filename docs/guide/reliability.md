# When the server is down

[← Back to README](../../README.md) · **English** · [한국어](../ko/guide/reliability.md)

What the hooks do without the server, how to keep it running, and how to hear about outages.

## Server down

Every adapter waits 10s for the server. If it's down, errors, or is slower than
that, the adapter runs `laya-guard check --offline` (regex + safe-command lane,
no model) instead:

| Call | Offline verdict |
|---|---|
| matches any regex policy (block or review level) | block |
| package.json script of a safe command matches a regex policy | block |
| safe command, or a chain of only safe commands | allow |
| anything else (it can't be judged without the model) | `review` (asked, or blocked where the agent can't ask; see below); `LAYA_GUARD_FAIL=closed` blocks it; `LAYA_GUARD_FAIL=open` allows it with a stderr warning |

Hook input the adapter can't read, and a call made when the CLI isn't on the agent's `PATH` either, get the same
answer as that last row (set `LAYA_GUARD_BIN` to the CLI's full path, e.g. `~/.venv/bin/laya-guard`).

The default, **`ask`**, sends what the guard couldn't judge to a person. Claude Code, Cursor, Qwen Code, Copilot CLI
and Amp's legacy permissions helper put the question to you, and when nobody can answer (a headless run) the ask is
denied. The agents with no ask lane (Gemini CLI, Codex, Windsurf, Cline, OpenCode, Kilo, the Amp plugin) have nobody to
ask, so they block the call and say that `LAYA_GUARD_FAIL=open` would let it through. `LAYA_GUARD_REVIEW=pass` means
nobody is asked either, so with it an offline `review` is blocked. **`closed`** blocks every unjudged call, for
unattended runs (CI, scheduled agents). **`open`** lets them pass with a warning, for when an outage must not stop the
work and you accept that those calls run unchecked. (`open` was the default up to 0.3.0.)

An outage is not a verdict, so it is recorded as one of its own: every offline check is appended to the verdict log
with `via: "offline"` (hash and length only, like every entry), the server records how it judged
(`via: "model"`, `"regex"`, `"allowlist"`, `"heuristic"` after a model error, `"limit"`), and `laya-guard stats`
prints how many calls were judged without the model, with the server down or after a model error. Mock mode doesn't
count: that is a choice, not an outage.

### Keep it running, and hear about outages

A guard whose server you have to remember to start has a single point of failure, so laya-guard can start it for you,
**if you ask**: `laya-guard service install` writes a macOS LaunchAgent (`~/Library/LaunchAgents/com.laya-guard.server.plist`)
or a Linux systemd user unit (`~/.config/systemd/user/laya-guard.service`), shows the file first, and starts the server
now and at every login (restarting it after a crash, at most every 30 seconds). The model is loaded up front, about
1.4 GB and 5 seconds per start; `--no-warmup` loads it on the first call instead (about 90 MB while idle). A server
you started by hand keeps the port; the service takes over once it stops. `laya-guard service status` shows where it
stands and `laya-guard service uninstall` removes it. The dashboard's Overview has the same switch ("Start at login").
Hooks don't depend on any of this: without the server they use the offline check, as before. Windows isn't
supported yet. The environment the server reads (`LAYA_GUARD_ALERT_CMD`, `LAYA_GUARD_LOG_PATH`, ...) is copied into the
file when you install, and the file is readable only by you.

The dashboard turns **Degraded** (amber, with a note and a "Judged without the model" card) when calls in the last hour
were judged without the model, and each call says who judged it. To be told when it happens, set
`LAYA_GUARD_ALERT_CMD` to any shell command: it is started in the background, at most once per
`LAYA_GUARD_ALERT_COOLDOWN` seconds for each kind of outage (`server-down`, from the hook's offline fallback, the one
piece of code still running when the server is down; `model-error`, from the server). The text is in
`LAYA_GUARD_ALERT_KIND` and `LAYA_GUARD_ALERT_MESSAGE`, never in the command line, and a hook never waits for the
command or fails because of it. For example, on macOS:

```bash
export LAYA_GUARD_ALERT_CMD='osascript -e "display notification \"$LAYA_GUARD_ALERT_MESSAGE\" with title \"laya-guard\""'
```
