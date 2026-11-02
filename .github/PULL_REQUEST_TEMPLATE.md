<!--
Thanks for contributing! See CONTRIBUTING.md for the gates and guidelines.
PR title must follow Conventional Commits, e.g. "fix(policy): stop flagging timestamps as RRNs".
-->

## Summary

<!-- What does this change, and why? Link related issues (e.g. Fixes #123). -->

## Test plan

<!-- How was it tested? -->

- [ ] `python -m pytest tests/ -q`
- [ ] MOCK evals: `--min-acc 1.0` (ko) and `--set shell --source tool --min-acc 0.96`
- [ ] If what the rules answer changed: `python scripts/rule_snapshot.py --update`, and the changed lines of `tests/data/rule_verdicts.json` are explained above
- [ ] If `policy.yaml`, the safe-command lane, or the model input changed: real-model eval numbers before/after
- [ ] Line added under `## [Unreleased]` in CHANGELOG.md
