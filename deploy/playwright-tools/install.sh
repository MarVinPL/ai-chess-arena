#!/bin/sh
set -eu
src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=${PLAYWRIGHT_TOOLS_HOME:-"$HOME/.local/share/playwright-tools"}
mkdir -p "$root" "$HOME/.local/bin"
cp "$src/package.json" "$src/package-lock.json" "$src/smoke.cjs" "$root/"
(cd "$root" && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --ignore-scripts --no-audit --no-fund)
install -m 0755 "$src/run" "$HOME/.local/bin/playwright-run"
