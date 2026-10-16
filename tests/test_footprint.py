"""The footprint report and the protection suggestions, built from file activity events.

Events are written here in the shape the log holds. HOME is a made-up directory so `~` rules and the report agree on it;
the config directory is in a temporary place. No model, no network.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from laya_guard import activity, cli, fileaccess, footprint, server  # noqa: E402
from laya_guard.guard import Verdict  # noqa: E402

HOME = "/Users/tester"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    log = tmp_path / "data" / "file-activity.jsonl"
    for k, v in {"HOME": HOME, "XDG_CONFIG_HOME": str(tmp_path / "config"), "LAYA_GUARD_MOCK": "1",
                 "LAYA_GUARD_FILE_ACTIVITY_PATH": str(log), "LAYA_GUARD_LOG_PATH": str(tmp_path / "verdicts.jsonl")}.items():
        monkeypatch.setenv(k, v)
    for k in ("XDG_DATA_HOME", "LAYA_GUARD_PROTECT", "LAYA_GUARD_FILE_ACTIVITY"):
        monkeypatch.delenv(k, raising=False)
    footprint._git_root.cache_clear()
    return log


def ev(minutes_ago, op="read", path="/work/a/src/x.py", *, agent="claude-code", cwd="/work/a", action="allow", source="hook",
       policy_id="general", would=None, hours_ago=0):
    ts = NOW - timedelta(minutes=minutes_ago, hours=hours_ago)
    e = {"ts": ts.isoformat(timespec="seconds"), "agent": agent, "tool_name": "Read", "op": op, "path": path, "abs": path,
         "cwd": cwd, "source": source, "action": action, "policy_id": policy_id}
    if would is not None or source == "os-monitor":
        e["would"] = would
    return e


def report(events, **kw):
    return footprint.build_report(events, now=NOW, home=HOME, **kw)


# --- the report -------------------------------------------------------------------------------------------------------

def test_events_are_grouped_by_project_and_split_into_sessions_where_nothing_happened_for_half_an_hour():
    events = [ev(300), ev(295, "write"), ev(290, "delete"),                       # an early run
              ev(100), ev(95, "write"),                                           # a later one, 3 hours on
              ev(20, agent="codex", path="/work/b/f.py", cwd="/work/b")]
    r = report(events)
    assert [p["project"] for p in r["projects"]] == ["/work/b", "/work/a"]        # most recent first
    a = r["projects"][1]
    assert (a["events"], a["read"], a["write"], a["delete"]) == (5, 2, 2, 1)
    assert [(s["agent"], s["events"]) for s in a["sessions"]] == [("claude-code", 3), ("claude-code", 2)]
    assert r["totals"] == {"events": 6, "sessions": 3, "stopped": 0, "flagged": 0, "outside": 0}


def test_two_agents_in_one_project_are_two_sessions_even_when_they_overlap():
    r = report([ev(10), ev(9, agent="codex"), ev(8), ev(7, agent="codex")])
    assert sorted((s["agent"], s["events"]) for s in r["projects"][0]["sessions"]) == [("claude-code", 2), ("codex", 2)]
    assert r["projects"][0]["agents"] == ["claude-code", "codex"]


def test_a_blocked_call_is_a_stopped_attempt_not_something_touched():
    r = report([ev(5), ev(4, "write", path=HOME + "/.aws/credentials", action="block", policy_id="protected-path")])
    p = r["projects"][0]
    assert (p["events"], p["read"], p["write"], p["stopped"], p["flagged"]) == (2, 1, 0, 1, 1)
    assert p["protected_hits"][0]["stopped"] is True and p["protected_hits"][0]["answer"] == "block"
    assert HOME not in json.dumps(r) and "~/.aws/credentials" in json.dumps(r)             # paths come out with ~


def test_protected_hits_come_from_hook_and_os_events_alike():
    r = report([ev(5, path=HOME + "/.ssh/id_rsa", action="review", policy_id="protected-path"),
                ev(4, path=HOME + "/.ssh/id_ed25519", source="os-monitor", action="observed", policy_id=None,
                   would="review"),                                                     # the OS line says it in `would` alone
                ev(3, path="/work/a/ok.py", source="os-monitor", action="observed", policy_id=None, would=None)])
    p = r["projects"][0]
    assert p["flagged"] == 2 and [h["source"] for h in p["protected_hits"]] == ["hook", "os-monitor"]
    assert p["sessions"][0]["sources"] == {"hook": 1, "os-monitor": 2}


def test_what_was_outside_the_project_and_what_was_touched_most():
    events = [ev(10, path="/work/a/src/x.py")] * 3 + [ev(9, path=HOME + "/Documents/notes.txt"), ev(8, path="/etc/hosts")]
    p = report(events)["projects"][0]
    assert p["outside"] == 2
    assert [o["path"] for o in p["outside_paths"]] == ["~/Documents/notes.txt", "/etc/hosts"]       # equal counts: by real path
    assert p["top_paths"][0] == {"path": "/work/a/src/x.py", "count": 3}


def test_the_window_the_agent_and_the_project_filter_what_is_counted():
    events = [ev(10), ev(5, agent="codex"), ev(1, hours_ago=30), ev(10, path="/work/b/z.py", cwd="/work/b"),
              ev(10, path="/work/a/sub/q.py", cwd="/work/a/sub")]
    assert report(events, window="24h")["totals"]["events"] == 4                         # the 30-hour-old one is out
    assert report(events, window="all")["totals"]["events"] == 5
    assert report(events, agent="codex")["totals"]["events"] == 1
    assert [p["project"] for p in report(events, project="/work/a")["projects"]] == ["/work/a", "/work/a/sub"]
    assert [p["project"] for p in report(events, project="/work/b")["projects"]] == ["/work/b"]
    with pytest.raises(ValueError):
        report(events, window="5d")


def test_a_project_is_the_git_repository_the_directory_is_in(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "pkg" / "deep").mkdir(parents=True)
    r = report([ev(3, cwd=str(repo / "pkg")), ev(2, cwd=str(repo / "pkg" / "deep")), ev(1, cwd=str(repo))])
    assert [p["project"] for p in r["projects"]] == [str(repo)] and r["projects"][0]["events"] == 3


def test_a_dotfiles_repository_in_home_does_not_make_the_whole_home_one_project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".git").mkdir(parents=True)
    (home / "work" / "a").mkdir(parents=True)
    r = footprint.build_report([ev(1, cwd=str(home / "work" / "a"))], now=NOW, home=str(home))
    assert r["projects"][0]["project"] == "~/work/a"


def test_events_with_no_directory_go_under_unknown_and_never_count_as_outside():
    old = ev(5, path="/x/y.py")
    del old["cwd"]                                       # recorded before the log kept the directory
    p = report([old, ev(4, path="/x/z.py", cwd=None)])["projects"][0]
    assert p["project"] == "(unknown)" and p["events"] == 2 and p["outside"] == 0


def test_lines_without_a_readable_time_are_counted_and_left_out():
    bad = ev(5)
    bad["ts"] = "yesterday"
    r = report([bad, ev(4), {"op": "read"}])
    assert r["skipped_events"] == 2 and r["totals"]["events"] == 1


def test_an_empty_window_says_so():
    assert "Nothing recorded" in footprint.render_text(report([]))


def test_the_text_report_names_projects_sessions_and_the_way_to_the_suggestions():
    events = [ev(20, path=HOME + "/.config/gcloud/creds.db"), ev(10, path=HOME + "/.ssh/id_rsa", action="review",
                                                                  policy_id="protected-path")]
    text = footprint.render_text(report(events), log_label="log: x", total_in_log=2)
    assert "File footprint, last 7d (log: x, 2 events)" in text and "/work/a" in text and "protected paths touched:" in text
    assert "~/.ssh/id_rsa" in text and "footprint --suggest" in text and "claude-code" in text


# --- suggestions ------------------------------------------------------------------------------------------------------

def sug(events, **kw):
    return footprint.suggest(events, home=HOME, **kw)


def test_a_known_secret_location_an_agent_touched_is_suggested_with_its_evidence():
    found = sug([ev(5, path=HOME + "/.config/gcloud/credentials.db"), ev(4, "write", path=HOME + "/.config/gcloud/x.json",
                                                                          agent="codex")])
    assert [(s["path"], s["kind"], s["action"], s["ops"]) for s in found] == [("~/.config/gcloud/**", "sensitive", "review", ["read", "write"])]
    s = found[0]
    assert (s["count"], s["agents"], s["reason"]) == (2, ["claude-code", "codex"], "Google Cloud credentials")
    assert s["example"].startswith("~/.config/gcloud/")


def test_what_a_rule_already_covers_is_not_suggested():
    covered = [ev(5, path=HOME + "/.ssh/id_rsa"), ev(4, path="/work/a/.env"), ev(3, path=HOME + "/.aws/credentials")]
    assert sug(covered) == []                                                           # all in the default rules
    user_rule = [fileaccess.Rule("~/.config/gcloud/**", frozenset({"read", "write"}), "review", "mine")]
    assert sug([ev(5, path=HOME + "/.config/gcloud/credentials.db")], rules=user_rule) == []


def test_a_secret_pattern_inside_a_project_is_still_suggested():
    found = sug([ev(5, path="/work/a/certs/server.pem"), ev(4, path="/work/a/deploy/terraform.tfstate")])
    assert sorted(s["path"] for s in found) == ["**/*.pem", "**/*.tfstate"]


def test_a_folder_outside_the_project_touched_rarely_is_suggested():
    found = sug([ev(5, path=HOME + "/Documents/taxes/2025.pdf"), ev(4, path=HOME + "/Documents/taxes/notes.txt")])
    assert len(found) == 1 and found[0]["kind"] == "rare" and found[0]["path"] == "~/Documents/taxes/**"
    assert found[0]["count"] == 2 and "not evidence" in found[0]["reason"]


def test_a_folder_an_agent_uses_all_the_time_is_not_suggested():
    often = [ev(i, path=HOME + "/Documents/notes/a.txt") for i in range(1, 8)]          # 7 touches: it is part of the workflow
    assert sug(often) == []
    sessions = [ev(5, path=HOME + "/other/a.txt", agent=a) for a in ("claude-code", "codex", "gemini-cli")]
    assert sug(sessions) == []                                                          # three agents: not "rare"


def test_hidden_folders_the_library_and_system_paths_are_not_rare_folders():
    quiet = [ev(5, path=HOME + "/.nvm/versions/x.js"), ev(4, path=HOME + "/Library/Caches/y"), ev(3, path="/usr/lib/z.dylib"),
             ev(2, path="/tmp/scratch")]
    assert sug(quiet) == []


def test_flagged_and_stopped_events_are_not_suggested_again():
    assert sug([ev(5, path=HOME + "/.config/gcloud/c.db", policy_id="protected-path", action="review"),
                ev(4, path=HOME + "/.config/gh/hosts.yml", action="block")]) == []


def test_suggestions_put_known_secrets_first_and_respect_the_limit():
    events = [ev(5, path=HOME + f"/Docs{i}/a.txt") for i in range(12)] + [ev(4, path=HOME + "/.azure/token")]
    found = sug(events, limit=5)
    assert len(found) == 5 and found[0]["kind"] == "sensitive" and all(s["kind"] == "rare" for s in found[1:])
    assert len(sug(events, limit=None)) == 13


def test_the_suggested_yaml_is_a_valid_protect_file_and_says_nothing_is_applied(tmp_path):
    found = sug([ev(5, path=HOME + "/.config/gcloud/c.db"), ev(4, path=HOME + "/Documents/taxes/a.pdf"),
                 ev(3, path="/work/a/k.pem")])
    text = footprint.render_rules_yaml(found, home=HOME)
    assert "Nothing is applied" in text and "not evidence of bad intent" in text
    rules = fileaccess._parse(text, "suggested")
    assert [r.glob for r in rules] == [s["path"] for s in found] and all(r.action == "review" for r in rules)
    assert all(r.ops == frozenset({"read", "write"}) for r in rules)


def test_a_log_of_a_hundred_thousand_events_is_summarised_quickly():
    import time
    events = [ev(i % 5000, path=f"/work/a/src/f{i % 3000}.py") for i in range(100_000)]
    t = time.perf_counter()
    r = report(events, window="all")
    assert r["totals"]["events"] == 100_000 and time.perf_counter() - t < 20


# --- through the CLI and the log -------------------------------------------------------------------------------------------

def write_log(log, events):
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(e) + "\n" for e in events))


def fresh(events):
    """Shift events so they are recent in real time: the CLI uses the clock."""
    shift = datetime.now(timezone.utc) - NOW
    out = []
    for e in events:
        e = dict(e)
        e["ts"] = (datetime.fromisoformat(e["ts"]) + shift).isoformat(timespec="seconds")
        out.append(e)
    return out


def test_footprint_with_no_log_says_how_to_turn_it_on(env, capsys):
    assert cli.main(["footprint"]) == 0
    assert "laya-guard activity on" in capsys.readouterr().out


def test_footprint_shows_the_report_and_json(env, capsys):
    write_log(env, fresh([ev(30), ev(20, "write"), ev(10, path=HOME + "/.config/gcloud/c.db")]))
    assert cli.main(["footprint"]) == 0
    out = capsys.readouterr().out
    assert "File footprint, last 7d" in out and "/work/a" in out and "3 events" in out
    assert cli.main(["footprint", "--json", "--since", "30d", "--agent", "claude-code"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["log_events"] == 3 and data["window"] == "30d" and data["projects"][0]["events"] == 3
    assert data["suggestions"] == 1


def test_footprint_filters_by_project_and_rejects_a_window_it_does_not_know(env, capsys):
    write_log(env, fresh([ev(10), ev(9, path="/work/b/q.py", cwd="/work/b")]))
    assert cli.main(["footprint", "--project", "/work/b", "--json"]) == 0
    assert [p["project"] for p in json.loads(capsys.readouterr().out)["projects"]] == ["/work/b"]
    with pytest.raises(SystemExit):
        cli.main(["footprint", "--since", "5d"])


def test_footprint_suggest_prints_yaml_to_stdout_and_applies_nothing(env, capsys, tmp_path):
    write_log(env, fresh([ev(10, path=HOME + "/.config/gcloud/c.db")]))
    assert cli.main(["footprint", "--suggest"]) == 0
    cap = capsys.readouterr()
    assert fileaccess._parse(cap.out, "stdout")[0].glob == "~/.config/gcloud/**"
    assert "protect.yaml" in cap.err
    assert not (tmp_path / "config" / "laya-guard" / "protect.yaml").exists()           # it never writes rules
    assert cli.main(["footprint", "--suggest", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["suggestions"][0]["kind"] == "sensitive"


def test_footprint_suggest_has_nothing_to_say_when_there_is_nothing(env, capsys):
    write_log(env, fresh([ev(10)]))
    assert cli.main(["footprint", "--suggest"]) == 0
    assert "Nothing to suggest" in capsys.readouterr().out


def test_activity_events_remember_the_directory_they_ran_in(env):
    activity.turn_on()
    ok = Verdict(action="allow", risk=0.0, policy_id="general", reason="ok")
    activity.record_call(agent="claude-code", tool_name="Read", text="", paths=["a.txt"], cwd="/work/a", verdict=ok)
    activity.record_call(agent="claude-code", tool_name="Read", text="", paths=["/abs/b.txt"], cwd="relative", verdict=ok)
    first, second = activity.load_events()
    assert first["cwd"] == "/work/a" and second["cwd"] is None


def test_what_the_guard_records_shows_up_in_the_footprint(env, capsys):
    activity.turn_on()
    server.guard(server.GuardReq(tool_call="Read: ~/.ssh/id_rsa", tool_name="Read", paths=["~/.ssh/id_rsa"], cwd="/work/a",
                                 agent="claude-code"))
    server.guard(server.GuardReq(tool_call="Edit: src/app.py", tool_name="Edit", paths=["src/app.py"], cwd="/work/a",
                                 agent="claude-code"))
    assert cli.main(["footprint", "--json"]) == 0
    p = json.loads(capsys.readouterr().out)["projects"][0]
    assert (p["project"], p["events"], p["flagged"], p["read"], p["write"]) == ("/work/a", 2, 1, 1, 1)
