# Reference

[← Back to README](../../README.md) · **English** · [한국어](../ko/guide/reference.md)

The HTTP API, configuration, telemetry, and the full list of limitations.

## API

- `GET /health` → `{"ok": true, "mock": false}`
- `POST /guard` `{tool_call, source?, cwd?, tool_name?, paths?, agent?}` → `{action, risk, policy_id, reason, mock, verdict_source}`
  (`source`: `user` | `tool` | `email` | `web`; non-user sources get stricter thresholds.
  `cwd`: absolute directory the command runs in; enables package.json script checks.
  `tool_name`: the agent's name for the tool being called (`Bash`, `Edit`, an MCP tool, …); it is logged (in clear, up
  to 64 characters; `null` when absent) and counted by `laya-guard stats`. With `paths` (the files the call names) it
  also marks a file tool call (`Read`, `Edit`, `MultiEdit`, `Write`, `NotebookEdit`, `Glob`, `Grep`): that call is
  judged by the paths alone, against the protected paths, and `tool_call` is ignored. For any other tool it doesn't
  change the verdict. `agent`: which agent is calling (`claude-code`); it only labels the opt-in file activity log.
  `reason` is Korean when the input contains Hangul, English otherwise.
  `verdict_source`: `regex` (prefilter block, model skipped) |
  `allowlist` (safe command, model skipped) | `model` |
  `heuristic` (MOCK mode, or the model errored and regex+heuristics stood in) |
  `limit` (input over 256K chars or a line over 16K chars, blocked unread).
  `laya-guard check --offline --json` returns the same shape with `offline`.)
- `POST /v1/systemone` — drop-in compatible shim for Jev-style `{state, questions}` calls
- `GET /dashboard` — the local dashboard page; `GET /api/stats?window=1h|24h|7d|30d|all` — the
  numbers behind it (totals, timeline, per-policy counts, recent verdicts as hash + length).
  Both answer 403 unless the `Host` header is loopback.
- `GET /api/agents` — the agents found on this machine and, for Claude Code, where the hook stands. `POST /api/agents/{agent}/{install|uninstall}`
  with `{"mode": "preview"}` returns the diff and its `diff_id`; `{"mode": "apply", "diff_id": ...}`
  makes the change. Needs the page's token and a same-origin JSON request (see [Dashboard](dashboard.md#dashboard)).

## Configuration

| Knob | Where | Default |
|---|---|---|
| `LAYA_GUARD_MOCK=1` | env | off — heuristics only, no model (for CI/demo) |
| `LAYA_GUARD_DEVICE` | env | `cpu` (deterministic; set `mps` for speed at the cost of reproducibility) |
| `LAYA_GUARD_URL` | env (hooks/adapters) | `http://localhost:8787/guard` |
| `LAYA_GUARD_FAIL` | env (hooks/adapters) | `ask` — with the server down, an unjudged call goes to a human, or is blocked by agents that can't ask; `closed` blocks it; `open` lets it pass (see [Server down](reliability.md#server-down)) |
| `LAYA_GUARD_REVIEW` | env (Claude Code hook) | `ask` — `review` becomes a permission prompt; `pass` lets it through without asking |
| `LAYA_GUARD_BIN` | env (hooks/adapters) | `laya-guard` — CLI used for the offline check |
| `--no-warmup` | `laya-guard serve` | off — the model loads before the server listens |
| `LAYA_GUARD_ALERT_CMD` | env (hooks and server) | unset — a shell command run (once per outage, in the background) when calls are judged without the model; see [Keep it running](reliability.md#keep-it-running-and-hear-about-outages) |
| `LAYA_GUARD_ALERT_COOLDOWN` | env | `600` — seconds before the same kind of alert can run again |
| block/review thresholds | `policy.yaml` → `defaults` | 0.8 / 0.45 |
| model checkpoint | `policy.yaml` → `defaults.model` | `multilingual` |

## Telemetry (measured dogfooding)

Every judgment is appended to a local JSONL log
(`~/.local/share/laya-guard/verdicts.jsonl`). **The raw `tool_call` text is never
stored** — only metadata (action, risk, policy, latency) plus a truncated SHA-256
for counting unique prompts.

```bash
laya-guard stats       # verdict distribution, block/review rates, top policies, latency
laya-guard dashboard   # the same as a local web page (see Dashboard); starts the server if it isn't running
```

The [dashboard](dashboard.md#dashboard) shows the same numbers, and more, in a browser.

Disable with `LAYA_GUARD_LOG=0`, relocate with `LAYA_GUARD_LOG_PATH=...`.
Publish the aggregates (never raw prompts) as field evidence next to benchmarks.

## Limitations (read before trusting it)

- With the server down, calls that are neither regex-flagged nor safe commands
  are **not judged by the model**: you're asked about them, or they're blocked by
  agents that can't ask ([Server down](reliability.md#server-down)). `LAYA_GUARD_FAIL=open`
  lets them pass unchecked instead.
- If the model fails at runtime, verdicts fall back to regex+heuristics. They are
  marked `verdict_source: "heuristic"` and a warning is logged — check it if you
  rely on model judgments.
- The safe-command lane allows `npm test`, `make test`, `go test`, etc. without the
  model. package.json scripts are regex-checked (see [How it works](how-it-works.md#how-it-works)), so `curl … | sh` or a
  secret upload in `scripts.test` is caught, but a quieter script (`node -e …`,
  `node scripts/x.js`) passes. Code those commands run is not checked at all:
  test files, `conftest.py`, `build.rs`, `node_modules/.bin`, Makefile recipes
  (expanding them with `make -n` would run `$(shell …)`), and `.npmrc`
  `script-shell`. An edit to them is judged only when the agent writes it, and only
  by adapters that hook file edits. Trim `safe_commands` in `policy.yaml` if that
  matters to you. (Claude Code's file tools, once guarded with `--files`, are judged by their paths alone, not by what
  they write.)
- Script checks trust the `cwd` the agent reports. If an agent's shell keeps a
  `cd` from an earlier call but reports the project root, the wrong package.json
  is read. Five adapters (Cline, Gemini, Qwen, Kilo, Amp) send the tool call as
  JSON, so it never qualifies as a safe command and goes to the model instead.
- Protected paths see declared access only: the path a file tool call names. A file read by a shell command, a script
  or a child process is invisible to them (the optional macOS [OS monitor](file-protection.md#see-what-the-os-saw-not-just-what-the-agent-said-macos-optional)
  sees those, but only reports), and a rule can't tell an honest `Read` of `.env` from a harmful one: a match is
  reviewed or blocked, not understood.
- Chain commands are judged one by one, so the model doesn't see that `cd /etc`
  came before `cat shadow`. Sensitive-path checks and whole-string regexes cover
  the common cases, not all of them.
- Regex `block` hits return a hardcoded `risk: 0.92` — the model is skipped, so the
  score is a flag, not a calibrated probability.
- Per-policy Laya hints (`choice_hint`/`score_hint` in `policy.yaml`) are declared
  but deliberately **not** wired: appending them to question instructions dropped
  AgentDojo attack recall 25/27 → 14/27 (overblocks fell only as a side effect of
  blanket leniency, so it was reverted). One generic question set is used until a
  mechanism that survives eval is found.
- AgentDojo numbers are **judge-level only** (attack text judged directly, default
  `source=user`), not end-to-end agent rollouts. See `eval/run_rollout.py` for the
  rollout harness (requires Ollama + `qwen2.5:7b`).
