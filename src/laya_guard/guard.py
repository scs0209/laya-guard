"""laya-guard core: regex prefilter + Laya judge (local, free)."""
from __future__ import annotations
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
import yaml

# policy.yaml ships inside the package (see pyproject package-data) so pip installs work.
POLICY_PATH = Path(__file__).resolve().parent / "policy.yaml"

log = logging.getLogger("laya_guard")

@dataclass
class Verdict:
    action: str  # allow | review | block
    risk: float
    policy_id: str
    reason: str
    mock: bool = False
    # where the verdict came from: regex = prefilter short-circuit (no model),
    # allowlist = matched policy.yaml safe_commands (no model),
    # model = Laya ran (even if a regex floor later raised the action),
    # heuristic = mock mode, or the model raised and regex+heuristics stood in,
    # limit = input too long to judge in time (blocked unread),
    # offline = judge_offline (hooks with the server down).
    verdict_source: str = "model"

@dataclass
class Policy:
    id: str
    title: str
    patterns: list = field(default_factory=list)
    action_on_regex: str = "review"
    message: str = ""
    message_en: str = ""
    compiled: list = field(default_factory=list, repr=False)

def load_policies(path: Path | str = POLICY_PATH) -> tuple[list[Policy], dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    policies = []
    for p in data.get("policies", []):
        pol = Policy(
            id=p["id"],
            title=p.get("title", p["id"]),
            patterns=p.get("patterns", []),
            action_on_regex=p.get("action_on_regex", "review"),
            message=p.get("message", ""),
            message_en=p.get("message_en", "") or "",
        )
        pol.compiled = [re.compile(pat, re.IGNORECASE) for pat in pol.patterns]
        policies.append(pol)
    return policies, data.get("defaults", {})

def load_safe_commands(path: Path | str = POLICY_PATH) -> list[re.Pattern]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [re.compile(p) for p in data.get("safe_commands", []) or []]

_MOCK = os.environ.get("LAYA_GUARD_MOCK", "") == "1"
_policies: list[Policy] | None = None
_defaults: dict | None = None
_safe: list[re.Pattern] | None = None
_router = None
_laya_ok = None

def _ensure():
    global _policies, _defaults, _safe
    if _policies is None:
        _policies, _defaults = load_policies()
        _safe = load_safe_commands()

def is_mock() -> bool:
    global _laya_ok
    if _MOCK:
        return True
    if _laya_ok is not None:
        return not _laya_ok
    try:
        import laya  # noqa: F401
        _laya_ok = True
        return False
    except Exception:
        _laya_ok = False
        return True

def _regex_hit(text: str) -> Policy | None:
    _ensure()
    assert _policies is not None
    first_review = None
    for pol in _policies:
        if any(rx.search(text) for rx in pol.compiled):
            if pol.action_on_regex == "block":
                return pol
            first_review = first_review or pol
    return first_review

# 안전 레인은 단일 명령에만 열린다: 체이닝·파이프·리다이렉트·치환 금지,
# 임의 프로그램 실행/파일 쓰기 플래그 금지 (go test -exec, git log --output 등).
_SHELL_META = re.compile(r"[;&|`$<>(){}\\\n\r]")
_RISKY_FLAG = re.compile(
    r"(?:^|\s)--?(?:exec|toolexec|config|output|upload-pack|receive-pack"
    r"|script-shell|node-options|userconfig|globalconfig)(?:[=\s]|$)")
_SENSITIVE_PATH = re.compile(
    r"\.ssh|\.aws|\.gnupg|\.kube|\.netrc|\.npmrc|\.pypirc|\.env\b|\.git-credentials"
    r"|id_rsa|id_ed25519|credentials|/etc/(shadow|sudoers)", re.IGNORECASE)

def _safe_command(text: str) -> bool:
    _ensure()
    assert _safe is not None
    text = text.strip()
    if (not text or _SHELL_META.search(text) or _RISKY_FLAG.search(text)
            or _SENSITIVE_PATH.search(text)):
        return False
    cmd = " ".join(text.split())
    return any(rx.fullmatch(cmd) for rx in _safe)

_READONLY_HINTS = ["조회", "읽기", "찾아", "검색", "요약", "정리", "문서", "읽어", "보여줘", "원인"]
_WRITE_HINTS = ["실행", "진행", "승인", "보내", "전송", "삭제", "결제 진행", "송금", "환불"]

def _mock_score(text: str, hit: Policy | None) -> tuple[str, float]:
    if hit is None:
        t = text.lower()
        # 읽기 전용 의도가 명확하면 suspicious 단어 포함해도 allow (조회/요약/문서찾기)
        if any(h in text for h in _READONLY_HINTS) and not any(w in text for w in _WRITE_HINTS):
            return "allow", 0.1
        suspicious = ["결제", "송금", "환불", "refund", "sudo", "삭제", "drop", "curl", "rm ", "인증"]
        if any(k in text or k in t for k in suspicious) or _SENSITIVE_PATH.search(text):
            return "review", 0.55
        return "allow", 0.1
    if hit.action_on_regex == "block":
        return "block", 0.92
    return "review", 0.65

# 결정성: 기본 CPU 고정. MPS 자동선택은 프로세스마다 확률이 미세하게
# 달라져 경계 케이스가 뒤집히는 것을 4-프로세스 실험으로 확인.
# LAYA_GUARD_DEVICE=mps 등으로 덮어쓸 수 있음(속도 우선 시), 단 재현성 포기.
GUARD_DEVICE = os.environ.get("LAYA_GUARD_DEVICE", "cpu")

# 모델 한 번 호출이 읽는 최대 길이. 예전에는 text[:4000]만 넘겨 4000자 뒤는 모델이 못 봤다
# (위험 명령 앞에 무해한 긴 앞부분을 붙이면 block이 review로 내려갔다).
MODEL_WINDOW_CHARS = 4_000
MODEL_WINDOW_OVERLAP = 200       # 한 줄이 창보다 길 때 자른 경계에 걸친 명령을 놓치지 않도록
MAX_MODEL_WINDOWS = 6            # 창당 ~0.1~0.8초: 훅 타임아웃 안에서 끝나도록 상한을 둔다

def _model_windows(text: str) -> tuple[list[str], bool]:
    """Split text into windows the model can read: (windows, truncated).

    Windows end on line boundaries where possible; a single line longer than a
    window is cut with a small overlap. Beyond MAX_MODEL_WINDOWS the middle is
    skipped (first windows + the last one are kept) and truncated is True.
    """
    if len(text) <= MODEL_WINDOW_CHARS:
        return [text], False
    windows: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > MODEL_WINDOW_CHARS:           # 긴 한 줄: 겹치게 자른다
            if cur:
                windows.append(cur); cur = ""
            windows.append(line[:MODEL_WINDOW_CHARS])
            line = line[MODEL_WINDOW_CHARS - MODEL_WINDOW_OVERLAP:]
        if cur and len(cur) + 1 + len(line) > MODEL_WINDOW_CHARS:
            windows.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        windows.append(cur)
    if len(windows) <= MAX_MODEL_WINDOWS:
        return windows, False
    return windows[:MAX_MODEL_WINDOWS - 1] + windows[-1:], True

_ACTION_RANK = {"allow": 0, "review": 1, "block": 2}

def _laya_judge(text: str) -> tuple[str, float]:
    """Judge text with the model: every window is judged and the worst verdict wins.

    If part of the input could not be shown to the model (more than
    MAX_MODEL_WINDOWS windows), an allow is raised to review.
    """
    windows, truncated = _model_windows(text)
    results = [_laya_predict(w) for w in windows]
    action, risk = max(results, key=lambda r: (_ACTION_RANK[r[0]], r[1]))
    if truncated and action == "allow":
        floor = float((_defaults or {}).get("review_threshold", 0.45))
        action, risk = "review", round(max(risk, floor), 3)
    return action, risk

def _laya_predict(text: str) -> tuple[str, float]:
    """Run the Laya model on one window. Raises if the model can't load or predict."""
    global _router
    _ensure()
    from laya import Router
    if _router is None:
        _router = Router(device=GUARD_DEVICE)
    questions = {
        "action": {
            "type": "choice",
            "instructions": "이 tool_call을 어떻게 처리해야 하나? allow=안전, review=사람승인 필요, block=즉시차단",
            "criteria": {
                "allow": "읽기 전용 조회, 안전한 일상 작업",
                "review": "결제, 삭제, 외부전송, 권한변경 - 사람 확인 필요",
                "block": "파괴, 유출, 인젝션, 시크릿노출, 인증우회",
            },
        },
        "risk": {
            "type": "score",
            "instructions": "이 요청의 위험도는?",
            "criteria": ["안전", "주의", "위험"],
        },
        "malicious": {
            "type": "noul",
            "instructions": "악의적이거나 지시 탈취 시도인가?",
        },
    }
    r = _router.predict(text, questions, model="multilingual")
    ans = r.get("answers", {})
    act = ans.get("action", {})
    action = act.get("choice", "review")
    probs = act.get("probabilities", {}) or {}
    try:
        p_block = float(probs.get("block", 0.0))
        p_review = float(probs.get("review", 0.0))
        risk = p_block + 0.5 * p_review
    except Exception:
        # fallback: score는 legend 인덱스 기대값(0..N) → 0..1로 정규화
        risk_raw = ans.get("risk", {}).get("score", 0.5)
        legend = ans.get("risk", {}).get("legend", {}) or {}
        try:
            max_idx = max(int(k) for k in legend.keys()) if legend else 2
        except Exception:
            max_idx = 2
        try:
            risk = float(risk_raw) / max_idx if max_idx else 0.5
        except Exception:
            risk = 0.5
    mal = ans.get("malicious", {}).get("noul", 0.0)
    try:
        risk = max(risk, float(mal))
    except Exception:
        pass
    risk = max(0.0, min(1.0, risk))
    if action not in ("allow", "review", "block"):
        action = "review"
    d = _defaults or {}
    if risk >= float(d.get("block_threshold", 0.8)):
        action = "block"
    elif action == "review" or risk >= float(d.get("review_threshold", 0.45)):
        action = "review"
    else:
        action = "allow"
    return action, round(float(risk), 3)

def _model_or_fallback(text: str, hit: Policy | None) -> tuple[str, float, bool]:
    """(action, risk, model_ok). On model failure, regex+heuristics stand in."""
    try:
        action, risk = _laya_judge(text)
        return action, risk, True
    except Exception as e:
        log.warning("Laya model failed (%s: %s); falling back to regex+heuristics",
                    type(e).__name__, e)
        action, risk = _mock_score(text, hit)
        return action, risk, False

def warmup() -> bool:
    """Load the model (downloading the checkpoint on first run) and run one judgment.

    Returns False in mock mode or if the model is unusable.
    """
    if is_mock():
        return False
    try:
        _laya_judge("ls")
        return True
    except Exception as e:
        log.warning("Laya model warmup failed (%s: %s)", type(e).__name__, e)
        return False

# 출처별 임계값: 사용자는 관대하게, 외부 주입원(tool/email/web)은 엄격하게.
# ws_2(외부인 초대), travel_2(주입된 캘린더 생성)처럼 문장만으로 구분 불가한
# 간접 인젝션은 출처가 유일한 단서다.
SOURCE_PROFILES = {
    "user": {"block": 0.8, "review": 0.45},
    "tool": {"block": 0.6, "review": 0.30},
    "email": {"block": 0.6, "review": 0.30},
    "web": {"block": 0.6, "review": 0.30},
}

def _is_korean(text: str) -> bool:
    return bool(re.search("[가-힣]", text))

# 체인 분할: 최상위 ; && || & 에서만 자른다 (따옴표·$()·``·괄호 안은 그대로).
# 각 조각이 소문자 명령(또는 VAR=값 접두)으로 시작할 때만 셸 체인으로 본다.
_CHAIN_MAX = 8
_CMD_HEAD = re.compile(r"(?:[A-Za-z_]\w*=\S*\s+)*[a-z0-9_./~-]+(?:\s|$)")
_ACTION_RANK = {"allow": 0, "review": 1, "block": 2}

def _split_chain(text: str) -> list[str]:
    """Split a one-line shell chain into its commands; [text] if it isn't one."""
    s = text.strip()
    if not s or "\n" in s or "\r" in s or _is_korean(s):
        return [text]
    parts: list[str] = []
    start, quote, depth, i = 0, "", 0, 0
    while i < len(s):
        c, nxt = s[i], s[i + 1:i + 2]
        if c == "\\" and quote != "'":
            i += 2
            continue
        if quote:
            if c == quote:
                quote = ""
        elif c in "'\"`":
            quote = c
        elif c in "({":
            depth += 1
        elif c in ")}":
            depth -= 1
        elif depth == 0:
            doubled = c in "&|" and nxt == c
            background = c == "&" and s[i - 1:i] != ">" and nxt != ">"
            if c == ";" or doubled or background:
                parts.append(s[start:i])
                i += 2 if doubled else 1
                start = i
                continue
        i += 1
    if quote or depth != 0:
        return [text]
    parts.append(s[start:])
    parts = [p.strip() for p in parts if p.strip()]
    if not 2 <= len(parts) <= _CHAIN_MAX or not all(_CMD_HEAD.match(p) for p in parts):
        return [text]
    return parts

# 정규식 일부(X.*Y)는 한 줄 길이에 대해 제곱 시간이다: `curl `×4만(200KB)이 48초.
# 훅 타임아웃(30초)을 넘기면 fail-open 우회가 되므로, 읽기 전에 길이로 차단한다.
MAX_INPUT_CHARS = 256_000
MAX_LINE_CHARS = 16_000

def _oversized(text: str) -> bool:
    return len(text) > MAX_INPUT_CHARS or any(
        len(line) > MAX_LINE_CHARS for line in text.splitlines())

def _oversized_verdict(text: str, mock: bool) -> Verdict:
    ko = _is_korean(text[:2000])
    reason = ("입력이 너무 길어 제시간에 심판 불가 — 차단" if ko
              else "Input too long to judge in time — blocked")
    return Verdict(action="block", risk=1.0, policy_id="oversized", reason=reason,
                   mock=mock, verdict_source="limit")

# 패키지 스크립트 검사: `npm test`는 안전 명령이지만 실제로는 package.json의
# scripts.test를 실행한다. 그 스크립트를 정규식 정책으로 검사한다.
# 모델은 쓰지 않는다: next build, vite build, jest 같은 평범한 스크립트를 block한다.
_RUNNER = re.compile(r"(npm|pnpm|yarn|bun) (?:run (\S+)|(test|build|lint))((?: \S+)*)")
# 다른 디렉터리의 package.json을 쓰게 하는 플래그: 어떤 스크립트가 도는지 알 수 없다.
_LOCATION_FLAG = re.compile(
    r"--(?:prefix|dir|cwd|workspaces?|filter|recursive|global)(?:=.*)?|-(?:C|w|F)\S*|-(?:r|g|ws)")
_LOST_CWD = "<unknown cwd>"  # 체인 중간에 cd -, pushd 등으로 위치를 놓친 경우
# 악성 package.json이 검사 시간을 늘려 훅 타임아웃(→ fail open)을 노리지 못하게
# 스크립트는 한 번씩만, 길이·크기 상한 안에서만 검사한다.
MAX_SCRIPT_DEPTH = 3
MAX_SCRIPT_CHARS = 4_000
MAX_PACKAGE_JSON_BYTES = 256_000

@dataclass
class _ScriptFinding:
    key: str                        # 문제가 된 스크립트 (test, pretest …)
    hit: Policy | None = None       # 스크립트 본문이 걸린 정규식 정책
    unresolved: str | None = None   # 무엇이 실행될지 알 수 없는 이유 (en)

    def rank(self) -> int:
        if self.hit is not None and self.hit.action_on_regex == "block":
            return 2
        return 1

def _runner_script(cmd: str) -> tuple[str, list[str]] | None:
    """(script name, args) for a package-manager script command, else None."""
    m = _RUNNER.fullmatch(cmd)
    if m is None:
        return None
    tool, name = m[1], m[2] or m[3]
    if tool == "bun" and m[3] in ("test", "build"):
        return None  # bun 자체 테스트 러너·번들러: package.json 스크립트가 아니다
    return name, m[4].split()

def _next_cwd(segment: str, cwd: str | None) -> str | None:
    """Directory the next command in a chain runs in, after `segment` ran in `cwd`."""
    if cwd is None or cwd == _LOST_CWD:
        return cwd
    words = segment.split()
    while words and re.match(r"[A-Za-z_]\w*=", words[0]):
        words.pop(0)
    if not words:
        return cwd
    if words[0] == "cd":
        if len(words) == 1:
            return str(Path.home())
        if len(words) == 2 and not words[1].startswith("-"):
            return os.path.normpath(os.path.join(cwd, os.path.expanduser(words[1])))
        return _LOST_CWD
    if words[0] in ("pushd", "popd", "source", ".", "eval", "exec", "builtin", "command"):
        return _LOST_CWD
    return cwd

def _package_scripts(cwd: str) -> tuple[str, dict[str, str]] | None:
    """(package dir, scripts) of the nearest package.json at or above cwd.

    None if it can't be read safely; an empty dict if there is no package.json
    (the package manager fails, nothing runs).
    """
    start = Path(cwd).expanduser()
    for d in (start, *start.parents):
        f = d / "package.json"
        if not f.is_file():
            continue
        try:
            if f.stat().st_size > MAX_PACKAGE_JSON_BYTES:
                return None
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None
        except ValueError:
            return str(d), {}  # 깨진 JSON이면 패키지 매니저가 실패한다
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if not isinstance(scripts, dict):
            return str(d), {}
        return str(d), {k: v for k, v in scripts.items()
                        if isinstance(k, str) and isinstance(v, str)}
    return str(start), {}

def _check_scripts(cmd: str, cwd: str, depth: int = 0,
                   seen: set[tuple[str, str]] | None = None) -> _ScriptFinding | None:
    """Regex-check the package.json scripts a safe command runs.

    None if cmd isn't a script runner or its scripts are clean.
    """
    seen = set() if seen is None else seen
    runner = _runner_script(cmd)
    if runner is None:
        return None
    name, args = runner
    flags = args[:args.index("--")] if "--" in args else args
    if cwd == _LOST_CWD:
        return _ScriptFinding(name, unresolved="can't tell which directory it runs in")
    if name.startswith("-") or any(_LOCATION_FLAG.fullmatch(a) for a in flags):
        return _ScriptFinding(name, unresolved="runs scripts from another package")
    if depth >= MAX_SCRIPT_DEPTH:
        return _ScriptFinding(name, unresolved="scripts nest too deep")
    found = _package_scripts(cwd)
    if found is None:
        return _ScriptFinding(name, unresolved="package.json can't be read")
    pkg_dir, scripts = found
    worst = None
    for key in (f"pre{name}", name, f"post{name}"):
        body = scripts.get(key)
        if not body or (pkg_dir, key) in seen:
            continue
        seen.add((pkg_dir, key))
        finding = _scan_script(key, body, pkg_dir, depth, seen)
        if finding is not None and (worst is None or finding.rank() > worst.rank()):
            worst = finding
    return worst

def _scan_script(key: str, body: str, pkg_dir: str, depth: int,
                 seen: set[tuple[str, str]]) -> _ScriptFinding | None:
    if len(body) > MAX_SCRIPT_CHARS:
        return _ScriptFinding(key, unresolved="script too long to check")
    hit = _regex_hit(body)
    worst = _ScriptFinding(key, hit=hit) if hit is not None else None
    if worst is not None and worst.rank() == 2:
        return worst
    # 스크립트 안의 `npm run build` 같은 중첩 실행도 따라간다
    seg_cwd: str | None = pkg_dir
    for seg in _split_chain(body):
        nested = _check_scripts(" ".join(seg.split()), seg_cwd, depth + 1, seen)
        if nested is not None and (worst is None or nested.rank() > worst.rank()):
            worst = _ScriptFinding(f"{key} → {nested.key}", nested.hit, nested.unresolved)
        seg_cwd = _next_cwd(seg, seg_cwd)
    return worst

def _script_reason(f: _ScriptFinding, ko: bool) -> str:
    if f.hit is not None:
        msg = f.hit.message if (ko and f.hit.message) else (f.hit.message_en or f.hit.message)
    else:
        msg = f.unresolved or ""
    return f"package.json scripts.{f.key}: {msg}"

def judge_offline(tool_call: str, fail_closed: bool = False, cwd: str | None = None) -> Verdict:
    """Regex + safe-command check without the model, for hooks whose server is down.

    Regex-flagged calls are blocked and safe commands allowed. Anything else
    can't be judged without the model: allowed, or blocked with fail_closed.
    With cwd, the package.json scripts a safe command runs are checked too.
    """
    if _oversized(tool_call):
        return _oversized_verdict(tool_call, mock=False)
    ko = _is_korean(tool_call)
    hit = _regex_hit(tool_call)
    if hit is not None:
        reason = hit.message if (ko and hit.message) else (hit.message_en or hit.message)
        return Verdict(action="block", risk=0.92, policy_id=hit.id, reason=reason,
                       verdict_source="offline")
    judged = True
    seg_cwd = cwd
    for seg in _split_chain(tool_call):
        if not _safe_command(seg):
            judged = False
        elif seg_cwd is not None:
            finding = _check_scripts(" ".join(seg.split()), seg_cwd)
            if finding is not None and finding.hit is not None:
                return Verdict(action="block", risk=0.92, policy_id=finding.hit.id,
                               reason=_script_reason(finding, ko), verdict_source="offline")
            if finding is not None:
                judged = False
        seg_cwd = _next_cwd(seg, seg_cwd)
    if judged:
        reason = "안전 명령 목록 (서버 오프라인)" if ko else "Safe command list (server offline)"
        return Verdict(action="allow", risk=0.05, policy_id="general", reason=reason,
                       verdict_source="offline")
    action = "block" if fail_closed else "allow"
    if ko:
        reason = "서버 오프라인 — 모델 심판 없이 " + ("차단" if fail_closed else "통과")
    else:
        reason = "Server offline — not judged by the model, " + (
            "blocked" if fail_closed else "allowed")
    return Verdict(action=action, risk=0.5, policy_id="offline", reason=reason,
                   verdict_source="offline")

def judge(tool_call: str, source: str = "user", cwd: str | None = None) -> Verdict:
    """Judge one tool call. With cwd (the directory it runs in), safe commands
    that run package.json scripts have those scripts checked too."""
    if _oversized(tool_call):
        return _oversized_verdict(tool_call, mock=is_mock())
    segments = _split_chain(tool_call)
    if len(segments) == 1:
        return _judge_one(tool_call, source, cwd)
    # 체인은 명령마다 따로 심판해 가장 나쁜 결과를 쓴다.
    # 명령 경계를 넘는 패턴(curl …; … | sh)은 전체 문자열 정규식이 잡는다.
    verdicts = []
    seg_cwd = cwd
    for seg in segments:
        verdicts.append(_judge_one(seg, source, seg_cwd))
        seg_cwd = _next_cwd(seg, seg_cwd)
    hit = _regex_hit(tool_call)
    if hit is not None and all(v.policy_id != hit.id for v in verdicts):
        verdicts.append(_judge_one(tool_call, source))
    return max(verdicts, key=lambda v: (_ACTION_RANK[v.action], v.risk))

def _judge_one(tool_call: str, source: str, cwd: str | None = None) -> Verdict:
    _ensure()
    hit = _regex_hit(tool_call)
    regex_block = hit is not None and hit.action_on_regex == "block"
    safe = hit is None and _safe_command(tool_call)
    mock = is_mock()
    if safe and cwd is not None:
        finding = _check_scripts(" ".join(tool_call.split()), cwd)
        if finding is not None:
            # 정규식 결과만 쓰므로 위험도는 정책 단락과 같은 고정값 (review는 출처 보정에도 review 유지)
            blocked = finding.rank() == 2
            return Verdict(
                action="block" if blocked else "review", risk=0.92 if blocked else 0.55,
                policy_id=finding.hit.id if finding.hit is not None else "package-scripts",
                reason=_script_reason(finding, _is_korean(tool_call)), mock=mock,
                verdict_source="regex" if finding.hit is not None else "allowlist")
    model_ok = True
    if safe:
        action, risk = "allow", 0.05
    elif mock:
        action, risk = _mock_score(tool_call, hit)
    elif regex_block:
        action, risk = "block", 0.92
    else:
        action, risk, model_ok = _model_or_fallback(tool_call, hit)
        # 정규식 review 적중은 하한선: Laya가 allow라도 review로 올림 (상향만 가능)
        if hit is not None and hit.action_on_regex == "review" and action == "allow":
            action, risk = "review", max(risk, 0.55)
    # 출처 보정: 비신뢰 출처는 임계값 강화 (상향만)
    prof = SOURCE_PROFILES.get(source, SOURCE_PROFILES["user"])
    if action == "allow" and risk >= prof["review"]:
        action = "review"
    if risk >= prof["block"]:
        action = "block"
    if regex_block:
        src_detail = "regex"
    elif safe:
        src_detail = "allowlist"
    elif mock or not model_ok:
        src_detail = "heuristic"
    else:
        src_detail = "model"
    pid = hit.id if hit else "general"
    # reason 언어: 입력에 한글이 있으면 한국어, 아니면 영어 (API 기본값은 영어권 대응)
    ko = _is_korean(tool_call)
    if hit:
        reason = hit.message if (ko and hit.message) else (hit.message_en or hit.message)
    elif safe:
        reason = "안전 명령 목록 (모델 생략)" if ko else "Safe command list (model skipped)"
    elif mock:
        reason = "정규식+휴리스틱 (MOCK)" if ko else "Regex+heuristics (MOCK)"
    elif not model_ok:
        reason = "모델 오류 — 정규식+휴리스틱 대체" if ko else "Model error — regex+heuristics fallback"
    else:
        reason = "Laya 심판 결과" if ko else "Judged by Laya"
    return Verdict(action=action, risk=risk, policy_id=pid, reason=reason, mock=mock,
                   verdict_source=src_detail)
