#!/bin/sh
# Installs two per-user LaunchAgents:
#   - the shim itself, started at login and restarted if it exits
#   - a one-shot `launchctl setenv` that points a patched OpenWhispr's OpenAI
#     Realtime client at the shim (see openwhispr-patch/). Apps opened from the
#     Dock or Finder inherit launchd's environment, not your shell's.
# Re-run after moving the repo. Undo with uninstall-launchagent.sh.
set -eu

LABEL=net.huuhka.openwhispr-foundry-shim
REPO="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/openwhispr-foundry-shim.log"
ENV_LABEL=net.huuhka.openwhispr-realtime-env
ENV_PLIST="$HOME/Library/LaunchAgents/$ENV_LABEL.plist"
REALTIME_URL="ws://localhost:${SHIM_PORT:-9447}/v1/realtime?intent=transcription"
PYTHON="$(command -v python3)"

# launchd starts agents with a bare PATH; the shim needs az and ffmpeg.
for bin in az ffmpeg; do
  command -v "$bin" >/dev/null || { echo "$bin not found on PATH" >&2; exit 1; }
done
AGENT_PATH="$(dirname "$(command -v az)"):$(dirname "$(command -v ffmpeg)"):/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$REPO/foundry_shim.py</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$AGENT_PATH</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

cat > "$ENV_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$ENV_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/launchctl</string>
    <string>setenv</string>
    <string>OPENWHISPR_OPENAI_REALTIME_URL</string>
    <string>$REALTIME_URL</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
EOF

DOMAIN="gui/$(id -u)"
for label in "$LABEL" "$ENV_LABEL"; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
done
launchctl bootstrap "$DOMAIN" "$PLIST"
launchctl bootstrap "$DOMAIN" "$ENV_PLIST"
echo "Installed $PLIST"
echo "Installed $ENV_PLIST (OPENWHISPR_OPENAI_REALTIME_URL=$REALTIME_URL)"
echo "Logs: $LOG"
