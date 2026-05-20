#!/usr/bin/env bash
# Sign + notarize the darwin binaries built by `make dist`.
#
# Required env (caller's responsibility — locally via
# scripts/load-secrets-aws.sh, in CI via GitHub Actions secrets):
#   APPLE_ID              Apple developer account email
#   APPLE_TEAM_ID         Apple team ID
#   APPLE_APP_PASSWORD    app-specific password for notarytool
#   APPLE_CERT_BASE64     base64-encoded Developer ID Application .p12
#   APPLE_CERT_PASSWORD   password for the .p12
#
# Standalone Mach-O binaries cannot be stapled; Gatekeeper resolves the
# notarization ticket online on first launch. The artifacts left in dist/
# are the already-signed binaries — distribute those directly.
set -euo pipefail

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

if [[ ! -d "$DIST" ]]; then
  echo "error: $DIST not found — run \`make dist\` first" >&2
  exit 1
fi

TMP_ROOT="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
KEYCHAIN_PATH="$TMP_ROOT/agentwiki-release-$$.keychain-db"
KEYCHAIN_PASSWORD="$(openssl rand -hex 16)"
CERT_PATH="$TMP_ROOT/agentwiki-release-cert-$$.p12"
STAGE="$(mktemp -d)"
ORIGINAL_KEYCHAINS=""

cleanup() {
  set +e
  if [[ -n "$ORIGINAL_KEYCHAINS" ]]; then
    # shellcheck disable=SC2086 # intentional word splitting to restore list
    security list-keychains -d user -s $ORIGINAL_KEYCHAINS >/dev/null 2>&1
  fi
  if [[ -f "$KEYCHAIN_PATH" ]]; then
    security delete-keychain "$KEYCHAIN_PATH" >/dev/null 2>&1
  fi
  rm -f "$CERT_PATH"
  rm -rf "$STAGE"
}
trap cleanup EXIT

echo "==> Creating temp keychain at $KEYCHAIN_PATH"
security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security set-keychain-settings -lut 21600 "$KEYCHAIN_PATH"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"

ORIGINAL_KEYCHAINS="$(security list-keychains -d user | sed -E 's/^[[:space:]]*"([^"]+)"$/\1/' | tr '\n' ' ')"
# shellcheck disable=SC2086 # intentional word splitting
security list-keychains -d user -s "$KEYCHAIN_PATH" $ORIGINAL_KEYCHAINS

echo "==> Decoding + importing Developer ID certificate"
printf '%s' "$APPLE_CERT_BASE64" | base64 --decode > "$CERT_PATH"
security import "$CERT_PATH" -k "$KEYCHAIN_PATH" -P "$APPLE_CERT_PASSWORD" \
  -T /usr/bin/codesign -T /usr/bin/security
security set-key-partition-list -S apple-tool:,apple:,codesign: \
  -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH" >/dev/null

IDENTITY="$(security find-identity -v -p codesigning "$KEYCHAIN_PATH" \
  | awk '/Developer ID Application/ {print $2; exit}')"
if [[ -z "$IDENTITY" ]]; then
  echo "error: no Developer ID Application identity in imported keychain" >&2
  security find-identity -v -p codesigning "$KEYCHAIN_PATH" >&2
  exit 1
fi
echo "==> Using identity $IDENTITY"

shopt -s nullglob
BINARIES=("$DIST"/agentwiki-launcher-darwin-*)
shopt -u nullglob
if [[ ${#BINARIES[@]} -eq 0 ]]; then
  echo "error: no agentwiki-launcher-darwin-* binaries in $DIST" >&2
  exit 1
fi

for BIN in "${BINARIES[@]}"; do
  BASE="$(basename "$BIN")"
  echo "==> Signing $BASE"
  codesign --force \
    --options runtime \
    --timestamp \
    --keychain "$KEYCHAIN_PATH" \
    --sign "$IDENTITY" \
    "$BIN"
  codesign --verify --strict --verbose=2 "$BIN"

  echo "==> Zipping $BASE for notarization"
  ZIP="$STAGE/$BASE.zip"
  (cd "$DIST" && /usr/bin/ditto -c -k --keepParent "$BASE" "$ZIP")

  echo "==> Submitting $BASE.zip to notarytool"
  xcrun notarytool submit "$ZIP" \
    --apple-id "$APPLE_ID" \
    --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" \
    --wait \
    --timeout 30m
done

echo "==> All binaries signed + notarized"
ls -lh "$DIST"
