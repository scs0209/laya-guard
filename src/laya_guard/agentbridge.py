"""The bridge to the coding agent you already have: run it once, headless and with every tool switched off, and take
its JSON answer. It is how the AI scenario engine gets its decisions without a hosted API, an API key or a model of
laya-guard's own.

What this is, exactly:

  * An adapter runs the agent's own command line as a child process of the server, once per decision (nothing stays
    running), in an empty temporary directory, with the prompt on stdin.
  * The agent is started so that it cannot act: built-in tools off, no settings or hooks, no MCP servers, no slash
    commands or skills, no saved session. It can only answer in text. What it answers is data: the engine checks it
    against a fixed schema and only ever applies it to a virtual world.
  * The agent talks to its own provider the way it always does, under your own login. laya-guard adds nothing from your
    machine to what it sends (the scenario you typed, the virtual files and the verdicts of the run), but the agent
    adds its own context to every request (for Claude Code: account details and any organisation instructions; your own
    CLAUDE.md and memory files are switched off for these calls), so the page asks for your consent before the first use.

Only Claude Code is supported, because it is the one agent with a switch that turns all tools off. The others (Gemini
CLI, Cursor, OpenCode, ...) can read or run things in their working directory unless told otherwise, and there is no
flag to make that impossible, so they are not wired in. `register()` is where another adapter would go.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

# What the child is started with on top of your environment: no hidden reasoning (a JSON decision needs none: about twice
# as fast), and no CLAUDE.md or memory files (yours would be sent with every request, and they have nothing to do with a scenario).
CHILD_ENV = {"MAX_THINKING_TOKENS": "0", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}

DEFAULT_TIMEOUT = 120.0           # seconds per decision
DEFAULT_BUDGET_USD = 0.5          # the agent's own spending cap for one decision (Claude Code's --max-budget-usd)


class AgentError(Exception):
    """The agent couldn't give an answer. `kind` says why, `message` is fit to show."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind, self.message = kind, message


@dataclass
class Completion:
    text: str                                   # what the agent said
    data: object | None = None                  # its structured answer, when the agent returned one
    cost_usd: float | None = None
    duration_ms: int | None = None
    model: str | None = None


@dataclass
class AgentInfo:
    id: str
    name: str
    found: bool
    path: str = ""
    version: str = ""
    logged_in: bool | None = None               # None: can't tell without asking it
    detail: str = ""
    models: list = field(default_factory=list)  # [{"id", "label"}]: choices the page may offer
    inert: str = ""                             # how it is kept from acting


def _run(argv: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None


class ClaudeCode:
    id = "claude-code"
    name = "Claude Code"
    inert = ("Run with --tools \"\" (no built-in tools), no settings or hooks, no MCP servers, no skills and no saved "
             "session, in an empty temporary directory, with your CLAUDE.md and memory files switched off: it can only answer in text.")
    models = [{"id": "", "label": "Claude Code's default"}, {"id": "haiku", "label": "Fast and cheap (haiku)"},
              {"id": "sonnet", "label": "Balanced (sonnet)"}]

    def binary(self) -> str | None:
        override = os.environ.get("LAYA_GUARD_CLAUDE_BIN", "")
        found = override if override and os.path.isfile(override) and os.access(override, os.X_OK) else shutil.which("claude")
        return found or None

    def detect(self) -> AgentInfo:
        path = self.binary()
        if not path:
            return AgentInfo(self.id, self.name, False, detail="`claude` isn't on PATH. Install Claude Code and log in, then reload.",
                             models=self.models, inert=self.inert)
        version = _run([path, "--version"], 10.0)
        status = _run([path, "auth", "status", "--json"], 10.0)
        logged_in: bool | None = None
        if status is not None and status.returncode == 0:
            try:
                logged_in = bool(json.loads(status.stdout).get("loggedIn"))      # only this flag is read: never the account details
            except (ValueError, AttributeError):
                logged_in = None
        detail = "" if logged_in is not False else "Claude Code isn't logged in. Run `claude` once in a terminal to log in."
        return AgentInfo(self.id, self.name, True, path, (version.stdout.strip() if version and version.returncode == 0 else ""),
                         logged_in, detail, self.models, self.inert)

    def argv(self, system: str, schema: dict | None, model: str | None, budget: float) -> list[str]:
        argv = [self.binary() or "claude", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
                "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
                "--system-prompt", system, "--max-budget-usd", f"{budget:g}"]
        if model:
            argv += ["--model", model]
        if schema is not None:
            argv += ["--json-schema", json.dumps(schema)]
        return argv

    def complete(self, system: str, prompt: str, *, schema: dict | None = None, model: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT, cancel: threading.Event | None = None,
                 budget: float = DEFAULT_BUDGET_USD) -> Completion:
        if not self.binary():
            raise AgentError("not-installed", "Claude Code isn't installed (`claude` isn't on PATH).")
        prompt = prompt.encode("utf-8", "replace").decode("utf-8")        # a lone surrogate can't be written to a pipe
        with tempfile.TemporaryDirectory(prefix="laya-guard-agent-") as cwd:
            try:
                proc = subprocess.Popen(self.argv(system, schema, model, budget), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, cwd=cwd, text=True, start_new_session=True,
                                        env={**os.environ, **CHILD_ENV})
            except OSError as exc:
                raise AgentError("failed", f"couldn't start Claude Code: {exc.strerror or exc}")
            deadline, first, out, err = time.monotonic() + timeout, True, "", ""
            try:
                while True:
                    try:
                        out, err = proc.communicate(input=prompt if first else None, timeout=0.2)
                        break
                    except subprocess.TimeoutExpired:
                        first = False
                        if cancel is not None and cancel.is_set():
                            raise AgentError("cancelled", "stopped")
                        if time.monotonic() > deadline:
                            raise AgentError("timeout", f"Claude Code didn't answer within {timeout:g} seconds.")
            finally:
                if proc.poll() is None:                   # whatever ended the wait (a timeout, a stop, an error), the agent doesn't outlive it
                    _kill(proc)
        if proc.returncode != 0 and not out.strip():
            raise AgentError("failed", _tail(err) or f"Claude Code exited with status {proc.returncode}.")
        return self.parse(out, err)

    def parse(self, out: str, err: str = "") -> Completion:
        try:
            reply = json.loads(out)
        except ValueError:
            raise AgentError("bad-output", "Claude Code's output wasn't JSON: " + _tail(out or err))
        if not isinstance(reply, dict):
            raise AgentError("bad-output", "Claude Code's output wasn't an object.")
        if reply.get("is_error"):
            message = str(reply.get("result") or reply.get("subtype") or "an error")
            kind = "not-logged-in" if "log" in message.lower() and "in" in message.lower() else "failed"
            raise AgentError(kind, f"Claude Code reported: {message}"[:400])
        usage = reply.get("modelUsage")
        return Completion(text=str(reply.get("result") or ""), data=reply.get("structured_output"),
                          cost_usd=reply.get("total_cost_usd"), duration_ms=reply.get("duration_ms"),
                          model=next(iter(usage), None) if isinstance(usage, dict) and usage else None)


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)               # its own session, so the agent's children go with it
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
    try:
        proc.communicate(timeout=3)
    except Exception:
        pass


def _tail(text: str, n: int = 300) -> str:
    text = (text or "").strip()
    return text[-n:]


# --- the registry -------------------------------------------------------------------------------------------------

_ADAPTERS: dict[str, object] = {ClaudeCode.id: ClaudeCode()}
_cache: dict[str, tuple[float, AgentInfo]] = {}
CACHE_SECONDS = 30.0


def register(adapter) -> None:
    """Add an adapter (anything with `id`, `name`, `detect()` and `complete()`)."""
    _ADAPTERS[adapter.id] = adapter
    _cache.clear()


def get(agent_id: str):
    return _ADAPTERS.get(agent_id)


def detect_all(fresh: bool = False) -> list[AgentInfo]:
    """What is installed and usable. Cached for a short while: asking costs a couple of processes per agent."""
    now, out = time.monotonic(), []
    for agent_id, adapter in _ADAPTERS.items():
        hit = _cache.get(agent_id)
        if hit is None or fresh or now - hit[0] > CACHE_SECONDS:
            hit = (now, adapter.detect())
            _cache[agent_id] = hit
        out.append(hit[1])
    return out
