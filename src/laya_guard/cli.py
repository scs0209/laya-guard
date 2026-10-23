"""CLI: laya-guard check / serve / eval."""
from __future__ import annotations
import argparse, dataclasses, json, os, sys, time
from . import activity, footprint, osmonitor
from .fileaccess import judge_file_call, judge_shell, stricter
from .guard import judge, judge_offline, is_mock, warmup
from .install import InstallError

def cmd_check(args):
    text = " ".join(args.text) if args.text else sys.stdin.read()
    cwd = os.path.abspath(args.cwd)
    v = judge_file_call(args.tool_name, args.path, cwd, args.access or None)   # a file call is judged by its paths alone
    shell = text if (args.shell or args.tool_name == "Bash") else None      # a shell command is also held to the protected paths
    if args.offline:
        # LAYA_GUARD_FAIL: ask (default) sends what it can't judge to a human, closed blocks it, open passes it
        unknown = {"open": "allow", "closed": "block"}.get(os.environ.get("LAYA_GUARD_FAIL", ""), "review")
        t0 = time.perf_counter()
        if v is None:
            v = stricter(judge_offline(text, unknown=unknown, cwd=cwd), judge_shell(shell, cwd))
        _log_offline(v, text, (time.perf_counter() - t0) * 1000.0, args.tool_name)
    elif v is None:
        v = stricter(judge(text, cwd=cwd), judge_shell(shell, cwd))
    if args.tool_name or args.shell or args.access:     # a tool call from an agent's hook, not someone typing a command
        activity.record_call(agent=args.agent, tool_name=args.tool_name, text=text, paths=args.path,
                             accesses=args.access or None, command=text if args.shell else None, cwd=cwd, verdict=v)
    if args.json:
        print(json.dumps(dataclasses.asdict(v), ensure_ascii=False))
        return 0 if v.action != "block" else 2
    icon = {"allow": "OK", "review": "REVIEW", "block": "BLOCK"}[v.action]
    print(f"[{icon}] {v.action} risk={v.risk} policy={v.policy_id} mock={v.mock}")
    print(f"reason: {v.reason}")
    return 0 if v.action != "block" else 2

def _log_offline(v, text: str, latency_ms: float, tool_name: str | None = None) -> None:
    """An offline verdict is an outage, not a real judgment: record it (hash and length only, like every
    entry) so it can be counted. A log that can't be written must never stop a hook."""
    from .alerts import maybe_alert
    from .log import log_verdict
    try:
        log_verdict(action=v.action, risk=v.risk, policy_id=v.policy_id, reason=v.reason, mock=False,
                    source="tool", latency_ms=latency_ms, tool_call=text, via="offline", tool_name=tool_name)
    except OSError:
        pass
    maybe_alert("server-down")             # LAYA_GUARD_ALERT_CMD, once per outage; never raises, never waits

def cmd_activity(args):
    """laya-guard activity [on|off]: the opt-in log of which files an agent's tool calls named."""
    path = activity.activity_path()
    if args.action == "on":
        activity.turn_on()
        print("File activity recording is on.\n"
              "  What: each file an agent's tool call names: its path, read / write / delete, and what the guard answered.\n"
              "        Never the contents of a file.\n"
              f"  Where: {_short(path)} (only you can read it)\n"
              "  Turn it off: laya-guard activity off")
        if activity.forced() is False:
            print("\nNote: LAYA_GUARD_FILE_ACTIVITY is set to off in this environment, so it stays off here.")
        return 0
    if args.action == "off":
        activity.turn_off()
        print(f"File activity recording is off. What was recorded is kept in {_short(path)}; delete it if you don't want it.")
        return 0
    events = activity.load_events()
    shown = events[-args.last:] if args.last > 0 else []
    if args.json:
        print(json.dumps({"enabled": activity.enabled(), "path": str(path), "total": len(events), "events": shown},
                         ensure_ascii=False))
        return 0
    state = "on" if activity.enabled() else "off"
    forced = activity.forced()
    print(f"File activity recording: {state}" + (" (set by LAYA_GUARD_FILE_ACTIVITY)" if forced is not None else ""))
    print(f"Log: {_short(path)} ({len(events)} event{'s' if len(events) != 1 else ''})")
    if not events and not activity.enabled():
        print("Turn it on with: laya-guard activity on")
    for e in shown:
        f = {k: _shown(e.get(k)) for k in ("ts", "agent", "tool_name", "op", "source", "path")}
        action = _shown(e.get("action")) + (f"!{_shown(e['would'])}" if e.get("would") else "")    # observed!review: it happened
        print(f"  {f['ts'][:19]}  {f['agent'] or '-':12} {f['tool_name'] or '-':10} "
              f"{f['op']:7} {action:14} {f['source']:10} {f['path']}")
    return 0

def cmd_footprint(args):
    """laya-guard footprint: what each project and session touched, and what is worth protecting, from the activity log."""
    path = activity.activity_path()
    events = activity.load_events()
    if not events:
        print(f"No file activity recorded yet ({_short(path)}).\n"
              + ("Recording is on: it fills in as your agents run." if activity.enabled()
                 else "Turn it on first: laya-guard activity on"))
        return 0
    try:
        if args.suggest:
            by_project, _ = footprint.select(events, window=args.since, project=args.project, agent=args.agent)
            found = footprint.suggest([e for rows in by_project.values() for _, e in rows])
            if args.json:
                print(json.dumps({"suggestions": found}, ensure_ascii=False, indent=1))
            elif found:
                print(_shown_text(footprint.render_rules_yaml(found)), end="")
                print("\n# Paste what you want under `protect:` in ~/.config/laya-guard/protect.yaml (edits apply to the next call).",
                      file=sys.stderr)
            else:
                print("Nothing to suggest: no touched path matches a well-known secret location or sits rarely outside "
                      "its project, beyond what protect.yaml already covers.")
            return 0
        report = footprint.build_report(events, window=args.since, project=args.project, agent=args.agent, top=args.top)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"log": str(path), "log_events": len(events), **report}, ensure_ascii=False, indent=1))
    else:
        print(_shown_text(footprint.render_text(report, log_label=f"log: {_short(path)}", total_in_log=len(events))))
    return 0

def _shown(value) -> str:
    """A logged value made safe for a terminal: what an agent named can hold escape sequences that would rewrite the
    screen, so anything unprintable is shown as an escape instead."""
    return "".join(c if c.isprintable() else f"\\u{ord(c):04x}" for c in str(value if value is not None else ""))

def _shown_text(text: str) -> str:
    return "\n".join(_shown(line) for line in text.split("\n"))

def cmd_watch(args):
    """laya-guard watch: the OS monitor (macOS, optional). Adds what an agent's processes really opened to the file
    activity log, from Apple's eslogger."""
    return osmonitor.watch(matches=args.match, reads=args.reads, ignore=args.ignore, to_stdout=args.stdout,
                           seed=not args.no_seed)

def cmd_serve(args):
    from .server import run
    if _server_up(args.host, args.port):
        # nothing to start: the port would be taken. (A login service lands here while a hand-started server
        # holds the port, exits non-zero, and is started again later.)
        print(f"laya-guard is already running on http://{args.host}:{args.port}", file=sys.stderr)
        return 1
    import signal
    # A stop (`kill`, launchd, systemd) must exit 0: uvicorn re-raises SIGTERM after shutting down, and launchd
    # takes the resulting 143 for a crash and starts the server again.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
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
    if s["by_tool"]:
        print("top tools:")
        for t in s["by_tool"]:
            print(f"  {t['tool_name']}: {t['count']}")
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
    if args.files:
        from . import install as ins_mod
        try:
            agent_id = ins_mod.spec_for(args.agent).id
        except InstallError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if agent_id != "claude-code":
            print("error: --files only applies to claude-code", file=sys.stderr)
            return 1
        if args.matcher:
            print("error: --files and --matcher both choose the tools the hook guards; use one", file=sys.stderr)
            return 1
        args.matcher = ins_mod.FILES_MATCHER
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

def cmd_audit(args):
    """Judge the shell commands Claude Code and Codex already ran, from their own transcripts."""
    from . import audit
    try:
        since = audit.parse_since(args.since)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    agents = audit.AGENTS if args.agent == "all" else (args.agent,)
    progress = None if args.json or not sys.stderr.isatty() else audit.progress_to_stderr
    report = audit.run(agents, since, progress=progress)
    if progress:
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
    if args.json:
        print(json.dumps(audit.to_json(report, redact=args.redact), indent=2, ensure_ascii=False))
    else:
        print(audit.render(report, limit=args.limit, redact=args.redact))
    return 0

def cmd_sandbox(args):
    """Turn an agent's own OS sandbox on, denying the protected paths (shows the change first)."""
    from pathlib import Path
    from . import sandbox as sbx
    if args.list or not args.agent:
        print("Agents whose own sandbox laya-guard can configure (`laya-guard sandbox <agent>`):")
        rows = [("claude-code", "user, project, local", "denies reading and writing the protected paths"),
                ("codex", "user, project", "a permissions profile: protected paths \"deny\""),
                ("copilot-cli", "user", "deniedPaths / readonlyPaths (existing paths, no globs)"),
                ("gemini-cli", "project", "a Seatbelt profile (macOS) in .gemini/"),
                ("qwen-code", "project", "a Seatbelt profile (macOS) in .qwen/"),
                ("cursor", "user", "turns cursor-agent's sandbox on; reads stay with the hook")]
        for agent, scopes, what in rows:
            print(f"  {agent:<12} {scopes:<22} {what}")
        print("\nAn agent with no sandbox of its own, run in a terminal (opencode, amp, kilo, ...):\n"
              "  laya-guard run -- <agent> [args]      (needs Anthropic's sandbox-runtime, `srt`)\n"
              "Agents inside an IDE (Windsurf, Cline, ...) can't be wrapped; the hook stays their guard.")
        return 0
    try:
        agent = sbx.agent_id(args.agent)
        scope = args.scope or ("project" if agent in ("gemini-cli", "qwen-code") else "user")
        plan = sbx.make_plan("remove" if args.remove else "apply", agent, scope, Path.home(),
                             Path(args.project_dir).resolve())
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    what = "Removing laya-guard's sandbox settings for" if args.remove else "Turning on the sandbox of"
    print(f"{what} {plan.agent}:\n")
    code, _ = _apply(lambda p: (sbx.apply_plan(p), None), plan, args)
    if code == 0 and not args.remove and not args.dry_run and plan.changed:
        print("\nRestart the agent so it reads the new settings.")
    return code

def cmd_run(args):
    from . import sandbox as sbx
    argv = list(args.command)
    if argv and argv[0] == "--":
        argv = argv[1:]
    try:
        return sbx.run(argv, allow_write=args.allow_write, allow_domains=args.allow_domain, dry_run=args.print_settings)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

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
        busy = False
        if args.service_cmd == "install" and svc.port_answers(args.host, args.port):
            st = svc.status(home)
            # the service itself answering (a reinstall) isn't something else holding the port
            busy = not (st.running is True and st.port == args.port)
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
    c.add_argument("--tool-name", default=None,
                   help="the agent's name for the tool (Read, Edit, Write, ...): with --path, the call is judged by the paths alone")
    c.add_argument("--agent", default=None, help="which agent is calling (claude-code, ...): labels the file activity log")
    c.add_argument("--shell", action="store_true",
                   help="the text is a shell command an agent is about to run: the paths it names are checked against "
                        "the protected paths, and the file activity log labels it")
    c.add_argument("--access", nargs=2, action="append", default=[], metavar=("OP", "PATH"),
                   help="what the call touches: read, write or delete, then a path (repeat for more); judged like --path")
    c.add_argument("--path", action="append", default=[],
                   help="a file the tool call names (repeat for more); judged against the protected paths, not by its content")
    c.add_argument("--cwd", default=".",
                   help="directory the command runs in; package.json scripts that `npm test` "
                        "and the like run are checked from here (default: current directory)")
    c.set_defaults(fn=cmd_check)
    ac = sub.add_parser("activity", help="the opt-in log of which files an agent's tool calls named")
    ac.add_argument("action", nargs="?", choices=("on", "off"), default=None,
                    help="on: start recording, off: stop (default: show the state and the latest events)")
    ac.add_argument("--last", type=int, default=20, help="how many of the latest events to show (default 20)")
    ac.add_argument("--json", action="store_true", help="print the state and events as JSON")
    ac.set_defaults(fn=cmd_activity)
    fp = sub.add_parser("footprint", help="what each project and session touched, and paths worth protecting, from the activity log",
                        description="Reads the file activity log (laya-guard activity on) and shows, per project and session, "
                                    "what your agents read, wrote and deleted, what hit a protected path and what was outside "
                                    "the project. --suggest prints protect.yaml entries for paths you may want to protect; "
                                    "it never applies anything, and an access alone is not evidence of bad intent.")
    fp.add_argument("--since", choices=footprint.WINDOW_CHOICES, default=footprint.DEFAULT_WINDOW,
                    help=f"how far back to look (default {footprint.DEFAULT_WINDOW})")
    fp.add_argument("--project", default=None, help="only this directory (and anything below it)")
    fp.add_argument("--agent", default=None, help="only this agent (claude-code, codex, ...)")
    fp.add_argument("--top", type=int, default=5, help="how many paths to list per project (default 5)")
    fp.add_argument("--suggest", action="store_true",
                    help="print protect.yaml entries for paths worth protecting: known secret locations an agent touched, and "
                         "folders outside the project it touched only rarely. Nothing is applied")
    fp.add_argument("--json", action="store_true", help="print JSON")
    fp.set_defaults(fn=cmd_footprint)
    w = sub.add_parser("watch", help="(macOS, optional) record the files an agent's processes really open, as the OS sees them",
                       description="Reads Apple's eslogger output and adds the files an agent's own processes open, create, "
                                   "delete or rename to the file activity log (source os-monitor). It sees what scripts and "
                                   "child processes do, which the hooks can't, but it only reports: nothing is blocked. On a "
                                   f"terminal it starts `sudo eslogger` itself; or pipe it in: {osmonitor.PIPE_COMMAND}. Needs sudo and Full "
                                   "Disk Access for the terminal app. Turn the log on first: laya-guard activity on.")
    w.add_argument("--match", action="append", default=[], metavar="PROGRAM[=AGENT]",
                   help="also treat a program with this name as an agent (repeat for more); built in: claude, codex, "
                        "cursor-agent, gemini, qwen, copilot, cline, opencode, kilo, amp")
    w.add_argument("--reads", choices=("protected", "outside", "all"), default="outside",
                   help="which reads to keep: protected (only protected paths), outside (default: those plus files in your own "
                        "folders outside the project), all (every file the agent's processes read: very large). Writes, "
                        "deletes and renames are always kept")
    w.add_argument("--ignore", action="append", default=[], metavar="GLOB",
                   help="don't record paths matching this glob (repeat for more; written like protect.yaml's, e.g. "
                        "'~/.claude/**'). A path a protected-path rule names is still recorded")
    w.add_argument("--stdout", action="store_true", help="print the events as JSON lines instead of recording them")
    w.add_argument("--no-seed", action="store_true", help="don't look for agents that are already running (from ps)")
    w.set_defaults(fn=cmd_watch)
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
    i.add_argument("--files", action="store_true",
                   help="Claude Code only: also guard the file tools (Read, Edit, MultiEdit, Write, NotebookEdit, Glob, Grep), "
                        "so the protected paths apply to them")
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
    au = sub.add_parser("audit", help="judge the shell commands Claude Code and Codex already ran, from their own "
                                      "transcripts (nothing is logged or sent)")
    # A day by default: a heavy day of 1,961 Claude Code commands took 11 minutes with the model (docs/guide/audit.md).
    au.add_argument("--since", default="24h", help="how far back: 24h, 7d, 30d or all (default: 24h)")
    au.add_argument("--agent", choices=("all", "claude-code", "codex"), default="all")
    au.add_argument("--limit", type=int, default=20, help="how many findings to list (default: 20)")
    au.add_argument("--redact", action="store_true", help="leave the command text out (for a screenshot)")
    au.add_argument("--json", action="store_true")
    au.set_defaults(fn=cmd_audit)
    sb = sub.add_parser("sandbox", help="turn an agent's own OS sandbox on, denying the protected paths (shows a diff first)")
    sb.add_argument("agent", nargs="?", help="claude-code, codex, copilot-cli, gemini-cli, qwen-code or cursor")
    _scope_args(sb, None)
    sb.add_argument("--list", action="store_true", help="what each agent's sandbox can take")
    sb.add_argument("--remove", action="store_true", help="take out what `laya-guard sandbox` added")
    sb.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    sb.add_argument("-y", "--yes", action="store_true", help="apply without asking")
    sb.set_defaults(fn=cmd_sandbox)
    rn = sub.add_parser("run", help="run an agent with no sandbox of its own inside sandbox-runtime (srt), "
                                    "denying the protected paths")
    rn.add_argument("--allow-write", action="append", default=[], help="another directory it may write to (repeat)")
    rn.add_argument("--allow-domain", action="append", default=[], help="another host it may reach (repeat)")
    rn.add_argument("--print-settings", action="store_true", help="print the srt settings and run nothing")
    rn.add_argument("command", nargs=argparse.REMAINDER, help="-- <agent> [args]")
    rn.set_defaults(fn=cmd_run)
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
