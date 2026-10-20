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

import archive
import chat
import foundry
import foundry_shim as shim
import phrases
import realtime
import stt
import websocket

# Tests never write meeting audio or read OpenWhispr's real database.
archive.KEEP_DAYS = 0
phrases.ENABLED = False


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

    def test_extract_locale_from_longest_phrase(self):
        self.assertEqual(stt.extract_locale({"phrases": [
            {"text": "ok", "locale": "en", "durationMilliseconds": 400},
            {"text": "käydään läpi", "locale": "fi", "durationMilliseconds": 3000}]}), "fi")
        self.assertIsNone(stt.extract_locale({"phrases": [{"text": "x"}]}))
        self.assertIsNone(stt.extract_locale({}))


class SttTimeoutTests(unittest.TestCase):
    def test_timeout_grows_with_audio_length(self):
        self.assertEqual(stt.stt_timeout(b"", "llm-speech"), stt.STT_TIMEOUT_S)
        self.assertEqual(stt.stt_timeout(b"\0" * 32_000 * 120, "llm-speech"), stt.STT_TIMEOUT_S + 30)
        self.assertEqual(stt.stt_timeout(b"\0" * 6_000 * 120, "mai"), stt.STT_TIMEOUT_S + 30)

    def transcribe(self, *results):
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"audio")
        self.addCleanup(os.remove, f.name)
        with mock.patch.object(stt, "recognize", side_effect=list(results)) as recognize:
            try:
                return stt.transcribe(f.name, "llm-speech", None, None), recognize
            except Exception as exc:
                return exc, recognize

    def test_unanswered_request_is_tried_once_more(self):
        text, recognize = self.transcribe(foundry.UpstreamTimeout(30, True), "Hei.")
        self.assertEqual(text, "Hei.")
        self.assertEqual(recognize.call_count, 2)

    def test_second_timeout_gives_up(self):
        exc, recognize = self.transcribe(foundry.UpstreamTimeout(30, True), foundry.UpstreamTimeout(30, False), "late")
        self.assertIsInstance(exc, foundry.UpstreamTimeout)
        self.assertEqual(recognize.call_count, 2)

    def test_other_errors_are_not_retried(self):
        exc, recognize = self.transcribe(foundry.UpstreamError(400, b"bad"), "late")
        self.assertIsInstance(exc, foundry.UpstreamError)
        self.assertEqual(recognize.call_count, 1)


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

    def test_timeout_drops_idle_connections(self):
        pool = foundry.ConnectionPool()
        idle, stuck = mock.Mock(), mock.Mock()
        stuck.getresponse.side_effect = TimeoutError("The read operation timed out")
        pool.put("example.azure.com", idle)
        with mock.patch.object(foundry, "POOL", pool), \
                mock.patch.object(pool, "get", return_value=(stuck, True)):
            with self.assertRaises(foundry.UpstreamTimeout) as caught:
                foundry.send_pooled("https://example.azure.com/x", b"", {}, timeout=5)
        self.assertEqual(stuck.timeout, 5)
        self.assertTrue(caught.exception.reused)
        stuck.close.assert_called_once()
        idle.close.assert_called_once()
        self.assertEqual(pool._idle, {})


class ConfigTests(unittest.TestCase):
    def test_load_config(self):
        import os
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("# comment\n\nSHIM_TEST_A=from-file\nSHIM_TEST_B = 'quoted'\n"
                    "SHIM_TEST_C=from-file\n#SHIM_TEST_D=commented\nnot a setting\n")
        self.addCleanup(os.remove, f.name)
        with mock.patch.dict(os.environ, {"SHIM_TEST_C": "from-env"}):
            foundry.load_config(f.name)
            self.assertEqual(os.environ["SHIM_TEST_A"], "from-file")
            self.assertEqual(os.environ["SHIM_TEST_B"], "quoted")
            self.assertEqual(os.environ["SHIM_TEST_C"], "from-env")
            self.assertNotIn("SHIM_TEST_D", os.environ)
        foundry.load_config(f.name + ".missing")  # no file is fine


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


class TranscribeTests(unittest.TestCase):
    """RealtimeSession._transcribe: language hints and retries."""

    def setUp(self):
        realtime._recent_language.update(language=None, at=float("-inf"))
        self.addCleanup(realtime._recent_language.update, language=None, at=float("-inf"))
        patcher = mock.patch.object(stt, "encode_pcm", return_value=b"mp3")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.session = realtime.RealtimeSession(FakeSocket(), "test")

    def recognize(self, *results):
        patcher = mock.patch.object(stt, "recognize_with_locale", side_effect=list(results))
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_short_segment_gets_language_of_long_ones(self):
        recognize = self.recognize(("Käydään budjetti läpi.", "fi"), ("Öö.", "fi"))
        self.session._transcribe(b"", 8000)
        self.session._transcribe(b"", 2000)
        self.assertIsNone(recognize.call_args_list[0].args[2])  # long: auto-detect
        self.assertEqual(recognize.call_args_list[1].args[2], "fi")

    def test_short_segment_does_not_teach_language(self):
        recognize = self.recognize(("あの、", "ja"), ("Joo.", "fi"))
        self.session._transcribe(b"", 2000)
        self.session._transcribe(b"", 2000)
        self.assertIsNone(recognize.call_args_list[1].args[2])

    def test_other_stream_language_is_borrowed(self):
        self.recognize(("Hyvää huomenta kaikille.", "fi"))
        self.session._transcribe(b"", 8000)
        other = realtime.RealtimeSession(FakeSocket(), "other")
        self.assertEqual(other._language_hint(), "fi")

    def test_client_language_wins(self):
        recognize = self.recognize(("Hello.", "en"))
        self.session.language = "en"
        self.session._transcribe(b"", 2000)
        self.assertEqual(recognize.call_args.args[2], "en")

    def test_meeting_prompt_is_used_without_client_prompt(self):
        recognize = self.recognize(("x", None), ("y", None))
        with mock.patch.object(phrases, "meeting_prompt", return_value="Liisa, Foundry"):
            self.session._transcribe(b"", 8000)
            self.session.prompt = "Dictionary"
            self.session._transcribe(b"", 8000)
        self.assertEqual(recognize.call_args_list[0].args[3], "Liisa, Foundry")
        self.assertEqual(recognize.call_args_list[1].args[3], "Dictionary")

    def test_transient_failure_is_retried(self):
        recognize = self.recognize(foundry.UpstreamError(503, b"busy"), ConnectionResetError(), ("Hei.", "fi"))
        with mock.patch.object(realtime.time, "sleep") as sleep:
            self.assertEqual(self.session._transcribe(b"", 8000), "Hei.")
        self.assertEqual(recognize.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], list(realtime.RETRY_DELAYS_S))

    def test_gives_up_after_retries(self):
        self.recognize(*[foundry.UpstreamError(429, b"slow down")] * 3)
        with mock.patch.object(realtime.time, "sleep"), self.assertRaises(foundry.UpstreamError):
            self.session._transcribe(b"", 8000)

    def test_rejected_request_is_not_retried(self):
        recognize = self.recognize(foundry.UpstreamError(400, b"bad"))
        with mock.patch.object(realtime.time, "sleep") as sleep, self.assertRaises(foundry.UpstreamError):
            self.session._transcribe(b"", 8000)
        self.assertEqual(recognize.call_count, 1)
        sleep.assert_not_called()


class PhrasesTests(unittest.TestCase):
    def make_db(self, notes, words):
        import os
        import sqlite3
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, path)
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY, participants TEXT, deleted_at TEXT,"
            " updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);"
            "CREATE TABLE custom_dictionary (id INTEGER PRIMARY KEY, word TEXT, deleted_at TEXT);")
        for participants, age_minutes, deleted in notes:
            conn.execute("INSERT INTO notes (participants, deleted_at, updated_at)"
                         " VALUES (?, ?, datetime('now', ?))",
                         (json.dumps(participants), deleted, f"-{age_minutes} minutes"))
        for word, deleted in words:
            conn.execute("INSERT INTO custom_dictionary (word, deleted_at) VALUES (?, ?)", (word, deleted))
        conn.commit()
        conn.close()
        return path

    def attendee(self, name, me=False):
        return {"email": "x@example.com", "displayName": name, "responseStatus": None, "self": me}

    def test_participant_names(self):
        self.assertEqual(phrases.participant_names(json.dumps([
            self.attendee("Pasi", me=True), self.attendee("Liisa Korhonen"),
            self.attendee("Al Smith"), self.attendee(None), self.attendee("bob@example.com")])),
            ["Liisa Korhonen", "Liisa", "Al Smith"])
        self.assertEqual(phrases.participant_names("not json"), [])
        self.assertEqual(phrases.participant_names(None), [])

    def test_recording_note_and_dictionary(self):
        path = self.make_db(
            notes=[([self.attendee("Old Meeting")], 30, None),          # too old
                   ([self.attendee("Liisa Korhonen")], 1, None),        # the recording note
                   ([self.attendee("Deleted Person")], 0, "2026-10-14")],
            words=[("Foundry", None), ("foundry", None), ("Gone", "2026-10-14"), ("a, b", None)])
        self.assertEqual(phrases.read_phrases(path), ["Liisa Korhonen", "Liisa", "Foundry"])

    def test_no_recent_note_gives_dictionary_only(self):
        path = self.make_db(notes=[([self.attendee("Old Meeting")], 30, None)], words=[("Foundry", None)])
        self.assertEqual(phrases.read_phrases(path), ["Foundry"])

    def test_cache_keeps_last_list_when_db_unreadable(self):
        cache = phrases.PhraseCache("/nonexistent/transcriptions.db")
        cache._phrases = ["Foundry"]
        with mock.patch.object(phrases, "log"):
            self.assertEqual(cache.get(), ["Foundry"])


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.root = tempfile.mkdtemp()
        import shutil
        self.addCleanup(shutil.rmtree, self.root)

    def test_clock(self):
        self.assertEqual(archive.clock(3_723_456), "01:02:03")

    def test_prune_removes_old_files_and_empty_days(self):
        import os
        import time

        old_day, new_day = os.path.join(self.root, "2026-09-01"), os.path.join(self.root, "2026-10-14")
        os.makedirs(old_day)
        os.makedirs(new_day)
        for path in (os.path.join(old_day, "a.mp3"), os.path.join(new_day, "b.mp3")):
            open(path, "w").close()
        twenty_days_ago = time.time() - 20 * 86400
        os.utime(os.path.join(old_day, "a.mp3"), (twenty_days_ago, twenty_days_ago))
        with mock.patch.object(archive, "KEEP_DAYS", 14):
            self.assertEqual(archive.prune(self.root), 1)
        self.assertEqual(os.listdir(self.root), ["2026-10-14"])

    def test_session_archive_writes_audio_and_lines(self):
        with mock.patch.object(archive.subprocess, "Popen") as popen:
            a = archive.SessionArchive("mic", "sess_0123456789abcdef", 24000, root=self.root)
            a.write(b"\x00\x01")
            a.line(65_000, "Hei kaikki.")
            a.close()
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[-1], a.audio_path)
        self.assertIn("24000", cmd)
        popen.return_value.stdin.write.assert_called_once_with(b"\x00\x01")
        with open(a.text_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertTrue(lines[0].startswith("# mic stream"))
        self.assertEqual(lines[1], "[00:01:05] Hei kaikki.")

    def test_dead_ffmpeg_turns_archive_off(self):
        with mock.patch.object(archive.subprocess, "Popen") as popen, mock.patch.object(archive, "log"):
            popen.return_value.stdin.write.side_effect = BrokenPipeError()
            a = archive.SessionArchive("system", "sess_x", 24000, root=self.root)
            a.write(b"\x00")
            a.write(b"\x00")
        self.assertEqual(popen.return_value.stdin.write.call_count, 1)

    def test_session_archives_segments_and_failures(self):
        ws = FakeSocket()
        session = realtime.RealtimeSession(ws, "test")
        session._transcribe = mock.Mock(side_effect=["First.", RuntimeError("azure down")])
        session.archive = mock.Mock(audio_path="x.mp3")
        session.handle({"type": "input_audio_buffer.append",
                        "audio": base64.b64encode(tone(3500) + silence(2100) + tone(3500) + silence(2100)).decode()})
        with mock.patch.object(realtime, "log"):
            while not session.jobs.empty():
                session._process(session.jobs.get_nowait())
        texts = [c.args[1] for c in session.archive.line.call_args_list]
        self.assertEqual(texts, ["First.", "(transcription failed: azure down)"])
        session.archive.write.assert_called_once()


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
