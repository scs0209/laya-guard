"""Browser tool calls: rules for what an agent does in a browser (go to a page, type, click, upload, run script).

The hook sends the tool's input as it is, and the call is judged by what each field holds, not by the shell rules or
the model (both read shell commands). Like the protected paths, this is a rule lane: no model, and nothing more is
logged than for any other call (hashes and lengths).

What the call says is all there is to judge. A click names an element id (`ref_12`, `e7`) or pixel coordinates, which
say nothing about what is under them; only the agent's own description of it (`action_summary`, Playwright's
`element`) does, when there is one. So a click is asked about when that description names a payment, an order, a
deletion or a send, and otherwise follows `unlabeled_clicks` in browser.yaml (allow by default).
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

from .fileaccess import config_dir, judge_accesses
from .guard import Verdict, _ensure, is_mock
from . import guard

log = logging.getLogger("laya_guard")

RULES_PATH = Path(__file__).with_name("browser.yaml")
MAX_STEPS = 50           # steps in one browser_batch; past it the call is asked about, never judged in part
MAX_TEXT = 20_000        # characters of one field that are read; a longer one is asked about
_RISK = {"allow": 0.05, "review": 0.55, "block": 0.92}
_RANK = {"allow": 0, "review": 1, "block": 2}

# Tool name prefix -> family. A tool of a family that isn't in its table below is asked about: a new tool should not
# pass unread.
FAMILIES = (
    ("mcp__Claude_Browser__", "claude"),                          # the Claude desktop app's built-in browser
    ("mcp__claude-in-chrome__", "claude"),                        # Claude in Chrome (same tool shapes)
    ("mcp__plugin_chrome-devtools-mcp_chrome-devtools__", "devtools"),
    ("mcp__chrome-devtools__", "devtools"),
    ("mcp__playwright__", "playwright"),
)
BROWSER_MATCHER = "|".join(p + ".*" for p, _ in FAMILIES)   # for a Claude Code hook matcher

# What each tool does: navigate, type, click, key, upload, script, batch, read (nothing outside the browser changes).
_CLAUDE = {
    "navigate": "navigate", "preview_start": "navigate", "form_input": "type", "javascript_tool": "script",
    "file_upload": "upload", "browser_batch": "batch", "computer": "computer",
    **{t: "read" for t in ("get_page_text", "read_page", "find", "read_console_messages", "read_network_requests",
                           "tabs_context", "tabs_context_mcp", "tabs_create", "tabs_create_mcp", "tabs_close",
                           "tabs_close_mcp", "tabs_select", "resize_window", "preview_list", "preview_logs",
                           "preview_stop", "gif_creator", "list_connected_browsers", "select_browser",
                           "switch_browser", "shortcuts_list")},
}
_COMPUTER = {"left_click": "click", "right_click": "click", "double_click": "click", "triple_click": "click",
             "left_click_drag": "click", "type": "type", "key": "key",
             **{a: "read" for a in ("screenshot", "zoom", "wait", "scroll", "scroll_to", "hover")}}
_DEVTOOLS = {
    "navigate_page": "navigate", "new_page": "navigate", "click": "click", "drag": "click", "fill": "type",
    "fill_form": "type", "type_text": "type", "press_key": "key", "upload_file": "upload", "evaluate_script": "script",
    "handle_dialog": "click",
    **{t: "read" for t in ("list_pages", "select_page", "close_page", "take_snapshot", "take_screenshot", "wait_for",
                           "hover", "resize_page", "emulate", "list_console_messages", "get_console_message",
                           "list_network_requests", "get_network_request", "get_css_styles", "lighthouse_audit",
                           "performance_start_trace", "performance_stop_trace", "performance_analyze_insight",
                           "screencast_start", "screencast_stop", "take_heapsnapshot")},
}
_PLAYWRIGHT = {
    "browser_navigate": "navigate", "browser_click": "click", "browser_drag": "click", "browser_type": "type",
    "browser_fill_form": "type", "browser_select_option": "click", "browser_press_key": "key",
    "browser_file_upload": "upload", "browser_evaluate": "script", "browser_handle_dialog": "click",
    **{t: "read" for t in ("browser_snapshot", "browser_take_screenshot", "browser_hover", "browser_wait_for",
                           "browser_resize", "browser_tabs", "browser_close", "browser_console_messages",
                           "browser_network_requests", "browser_navigate_back", "browser_install")},
}
_TABLES = {"claude": _CLAUDE, "devtools": _DEVTOOLS, "playwright": _PLAYWRIGHT}

# Words in an element's description that make a click (or Enter) something to ask about.
_RISKY_CLICK = re.compile(
    r"\b(?:pay|payment|checkout|check out|purchase|buy|order|subscribe|donate|transfer|send|submit|confirm|"
    r"delete|remove|erase|destroy|trash|deactivate|close account|unsubscribe|publish|post|merge|deploy|approve|authori[sz]e|"
    r"grant|accept|install|sign out|log ?out|wipe|reset|revoke)\b"
    r"|결제|구매|주문|송금|이체|후원|구독|삭제|휴지통|제거|탈퇴|해지|전송|보내기|제출|승인|게시|배포|설치|초기화|동의|로그아웃",
    re.IGNORECASE)
_SUBMIT_KEY = re.compile(r"(?:^|[\s+])(?:return|enter)$", re.IGNORECASE)
_PASSWORDISH = re.compile(r"password|passwd|passcode|\bpin\b|otp|one-time|cvc|cvv|card number|비밀번호|암호|인증번호|카드번호",
                          re.IGNORECASE)
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_DOWNLOAD_EXT = re.compile(r"\.(?:dmg|pkg|mpkg|exe|msi|bat|cmd|ps1|sh|command|app|deb|rpm|apk|jar|iso|zip|tar|gz|tgz|"
                           r"bz2|xz|7z|rar)$", re.IGNORECASE)
_TOKEN_PARAM = re.compile(r"(?:^|[?&#;])(?:access_token|id_token|refresh_token|token|api_?key|apikey|secret|password|"
                          r"passwd|sig|signature|auth|session|sessionid|code)=[^&#]{8,}", re.IGNORECASE)
_SCRIPT_READS = re.compile(r"document\s*\.\s*cookie|localStorage|sessionStorage|indexedDB|\.getAll\(|credentials",
                           re.IGNORECASE)
_SCRIPT_SENDS = re.compile(r"\bfetch\s*\(|XMLHttpRequest|sendBeacon|new\s+WebSocket|new\s+Image|\.src\s*=|"
                           r"location(?:\.href)?\s*=|window\.open\s*\(|\.submit\s*\(", re.IGNORECASE)
_LOCAL_SUFFIXES = (".localhost", ".test")


# --- user settings ---------------------------------------------------------------------------------------------------

def user_rules_path() -> Path:
    override = os.environ.get("LAYA_GUARD_BROWSER", "")
    return Path(override).expanduser() if override else config_dir() / "browser.yaml"


@lru_cache(maxsize=8)
def _load_file(path: str, mtime_ns: int) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("expected a mapping")
    return data


def settings() -> dict:
    """The packaged defaults with the user's browser.yaml on top: `sites` add up, `unlabeled_clicks` is replaced. A
    user file that can't be read leaves the defaults in force (and says so in the log)."""
    base = _load_file(str(RULES_PATH), RULES_PATH.stat().st_mtime_ns)
    sites = [s for s in base.get("sites") or [] if isinstance(s, str)]
    unlabeled = base.get("unlabeled_clicks", "allow")
    user = user_rules_path()
    try:
        if user.is_file():
            mine = _load_file(str(user), user.stat().st_mtime_ns)
            sites += [s for s in mine.get("sites") or [] if isinstance(s, str)]
            unlabeled = mine.get("unlabeled_clicks", unlabeled)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        log.warning("browser rules: %s ignored: %s", user, exc)
    if unlabeled not in ("allow", "review"):
        log.warning("browser rules: unlabeled_clicks must be allow or review, not %r; using review", unlabeled)
        unlabeled = "review"
    return {"sites": [s.strip().lower() for s in sites if s.strip()], "unlabeled_clicks": unlabeled}


# --- the call --------------------------------------------------------------------------------------------------------

def family(tool_name: str | None) -> tuple[str, str] | None:
    """(family, the tool's own name) for a browser tool, None for anything else."""
    if not isinstance(tool_name, str):
        return None
    for prefix, fam in FAMILIES:
        if tool_name.startswith(prefix):
            return fam, tool_name[len(prefix):]
    return None


def is_browser_tool(tool_name: str | None) -> bool:
    return family(tool_name) is not None


def call_text(tool_name: str | None, tool_input) -> str:
    """The text the hook judges for a tool that is neither Bash nor a file tool, as guard-hook.sh builds it: a `command`
    field when there is one, else the tool's name and its input's values."""
    c = tool_input.get("command") if isinstance(tool_input, dict) else None
    if isinstance(c, str) and c:
        return c.rstrip("\n").strip()
    values = tool_input.values() if isinstance(tool_input, dict) else [tool_input]
    return ((tool_name or "") + ": " + " ".join(str(v) for v in values)).rstrip("\n").strip()


def _v(action: str, reason: str, policy_id: str = "browser") -> Verdict:
    return Verdict(action=action, risk=_RISK[action], policy_id=policy_id, reason=reason, mock=is_mock(),
                   verdict_source="regex" if action != "allow" else "allowlist")


def _worst(verdicts: list[Verdict]) -> Verdict:
    return max(verdicts, key=lambda v: (_RANK[v.action], v.risk))


def _strs(*values) -> list[str]:
    """Every string in the values, nested lists and dicts included."""
    out = []
    for v in values:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            out += _strs(*v.values())
        elif isinstance(v, (list, tuple)):
            out += _strs(*v)
    return out


def _secret(text: str) -> Verdict | None:
    """The secrets-leak policy's patterns (tokens, keys, private keys, a resident registration number with its label)."""
    _ensure()
    pol = next((p for p in guard._policies or [] if p.id == "secrets-leak"), None)
    if pol is not None and any(rx.search(text) for rx in pol.compiled):
        return _v("block", pol.message_en or pol.message, "secrets-leak")
    return None


def _luhn(digits: str) -> bool:
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def _sensitive_text(text: str, label: str) -> Verdict | None:
    if len(text) > MAX_TEXT:
        return _v("review", "text too long to check")
    hit = _secret(text)
    if hit is not None:
        return hit
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return _v("review", "typing a card number")
    if guard._RRN_SHAPE.search(text):
        return _v("review", "typing a resident registration number")
    if label and _PASSWORDISH.search(label):
        return _v("review", "typing into a password, PIN or card field")
    return None


def _site_listed(host: str, sites: list[str]) -> bool:
    host = host.lower().rstrip(".")
    if host in ("localhost", "") or host.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        if ipaddress.ip_address(host.strip("[]")).is_loopback:
            return True
    except ValueError:
        pass
    for s in sites:
        if s.startswith("*.") and (host.endswith(s[1:]) or host == s[2:]):
            return True
        if host == s:
            return True
    return False


def _navigate(url, cwd: str | None, sites: list[str]) -> Verdict:
    if not isinstance(url, str) or not url.strip():
        return _v("allow", "no URL to open (back, forward, reload)")
    if len(url) > MAX_TEXT:
        return _v("review", "URL too long to check")
    hit = _secret(unquote(url))
    if hit is not None:
        return hit
    if url.strip().lower() == "about:blank":
        return _v("allow", "a blank page")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme == "file":
        path = unquote(parts.path)
        rule = judge_accesses([("read", path)], cwd)
        return rule or _v("review", "opening a local file in the browser")
    if scheme not in ("http", "https"):
        return _v("review", f"a {scheme or 'scheme-less'} URL")
    if _TOKEN_PARAM.search(parts.query) or _TOKEN_PARAM.search(parts.fragment):
        return _v("review", "a URL that carries a token or password")
    if _DOWNLOAD_EXT.search(unquote(parts.path)):
        return _v("review", "downloading an installer or archive")
    host = parts.hostname or ""
    if not _site_listed(host, sites):
        return _v("review", f"a site not on the list ({host})")
    return _v("allow", "a listed site")


def _click(label: str, unlabeled: str) -> Verdict:
    if label and _RISKY_CLICK.search(label):
        return _v("review", "a click that may pay, order, delete or send something")
    if label:
        return _v("allow", "a click on an element described as harmless")
    return _v(unlabeled, "a click on an element the call doesn't describe")


def _key(keys: str, label: str) -> Verdict:
    if _SUBMIT_KEY.search(keys.strip()) and label and _RISKY_CLICK.search(label):
        return _v("review", "a key press that may pay, order, delete or send something")
    return _v("allow", "a key press")


def _script(code: str) -> Verdict:
    if len(code) > MAX_TEXT:
        return _v("review", "script too long to check")
    hit = _secret(code)
    if hit is not None:
        return hit
    reads, sends = _SCRIPT_READS.search(code), _SCRIPT_SENDS.search(code)
    if reads and sends:
        return _v("block", "a script that reads cookies or storage and sends something out")
    if reads:
        return _v("review", "a script that reads cookies or storage")
    return _v("allow", "a page script")


def _upload(paths, cwd: str | None) -> Verdict:
    named = [p for p in _strs(paths) if p.strip()]
    if len(named) > 50:
        return _v("review", "too many files to check in one upload")
    rule = judge_accesses([("read", p) for p in named], cwd) if named else None
    if rule is not None:              # a protected file leaves the machine: block, whatever the rule says for a read
        return _v("block", f"uploading a protected file ({rule.reason})", "protected-path")
    return _v("review", "uploading a file from this machine")


def _one(fam: str, tool: str, a: dict, cwd: str | None, conf: dict, depth: int = 0) -> Verdict:
    kind = _TABLES[fam].get(tool)
    if kind == "computer":
        kind = _COMPUTER.get(a.get("action"))
    label = " ".join(_strs(a.get("action_summary"), a.get("element")))
    if kind == "read":
        return _v("allow", "reads the page")
    if kind == "navigate":
        v = _navigate(a.get("url"), cwd, conf["sites"])
        init = a.get("initScript")
        return _worst([v, _script(init)]) if isinstance(init, str) and init else v
    if kind == "type":
        texts = _strs(a.get("text"), a.get("value"), a.get("fields"), a.get("elements"))
        found = [v for v in (_sensitive_text(t, label + " " + " ".join(_strs(a.get("fields")))) for t in texts) if v]
        verdicts = found + ([_key(a["submitKey"], label)] if isinstance(a.get("submitKey"), str) else [])
        if a.get("submit") is True:
            verdicts.append(_key("Enter", label))
        return _worst(verdicts) if verdicts else _v("allow", "typing ordinary text")
    if kind == "click":
        return _click(label, conf["unlabeled_clicks"])
    if kind == "key":
        keys = a.get("text") if fam == "claude" else a.get("key")
        return _key(keys if isinstance(keys, str) else "", label)
    if kind == "script":
        code = " ".join(_strs(a.get("text"), a.get("function"), a.get("code")))
        return _script(code)
    if kind == "upload":
        return _upload([a.get("paths"), a.get("filePaths")], cwd)
    if kind == "batch":
        steps = a.get("actions")
        if not isinstance(steps, list) or depth:
            return _v("review", "a batch this check can't read")
        if len(steps) > MAX_STEPS:
            return _v("review", "too many steps in one batch to check")
        prefix = next(p for p, f in FAMILIES if f == fam)
        verdicts = []
        for s in steps:
            name, inp = (s.get("name"), s.get("input")) if isinstance(s, dict) else (None, None)
            if not isinstance(name, str) or not isinstance(inp, dict):
                verdicts.append(_v("review", "a batch step this check can't read"))
                continue
            sub = family(name) or family(prefix + name)
            verdicts.append(_one(sub[0], sub[1], inp, cwd, conf, depth + 1) if sub
                            else _v("review", f"a batch step with an unknown tool ({name})"))
        return _worst(verdicts) if verdicts else _v("allow", "an empty batch")
    return _v("review", f"a browser tool this check doesn't know ({tool})")


def judge_browser_call(tool_name: str | None, tool_input, cwd: str | None = None) -> Verdict | None:
    """The verdict for a browser tool call, or None when the tool isn't one (the caller judges it as usual)."""
    fam = family(tool_name)
    if fam is None:
        return None
    if not isinstance(tool_input, dict):
        return _v("review", "a browser call this check can't read")
    return _one(fam[0], fam[1], tool_input, cwd, settings())
