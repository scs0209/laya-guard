"""Local verdict telemetry for measured dogfooding.

Every judgment is appended as one JSON line. Privacy rule: the raw
``tool_call`` text is NEVER stored (it may contain secrets). Only metadata
plus a truncated SHA-256 (for dedup/counting unique prompts) is kept.

Control via env:
  LAYA_GUARD_LOG=0        disable logging (default: enabled)
  LAYA_GUARD_LOG_PATH=... override the log file location
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def log_enabled() -> bool:
    return os.environ.get("LAYA_GUARD_LOG", "1") != "0"


def log_path() -> Path:
    override = os.environ.get("LAYA_GUARD_LOG_PATH", "")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "laya-guard" / "verdicts.jsonl"


_SCRIPT_REASON = re.compile(r"^(package\.json scripts\.).*: ", re.S)


def _public_reason(reason: str) -> str:
    """The reason as it may be logged. A package.json script finding reads "package.json scripts.<name>: <policy
    message>" and the name (or a chain of them) comes from the user's own file, so it is dropped here; the verdict
    the caller gets keeps it. Greedy on purpose: if a message itself held a colon, more is hidden, never less."""
    return _SCRIPT_REASON.sub("package.json script: ", reason)


TOOL_NAME_MAX = 64


def _clean_tool_name(tool_name: str | None) -> str | None:
    """The tool name as it is logged: non-printable characters (newlines, escape sequences' ESC, ...) dropped,
    then stripped and cut to TOOL_NAME_MAX characters, or None when nothing is left."""
    if not tool_name:
        return None
    name = "".join(ch for ch in tool_name if ch.isprintable()).strip()
    return name[:TOOL_NAME_MAX] or None


def log_verdict(
    *,
    action: str,
    risk: float,
    policy_id: str,
    reason: str,
    mock: bool,
    source: str,
    latency_ms: float,
    tool_call: str,
    via: str = "",
    tool_name: str | None = None,
) -> Path | None:
    """Append one redacted verdict entry. Returns the log path, or None if disabled.

    ``tool_name`` is the agent's name for the tool that was called (``Bash``, ``Edit``, an MCP tool, ...). It is
    metadata, not user content, so it is kept in clear, cut to ``TOOL_NAME_MAX`` characters; ``None`` (or blank)
    is logged as ``null``."""
    if not log_enabled():
        return None
    path = log_path()
    digest = hashlib.sha256(tool_call.encode("utf-8")).hexdigest()[:16]
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": action,
        "risk": risk,
        "policy_id": policy_id,
        "reason": _public_reason(reason),  # policy message from policy.yaml — no user content
        "mock": mock,
        "source": source,
        "latency_ms": round(float(latency_ms), 1),
        "tool_len": len(tool_call),
        "tool_sha": digest,
        "tool_name": _clean_tool_name(tool_name),
    }
    if via:
        # regex | allowlist | model | heuristic | limit | offline: how the verdict was made
        entry["verdict_source"] = via
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def load_entries(path: Path | str | None = None) -> list[dict]:
    """Read entries, skipping malformed lines (shared append file)."""
    p = Path(path).expanduser() if path else log_path()
    if not p.exists():
        return []
    entries = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def degraded_kind(entry: dict) -> str | None:
    """How a call was judged without the model, or None if it wasn't: "offline" (the server was down or too
    slow: regexes and the safe list only) or "model_error" (the server was up but the model failed: heuristics
    stood in). Mock mode is a choice, not a failure."""
    verdict_source = entry.get("verdict_source") or entry.get("via")
    if verdict_source == "offline":
        return "offline"
    if verdict_source == "heuristic" and not entry.get("mock"):
        return "model_error"
    return None


def summarize(entries: list[dict]) -> dict:
    """Aggregate verdict distribution + latency. Pure function (testable)."""
    total = len(entries)
    by_action: dict[str, int] = {}
    by_policy: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    latencies: list[float] = []
    mocks = offline = model_error = 0
    for e in entries:
        a = str(e.get("action", "?"))
        by_action[a] = by_action.get(a, 0) + 1
        p = str(e.get("policy_id", "?"))
        by_policy[p] = by_policy.get(p, 0) + 1
        tool = e.get("tool_name")
        if tool:  # older entries and callers that don't send one have no tool name; they aren't a tool
            by_tool[str(tool)] = by_tool.get(str(tool), 0) + 1
        try:
            latencies.append(float(e.get("latency_ms", 0.0)))
        except (TypeError, ValueError):
            pass
        if e.get("mock"):
            mocks += 1
        kind = degraded_kind(e)
        offline += kind == "offline"
        model_error += kind == "model_error"

    def rate(n: int) -> float:
        return round(n / total, 3) if total else 0.0

    top_policies = sorted(by_policy.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_tools = sorted(by_tool.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "total": total,
        "by_action": by_action,
        "block_rate": rate(by_action.get("block", 0)),
        "review_rate": rate(by_action.get("review", 0)),
        "top_policies": [{"policy_id": k, "count": v} for k, v in top_policies],
        "by_tool": [{"tool_name": k, "count": v} for k, v in top_tools],
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "mock_share": rate(mocks),
        # calls judged without the model, kept apart from real verdicts: an outage is not a denial
        "degraded": {"offline": offline, "model_error": model_error, "total": offline + model_error,
                     "share": rate(offline + model_error)},
    }
