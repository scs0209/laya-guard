"""Protected paths: the rules an agent's file tool calls are judged against. No model, no network.

Every test runs with HOME and the config directory in a temporary place, so neither the developer's own
`~/.config/laya-guard/protect.yaml` nor their real `~/.ssh` can change a result.
"""
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from laya_guard import fileaccess  # noqa: E402
from laya_guard.fileaccess import Rule, judge_file_call, judge_paths  # noqa: E402


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("LAYA_GUARD_MOCK", "1")


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("LAYA_GUARD_PROTECT", raising=False)
    return h


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "project"
    p.mkdir()
    return p


def _user_rules(home, text):
    path = home / ".config" / "laya-guard" / "protect.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- the defaults ----------------------------------------------------------------------------------------------------

def test_credential_stores_are_reviewed_to_read_and_blocked_to_change(home, project):
    read = judge_paths("Read", ["~/.ssh/id_rsa"], str(project))
    assert (read.action, read.policy_id, read.verdict_source) == ("review", "protected-path", "regex")
    assert judge_paths("Write", ["~/.aws/credentials"], str(project)).action == "block"
    assert judge_paths("Edit", [str(home / ".ssh" / "authorized_keys")], str(project)).action == "block"
    assert judge_paths("Grep", ["~/.ssh"], str(project)).action == "review"       # the directory itself counts
    assert judge_paths("Read", ["~/.sshx/notes"], str(project)) is None             # a different name


def test_env_files_and_private_keys_are_protected_in_any_project(home, project):
    assert judge_paths("Edit", [".env.production"], str(project)).action == "review"
    assert judge_paths("Read", [str(project / "deep" / "er" / ".env")], str(project)).action == "review"
    assert judge_paths("Read", ["keys/id_ed25519"], str(project)).action == "review"
    for name in (".env.example", ".env.sample", ".env.template", ".env.dist"):
        assert judge_paths("Read", [name], str(project)) is None, name


def test_the_agents_own_hooks_are_reviewed_to_change_but_not_to_read(home, project):
    assert judge_paths("Write", ["~/.claude/settings.json"], str(project)).action == "review"
    assert judge_paths("Edit", ["~/.claude/hooks/guard.sh"], str(project)).action == "review"
    assert judge_paths("Read", ["~/.claude/settings.json"], str(project)) is None
    assert judge_paths("Write", [".claude/settings.json"], str(project)) is None     # a project's own file is fine


@pytest.mark.parametrize("path", ["~/.bashrc", "~/.bash_profile", "~/.profile", "~/.zshrc", "~/.zshenv", "~/.zprofile",
                                  "~/.config/fish/config.fish", "{project}/.git/hooks/pre-commit", "{project}/.git/hooks"])
def test_shell_startup_files_and_git_hooks_are_reviewed_to_change_but_not_to_read(home, project, path):
    path = path.format(project=project)
    for tool in ("Write", "Edit", "MultiEdit"):
        v = judge_paths(tool, [path], str(project))
        assert (v.action, v.policy_id) == ("review", "protected-path"), (tool, path)
    assert judge_paths("Read", [path], str(project)) is None                           # reading them is fine
    assert judge_file_call("Write", [path], str(project)).action == "review"


def test_other_files_next_to_them_are_not_caught(home, project):
    for path in ("~/.zshrc.bak", "~/.config/fish/functions/x.fish", "{project}/.git/config", "{project}/hooks/pre-commit",
                 "{project}/notes.md"):
        assert judge_paths("Write", [path.format(project=project)], str(project)) is None, path


def test_the_reason_never_names_the_file(home, project):
    v = judge_paths("Read", ["~/.ssh/id_rsa"], str(project))
    assert "id_rsa" not in v.reason and ".ssh" not in v.reason


# --- what counts as a file tool call -----------------------------------------------------------------------------------

def test_a_file_tool_call_is_judged_by_its_paths_alone(home, project):
    v = judge_file_call("Write", ["src/app.py"], str(project))
    assert (v.action, v.policy_id, v.verdict_source) == ("allow", "general", "allowlist")
    assert judge_file_call("Read", ["~/.ssh/id_rsa"], str(project)).action == "review"


def test_anything_else_is_left_to_the_judge(home, project):
    cwd = str(project)
    assert judge_file_call("Bash", ["~/.ssh/id_rsa"], cwd) is None                # not a file tool
    assert judge_file_call("mcp__x__tool", ["~/.ssh/id_rsa"], cwd) is None
    assert judge_file_call(None, ["~/.ssh/id_rsa"], cwd) is None
    assert judge_file_call("Read", None, cwd) is None                              # no paths named
    assert judge_file_call("Read", [], cwd) is None
    assert judge_file_call("Read", ["", "  ", 5], cwd) is None                      # nothing usable
    assert judge_paths("Bash", ["~/.ssh/id_rsa"], cwd) is None


def test_every_tool_the_install_flag_guards_is_a_known_file_tool():
    from laya_guard.install import FILES_MATCHER
    guarded = set(FILES_MATCHER.split("|")) - {"Bash"}
    assert guarded <= set(fileaccess.FILE_TOOLS)
    assert fileaccess.FILE_TOOLS["Read"] == "read" and fileaccess.FILE_TOOLS["Write"] == "write"


# --- any agent's adapter can say what a call touches -------------------------------------------------------------------

def test_an_adapters_own_accesses_are_judged_like_a_claude_code_file_tool(home, project):
    cwd = str(project)
    read = judge_file_call("read_file", None, cwd, accesses=[{"op": "read", "path": "~/.ssh/id_rsa"}])
    assert (read.action, read.policy_id) == ("review", "protected-path")           # not a Claude Code tool name, still judged
    assert judge_file_call("write_file", None, cwd, accesses=[{"op": "write", "path": "~/.aws/credentials"}]).action == "block"
    assert judge_file_call("anything", None, cwd, accesses=[("write", "src/app.py")]).action == "allow"   # pairs work too


def test_deleting_is_judged_by_the_write_rules(home, project):
    cwd = str(project)
    assert judge_file_call("rm", None, cwd, accesses=[{"op": "delete", "path": "~/.ssh/id_rsa"}]).action == "block"
    assert judge_file_call("rm", None, cwd, accesses=[{"op": "delete", "path": "~/.claude/settings.json"}]).action == "review"
    assert judge_file_call("read_file", None, cwd, accesses=[{"op": "read", "path": "~/.claude/settings.json"}]).action == "allow"


def test_accesses_that_make_no_sense_are_dropped_and_override_paths():
    good = fileaccess.accesses_for("Read", ["a.txt"], [{"op": "write", "path": "b.txt"}])
    assert good == [("write", "b.txt")]                                             # the adapter's reading wins
    assert fileaccess.accesses_for("Read", ["a.txt"], []) == []                      # an adapter that says "nothing" means nothing
    junk = [{"op": "launch", "path": "x"}, {"op": "read"}, {"op": "read", "path": " "}, "x", None, 5, ["read"], ("read", 3)]
    assert fileaccess.accesses_for("Read", None, junk) == []
    assert fileaccess.accesses_for("Bash", ["a.txt"], None) == []                    # paths only count for a known file tool
    assert fileaccess.accesses_for("Read", ["a.txt"]) == [("read", "a.txt")]


# --- your own rules ----------------------------------------------------------------------------------------------------

def test_your_rules_add_to_the_defaults_and_relative_globs_follow_the_agents_directory(home, project, tmp_path):
    _user_rules(home, f"""
version: 1
protect:
  - {{path: "prisma/migrations/**", ops: [write], action: block, reason: "schema history"}}
  - {{path: "**/secrets.yaml", action: review}}
  - {{path: "{project}/legacy/**", action: block}}
""")
    cwd = str(project)
    assert judge_paths("Write", ["prisma/migrations/001.sql"], cwd).action == "block"
    assert judge_paths("Read", ["prisma/migrations/001.sql"], cwd) is None           # ops: write only
    assert judge_paths("Edit", ["prisma/migrations"], cwd).action == "block"         # dir/** includes the directory
    assert judge_paths("Write", ["prisma/migrations/001.sql"], str(tmp_path)).action == "block"   # relative: any project
    assert judge_paths("Write", ["legacy/x.py"], cwd).action == "block"                           # absolute: this one only
    assert judge_paths("Write", ["legacy/x.py"], str(tmp_path)) is None
    assert judge_paths("Read", ["any/where/secrets.yaml"], cwd).action == "review"
    assert judge_paths("Read", ["~/.ssh/id_rsa"], cwd).action == "review"             # the defaults still apply


def test_the_strictest_matching_rule_wins(home, project):
    _user_rules(home, 'version: 1\nprotect:\n  - {path: "~/.ssh/**", ops: [read], action: block, reason: "locked down"}\n')
    v = judge_paths("Read", ["~/.ssh/id_rsa"], str(project))
    assert (v.action, v.reason) == ("block", "locked down")


def test_edits_to_your_rules_apply_to_the_next_call(home, project):
    path = _user_rules(home, 'version: 1\nprotect:\n  - {path: "notes/**", action: review}\n')
    assert judge_paths("Read", ["notes/a.md"], str(project)).action == "review"
    path.write_text('version: 1\nprotect:\n  - {path: "notes/**", action: block}\n', encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))                   # a coarse clock can repeat an mtime
    assert judge_paths("Read", ["notes/a.md"], str(project)).action == "block"


def test_a_broken_file_never_switches_the_defaults_off(home, project, caplog):
    _user_rules(home, "protect: [this is: not, valid")
    with caplog.at_level(logging.WARNING, logger="laya_guard"):
        assert judge_paths("Read", ["~/.ssh/id_rsa"], str(project)).action == "review"
    assert any("ignored" in r.message for r in caplog.records)


def test_a_bad_entry_is_skipped_and_the_good_ones_kept(home, project, caplog):
    _user_rules(home, """
version: 1
protect:
  - {path: "a/**", action: nuke}
  - {path: "", action: review}
  - {action: review}
  - {path: "b/**", ops: [delete], action: review}
  - {path: "c/**", action: block}
""")
    with caplog.at_level(logging.WARNING, logger="laya_guard"):
        assert judge_paths("Write", ["c/x"], str(project)).action == "block"
        assert judge_paths("Write", ["a/x"], str(project)) is None
        assert judge_paths("Write", ["b/x"], str(project)) is None
    assert sum("skipped" in r.message for r in caplog.records) >= 4


def test_an_env_override_points_at_another_file(home, project, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere.yaml"
    other.write_text('version: 1\nprotect:\n  - {path: "x/**", action: block}\n', encoding="utf-8")
    monkeypatch.setenv("LAYA_GUARD_PROTECT", str(other))
    assert judge_paths("Write", ["x/a"], str(project)).action == "block"


# --- a path can't be dressed up ----------------------------------------------------------------------------------------

def test_dotdot_and_case_do_not_dodge_a_rule(home, project):
    cwd = str(project)
    assert judge_paths("Read", ["../home/.ssh/id_rsa"], str(home / "elsewhere")).action == "review"
    assert judge_paths("Read", [f"{home}/project/../.ssh/id_rsa"], cwd).action == "review"
    assert judge_paths("Read", ["~/.SSH/ID_RSA"], cwd).action == "review"          # the usual file systems ignore case


def test_a_symlink_into_a_protected_place_is_caught_through_its_real_path(home, project):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("key")
    (project / "innocent.txt").symlink_to(home / ".ssh" / "id_rsa")
    assert judge_paths("Read", ["innocent.txt"], str(project)).action == "review"
    assert judge_paths("Write", ["innocent.txt"], str(project)).action == "block"


def test_a_link_named_like_an_exception_does_not_become_one(home, project):
    (project / ".env").write_text("TOKEN=1")
    (project / ".env.example").symlink_to(project / ".env")
    assert judge_paths("Read", [".env.example"], str(project)).action == "review"  # its target isn't an example


@pytest.mark.parametrize("paths", [
    ["a"] * fileaccess.MAX_PATHS + ["~/.ssh/id_rsa"],                                  # the protected one is the 51st
    ["a"] * 500 + ["~/.ssh/id_rsa"],
    ["a.txt", "../" * 1500 + ".ssh/id_rsa"],                                           # one short path, one padded past the limit
    ["x" * (fileaccess.MAX_PATH_LEN + 1)],                                             # nothing protected, but not checkable
])
def test_a_call_past_a_limit_is_reviewed_not_skipped(home, project, paths):
    for verdict in (judge_paths("Read", paths, str(project)), judge_file_call("Read", paths, str(project))):
        assert (verdict.action, verdict.policy_id, verdict.verdict_source) == ("review", "protected-path", "limit")
        assert ".ssh" not in verdict.reason                                            # the reason never names a path


def test_an_adapters_accesses_past_a_limit_are_reviewed_too(home, project):
    many = [{"op": "read", "path": "a.txt"}] * fileaccess.MAX_PATHS + [{"op": "read", "path": "~/.ssh/id_rsa"}]
    long = [{"op": "write", "path": "x" * (fileaccess.MAX_PATH_LEN + 1)}]
    for accesses in (many, long):
        v = judge_file_call(None, None, str(project), accesses)
        assert (v.action, v.policy_id, v.verdict_source) == ("review", "protected-path", "limit")
    assert judge_file_call(None, None, str(project), many[:fileaccess.MAX_PATHS]).action == "allow"


def test_a_call_at_the_limits_is_still_judged_path_by_path(home, project):
    at_cap = ["a.txt"] * fileaccess.MAX_PATHS
    assert judge_paths("Read", at_cap, str(project)) is None
    assert judge_file_call("Read", at_cap, str(project)).action == "allow"
    assert judge_paths("Read", ["x" * fileaccess.MAX_PATH_LEN], str(project)) is None
    assert judge_paths("Read", at_cap[:-1] + ["~/.ssh/id_rsa"], str(project)).verdict_source == "regex"   # still found at 50


# --- globs --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("glob, path, hit", [
    ("a/*.txt", "a/x.txt", True),
    ("a/*.txt", "a/b/x.txt", False),                  # * stays inside one directory
    ("a/**/x.txt", "a/x.txt", True),
    ("a/**/x.txt", "a/b/c/x.txt", True),
    ("**/x.txt", "x.txt", True),
    ("a/?.txt", "a/b.txt", True),
    ("a/?.txt", "a/bc.txt", False),
    ("a/[1].txt", "a/[1].txt", True),                 # no character classes: brackets are literal
    ("a/[1].txt", "a/1.txt", False),
    ("a/(x)+.txt", "a/(x)+.txt", True),               # regex characters are literal too
    ("a/dir/**", "a/dir", True),
    ("a/dir/**", "a/dir/x/y", True),
    ("a/dir/**", "a/dirx/y", False),
])
def test_glob_semantics(home, project, glob, path, hit):
    rule = Rule(glob=glob, ops=frozenset({"read"}), action="review", reason="t")
    got = judge_paths("Read", [path], str(project), rules=[rule])
    assert (got is not None) is hit
