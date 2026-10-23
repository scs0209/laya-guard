# Audit what your agents already ran

Before you install a hook, you can see what it would have done. Claude Code and Codex keep every session on disk,
including each shell command the agent ran, where it ran, and when. `laya-guard audit` reads those transcripts and
judges every command the way that agent's hook would have. Then it lists what would have been reviewed or blocked.

```bash
laya-guard audit                        # the last 24 hours, Claude Code and Codex
laya-guard audit --since 7d             # a week (7d, 24h, 30d or all)
laya-guard audit --agent codex          # one agent
laya-guard audit --redact               # reasons instead of commands, for a screenshot
laya-guard audit --json                 # everything, one finding per held command
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

(The commands above are made up; the shape is real.)

## What it reads

| Agent | Transcripts | A shell call is |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` (subagents included) | a `Bash` tool use: `input.command`, the session's `cwd`, `timestamp`, and whether the result was an error |
| Codex | `~/.codex/sessions/**/*.jsonl` (or under `CODEX_HOME`) | an `exec_command` / `shell` function call: `cmd`, `workdir` (else the session's `cwd`), `timestamp` |

Only files changed within `--since` are opened, and only calls inside it are judged.

## How it judges

Each command goes through the same judge the server uses, with that agent's settings:

- **Claude Code** is judged as source `user` and with `ask`: its hook turns a review into a permission prompt, so a
  block only the model reached is a review ([Under the hood](how-it-works.md#how-it-works)).
- **Codex** is judged as source `tool` without `ask`: its hook lets a review through with a note, so a model block
  stays a block.
- Both are also held to the [protected paths](file-protection.md), as the hook does.

The same command in the same directory is judged once. Commands the safe lane or a pattern decides cost nothing; the
rest go to the model. Measured on a laptop CPU (Oct 2026): a heavy day of 1,961 Claude Code commands took 11 minutes,
about 0.33 s a command with the model load included (long heredocs are read in several windows). That is why the
default period is a day. A progress counter runs on stderr when it is a terminal. If the model isn't installed it says so in the summary line
(`rules and heuristics only (mock mode)`).

## What it doesn't do

- **Nothing is written or sent.** The verdict log, the dashboard and the alerts never see these judgments; the
  transcripts are only read.
- **It says what the hook would have answered, not what happened.** A command held here might have been fine, and an
  allowed command could still have done harm through a script the text doesn't show. The OS sandbox
  ([OS sandbox](sandbox.md)) covers that part.
- **Other agents** (Gemini CLI, Cursor, Copilot CLI…) are not read yet. Their transcripts are in other formats or not
  kept on disk.
- **Other tools** than the shell (file edits, web fetches, MCP calls) are not judged.
