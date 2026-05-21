#!/usr/bin/env bash
# Build the Windows launcher (amd64).
#
# Outputs dist/agentwiki-launcher-windows-amd64.exe — single
# self-contained .exe linked with -H windowsgui so it has no console
# window. The user downloads it directly, double-clicks → SmartScreen
# "More info" → "Run anyway" (one-time, unsigned), the .exe pops a
# MessageBox confirming install and registers the agentwiki:// URL
# handler under HKCU\Software\Classes. Every subsequent URL dispatch
# runs silently in the background.
#
# Authenticode signing is a follow-up.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
DIST="$ROOT/dist"
mkdir -p "$DIST"
cd "$ROOT"

arch=amd64
out="$DIST/agentwiki-launcher-windows-$arch.exe"
echo "==> Building windows/$arch (windowsgui, no console)"
GOOS=windows GOARCH="$arch" CGO_ENABLED=0 \
  go build -trimpath -ldflags="-s -w -H windowsgui" -o "$out" ./cmd/agentwiki-launcher

echo "==> Built $out"
ls -lh "$out"
