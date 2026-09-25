#!/usr/bin/env python3
"""Offline tests for the shim modules. ffmpeg, az and Azure are stubbed.

    python3 test_shim.py
"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import base64
import math
import socket
import struct

import chat
import foundry
import foundry_shim as shim
import realtime
import stt
import websocket


class DefinitionTests(unittest.TestCase):
    def test_resolve_model(self):
        self.assertEqual(stt.resolve_model(""), ("mai", "MAI-Transcribe-2"))
        self.assertEqual(stt.resolve_model("MAI-Transcribe-1.5"), ("mai", "MAI-Transcribe-1.5"))
        self.assertEqual(stt.resolve_model("llm-speech"), ("llm-speech", ""))
        with self.assertRaises(ValueError):
            stt.resolve_model("whisper-1")

    def test_locales(self):
        self.assertEqual(stt.resolve_locale("mai", "fi"), "fi")
        self.assertEqual(stt.resolve_locale("mai", "en-US"), "en")
        self.assertEqual(stt.resolve_locale("llm-speech", "fi"), "fi-FI")
        self.assertEqual(stt.resolve_locale("llm-speech", "en-GB"), "en-GB")
        self.assertIsNone(stt.resolve_locale("llm-speech", "et"))  # unsupported -> auto-detect
        self.assertIsNone(stt.resolve_locale("mai", "auto"))

    def test_mai_definition(self):
        d = stt.build_definition("mai", "MAI-Transcribe-2", "fi", "OpenWhispr, Foundry, ")
        self.assertEqual(d["enhancedMode"]["model"], "MAI-Transcribe-2")
        self.assertEqual(d["enhancedMode"]["modelOptions"]["transcribeStyle"], stt.MAI_STYLE)
        self.assertEqual(d["locales"], ["fi"])
        self.assertEqual(d["phraseList"]["phrases"], ["OpenWhispr", "Foundry"])

    def test_llm_definition(self):
        d = stt.build_definition("llm-speech", "", None, None)
        self.assertEqual(d, {"enhancedMode": {"enabled": True, "task": "transcribe"}})

    def test_multipart_roundtrip(self):
        body, ctype = stt.encode_multipart({"model": "x"}, {"file": ("a.webm", "audio/webm", b"\x00\x01")})
        fields, files = shim.parse_multipart_form(body, ctype)
        self.assertEqual(fields, {"model": "x"})
        self.assertEqual(files, {"file": ("a.webm", b"\x00\x01")})

    def test_extract_text(self):
        self.assertEqual(stt.extract_text({"combinedPhrases": [{"text": "a"}, {"text": "b"}]}), "a b")
        self.assertEqual(stt.extract_text({}), "")


class ChatAdaptTests(unittest.TestCase):
    def test_openwhispr_lan_body(self):
        # What OpenWhispr's self-hosted chat sends with "Disable thinking output" on.
        body = {"model": "gpt-5.4-nano", "messages": [], "stream": True, "max_tokens": 4096,
                "temperature": 0, "reasoning": {"effort": "none"},
                "chat_template_kwargs": {"enable_thinking": False}}
        self.assertEqual(chat.adapt_chat_body(body), {
            "model": "gpt-5.4-nano", "messages": [], "stream": True,
            "max_completion_tokens": 4096, "temperature": 0, "reasoning_effort": "none"})

    def test_thinking_left_to_model_default(self):
        out = chat.adapt_chat_body({"model": "m", "messages": [], "max_completion_tokens": 10})
        self.assertNotIn("reasoning_effort", out)

    def test_explicit_effort_wins(self):
        out = chat.adapt_chat_body({"model": "m", "messages": [], "reasoning_effort": "low",
                                    "reasoning": {"effort": "none"}})
        self.assertEqual(out["reasoning_effort"], "low")
        self.assertNotIn("reasoning", out)

    def test_rejected_param(self):
        err = json.dumps({"error": {"param": "temperature", "code": "unsupported_value"}}).encode()
        self.assertEqual(chat.rejected_param(err), "temperature")
        self.assertIsNone(chat.rejected_param(json.dumps({"error": {"param": "messages"}}).encode()))
        self.assertIsNone(chat.rejected_param(b"not json"))

    def test_open_chat_strips_rejected_param(self):
        err = foundry.UpstreamError(400, json.dumps({"error": {"param": "temperature"}}).encode())
        sentinel = object()
        with mock.patch.object(chat, "open_azure", side_effect=[err, sentinel]) as oa:
            resp, sent = chat.open_chat({"model": "m", "messages": [], "temperature": 0})
        self.assertIs(resp, sentinel)
        self.assertNotIn("temperature", sent)
        self.assertEqual(oa.call_count, 2)


class PoolTests(unittest.TestCase):
    def test_stale_reused_connection_falls_back_to_new(self):
        stale, fresh = mock.Mock(), mock.Mock()
        stale.request.side_effect = foundry.http.client.RemoteDisconnected("idle close")
        fresh.getresponse.return_value = mock.Mock(status=200, headers={})
        with mock.patch.object(foundry.POOL, "get", side_effect=[(stale, True), (fresh, False)]):
            resp = foundry.send_pooled("https://example.azure.com/x?y=1", b"b", {})
        stale.close.assert_called_once()
        fresh.request.assert_called_once_with("POST", "/x?y=1", body=b"b", headers={})
        self.assertFalse(resp.reused)

    def test_new_connection_failure_raises(self):
        broken = mock.Mock()
        broken.request.side_effect = ConnectionRefusedError()
        with mock.patch.object(foundry.POOL, "get", return_value=(broken, False)):
            with self.assertRaises(ConnectionRefusedError):
                foundry.send_pooled("https://example.azure.com/x", b"", {})

    def test_connection_returned_only_when_fully_read(self):
        pool = foundry.ConnectionPool()
        conn = mock.Mock()
        done = mock.Mock(status=200, headers={}, will_close=False)
        done.isclosed.return_value = True
        with mock.patch.object(foundry, "POOL", pool):
            foundry.PooledResponse("h", conn, done, False).close()
            self.assertEqual(pool.get("h"), (conn, True))
            partial = mock.Mock(status=200, headers={}, will_close=False)
            partial.isclosed.return_value = False
            foundry.PooledResponse("h", conn, partial, False).close()
            conn.close.assert_called_once()
            self.assertFalse(pool.get("h")[1])


# ---------------------------------------------------------------- realtime

RATE = 24000


def tone(ms, amplitude=8000):
    n = RATE * ms // 1000
    return struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(n)))


def silence(ms):
    return b"\x00\x00" * (RATE * ms // 1000)


class SegmenterTests(unittest.TestCase):
    PROFILE = {"min_segment_ms": 3000, "pause_ms": 600, "long_pause_ms": 1500}

    def run_segmenter(self, pcm, profile=None):
        starts, segments = [], []
        seg = realtime.Segmenter(RATE, profile or self.PROFILE, starts.append,
                                 lambda p, a, b: segments.append((p, a, b)))
        seg.feed(pcm)
        return seg, starts, segments

    def test_pause_closes_segment_after_min_length(self):
        _, starts, segments = self.run_segmenter(tone(3500) + silence(800))
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(segments), 1)
        self.assertIsNotNone(segments[0][0])

    def test_mic_profile_keeps_thinking_pauses_inside_a_turn(self):
        # "agendana on 5 ... viime vuoden budjetti": 4 s of speech, a 900 ms pause, more speech
        mic = realtime.PROFILES["mic"]
        _, _, segments = self.run_segmenter(tone(4000) + silence(900) + tone(3000) + silence(300), mic)
        self.assertEqual(segments, [])
        _, _, segments = self.run_segmenter(tone(4000) + silence(2100), mic)
        self.assertEqual(len(segments), 1)  # a real stop still ends it

    def test_short_pause_inside_short_segment_does_not_cut(self):
        _, _, segments = self.run_segmenter(tone(1000) + silence(700) + tone(1000) + silence(200))
        self.assertEqual(segments, [])  # 700 ms pause, but segment still under MIN_SEGMENT_MS

    def test_long_pause_cuts_short_segment(self):
        _, _, segments = self.run_segmenter(tone(1000) + silence(1600))
        self.assertEqual(len(segments), 1)

    def test_force_cut_at_max_length(self):
        _, starts, segments = self.run_segmenter(tone(realtime.MAX_SEGMENT_MS + 1000))
        self.assertEqual(len(segments), 1)
        self.assertEqual(len(starts), 2)  # remainder continues as a new segment

    def test_noise_blip_is_dropped(self):
        _, _, segments = self.run_segmenter(tone(100) + silence(2000))
        self.assertEqual(len(segments), 1)
        self.assertIsNone(segments[0][0])

    def test_flush_ends_open_segment(self):
        seg, _, segments = self.run_segmenter(tone(1500))
        self.assertTrue(seg.flush())
        self.assertEqual(len(segments), 1)


class FakeSocket:
    def __init__(self):
        self.sent = []

    def send_text(self, text):
        self.sent.append(json.loads(text))

    def close(self):
        pass

    def types(self):
        return [e["type"] for e in self.sent]


class RealtimeSessionTests(unittest.TestCase):
    def make(self, texts):
        ws = FakeSocket()
        session = realtime.RealtimeSession(ws, "test")
        session._transcribe = mock.Mock(side_effect=list(texts))
        return ws, session

    def append(self, session, pcm):
        session.handle({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()})

    def drain(self, session):
        while not session.jobs.empty():
            session._process(session.jobs.get_nowait())

    def test_session_update_maps_unknown_model(self):
        ws, session = self.make([])
        session.handle({"type": "session.update", "session": {"type": "transcription", "audio": {"input": {
            "format": {"type": "audio/pcm", "rate": 24000},
            "transcription": {"model": "gpt-4o-mini-transcribe"},
            "turn_detection": {"type": "server_vad", "threshold": 0.6, "silence_duration_ms": 500}}}}})
        self.assertEqual(ws.types(), ["session.updated"])
        self.assertEqual(session.stt_model, realtime.REALTIME_MODEL)
        self.assertEqual(session.stream, "mic")
        self.assertEqual(session.segmenter.min_segment_ms, realtime.PROFILES["mic"]["min_segment_ms"])

    def test_system_stream_detected_from_threshold(self):
        _, session = self.make([])
        session.handle({"type": "session.update", "session": {"audio": {"input": {
            "turn_detection": {"type": "server_vad", "threshold": 0.3}}}}})
        self.assertEqual(session.stream, "system")
        self.assertEqual(session.segmenter.pause_ms, realtime.PROFILES["system"]["pause_ms"])

    def test_segments_then_commit_combines_remaining(self):
        ws, session = self.make(["First.", "Second."])
        self.append(session, tone(3500) + silence(2100))   # segment 1 closes on a 2 s pause
        self.drain(session)                               # ...and is transcribed before the stop
        self.append(session, tone(1500))                  # segment 2 still open at stop
        session.handle({"type": "input_audio_buffer.commit"})
        self.drain(session)
        completed = [e for e in ws.sent if e["type"].endswith("transcription.completed")]
        self.assertEqual([e["transcript"] for e in completed], ["First.", "Second."])
        self.assertIn("input_audio_buffer.speech_started", ws.types())

    def test_in_flight_segment_is_merged_into_commit_result(self):
        ws, session = self.make(["First.", "Second."])
        self.append(session, tone(3500) + silence(2100))
        self.append(session, tone(1500))
        session.handle({"type": "input_audio_buffer.commit"})  # before the worker ran segment 1
        self.drain(session)
        completed = [e for e in ws.sent if e["type"].endswith("transcription.completed")]
        self.assertEqual([e["transcript"] for e in completed], ["First. Second."])

    def test_empty_commit_uses_openai_wording(self):
        ws, session = self.make([])
        self.append(session, silence(500))
        session.handle({"type": "input_audio_buffer.commit"})
        self.drain(session)
        self.assertEqual(ws.types(), ["error"])
        self.assertIn("buffer too small", ws.sent[0]["error"]["message"])

    def test_failed_segment_reports_failed_event(self):
        ws, session = self.make([RuntimeError("azure down")])
        self.append(session, tone(3500) + silence(2100))
        self.drain(session)
        self.assertIn("conversation.item.input_audio_transcription.failed", ws.types())


class WebSocketTests(unittest.TestCase):
    def test_accept_key_rfc_example(self):
        self.assertEqual(websocket.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_unmask_roundtrip(self):
        mask = b"\x01\x02\x03\x04"
        data = bytes(range(250))
        self.assertEqual(websocket.unmask(websocket.unmask(data, mask), mask), data)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), shim.ShimHandler)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def post(self, fields):
        body, ctype = stt.encode_multipart(fields, {"file": ("audio.webm", "audio/webm", b"fake")})
        req = urllib.request.Request(self.url + "/v1/audio/transcriptions", data=body,
                                     headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_happy_path(self):
        def fake_convert(path, backend):
            import tempfile
            return tempfile.mkstemp(suffix="." + stt.UPLOAD_FORMATS[backend][0])[1]
        with mock.patch.object(stt, "convert_audio", fake_convert), \
             mock.patch.object(stt, "transcribe", return_value="hei") as t:
            status, data = self.post({"model": "llm-speech", "language": "fi", "prompt": "a, b"})
        self.assertEqual((status, data["text"]), (200, "hei"))
        self.assertEqual(t.call_args.args[1:], ("llm-speech", "fi", "a, b"))

    def test_models_lists_chat_models(self):
        with urllib.request.urlopen(self.url + "/v1/models") as r:
            ids = [m["id"] for m in json.loads(r.read())["data"]]
        self.assertEqual(ids, chat.CHAT_MODELS)

    def ws_connect(self):
        port = self.server.server_address[1]
        sock = socket.create_connection(("127.0.0.1", port))
        sock.sendall((f"GET /v1/realtime?intent=transcription HTTP/1.1\r\nHost: x\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        f = sock.makefile("rb")
        status = f.readline()
        while f.readline() not in (b"\r\n", b""):
            pass
        return sock, f, status

    def ws_send(self, sock, text, opcode=0x1):
        payload = text.encode()
        mask = b"\x11\x22\x33\x44"
        n = len(payload)
        if n < 126:
            length = bytes([0x80 | n])
        elif n < 1 << 16:
            length = bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            length = bytes([0x80 | 127]) + struct.pack("!Q", n)
        header = bytes([0x80 | opcode]) + length
        sock.sendall(header + mask + websocket.unmask(payload, mask))

    def ws_recv(self, f):
        b0, b1 = f.read(2)
        n = b1 & 0x7F
        if n == 126:
            (n,) = struct.unpack("!H", f.read(2))
        return b0 & 0x0F, f.read(n)

    def test_realtime_websocket_end_to_end(self):
        sock, f, status = self.ws_connect()
        self.assertIn(b"101", status)
        op, created = self.ws_recv(f)
        self.assertEqual(json.loads(created)["type"], "session.created")
        self.ws_send(sock, "ping", opcode=0x9)
        self.assertEqual(self.ws_recv(f), (0xA, b"ping"))
        with mock.patch.object(realtime.RealtimeSession, "_transcribe", return_value="Hei."):
            audio = base64.b64encode(tone(1500)).decode()
            self.ws_send(sock, json.dumps({"type": "input_audio_buffer.append", "audio": audio}))
            self.ws_send(sock, json.dumps({"type": "input_audio_buffer.commit"}))
            events = []
            while not events or not events[-1]["type"].endswith("completed"):
                events.append(json.loads(self.ws_recv(f)[1]))
        self.assertEqual(events[-1]["transcript"], "Hei.")
        sock.close()

    def test_unknown_model_is_400(self):
        status, data = self.post({"model": "whisper-1"})
        self.assertEqual(status, 400)
        self.assertIn("unknown model", data["error"])


if __name__ == "__main__":
    unittest.main()
