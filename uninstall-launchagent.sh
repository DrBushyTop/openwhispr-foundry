#!/bin/sh
# Removes what install-launchagent.sh installed. Keep the labels in sync with it.
set -eu
DOMAIN="gui/$(id -u)"
for label in net.huuhka.openwhispr-foundry-shim net.huuhka.openwhispr-realtime-env; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
  echo "Removed $label"
done
launchctl unsetenv OPENWHISPR_OPENAI_REALTIME_URL
