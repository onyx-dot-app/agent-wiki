#!/usr/bin/env bash
# Build the Linux launcher AppImage (amd64).
#
# Outputs dist/AgentWikiLauncher-x86_64.AppImage — single executable.
# User downloads it, chmod +x, double-clicks → runtime auto-mounts the
# AppImage via FUSE and runs AppRun, which exec's agentwiki-launcher
# with the URL handler scheme. First run registers the .desktop entry
# under ~/.local/share/applications pointing at $APPIMAGE so future
# agentwiki:// URLs resolve correctly even after the FUSE mount
# disappears.
#
# Requires `appimagetool` on PATH. In CI we wget it from the AppImage
# upstream release on demand.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
DIST="$ROOT/dist"
mkdir -p "$DIST"
cd "$ROOT"

if ! command -v appimagetool >/dev/null 2>&1; then
  echo "error: appimagetool not on PATH. Install from https://github.com/AppImage/AppImageKit/releases/" >&2
  exit 1
fi

arch=amd64
bin="$DIST/agentwiki-launcher-linux-$arch"
echo "==> Building linux/$arch for AppImage"
GOOS=linux GOARCH="$arch" CGO_ENABLED=0 \
  go build -trimpath -ldflags="-s -w" -o "$bin" ./cmd/agentwiki-launcher

appdir="$DIST/AgentWikiLauncher.AppDir"
rm -rf "$appdir"
mkdir -p "$appdir/usr/bin"
cp "$bin" "$appdir/usr/bin/agentwiki-launcher"
chmod +x "$appdir/usr/bin/agentwiki-launcher"

# AppRun is the entrypoint AppImage runtime exec's. Forward all argv so
# the URL handler dispatch ($1 = agentwiki://...) reaches the binary.
cat > "$appdir/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/agentwiki-launcher" "$@"
EOF
chmod +x "$appdir/AppRun"

# Minimal .desktop required by AppImage spec. Note: this is the one
# bundled INSIDE the AppImage so the runtime recognises it; the .desktop
# we install on the user's machine is written separately by
# install_linux.go using $APPIMAGE as the Exec target.
cat > "$appdir/agentwiki-launcher.desktop" <<'EOF'
[Desktop Entry]
Name=AgentWikiLauncher
Comment=Agent Wiki helper — handles agentwiki:// URLs
Exec=AppRun
Icon=agentwiki-launcher
Type=Application
Categories=Utility;
Terminal=false
NoDisplay=true
MimeType=x-scheme-handler/agentwiki;
EOF

# AppImage spec requires an icon next to the .desktop. We ship a minimal
# 1x1 transparent PNG placeholder; replace with a real icon when one
# exists.
cp "$HERE/agentwiki-launcher.png" "$appdir/agentwiki-launcher.png"

out="$DIST/AgentWikiLauncher-x86_64.AppImage"
rm -f "$out"
ARCH=x86_64 appimagetool "$appdir" "$out"
rm -rf "$appdir"
chmod +x "$out"
echo "==> Built $out"
ls -lh "$out"
