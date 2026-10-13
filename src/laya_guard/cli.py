"""CLI: laya-guard check / serve / eval."""
from __future__ import annotations
import argparse, dataclasses, json, os, sys
from .guard import judge, judge_offline, is_mock, warmup

def cmd_check(args):
    text = " ".join(args.text) if args.text else sys.stdin.read()
    cwd = os.path.abspath(args.cwd)
    if args.offline:
        v = judge_offline(text, fail_closed=os.environ.get("LAYA_GUARD_FAIL") == "closed",
                          cwd=cwd)
    else:
        v = judge(text, cwd=cwd)
    if args.json:
        print(json.dumps(dataclasses.asdict(v), ensure_ascii=False))
        return 0 if v.action != "block" else 2
    icon = {"allow": "OK", "review": "REVIEW", "block": "BLOCK"}[v.action]
    print(f"[{icon}] {v.action} risk={v.risk} policy={v.policy_id} mock={v.mock}")
    print(f"reason: {v.reason}")
    return 0 if v.action != "block" else 2

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
    for p in s["top_policies"]:
        print(f"  {p['policy_id']}: {p['count']}")
    return 0

def _server_up(host: str, port: int) -> bool:
    import urllib.request
    try:
        urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1)
        return True
    except Exception:
        return False

def cmd_dashboard(args):
    """Open the local dashboard; start the server first if it isn't running."""
    import threading, time, webbrowser
    url = f"http://{args.host}:{args.port}/dashboard"
    if _server_up(args.host, args.port):
        print(f"dashboard: {url}")
        if not args.no_open:
            webbrowser.open(url)
        return 0
    if not args.no_open:
        def open_when_ready():
            for _ in range(240):             # the model can take a while to load
                if _server_up(args.host, args.port):
                    webbrowser.open(url)
                    return
                time.sleep(0.5)
        threading.Thread(target=open_when_ready, daemon=True).start()
    print(f"laya-guard is not running; starting it. dashboard: {url}", flush=True)
    return cmd_serve(args)

def main(argv=None):
    ap = argparse.ArgumentParser(prog="laya-guard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("text", nargs="*")
    c.add_argument("--offline", action="store_true",
                   help="regex + safe-command check only, no model (what hooks fall back to "
                        "when the server is down; LAYA_GUARD_FAIL=closed blocks unjudged calls)")
    c.add_argument("--json", action="store_true", help="print the verdict as JSON")
    c.add_argument("--cwd", default=".",
                   help="directory the command runs in; package.json scripts that `npm test` "
                        "and the like run are checked from here (default: current directory)")
    c.set_defaults(fn=cmd_check)
    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8787)
    s.add_argument("--no-warmup", action="store_true", help="skip loading the model before listening"); s.set_defaults(fn=cmd_serve)
    t = sub.add_parser("stats"); t.add_argument("--path", default=None); t.add_argument("--json", action="store_true"); t.set_defaults(fn=cmd_stats)
    d = sub.add_parser("dashboard", help="open the local dashboard (starts the server if it isn't running)")
    d.add_argument("--host", default="127.0.0.1"); d.add_argument("--port", type=int, default=8787)
    d.add_argument("--no-open", action="store_true", help="print the URL instead of opening a browser")
    d.add_argument("--no-warmup", action="store_true", help="skip loading the model when starting the server")
    d.set_defaults(fn=cmd_dashboard)
    args = ap.parse_args(argv)
    return args.fn(args)

if __name__ == "__main__":
    raise SystemExit(main())
