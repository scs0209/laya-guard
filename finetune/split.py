"""Which commands the rules already decide (no model) and which only the model would judge: those need a label.
Judged in each command's own directory, so `npm test` is checked against that project's package.json scripts.

    python finetune/split.py               # adds `rule` to train/commands.jsonl
    python finetune/split.py --batches 6   # also writes train/batch<N>.jsonl of the commands that need a label"""
import argparse, collections, os, sys
from pathlib import Path
os.environ["LAYA_GUARD_MOCK"] = "1"                          # the rules only
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
sys.path.insert(0, str(common.ROOT / "src"))
from laya_guard import guard

def rule_answer(command: str, cwd: str | None) -> str:
    """As scripts/rule_snapshot.py answers, but in the command's directory. When that folder is gone, the package
    scripts a command runs can't be checked, so a person labels it rather than the safe lane allowing it."""
    gone = bool(cwd) and not os.path.isdir(cwd)
    v = guard.judge(command, ask=True, cwd=None if gone else cwd)
    if v.verdict_source == "regex":
        return f"{v.action}:{v.policy_id}"
    if v.verdict_source == "allowlist":                      # a package script it runs can still be held
        if v.action == "allow":
            return "model" if gone else "allow:safe"
        return f"{v.action}:{v.policy_id}"
    if v.policy_id not in ("general", "offline"):
        return f"review:{v.policy_id}"
    return "model"

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--batches", type=int, default=0, help="split the commands that need a label into this many files")
    args = ap.parse_args()
    common.private_dir()
    import json
    rows = [json.loads(l) for l in open(common.TRAIN / "commands.jsonl")]
    for r in rows:
        r["rule"] = rule_answer(r["command"], r.get("cwd"))
    files = {common.TRAIN / "commands.jsonl": rows}
    need = [{"id": r["id"], "project": r["project"], "command": r["command"]} for r in rows if r["rule"] == "model"]
    if args.batches:
        size = -(-len(need) // args.batches)
        for b in range(args.batches):
            files[common.TRAIN / f"batch{b + 1}.jsonl"] = need[b * size:(b + 1) * size]
    common.write_jsonl(files)
    if args.batches:                                         # an earlier, larger --batches leaves files to label twice
        for stale in common.TRAIN.glob("batch*.jsonl"):
            if stale not in files:
                stale.unlink()
    print(dict(collections.Counter(r["rule"].split(":")[0] for r in rows)), "| need a label:", len(need))

main()
