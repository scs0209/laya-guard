"""The model-API adapters (Anthropic, OpenAI, Gemini): what a key unlocks, what is sent, and every way a call can fail.

The APIs are stood in by tests/fake_api.py: real HTTP on 127.0.0.1, a scripted answer, no real key and no real bill.
"""
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
import fake_api  # noqa: E402
from laya_guard import agentbridge  # noqa: E402
from laya_guard.agentbridge import AgentError  # noqa: E402


def adapter(id):
    return agentbridge.get(id)


@pytest.fixture
def fake(monkeypatch):
    fake = fake_api.start()
    monkeypatch.setattr(agentbridge.AnthropicApi, "base", fake.base)
    monkeypatch.setattr(agentbridge.OpenAiApi, "base", fake.base)
    monkeypatch.setattr(agentbridge.GeminiApi, "base", fake.base)
    for a in (adapter("anthropic-api"), adapter("openai-api"), adapter("gemini-api")):
        monkeypatch.setattr(a, "models", [])
    agentbridge._cache.clear()
    yield fake
    fake.close()
    agentbridge._cache.clear()


def use_key(monkeypatch, **keys):
    for k, v in keys.items():
        monkeypatch.setenv(k, v)


# ---- without a key -------------------------------------------------------------------------------------------------

def test_without_a_key_nothing_is_found_and_nothing_runs(fake, monkeypatch):
    for a, env in ((adapter("anthropic-api"), "ANTHROPIC_API_KEY"), (adapter("openai-api"), "OPENAI_API_KEY"),
                   (adapter("gemini-api"), "GEMINI_API_KEY")):
        info = a.detect()
        assert (info.found, info.kind) == (False, "api") and env in info.detail
        assert "restart laya-guard" in info.detail
        with pytest.raises(AgentError) as e:
            a.complete("sys", "prompt")
        assert e.value.kind == "not-installed" and env in e.value.message
    assert "no tools" in adapter("openai-api").inert


def test_gemini_also_reads_google_api_key(fake, monkeypatch):
    use_key(monkeypatch, GOOGLE_API_KEY="AIza-test")
    fake.models = [{"id": "models/gemini-2.0-flash"}]
    info = adapter("gemini-api").detect()
    assert info.found and info.logged_in is True


# ---- listing models ------------------------------------------------------------------------------------------------

def test_detect_offers_chat_models_cheap_default_first(fake, monkeypatch):
    use_key(monkeypatch, OPENAI_API_KEY="sk-test")
    fake.models = [{"id": "gpt-4o", "created": 1}, {"id": "gpt-4o-mini", "created": 3}, {"id": "o1", "created": 2},
                   {"id": "text-embedding-ada-002", "created": 4}, {"id": "tts-1", "created": 5},
                   {"id": "gpt-4o-deep-research", "created": 6}, {"id": "gpt-5-pro", "created": 7},
                   {"id": "gpt-codex", "created": 8}]
    info = adapter("openai-api").detect()
    assert info.found and info.logged_in is True
    ids = [m["id"] for m in info.models]
    assert ids[0] == "gpt-4o-mini" and "(default)" in info.models[0]["label"]
    assert not any("embed" in m or "tts" in m for m in ids)           # not chat models
    assert not any("codex" in m or "-pro" in m or "deep-research" in m for m in ids)  # this endpoint doesn't serve them


def test_detect_keeps_the_newest_anthropic_first_and_prefers_haiku(fake, monkeypatch):
    use_key(monkeypatch, ANTHROPIC_API_KEY="sk-ant-test")
    fake.models = [{"id": "claude-sonnet-4-5"}, {"id": "claude-haiku-4-5"}, {"id": "not-a-model"}]
    info = adapter("anthropic-api").detect()
    assert [m["id"] for m in info.models] == ["claude-haiku-4-5", "claude-sonnet-4-5"]


def test_detect_strips_the_gemini_prefix_and_sorts_newest_first(fake, monkeypatch):
    use_key(monkeypatch, GEMINI_API_KEY="AIza-test")
    fake.models = [{"id": "models/gemini-1.5-flash"}, {"id": "models/gemini-2.0-flash"}, {"id": "models/embedding-001"}]
    info = adapter("gemini-api").detect()
    assert [m["id"] for m in info.models] == ["gemini-2.0-flash", "gemini-1.5-flash"]


def test_a_key_that_lists_no_chat_model_is_said_so(fake, monkeypatch):
    use_key(monkeypatch, OPENAI_API_KEY="sk-test")
    fake.models = [{"id": "text-embedding-ada-002"}]
    info = adapter("openai-api").detect()
    assert info.found and info.logged_in is True and "no chat model" in info.detail
    with pytest.raises(AgentError) as e:
        adapter("openai-api").complete("sys", "prompt")
    assert e.value.kind == "failed" and "no chat model" in e.value.message


# ---- refusals: a bad key, a rate limit, a server error ---------------------------------------------------------------

def test_a_rejected_key_is_logged_out_not_broken(fake, monkeypatch):
    use_key(monkeypatch, ANTHROPIC_API_KEY="sk-bad")
    fake.status, fake.error = 401, {"error": {"message": "invalid x-api-key"}}
    info = adapter("anthropic-api").detect()
    assert info.found and info.logged_in is False and "rejected the key" in info.detail and "sk-bad" not in info.detail
    with pytest.raises(AgentError) as e:
        adapter("anthropic-api").complete("sys", "prompt")
    assert e.value.kind == "not-logged-in"


def test_gemini_answers_a_bad_key_with_400(fake, monkeypatch):
    use_key(monkeypatch, GEMINI_API_KEY="AIza-bad")
    fake.status, fake.error = 400, {"error": {"message": "API key not valid. Pass a valid API key."}}
    info = adapter("gemini-api").detect()
    assert info.logged_in is False
    with pytest.raises(AgentError) as e:
        adapter("gemini-api").complete("sys", "prompt")
    assert e.value.kind == "not-logged-in"


def test_a_rate_limit_says_so_and_other_errors_keep_their_words(fake, monkeypatch):
    use_key(monkeypatch, OPENAI_API_KEY="sk-test")
    fake.status, fake.error = 429, {"error": {"message": "You exceeded your quota."}}
    with pytest.raises(AgentError) as e:
        adapter("openai-api").complete("sys", "prompt")
    assert e.value.kind == "failed" and "429" in e.value.message and "quota" in e.value.message
    fake.status, fake.error = 500, {"error": {"message": "The server had an error."}}
    with pytest.raises(AgentError) as e:
        adapter("openai-api").complete("sys", "prompt")
    assert e.value.kind == "failed" and "500" in e.value.message


def test_the_key_never_reaches_an_error_message(fake, monkeypatch):
    secret = "sk-secret-abc123"
    use_key(monkeypatch, OPENAI_API_KEY=secret)
    fake.status = 500
    fake.error = {"error": {"message": f"key {secret} broke something"}}
    with pytest.raises(AgentError) as e:
        adapter("openai-api").complete("sys", "prompt")
    assert secret not in e.value.message and "[key]" in e.value.message


# ---- a call: what is sent, what comes back ---------------------------------------------------------------------------

def test_a_completion_carries_system_and_prompt_and_no_tools(fake, monkeypatch):
    use_key(monkeypatch, OPENAI_API_KEY="sk-test")
    fake.models = [{"id": "gpt-4o-mini", "created": 1}]
    fake.replies = ['{"type": "read_file", "path": "package.json"}']
    done = adapter("openai-api").complete("THE SYSTEM", "THE PROMPT", schema={"type": "object"})
    assert '"read_file"' in done.text and done.model == "gpt-4o-mini" and done.duration_ms is not None
    call = fake.chats()[0]
    body = call["body"]
    assert body["model"] == "gpt-4o-mini" and body["messages"][0] == {"role": "system", "content": "THE SYSTEM"}
    assert "THE PROMPT" in body["messages"][1]["content"] and '"type": "object"' in body["messages"][1]["content"]
    assert call["headers"]["authorization"] == "Bearer sk-test"                      # the key goes in a header, never the page
    assert "sk-test" not in (body["messages"][1]["content"])


def test_anthropic_blocks_and_an_explicit_model_wins(fake, monkeypatch):
    use_key(monkeypatch, ANTHROPIC_API_KEY="sk-ant-test")
    fake.models = [{"id": "claude-haiku-4-5"}, {"id": "claude-sonnet-4-5"}]
    fake.replies = ['{"type": "finish", "outcome": "done"}']
    done = adapter("anthropic-api").complete("sys", "prompt", model="claude-sonnet-4-5")
    assert '"finish"' in done.text and done.model == "claude-sonnet-4-5"
    call = fake.chats()[0]
    assert call["body"]["model"] == "claude-sonnet-4-5" and call["body"]["system"] == "sys"
    assert call["headers"]["x-api-key"] == "sk-ant-test" and call["headers"]["anthropic-version"] == "2023-06-01"


def test_a_model_refusal_is_an_error_not_an_answer(fake, monkeypatch):
    use_key(monkeypatch, OPENAI_API_KEY="sk-test")
    adapter("openai-api").models = [{"id": "gpt-4o-mini", "label": "gpt-4o-mini (default)"}]

    class Refuser:
        def _handle(self, h, body):
            import json as j
            self.requests.append({"method": h.command, "path": h.path, "headers": {}, "body": body})
            out = j.dumps({"model": "gpt-4o-mini",
                           "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "refusal": "I won't play along"}}]})
            self._send(h, 200, out)
    fake._handle = Refuser._handle.__get__(fake, type(fake))
    with pytest.raises(AgentError) as e:
        adapter("openai-api").complete("sys", "prompt")
    assert e.value.kind == "failed" and "refused" in e.value.message


def test_garbage_answers_are_errors_not_crashes(fake, monkeypatch):
    use_key(monkeypatch, OPENAI_API_KEY="sk-test")
    adapter("openai-api").models = [{"id": "gpt-4o-mini", "label": "gpt-4o-mini (default)"}]
    fake.raw = "this is not json"
    with pytest.raises(AgentError) as e:
        adapter("openai-api").complete("sys", "prompt")
    assert e.value.kind == "bad-output"
    fake.raw = "[1, 2]"
    with pytest.raises(AgentError) as e:
        adapter("openai-api").complete("sys", "prompt")
    assert e.value.kind == "bad-output" and "no message" in e.value.message


def test_a_slow_api_times_out_and_a_stop_lands_at_once(fake, monkeypatch):
    use_key(monkeypatch, GEMINI_API_KEY="AIza-test")
    fake.models = [{"id": "models/gemini-2.0-flash"}]
    fake.sleep = 30
    with pytest.raises(AgentError) as e:
        adapter("gemini-api").complete("sys", "prompt", timeout=0.6)
    assert e.value.kind == "timeout"
    cancel = threading.Event()
    threading.Timer(0.4, cancel.set).start()
    with pytest.raises(AgentError) as e:
        adapter("gemini-api").complete("sys", "prompt", timeout=60, cancel=cancel)
    assert e.value.kind == "cancelled"


def test_an_unreachable_api_is_a_failure_with_its_reason(fake, monkeypatch):
    use_key(monkeypatch, OPENAI_API_KEY="sk-test")
    monkeypatch.setattr(agentbridge.OpenAiApi, "base", "http://127.0.0.1:1")
    with pytest.raises(AgentError) as e:
        adapter("openai-api").complete("sys", "prompt")
    assert e.value.kind == "failed" and "api.openai.com" in e.value.message and "sk-test" not in e.value.message


# ---- the small helpers ------------------------------------------------------------------------------------------------

def test_the_default_is_the_newest_cheap_stable_model():
    d = agentbridge._default_first
    assert d(["gpt-4o", "gpt-4o-mini", "gpt-4o-mini-preview"], "mini") == ["gpt-4o-mini", "gpt-4o", "gpt-4o-mini-preview"]
    assert d(["gpt-4o-mini-preview", "gpt-4o"], "mini") == ["gpt-4o-mini-preview", "gpt-4o"]
    assert d(["gpt-4o", "o1"], "mini") == ["gpt-4o", "o1"]
    assert d([], "mini") == []


def test_api_error_bodies_are_read_as_messages():
    assert agentbridge._api_message('{"error": {"message": "boom"}}') == "boom"
    assert agentbridge._api_message('[{"error": {"message": "first"}}]') == "first"
    assert agentbridge._api_message("plain text") == "plain text"
    assert agentbridge._redact("key sk-1 here", "sk-1") == "key [key] here"
