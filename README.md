# OpenWhispr to Azure Foundry shim

Use [OpenWhispr](https://github.com/OpenWhispr/openwhispr) with speech and language models on Azure AI Foundry, signed in with your az CLI login. No API keys.

The shim is a small local server on port 9447. OpenWhispr treats it as a self-hosted speech-to-text and language model server, and the shim forwards each request to Azure. It's based on OpenWhispr's [custom-asr-shim example](https://github.com/OpenWhispr/openwhispr/tree/main/examples/custom-asr-shim).

What works:

- **Dictation** with MAI-Transcribe-2 or LLM Speech. Your OpenWhispr dictionary goes along as a phrase list. A 14s clip takes about 0.4s.
- **Text cleanup** with gpt-5.4-mini and other Foundry deployments, about 1s per dictation.
- **Meeting transcription and screenshots in notes.** These need a patched build of OpenWhispr, see [openwhispr-patch](openwhispr-patch/README.md).
- **Dictation from an Android phone**, through the patched app and Tailscale, with the same models, prompt and dictionary. See [android](android/README.md).

## Quickstart

On a Mac with [Homebrew](https://brew.sh) and Python 3, and Azure resources as listed under [requirements](#requirements):

```sh
brew install ffmpeg azure-cli
git clone https://github.com/DrBushyTop/openwhispr-foundry && cd openwhispr-foundry
az login --tenant <your-tenant-id>
cp config.example.env config.env   # then fill in your tenant, resource URLs and deployment names
./install-launchagent.sh           # starts the shim now and at every login
curl http://localhost:9447/v1/models
```

The `curl` should list the chat deployments from your config. Then in OpenWhispr:

- Settings → AI Models → Speech-to-Text → Self-Hosted: Server URL `http://localhost:9447`, Model `mai-transcribe-2`
- Settings → AI Models → Language Models → Self-Hosted: Endpoint URL `http://localhost:9447`, API key empty, click Refresh and pick a model, turn on "Disable thinking output"

Dictate something. If it doesn't work, the [setup](#setup) section below goes through each step, and [troubleshooting](#troubleshooting) covers the common problems.

## Requirements

- macOS
- Python 3.8 or newer. The shim uses only the standard library.
- [ffmpeg](https://ffmpeg.org) and the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), both on your `PATH`: `brew install ffmpeg azure-cli`
- An Azure account with a data-plane role on the Speech and Azure OpenAI resources. The subscription-level Foundry User role covers both.
- To use your own Azure resources: a Speech or Foundry resource in a region that offers MAI-Transcribe, and chat model deployments on an Azure OpenAI or Foundry resource. LLM Speech is optional and only available in some regions.

## Setup

The quickstart steps again, with what each one does and how to check it.

### 1. Sign in to Azure

```sh
az login --tenant 7135bcf1-5a12-4e82-ad41-c263afa243e8
```

Use your own tenant ID if you're on your own resources.

### 2. Point the shim at your resources

The defaults point at my Azure resources. To use yours, copy the example config and fill in your tenant, resource URLs and deployment names:

```sh
cp config.example.env config.env
```

Each setting in `config.example.env` has a comment explaining it. `config.env` is gitignored. The shim reads it at startup, so restart the shim after editing it.

### 3. Start the shim at login

```sh
./install-launchagent.sh
```

This installs a LaunchAgent that starts the shim now and at every login, and restarts it if it exits. It also sets the variable the patched app uses for meetings, which the official app ignores, and then opens OpenWhispr Patched if it's in `/Applications`. So you can turn off the app's own **Launch at login**: it didn't start the app after a reboot on M1, and it could start the app before the variable was set.

To check that it's running:

```sh
curl http://localhost:9447/v1/models
```

To try it without installing anything, run `python3 foundry_shim.py` in a terminal instead.

### 4. Point OpenWhispr at the shim

Settings → AI Models → Speech-to-Text → Self-Hosted:

- Server URL: `http://localhost:9447`
- Model: `mai-transcribe-2`

Settings → AI Models → Language Models → Self-Hosted:

- Endpoint URL: `http://localhost:9447`
- API key: leave empty
- Model: click Refresh, then pick `gpt-5.4-mini`
- Disable thinking output: on

Dictate something. The shim log should show an `stt` line, then a `chat` line:

```sh
tail -f ~/Library/Logs/openwhispr-foundry-shim.log
```

## Choosing models

For speech-to-text, type one of these in the Model field:

| Model | When to use it |
| --- | --- |
| `mai-transcribe-2` | The default, and the fastest. Also used when the field is empty. |
| `mai-transcribe-1.5` | The previous MAI version. |
| `llm-speech` | Alternative backend. Leave OpenWhispr's language on auto, since a forced language made it return lowercase text with no punctuation. |

For language models, the Refresh button lists the models in `FOUNDRY_CHAT_MODELS`. With the default config:

| Model | When to use it |
| --- | --- |
| `gpt-5.4-mini` | Dictation cleanup. The most faithful to what you said in testing. |
| `gpt-6-luna` | Dictation cleanup, a bit slower. |
| `gpt-6-sol` | Meeting notes. Slower, but better at telling a proposal from a decision. |

The shim sends these three to EU data zone deployments (`gpt-5.4-mini-eu` and so on, set in `FOUNDRY_CHAT_DEPLOYMENTS`), so your transcripts are processed only in EU regions. The deployments with the plain names are Global Standard, which Azure can process in any region. gpt-5.4-nano has no EU data zone version, so it's no longer listed. Speech stays in Europe too: MAI-Transcribe runs in swedencentral and LLM Speech in northeurope.

## Spoken Finnish

OpenWhispr's default cleanup prompt says "Fix grammar, spelling, punctuation", and with it the cleanup model turned Finnish puhekieli into kirjakieli: "ei oo ... mut katotaan" became "ei ole ... mutta katsotaan". Both speech models kept the spoken forms in a test with Finnish `say` audio, so it's the cleanup step that changes them.

[docs/cleanup-prompt.txt](docs/cleanup-prompt.txt) is the default prompt with that rule replaced. It fixes only punctuation and capitalization, and has a REGISTER rule: words stay in the form you said them, so puhekieli stays puhekieli and kirjakieli stays kirjakieli. It also counts "ei ku" and "tai siis" as self-corrections, and has a Finnish example of each register. Times are written 17.30. In testing with gpt-5.4-mini, nine Finnish sentences in both registers kept their register in three runs each. Self-corrections and English dictation still came out as before. Once, "tienny" became "tiennyt"; that was before `tienny` was added to the rule's examples.

To use it, paste the file into Prompt Studio's cleanup prompt and save, or run:

```sh
./set-cleanup-prompt.sh            # or: ./set-cleanup-prompt.sh path/to/prompt.txt
```

The script quits OpenWhispr Patched, sets the prompt and starts the app again, so it works over SSH too. Leave OpenWhispr's language on auto. With Finnish selected, the app adds "maintaining proper Finnish grammar, spelling, and punctuation" after your prompt, and that can't be edited.

## Meetings and screenshots

The official OpenWhispr app can't send Note Recording to a local server, so meetings need a patched build. The same build lets you paste screenshots into a note and have note actions send them to the model. [openwhispr-patch/README.md](openwhispr-patch/README.md) covers building it and setting it up.

For meetings, the shim sends your OpenWhispr dictionary and the note's participant names to Azure as a phrase list, and retries segments that fail. It also saves each meeting's audio and its own transcript under `~/Library/Application Support/openwhispr-foundry/meetings` for 14 days, so nothing is lost if a segment fails or a note goes missing. Set `FOUNDRY_MEETING_AUDIO_DAYS=0` in `config.env` to turn that off. [docs/how-it-works.md](docs/how-it-works.md#language-phrases-retries-and-saved-audio) has the details.

## Troubleshooting

**`az CLI token fetch failed` in the log.** Your az login expired. Run the `az login` from step 1 again. The shim picks up the new login on the next request, no restart needed.

**Prompt Studio's Test button says "Custom endpoint base URL missing".** The test checks the Custom provider's URL instead of the self-hosted one. Ignore it, dictate for real and look for a `chat` line in the log.

**The first dictation after a break is slower.** The shim reuses connections to Azure, but drops them after 100 seconds idle. Reconnecting adds about 170ms.

**You moved the repo, or changed Python, az or ffmpeg.** Run `./install-launchagent.sh` again. It records their paths.

**You edited the shim or `config.env`.** Restart it:

```sh
launchctl kickstart -k gui/$(id -u)/net.huuhka.openwhispr-foundry-shim
```

The startup lines in the log show which tenant, endpoints and models it picked up.

**You changed `SHIM_PORT`.** Run `./install-launchagent.sh` again so the meetings URL follows, and update the URLs in OpenWhispr's settings.

## Uninstall

```sh
./uninstall-launchagent.sh
```

This stops the shim, removes both LaunchAgents and unsets the meetings variable.

## More detail

- [docs/how-it-works.md](docs/how-it-works.md): code layout, Azure endpoints, audio formats, meeting segmentation, parameter fixes, settings and the measurements behind the defaults.
- [openwhispr-patch/README.md](openwhispr-patch/README.md): the patched OpenWhispr app.
- [android/README.md](android/README.md): the Whispr Mic phone app.

Run the tests with `python3 test_shim.py`.

## License

MIT, see [LICENSE](LICENSE). Includes code from OpenWhispr and OpenDictate, see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
