# Connecting your agents

[← Back to README](../../README.md) · **English** · [한국어](../ko/guide/agents.md)

How `laya-guard install` hooks each supported agent, what it writes, and how to call the guard from any other agent loop.

## Supported agents

| Agent | Install | Integration | `review` becomes |
|---|---|---|---|
| Claude Code | `laya-guard install claude-code` | [PreToolUse hook](../../guard-hook.sh) | `ask` (permission prompt; `LAYA_GUARD_REVIEW=pass` to pass through) |
| Codex CLI | `laya-guard install codex` | [`PreToolUse` hook](../../integrations/codex/) | allow + stderr note (no ask lane) |
| Cursor | `laya-guard install cursor` | [`beforeShellExecution` hook](../../integrations/cursor/) | `ask` (human approval) |
| Gemini CLI | `laya-guard install gemini-cli` | [`BeforeTool` hook](../../integrations/gemini/check.py) | exit 0 + stderr note |
| Qwen Code | `laya-guard install qwen-code` | [`PreToolUse` hook](../../integrations/qwen/check.py) | `ask` (deny when headless) |
| Windsurf | `laya-guard install windsurf` | [`pre_run_command` hook](../../integrations/windsurf/guard.py) | exit 0 + stderr note |
| Copilot CLI | `laya-guard install copilot-cli` | [`preToolUse` hook](../../integrations/copilot/policy.py) | `ask` (deny when headless) |
| Cline | `laya-guard install cline` | [`PreToolUse` executable](../../integrations/cline/PreToolUse) | passes (`cancel: false`) |
| OpenCode | `laya-guard install opencode` | [`tool.execute.before` plugin](../../integrations/opencode/laya-guard.ts) | allow + stderr warning |
| Kilo Code | `laya-guard install kilo` | [`tool.execute.before` plugin](../../integrations/kilo/laya-guard.ts) | allow + stderr warning |
| Amp | `laya-guard install amp` | [`tool.call` plugin](../../integrations/amp/laya-guard.ts) | allow + stderr note |

No hook mechanism, so no adapter: Aider, Roo Code (shut down May 2026),
Continue (tiers only, repo archived), Junie (allow/ask only). Details in
[integrations/](../../integrations/).

`laya-guard install <agent>` does the setup for you ([what it writes and what's left to do](#1c-every-other-agent));
the manual steps per agent are in [integrations/](../../integrations/). Claude Code, Cursor, Qwen Code and Copilot CLI route
`review` to a human (and deny it when nobody can answer); elsewhere `review` warns and passes, so use native
`permission`/`allowlist` rules alongside if you want hard asks.

Which agents the [file activity log](file-protection.md#see-which-files-your-agents-touched-opt-in) records: shell commands from all of the
above, and Claude Code's file tools. The others' file tools reach it agent by agent, as their hooks are extended past
shell commands.

## Use cases

Ready-made adapters for 11 agents live in [integrations/](../../integrations/) —
see Supported agents above for the full list.

### 1. Guard Claude Code (PreToolUse hook)

```bash
laya-guard install claude-code             # ~/.claude/settings.json; shows a diff and asks first
laya-guard install claude-code --scope local   # ./.claude/settings.local.json (also: project)
laya-guard status --check                  # where it's installed, is the server up, does the hook work
laya-guard uninstall claude-code           # removes only what `install` added
```

`install` registers the hook for the `Bash` tool (`--matcher` to change that, `--files` to add the file tools:
[Files guide](file-protection.md#protect-files-from-the-agents-file-tools-opt-in)), writes absolute
paths (hooks often don't inherit your virtualenv's `PATH`), prints a diff, backs up the file
and asks before writing (`--dry-run` to only look, `--yes` to skip the question). It merges into
your existing hooks, never overwrites them, and leaves a hand-written `guard-hook.sh` entry alone
unless you pass `--force`. Afterwards it sends two test calls through the hook (a harmless one and a
download piped into a shell) and checks the exit codes. The hook is a bash script, so Windows isn't
supported yet. To do it by hand instead:

Register [guard-hook.sh](../../guard-hook.sh) as a PreToolUse hook. It reads the tool
payload from stdin, POSTs it to the local server, and exits 2 on `block`
(cancelling that run). `review` asks you: the hook prints a `permissionDecision: "ask"`
JSON and Claude Code shows a permission prompt with the policy and reason
(`LAYA_GUARD_REVIEW=pass` restores the old pass-through). `allow` passes silently.
Non-interactive runs (`claude -p`) have nobody to answer, so an `ask` is denied there.
For the Bash tool only the command is judged, not the agent's description.
If the server is unreachable or slower than 10s, it falls back to the offline
check (see [Server down](reliability.md#server-down)). Point it elsewhere with `LAYA_GUARD_URL`.

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
rewrites the file without them. A hand-written adapter from [integrations/cursor/](../../integrations/cursor/) is
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
[integrations/](../../integrations/) (a test keeps them identical). **None of the agents are launched in the tests.**
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
