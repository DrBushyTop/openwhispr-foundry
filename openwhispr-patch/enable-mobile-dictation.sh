#!/bin/sh
# Turns on mobile dictation in OpenWhispr Patched and publishes it to your
# tailnet with Tailscale Serve. Prints the URL and token for the phone app.
#
#   openwhispr-patch/enable-mobile-dictation.sh [https-port | svc:name]
#
# The app listens on 127.0.0.1:$LOCAL_PORT only. Tailscale Serve gives it a
# tailnet-only HTTPS address:
#   - a port on this machine's name, default 8450: https://<mac>.<tailnet>.ts.net:8450
#   - or a Tailscale Service with its own name: svc:whispr gives
#     https://whispr.<tailnet>.ts.net. This needs a tagged machine and a
#     Service set up in the admin console first; see openwhispr-patch/README.md.
# The token stays the same on re-runs; delete ~/.openwhispr/mobile-dictation.json
# for a new one.
# Undo: tailscale serve --https=<port> off (or tailscale serve clear svc:<name>),
# and delete that file.
set -eu

LOCAL_PORT=9448
TARGET="${1:-8450}"
CONFIG="$HOME/.openwhispr/mobile-dictation.json"

command -v tailscale >/dev/null || { echo "tailscale not found on PATH" >&2; exit 1; }

if [ ! -f "$CONFIG" ]; then
  mkdir -p "$(dirname "$CONFIG")"
  umask 077
  printf '{"port": %s, "token": "%s"}\n' "$LOCAL_PORT" "$(openssl rand -hex 32)" > "$CONFIG"
  echo "Created $CONFIG"
fi
LOCAL_PORT="$(sed -n 's/.*"port": *\([0-9]*\).*/\1/p' "$CONFIG")"
TOKEN="$(sed -n 's/.*"token": *"\([0-9a-f]*\)".*/\1/p' "$CONFIG")"

STATUS="$(tailscale status --json)"
case "$TARGET" in
  svc:*)
    tailscale serve --service="$TARGET" --https=443 "http://127.0.0.1:$LOCAL_PORT" >/dev/null
    SUFFIX="$(printf '%s' "$STATUS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["MagicDNSSuffix"])')"
    URL="https://${TARGET#svc:}.$SUFFIX"
    ;;
  *)
    tailscale serve --bg --https="$TARGET" "http://127.0.0.1:$LOCAL_PORT" >/dev/null
    HOST="$(printf '%s' "$STATUS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    URL="https://$HOST:$TARGET"
    ;;
esac

echo
echo "Server URL: $URL"
echo "Token:      $TOKEN"
echo
echo "Restart OpenWhispr Patched if it was running, so it reads $CONFIG."
echo "Check it from any tailnet device:"
echo "  curl -H \"Authorization: Bearer $TOKEN\" $URL/v1/health"
case "$TARGET" in
  svc:*)
    echo
    echo "If that can't connect, approve this machine as a host of $TARGET in the"
    echo "admin console, then run: tailscale serve drain $TARGET; tailscale serve advertise $TARGET"
    ;;
esac
