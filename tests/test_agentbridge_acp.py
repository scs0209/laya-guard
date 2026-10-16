"""The ACP bridge: what the client sends (and never offers), what it collects, and every way a turn can fail.

The agent is stood in by tests/fake_acp.py: a real child process speaking NDJSON JSON-RPC over stdio, scripted per
test, with no model behind it.
"""
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import fake_acp  # noqa: E402
from laya_guard import agentbridge  # noqa: E402
from laya_guard.agentbridge import AgentError  # noqa: E402


@pytest.fixture
def fake(tmp_path, monkeypatch):
    def make(replies=None, **cfg):
        f = fake_acp.install(tmp_path / "acpbin", replies, **cfg)
        monkeypatch.setenv("LAYA_GUARD_CODEX_ACP_BIN", str(f["bin"]))
        for k, v in f["env"].items():
            monkeypatch.setenv(k, v)
        agentbridge._cache.clear()
        return f
    return make


def codex():
    return agentbridge.get("codex-acp")


def by_method(calls, method):
    return [m for m in calls if m.get("method") == method]


def test_the_client_offers_nothing_and_runs_nowhere(fake, tmp_path):
    f = fake(['{"type": "read_file", "path": "package.json"}'])
    done = codex().complete("THE SYSTEM", "THE PROMPT", schema={"type": "object"})
    assert '"read_file"' in done.text and done.duration_ms is not None
    calls = f["calls"]()
    init = by_method(calls, "initialize")[0]
    assert init["params"]["clientCapabilities"] == {}                       # no fs, no terminal, no elicitation
    assert init["params"]["protocolVersion"] == 1 and init["params"]["clientInfo"]["name"] == "laya-guard"
    new = by_method(calls, "session/new")[0]
    assert new["params"]["mcpServers"] == []                                # no MCP servers either
    assert "laya-guard-agent-" in new["params"]["cwd"] and not Path(new["params"]["cwd"]).exists()
    prompt = by_method(calls, "session/prompt")[0]
    assert prompt["params"]["sessionId"] == "sess-test"
    assert prompt["params"]["prompt"][0] == {"type": "text", "text": "THE SYSTEM"}
    assert "THE PROMPT" in prompt["params"]["prompt"][1]["text"] and '"type": "object"' in prompt["params"]["prompt"][1]["text"]


def test_chunks_join_and_a_reported_cost_is_kept(fake):
    f = fake([["{", '"type": "finish"}']], cost=0.02)
    done = codex().complete("s", "p")
    assert done.text == '{"type": "finish"}' and done.cost_usd == 0.02


def test_a_permission_is_denied_and_anything_else_is_refused(fake):
    f = fake([[{"_permission": True}, "ok"], [{"_fs": "fs/read_text_file"}]])
    done = codex().complete("s", "p")
    assert done.text == "ok"
    answer = next(m for m in f["calls"]() if m.get("id") == "perm-1")
    assert answer["result"] == {"outcome": {"outcome": "cancelled"}}         # never allowed, always cancelled
    codex().complete("s", "p")
    answer = next(m for m in f["calls"]() if m.get("id") == "fs-1")
    assert answer["error"]["code"] == -32601                                # never advertised: method not found


def test_a_talkative_stdout_does_not_break_the_turn(fake):
    fake(["hi"], garbage="some log line")
    assert codex().complete("s", "p").text == "hi"


def test_a_different_protocol_version_is_a_failure_not_a_handshake(fake):
    fake(["hi"], protocol_version=99)
    with pytest.raises(AgentError) as e:
        codex().complete("s", "p")
    assert e.value.kind == "failed" and "v99" in e.value.message


def test_a_login_refusal_is_logged_out_not_broken(fake):
    fake(["hi"], error_on="session/new", error="Not logged in: run codex login")
    with pytest.raises(AgentError) as e:
        codex().complete("s", "p")
    assert e.value.kind == "not-logged-in" and "login" in e.value.message


def test_refusals_and_strange_stops_are_errors(fake):
    fake(["no"], stop_reason="refusal")
    with pytest.raises(AgentError) as e:
        codex().complete("s", "p")
    assert e.value.kind == "failed" and "refused" in e.value.message
    fake(["no"], stop_reason="mysterious")
    with pytest.raises(AgentError) as e:
        codex().complete("s", "p")
    assert e.value.kind == "failed" and "mysterious" in e.value.message


def test_a_slow_agent_times_out_and_a_stop_kills_it(fake):
    f = fake(["hi"], sleep=30)
    t0 = time.monotonic()
    with pytest.raises(AgentError) as e:
        codex().complete("s", "p", timeout=0.8)
    assert e.value.kind == "timeout" and time.monotonic() - t0 < 8
    pid = int(Path(str(f["log"]) + ".pid").read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)                                                     # the child did not outlive the wait
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    with pytest.raises(AgentError) as e:
        codex().complete("s", "p", cancel=cancel, timeout=60)
    assert e.value.kind == "cancelled"      # a sleeping agent can't read the session/cancel sent first; it is killed


def test_a_missing_binary_is_reported_before_anything_runs(monkeypatch, tmp_path):
    monkeypatch.delenv("LAYA_GUARD_CODEX_ACP_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    info = agentbridge.CodexAcp.detect()
    assert info.found is False and "codex-acp" in info.detail
    with pytest.raises(AgentError) as e:
        agentbridge.CodexAcp.complete("s", "p")
    assert e.value.kind == "not-installed"
    gemini = agentbridge.GeminiAcp.detect()
    assert gemini.found is False and "gemini" in gemini.detail


def test_detect_reads_a_key_but_never_claims_a_login_it_did_not_check(fake, monkeypatch):
    fake()
    info = codex().detect()
    assert info.found and info.logged_in is None and "CODEX_API_KEY" in info.detail and info.kind == "cli"
    assert "denied" in info.inert and "read-only" in info.sends
    monkeypatch.setenv("CODEX_API_KEY", "sk-test")
    assert codex().detect().logged_in is True


def test_how_each_agent_is_started(fake, monkeypatch):
    f = fake()
    assert codex().argv() == [str(f["bin"])]
    monkeypatch.delenv("LAYA_GUARD_CODEX_ACP_BIN")
    monkeypatch.setattr(agentbridge.shutil, "which", lambda name: "/bin/npx" if name == "npx" else None)
    assert agentbridge.CodexAcp.argv() == ["/bin/npx", "-y", "@agentclientprotocol/codex-acp"]
    monkeypatch.setattr(agentbridge.shutil, "which", lambda name: "/bin/gemini" if name == "gemini" else None)
    assert agentbridge.GeminiAcp.argv() == ["/bin/gemini", "--acp"]


def test_the_registry_lists_every_bridge_in_pick_order(fake):
    assert [a.id for a in agentbridge.detect_all(fresh=True)] == [
        "claude-code", "codex-acp", "gemini-acp", "anthropic-api", "openai-api", "gemini-api", "manual-relay", "demo"]
