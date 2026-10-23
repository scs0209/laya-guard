"""laya-guard audit: judge the shell commands your coding agents already ran, from their own transcripts.

Claude Code keeps every session in ~/.claude/projects/<project>/<session>.jsonl and Codex in
~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl (or under CODEX_HOME). Both record each shell command the agent asked
to run, where, and when. This reads those files, judges every command the way that agent's hook would have (the judge
with the agent's source and ask setting, plus the protected paths), and reports what would have been reviewed or
blocked.

Nothing is written and nothing leaves the machine: the verdict log and the dashboard don't see these judgments, and
the transcripts are only read. `--redact` leaves the command text out of the report, for a screenshot.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import fileaccess
from .guard import ASK_AGENTS, Verdict, is_mock, judge

AGENTS = ("claude-code", "codex")
_RANK = {"allow": 0, "review": 1, "block": 2}


@dataclass
class Call:
    agent: str
    when: datetime | None
    cwd: str | None
    command: str
    session: str
    status: str = "no result"          # ok | error | no result: what the transcript says happened after the call


@dataclass
class Finding:
    call: Call
    verdict: Verdict


@dataclass
class Report:
    since: datetime | None
    calls: list = field(default_factory=list)
    findings: list = field(default_factory=list)        # review and block only, worst first
    counts: dict = field(default_factory=dict)          # action -> number of calls
    sessions: dict = field(default_factory=dict)        # agent -> number of sessions read
    model: bool = False
    seconds: float = 0.0


def parse_since(text: str | None) -> datetime | None:
    """`7d`, `24h`, `30d`, or `all`. None and "all" mean no limit."""
    if not text or text == "all":
        return None
    unit, n = text[-1], text[:-1]
    if unit not in "dh" or not n.isdigit():
        raise ValueError(f"--since takes a number of days or hours (7d, 24h) or `all`, not {text!r}")
    delta = timedelta(days=int(n)) if unit == "d" else timedelta(hours=int(n))
    return datetime.now(timezone.utc) - delta


def _time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _recent_files(paths, since: datetime | None):
    for p in paths:
        try:
            if since is None or datetime.fromtimestamp(p.stat().st_mtime, timezone.utc) >= since:
                yield p
        except OSError:
            continue


def _lines(path: Path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def claude_calls(home: Path, since: datetime | None) -> tuple[list, int]:
    """Bash tool calls in Claude Code transcripts (subagents' included)."""
    root = home / ".claude" / "projects"
    files = list(_recent_files(sorted(root.glob("**/*.jsonl")), since)) if root.is_dir() else []
    calls = []
    for path in files:
        pending: dict[str, Call] = {}
        for o in _lines(path):
            msg = o.get("message") if isinstance(o, dict) else None
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use" and part.get("name") == "Bash":
                    cmd = (part.get("input") or {}).get("command")
                    when = _time(o.get("timestamp"))
                    if isinstance(cmd, str) and cmd.strip() and (since is None or when is None or when >= since):
                        call = Call("claude-code", when, o.get("cwd"), cmd, path.stem)
                        calls.append(call)
                        pending[part.get("id")] = call
                elif part.get("type") == "tool_result" and part.get("tool_use_id") in pending:
                    pending.pop(part["tool_use_id"]).status = "error" if part.get("is_error") else "ok"
    return calls, len(files)


def _codex_home(home: Path) -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env).expanduser() if env else home / ".codex"


def codex_calls(home: Path, since: datetime | None) -> tuple[list, int]:
    """exec_command calls in Codex session files."""
    root = _codex_home(home) / "sessions"
    files = list(_recent_files(sorted(root.glob("**/*.jsonl")), since)) if root.is_dir() else []
    calls = []
    for path in files:
        cwd, pending = None, {}
        for o in _lines(path):
            payload = o.get("payload") if isinstance(o, dict) else None
            if not isinstance(payload, dict):
                continue
            if o.get("type") == "session_meta":
                cwd = payload.get("cwd") or cwd
            elif payload.get("type") == "function_call" and payload.get("name") in ("exec_command", "shell"):
                try:
                    args = json.loads(payload.get("arguments") or "{}")
                except ValueError:
                    continue
                cmd = args.get("cmd", args.get("command"))
                if isinstance(cmd, list):
                    cmd = " ".join(str(c) for c in cmd)
                when = _time(o.get("timestamp"))
                if isinstance(cmd, str) and cmd.strip() and (since is None or when is None or when >= since):
                    call = Call("codex", when, args.get("workdir") or cwd, cmd, path.stem)
                    calls.append(call)
                    pending[payload.get("call_id")] = call
            elif payload.get("type") == "function_call_output" and payload.get("call_id") in pending:
                pending.pop(payload["call_id"]).status = "ok"     # it ran; the output's format doesn't say how it ended
    return calls, len(files)


# The source each agent's hook sends: Claude Code's sends none (so "user"), Codex's says "tool". Judging a call with
# another agent's source would make the report stricter or looser than the hook ever was.
SOURCES = {"claude-code": "user", "codex": "tool"}


def judge_call(call: Call) -> Verdict:
    """What the hook would have answered: the judge, held to the protected paths too. Nothing is logged."""
    cwd = call.cwd if call.cwd and os.path.isabs(call.cwd) else None
    v = judge(call.command, source=SOURCES.get(call.agent, "tool"), cwd=cwd, ask=call.agent in ASK_AGENTS)
    return fileaccess.stricter(v, fileaccess.judge_shell(call.command, cwd))


def run(agents=AGENTS, since: datetime | None = None, home: Path | None = None, progress=None) -> Report:
    home = home or Path.home()
    t0 = time.perf_counter()
    report = Report(since=since, model=not is_mock())
    readers = {"claude-code": claude_calls, "codex": codex_calls}
    for agent in agents:
        calls, sessions = readers[agent](home, since)
        report.calls += calls
        report.sessions[agent] = sessions
    cache: dict[tuple, Verdict] = {}                    # the same command in the same place is judged once
    for i, call in enumerate(report.calls):
        key = (call.command, call.cwd)
        if key not in cache:
            cache[key] = judge_call(call)
        v = cache[key]
        report.counts[v.action] = report.counts.get(v.action, 0) + 1
        if v.action != "allow":
            report.findings.append(Finding(call, v))
        if progress and (i + 1) % 50 == 0:
            progress(i + 1, len(report.calls))
    report.findings.sort(key=lambda f: (-_RANK[f.verdict.action], -f.verdict.risk,
                                        -(f.call.when.timestamp() if f.call.when else 0)))
    report.seconds = time.perf_counter() - t0
    return report


# --- the report ----------------------------------------------------------------------------------------------------

_NAMES = {"claude-code": "Claude Code", "codex": "Codex"}


def _short_path(path: str | None, home: Path, width: int = 36) -> str:
    if not path:
        return ""
    h = str(home)
    p = "~" + path[len(h):] if path == h or path.startswith(h + os.sep) else path
    return p if len(p) <= width else "…" + p[-(width - 1):]


def _grouped(findings):
    """The same command held the same way is one line, with how many times and when last."""
    groups: dict[tuple, list] = {}
    for f in findings:
        groups.setdefault((f.call.command, f.verdict.action, f.verdict.policy_id), []).append(f)
    out = []
    for fs in groups.values():
        latest = max(fs, key=lambda f: f.call.when.timestamp() if f.call.when else 0)
        out.append((latest, len(fs)))
    out.sort(key=lambda g: (-_RANK[g[0].verdict.action], -g[0].verdict.risk,
                            -(g[0].call.when.timestamp() if g[0].call.when else 0)))
    return out


def _one_line(text: str, width: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[:width - 1] + "…"


def render(report: Report, limit: int = 20, redact: bool = False, home: Path | None = None) -> str:
    home = home or Path.home()
    span = "all time" if report.since is None else f"since {report.since.astimezone().strftime('%Y-%m-%d %H:%M')}"
    lines = [f"laya-guard audit — {span}", ""]
    for agent, n in report.sessions.items():
        count = sum(1 for c in report.calls if c.agent == agent)
        lines.append(f"  {_NAMES[agent]:<12} {n} sessions, {count} shell commands")
    total = len(report.calls)
    if not total:
        lines += ["", "No shell commands found in that period."]
        return "\n".join(lines)
    c = report.counts
    lines += ["", f"  block {c.get('block', 0)}   review {c.get('review', 0)}   allow {c.get('allow', 0)}"
                  f"   ({'with the model' if report.model else 'rules and heuristics only (mock mode)'}, "
                  f"{report.seconds:.0f}s)"]
    if not report.findings:
        lines += ["", "Nothing would have been held."]
        return "\n".join(lines)
    groups = _grouped(report.findings)
    shown = groups[:limit]
    lines += ["", f"What the hook would have held (worst first, {len(shown)} of {len(groups)} distinct commands):", ""]
    for f, n in shown:
        when = f.call.when.astimezone().strftime("%m-%d %H:%M") if f.call.when else "     ?     "
        times = f"×{n}" if n > 1 else ""
        lines.append(f"  {f.verdict.action.upper():<6} {when}  {_NAMES[f.call.agent]:<11} {f.verdict.policy_id:<16}"
                     f" {times:<5} {_short_path(f.call.cwd, home)}")
        detail = f.verdict.reason if redact else _one_line(f.call.command, 110)
        lines.append(f"         {detail}")
    lines += ["", "Judged on this machine; nothing was logged or sent. --redact leaves the commands out."]
    return "\n".join(lines)


def to_json(report: Report, redact: bool = False) -> dict:
    return {
        "since": report.since.isoformat() if report.since else None,
        "sessions": report.sessions,
        "commands": len(report.calls),
        "counts": report.counts,
        "model": report.model,
        "findings": [{
            "agent": f.call.agent,
            "when": f.call.when.isoformat() if f.call.when else None,
            "cwd": f.call.cwd,
            "status": f.call.status,
            "action": f.verdict.action,
            "risk": f.verdict.risk,
            "policy_id": f.verdict.policy_id,
            "reason": f.verdict.reason,
            **({} if redact else {"command": f.call.command}),
        } for f in report.findings],
    }


def progress_to_stderr(done: int, total: int) -> None:
    print(f"\r  judging {done}/{total}…", end="", file=sys.stderr, flush=True)
