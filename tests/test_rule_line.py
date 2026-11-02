"""The line between the rules and the model (docs/guide/how-it-works.md, "What the rules catch"), checked on every PR.

A block-level rule skips the model, so a false block can't be corrected later: an ordinary command is never blocked by
a rule. A command the rules already block, rewritten the way the shell runs it, stays blocked. And any other change to
what the rules answer has to be saved in tests/data/rule_verdicts.json in the same PR, so it shows in the diff.
"""
import importlib.util
import json
import os
from pathlib import Path

os.environ["LAYA_GUARD_MOCK"] = "1"
ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rule_snapshot", ROOT / "scripts" / "rule_snapshot.py")
snapshot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(snapshot)


def _ordinary() -> list[tuple[str, str]]:
    """Commands no rule may block: the benchmark's everyday commands, the eval set's allowed ones, and
    tests/data/never_blocked.jsonl (dangerous words as text, narrower deletes, what the shell wouldn't run)."""
    out = [(c["id"], c["command"]) for c in snapshot._jsonl(ROOT / "bench" / "cases.jsonl") if c["set"] == "everyday"]
    out += [(f"shell:{r['id']}", r["tool_call"]) for r in snapshot._jsonl(ROOT / "eval" / "testset_shell.jsonl")
            if r["expected"] == "allow"]
    out += [(r["why"], r["command"]) for r in snapshot._jsonl(ROOT / "tests" / "data" / "never_blocked.jsonl")]
    return out


def test_an_ordinary_command_is_never_blocked_by_a_rule():
    blocked = [f"{why}: {cmd!r} -> {snapshot.rule_answer(cmd)}" for why, cmd in _ordinary()
               if snapshot.rule_answer(cmd).startswith("block:")]
    assert not blocked, "a rule blocks an ordinary command (a false block skips the model):\n" + "\n".join(blocked)


def test_a_rewrite_of_a_blocked_command_stays_blocked():
    missed = [f"{r['command']!r} -> {snapshot.rule_answer(r['command'])}"
              for r in snapshot._jsonl(ROOT / "tests" / "data" / "rewrites_blocked.jsonl")
              if not snapshot.rule_answer(r["command"]).startswith("block:")]
    assert not missed, "the shell runs these as a blocked command, and no rule blocks them:\n" + "\n".join(missed)


def test_what_the_rules_answer_is_saved_with_the_change():
    saved = json.loads(snapshot.SNAPSHOT.read_text(encoding="utf-8"))
    diff = snapshot.changes(saved, snapshot.current())
    assert not diff, ("what the rules answer changed. If that is intended, run `python scripts/rule_snapshot.py "
                      "--update` and commit tests/data/rule_verdicts.json with the change:\n" + "\n".join(diff))
