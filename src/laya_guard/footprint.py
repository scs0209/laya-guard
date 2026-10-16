"""Footprint: what each project and session touched, and which paths are worth protecting, from the file activity log.

    laya-guard footprint [--since 7d] [--project DIR] [--agent NAME] [--json]
    laya-guard footprint --suggest

It reads what `laya-guard activity on` recorded (hook, shell and os-monitor events) and answers two questions: where did my
agents reach, and what could I protect that I'm not protecting yet. Nothing here changes anything: a suggestion is text
to review, never a rule that is applied, and an access alone is not evidence of bad intent. An agent that reads
`~/Documents/taxes/2025.pdf` once may have been asked to.

Reading the numbers:

* A call the guard blocked did not happen, so it is counted as a stopped attempt, not as something touched. A call the
  guard sent to review is counted as touched: the log can't tell whether you approved it.
* A "session" is a run of one agent's activity in one project with no gap over 30 minutes. The hooks don't send a session
  id, so this is a guess from the timestamps.
* A project is the git repository the event's directory is in (the directory itself when there is none). Events from
  before the log recorded `cwd`, and calls whose adapter sent no directory, go under "(unknown)".
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from .dashboard import WINDOWS, parse_ts
from .fileaccess import judge_accesses, load_rules, matches_glob, outside_project

SESSION_GAP = timedelta(minutes=30)
UNKNOWN = "(unknown)"
RARE_EVENTS = 5                      # a folder touched this few times, in this few sessions, costs little to protect
RARE_SESSIONS = 2
DEFAULT_WINDOW = "7d"
SUGGESTION_LIMIT = 10
WINDOW_CHOICES = tuple(w for w in WINDOWS if w != "1h")

# Well-known places that hold secrets and that protect.yaml doesn't cover by default: (glob, reason). A suggestion is made
# only when an agent actually touched one.
SENSITIVE = (
    ("~/.config/gcloud/**", "Google Cloud credentials"),
    ("~/.azure/**", "Azure credentials"),
    ("~/.config/gh/hosts.yml", "GitHub CLI token"),
    ("~/.terraform.d/credentials.tfrc.json", "Terraform Cloud token"),
    ("~/.cargo/credentials*", "crates.io token"),
    ("~/.gem/credentials", "RubyGems token"),
    ("~/.vault-token", "Vault token"),
    ("~/.config/doctl/**", "DigitalOcean token"),
    ("~/.config/op/**", "1Password CLI data"),
    ("~/.zsh_history", "shell history (often holds pasted secrets)"),
    ("~/.bash_history", "shell history (often holds pasted secrets)"),
    ("~/Library/Keychains/**", "macOS keychains"),
    ("~/.local/share/keyrings/**", "Linux keyrings"),
    ("~/Library/Application Support/Google/Chrome/**", "browser profile (cookies, saved logins)"),
    ("~/Library/Application Support/Firefox/**", "browser profile (cookies, saved logins)"),
    ("~/Library/Cookies/**", "browser cookies"),
    ("**/.npmrc", "package registry tokens (a project's own .npmrc)"),
    ("**/.pypirc", "package registry tokens"),
    ("**/.git-credentials", "stored git logins"),
    ("**/*.pem", "may hold a private key"),
    ("**/*.p12", "certificate with a private key"),
    ("**/*.pfx", "certificate with a private key"),
    ("**/*.key", "may hold a private key"),
    ("**/*.keystore", "signing keys"),
    ("**/*.tfstate", "Terraform state (holds secret values)"),
    ("**/service-account*.json", "cloud service account key"),
    ("**/credentials.json", "credentials file"),
)


# --- reading events ------------------------------------------------------------------------------------------------------

def _abs(e: dict) -> str | None:
    p = e.get("abs") or e.get("path")
    return p if isinstance(p, str) and p.startswith("/") else None


def touched(e: dict) -> bool:
    """False for a call the guard blocked: it never happened."""
    return e.get("action") != "block"


def flagged(e: dict) -> bool:
    """The access hit a protected-path rule (hook and shell events say so in policy_id, OS events in `would`)."""
    return e.get("policy_id") == "protected-path" or bool(e.get("would"))


@lru_cache(maxsize=2048)
def _git_root(cwd: str, home: str) -> str | None:
    p = Path(cwd)
    for d in (p, *p.parents):
        if str(d) == home or str(d) == "/":
            return None                                 # a dotfiles repo in ~ doesn't make the whole home one project
        if (d / ".git").exists():
            return str(d)
    return None


def project_of(e: dict, home: str) -> str:
    cwd = e.get("cwd")
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        return UNKNOWN
    return _git_root(cwd, home) or cwd


def shorten(path: str, home: str) -> str:
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def _inside(path: str, project: str) -> bool:
    return project != UNKNOWN and (path == project or path.startswith(project.rstrip("/") + "/"))


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


# --- the report ----------------------------------------------------------------------------------------------------------

def _sessions(rows: list[tuple[datetime, dict]]) -> list[list[tuple[datetime, dict]]]:
    """Runs of one agent's events in one project, split where nothing happened for SESSION_GAP."""
    by_agent: dict[str, list] = defaultdict(list)
    for row in rows:
        by_agent[str(row[1].get("agent") or "-")].append(row)
    out = []
    for agent_rows in by_agent.values():
        agent_rows.sort(key=lambda r: r[0])
        current = [agent_rows[0]]
        for row in agent_rows[1:]:
            if row[0] - current[-1][0] > SESSION_GAP:
                out.append(current)
                current = []
            current.append(row)
        out.append(current)
    return sorted(out, key=lambda s: s[0][0])


def _tally(rows: list[tuple[datetime, dict]]) -> dict:
    ops, sources = Counter(), Counter()
    for _, e in rows:
        if touched(e):
            ops[e.get("op")] += 1
            sources[e.get("source") or "-"] += 1
    return {"read": ops["read"], "write": ops["write"], "delete": ops["delete"], "sources": dict(sources)}


def select(events: list[dict], *, now: datetime | None = None, window: str = DEFAULT_WINDOW, project: str | None = None,
           agent: str | None = None, home: str | None = None) -> tuple[dict[str, list], int]:
    """The events inside `window` (24h | 7d | 30d | all), for one project directory or agent if given, grouped by project
    as {project: [(time, event)]}, and how many lines were left out for having no readable time."""
    if window not in WINDOWS:
        raise ValueError(f"window must be one of {', '.join(WINDOW_CHOICES)}")
    now = now or datetime.now(timezone.utc)
    home = (home or os.path.expanduser("~")).rstrip("/")
    wanted = os.path.abspath(os.path.expanduser(project)).rstrip("/") if project else None
    cutoff = now - WINDOWS[window] if WINDOWS[window] else None
    by_project: dict[str, list] = defaultdict(list)
    skipped = 0
    for e in events:
        ts = parse_ts(e.get("ts"))
        if ts is None:
            skipped += 1
            continue
        if (cutoff and ts < cutoff) or (agent and e.get("agent") != agent):
            continue
        proj = project_of(e, home)
        if wanted and not (proj == wanted or proj.startswith(wanted + "/")):
            continue
        by_project[proj].append((ts, e))
    return by_project, skipped


def build_report(events: list[dict], *, now: datetime | None = None, window: str = DEFAULT_WINDOW, project: str | None = None,
                 agent: str | None = None, top: int = 5, home: str | None = None) -> dict:
    """The footprint of the selected events (see `select`). Paths come out with ~ for the home directory."""
    now = now or datetime.now(timezone.utc)
    home = (home or os.path.expanduser("~")).rstrip("/")
    by_project, skipped = select(events, now=now, window=window, project=project, agent=agent, home=home)

    projects, totals = [], Counter()
    for proj, rows in by_project.items():
        rows.sort(key=lambda r: r[0])
        real = [r for r in rows if touched(r[1])]
        paths = Counter()
        outside: Counter = Counter()
        for _, e in real:
            p = _abs(e)
            if p:
                paths[p] += 1
                if proj != UNKNOWN and not _inside(p, proj):
                    outside[p] += 1
        hits = [(ts, e) for ts, e in rows if flagged(e)]
        stopped = sum(1 for _, e in rows if not touched(e))
        sessions = []
        for run in _sessions(rows):
            tally = _tally(run)
            sessions.append({"agent": str(run[0][1].get("agent") or "-"), "start": _iso(run[0][0]), "end": _iso(run[-1][0]),
                             "events": len(run), **tally,
                             "flagged": sum(1 for _, e in run if flagged(e)),
                             "stopped": sum(1 for _, e in run if not touched(e))})
        projects.append({
            "project": shorten(proj, home), "events": len(rows), **_tally(rows), "stopped": stopped,
            "agents": sorted({str(e.get("agent") or "-") for _, e in rows}),
            "flagged": len(hits), "outside": sum(outside.values()), "last": _iso(rows[-1][0]),
            "sessions": sessions,
            "top_paths": [{"path": shorten(p, home), "count": c} for p, c in sorted(paths.items(), key=lambda x: (-x[1], x[0]))[:top]],
            "outside_paths": [{"path": shorten(p, home), "count": c}
                              for p, c in sorted(outside.items(), key=lambda x: (-x[1], x[0]))[:top]],
            "protected_hits": [{"ts": _iso(ts), "agent": str(e.get("agent") or "-"), "op": e.get("op"),
                                "path": shorten(_abs(e) or str(e.get("path")), home), "source": e.get("source"),
                                "answer": e.get("would") or e.get("action"), "stopped": not touched(e)}
                               for ts, e in hits[-top:]],
        })
        totals["events"] += len(rows)
        totals["stopped"] += stopped
        totals["flagged"] += len(hits)
        totals["outside"] += sum(outside.values())
        totals["sessions"] += len(sessions)
    projects.sort(key=lambda p: p["last"], reverse=True)
    return {"window": window, "generated": _iso(now), "projects": projects, "skipped_events": skipped,
            "totals": {k: totals[k] for k in ("events", "sessions", "stopped", "flagged", "outside")},
            "suggestions": len(suggest([e for rows in by_project.values() for _, e in rows], home=home, limit=None))}


# --- suggestions -----------------------------------------------------------------------------------------------------------

def suggest(events: list[dict], *, rules=None, home: str | None = None, limit: int | None = SUGGESTION_LIMIT) -> list[dict]:
    """Paths an agent touched that no protect rule covers yet, worth a look. Two kinds:

    sensitive  matches a well-known place for secrets (SENSITIVE) that the default rules don't cover.
    rare       a folder in your own home, outside the project, that agents touched only a few times (in a few
               sessions). Protecting something an agent uses all the time means constant review prompts; this is
               the other end: cheap to protect, and a place you may not expect an agent to be.

    Each suggestion is a dict (path, ops, action, reason, kind, count, sessions, agents, last, example) that
    `render_rules_yaml` turns into protect.yaml text. Nothing is applied."""
    home = (home or os.path.expanduser("~")).rstrip("/")
    rules = load_rules() if rules is None else rules
    seen: dict[tuple, dict] = {}                          # one entry per distinct (op, path, project): the log is large
    for e in events:
        path, op = _abs(e), e.get("op")
        if not path or not touched(e) or flagged(e) or op not in ("read", "write", "delete"):
            continue
        ts = parse_ts(e.get("ts"))
        a = seen.setdefault((op, path, project_of(e, home)), {"count": 0, "agents": set(), "sessions": set(), "last": None})
        a["count"] += 1
        a["agents"].add(str(e.get("agent") or "-"))
        a["sessions"].add((str(e.get("agent") or "-"), ts.date().isoformat() if ts else ""))
        if ts and (a["last"] is None or _iso(ts) > a["last"]):
            a["last"] = _iso(ts)
    sensitive: dict[str, dict] = {}
    folders: dict[str, dict] = {}
    for (op, path, proj), a in seen.items():
        match = next(((g, why) for g, why in SENSITIVE if matches_glob(g, path, home)), None)
        if match:
            bucket, key, why = sensitive, match[0], match[1]
        elif proj != UNKNOWN and outside_project(path, proj, home):
            bucket, key, why = folders, str(Path(path).parent), None
        else:
            continue
        if judge_accesses([(op, path)], proj if proj != UNKNOWN else home, rules) is not None:
            continue                                        # a rule already covers it
        item = bucket.setdefault(key, {"path": key, "ops": ["read", "write"], "action": "review", "reason": why,
                                       "kind": "sensitive" if bucket is sensitive else "rare", "count": 0,
                                       "agents": set(), "_sessions": set(), "last": None, "example": path})
        item["count"] += a["count"]
        item["agents"] |= a["agents"]
        item["_sessions"] |= a["sessions"]
        if a["last"] and (item["last"] is None or a["last"] > item["last"]):
            item["last"] = a["last"]
    found = list(sensitive.values())
    for folder, item in folders.items():
        if item["count"] <= RARE_EVENTS and len(item["_sessions"]) <= RARE_SESSIONS:
            item.update(path=shorten(folder, home) + "/**",
                        reason=f"outside the project, touched {item['count']} time(s) in {len(item['_sessions'])} "
                               f"session(s): worth a look, not evidence of anything")
            found.append(item)
    for item in found:
        item["sessions"] = len(item.pop("_sessions"))
        item["agents"] = sorted(item["agents"])
        item["example"] = shorten(item["example"], home)
    found.sort(key=lambda s: (s["kind"] != "sensitive", -s["count"], s["path"]))
    return found if limit is None else found[:limit]


def render_rules_yaml(suggestions: list[dict], home: str | None = None) -> str:
    """The suggestions as protect.yaml entries, ready to paste into ~/.config/laya-guard/protect.yaml."""
    home = (home or os.path.expanduser("~")).rstrip("/")
    lines = ["# Suggested from your file activity. Nothing is applied: read each one, keep what you want.",
             "# An access is not evidence of bad intent; these only mark places you may want a second look at.",
             "version: 1", "protect:"]
    for s in suggestions:
        lines += [f"  # {s['kind']}: {s['count']} access(es) by {', '.join(s['agents'])}, last {str(s['last'] or '?')[:10]}",
                  f"  - path: {json.dumps(s['path'])}",
                  f"    ops: [{', '.join(s['ops'])}]",
                  f"    action: {s['action']}",
                  f"    reason: {json.dumps(s['reason'])}"]
    return "\n".join(lines) + "\n"


# --- text ----------------------------------------------------------------------------------------------------------------

def _local(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%m-%d %H:%M")


def _ops(d: dict) -> str:
    return f"read {d['read']}, write {d['write']}, delete {d['delete']}"


def render_text(report: dict, *, log_label: str = "", total_in_log: int | None = None) -> str:
    t = report["totals"]
    head = f"File footprint, last {report['window']}" if report["window"] != "all" else "File footprint, all recorded activity"
    lines = [head + (f" ({log_label}" + (f", {total_in_log} events" if total_in_log is not None else "") + ")" if log_label else "")]
    if not report["projects"]:
        return lines[0] + "\n\nNothing recorded in this window."
    lines.append(f"{t['events']} events in {len(report['projects'])} project(s) and {t['sessions']} session(s); "
                 f"{t['flagged']} on protected paths, {t['outside']} outside their project"
                 + (f", {t['stopped']} stopped by the guard (not counted as touched)" if t["stopped"] else "") + ".")
    for p in report["projects"]:
        lines += ["", f"{p['project']}   {', '.join(p['agents'])}",
                  f"  {p['events']} events ({_ops(p)}); {p['flagged']} on protected paths, {p['outside']} outside the project"
                  + (f", {p['stopped']} stopped" if p["stopped"] else "")]
        for s in p["sessions"][-5:]:
            lines.append(f"  {_local(s['start'])} - {_local(s['end'])[6:]}  {s['agent']:<12} {s['events']:>4} events  ({_ops(s)})"
                         + (f"  [{s['flagged']} protected]" if s["flagged"] else ""))
        if len(p["sessions"]) > 5:
            lines.append(f"  ... {len(p['sessions']) - 5} earlier session(s)")
        if p["protected_hits"]:
            lines.append("  protected paths touched:")
            lines += [f"    {_local(h['ts'])}  {h['agent']:<12} {h['op']:<6} {h['path']}   ({h['answer']}"
                      f"{', stopped' if h['stopped'] else ''})" for h in p["protected_hits"]]
        if p["outside_paths"]:
            lines.append("  outside the project:")
            lines += [f"    {o['path']}  x{o['count']}" for o in p["outside_paths"]]
        if p["top_paths"]:
            lines.append("  most touched:")
            lines += [f"    {o['path']}  x{o['count']}" for o in p["top_paths"]]
    if report["skipped_events"]:
        lines.append(f"\n({report['skipped_events']} line(s) without a readable time were left out.)")
    if report.get("suggestions"):
        lines.append(f"\n{report['suggestions']} path(s) you may want to protect: laya-guard footprint --suggest")
    return "\n".join(lines)
