"""OpenAI Realtime transcription protocol, backed by MAI batch transcription.

OpenWhispr's OpenAI Realtime client (meetings, and streaming dictation) streams
PCM16 as base64 `input_audio_buffer.append` events and expects server-side
turn detection. This module plays the server: an energy VAD cuts the stream at
pauses, each finished segment goes to stt.recognize() on a worker thread, and
the text comes back as `conversation.item.input_audio_transcription.completed`.

Cutting at pauses instead of fixed intervals keeps words whole. Each stream
gets a profile (PROFILES): a segment ends at a pause of pause_ms once it's at
least min_segment_ms long, or at any pause of long_pause_ms. Short segments
cost accuracy: in a real meeting, 3-5 s segments split "agendana on 5 | viime
vuoden budjetti" at a thinking pause, and a 2 s "öö" came back as Japanese.

Events sent:     session.created, session.updated,
                 input_audio_buffer.speech_started / speech_stopped / committed / cleared,
                 conversation.item.input_audio_transcription.completed / failed, error
Events handled:  session.update, input_audio_buffer.append / commit / clear
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import queue
import sys
import threading
import time
import uuid
from array import array
from collections import deque

import stt
from foundry import log
from websocket import OP_BINARY, ConnectionClosed, WebSocket

# OpenWhispr sends OpenAI model names (gpt-4o-mini-transcribe...). Anything the
# speech route doesn't know maps to this.
REALTIME_MODEL = os.environ.get("FOUNDRY_REALTIME_MODEL", "mai-transcribe-2")

FRAME_MS = 20
START_FRAMES = 3           # 60 ms of voiced frames opens a segment
PREFIX_MS = 300            # audio kept from before speech started
# OpenWhispr opens one socket per meeting source and sends a lower VAD threshold
# for system audio (0.3, MEETING_SYSTEM_VAD_THRESHOLD in ipcHandlers.js) than
# for the mic (0.6). That threshold is the only hint which stream is which.
SYSTEM_THRESHOLD_BELOW = 0.45
PROFILES = {
    # The mic is you. Long segments give MAI context for punctuation and
    # language detection; a thinking pause mid-sentence shouldn't end the turn.
    "mic": {"min_segment_ms": 10000, "pause_ms": 800, "long_pause_ms": 2000},
    # System audio is everyone else. OpenWhispr labels speakers per segment,
    # so cut sooner to keep one segment from spanning two speakers.
    "system": {"min_segment_ms": 5000, "pause_ms": 700, "long_pause_ms": 1500},
}
MAX_SEGMENT_MS = 30000     # hard cut, at the quietest frame in the last FORCE_CUT_WINDOW_MS
FORCE_CUT_WINDOW_MS = 3000
MIN_VOICED_MS = 200        # segments with less voiced audio are noise, not speech
MIN_RMS = float(os.environ.get("FOUNDRY_VAD_MIN_RMS", "300"))  # int16 units, ~-40 dBFS
NOISE_FACTOR = 3.0         # voiced = louder than 3x the running noise floor


def frame_rms(frame: bytes) -> float:
    samples = array("h", frame)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    return (sum(s * s for s in samples) / len(samples)) ** 0.5


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class Segmenter:
    """Energy VAD over a PCM16 stream. Calls on_start(start_ms) when speech
    begins and on_segment(pcm, start_ms, end_ms) when a segment is complete."""

    def __init__(self, sample_rate: int, profile: dict, on_start, on_segment) -> None:
        self.sample_rate = sample_rate
        self.pause_ms = profile["pause_ms"]
        self.min_segment_ms = profile["min_segment_ms"]
        self.long_pause_ms = profile["long_pause_ms"]
        self.on_start = on_start
        self.on_segment = on_segment
        self.frame_bytes = sample_rate * FRAME_MS // 1000 * 2
        self.reset()

    def reset(self) -> None:
        self.pending = bytearray()
        self.prefix: deque[tuple[bytes, float, bool]] = deque(maxlen=PREFIX_MS // FRAME_MS)
        self.segment: list[tuple[bytes, float, bool]] = []
        self.in_speech = False
        self.voiced_run = 0
        self.silence_ms = 0
        self.noise_floor = MIN_RMS / NOISE_FACTOR
        self.stream_ms = 0
        self.segment_start_ms = 0

    def feed(self, pcm: bytes) -> None:
        self.pending += pcm
        while len(self.pending) >= self.frame_bytes:
            frame = bytes(self.pending[: self.frame_bytes])
            del self.pending[: self.frame_bytes]
            self._frame(frame)

    def _frame(self, frame: bytes) -> None:
        rms = frame_rms(frame)
        voiced = rms > max(MIN_RMS, self.noise_floor * NOISE_FACTOR)
        self.stream_ms += FRAME_MS
        if not self.in_speech:
            self.prefix.append((frame, rms, voiced))
            if voiced:
                self.voiced_run += 1
            else:
                self.voiced_run = 0
                self.noise_floor = 0.95 * self.noise_floor + 0.05 * rms
            if self.voiced_run >= START_FRAMES:
                self._start(list(self.prefix))
            return

        self.segment.append((frame, rms, voiced))
        self.silence_ms = 0 if voiced else self.silence_ms + FRAME_MS
        segment_ms = len(self.segment) * FRAME_MS
        if self.silence_ms >= self.long_pause_ms or (
            self.silence_ms >= self.pause_ms and segment_ms >= self.min_segment_ms
        ):
            self._finish(self.segment)
        elif segment_ms >= MAX_SEGMENT_MS:
            self._force_cut()

    def _start(self, frames: list) -> None:
        self.in_speech = True
        self.segment = frames
        self.silence_ms = 0
        self.voiced_run = 0
        self.prefix.clear()
        self.segment_start_ms = self.stream_ms - len(frames) * FRAME_MS
        self.on_start(self.segment_start_ms)

    def _finish(self, frames: list) -> None:
        end_ms = self.segment_start_ms + len(frames) * FRAME_MS
        voiced_ms = sum(FRAME_MS for _, _, v in frames if v)
        self.in_speech = False
        self.segment = []
        self.silence_ms = 0
        if voiced_ms >= MIN_VOICED_MS:
            self.on_segment(b"".join(f for f, _, _ in frames), self.segment_start_ms, end_ms)
        else:
            self.on_segment(None, self.segment_start_ms, end_ms)  # noise blip: close the turn, skip Azure

    def _force_cut(self) -> None:
        window = FORCE_CUT_WINDOW_MS // FRAME_MS
        tail_start = len(self.segment) - window
        quietest = min(range(tail_start, len(self.segment)), key=lambda i: self.segment[i][1])
        head, rest = self.segment[: quietest + 1], self.segment[quietest + 1:]
        self._finish(head)
        self._start(rest)

    def flush(self) -> bool:
        """End any open segment now (client commit). True if one was open."""
        if self.in_speech and self.segment:
            self._finish(self.segment)
            return True
        return False


class RealtimeSession:
    def __init__(self, ws: WebSocket, label: str) -> None:
        self.ws = ws
        self.label = label
        self.session_id = new_id("sess")
        self.sample_rate = 24000
        self.stream = "mic"  # until session.update says otherwise
        self.requested_model = REALTIME_MODEL
        self.stt_model = REALTIME_MODEL
        self.language: str | None = None
        self.prompt: str | None = None
        self.segmenter = self._new_segmenter()
        self.item_id: str | None = None
        self.previous_item_id: str | None = None
        self.jobs: queue.Queue = queue.Queue()
        # After a client commit, results are held and sent as one completed
        # event: OpenWhispr stops waiting at the first completed after commit.
        self.committing = False
        self.after_commit: list[str] = []
        self.after_commit_count = 0
        self.segments_sent = 0
        self.audio_bytes = 0
        self.started = time.monotonic()

    def _new_segmenter(self) -> Segmenter:
        return Segmenter(self.sample_rate, PROFILES[self.stream], self._on_speech_start, self._on_segment)

    # -------------------------------------------------------------- outgoing

    def send(self, event: dict) -> None:
        event.setdefault("event_id", new_id("event"))
        try:
            self.ws.send_text(json.dumps(event))
        except ConnectionClosed:
            pass  # reader loop notices and ends the session

    def session_object(self) -> dict:
        return {
            "id": self.session_id,
            "object": "realtime.transcription_session",
            "type": "transcription",
            "audio": {"input": {
                "format": {"type": "audio/pcm", "rate": self.sample_rate},
                "transcription": {"model": self.requested_model,
                                  **({"language": self.language} if self.language else {})},
                "turn_detection": {"type": "server_vad",
                                   "silence_duration_ms": PROFILES[self.stream]["pause_ms"],
                                   "prefix_padding_ms": PREFIX_MS},
            }},
        }

    def error(self, code: str, message: str) -> None:
        self.send({"type": "error", "error": {"type": "invalid_request_error",
                                             "code": code, "message": message}})

    # -------------------------------------------------------------- VAD callbacks

    def _on_speech_start(self, start_ms: int) -> None:
        self.item_id = new_id("item")
        self.send({"type": "input_audio_buffer.speech_started",
                   "audio_start_ms": start_ms, "item_id": self.item_id})

    def _on_segment(self, pcm: bytes | None, start_ms: int, end_ms: int) -> None:
        item_id = self.item_id or new_id("item")
        self.item_id = None
        self.send({"type": "input_audio_buffer.speech_stopped",
                   "audio_end_ms": end_ms, "item_id": item_id})
        if pcm is None:
            return  # noise blip: no turn, no Azure call
        self.send({"type": "input_audio_buffer.committed",
                   "item_id": item_id, "previous_item_id": self.previous_item_id})
        self.previous_item_id = item_id
        self.jobs.put(("segment", item_id, pcm, end_ms - start_ms))

    # -------------------------------------------------------------- worker

    def _worker(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            self._process(job)

    def _process(self, job: tuple) -> None:
        if job[0] == "commit":
            self._finish_commit(job[1])
            return
        _, item_id, pcm, _duration_ms = job
        try:
            text = self._transcribe(pcm)
            failed = None
        except Exception as exc:  # one bad segment must not end a meeting
            log(f"rt {self.label}: segment {item_id} failed: {exc}")
            text, failed = "", str(exc)
        if self.committing:
            self.after_commit_count += 1
            if text:
                self.after_commit.append(text)
            return
        if failed:
            self.send({"type": "conversation.item.input_audio_transcription.failed",
                       "item_id": item_id, "content_index": 0,
                       "error": {"type": "transcription_error", "message": failed}})
        else:
            self._send_completed(item_id, text)

    def _transcribe(self, pcm: bytes) -> str:
        backend, _ = stt.resolve_model(self.stt_model)
        audio = stt.encode_pcm(pcm, self.sample_rate, backend)
        return stt.recognize(audio, self.stt_model, self.language, self.prompt)

    def _send_completed(self, item_id: str, text: str) -> None:
        self.segments_sent += 1
        self.send({"type": "conversation.item.input_audio_transcription.completed",
                   "item_id": item_id, "content_index": 0, "transcript": text})

    def _finish_commit(self, item_id: str) -> None:
        if self.after_commit_count == 0:
            # OpenAI's wording: OpenWhispr matches "buffer too small" / "commit_empty"
            # to stop waiting for a final turn. Any other text costs a 3 s timeout.
            self.error("input_audio_buffer_commit_empty",
                       "Error committing input audio buffer: buffer too small. "
                       "Expected at least 100ms of audio, but buffer only has 0.00ms of audio.")
        else:
            self._send_completed(item_id, " ".join(self.after_commit))
        self.committing = False
        self.after_commit, self.after_commit_count = [], 0

    # -------------------------------------------------------------- incoming

    def handle(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "input_audio_buffer.append":
            try:
                pcm = base64.b64decode(event.get("audio") or "")
            except (binascii.Error, ValueError):
                self.error("invalid_audio", "audio must be base64 PCM16")
                return
            self.audio_bytes += len(pcm)
            self.segmenter.feed(pcm)
        elif kind == "input_audio_buffer.commit":
            self.committing = True
            self.segmenter.flush()
            self.jobs.put(("commit", self.previous_item_id or new_id("item")))
        elif kind == "input_audio_buffer.clear":
            self.segmenter.reset()
            self.item_id = None
            self.send({"type": "input_audio_buffer.cleared"})
        elif kind in ("session.update", "transcription_session.update"):
            self._update_session(event.get("session") or {})
            self.send({"type": "session.updated", "session": self.session_object()})
        # Anything else (response.create, conversation.item.*) has no meaning here.

    def _update_session(self, session: dict) -> None:
        audio_in = (session.get("audio") or {}).get("input") or {}
        # GA shape (audio.input.*) first, preview shape (input_audio_*) as fallback.
        fmt = audio_in.get("format") or {}
        transcription = audio_in.get("transcription") or session.get("input_audio_transcription") or {}
        turn = audio_in.get("turn_detection") or session.get("turn_detection") or {}
        if fmt.get("rate"):
            self.sample_rate = int(fmt["rate"])
        # OpenWhispr's silence_duration_ms (600) is tuned for OpenAI's VAD and is
        # ignored; its threshold only tells the mic and system streams apart.
        if isinstance(turn, dict) and isinstance(turn.get("threshold"), (int, float)):
            self.stream = "system" if turn["threshold"] < SYSTEM_THRESHOLD_BELOW else "mic"
        if transcription.get("model"):
            self.requested_model = transcription["model"]
            try:
                stt.resolve_model(self.requested_model)
                self.stt_model = self.requested_model
            except ValueError:
                self.stt_model = REALTIME_MODEL
        self.language = transcription.get("language") or self.language
        self.prompt = transcription.get("prompt") or self.prompt
        self.segmenter = self._new_segmenter()
        log(f"rt {self.label}: {self.stream} stream, {PROFILES[self.stream]}")

    def run(self) -> None:
        worker = threading.Thread(target=self._worker, daemon=True)
        worker.start()
        log(f"rt {self.label}: open")
        self.send({"type": "session.created", "session": self.session_object()})
        try:
            while True:
                opcode, payload = self.ws.receive()
                if opcode == OP_BINARY:
                    self.audio_bytes += len(payload)
                    self.segmenter.feed(payload)
                    continue
                try:
                    event = json.loads(payload)
                except ValueError:
                    self.error("invalid_json", "events must be JSON objects")
                    continue
                if isinstance(event, dict):
                    self.handle(event)
        except ConnectionClosed:
            pass
        finally:
            self.jobs.put(None)
            self.ws.close()
            audio_s = self.audio_bytes / (2 * self.sample_rate)
            log(f"rt {self.label} ({self.stream}): closed after {time.monotonic() - self.started:.0f}s, "
                f"audio={audio_s:.1f}s model={self.stt_model} (asked {self.requested_model}) "
                f"segments={self.segments_sent}")
