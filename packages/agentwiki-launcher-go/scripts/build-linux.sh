#!/usr/bin/env bash
# Build Linux launcher tarballs (amd64 + arm64).
#
# Outputs dist/agentwiki-launcher-linux-<arch>.tar.gz — each contains the
# binary + install.sh that copies it into ~/.local/bin and registers the
# agentwiki:// URL scheme via xdg-mime.
#
# No code signing on Linux — distros + xdg-open don't expect it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
DIST="$ROOT/dist"
mkdir -p "$DIST"
cd "$ROOT"

for arch in amd64 arm64; do
  bin="$DIST/agentwiki-launcher-linux-$arch"
  echo "==> Building linux/$arch"
  GOOS=linux GOARCH="$arch" CGO_ENABLED=0 \
    go build -trimpath -ldflags="-s -w" -o "$bin" ./cmd/agentwiki-launcher

  pkgdir="$DIST/pkg-linux-$arch"
  rm -rf "$pkgdir"
  mkdir -p "$pkgdir"
  cp "$bin" "$pkgdir/agentwiki-launcher"
  chmod +x "$pkgdir/agentwiki-launcher"

  cat > "$pkgdir/install.sh" <<'EOF'
#!/usr/bin/env bash
# AgentWikiLauncher installer for Linux. Copies the binary into
# ~/.local/bin and registers the agentwiki:// URL handler via xdg-mime.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
install -m 0755 "$HERE/agentwiki-launcher" "$BIN_DIR/agentwiki-launcher"
"$BIN_DIR/agentwiki-launcher" install
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Note: add $BIN_DIR to your PATH if it's not already there." ;;
esac
echo "Done. Click Run Agent in the wiki."
EOF
  chmod +x "$pkgdir/install.sh"

  tarball="$DIST/agentwiki-launcher-linux-$arch.tar.gz"
  rm -f "$tarball"
  tar -C "$pkgdir" -czf "$tarball" agentwiki-launcher install.sh
  rm -rf "$pkgdir"
  echo "==> Built $tarball"
done

ls -lh "$DIST"/agentwiki-launcher-linux-*.tar.gz
