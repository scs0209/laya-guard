"""Starting the server at login: what gets written, the commands that follow, and how it is shown.

Nothing here talks to a real launchd or systemd: the service module is given a fake runner, and tests/conftest.py
makes the default one fail so a slip can't reach the developer's own machine.
"""
import json
import plistlib
import re
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from laya_guard import cli, service  # noqa: E402
from laya_guard.install import InstallError  # noqa: E402


class Manager:
    """A stand-in for launchctl / systemctl: records every call, answers from `rules`
    ([(substring of the command, [exit codes in order, the last one repeats], stdout)])."""

    def __init__(self, *rules):
        self.calls, self.rules = [], [[n, list(codes), out] for n, codes, out in rules]

    def __call__(self, cmd):
        self.calls.append(" ".join(cmd))
        for needle, codes, out in self.rules:
            if needle in self.calls[-1]:
                code = codes.pop(0) if len(codes) > 1 else codes[0]
                return subprocess.CompletedProcess(cmd, code, out if code == 0 else "", "" if code == 0 else "boom")
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return h


@pytest.fixture
def binary(tmp_path):
    b = tmp_path / "bin dir" / "laya-guard"              # a space in the path: the quoting has to hold
    b.parent.mkdir()
    b.write_text("#!/bin/sh\nexit 0\n")
    b.chmod(0o755)
    return b


def plan_for(action, home, binary, platform="darwin", environ=None, **kw):
    return service.make_plan(action, home, bin_override=str(binary), platform=platform,
                             environ={} if environ is None else environ, **kw)


# --- what is written ----------------------------------------------------------------------------

def test_the_launchagent_restarts_after_a_crash_but_not_a_clean_stop(home, binary):
    plan = plan_for("install", home, binary)
    assert plan.outcome == "added" and plan.path == home / "Library" / "LaunchAgents" / "com.laya-guard.server.plist"
    data = plistlib.loads(plan.artifact.after.encode())
    assert data["Label"] == "com.laya-guard.server" and data["ProgramArguments"] == [str(binary.resolve()), "serve"]
    assert data["RunAtLoad"] is True and data["KeepAlive"] == {"SuccessfulExit": False}
    assert data["ThrottleInterval"] == 30 and "EnvironmentVariables" not in data
    assert data["StandardOutPath"] == data["StandardErrorPath"] == str(home / "Library" / "Logs" / "laya-guard.log")
    assert plan.artifact.mode == 0o600                                      # an environment may hold a webhook URL


def test_port_and_model_loading_are_options_and_defaults_stay_out_of_the_file(home, binary):
    args = lambda **kw: plistlib.loads(plan_for("install", home, binary, **kw).artifact.after.encode())["ProgramArguments"][1:]
    assert args() == ["serve"]
    assert args(port=9001, warmup=False) == ["serve", "--port", "9001", "--no-warmup"]
    assert args(host="0.0.0.0") == ["serve", "--host", "0.0.0.0"]
    with pytest.raises(service.ServiceError):
        plan_for("install", home, binary, port=70000)


def test_only_the_settings_the_server_reads_are_copied_and_path_only_with_an_alert_command(home, binary):
    env = {"LAYA_GUARD_LOG_PATH": "/data/v.jsonl", "LAYA_GUARD_FAIL": "closed", "LAYA_GUARD_MOCK": "1",
           "AWS_SECRET_ACCESS_KEY": "nope", "PATH": "/opt/homebrew/bin:/usr/bin"}
    only = plistlib.loads(plan_for("install", home, binary, environ=env).artifact.after.encode())
    assert only["EnvironmentVariables"] == {"LAYA_GUARD_LOG_PATH": "/data/v.jsonl"}      # no hook-only, mock or foreign vars, no PATH
    env["LAYA_GUARD_ALERT_CMD"] = 'osascript -e "display notification"'
    both = plistlib.loads(plan_for("install", home, binary, environ=env).artifact.after.encode())
    assert both["EnvironmentVariables"] == {"LAYA_GUARD_LOG_PATH": "/data/v.jsonl", "PATH": env["PATH"],
                                            "LAYA_GUARD_ALERT_CMD": env["LAYA_GUARD_ALERT_CMD"]}
    assert any("LAYA_GUARD_ALERT_CMD" in n and "/opt/homebrew" not in n for n in
               plan_for("install", home, binary, environ=env).notes)                     # the note names them, never the values


def test_the_systemd_unit_quotes_what_systemd_treats_specially(home, binary):
    env = {"LAYA_GUARD_ALERT_CMD": 'curl -d "100% $HOME" https://hooks.example/x', "PATH": "/usr/bin"}
    plan = plan_for("install", home, binary, platform="linux", environ=env)
    assert plan.path == home / ".config" / "systemd" / "user" / "laya-guard.service"
    text = plan.artifact.after
    assert text.startswith(service.UNIT_MARKER)
    assert f'ExecStart="{binary.resolve()}" "serve"' in text
    assert "Restart=on-failure" in text and "RestartSec=30" in text and "WantedBy=default.target" in text
    assert r'Environment="LAYA_GUARD_ALERT_CMD=curl -d \"100%% $$HOME\" https://hooks.example/x"' in text


def test_xdg_config_home_moves_the_unit(home, binary, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert plan_for("install", home, binary, platform="linux").path == tmp_path / "xdg" / "systemd" / "user" / "laya-guard.service"


def test_windows_and_other_systems_are_not_supported_yet(home, binary):
    with pytest.raises(InstallError, match="macOS and Linux"):
        plan_for("install", home, binary, platform="win32")


# --- planning: install again, someone else's file, uninstall -----------------------------------

def test_outcomes_added_unchanged_updated(home, binary):
    first = plan_for("install", home, binary)
    service.apply_plan(first, run=Manager(), uid=501)
    again = plan_for("install", home, binary)
    assert again.outcome == "unchanged" and not again.changed and again.diff == "" and again.notes == []
    moved = plan_for("install", home, binary, port=9001)
    assert moved.outcome == "updated" and "--port" in moved.diff and moved.diff_id != first.diff_id


def test_a_file_that_isnt_ours_is_left_alone_unless_forced(home, binary):
    path = service.unit_path("macos", home)
    path.parent.mkdir(parents=True)
    path.write_bytes(plistlib.dumps({"Label": "com.someone.else", "ProgramArguments": ["/bin/true"]}))
    refused = plan_for("install", home, binary)
    assert refused.outcome == "manual" and not refused.changed and "wasn't written by" in refused.existing
    with pytest.raises(service.ServiceError, match="force"):
        service.apply_plan(refused, run=Manager())
    assert plistlib.loads(path.read_bytes())["Label"] == "com.someone.else"
    assert plan_for("uninstall", home, binary).outcome == "manual"
    forced = plan_for("install", home, binary, force=True)
    assert forced.outcome == "updated" and not forced.ours
    backups, _ = service.apply_plan(forced, run=Manager(), uid=501)
    assert len(backups) == 1 and plistlib.loads(backups[0].read_bytes())["Label"] == "com.someone.else"
    assert plistlib.loads(path.read_bytes())["Label"] == "com.laya-guard.server"


def test_a_unit_without_our_marker_is_not_ours(home, binary):
    path = service.unit_path("linux", home)
    path.parent.mkdir(parents=True)
    path.write_text("[Service]\nExecStart=/bin/true\n")
    assert plan_for("install", home, binary, platform="linux").outcome == "manual"


def test_uninstall_plans(home, binary):
    assert plan_for("uninstall", home, binary).outcome == "nothing"
    service.apply_plan(plan_for("install", home, binary), run=Manager(), uid=501)
    removal = plan_for("uninstall", home, binary)
    assert removal.outcome == "removed" and removal.changed and removal.artifact.after is None
    assert removal.diff.startswith("--- ") and "+++ /dev/null" in removal.diff


# --- applying: the commands that follow the write ----------------------------------------------

def test_macos_install_writes_then_loads_the_agent(home, binary):
    mgr = Manager()
    plan = plan_for("install", home, binary)
    backups, ran = service.apply_plan(plan, run=mgr, uid=501)
    path = plan.path
    assert path.exists() and (path.stat().st_mode & 0o777) == 0o600 and backups == []
    assert (home / "Library" / "Logs").is_dir()                               # launchd won't create the log's folder
    assert mgr.calls == ["launchctl version",
                         "launchctl enable gui/501/com.laya-guard.server",
                         "launchctl bootout gui/501/com.laya-guard.server",
                         f"launchctl bootstrap gui/501 {path}"]
    assert ran[-1].startswith("launchctl bootstrap")


def test_replacing_our_own_file_leaves_no_backup_behind(home, binary):
    service.apply_plan(plan_for("install", home, binary), run=Manager(), uid=501)
    backups, _ = service.apply_plan(plan_for("install", home, binary, port=9001), run=Manager(), uid=501)
    assert backups == [] and [p.name for p in service.unit_path("macos", home).parent.iterdir()] == ["com.laya-guard.server.plist"]


def test_bootstrap_is_retried_while_bootout_finishes(home, binary):
    mgr = Manager(("launchctl bootstrap", [5, 5, 0], ""))
    slept = []
    service.apply_plan(plan_for("install", home, binary), run=mgr, uid=501, sleep=slept.append)
    assert [c for c in mgr.calls if "bootstrap" in c].__len__() == 3 and len(slept) == 2


def test_a_launchagent_that_will_not_load_is_reported(home, binary):
    mgr = Manager(("launchctl bootstrap", [5], ""))
    with pytest.raises(service.ServiceError, match=r"starting the LaunchAgent failed \(exit 5\): boom"):
        service.apply_plan(plan_for("install", home, binary), run=mgr, uid=501, sleep=lambda s: None)


def test_nothing_is_written_when_the_service_manager_is_missing(home, binary):
    for platform, needle in (("darwin", "launchctl"), ("linux", "systemctl")):
        mgr = Manager((needle, [127], ""))
        plan = plan_for("install", home, binary, platform=platform)
        with pytest.raises(service.ServiceError, match="isn't available"):
            service.apply_plan(plan, run=mgr, uid=501)
        assert not plan.path.exists() and len(mgr.calls) == 1


def test_macos_uninstall_stops_the_agent_and_deletes_the_file(home, binary):
    service.apply_plan(plan_for("install", home, binary), run=Manager(), uid=501)
    mgr = Manager()
    plan = plan_for("uninstall", home, binary)
    backups, _ = service.apply_plan(plan, run=mgr, uid=501)
    assert mgr.calls == ["launchctl bootout gui/501/com.laya-guard.server"]     # no preflight: removing must always work
    assert not plan.path.exists() and backups == []


def test_linux_install_and_uninstall(home, binary):
    mgr = Manager()
    plan = plan_for("install", home, binary, platform="linux")
    service.apply_plan(plan, run=mgr)
    assert mgr.calls == ["systemctl --user show-environment", "systemctl --user daemon-reload",
                         "systemctl --user enable laya-guard.service", "systemctl --user restart laya-guard.service"]
    assert plan.path.exists()
    mgr = Manager()
    out = plan_for("uninstall", home, binary, platform="linux")
    service.apply_plan(out, run=mgr)
    assert mgr.calls == ["systemctl --user disable laya-guard.service", "systemctl --user daemon-reload",
                         "systemctl --user stop laya-guard.service"]             # disabled while the unit file still exists
    assert not out.path.exists()


def test_a_page_served_by_the_service_gets_its_answer_before_the_server_is_restarted(home, binary):
    service.apply_plan(plan_for("install", home, binary), run=Manager(), uid=501)
    mgr, spawned = Manager(), []
    plan = plan_for("install", home, binary, port=9001)
    _, ran = service.apply_plan(plan, run=mgr, uid=501, detach=True, spawn=spawned.append)
    assert not any("bootout" in c or "bootstrap" in c for c in mgr.calls)            # nothing that stops the server runs here
    assert len(spawned) == 1 and spawned[0].startswith("sleep 1; ")
    assert "launchctl bootout gui/501/com.laya-guard.server" in spawned[0]
    assert re.search(r"for i in \$\(seq 4\); do launchctl bootstrap gui/501 \S+ && break; sleep 1; done", spawned[0])
    assert ran[-1].endswith("(a moment after this answer)") and plan.path.exists()
    removal = plan_for("uninstall", home, binary)
    service.apply_plan(removal, run=Manager(), uid=501, detach=True, spawn=spawned.append)
    assert spawned[1] == "sleep 1; launchctl bootout gui/501/com.laya-guard.server" and not removal.path.exists()


def test_the_default_runner_and_spawner_are_the_guarded_ones_in_tests(home, binary):
    # conftest swaps them; this is what makes a slip harmless instead of touching the developer's launchd
    with pytest.raises(service.ServiceError, match="isn't available"):
        service.apply_plan(plan_for("install", home, binary), uid=501)
    assert service.status(home, platform="darwin").state == "not-installed"


# --- looking ------------------------------------------------------------------------------------

LAUNCHCTL_RUNNING = "com.laya-guard.server = {\n\tactive count = 1\n\tstate = running\n\tpid = 4242\n}\n"
LAUNCHCTL_WAITING = "com.laya-guard.server = {\n\tstate = not running\n\tlast exit code = 1\n}\n"


def test_status_reads_the_file_and_asks_the_service_manager(home, binary):
    assert service.status(home, run=Manager(), platform="darwin").state == "not-installed"
    service.apply_plan(plan_for("install", home, binary, port=9001, warmup=False), run=Manager(), uid=501)
    running = service.status(home, run=Manager(("launchctl print", [0], LAUNCHCTL_RUNNING)), platform="darwin", uid=501)
    assert (running.state, running.running, running.port, running.warmup) == ("installed", True, 9001, False)
    waiting = service.status(home, run=Manager(("launchctl print", [0], LAUNCHCTL_WAITING)), platform="darwin", uid=501)
    assert waiting.running is False and "last exit code 1" in waiting.detail
    unloaded = service.status(home, run=Manager(("launchctl print", [113], "")), platform="darwin", uid=501)
    assert unloaded.running is False and "not loaded" in unloaded.detail
    unknown = service.status(home, run=Manager(("launchctl print", [127], "")), platform="darwin", uid=501)
    assert unknown.running is None and unknown.state == "installed"


def test_status_on_linux(home, binary):
    service.apply_plan(plan_for("install", home, binary, platform="linux"), run=Manager())
    active = service.status(home, run=Manager(("is-active", [0], "active\n")), platform="linux")
    assert (active.state, active.running, active.port, active.warmup) == ("installed", True, 8787, True)
    failed = service.status(home, run=Manager(("is-active", [3], "")), platform="linux")
    assert failed.running is False


def test_status_notices_a_service_whose_program_is_gone(home, binary):
    service.apply_plan(plan_for("install", home, binary), run=Manager(), uid=501)
    binary.unlink()
    st = service.status(home, run=Manager(), platform="darwin", uid=501)
    assert st.state == "broken" and "missing" in st.detail


def test_status_says_when_the_file_is_someone_elses(home):
    path = service.unit_path("macos", home)
    path.parent.mkdir(parents=True)
    path.write_text("not a plist")
    assert service.status(home, run=Manager(), platform="darwin").state == "manual"


# --- the command line ---------------------------------------------------------------------------

@pytest.fixture
def cli_env(home, binary, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(service, "port_answers", lambda host, port: False)
    monkeypatch.setattr(service, "platform_kind", lambda platform=None: "macos")
    return ["--bin", str(binary)]


def test_cli_dry_run_shows_the_file_and_writes_nothing(cli_env, home, capsys):
    assert cli.main(["service", "install", "--dry-run", *cli_env]) == 0
    out = capsys.readouterr().out
    assert "Adding the LaunchAgent" in out and "<key>RunAtLoad</key>" in out and "(dry run: nothing was written)" in out
    assert "Note: The server starts now and at every login" in out and out.count("The server starts now") == 1
    assert not service.unit_path("macos", home).exists()


def test_cli_needs_a_yes_when_stdin_is_not_a_terminal(cli_env, home, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli.main(["service", "install", *cli_env]) == 2
    assert not service.unit_path("macos", home).exists() and "pass --yes" in capsys.readouterr().err


def test_cli_install_status_uninstall(cli_env, home, capsys, monkeypatch):
    mgr = Manager(("launchctl print", [0], LAUNCHCTL_RUNNING))
    monkeypatch.setattr(service, "run_command", mgr)
    assert cli.main(["service", "install", "--yes", "--no-warmup", *cli_env]) == 0
    out = capsys.readouterr().out
    assert "Wrote ~/Library/LaunchAgents/com.laya-guard.server.plist" in out and "launchctl bootstrap" in out
    assert cli.main(["service", "install", "--yes", "--no-warmup", *cli_env]) == 0
    assert "Already installed and up to date" in capsys.readouterr().out

    assert cli.main(["service", "status", "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert (st["state"], st["running"], st["warmup"], st["port"]) == ("installed", True, False, 8787)
    assert cli.main(["service", "status"]) == 0
    text = capsys.readouterr().out
    assert "LaunchAgent: installed" in text and "model loaded on the first call" in text and "service manager: running" in text

    assert cli.main(["service", "uninstall", "--yes"]) == 0
    assert "stopped and removed" in capsys.readouterr().out and not service.unit_path("macos", home).exists()
    assert cli.main(["service", "uninstall", "--yes"]) == 0
    assert "Not installed" in capsys.readouterr().out


def test_cli_reports_a_service_manager_that_is_missing_as_an_error(cli_env, home, capsys):
    assert cli.main(["service", "install", "--yes", *cli_env]) == 1                  # conftest: no launchctl in tests
    assert "launchctl" in capsys.readouterr().err and not service.unit_path("macos", home).exists()


def test_cli_does_not_take_its_own_service_for_a_server_started_by_hand(cli_env, home, capsys, monkeypatch):
    monkeypatch.setattr(service, "port_answers", lambda host, port: True)
    for running, note in ((True, False), (False, True), (None, True)):
        monkeypatch.setattr(service, "status", lambda home, running=running: service.ServiceStatus(
            kind="macos", path=service.unit_path("macos", home), state="installed", running=running))
        assert cli.main(["service", "install", "--dry-run", *cli_env]) == 0
        assert ("Something already answers on port 8787" in capsys.readouterr().out) is note, running


def test_serve_exits_0_when_it_is_told_to_stop(monkeypatch):
    # uvicorn re-raises SIGTERM after a clean shutdown; launchd would take the 143 for a crash and restart it
    import signal
    pytest.importorskip("fastapi")
    monkeypatch.setattr(cli, "_server_up", lambda host, port: False)
    codes = []

    def fake_run(host, port):
        with pytest.raises(SystemExit) as stop:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        codes.append(stop.value.code)

    monkeypatch.setattr("laya_guard.server.run", fake_run)
    before = signal.getsignal(signal.SIGTERM)
    try:
        cli.cmd_serve(type("A", (), {"host": "127.0.0.1", "port": 8787, "no_warmup": True}))
    finally:
        signal.signal(signal.SIGTERM, before)
    assert codes == [0]


def test_a_real_server_stopped_with_sigterm_exits_0(tmp_path):
    import signal
    import socket
    import urllib.request
    pytest.importorskip("uvicorn")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, LAYA_GUARD_MOCK="1", LAYA_GUARD_LOG="0", PYTHONPATH=str(Path(__file__).resolve().parent.parent / "src"))
    proc = subprocess.Popen([sys.executable, "-m", "laya_guard.cli", "serve", "--port", str(port), "--no-warmup"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("the server didn't start")
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_serve_does_not_start_a_second_server_on_a_taken_port(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_server_up", lambda host, port: True)
    monkeypatch.setattr("laya_guard.cli.warmup", lambda: pytest.fail("must not load the model"))
    args = type("A", (), {"host": "127.0.0.1", "port": 8787, "no_warmup": False})
    assert cli.cmd_serve(args) == 1
    assert "already running" in capsys.readouterr().err


# --- the dashboard's "Start at login" -----------------------------------------------------------

@pytest.fixture
def api(home, binary, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from laya_guard import server
    mgr = Manager(("launchctl print", [113], ""))                      # not loaded until a test says otherwise
    spawned = []
    monkeypatch.setattr(service, "cli_path", lambda override=None: binary)
    monkeypatch.setattr(service, "platform_kind", lambda platform=None: "macos")
    monkeypatch.setattr(service, "port_answers", lambda host, port: True)
    monkeypatch.setattr(service, "run_command", mgr)
    monkeypatch.setattr(service, "spawn_detached", spawned.append)
    client = TestClient(server.app, base_url="http://127.0.0.1:8787")
    token = re.search(r'name="laya-token" content="([^"]+)"', client.get("/dashboard").text).group(1)

    def post(action, body, headers=None):
        h = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:8787", "X-Laya-Token": token, **(headers or {})}
        return client.post(f"/api/service/{action}", content=json.dumps(body), headers={k: v for k, v in h.items() if v})

    def running(yes=True):
        mgr.rules[0][1], mgr.rules[0][2] = ([0], LAUNCHCTL_RUNNING) if yes else ([113], "")

    return type("Api", (), {"client": client, "post": staticmethod(post), "mgr": mgr, "spawned": spawned,
                            "home": home, "running": staticmethod(running)})


def test_service_status_is_readable_and_local_only(api):
    body = api.client.get("/api/service").json()
    assert body["supported"] and body["how"] == "LaunchAgent" and body["state"] == "not-installed"
    assert body["path"] == "~/Library/LaunchAgents/com.laya-guard.server.plist" and body["log"] == "~/Library/Logs/laya-guard.log"
    assert str(api.home) not in json.dumps(body)
    assert api.client.get("/api/service", headers={"host": "evil.example:8787"}).status_code == 403


def test_service_status_says_so_where_it_is_not_supported(api, monkeypatch):
    def refuse(platform=None):
        raise service.ServiceError("not on this system")
    monkeypatch.setattr(service, "platform_kind", refuse)
    assert api.client.get("/api/service").json() == {"supported": False, "detail": "not on this system"}
    assert api.post("install", {"mode": "preview"}).status_code == 400


def test_the_service_can_be_started_at_login_and_removed_from_the_dashboard(api):
    path = service.unit_path("macos", api.home)
    preview = api.post("install", {"mode": "preview"}).json()
    assert preview["outcome"] == "added" and preview["changed"] and not preview["applied"]
    assert "com.laya-guard.server" in preview["diff"] and str(api.home) not in preview["diff"].split("\n@@")[0]
    assert any("every login" in n for n in preview["notes"]) and any("port 8787" in n for n in preview["notes"])
    assert not path.exists() and not any("bootstrap" in c for c in api.mgr.calls)           # a preview changes nothing

    assert api.post("install", {"mode": "apply"}).status_code == 409                        # needs the shown diff
    done = api.post("install", {"mode": "apply", "diff_id": preview["diff_id"]}).json()
    assert done["applied"] and done["detached"] is False and path.exists()
    assert any("launchctl bootstrap" in r for r in done["ran"])

    api.running()
    now = api.client.get("/api/service").json()
    assert (now["state"], now["running"], now["port"], now["warmup"]) == ("installed", True, 8787, True)

    api.running(False)
    removal = api.post("uninstall", {"mode": "preview"}).json()
    assert removal["outcome"] == "removed" and removal["diff"]
    gone = api.post("uninstall", {"mode": "apply", "diff_id": removal["diff_id"]}).json()
    assert gone["applied"] and not path.exists()
    assert api.client.get("/api/service").json()["state"] == "not-installed"


def test_the_service_changes_need_every_check(api):
    body = {"mode": "preview"}
    assert api.post("install", body, headers={"X-Laya-Token": "wrong"}).status_code == 403
    assert api.post("install", body, headers={"Origin": "http://evil.example"}).status_code == 403
    assert api.post("install", body, headers={"Content-Type": "text/plain"}).status_code == 415
    assert api.post("install", body, headers={"host": "evil.example:8787"}).status_code == 403
    assert api.post("reinstall", body).status_code == 404
    assert api.post("install", {"mode": "maybe"}).status_code == 400
    assert not service.unit_path("macos", api.home).exists()


def test_a_file_that_appeared_after_the_preview_is_not_overwritten(api):
    preview = api.post("install", {"mode": "preview"}).json()
    path = service.unit_path("macos", api.home)
    path.parent.mkdir(parents=True)
    path.write_bytes(plistlib.dumps({"Label": "com.someone.else"}))
    r = api.post("install", {"mode": "apply", "diff_id": preview["diff_id"]})
    assert r.status_code == 200 and r.json()["applied"] is False and r.json()["outcome"] == "manual"
    assert plistlib.loads(path.read_bytes())["Label"] == "com.someone.else" and not api.mgr.calls[1:]


def test_a_service_file_that_changed_after_the_preview_needs_a_new_preview(api, binary):
    first = api.post("install", {"mode": "preview"}).json()
    api.post("install", {"mode": "apply", "diff_id": first["diff_id"]})
    stale = api.post("install", {"mode": "preview", "warmup": False}).json()
    service.apply_plan(service.make_plan("install", api.home, bin_override=str(binary), port=9001, environ={}),
                       run=Manager(), uid=501)                                  # someone else changes the file meanwhile
    assert api.post("install", {"mode": "apply", "warmup": False, "diff_id": stale["diff_id"]}).status_code == 409


def test_a_launchagent_that_will_not_load_comes_back_as_a_readable_error(api, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    api.mgr.rules.insert(0, ["launchctl bootstrap", [5], ""])
    preview = api.post("install", {"mode": "preview"}).json()
    r = api.post("install", {"mode": "apply", "diff_id": preview["diff_id"]})
    assert r.status_code == 400 and "starting the LaunchAgent failed" in r.json()["detail"]


def test_a_dashboard_served_by_the_service_answers_before_the_server_restarts(api):
    first = api.post("install", {"mode": "preview"}).json()
    api.post("install", {"mode": "apply", "diff_id": first["diff_id"]})
    api.running()                                                         # the service is up and answering this very page
    calls_before = len(api.mgr.calls)
    update = api.post("install", {"mode": "preview", "warmup": False}).json()
    assert update["outcome"] == "updated" and any("goes away for a few seconds" in n for n in update["notes"])
    assert not any("port 8787" in n and "started by hand" in n for n in update["notes"])           # nothing else holds the port
    done = api.post("install", {"mode": "apply", "warmup": False, "diff_id": update["diff_id"]}).json()
    assert done["applied"] and done["detached"] is True and len(api.spawned) == 1
    assert not any("bootout" in c or "bootstrap" in c for c in api.mgr.calls[calls_before:])

    removal = api.post("uninstall", {"mode": "preview"}).json()
    assert any("stops with it" in n for n in removal["notes"])
    api.post("uninstall", {"mode": "apply", "diff_id": removal["diff_id"]})
    assert api.spawned[1].endswith(f"launchctl bootout gui/{os.getuid()}/com.laya-guard.server")
    assert not service.unit_path("macos", api.home).exists()
