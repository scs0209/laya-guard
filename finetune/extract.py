"""Shell commands your agents ran (Claude Code and Codex transcripts, read by laya-guard's audit), from the project
folders you choose, deduplicated, with secrets and the home path masked and anything that overlaps the evaluation sets
dropped. Writes train/commands.jsonl (train/ is git-ignored: this is your data).

    python finetune/extract.py --projects ~/Projects --exclude acme,clientname

--projects: only sessions whose working directory is under this folder (default ~/Projects).
--exclude:  words that drop a session folder or a command that contains them, e.g. your employer's or a client's name,
            so work never ends up in a model you might share."""
import argparse, json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from laya_guard import audit

HOME = Path.home()
EXCLUDED_PARTS: tuple = ()                          # set from --exclude
PROJECTS = "~/Projects/"                            # set from --projects
SECRETS = [
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "<GITHUB_TOKEN>"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "<HF_TOKEN>"),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}\b"), "<API_KEY>"),
    (re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}\b"), "<SLACK_TOKEN>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<AWS_KEY>"),
    (re.compile(r"(?i)\b(bearer|token|password|passwd|secret|api[_-]?key)(\s*[:=]\s*|\s+)['\"]?[^\s'\"]{8,}"), r"\1\2<SECRET>"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<EMAIL>"),
    (re.compile(r"(?<![\w/])[A-Za-z0-9+/_-]{40,}={0,2}(?![\w/])"), "<LONG_STRING>"),
]

def mask(cmd: str) -> str:
    cmd = cmd.replace(str(HOME), "~")
    cmd = re.sub(r"/Users/[^/\s'\"]+", "~", cmd)
    for rx, rep in SECRETS:
        cmd = rx.sub(rep, cmd)
    return cmd

def personal(cwd: str | None) -> bool:
    if not cwd:
        return False
    rel = cwd.replace(str(HOME), "~")
    return rel.startswith(PROJECTS) and not any(p in rel.lower() for p in EXCLUDED_PARTS)

def eval_texts() -> list[str]:
    out = [json.loads(l)["command"] for l in open(ROOT / "bench/cases.jsonl") if l.strip()]
    for f in ("incidents", "ko_30", "shell"):
        out += [json.loads(l)["tool_call"] for l in open(ROOT / f"eval/testset_{f}.jsonl") if l.strip()]
    for f in ("never_blocked", "rewrites_blocked"):
        out += [json.loads(l)["command"] for l in open(ROOT / f"tests/data/{f}.jsonl") if l.strip()]
    return [" ".join(t.split()) for t in out]

def main():
    global EXCLUDED_PARTS, PROJECTS
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--projects", default="~/Projects")
    ap.add_argument("--exclude", default="", help="comma-separated words")
    args = ap.parse_args()
    PROJECTS = str(Path(args.projects).expanduser()).replace(str(HOME), "~").rstrip("/") + "/"
    EXCLUDED_PARTS = tuple(w.strip().lower() for w in args.exclude.split(",") if w.strip())
    (ROOT / "train").mkdir(exist_ok=True)
    calls = []
    for fn in (audit.claude_calls, audit.codex_calls):
        calls += fn(HOME, None)[0]
    evals = eval_texts()
    seen, rows, dropped = set(), [], {"not personal": 0, "too long": 0, "overlaps eval": 0, "duplicate": 0}
    for c in calls:
        if not personal(c.cwd):
            dropped["not personal"] += 1; continue
        cmd = mask(c.command.strip())
        if len(cmd) > 1500:
            dropped["too long"] += 1; continue
        key = " ".join(cmd.split())
        if any(part in key.lower() for part in EXCLUDED_PARTS):
            dropped["excluded word"] = dropped.get("excluded word", 0) + 1; continue
        if any(e and e in key for e in evals) or key in evals:
            dropped["overlaps eval"] += 1; continue
        if key in seen:
            dropped["duplicate"] += 1; continue
        seen.add(key)
        rows.append({"command": cmd, "project": c.cwd.replace(str(HOME), "~").split("/")[2], "status": c.status})
    with open(ROOT / "train/commands.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("kept", len(rows), "dropped", dropped)

main()
