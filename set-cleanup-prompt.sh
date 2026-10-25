#!/bin/zsh
# Sets OpenWhispr Patched's dictation cleanup prompt (Prompt Studio's custom
# prompt) from a file, by default docs/cleanup-prompt.txt. Same result as
# pasting the file into Prompt Studio and saving, for a machine you're not at:
#   ssh pasi@m1 'cd ~/Code/Omat/openwhispr-foundry && git pull && ./set-cleanup-prompt.sh'
#
# The prompt lives in the app's localStorage, which it reads at startup and
# keeps open while it runs. So this quits the app, starts it once with a
# DevTools port on 127.0.0.1, sets the key through a window, reads it back,
# and restarts the app normally.
set -eu
# T3 Code sets this for its own shells. With it, the app binary runs as plain
# Node and exits 0 at once, and `open` launched from that shell fails too.
unset ELECTRON_RUN_AS_NODE
export PATH="/opt/homebrew/bin:$PATH"  # node, in SSH sessions too

PROMPT_FILE="${1:-${0:A:h}/docs/cleanup-prompt.txt}"
APP="/Applications/OpenWhispr Patched.app"
PORT=9339

[ -f "$PROMPT_FILE" ] || { echo "$PROMPT_FILE not found" >&2; exit 1; }
[ -d "$APP" ] || { echo "$APP not found" >&2; exit 1; }
command -v node >/dev/null || { echo "node not found on PATH" >&2; exit 1; }

# SIGTERM only hides the app to the tray, so quit it through Apple Events.
quit_app() {
  osascript -e 'quit app "OpenWhispr Patched"' 2>/dev/null || true
  for i in {1..40}; do pgrep -f "$APP/Contents/MacOS/" >/dev/null || return 0; sleep 0.25; done
  echo "OpenWhispr Patched didn't quit" >&2
  return 1
}

quit_app
"$APP/Contents/MacOS/OpenWhispr Patched" \
  --remote-debugging-port=$PORT --remote-debugging-address=127.0.0.1 >/dev/null 2>&1 &

node --input-type=module - "$PORT" "$PROMPT_FILE" <<'EOF'
import { readFileSync } from "node:fs";
const [port, file] = process.argv.slice(2);
const prompt = readFileSync(file, "utf8").replace(/\n+$/, "");
let target;
for (let i = 0; i < 60 && !target; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    target = list.find((t) => t.type === "page" && t.url.startsWith("file://"));
  } catch {}
  if (!target) await new Promise((r) => setTimeout(r, 500));
}
if (!target) throw new Error("no OpenWhispr window on the DevTools port");
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
const expression = `(() => { const p = ${JSON.stringify(prompt)};
  localStorage.setItem("customPrompt.cleanup", p);
  return localStorage.getItem("customPrompt.cleanup") === p; })()`;
ws.send(JSON.stringify({ id: 1, method: "Runtime.evaluate", params: { expression, returnByValue: true } }));
const reply = await new Promise((r) => { ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id === 1) r(d); }; });
ws.close();
if (reply.result?.result?.value !== true) throw new Error(`setting the prompt failed: ${JSON.stringify(reply)}`);
console.log(`Cleanup prompt set from ${file} (${prompt.length} chars)`);
EOF

sleep 1  # let localStorage reach the disk before quitting
quit_app
/usr/bin/open -g "$APP"
echo "Restarted OpenWhispr Patched"
