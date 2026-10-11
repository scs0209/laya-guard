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
  `LAYA_GUARD_BIN` support, and a contract test in `tests/test_integrations.py`.
- Keep PRs small and focused. Add a line under `## [Unreleased]` in
  [CHANGELOG.md](CHANGELOG.md).
- Commit messages and PR descriptions in English.
