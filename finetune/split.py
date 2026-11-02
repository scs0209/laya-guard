"""Which commands the rules already decide (no model) and which only the model would judge: those need a label."""
import json, os, sys, collections
from pathlib import Path
os.environ["LAYA_GUARD_MOCK"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import rule_snapshot
rows = [json.loads(l) for l in open(ROOT / "train/commands.jsonl")]
for r in rows:
    r["rule"] = rule_snapshot.rule_answer(r["command"])
c = collections.Counter(r["rule"].split(":")[0] for r in rows)
print(dict(c)); print(collections.Counter(r["rule"] for r in rows if not r["rule"].startswith(("allow", "model"))).most_common(10))
with open(ROOT / "train/commands.jsonl", "w") as f:
    for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
