"""The web trial's judge, without the web framework: what a pasted command gets, and how often a visitor may ask.

Only the judge is used. `laya-guard serve` also has the dashboard and the endpoints that change an agent's settings,
which must never face the internet. Nothing is logged or stored: judge() never writes the verdict log (only
`laya-guard serve` does), and the command lives only for the request.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque

from laya_guard import fileaccess
from laya_guard.guard import ASK_AGENTS, judge_traced

MAX_CHARS = 4000                        # one model window; longer input is a different, slower path
MAX_BODY = 32_768                       # bytes of request body read at all (4,000 characters fit with room for JSON)
PER_MINUTE = 20                         # requests per visitor per minute
ALL_PER_MINUTE = 300                    # requests per minute from everyone: X-Forwarded-For can be forged, this can't
MAX_CLIENTS = 10_000
# The hook's own settings for each agent: the source it sends; ASK_AGENTS says whether it can ask
AGENTS = {"claude-code": "user", "cursor": "tool", "codex": "tool", "gemini-cli": "tool"}
STEP_KEYS = ("stage", "index", "total", "text", "result", "policy_id", "status", "why", "whole")

_lock = threading.Lock()                # one judgment at a time: the model is not shared across threads


class Refused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class RateLimit:
    """A per-visitor limit and an overall one. Visitors are kept least recently seen first, so the ones idle for a
    minute are dropped from the front as requests come in, and there are never more than MAX_CLIENTS."""

    def __init__(self, per_minute: int = PER_MINUTE, all_per_minute: int = ALL_PER_MINUTE,
                 max_clients: int = MAX_CLIENTS):
        self.per_minute, self.all_per_minute, self.max_clients = per_minute, all_per_minute, max_clients
        self.hits: OrderedDict[str, deque] = OrderedDict()
        self.all: deque = deque()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        while self.hits:                                      # forget visitors idle for a minute
            oldest = next(iter(self.hits.values()))
            if oldest and now - oldest[-1] <= 60:
                break
            self.hits.popitem(last=False)
        while self.all and now - self.all[0] > 60:
            self.all.popleft()
        q = self.hits.pop(client, None) or deque()
        while q and now - q[0] > 60:
            q.popleft()
        self.hits[client] = q                                 # most recently seen last
        if len(q) >= self.per_minute or len(self.all) >= self.all_per_minute:
            return False
        q.append(now)
        self.all.append(now)
        while len(self.hits) > self.max_clients:
            self.hits.popitem(last=False)
        return True


def client_of(headers, peer: str | None) -> str:
    # Spaces sit behind a proxy; the first X-Forwarded-For hop is the visitor. A client can forge it, which only
    # spreads its requests over several per-visitor limits; the overall limit still holds.
    return (headers.get("x-forwarded-for", "").split(",")[0].strip()) or peer or "?"


def judge_command(command: str, agent: str = "claude-code") -> dict:
    """What laya-guard's hook for `agent` answers for `command`, with each step of how it decided."""
    if not isinstance(command, str) or not command.strip():
        raise Refused(422, "enter a command")
    if len(command) > MAX_CHARS:
        raise Refused(422, f"at most {MAX_CHARS} characters")
    if not isinstance(agent, str) or agent not in AGENTS:
        raise Refused(400, "unknown agent")
    steps: list[dict] = []
    t0 = time.perf_counter()
    with _lock:
        v = judge_traced(command, source=AGENTS[agent], emit=steps.append, ask=agent in ASK_AGENTS)
        v = fileaccess.stricter(v, fileaccess.judge_shell(command, None))
    return {
        "action": v.action, "risk": round(v.risk, 3), "policy_id": v.policy_id, "reason": v.reason,
        "verdict_source": v.verdict_source, "model": not v.mock, "ms": round((time.perf_counter() - t0) * 1000),
        "steps": [{k: s[k] for k in s if k in STEP_KEYS} for s in steps],
    }
