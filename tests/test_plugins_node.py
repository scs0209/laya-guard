"""The TypeScript plugins (OpenCode, Kilo, Amp) and the installed Cursor hook, run for real.

The plugins are loaded by node itself (it strips the types), handed the call an agent would give them, and pointed at a
stub guard server or at a dead one with the `laya-guard` CLI behind it. Skipped where node can't run TypeScript.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEAD = "http://127.0.0.1:1/none"


def _node_runs_ts() -> bool:
    node = shutil.which("node")
    if not node:
        return False
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.ts"
        f.write_text("const x: number = 1;\nconsole.log(x);\n")
        try:
            r = subprocess.run([node, str(f)], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return r.returncode == 0 and r.stdout.strip() == "1"


needs_node = pytest.mark.skipif(not _node_runs_ts(), reason="needs a node that runs TypeScript directly (22.18+)")

PLUGINS = {
    "opencode": (ROOT / "integrations" / "opencode" / "laya-guard.ts", "opencode"),
    "kilo": (ROOT / "integrations" / "kilo" / "laya-guard.ts", "kilo"),
    "amp": (ROOT / "integrations" / "amp" / "laya-guard.ts", "amp"),
}

DRIVER = """
import { pathToFileURL } from "node:url";
const [, , kind, file, dir, mode] = process.argv;
const mod = await import(pathToFileURL(file).href);
const shell = "cat notes.txt > out.txt";
if (kind === "amp") {
  const handlers = {};
  mod.default({ on: (ev, fn) => { handlers[ev] = fn; }, helpers: {} });
  await handlers["tool.call"]({ tool: "Bash", input: { cmd: shell } });
} else {
  const hooks = kind === "opencode" ? await mod.LayaGuard({ directory: dir }) : await mod.default.server({ directory: dir });
  await hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: shell } });
  if (mode !== "shell-only") await hooks["tool.execute.before"]({ tool: "read" }, { args: { filePath: "/x/y.txt" } });
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory):
    path = tmp_path_factory.mktemp("driver") / "drive.mjs"
    path.write_text(DRIVER)
    return path


@pytest.fixture(scope="module")
def stub():
    bodies = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            bodies.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()))
            data = json.dumps({"action": "allow", "risk": 0.1, "policy_id": "general", "reason": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/guard", bodies
    srv.shutdown()


def _drive(driver, kind, url, tmp_path, mode="", **env):
    plugin = PLUGINS[kind][0]
    environ = dict(os.environ, LAYA_GUARD_URL=url, LAYA_GUARD_FAIL="open", LAYA_GUARD_MOCK="1", **env)
    return subprocess.run(["node", str(driver), kind, str(plugin), str(tmp_path), mode], capture_output=True, text=True,
                          env=environ, timeout=90)


@needs_node
@pytest.mark.parametrize("kind", PLUGINS)
def test_a_plugin_names_its_agent_and_sends_the_bare_shell_command(driver, stub, tmp_path, kind):
    url, bodies = stub
    bodies.clear()
    p = _drive(driver, kind, url, tmp_path)
    assert p.returncode == 0, p.stderr
    shell = bodies[0]
    assert shell["agent"] == PLUGINS[kind][1]
    assert shell["command"] == "cat notes.txt > out.txt"
    assert shell["tool_name"] == ("Bash" if kind == "amp" else "bash")
    if kind == "kilo":
        assert shell["tool_call"] != shell["command"]                                # still judged as the wrapper
    if kind != "amp":                                                                # Amp's plugin judges shell calls only
        read = bodies[1]
        assert read["agent"] == PLUGINS[kind][1] and read["tool_name"] == "read" and "command" not in read


@needs_node
@pytest.mark.parametrize("kind", PLUGINS)
def test_a_plugin_records_shell_paths_through_the_offline_fallback(driver, tmp_path, kind):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_cli = bin_dir / "laya-guard"
    stub_cli.write_text(f'#!/bin/sh\nPYTHONPATH="{ROOT / "src"}" exec "{sys.executable}" -m laya_guard.cli "$@"\n')
    stub_cli.chmod(0o755)
    log = tmp_path / "activity.jsonl"
    p = _drive(driver, kind, DEAD, tmp_path, mode="shell-only", LAYA_GUARD_BIN=str(stub_cli), HOME=str(tmp_path),
               LAYA_GUARD_FILE_ACTIVITY="1", LAYA_GUARD_FILE_ACTIVITY_PATH=str(log))
    assert p.returncode == 0, p.stderr
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(e["agent"], e["op"], e["path"], e["source"]) for e in events] == [
        (PLUGINS[kind][1], "read", "notes.txt", "shell"), (PLUGINS[kind][1], "write", "out.txt", "shell")]


# --- the installed Cursor hook is a bash script, not the Python file under integrations/ ---------------------------

CURSOR_SH = ROOT / "src" / "laya_guard" / "hooks" / "guard-cursor-hook.sh"


def test_the_installed_cursor_hook_names_its_agent_and_records_offline(tmp_path):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()))
            data = json.dumps({"action": "allow", "risk": 0.1, "policy_id": "general", "reason": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    payload = json.dumps({"command": "cat notes.txt > out.txt", "cwd": "/work"})
    try:
        base = dict(os.environ, LAYA_GUARD_FAIL="open", LAYA_GUARD_MOCK="1", HOME=str(tmp_path))
        subprocess.run(["bash", str(CURSOR_SH)], input=payload, capture_output=True, text=True, timeout=60,
                       env=dict(base, LAYA_GUARD_URL=f"http://127.0.0.1:{srv.server_port}/guard"))
    finally:
        srv.shutdown()
    body = seen[0]
    assert (body["agent"], body["tool_name"], body["command"], body["cwd"]) == (
        "cursor", "beforeShellExecution", "cat notes.txt > out.txt", "/work")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_cli = bin_dir / "laya-guard"
    stub_cli.write_text(f'#!/bin/sh\nPYTHONPATH="{ROOT / "src"}" exec "{sys.executable}" -m laya_guard.cli "$@"\n')
    stub_cli.chmod(0o755)
    log = tmp_path / "activity.jsonl"
    subprocess.run(["bash", str(CURSOR_SH)], input=payload, capture_output=True, text=True, timeout=60,
                   env=dict(base, LAYA_GUARD_URL=DEAD, LAYA_GUARD_BIN=str(stub_cli), LAYA_GUARD_FILE_ACTIVITY="1",
                            LAYA_GUARD_FILE_ACTIVITY_PATH=str(log)))
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(e["agent"], e["op"], e["path"], e["abs"], e["source"]) for e in events] == [
        ("cursor", "read", "notes.txt", "/work/notes.txt", "shell"), ("cursor", "write", "out.txt", "/work/out.txt", "shell")]
