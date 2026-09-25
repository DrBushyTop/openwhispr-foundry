# OpenWhispr to Azure Foundry shim

A local HTTP server that exposes Azure AI Foundry as OpenWhispr's self-hosted speech-to-text and language model endpoints. Auth is the local az CLI login, with no API keys. Based on OpenWhispr's [custom-asr-shim example](https://github.com/OpenWhispr/openwhispr/tree/main/examples/custom-asr-shim).

```
OpenWhispr STT  --multipart webm-->   shim :9447/audio/transcriptions --wav-->  Azure Speech transcriptions:transcribe
OpenWhispr LLM  --chat completions--> shim :9447/v1/chat/completions  ------->  Foundry /openai/v1/chat/completions
```

Port 9447 spells "WHIS" on a phone keypad. Paths work with or without a `/v1` prefix.

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

## Language models (text cleanup)

`GET /models` lists `gpt-5.4-mini`, `gpt-5.4-nano` and `gpt-6-luna`, which are deployments on `opencode-lpqn3wrkin5y2`. `POST /chat/completions` forwards any deployment name, streaming included. OpenWhispr owns the prompts. The shim changes only the parameters Azure rejects:

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
./install-launchagent.sh     # writes ~/Library/LaunchAgents/net.huuhka.openwhispr-foundry-shim.plist and starts it
./uninstall-launchagent.sh   # stops and removes it
```

The agent starts at login and launchd restarts it if it exits. Re-run the install script if you move the repo or change Python, az, or ffmpeg paths. Logs go to `~/Library/Logs/openwhispr-foundry-shim.log`, with one `stt` or `chat` line per request showing latency.

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

Optional env vars: `FOUNDRY_MAI_ENDPOINT`, `FOUNDRY_LLM_SPEECH_ENDPOINT`, `FOUNDRY_DEFAULT_MODEL`, `FOUNDRY_MAI_STYLE` (`clean` or `verbatim`), `FOUNDRY_CHAT_ENDPOINT`, `FOUNDRY_CHAT_MODELS` (comma-separated, for `/models`), `SHIM_PORT`. The LaunchAgent doesn't pass env vars, so to change a default for the auto-started shim, edit it in `foundry_shim.py` and restart.

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
