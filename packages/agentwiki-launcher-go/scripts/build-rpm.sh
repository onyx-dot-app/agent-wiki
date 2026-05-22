#!/usr/bin/env bash
# Build the Linux launcher RPM (amd64).
#
# Outputs dist/agentwiki-launcher-<version>-1.x86_64.rpm — installs the
# binary to /usr/bin and the .desktop to /usr/share/applications.
# Double-click installs via GNOME Software / KDE Discover / `rpm -i`;
# the %post scriptlet refreshes the desktop database so xdg-open
# immediately recognises the agentwiki:// scheme. No chmod, no terminal.
#
# Covers Fedora, RHEL/CentOS/Rocky/Alma, openSUSE, and derivatives.
#
# Build requires Linux + `rpm-build` (apt-get install rpm OR dnf install
# rpm-build). macOS brew rpm refuses x86_64 cross-builds, so this only
# runs cleanly in CI (ubuntu-latest).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
DIST="$ROOT/dist"
mkdir -p "$DIST"
cd "$ROOT"

if ! command -v rpmbuild >/dev/null 2>&1; then
  echo "error: rpmbuild not on PATH (apt-get install rpm OR dnf install rpm-build)" >&2
  exit 1
fi

VERSION="${VERSION:-0.1.0}"
arch=amd64
rpmarch=x86_64
bin="$DIST/agentwiki-launcher-linux-$arch"

if [[ ! -x "$bin" ]]; then
  echo "==> Building linux/$arch"
  GOOS=linux GOARCH="$arch" CGO_ENABLED=0 \
    go build -trimpath -ldflags="-s -w" -o "$bin" ./cmd/agentwiki-launcher
fi

# Build RPM in a sandbox tree so we don't touch ~/rpmbuild.
topdir="$DIST/rpm-stage"
rm -rf "$topdir"
mkdir -p "$topdir"/{BUILD,RPMS,SOURCES,SPECS,SRPMS,BUILDROOT}

# Stage the install tree under BUILD so the spec %files section just
# references absolute paths.
build="$topdir/BUILD/agentwiki-launcher-$VERSION"
mkdir -p "$build/usr/bin" "$build/usr/share/applications"
cp "$bin" "$build/usr/bin/agentwiki-launcher"
chmod 755 "$build/usr/bin/agentwiki-launcher"

cat > "$build/usr/share/applications/agentwiki-launcher.desktop" <<'EOF'
[Desktop Entry]
Name=AgentWikiLauncher
Comment=Agent Wiki helper — handles agentwiki:// URLs
Exec=/usr/bin/agentwiki-launcher dispatch %u
Terminal=false
Type=Application
NoDisplay=true
MimeType=x-scheme-handler/agentwiki;
EOF
chmod 644 "$build/usr/share/applications/agentwiki-launcher.desktop"

cat > "$topdir/SPECS/agentwiki-launcher.spec" <<EOF
Name:           agentwiki-launcher
Version:        $VERSION
Release:        1%{?dist}
Summary:        Agent Wiki launcher (URL handler for agentwiki:// scheme)
License:        Proprietary
URL:            https://github.com/onyx-dot-app/agent-wiki
BuildArch:      $rpmarch
Requires:       xdg-utils
# Skip debuginfo generation — pure Go binary, no debug symbols requested.
%global debug_package %{nil}

%description
Helper that exchanges launch codes with the agent-wiki backend and
spawns claude-code / codex in a terminal. Registers itself as the
system handler for the agentwiki:// URL scheme.

%install
mkdir -p %{buildroot}/usr/bin %{buildroot}/usr/share/applications
install -m 0755 %{_builddir}/agentwiki-launcher-$VERSION/usr/bin/agentwiki-launcher \\
  %{buildroot}/usr/bin/agentwiki-launcher
install -m 0644 %{_builddir}/agentwiki-launcher-$VERSION/usr/share/applications/agentwiki-launcher.desktop \\
  %{buildroot}/usr/share/applications/agentwiki-launcher.desktop

%files
/usr/bin/agentwiki-launcher
/usr/share/applications/agentwiki-launcher.desktop

%post
update-desktop-database /usr/share/applications/ >/dev/null 2>&1 || true
exit 0

%postun
update-desktop-database /usr/share/applications/ >/dev/null 2>&1 || true
exit 0
EOF

rpmbuild --define "_topdir $topdir" \
         --define "_target_cpu $rpmarch" \
         --define "_arch $rpmarch" \
         --define "_binary_payload w9.gzdio" \
         -bb "$topdir/SPECS/agentwiki-launcher.spec" >/dev/null

built="$topdir/RPMS/$rpmarch/agentwiki-launcher-$VERSION-1.$rpmarch.rpm"
if [[ ! -f "$built" ]]; then
  # Some rpmbuild versions include the dist tag (.el9, .fc40) in the filename.
  built="$(ls "$topdir/RPMS/$rpmarch/"agentwiki-launcher-"$VERSION"-1*.rpm | head -1)"
fi
out="$DIST/agentwiki-launcher-$VERSION-1.$rpmarch.rpm"
cp "$built" "$out"
rm -rf "$topdir"
echo "==> Built $out"
ls -lh "$out"
