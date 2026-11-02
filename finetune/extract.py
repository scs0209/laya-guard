"""Shell commands your agents ran (Claude Code and Codex transcripts, read by laya-guard's audit), from the project
folders you choose, deduplicated, with secrets and the home path masked and anything like the evaluation sets
dropped. Writes train/commands.jsonl, readable by you only (train/ is git-ignored: this is your data).

    python finetune/extract.py --projects ~/Projects --exclude acme,clientname

--projects: only sessions whose working directory is this folder or under it (default ~/Projects).
--exclude:  words that drop a session folder or a command containing them, e.g. your employer's or a client's name,
            so work never ends up in a model you might share. Checked against the command as it ran, before masking.

Each row has an `id`: a hash of the command as it ran. Labels refer to it, so re-running this (new sessions, a masking
change) never moves a label onto another command."""
import argparse, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
sys.path.insert(0, str(common.ROOT / "src"))
from laya_guard import audit

def in_projects(cwd: str | None, root: str, excluded: tuple) -> bool:
    if not cwd or not os.path.isabs(cwd):
        return False
    path = os.path.normpath(cwd)                           # `~/Projects/../client` is not under ~/Projects
    return (path == root or path.startswith(root + os.sep)) and not any(w in path.lower() for w in excluded)

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--projects", default="~/Projects")
    ap.add_argument("--exclude", default="", help="comma-separated words")
    ap.add_argument("--max-chars", type=int, default=1500, help="longer commands are left out")
    args = ap.parse_args()
    root = os.path.normpath(str(Path(args.projects).expanduser().resolve()))
    excluded = tuple(w.strip().lower() for w in args.exclude.split(",") if w.strip())
    common.private_dir()

    calls, files = [], 0
    for fn in (audit.claude_calls, audit.codex_calls):
        got, n = fn(common.HOME, None)
        calls += got; files += n
    evals = common.eval_texts()
    seen, rows = set(), []
    dropped = {"not in --projects": 0, "excluded word": 0, "too long": 0, "like an eval case": 0, "duplicate": 0}
    for c in calls:
        if not in_projects(c.cwd, root, excluded):
            dropped["not in --projects"] += 1; continue
        raw = c.command.strip()
        if any(w in raw.lower() for w in excluded):             # before masking: a masked value can hide the word
            dropped["excluded word"] += 1; continue
        cmd = common.mask(raw)
        if len(cmd) > args.max_chars:
            dropped["too long"] += 1; continue
        cid = common.command_id(raw)
        if cid in seen:
            dropped["duplicate"] += 1; continue
        if common.near_eval(cmd, evals):
            dropped["like an eval case"] += 1; continue
        seen.add(cid)
        rows.append({"id": cid, "command": cmd, "project": os.path.relpath(os.path.normpath(c.cwd), root).split(os.sep)[0],
                     "session": common.command_id(c.session or "")[:12], "status": c.status,
                     "cwd": os.path.normpath(c.cwd)})   # local only, for split.py's package-script check; never exported
    common.write_jsonl({common.TRAIN / "commands.jsonl": rows})
    print(f"read {files} session files; kept {len(rows)}; dropped {dropped}")
    print("Sessions audit couldn't read, or lines it couldn't parse, are skipped silently; compare the count of session "
          "files with what you expect.")

main()
