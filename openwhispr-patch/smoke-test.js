// Smoke test: does this OpenWhispr version's OpenAI Realtime client, with the
// URL patch, still work against the shim? Streams speech, stops, and expects
// a transcript back. build.sh runs it before packaging.
//
//   OPENWHISPR_OPENAI_REALTIME_URL=ws://localhost:9447/v1/realtime \
//     node smoke-test.js <openwhispr-dir> <pcm16-mono-24k-file>
const fs = require("fs");
const os = require("os");
const path = require("path");
const Module = require("module");

const [dir, pcmFile] = process.argv.slice(2);
if (!dir || !pcmFile || !process.env.OPENWHISPR_OPENAI_REALTIME_URL) {
  console.error("usage: OPENWHISPR_OPENAI_REALTIME_URL=... node smoke-test.js <openwhispr-dir> <pcm-file>");
  process.exit(2);
}

// The client requires ./debugLogger, which needs Electron. Load a copy next to a stub.
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "openwhispr-smoke-"));
fs.copyFileSync(path.join(dir, "src/helpers/openaiRealtimeStreaming.js"), path.join(tmp, "client.js"));
fs.writeFileSync(
  path.join(tmp, "debugLogger.js"),
  "const quiet = () => {};\n" +
    "module.exports = { debug: quiet, info: quiet, warn: quiet, error: (m, c) => console.error(m, c || '') };\n"
);
process.env.NODE_PATH = path.join(dir, "node_modules"); // resolve `ws` from the build
Module._initPaths();
const Client = require(path.join(tmp, "client.js"));

const CHUNK = 800 * 2; // OpenWhispr's audio worklet batch: 800 samples of PCM16
const pcm = fs.readFileSync(pcmFile);
const deadline = setTimeout(() => {
  console.error("smoke test: timed out after 60 s");
  process.exit(1);
}, 60000);

(async () => {
  const client = new Client();
  client.onError = (err) => console.error("smoke test: client error:", err.message);
  await client.connect({ apiKey: "smoke-test", model: "gpt-4o-mini-transcribe" });
  for (let off = 0; off < pcm.length; off += CHUNK) {
    client.sendAudio(pcm.subarray(off, off + CHUNK));
    await new Promise((r) => setTimeout(r, 5)); // ~7x real time
  }
  const { text } = await client.disconnect();
  clearTimeout(deadline);
  fs.rmSync(tmp, { recursive: true, force: true });
  if (!text.trim()) {
    console.error("smoke test: connected, but no transcript came back");
    process.exit(1);
  }
  console.log(`smoke test: ok, transcript "${text.trim()}"`);
})().catch((err) => {
  console.error("smoke test failed:", err.message || err);
  process.exit(1);
});
