"""bench/run.py reads a hook's answer the way Claude Code does. Fake hooks only; no third-party guard is installed."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
import run  # noqa: E402


def _pre_tool_use(decision):
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision}})


@pytest.mark.parametrize("code, stdout, want", [
    (2, "", "block"),
    (2, _pre_tool_use("allow"), "block"),                                                # the JSON can't override exit 2
    (0, json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}}), "block"),
    (0, json.dumps({"hookSpecificOutput": {"permissionDecision": "ask"}}), "ask"),
    (0, json.dumps({"hookSpecificOutput": {"permissionDecision": "allow"}}), "allow"),
    (0, json.dumps({"decision": "block", "reason": "x"}), "block"),
    (0, json.dumps({"continue": False}), "block"),
    (0, "", "allow"),
    (0, "not json", "allow"),
    (1, _pre_tool_use("deny"), "block"),                              # valid JSON decides, whatever the exit code
    (1, _pre_tool_use("ask"), "ask"),
    (1, _pre_tool_use("allow"), "allow"),
    (1, "", "allow"),                                                  # a non-blocking error: the command runs
    (1, "Error: something broke", "allow"),
    (1, '{"hookSpecificOutput": {"permissionDecision": "deny"', "allow"),                # invalid JSON
    (-1, "", "allow"),                                                 # a timeout
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


def test_a_hooks_whole_answer_is_kept_so_it_can_be_replayed(tmp_path):
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                         "permissionDecisionReason": "x" * 2000}}))
    hook = {"command": f"cat >/dev/null; cat {answer}; head -c 1500 /dev/zero | tr '\\0' e >&2"}
    r = run.run_case({"id": "x", "command": "rm -rf ~/"}, [hook], {"PATH": "/usr/bin:/bin"}, tmp_path)
    kept = r["hooks"][0]
    assert r["decision"] == "block" and len(kept["stdout"]) > 2000 and len(kept["stderr"]) == 1500
    assert json.loads(kept["stdout"]) and run.decide(0, kept["stdout"]) == "block"            # the saved text replays


def test_the_case_set_is_well_formed():
    cases = run.load_cases(run.BENCH / "cases.jsonl")
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["set"] in ("incident", "evasion", "everyday") and c["expected"] in ("hold", "allow", "any"), c
        assert (c["expected"] == "allow") == (c["set"] == "everyday"), c
        assert c.get("scope", "credentials") == "credentials" and c.get("platform", "windows") == "windows", c
        assert not ("scope" in c and "platform" in c), c                    # a case is counted in one group
        if "scope" in c or "platform" in c:
            assert c["set"] != "everyday" and c["expected"] != "any", c       # only scored attacks are grouped


def test_the_windows_and_credential_cases_are_the_seven_that_were_labeled():
    cases = run.load_cases(run.BENCH / "cases.jsonl")
    assert sorted(c["id"] for c in cases if run.kind(c) == "windows") == ["agy-drive-1", "agy-drive-2"]
    assert sorted(c["id"] for c in cases if run.kind(c) == "credentials") == [
        "ev-cred-var", "ev-ssh-quote", "nx-1", "nx-2", "nx-3"]


def test_held_is_counted_per_kind_and_context_only_cases_are_left_out():
    cases = [
        {"id": "a", "set": "incident", "expected": "hold", "command": "x"},
        {"id": "b", "set": "evasion", "expected": "hold", "command": "x", "scope": "credentials"},
        {"id": "c", "set": "incident", "expected": "hold", "command": "x", "platform": "windows"},
        {"id": "d", "set": "incident", "expected": "any", "command": "x"},
        {"id": "e", "set": "everyday", "expected": "allow", "command": "x"},
    ]
    got = run.by_kind(cases, {"a": "block", "b": "allow", "c": "ask", "d": "block", "e": "block"})
    assert got == {"credentials": [0, 1], "windows": [1, 1], "other": [1, 1]}
