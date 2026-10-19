# OS sandbox

The hook judges what a command's text says. A script, a child process, or a path built while a command runs never
reaches it ([Real incidents](incidents.md)). An OS sandbox does: the kernel refuses the read or the write, whatever
asked for it. Most coding agents ship one, but their defaults stop writes outside the project and leave reads alone,
so a script can still read `~/.ssh` or `~/.aws/credentials`.

`laya-guard sandbox <agent>` turns the agent's own sandbox on and denies it the paths in
[protect.yaml](../../src/laya_guard/protect.yaml) (and your own `~/.config/laya-guard/protect.yaml`). The hook keeps
judging every call first; the sandbox is the backstop for what it can't see.

```bash
laya-guard sandbox --list               # what each agent's sandbox can take
laya-guard sandbox claude-code          # shows the change, asks, backs up the file, writes it
laya-guard sandbox codex --dry-run      # only show it
laya-guard sandbox claude-code --remove # takes out exactly what was added
laya-guard run -- opencode              # an agent with no sandbox of its own, inside sandbox-runtime
```

A sandbox can only refuse, it can't ask. A protected path the hook would `review` is denied in the sandbox. When a
command really needs one (git over SSH needs `~/.ssh`), the agent offers to run it outside the sandbox and asks you.

## What each agent gets

| Agent | Where | What laya-guard writes | Checked on this machine |
|---|---|---|---|
| Claude Code | `~/.claude/settings.json` (or `--scope project/local`) | `sandbox.enabled`, `sandbox.filesystem.denyRead` / `denyWrite` | a real session (2.1.280): protected reads refused, project files readable, the hook still ran |
| Codex | `~/.codex/config.toml` | a permissions profile (`default_permissions = "laya-guard"`, extends `:workspace`): protected paths `"deny"`, write-only ones `"read"` | `codex sandbox` (0.163.0) |
| Copilot CLI | `~/.copilot/settings.json` | `sandbox.enabled`, `sandbox.userPolicy.filesystem.deniedPaths` / `readonlyPaths` | the CLI (1.0.98) reads the settings; enforcement needs a signed-in session, not run |
| Gemini CLI | `<project>/.gemini/` | a Seatbelt profile built from the installed CLI's default plus the denies, `tools.sandbox`, `SEATBELT_PROFILE` in `.gemini/.env` | the profile under `sandbox-exec` (0.20.0) |
| Qwen Code | `<project>/.qwen/` | the same, from Qwen's own default profile | the profile under `sandbox-exec` (0.25.2) |
| Cursor | `~/.cursor/cli-config.json` | `sandbox.mode = "enabled"` only | not run |
| OpenCode, Amp, Kilo, ... in a terminal | nothing written | `laya-guard run -- <agent>` runs it inside [sandbox-runtime](https://github.com/anthropics/sandbox-runtime) (`srt`) with the protected paths denied | `opencode` and a probe under `srt` 0.0.80 |
| Windsurf, Cline, and agents inside an IDE | — | not supported: the IDE can't be wrapped from outside | — |

The probe on every row that says checked: reading a test secret and `~/.ssh` was refused, a private key nested in the
project (`**/id_rsa`) was refused, a project file was readable.

## What each sandbox can't take

These stay with the hook, and `laya-guard sandbox` lists them before writing:

- **Rules with exceptions.** `**/.env.*` has `except: [**/.env.example, ...]`, and no sandbox here can carve an
  exception out of a deny. `**/.env` goes in (its exceptions can't match it).
- **Codex:** a glob takes only `"deny"`, so a write-only `**/` rule (`**/.git/hooks`) can't be expressed. `**/` globs
  apply under the workspace roots, and on Linux and Windows Codex resolves them when it starts. Codex ignores the
  profile while `sandbox_mode` is set anywhere (config.toml or `-s/--sandbox`); laya-guard refuses to write it then.
- **Copilot CLI:** plain paths only, no globs. Only paths that exist are listed, because Copilot creates a missing
  denied path as a directory before every command. Its sandbox also runs laya-guard's hook, and on macOS that sandbox
  can't reach localhost, so the hook may fall back to the offline check.
- **Gemini CLI / Qwen Code:** macOS only (Seatbelt). The installed versions look for the profile only in `.gemini/` or
  `.qwen/` under the directory they start in, so each project needs its own (`--scope project`, the default for them).
  The whole CLI runs inside the profile, hooks included. Re-run after upgrading the CLI: the profile is rebuilt from
  its default. With docker/podman the sandbox only mounts the project, so `~/.ssh` isn't there anyway.
- **Cursor:** its sandbox has no deny list for reads outside a git repository, so this only turns it on (writes
  outside the project, network). In the Cursor app the setting is under Settings > Agents > Approvals & Execution.
- **`laya-guard run`:** needs `srt` (`npm install -g @anthropic-ai/sandbox-runtime`). It can't leave the network
  open, so it allows the common model APIs and package registries; add a host with `--allow-domain`, a writable
  directory with `--allow-write`. An agent that has its own sandbox shouldn't be run under it: Seatbelt can't be
  nested, so the agent's sandbox would fail to start.

## How it is undone

Every change is shown before it's written and the old file is backed up next to it (`*.laya-guard-<time>.bak`).
`~/.config/laya-guard/sandbox.json` records what was added, so `--remove` takes out only those entries, puts back a
switch it turned on, and deletes a file it created if nothing else is in it. Codex's profile and the Seatbelt files
are marked blocks and files that `--remove` deletes.
