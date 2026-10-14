#!/usr/bin/env bash
# Rebuild src/laya_guard/static/vendor/three.min.js: a tree-shaken, minified build of ONE pinned three.js release that
# contains only the classes the simulation view uses (everything it names as `THREE.<Name>` in static/sim/*.js).
#
# The built file is committed, so users and CI never run this: it needs node, npm and network access, and is only for
# bumping three.js or after the view starts using another class.
#
#   scripts/vendor_three.sh            # the pinned version below
#   scripts/vendor_three.sh 0.187.0    # another release
set -euo pipefail

VERSION="${1:-0.186.1}"
ESBUILD="0.25.0"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIM="$ROOT/src/laya_guard/static/sim"
OUT="$ROOT/src/laya_guard/static/vendor"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

names="$(grep -ho 'THREE\.[A-Za-z0-9_]*' "$SIM"/*.js | sed 's/THREE\.//' | sort -u | paste -sd, -)"
[ -n "$names" ] || { echo "no THREE.* names found in $SIM" >&2; exit 1; }

cd "$WORK"
npm init -y >/dev/null
npm pack "three@$VERSION" --silent >/dev/null
npm install --silent --no-audit --no-fund "./three-$VERSION.tgz" "esbuild@$ESBUILD"
printf 'export { %s } from "three";\n' "${names//,/, }" > entry.js
npx esbuild entry.js --bundle --minify --format=esm --target=es2020 --legal-comments=none --outfile=three.min.js >/dev/null

mkdir -p "$OUT"
{
  REV="${VERSION#0.}"; REV="${REV%%.*}"
  printf '/* three.js r%s (npm three@%s), MIT License, (c) 2010-2026 three.js authors. A tree-shaken build with only the\n' "$REV" "$VERSION"
  printf '   classes laya-guard'"'"'s simulation view uses; see THREE_LICENSE.txt and scripts/vendor_three.sh. */\n'
  cat three.min.js
} > "$OUT/three.min.js"
cp node_modules/three/LICENSE "$OUT/THREE_LICENSE.txt"
echo "built $OUT/three.min.js ($(wc -c < "$OUT/three.min.js") bytes) with: $names"
