# Whispr Mic privacy policy

Whispr Mic is a dictation app. It sends your speech to an OpenWhispr server that you run and set up yourself, and types the text it gets back into the text field you're in. Whispr Mic has no server of its own, no accounts, no ads and no analytics.

## What the app accesses

- **Your voice.** When you tap the mic, the app records audio until you tap it again.
- **The text field you're typing in.** The app uses Android's accessibility service to see which app and text field are focused, and the text in that field. It does this only in the apps you choose in its settings, never in password fields. It uses this only to show the mic button, insert your dictation and notice words you correct right after a dictation. It doesn't read anything else on screen or control your phone.
- **Your list of apps.** The app lists your installed apps so you can choose where the mic appears. This list stays on the phone.

## What leaves the phone, and where it goes

Everything the app sends goes only to the server address you enter in its settings, over HTTPS, with the token you enter:

- the audio recording of each dictation;
- after a dictation, the dictated text and your corrections to it, so the server can learn the words you use;
- dictionary words you add, edit or remove in the app.

That server is yours, not the developer's. It may pass this data on to the speech recognition and text cleanup services you've set it up to use, under those services' own terms. Nothing is sent to the developer of Whispr Mic or anyone else.

## What stays on the phone

The server address, token and settings stay in the app's private storage. A recording is kept in the app's cache only until it's sent or discarded, then deleted. Uninstalling the app removes all of it.

## Contact

Questions about this policy: open an issue at <https://github.com/DrBushyTop/openwhispr-foundry/issues>.

Last updated: October 20, 2026
