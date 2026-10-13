# Observe and label (Claude Code)

Tools for collecting **human-labeled verdicts** from your own usage, so wrong verdicts
(overblocks and missed attacks) can be measured and turned into eval rows. They never
block or ask anything; the enforcing hook is still [`guard-hook.sh`](../../guard-hook.sh).

| Script | Hook | What it does |
|---|---|---|
| `observe-hook.py` | PreToolUse | Logs the verdict (server, or `check --offline` when it is down) plus the masked command. Always exits 0. |
| `observe-post-hook.py` | PostToolUse | Logs the `tool_use_id` of calls that actually ran. Never stores tool output. |
| `observe-label.py` | (CLI) | Walks through flagged calls and records the correct verdict you type. |
| `observe-report.py` | (CLI) | Verdict counts, latency, and the flagged commands. |
| `observe_common.py` | | Shared paths, masking and helpers. Keep it next to the scripts. |

## Setup

Copy the directory somewhere stable and register the hooks. Run the observer alone for a
dry run, or next to `guard-hook.sh` (hooks in one matcher run in parallel):

```json
{
  "hooks": {
    "PreToolUse": [{"matcher": "Bash", "hooks": [
      {"type": "command", "command": "/path/to/laya-guard/guard-hook.sh"},
      {"type": "command", "command": "python3 /path/to/observe/observe-hook.py"}
    ]}],
    "PostToolUse": [{"matcher": "Bash", "hooks": [
      {"type": "command", "command": "python3 /path/to/observe/observe-post-hook.py"}
    ]}]
  }
}
```

Set `LAYA_GUARD_BIN` if `laya-guard` isn't on the agent's `PATH`, and `LAYA_GUARD_URL` if the
server isn't on `http://127.0.0.1:8787/guard`. Data lives in `$LAYA_GUARD_HOME`
(default `$XDG_DATA_HOME/laya-guard`, i.e. `~/.local/share/laya-guard`).

## Labeling

```bash
python3 observe-label.py            # flagged calls, one by one
python3 observe-label.py --all      # include allow, to look for missed attacks
python3 observe-label.py add "<command>" block
python3 observe-label.py stats
```

At each prompt type `a` / `r` / `b` for the correct verdict (allow / review / block), Enter if
the model was right, `s` to skip, `q` to quit. `stats` prints the model-vs-label matrix.

**Only labels you type are ground truth.** The log also shows whether a call ran after a
`review`, but that is a hint: if an agent talks you into approving a dangerous command, it must
not be learned as safe. Don't auto-label from approvals.

## Privacy

This is opt-in and separate from `verdicts.jsonl`, which never stores command text. Here the
**masked command (up to 4,000 characters)** is kept so you can label it later. Files are mode
0600 and local only. Masking is best-effort: it hides common token shapes, `KEY=value` pairs
whose name contains PASSWORD / SECRET / TOKEN / API_KEY, bearer tokens and PEM private keys,
but it cannot catch a free-form password. Delete the files when you are done.
