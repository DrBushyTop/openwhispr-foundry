#!/usr/bin/env python3
"""Offline tests for foundry_shim.py. ffmpeg, az and Azure are stubbed.

    python3 test_shim.py
"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import foundry_shim as shim


class DefinitionTests(unittest.TestCase):
    def test_resolve_model(self):
        self.assertEqual(shim.resolve_model(""), ("mai", "MAI-Transcribe-2"))
        self.assertEqual(shim.resolve_model("MAI-Transcribe-1.5"), ("mai", "MAI-Transcribe-1.5"))
        self.assertEqual(shim.resolve_model("llm-speech"), ("llm-speech", ""))
        with self.assertRaises(ValueError):
            shim.resolve_model("whisper-1")

    def test_locales(self):
        self.assertEqual(shim.resolve_locale("mai", "fi"), "fi")
        self.assertEqual(shim.resolve_locale("mai", "en-US"), "en")
        self.assertEqual(shim.resolve_locale("llm-speech", "fi"), "fi-FI")
        self.assertEqual(shim.resolve_locale("llm-speech", "en-GB"), "en-GB")
        self.assertIsNone(shim.resolve_locale("llm-speech", "et"))  # unsupported -> auto-detect
        self.assertIsNone(shim.resolve_locale("mai", "auto"))

    def test_mai_definition(self):
        d = shim.build_definition("mai", "MAI-Transcribe-2", "fi", "OpenWhispr, Foundry, ")
        self.assertEqual(d["enhancedMode"]["model"], "MAI-Transcribe-2")
        self.assertEqual(d["enhancedMode"]["modelOptions"]["transcribeStyle"], shim.MAI_STYLE)
        self.assertEqual(d["locales"], ["fi"])
        self.assertEqual(d["phraseList"]["phrases"], ["OpenWhispr", "Foundry"])

    def test_llm_definition(self):
        d = shim.build_definition("llm-speech", "", None, None)
        self.assertEqual(d, {"enhancedMode": {"enabled": True, "task": "transcribe"}})

    def test_multipart_roundtrip(self):
        body, ctype = shim.encode_multipart({"model": "x"}, {"file": ("a.webm", "audio/webm", b"\x00\x01")})
        fields, files = shim.parse_multipart_form(body, ctype)
        self.assertEqual(fields, {"model": "x"})
        self.assertEqual(files, {"file": ("a.webm", b"\x00\x01")})

    def test_extract_text(self):
        self.assertEqual(shim.extract_text({"combinedPhrases": [{"text": "a"}, {"text": "b"}]}), "a b")
        self.assertEqual(shim.extract_text({}), "")


class ChatAdaptTests(unittest.TestCase):
    def test_openwhispr_lan_body(self):
        # What OpenWhispr's self-hosted chat sends with "Disable thinking output" on.
        body = {"model": "gpt-5.4-nano", "messages": [], "stream": True, "max_tokens": 4096,
                "temperature": 0, "reasoning": {"effort": "none"},
                "chat_template_kwargs": {"enable_thinking": False}}
        self.assertEqual(shim.adapt_chat_body(body), {
            "model": "gpt-5.4-nano", "messages": [], "stream": True,
            "max_completion_tokens": 4096, "temperature": 0, "reasoning_effort": "none"})

    def test_thinking_left_to_model_default(self):
        out = shim.adapt_chat_body({"model": "m", "messages": [], "max_completion_tokens": 10})
        self.assertNotIn("reasoning_effort", out)

    def test_explicit_effort_wins(self):
        out = shim.adapt_chat_body({"model": "m", "messages": [], "reasoning_effort": "low",
                                    "reasoning": {"effort": "none"}})
        self.assertEqual(out["reasoning_effort"], "low")
        self.assertNotIn("reasoning", out)

    def test_rejected_param(self):
        err = json.dumps({"error": {"param": "temperature", "code": "unsupported_value"}}).encode()
        self.assertEqual(shim.rejected_param(err), "temperature")
        self.assertIsNone(shim.rejected_param(json.dumps({"error": {"param": "messages"}}).encode()))
        self.assertIsNone(shim.rejected_param(b"not json"))

    def test_open_chat_strips_rejected_param(self):
        err = shim.UpstreamError(400, json.dumps({"error": {"param": "temperature"}}).encode())
        sentinel = object()
        with mock.patch.object(shim, "open_azure", side_effect=[err, sentinel]) as oa:
            resp, sent = shim.open_chat({"model": "m", "messages": [], "temperature": 0})
        self.assertIs(resp, sentinel)
        self.assertNotIn("temperature", sent)
        self.assertEqual(oa.call_count, 2)


class PoolTests(unittest.TestCase):
    def test_stale_reused_connection_falls_back_to_new(self):
        stale, fresh = mock.Mock(), mock.Mock()
        stale.request.side_effect = shim.http.client.RemoteDisconnected("idle close")
        fresh.getresponse.return_value = mock.Mock(status=200, headers={})
        with mock.patch.object(shim.POOL, "get", side_effect=[(stale, True), (fresh, False)]):
            resp = shim.send_pooled("https://example.azure.com/x?y=1", b"b", {})
        stale.close.assert_called_once()
        fresh.request.assert_called_once_with("POST", "/x?y=1", body=b"b", headers={})
        self.assertFalse(resp.reused)

    def test_new_connection_failure_raises(self):
        broken = mock.Mock()
        broken.request.side_effect = ConnectionRefusedError()
        with mock.patch.object(shim.POOL, "get", return_value=(broken, False)):
            with self.assertRaises(ConnectionRefusedError):
                shim.send_pooled("https://example.azure.com/x", b"", {})

    def test_connection_returned_only_when_fully_read(self):
        pool = shim.ConnectionPool()
        conn = mock.Mock()
        done = mock.Mock(status=200, headers={}, will_close=False)
        done.isclosed.return_value = True
        with mock.patch.object(shim, "POOL", pool):
            shim.PooledResponse("h", conn, done, False).close()
            self.assertEqual(pool.get("h"), (conn, True))
            partial = mock.Mock(status=200, headers={}, will_close=False)
            partial.isclosed.return_value = False
            shim.PooledResponse("h", conn, partial, False).close()
            conn.close.assert_called_once()
            self.assertFalse(pool.get("h")[1])


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
        body, ctype = shim.encode_multipart(fields, {"file": ("audio.webm", "audio/webm", b"fake")})
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
            return tempfile.mkstemp(suffix="." + shim.UPLOAD_FORMATS[backend][0])[1]
        with mock.patch.object(shim, "convert_audio", fake_convert), \
             mock.patch.object(shim, "transcribe", return_value="hei") as t:
            status, data = self.post({"model": "llm-speech", "language": "fi", "prompt": "a, b"})
        self.assertEqual((status, data["text"]), (200, "hei"))
        self.assertEqual(t.call_args.args[1:], ("llm-speech", "fi", "a, b"))

    def test_models_lists_chat_models(self):
        with urllib.request.urlopen(self.url + "/v1/models") as r:
            ids = [m["id"] for m in json.loads(r.read())["data"]]
        self.assertEqual(ids, shim.CHAT_MODELS)

    def test_unknown_model_is_400(self):
        status, data = self.post({"model": "whisper-1"})
        self.assertEqual(status, 400)
        self.assertIn("unknown model", data["error"])


if __name__ == "__main__":
    unittest.main()
