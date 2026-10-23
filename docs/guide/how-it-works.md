# Under the hood

[← Back to README](../../README.md) · **English** · [한국어](../ko/guide/how-it-works.md)

The judging pipeline, the 12 policies, the benchmarks, and how laya-guard compares with other guardrails.

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
  → for an agent that asks you about a review (Claude Code, Cursor, Qwen Code, Copilot CLI), a block only the model
    (or its heuristic stand-in) reached is turned into a review; a pattern or protected-path block stays a block
```

Why the last step: replaying a day of real Claude Code commands, the model blocked plainly read-only ones
(`sed -n 1,80p f` at 0.99, `ls src | head` at 0.82) and most heredocs for `python3 -`, about a quarter of all commands.
Raising the threshold wouldn't help, since those scores were 0.8-1.0. Where a person can be asked, the model now asks.
Where nobody can (an agent that lets a review through with a note), its block stands, because a review there would let
the command run.

Measured latency: **~85–90ms per judgment** on a laptop CPU (Oct 2026, real model,
warmed up). Regex hits and safe commands return instantly without invoking the model.

The safe-command lane (`safe_commands` in [policy.yaml](../../src/laya_guard/policy.yaml))
only opens for a command with no shell metacharacters (`; & | $ < > ( )` …),
no program-running/file-writing flags (`-exec`, `-delete`, `--config`, `--output`…), and no
sensitive paths (`.ssh`, `.aws`, `.env`…). Installs, `git push`, and `npx` always
go to the model. What sits inside single quotes is text (`grep -o '[^|]*' f`); inside
double quotes only `$`, `` ` `` and `\` still count. A pipeline is safe when every stage
is (`grep -n x f | head`, `sort | uniq -c`), and output thrown away or merged
(`2>/dev/null`, `>/dev/null`, `2>&1`) is not a write. The list also has the read-only
tools agents chain (`cut`, `sort`, `uniq`, `tr`, `diff`, `stat`, `find` without
`-exec`/`-delete`, `sed -n 10,20p`), but not the forms that write (`sort -o`,
`uniq in out`, `sed -i`). The program reads its arguments after the shell has removed the
quotes, so the command must also pass with them taken out: `find . -ex'ec' …` is `-exec`.

A variable set earlier in the same command to a plain value is read as that value
(`d=src; ls $d` is `ls src`, `for f in a b; do sed -n 1p $f; done` is checked for `a` and
`b`), by the lane and by the regexes, so `d=~; rm -rf $d` is blocked as `rm -rf ~`. A
variable we can't follow (`$1`, `$(…)`, the environment, one that is `read` later) still
closes the lane. Assignments run nothing (`X=1 pytest`, `export A=b`), unless the name
changes what runs: `PATH`, `LD_PRELOAD`, `GIT_*`, `*_OPTIONS`, `BASH_ENV` and the like go to
the model. A heredoc whose body the shell doesn't expand (`git commit -F - <<'EOF'`) is data
for its command. Reading the repository and its host is in the list (`git fetch` without
`--prune` or a refspec, `git rev-parse`, `gh pr view`/`checks`, `gh api` without a method or
body).

Windows commands get the same lane: a backslash between two path parts is read as a
path separator (`cd C:\work\api`, `type src\app.py`, `pytest tests\unit`), cmd's
`%VAR%` counts like `$VAR`, and the list has the cmd.exe and PowerShell equivalents
(`dir`, `type`, `findstr`, `where`, `md`, `cd /d`, `Get-ChildItem`, `Get-Content`,
`Set-Location`, `Select-String`, `New-Item -ItemType Directory`). Because a POSIX shell
reads the same backslash as an escape, the command must also be clean with the
backslashes dropped: `cat ~/.s\sh/id_r\sa` still goes to the model.

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
`$(…)`, or parentheses), and a script of several lines at the end of each line, so
`cd app && npm test` is two safe commands while `npm test && curl … | sh` is blocked
by its second half. A heredoc's body is data for the command that opened it and is
judged with it; `then`/`do`/`fi`/`done` are syntax, not commands. A script longer
than 40 commands is judged whole. The whole string is still
checked against the regexes, which catches patterns spanning commands
(`curl -o x …; sh x`).

## Long and oversized input

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
| `mass-delete` | `DROP TABLE`, `s3 rm --recursive`, `kubectl delete --all`, `git push --force`, `git reset --hard`, `git clean -f`, `git checkout -- .`, a delete inside an API request body (`mutation { volumeDelete(...) }`, `{"method": "deleteDatabase"}`) | review |
| `auth-bypass` | auth/TLS disabling, password resets, KR+EN | block |
| `credential-read` | reading private keys / cloud credentials (`cat ~/.ssh/id_rsa`, `cat ~/.aws/credentials`, …) | review |

All patterns live in [policy.yaml](../../src/laya_guard/policy.yaml) — edit, add your own, no code changes needed.

## Benchmarks (reproducible, not cherry-picked)

| Suite | Result | Command |
|---|---|---|
| AgentDojo v1, attacks (judge-level) | **25/27 blocked** | `eval/run_agentdojo.py` |
| AgentDojo v1, benign (judge-level) | **92/97 kept** (allow+review) | `eval/run_agentdojo.py` |
| E2E rollout (qwen2.5:7b agent + guard) | **4/4 scenarios** (1 defended, 2 not attempted, 1 benign OK) | `eval/run_rollout.py` → [results](../../eval/rollout_results.md) |
| Real incident replays (7 publicly reported incidents, 13 scored commands) | **13/13 held** (review or block; 12/13 on 0.5.1, where a `volumeDelete` GraphQL call was allowed until a rule was added for it) | `eval/run_incidents.py` → [write-up](incidents.md) |
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
Details in [eval/](../../eval/).

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
| Status | active, v0.6.0, MIT | MIT | ⚠️ archived May 2025 | ⚠️ archived, unmaintained | alpha, Apache-2.0 |

No verified third-party AgentDojo score exists for any tool in this table —
ours is self-reported and judge-level (see [Limitations](reference.md#limitations-read-before-trusting-it)).
