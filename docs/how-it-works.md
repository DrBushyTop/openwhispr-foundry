# How the shim works

Technical notes on the shim itself: which Azure endpoint each request goes to, what the shim changes on the way, and the measurements behind the defaults. For setup, see the [README](../README.md). For the patched OpenWhispr app, see [openwhispr-patch/README.md](../openwhispr-patch/README.md).

```
OpenWhispr STT      --multipart webm-->   shim :9447/audio/transcriptions --mp3/wav-->   Azure Speech transcriptions:transcribe
OpenWhispr meetings --Realtime WebSocket-> shim :9447/v1/realtime  --pause-cut segments-> Azure Speech transcriptions:transcribe
OpenWhispr LLM      --chat completions--> shim :9447/v1/chat/completions  ------------->  Foundry /openai/v1/chat/completions
```

Paths work with or without a `/v1` prefix.

## Code layout

| File | Contents |
| --- | --- |
| `foundry_shim.py` | HTTP server, routes, `main`. The LaunchAgent runs this. |
| `foundry.py` | `config.env` loading, az CLI token, HTTPS connection pool, `open_azure`, logging |
| `stt.py` | model and locale mapping, Azure Speech request, audio conversion |
| `realtime.py` | OpenAI Realtime transcription protocol, pause detection, segment worker |
| `websocket.py` | minimal RFC 6455 server |
| `chat.py` | chat completions forwarding and parameter fixes |
| `test_shim.py` | tests, run with `python3 test_shim.py` |
| `config.example.env` | every setting with its default, copy to `config.env` |
| `openwhispr-patch/` | the OpenWhispr patches, build and signing scripts |

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

LLM Speech's `enhancedMode.prompt` can't do dictation cleanup. It changes output format (`Output must be in lexical format.` works), but it ignored rules like "keep only the corrected version" and "convert spoken period to .". Cleanup belongs in OpenWhispr's Language Models step.

## Connection reuse

The shim keeps HTTPS connections to Azure open between requests. A new connection costs about 170ms (TCP plus TLS, with a ~55ms round trip from Italy). Only the first request to each host after a restart pays it. The log shows `conn=new` or `conn=reused` on each line. Azure closed idle connections somewhere between 90 and 180 seconds in testing, so the shim drops them after 100s. The first dictation after a longer pause opens a new connection. If Azure closes one sooner, the attempt fails instantly and the shim reconnects.

Measured on a 14s clip, MAI-Transcribe-2 in swedencentral:

| | Upstream | End to end through the shim |
| --- | --- | --- |
| WAV, new connection per request (before) | 0.63–0.85s | ~0.85s |
| MP3, reused connection (now) | 0.27–0.34s | ~0.40s |

## Meetings (realtime transcription)

This route only gets traffic from the patched app. The official app has no way to point Note Recording at a local server. [openwhispr-patch/README.md](../openwhispr-patch/README.md#why-a-patch) explains the patch.

The shim plays OpenAI's Realtime transcription server. OpenWhispr opens one WebSocket for the mic and one for system audio, and streams 24 kHz PCM16. `realtime.py` runs an energy VAD on each stream:

- A segment starts after 60ms of speech, keeping 300ms of audio from before it.
- It ends at a pause, with rules per stream:

  | Stream | Ends at a pause of | once it's at least | or at any pause of |
  | --- | --- | --- | --- |
  | mic (you) | 800ms | 10s | 2s |
  | system (others) | 700ms | 5s | 1.5s |

- At 30s it's cut at the quietest 20ms frame in its last 3s.
- Segments with under 200ms of voiced audio are dropped as noise.

The shim tells the streams apart by the detection threshold OpenWhispr sends: 0.3 for system audio (`MEETING_SYSTEM_VAD_THRESHOLD`), 0.6 for the mic. It ignores OpenWhispr's `silence_duration_ms` (600), which is tuned for OpenAI's own detection.

Why these numbers. The first real meeting used 600ms/3s rules for both streams and got 3–5s segments. A thinking pause split "agendana on 5 | viime vuoden budjetin läpikäynti", and MAI ended the first half with a full stop. A 2s segment of just "öö" came back in Japanese as "あの、" ("ano" means "um" in Japanese). The mic can take longer segments because it's only you. The system stream cuts sooner because OpenWhispr assigns one speaker label per segment, and a long segment could span two remote speakers.

Each segment is encoded to MP3 in memory and sent to MAI-Transcribe-2 on a worker thread, one per stream, so results arrive in order. When recording stops, OpenWhispr sends `commit` and stops waiting at the first transcript after it. So after a commit the shim sends everything still pending as one combined transcript. OpenWhispr labels speakers locally after the meeting. The shim only returns text.

OpenWhispr sends OpenAI model names (`gpt-4o-mini-transcribe`), which map to `FOUNDRY_REALTIME_MODEL` (default `mai-transcribe-2`). A realtime session gets no dictionary words or language hint from OpenWhispr, so MAI auto-detects the language.

### Test results

With the first 600ms/3s rules, tested with OpenWhispr's own client (`openaiRealtimeStreaming.js` with the patch, run in Node) on 40s of English and Finnish with pauses. It returned six segments, each 0.2–0.3s after its pause, since MAI took 0.18–0.30s per segment. Finnish numbers came out as "kello 15.30" and "6 osallistujaa". `disconnect()` returned 2–13ms after the stop when the last phrase had already closed, and about 250ms when the stop fell mid-word. 51s of speech with no real pauses was force-cut at 28s, on a word boundary.

With the current mic rules, Finnish speech with 800–900ms thinking pauses stayed whole and was cut only at 2.5s topic breaks, into three 6–10s segments.

### Upstream context

[Issue #1280](https://github.com/OpenWhispr/openwhispr/issues/1280) tracks realtime coverage. A maintainer there declined fixed-interval HTTP chunking for meetings because it clips words. That's the approach of [#2083](https://github.com/OpenWhispr/openwhispr/pull/2083) and [#1337](https://github.com/OpenWhispr/openwhispr/pull/1337). Cutting at pauses avoids most of that.

## Language models

`GET /models` lists `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-6-luna` and `gpt-6-sol`, which are deployments on `opencode-lpqn3wrkin5y2`. `POST /chat/completions` forwards any deployment name, streaming included. OpenWhispr owns the prompts. The shim changes only the parameters Azure rejects:

- It renames `max_tokens` to `max_completion_tokens`. gpt-5 and newer on Azure reject `max_tokens`.
- OpenWhispr's "Disable thinking output" toggle sends `reasoning: {effort}`, `think`, `thinking` or `chat_template_kwargs`, which are Ollama and vLLM hints. The shim turns them into `reasoning_effort`, so the toggle still works. Without a hint, the model's default applies.
- If Azure rejects a parameter by name, the shim retries without it. For example, gpt-6-luna rejects `temperature` when thinking is on.
- It accepts request bodies up to 64 MB, so note actions can send screenshots.

Measured with thinking disabled (`reasoning_effort: none`). Cleanup of one dictation takes about 1s, and most of that is request overhead, not model size:

| Model | Non-streaming | Streaming, first byte | Note |
| --- | --- | --- | --- |
| gpt-5.4-nano | 1.0–1.2s | 0.7s | sometimes trims wording ("Can you send me" became "Send me") |
| gpt-5.4-mini | 0.9–1.4s | 0.5s | most faithful in testing |
| gpt-6-luna | 1.3–1.8s | 0.6s | about 2.2s with thinking on |

gpt-6-sol is for note formatting. On a 40s test transcript it took 2.5s without thinking and 2.8s with it (2.2s streaming). It was also the only model that recorded a proposal as a proposal rather than a decision.

## Auth

The shim runs `az account get-access-token --tenant <FOUNDRY_TENANT_ID> --resource https://cognitiveservices.azure.com` and caches the token until 5 minutes before expiry. With `FOUNDRY_TENANT_ID` set to an empty value it leaves out `--tenant` and uses az's current tenant. One token covers Speech and Azure OpenAI. Your az user needs a data-plane role on both resources. The subscription-level Foundry User role covers it.

## Configuration

Every setting has a default that matches my resources above. The shim reads overrides from env vars and from `config.env` in the repo root, and env vars win. `config.env` is how the auto-started shim gets settings, since the LaunchAgent passes no env vars. `foundry.py` loads the file when it's first imported, before the other modules read their settings. [`config.example.env`](../config.example.env) lists every setting with a comment.

| Variable | What it sets |
| --- | --- |
| `FOUNDRY_TENANT_ID` | Entra tenant for the az token, empty for az's current tenant |
| `FOUNDRY_MAI_ENDPOINT` | Speech resource for MAI-Transcribe |
| `FOUNDRY_LLM_SPEECH_ENDPOINT` | Speech resource for LLM Speech |
| `FOUNDRY_DEFAULT_MODEL` | backend when the Model field is empty |
| `FOUNDRY_MAI_STYLE` | `clean` or `verbatim` |
| `FOUNDRY_CHAT_ENDPOINT` | Azure OpenAI resource for chat |
| `FOUNDRY_CHAT_MODELS` | comma-separated list returned by `/models` |
| `FOUNDRY_REALTIME_MODEL` | backend for meetings, default `mai-transcribe-2` |
| `FOUNDRY_VAD_MIN_RMS` | quietest audio counted as speech in meetings, default 300 |
| `SHIM_HOST` | listen address, default `127.0.0.1` |
| `SHIM_PORT` | listen port, default 9447 ("WHIS" on a phone keypad) |

`install-launchagent.sh` and `openwhispr-patch/build.sh` also read `SHIM_PORT` from `config.env`, for the meetings URL and the smoke test.

A few names aren't settings, because nothing breaks if you keep them: the LaunchAgent labels (`net.huuhka.*` in the install and uninstall scripts), and the patched app's bundle ID and signing identity name in `openwhispr-patch/`. Comments next to each say what else has to change with them.

## Logs

Logs go to `~/Library/Logs/openwhispr-foundry-shim.log`. Each request writes one `stt` or `chat` line with its latency and `conn=new` or `conn=reused`. Realtime sessions write `rt` lines when they open and close.
