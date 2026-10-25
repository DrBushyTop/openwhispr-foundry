# Third-party notices

This repository includes code and text from the projects below. Their notices are kept here as their licenses require.

## OpenWhispr

https://github.com/OpenWhispr/openwhispr

Used in `foundry_shim.py` (the multipart parser and transcription handler follow `examples/custom-asr-shim/shim_template.py`), `stt.py` (the ffmpeg conversion), `docs/cleanup-prompt.txt` (a modified copy of OpenWhispr's default cleanup prompt), and the OpenWhispr source lines in `openwhispr-patch/*.patch`.

MIT License

Copyright (c) 2024 OpenWhispr Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## OpenDictate

https://github.com/lebedev-nikita/open-dictate

Used in `android/app/src/main/java/net/huuhka/whisprmic/MicAccessibilityService.kt` (finding the focused text field) and `TextInsertion.kt` (selection clamping and placeholder detection), adapted from `OpenDictateAccessibilityService.kt` and `EditableTextComposer.kt`.

MIT License

Copyright (c) 2026 OpenDictate contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Material Icons

https://github.com/google/material-design-icons

The icons in `android/app/src/main/res/drawable/` use Material Icons by Google, licensed under the Apache License, Version 2.0. See [licenses/Apache-2.0.txt](licenses/Apache-2.0.txt).

## Gradle Wrapper

`android/gradlew`, `android/gradlew.bat` and `android/gradle/wrapper/` are from Gradle, Copyright © 2015-2021 the original authors, licensed under the Apache License, Version 2.0. See [licenses/Apache-2.0.txt](licenses/Apache-2.0.txt).
