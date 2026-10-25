# OpenWhispr Patched

Scripts that build a patched copy of OpenWhispr, installed as a separate app called **OpenWhispr Patched**. You only need it for three things the official app can't do:

- **Meeting transcription through the shim.** Note Recording streams to Azure via the shim instead of to OpenAI.
- **Screenshots in notes.** Paste a slide into a meeting note, and note actions like Generate Notes send it to the model with the transcript.
- **Dictation from your phone.** The [Whispr Mic](../android/README.md) Android app sends recordings to the app over Tailscale and gets back text cleaned up with your settings.

The patches contain and modify OpenWhispr source code (MIT, Copyright (c) 2024 OpenWhispr Team). See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

Plain dictation and text cleanup work with the official app. You don't need this for those.

## Quickstart

Do the [main quickstart](../README.md#quickstart) first, so the shim is running. Then, from the repo root:

```sh
xcode-select --install                    # if you don't have the command line tools yet
brew install node@24                      # only if your default node isn't the version in OpenWhispr's .nvmrc
git clone https://github.com/OpenWhispr/openwhispr ../openwhispr
openwhispr-patch/create-signing-cert.sh   # once, asks for your password
openwhispr-patch/build.sh                 # about 10 minutes and 0.5 GB the first time
```

Quit OpenWhispr, then install the build:

```sh
rm -rf "/Applications/OpenWhispr Patched.app" && ditto ../openwhispr-build/dist/mac-arm64/"OpenWhispr Patched.app" "/Applications/OpenWhispr Patched.app"
```

`build.sh` prints this command with the right path at the end, in case yours differs.

Open **OpenWhispr Patched** and allow the permissions it asks for. Then set Settings → AI Models → Speech-to-Text → Note Recording → Cloud Providers to Provider OpenAI, any model, and any non-empty API key. Start a note recording and check the shim log for `rt` lines.

The sections below explain each step and what to do when the build stops.

## Why a patch

OpenWhispr's Note Recording can't use a self-hosted URL. The settings card says "Coming Soon" and the router rejects it. Every meeting streaming provider has a fixed URL. Bring-your-own-key OpenAI uses `openaiRealtimeStreaming.js`, which hardcodes `wss://api.openai.com/v1/realtime`.

`realtime-url.patch` changes that one line to read `OPENWHISPR_OPENAI_REALTIME_URL` first. The shim's `install-launchagent.sh` sets that variable at login to `ws://localhost:9447/v1/realtime?intent=transcription`. The official app ignores the variable.

`note-images.patch` adds the screenshot support, described [below](#screenshots-in-notes). `mobile-dictation.patch` adds the endpoint for the phone app, described under [phone dictation](#phone-dictation).

## Before you build

- The shim is installed with `../install-launchagent.sh` and running. The build smoke-tests against it, and the app needs the variable that script sets.
- A clone of OpenWhispr next to this repo: `git clone https://github.com/OpenWhispr/openwhispr ../openwhispr` (run from the repo root).
- Node and npm. OpenWhispr pins a Node major version. If your default Node differs, `build.sh` looks for Homebrew's `node@<version>`, so install it with `brew install node@24` (or whatever `.nvmrc` says).
- Xcode command line tools and ffmpeg.

The first build downloads about 0.5 GB and took about 10 minutes. Later builds reuse the downloads.

## Build and install

Run these from the repo root:

```sh
openwhispr-patch/create-signing-cert.sh   # once: local code-signing certificate, asks for your password
openwhispr-patch/build.sh                 # builds ../openwhispr at its newest vX.Y.Z release tag
```

To build a specific release instead: `openwhispr-patch/build.sh ../openwhispr v1.10.4`.

The patches were last updated for and tested on **OpenWhispr v1.10.4**: they apply, the app builds, the realtime smoke test passes, and `npm run typecheck` is clean. OpenWhispr's own `npm test` has 17 failures on v1.10.4 with or without the patches. A newer release may need the patches updated; `build.sh` stops and says which patch no longer applies.

Then quit OpenWhispr (only one of the two apps can run at a time) and install:

```sh
rm -rf "/Applications/OpenWhispr Patched.app" && ditto ../openwhispr-build/dist/mac-arm64/"OpenWhispr Patched.app" "/Applications/OpenWhispr Patched.app"
```

Open **OpenWhispr Patched** from Applications. macOS asks once for microphone, screen recording (for system audio) and accessibility. It may also ask for access to OpenWhispr's keychain item for stored keys. Allow it.

## What changes compared to the official app

- It's a separate app at `/Applications/OpenWhispr Patched.app`, bundle ID `net.huuhka.openwhispr-patched`. It gets its own entries under Privacy & Security.
- It has no update feed, so the official updater never replaces it. You update it by rebuilding.
- Settings are shared. Both apps read `~/Library/Application Support/open-whispr`, since Electron names that folder after `package.json`, not the product name. The patched app starts with your current setup.
- Both apps share the single-instance lock, so only one runs at a time and they never compete for the hotkeys.
- The menu bar still reads "OpenWhispr", because `main.js` forces that name.

## Set up meetings

Settings → AI Models → Speech-to-Text → Note Recording → Cloud Providers:

- Provider: OpenAI
- Model: any, for example `gpt-4o-mini-transcribe`. The shim uses MAI regardless.
- OpenAI API key: any non-empty value. The shim ignores it and uses your az login.

Start a note recording and check the shim log for `rt` lines. If there are none, the app probably started before `install-launchagent.sh` set `OPENWHISPR_OPENAI_REALTIME_URL`. Apps opened from the Dock or Finder only see it if they start after it's set, so quit and reopen the app. [docs/how-it-works.md](../docs/how-it-works.md#meetings-realtime-transcription) explains how the shim cuts the audio into segments.

## Screenshots in notes

This is for meetings where someone presents slides. Take a screenshot with Ctrl+Cmd+Shift+4, click in the note's Notes tab, and press Cmd+V. Dragging the screenshot thumbnail in works too. The Notes tab stays editable while recording.

When you run a note action, each screenshot goes to the model with a label saying where it sits in your notes and when during the recording you pasted it. Generate Notes and Detailed Notes can place a screenshot in their output. Follow-up email uses what's on the slides but includes no images, since you paste the email elsewhere.

Things to know:

- Images reach the model through Self-Hosted (how this setup is configured) and the OpenAI-compatible providers (OpenAI, custom, OpenRouter, Groq). Anthropic, Gemini and OpenWhispr Cloud send the notes without the images.
- Up to 40 screenshots per action.
- Screenshots only show inside the app. Markdown mirror files and `.md` exports have just the link.
- The official app has no image support. Editing a note there drops its screenshot links.

Tested in the installed app on a meeting note with a Finnish transcript and a pasted 2560×1440 slide. Detailed Notes with gpt-6-sol took 4.5s (3,694 tokens in). It took the slide's dates and prices exactly and matched "Liisa" in the transcript to "Liisa Korhonen" on the slide. It also placed the screenshot under the section it supports. Follow-up email used the same figures and included no image.

### How screenshots are stored and sent

- **Storage.** `src/helpers/noteMedia.js` saves each image to `~/Library/Application Support/open-whispr/note-media/<paste time in ms>-<random>.png`. The note text only holds `![Screenshot 10:04](openwhispr-media://image/<name>)`, and the app serves that scheme from the folder. Embedding the image in the note text as base64 would put megabytes into the search index, the Markdown mirror, cloud sync and the "ask about this note" chat, which sends the whole note.
- **Editor.** A TipTap image node (`src/components/ui/noteImageExtension.ts`) handles paste and drop. It accepts only `openwhispr-media://` sources, so pasting HTML from a web page never makes the app load remote images. A paste that also carries text, like cells copied from Excel, is still pasted as text.
- **Note actions.** Each screenshot becomes `[Screenshot N, pasted HH:MM]` where it sits in your notes. If it was pasted during the recording, the same label goes into the transcript before the first segment that started after it, so the model knows what was being said. The images are sent after the text at up to 2048px, as PNG or JPEG, whichever is smaller (a 2560×1440 slide came to 98 KB). They go with a short instruction to treat them as source material with the transcript's weight. The app removes any image link the model writes other than the exact links it was given. Providers without image support get neither the images nor the instruction.
- **Cleanup.** At startup the app deletes files in `note-media` that no live note links to, once they're a day old. A deleted note's screenshots go the next time the app starts.
- **Request size.** The shim accepts chat requests up to 64 MB for this.

## Phone dictation

`mobile-dictation.patch` lets the [Whispr Mic](../android/README.md) Android app dictate through this app. A recording from the phone goes through the same steps as a hotkey dictation: your speech-to-text settings, the dictionary, cleanup with your prompt, and snippets. The text comes back to the phone instead of being pasted on the Mac. Settings stay in one place, the Mac app. The phone can also edit the dictionary and teach it words you corrected, so you don't need the Mac's screen for that.

It's off until you turn it on. From the repo root:

```sh
openwhispr-patch/enable-mobile-dictation.sh              # https://<mac>.<tailnet>.ts.net:8450
openwhispr-patch/enable-mobile-dictation.sh 8452         # another port
openwhispr-patch/enable-mobile-dictation.sh svc:whispr   # https://whispr.<tailnet>.ts.net, a Tailscale Service
```

The script:

- creates `~/.openwhispr/mobile-dictation.json` with the local port (9448) and a random 64-character token, readable only by you. Re-runs keep the token. Delete the file to get a new one.
- publishes the port to your tailnet with Tailscale Serve: `tailscale serve --bg --https=8450 http://127.0.0.1:9448`, or `tailscale serve --service=svc:whispr --https=443 http://127.0.0.1:9448` for a Service.
- prints the URL and token to enter in the phone app, and a `curl` to check it.

Restart the app afterwards so it reads the file. To turn it off, run `tailscale serve --https=8450 off` (or `tailscale serve clear svc:whispr`) and delete the file.

The app needs to be open and the Mac awake for the phone to reach it.

### A short URL with a Tailscale Service

A [Tailscale Service](https://tailscale.com/docs/features/tailscale-services) gives the endpoint its own name, like `https://whispr.<tailnet>.ts.net`, with no port. It needs some setup in the admin console first:

1. **Tag the Mac.** Only tagged machines can host a Service. Add a tag under Access controls → Tags (M1 uses `tag:ocm1`), then on the machine's page choose Edit ACL tags.
2. **Create the Service.** Under Services, add one named `whispr` with port `tcp:443`.
3. **Let the Mac host it.** In the policy file, add the tag as an auto approver for the Service. `autogroup:member` doesn't match a tagged machine.

   ```json
   "autoApprovers": { "services": { "svc:whispr": ["tag:ocm1"] } }
   ```

4. **Allow your devices to reach it.** Add a grant from your user (or group) to `svc:whispr` on `tcp:443`.
5. **Run the script** with `svc:whispr`.

If the URL doesn't connect and the Service page lists no hosts, the Mac advertised the Service before it was approved. Advertise it again with `tailscale serve drain svc:whispr; tailscale serve advertise svc:whispr`. The first request after that can take a few seconds while Tailscale gets a certificate for the new name.

### The endpoint

The app listens on 127.0.0.1 only, so only Tailscale Serve (or something else on the Mac) can reach it. Every request needs `Authorization: Bearer <token>`.

- `GET /v1/health` returns a summary of the settings dictations use: speech-to-text mode and model, whether cleanup is on, its model and whether you have a custom prompt, language, and how many dictionary words and snippets there are. The phone app shows this after the connection test.
- `POST /v1/dictate` takes the audio as the raw body, with `Content-Type: audio/wav` (or another `audio/*` type), up to 25 MB. It returns `{"text", "rawText", "cleanupFailed", "source", "timings"}`. `text` is empty if there was no speech.
- `GET /v1/dictionary` returns `{"words": [{"word", "learned"}], "autoLearn"}`: the dictionary oldest first, `learned` for words auto-learn added, and whether OpenWhispr's auto-learn setting is on.
- `POST /v1/dictionary` takes `{"add": [...], "remove": [...]}` and returns the dictionary as above. A rename is both in one request. Words added this way count as typed by hand.
- `POST /v1/learn` takes `{"original", "edited"}`: a dictation, and the text after the user's corrections. It runs OpenWhispr's own correction learner (`src/utils/correctionLearner.js`, the one desktop auto-learn uses) against the dictionary, skips words where only the ending changed, adds the rest as learned, and returns `{"words": [...], "autoLearn"}`. With auto-learn off in OpenWhispr it adds nothing.

The dictionary requests write to OpenWhispr's database directly, like its CLI bridge, and tell the app's windows, so the next dictation and the Settings page see the change. Changes sync to OpenWhispr's cloud the same way as desktop auto-learn ones.

Errors come back as `{"error": "..."}` with 400 for a malformed request, 401 for a wrong token, 413 for too much audio, 415 for a non-audio body, 502 when transcription fails, 503 when the dictation window isn't running, and 504 after two minutes without an answer.

Dictations run one at a time. The main process (`src/helpers/mobileDictation.js`) passes the audio to the dictation window, where `src/hooks/useMobileDictation.js` runs it through a separate `AudioManager`, so a phone dictation never interrupts one on the Mac. The agent route is off for these: a dictation that starts with the agent's name is cleaned up like any other, rather than sent to the agent.

Tested from M4 over Tailscale to the build on M1: a 5-second clip came back cleaned up in 3.3 seconds (1.7 s speech-to-text with LLM Speech, 1.5 s cleanup with gpt-5.4-mini).

## Updating

The patched app has no update feed, so nothing tells you about new releases. Check with `gh release list --repo OpenWhispr/openwhispr --limit 3`, or watch the repo's releases on GitHub. To update:

```sh
openwhispr-patch/build.sh     # fetches tags itself; no need to pull ../openwhispr
# quit OpenWhispr Patched, then:
rm -rf "/Applications/OpenWhispr Patched.app" && ditto ../openwhispr-build/dist/mac-arm64/"OpenWhispr Patched.app" "/Applications/OpenWhispr Patched.app"
```

Permissions carry over, since the bundle ID and certificate stay the same. Release tags are safer than `main`, which can be mid-change.

The build stops early in two cases:

- Upstream changed code a patch touches. `git apply` fails and names the patch that needs updating.
- Upstream changed the realtime protocol. The smoke test fails and the shim needs updating. The smoke test streams a spoken sentence through the new version's client into the running shim, and passes only if a transcript comes back. If the shim isn't running, the build prints a warning and skips the test.

## Removing the official app

The patched app doesn't need the official one. Quit it and move `/Applications/OpenWhispr.app` to the Trash. Its old permission entries can be cleared with `tccutil reset All com.gizmolabs.openwhispr`.

Don't use OpenWhispr's `scripts/complete-uninstall.sh`. It also deletes `~/Library/Application Support/open-whispr`, which is the settings folder the patched app uses.

Keeping the official app costs 758 MB and gives you a fallback if a patched build misbehaves.

## Why the signing certificate

macOS ties microphone, screen recording and accessibility grants to the app's signing certificate. The official app's grants belong to OpenWhispr's Apple team (`T832773L2J`), so no local build can reuse them. An unsigned build is identified by the hash of its own files, which changes with every rebuild, so the prompts would come back after every update.

With the local certificate you grant them once. They survive rebuilds as long as the certificate stays in your keychain. `codesign` refuses a self-signed certificate that isn't trusted (tested), which is why `create-signing-cert.sh` trusts it for code signing and asks for your password.

Without the certificate, `build.sh` still builds, but prints a warning and leaves the app unsigned.

## What build.sh does

1. Checks out the release in a separate git worktree (`../openwhispr-build`), so your clone stays clean.
2. Applies `realtime-url.patch`, `note-images.patch` and `mobile-dictation.patch`.
3. Runs `npm ci`, then the realtime smoke test (`smoke-test.js`) against the running shim.
4. Runs OpenWhispr's own prepack steps, which compile the native helpers and download the bundled binaries.
5. Runs `electron-builder --mac --dir` with the new name and bundle ID, and removes the update feed.
6. Signs the app with `sign.js`, which uses `@electron/osx-sign` with hardened runtime and OpenWhispr's entitlements.

## Build problems

- **`npm ci` fails on the Node version.** OpenWhispr pins Node 24 in `.nvmrc` and sets `engine-strict`. `build.sh` switches to Homebrew's keg-only `node@24` if it's installed (`brew install node@24`), leaving your default `node` alone.
- **The build hangs after "Downloaded".** `download-whisper-vad-model.js` can hang there, because an open socket to Hugging Face's CDN keeps Node running. Kill that `node scripts/download-whisper-vad-model.js` process and re-run. The rerun skips finished downloads.
- **Why `sign.js` skips identity validation.** `@electron/osx-sign` looks identities up under the default trust policy, where the self-signed certificate is reported as not trusted. `sign.js` passes `identityValidation: false` and lets `codesign` check it under the code-signing policy.

## Files

| File | What it does |
| --- | --- |
| `build.sh` | builds and signs OpenWhispr Patched |
| `create-signing-cert.sh` | creates and trusts the local code-signing certificate |
| `sign.js` | signs the built app with hardened runtime |
| `smoke-test.js` | streams audio through the new client into the shim |
| `realtime-url.patch` | reads `OPENWHISPR_OPENAI_REALTIME_URL` for meetings |
| `note-images.patch` | screenshots in notes |
| `mobile-dictation.patch` | the endpoint for phone dictation |
| `enable-mobile-dictation.sh` | turns on phone dictation and publishes it with Tailscale Serve |
