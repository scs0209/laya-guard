"""bench/run.py reads a hook's answer the way Claude Code does. Fake hooks only; no third-party guard is installed."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
import run  # noqa: E402


@pytest.mark.parametrize("code, stdout, want", [
    (2, "", "block"),
    (0, json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}}), "block"),
    (0, json.dumps({"hookSpecificOutput": {"permissionDecision": "ask"}}), "ask"),
    (0, json.dumps({"hookSpecificOutput": {"permissionDecision": "allow"}}), "allow"),
    (0, json.dumps({"decision": "block", "reason": "x"}), "block"),
    (0, json.dumps({"continue": False}), "block"),
    (0, "", "allow"),
    (0, "not json", "allow"),
    (1, json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}}), "allow"),   # a non-blocking error
])
def test_a_hook_answer_is_read_as_claude_code_reads_it(code, stdout, want):
    assert run.decide(code, stdout) == want


def _settings(home, groups):
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": groups}}))


def test_only_hooks_that_match_bash_are_run(tmp_path):
    _settings(tmp_path, [
        {"matcher": "Bash|PowerShell", "hooks": [{"type": "command", "command": "a"}]},
        {"matcher": "Edit", "hooks": [{"type": "command", "command": "b"}]},
        {"hooks": [{"type": "command", "command": "c"}]},
    ])
    assert [h["command"] for h in run.bash_hooks(tmp_path)] == ["a", "c"]


def test_the_strictest_hook_wins_and_gets_the_claude_code_input(tmp_path):
    seen = tmp_path / "seen.json"
    ask = f"cat > {seen}; echo '{{\"hookSpecificOutput\": {{\"permissionDecision\": \"ask\"}}}}'"
    hooks = [{"command": "cat >/dev/null; exit 0"}, {"command": ask}, {"command": "cat >/dev/null; exit 1"}]
    case = {"id": "x", "command": "rm -rf ~/"}
    r = run.run_case(case, hooks, {"PATH": "/usr/bin:/bin"}, tmp_path)
    assert r["decision"] == "ask" and [h["decision"] for h in r["hooks"]] == ["allow", "ask", "allow"]
    sent = json.loads(seen.read_text())
    assert (sent["hook_event_name"], sent["tool_name"], sent["tool_input"]["command"]) == ("PreToolUse", "Bash",
                                                                                          "rm -rf ~/")
    assert run.run_case(case, hooks + [{"command": "cat >/dev/null; exit 2"}], {"PATH": "/usr/bin:/bin"},
                        tmp_path)["decision"] == "block"


def test_the_case_set_is_well_formed():
    cases = run.load_cases(run.BENCH / "cases.jsonl")
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["set"] in ("incident", "evasion", "everyday") and c["expected"] in ("hold", "allow", "any"), c
        assert (c["expected"] == "allow") == (c["set"] == "everyday"), c
