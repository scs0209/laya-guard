"""OS sandbox settings for coding agents, made from protect.yaml.

The hooks judge what a command's text says. A script, a child process or a path built while a command runs never
reaches them (docs/guide/incidents.md). An OS sandbox does: the kernel refuses the read or the write whatever asked
for it. Most agents ship one, but their defaults stop writes outside the project and leave reads alone, so
`cat ~/.ssh/id_rsa` from a script still works. This turns the protected paths into each agent's own sandbox settings:

    claude-code   ~/.claude/settings.json        sandbox.enabled + sandbox.filesystem.denyRead / denyWrite
    codex         ~/.codex/config.toml           a permissions profile (`default_permissions`), "deny" / "read"
    copilot-cli   ~/.copilot/settings.json       sandbox.enabled + sandbox.userPolicy.filesystem
    gemini-cli    <project>/.gemini/             a Seatbelt profile (macOS) + tools.sandbox + SEATBELT_PROFILE
    qwen-code     <project>/.qwen/               the same, Qwen's way
    cursor        ~/.cursor/cli-config.json      the sandbox on; it has no way to deny a read, the hook keeps that

and, for an agent with no sandbox of its own, `laya-guard run -- <agent>` starts it inside Anthropic's sandbox-runtime
(`srt`). An agent that runs inside an IDE (Windsurf, Cline, ...) can't be wrapped from here; the hook stays its guard.

A sandbox can only refuse, it can't ask: a protected path the hook would `review` is denied here. The hook still
judges every call first. What each agent's sandbox can't express (a glob it doesn't take, a rule with `except`) is
listed in the notes and left to the hook. Nothing is written without `apply`; every change is shown first, the old
file is backed up, and `remove` takes out exactly what was added (recorded in ~/.config/laya-guard/sandbox.json).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import fileaccess
from .install import (Artifact, InstallError, Settings, _diff, _toml_check, load_settings, scope_path, SPECS,
                      write_artifact)

AGENTS = ("claude-code", "codex", "copilot-cli", "gemini-cli", "qwen-code", "cursor")
ALIASES = {"claude": "claude-code", "copilot": "copilot-cli", "gemini": "gemini-cli", "qwen": "qwen-code",
           "cursor-agent": "cursor"}
PROFILE = "laya-guard"                     # the name of the Codex permissions profile and the Seatbelt profile
_GLOB = re.compile(r"[*?\[]")


def agent_id(name: str) -> str:
    agent = ALIASES.get(name, name)
    if agent not in AGENTS:
        raise InstallError(f"no sandbox support for {name!r}: the agents with one are {', '.join(AGENTS)}. "
                           "For another agent run in a terminal, use `laya-guard run -- <agent>`")
    return agent


# --- protect.yaml -> what to deny ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Entry:
    path: str          # ~/x or /x (home, abs) or the tail of **/x (anywhere); a trailing /** is dropped
    kind: str          # home | abs | anywhere
    read: bool         # deny reading (a rule with ops: [read]); reading is only ever denied with writing too
    write: bool        # deny writing

    @property
    def glob(self) -> bool:
        return bool(_GLOB.search(self.path))


@dataclass
class Policy:
    entries: list = field(default_factory=list)
    skipped: list = field(default_factory=list)        # (path, why): left to the hook


def policy(rules=None) -> Policy:
    """The protected paths as deny entries: a path with a read rule is denied to read and write, one with only write
    rules is denied to write. Rules with `except` and relative globs are skipped (the sandboxes can't say them)."""
    rules = fileaccess.load_rules() if rules is None else rules
    ops: dict[tuple[str, str], set] = {}
    out = Policy()
    for rule in rules:
        path = rule.glob[:-3] if rule.glob.endswith("/**") else rule.glob
        if path.startswith("~/"):
            kind = "home"
        elif path.startswith("/"):
            kind = "abs"
        elif path.startswith("**/") and len(path) > 3:
            kind, path = "anywhere", path[3:]
        else:
            out.skipped.append((rule.glob, "a path relative to the project"))
            continue
        if rule.excepts and _excepts_overlap(kind, path, rule.excepts):
            out.skipped.append((rule.glob, "the rule has exceptions (`except:`)"))
            continue
        ops.setdefault((kind, path), set()).update(rule.ops)
    for (kind, path), o in ops.items():
        read = "read" in o
        out.entries.append(Entry(path, kind, read=read, write=True))
    out.entries.sort(key=lambda e: (e.kind != "home", e.kind != "abs", e.path))
    return out


def _excepts_overlap(kind: str, path: str, excepts) -> bool:
    """Whether an `except:` glob can match something the rule's path does. A sandbox can't carve exceptions out, so
    such a rule is left to the hook; `**/.env` with `except: [**/.env.example]` can go in as it is."""
    if kind != "anywhere":
        return True
    for ex in excepts:
        tail = ex[3:] if ex.startswith("**/") else ex
        if fnmatch.fnmatchcase(tail, path) or fnmatch.fnmatchcase(path, tail):
            return True
    return False


def _expand(entry: Entry, home: Path) -> str:
    return str(home / entry.path[2:]) if entry.kind == "home" else entry.path


# --- the plan (same shape the CLI's preview/apply uses for install) --------------------------------------------------

@dataclass
class SandboxPlan:
    action: str                     # apply | remove
    agent: str
    artifacts: list
    notes: list = field(default_factory=list)
    state: dict | None = None       # what to record in sandbox.json after writing (None: forget the agent)
    repaired: bool = False

    @property
    def changed(self) -> bool:
        return any(a.changed for a in self.artifacts)

    @property
    def diff(self) -> str:
        return "\n".join(_diff(a.path, a.before, a.after) for a in self.artifacts if a.changed)


def state_path() -> Path:
    return fileaccess.config_dir() / "sandbox.json"


def _state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _state_key(agent: str, path: Path) -> str:
    return f"{agent}:{path}"


def apply_plan(plan: SandboxPlan) -> list:
    """Write every changed file, then the record. If any write fails, the files already written go back to what they
    were and the record is left as it was, so a half-applied change can't outlive its record."""
    state = _state()
    key = _state_key(plan.agent, plan.artifacts[0].path)
    if plan.state is None:
        state.pop(key, None)
    else:
        state[key] = plan.state
    written, backups = [], []
    sp = state_path()
    sp_before = sp.read_text(encoding="utf-8") if sp.is_file() else None
    try:
        for a in plan.artifacts:
            if a.changed:
                backup = write_artifact(a)
                written.append(a)
                if backup:
                    backups.append(backup)
        sp.parent.mkdir(parents=True, exist_ok=True)
        write_artifact(Artifact(sp, sp_before, json.dumps(state, indent=2, ensure_ascii=False) + "\n"))
    except BaseException:
        for a in reversed(written):
            try:
                write_artifact(Artifact(a.path, a.after, a.before))
            except OSError:
                pass
        raise
    return backups


def make_plan(action: str, agent: str, scope: str = "user", home: Path | None = None,
              project_dir: Path | None = None, rules=None) -> SandboxPlan:
    agent = agent_id(agent)
    home = home or Path.home()
    project_dir = project_dir or Path.cwd()
    pol = policy(rules)
    maker = {"claude-code": _claude, "codex": _codex, "copilot-cli": _copilot, "gemini-cli": _gemini,
             "qwen-code": _gemini, "cursor": _cursor}[agent]
    plan = maker(action, agent, scope, home, project_dir, pol)
    if action == "apply":
        plan.notes += [f"left to the hook: {p} ({why})" for p, why in pol.skipped]
        if agent != "cursor" and any(e.read and e.path in ("~/.ssh", ".ssh") for e in pol.entries):
            plan.notes.append("~/.ssh can't be read inside the sandbox, so git over SSH (push, pull, clone) fails "
                              "there; the agent has to run it outside the sandbox, which asks you first")
    return plan


# --- JSON helpers --------------------------------------------------------------------------------------------------

def _obj(parent: dict, key: str, where: Path) -> dict:
    value = parent.setdefault(key, {})
    if not isinstance(value, dict):
        raise InstallError(f"{where}: `{key}` isn't an object; nothing was changed")
    return value


def _list(parent: dict, key: str, where: Path) -> list:
    value = parent.setdefault(key, [])
    if not isinstance(value, list):
        raise InstallError(f"{where}: `{key}` isn't a list; nothing was changed")
    return value


def _json_plan(action: str, agent: str, settings: Settings, enable: tuple, lists: dict, extra_notes=()) -> SandboxPlan:
    """apply: set the `enable` key path to its value (remembering if it was already so) and add the entries in
    `lists` ({key path: [values]}); remove: undo exactly what sandbox.json says was added to this file."""
    path = settings.path
    data = json.loads(json.dumps(settings.data))
    key = _state_key(agent, path)
    *parents, last = enable[0]
    if action == "apply":
        node = data
        for p in parents:
            node = _obj(node, p, path)
        prior = _state().get(key, {})
        enabled_by_us = prior.get("enabled_by_us", node.get(last) != enable[1])
        # what the switch was before, so remove puts back an explicit "disabled"/false instead of dropping the key
        had_prior = prior.get("had_prior", last in node)
        prior_value = prior.get("prior_value", node.get(last))
        node[last] = enable[1]
        added = {".".join(k): list(prior.get("added", {}).get(".".join(k), [])) for k in lists}
        for keys, values in lists.items():
            node = data
            for p in keys[:-1]:
                node = _obj(node, p, path)
            target = _list(node, keys[-1], path)
            for v in values:
                if v not in target:
                    target.append(v)
                    added[".".join(keys)].append(v)
        state = {"agent": agent, "file": str(path), "enabled_by_us": enabled_by_us, "added": added,
                 "had_prior": had_prior, "prior_value": prior_value,
                 "created": prior.get("created", not settings.exists)}
    else:
        prior = _state().get(key)
        if not prior:
            return SandboxPlan(action, agent, [Artifact(path, settings.raw or None, settings.raw or None)],
                               notes=["laya-guard has no record of changing this file"], state=None)
        for dotted, values in prior.get("added", {}).items():
            keys = dotted.split(".")
            node = data
            for p in keys[:-1]:
                node = node.get(p) if isinstance(node, dict) else None
            if isinstance(node, dict) and isinstance(node.get(keys[-1]), list):
                node[keys[-1]] = [v for v in node[keys[-1]] if v not in values]
        if prior.get("enabled_by_us"):
            node = data
            for p in parents:
                node = node.get(p) if isinstance(node, dict) else None
            if isinstance(node, dict):
                if prior.get("had_prior"):
                    node[last] = prior.get("prior_value")
                else:
                    node.pop(last, None)
        _prune(data)
        state = None
    before = settings.raw if settings.exists else None
    after = settings.render(data)
    if action == "remove" and not data and prior.get("created"):
        after = None                            # laya-guard created the file and nothing else is in it
    return SandboxPlan(action, agent, [Artifact(path, before, after)], notes=list(extra_notes), state=state,
                       repaired=settings.repaired)


def _prune(node):
    """Drop the empty objects and lists our removal left behind (only below the top)."""
    if isinstance(node, dict):
        for k in list(node):
            _prune(node[k])
            if isinstance(node[k], (dict, list)) and not node[k]:
                del node[k]


# --- Claude Code ---------------------------------------------------------------------------------------------------

def _claude_path(entry: Entry) -> str:
    # sandbox path syntax: ~/x is home, /x absolute; a bare path in user settings would mean ~/.claude/x
    return "/**/" + entry.path if entry.kind == "anywhere" else entry.path


def _claude(action, agent, scope, home, project_dir, pol):
    path = scope_path(SPECS["claude-code"], scope, home, project_dir)
    settings = load_settings(path)
    deny_read = [_claude_path(e) for e in pol.entries if e.read]
    deny_write = [_claude_path(e) for e in pol.entries if e.write
                  and not (e.glob or e.kind == "anywhere") or (e.write and sys.platform == "darwin")]
    notes = ["Claude Code runs a sandboxed Bash command without asking (sandbox.autoAllowBashIfSandboxed). "
             "laya-guard's hook still judges each one first",
             "the sandbox covers Bash and what it starts; Read/Edit/Write stay with the hook (`install --files`)"]
    if sys.platform != "darwin":
        notes.append("on Linux the sandbox needs bubblewrap and socat; globs in denyWrite are macOS-only, so "
                     "**/ write rules are left to the hook")
    return _json_plan(action, agent, settings, (("sandbox", "enabled"), True),
                      {("sandbox", "filesystem", "denyRead"): deny_read,
                       ("sandbox", "filesystem", "denyWrite"): deny_write}, notes)


# --- Copilot CLI ---------------------------------------------------------------------------------------------------

def _copilot_home(home: Path) -> Path:
    env = os.environ.get("COPILOT_HOME")
    return Path(env).expanduser() if env else home / ".copilot"


def _copilot(action, agent, scope, home, project_dir, pol):
    if scope != "user":
        raise InstallError("Copilot CLI only takes sandbox settings from the user file (~/.copilot/settings.json)")
    path = _copilot_home(home) / "settings.json"
    settings = load_settings(path)
    denied, readonly, notes = [], [], []
    for e in pol.entries:
        if e.kind == "anywhere" or e.glob:
            notes.append(f"left to the hook: {_claude_path(e)} (Copilot's sandbox takes plain paths only)")
            continue
        p = _expand(e, home)
        if not os.path.lexists(p):
            continue            # Copilot creates a missing denied path as a directory before every command
        (denied if e.read else readonly).append(p)
    notes += ["Copilot runs laya-guard's hook inside its sandbox. On macOS that sandbox can't reach localhost "
              "(sandbox.userPolicy.network.allowLocalNetwork), so the hook may fall back to the offline check",
              "only paths that exist now are listed; run this again after creating one"]
    return _json_plan(action, agent, settings, (("sandbox", "enabled"), True),
                      {("sandbox", "userPolicy", "filesystem", "deniedPaths"): denied,
                       ("sandbox", "userPolicy", "filesystem", "readonlyPaths"): readonly}, notes)


# --- Cursor --------------------------------------------------------------------------------------------------------

def _cursor(action, agent, scope, home, project_dir, pol):
    if scope != "user":
        raise InstallError("Cursor's CLI sandbox setting lives in ~/.cursor/cli-config.json (user scope)")
    path = home / ".cursor" / "cli-config.json"
    settings = load_settings(path, lenient=True)
    notes = ["Cursor's sandbox has no way to deny reading a path outside a git repository, so the protected "
             "paths stay with the hook; this only turns the sandbox on (writes outside the project, network)",
             "this is cursor-agent's setting. In the Cursor app: Settings > Agents > Approvals & Execution, "
             "choose Auto-review"]
    return _json_plan(action, agent, settings, (("sandbox", "mode"), "enabled"), {}, notes)


# --- Codex: a permissions profile in config.toml ---------------------------------------------------------------------

CODEX_TOP_BEGIN = "# >>> laya-guard sandbox (managed by `laya-guard sandbox codex`; `--remove` takes it out) >>>"
CODEX_TOP_END = "# <<< laya-guard sandbox (top) <<<"
CODEX_BEGIN = "# >>> laya-guard sandbox profile (managed by `laya-guard sandbox codex`) >>>"
CODEX_END = "# <<< laya-guard sandbox profile <<<"


def _span(text: str, begin: str, end: str):
    a = text.find(begin)
    if a < 0:
        return None
    b = text.find(end, a)
    if b < 0:
        return None
    b = text.find("\n", b)
    return a, (len(text) if b < 0 else b + 1)


def _cut(text: str, begin: str, end: str) -> str:
    span = _span(text, begin, end)
    if not span:
        return text
    head, tail = text[:span[0]], text[span[1]:]
    if head.endswith("\n\n") and (not tail or tail.startswith("\n")):
        head = head[:-1]
    if not head and tail.startswith("\n"):
        tail = tail[1:]
    return head + tail


def _codex_profile(pol: Policy, notes: list | None = None) -> str:
    plain, anywhere = [], []
    for e in pol.entries:
        mode = "deny" if e.read else "read"         # "deny" stops reading and writing, "read" only writing
        if e.kind == "anywhere" and mode == "read":
            if notes is not None:                   # Codex: a glob only takes "deny"
                notes.append(f"left to the hook: **/{e.path} (Codex takes only \"deny\" for a glob, not read-only)")
            continue
        (anywhere if e.kind == "anywhere" else plain).append((e.path if e.kind != "anywhere" else "**/" + e.path, mode))
    lines = [CODEX_BEGIN, f"[permissions.{PROFILE}]",
             'description = "Codex workspace sandbox, plus the paths protect.yaml protects (laya-guard)"',
             'extends = ":workspace"', "", f"[permissions.{PROFILE}.filesystem]"]
    lines += [f"{json.dumps(p)} = {json.dumps(m)}" for p, m in plain]
    if anywhere:
        lines += ["", f'[permissions.{PROFILE}.filesystem.":workspace_roots"]']
        lines += [f"{json.dumps(p)} = {json.dumps(m)}" for p, m in anywhere]
    lines.append(CODEX_END)
    return "\n".join(lines) + "\n"


def _codex(action, agent, scope, home, project_dir, pol):
    path = scope_path(SPECS["codex"], scope, home, project_dir).with_name("config.toml")
    before = path.read_text(encoding="utf-8") if path.is_file() else None
    text = before or ""
    if text:
        _toml_check(text, path)
    bare = _cut(_cut(text, CODEX_TOP_BEGIN, CODEX_TOP_END), CODEX_BEGIN, CODEX_END)
    notes = []
    if action == "remove":
        after = bare if bare.strip() else None if before is not None and before != bare else before
        return SandboxPlan(action, agent, [Artifact(path, before, after)], state=None)
    if re.search(r"^\s*default_permissions\s*=", bare, re.M):
        raise InstallError(f"{path} already chooses a permissions profile (default_permissions); laya-guard won't "
                           f"replace it. Add the paths to that profile by hand, or remove the line and run this again")
    if re.search(r"^\s*sandbox_mode\s*=", bare, re.M):
        raise InstallError(f"{path} sets sandbox_mode, and while it does Codex ignores permissions profiles "
                           f"(default_permissions). Remove sandbox_mode (or the `-s/--sandbox` flag from your alias), "
                           f"then run this again")
    top = "\n".join([CODEX_TOP_BEGIN, f'default_permissions = "{PROFILE}"', CODEX_TOP_END]) + "\n"
    # default_permissions is a top-level key, so it goes before the first table; the profile's tables go last
    body = bare if not bare or bare.endswith("\n") else bare + "\n"
    after = top + ("\n" + body.strip("\n") + "\n" if body.strip() else "") + "\n" + _codex_profile(pol, notes)
    _toml_check(after, path)
    notes += ["the profile extends Codex's :workspace sandbox: writes stay inside the project (and temp) as with "
              "sandbox_mode = \"workspace-write\"; a path marked \"deny\" can't be read or written",
              "don't pass -s/--sandbox: Codex ignores the profile while sandbox_mode is set",
              "**/ globs apply under the workspace roots only; on Linux and Windows Codex resolves them when it "
              "starts, so a file created later isn't covered"]
    if scope == "project":
        notes.append("Codex reads a project's .codex/config.toml only once you trust the project")
    return SandboxPlan(action, agent, [Artifact(path, before, after)], notes=notes, state={"agent": agent,
                       "file": str(path)})


# --- Gemini CLI / Qwen Code: a Seatbelt profile next to the project ------------------------------------------------

_SEATBELT_HEADER = ";; laya-guard: the paths protect.yaml protects, denied on top of {base}. Written by " \
                   "`laya-guard sandbox {agent}`; `--remove` deletes this file.\n"
_PRIVATE = ("/etc/", "/tmp/", "/var/")


def _package_root(binary: str | None, name: str) -> Path | None:
    if not binary:
        return None
    p = Path(binary).resolve()
    for parent in p.parents:
        pkg = parent / "package.json"
        if pkg.is_file():
            try:
                if json.loads(pkg.read_text(encoding="utf-8")).get("name") == name:
                    return parent
            except (OSError, ValueError):
                pass
    return None


def base_profile(agent: str) -> tuple[str, Path]:
    """The installed CLI's own default Seatbelt profile (permissive-open): ours is that plus the denies."""
    binary, name = (("gemini", "@google/gemini-cli") if agent == "gemini-cli" else ("qwen", "@qwen-code/qwen-code"))
    override = os.environ.get("LAYA_GUARD_SEATBELT_BASE")
    if override:
        return Path(override).read_text(encoding="utf-8"), Path(override)
    root = _package_root(shutil.which(binary), name)
    found = sorted(root.rglob("sandbox-macos-permissive-open.sb")) if root else []
    if not found:
        raise InstallError(f"couldn't find {binary}'s sandbox-macos-permissive-open.sb (is `{binary}` installed and "
                           f"on PATH?); laya-guard builds its profile on top of that one")
    return found[0].read_text(encoding="utf-8"), found[0]


def _sbpl_path(entry: Entry) -> str:
    def quote(s: str) -> str:
        return json.dumps(s)
    if entry.kind == "anywhere":
        tail = entry.path
        rx = re.escape(tail).replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
        return f'(regex #"(^|/){rx}(/|$)")'
    if entry.glob:
        return ""
    if entry.kind == "home":
        return f'(subpath (string-append (param "HOME_DIR") {quote(entry.path[1:])}))'
    p = entry.path
    if p.startswith(_PRIVATE) or p in ("/etc", "/tmp", "/var"):
        p = "/private" + p
    return f"(subpath {quote(p)})"


def seatbelt_profile(base: str, base_name: str, agent: str, pol: Policy) -> str:
    read = [s for s in (_sbpl_path(e) for e in pol.entries if e.read) if s]
    write = [s for s in (_sbpl_path(e) for e in pol.entries if e.write) if s]
    out = [_SEATBELT_HEADER.format(base=base_name, agent=agent), base.rstrip("\n"), "",
           ";; ---- laya-guard: later rules win over the ones above ----"]
    if read:
        out += ["(deny file-read*", *[f"    {s}" for s in read], ")"]
    if write:
        out += ["(deny file-write*", *[f"    {s}" for s in write], ")"]
    return "\n".join(out) + "\n"


def _env_text(before: str | None, remove: bool) -> str | None:
    lines = [ln for ln in (before or "").splitlines() if ln.strip() != f"SEATBELT_PROFILE={PROFILE}"]
    if not remove:
        if any(ln.startswith("SEATBELT_PROFILE=") for ln in lines):
            raise InstallError("the project's settings .env already sets SEATBELT_PROFILE; laya-guard won't replace it")
        lines.append(f"SEATBELT_PROFILE={PROFILE}")
    text = "\n".join(lines) + "\n" if lines else None
    return text if text and text.strip() else None


def _gemini(action, agent, scope, home, project_dir, pol):
    if scope != "project":
        raise InstallError(f"{agent} only looks for a custom sandbox profile inside the project it runs in "
                           f"(.gemini/ or .qwen/ under the current directory): use --scope project")
    folder = project_dir / (".gemini" if agent == "gemini-cli" else ".qwen")
    sb = folder / f"sandbox-macos-{PROFILE}.sb"
    env = folder / ".env"
    settings = load_settings(folder / "settings.json")
    sb_before = sb.read_text(encoding="utf-8") if sb.is_file() else None
    env_before = env.read_text(encoding="utf-8") if env.is_file() else None
    if action == "remove":
        plan = _json_plan("remove", agent, settings, (("tools", "sandbox"), "sandbox-exec"), {})
        plan.artifacts += [Artifact(sb, sb_before, None), Artifact(env, env_before, _env_text(env_before, True))]
        return plan
    if sys.platform != "darwin":
        raise InstallError(f"{agent}'s Seatbelt profiles are macOS-only. With docker/podman its sandbox mounts only "
                           f"the project, so ~/.ssh and ~/.aws aren't there to read")
    base, base_path = base_profile(agent)
    plan = _json_plan("apply", agent, settings, (("tools", "sandbox"), "sandbox-exec"), {})
    plan.artifacts += [Artifact(sb, sb_before, seatbelt_profile(base, base_path.name, agent, pol)),
                       Artifact(env, env_before, _env_text(env_before, False))]
    plan.notes += [f"{agent} looks for the profile in .{'gemini' if agent == 'gemini-cli' else 'qwen'}/ under the "
                   "directory it starts in: start it from the project root (each project needs its own)",
                   f"the profile is rebuilt from {base_path.name} of the installed CLI; run this again after an upgrade",
                   "the CLI, its hooks and laya-guard's hook all run inside this sandbox",
                   "a shell `SEATBELT_PROFILE` or a `.env` found before this one overrides the choice"]
    if agent == "gemini-cli":
        plan.notes.append("Gemini CLI 0.20 needs tools.enableHooks for laya-guard's hook (`laya-guard install "
                          "gemini-cli` sets it)")
    return plan


# --- laya-guard run: an agent without a sandbox, inside srt ----------------------------------------------------------

# srt can't leave the network open, so these are the hosts the common agents and package managers need.
DEFAULT_DOMAINS = (
    "api.anthropic.com", "claude.ai", "platform.claude.com", "api.openai.com", "chatgpt.com", "auth.openai.com",
    "generativelanguage.googleapis.com", "oauth2.googleapis.com", "openrouter.ai", "opencode.ai", "models.dev",
    "ampcode.com", "kilocode.ai", "api.github.com", "github.com", "objects.githubusercontent.com",
    "registry.npmjs.org", "pypi.org", "files.pythonhosted.org",
)


def srt_settings(pol: Policy, home: Path, cwd: Path, allow_write=(), allow_domains=(), agent: str = "") -> dict:
    deny_read, deny_write = [], []
    for e in pol.entries:
        if e.kind == "anywhere":
            p = str(cwd / "**" / e.path)            # srt has no "at any depth anywhere"; under the project it can
        else:
            p = _expand(e, home)
        if sys.platform != "darwin" and (e.glob or e.kind == "anywhere") and e.write and not e.read:
            continue                                # srt takes globs in write lists on macOS only
        (deny_read if e.read else deny_write).append(p)
    # Not all of ~/.config: from there a process could rewrite laya-guard's own rules or drop a file that runs at
    # every login (~/.config/autostart, ~/.config/systemd/user). Only the agent's own folder, and never laya-guard's.
    name = Path(agent).name
    own = [str(home / ".config" / name)] if name and name not in (".", "..", "laya-guard") else []
    writable = [str(cwd), tempfile.gettempdir(), "/tmp", str(home / ".cache"), str(home / ".local" / "share"),
                str(home / ".local" / "state"), *own, *[str(Path(p).expanduser()) for p in allow_write]]
    deny_write.append(str(fileaccess.config_dir()))
    return {"network": {"allowedDomains": sorted(set(DEFAULT_DOMAINS) | set(allow_domains)), "deniedDomains": []},
            "filesystem": {"denyRead": deny_read, "allowRead": [], "allowWrite": writable,
                           "denyWrite": [p for p in deny_write if p not in deny_read] + deny_read},
            "allowPty": True}


def find_srt() -> str | None:
    return os.environ.get("LAYA_GUARD_SRT") or shutil.which("srt")


def run(argv: list[str], allow_write=(), allow_domains=(), rules=None, home: Path | None = None,
        cwd: Path | None = None, dry_run: bool = False) -> int:
    srt = find_srt()
    if not srt:
        raise InstallError("`srt` isn't installed: npm install -g @anthropic-ai/sandbox-runtime "
                           "(or point LAYA_GUARD_SRT at it)")
    if not argv:
        raise InstallError("nothing to run: laya-guard run -- <agent> [args]")
    home, cwd = home or Path.home(), cwd or Path.cwd()
    settings = srt_settings(policy(rules), home, cwd, allow_write, allow_domains, agent=argv[0])
    fd, cfg = tempfile.mkstemp(prefix="laya-guard-srt-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2)
    if dry_run:
        print(json.dumps(settings, indent=2))
        os.unlink(cfg)
        return 0
    import subprocess
    try:
        return subprocess.call([srt, "--settings", cfg, "--", *argv])     # `--`: the agent's flags aren't srt's
    finally:
        try:
            os.unlink(cfg)
        except OSError:
            pass
