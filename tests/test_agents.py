"""Installers for every supported agent: where each hook goes, what the entry looks like, how it merges
with what is already there, and that the real adapters answer the way the agent expects.

Everything runs in a temporary HOME with a stand-in `laya-guard` executable. No model, no network, and
none of the agents themselves: their formats come from their docs and from files other tools left on a
real machine (see the comments next to each fixture).
"""
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from laya_guard import cli, install  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_BIN_DIR = Path(tempfile.mkdtemp())
GUARD_BIN = _BIN_DIR / "laya-guard"        # stand-in for the installed executable
GUARD_BIN.write_text(f'#!/bin/sh\nPYTHONPATH="{ROOT / "src"}" exec "{sys.executable}" -m laya_guard.cli "$@"\n')
GUARD_BIN.chmod(0o755)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(install, "server_health", lambda url=None: False)
    return tmp_path


def plan(action, agent, home, **kw):
    return install.make_plan(action, agent, "user", home, home / "proj", bin_override=str(GUARD_BIN), **kw)


def apply(action, agent, home, **kw):
    p = plan(action, agent, home, **kw)
    backups, probes = install.apply_plan(p)
    return p, backups, probes


def read_json(path):
    return json.loads(path.read_text())


# --- the adapters we ship are the ones documented in integrations/ ------------------------------

PACKAGED = {
    "guard-codex-hook.py": "integrations/codex/check_command.py",
    "guard-gemini-hook.py": "integrations/gemini/check.py",
    "guard-qwen-hook.py": "integrations/qwen/check.py",
    "guard-windsurf-hook.py": "integrations/windsurf/guard.py",
    "guard-copilot-hook.py": "integrations/copilot/policy.py",
    "guard-cline-hook.py": "integrations/cline/PreToolUse",
    "guard-opencode-plugin.ts": "integrations/opencode/laya-guard.ts",
    "guard-kilo-plugin.ts": "integrations/kilo/laya-guard.ts",
    "guard-amp-plugin.ts": "integrations/amp/laya-guard.ts",
}


@pytest.mark.parametrize("packaged,original", PACKAGED.items())
def test_packaged_adapter_matches_the_documented_one(packaged, original):
    assert install.hook_script(packaged).read_bytes() == (ROOT / original).read_bytes(), \
        f"copy integrations/{original.split('integrations/')[1]} to src/laya_guard/hooks/{packaged}"


def test_every_agent_ships_its_adapter_under_a_name_other_tools_dont_use():
    generic = {"check.py", "guard.py", "policy.py", "cursor-hook.sh", "PreToolUse", "hooks.json"}
    for spec in install.SPECS.values():
        assert install.hook_script(spec.adapter).is_file(), spec.id
        assert spec.adapter not in generic and spec.adapter.startswith("guard-"), spec.id


def test_packaged_plugins_have_a_bin_default_to_set_and_the_right_export_shape():
    for name in ("guard-opencode-plugin.ts", "guard-kilo-plugin.ts", "guard-amp-plugin.ts"):
        text = install.hook_script(name).read_text()
        assert install._BIN_DEFAULT.search(text), name
    kilo = install.hook_script("guard-kilo-plugin.ts").read_text()
    assert 'export default { id: "laya-guard", server: LayaGuard }' in kilo       # Kilo needs the default descriptor
    assert "export const LayaGuard" in install.hook_script("guard-opencode-plugin.ts").read_text()
    amp = install.hook_script("guard-amp-plugin.ts").read_text()
    assert "export default function (amp: PluginAPI)" in amp and "amp.on('tool.call'" in amp and "reject-and-continue" in amp


# --- JSON agents: one parametrized contract -----------------------------------------------------
# agent: (path under HOME, an unrelated file of the user's, where our entry ends up, the entry check)

OTHER_HOOK = {"type": "command", "command": "echo other-tool"}


def _grouped(settings, event, matcher):
    groups = settings["hooks"][event]
    ours = [g for g in groups if any(install.is_managed(h.get("command", "")) for h in g.get("hooks", []))]
    assert len(ours) == 1 and ours[0]["matcher"] == matcher
    return ours[0]["hooks"][-1]


CASES = {
    "gemini-cli": (".gemini/settings.json", {"theme": "dark", "tools": {"core": ["x"]}}),
    "qwen-code": (".qwen/settings.json", {"model": {"name": "q"}}),
    "windsurf": (".codeium/windsurf/hooks.json", {"hooks": {"post_run_command": [{"command": "echo done"}]}}),
    "codex": (".codex/hooks.json", {"hooks": {"SessionStart": [{"hooks": [OTHER_HOOK]}]}}),
}


@pytest.mark.parametrize("agent", CASES)
def test_json_agent_install_merges_checks_itself_and_uninstalls_cleanly(home, agent):
    rel, original = CASES[agent]
    path = home / rel
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(original, indent=2) + "\n")

    p, backups, probes = apply("install", agent, home)
    assert p.outcome == "added" and len(backups) == 1 and backups[0].read_text() == json.dumps(original, indent=2) + "\n"
    assert [(label, e == g) for label, e, g in probes] == [("a harmless call passes", True),
                                                          ("a download piped into a shell is blocked", True)]
    data = read_json(path)
    for key, value in original.items():                       # the user's own settings stay
        assert key in data
    assert install.status("user", home, home / "proj", agent).state == "installed"

    again = plan("install", agent, home)
    assert again.outcome == "unchanged" and not again.changed

    gone, _, _ = apply("uninstall", agent, home)
    assert gone.outcome == "removed"
    after = read_json(path)
    for key, value in original.items():
        if key != "tools":                                    # Gemini's tools.enableHooks flag is left on
            assert after[key] == value, key
    assert install.status("user", home, home / "proj", agent).state == "not-installed"


def test_gemini_entry_and_its_hooks_switch(home):
    path = home / ".gemini" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"tools": {"core": ["x"], "enableHooks": False}}))
    p, _, _ = apply("install", "gemini-cli", home)
    data = read_json(path)
    assert data["tools"] == {"core": ["x"], "enableHooks": True}                       # hooks are off by default in 0.20
    hook = _grouped(data, "BeforeTool", "^run_shell_command$")
    assert hook["name"] == "laya-guard" and hook["type"] == "command" and hook["timeout"] == 30000    # milliseconds
    assert any("tools.enableHooks" in n for n in p.notes)
    # the hook alone isn't enough: with the switch off, status says so and a repair turns it on
    data["tools"]["enableHooks"] = False
    path.write_text(json.dumps(data))
    st = install.status("user", home, home / "proj", "gemini-cli")
    assert st.state == "broken" and "enableHooks" in st.detail
    fix, _, _ = apply("install", "gemini-cli", home)
    assert fix.outcome == "updated" and read_json(path)["tools"]["enableHooks"] is True


def test_gemini_says_when_the_switch_also_wakes_up_other_tools_hooks(home):
    path = home / ".gemini" / "settings.json"                # a real settings.json: another tool's hooks, no switch
    path.parent.mkdir()
    path.write_text(json.dumps({"hooks": {"BeforeTool": [{"matcher": "x", "hooks": [OTHER_HOOK]}], "SessionStart": []}}))
    p = plan("install", "gemini-cli", home)
    assert any("BeforeTool" in n and "start running too" in n for n in p.notes)
    assert not any("SessionStart" in n for n in p.notes)     # an empty event isn't a hook that would start running
    clean = home / "other" / ".gemini" / "settings.json"
    clean.parent.mkdir(parents=True)
    assert not any("start running" in n for n in plan("install", "gemini-cli", home / "other").notes)


def test_qwen_windsurf_and_codex_json_entries(home):
    apply("install", "qwen-code", home)
    hook = _grouped(read_json(home / ".qwen" / "settings.json"), "PreToolUse", "^run_shell_command$")
    assert hook["name"] == "laya-guard" and hook["timeout"] == 30                      # seconds

    apply("install", "windsurf", home)
    ws = read_json(home / ".codeium" / "windsurf" / "hooks.json")
    assert list(ws) == ["hooks"] and ws["hooks"]["pre_run_command"][0]["show_output"] is True
    assert install.is_managed(ws["hooks"]["pre_run_command"][0]["command"])

    p, _, _ = apply("install", "codex", home)
    assert p.path == home / ".codex" / "hooks.json" and not (home / ".codex" / "config.toml").exists()
    hook = _grouped(read_json(p.path), "PreToolUse", "^Bash$")
    assert hook["timeout"] == 30 and hook["statusMessage"].startswith("laya-guard")
    assert "/hooks" in p.follow_up                                                      # Codex asks the user to trust it


def test_python_adapters_run_under_the_interpreter_that_installed_them(home):
    for agent in ("codex", "gemini-cli", "qwen-code", "windsurf", "copilot-cli"):
        command = plan("install", agent, home).command
        tokens = install._tokens(command)
        assert tokens[2] == sys.executable and Path(tokens[3]).name == install.SPECS[agent].adapter, agent


# --- Copilot CLI: a drop-in file of its own -----------------------------------------------------
# ~/.copilot/hooks/orca.json is what another tool leaves there (version 1, PascalCase events, `bash` commands)

ORCA_COPILOT = {"version": 1, "hooks": {"SessionStart": [{"type": "command", "bash": "echo hi", "timeoutSec": 5}]}}


def test_copilot_gets_its_own_hook_file_and_leaves_other_tools_files_alone(home):
    hooks = home / ".copilot" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "orca.json").write_text(json.dumps(ORCA_COPILOT))
    p, backups, probes = apply("install", "copilot-cli", home)
    mine = hooks / "laya-guard.json"
    assert p.path == mine and backups == [] and all(e == g for _, e, g in probes) and len(probes) == 2
    data = read_json(mine)
    entry = data["hooks"]["preToolUse"][0]
    assert data["version"] == 1 and entry["type"] == "command" and entry["timeoutSec"] == 30 and entry["matcher"] == "bash"
    assert install.is_managed(entry["bash"])                                            # Copilot runs the `bash` field
    assert read_json(hooks / "orca.json") == ORCA_COPILOT
    apply("uninstall", "copilot-cli", home)
    assert not mine.exists() and read_json(hooks / "orca.json") == ORCA_COPILOT          # nothing of ours is left
    assert [f.name for f in hooks.glob("*.json")] == ["orca.json"]                       # the backup isn't a *.json


def test_copilot_keeps_other_hooks_in_a_file_that_was_already_there(home):
    mine = home / ".copilot" / "hooks" / "laya-guard.json"
    mine.parent.mkdir(parents=True)
    mine.write_text(json.dumps(ORCA_COPILOT))
    apply("install", "copilot-cli", home)
    apply("uninstall", "copilot-cli", home)
    assert read_json(mine) == ORCA_COPILOT                       # not ours alone, so it stays


def test_copilot_hand_written_adapter_is_reported(home):
    mine = home / ".copilot" / "hooks" / "laya-guard.json"        # what integrations/README.md tells people to create
    mine.parent.mkdir(parents=True)
    manual = {"version": 1, "hooks": {"preToolUse": [{"type": "command", "bash": "~/.copilot/hooks/laya-guard.py", "timeoutSec": 30}]}}
    mine.write_text(json.dumps(manual))
    assert plan("install", "copilot-cli", home).outcome == "manual"
    assert install.status("user", home, home / "proj", "copilot-cli").state == "manual"


# --- Codex: hooks kept inline in config.toml ----------------------------------------------------
# the shape of a real ~/.codex/config.toml that another tool wrote hooks into

CODEX_TOML = '''model = "x"

[features]
goals = true

[[hooks.SessionStart]]
matcher = "startup"

[[hooks.SessionStart.hooks]]
type = "command"
command = "echo session"
timeout = 5
'''


def test_codex_with_inline_toml_hooks_gets_a_marked_block_and_restores_byte_for_byte(home):
    cfg = home / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(CODEX_TOML)
    p, backups, probes = apply("install", "codex", home)
    assert p.path == cfg and not (home / ".codex" / "hooks.json").exists()          # one representation per layer
    assert len(backups) == 1 and backups[0].read_text() == CODEX_TOML and len(probes) == 2
    text = cfg.read_text()
    assert text.startswith(CODEX_TOML) and install.TOML_BEGIN in text and text.rstrip().endswith(install.TOML_END)
    try:
        import tomllib
    except ImportError:                                                              # Python 3.10: no parser to check with
        tomllib = None
    if tomllib:
        parsed = tomllib.loads(text)
        assert parsed["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "echo session"     # theirs, untouched
        pre = parsed["hooks"]["PreToolUse"][0]
        assert pre["matcher"] == "^Bash$" and install.is_managed(pre["hooks"][0]["command"]) and pre["hooks"][0]["timeout"] == 30
    assert install.status("user", home, home / "proj", "codex").state == "installed"
    assert plan("install", "codex", home).outcome == "unchanged"
    apply("uninstall", "codex", home)
    assert cfg.read_text() == CODEX_TOML                                              # not a byte of theirs changed


def test_codex_prefers_an_existing_hooks_json_and_reports_a_hand_written_toml_entry(home):
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text(CODEX_TOML)
    (home / ".codex" / "hooks.json").write_text("{}")
    assert plan("install", "codex", home).path == home / ".codex" / "hooks.json"
    (home / ".codex" / "hooks.json").unlink()
    (home / ".codex" / "config.toml").write_text(CODEX_TOML + '\n[[hooks.PreToolUse.hooks]]\ncommand = "python3 ~/.codex/hooks/check_command.py"\n')
    p = plan("install", "codex", home, force=True)                                   # even --force can't swap TOML lines
    assert p.outcome == "manual" and "delete it by hand" in p.existing and not p.changed
    assert install.status("user", home, home / "proj", "codex").state == "manual"


def test_codex_refuses_a_config_toml_that_is_not_valid_toml(home):
    pytest.importorskip("tomllib")
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('[[hooks.SessionStart]\nbroken = \n')
    with pytest.raises(install.InstallError, match="TOML"):
        plan("install", "codex", home)


# --- plugin and wrapper agents ------------------------------------------------------------------

PLUGINS = {"opencode": ".config/opencode/plugins/laya-guard.ts",
           "kilo": ".config/kilo/plugin/laya-guard.ts",
           "amp": ".config/amp/plugins/laya-guard.ts"}


@pytest.mark.parametrize("agent", PLUGINS)
def test_plugin_agents_get_the_packaged_plugin_with_the_cli_path_baked_in(home, agent):
    path = home / PLUGINS[agent]
    p, backups, probes = apply("install", agent, home)
    assert p.path == path and p.outcome == "added" and backups == [] and probes == []       # only the agent can run a plugin
    text = path.read_text()
    assert install.MARKER in text.splitlines()[0]
    assert f'process.env.LAYA_GUARD_BIN ?? {json.dumps(str(GUARD_BIN.resolve()))}' in text
    assert install._normalized_plugin(text) == install._normalized_plugin(install.hook_script(install.SPECS[agent].adapter).read_text())
    st = install.status("user", home, home / "proj", agent)
    assert st.state == "installed" and st.command == ""
    assert plan("install", agent, home).outcome == "unchanged"

    path.write_text(text.replace("GUARD_URL", "GUARD_URL_EDITED", 1))                       # the file drifted from what ships
    assert install.status("user", home, home / "proj", agent).state == "broken"
    fix, backups, _ = apply("install", agent, home)
    assert fix.outcome == "updated" and len(backups) == 1 and path.read_text() == text

    gone, backups, _ = apply("uninstall", agent, home)
    assert gone.outcome == "removed" and not path.exists() and len(backups) == 1
    assert install.status("user", home, home / "proj", agent).state == "not-installed"


@pytest.mark.parametrize("agent", PLUGINS)
def test_a_plugin_file_laya_guard_didnt_write_is_left_alone_unless_forced(home, agent):
    path = home / PLUGINS[agent]
    path.parent.mkdir(parents=True)
    path.write_text("// my own plugin\n")
    assert plan("install", agent, home).outcome == "manual" and not plan("install", agent, home).changed
    assert install.status("user", home, home / "proj", agent).state == "manual"
    left, _, _ = apply("uninstall", agent, home)
    assert left.outcome == "nothing" and left.left == 1 and path.read_text() == "// my own plugin\n"
    forced, backups, _ = apply("install", agent, home, force=True)
    assert forced.outcome == "updated" and len(backups) == 1 and install.MARKER in path.read_text()


def test_plugin_directories_follow_xdg_config_home_and_the_project_scope(home, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    assert plan("install", "amp", home).path == home / "xdg" / "amp" / "plugins" / "laya-guard.ts"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    proj = home / "proj"
    for agent, rel in {"opencode": ".opencode/plugins", "kilo": ".kilo/plugin", "amp": ".amp/plugins"}.items():
        p = install.make_plan("install", agent, "project", home, proj, bin_override=str(GUARD_BIN))
        assert p.path == proj / rel / "laya-guard.ts", agent


def test_cline_gets_an_executable_wrapper_that_answers_like_cline_expects(home):
    path = home / "Documents" / "Cline" / "Hooks" / "PreToolUse"
    p, backups, probes = apply("install", "cline", home)
    assert p.path == path and backups == [] and len(probes) == 2 and all(e == g for _, e, g in probes)
    assert path.stat().st_mode & stat.S_IXUSR                                         # Cline only runs executables
    lines = path.read_text().splitlines()
    assert lines[0] == "#!/bin/sh" and install.MARKER in lines[1] and "guard-cline-hook.py" in lines[-1]
    assert "Hooks tab" in p.follow_up                                                 # Cline's own on/off switch is its UI
    assert install.status("user", home, home / "proj", "cline").state == "installed"
    apply("uninstall", "cline", home)
    assert not path.exists()


def test_cline_status_notices_when_the_adapter_it_points_to_is_gone(home):
    apply("install", "cline", home)
    path = home / "Documents" / "Cline" / "Hooks" / "PreToolUse"
    path.write_text(path.read_text().replace("guard-cline-hook.py", "gone/guard-cline-hook.py"))
    st = install.status("user", home, home / "proj", "cline")
    assert st.state == "broken" and "not found" in st.detail


# --- the CLI ------------------------------------------------------------------------------------

def _cli(*argv):
    return cli.main(list(argv))


def test_cli_installs_any_agent_by_name_or_short_name_and_says_what_is_left_to_do(home, capsys):
    assert _cli("install", "gemini", "--bin", str(GUARD_BIN), "--yes") == 0              # `gemini` is short for gemini-cli
    out = capsys.readouterr().out
    assert "Gemini CLI" in out and "tools.enableHooks" in out and "answered allow" in out and "answered deny" in out
    assert "Next: Restart Gemini CLI" in out
    assert _cli("install", "amp", "--bin", str(GUARD_BIN), "--yes") == 0
    out = capsys.readouterr().out
    assert "No test calls" in out and 'Next: Run "plugins: reload"' in out
    assert _cli("install", "codex", "--bin", str(GUARD_BIN), "--dry-run") == 0
    assert "Heads-up" in capsys.readouterr().out                                          # format known from docs only
    assert not (home / ".codex").exists()


def test_cli_status_lists_what_is_here_and_summarizes_the_rest(home, capsys, monkeypatch):
    monkeypatch.setattr(install, "_search_dirs", lambda h: [])
    monkeypatch.setattr(install, "_app_dirs", lambda h: [])
    (home / ".gemini").mkdir()
    apply("install", "amp", home)
    assert _cli("status") == 0
    out = capsys.readouterr().out
    assert "gemini-cli" in out and "amp" in out and "installed" in out
    assert "not found on this machine:" in out and "cursor" in out.split("not found on this machine:")[1]
    assert _cli("status", "--all") == 0
    assert "windsurf" in capsys.readouterr().out.split("not found on this machine")[0]
    assert _cli("status", "--agent", "kilo", "--check", "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"server", "kilo", "probes"} and report["kilo"][0]["state"] == "not-installed" and report["probes"] == []
    assert _cli("status", "--agent", "aider") == 1
    assert _cli("uninstall", "aider", "--yes") == 1


def test_detection_knows_where_every_supported_agent_lives(tmp_path, monkeypatch):
    bin_dir, apps = tmp_path / "bin", tmp_path / "Applications"
    for d in (bin_dir, apps):
        d.mkdir()
    monkeypatch.setattr(install, "_search_dirs", lambda h: [bin_dir])
    monkeypatch.setattr(install, "_app_dirs", lambda h: [apps])
    assert {d["id"] for d in install.detect_agents(tmp_path)} == set(install.AGENTS)
    for cmd in ("qwen", "copilot", "opencode", "kilocode", "amp", "windsurf"):
        f = bin_dir / cmd
        f.write_text("#!/bin/sh\n")
        f.chmod(0o755)
    (tmp_path / ".config" / "kilo").mkdir(parents=True)
    (tmp_path / "Documents" / "Cline").mkdir(parents=True)
    got = {d["id"]: d for d in install.detect_agents(tmp_path)}
    found = {i for i, d in got.items() if d["found"]}
    assert found == {"qwen-code", "copilot-cli", "opencode", "kilo", "amp", "windsurf", "cline"}
    assert got["cline"]["via"] == ["~/Documents/Cline"] and got["kilo"]["how"] == "installed"


def test_agent_names_are_unique_and_every_spec_has_what_its_kind_needs():
    assert len(install.SPECS) == len(install.KNOWN_AGENTS) == 11
    assert {k.id for k in install.KNOWN_AGENTS} == set(install.SPECS)
    for spec in install.SPECS.values():
        assert "user" in spec.paths and "project" in spec.paths or spec.id == "claude-code"
        if spec.kind == "json":
            assert spec.entry and spec.event, spec.id
            assert install.is_ours(install.hook_command(Path("/x/laya-guard"), Path("/y") / spec.adapter, spec.runner), spec.adapter)
        else:
            assert spec.kind in ("plugin", "wrapper") and not spec.event
