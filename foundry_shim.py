#!/usr/bin/env python3
"""Local shim: OpenWhispr self-hosted endpoints -> Azure AI Foundry.

Routes, all authenticated with the local az CLI login (no API keys). Every
path also works with a /v1 prefix.

  POST /audio/transcriptions   Speech-to-Text -> Self-Hosted          (stt.py)
      OpenAI-style multipart in, {"text": "..."} out. The Model field picks
      mai-transcribe-2 (default) or llm-speech.

  GET  /realtime (WebSocket)   OpenAI Realtime transcription            (realtime.py)
      For meetings and streaming dictation through a patched OpenWhispr
      whose OpenAI Realtime URL points here. Pause-cut segments go to MAI.

  POST /chat/completions, GET /models   Language Models -> Self-Hosted  (chat.py)
      Forwards to the Foundry /openai/v1 endpoint, streaming included.

foundry.py holds the az token, connection pool and logging. websocket.py is a
minimal RFC 6455 server. Standard library only; needs Python 3.8+, ffmpeg and az.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chat
import realtime
import stt
import websocket
from foundry import TENANT_ID, TOKEN, UpstreamError, log

HOST = os.environ.get("SHIM_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHIM_PORT", "9447"))  # "WHIS" on a phone keypad
MAX_BODY_BYTES = 25 * 1024 * 1024


# parse_multipart_form and the transcription handler follow OpenWhispr's
# examples/custom-asr-shim/shim_template.py.

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
        path = route(self.path)
        if path == "/realtime" and websocket.is_upgrade_request(self.headers):
            self._realtime()
            return
        if path == "/models":
            # Only chat models: the Language Models panel lists these. The
            # Speech-to-Text panel takes a free-text Model field and never calls this.
            self._send_json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "azure-foundry"} for m in chat.CHAT_MODELS
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

    def _realtime(self) -> None:
        self.wfile.write(websocket.handshake_response(self.headers))
        self.wfile.flush()
        ws = websocket.WebSocket(self.rfile, self.wfile)
        label = f"{self.client_address[1]}"
        try:
            realtime.RealtimeSession(ws, label).run()
        finally:
            self.close_connection = True

    def _chat(self) -> None:
        raw = self._read_body()
        if raw is None:
            return
        try:
            body = chat.adapt_chat_body(json.loads(raw))
        except (ValueError, AttributeError):
            self._send_json(400, {"error": {"message": "body must be a JSON object"}})
            return
        model = body.get("model", "?")
        stream = bool(body.get("stream"))
        started = time.monotonic()
        try:
            resp, sent = chat.open_chat(body)
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
            backend, _ = stt.resolve_model(model)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        suffix = os.path.splitext(filename)[1] or ".webm"
        fd, in_path = tempfile.mkstemp(suffix=suffix)
        upload_path = None
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(file_bytes)
            upload_path = stt.convert_audio(in_path, backend)
            text = stt.transcribe(upload_path, model, language, prompt)
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
    log(f"  stt mai-transcribe-2 -> {stt.MAI_ENDPOINT} (style={stt.MAI_STYLE})")
    log(f"  stt llm-speech       -> {stt.LLM_SPEECH_ENDPOINT}")
    log(f"  stt default model: {stt.DEFAULT_STT_MODEL}")
    log(f"  realtime ws://localhost:{PORT}/v1/realtime -> {realtime.REALTIME_MODEL}")
    log(f"  chat -> {chat.CHAT_ENDPOINT} (listed: {', '.join(chat.CHAT_MODELS)})")
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
