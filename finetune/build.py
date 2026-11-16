"""Run after finetune/extract.py, finetune/split.py and labeling (train/labels*.jsonl, see finetune/LABELING.md).

train/train.jsonl and train/val.jsonl in laya-train's {state, questions, expected} form, with the exact question
laya-guard asks at inference (guard._laya_predict). Rows: the labeled commands only the model would judge, the
commands the safe lane allows (unless --no-safe-lane), examples you wrote (train/synthetic.jsonl, augment.jsonl,
round3.jsonl), and the desktop click examples kept in the repo (finetune/data/desktop_clicks.jsonl, unless
--no-desktop). Nothing equal or similar (>= 0.85) to a case in bench/, eval/ or tests/data/ is kept, a command is kept
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
    """One partition of agent sessions for all labels: a session's rows all go to train or all to val, so validation
    never shares a session with training. Sessions are picked for val until each label has about val_share of its rows
    there, never taking a label's last training row. Each label must come from at least two sessions."""
    random.seed(0)
    groups = collections.defaultdict(list)
    for d in data:
        groups[d["group"]].append(d)
    count = collections.Counter(d["label"] for d in data)
    spans = collections.Counter(lab for g in groups.values() for lab in {d["label"] for d in g})
    for lab in sorted(count):
        if spans[lab] < 2:
            raise SystemExit(f"rows labeled {lab!r} come from {spans[lab]} session(s): need at least two, so one can "
                             "be held out while another stays in training")
    target = {lab: max(1, round(n * val_share)) for lab, n in count.items()}
    in_val, left = collections.Counter(), collections.Counter(count)
    keys = sorted(groups); random.shuffle(keys)
    val_keys = set()
    # First pass: a session only if it doesn't push a label far past its target (sessions are big and mostly allow);
    # second pass, only for a label still missing from val: any session that has it.
    for capped in (True, False):
        for k in keys:
            if k in val_keys:
                continue
            labs = collections.Counter(d["label"] for d in groups[k])
            wanted = [l for l in labs if in_val[l] < target[l]] if capped else [l for l in labs if not in_val[l]]
            over = capped and any(in_val[l] + n > max(1.5 * target[l], target[l] + 2) for l, n in labs.items())
            if not wanted or over or any(left[l] - n < 1 for l, n in labs.items()):
                continue
            val_keys.add(k); in_val.update(labs); left.subtract(labs)
    if missing := sorted(l for l in count if not in_val[l]):
        raise SystemExit(f"no session could be held out for {missing} without leaving it out of training")
    train, val = [], []
    for k in keys:
        for d in groups[k]:
            if k in val_keys:
                val.append(d)
            else:
                train += [d] * (oversample if d["label"] != "allow" else 1)
    return train, val

def main():
    ap = argparse.ArgumentParser(description="train/train.jsonl and train/val.jsonl for laya-train")
    ap.add_argument("--no-safe-lane", action="store_true",
                    help="leave out commands the safe lane allows: the model never sees them in use")
    ap.add_argument("--no-desktop", action="store_true",
                    help="leave out the desktop click examples (finetune/data/desktop_clicks.jsonl)")
    ap.add_argument("--oversample", type=int, default=1,
                    help="repeat review and block rows this many times in train (not val), when allow dominates")
    args = ap.parse_args()
    if args.oversample < 1:
        ap.error("--oversample must be 1 or more")
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
                # masked like the transcript commands: a written example can hold an email or a path too
                candidates.append({"command": common.mask(d["command"]), "label": d["label"], "source": name,
                                   "group": d["command"]})
    if not args.no_desktop:
        # desktop clicks: written examples kept in the repo (finetune/desktop_data.py). A click whose label the desktop
        # eval set judges on its own is left out: those sentences differ only in the label, so the >= 0.85 likeness
        # below can't tell them apart from the eval cases, and it would drop nearly all of them.
        import desktop_data
        held_out = desktop_data.held_out_labels()
        for line in open(desktop_data.OUT, encoding="utf-8"):
            d = json.loads(line)
            if desktop_data.click_label(d["command"]).lower() not in held_out:
                # grouped by the button's label, so "Send" in Mail, in Messages and with no app all fall on one side
                # of the split: grouped by sentence, 69 of val's 82 clicks had a label also in training
                candidates.append({"command": d["command"], "label": d["label"], "source": "desktop",
                                   "group": "click:" + desktop_data.click_label(d["command"]).lower()})
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
    # Clicks are split on their own and never repeated: mixed in, they reshuffle which shell sessions land in val (the
    # first click round moved 113 shell rows out of training), and repeating their many reviews tilted the whole model
    # toward review (everyday commands the model allowed fell from 44/50 to 27/50).
    clicks = [d for d in data if d["source"] == "desktop"]
    train, val = split([d for d in data if d["source"] != "desktop"], oversample=args.oversample)
    if clicks:
        click_train, click_val = split(clicks)
        train, val = train + click_train, val + click_val
    out = lambda items: [{"state": d["command"], "questions": {"action": QUESTIONS["action"]},
                          "expected": {"action": d["label"]}, "source": d["source"]} for d in items]
    common.write_jsonl({common.TRAIN / "train.jsonl": out(train), common.TRAIN / "val.jsonl": out(val)})
    for name, items in (("train", train), ("val", val)):
        print(name, len(items), dict(collections.Counter(d["label"] for d in items)))

main()
