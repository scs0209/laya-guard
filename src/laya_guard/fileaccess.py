"""Protected paths: rules for the files an agent touches with its own file tools.

The hook that guards Claude Code's file tools (Read, Edit, Write, ...) tells the server which paths a call names. A
rule turns a path and an operation into review or block, before the access happens. It is a rule lane, like the regex
policies: no model is involved, and the path itself is never logged (the log keeps hashes and lengths).

This sees declared access only: the path a tool call names. A file read inside a shell command, a script or a child
process never reaches it.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .guard import Verdict, is_mock

log = logging.getLogger("laya_guard")

RULES_PATH = Path(__file__).with_name("protect.yaml")

# The agent's tool name -> what it does to the file. Anything else is not a file tool and is never judged here.
FILE_TOOLS = {"Read": "read", "Glob": "read", "Grep": "read", "NotebookRead": "read",
              "Edit": "write", "MultiEdit": "write", "Write": "write", "NotebookEdit": "write"}
OPS = ("read", "write")
ACTIONS = ("review", "block")
_RISK = {"review": 0.55, "block": 0.92}  # the fixed values the regex short-circuits already use
_RANK = {"review": 1, "block": 2}
MAX_PATHS = 50
MAX_PATH_LEN = 4096


@dataclass(frozen=True)
class Rule:
    glob: str
    ops: frozenset
    action: str
    reason: str
    excepts: tuple = ()


# --- loading -------------------------------------------------------------------------------------------------------

def config_dir() -> Path:
    """Where laya-guard keeps what the user sets: ~/.config/laya-guard (or under XDG_CONFIG_HOME)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "laya-guard"


def user_rules_path() -> Path:
    override = os.environ.get("LAYA_GUARD_PROTECT", "")
    return Path(override).expanduser() if override else config_dir() / "protect.yaml"


def _parse(text: str, where: str) -> list[Rule]:
    """The rules in one file. An entry that doesn't make sense is skipped with a warning, never guessed at."""
    data = yaml.safe_load(text) or {}
    entries = data.get("protect") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError("expected a `protect:` list")
    rules = []
    for i, e in enumerate(entries, 1):
        try:
            glob = e["path"]
            ops = e.get("ops", list(OPS))
            action = e.get("action", "review")
            excepts = e.get("except", [])
            if not isinstance(glob, str) or not glob.strip():
                raise ValueError("path must be a non-empty string")
            if not isinstance(ops, list) or not ops or any(o not in OPS for o in ops):
                raise ValueError(f"ops must be a non-empty list of {', '.join(OPS)}")
            if action not in ACTIONS:
                raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
            if not isinstance(excepts, list) or any(not isinstance(x, str) for x in excepts):
                raise ValueError("except must be a list of globs")
            reason = e.get("reason") or f"protected path ({glob})"
            rules.append(Rule(glob.strip(), frozenset(ops), action, str(reason), tuple(excepts)))
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            log.warning("protect rules: entry %d of %s skipped: %s", i, where, exc)
    return rules


@lru_cache(maxsize=8)
def _load_file(path: str, mtime_ns: int) -> tuple:
    return tuple(_parse(Path(path).read_text(encoding="utf-8"), path))


def load_rules() -> list[Rule]:
    """The packaged defaults plus the user's own file. Edits to the user's file apply to the next call. A user file
    that can't be read leaves the defaults in force (and says so in the log) instead of switching protection off."""
    rules = list(_load_file(str(RULES_PATH), RULES_PATH.stat().st_mtime_ns))
    user = user_rules_path()
    try:
        if user.is_file():
            rules += _load_file(str(user), user.stat().st_mtime_ns)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        log.warning("protect rules: %s ignored: %s", user, exc)
    return rules


# --- matching ------------------------------------------------------------------------------------------------------

def _translate(glob: str) -> str:
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("/**", i) and i + 3 == len(glob):
            out.append("(?:/.*)?")  # dir/** is the directory itself and everything below it
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return "".join(out)


@lru_cache(maxsize=512)
def _regex(glob: str, cwd: str, home: str) -> re.Pattern:
    """Absolute paths are matched case-insensitively: the default file systems on macOS and Windows are."""
    if glob == "~" or glob.startswith("~/"):
        prefix, rest = home, glob[1:]
    elif glob.startswith("/") or glob.startswith("**"):
        prefix, rest = "", glob
    else:
        prefix, rest = cwd.rstrip("/") + "/", glob
    return re.compile("^" + re.escape(prefix) + _translate(rest) + "$", re.IGNORECASE)


def matches_glob(glob: str, path: str, cwd: str | None = None) -> bool:
    """Whether an absolute path matches a glob written the way protect.yaml writes them (~, **/, dir/**, relative to cwd)."""
    cwd = cwd if cwd and os.path.isabs(cwd) else os.getcwd()
    return bool(_regex(glob, cwd, os.path.expanduser("~")).match(path))


def outside_project(path: str, project: str | None, home: str) -> bool:
    """A file in one of your own folders (Documents, other projects, ...) that is not where the agent was started.
    System directories, hidden directories (~/.nvm, ~/.zshrc, ...) and ~/Library are the machine's own business: a
    shell reads dozens of them every time it starts, so they only count when a protected-path rule names them."""
    if project is None:
        return False
    root = project.rstrip("/")
    if path == root or path.startswith(root + "/"):
        return False
    if not path.startswith(home + "/"):
        return False
    first = path[len(home) + 1:].split("/", 1)[0]
    return not first.startswith(".") and first != "Library"


def _candidates(path: str, cwd: str) -> list[str]:
    """The path as named and, when it differs, where it really points (a link can lead into a protected place).

    The operating system follows a link before it applies a `..` after it (`link/../x` is the parent of the link's
    target, not the directory holding the link), so the path is resolved as written too, not only after `..` has been
    collapsed as text."""
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    named = os.path.normpath(p)
    out = [named]
    for q in (p, named):
        try:
            real = os.path.realpath(q)
        except (OSError, ValueError):
            continue
        if real not in out:
            out.append(real)
    return out


def _roots(cwd: str, home: str) -> list[tuple[str, str]]:
    """The directories `~/` and a relative glob stand for: as given and, when they differ, where they really are (a
    resolved candidate starts with the real home, which may itself be a link)."""
    roots = [(cwd, home)]
    try:
        real = (os.path.realpath(cwd), os.path.realpath(home))
    except (OSError, ValueError):
        return roots
    return roots if real == roots[0] else roots + [real]


def _protects(rule: Rule, candidates: list[str], roots: list[tuple[str, str]]) -> bool:
    """A candidate is protected when the rule's glob matches it and no `except` does. Each candidate stands alone, so
    a link called .env.example that points at .env is still caught through its real path."""
    for cwd, home in roots:
        rx = _regex(rule.glob, cwd, home)
        skips = [_regex(x, cwd, home) for x in rule.excepts]
        if any(rx.match(c) and not any(s.match(c) for s in skips) for c in candidates):
            return True
    return False


# An access is (op, path). Deleting a file is changing it, so a delete is judged by the `write` rules.
RULE_OP = {"read": "read", "write": "write", "delete": "write"}


def _raw_accesses(tool_name: str | None, paths, accesses=None) -> list[tuple]:
    """Every (op, path) a call names, as given: nothing dropped, nothing checked."""
    if isinstance(accesses, (list, tuple)):
        raw = []
        for a in accesses:
            if isinstance(a, dict):
                raw.append((a.get("op"), a.get("path")))
            elif isinstance(a, (list, tuple)) and len(a) == 2:
                raw.append((a[0], a[1]))
        return raw
    if tool_name in FILE_TOOLS and isinstance(paths, (list, tuple)):
        return [(FILE_TOOLS[tool_name], p) for p in paths]
    return []


def accesses_for(tool_name: str | None, paths, accesses=None) -> list[tuple[str, str]]:
    """The (op, path) pairs a call names, ready to judge or to record. `accesses` is an adapter's own reading of what
    the call touches (op is read, write or delete), so it works for any agent. Without it, `paths` of a Claude Code file
    tool (FILE_TOOLS) mean the op that tool performs. Entries that make no sense are dropped, never guessed at, and at
    most MAX_PATHS come back: a caller that judges must check `_over_limit` first, or what is past the cap goes unchecked."""
    ok = [(op, p) for op, p in _raw_accesses(tool_name, paths, accesses)
          if op in RULE_OP and isinstance(p, str) and p.strip() and len(p) <= MAX_PATH_LEN]
    return ok[:MAX_PATHS]


def _over_limit(raw: list[tuple]) -> bool:
    """A call that names more paths than we look at, or a path too long to look at. Skipping what is past a limit would
    let an unchecked path through (the 51st could be ~/.ssh/id_rsa), so such a call is reviewed instead."""
    return len(raw) > MAX_PATHS or any(isinstance(p, str) and len(p) > MAX_PATH_LEN for _, p in raw)


def _limit_verdict() -> Verdict:
    return Verdict(action="review", risk=_RISK["review"], policy_id="protected-path",
                   reason="too many or too long paths to check", mock=is_mock(), verdict_source="limit")


def judge_accesses(accesses: list[tuple[str, str]], cwd: str | None = None, rules: list[Rule] | None = None) -> Verdict | None:
    """The verdict for a set of accesses, or None when no rule applies to any of them."""
    if not accesses:
        return None
    rules = load_rules() if rules is None else rules
    cwd = cwd if cwd and os.path.isabs(cwd) else os.getcwd()
    roots = _roots(cwd, os.path.expanduser("~"))
    worst: Rule | None = None
    for op, raw in accesses:
        candidates = _candidates(raw, cwd)
        for rule in rules:
            if RULE_OP[op] in rule.ops and _protects(rule, candidates, roots):
                if worst is None or _RANK[rule.action] > _RANK[worst.action]:
                    worst = rule
    if worst is None:
        return None
    return Verdict(action=worst.action, risk=_RISK[worst.action], policy_id="protected-path", reason=worst.reason,
                   mock=is_mock(), verdict_source="regex")


def judge_paths(tool_name: str | None, paths, cwd: str | None = None, rules: list[Rule] | None = None) -> Verdict | None:
    """The verdict for a Claude Code file tool call, or None when no rule applies (not a file tool, no paths, no
    matching rule). A call past a limit (MAX_PATHS paths, MAX_PATH_LEN characters each) is reviewed: nothing is skipped."""
    if _over_limit(_raw_accesses(tool_name, paths)):
        return _limit_verdict()
    return judge_accesses(accesses_for(tool_name, paths), cwd, rules)


def judge_file_call(tool_name: str | None, paths, cwd: str | None = None, accesses=None) -> Verdict | None:
    """The verdict for a file call, decided by the paths it names alone, or None when this isn't one (another tool, or no
    usable path), so the caller judges the call as usual. A protected path is reviewed or blocked and anything else is
    allowed. The content being read or written is not judged, and no model runs. `accesses` lets any agent's adapter
    say what a call touches; without it only Claude Code's file tools (`paths`) count. A call past a limit is reviewed,
    never judged on the part that fits: the part that doesn't could be the protected one."""
    if _over_limit(_raw_accesses(tool_name, paths, accesses)):
        return _limit_verdict()
    pairs = accesses_for(tool_name, paths, accesses)
    if not pairs:
        return None
    return judge_accesses(pairs, cwd) or Verdict(
        action="allow", risk=0.0, policy_id="general", reason="File tool call: no protected path matched",
        mock=is_mock(), verdict_source="allowlist")
