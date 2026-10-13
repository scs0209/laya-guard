"""Read-only dashboard for the local verdict log (standard library only).

Pure functions used by server.py: statistics over `verdicts.jsonl`, the loopback Host check
that defends against DNS rebinding, and the page itself. The log never contains command text
(only a hash and a length), so neither does the dashboard.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7),
           "30d": timedelta(days=30), "all": None}
ACTIONS = ("allow", "review", "block")
RECENT_LIMIT = 100
POLICY_LIMIT = 10
TAPE_LIMIT = 240                       # calls drawn on the verdict tape
_LETTER = {"allow": "a", "review": "r", "block": "b"}
MAX_BUCKETS = 90
# window -> bucket size
_BUCKET = {"1h": timedelta(minutes=5), "24h": timedelta(hours=1), "7d": timedelta(hours=6),
           "30d": timedelta(days=1), "all": timedelta(days=1)}


def host_allowed(host: str | None) -> bool:
    """True if the Host header names this machine's loopback. Rejects `evil.example` pointing
    at 127.0.0.1 (DNS rebinding): a page from another origin can't talk to the dashboard."""
    if not host:
        return False
    host = host.strip().lower()
    if host.startswith("["):                                   # [::1]:8787
        end = host.find("]")
        name = host[1:end] if end != -1 else ""
    elif host.count(":") == 1:                                 # localhost:8787
        name = host.rsplit(":", 1)[0]
    else:                                                      # 127.0.0.1 or bare ::1
        name = host
    return name in ALLOWED_HOSTS


def parse_ts(value) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    rank = max(math.ceil(pct / 100 * len(sorted_values)), 1)
    return sorted_values[min(rank, len(sorted_values)) - 1]


def _floor(ts: datetime, size: timedelta) -> datetime:
    seconds = int(size.total_seconds())
    return datetime.fromtimestamp(ts.timestamp() // seconds * seconds, tz=timezone.utc)


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_stats(entries: list[dict], now: datetime, window: str = "24h") -> dict:
    """Aggregate verdict entries for one window. Entries without a parsable timestamp only
    count in the `all` window."""
    if window not in WINDOWS:
        raise ValueError(f"unknown window: {window}")
    delta = WINDOWS[window]
    cutoff = now - delta if delta else None
    rows: list[tuple[datetime | None, dict]] = []
    for entry in entries:
        ts = parse_ts(entry.get("ts"))
        if cutoff is not None and (ts is None or ts < cutoff):
            continue
        rows.append((ts, entry))

    counts = {a: 0 for a in ACTIONS}
    latencies: list[float] = []
    policies: dict[str, dict[str, int]] = {}
    sources: dict[str, int] = {}
    mocks = 0
    for _, e in rows:
        action = e.get("action") if e.get("action") in counts else None
        if action:
            counts[action] += 1
        pol = policies.setdefault(str(e.get("policy_id", "?")), {"count": 0, **{a: 0 for a in ACTIONS}})
        pol["count"] += 1
        if action:
            pol[action] += 1
        sources[str(e.get("source", "?"))] = sources.get(str(e.get("source", "?")), 0) + 1
        mocks += 1 if e.get("mock") else 0
        try:
            latencies.append(float(e.get("latency_ms", 0.0)))
        except (TypeError, ValueError):
            pass
    total = len(rows)
    latencies.sort()

    def rate(n: int) -> float:
        return round(n / total, 4) if total else 0.0

    timed = [(ts, e) for ts, e in rows if ts is not None]
    size = _BUCKET[window]
    start = _floor(cutoff, size) if cutoff else (_floor(min(ts for ts, _ in timed), size) if timed else None)
    timeline: list[dict] = []
    if start is not None:
        buckets: dict[datetime, dict[str, int]] = {}
        t = start
        while t <= now:
            buckets[t] = {a: 0 for a in ACTIONS}
            t += size
        for ts, e in timed:
            slot = buckets.get(_floor(ts, size))
            if slot is not None and e.get("action") in slot:
                slot[e["action"]] += 1
        timeline = [{"t": _iso(t), **c} for t, c in sorted(buckets.items())][-MAX_BUCKETS:]

    recent_rows = sorted(timed, key=lambda r: r[0], reverse=True)[:RECENT_LIMIT]
    recent = [{
        "ts": _iso(ts), "action": e.get("action"), "risk": e.get("risk"),
        "policy_id": e.get("policy_id"), "reason": e.get("reason"), "source": e.get("source"),
        "latency_ms": e.get("latency_ms"), "tool_len": e.get("tool_len"),
        "tool_sha": str(e.get("tool_sha", ""))[:8],
    } for ts, e in recent_rows]

    # the verdict tape: the latest calls in order, oldest first, as [letter, time]
    tape = [[_LETTER.get(e.get("action"), "?"), _iso(ts)]
            for ts, e in sorted(timed, key=lambda r: r[0])[-TAPE_LIMIT:]]

    top = sorted(policies.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    return {
        "window": window,
        "generated_at": _iso(now),
        "totals": {
            "total": total, **counts,
            "block_rate": rate(counts["block"]), "review_rate": rate(counts["review"]),
            "allow_rate": rate(counts["allow"]),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            "p95_latency_ms": round(_percentile(latencies, 95), 1),
            "mock_share": rate(mocks),
        },
        "timeline": timeline,
        "tape": tape,
        "policies": [{"policy_id": k, **v} for k, v in top[:POLICY_LIMIT]],
        "sources": [{"source": k, "count": v} for k, v in sorted(sources.items(), key=lambda kv: -kv[1])],
        "recent": recent,
    }


def display_path(path: Path) -> str:
    """The log path with the home directory shortened, for the page."""
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home + os.sep) else s


def page_headers(nonce: str) -> dict[str, str]:
    """Headers for the HTML page: a nonce-only CSP (no external resource can load) and no framing."""
    return {
        "Content-Security-Policy": (
            f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'"),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }


def api_headers() -> dict[str, str]:
    return {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def render_page(nonce: str) -> str:
    html = resources.files("laya_guard").joinpath("dashboard.html").read_text(encoding="utf-8")
    return html.replace("{{NONCE}}", nonce)
