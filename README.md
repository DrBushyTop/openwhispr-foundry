# OpenWhispr to Azure Foundry shim

A local HTTP server that exposes Azure AI Foundry as OpenWhispr's self-hosted speech-to-text and language model endpoints. Auth is the local az CLI login, with no API keys. Based on OpenWhispr's [custom-asr-shim example](https://github.com/OpenWhispr/openwhispr/tree/main/examples/custom-asr-shim).

```
OpenWhispr STT      --multipart webm-->   shim :9447/audio/transcriptions --mp3/wav-->   Azure Speech transcriptions:transcribe
OpenWhispr meetings --Realtime WebSocket-> shim :9447/v1/realtime  --pause-cut segments-> Azure Speech transcriptions:transcribe
OpenWhispr LLM      --chat completions--> shim :9447/v1/chat/completions  ------------->  Foundry /openai/v1/chat/completions
```

Port 9447 spells "WHIS" on a phone keypad. Paths work with or without a `/v1` prefix. The meetings route needs a patched OpenWhispr, see [Meetings](#meetings-note-recording).

| File | Contents |
| --- | --- |
| `foundry_shim.py` | HTTP server, routes, `main`. The LaunchAgent runs this. |
| `foundry.py` | az CLI token, HTTPS connection pool, `open_azure`, logging |
| `stt.py` | model and locale mapping, Azure Speech request, audio conversion |
| `realtime.py` | OpenAI Realtime transcription protocol, pause detection, segment worker |
| `websocket.py` | minimal RFC 6455 server |
| `chat.py` | chat completions forwarding and parameter fixes |
| `openwhispr-patch/` | the OpenWhispr patch, build and signing scripts |

## Speech-to-text

The OpenWhispr **Model** field picks the backend for each request:

| Model field | Backend | Endpoint (resource group `opencode`) |
| --- | --- | --- |
| `mai-transcribe-2` (or empty) | MAI-Transcribe-2, `transcribeStyle: clean` | `opencode-lpqn3wrkin5y2` (swedencentral) |
| `mai-transcribe-1.5` | MAI-Transcribe-1.5 | same |
| `llm-speech` | LLM Speech, enhanced mode | `opencode-neu-lpqn3wrkin5y2` (northeurope) |

LLM Speech isn't available in swedencentral. The swedencentral resource returns `InvalidModel` for it, so LLM Speech requests go to the northeurope resource.

OpenWhispr sends its custom dictionary as `prompt`, and the shim puts it into `phraseList.phrases`. The `language` field goes into `locales`. LLM Speech gets it mapped to a full locale (`fi` becomes `fi-FI`). With a forced locale, LLM Speech returned unpunctuated lowercase text in testing, so leave the language on auto for it.

The upload format differs per backend. MAI gets 48 kbps MP3, which is about 5x smaller than WAV and saved about 150ms per request. Its transcripts matched WAV exactly. LLM Speech gets WAV because it's format-sensitive. With MP3 it heard "Azure Foundryyn" as "Asher Phone:ään" on every run, and with WebM it dropped casing and punctuation. The ffmpeg conversion takes 30–40ms.

## Connection reuse

The shim keeps HTTPS connections to Azure open between requests. A new connection costs about 170ms (TCP plus TLS, with a ~55ms round trip from Italy). Only the first request to each host after a restart pays it. The log shows `conn=new` or `conn=reused` on each line. Azure closed idle connections somewhere between 90 and 180 seconds in testing, so the shim drops them after 100s. The first dictation after a longer pause opens a new connection. If Azure closes one sooner, the attempt fails instantly and the shim reconnects.

Measured on a 14s clip, MAI-Transcribe-2 in swedencentral:

| | Upstream | End to end through the shim |
| --- | --- | --- |
| WAV, new connection per request (before) | 0.63–0.85s | ~0.85s |
| MP3, reused connection (now) | 0.27–0.34s | ~0.40s |

LLM Speech's `enhancedMode.prompt` can't do dictation cleanup. It changes output format (`Output must be in lexical format.` works), but it ignored rules like "keep only the corrected version" and "convert spoken period to .". Cleanup belongs in OpenWhispr's Language Models step.

## Meetings (Note Recording)

OpenWhispr's Note Recording can't use a self-hosted URL (the settings card says "Coming Soon" and the router rejects it). Every meeting streaming provider has a fixed URL. Bring-your-own-key OpenAI uses `openaiRealtimeStreaming.js`, which hardcodes `wss://api.openai.com/v1/realtime`. `openwhispr-patch/realtime-url.patch` changes that one line to read `OPENWHISPR_OPENAI_REALTIME_URL` first. The install script sets that variable at login to `ws://localhost:9447/v1/realtime?intent=transcription`. The official app ignores the variable.

The shim plays OpenAI's Realtime transcription server. OpenWhispr opens one WebSocket for the mic and one for system audio, and streams 24 kHz PCM16. `realtime.py` runs an energy VAD on each stream:

- A segment starts after 60ms of speech, keeping 300ms of audio from before it.
- It ends at a pause, with rules per stream:

  | Stream | Ends at a pause of | once it's at least | or at any pause of |
  | --- | --- | --- | --- |
  | mic (you) | 800ms | 10s | 2s |
  | system (others) | 700ms | 5s | 1.5s |

- At 30s it's cut at the quietest 20ms frame in its last 3s.
- Segments with under 200ms of voiced audio are dropped as noise.

The shim tells the streams apart by the detection threshold OpenWhispr sends: 0.3 for system audio (`MEETING_SYSTEM_VAD_THRESHOLD`), 0.6 for the mic. It ignores OpenWhispr's `silence_duration_ms` (600), which is tuned for OpenAI's own detection. The first real meeting used 600ms/3s rules for both streams and got 3–5s segments. A thinking pause split "agendana on 5 | viime vuoden budjetin läpikäynti", and MAI ended the first half with a full stop. A 2s segment of just "öö" came back in Japanese as "あの、" ("ano" means "um" in Japanese). The mic can take longer segments because it's only you. The system stream cuts sooner because OpenWhispr assigns one speaker label per segment, and a long segment could span two remote speakers.

Each segment is encoded to MP3 in memory and sent to MAI-Transcribe-2 on a worker thread, one per stream, so results arrive in order. When recording stops, OpenWhispr sends `commit` and stops waiting at the first transcript after it. So after a commit the shim sends everything still pending as one combined transcript. OpenWhispr labels speakers locally after the meeting; the shim only returns text.

With the first 600ms/3s rules, tested with OpenWhispr's own client (`openaiRealtimeStreaming.js` with the patch, run in Node) on 40s of English and Finnish with pauses. It returned six segments, each 0.2–0.3s after its pause, since MAI took 0.18–0.30s per segment. Finnish numbers came out as "kello 15.30" and "6 osallistujaa". `disconnect()` returned 2–13ms after the stop when the last phrase had already closed, and about 250ms when the stop fell mid-word. 51s of speech with no real pauses was force-cut at 28s, on a word boundary. With the mic rules, Finnish speech with 800–900ms thinking pauses stayed whole and was cut only at 2.5s topic breaks, into three 6–10s segments.

OpenWhispr sends OpenAI model names (`gpt-4o-mini-transcribe`), which map to `FOUNDRY_REALTIME_MODEL` (default `mai-transcribe-2`). A realtime session gets no dictionary words or language hint from OpenWhispr, so MAI auto-detects the language.

### Build the patched app

The patched build is a separate app, **OpenWhispr Patched** (`/Applications/OpenWhispr Patched.app`, bundle ID `net.huuhka.openwhispr-patched`). It sits next to the official app, gets its own entries under Privacy & Security, and has no update feed, so the official updater never replaces it.

```sh
openwhispr-patch/create-signing-cert.sh   # once: local code-signing certificate, asks for your password
openwhispr-patch/build.sh                 # ../openwhispr at its newest vX.Y.Z release tag
openwhispr-patch/build.sh ../openwhispr v1.10.2
```

`build.sh` builds in a separate git worktree (`../openwhispr-build`), so your clone stays clean. It applies `realtime-url.patch` and `note-images.patch` (see [Screenshots in notes](#screenshots-in-notes)) and runs OpenWhispr's own prepack steps, which compile the native helpers and download the bundled binaries. Then it runs `electron-builder --mac --dir` with the new name and bundle ID, and signs the app with `sign.js`. That uses `@electron/osx-sign` with hardened runtime and OpenWhispr's entitlements. It needs Node and the Xcode command line tools.

Why the signing certificate: macOS ties microphone, screen recording (system audio) and accessibility grants to the app's signing certificate. The official app's grants belong to OpenWhispr's Apple team (`T832773L2J`), so no local build can reuse them. An unsigned build is identified by the hash of its own files, which changes with every rebuild, so the prompts would come back after every update. With the local certificate you grant them once. They survive rebuilds as long as the certificate stays in your keychain. `codesign` refuses a self-signed certificate that isn't trusted (tested), which is why the script trusts it for code signing and asks for your password.

Settings are shared. Both apps read `~/Library/Application Support/open-whispr`, since Electron names that folder after `package.json`, not the product name. So the patched app starts with your current setup. They also share the single-instance lock: only one of them can run at a time, so they never compete for the hotkeys. The patched app may ask once for access to OpenWhispr's keychain item for stored keys; allow it. The menu bar still reads "OpenWhispr" (`main.js` forces that name).

### Updating

The patched app has no update feed, so nothing tells you about new releases. Check with `gh release list --repo OpenWhispr/openwhispr --limit 3`, or watch the repo's releases on GitHub. To update:

```sh
openwhispr-patch/build.sh     # fetches tags itself; no need to pull ../openwhispr
# quit OpenWhispr Patched, then:
rm -rf "/Applications/OpenWhispr Patched.app" && ditto ../openwhispr-build/dist/mac-arm64/"OpenWhispr Patched.app" "/Applications/OpenWhispr Patched.app"
```

Release tags are safer than `main`, which can be mid-change. Permissions carry over, since the bundle ID and certificate stay the same. The build stops early in two cases. If upstream changed code a patch touches, `git apply` fails and names the patch that needs updating. If upstream changed the realtime protocol, the smoke test fails and the shim needs updating. The smoke test streams a spoken sentence through the new version's client into the running shim, and it passes only if a transcript comes back.

### Removing the official app

The patched app doesn't need the official one. Quit it and move `/Applications/OpenWhispr.app` to the Trash. Don't use OpenWhispr's `scripts/complete-uninstall.sh`: it also deletes `~/Library/Application Support/open-whispr`, which is the settings folder the patched app uses. Its old permission entries can be cleared with `tccutil reset All com.gizmolabs.openwhispr`. Keeping it installed costs 758 MB and gives you a fallback if a patched build misbehaves.

Build notes from the first run (v1.10.2, about 0.5 GB downloaded, about 10 minutes):

- OpenWhispr pins Node 24 in `.nvmrc` and sets `engine-strict`, so `npm ci` fails on a newer default Node. `build.sh` then uses Homebrew's keg-only `node@24` (`brew install node@24`), leaving your default `node` alone.
- `download-whisper-vad-model.js` can hang after printing "Downloaded", because an open socket to Hugging Face's CDN keeps Node running. If the log stops there, kill that `node scripts/download-whisper-vad-model.js` process and re-run. The rerun skips finished downloads.
- `@electron/osx-sign` looks identities up under the default trust policy, where the self-signed certificate is reported as not trusted. `sign.js` therefore passes `identityValidation: false` and lets `codesign` check it under the code-signing policy.

### OpenWhispr settings for meetings

Settings → AI Models → Speech-to-Text → Note Recording → Cloud Providers:

- Provider: OpenAI
- Model: any, for example `gpt-4o-mini-transcribe`. The shim uses MAI regardless.
- OpenAI API key: any non-empty value. The shim ignores it; auth goes through your az login.

Upstream context: [issue #1280](https://github.com/OpenWhispr/openwhispr/issues/1280) tracks realtime coverage. A maintainer there declined fixed-interval HTTP chunking for meetings because it clips words. That's the approach of [#2083](https://github.com/OpenWhispr/openwhispr/pull/2083) and [#1337](https://github.com/OpenWhispr/openwhispr/pull/1337). Cutting at pauses avoids most of that.

## Screenshots in notes

`openwhispr-patch/note-images.patch` lets you paste screenshots into a note, and note actions send them to the model. It's for meetings where a customer presents something: take a screenshot of the slide with Ctrl+Cmd+Shift+4, click in the note's Notes tab, and press Cmd+V. Dragging the screenshot thumbnail in works too. The Notes tab stays editable while recording.

- **Storage.** `src/helpers/noteMedia.js` saves each image to `~/Library/Application Support/open-whispr/note-media/<paste time in ms>-<random>.png`. The note text only holds `![Screenshot 10:04](openwhispr-media://image/<name>)`, and the app serves that scheme from the folder. Embedding the image in the note text as base64 would put megabytes into the search index, the Markdown mirror, cloud sync and the "ask about this note" chat, which sends the whole note.
- **Editor.** A TipTap image node (`src/components/ui/noteImageExtension.ts`) handles paste and drop. It accepts only `openwhispr-media://` sources, so pasting HTML from a web page never makes the app load remote images. A paste that also carries text, like cells copied from Excel, is still pasted as text.
- **Note actions.** Each screenshot becomes `[Screenshot N, pasted HH:MM]` where it sits in your notes. If it was pasted during the recording, the same label goes into the transcript before the first segment that started after it, so the model knows what was being said. The images are sent after the text at up to 2048px, as PNG or JPEG, whichever is smaller (a 2560×1440 slide came to 98 KB). They go with a short instruction to treat them as source material with the transcript's weight. Generate Notes and Detailed Notes may place a screenshot in the output with its exact link, and the app removes any other image link the model writes. Follow-up email gets the content but no images, since the email is pasted elsewhere.
- **Providers.** Images reach the model through Self-Hosted (how the setup above is configured) and the OpenAI-compatible providers (OpenAI, custom, OpenRouter, Groq). Anthropic, Gemini and OpenWhispr Cloud send the notes without the images and without the instruction.
- **Cleanup.** At startup the app deletes files in `note-media` that no live note links to, once they're a day old. A deleted note's screenshots go the next time the app starts.
- **Limits.** Up to 40 screenshots per action. The shim accepts chat requests up to 64 MB for this. Links don't render outside the app, in Markdown mirror files or `.md` exports. The official app has no image support, so editing a note there drops its screenshot links.

Tested in the installed app on a meeting note with a Finnish transcript and a pasted 2560×1440 slide. Detailed Notes with gpt-6-sol took 4.5s (3,694 tokens in). It took the slide's dates and prices exactly and matched "Liisa" in the transcript to "Liisa Korhonen" on the slide. It also placed the screenshot under the section it supports. Follow-up email used the same figures and included no image.

## Language models (text cleanup)

`GET /models` lists `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-6-luna` and `gpt-6-sol`, which are deployments on `opencode-lpqn3wrkin5y2`. The first three are fast enough for dictation cleanup. gpt-6-sol is for note formatting. On a 40s test transcript it took 2.5s without thinking and 2.8s with it (2.2s streaming). It was also the only model that recorded a proposal as a proposal rather than a decision. `POST /chat/completions` forwards any deployment name, streaming included. OpenWhispr owns the prompts. The shim changes only the parameters Azure rejects:

- It renames `max_tokens` to `max_completion_tokens`. gpt-5 and newer on Azure reject `max_tokens`.
- OpenWhispr's "Disable thinking output" toggle sends `reasoning: {effort}`, `think`, `thinking` or `chat_template_kwargs`, which are Ollama and vLLM hints. The shim turns them into `reasoning_effort`, so the toggle still works. Without a hint, the model's default applies.
- If Azure rejects a parameter by name, the shim retries without it. For example, gpt-6-luna rejects `temperature` when thinking is on.

Measured with thinking disabled (`reasoning_effort: none`). Cleanup of one dictation takes about 1s, and most of that is request overhead, not model size:

| Model | Non-streaming | Streaming, first byte | Note |
| --- | --- | --- | --- |
| gpt-5.4-nano | 1.0–1.2s | 0.7s | sometimes trims wording ("Can you send me" became "Send me") |
| gpt-5.4-mini | 0.9–1.4s | 0.5s | most faithful in testing |
| gpt-6-luna | 1.3–1.8s | 0.6s | about 2.2s with thinking on |

## Auth

The shim runs `az account get-access-token --tenant 7135bcf1-... --resource https://cognitiveservices.azure.com` and caches the token until 5 minutes before expiry. One token covers Speech and Azure OpenAI. Your az user needs a data-plane role on both resources. The subscription-level Foundry User role covers it.

## Run at login

```sh
./install-launchagent.sh     # the shim agent, plus one that sets OPENWHISPR_OPENAI_REALTIME_URL at login
./uninstall-launchagent.sh   # stops and removes both, and unsets the variable
```

The agent starts at login and launchd restarts it if it exits. Re-run the install script if you move the repo or change Python, az, or ffmpeg paths. Logs go to `~/Library/Logs/openwhispr-foundry-shim.log`, with one `stt` or `chat` line per request showing latency, and `rt` lines when a realtime session opens and closes.

Restart after editing the shim:

```sh
launchctl kickstart -k gui/$(id -u)/net.huuhka.openwhispr-foundry-shim
```

If the az login expires, the log shows `az CLI token fetch failed`. Run `az login --tenant 7135bcf1-5a12-4e82-ad41-c263afa243e8`. The shim picks up the new login on the next request without a restart.

## Run manually

Requires Python 3.8+, ffmpeg, and az. Standard library only.

```sh
python3 foundry_shim.py
```

Optional env vars: `FOUNDRY_MAI_ENDPOINT`, `FOUNDRY_LLM_SPEECH_ENDPOINT`, `FOUNDRY_DEFAULT_MODEL`, `FOUNDRY_MAI_STYLE` (`clean` or `verbatim`), `FOUNDRY_CHAT_ENDPOINT`, `FOUNDRY_CHAT_MODELS` (comma-separated, for `/models`), `FOUNDRY_REALTIME_MODEL`, `FOUNDRY_VAD_MIN_RMS` (pause detector floor, default 300), `SHIM_PORT`. The LaunchAgent doesn't pass env vars, so to change a default for the auto-started shim, edit it in the module that reads it and restart.

## OpenWhispr settings

Settings → AI Models → Speech-to-Text → Self-Hosted:

- Server URL: `http://localhost:9447`
- Model: `mai-transcribe-2` or `llm-speech`

Settings → AI Models → Language Models → Self-Hosted:

- Endpoint URL: `http://localhost:9447`
- API key: leave empty
- Model: pick `gpt-5.4-mini` (or nano or luna) after Refresh
- Disable thinking output: on

The Prompt Studio "Test" button may still say "Custom endpoint base URL missing" in this setup. The test checks the Custom provider's URL setting instead of the self-hosted one. Dictate for real and check the shim log for a `chat` line.

## Tests

```sh
python3 test_shim.py
```
