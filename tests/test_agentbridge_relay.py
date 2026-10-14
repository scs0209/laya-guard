"""The manual relay: a decision waits until someone on this machine answers it through the API.

No child process, no model, no key: `complete()` parks the decision on the board, and whoever holds the dashboard
token (a person, or another program) posts the reply. The engine validates it like any other answer.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from laya_guard import agentbridge  # noqa: E402
from laya_guard.agentbridge import AgentError  # noqa: E402

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


@pytest.fixture
def board(monkeypatch):
    fresh = agentbridge.RelayBoard()
    monkeypatch.setattr(agentbridge, "BOARD", fresh)
    return fresh


def relay():
    return agentbridge.get("manual-relay")


def test_it_is_always_there_and_sends_nothing_anywhere():
    info = relay().detect()
    assert (info.found, info.logged_in, info.kind) == (True, True, "manual")
    assert "no agent" in info.inert.lower() and "sends nothing" in info.sends


def test_an_answer_posted_from_another_thread_ends_the_wait(board):
    done = []
    worker = threading.Thread(target=lambda: done.append(relay().complete("sys", "prompt", timeout=30)),
                              daemon=True)
    worker.start()
    time.sleep(0.5)
    (item,) = board.list()
    assert item["system"] == "sys" and "prompt" in item["prompt"]
    board.answer(item["decision_id"], '{"type": "finish", "outcome": "done"}')
    worker.join(5)
    assert not worker.is_alive() and '"finish"' in done[0].text and done[0].duration_ms is not None
    assert board.list() == []                                       # answered decisions leave the board


def test_nobody_answering_is_a_timeout_not_a_hang(board):
    t0 = time.monotonic()
    with pytest.raises(AgentError) as e:
        relay().complete("sys", "prompt", timeout=0.6)
    assert e.value.kind == "timeout" and time.monotonic() - t0 < 5 and board.list() == []


def test_a_stop_lands_at_once(board):
    cancel = threading.Event()
    threading.Timer(0.4, cancel.set).start()
    with pytest.raises(AgentError) as e:
        relay().complete("sys", "prompt", cancel=cancel, timeout=60)
    assert e.value.kind == "cancelled" and board.list() == []


def test_empty_and_huge_answers_are_refused_before_they_park(board):
    worker = threading.Thread(target=lambda: relay().complete("sys", "prompt", timeout=30), daemon=True)
    worker.start()
    time.sleep(0.5)
    (item,) = board.list()
    with pytest.raises(ValueError):
        board.answer(item["decision_id"], "   ")
    with pytest.raises(ValueError):
        board.answer(item["decision_id"], "x" * (agentbridge.RELAY_MAX_TEXT + 1))
    assert len(board.list()) == 1                                   # still waiting: nothing was taken
    with pytest.raises(KeyError):
        board.answer("no-such-decision", "hi")
    board.answer(item["decision_id"], "hi")
    worker.join(5)


def test_two_runs_never_get_each_others_answers(board):
    got = {}
    threading.Thread(target=lambda: got.setdefault("a", relay().complete("sys", "A", timeout=30)), daemon=True).start()
    threading.Thread(target=lambda: got.setdefault("b", relay().complete("sys", "B", timeout=30)), daemon=True).start()
    time.sleep(0.5)
    by_prompt = {i["prompt"]: i["decision_id"] for i in board.list()}
    assert set(by_prompt) == {"A", "B"}
    board.answer(by_prompt["B"], "b-answer")
    board.answer(by_prompt["A"], "a-answer")
    time.sleep(1.0)
    assert (got["a"].text, got["b"].text) == ("a-answer", "b-answer")


def test_the_endpoints_list_and_answer(board, tmp_path, monkeypatch):
    import json
    import re
    from fastapi.testclient import TestClient
    from laya_guard import guard
    from laya_guard.server import app
    monkeypatch.setattr(guard, "_MOCK", True)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    token = re.search(r'name="laya-token" content="([^"]+)"', client.get("/dashboard").text).group(1)

    def post(path, body):
        return client.post(path, content=json.dumps(body),
                           headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8787",
                                    "X-Laya-Token": token})

    worker = threading.Thread(target=lambda: relay().complete("sys", "prompt", timeout=30), daemon=True)
    worker.start()
    time.sleep(0.5)
    pending = client.get("/api/sim/ai/relay/pending").json()["pending"]
    assert len(pending) == 1 and pending[0]["prompt"] == "prompt" and "answer" not in pending[0]
    decision = pending[0]["decision_id"]
    assert post("/api/sim/ai/relay/answer", {"decision_id": decision, "text": "  "}).status_code == 400
    assert post("/api/sim/ai/relay/answer", {"decision_id": "nobody", "text": "hi"}).status_code == 404
    assert post("/api/sim/ai/relay/answer", {"decision_id": decision, "text": "hi"}).status_code == 200
    worker.join(5)
    assert client.get("/api/sim/ai/relay/pending").json() == {"pending": []}
