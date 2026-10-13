"""Keep every test away from the real verdict log.

The server, the hooks and `laya-guard check --offline` all append to it, so a test that runs one of them
would otherwise write into the developer's own log. Tests that want a log of their own set LAYA_GUARD_LOG_PATH
themselves; everything else lands in a temporary file. Subprocesses inherit it.
"""
import os
import tempfile
from pathlib import Path

os.environ["LAYA_GUARD_LOG_PATH"] = str(Path(tempfile.mkdtemp(prefix="laya-guard-test-log-")) / "verdicts.jsonl")
