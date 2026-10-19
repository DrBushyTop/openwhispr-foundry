# Whispr Mic

An Android app that adds a floating mic button above the keyboard. Tap it, talk, tap again, and the text appears in the field you were typing in. You keep using your normal keyboard (Gboard or any other).

The recording goes to OpenWhispr Patched on your Mac over Tailscale, which runs it through the same pipeline as a hotkey dictation: your speech-to-text model, cleanup prompt, dictionary and snippets. The phone has no settings of its own for any of that. Change them in OpenWhispr on the Mac. The dictionary is the exception: you can also edit it from the phone, and the app adds words you correct, so you don't need to open the Mac for it.

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

Open **Whispr Mic**. It opens on the **Setup** tab until everything is in place; the tab has a red dot while something is missing, and its Status list shows what.

1. **Allow microphone and notifications.** Notifications are needed to see error messages, because Android hides messages from background apps without them.
2. **Turn on the mic button in Accessibility.** This opens Android's accessibility settings. Find Whispr Mic and turn it on. If the switch is greyed out and says "Restricted setting", go to Settings → Apps → Whispr Mic, open the ⋮ menu, choose **Allow restricted settings**, and try again.
3. **Server URL and token.** Enter the URL and token that `enable-mobile-dictation.sh` printed, then tap **Save and test connection**. It should say "Connected to OpenWhispr" and show which models and prompt OpenWhispr uses.
4. **Choose the apps.** The mic only shows in the apps you tick in the **Apps** tab, and none are ticked at first. That tab has a red dot until you tick one.

## Using it

Tap into a text field in one of your chosen apps. The mic appears above the keyboard, on the right.

- **Dictate.** Tap the mic and start talking; the button turns red. Tap it again to send. A spinner shows while OpenWhispr works, then the text is typed at the cursor. If you had text selected, the dictation replaces it. The screen stays on until the text is in, so it doesn't lock halfway through a long dictation.
- **Cancel.** Tap the small ✕ next to the button. It cancels a recording, or stops waiting while OpenWhispr is still working.
- **If sending fails**, the button turns orange and keeps the recording. Tap it to try again, or tap ✕ to throw the recording away. The recording is dropped if you move to a text field in another app, or after 10 minutes, so it can't end up somewhere you didn't mean.
- **If you leave the field** before the text arrives, nothing is typed anywhere, and the button turns orange the same way. Tap into the field you want and tap the button to send the recording again.
- **Move the button.** Hold it until it turns blue, then drag it. It stays where you drop it, in every app, at the same height above the keyboard. A quick swipe doesn't move it, so you can't move it by accident.
- **Hide it in an app.** While you're holding the button, a "Hide in <app>" bar appears at the top of the screen. Drop the button on it. Bring it back in Whispr Mic's **Apps** tab.

The first time the button appears, a short tip explains tap and hold.

### Learning from your corrections

Like OpenWhispr's auto-learn on the desktop, the app notices when you fix a misheard word. After a dictation goes in, it follows that field until you send the message, leave the field, or a minute passes. If you changed words, it sends the dictation and your edited version of it to OpenWhispr, which picks out the corrected words and adds them to the dictionary, marked as learned. Future dictations on the phone and the Mac then listen for them.

A toast and a notification say which words were added. **Undo** in the notification removes them again. Only the latest notification is kept; remove older words in the Dictionary tab.

OpenWhispr uses the same rules as on the desktop: only one-for-one word swaps that sound alike count, not rewrites, short or common words, or words already in the dictionary. The phone also skips changed endings, like "meeting" to "meetings", which are usually grammar or a Gboard suggestion rather than a misheard word.

Only the dictated text is sent, never the rest of the field. If you also changed the text around the dictation, nothing is sent for it.

Turn it off with **Learn from my corrections** in the Dictionary tab. OpenWhispr's own auto-learn setting on the Mac also applies; the tab says if that one is off.

### The dictionary

The **Dictionary** tab, where the app opens, lists OpenWhispr's dictionary, newest first, with learned words marked. Type in the field at the top to find a word; tap **Add** to add what you typed. Tap a word to fix its spelling or delete it. Changes are saved straight to OpenWhispr on the Mac, and apply to the next dictation.

## Settings

The app has three tabs. The two you come back to are first; the one you set once is last.

- **Dictionary.** See [The dictionary](#the-dictionary).
- **Apps.** Where the mic shows. "Only in apps I choose" is the default: the mic shows in the apps you tick and nowhere else. "In every app" flips it around, so the list is the apps to hide it in. Each mode keeps its own list. The list is searchable, with ticked apps first. The mic never shows in password fields or in Whispr Mic itself.
- **Setup.** What's still missing, the server URL and token, and the button's options: **Lock it in place** (holding the button no longer picks it up) and **Move it back to the default spot** (back on the right, just above the keyboard). A short how-to is at the bottom.

## Troubleshooting

**"Can't find host. Is Tailscale on?" or "Can't connect".** Tailscale is off on the phone, or the Mac is asleep or offline. With a Tailscale Service, the Mac may also not be approved as its host yet; see the Service setup. Check with the `curl` that `enable-mobile-dictation.sh` printed, from another device on the tailnet.

**"The token was refused".** The token in the app doesn't match `~/.openwhispr/mobile-dictation.json` on the Mac. Run `enable-mobile-dictation.sh` again to print it.

**"Server answered 502" in the connection test.** Tailscale reached the Mac, but nothing listens behind it: OpenWhispr Patched isn't open, or it was already running when you created the config file. Open or restart it.

**"OpenWhispr's dictation window isn't running".** The app is starting up or stuck. Wait a moment, or restart it.

**"You left the text field, so nothing was typed."** The field the dictation started in wasn't focused any more when the text came back, or the app is no longer one the mic may type into. Tap into the field and tap the orange button to send the recording again.

**"Couldn't type into the field. The text is on the clipboard."** Some apps don't let accessibility services set their text. Paste it with a long-press in the field.

**"The recording was silent."** Android gave the app silence instead of audio, usually because another app holds the microphone.

**"OpenWhispr Patched on the Mac needs updating for this"** in the Dictionary tab. The Mac app was built before the dictionary endpoints. Rebuild it with the current `mobile-dictation.patch`.

**No mic button.** Check that the accessibility service is still on; Android turns it off when the app is force-stopped or updated through some installers. Also check that the app is ticked in the Apps tab.

## How it works

- `MicAccessibilityService` watches for a focused, editable field while the keyboard is open, and shows the button in an accessibility overlay window. The window can't take focus, so the keyboard and field stay as they are.
- Android only lets a background app record from a microphone-type foreground service, so a tap starts `RecorderService`. It records 16 kHz mono WAV with `AudioRecord` and shows a "Listening" notification while it does.
- `DictationClient` posts the WAV to `/v1/dictate` with the token and waits for the text (up to two minutes). Answers are treated as untrusted: they're capped at 256 KB, and a dictation's text at 30,000 characters.
- Before the text goes in, the field is checked again: it must still be the focused field, editable, not a password field, and in an app the mic is allowed in. The same check runs when a dictation starts or is sent again. If it fails, the text is dropped, without going to the clipboard.
- The text is inserted with `ACTION_SET_TEXT`, computed from the field's current text and cursor so it lands at the cursor with sensible spacing (`TextInsertion.kt`). Some apps, Telegram and WhatsApp among them, report their "Message" placeholder as the text of an empty field; a field that reports no cursor counts as empty, so the placeholder isn't kept. The last recording stays in the app's cache only while it can still be sent again; it's deleted once the text is in, or when it's cancelled, discarded or dropped.
- After inserting, the service keeps the field and reads its text on each text-change event (`EditWatch` in `TextInsertion.kt`). An empty field means the message was sent, so the text from just before that counts. When the watch ends, `DictationClient` posts the dictation and the edited text to `/v1/learn`. Only the dictated part is sent, and only while the text around it is unchanged.

The server side is in [openwhispr-patch/README.md](../openwhispr-patch/README.md#phone-dictation).

## Files

| File | What it does |
| --- | --- |
| `MicAccessibilityService.kt` | finds the focused field, shows and moves the button, runs a dictation, follows corrections |
| `MicButton.kt` | the button, the ✕ next to it, and the "Hide in" bar |
| `RecorderService.kt`, `WavRecorder.kt` | records the microphone to a WAV file |
| `DictationClient.kt` | talks to OpenWhispr |
| `TextInsertion.kt` | works out the field's new text and cursor, and what the user changed afterwards |
| `LearnedWords.kt` | the "Added to the dictionary" notification and its Undo |
| `SettingsActivity.kt` | the settings, its tabs and the Setup tab |
| `DictionaryPage.kt`, `AppsPage.kt` | the Dictionary and Apps tabs |
| `Prefs.kt` | stored settings |

Run the unit tests with `./gradlew testDebugUnitTest`.
