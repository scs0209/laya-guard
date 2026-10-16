"""The bridge to the coding agent: how it is started (inert), what it is sent, and every way it can fail."""
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import fake_claude  # noqa: E402
from laya_guard import agentbridge  # noqa: E402
from laya_guard.agentbridge import AgentError, ClaudeCode  # noqa: E402


@pytest.fixture
def fake(tmp_path, monkeypatch):
    def make(replies=None, **cfg):
        f = fake_claude.install(tmp_path / "bin", replies, **cfg)
        for k, v in f["env"].items():
            monkeypatch.setenv(k, v)
        agentbridge._cache.clear()
        return f
    return make


def test_the_agent_is_started_so_that_it_cannot_act(fake):
    f = fake([{"ok": True}])
    ClaudeCode().complete("You are a function.", "THE PROMPT", schema={"type": "object"}, model="haiku")
    call = f["calls"]()[0]
    a = call["argv"]
    assert a[0] == "-p" and "THE PROMPT" not in a and call["prompt"] == "THE PROMPT"            # the prompt goes in on stdin
    assert a[a.index("--tools") + 1] == ""                                                       # every built-in tool off
    assert a[a.index("--setting-sources") + 1] == ""                                             # no settings, no hooks
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in a
    assert a[a.index("--output-format") + 1] == "json" and a[a.index("--system-prompt") + 1] == "You are a function."
    assert a[a.index("--model") + 1] == "haiku" and a[a.index("--json-schema") + 1] == '{"type": "object"}'
    assert float(a[a.index("--max-budget-usd") + 1]) > 0
    assert call["thinking"] == "0"                                                               # no hidden reasoning for a JSON decision
    assert call["env"] == {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}       # none of your CLAUDE.md or memory files go along
    assert not {"--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--permission-mode", "--bare"} & set(a)


def test_it_runs_in_an_empty_throwaway_directory(fake):
    f = fake([{"ok": True}])
    ClaudeCode().complete("s", "p")
    cwd = f["calls"]()[0]["cwd"]
    assert "laya-guard-agent-" in cwd and not Path(cwd).exists()               # gone again, and it was never the project


def test_a_structured_answer_comes_back_as_data(fake):
    fake([{"type": "read_file", "path": "package.json"}])
    c = ClaudeCode().complete("s", "p")
    assert c.data == {"type": "read_file", "path": "package.json"} and '"read_file"' in c.text
    assert c.cost_usd == 0.001 and c.model == "fake-model" and c.duration_ms == 12


def test_a_plain_text_answer_is_kept_for_the_caller_to_parse(fake):
    fake(["```json\n{\"type\": \"finish\"}\n```"])
    c = ClaudeCode().complete("s", "p")
    assert c.data is None and "finish" in c.text


def test_the_agents_own_error_is_reported_with_its_words(fake):
    fake([{"_error": "Credit balance is too low"}])
    with pytest.raises(AgentError) as e:
        ClaudeCode().complete("s", "p")
    assert e.value.kind == "failed" and "Credit balance is too low" in e.value.message
    fake([{"_error": "Not logged in · Please run /login"}])
    with pytest.raises(AgentError) as e:
        ClaudeCode().complete("s", "p")
    assert e.value.kind == "not-logged-in"


def test_garbage_and_failures_are_errors_not_crashes(fake):
    fake(raw="this is not json")
    with pytest.raises(AgentError) as e:
        ClaudeCode().complete("s", "p")
    assert e.value.kind == "bad-output"
    fake(raw="[1, 2]")
    with pytest.raises(AgentError) as e:
        ClaudeCode().complete("s", "p")
    assert e.value.kind == "bad-output"
    fake(exit_code=3, stderr="boom: something broke")
    with pytest.raises(AgentError) as e:
        ClaudeCode().complete("s", "p")
    assert e.value.kind == "failed" and "something broke" in e.value.message


def test_a_slow_agent_is_killed_at_the_timeout(fake):
    fake(sleep=30)
    t0 = time.monotonic()
    with pytest.raises(AgentError) as e:
        ClaudeCode().complete("s", "p", timeout=0.6)
    assert e.value.kind == "timeout" and time.monotonic() - t0 < 5


def test_cancelling_kills_the_agent_at_once(fake):
    fake(sleep=30)
    cancel = threading.Event()
    threading.Timer(0.4, cancel.set).start()
    t0 = time.monotonic()
    with pytest.raises(AgentError) as e:
        ClaudeCode().complete("s", "p", cancel=cancel, timeout=60)
    assert e.value.kind == "cancelled" and time.monotonic() - t0 < 5


def test_a_missing_agent_is_reported_before_anything_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("LAYA_GUARD_CLAUDE_BIN", str(tmp_path / "nope"))
    monkeypatch.setenv("PATH", str(tmp_path))
    info = ClaudeCode().detect()
    assert info.found is False and "isn't on PATH" in info.detail
    with pytest.raises(AgentError) as e:
        ClaudeCode().complete("s", "p")
    assert e.value.kind == "not-installed"


def test_detect_reads_only_the_login_flag_never_the_account(fake):
    fake()
    info = ClaudeCode().detect()
    assert info.found and info.logged_in is True and info.version.startswith("9.9.9")
    assert "someone@example.com" not in repr(info) and "Acme" not in repr(info)
    assert "--tools" in info.inert
    fake(logged_in=False)
    out = ClaudeCode().detect()
    assert out.logged_in is False and "isn't logged in" in out.detail


def test_detect_all_asks_once_per_half_minute(fake, monkeypatch):
    f = fake()
    agentbridge.detect_all(fresh=True)
    seen = []
    real = ClaudeCode.detect
    monkeypatch.setattr(ClaudeCode, "detect", lambda self: seen.append(1) or real(self))
    agentbridge.detect_all()
    agentbridge.detect_all()
    assert seen == []                                        # served from the cache
    agentbridge.detect_all(fresh=True)
    assert seen == [1]
    assert [a.id for a in agentbridge.detect_all()] == ["claude-code", "codex-acp", "gemini-acp", "anthropic-api",
                                                         "openai-api", "gemini-api", "manual-relay", "demo"]


def test_another_adapter_can_be_registered(monkeypatch):
    class Other:
        id, name = "other", "Other"
        def detect(self): return agentbridge.AgentInfo("other", "Other", True)
        def complete(self, *a, **k): return agentbridge.Completion("{}")
    monkeypatch.setitem(agentbridge._ADAPTERS, "other", Other())
    agentbridge._cache.clear()
    assert [a.id for a in agentbridge.detect_all(fresh=True)][-1] == "other" and agentbridge.get("other").name == "Other"


def test_a_lone_surrogate_in_the_prompt_does_not_break_the_call(fake):
    f = fake([{"ok": True}])
    done = agentbridge.get("claude-code").complete("sys", "scenario \ud83d text", schema=None)
    assert done.text and "scenario ? text" in f["calls"]()[0]["prompt"]


def test_whatever_ends_the_wait_the_agent_does_not_outlive_it(fake, monkeypatch):
    """An exception while waiting (not just a timeout or a stop) used to leave the child running."""
    fake([{"ok": True}], sleep=30)
    killed = []
    real_kill = agentbridge._kill
    monkeypatch.setattr(agentbridge, "_kill", lambda proc: (killed.append(proc), real_kill(proc)))
    real_communicate = agentbridge.subprocess.Popen.communicate
    calls = {"n": 0}

    def communicate(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("something unexpected")
        return real_communicate(self, *a, **kw)
    monkeypatch.setattr(agentbridge.subprocess.Popen, "communicate", communicate)
    with pytest.raises(RuntimeError):
        agentbridge.get("claude-code").complete("sys", "prompt")
    assert len(killed) == 1 and killed[0].poll() is not None
