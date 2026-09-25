#!/usr/bin/env python3
"""Local shim: OpenWhispr self-hosted endpoints -> Azure AI Foundry.

Two routes, both authenticated with the local az CLI login (no API keys):

  POST /audio/transcriptions   Speech-to-Text -> Self-Hosted
      OpenAI-style multipart in, {"text": "..."} out. Audio is transcoded to
      16 kHz mono MP3 (MAI) or WAV (LLM Speech) and sent to Azure Speech fast transcription
      (api-version 2025-10-15) with enhanced mode. OpenWhispr's Model field
      picks the backend:
        mai-transcribe-2 (default; also mai-transcribe-1.5)   MAI-Transcribe
        llm-speech                                            LLM Speech

  POST /chat/completions, GET /models   Language Models -> Self-Hosted
      Forwards OpenAI chat completions to the Foundry /openai/v1 endpoint,
      streaming included. OpenWhispr owns the prompts; the shim only rewrites
      the parameters Azure rejects (see adapt_chat_body).

Every path also works with a /v1 prefix. The tenant is hardcoded so a different
default az account can't send tokens for the wrong directory.

Standard library only. Needs Python 3.8+, ffmpeg and az on PATH.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TENANT_ID = "7135bcf1-5a12-4e82-ad41-c263afa243e8"  # huuhka.net
TOKEN_RESOURCE = "https://cognitiveservices.azure.com"
SPEECH_API_VERSION = "2025-10-15"

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

CHAT_ENDPOINT = os.environ.get(
    "FOUNDRY_CHAT_ENDPOINT", "https://opencode-lpqn3wrkin5y2.openai.azure.com/openai/v1"
)
# Deployment names listed on GET /models. Requests for other deployments still pass through.
CHAT_MODELS = [m.strip() for m in os.environ.get(
    "FOUNDRY_CHAT_MODELS", "gpt-5.4-mini,gpt-5.4-nano,gpt-6-luna"
).split(",") if m.strip()]

HOST = os.environ.get("SHIM_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHIM_PORT", "9447"))  # "WHIS" on a phone keypad
MAX_BODY_BYTES = 25 * 1024 * 1024
UPSTREAM_TIMEOUT_S = 120
# Azure closed idle connections somewhere between 90s and 180s in testing, so
# older ones are dropped instead of tried. A stale one fails instantly anyway.
POOL_MAX_IDLE_S = 100


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- auth

class AzCliToken:
    """Caches an az CLI access token until 5 minutes before it expires."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token = ""
        self._expires_at = 0.0

    def get(self) -> str:
        with self._lock:
            if time.time() < self._expires_at - 300:
                return self._token
            out = subprocess.run(
                ["az", "account", "get-access-token",
                 "--tenant", TENANT_ID,
                 "--resource", TOKEN_RESOURCE,
                 "--query", "{t:accessToken,e:expires_on}", "-o", "json"],
                check=True, capture_output=True, text=True,
            )
            data = json.loads(out.stdout)
            self._token = data["t"]
            self._expires_at = float(data["e"])
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._expires_at = 0.0


TOKEN = AzCliToken()


class UpstreamError(Exception):
    def __init__(self, status: int, body: bytes) -> None:
        super().__init__(f"Azure HTTP {status}: {body[:500].decode('utf-8', 'replace')}")
        self.status = status
        self.body = body


# ---------------------------------------------------------------- connection reuse
# A fresh TLS connection to Azure costs ~170ms (measured: tcp ~55ms + tls ~110ms).
# Keeping connections open between requests skips that on every warm request.

class ConnectionPool:
    """Idle HTTPS connections per host. Thread-safe; one request per connection at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle: dict[str, list[tuple[http.client.HTTPSConnection, float]]] = {}

    def get(self, host: str) -> tuple[http.client.HTTPSConnection, bool]:
        """Return (connection, reused)."""
        with self._lock:
            conns = self._idle.get(host, [])
            while conns:
                conn, last_used = conns.pop()
                if time.monotonic() - last_used < POOL_MAX_IDLE_S:
                    return conn, True
                conn.close()
        return http.client.HTTPSConnection(host, timeout=UPSTREAM_TIMEOUT_S), False

    def put(self, host: str, conn: http.client.HTTPSConnection) -> None:
        with self._lock:
            self._idle.setdefault(host, []).append((conn, time.monotonic()))


POOL = ConnectionPool()

# Errors that mean an idle pooled connection was closed by Azure before we used it.
STALE_CONNECTION_ERRORS = (
    http.client.RemoteDisconnected, http.client.CannotSendRequest,
    http.client.BadStatusLine, ConnectionError, ssl.SSLError,
)


class PooledResponse:
    """An http.client response that returns its connection to the pool on close,
    but only if the body was fully read and the server didn't ask to close."""

    def __init__(self, host: str, conn: http.client.HTTPSConnection,
                 resp: http.client.HTTPResponse, reused: bool) -> None:
        self._host, self._conn, self._resp = host, conn, resp
        self.status = resp.status
        self.headers = resp.headers
        self.reused = reused

    def read(self) -> bytes:
        return self._resp.read()

    def __iter__(self):
        return iter(self._resp)  # yields lines as they arrive; used for SSE streams

    def close(self) -> None:
        if self._resp.isclosed() and not self._resp.will_close:
            POOL.put(self._host, self._conn)
        else:
            self._resp.close()
            self._conn.close()

    def __enter__(self) -> "PooledResponse":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def send_pooled(url: str, body: bytes, headers: dict[str, str]) -> PooledResponse:
    parts = urllib.parse.urlsplit(url)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    while True:
        conn, reused = POOL.get(parts.netloc)
        try:
            conn.request("POST", path, body=body, headers=headers)
            return PooledResponse(parts.netloc, conn, conn.getresponse(), reused)
        except STALE_CONNECTION_ERRORS:
            conn.close()
            if not reused:
                raise
            # Azure closed the idle connection; take another one or open a fresh one.


def open_azure(url: str, body: bytes, content_type: str) -> PooledResponse:
    """POST with the az CLI token and return the open response.
    Retries once with a fresh token on 401. Raises UpstreamError on other errors."""
    for attempt in (1, 2):
        resp = send_pooled(url, body, {
            "Content-Type": content_type,
            "Authorization": f"Bearer {TOKEN.get()}",
        })
        if resp.status < 400:
            return resp
        with resp:
            detail = resp.read()
        if resp.status == 401 and attempt == 1:
            TOKEN.invalidate()  # token revoked or clock skew; fetch a fresh one once
            continue
        raise UpstreamError(resp.status, detail)
    raise UpstreamError(401, b"Azure rejected the token twice")


# ---------------------------------------------------------------- speech to text

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


def transcribe(audio_path: str, model: str, language: str | None, prompt: str | None) -> str:
    backend, upstream_model = resolve_model(model)
    endpoint = MAI_ENDPOINT if backend == "mai" else LLM_SPEECH_ENDPOINT
    url = f"{endpoint.rstrip('/')}/speechtotext/transcriptions:transcribe?api-version={SPEECH_API_VERSION}"
    definition = build_definition(backend, upstream_model, language, prompt)
    with open(audio_path, "rb") as f:
        audio = f.read()
    ext, mime, _ = UPLOAD_FORMATS[backend]
    body, ctype = encode_multipart(
        {"definition": json.dumps(definition)},
        {"audio": (f"audio.{ext}", mime, audio)},
    )
    started = time.monotonic()
    with open_azure(url, body, ctype) as resp:
        result = json.loads(resp.read())
        reused = resp.reused
    text = extract_text(result)
    log(f"stt {backend}{'/' + upstream_model if upstream_model else ''} "
        f"audio={result.get('durationMilliseconds', '?')}ms upload={len(audio) // 1024}KB "
        f"conn={'reused' if reused else 'new'} "
        f"upstream={time.monotonic() - started:.2f}s chars={len(text)}")
    return text


# ---------------------------------------------------------------- chat completions

def adapt_chat_body(body: dict) -> dict:
    """Rewrite what OpenWhispr's self-hosted (llama.cpp-style) requests carry
    into what Azure OpenAI accepts. Everything else passes through untouched.

    - max_tokens: gpt-5+ on Azure rejects it; renamed to max_completion_tokens.
    - reasoning {effort}, think, thinking, chat_template_kwargs: OpenWhispr's
      "Disable thinking output" hints for Ollama/vLLM. Azure 400s on them.
      They become reasoning_effort, so OpenWhispr's toggle still decides.
    """
    body = dict(body)
    if "max_tokens" in body:
        tokens = body.pop("max_tokens")
        body.setdefault("max_completion_tokens", tokens)

    effort = None
    reasoning = body.pop("reasoning", None)
    if isinstance(reasoning, dict):
        if reasoning.get("effort"):
            effort = reasoning["effort"]
        elif reasoning.get("enabled") is False:
            effort = "none"
    if body.pop("think", None) is False:
        effort = "none"
    thinking = body.pop("thinking", None)
    if isinstance(thinking, dict) and thinking.get("type") == "disabled":
        effort = "none"
    kwargs = body.pop("chat_template_kwargs", None)
    if isinstance(kwargs, dict) and kwargs.get("enable_thinking") is False:
        effort = effort or "none"
    if effort and "reasoning_effort" not in body:
        body["reasoning_effort"] = effort
    return body


def rejected_param(error_body: bytes) -> str | None:
    """Name of the parameter Azure rejected, if the 400 says so."""
    try:
        err = json.loads(error_body).get("error", {})
    except (ValueError, AttributeError):
        return None
    param = err.get("param")
    if param and param not in ("model", "messages"):
        return param
    return None


def open_chat(body: dict):
    """Open a chat completion. If Azure rejects a parameter by name
    (e.g. temperature on a model that doesn't take it), drop it and retry."""
    url = f"{CHAT_ENDPOINT.rstrip('/')}/chat/completions"
    for _ in range(4):
        try:
            return open_azure(url, json.dumps(body).encode(), "application/json"), body
        except UpstreamError as exc:
            param = rejected_param(exc.body) if exc.status == 400 else None
            if not param or param not in body:
                raise
            log(f"chat {body.get('model')}: Azure rejected '{param}', retrying without it")
            body = {k: v for k, v in body.items() if k != param}
    raise UpstreamError(400, b'{"error": {"message": "too many rejected parameters"}}')


# ---------------------------------------------------------------- local HTTP server
# parse_multipart_form / convert_audio / the transcription handler follow
# OpenWhispr's examples/custom-asr-shim/shim_template.py.

def parse_multipart_form(body: bytes, content_type: str) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    if not m:
        raise ValueError("missing multipart boundary in Content-Type")
    delim = b"--" + m.group(1).strip().encode()
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for chunk in body.split(delim):
        if not chunk or chunk.startswith(b"--"):
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        if b"\r\n\r\n" not in chunk:
            continue
        raw_headers, content = chunk.split(b"\r\n\r\n", 1)
        disposition = ""
        for line in raw_headers.decode("utf-8", "replace").split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                disposition = line
        name_match = re.search(r'name="([^"]*)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        file_match = re.search(r'filename="([^"]*)"', disposition)
        if file_match is not None:
            files[name] = (file_match.group(1), content)
        else:
            fields[name] = content.decode("utf-8", "replace")
    return fields, files


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


def route(path: str) -> str:
    path = path.split("?", 1)[0].rstrip("/")
    if path.startswith("/v1/"):
        path = path[3:]
    return path


class ShimHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        log(f"{self.address_string()} {format % args}")

    def _send_json(self, status: int, payload: dict) -> None:
        self._send_bytes(status, json.dumps(payload).encode("utf-8"))

    def _send_bytes(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return None
        if length <= 0:
            self._send_json(400, {"error": "empty body"})
            return None
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"error": "request body too large"})
            return None
        return self.rfile.read(length)

    def do_GET(self) -> None:  # noqa: N802
        if route(self.path) == "/models":
            # Only chat models: the Language Models panel lists these. The
            # Speech-to-Text panel takes a free-text Model field and never calls this.
            self._send_json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "azure-foundry"} for m in CHAT_MODELS
            ]})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = route(self.path)
        if path == "/audio/transcriptions":
            self._transcription()
        elif path == "/chat/completions":
            self._chat()
        else:
            self._send_json(404, {"error": "not found"})

    def _chat(self) -> None:
        raw = self._read_body()
        if raw is None:
            return
        try:
            body = adapt_chat_body(json.loads(raw))
        except (ValueError, AttributeError):
            self._send_json(400, {"error": {"message": "body must be a JSON object"}})
            return
        model = body.get("model", "?")
        stream = bool(body.get("stream"))
        started = time.monotonic()
        try:
            resp, sent = open_chat(body)
        except UpstreamError as exc:
            log(f"chat {model} failed: {exc}")
            # Pass Azure's error through so OpenWhispr can show or react to it.
            self._send_bytes(exc.status, exc.body or b"{}")
            return
        except Exception as exc:
            log(f"chat {model} failed: {exc}")
            self._send_json(502, {"error": {"message": f"shim: {exc}"}})
            return

        with resp:
            if not stream:
                data = resp.read()
                usage = ""
                try:
                    u = json.loads(data).get("usage") or {}
                    usage = f" tokens={u.get('prompt_tokens')}->{u.get('completion_tokens')}"
                except ValueError:
                    pass
                log(f"chat {model} effort={sent.get('reasoning_effort', '-')} "
                    f"conn={'reused' if resp.reused else 'new'} "
                    f"upstream={time.monotonic() - started:.2f}s{usage}")
                self._send_bytes(200, data, resp.headers.get("Content-Type", "application/json"))
                return
            # Stream SSE line by line. No Content-Length; the connection closes at the end.
            self.send_response(200)
            self.send_header("Content-Type", resp.headers.get("Content-Type", "text/event-stream"))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            first = None
            try:
                for line in resp:
                    if first is None:
                        first = time.monotonic() - started
                    self.wfile.write(line)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                log(f"chat {model}: client disconnected mid-stream")
            self.close_connection = True
            log(f"chat {model} stream effort={sent.get('reasoning_effort', '-')} "
                f"conn={'reused' if resp.reused else 'new'} "
                f"first_byte={first or 0:.2f}s total={time.monotonic() - started:.2f}s")

    def _transcription(self) -> None:
        raw = self._read_body()
        if raw is None:
            return
        try:
            fields, files = parse_multipart_form(raw, self.headers.get("Content-Type", ""))
        except ValueError as exc:
            self._send_json(400, {"error": f"bad multipart: {exc}"})
            return
        if "file" not in files:
            self._send_json(400, {"error": "missing 'file' field"})
            return

        filename, file_bytes = files["file"]
        model = fields.get("model", "")
        language = fields.get("language") or None
        prompt = fields.get("prompt") or None
        try:
            backend, _ = resolve_model(model)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        suffix = os.path.splitext(filename)[1] or ".webm"
        fd, in_path = tempfile.mkstemp(suffix=suffix)
        upload_path = None
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(file_bytes)
            upload_path = convert_audio(in_path, backend)
            text = transcribe(upload_path, model, language, prompt)
            self._send_json(200, {"text": text, "object": "transcription"})
        except FileNotFoundError as exc:
            self._send_json(500, {"error": f"{exc.filename or 'ffmpeg/az'} not found on PATH"})
        except subprocess.CalledProcessError as exc:
            what = "az CLI token fetch failed (run `az login`)" if exc.cmd and exc.cmd[0] == "az" \
                else "ffmpeg failed to transcode audio"
            log(f"error: {what}: {getattr(exc, 'stderr', '')}")
            self._send_json(500, {"error": what})
        except Exception as exc:
            log(f"error: {exc}")
            self._send_json(502, {"error": f"transcription failed: {exc}"})
        finally:
            for path in (in_path, upload_path):
                if path and os.path.exists(path):
                    os.remove(path)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), ShimHandler)
    server.daemon_threads = True
    log(f"Foundry shim on http://localhost:{PORT} (tenant {TENANT_ID})")
    log(f"  stt mai-transcribe-2 -> {MAI_ENDPOINT} (style={MAI_STYLE})")
    log(f"  stt llm-speech       -> {LLM_SPEECH_ENDPOINT}")
    log(f"  stt default model: {DEFAULT_STT_MODEL}")
    log(f"  chat -> {CHAT_ENDPOINT} (listed: {', '.join(CHAT_MODELS)})")
    try:
        TOKEN.get()  # fail fast if az isn't logged in to the tenant
        log("  az CLI token: ok")
    except Exception as exc:
        log(f"  az CLI token: FAILED ({exc}). Run: az login --tenant {TENANT_ID}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
