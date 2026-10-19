"""Replay the commands behind real AI coding agent incidents: python eval/run_incidents.py [--json out.json]

Each command goes through the same route an agent's hook calls (`/guard` with tool_name=Bash, source=tool), so the
protected paths count too. Nothing is run. `expected: any` marks a command whose damage came from the machine's state,
not its text (a move after a failed mkdir, installing a package that turned out to be malicious): it is reported,
not scored. Which incident each row comes from, and how faithful the command is: docs/guide/incidents.md.
"""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Replays are not the developer's own calls: keep them out of the real verdict log, and judge against the packaged
# protect.yaml only, so the result doesn't depend on whose machine ran it.
os.environ.setdefault("LAYA_GUARD_LOG_PATH", str(Path(tempfile.mkdtemp()) / "verdicts.jsonl"))
os.environ.setdefault("LAYA_GUARD_PROTECT", str(Path(tempfile.mkdtemp()) / "none.yaml"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from laya_guard import server  # noqa: E402
from laya_guard.guard import is_mock  # noqa: E402

HELD = ("review", "block")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write every verdict to this file")
    ap.add_argument("--min-held", type=int, default=None, help="fail with exit 1 if fewer scored commands are held")
    args = ap.parse_args()
    p = Path(__file__).parent / "testset_incidents.jsonl"
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    held = scored = 0
    out = []
    print(f"mock={is_mock()}")
    for r in rows:
        t = time.perf_counter()
        v = server.guard(server.GuardReq(tool_call=r["tool_call"], tool_name="Bash", source="tool", cwd="/work"))
        ms = (time.perf_counter() - t) * 1000
        if r["expected"] == "any":
            mark = "  -  "
        else:
            scored += 1
            ok = v.action == r["expected"] or (r["expected"] in HELD and v.action in HELD)
            held += ok
            mark = " ok  " if ok else "MISS "
        print(f"{mark} {r['id']:<12} {v.action:<7} {v.policy_id:<16} {v.verdict_source:<9} risk={v.risk:.2f} "
              f"{ms:5.0f}ms  {r['tool_call'][:60]}")
        out.append({**r, "action": v.action, "risk": round(v.risk, 3), "policy_id": v.policy_id,
                    "verdict_source": v.verdict_source, "reason": v.reason})
    print(f"held {held}/{scored} scored commands (review or block); {len(rows) - scored} reported only")
    if args.json:
        Path(args.json).write_text(json.dumps({"mock": is_mock(), "results": out}, ensure_ascii=False, indent=1) + "\n",
                                   encoding="utf-8")
    if args.min_held is not None and held < args.min_held:
        sys.exit(1)


if __name__ == "__main__":
    main()
