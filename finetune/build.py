"""Run after finetune/extract.py, finetune/split.py and labeling (train/labels*.jsonl, see finetune/LABELING.md).

train/train.jsonl and train/val.jsonl in laya-train's {state, questions, expected} form, with the exact question
laya-guard asks at inference (guard._laya_predict). Rows: the labeled commands only the model would judge, the
commands the safe lane allows (unless --no-safe-lane), and examples you wrote (train/synthetic.jsonl, augment.jsonl,
round3.jsonl). Nothing equal or similar (>= 0.85) to a case in bench/, eval/ or tests/data/ is kept, a command is kept
once across all sources, and commands from one agent session stay on one side of the train/val split.

Labels are `{"id": <commands.jsonl id>, "label": ..., "why": ...}` lines in train/labels*.jsonl, read in name order
with labels_overrides.jsonl last, so a correction there always wins."""
import argparse, collections, glob, inspect, json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
sys.path.insert(0, str(common.ROOT / "src"))
from laya_guard import guard

# the questions dict, copied out of _laya_predict's source so training and inference can't drift apart
src = inspect.getsource(guard._laya_predict)
QUESTIONS = eval(src[src.index("questions = {") + len("questions = "):src.index("\n    r = _router.predict")].strip())
WRITTEN = ("synthetic", "augment", "round3")

def read_labels(rows_by_id: dict) -> dict:
    files = sorted(glob.glob(str(common.TRAIN / "labels*.jsonl")), key=lambda f: (Path(f).name == "labels_overrides.jsonl", f))
    labels, unknown = {}, 0
    for f in files:
        for line in open(f):
            if not line.strip():
                continue
            d = json.loads(line)
            if "id" not in d:
                raise SystemExit(f"{f}: labels need the command's `id` (from train/commands.jsonl), not a line number: "
                                 "a line number moves when extract.py runs again")
            if d["id"] not in rows_by_id:
                unknown += 1; continue
            labels[d["id"]] = d["label"]
    if unknown:
        print(f"{unknown} labels name a command that is no longer in train/commands.jsonl (left out)")
    return labels

def split(data: list, val_share: float = 0.15, oversample: int = 1) -> tuple[list, list]:
    """Stratified by label; a session's commands stay together, so a command's near-twin can't sit on the other side.
    Every label keeps at least one row in train."""
    random.seed(0)
    train, val = [], []
    by_label = collections.defaultdict(list)
    for d in data:
        by_label[d["label"]].append(d)
    for lab, items in sorted(by_label.items()):
        if len(items) < 2:
            raise SystemExit(f"only {len(items)} example labeled {lab!r}: add more before training")
        groups = collections.defaultdict(list)
        for d in items:
            groups[d["group"]].append(d)
        keys = sorted(groups); random.shuffle(keys)
        target, taken = max(1, round(len(items) * val_share)), []
        for k in keys:
            if len(taken) >= target or len(taken) + len(groups[k]) > len(items) - 1:
                continue
            taken += groups[k]
        in_val = {id(d) for d in taken}
        rest = [d for k in keys for d in groups[k] if id(d) not in in_val]
        val += taken; train += rest * (oversample if lab != "allow" else 1)
    return train, val

def main():
    ap = argparse.ArgumentParser(description="train/train.jsonl and train/val.jsonl for laya-train")
    ap.add_argument("--no-safe-lane", action="store_true",
                    help="leave out commands the safe lane allows: the model never sees them in use")
    ap.add_argument("--oversample", type=int, default=1,
                    help="repeat review and block rows this many times in train (not val), when allow dominates")
    args = ap.parse_args()
    common.private_dir()
    rows = [json.loads(l) for l in open(common.TRAIN / "commands.jsonl")]
    by_id = {r["id"]: r for r in rows}
    labels = read_labels(by_id)
    candidates = []
    for r in rows:
        if r["rule"] == "model" and r["id"] in labels:
            candidates.append({"command": r["command"], "label": labels[r["id"]], "source": "real", "group": r["session"]})
        elif r["rule"] == "allow:safe" and not args.no_safe_lane:
            candidates.append({"command": r["command"], "label": "allow", "source": "real-safe-lane", "group": r["session"]})
    for name in WRITTEN:
        path = common.TRAIN / f"{name}.jsonl"
        if path.exists():
            for line in open(path):
                d = json.loads(line)
                candidates.append({"command": d["command"], "label": d["label"], "source": name, "group": d["command"]})
    evals = common.eval_texts()
    seen, data, near, conflicts = {}, [], 0, 0
    for c in candidates:
        key = " ".join(c["command"].split()).lower()
        if key in seen:                                   # one row per command, whatever the source
            conflicts += seen[key]["label"] != c["label"]
            continue
        if common.near_eval(c["command"], evals):
            near += 1; continue
        seen[key] = c; data.append(c)
    print(f"{len(data)} rows; dropped {near} like an eval case and {len(candidates) - len(data) - near} duplicates "
          f"({conflicts} with a different label: the first source wins, real before written)")
    train, val = split(data, oversample=args.oversample)
    out = lambda items: [{"state": d["command"], "questions": {"action": QUESTIONS["action"]},
                          "expected": {"action": d["label"]}, "source": d["source"]} for d in items]
    common.write_jsonl({common.TRAIN / "train.jsonl": out(train), common.TRAIN / "val.jsonl": out(val)})
    for name, items in (("train", train), ("val", val)):
        print(name, len(items), dict(collections.Counter(d["label"] for d in items)))

main()
