"""Serves the launcher distributions per platform.

Routes:
  GET /installer/mac           → AgentWikiLauncher.zip (signed/notarized/stapled .app)
  GET /installer/linux?arch=…  → agentwiki-launcher-linux-<arch>.tar.gz
  GET /installer/windows       → agentwiki-launcher-windows-amd64.zip
  GET /installer/app           → mac alias (kept for older FE builds)
  GET /installer/binary?arch=… → raw mac Mach-O (legacy, used by /installer/script)
  GET /installer/script        → one-shot .command (legacy mac fallback)

Frontend detects the user's OS from the User-Agent and links to the
right route; the FE flow is: download → drag .app into /Applications
(mac) / extract + run install.sh (linux) / extract + run install.bat
(windows). On first Run Agent the helper prompts to pin this wiki's URL.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

router = APIRouter()

_BINARIES_DIR = Path(__file__).resolve().parents[2] / "static" / "installers"

# Legacy: raw mac binaries served by /installer/binary for the one-shot
# .command installer script. New code goes through /installer/{platform}.
_MAC_BINARIES: dict[str, str] = {
    "darwin-arm64": "agentwiki-launcher-darwin-arm64",
    "darwin-amd64": "agentwiki-launcher-darwin-amd64",
}

# (filename on disk, media type) per platform-shaped bundle.
# Windows is now a single .exe (GUI subsystem, no install.bat wrapper).
# Linux ships AppImage as the default (single executable, double-click)
# + tarballs as fallback for distros that prefer extract+install.sh.
_MAC_BUNDLE = ("AgentWikiLauncher.zip", "application/zip")
_WINDOWS_BUNDLE = ("agentwiki-launcher-windows-amd64.exe", "application/octet-stream")
_LINUX_APPIMAGE = ("AgentWikiLauncher-x86_64.AppImage", "application/octet-stream")
_LINUX_BUNDLES: dict[str, tuple[str, str]] = {
    "amd64": ("agentwiki-launcher-linux-amd64.tar.gz", "application/gzip"),
    "arm64": ("agentwiki-launcher-linux-arm64.tar.gz", "application/gzip"),
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
        return "darwin-arm64"  # graceful fallback; non-mac not supported here
    return "darwin-arm64"


def _wiki_base(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _stream(fname: str, media_type: str) -> Response:
    path = _BINARIES_DIR / fname
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="%s missing on this server; expected at %s" % (fname, path),
        )
    return FileResponse(path, media_type=media_type, filename=fname)


@router.get("/installer/mac")
def installer_mac() -> Response:
    """Stream the signed + notarized + stapled AgentWikiLauncher.app zip."""
    return _stream(*_MAC_BUNDLE)


@router.get("/installer/app")
def installer_app() -> Response:
    """Back-compat alias for older frontend builds — same as /installer/mac."""
    return _stream(*_MAC_BUNDLE)


@router.get("/installer/linux")
def installer_linux(format: str = "appimage", arch: str = "amd64") -> Response:
    """Stream the linux launcher.

    Default format is ``appimage`` — single self-contained executable,
    double-click after ``chmod +x``. ``format=tar.gz`` falls back to the
    tarball (binary + install.sh) for distros that prefer the manual
    install path. ``arch`` only applies to tarball (AppImage ships
    amd64 only for v1).
    """
    if format == "appimage":
        if arch != "amd64":
            raise HTTPException(
                status_code=404,
                detail="appimage is amd64-only; use format=tar.gz for arm64",
            )
        return _stream(*_LINUX_APPIMAGE)
    if format == "tar.gz":
        bundle = _LINUX_BUNDLES.get(arch)
        if bundle is None:
            raise HTTPException(
                status_code=404,
                detail="unsupported linux arch %r; expected one of %s"
                % (arch, sorted(_LINUX_BUNDLES)),
            )
        return _stream(*bundle)
    raise HTTPException(
        status_code=404,
        detail="unsupported linux format %r; expected 'appimage' or 'tar.gz'" % (format,),
    )


@router.get("/installer/windows")
def installer_windows() -> Response:
    """Stream the windows launcher .exe (amd64 only for now).

    Single self-contained .exe built with -H windowsgui. Unsigned — users
    will see SmartScreen's "Windows protected your PC" prompt on first
    run and need to click "More info" → "Run anyway". On run, the .exe
    auto-installs the URL handler under HKCU\\Software\\Classes\\agentwiki
    and pops a MessageBox confirming success. Authenticode signing is a
    follow-up.
    """
    return _stream(*_WINDOWS_BUNDLE)


@router.get("/installer/binary")
def installer_binary(request: Request, arch: str | None = None) -> Response:
    """Returns the macOS helper binary for the requested arch.

    Legacy — used by the one-shot .command installer at /installer/script.
    """
    if arch is None:
        arch = _detect_arch(request.headers.get("user-agent", ""))
    fname = _MAC_BINARIES.get(arch)
    if fname is None:
        raise HTTPException(
            status_code=404,
            detail="unsupported arch %r" % (arch,),
        )
    return _stream(fname, "application/octet-stream")


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
    script = """#!/bin/bash
# agentwiki-launcher one-shot installer (macOS).
# Downloads the helper binary from %s, pins the wiki endpoint, and
# registers the agentwiki:// URL handler with LaunchServices.

set -euo pipefail

WIKI_URL="%s"
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
""" % (base, base)
    return Response(
        content=script,
        media_type="text/x-shellscript",
        headers={
            "Content-Disposition": 'attachment; filename="agentwiki-installer.command"',
        },
    )
