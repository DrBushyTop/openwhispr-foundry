"""Speech to text: OpenWhispr's model/language/dictionary fields -> Azure
Speech fast transcription (api-version 2025-10-15) with enhanced mode."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import uuid

from foundry import log, open_azure

SPEECH_API_VERSION = "2025-10-15"

# Defaults are my resources; set FOUNDRY_MAI_ENDPOINT and FOUNDRY_LLM_SPEECH_ENDPOINT
# (env or config.env) to your Speech or Foundry resource URLs. They can be the same
# resource if its region offers both MAI-Transcribe and LLM Speech.
#
# opencode-lpqn3wrkin5y2 (swedencentral) runs MAI-Transcribe but not LLM Speech.
# LLM Speech needs a supported region (centralindia, eastus, northeurope,
# southeastasia, westus, westus2), so it goes to opencode-neu-lpqn3wrkin5y2
# (northeurope, same resource group).
MAI_ENDPOINT = os.environ.get(
    "FOUNDRY_MAI_ENDPOINT", "https://opencode-lpqn3wrkin5y2.cognitiveservices.azure.com"
)
LLM_SPEECH_ENDPOINT = os.environ.get(
    "FOUNDRY_LLM_SPEECH_ENDPOINT", "https://opencode-neu-lpqn3wrkin5y2.cognitiveservices.azure.com"
)
DEFAULT_STT_MODEL = os.environ.get("FOUNDRY_DEFAULT_MODEL", "mai-transcribe-2")
# "clean" drops fillers (um, uh), which is what you want for dictation. "verbatim" keeps them.
MAI_STYLE = os.environ.get("FOUNDRY_MAI_STYLE", "clean")


def resolve_model(model: str) -> tuple[str, str]:
    """Map OpenWhispr's Speech-to-Text Model field to (backend, upstream model name)."""
    m = (model or DEFAULT_STT_MODEL).strip().lower()
    if m in ("llm-speech", "llm", "llmspeech"):
        return "llm-speech", ""
    mai = re.fullmatch(r"mai-transcribe-([\d.]+)", m)
    if mai:
        return "mai", f"MAI-Transcribe-{mai.group(1)}"
    if m == "mai":
        return "mai", "MAI-Transcribe-2"
    raise ValueError(f"unknown model {model!r}; use 'mai-transcribe-2' or 'llm-speech'")


def parse_phrases(prompt: str | None) -> list[str]:
    """OpenWhispr sends the custom dictionary as 'term1, term2, ...'."""
    if not prompt:
        return []
    return [p.strip() for p in prompt.split(",") if p.strip()]


# LLM Speech rejects bare ISO codes ("en") with InvalidLocale; it wants a full
# locale. MAI-Transcribe takes the bare code. These are the LLM Speech languages.
LLM_SPEECH_LOCALES = {
    "ar": "ar-SA", "zh": "zh-CN", "cs": "cs-CZ", "da": "da-DK", "nl": "nl-NL",
    "en": "en-US", "fi": "fi-FI", "fr": "fr-FR", "de": "de-DE", "el": "el-GR",
    "he": "he-IL", "hi": "hi-IN", "hu": "hu-HU", "id": "id-ID", "it": "it-IT",
    "ja": "ja-JP", "ko": "ko-KR", "nb": "nb-NO", "no": "nb-NO", "pl": "pl-PL",
    "pt": "pt-BR", "ru": "ru-RU", "es": "es-ES", "sv": "sv-SE", "th": "th-TH",
    "tr": "tr-TR",
}


def resolve_locale(backend: str, language: str | None) -> str | None:
    if not language or language.lower() == "auto":
        return None
    if backend == "mai":
        return language.split("-")[0].lower()
    if "-" in language:
        return language
    # Unknown language: let LLM Speech auto-detect instead of failing the request.
    return LLM_SPEECH_LOCALES.get(language.lower())


def build_definition(backend: str, upstream_model: str, language: str | None, prompt: str | None) -> dict:
    definition: dict = {}
    if backend == "mai":
        definition["enhancedMode"] = {
            "enabled": True,
            "model": upstream_model,
            "modelOptions": {"transcribeStyle": MAI_STYLE},
        }
    else:
        definition["enhancedMode"] = {"enabled": True, "task": "transcribe"}
    locale = resolve_locale(backend, language)
    if locale:
        definition["locales"] = [locale]
    phrases = parse_phrases(prompt)
    if phrases:
        definition["phraseList"] = {"phrases": phrases}
    return definition


def encode_multipart(fields: dict[str, str], files: dict[str, tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            + value.encode() + b"\r\n"
        )
    for name, (filename, ctype, data) in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode()
            + data + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def extract_text(response: dict) -> str:
    phrases = response.get("combinedPhrases") or []
    return " ".join(p.get("text", "") for p in phrases).strip()


def extract_locale(response: dict) -> str | None:
    """Language Azure detected, from the longest phrase ("fi" for MAI, "fi-FI" for LLM Speech)."""
    phrases = [p for p in response.get("phrases") or [] if p.get("locale")]
    if not phrases:
        return None
    return max(phrases, key=lambda p: p.get("durationMilliseconds") or 0)["locale"]


# Upload format per backend, all 16 kHz mono. MAI-Transcribe only takes WAV,
# MP3 or FLAC. 48 kbps MP3 is ~5x smaller than WAV (81KB vs 430KB for 14s),
# which cut ~150ms of upload, and MAI's transcripts matched WAV exactly on
# English and Finnish clips (32k and below changed formatting details).
# LLM Speech is format-sensitive: MP3 turned "Azure Foundryyn" into
# "Asher Phone:ään" on every run, and WebM lost casing and punctuation,
# so it keeps lossless WAV.
UPLOAD_FORMATS = {
    "mai": ("mp3", "audio/mpeg", ["-b:a", "48k", "-f", "mp3"]),
    "llm-speech": ("wav", "audio/wav", ["-f", "wav"]),
}
# Bytes per second of audio in each upload format, to tell a clip's length from its size.
UPLOAD_BYTES_PER_S = {"mai": 6_000, "llm-speech": 32_000}

# Azure answers in a few seconds even for two minutes of audio (the slowest of
# 130 LLM Speech requests took 4.4 s), but now and then it takes the upload and
# never answers. Waiting the general 120 s for that kept the phone spinning for
# two minutes, so speech gets 15 s, plus a second for every 10 s of audio.
STT_TIMEOUT_S = 15

# What a dictation goes to when LLM Speech doesn't answer. Sending LLM Speech
# the same clip again didn't help: one 36 s clip went unanswered four times in a
# row, while other clips around it came back in under a second.
FALLBACK_MODEL = "mai-transcribe-2"


def stt_timeout(audio: bytes, backend: str) -> float:
    return STT_TIMEOUT_S + len(audio) / UPLOAD_BYTES_PER_S[backend] / 10


def convert_audio(input_path: str, backend: str) -> str:
    """Transcode for `backend` (see UPLOAD_FORMATS). ~30-40ms with ffmpeg.
    Returns the path to a new temp file; caller owns cleanup."""
    ext, _, args = UPLOAD_FORMATS[backend]
    fd, out_path = tempfile.mkstemp(suffix=f".{ext}")
    os.close(fd)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ar", "16000", "-ac", "1", *args, out_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        os.remove(out_path)
        raise
    return out_path


def encode_pcm(pcm: bytes, sample_rate: int, backend: str) -> bytes:
    """Encode raw mono PCM16 (from the realtime route) for `backend`, in memory."""
    _, _, args = UPLOAD_FORMATS[backend]
    out = subprocess.run(
        ["ffmpeg", "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
         "-ar", "16000", "-ac", "1", *args, "pipe:1"],
        input=pcm, check=True, capture_output=True,
    )
    return out.stdout


def recognize(audio: bytes, model: str, language: str | None, prompt: str | None) -> str:
    """Send already-encoded audio (see UPLOAD_FORMATS) to Azure and return the text."""
    return recognize_with_locale(audio, model, language, prompt)[0]


def recognize_with_locale(audio: bytes, model: str, language: str | None,
                          prompt: str | None) -> tuple[str, str | None]:
    """recognize(), plus the language Azure detected."""
    backend, upstream_model = resolve_model(model)
    endpoint = MAI_ENDPOINT if backend == "mai" else LLM_SPEECH_ENDPOINT
    url = f"{endpoint.rstrip('/')}/speechtotext/transcriptions:transcribe?api-version={SPEECH_API_VERSION}"
    definition = build_definition(backend, upstream_model, language, prompt)
    ext, mime, _ = UPLOAD_FORMATS[backend]
    body, ctype = encode_multipart(
        {"definition": json.dumps(definition)},
        {"audio": (f"audio.{ext}", mime, audio)},
    )
    started = time.monotonic()
    with open_azure(url, body, ctype, stt_timeout(audio, backend)) as resp:
        result = json.loads(resp.read())
        reused = resp.reused
    text = extract_text(result)
    log(f"stt {backend}{'/' + upstream_model if upstream_model else ''} "
        f"audio={result.get('durationMilliseconds', '?')}ms upload={len(audio) // 1024}KB "
        f"conn={'reused' if reused else 'new'} "
        f"upstream={time.monotonic() - started:.2f}s chars={len(text)}")
    return text, extract_locale(result)


def transcribe(audio_path: str, model: str, language: str | None, prompt: str | None) -> str:
    """Transcribe a file already converted by convert_audio(). When Azure doesn't
    answer, LLM Speech hands the clip to FALLBACK_MODEL, and MAI-Transcribe tries
    once more on a new connection. The realtime route retries on its own."""
    with open(audio_path, "rb") as f:
        audio = f.read()
    try:
        return recognize(audio, model, language, prompt)
    except TimeoutError as exc:
        if resolve_model(model)[0] != "llm-speech":
            log(f"stt: {exc}, upload={len(audio) // 1024}KB; trying once more")
            return recognize(audio, model, language, prompt)
        log(f"stt: LLM Speech: {exc}, upload={len(audio) // 1024}KB; trying {FALLBACK_MODEL}")
    # LLM Speech's upload is WAV; MAI-Transcribe takes MP3.
    mp3_path = convert_audio(audio_path, resolve_model(FALLBACK_MODEL)[0])
    try:
        with open(mp3_path, "rb") as f:
            return recognize(f.read(), FALLBACK_MODEL, language, prompt)
    except TimeoutError as exc:
        raise TimeoutError(f"Azure didn't answer, with LLM Speech or {FALLBACK_MODEL}") from exc
    finally:
        os.remove(mp3_path)
