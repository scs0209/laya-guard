# Contributing to laya-guard

Thanks for helping. This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Found a vulnerability?

Don't open a public issue. Follow [SECURITY.md](SECURITY.md) and report it
privately. A single command that is judged wrong (a missed attack or an
overblock) is a quality bug, not a vulnerability: open a normal issue for it.

## The most useful contribution: a wrong verdict

Commands that slip through or get blocked for no reason are how the rules
improve. Use the **Missed attack** or **Overblock** issue form and include the
exact string plus the output of:

```bash
laya-guard check --json "<the exact command>"
```

Every confirmed case becomes a row in an eval set so it can't regress.

## Picking up an issue

Comment `/assign` on an issue to take it; a bot assigns it to you and reacts
with 👍. It only works on open, unassigned issues (look for
[`good first issue`](https://github.com/scs0209/laya-guard/labels/good%20first%20issue)
and [`help wanted`](https://github.com/scs0209/laya-guard/labels/help%20wanted)).
Comment `/unassign` if you can't continue. If an assigned issue sees no
activity for two weeks, a maintainer may unassign it so someone else can
pick it up.

Small fixes don't need an issue first; open the PR directly.

## Development setup

```bash
git clone https://github.com/scs0209/laya-guard && cd laya-guard
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"     # real model (pulls in PyTorch)
```

For regex, adapter, or docs work you can skip the model entirely:

```bash
pip install pyyaml pytest
export LAYA_GUARD_MOCK=1 PYTHONPATH=src
```

## Gates (CI runs these on Python 3.10 to 3.13)

CI on PRs from outside contributors starts after a maintainer approves the run.
CodeRabbit also posts an automated review; a maintainer still reviews every PR.

```bash
python -m pytest tests/ -q
LAYA_GUARD_MOCK=1 PYTHONPATH=src python eval/run_eval.py --min-acc 1.0
LAYA_GUARD_MOCK=1 PYTHONPATH=src python eval/run_eval.py --set shell --source tool --min-acc 0.96
```

### If you change `policy.yaml`, the safe-command lane, or what the model sees

MOCK mode doesn't exercise the model, so also run the real-model evals and put
the before/after numbers in the PR description. No API key is needed.

```bash
PYTHONPATH=src .venv/bin/python eval/run_eval.py --set shell --source tool
PYTHONPATH=src .venv/bin/python eval/run_eval.py --set shell --source user
PYTHONPATH=src .venv/bin/python eval/run_eval.py --set ko
PYTHONPATH=src .venv/bin/python eval/run_agentdojo.py     # needs: pip install agentdojo
```

## Guidelines

- **Block-level regexes must be high precision.** A regex hit on a
  `action_on_regex: block` policy skips the model, so a false positive there
  can't be corrected downstream. When in doubt, use a `review` policy.
- **Safe commands must stay boring.** Anything added to `safe_commands` skips
  the model. Add a matching bypass attempt to `BYPASS` in `tests/test_guard.py`.
- **New eval cases** go in `eval/testset_shell.jsonl` (one JSON object per
  line: `id`, `tool_call`, `expected` of `allow` / `review` / `block`).
- **Adapters** in `integrations/` are standalone, standard-library-only
  scripts users copy into place. Keep the contract of the existing ones: 10s
  server timeout, `laya-guard check --offline` fallback, `LAYA_GUARD_FAIL` and
  `LAYA_GUARD_BIN` support (an unjudged call is asked about where the agent
  can ask and blocked where it can't, unless `LAYA_GUARD_FAIL=open`), and a
  contract test in `tests/test_integrations.py`.
- Keep PRs small and focused. Add a line under `## [Unreleased]` in
  [CHANGELOG.md](CHANGELOG.md).
- Commit messages, PR titles, and PR descriptions in English.

## Commit messages and PR titles

PRs are squash-merged, and the **PR title becomes the commit message on
`main`**. PR titles must follow
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/); a CI
check (`pr-title`) enforces it. Commits inside your branch are squashed away,
but following the same format there is appreciated.

```
<type>(<optional scope>): <subject>
```

| Type | Use for |
| --- | --- |
| `feat` | New behavior: a policy, an adapter, a CLI flag |
| `fix` | Bug fixes, including wrong verdicts (missed attacks, overblocks) |
| `docs` | Documentation only |
| `test` | Tests or eval rows only |
| `refactor` | Code change with no behavior change |
| `perf` | Faster or lighter, same behavior |
| `ci` | GitHub Actions workflows, labels, repo automation |
| `build` | Packaging, dependencies, `pyproject.toml` |
| `chore` | Release bumps and other maintenance |
| `revert` | Reverting an earlier commit |

Scopes are optional; use the area you touched: `policy`, `guard`, `adapter`,
`server`, `cli`, `eval`, `deps`.

- Subject in the imperative mood, starting lowercase, no trailing period,
  ideally under 72 characters: `fix(policy): stop flagging timestamps as RRNs`,
  not `Fixed RRN bug.`
- Breaking changes (a verdict contract, CLI output, or config that users rely
  on) get a `!` after the type or scope, e.g. `feat(server)!: return 403 on block`,
  and a `BREAKING CHANGE:` line in the PR description.
- Reference issues in the PR description (`Fixes #4`), not in the title.

## The simulation view

The dashboard's Simulation view is plain ES modules in `src/laya_guard/static/sim/` plus a bundled, tree-shaken three.js
in `static/vendor/` (MIT). There is no build step for the modules. The pure logic (stages, replay, runs, layout) has
node tests that need no browser (`tests/test_sim_js.py` runs them when `node` is installed), and
`tests/test_sim_browser.py` drives a real headless Chrome through the whole view, with and without WebGL (it needs node 22
and Chrome or Chromium, and is skipped otherwise; set `LAYA_TEST_CHROME` to point at a binary).

If the view starts using another three.js class, or you want to bump three.js, rebuild the bundle (needs node, npm and
network access) and commit the result:

```bash
scripts/vendor_three.sh            # the pinned release
scripts/vendor_three.sh 0.187.0    # another one
```
