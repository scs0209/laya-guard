"""A stand-in for the `claude` command line, so the agent bridge and the AI scenario engine are tested through their
real code path (a child process, argv, stdin, JSON on stdout) without calling any model.

`install(dir, replies)` writes an executable `claude` and returns the environment variables to put in front of the
server or the test: LAYA_GUARD_CLAUDE_BIN and FAKE_CLAUDE_CFG. Every call is appended to `log` (argv, the prompt it was
sent, its working directory) so a test can see exactly what the agent was asked.

A reply is a dict (answered as the structured output), a string (answered as plain text, to test fences and chatter),
or {"_error": "message"} (Claude Code's own error reply)."""
import json
import os
import stat
import sys
from pathlib import Path

SCRIPT = r'''#!{python}
import json, os, sys, time
cfg = json.load(open(os.environ["FAKE_CLAUDE_CFG"]))
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("9.9.9 (Fake Claude Code)"); sys.exit(0)
if args[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": cfg.get("logged_in", True), "authMethod": "fake", "email": "someone@example.com", "orgName": "Acme"}})); sys.exit(0)
prompt = sys.stdin.read()
with open(cfg["log"], "a") as fh:
    fh.write(json.dumps({{"argv": args, "prompt": prompt, "cwd": os.getcwd(), "thinking": os.environ.get("MAX_THINKING_TOKENS"), "env": {{k: os.environ.get(k) for k in ("CLAUDE_CODE_DISABLE_CLAUDE_MDS", "CLAUDE_CODE_DISABLE_AUTO_MEMORY")}}}}) + "\n")
with open(cfg["log"] + ".pid", "a") as fh:
    fh.write(str(os.getpid()) + "\n")
counter = cfg["log"] + ".n"
n = int(open(counter).read()) if os.path.exists(counter) else 0
open(counter, "w").write(str(n + 1))
if cfg.get("sleep"):
    time.sleep(cfg["sleep"])
if cfg.get("stderr"):
    sys.stderr.write(cfg["stderr"])
if cfg.get("exit_code"):
    sys.exit(cfg["exit_code"])
if cfg.get("raw") is not None:
    sys.stdout.write(cfg["raw"]); sys.exit(0)
replies = cfg["replies"]
reply = replies[min(n, len(replies) - 1)]
out = {{"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.001, "duration_ms": 12, "modelUsage": {{"fake-model": {{}}}}}}
if isinstance(reply, dict) and "_error" in reply:
    out.update(is_error=True, subtype="error", result=reply["_error"])
elif isinstance(reply, dict):
    out.update(result=json.dumps(reply), structured_output=reply)
else:
    out.update(result=str(reply))
print(json.dumps(out))
'''


def install(directory, replies=None, **config):
    """Write the fake `claude` into `directory`; returns {"env": {...}, "log": Path, "bin": Path, "calls": callable}."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    binary, cfg_path, log = directory / "claude", directory / "fake_claude.json", directory / "calls.jsonl"
    binary.write_text(SCRIPT.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    cfg = {"replies": replies if replies is not None else [{}], "log": str(log), **config}
    cfg_path.write_text(json.dumps(cfg))
    for stale in (log, Path(str(log) + ".n"), Path(str(log) + ".pid")):
        stale.unlink(missing_ok=True)

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return {"env": {"LAYA_GUARD_CLAUDE_BIN": str(binary), "FAKE_CLAUDE_CFG": str(cfg_path)}, "log": log, "bin": binary, "calls": calls,
            "set": lambda **kw: cfg_path.write_text(json.dumps({**json.loads(cfg_path.read_text()), **kw}))}
