"""laya-guard sandbox / run: protect.yaml turned into each agent's own OS sandbox settings.

Everything runs with HOME, the config directory and the agents' homes in a temporary place. No agent is started; the
Seatbelt test (macOS only) runs `sandbox-exec` on a generated profile.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from laya_guard import cli, sandbox  # noqa: E402
from laya_guard.install import InstallError  # noqa: E402

try:
    import tomllib
except ImportError:                  # Python 3.10
    tomllib = None


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".ssh").mkdir(parents=True)
    (h / ".ssh" / "id_ed25519").write_text("k")
    for k, v in {"HOME": str(h), "LAYA_GUARD_LOG_PATH": str(tmp_path / "v.jsonl")}.items():
        monkeypatch.setenv(k, v)
    for k in ("XDG_CONFIG_HOME", "LAYA_GUARD_PROTECT", "COPILOT_HOME", "LAYA_GUARD_SEATBELT_BASE", "LAYA_GUARD_SRT"):
        monkeypatch.delenv(k, raising=False)
    return h


def _plan(action, agent, home, scope="user", project=None):
    return sandbox.make_plan(action, agent, scope, home, project or home / "proj")


def _write(plan):
    sandbox.apply_plan(plan)


# --- protect.yaml -> deny entries ------------------------------------------------------------------------------------

def test_the_protected_paths_become_deny_entries(home):
    pol = sandbox.policy()
    by = {(e.kind, e.path): e for e in pol.entries}
    assert by[("home", "~/.ssh")].read and by[("home", "~/.ssh")].write
    assert not by[("home", "~/.bashrc")].read and by[("home", "~/.bashrc")].write      # write-only rule
    assert by[("anywhere", ".env")].read             # its except (.env.example) can't match `.env`
    assert any(p == "**/.env.*" for p, _ in pol.skipped)      # .env.example is carved out: left to the hook


# --- Claude Code ---------------------------------------------------------------------------------------------------

def test_claude_code_gets_deny_lists_and_remove_restores_the_file(home):
    path = home / ".claude" / "settings.json"
    path.parent.mkdir()
    original = json.dumps({"model": "opus", "sandbox": {"filesystem": {"denyRead": ["~/mine"]}}}, indent=2) + "\n"
    path.write_text(original)
    plan = _plan("apply", "claude-code", home)
    _write(plan)
    data = json.loads(path.read_text())
    assert data["sandbox"]["enabled"] is True and data["model"] == "opus"
    assert "~/mine" in data["sandbox"]["filesystem"]["denyRead"]
    assert "~/.ssh" in data["sandbox"]["filesystem"]["denyRead"]
    assert "/**/id_rsa" in data["sandbox"]["filesystem"]["denyRead"]
    assert "~/.bashrc" in data["sandbox"]["filesystem"]["denyWrite"]
    assert not _plan("apply", "claude-code", home).changed            # applying twice changes nothing
    _write(_plan("remove", "claude-code", home))
    assert json.loads(path.read_text()) == json.loads(original)


def test_a_file_laya_guard_created_is_deleted_on_remove(home):
    _write(_plan("apply", "claude-code", home))
    path = home / ".claude" / "settings.json"
    assert path.exists()
    _write(_plan("remove", "claude-code", home))
    assert not path.exists()


def test_an_enabled_flag_the_user_set_survives_remove(home):
    path = home / ".claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"sandbox": {"enabled": True}}))
    _write(_plan("apply", "claude-code", home))
    _write(_plan("remove", "claude-code", home))
    assert json.loads(path.read_text()) == {"sandbox": {"enabled": True}}


# --- Codex ---------------------------------------------------------------------------------------------------------

def test_codex_gets_a_permissions_profile_and_remove_restores_the_file(home):
    path = home / ".codex" / "config.toml"
    path.parent.mkdir()
    original = 'model = "gpt-5"\n\n[tui]\ntheme = "dark"\n'
    path.write_text(original)
    _write(_plan("apply", "codex", home))
    text = path.read_text()
    if tomllib:
        data = tomllib.loads(text)
        assert data["default_permissions"] == "laya-guard" and data["model"] == "gpt-5"
        prof = data["permissions"]["laya-guard"]
        assert prof["extends"] == ":workspace" and prof["filesystem"]["~/.ssh"] == "deny"
        assert prof["filesystem"]["~/.bashrc"] == "read"
        assert prof["filesystem"][":workspace_roots"]["**/id_rsa"] == "deny"
        assert "**/.git/hooks" not in prof["filesystem"][":workspace_roots"]     # a glob takes only "deny"
        assert data["tui"]["theme"] == "dark"
    _write(_plan("remove", "codex", home))
    assert path.read_text() == original


def test_codex_refuses_while_sandbox_mode_or_another_profile_is_set(home):
    path = home / ".codex" / "config.toml"
    path.parent.mkdir()
    for line in ('sandbox_mode = "workspace-write"\n', 'default_permissions = "mine"\n'):
        path.write_text(line)
        with pytest.raises(InstallError):
            _plan("apply", "codex", home)


# --- Copilot CLI ---------------------------------------------------------------------------------------------------

def test_copilot_lists_only_paths_that_exist(home):
    _write(_plan("apply", "copilot-cli", home))
    fs = json.loads((home / ".copilot" / "settings.json").read_text())["sandbox"]["userPolicy"]["filesystem"]
    assert str(home / ".ssh") in fs["deniedPaths"]
    assert str(home / ".aws") not in fs["deniedPaths"]             # Copilot would create it as a directory
    assert not any("*" in p for p in fs["deniedPaths"] + fs["readonlyPaths"])
    with pytest.raises(InstallError):
        _plan("apply", "copilot-cli", home, scope="project")


# --- Cursor --------------------------------------------------------------------------------------------------------

def test_cursor_turns_the_cli_sandbox_on_and_tolerates_trailing_commas(home):
    path = home / ".cursor" / "cli-config.json"
    path.parent.mkdir()
    path.write_text('{"approvalMode": "allowlist", "sandbox": {"mode": "disabled",},}')
    plan = _plan("apply", "cursor", home)
    assert plan.repaired
    _write(plan)
    data = json.loads(path.read_text())
    assert data["sandbox"]["mode"] == "enabled" and data["approvalMode"] == "allowlist"
    _write(_plan("remove", "cursor", home))
    # the user's explicit "disabled" comes back; the key isn't dropped to the CLI's default (pointed out in review)
    assert json.loads(path.read_text()) == {"approvalMode": "allowlist", "sandbox": {"mode": "disabled"}}


def test_a_failed_write_puts_back_the_files_already_written(home, tmp_path, monkeypatch):
    """Gemini writes three files; if a later one fails, the earlier ones go back and nothing is recorded."""
    base = tmp_path / "sandbox-macos-permissive-open.sb"
    base.write_text("(version 1)\n(allow default)\n")
    monkeypatch.setenv("LAYA_GUARD_SEATBELT_BASE", str(base))
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    project = tmp_path / "proj"
    (project / ".gemini").mkdir(parents=True)
    settings = project / ".gemini" / "settings.json"
    settings.write_text('{"theme": "dark"}\n')
    plan = _plan("apply", "gemini-cli", home, scope="project", project=project)
    real = sandbox.write_artifact

    def failing(artifact):
        if artifact.path.name == ".env":
            raise OSError("disk full")
        return real(artifact)
    monkeypatch.setattr(sandbox, "write_artifact", failing)
    with pytest.raises(OSError):
        sandbox.apply_plan(plan)
    assert settings.read_text() == '{"theme": "dark"}\n'
    assert not (project / ".gemini" / "sandbox-macos-laya-guard.sb").exists()
    assert not sandbox.state_path().exists()


# --- Gemini CLI / Qwen Code ------------------------------------------------------------------------------------------

BASE = "(version 1)\n(allow default)\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt profiles are macOS-only")
@pytest.mark.parametrize("agent,folder", [("gemini-cli", ".gemini"), ("qwen-code", ".qwen")])
def test_gemini_and_qwen_get_a_profile_in_the_project(home, tmp_path, monkeypatch, agent, folder):
    base = tmp_path / "sandbox-macos-permissive-open.sb"
    base.write_text(BASE)
    monkeypatch.setenv("LAYA_GUARD_SEATBELT_BASE", str(base))
    project = tmp_path / "proj"
    project.mkdir()
    (project / folder).mkdir()
    (project / folder / ".env").write_text("FOO=1\n")
    with pytest.raises(InstallError):
        _plan("apply", agent, home, scope="user", project=project)     # the CLI only looks in the project
    _write(_plan("apply", agent, home, scope="project", project=project))
    sb = (project / folder / "sandbox-macos-laya-guard.sb").read_text()
    assert sb.endswith(")\n") and '(string-append (param "HOME_DIR") "/.ssh")' in sb
    assert (project / folder / ".env").read_text() == "FOO=1\nSEATBELT_PROFILE=laya-guard\n"
    assert json.loads((project / folder / "settings.json").read_text())["tools"]["sandbox"] == "sandbox-exec"
    _write(_plan("remove", agent, home, scope="project", project=project))
    assert (project / folder / ".env").read_text() == "FOO=1\n"
    assert not (project / folder / "sandbox-macos-laya-guard.sb").exists()
    assert not (project / folder / "settings.json").exists()


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("sandbox-exec"), reason="needs macOS sandbox-exec")
def test_the_generated_seatbelt_profile_denies_the_protected_paths(home, tmp_path):
    secret = home / ".ssh" / "id_ed25519"
    work = tmp_path / "work"
    work.mkdir()
    (work / "ok.txt").write_text("fine")
    (work / "deep").mkdir()
    (work / "deep" / "id_rsa").write_text("k")
    profile = tmp_path / "p.sb"
    profile.write_text(sandbox.seatbelt_profile(BASE, "base.sb", "gemini-cli", sandbox.policy()))

    def cat(path):
        return subprocess.run(["sandbox-exec", "-f", str(profile), "-D", f"HOME_DIR={home}", "cat", str(path)],
                              capture_output=True, text=True).returncode
    assert cat(work / "ok.txt") == 0
    assert cat(secret) != 0
    assert cat(work / "deep" / "id_rsa") != 0


# --- laya-guard run --------------------------------------------------------------------------------------------------

def test_srt_settings_deny_the_protected_paths_and_keep_the_project_writable(home, tmp_path):
    s = sandbox.srt_settings(sandbox.policy(), home, tmp_path, allow_write=["~/extra"], allow_domains=["x.example"])
    assert str(home / ".ssh") in s["filesystem"]["denyRead"]
    assert str(tmp_path) in s["filesystem"]["allowWrite"] and str(home / "extra") in s["filesystem"]["allowWrite"]
    assert str(home / ".bashrc") in s["filesystem"]["denyWrite"]
    assert "api.anthropic.com" in s["network"]["allowedDomains"] and "x.example" in s["network"]["allowedDomains"]
    assert "*" not in s["network"]["allowedDomains"] and s["allowPty"] is True


def test_srt_never_lets_the_agent_write_laya_guards_config_or_all_of_dot_config(home, tmp_path):
    """From ~/.config a process could rewrite protect.yaml or add a login autostart (pointed out in review)."""
    s = sandbox.srt_settings(sandbox.policy(), home, tmp_path, agent="/opt/homebrew/bin/opencode")
    fs = s["filesystem"]
    assert str(home / ".config") not in fs["allowWrite"]
    assert str(home / ".config" / "opencode") in fs["allowWrite"]
    assert str(home / ".config" / "laya-guard") in fs["denyWrite"]
    s = sandbox.srt_settings(sandbox.policy(), home, tmp_path, agent="laya-guard")
    assert str(home / ".config" / "laya-guard") not in s["filesystem"]["allowWrite"]


def test_run_says_how_to_get_srt(home, monkeypatch, capsys):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert cli.main(["run", "--", "opencode"]) == 1
    assert "sandbox-runtime" in capsys.readouterr().err


def test_run_passes_the_agent_after_a_double_dash(home, tmp_path, monkeypatch):
    fake = tmp_path / "srt"
    fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$SRT_ARGS\"\n")
    fake.chmod(0o755)
    monkeypatch.setenv("LAYA_GUARD_SRT", str(fake))
    monkeypatch.setenv("SRT_ARGS", str(tmp_path / "args"))
    assert cli.main(["run", "--", "opencode", "--version"]) == 0
    args = (tmp_path / "args").read_text().split("\n")
    assert args[0] == "--settings" and args[2:5] == ["--", "opencode", "--version"]


# --- the CLI --------------------------------------------------------------------------------------------------------

def test_the_cli_shows_the_change_and_writes_it_with_yes(home, capsys):
    assert cli.main(["sandbox", "claude-code", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert '"denyRead"' in out and "dry run" in out
    assert not (home / ".claude" / "settings.json").exists()
    assert cli.main(["sandbox", "claude", "-y"]) == 0
    assert json.loads((home / ".claude" / "settings.json").read_text())["sandbox"]["enabled"] is True
    assert cli.main(["sandbox", "claude", "--remove", "-y"]) == 0
    assert not (home / ".claude" / "settings.json").exists()


def test_the_cli_lists_what_each_agent_can_take(home, capsys):
    assert cli.main(["sandbox", "--list"]) == 0
    out = capsys.readouterr().out
    assert "codex" in out and "laya-guard run" in out
    assert cli.main(["sandbox", "windsurf"]) == 1
