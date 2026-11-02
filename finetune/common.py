"""What the finetune scripts share: masking, a command's stable id, the evaluation sets they must never train on, and
writing private files in one piece."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAIN = ROOT / "train"
HOME = Path.home()

# Values that name or prove an identity. A placeholder replaces the value; the name before it stays, so the model still
# sees that a secret was there.
_SECRET_NAME = r"[A-Za-z0-9_]*(?:key|token|secret|password|passwd|pwd|credential|auth|client_id|dsn|cookie|session)[A-Za-z0-9_]*"
SECRETS = [
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "<GITHUB_TOKEN>"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "<HF_TOKEN>"),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}\b"), "<API_KEY>"),
    (re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}\b"), "<SLACK_TOKEN>"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "<AWS_KEY>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), "<JWT>"),
    # scheme://user:password@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@'\"]+:[^@\s'\"/]+@"), r"\1<USER>:<SECRET>@"),
    # Authorization: Bearer x, -H 'X-Api-Key: x'
    (re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic|token)\s+)[^\s'\"]+"), r"\1<SECRET>"),
    (re.compile(r"(?i)((?:x-)?(?:api|auth)[-_]?(?:key|token)\s*:\s*)[^\s'\"]+"), r"\1<SECRET>"),
    # --password x, --token=x, --api-key x
    (re.compile(r"(?i)(--(?:password|passwd|token|api[-_]?key|secret|client[-_]secret|auth[-_]token)(?:=|\s+))"
                r"(?![<$])[^\s'\"]+"), r"\1<SECRET>"),
    # NAME="a value with spaces", 'name': 'value'
    (re.compile(rf"(?i)([\"']?{_SECRET_NAME}[\"']?\s*[:=]\s*)([\"'])(?![<$])(?:(?!\2).){{4,}}\2"), r"\1\2<SECRET>\2"),
    # NAME=value (stops at a quote, a space or a backslash, so `\nNEXT=` in a printf isn't eaten)
    (re.compile(rf"(?i)(\b{_SECRET_NAME}\s*=\s*)(?![<$\"'\\])[^\s'\"\\]{{4,}}"), r"\1<SECRET>"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<EMAIL>"),
    # an opaque string of 24+ characters with both letters and digits: an id, a key, a hash
    (re.compile(r"(?<![\w/.-])(?=[A-Za-z0-9+/_-]*\d)(?=[A-Za-z0-9+/_-]*[A-Za-z])[A-Za-z0-9+/_-]{24,}={0,2}(?![\w/.-])"),
     "<LONG_STRING>"),
]

def mask(cmd: str) -> str:
    cmd = cmd.replace(str(HOME), "~")
    cmd = re.sub(r"/Users/[^/\s'\"]+|/home/[^/\s'\"]+", "~", cmd)
    for rx, rep in SECRETS:
        cmd = rx.sub(rep, cmd)
    return cmd

def command_id(raw_command: str) -> str:
    """A command's stable id, from the command as it ran (before masking, so a masking change doesn't move labels).
    Only the hash is stored."""
    return hashlib.sha256(" ".join(raw_command.split()).encode()).hexdigest()[:16]

def _norm(text: str) -> str:
    return " ".join(text.split()).lower()

def eval_texts() -> list[str]:
    """Every case in the benchmark, the eval sets and the test fixtures, normalized: nothing like these may be trained on."""
    out = [json.loads(l)["command"] for l in open(ROOT / "bench/cases.jsonl") if l.strip()]
    for f in ("incidents", "ko_30", "shell"):
        out += [json.loads(l)["tool_call"] for l in open(ROOT / f"eval/testset_{f}.jsonl") if l.strip()]
    for f in ("never_blocked", "rewrites_blocked"):
        out += [json.loads(l)["command"] for l in open(ROOT / f"tests/data/{f}.jsonl") if l.strip()]
    return [_norm(t) for t in out]

def near_eval(text: str, evals: list[str], ratio: float = 0.85) -> bool:
    """Equal to, containing, or as similar as `ratio` (case-insensitive) to an evaluation case."""
    t = _norm(text)
    for e in evals:
        if not e:
            continue
        if t == e or e in t:
            return True
        m = difflib.SequenceMatcher(None, t, e)
        if m.real_quick_ratio() >= ratio and m.quick_ratio() >= ratio and m.ratio() >= ratio:
            return True
    return False

def private_dir() -> Path:
    """train/, readable by you only."""
    os.umask(0o077)
    TRAIN.mkdir(mode=0o700, exist_ok=True)
    os.chmod(TRAIN, 0o700)
    return TRAIN

def write_jsonl(files: dict[Path, list[dict]]) -> None:
    """Write several JSONL files so they change together: each goes to a temporary file first (owner-only), and they
    are renamed into place only once all are written, so an interruption never pairs a new file with an old one."""
    tmps = {}
    try:
        for path, rows in files.items():
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
            with os.fdopen(fd, "w") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmps[path] = tmp
        for path, tmp in tmps.items():
            os.replace(tmp, path)
    finally:
        for tmp in tmps.values():
            if os.path.exists(tmp):
                os.unlink(tmp)
