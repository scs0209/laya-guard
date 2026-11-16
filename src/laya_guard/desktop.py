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

Peekaboo (openclaw/Peekaboo) has tools of its own: a click names an element ID from its last `see`, a text query, or
coordinates. A query is looked up by name in the target app (and Finder); an element ID is turned into its label from
that `see` result in the agent's transcript, then looked up the same way. `agent` (Peekaboo's own model running steps
the hook never sees) and `analyze` (a screenshot sent to an AI provider) are asked about.

The Laya model as a second opinion (LAYA_GUARD_DESKTOP_MODEL=1, server only, off by default): text the rules let an
agent type, shortcuts with cmd, ctrl or option, and clicks on a labeled element are also shown to the model, which can
raise them to a review but never to a block, so a wrong guess costs one permission prompt. Plain keys stay with the
rules (it held Escape). The model was trained on desktop clicks (finetune/desktop_data.py): on clicks it never saw it
let 57 of 61 ordinary ones through and held 14 of 17 that should be asked about; the button words below hold the rest.
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


def _click(action: str, a: dict, cwd: str | None, look, model=None) -> Verdict:
    point = _point(a, "start_coordinate") if action in ("left_click_drag", "drag") and a.get("start_coordinate") \
        else _point(a)
    info = _at(point, look)
    if info is None:
        return _v("review", "can't see what is under the pointer (laya-guard desktop setup)")
    hit = _files(info, cwd, "dragging" if "drag" in action else "clicking")
    if hit is not None:
        return hit
    if _risky(info):
        return _v("review", "a click that may pay, order, delete or send something")
    return _ask_model(_v("allow", "a click on nothing protected"), info, model)


# Button words the browser list (browser._RISKY_CLICK) leaves out that matter in a desktop app: a reply or forward goes
# out, a push publishes code, revert/discard/overwrite/replace lose work, leave and uninstall can't be taken back.
# The model catches most of these too, but measured on clicks it never saw it let a few of each kind through.
_RISKY_DESKTOP = re.compile(
    r"\b(?:reply|forward|push|bin|revert|discard|overwrite|replace|leave|uninstall|share|invite|wire|don'?t save)\b"
    r"|답장|전달|푸시|지우|되돌|폐기|덮어|대치|나가|공유|저장 안 함",
    re.IGNORECASE)


def _risky(info: dict) -> bool:
    labels = " ".join(s for s in info.get("labels") or [] if isinstance(s, str))
    return bool(labels) and bool(browser._RISKY_CLICK.search(labels) or _RISKY_DESKTOP.search(labels))


# --- the model's second opinion ---------------------------------------------------------------------------------------

MODEL_ENV = "LAYA_GUARD_DESKTOP_MODEL"
_MODIFIED = re.compile(r"(?:^|[\s+])(?:cmd|command|ctrl|control|option|opt|alt|super|meta)\+", re.IGNORECASE)


def model_enabled() -> bool:
    return os.environ.get(MODEL_ENV, "").strip().lower() in ("1", "on", "true", "yes")


def second_opinion():
    """The model step for typed text and shortcuts, or None when LAYA_GUARD_DESKTOP_MODEL is off or the model isn't
    installed (mock mode). It returns (action, risk); a model that fails says allow, so the rules' verdict stands."""
    if not model_enabled() or is_mock():
        return None
    from .guard import _model_or_fallback

    def ask(text: str) -> tuple[str, float]:
        action, risk, ok = _model_or_fallback(text, None)
        return (action, risk) if ok else ("allow", 0.0)
    return ask


_KINDS = {"AXButton": "button", "AXMenuItem": "menu item", "AXMenuBarItem": "menu", "AXCheckBox": "checkbox",
          "AXRadioButton": "option", "AXLink": "link", "AXTab": "tab", "AXPopUpButton": "pop-up menu",
          "AXRow": "row", "AXCell": "item", "AXStaticText": "item", "AXTextField": "item", "AXImage": "icon"}


def describe_click(label: str, role: str = "", app: str = "", context: str = "") -> str:
    """A click as the model reads it, in training and in use alike (finetune/desktop_data.py writes its examples
    with this): what is clicked, where, and, when known, what the window around it says."""
    kind = _KINDS.get(role, "button" if not role else "element")
    text = f'desktop: click the "{label.strip()[:120]}" {kind}'
    if app.strip():
        text += f" in {app.strip()[:60]}"
    if context.strip():
        text += f' (the window says: "{" ".join(context.split())[:300]}")'
    return text


def _confirm(v: Verdict, text: str, what: str, model) -> Verdict:
    """An allow from the rules, shown to the model when it is on: it may become a review, never a block. The reason
    names what was done, never the text itself (it can be shown on screen)."""
    if model is None or v.action != "allow":
        return v
    try:
        action, risk = model(text)
    except Exception:
        return v
    if action == "allow":
        return v
    return Verdict(action="review", risk=round(float(risk), 3), policy_id="desktop-model", mock=False,
                   reason=f"the Laya model asks about {what} (risk {float(risk):.2f})", verdict_source="model")


def _ask_model(v: Verdict, info: dict, model) -> Verdict:
    """A click the rules allow, shown to the model as describe_click words it (the element's own label first)."""
    labels = [s for s in info.get("labels") or [] if isinstance(s, str) and s.strip()]
    if model is None or not labels:
        return v
    role = info.get("role") if isinstance(info.get("role"), str) and info.get("role", "").startswith("AX") else ""
    app = info.get("app") if isinstance(info.get("app"), str) else ""
    return _confirm(v, describe_click(labels[0], role, app), "this click", model)


def _key(a: dict, cwd: str | None, look, model=None) -> Verdict:
    keys = a.get("text") if isinstance(a.get("text"), str) else a.get("key") or a.get("keys")
    keys = "+".join(keys) if isinstance(keys, list) else keys
    if not isinstance(keys, str):
        return _v("allow", "a key press")
    shortcut = (lambda v: _confirm(v, f"desktop: press {keys[:200]}", "this shortcut", model)) \
        if _MODIFIED.search(keys) else (lambda v: v)
    if not any(_OPENS.match(k) for k in keys.split()):
        return shortcut(_v("allow", "a key press"))
    info = look("selection")
    if info is None:
        return _v("review", "can't see what this key opens (laya-guard desktop setup)")
    return _files(info, cwd, "opening") or shortcut(_v("allow", "a key press on nothing protected"))


def _type(a: dict, cwd: str | None, model=None) -> Verdict:
    text = " ".join(browser._strs(a.get("text"), a.get("value")))
    hit = browser._sensitive_text(text, "")
    if hit is not None:
        return dataclasses.replace(hit, policy_id="desktop") if hit.policy_id == "browser" else hit
    paths = [m.group(1) for m in _PATHLIKE.finditer(text)][:50]
    rule = judge_accesses([("read", p) for p in paths], cwd) if paths else None
    if rule is not None:
        return _v(rule.action, f"typing the path of a protected file ({rule.reason})", "protected-path")
    allowed = _v("allow", "typing ordinary text")
    return _confirm(allowed, f"desktop: type this text: {text}", "this typed text", model) if text.strip() else allowed


def judge_desktop_call(tool_name: str | None, tool_input, cwd: str | None = None, look=None,
                       transcript: str | None = None, model=None) -> Verdict | None:
    """The verdict for a desktop computer-use call, or None when the call isn't one (the caller judges it as usual).
    transcript: the agent's transcript file, where Peekaboo's element IDs are turned into labels.
    model: second_opinion()'s callable, which may raise typed text and shortcuts the rules allow to a review."""
    look = look or lookup
    tool = peekaboo_tool(tool_name)
    if tool is not None and isinstance(tool_input, dict):
        return _peekaboo(tool, tool_input, cwd, look, transcript, model)
    parsed = parse(tool_name, tool_input)
    if parsed is None:
        return None
    action, a = parsed
    if action in READS:
        return _v("allow", "looks at the screen")
    if action in CLICKS:
        return _click(action, a, cwd, look, model)
    if action in KEYS:
        return _key(a, cwd, look, model)
    if action in TYPES:
        return _type(a, cwd, model)
    return _v("review", f"a desktop action this check doesn't know ({action[:40]})")


# --- Peekaboo ---------------------------------------------------------------------------------------------------------

_PEEKABOO = re.compile(r"^mcp__[^_]*peekaboo[^_]*__(\w+)$", re.IGNORECASE)
PEEKABOO_READS = {"see", "image", "inspect_ui", "capture", "sleep", "verify_state", "permissions", "window", "dock",
                  "space", "app"}
_RISKY_TOOLS = {"agent": "Peekaboo's own agent runs steps this check never sees",
                "analyze": "a screenshot is sent to an AI provider",
                "browser": "a browser tool passed through Peekaboo",
                "clipboard": "the clipboard is read or changed"}
_OPEN_WORDS = re.compile(r"\bopen\b|열기", re.IGNORECASE)
_ELEMENT_ID = re.compile(r"^[A-Za-z][\w.-]{0,63}$")


def peekaboo_tool(tool_name: str | None) -> str | None:
    """The Peekaboo tool's own name (click, see, ...) for an MCP server whose name says peekaboo, else None."""
    m = _PEEKABOO.match(tool_name or "")
    return m.group(1).lower() if m else None


def element_label(element_id: str, transcript: str | None, max_bytes: int = 4_000_000) -> str | None:
    """The label an element ID had in the latest tool result that lists it (Peekaboo's `see`), read from the end of the
    agent's transcript. None when the transcript is missing or doesn't name the ID."""
    if not transcript or not _ELEMENT_ID.match(element_id or ""):
        return None
    try:
        with open(transcript, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    word = re.compile(r"(?<![\w-])" + re.escape(element_id) + r"(?![\w-])")
    for line in reversed(lines):
        if element_id not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        for text in _result_texts(entry):
            for row in text.splitlines():
                if word.search(row):
                    label = _label_in(row, element_id)
                    if label:
                        return label
    return None


def _result_texts(entry) -> list[str]:
    """The text of the tool results in one transcript line (where a `see` lists its elements)."""
    content = (entry.get("message") or {}).get("content") if isinstance(entry, dict) else None
    out = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            inner = block.get("content")
            out += [inner] if isinstance(inner, str) else browser._strs(inner)
    return out


def _label_in(row: str, element_id: str) -> str | None:
    quoted = [q for pair in re.findall(r'"([^"]{1,200})"|\'([^\']{1,200})\'', row) for q in pair if q]
    quoted = [q for q in quoted if q != element_id and not re.fullmatch(r"AX\w+|[\d.,\s()x×-]+", q)]
    if quoted:
        return max(quoted, key=len)
    rest = re.sub(r"(?<![\w-])" + re.escape(element_id) + r"(?![\w-])|\bAX\w+|[\[\](){}:=|]|-?\d+(?:\.\d+)?", " ", row)
    rest = " ".join(w for w in rest.split() if w.lower() not in ("label", "title", "role", "id", "at", "frame", "bounds"))
    return rest[:200] or None


def _target(a: dict, transcript: str | None, look) -> tuple[dict | None, str]:
    """What a Peekaboo click/action/drag lands on: the helper's answer, and how it was found (for the reason)."""
    pid = a.get("pid")
    pids = [str(int(pid))] if isinstance(pid, (int, float)) and pid > 0 else []
    query = a.get("query")
    if isinstance(query, str) and query.strip():
        return look("find", query.strip()[:200], *pids), f"'{query.strip()[:40]}'"
    on = a.get("on") or a.get("from")
    if isinstance(on, str) and on.strip():
        label = element_label(on.strip(), transcript)
        if label is None:
            return None, f"element {on.strip()[:20]} (not in the transcript's last see)"
        return look("find", label, *pids), f"'{label[:40]}'"
    coords = a.get("coords") or a.get("from_coords")
    if isinstance(coords, str) and coords.strip():
        try:
            x, y = (float(c) for c in coords.split(",")[:2])
        except ValueError:
            return None, "coordinates this check can't read"
        if str(a.get("coordinate_space") or "").lower() not in ("global", "screen"):
            return None, "coordinates relative to a window this check can't place"
        scale = float(os.environ.get("LAYA_GUARD_AX_SCALE", "") or 1.0)
        return look("at", f"{x * scale:.0f}", f"{y * scale:.0f}"), "the point clicked"
    return None, "a target this check can't read"


def _on_target(tool: str, a: dict, cwd: str | None, look, transcript: str | None, model=None) -> Verdict:
    info, what = _target(a, transcript, look)
    if info is None:
        return _v("review", f"can't see what this {tool} lands on: {what}")
    hit = _files(info, cwd, "dragging" if tool == "drag" else "clicking")
    if hit is not None:
        return hit
    if _risky(info):
        return _v("review", "a click that may pay, order, delete or send something")
    if not info.get("found", True):
        return _v("review", f"can't find {what} on the screen")
    allowed = _v("allow", f"a {tool} on nothing protected")
    return _ask_model(allowed, info, model) if tool == "click" else allowed


def _keys(a: dict) -> str:
    """Peekaboo's press as key words: keys ["cmd+o", "Return"], or key "o" with modifiers ["cmd"]."""
    keys = a.get("keys")
    if isinstance(keys, list):
        return " ".join(k for k in keys if isinstance(k, str))
    key = a.get("key") if isinstance(a.get("key"), str) else ""
    mods = [m for m in a.get("modifiers") or [] if isinstance(m, str)]
    return "+".join(mods + [key]) if key else ""


def _peekaboo(tool: str, a: dict, cwd: str | None, look, transcript: str | None, model=None) -> Verdict:
    action = str(a.get("action") or "").lower()
    if tool in _RISKY_TOOLS and not (tool == "clipboard" and action in ("status",)):
        return _v("review", _RISKY_TOOLS[tool])
    if tool in PEEKABOO_READS or tool == "scroll" or (tool in ("menu", "dialog") and action == "list"):
        return _v("allow", "looks at or arranges the screen")
    if tool in ("click", "action", "drag"):
        return _on_target(tool, a, cwd, look, transcript, model)
    if tool == "press":
        return _key({"text": _keys(a)}, cwd, look, model)
    if tool in ("type", "paste", "set_value"):
        return _type(a, cwd, model)
    if tool == "select_text":
        return _type(a, cwd)                                    # selecting text types nothing: no second opinion
    if tool in ("menu", "dialog"):
        named = " ".join(browser._strs(a.get("path"), a.get("item"), a.get("button")))
        if a.get("text"):
            typed = _type(a, cwd, model)
            if typed.action != "allow":
                return typed
        if named and _OPEN_WORDS.search(named):
            return _key({"text": "Return"}, cwd, look)       # opening: whatever is selected is what opens
        if named and _risky({"labels": [named]}):
            return _v("review", f"a {tool} item that may delete, send or pay ('{named[:40]}')")
        return _v("allow", f"a {tool} action on nothing protected")
    return _v("review", f"a Peekaboo tool this check doesn't know ({tool[:40]})")
