"""CLI: laya-guard check / serve / eval."""
from __future__ import annotations
import argparse, dataclasses, json, os, sys, time
from .guard import judge, judge_offline, is_mock, warmup
from .install import InstallError

def cmd_check(args):
    text = " ".join(args.text) if args.text else sys.stdin.read()
    cwd = os.path.abspath(args.cwd)
    if args.offline:
        # LAYA_GUARD_FAIL: ask (default) sends what it can't judge to a human, closed blocks it, open passes it
        unknown = {"open": "allow", "closed": "block"}.get(os.environ.get("LAYA_GUARD_FAIL", ""), "review")
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
    from .alerts import maybe_alert
    from .log import log_verdict
    try:
        log_verdict(action=v.action, risk=v.risk, policy_id=v.policy_id, reason=v.reason, mock=False,
                    source="tool", latency_ms=latency_ms, tool_call=text, via="offline")
    except OSError:
        pass
    maybe_alert("server-down")             # LAYA_GUARD_ALERT_CMD, once per outage; never raises, never waits

def cmd_serve(args):
    from .server import run
    if _server_up(args.host, args.port):
        # nothing to start: the port would be taken. (A login service lands here while a hand-started server
        # holds the port, exits non-zero, and is started again later.)
        print(f"laya-guard is already running on http://{args.host}:{args.port}", file=sys.stderr)
        return 1
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

def _short(path) -> str:
    from pathlib import Path
    home = str(Path.home())
    s = str(path)
    return "~" + s[len(home):] if s.startswith(home + os.sep) else s

def _plan(args, action):
    """The planned change for the agent/scope in args (writes nothing); raises InstallError."""
    from pathlib import Path
    from . import install as ins
    args.agent = ins.spec_for(args.agent).id            # accept `gemini`, `copilot`, ... too
    return ins, ins.make_plan(action, args.agent, args.scope, Path.home(), Path(args.project_dir).resolve(),
                              bin_override=getattr(args, "bin", None), matcher=getattr(args, "matcher", None),
                              force=args.force)

def _apply(apply, plan, args) -> tuple[int, bool]:
    """Show the diff and, once confirmed, write it with `apply(plan)`. Returns (exit code, wrote)."""
    if not plan.changed:
        print("Nothing to change.")
        return 0, False
    if plan.repaired:
        print("Note: the file has trailing commas. The agent accepts them, strict JSON doesn't, and they are "
              "dropped when it is rewritten.\n")
    for note in plan.notes:
        print(f"Note: {note}\n")
    print(plan.diff)
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
    backups, _ = apply(plan)
    for a in (a for a in plan.artifacts if a.changed):
        print(f"\n{'Removed' if a.after is None else 'Wrote'} {_short(a.path)}")
    for b in backups:
        print(f"  backup: {_short(b)}")
    return 0, True

_ANSWERS = {0: "allow", 1: "ask", 2: "deny", 9: "unreadable output"}

def _probe_line(agent, label, expected, got) -> str:
    ok = "ok  " if got == expected else "FAIL"
    if agent == "claude-code":
        return f"  {ok} {label} (exit {got}, expected {expected})"
    return f"  {ok} {label} (answered {_ANSWERS.get(got, f'exit {got}')}, expected {_ANSWERS.get(expected, expected)})"

def cmd_install(args):
    try:
        ins, plan = _plan(args, "install")
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    spec = plan.spec
    if plan.outcome == "unchanged":
        print(f"Already installed and up to date in {_short(plan.path)}.")
        return 0
    if plan.outcome == "manual":
        print(f"{_short(plan.path)} already has a laya-guard hook, but not through `laya-guard install`:\n"
              f"  {plan.existing}\nLeft as it is. Re-run with --force to replace it.")
        return 0
    verb = "Adding" if plan.outcome == "added" else "Updating"
    how = f", matcher {(args.matcher or spec.matcher)!r}" if spec.grouped and spec.id == "claude-code" else ""
    print(f"{verb} the laya-guard hook for {spec.name} ({args.scope} scope{how}) in {_short(plan.path)}:\n")
    if spec.unverified:
        print(f"(Heads-up: this format is {spec.unverified}; it hasn't been run against the app itself.)\n")
    code, wrote = _apply(lambda p: ins.apply_plan(p, verify=False), plan, args)
    if code or not wrote:
        return code
    if not ins.server_health():
        print("\nThe laya-guard server isn't running, so the hook uses the offline check (regexes only) "
              "until you start it: `laya-guard serve`.")
    failed = False
    if not args.no_verify:
        probes = ins.run_probes(plan.command, args.agent)
        if probes:
            print("\nChecking the hook:")
            for label, expected, got in probes:
                print(_probe_line(args.agent, label, expected, got))
                failed = failed or got != expected
        else:
            print(f"\nNo test calls: {spec.name} loads this by itself, so laya-guard can't run it from here.")
    print(f"\nNext: {plan.follow_up}")
    if failed:
        print("\nThe hook is registered but didn't behave as expected; run `laya-guard status --check`.", file=sys.stderr)
        return 1
    return 0

def cmd_uninstall(args):
    try:
        ins, plan = _plan(args, "uninstall")
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if plan.outcome != "removed":
        extra = " A hand-written laya-guard entry is left alone; use --force to remove it too." if plan.left else ""
        print(f"No hook written by `laya-guard install` found in {_short(plan.path)}.{extra}")
        return 0
    print(f"Removing the laya-guard hook for {plan.spec.name} from {_short(plan.path)}:\n")
    code, _ = _apply(lambda p: ins.apply_plan(p, verify=False), plan, args)
    if code == 0 and plan.left:
        print(f"{plan.left} hand-written laya-guard entr{'y' if plan.left == 1 else 'ies'} left in place (use --force).")
    return code

def cmd_service(args):
    """Keep the server running from login on: install, uninstall or show the LaunchAgent / systemd unit."""
    import json
    from pathlib import Path
    from . import service as svc
    home = Path.home()
    try:
        if args.service_cmd == "status":
            st = svc.status(home)
            up = svc.port_answers("127.0.0.1", st.port)
            if args.json:
                print(json.dumps({"kind": st.kind, "path": str(st.path), "state": st.state, "running": st.running,
                                  "server_up": up, "port": st.port, "warmup": st.warmup, "detail": st.detail}, indent=1))
                return 0
            label = {"installed": "installed", "broken": "BROKEN", "manual": "a file is there that isn't ours",
                     "not-installed": "not installed"}[st.state]
            print(f"{svc.how(st.kind)}: {label}  ({_short(st.path)})" + (f"\n  {st.detail}" if st.detail else ""))
            if st.state in ("installed", "broken"):
                print(f"  starts at login; model {'loaded at start' if st.warmup else 'loaded on the first call'}; port {st.port}")
                print(f"  service manager: {'running' if st.running else 'not running' if st.running is False else 'unknown'}")
                print(f"  log: {svc.log_hint(st.kind, home)}")
            print(f"server on port {st.port}: {'answering' if up else 'not answering'}")
            return 0
        busy = args.service_cmd == "install" and svc.port_answers(args.host, args.port)
        plan = svc.make_plan(args.service_cmd, home, bin_override=getattr(args, "bin", None),
                             host=getattr(args, "host", "127.0.0.1"), port=getattr(args, "port", svc.DEFAULT_PORT),
                             warmup=not getattr(args, "no_warmup", False), force=args.force, port_busy=busy)
        if plan.outcome == "manual":
            print(f"{plan.existing}.\nLeft as it is. Re-run with --force to replace it (the old file is backed up).")
            return 0
        if plan.outcome == "nothing":
            print(f"Not installed: there is no {_short(plan.path)}.")
            return 0
        if plan.outcome == "unchanged":
            print(f"Already installed and up to date in {_short(plan.path)}.")
            return 0
        print(f"{'Adding' if plan.outcome == 'added' else 'Updating' if plan.outcome == 'updated' else 'Removing'} "
              f"the {svc.how(plan.kind)} for the laya-guard server ({_short(plan.path)}):\n")
        code, wrote = _apply(svc.apply_plan, plan, args)
        if code or not wrote:
            return code
        if plan.ran:
            print("\nCommands run:\n  " + "\n  ".join(plan.ran))
        if plan.action == "install":
            print(f"\n{plan.follow_up}")
        else:
            print("\nThe service is stopped and removed. `laya-guard serve` still starts the server by hand.")
        return 0
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc.strerror or exc}", file=sys.stderr)
        return 1

def cmd_status(args):
    import json
    from pathlib import Path
    from . import install as ins
    home, project = Path.home(), Path(args.project_dir).resolve()
    names = [args.agent] if args.agent else list(ins.AGENTS)
    try:
        agents = [ins.spec_for(n).id for n in names]
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report = {a: [ins.status(s, home, project, a) for s in ([args.scope] if args.scope else ins.SPECS[a].scopes)
                  if s in ins.SPECS[a].scopes] for a in agents}
    up = ins.server_health()
    probes = None
    if args.check:
        probes = []
        for agent, rows in report.items():
            active = next((r for r in rows if r.state in ("installed", "manual") and r.command), None)
            if active:
                probes += [(agent, *probe) for probe in ins.run_probes(active.command, agent)]
    ok = all(e == g for _, _, e, g in (probes or []))
    if args.json:
        print(json.dumps({
            "server": up,
            **{agent: [{"scope": r.scope, "path": str(r.path), "state": r.state, "command": r.command,
                        "detail": r.detail} for r in rows] for agent, rows in report.items()},
            **({"probes": [{"agent": a, "label": l, "expected": e, "exit": g, "ok": e == g} for a, l, e, g in probes]}
               if probes is not None else {})}, indent=1))
        return 0 if ok else 1
    present = {d["id"] for d in ins.detect_agents(home) if d["found"]}
    quiet = []
    for agent, rows in report.items():
        if not args.agent and not args.all and agent not in present and all(r.state == "not-installed" for r in rows):
            quiet.append(agent)                      # not here and nothing installed: don't list eleven agents
            continue
        print(agent)
        if not rows:
            print(f"  (no {args.scope} scope)")
            continue
        width = max(len(_short(r.path)) for r in rows) + 2
        for r in rows:
            label = {"installed": "installed", "manual": "installed by hand", "broken": "BROKEN",
                     "invalid": "invalid file", "not-installed": "not installed"}[r.state]
            print(f"  {r.scope:<8}{_short(r.path):<{width}}{label}" + (f"  ({r.detail})" if r.detail else ""))
    if quiet:
        print(f"not found on this machine: {', '.join(quiet)} (`laya-guard status --all` lists them too)")
    print(f"server: {'running' if up else 'not running; the hook uses the offline check (regexes only)'}")
    if probes is not None:
        print("probes:" if probes else "probes: nothing installed that laya-guard can run from here")
        for agent, label, expected, got in probes:
            print(_probe_line(agent, label, expected, got))
    return 0 if ok else 1

def main(argv=None):
    ap = argparse.ArgumentParser(prog="laya-guard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("text", nargs="*")
    c.add_argument("--offline", action="store_true",
                   help="regex + safe-command check only, no model (what hooks fall back to when the "
                        "server is down; unjudged calls are sent to a human by default, "
                        "LAYA_GUARD_FAIL=closed blocks them, =open passes them)")
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
    def _scope_args(p, default):
        p.add_argument("--scope", choices=("user", "project", "local"), default=default,
                       help="user: the agent's own config in your home directory; project: the one inside the "
                            "project (see --project-dir); local: Claude Code's settings.local.json only")
        p.add_argument("--project-dir", default=".", help="project for the project/local scopes (default: current directory)")
    i = sub.add_parser("install", help="register the laya-guard hook in a coding agent (shows a diff first)")
    i.add_argument("agent", help="claude-code, codex, cursor, gemini-cli, qwen-code, windsurf, copilot-cli, "
                                 "cline, opencode, kilo or amp (gemini, qwen, copilot and claude work too)")
    _scope_args(i, "user")
    i.add_argument("--matcher", default=None, help="Claude Code only: the tools the hook guards (default: Bash). "
                                                   "The other agents' hooks always guard shell commands")
    i.add_argument("--bin", default=None, help="absolute path of the laya-guard executable (default: found automatically)")
    i.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    i.add_argument("-y", "--yes", action="store_true", help="apply without asking")
    i.add_argument("--force", action="store_true", help="also replace a hand-written laya-guard hook entry")
    i.add_argument("--no-verify", action="store_true", help="skip the test calls after installing")
    i.set_defaults(fn=cmd_install)
    u = sub.add_parser("uninstall", help="remove the hook that `laya-guard install` added")
    u.add_argument("agent", help="the agent to remove the hook from (same names as install)")
    _scope_args(u, "user")
    u.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    u.add_argument("-y", "--yes", action="store_true", help="apply without asking")
    u.add_argument("--force", action="store_true", help="also remove a hand-written laya-guard hook entry")
    u.set_defaults(fn=cmd_uninstall)
    sv = sub.add_parser("service", help="start the server at login (macOS LaunchAgent / Linux systemd user unit); opt-in")
    svs = sv.add_subparsers(dest="service_cmd", required=True)
    si = svs.add_parser("install", help="start the server now and at every login (shows the file first)")
    si.add_argument("--host", default="127.0.0.1"); si.add_argument("--port", type=int, default=8787)
    si.add_argument("--no-warmup", action="store_true", help="load the model on the first call instead of at start (about 90 MB idle instead of 1.4 GB)")
    si.add_argument("--bin", default=None, help="absolute path of the laya-guard executable (default: found automatically)")
    su = svs.add_parser("uninstall", help="stop the service and remove its file")
    for q in (si, su):
        q.add_argument("--dry-run", action="store_true", help="print the file, change nothing")
        q.add_argument("-y", "--yes", action="store_true", help="apply without asking")
        q.add_argument("--force", action="store_true", help="also replace or remove a file that `laya-guard service install` didn't write (backed up)")
    ss = svs.add_parser("status", help="whether the service is installed and running")
    ss.add_argument("--json", action="store_true")
    sv.set_defaults(fn=cmd_service)
    st = sub.add_parser("status", help="where the hook is installed, whether the server is up")
    _scope_args(st, None)
    st.add_argument("--agent", default=None, help="only this agent (default: the ones found on this machine)")
    st.add_argument("--all", action="store_true", help="list every supported agent, including the ones not found here")
    st.add_argument("--check", action="store_true", help="also run two test calls through the installed hook")
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=cmd_status)
    args = ap.parse_args(argv)
    return args.fn(args)

if __name__ == "__main__":
    raise SystemExit(main())
