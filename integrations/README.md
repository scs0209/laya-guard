# Agent integrations

All adapters gate tool execution through your local laya-guard server
(`laya-guard serve`, default `http://localhost:8787/guard`).
Set `LAYA_GUARD_URL` to override. When the server is unreachable or slower
than 10s, every adapter falls back to `laya-guard check --offline`:
regex-flagged calls are blocked, safe commands pass, and the rest pass unless
`LAYA_GUARD_FAIL=closed`. Set `LAYA_GUARD_BIN` if the CLI isn't on the agent's
`PATH`. See the main README's "Server down" section.

| Agent | Mechanism | Files | review maps to |
|---|---|---|---|
| Claude Code | PreToolUse hook (stdin JSON, exit 2 blocks) | [`../guard-hook.sh`](../guard-hook.sh) | `ask` (permission prompt; `LAYA_GUARD_REVIEW=pass` to pass through) |
| OpenCode | `tool.execute.before` plugin (throw blocks) | [`opencode/laya-guard.ts`](opencode/laya-guard.ts) | allow + stderr warning |
| Codex CLI | `PreToolUse` command hook (deny JSON) | [`codex/`](codex/) | allow + stderr note (PreToolUse has no ask lane) |
| Cursor | `beforeShellExecution` hook (exit 2 / deny JSON) | [`cursor/`](cursor/) | `ask` (human approval lane) |
| Kilo Code | `tool.execute.before` plugin (throw blocks) | [`kilo/laya-guard.ts`](kilo/laya-guard.ts) | allow + stderr warning |
| Windsurf (Cascade) | `pre_run_command` hook (exit 2 blocks) | [`windsurf/`](windsurf/) (config: `~/.codeium/windsurf/hooks.json`) | exit 0 + stderr note |
| Cline | `PreToolUse` executable (`cancel: true/false`) | [`cline/PreToolUse`](cline/PreToolUse) (copy to `.clinerules/hooks/PreToolUse`) | passes (`cancel: false`) |
| Gemini CLI | `BeforeTool` hook (`decision: deny` / exit 2) | [`gemini/`](gemini/) | exit 0 + stderr note |
| Qwen Code | `PreToolUse` hook (`permissionDecision`) | [`qwen/`](qwen/) | `ask` (falls back to deny headless) |
| Amp | `delegate` permissions helper (exit 0/1/2) | [`amp/permissions-helper`](amp/permissions-helper) (`amp permissions add delegate`) | exit 1 = ask |
| Copilot CLI | `preToolUse` hook (`permissionDecision`) | [`copilot/`](copilot/) | `ask` (deny when headless) |

No hook mechanism (documented as unsupported — no adapter possible):
**Aider** (no pre-execution hook or policy found in official docs),
**Roo Code** (no released script hook; extension shut down May 2026 — Kilo Code
is its active lineage), **Continue** (allow/ask/exclude tiers only, no hooks;
repo archived), **Junie** (allow/ask allowlists only, no deny, no hooks).

## Claude Code

`guard-hook.sh` is a PreToolUse hook: register it in your settings, it POSTs
each tool call and exits 2 on `block`. On `review` it prints a `permissionDecision: "ask"`
JSON so Claude Code asks you; set `LAYA_GUARD_REVIEW=pass` to let `review` through.
In non-interactive runs (`claude -p`) nobody can answer, so the `ask` is denied.

## OpenCode

Copy `opencode/laya-guard.ts` to `.opencode/plugins/laya-guard.ts` (project)
or `~/.config/opencode/plugins/laya-guard.ts` (global). Docs:
https://opencode.ai/docs/plugins/ — the `tool.execute.before` hook throws to
block. For hard asks, add `permission` rules in `opencode.json`:
https://opencode.ai/docs/permissions/

## Codex CLI

1. `cp codex/check_command.py ~/.codex/hooks/ && chmod +x ~/.codex/hooks/check_command.py`
2. Append `codex/config-snippet.toml` to `~/.codex/config.toml`
   (requires `[features] hooks = true`).
3. Docs: https://developers.openai.com/codex/hooks — note hooks are a
   guardrail, not a complete enforcement boundary.

## Cursor

1. `cp cursor/laya-guard.py ~/.cursor/hooks/ && chmod +x ~/.cursor/hooks/laya-guard.py`
2. Merge `cursor/hooks.json` into `~/.cursor/hooks.json` (user) or
   `<project>/.cursor/hooks.json` (project; change the command path to
   `.cursor/hooks/laya-guard.py`). Docs: https://cursor.com/docs/hooks

## Kilo Code

Copy `kilo/laya-guard.ts` to `.kilo/plugin/laya-guard.ts` (project) or
`~/.config/kilo/plugin/laya-guard.ts` (global). For hard asks, add
`permission` rules in `kilo.jsonc`. Docs:
https://kilo.ai/docs/automate/extending/plugins

## Windsurf (Cascade)

1. `cp windsurf/guard.py <hooks-dir>/ && chmod +x <hooks-dir>/guard.py`
2. Add a `pre_run_command` entry to `~/.codeium/windsurf/hooks.json`
   (workspace: `.devin/hooks.json`):
   `{"hooks": {"pre_run_command": [{"command": "<hooks-dir>/guard.py",
   "show_output": true}]}}`. Exit 2 blocks. Docs: Cascade Hooks.

## Cline

Copy `cline/PreToolUse` to `.clinerules/hooks/PreToolUse` (project) or
`~/Documents/Cline/Hooks/PreToolUse` (global), `chmod +x`, and enable hooks
in Cline Features settings. The script returns `{"cancel": true/false}`.

## Gemini CLI

1. `cp gemini/check.py .gemini/hooks/ && chmod +x .gemini/hooks/check.py`
2. Merge `gemini/settings-snippet.json` into `.gemini/settings.json`
   (project) or `~/.gemini/settings.json` (user). Deny via
   `{"decision": "deny"}` or exit 2. Docs: Gemini CLI hooks guide.

## Qwen Code

1. `cp qwen/check.py .qwen/hooks/ && chmod +x .qwen/hooks/check.py`
2. Merge `qwen/settings-snippet.json` into `.qwen/settings.json`
   (project) or `~/.qwen/settings.json` (user). `review` maps to `ask`.
   Docs: Qwen Code hooks reference.

## Amp

1. `cp amp/permissions-helper ~/.local/bin/amp-laya-guard && chmod +x ~/.local/bin/amp-laya-guard`
2. `amp permissions add delegate --to amp-laya-guard '*'`
   Exit codes are the full verdict: 0 allow, 1 ask, 2 reject.
   Docs: Amp permissions guide.

## GitHub Copilot CLI

1. `cp copilot/policy.py ~/.copilot/hooks/ && chmod +x ~/.copilot/hooks/policy.py`
2. Add `copilot/laya-guard.json` to `~/.copilot/hooks/` (user) or
   `.github/hooks/` (repo). `review` maps to `ask` (deny when headless).
   Docs: Copilot hooks reference.
