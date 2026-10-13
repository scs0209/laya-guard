#!/bin/bash
# The Claude Code hook itself ships inside the package (src/laya_guard/hooks/guard-hook.sh) so
# `laya-guard install claude-code` can register it after a plain `pip install`. This wrapper keeps
# the old path in a git checkout working: settings that point here keep running the same hook.
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/src/laya_guard/hooks/guard-hook.sh" "$@"
