#!/usr/bin/env python3
"""Summarize observe.log: verdict counts, latency, and the commands that were flagged.

usage: observe-report.py [--top N] [--all]   (default: top 20 flagged commands)
"""
import argparse
import collections

from observe_common import OBSERVE_LOG, read_jsonl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--all", action="store_true", help="list allow calls as well")
    args = ap.parse_args()

    rows = read_jsonl(OBSERVE_LOG)
    if not rows:
        print(f"No records in {OBSERVE_LOG}")
        return 1

    counts = collections.Counter(r.get("verdict") for r in rows)
    total = len(rows)
    print(f"{rows[0].get('ts')} ~ {rows[-1].get('ts')} | {total} calls")
    for verdict in ("allow", "review", "block", "error"):
        if counts[verdict]:
            print(f"  {verdict:<7}{counts[verdict]:>6}  ({counts[verdict] / total:.1%})")
    latency = sorted(r.get("ms", 0) for r in rows)
    p95 = latency[max(int(len(latency) * 0.95) - 1, 0)]
    print(f"  latency median {latency[len(latency) // 2]}ms | p95 {p95}ms | "
          f"offline fallback {sum(1 for r in rows if r.get('offline'))}")

    flagged = rows if args.all else [r for r in rows if r.get("verdict") in ("review", "block", "error")]
    groups = collections.Counter(
        (r.get("verdict"), r.get("policy") or "-", r.get("tool") or "-", r.get("command") or "") for r in flagged)
    print(f"\n{'all' if args.all else 'flagged'} commands (top {args.top}):")
    for (verdict, policy, tool, command), n in groups.most_common(args.top):
        print(f"  [{verdict:<6}] x{n:<3} {policy:<16} {tool:<6} {command[:110]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
