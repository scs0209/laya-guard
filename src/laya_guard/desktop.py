"""Desktop computer use: what an agent clicks, opens or types on the screen itself (a computer-use MCP server).

Such a call names pixel coordinates, which say nothing about what is under them. So right before it runs, laya-guard
asks macOS what is there, through the Accessibility API (the `laya-guard-ax` helper, built by `laya-guard desktop
setup`): a Finder row or desktop icon gives its file's path, a button its name. A click on a file under a protected path
(protect.yaml) is reviewed or blocked like a Read of it; so is Return or cmd+O on one that is selected, and typing its
path (Go to Folder). A click on a button named like paying, deleting or sending is asked about. Secrets typed are
blocked. When the helper can't see the screen (not set up, no permission, another OS), a click is asked about.

The call shape is Anthropic's computer tool, which computer-use MCP servers copy: `action` (left_click, double_click,
key, type, ...) and `coordinate` [x, y], or one tool per action (`left_click` with `x`, `y`). Coordinates are taken as
screen points of the main display; LAYA_GUARD_AX_SCALE multiplies them for a server that sends screenshot pixels.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import browser
from .fileaccess import config_dir, judge_accesses
from .guard import Verdict, is_mock

SOURCE = Path(__file__).with_name("ax") / "axlookup.swift"
LOOKUP_TIMEOUT = 2.0
_RISK = {"allow": 0.05, "review": 0.55, "block": 0.92}

CLICKS = {"left_click", "right_click", "double_click", "triple_click", "middle_click", "click", "left_click_drag",
          "drag", "left_mouse_down", "left_mouse_up"}
READS = {"screenshot", "mouse_move", "move", "cursor_position", "scroll", "wait", "zoom", "hold_key"}
KEYS = {"key", "press_key", "keypress", "key_press", "hotkey"}
TYPES = {"type", "type_text"}
ACTIONS = CLICKS | READS | KEYS | TYPES
# Keys that open what is selected: Return/Enter, cmd+O, cmd+Down (Finder), space (Quick Look shows the content).
_OPENS = re.compile(r"^(?:return|enter|kp_enter|space|(?:cmd|command|super|meta)\+(?:o|down))$", re.IGNORECASE)
_PATHLIKE = re.compile(r"(?:^|\s)(~?/[^\s]+)")


def helper_path() -> Path:
    override = os.environ.get("LAYA_GUARD_AX", "")
    return Path(override).expanduser() if override else config_dir() / "bin" / "laya-guard-ax"


def build_helper() -> Path:
    """Compile the helper with swiftc (Xcode's command line tools) into the config folder; returns its path."""
    swiftc = shutil.which("swiftc")
    if sys.platform != "darwin" or swiftc is None:
        raise RuntimeError("the desktop helper needs macOS and swiftc (xcode-select --install)")
    out = helper_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([swiftc, "-O", str(SOURCE), "-o", str(out)], capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("swiftc failed: " + (r.stderr.strip().splitlines() or ["no output"])[-1])
    return out


def lookup(*args: str) -> dict | None:
    """What the helper says (`at X Y`, `pointer`, `selection`), or None when it can't say: not built, failed, too slow,
    or not allowed to read the screen."""
    exe = helper_path()
    if not exe.is_file():
        return None
    try:
        r = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=LOOKUP_TIMEOUT)
        data = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("trusted"):
        return None
    return data


# --- the call --------------------------------------------------------------------------------------------------------

def parse(tool_name: str | None, tool_input) -> tuple[str, dict] | None:
    """(action, input) for a desktop computer-use call, None for anything else. A browser tool is never one."""
    if not isinstance(tool_input, dict) or browser.is_browser_tool(tool_name):
        return None
    action = tool_input.get("action")
    if not isinstance(action, str):
        action = (tool_name or "").rsplit("__", 1)[-1]          # one tool per action: mcp__computer__left_click
    action = action.strip().lower()
    if action in ACTIONS:
        return action, tool_input
    # an action this check doesn't know, on a call that points somewhere on the screen: still a desktop call
    return (action, tool_input) if "coordinate" in tool_input or "start_coordinate" in tool_input else None


def _point(a: dict, key: str = "coordinate") -> tuple[float, float] | None:
    c = a.get(key)
    if isinstance(c, (list, tuple)) and len(c) == 2:
        x, y = c
    elif key == "coordinate" and "x" in a and "y" in a:
        x, y = a["x"], a["y"]
    else:
        return None
    try:
        scale = float(os.environ.get("LAYA_GUARD_AX_SCALE", "") or 1.0)
        return float(x) * scale, float(y) * scale
    except (TypeError, ValueError):
        return None


def _v(action: str, reason: str, policy_id: str = "desktop") -> Verdict:
    return Verdict(action=action, risk=_RISK[action], policy_id=policy_id, reason=reason, mock=is_mock(),
                   verdict_source="regex" if action != "allow" else "allowlist")


def _files(info: dict, cwd: str | None, what: str) -> Verdict | None:
    paths = [p for p in info.get("paths") or [] if isinstance(p, str) and p][:50]
    rule = judge_accesses([("read", p) for p in paths], cwd) if paths else None
    if rule is None:
        return None
    return _v(rule.action, f"{what} a protected file ({rule.reason})", "protected-path")


def _at(point, look) -> dict | None:
    return look("at", f"{point[0]:.0f}", f"{point[1]:.0f}") if point else look("pointer")


def _click(action: str, a: dict, cwd: str | None, look) -> Verdict:
    point = _point(a, "start_coordinate") if action in ("left_click_drag", "drag") and a.get("start_coordinate") \
        else _point(a)
    info = _at(point, look)
    if info is None:
        return _v("review", "can't see what is under the pointer (laya-guard desktop setup)")
    hit = _files(info, cwd, "dragging" if "drag" in action else "clicking")
    if hit is not None:
        return hit
    labels = " ".join(s for s in info.get("labels") or [] if isinstance(s, str))
    if labels and browser._RISKY_CLICK.search(labels):
        return _v("review", "a click that may pay, order, delete or send something")
    return _v("allow", "a click on nothing protected")


def _key(a: dict, cwd: str | None, look) -> Verdict:
    keys = a.get("text") if isinstance(a.get("text"), str) else a.get("key") or a.get("keys")
    keys = "+".join(keys) if isinstance(keys, list) else keys
    if not isinstance(keys, str) or not any(_OPENS.match(k) for k in keys.split()):
        return _v("allow", "a key press")
    info = look("selection")
    if info is None:
        return _v("review", "can't see what this key opens (laya-guard desktop setup)")
    return _files(info, cwd, "opening") or _v("allow", "a key press on nothing protected")


def _type(a: dict, cwd: str | None) -> Verdict:
    text = " ".join(browser._strs(a.get("text"), a.get("value")))
    hit = browser._sensitive_text(text, "")
    if hit is not None:
        return dataclasses.replace(hit, policy_id="desktop") if hit.policy_id == "browser" else hit
    paths = [m.group(1) for m in _PATHLIKE.finditer(text)][:50]
    rule = judge_accesses([("read", p) for p in paths], cwd) if paths else None
    if rule is not None:
        return _v(rule.action, f"typing the path of a protected file ({rule.reason})", "protected-path")
    return _v("allow", "typing ordinary text")


def judge_desktop_call(tool_name: str | None, tool_input, cwd: str | None = None, look=None) -> Verdict | None:
    """The verdict for a desktop computer-use call, or None when the call isn't one (the caller judges it as usual)."""
    parsed = parse(tool_name, tool_input)
    if parsed is None:
        return None
    action, a = parsed
    look = look or lookup
    if action in READS:
        return _v("allow", "looks at the screen")
    if action in CLICKS:
        return _click(action, a, cwd, look)
    if action in KEYS:
        return _key(a, cwd, look)
    if action in TYPES:
        return _type(a, cwd)
    return _v("review", f"a desktop action this check doesn't know ({action[:40]})")
