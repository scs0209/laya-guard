"""CLI: laya-guard check / serve / eval."""
from __future__ import annotations
import argparse, dataclasses, json, os, sys, time
from .guard import judge, judge_offline, is_mock, warmup

def cmd_check(args):
    text = " ".join(args.text) if args.text else sys.stdin.read()
    cwd = os.path.abspath(args.cwd)
    if args.offline:
        # LAYA_GUARD_FAIL: open (default) passes what it can't judge, closed blocks it, ask sends it to a human
        unknown = {"closed": "block", "ask": "review"}.get(os.environ.get("LAYA_GUARD_FAIL", ""), "allow")
        t0 = time.perf_counter()
        v = judge_offline(text, unknown=unknown, cwd=cwd)
        _log_offline(v, text, (time.perf_counter() - t0) * 1000.0)
    else:
        v = judge(text, cwd=cwd)
    if args.json:
        print(json.dumps(dataclasses.asdict(v), ensure_ascii=False))
        return 0 if v.action != "block" else 2
    icon = {"allow": "OK", "review": "REVIEW", "block": "BLOCK"}[v.action]
    print(f"[{icon}] {v.action} risk={v.risk} policy={v.policy_id} mock={v.mock}")
    print(f"reason: {v.reason}")
    return 0 if v.action != "block" else 2

def _log_offline(v, text: str, latency_ms: float) -> None:
    """An offline verdict is an outage, not a real judgment: record it (hash and length only, like every
    entry) so it can be counted. A log that can't be written must never stop a hook."""
    from .log import log_verdict
    try:
        log_verdict(action=v.action, risk=v.risk, policy_id=v.policy_id, reason=v.reason, mock=False,
                    source="tool", latency_ms=latency_ms, tool_call=text, via="offline")
    except OSError:
        pass

def cmd_serve(args):
    from .server import run
    # Load before listening: hooks time out (and fail open) if the first
    # request has to wait for the checkpoint download.
    if not args.no_warmup and not is_mock():
        print("loading Laya model (first run downloads the checkpoint)...", flush=True)
        if not warmup():
            print("warning: model failed to load; verdicts will fall back to "
                  "regex+heuristics (verdict_source=heuristic)", file=sys.stderr)
    print(f"laya-guard on http://{args.host}:{args.port} mock={is_mock()}")
    run(args.host, args.port)

def cmd_stats(args):
    import json
    from .log import load_entries, summarize, log_path
    entries = load_entries(args.path)
    s = summarize(entries)
    if args.json:
        print(json.dumps({"path": str(args.path or log_path()), **s}, ensure_ascii=False, indent=1))
        return 0
    print(f"verdicts: {s['total']} (log: {args.path or log_path()})")
    print(f"by_action: {s['by_action']}")
    print(f"block_rate={s['block_rate']} review_rate={s['review_rate']} mock_share={s['mock_share']}")
    print(f"avg_latency_ms={s['avg_latency_ms']}")
    d = s["degraded"]
    if d["total"]:
        print(f"judged without the model: {d['total']} ({d['offline']} with the server down, "
              f"{d['model_error']} after a model error), {d['share']:.1%} of all")
    for p in s["top_policies"]:
        print(f"  {p['policy_id']}: {p['count']}")
    return 0

def main(argv=None):
    ap = argparse.ArgumentParser(prog="laya-guard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("text", nargs="*")
    c.add_argument("--offline", action="store_true",
                   help="regex + safe-command check only, no model (what hooks fall back to when the "
                        "server is down; LAYA_GUARD_FAIL=closed blocks unjudged calls, =ask asks a human)")
    c.add_argument("--json", action="store_true", help="print the verdict as JSON")
    c.add_argument("--cwd", default=".",
                   help="directory the command runs in; package.json scripts that `npm test` "
                        "and the like run are checked from here (default: current directory)")
    c.set_defaults(fn=cmd_check)
    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8787)
    s.add_argument("--no-warmup", action="store_true", help="skip loading the model before listening"); s.set_defaults(fn=cmd_serve)
    t = sub.add_parser("stats"); t.add_argument("--path", default=None); t.add_argument("--json", action="store_true"); t.set_defaults(fn=cmd_stats)
    args = ap.parse_args(argv)
    return args.fn(args)

if __name__ == "__main__":
    raise SystemExit(main())
