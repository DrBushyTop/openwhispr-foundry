# Whispr Mic

An Android app that adds a floating mic button above the keyboard. Tap it, talk, tap again, and the text appears in the field you were typing in. You keep using your normal keyboard (Gboard or any other).

The recording goes to OpenWhispr Patched on your Mac over Tailscale, which runs it through the same pipeline as a hotkey dictation: your speech-to-text model, cleanup prompt, dictionary and snippets. The phone has no settings of its own for any of that. Change them in OpenWhispr on the Mac.

## What you need

- A Mac running **OpenWhispr Patched** with mobile dictation turned on (below). It has to be awake and the app open, so turn on **Launch at login** in its settings.
- Tailscale on the Mac and the phone, in the same tailnet.
- Android 11 or newer.

## Set up the Mac

Build and install OpenWhispr Patched as described in [openwhispr-patch/README.md](../openwhispr-patch/README.md). Then, from the repo root:

```sh
openwhispr-patch/enable-mobile-dictation.sh
```

It creates `~/.openwhispr/mobile-dictation.json` with a random token, publishes the endpoint to your tailnet on `https://<mac>.<tailnet>.ts.net:8450` with Tailscale Serve, and prints the URL, the token and a `curl` to check it. Restart OpenWhispr Patched afterwards so it reads the file.

For a shorter URL without a port, like `https://whispr.<tailnet>.ts.net`, run it with `svc:whispr` to publish a Tailscale Service instead. That needs some setup in the Tailscale admin console first, described in [A short URL with a Tailscale Service](../openwhispr-patch/README.md#a-short-url-with-a-tailscale-service). M1 is set up this way.

The endpoint only listens on 127.0.0.1, and Tailscale Serve only answers devices in your tailnet. Every request also needs the token.

## Build and install the app

Building needs JDK 17 and the Android SDK (platform 36). Point `android/local.properties` at the SDK with `sdk.dir=/path/to/sdk`, or set `ANDROID_HOME`. Then:

```sh
cd android
./gradlew assembleDebug
```

The APK is at `app/build/outputs/apk/debug/app-debug.apk`. Install it either way:

- **adb** (simplest): turn on wireless debugging on the phone, `adb pair` and `adb connect` once, then `adb install -r app/build/outputs/apk/debug/app-debug.apk`.
- **The file**: copy the APK to the phone and open it. Android asks you to allow installs from that app. Android then blocks the accessibility permission for apps installed this way. The fix is in step 2 below.

## Set up the phone

Open **Whispr Mic**. The Setup section at the top shows what's still missing.

1. **Allow microphone and notifications.** Notifications are needed to see error messages, because Android hides messages from background apps without them.
2. **Turn on the mic button in Accessibility.** This opens Android's accessibility settings. Find Whispr Mic and turn it on. If the switch is greyed out and says "Restricted setting", go to Settings → Apps → Whispr Mic, open the ⋮ menu, choose **Allow restricted settings**, and try again.
3. **Server URL and token.** Enter the URL and token that `enable-mobile-dictation.sh` printed, then tap **Save and test connection**. It should say "Connected to OpenWhispr" and show which models and prompt OpenWhispr uses.

## Using it

Tap into any text field. The mic appears above the keyboard, on the right.

- **Dictate.** Tap the mic and start talking; the button turns red. Tap it again to send. A spinner shows while OpenWhispr works, then the text is typed at the cursor. If you had text selected, the dictation replaces it.
- **Cancel.** Tap the small ✕ next to the button. It cancels a recording, or stops waiting while OpenWhispr is still working.
- **If sending fails**, the button turns orange and keeps the recording. Tap it to try again, or tap ✕ to throw the recording away. The recording is dropped if you move to a text field in another app, or after 10 minutes, so it can't end up somewhere you didn't mean.
- **Move the button.** Hold it until it turns blue, then drag it. It stays where you drop it, in every app, at the same height above the keyboard. A quick swipe doesn't move it, so you can't move it by accident.
- **Hide it in an app.** While you're holding the button, a "Hide in <app>" bar appears at the top of the screen. Drop the button on it. Bring it back in Whispr Mic → Choose apps.

The first time the button appears, a short tip explains tap and hold.

## Settings

- **Where the mic shows.** "In every app" is the default; **Choose apps to hide it in** opens a searchable list of your apps. "Only in apps I choose" flips it around, so the mic shows in the apps you tick and nowhere else. Each mode keeps its own list. The mic never shows in password fields or in Whispr Mic itself.
- **Lock it in place.** Holding the button no longer picks it up.
- **Move it back to the default spot.** Puts the button back on the right, just above the keyboard.

## Troubleshooting

**"Can't find host. Is Tailscale on?" or "Can't connect".** Tailscale is off on the phone, or the Mac is asleep or offline. With a Tailscale Service, the Mac may also not be approved as its host yet; see the Service setup. Check with the `curl` that `enable-mobile-dictation.sh` printed, from another device on the tailnet.

**"The token was refused".** The token in the app doesn't match `~/.openwhispr/mobile-dictation.json` on the Mac. Run `enable-mobile-dictation.sh` again to print it.

**"Server answered 502" in the connection test.** Tailscale reached the Mac, but nothing listens behind it: OpenWhispr Patched isn't open, or it was already running when you created the config file. Open or restart it.

**"OpenWhispr's dictation window isn't running".** The app is starting up or stuck. Wait a moment, or restart it.

**"Couldn't type into the field. The text is on the clipboard."** Some apps don't let accessibility services set their text. Paste it with a long-press in the field.

**"The recording was silent."** Android gave the app silence instead of audio, usually because another app holds the microphone.

**No mic button.** Check that the accessibility service is still on; Android turns it off when the app is force-stopped or updated through some installers. Also check that the app isn't hidden under Where the mic shows.

## How it works

- `MicAccessibilityService` watches for a focused, editable field while the keyboard is open, and shows the button in an accessibility overlay window. The window can't take focus, so the keyboard and field stay as they are.
- Android only lets a background app record from a microphone-type foreground service, so a tap starts `RecorderService`. It records 16 kHz mono WAV with `AudioRecord` and shows a "Listening" notification while it does.
- `DictationClient` posts the WAV to `/v1/dictate` with the token and waits for the text (up to two minutes).
- The text is inserted with `ACTION_SET_TEXT`, computed from the field's current text and cursor so it lands at the cursor with sensible spacing (`TextInsertion.kt`). Some apps, Telegram and WhatsApp among them, report their "Message" placeholder as the text of an empty field; a field that reports no cursor counts as empty, so the placeholder isn't kept. The last recording stays in the app's cache, so a failed send can be retried.

The server side is in [openwhispr-patch/README.md](../openwhispr-patch/README.md#phone-dictation).

## Files

| File | What it does |
| --- | --- |
| `MicAccessibilityService.kt` | finds the focused field, shows and moves the button, runs a dictation |
| `MicButton.kt` | the button, the ✕ next to it, and the "Hide in" bar |
| `RecorderService.kt`, `WavRecorder.kt` | records the microphone to a WAV file |
| `DictationClient.kt` | talks to OpenWhispr |
| `TextInsertion.kt` | works out the field's new text and cursor |
| `SettingsActivity.kt`, `AppPickerActivity.kt` | the settings screen and the app list |
| `Prefs.kt` | stored settings |

Run the unit tests with `./gradlew testDebugUnitTest`.
