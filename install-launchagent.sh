#!/bin/sh
# Installs a per-user LaunchAgent that starts foundry_shim.py at login and
# restarts it if it exits. Re-run after moving the repo. Undo with uninstall-launchagent.sh.
set -eu

LABEL=net.huuhka.openwhispr-foundry-shim
REPO="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/openwhispr-foundry-shim.log"
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

DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed $PLIST"
echo "Logs: $LOG"
