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
    entries = [_entry(i, tool_call="SECRET COMMAND", tool_sha="ABCDEF0123456789")
               for i in range(1, dashboard.RECENT_LIMIT + 30)]
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


def test_degraded_counts_calls_judged_without_the_model_and_leaves_mock_out():
    entries = [_entry(5, via="model"), _entry(6, via="regex"), _entry(7, via="allowlist"),
               _entry(8, via="offline"), _entry(9, via="offline"), _entry(10, via="heuristic"),
               _entry(11, via="heuristic", mock=True), _entry(12)]                        # old entries have no `via`
    d = dashboard.build_stats(entries, NOW, "24h")["degraded"]
    assert (d["offline"], d["model_error"], d["total"]) == (2, 1, 3)
    assert d["share"] == round(3 / 8, 4)


def test_degraded_last_hour_ignores_the_window_and_old_or_future_entries():
    entries = [_entry(10, via="offline"), _entry(30, via="heuristic"), _entry(90, via="offline"),
               _entry(-30, via="offline"), _entry(20)]
    stats = dashboard.build_stats(entries, NOW, "1h")
    assert stats["degraded"]["total"] == 3                                     # the future entry's minute is in the window too
    hour = dashboard.build_stats(entries, NOW, "7d")["degraded"]["last_hour"]
    assert (hour["offline"], hour["model_error"], hour["total"]) == (1, 1, 2)
    assert hour["latest"] == (NOW - timedelta(minutes=10)).isoformat(timespec="seconds").replace("+00:00", "Z")


def test_degraded_is_zero_when_everything_was_judged_normally():
    d = dashboard.build_stats([_entry(5, via="model"), _entry(6)], NOW, "24h")["degraded"]
    assert d["total"] == 0 and d["share"] == 0.0
    assert d["last_hour"] == {"offline": 0, "model_error": 0, "total": 0, "latest": None}
    empty = dashboard.build_stats([], NOW, "24h")["degraded"]
    assert empty["total"] == 0 and empty["last_hour"]["latest"] is None


def test_recent_rows_say_who_judged_them():
    rows = dashboard.build_stats([_entry(1, via="offline"), _entry(2, via="model"), _entry(3)], NOW)["recent"]
    assert [r["via"] for r in rows] == ["offline", "model", None]


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


# --- installing the hook from the dashboard -----------------------------------------------------

def test_origin_and_token_checks():
    ok = dashboard.origin_allowed
    assert ok("http://127.0.0.1:8787", "127.0.0.1:8787") and ok("http://localhost:8787", "localhost:8787")
    assert ok("http://[::1]:8787", "[::1]:8787")
    for origin, host in [(None, "127.0.0.1:8787"), ("", "127.0.0.1:8787"), ("null", "127.0.0.1:8787"),
                         ("http://evil.example", "127.0.0.1:8787"), ("http://127.0.0.1:9999", "127.0.0.1:8787"),
                         ("https://127.0.0.1:8787", "127.0.0.1:8787"), ("http://127.0.0.1:8787", "evil.example:8787"),
                         ("http://localhost:8787", "127.0.0.1:8787")]:
        assert not ok(origin, host), (origin, host)
    assert dashboard.token_ok("abc", "abc")
    assert not dashboard.token_ok("abd", "abc") and not dashboard.token_ok(None, "abc") and not dashboard.token_ok("", "")


def test_page_carries_the_token_placeholder():
    html = dashboard.render_page("N0NC3", "T0K3N")
    assert "{{TOKEN}}" not in html and 'name="laya-token" content="T0K3N"' in html


@pytest.fixture
def agents(client, tmp_path, monkeypatch):
    """The dashboard client with HOME in a temp dir and a stand-in laya-guard executable."""
    from laya_guard import install
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)          # CI runners set it; plugin paths must stay under HOME
    stub = tmp_path / "laya-guard"
    stub.write_text(f'#!/bin/sh\nPYTHONPATH="{Path(__file__).resolve().parent.parent / "src"}" '
                    f'exec "{sys.executable}" -m laya_guard.cli "$@"\n')
    stub.chmod(0o755)
    monkeypatch.setattr(install, "cli_path", lambda override=None: stub)
    monkeypatch.setattr(install, "_search_dirs", lambda h: [tmp_path / "no-bin"])     # don't see this machine's agents
    monkeypatch.setattr(install, "_app_dirs", lambda h: [])
    page = client.get("/dashboard")
    token = re.search(r'name="laya-token" content="([^"]+)"', page.text).group(1)
    settings = home / ".claude" / "settings.json"

    def post(action, body, agent="claude-code", **kw):
        headers = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:8787", "X-Laya-Token": token}
        headers.update(kw.get("headers", {}))
        headers = {k: v for k, v in headers.items() if v is not None}
        return client.post(f"/api/agents/{agent}/{action}", content=json.dumps(body), headers=headers)

    return type("Agents", (), {"client": client, "post": staticmethod(post), "settings": settings, "home": home, "token": token})


def test_agents_status_is_readable_and_reports_not_installed(agents):
    r = agents.client.get("/api/agents")
    assert r.status_code == 200
    a = r.json()["agents"][0]
    assert (a["id"], a["name"], a["scope"], a["state"]) == ("claude-code", "Claude Code", "user", "not-installed")
    assert a["path"].endswith(".claude/settings.json") and a["path"].startswith("~")
    assert agents.client.get("/api/agents", headers={"host": "evil.example:8787"}).status_code == 403


def test_agents_api_lists_every_supported_agent_and_whether_it_was_found(agents):
    (agents.home / ".codex").mkdir()
    (agents.home / ".cursor").mkdir()
    rows = agents.client.get("/api/agents").json()["agents"]
    assert [r["id"] for r in rows] == ["claude-code", "codex", "cursor", "gemini-cli", "qwen-code", "windsurf",
                                       "copilot-cli", "cline", "opencode", "kilo", "amp"]
    by_id = {r["id"]: r for r in rows}
    assert all(r["supported"] and r["state"] == "not-installed" for r in rows)
    assert [r["id"] for r in rows if r["found"]] == ["codex", "cursor"]            # only what exists in this HOME
    assert by_id["codex"]["how"] == "config" and by_id["codex"]["via"] == ["~/.codex"]
    assert by_id["cursor"]["path"] == "~/.cursor/hooks.json" and by_id["codex"]["path"] == "~/.codex/hooks.json"
    assert by_id["opencode"]["path"] == "~/.config/opencode/plugins/laya-guard.ts"
    assert by_id["codex"]["follow_up"] and by_id["cline"]["follow_up"] and by_id["amp"]["probes"] is False
    assert by_id["cursor"]["probes"] is True
    assert str(agents.home) not in json.dumps(rows)                              # only ~/ paths, never the full home
    assert agents.post("install", {"mode": "preview"}, agent="not-an-agent").status_code == 404


# what another tool leaves in a real ~/.cursor/hooks.json, trailing commas included
CURSOR_FILE = ('{\n  "hooks": {\n    "beforeShellExecution": [\n      {"command": "/bin/sh /x/cursor-hook.sh", "timeout": 10}\n    ],\n'
               '    "stop": [\n      {"command": "echo stop"},\n    ],\n  },\n  "version": 1\n}\n')


def test_cursor_hook_installs_and_uninstalls_through_the_dashboard(agents):
    path = agents.home / ".cursor" / "hooks.json"
    path.parent.mkdir()
    path.write_text(CURSOR_FILE, encoding="utf-8")
    preview = agents.post("install", {"mode": "preview"}, agent="cursor").json()
    assert preview["changed"] and preview["outcome"] == "added" and preview["path"] == "~/.cursor/hooks.json"
    assert any("trailing commas" in n for n in preview["notes"])               # the page tells the user the file is cleaned up
    assert "guard-cursor-hook.sh" in preview["diff"] and str(agents.home) not in preview["diff"].split("\n@@")[0]
    assert path.read_text() == CURSOR_FILE                                       # a preview writes nothing

    assert agents.post("install", {"mode": "apply"}, agent="cursor").status_code == 409                  # needs the shown diff
    done = agents.post("install", {"mode": "apply", "diff_id": preview["diff_id"]}, agent="cursor").json()
    assert done["applied"] and done["backup"] and all(p["ok"] for p in done["probes"]) and len(done["probes"]) == 2
    written = json.loads(path.read_text())
    commands = [h["command"] for h in written["hooks"]["beforeShellExecution"]]
    assert commands[0] == "/bin/sh /x/cursor-hook.sh" and "guard-cursor-hook.sh" in commands[1]      # the other tool's hook stays
    assert written["hooks"]["stop"] == [{"command": "echo stop"}] and written["version"] == 1
    row = [r for r in agents.client.get("/api/agents").json()["agents"] if r["id"] == "cursor"][0]
    assert row["state"] == "installed" and row["managed"] is True

    p = agents.post("uninstall", {"mode": "preview"}, agent="cursor").json()
    agents.post("uninstall", {"mode": "apply", "diff_id": p["diff_id"]}, agent="cursor")
    after = json.loads(path.read_text())
    assert [h["command"] for h in after["hooks"]["beforeShellExecution"]] == ["/bin/sh /x/cursor-hook.sh"]


def test_changing_settings_needs_every_check(agents):
    body = {"scope": "user", "mode": "preview"}
    assert agents.post("install", body).status_code == 200
    assert agents.post("install", body, headers={"X-Laya-Token": None}).status_code == 403          # no token
    assert agents.post("install", body, headers={"X-Laya-Token": "wrong"}).status_code == 403
    assert agents.post("install", body, headers={"Origin": "http://evil.example"}).status_code == 403
    assert agents.post("install", body, headers={"Origin": None}).status_code == 403                # no Origin
    assert agents.post("install", body, headers={"Content-Type": "text/plain"}).status_code == 415  # a form-style post
    assert agents.post("install", body, headers={"host": "evil.example:8787", "Origin": "http://evil.example:8787"}).status_code == 403
    assert not agents.settings.exists()


def test_preview_shows_the_diff_and_writes_nothing(agents):
    r = agents.post("install", {"mode": "preview"})
    body = r.json()
    assert body["changed"] and body["outcome"] == "added" and body["applied"] is False
    assert "guard-hook.sh" in body["diff"] and body["diff"].count("\n+") > 5 and body["diff_id"]
    header = body["diff"].split("\n@@")[0]
    assert "~/.claude/settings.json (after)" in header and str(agents.home) not in header   # the short path, not the full one
    assert not agents.settings.exists()


def test_applying_needs_the_diff_that_was_shown(agents):
    preview = agents.post("install", {"mode": "preview"}).json()
    assert agents.post("install", {"mode": "apply"}).status_code == 409                       # no diff id
    assert agents.post("install", {"mode": "apply", "diff_id": "0" * 16}).status_code == 409   # not the one shown
    assert not agents.settings.exists()
    done = agents.post("install", {"mode": "apply", "diff_id": preview["diff_id"]}).json()
    assert done["applied"] is True and done["backup"] is None
    assert [p["ok"] for p in done["probes"]] == [True, True]                                   # both test calls behave
    entry = json.loads(agents.settings.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
    assert "LAYA_GUARD_MANAGED=1" in entry["command"]
    assert agents.client.get("/api/agents").json()["agents"][0]["state"] == "installed"
    again = agents.post("install", {"mode": "preview"}).json()
    assert again["changed"] is False and again["diff_id"] is None


def test_a_change_made_after_the_preview_is_not_overwritten(agents):
    agents.settings.parent.mkdir()
    agents.settings.write_text('{"model": "a"}\n', encoding="utf-8")
    preview = agents.post("install", {"mode": "preview"}).json()
    agents.settings.write_text('{"model": "b"}\n', encoding="utf-8")                          # edited meanwhile
    r = agents.post("install", {"mode": "apply", "diff_id": preview["diff_id"]})
    assert r.status_code == 409 and "changed since the preview" in r.json()["detail"]
    assert agents.settings.read_text() == '{"model": "b"}\n'


def test_uninstall_through_the_dashboard_restores_the_file_and_backs_up(agents):
    agents.settings.parent.mkdir()
    agents.settings.write_text('{"model": "a"}\n', encoding="utf-8")
    p = agents.post("install", {"mode": "preview"}).json()
    done = agents.post("install", {"mode": "apply", "diff_id": p["diff_id"]}).json()
    assert done["backup"] and json.loads(agents.settings.read_text())["model"] == "a"
    p = agents.post("uninstall", {"mode": "preview"}).json()
    assert p["outcome"] == "removed"
    agents.post("uninstall", {"mode": "apply", "diff_id": p["diff_id"]})
    assert json.loads(agents.settings.read_text()) == {"model": "a"}
    assert len(list(agents.settings.parent.glob("*.bak"))) == 2


def test_a_hand_written_entry_needs_force_and_bad_requests_are_refused(agents):
    agents.settings.parent.mkdir()
    manual = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/x/guard-hook.sh"}]}]}}
    agents.settings.write_text(json.dumps(manual), encoding="utf-8")
    assert agents.post("install", {"mode": "preview"}).json()["changed"] is False              # left alone
    forced = agents.post("install", {"mode": "preview", "force": True}).json()
    assert forced["changed"] is True and forced["outcome"] == "updated"
    assert agents.post("install", {"scope": "project"}).status_code == 400                   # user scope only
    assert agents.post("install", {"mode": "dance"}).status_code == 400
    assert agents.post("frobnicate", {}).status_code == 404
    r = agents.client.post("/api/agents/not-an-agent/install", content="{}",
                           headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8787",
                                    "X-Laya-Token": agents.token})
    assert r.status_code == 404
    agents.settings.write_text("{nope", encoding="utf-8")
    assert agents.post("install", {"mode": "preview"}).status_code == 400                     # invalid JSON is refused
    assert agents.settings.read_text() == "{nope"


def test_page_drops_a_response_for_a_window_that_is_no_longer_selected():
    # a slow 24h poll must not overwrite the page after the user pressed 1 for the last hour
    html = dashboard.render_page("N0NC3")
    start = html.index("function load(fresh)")
    body = html[start:html.index("function setWindow", start)]
    assert "var win = state.window;" in body and 'encodeURIComponent(win)' in body
    assert body.index("if (win !== state.window) return;") < body.index("state.data = data;")


def test_page_has_the_degraded_indicator_and_the_start_at_login_card():
    html = dashboard.render_page("N0NC3", "T0K3N")
    for needle in ("Degraded", "Judged without the model", "Judged by", "Start at login", "/api/service/", "no model"):
        assert needle in html, needle
    assert html.count("<script") == 1 and html.count("<style") == 1 and "style=" not in html and "innerHTML" not in html


def test_overview_keeps_the_verdict_counts_apart_from_the_setup_cards():
    html = dashboard.render_page("N0NC3", "T0K3N")
    assert html.index('id="cards-verdicts"') < html.index('id="protection-label"') < html.index('id="cards-protection"')
    verdicts = html[html.index("function renderCards"):html.index("function renderFooter")]
    first, _, second = verdicts.partition("agentList.filter")
    assert all(t in first for t in ('title: "Blocked"', 'title: "Held for review"', 'title: "Allowed"', "Judged without the model"))
    assert "serviceCard(" in second and "Judged without the model" not in second and 'title: "Blocked"' not in second
