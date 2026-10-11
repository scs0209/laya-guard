"""MOCK/실전 평가 러너: python eval/run_eval.py [--set ko|shell] [--source user|tool] [--min-acc 1.0]"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from laya_guard.guard import judge, is_mock

SETS = {
    "ko": "testset_ko_30.jsonl",      # 한국어 자연어 요청 30문항
    "shell": "testset_shell.jsonl",   # 일상 개발 명령 33 + 위험/우회 명령 27 (체인 10 포함)
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=sorted(SETS), default="ko")
    ap.add_argument("--source", default="user",
                    help="source passed to judge (adapters send 'tool')")
    ap.add_argument("--min-acc", type=float, default=None,
                    help="fail with exit 1 if accuracy is below this (for CI)")
    args = ap.parse_args()
    p = Path(__file__).parent / SETS[args.set]
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    ok = 0
    fails = []
    for r in rows:
        v = judge(r["tool_call"], source=args.source)
        good = (v.action == r["expected"]) or (r["expected"] in ("review", "block") and v.action in ("review", "block"))
        ok += good
        if not good:
            fails.append((r["id"], r["tool_call"][:50], r["expected"], v.action, v.policy_id))
    acc = ok / len(rows)
    print(f"set={args.set} source={args.source} mock={is_mock()} acc={ok}/{len(rows)} = {acc:.2f}")
    for f in fails:
        print("FAIL", f)
    if args.min_acc is not None and acc < args.min_acc:
        sys.exit(1)

if __name__ == "__main__":
    main()
