"""CLI: laya-guard check / serve / eval."""
from __future__ import annotations
import argparse, dataclasses, json, os, sys
from .guard import judge, judge_offline, is_mock, warmup
from .install import InstallError

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

def _short(path) -> str:
    from pathlib import Path
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home + os.sep) else s

def _prepare(args):
    """(install module, Settings) for the agent/scope in args; raises InstallError."""
    from pathlib import Path
    from . import install as ins
    if args.agent not in ins.AGENTS:
        raise ins.InstallError(f"unsupported agent {args.agent!r}; supported: {', '.join(ins.AGENTS)}")
    if sys.platform == "win32":
        raise ins.InstallError("the hook is a bash script, so Windows isn't supported yet")
    path = ins.settings_path(args.scope, Path.home(), Path(args.project_dir).resolve())
    return ins, ins.load_settings(path)

def _apply(ins, settings, new_data, args) -> tuple[int, bool]:
    """Show the diff and, once confirmed, write it. Returns (exit code, wrote)."""
    if new_data == settings.data:
        print("Nothing to change.")
        return 0, False
    new_text = settings.render(new_data)
    print(ins.unified_diff(settings, new_text))
    if args.dry_run:
        print("\n(dry run: nothing was written)")
        return 0, False
    if not args.yes:
        if not sys.stdin.isatty():
            print("\nNot applied: stdin isn't a terminal, pass --yes to confirm.", file=sys.stderr)
            return 2, False
        if input("\nApply this change? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 1, False
    backup = ins.write_settings(settings, new_text)
    print(f"\nWrote {_short(settings.path)}" + (f" (backup: {_short(backup)})" if backup else ""))
    return 0, True

def cmd_install(args):
    try:
        ins, settings = _prepare(args)
        command = ins.hook_command(ins.cli_path(args.bin), ins.hook_script())
        data, outcome = ins.plan_install(settings, command, args.matcher, args.force)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if outcome == "unchanged":
        print(f"Already installed and up to date in {_short(settings.path)}.")
        return 0
    if outcome == "manual":
        existing = ins.find_entries(settings.data)[0][2]["command"]
        print(f"{_short(settings.path)} already runs guard-hook.sh, but not through `laya-guard install`:\n"
              f"  {existing}\nLeft as it is. Re-run with --force to replace it.")
        return 0
    verb = "Adding" if outcome == "added" else "Updating"
    print(f"{verb} the laya-guard hook ({args.scope} scope, matcher {args.matcher!r}) in {_short(settings.path)}:\n")
    code, wrote = _apply(ins, settings, data, args)
    if code or not wrote:
        return code
    if not ins.server_health():
        print("\nThe laya-guard server isn't running, so the hook uses the offline check (regexes only) "
              "until you start it: `laya-guard serve`.")
    if args.no_verify:
        return 0
    print("\nChecking the hook:")
    failed = False
    for label, expected, got in ins.run_probes(command):
        print(f"  {'ok  ' if got == expected else 'FAIL'} {label} (exit {got}, expected {expected})")
        failed = failed or got != expected
    if failed:
        print("\nThe hook is registered but didn't behave as expected; run `laya-guard status --check`.", file=sys.stderr)
        return 1
    return 0

def cmd_uninstall(args):
    try:
        ins, settings = _prepare(args)
        data, removed, left = ins.plan_uninstall(settings, args.force)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not removed:
        extra = " A hand-written guard-hook.sh entry is left alone; use --force to remove it too." if left else ""
        print(f"No hook written by `laya-guard install` found in {_short(settings.path)}.{extra}")
        return 0
    print(f"Removing the laya-guard hook from {_short(settings.path)}:\n")
    code, _ = _apply(ins, settings, data, args)
    if code == 0 and left:
        print(f"{left} hand-written guard-hook.sh entr{'y' if left == 1 else 'ies'} left in place (use --force).")
    return code

def cmd_status(args):
    import json
    from pathlib import Path
    from . import install as ins
    scopes = [args.scope] if args.scope else list(ins.SCOPES)
    rows = [ins.status(s, Path.home(), Path(args.project_dir).resolve()) for s in scopes]
    up = ins.server_health()
    probes = None
    if args.check:
        active = next((r for r in rows if r.state in ("installed", "manual")), None)
        probes = ins.run_probes(active.command) if active else []
    if args.json:
        print(json.dumps({
            "server": up,
            "claude-code": [{"scope": r.scope, "path": str(r.path), "state": r.state, "command": r.command,
                             "detail": r.detail} for r in rows],
            **({"probes": [{"label": l, "expected": e, "exit": g, "ok": e == g} for l, e, g in probes]}
               if probes is not None else {})}, indent=1))
        return 0 if all(e == g for _, e, g in (probes or [])) else 1
    print("claude-code")
    width = max(len(_short(r.path)) for r in rows) + 2
    for r in rows:
        label = {"installed": "installed", "manual": "installed by hand", "broken": "BROKEN",
                 "invalid": "invalid file", "not-installed": "not installed"}[r.state]
        print(f"  {r.scope:<8}{_short(r.path):<{width}}{label}" + (f"  ({r.detail})" if r.detail else ""))
    print(f"server: {'running' if up else 'not running; the hook uses the offline check (regexes only)'}")
    if probes is not None:
        print("probes:" if probes else "probes: nothing installed to check")
        for label, expected, got in probes:
            print(f"  {'ok  ' if got == expected else 'FAIL'} {label} (exit {got}, expected {expected})")
    return 0 if all(e == g for _, e, g in (probes or [])) else 1

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
    def _scope_args(p, default):
        p.add_argument("--scope", choices=("user", "project", "local"), default=default,
                       help="user: ~/.claude/settings.json; project: ./.claude/settings.json; "
                            "local: ./.claude/settings.local.json")
        p.add_argument("--project-dir", default=".", help="project for the project/local scopes (default: current directory)")
    i = sub.add_parser("install", help="register the laya-guard hook in a coding agent (shows a diff first)")
    i.add_argument("agent", help="claude-code")
    _scope_args(i, "user")
    i.add_argument("--matcher", default="Bash", help="tools the hook guards, a Claude Code matcher (default: Bash)")
    i.add_argument("--bin", default=None, help="absolute path of the laya-guard executable (default: found automatically)")
    i.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    i.add_argument("-y", "--yes", action="store_true", help="apply without asking")
    i.add_argument("--force", action="store_true", help="also replace a hand-written guard-hook.sh entry")
    i.add_argument("--no-verify", action="store_true", help="skip the test calls after installing")
    i.set_defaults(fn=cmd_install)
    u = sub.add_parser("uninstall", help="remove the hook that `laya-guard install` added")
    u.add_argument("agent", help="claude-code")
    _scope_args(u, "user")
    u.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    u.add_argument("-y", "--yes", action="store_true", help="apply without asking")
    u.add_argument("--force", action="store_true", help="also remove a hand-written guard-hook.sh entry")
    u.set_defaults(fn=cmd_uninstall)
    st = sub.add_parser("status", help="where the hook is installed, whether the server is up")
    _scope_args(st, None)
    st.add_argument("--check", action="store_true", help="also run two test calls through the installed hook")
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    return args.fn(args)

if __name__ == "__main__":
    raise SystemExit(main())
