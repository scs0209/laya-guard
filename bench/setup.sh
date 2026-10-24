#!/usr/bin/env bash
# Install each guard with its own installer into its own HOME under bench/.tools/<guard>/home, so bench/run.py can
# call the hook exactly as Claude Code would. Nothing touches your real ~/.claude or global packages.
#
#   bash bench/setup.sh                 # every guard
#   bash bench/setup.sh dcg shellfirm   # some
#
# Pinned to the versions in bench/guards.json. macOS arm64 for dcg's binary; on other platforms pick the matching
# asset from its release and update DCG_ASSET / DCG_SHA256.
set -euo pipefail
BENCH="$(cd "$(dirname "$0")" && pwd)"
TOOLS="$BENCH/.tools"
REPO="$(dirname "$BENCH")"
PY="${PYTHON:-$REPO/.venv/bin/python}"

DCG_VERSION=v0.15.3
DCG_ASSET=dcg-aarch64-apple-darwin.tar.xz
DCG_SHA256=50a1a50762299c7e4b5ebaf3f932f45066c8e7c86a1d5d419b01babd1e2698d7
CCSN_VERSION=2.6.18
SHELLFIRM_VERSION=0.3.10

fresh_home() { rm -rf "$TOOLS/$1/home"; mkdir -p "$TOOLS/$1/home"; }
in_home() { local g=$1; shift; (cd "$TOOLS/$g/home" && HOME="$TOOLS/$g/home" XDG_CONFIG_HOME="$TOOLS/$g/home/.config" "$@"); }

setup_laya-guard() {
  fresh_home laya-guard
  # this repository's own CLI: the hook it installs for Claude Code (the server is started by run.py)
  in_home laya-guard env PYTHONPATH="$REPO/src" "$PY" -m laya_guard.cli install claude-code -y --no-verify >/dev/null
}

install_dcg() {
  fresh_home "$1"
  local d="$TOOLS/$1"
  curl -fsSL -o "$d/$DCG_ASSET" \
    "https://github.com/Dicklesworthstone/destructive_command_guard/releases/download/$DCG_VERSION/$DCG_ASSET"
  echo "$DCG_SHA256  $d/$DCG_ASSET" | shasum -a 256 -c -
  tar -xJf "$d/$DCG_ASSET" -C "$d"
  # its installer, `dcg install`, writes the Claude Code hook (the README's curl | bash script also does this)
  in_home "$1" "$d/dcg" install --quiet
}

setup_dcg() {
  install_dcg dcg                       # no config file: dcg's default packs
}

setup_dcg-packs() {
  install_dcg dcg-packs
  # the same install plus the opt-in packs for the cloud, database and platform cases, enabled the way its README
  # says (`[packs] enabled` in ~/.config/dcg/config.toml). A separate row; the default dcg row stays as it is.
  mkdir -p "$TOOLS/dcg-packs/home/.config/dcg"
  cat > "$TOOLS/dcg-packs/home/.config/dcg/config.toml" <<'TOML'
[packs]
enabled = ["cloud.aws", "database.postgresql", "platform.railway"]
TOML
}

setup_cc-safety-net() {
  fresh_home cc-safety-net
  local d="$TOOLS/cc-safety-net"
  npm install --silent --no-fund --no-audit --prefix "$d" "cc-safety-net@$CCSN_VERSION"
  # `install --claude-code` adds a Claude Code marketplace plugin through the claude CLI. Its hooks/hooks.json runs
  # this command on every PreToolUse; we write the same entry, with CLAUDE_PLUGIN_ROOT pointing at the npm package
  # of the same version (run.py sets it), instead of running the claude CLI here.
  mkdir -p "$d/home/.claude"
  cat > "$d/home/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "PreToolUse": [
      {"hooks": [{"type": "command", "command": "node \"${CLAUDE_PLUGIN_ROOT}/dist/bin/cc-safety-net.js\" hook --coding-cli"}]}
    ]
  }
}
JSON
}

setup_shellfirm() {
  fresh_home shellfirm
  local d="$TOOLS/shellfirm"
  npm install --silent --no-fund --no-audit --prefix "$d" "@shellfirm/cli@$SHELLFIRM_VERSION"
  # its installer for Claude Code; hooks only (the MCP half registers a server with the claude CLI)
  in_home shellfirm env PATH="$d/node_modules/.bin:$PATH" shellfirm connect claude-code --hooks-only >/dev/null
}

guards=("$@")
[ ${#guards[@]} -eq 0 ] && guards=(laya-guard dcg dcg-packs cc-safety-net shellfirm)
for g in "${guards[@]}"; do
  echo "== $g"
  mkdir -p "$TOOLS/$g"
  "setup_$g"
  test -f "$TOOLS/$g/home/.claude/settings.json" && echo "   hook written to $TOOLS/$g/home/.claude/settings.json"
done
