#!/usr/bin/env bash
# Build the Linux launcher Debian package (amd64).
#
# Outputs dist/agentwiki-launcher_<version>_amd64.deb — installs the
# binary to /usr/bin and the .desktop file to /usr/share/applications.
# Double-click installs via Software Center / `dpkg -i`; the postinst
# refreshes the desktop database so xdg-open immediately recognises the
# agentwiki:// scheme. No chmod, no terminal, no manual install step.
#
# Covers Ubuntu, Debian, Mint, Pop!_OS, ElementaryOS, and derivatives.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
DIST="$ROOT/dist"
mkdir -p "$DIST"
cd "$ROOT"

if ! command -v dpkg-deb >/dev/null 2>&1; then
  echo "error: dpkg-deb not on PATH (apt-get install dpkg)" >&2
  exit 1
fi

VERSION="${VERSION:-0.1.0}"
arch=amd64
bin="$DIST/agentwiki-launcher-linux-$arch"

if [[ ! -x "$bin" ]]; then
  echo "==> Building linux/$arch"
  GOOS=linux GOARCH="$arch" CGO_ENABLED=0 \
    go build -trimpath -ldflags="-s -w" -o "$bin" ./cmd/agentwiki-launcher
fi

stage="$DIST/deb-stage-$arch"
rm -rf "$stage"
mkdir -p \
  "$stage/DEBIAN" \
  "$stage/usr/bin" \
  "$stage/usr/share/applications"

cp "$bin" "$stage/usr/bin/agentwiki-launcher"
chmod 755 "$stage/usr/bin/agentwiki-launcher"

# System-wide .desktop. With only one handler registered for the
# agentwiki:// scheme, xdg-open picks it without an explicit
# `xdg-mime default` per-user step. Exec is absolute (no PATH lookup
# needed) and points at the installed binary.
cat > "$stage/usr/share/applications/agentwiki-launcher.desktop" <<'EOF'
[Desktop Entry]
Name=AgentWikiLauncher
Comment=Agent Wiki helper — handles agentwiki:// URLs
Exec=/usr/bin/agentwiki-launcher dispatch %u
Terminal=false
Type=Application
NoDisplay=true
MimeType=x-scheme-handler/agentwiki;
EOF
chmod 644 "$stage/usr/share/applications/agentwiki-launcher.desktop"

# DEBIAN/control — minimal metadata + dependencies. xdg-utils ships
# xdg-mime + xdg-open which the helper needs at dispatch time.
cat > "$stage/DEBIAN/control" <<EOF
Package: agentwiki-launcher
Version: $VERSION
Section: utils
Priority: optional
Architecture: $arch
Depends: xdg-utils
Maintainer: agent-wiki <noreply@onyx.app>
Description: Agent Wiki launcher (URL handler for agentwiki:// scheme)
 Helper that exchanges launch codes with the agent-wiki backend and
 spawns claude-code / codex in a terminal. Registers itself as the
 system handler for the agentwiki:// URL scheme.
EOF
chmod 644 "$stage/DEBIAN/control"

cat > "$stage/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
update-desktop-database /usr/share/applications/ >/dev/null 2>&1 || true
exit 0
EOF
chmod 755 "$stage/DEBIAN/postinst"

cat > "$stage/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
update-desktop-database /usr/share/applications/ >/dev/null 2>&1 || true
exit 0
EOF
chmod 755 "$stage/DEBIAN/postrm"

out="$DIST/agentwiki-launcher_${VERSION}_${arch}.deb"
rm -f "$out"
dpkg-deb --build --root-owner-group "$stage" "$out"
rm -rf "$stage"
echo "==> Built $out"
ls -lh "$out"
