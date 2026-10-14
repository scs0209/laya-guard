"""Keep every test away from the developer's own machine.

The log: the server, the hooks and `laya-guard check --offline` all append to the verdict log, so a test that runs
one of them would otherwise write into the developer's own log. Tests that want a log of their own set
LAYA_GUARD_LOG_PATH themselves; everything else lands in a temporary file. Subprocesses inherit it.

The service manager: no test may start, stop or query the developer's launchd / systemd (see below).

Model API keys: the developer's own are removed, so no test calls a real API or bills a real key.
"""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

os.environ["LAYA_GUARD_LOG_PATH"] = str(Path(tempfile.mkdtemp(prefix="laya-guard-test-log-")) / "verdicts.jsonl")
for _key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "CODEX_API_KEY"):
    os.environ.pop(_key, None)


@pytest.fixture(autouse=True)
def no_real_service_manager(monkeypatch):
    """Tests of the service module hand it a fake runner; anything that reaches the default one finds the service
    manager "not available", and nothing may start a detached script."""
    try:
        from laya_guard import service
    except ImportError:
        return

    def unavailable(cmd):
        return subprocess.CompletedProcess(cmd, 127, "", "no service manager in tests")

    def no_spawn(script):
        raise AssertionError(f"a test tried to start a detached script: {script}")

    monkeypatch.setattr(service, "run_command", unavailable)
    monkeypatch.setattr(service, "spawn_detached", no_spawn)
