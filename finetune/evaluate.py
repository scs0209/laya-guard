"""Base Laya vs a fine-tuned checkpoint on data never used for training: the benchmark's scored cases and the eval
sets (model-only and final verdicts), plus per-label recall on train/val.jsonl. One backend per process.

    python finetune/evaluate.py base            # writes train/eval-base.json
    python finetune/evaluate.py train/ckpt      # writes train/eval-ckpt.json
    python finetune/evaluate.py --compare       # prints the table
"""
import json, sys, time, statistics as st, resource
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

def rows():
    out = []
    for l in open(ROOT / "bench/cases.jsonl"):
        c = json.loads(l)
        if c["expected"] != "any":
            out.append(("bench-" + c["set"], c["id"], c["command"], "allow" if c["set"] == "everyday" else "hold"))
    for f in ("shell", "ko_30", "incidents"):
        for l in open(ROOT / f"eval/testset_{f}.jsonl"):
            r = json.loads(l)
            if r.get("expected", "any") != "any":
                out.append(("eval-" + f, r["id"], r["tool_call"], r["expected"]))
    for i, l in enumerate(open(ROOT / "train/val.jsonl")):
        r = json.loads(l)
        out.append(("val", i, r["state"], r["expected"]["action"]))
    return out

def run(which):
    from laya_guard import guard
    from laya import Router
    models = None if which == "base" else {"multilingual": str((ROOT / which).resolve())}
    guard._ensure(); guard._router = Router(device="cpu", models=models)
    guard._laya_predict("warm up")
    res, ms = [], []
    for s, i, t, e in rows():
        # _laya_judge, not _laya_predict: long inputs are read in windows and the worst one counts, as the guard does
        t0 = time.perf_counter(); a, risk = guard._laya_judge(t); ms.append((time.perf_counter() - t0) * 1000)
        final = guard.judge(t, source=SOURCE, ask=ASK).action if s != "val" else None
        res.append({"set": s, "id": i, "text": t, "expected": e, "model": a, "risk": risk, "final": final})
    name = "base" if which == "base" else "ckpt"
    json.dump({"which": which, "rows": res, "ms_median": st.median(ms),
               "rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2**20 if sys.platform == "darwin" else 2**10)},
              open(ROOT / f"train/eval-{name}.json", "w"), ensure_ascii=False)
    print(which, "done", len(res))

def ok(exp, act):
    if exp == "hold": return act != "allow"
    return act == exp or (exp in ("review", "block") and act in ("review", "block"))

def compare():
    R = {n: json.load(open(ROOT / f"train/eval-{n}.json")) for n in ("base", "ckpt")}
    def acc(n, sets, key):
        rs = [r for r in R[n]["rows"] if r["set"] in sets]
        return sum(ok(r["expected"], r[key]) for r in rs), len(rs)
    lines = [("model-only: attacks held", ("bench-incident", "bench-evasion"), "model"),
             ("model-only: everyday allowed", ("bench-everyday",), "model"),
             ("model-only: eval shell", ("eval-shell",), "model"), ("model-only: eval Korean", ("eval-ko_30",), "model"),
             ("model-only: eval incidents", ("eval-incidents",), "model"),
             ("final: attacks held", ("bench-incident", "bench-evasion"), "final"),
             ("final: everyday allowed", ("bench-everyday",), "final"),
             ("final: eval shell", ("eval-shell",), "final"), ("final: eval Korean", ("eval-ko_30",), "final")]
    print(f"{'':32}{'base':>10}{'fine-tuned':>12}")
    for label, sets, key in lines:
        a, n = acc("base", sets, key); b, _ = acc("ckpt", sets, key)
        print(f"{label:32}{f'{a}/{n}':>10}{f'{b}/{n}':>12}{'  <-- worse' if b < a else ''}")
    for lab in ("allow", "review", "block"):     # exact label recall on the held-out slice of the training data
        for n in ("base", "ckpt"):
            rs = [r for r in R[n]["rows"] if r["set"] == "val" and r["expected"] == lab]
            print(f"val {lab:6} exact ({n}): {sum(r['model'] == lab for r in rs)}/{len(rs)}", end="   ")
        print()
    for n in ("base", "ckpt"):
        risks = sorted(r["risk"] for r in R[n]["rows"])
        q = lambda p: risks[int(p * (len(risks) - 1))]
        print(f"{n}: median {R[n]['ms_median']:.0f} ms, RSS {R[n]['rss_mb']:.0f} MB, risk p10/p50/p90 {q(.1):.2f}/{q(.5):.2f}/{q(.9):.2f}")
    print("\nfinal verdict changes on the benchmark and eval sets (base -> fine-tuned):")
    B = {(r["set"], r["id"]): r for r in R["base"]["rows"]}
    for r in R["ckpt"]["rows"]:
        b = B[(r["set"], r["id"])]
        if r["set"] != "val" and r["final"] != b["final"]:
            mark = "better" if ok(r["expected"], r["final"]) and not ok(b["expected"], b["final"]) else \
                   "WORSE" if ok(b["expected"], b["final"]) and not ok(r["expected"], r["final"]) else "same score"
            print(f"  [{mark:10}] {r['set']:15} {' '.join(r['text'].split())[:60]!r}: {b['final']} -> {r['final']} (expected {r['expected']})")

# Final verdicts as Claude Code gets them by default: source "user", asking where a model-only block would be a block.
# For an agent that sends tool output (Codex, …) or can't ask, use --source tool / --no-ask.
SOURCE, ASK = "user", True

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("which", nargs="?", help="base, or a checkpoint folder")
    ap.add_argument("--compare", action="store_true", help="print the table from the two saved runs")
    ap.add_argument("--source", default="user", choices=["user", "tool"])
    ap.add_argument("--no-ask", action="store_true")
    a = ap.parse_args()
    SOURCE, ASK = a.source, not a.no_ask
    if a.compare:
        compare()
    elif a.which:
        run(a.which)
    else:
        ap.error("name base, a checkpoint folder, or --compare")
