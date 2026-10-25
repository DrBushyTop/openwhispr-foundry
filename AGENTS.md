# Notes for coding agents

The [README](README.md) covers setup and [docs/how-it-works.md](docs/how-it-works.md) the code. These are traps agents hit while working on this repo.

## Starting OpenWhispr Patched from a T3 Code shell

T3 Code sets `ELECTRON_RUN_AS_NODE=1` in its shells. With it, the app's binary runs as plain Node: it exits 0 within a second, prints only a `codesign_util.cc ... task_name_for_pid` line, and rejects Chromium flags (`bad option: --enable-logging=stderr`, exit 9). `open -g` launched from that shell failed the same way. It looks like a broken build or a stuck single-instance lock, but it's neither.

Check with `echo ${ELECTRON_RUN_AS_NODE-unset}`, and start the app with it removed:

```sh
env -u ELECTRON_RUN_AS_NODE open -g "/Applications/OpenWhispr Patched.app"
```

SSH sessions to m1 don't have the variable. Run `install-launchagent.sh` with `env -u ELECTRON_RUN_AS_NODE` too, since it opens the app.

## Quitting the app

SIGTERM only hides it to the tray. A later `open` then just brings back the old process, with the environment it started with. Quit it with `osascript -e 'quit app "OpenWhispr Patched"'` and wait until `pgrep -f "OpenWhispr Patched.app/Contents/MacOS/"` finds nothing. Check that no meeting is recording first: the shim log shows an `rt ... open` line without a matching `closed`.

## Changing OpenWhispr's settings

The app keeps settings in its renderer's localStorage (`~/Library/Application Support/open-whispr/Local Storage/leveldb`). It reads them at startup and holds the database open while it runs, so don't edit the files. `set-cleanup-prompt.sh` shows the way that works: quit the app, start it once with `--remote-debugging-port` on 127.0.0.1, call `localStorage.setItem` in a window through DevTools, quit, and `open -g` it again. The same works for other keys, such as `preferredLanguage`. Key names are in `src/stores/settingsStore.ts` of the OpenWhispr source (`~/Code/Omat/openwhispr-build`).

To read a value without the app, parse the LevelDB `.log` file. Records are split into 32 KB blocks with a 7-byte header each, so a long value like the cleanup prompt can't be read with `strings`.

Transcription history is off on m1, so `transcriptions.db` has no raw-and-cleaned pairs to compare there. To see what the cleanup step does, send the same system prompt to the shim's `/v1/chat/completions`. The prompt is the cleanup prompt, then the language instruction from `src/config/languageRegistry.json`, then the dictionary suffix. Send the transcript wrapped as OpenWhispr wraps it, in `src/config/prompts/index.ts`.
