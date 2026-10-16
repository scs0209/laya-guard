"""A stand-in for an ACP-speaking agent (Codex through `codex-acp`, `gemini --acp`), so the ACP bridge is tested
through its real code path (a child process, NDJSON JSON-RPC over stdio) without calling any model.

`install(dir, replies=None, **config)` writes an executable `acp-fake` and returns the environment to put in front of
the test: LAYA_GUARD_CODEX_ACP_BIN (or _GEMINI_BIN) and FAKE_ACP_CFG. Every message received is appended to `log` so a
test can see exactly what the client sent; every reply the fake sent back is in `log + ".out"`.

A reply is a string (answered as an `agent_message_chunk` before the turn ends), {"_permission": True} (asks
`session/request_permission` once and records the client's answer), or {"_fs": "fs/read_text_file"} (calls a method
the client never advertised, to check it is refused, and records the answer). `stop_reason` sets the turn's end.
"""
import json
import os
import stat
import sys
from pathlib import Path

SCRIPT = r'''#!{python}
import json, os, sys, time
cfg = json.load(open(os.environ["FAKE_ACP_CFG"]))
log = cfg["log"]
seq = [0]
with open(log + ".pid", "a") as fh:
    fh.write(str(os.getpid()) + "\n")

def record(direction, message):
    with open(log if direction == "in" else log + ".out", "a") as fh:
        fh.write(json.dumps(message) + "\n")

def send(message):
    record("out", message)
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()

def notify(session_id, update):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": update}})

def reply(call_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": call_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    send(message)

def answer_request(message, result=None, error=None):
    if isinstance(message.get("id"), (int, str)):
        reply(message["id"], result, error)

def read_client_line():
    line = sys.stdin.readline()
    return line.strip() if line else ""

def await_answer(want_id):
    while True:
        line = read_client_line()
        if not line:
            return None
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict):
            record("in", message)
            if message.get("id") == want_id:
                return message

counter = log + ".n"
n = int(open(counter).read()) if os.path.exists(counter) else 0
if cfg.get("garbage"):
    sys.stdout.write(cfg["garbage"] + "\n")
    sys.stdout.flush()
if cfg.get("stderr"):
    sys.stderr.write(cfg["stderr"])
if cfg.get("exit_code"):
    sys.exit(cfg.get("exit_code"))
replies = cfg.get("replies", ["{}"])
session = {"id": None}

while True:
    line = read_client_line()
    if not line:
        break                                           # EOF: the client went away; exit quietly
    try:
        message = json.loads(line)
    except ValueError:
        continue
    if not isinstance(message, dict):
        continue
    record("in", message)
    method = message.get("method")
    if method == "initialize":
        answer_request(message, {"protocolVersion": cfg.get("protocol_version", 1),
                                 "agentCapabilities": cfg.get("capabilities", {}),
                                 "agentInfo": {"name": "fake-acp", "version": "0.0"},
                                 "authMethods": cfg.get("auth_methods", [])})
    elif method == "session/new":
        if cfg.get("error_on") == "session/new":
            answer_request(message, error={"code": -32000, "message": cfg.get("error", "not logged in")})
            continue
        session["id"] = "sess-test"
        answer_request(message, {"sessionId": session["id"]})
    elif method == "session/prompt":
        if cfg.get("sleep"):
            time.sleep(cfg["sleep"])
        if cfg.get("error_on") == "session/prompt":
            answer_request(message, error={"code": -32000, "message": cfg.get("error", "not logged in")})
            continue
        say = replies[min(n, len(replies) - 1)]
        n += 1
        open(counter, "w").write(str(n))
        for part in (say if isinstance(say, list) else [say]):
            if isinstance(part, dict) and part.get("_permission"):
                seq[0] += 1
                want = "perm-%d" % seq[0]
                send({"jsonrpc": "2.0", "id": want, "method": "session/request_permission",
                      "params": {"sessionId": session["id"],
                                 "toolCall": {"toolCallId": "call-1", "title": "rm -rf /", "kind": "other"},
                                 "options": [{"optionId": "allow", "name": "Allow", "kind": "allow"}]}})
                await_answer(want)
            elif isinstance(part, dict) and part.get("_fs"):
                seq[0] += 1
                want = "fs-%d" % seq[0]
                send({"jsonrpc": "2.0", "id": want, "method": part["_fs"],
                      "params": {"sessionId": session["id"], "path": "/etc/passwd"}})
                await_answer(want)
            else:
                notify(session["id"], {"sessionUpdate": "agent_message_chunk",
                                       "content": {"type": "text", "text": part if isinstance(part, str) else json.dumps(part)}})
        if cfg.get("cost") is not None:
            notify(session["id"], {"sessionUpdate": "usage_update", "used": 10, "size": 100,
                                   "cost": {"amount": cfg["cost"], "currency": "USD"}})
        answer_request(message, {"stopReason": cfg.get("stop_reason", "end_turn")})
    elif method == "session/cancel":
        pass                                    # a notification: nothing to answer
    elif "id" in message and isinstance(message.get("id"), (int, str)):
        answer_request(message, error={"code": -32601, "message": "Method not found"})
'''


def install(directory, replies=None, **config):
    """Write the fake ACP agent into `directory`; returns {"env", "log", "bin", "calls", "sent", "set"}."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    binary, cfg_path, log = directory / "acp-fake", directory / "fake_acp.json", directory / "acp-calls.jsonl"
    binary.write_text(SCRIPT.replace("{python}", sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    cfg = {"replies": replies if replies is not None else ["{}"], "log": str(log), **config}
    cfg_path.write_text(json.dumps(cfg))
    for stale in (log, Path(str(log) + ".n"), Path(str(log) + ".out"), Path(str(log) + ".pid")):
        stale.unlink(missing_ok=True)

    def read(path):
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    return {"env": {"FAKE_ACP_CFG": str(cfg_path)}, "log": log, "bin": binary,
            "calls": lambda: read(log), "sent": lambda: read(Path(str(log) + ".out")),
            "set": lambda **kw: cfg_path.write_text(json.dumps({**json.loads(cfg_path.read_text()), **kw}))}
