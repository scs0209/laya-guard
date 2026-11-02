"""Run after finetune/extract.py, finetune/split.py and labeling (train/labels*.jsonl, see finetune/LABELING.md).

train/train.jsonl and train/val.jsonl in laya-train's {state, questions, expected} form, with the exact questions
laya-guard asks at inference (guard._laya_predict). Labels: the drafted labels for commands only the model would
judge, the rule's answer for commands the safe lane allows, and the synthetic risky examples. The repo's evaluation
sets (bench/, eval/, tests/data/) are never in here; val is a held-out slice of this data, stratified by label."""
import glob, json, random, sys, collections, inspect, re
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from laya_guard import guard

# the questions dict, copied out of _laya_predict's source so training and inference can't drift apart
src = inspect.getsource(guard._laya_predict)
QUESTIONS = eval(src[src.index("questions = {") + len("questions = "):src.index("\n    r = _router.predict")].strip())

rows = [json.loads(l) for l in open(ROOT / "train/commands.jsonl")]
labels = {}
for f in sorted(glob.glob(str(ROOT / "train/labels*.jsonl"))):
    for l in open(f):
        d = json.loads(l); labels[d["i"]] = d["label"]
data = []
for i, r in enumerate(rows):
    if r["rule"] == "model" and i in labels:
        data.append((r["command"], labels[i], "real"))
    elif r["rule"] == "allow:safe":
        data.append((r["command"], "allow", "real-safe-lane"))
# Written examples (synthetic.jsonl, augment.jsonl) must not copy or closely paraphrase an evaluation case.
import difflib
EVAL = [json.loads(l)["command"] for l in open(ROOT / "bench/cases.jsonl") if l.strip()]
for f in ("incidents", "ko_30", "shell"):
    EVAL += [json.loads(l)["tool_call"] for l in open(ROOT / f"eval/testset_{f}.jsonl") if l.strip()]
for f in ("never_blocked", "rewrites_blocked"):
    EVAL += [json.loads(l)["command"] for l in open(ROOT / f"tests/data/{f}.jsonl") if l.strip()]
EVAL = [" ".join(e.split()).lower() for e in EVAL]
def near_eval(cmd):
    c = " ".join(cmd.split()).lower()
    return any(c == e or difflib.SequenceMatcher(None, c, e).ratio() >= 0.85 for e in EVAL)
too_close = []
for name in ("synthetic", "augment"):              # optional: examples you wrote yourself
    if not (ROOT / f"train/{name}.jsonl").exists():
        continue
    for l in open(ROOT / f"train/{name}.jsonl"):
        d = json.loads(l)
        if near_eval(d["command"]):
            too_close.append(d["command"]); continue
        data.append((d["command"], d["label"], name))
print("dropped as too close to an evaluation case:", too_close)

random.seed(0)
by = collections.defaultdict(list)
for d in data: by[d[1]].append(d)
train, val = [], []
for lab, items in by.items():
    random.shuffle(items); k = max(1, round(len(items) * 0.15))
    val += items[:k]; train += items[k:]
def write(path, items):
    with open(path, "w") as f:
        for cmd, lab, srcname in items:
            f.write(json.dumps({"state": cmd, "questions": {"action": QUESTIONS["action"]}, "expected": {"action": lab},
                                "source": srcname}, ensure_ascii=False) + "\n")
write(ROOT / "train/train.jsonl", train); write(ROOT / "train/val.jsonl", val)
print("train", len(train), collections.Counter(x[1] for x in train)); print("val", len(val), collections.Counter(x[1] for x in val))
