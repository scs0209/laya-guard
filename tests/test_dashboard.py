"""Dashboard: pure logic (stats, Host check, CSP, page) and the FastAPI routes.

The route tests need fastapi + httpx and are skipped without them; the logic tests only need the
standard library.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("LAYA_GUARD_MOCK", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from laya_guard import cli, dashboard  # noqa: E402

NOW = datetime(2026, 10, 13, 12, 0, 30, tzinfo=timezone.utc)


def _entry(minutes_ago: float, action="allow", policy="general", risk=0.1, latency=100.0, **extra):
    ts = NOW - timedelta(minutes=minutes_ago)
    return dict({"ts": ts.isoformat(timespec="seconds"), "action": action, "risk": risk, "policy_id": policy,
                 "reason": "why", "mock": False, "source": "user", "latency_ms": latency, "tool_len": 12,
                 "tool_sha": "0123456789abcdef"}, **extra)


# --- Host check -------------------------------------------------------------------------------

def test_host_allowed_accepts_only_loopback_names():
    for ok in ["127.0.0.1", "127.0.0.1:8787", "localhost", "LOCALHOST:8787", "[::1]:8787", "::1", " localhost "]:
        assert dashboard.host_allowed(ok), ok
    for bad in [None, "", "evil.example", "evil.example:8787", "localhost.evil.example", "127.0.0.1.evil.example",
                "0.0.0.0", "192.168.1.5:8787", "[::2]:8787", "localhost@evil.example"]:
        assert not dashboard.host_allowed(bad), bad


# --- statistics ---------------------------------------------------------------------------------

def test_build_stats_counts_rates_and_latency():
    entries = [_entry(1, "allow", latency=100), _entry(2, "allow", latency=200), _entry(3, "review", "mass-delete", 0.6, 300),
               _entry(4, "block", "pipe-to-shell", 0.92, 1000)]
    s = dashboard.build_stats(entries, NOW, "24h")
    t = s["totals"]
    assert (t["total"], t["allow"], t["review"], t["block"]) == (4, 2, 1, 1)
    assert (t["allow_rate"], t["review_rate"], t["block_rate"]) == (0.5, 0.25, 0.25)
    assert t["avg_latency_ms"] == 400.0 and t["p95_latency_ms"] == 1000.0
    assert [p["policy_id"] for p in s["policies"]] == ["general", "mass-delete", "pipe-to-shell"]
    assert s["policies"][0] == {"policy_id": "general", "count": 2, "allow": 2, "review": 0, "block": 0}


def test_window_filters_old_and_untimed_entries():
    entries = [_entry(30), _entry(90), _entry(60 * 26), {"action": "allow", "policy_id": "x"}]
    assert dashboard.build_stats(entries, NOW, "1h")["totals"]["total"] == 1
    assert dashboard.build_stats(entries, NOW, "24h")["totals"]["total"] == 2
    assert dashboard.build_stats(entries, NOW, "7d")["totals"]["total"] == 3
    allw = dashboard.build_stats(entries, NOW, "all")
    assert allw["totals"]["total"] == 4                  # the entry without a timestamp counts only here
    assert len(allw["recent"]) == 3                      # ...but can't be placed in time


def test_timeline_buckets_are_contiguous_and_add_up():
    entries = [_entry(5, "allow"), _entry(6, "block"), _entry(65, "review")]
    s = dashboard.build_stats(entries, NOW, "24h")
    tl = s["timeline"]
    assert len(tl) == 25                                  # hourly buckets from the window start through now
    times = [datetime.strptime(b["t"], "%Y-%m-%dT%H:%M:%SZ") for b in tl]
    assert all(b - a == timedelta(hours=1) for a, b in zip(times, times[1:]))
    assert sum(b["allow"] + b["review"] + b["block"] for b in tl) == 3
    # NOW is 12:00:30: entries 5 and 6 minutes ago fall in the 11:00 bucket, 65 minutes ago in 10:00
    assert tl[-2]["allow"] == 1 and tl[-2]["block"] == 1 and tl[-3]["review"] == 1
    assert tl[-1] == {"t": "2026-10-13T12:00:00Z", "allow": 0, "review": 0, "block": 0}
    assert len(dashboard.build_stats(entries, NOW, "1h")["timeline"]) == 13        # 5-minute buckets
    assert dashboard.build_stats([], NOW, "all")["timeline"] == []


def test_tape_is_the_latest_calls_oldest_first():
    entries = [_entry(i, "block" if i == 4 else "allow") for i in range(1, 6)]     # i minutes ago
    s = dashboard.build_stats(entries, NOW, "24h")
    assert "".join(c[0] for c in s["tape"]) == "abaaa"                          # oldest on the left
    stamps = [c[1] for c in s["tape"]]
    assert stamps == sorted(stamps)
    many = [_entry(i / 10) for i in range(1, dashboard.TAPE_LIMIT + 61)]
    tape = dashboard.build_stats(many, NOW, "24h")["tape"]
    assert len(tape) == dashboard.TAPE_LIMIT and tape[-1][1] >= tape[0][1]        # capped, newest kept
    odd = dashboard.build_stats([_entry(1, "weird")], NOW, "24h")["tape"]
    assert odd[0][0] == "?"
    assert dashboard.build_stats([], NOW, "24h")["tape"] == []


def test_recent_is_newest_first_capped_and_has_no_command_text():
    entries = [_entry(i, tool_call="SECRET COMMAND", tool_sha="ABCDEF0123456789") for i in range(1, 80)]
    s = dashboard.build_stats(entries, NOW, "24h")
    assert len(s["recent"]) == dashboard.RECENT_LIMIT
    stamps = [r["ts"] for r in s["recent"]]
    assert stamps == sorted(stamps, reverse=True)
    assert s["recent"][0]["tool_sha"] == "ABCDEF01"                  # shortened hash
    assert "SECRET COMMAND" not in json.dumps(s)                    # nothing but the fields we pick


def test_unknown_window_and_odd_entries_are_handled():
    with pytest.raises(ValueError):
        dashboard.build_stats([], NOW, "1y")
    odd = [{"ts": "not a date", "action": "allow"}, {"ts": _entry(1)["ts"], "action": "weird", "latency_ms": "x"}]
    s = dashboard.build_stats(odd, NOW, "all")
    # the untimed entry still counts in `all`; the unknown action is counted as a call but not as allow/review/block
    assert s["totals"]["total"] == 2 and s["totals"]["allow"] == 1
    assert s["totals"]["review"] == 0 and s["totals"]["block"] == 0


# --- page and headers ---------------------------------------------------------------------------

def test_csp_allows_only_the_nonce_and_nothing_external():
    h = dashboard.page_headers("N0NC3")
    csp = h["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "'nonce-N0NC3'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert h["Cache-Control"] == "no-store" and h["Referrer-Policy"] == "no-referrer"


def test_page_uses_the_nonce_and_is_self_contained():
    html = dashboard.render_page("N0NC3")
    assert "{{NONCE}}" not in html
    assert html.count('nonce="N0NC3"') == 2                         # one <style>, one <script>
    assert not re.search(r'(?:src|href)=["\']https?://', html)      # no CDN, no external font
    assert " style=" not in html                                     # inline style attributes would break the CSP
    assert "innerHTML" not in html                                   # log fields are written with textContent


# --- CLI ----------------------------------------------------------------------------------------

def test_dashboard_command_opens_the_page_when_the_server_is_up(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli, "_server_up", lambda host, port: True)
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    assert cli.main(["dashboard", "--port", "9001"]) == 0
    assert opened == ["http://127.0.0.1:9001/dashboard"]
    assert "http://127.0.0.1:9001/dashboard" in capsys.readouterr().out
    opened.clear()
    assert cli.main(["dashboard", "--no-open"]) == 0 and opened == []


def test_dashboard_command_starts_the_server_when_it_is_down(monkeypatch, capsys):
    started = []
    monkeypatch.setattr(cli, "_server_up", lambda host, port: False)
    monkeypatch.setattr(cli, "cmd_serve", lambda args: started.append((args.host, args.port)) or 0)
    assert cli.main(["dashboard", "--no-open", "--no-warmup"]) == 0
    assert started == [("127.0.0.1", 8787)]
    assert "not running; starting it" in capsys.readouterr().out


# --- routes -------------------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    log = tmp_path / "verdicts.jsonl"
    stamp = datetime.now(timezone.utc)
    rows = [{"ts": (stamp - timedelta(minutes=m)).isoformat(timespec="seconds"), "action": a, "risk": 0.5,
             "policy_id": p, "reason": "r", "mock": False, "source": "user", "latency_ms": 50.0, "tool_len": 3,
             "tool_sha": "deadbeefdeadbeef"} for m, a, p in [(1, "allow", "general"), (2, "block", "pipe-to-shell")]]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setenv("LAYA_GUARD_LOG_PATH", str(log))
    monkeypatch.delenv("LAYA_GUARD_LOG", raising=False)
    from laya_guard.server import app
    return TestClient(app, base_url="http://127.0.0.1:8787")


def test_dashboard_routes_serve_localhost_only(client):
    page = client.get("/dashboard")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    nonce = re.search(r"'nonce-([^']+)'", page.headers["content-security-policy"]).group(1)
    assert f'nonce="{nonce}"' in page.text
    other = client.get("/dashboard")
    assert other.headers["content-security-policy"] != page.headers["content-security-policy"]   # fresh nonce per page
    for path in ("/dashboard", "/api/stats"):
        evil = client.get(path, headers={"host": "evil.example:8787"})       # DNS rebinding
        assert evil.status_code == 403, path


def test_api_stats_returns_the_log_and_health(client):
    r = client.get("/api/stats", params={"window": "all"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["totals"]["total"] == 2 and body["totals"]["block"] == 1
    assert body["health"]["ok"] is True and body["health"]["logging"] is True
    assert {"mock", "version", "log_path"} <= set(body["health"])
    assert client.get("/api/stats", params={"window": "1y"}).status_code == 400


def test_api_stats_reports_when_logging_is_off(client, monkeypatch):
    monkeypatch.setenv("LAYA_GUARD_LOG", "0")
    assert client.get("/api/stats").json()["health"]["logging"] is False
