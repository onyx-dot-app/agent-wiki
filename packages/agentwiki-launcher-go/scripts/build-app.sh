#!/usr/bin/env bash
# Build, sign, notarize, staple AgentWikiLauncher.app for distribution.
#
# Outputs dist/AgentWikiLauncher.zip — stapled + notarized, drag-to-Applications
# ready. Gatekeeper passes on first open without "Open anyway" friction.
#
# Required env:
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD,
#   APPLE_CERT_BASE64, APPLE_CERT_PASSWORD
#
# Locally: `source scripts/load-secrets-aws.sh && ./scripts/build-app.sh`.
set -euo pipefail
umask 077

REQUIRED=(APPLE_ID APPLE_TEAM_ID APPLE_APP_PASSWORD APPLE_CERT_BASE64 APPLE_CERT_PASSWORD)
for var in "${REQUIRED[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "error: $var not set" >&2
    exit 1
  fi
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
DIST="$ROOT/dist"
APP="$DIST/AgentWikiLauncher.app"
APP_ZIP="$DIST/AgentWikiLauncher.zip"

TMP_ROOT="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
KEYCHAIN_PATH=""
CERT_PATH=""
ORIGINAL_KEYCHAINS=""

cleanup() {
  set +e
  if [[ -n "$ORIGINAL_KEYCHAINS" ]]; then
    # shellcheck disable=SC2086 # intentional word-splitting to restore list
    security list-keychains -d user -s $ORIGINAL_KEYCHAINS >/dev/null 2>&1
  fi
  if [[ -n "$KEYCHAIN_PATH" && -f "$KEYCHAIN_PATH" ]]; then
    security delete-keychain "$KEYCHAIN_PATH" >/dev/null 2>&1
  fi
  [[ -n "$CERT_PATH" ]] && rm -f "$CERT_PATH"
}
trap cleanup EXIT

echo "==> Building darwin universal binary"
(cd "$ROOT" && make dist)
lipo -create \
  "$DIST/agentwiki-launcher-darwin-arm64" \
  "$DIST/agentwiki-launcher-darwin-amd64" \
  -output "$DIST/agentwiki-launcher-universal"

echo "==> Assembling AgentWikiLauncher.app"
rm -rf "$APP"
osacompile -o "$APP" "$HERE/stub.applescript"

cp "$DIST/agentwiki-launcher-universal" "$APP/Contents/Resources/agentwiki-launcher"
chmod +x "$APP/Contents/Resources/agentwiki-launcher"

PLIST="$APP/Contents/Info.plist"
pb() { /usr/libexec/PlistBuddy -c "$1" "$PLIST"; }
pb_replace() { pb "Delete :$1" 2>/dev/null || true; pb "Add :$1 $2"; }

pb_replace "CFBundleIdentifier" "string com.onyx.agentwiki.launcher"
pb_replace "CFBundleName" "string AgentWikiLauncher"
pb_replace "CFBundleDisplayName" "string AgentWikiLauncher"
pb_replace "LSUIElement" "bool true"
pb "Delete :CFBundleURLTypes" 2>/dev/null || true
pb "Add :CFBundleURLTypes array"
pb "Add :CFBundleURLTypes:0 dict"
pb "Add :CFBundleURLTypes:0:CFBundleURLName string com.onyx.agentwiki.launcher.url"
pb "Add :CFBundleURLTypes:0:CFBundleURLSchemes array"
pb "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string agentwiki"

KEYCHAIN_PATH="$TMP_ROOT/agentwiki-app-$$.keychain-db"
CERT_PATH="$TMP_ROOT/agentwiki-app-cert-$$.p12"
KEYCHAIN_PASSWORD="$(openssl rand -hex 16)"

echo "==> Creating temp keychain"
security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security set-keychain-settings -lut 21600 "$KEYCHAIN_PATH"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
ORIGINAL_KEYCHAINS="$(security list-keychains -d user | sed -E 's/^[[:space:]]*"([^"]+)"$/\1/' | tr '\n' ' ')"
# shellcheck disable=SC2086
security list-keychains -d user -s "$KEYCHAIN_PATH" $ORIGINAL_KEYCHAINS

printf '%s' "$APPLE_CERT_BASE64" | base64 -D > "$CERT_PATH"
security import "$CERT_PATH" -k "$KEYCHAIN_PATH" -P "$APPLE_CERT_PASSWORD" \
  -T /usr/bin/codesign -T /usr/bin/security
security set-key-partition-list -S apple-tool:,apple:,codesign: \
  -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH" >/dev/null

IDENTITY="$(security find-identity -v -p codesigning "$KEYCHAIN_PATH" \
  | awk '/Developer ID Application/ {print $2; exit}')"
if [[ -z "$IDENTITY" ]]; then
  echo "error: no Developer ID Application identity in keychain" >&2
  exit 1
fi
echo "==> Using identity $IDENTITY"

echo "==> Signing inner Go binary"
codesign --force --options runtime --timestamp \
  --keychain "$KEYCHAIN_PATH" --sign "$IDENTITY" \
  "$APP/Contents/Resources/agentwiki-launcher"

echo "==> Signing AppleScript applet"
codesign --force --options runtime --timestamp \
  --keychain "$KEYCHAIN_PATH" --sign "$IDENTITY" \
  "$APP/Contents/MacOS/applet"

echo "==> Signing AgentWikiLauncher.app bundle"
codesign --force --options runtime --timestamp \
  --keychain "$KEYCHAIN_PATH" --sign "$IDENTITY" \
  "$APP"

codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> Zipping for notarization"
rm -f "$APP_ZIP"
ditto -c -k --keepParent "$APP" "$APP_ZIP"

echo "==> Submitting to notarytool"
xcrun notarytool submit "$APP_ZIP" \
  --apple-id "$APPLE_ID" \
  --team-id "$APPLE_TEAM_ID" \
  --password "$APPLE_APP_PASSWORD" \
  --wait --timeout 30m

echo "==> Stapling .app"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"

echo "==> Re-zipping post-staple for distribution"
rm -f "$APP_ZIP"
ditto -c -k --keepParent "$APP" "$APP_ZIP"

echo "==> Done"
ls -lh "$APP_ZIP" "$APP"
spctl -a -vvv -t install "$APP" || true
