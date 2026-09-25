#!/bin/sh
# Builds "OpenWhispr Patched": OpenWhispr with the patches below applied,
# under its own name and bundle ID so it sits next to the official app.
#
#   realtime-url.patch  meeting transcription through the shim
#   note-images.patch   screenshots pasted into notes, sent to note actions
#
#   openwhispr-patch/build.sh [openwhispr-clone] [git-ref]
#
# Defaults: ../openwhispr, and its newest vX.Y.Z release tag (fetched first,
# so there's no need to pull the clone). Builds in a separate git worktree
# (../openwhispr-build) so the clone stays clean. Needs Node, npm, ffmpeg and
# the Xcode command line tools. The first build downloads OpenWhispr's bundled
# binaries and models (~0.5 GB); later builds reuse them.
#
# Before packaging, smoke-test.js runs the new version's realtime client
# against the shim, so a protocol change upstream fails the build instead of
# silently breaking meetings.
#
# Signs with the identity from create-signing-cert.sh if it exists, so macOS
# permissions survive rebuilds. Without it the app is unsigned and macOS asks
# for permissions again after every rebuild.
set -eu

# The app's name and bundle ID. Any reverse-DNS ID works. macOS keys
# permissions to the bundle ID, so changing it later means granting them again.
APP_ID="net.huuhka.openwhispr-patched"
PRODUCT="OpenWhispr Patched"
# Must match NAME in create-signing-cert.sh.
SIGN_ID="OpenWhispr Patched Local Signing"

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "${1:-$HERE/../../openwhispr}" && pwd)"
WORK="$(dirname "$SRC")/openwhispr-build"
if [ -z "${SHIM_PORT:-}" ] && [ -f "$HERE/../config.env" ]; then
  SHIM_PORT="$(sed -n 's/^SHIM_PORT=//p' "$HERE/../config.env" | tail -1)"
fi
SHIM_URL="http://localhost:${SHIM_PORT:-9447}"

git -C "$SRC" fetch --tags --quiet
# App releases are plain vX.Y.Z tags; the repo also tags helper binaries.
LATEST="$(git -C "$SRC" tag --list 'v[0-9]*' --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -1)"
REF="${2:-$LATEST}"
git -C "$SRC" rev-parse --verify --quiet "$REF^{commit}" >/dev/null \
  || { echo "unknown ref $REF (pass one as the second argument)" >&2; exit 1; }

if [ -d "$WORK" ]; then
  git -C "$WORK" reset --hard --quiet
  # Files the patches add; ignored build output (node_modules, dist) stays.
  git -C "$WORK" clean -fdq
  git -C "$WORK" checkout --quiet --detach "$REF"
else
  git -C "$SRC" worktree add --detach "$WORK" "$REF"
fi
for PATCH in realtime-url note-images; do
  git -C "$WORK" apply "$HERE/$PATCH.patch" \
    || { echo "$PATCH.patch no longer applies to $REF: update it against that release." >&2; exit 1; }
done
echo "Building $PRODUCT from OpenWhispr $REF in $WORK"

cd "$WORK"
# OpenWhispr pins a Node major in .nvmrc and sets engine-strict, so a newer
# default node fails `npm ci`. Use Homebrew's node@<major> when it differs.
WANT_NODE="$(tr -dc '0-9' < .nvmrc)"
HAVE_NODE="$(node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/')"
if [ -n "$WANT_NODE" ] && [ "$HAVE_NODE" != "$WANT_NODE" ]; then
  KEG="$(brew --prefix 2>/dev/null)/opt/node@$WANT_NODE/bin"
  [ -x "$KEG/node" ] || { echo "OpenWhispr needs Node $WANT_NODE: brew install node@$WANT_NODE" >&2; exit 1; }
  PATH="$KEG:$PATH"
  export PATH
fi
echo "Using node $(node --version), npm $(npm --version)"
npm ci

# Fail before the slow steps if the realtime client no longer works with the shim.
if curl -sf -o /dev/null "$SHIM_URL/v1/models"; then
  SMOKE="$(mktemp -d)"
  say -o "$SMOKE/speech.aiff" "This is a smoke test of the patched OpenWhispr build."
  ffmpeg -loglevel error -y -i "$SMOKE/speech.aiff" -ar 24000 -ac 1 -f s16le "$SMOKE/speech.pcm"
  OPENWHISPR_OPENAI_REALTIME_URL="ws://${SHIM_URL#http://}/v1/realtime?intent=transcription" \
    node "$HERE/smoke-test.js" "$WORK" "$SMOKE/speech.pcm" \
    || { echo "Realtime smoke test failed: check the client changes in $REF before installing." >&2; exit 1; }
  rm -rf "$SMOKE"
else
  echo "WARNING: shim not reachable at $SHIM_URL, skipping the realtime smoke test."
fi

# `npm run pack` without its fixed unsigned flags: same prepack steps (native
# helpers, bundled binaries), then electron-builder with our name and bundle ID.
# identity=null skips electron-builder's signing; sign.js signs below.
npm run prepack
npm run build:renderer
rm -rf dist/mac dist/mac-arm64
npx electron-builder --mac --dir \
  -c.appId="$APP_ID" \
  -c.productName="$PRODUCT" \
  -c.mac.identity=null \
  -c.mac.notarize=false

APP="$(find "$WORK/dist" -maxdepth 2 -name "$PRODUCT.app" -type d | head -1)"
# No update feed: an update would be OpenWhispr's official build.
rm -f "$APP/Contents/Resources/app-update.yml"

if security find-identity -v -p codesigning | grep -q "\"$SIGN_ID\""; then
  node "$HERE/sign.js" "$APP" "$SIGN_ID" "$WORK/resources/mac/entitlements.mac.plist"
else
  echo "WARNING: no \"$SIGN_ID\" identity (run create-signing-cert.sh)."
  echo "         The app is unsigned; macOS will ask for permissions again after each rebuild."
fi

echo
echo "Built: $APP"
echo "Quit OpenWhispr (only one of the two can run at a time), then:"
echo "  rm -rf \"/Applications/$PRODUCT.app\" && ditto \"$APP\" \"/Applications/$PRODUCT.app\""
