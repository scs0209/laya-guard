"""Keep the laya-guard server running from login on (opt-in): a macOS LaunchAgent or a Linux systemd user unit.

Nothing here runs unless you ask for it with `laya-guard service install` (or the dashboard's "Start at login").
The hooks never depend on it: with the server down they use the offline check, and `laya-guard serve` by hand
works exactly as before. What the service adds is that the model is loaded and answering after a reboot or a
crash, so a hook doesn't quietly fall back to regexes only.

A plan is worked out first (the file's text before and after, nothing written), shown as a diff, and only then
applied. The files are generated, so unlike the agents' settings they aren't backed up when replaced or removed;
a file that wasn't written by this module is left alone unless `force` says otherwise, and is then backed up.
"""
from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .install import Artifact, InstallError, _config_home, _diff, cli_path, write_artifact

LABEL = "com.laya-guard.server"
UNIT = "laya-guard.service"
UNIT_MARKER = "# Managed by `laya-guard service install`; `laya-guard service uninstall` removes it."
RESTART_DELAY = 30            # seconds between starts: a crash loop or a busy port must not spin
DEFAULT_PORT = 8787
# what the server reads from its environment; a login service doesn't inherit the shell's, so these are copied in
ENV_KEYS = ("LAYA_GUARD_ALERT_CMD", "LAYA_GUARD_ALERT_COOLDOWN", "LAYA_GUARD_LOG", "LAYA_GUARD_LOG_PATH",
            "LAYA_GUARD_DEVICE")


class ServiceError(InstallError):
    """Something the user can fix or should know; the message says what."""


Runner = Callable[[list], "subprocess.CompletedProcess"]


def run_command(cmd: list) -> "subprocess.CompletedProcess":
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


# --- where things go ----------------------------------------------------------------------------

def platform_kind(platform: str | None = None) -> str:
    p = platform or sys.platform
    if p == "darwin":
        return "macos"
    if p.startswith("linux"):
        return "linux"
    raise ServiceError("starting at login is supported on macOS and Linux for now; on this system run "
                       "`laya-guard serve` yourself")


def how(kind: str) -> str:
    return "LaunchAgent" if kind == "macos" else "systemd user unit"


def unit_path(kind: str, home: Path) -> Path:
    if kind == "macos":
        return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    return _config_home(home) / "systemd" / "user" / UNIT


def log_file(kind: str, home: Path) -> Path | None:
    return home / "Library" / "Logs" / "laya-guard.log" if kind == "macos" else None


def _short(path: Path, home: Path) -> str:
    s = str(path)
    return "~" + s[len(str(home)):] if s.startswith(str(home) + os.sep) else s


def log_hint(kind: str, home: Path) -> str:
    f = log_file(kind, home)
    return _short(f, home) if f else f"journalctl --user -u {UNIT}"


# --- what gets written --------------------------------------------------------------------------

def server_args(bin_path: Path, host: str, port: int, warmup: bool) -> list[str]:
    args = [str(bin_path), "serve"]
    if host != "127.0.0.1":
        args += ["--host", host]
    if port != DEFAULT_PORT:
        args += ["--port", str(port)]
    if not warmup:
        args.append("--no-warmup")
    return args


def service_env(environ) -> dict[str, str]:
    """The settings of the shell that installs it, copied into the service. PATH only comes along with an alert
    command, which usually needs it (launchd's own PATH has no Homebrew)."""
    env = {k: environ[k] for k in ENV_KEYS if environ.get(k)}
    if env.get("LAYA_GUARD_ALERT_CMD") and environ.get("PATH"):
        env["PATH"] = environ["PATH"]
    return env


def render_plist(args: list[str], env: dict[str, str], log: Path) -> str:
    data = {"Label": LABEL, "ProgramArguments": args, "RunAtLoad": True,
            # restart after a crash (non-zero exit) but not after a clean stop
            "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": RESTART_DELAY,
            "StandardOutPath": str(log), "StandardErrorPath": str(log)}
    if env:
        data["EnvironmentVariables"] = env
    return plistlib.dumps(data).decode("utf-8")


def _unit_quote(text: str) -> str:
    """One word in a systemd unit: quoted, with the characters it treats specially escaped."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def render_unit(args: list[str], env: dict[str, str]) -> str:
    lines = [UNIT_MARKER, "[Unit]", "Description=laya-guard server (judges coding agents' tool calls)",
             "After=network.target", "", "[Service]", "ExecStart=" + " ".join(_unit_quote(a) for a in args),
             "Restart=on-failure", f"RestartSec={RESTART_DELAY}"]
    lines += [f"Environment={_unit_quote(f'{k}={v}')}" for k, v in sorted(env.items())]
    lines += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def is_ours(kind: str, text: str) -> bool:
    if kind == "macos":
        try:
            return plistlib.loads(text.encode("utf-8")).get("Label") == LABEL
        except Exception:
            return False
    return UNIT_MARKER in text


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ServiceError(f"can't read {path}: {exc}")


@dataclass
class ServicePlan:
    action: str                 # install | uninstall
    kind: str                   # macos | linux
    home: Path
    artifact: Artifact
    outcome: str                # install: added | updated | unchanged | manual; uninstall: removed | nothing | manual
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    existing: str = ""          # what was found at the path when it isn't ours
    ours: bool = True           # the file being replaced or removed was written by this module
    ran: list = field(default_factory=list)     # the commands apply_plan ran, for the report

    repaired = False            # (the CLI's shared apply step asks every plan)

    @property
    def artifacts(self) -> list:
        return [self.artifact]

    @property
    def path(self) -> Path:
        return self.artifact.path

    @property
    def changed(self) -> bool:
        return self.artifact.changed

    @property
    def diff(self) -> str:
        return _diff(self.artifact.path, self.artifact.before, self.artifact.after) if self.changed else ""

    @property
    def diff_id(self) -> str:
        a = self.artifact
        return hashlib.sha256(f"{a.path}\0{a.before}\0{a.after}".encode("utf-8")).hexdigest()[:16]

    @property
    def follow_up(self) -> str:
        if self.action != "install":
            return ""
        return ("Check it any time with `laya-guard service status`; it starts again at every login and after a crash."
                if self.outcome != "unchanged" else "")


def _install_notes(kind: str, home: Path, env: dict, warmup: bool, port: int, busy: bool) -> list[str]:
    notes = ["The server starts now and at every login, and starts again after a crash (30 seconds apart at most)."]
    notes.append("The model is loaded before it answers: about 1.4 GB of memory, about 5 seconds per start." if warmup
                 else "The model loads on the first call instead (about 5 seconds, then it stays): about 90 MB of "
                      "memory while idle.")
    if env:
        notes.append("Copied from this environment into the service: " + ", ".join(sorted(env)) + ".")
    notes.append(f"Its output goes to {log_hint(kind, home)}.")
    if busy:
        notes.append(f"Something already answers on port {port} (a server started by hand, probably). It keeps running; "
                     "the service checks every 30 seconds and takes over once the port is free.")
    return notes


def make_plan(action: str, home: Path, *, bin_override: str | None = None, host: str = "127.0.0.1",
              port: int = DEFAULT_PORT, warmup: bool = True, environ=None, platform: str | None = None,
              force: bool = False, port_busy: bool = False) -> ServicePlan:
    """Work out a change without writing anything. Raises ServiceError (an InstallError) with advice."""
    if action not in ("install", "uninstall"):
        raise ServiceError(f"unknown action {action!r}")
    kind = platform_kind(platform)
    path = unit_path(kind, home)
    before = _read(path)
    ours = before is None or is_ours(kind, before)
    mode = 0o600                          # the environment can hold a webhook URL: nobody else needs to read it
    if before is not None and not ours and not force:
        return ServicePlan(action, kind, home, Artifact(path, before, before, mode), "manual", ours=False,
                           existing=f"{path} exists but wasn't written by `laya-guard service install`")
    if action == "uninstall":
        if before is None:
            return ServicePlan(action, kind, home, Artifact(path, None, None, mode), "nothing")
        return ServicePlan(action, kind, home, Artifact(path, before, None, mode), "removed", ours=ours)
    if not 0 < port < 65536:
        raise ServiceError(f"{port} isn't a port")
    args = server_args(cli_path(bin_override), host, port, warmup)
    env = service_env(os.environ if environ is None else environ)
    after = render_plist(args, env, log_file(kind, home)) if kind == "macos" else render_unit(args, env)
    outcome = "added" if before is None else ("unchanged" if before == after else "updated")
    return ServicePlan("install", kind, home, Artifact(path, before, after, mode), outcome, args, env,
                       _install_notes(kind, home, env, warmup, port, port_busy) if outcome != "unchanged" else [],
                       ours=ours)


# --- applying -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Cmd:
    argv: tuple
    need: str | None = None     # a failing exit code raises ServiceError("<need> failed ..."); None: ignore it
    retries: int = 1            # `launchctl bootstrap` can fail for a moment while `bootout` is still finishing

    def __str__(self) -> str:
        return shlex.join(self.argv)


def _domain(uid: int) -> str:
    return f"gui/{uid}"


def _commands(plan: ServicePlan, uid: int) -> tuple[list[Cmd], list[Cmd], list[Cmd]]:
    """The service manager's side of a change: (before the file is written, after it, and the commands that stop
    or restart the server itself). The last group is the one a dashboard served by that very server must not wait
    for: stopping the server kills the request that asked for it."""
    target, path = f"{_domain(uid)}/{LABEL}", str(plan.path)
    if plan.kind == "macos":
        if plan.action == "install":
            return [], [Cmd(("launchctl", "enable", target))], [
                Cmd(("launchctl", "bootout", target)),                    # an older version, if one is loaded
                Cmd(("launchctl", "bootstrap", _domain(uid), path), "starting the LaunchAgent", 4)]
        return [], [], [Cmd(("launchctl", "bootout", target))]            # not loaded is fine
    systemctl = ("systemctl", "--user")
    if plan.action == "install":
        return [], [Cmd(systemctl + ("daemon-reload",), "reloading systemd"),
                    Cmd(systemctl + ("enable", UNIT), "enabling the service")], [
                Cmd(systemctl + ("restart", UNIT), "starting the service")]
    return [Cmd(systemctl + ("disable", UNIT))], [Cmd(systemctl + ("daemon-reload",))], [Cmd(systemctl + ("stop", UNIT))]


def preflight(kind: str, run: Runner | None = None) -> None:
    """Fail before anything is written when the service manager isn't there to use."""
    run = run or run_command
    if kind == "macos":
        if run(["launchctl", "version"]).returncode:
            raise ServiceError("`launchctl` isn't available, so a LaunchAgent can't be started from here")
    elif run(["systemctl", "--user", "show-environment"]).returncode:
        raise ServiceError("`systemctl --user` isn't available (no systemd user session), so a service can't be "
                           "started from here; run `laya-guard serve` from your own startup script instead")


def _script(commands: list[Cmd]) -> str:
    """The commands as one shell script that starts with a pause, so the page that asked gets its answer first."""
    lines = ["sleep 1"]
    for c in commands:
        line = shlex.join(c.argv)
        lines.append(f"for i in $(seq {c.retries}); do {line} && break; sleep 1; done" if c.retries > 1 else line)
    return "; ".join(lines)


def spawn_detached(script: str) -> None:
    """Run a script that outlives this process (its own session, so stopping the service doesn't take it along)."""
    subprocess.Popen(["sh", "-c", script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def apply_plan(plan: ServicePlan, run: Runner | None = None, uid: int | None = None,
               sleep: Callable[[float], None] | None = None, detach: bool = False,
               spawn: Callable[[str], None] | None = None) -> tuple[list[Path], list[str]]:
    """Write the file and tell the service manager. Returns (backups, the commands that were run).

    `detach`: leave the commands that stop or restart the server to a detached script a moment later, for a caller
    that may itself be the server being stopped. The answer then says the file is written, not that it is running."""
    run, spawn, sleep = run or run_command, spawn or spawn_detached, sleep or time.sleep   # looked up now, so a test can swap them
    if plan.outcome == "manual":
        raise ServiceError(plan.existing + "; pass force to replace it (it is backed up first)")
    if not plan.changed:
        return [], plan.ran                                              # already what it should be, or not there
    uid = os.getuid() if uid is None else uid
    ran = plan.ran

    def execute(c: Cmd) -> None:
        for n in range(c.retries):
            proc = run(list(c.argv))
            if not proc.returncode or n + 1 == c.retries:
                break
            sleep(0.5)
        ran.append(str(c))
        if c.need:
            _need(proc, c.need)

    if plan.action == "install":
        preflight(plan.kind, run)                                        # removing must work even without it
    before, after, later = _commands(plan, uid)
    for c in before:
        execute(c)
    if plan.kind == "macos" and plan.action == "install":
        log = log_file("macos", plan.home)
        if log:
            log.parent.mkdir(parents=True, exist_ok=True)
    backup = _write(plan)
    for c in after:
        execute(c)
    if detach and later:
        spawn(_script(later))
        ran.extend(f"{c} (a moment after this answer)" for c in later)
    else:
        for c in later:
            execute(c)
    return ([backup] if backup else []), ran


def _need(proc, what: str) -> None:
    if proc.returncode:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise ServiceError(f"{what} failed (exit {proc.returncode})" + (f": {detail[-1]}" if detail else ""))


def _write(plan: ServicePlan) -> Path | None:
    """Our own generated file is replaced without a backup; anything else is backed up first."""
    a = plan.artifact
    return write_artifact(a if not plan.ours else Artifact(a.path, None, a.after, a.mode))


# --- looking ------------------------------------------------------------------------------------

@dataclass
class ServiceStatus:
    kind: str
    path: Path
    state: str                  # not-installed | installed | broken | manual
    running: bool | None = None         # the service manager's view; None when it can't be asked
    detail: str = ""
    port: int = DEFAULT_PORT
    warmup: bool = True


def _parse_args(kind: str, text: str) -> list[str]:
    if kind == "macos":
        return list(plistlib.loads(text.encode("utf-8")).get("ProgramArguments", []))
    m = re.search(r"^ExecStart=(.*)$", text, re.M)
    return shlex.split(m.group(1)) if m else []


def status(home: Path, run: Runner | None = None, platform: str | None = None, uid: int | None = None) -> ServiceStatus:
    run = run or run_command
    kind = platform_kind(platform)
    path = unit_path(kind, home)
    text = _read(path)
    if text is None:
        return ServiceStatus(kind, path, "not-installed")
    if not is_ours(kind, text):
        return ServiceStatus(kind, path, "manual", detail="a file is there that `laya-guard service install` didn't write")
    try:
        args = _parse_args(kind, text)
    except Exception:
        args = []
    port = DEFAULT_PORT
    if "--port" in args and args.index("--port") + 1 < len(args):
        try:
            port = int(args[args.index("--port") + 1])
        except ValueError:
            pass
    st = ServiceStatus(kind, path, "installed", port=port, warmup="--no-warmup" not in args)
    if not args or not Path(args[0]).is_file():
        st.state, st.detail = "broken", f"{args[0] if args else 'the command'} is missing; install the service again"
    uid = os.getuid() if uid is None else uid
    if kind == "macos":
        proc = run(["launchctl", "print", f"{_domain(uid)}/{LABEL}"])
        if proc.returncode == 0:
            st.running = bool(re.search(r"^\s*pid = \d+", proc.stdout, re.M)) or bool(re.search(r"state = running", proc.stdout))
            if not st.running:
                code = re.search(r"last exit code = (\S+)", proc.stdout)
                st.detail = st.detail or "loaded, waiting for its next start" + (f" (last exit code {code.group(1)})" if code else "")
        elif proc.returncode != 127:
            st.running = False
            st.detail = st.detail or "installed but not loaded; it loads at your next login, or run `laya-guard service install`"
    else:
        proc = run(["systemctl", "--user", "is-active", UNIT])
        if proc.returncode != 127:
            word = (proc.stdout or "").strip()
            st.running = word == "active"
            if word and word != "active":
                st.detail = st.detail or f"systemd says it is {word}"
    return st


def port_answers(host: str, port: int) -> bool:
    """Does something answer /health there? (the server, when it is up)"""
    try:
        urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1)
        return True
    except Exception:
        return False
