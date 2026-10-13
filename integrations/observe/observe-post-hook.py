#!/usr/bin/env python3
"""laya-guard observer (Claude Code PostToolUse hook): record that a tool call actually ran.

Only the tool_use_id is stored, never the tool output (it may contain secrets). Paired with
observe.log by id: a call that was `review` and ran was approved by the user (or auto-approved);
a call with no record here was denied or blocked. observe-label.py shows this as a hint only;
it is never turned into a label automatically. Always exits 0.
"""
import json
import sys
import time

from observe_common import OUTCOME_LOG, append_jsonl


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        append_jsonl(OUTCOME_LOG, {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "id": payload.get("tool_use_id"),
            "tool": payload.get("tool_name"),
        })
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
