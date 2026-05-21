#!/usr/bin/env bash
# Build the Windows launcher zip (amd64).
#
# Outputs dist/agentwiki-launcher-windows-amd64.zip — contains the .exe +
# install.bat that copies it into %LOCALAPPDATA%\AgentWikiLauncher and
# registers the agentwiki:// URL scheme under HKCU\Software\Classes.
#
# No code signing yet — users will see SmartScreen's "Windows protected
# your PC" prompt and need to click More info → Run anyway. Authenticode
# signing is a follow-up (sectigo / DigiCert / Azure Trusted Signing).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
DIST="$ROOT/dist"
mkdir -p "$DIST"
cd "$ROOT"

arch=amd64
bin="$DIST/agentwiki-launcher-windows-$arch.exe"
echo "==> Building windows/$arch"
GOOS=windows GOARCH="$arch" CGO_ENABLED=0 \
  go build -trimpath -ldflags="-s -w" -o "$bin" ./cmd/agentwiki-launcher

pkgdir="$DIST/pkg-windows-$arch"
rm -rf "$pkgdir"
mkdir -p "$pkgdir"
cp "$bin" "$pkgdir/agentwiki-launcher.exe"

cat > "$pkgdir/install.bat" <<'EOF'
@echo off
REM AgentWikiLauncher installer for Windows. Copies the .exe to
REM %LOCALAPPDATA%\AgentWikiLauncher and registers the agentwiki://
REM URL handler in HKCU\Software\Classes.
setlocal
set TARGET=%LOCALAPPDATA%\AgentWikiLauncher
if not exist "%TARGET%" mkdir "%TARGET%"
copy /Y "%~dp0agentwiki-launcher.exe" "%TARGET%\agentwiki-launcher.exe" >NUL
if errorlevel 1 (
  echo Copy failed.
  pause
  exit /b 1
)
"%TARGET%\agentwiki-launcher.exe" install
if errorlevel 1 (
  echo Install failed.
  pause
  exit /b 1
)
echo Done. You can close this window and click Run Agent in the wiki.
pause
EOF

zip_out="$DIST/agentwiki-launcher-windows-$arch.zip"
rm -f "$zip_out"
if command -v zip >/dev/null 2>&1; then
  (cd "$pkgdir" && zip -q -r "$zip_out" agentwiki-launcher.exe install.bat)
else
  python3 - "$zip_out" "$pkgdir" <<'PY'
import sys, zipfile, pathlib
out = sys.argv[1]
src = pathlib.Path(sys.argv[2])
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for p in src.iterdir():
        zf.write(p, p.name)
PY
fi
rm -rf "$pkgdir"
echo "==> Built $zip_out"
ls -lh "$zip_out"
