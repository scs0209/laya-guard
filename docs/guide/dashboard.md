# Dashboard and simulation

[← Back to README](../../README.md) · **English** · [한국어](../ko/guide/dashboard.md)

The local dashboard (`laya-guard dashboard`), the pipeline simulation and AI scenarios.

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
  reviews, and one card per agent that isn't protected yet. With the [file activity log](file-protection.md#see-which-files-your-agents-touched-opt-in)
  on, a File footprint card shows what each project touched (click a project for the detail); while the log is off and
  empty it shows only a hint.
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
