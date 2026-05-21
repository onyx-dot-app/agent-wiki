"""Serves the macOS launcher distribution: signed/notarized .app zip,
raw signed binaries (legacy), and a one-shot install shell script
(legacy fallback for users who can't drag the .app).

Primary flow:

  1. Click "Download installer" in the wiki UI → /api/installer/app
     streams AgentWikiLauncher.zip.
  2. Unzip, drag AgentWikiLauncher.app to /Applications.
  3. Click Run Agent → first launch prompts to pin this wiki's URL,
     then dispatches the run.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

router = APIRouter()

_BINARIES_DIR = Path(__file__).resolve().parents[2] / "static" / "installers"

# Map (platform_arch) → on-disk filename. macOS is all we ship today.
_BINARIES: dict[str, str] = {
    "darwin-arm64": "agentwiki-launcher-darwin-arm64",
    "darwin-amd64": "agentwiki-launcher-darwin-amd64",
}


def _detect_arch(user_agent: str) -> str:
    """Cheap UA sniff. Apple Silicon Macs report ``Macintosh; Intel Mac OS X``
    in browsers (Apple keeps the legacy "Intel" string for compat), so this
    can't reliably tell arm64 from amd64 in a browser UA alone. Default to
    arm64 (current Apple Silicon majority); the script can also be invoked
    with an explicit ``?arch=…`` override.
    """
    ua = user_agent.lower()
    if "mac" not in ua and "darwin" not in ua:
        return "darwin-arm64"  # graceful fallback; non-mac not supported yet
    return "darwin-arm64"


def _wiki_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/installer/app")
def installer_app() -> Response:
    """Stream the signed + notarized + stapled AgentWiki.app zip.

    Built by ``packages/agentwiki-launcher-go/scripts/build-app.sh``.
    Users drag the .app from the unzipped download to /Applications;
    on first Run Agent the launcher prompts to pin this wiki's URL.
    """
    path = _BINARIES_DIR / "AgentWikiLauncher.zip"
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"AgentWikiLauncher.zip missing on this server; expected at {path}",
        )
    return FileResponse(
        path,
        media_type="application/zip",
        filename="AgentWikiLauncher.zip",
    )


@router.get("/installer/binary")
def installer_binary(request: Request, arch: str | None = None) -> Response:
    """Returns the macOS helper binary for the requested arch."""
    if arch is None:
        arch = _detect_arch(request.headers.get("user-agent", ""))
    fname = _BINARIES.get(arch)
    if fname is None:
        raise HTTPException(status_code=404, detail=f"unsupported arch {arch!r}")
    path = _BINARIES_DIR / fname
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"installer binary missing on this server; expected at {path}",
        )
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=fname,
    )


@router.get("/installer/script")
def installer_script(request: Request) -> Response:
    """Returns the one-shot installer as a ``.command`` file. macOS
    auto-runs ``.command`` files in Terminal on double-click — no
    "right-click → Open With Terminal" needed. The script downloads
    the binary from this same backend, strips Gatekeeper quarantine,
    pins the wiki endpoint, and registers the macOS .app for the
    ``agentwiki://`` URL scheme.
    """
    base = _wiki_base(request)
    # macOS arch detection inside the script — works at install time
    # rather than relying on the browser's User-Agent which lies about
    # arm64 vs amd64.
    script = f"""#!/bin/bash
# agentwiki-launcher one-shot installer (macOS).
# Downloads the helper binary from {base}, pins the wiki endpoint, and
# registers the agentwiki:// URL handler with LaunchServices.

set -euo pipefail

WIKI_URL="{base}"
INSTALL_DIR="$HOME/.agentwiki/bin"
BIN_PATH="$INSTALL_DIR/agentwiki-launcher"

arch=$(uname -m)
case "$arch" in
  arm64)  PLATFORM="darwin-arm64" ;;
  x86_64) PLATFORM="darwin-amd64" ;;
  *)      echo "unsupported arch: $arch" >&2; exit 1 ;;
esac

echo "[1/4] Downloading $PLATFORM binary from $WIKI_URL …"
mkdir -p "$INSTALL_DIR"
curl --fail --location --silent --show-error \\
  "$WIKI_URL/api/installer/binary?arch=$PLATFORM" \\
  -o "$BIN_PATH"
chmod +x "$BIN_PATH"

# Drop Gatekeeper's quarantine flag so the unsigned binary runs
# without the user having to right-click → Open.
xattr -d com.apple.quarantine "$BIN_PATH" 2>/dev/null || true

echo "[2/4] Pinning wiki endpoint to $WIKI_URL …"
"$BIN_PATH" set-endpoint "$WIKI_URL"

echo "[3/4] Registering agentwiki:// URL handler …"
"$BIN_PATH" install

echo ""
echo "Done. You can close this window and click Run Agent in the wiki."
echo ""
read -p "Press Enter to close..." _
"""
    return Response(
        content=script,
        media_type="text/x-shellscript",
        headers={
            "Content-Disposition": 'attachment; filename="agentwiki-installer.command"',
        },
    )
