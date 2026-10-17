# Files: protect, record, review

[← Back to README](../../README.md) · **English** · [한국어](../ko/guide/file-protection.md)

Protected paths for Claude Code's file tools, the opt-in log of the files your agents touched, the optional macOS OS monitor, and `laya-guard footprint`.

## Protect files from the agent's file tools (opt-in)

The Claude Code hook guards `Bash` by default, so Claude Code's own `Read`, `Edit` and `Write` never reach it: nothing
stops them from opening `~/.ssh/id_rsa`. `--files` adds them:

```bash
laya-guard install claude-code --files     # Bash + Read, Edit, MultiEdit, Write, NotebookEdit, Glob, Grep
```

A file tool call is then judged by the paths it names, against **protected paths**: no model runs, and the content
being read or written is neither judged nor sent. Out of the box, credential stores (`~/.ssh`, `~/.aws`, `~/.gnupg`,
`~/.kube`, …), `.env*` files and private keys are *reviewed* (Claude Code asks you) when read, credential stores are
*blocked* when changed, and a write to the agent's own user-level settings and hooks, to a shell startup file
(`~/.bashrc`, `~/.zshrc`, …) or to `.git/hooks/**` is reviewed ([`protect.yaml`](../../src/laya_guard/protect.yaml)). A call that
names more than 50 paths, or a path over 4096 characters, is reviewed too, never skipped. Add your own in `~/.config/laya-guard/protect.yaml`; edits apply to the
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

### Shell commands

Every agent's shell commands are held to the same rules, by the paths their text names, online and offline. The files a
command plainly reads or changes (`cat`, `grep`, `source`, `<`, `rm`, `mv`, `cp`'s target, `>`, …) count with what is
done to them, and a relative path follows a plain `cd`, so `cd /etc && cat shadow` is a read of `/etc/shadow`. Any other
path-like string in the command, like the code in `python -c "open('/home/me/.ssh/id_rsa')"`, counts as a read: what
the program does with it is unknown, so a write rule is not applied on a guess. This only ever makes a verdict stricter:
the command is still judged by the regexes and the model, and the stricter answer wins. A command with more than 200
path-like strings is reviewed. Try it: `laya-guard check --offline --shell "cd /etc && cat shadow"`.

A path a script or a child process only builds while it runs (`open(os.environ["KEY_FILE"])`, a file read by a build
tool) never reaches the hooks; on macOS the [OS monitor](#see-what-the-os-saw-not-just-what-the-agent-said-macos-optional)
records it. A `cd` that can't be read off the text (`cd -`, `cd $DIR`, `pushd`) isn't followed.

## See which files your agents touched (opt-in)

laya-guard can keep a record of the files an agent's tool calls name, so you can see what your agents actually reached
for. It is off by default, because it writes **file paths** (never contents) to disk:

```bash
laya-guard activity on        # start recording
laya-guard activity           # the state and the latest events
laya-guard activity off       # stop; what was recorded stays
```

Each line of `~/.local/share/laya-guard/file-activity.jsonl` (readable by you only; `LAYA_GUARD_FILE_ACTIVITY_PATH`
moves it) is one file one call named: `{ts, agent, tool_name, op, path, abs, cwd, source, action, policy_id}` (`cwd`: the directory
the call ran in; OS-monitor lines add `would`).

- `op` is `read`, `write` or `delete`.
- `source` says how far to trust a path. `hook`: a file tool (`Read`, `Edit`, `Write`, …) named it. `shell`: guessed
  from a Bash command, simple forms only (`cat`, `rm`, `cp`, `mv`, `tee`, redirects, …). Anything the shell would still
  have to expand (`$VAR`, globs, `$(…)`) is skipped, so this misses things.
- `action` is what the guard answered, so a blocked attempt isn't mistaken for an access.

It lives next to `verdicts.jsonl`, not in it: that file still never holds a path. Set `LAYA_GUARD_FILE_ACTIVITY=1` or
`0` to decide from the environment instead (it wins over `activity on|off`). Recording stops at 50 MB. Bash calls are
recorded as soon as it is on, for **every supported agent** (each adapter sends its agent's name and the bare command);
file tool calls need `install --files` and are Claude Code only so far, because the other agents' hooks are registered
for shell commands only. It records declared access only. An adapter that knows what a non-shell tool touches can
report it as `accesses` (`POST /guard`, or `laya-guard check --access read PATH`), which is judged against the protected
paths and recorded like a file tool.

## See what the OS saw, not just what the agent said (macOS, optional)

The hooks only know what a tool call says it will touch. A script the agent runs, a child process or a build tool never
reaches them. On macOS, `laya-guard watch` asks the operating system instead: it reads Apple's `eslogger` (Endpoint
Security), keeps the files **the agent's own processes** really opened, created, deleted or renamed, and adds them to the
same file activity log with `source: "os-monitor"`.

```bash
laya-guard activity on        # the log it writes to
laya-guard watch              # starts `sudo eslogger` for you (it asks for your password); Ctrl+C stops it
# or run the privileged part yourself and pipe it in (only eslogger runs as root, laya-guard runs as you):
sudo eslogger exec fork exit open unlink rename create | laya-guard watch
```

It needs `sudo`, and Full Disk Access for the terminal app you run it from (System Settings > Privacy & Security > Full
Disk Access; quit and reopen the app). Then `laya-guard activity` shows the events next to the hook ones.

- **Which processes.** A program named for an agent (`claude`, `codex`, `cursor-agent`, `gemini`, `qwen`, `copilot`,
  `cline`, `opencode`, `kilo`, `amp`; `--match NAME[=agent]` adds more) starts a tree, and everything it starts is in it,
  scripts and child processes included. Agents already running when you start are found from `ps`. Everything else on the
  machine is dropped as soon as it is read.
- **What is kept.** Writes, deletes and renames always. Reads when a protected path is named, and (by default) when the
  file is in one of your own folders outside the project the agent was started in; system directories, hidden
  directories (`~/.nvm`, `~/.zshrc`, …) and `~/Library` are ambient noise unless a rule names them. `--reads
  protected|outside|all` changes that (`all` is very large) and `--ignore GLOB` cuts your own noise (a protected path is
  never ignored). The same program doing the same thing to the same path within five seconds is one event.
- **The record.** Like the hook events, plus `action: "observed"` (it already happened; nothing was stopped) and `would`:
  what a [protected-path](#protect-files-from-the-agents-file-tools-opt-in) rule would have answered (`review`, `block` or
  `null`). `tool_name` is the program that touched the file (`cat`, `python3`, …).
- **Privacy.** An `exec` message carries the process's whole environment, which holds secrets: it is never read, stored or
  logged, and neither are command lines or file contents. Paths are written in clear to the same owner-only file.

It reports; it cannot block, so blocking stays with the hooks. Apple documents `eslogger`'s output as "NOT API": the shape
can change with a macOS release, an unrecognised message is skipped rather than guessed at, and `watch` says so when a
stream never looks like what it expects. Events can be lost if the machine is overloaded, and macOS reports the file that
was opened, which may not be the name the agent used to reach it (a symlink), so write rules with real paths. A probe on
one Mac (macOS 26) saw every read, write, delete and rename a stand-in agent made, including reads inside Python and Node
child processes, at about 700 messages a second and 8 % of one core for `eslogger`. It has not been run for long sessions
with real agents yet.

## See where your agents reached, and what to protect (footprint)

With the file activity log on, `laya-guard footprint` answers "what did my agents touch?" per project and session, and
"what should I protect that I'm not protecting yet?":

```bash
laya-guard footprint                     # the last 7 days, per project and session
laya-guard footprint --since 30d --project ~/work/app --agent claude-code
laya-guard footprint --suggest           # protect.yaml entries worth a look; nothing is applied
laya-guard footprint --json              # the same, for scripts
```

For each project: how many reads, writes and deletes, which agents, sessions, the accesses that hit a protected path, the
files outside the project and the most touched ones. The dashboard's Overview has the same as a File footprint card
(only when the log has something; click a project for the detail).

- **A project** is the git repository the call's directory is in (the directory itself when there is none); events from
  before the log kept `cwd`, and calls whose adapter sent no directory, are under `(unknown)`.
- **A session** is one agent's run of activity in one project with no gap over 30 minutes. The hooks don't send a session
  id, so this is a guess from the timestamps.
- **Stopped is not touched.** A call the guard blocked didn't happen, so it is counted as a stopped attempt. A call sent to
  review is counted as touched: the log can't tell whether you approved it.
- **Suggestions** are of two kinds, and never applied. *Sensitive*: an agent touched a well-known place for secrets that the
  default rules don't cover (cloud CLI credentials, browser profiles, shell history, `*.pem`, `*.tfstate`, ...). *Rare*:
  a folder in your own home, outside the project, that agents touched only a few times (in a few sessions): cheap to
  protect, and somewhere you may not expect an agent to be. Something an agent uses all the time is not suggested, since
  protecting it means constant review prompts. Each comes as a `protect.yaml` entry with `review` as its action; paste
  what you want into `~/.config/laya-guard/protect.yaml`. **An access alone is not evidence of bad intent**: an agent that
  read `~/Documents/taxes/2025.pdf` once may have been asked to.
