# -*- coding: utf-8 -*-
"""对话入口 HTTP 测试（/api/analysis/dialog/*，工单 K-DIALOG-C1-20261107）。"""
import io
import json
import os
import sys
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, os.path.join(WT, "portal"))
sys.path.insert(0, WT)

import api_dialog as mod  # noqa: E402


class FakeStore:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def list_sessions(self, customer_id, limit=30):
        return []


class FakeService:
    def __init__(self):
        self.store = FakeStore()
        self.calls = []

    class _Client:
        def health(self):
            return {"ok": True, "db": True}

    client = _Client()

    def list_sessions(self):
        self.calls.append(("list",))
        return {"ok": True, "sessions": [{"id": "sess_1", "title": "分析 600337.SH"}]}

    def get_session(self, sid):
        self.calls.append(("get", sid))
        if sid == "missing":
            return {"ok": False, "error": "会话不存在"}
        return {"ok": True, "session": {"id": sid, "state": "ready"},
                "turns": [], "question": None, "subscriptions": []}

    def handle_message(self, text, session_id=None, channel="pc"):
        self.calls.append(("message", text, session_id, channel))
        return {"ok": True, "session": {"id": session_id or "sess_new"},
                "reply": {"text": "已收到"}, "turns": [], "question": None}

    def handle_answer(self, sid, qid, option_key=None, text=None):
        self.calls.append(("answer", sid, qid, option_key))
        return {"ok": True, "session": {"id": sid}, "reply": {"text": "ok"},
                "turns": [], "question": None}

    def set_profile(self, **prefs):
        self.calls.append(("profile", tuple(sorted(prefs.items()))))
        return {"ok": True, "profile": dict(prefs)}


class FakeHandler:
    def __init__(self, headers=None, body=None):
        self.client_address = ("127.0.0.1", 1234)
        self.headers = dict(headers or {})
        raw = json.dumps(body if body is not None else {}).encode("utf-8")
        self.headers.setdefault("Content-Length", str(len(raw)))
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
        self.obj = None

    def _json(self, obj, status=200):
        self.status = status
        self.obj = obj

    def _check_portal_token(self):
        return self.headers.get("X-Portal-Token") == "tok"


class FakeDialogue:
    DB = "/tmp/dialog.db"


class TestDialogApi(unittest.TestCase):
    def setUp(self):
        self.svc = FakeService()
        self._orig_service = mod._service
        self._orig_dialog = mod._dialog
        mod._service = lambda d: self.svc
        mod._dialog = lambda: FakeDialogue

    def tearDown(self):
        mod._service = self._orig_service
        mod._dialog = self._orig_dialog

    def _h(self, body=None, token=True):
        h = FakeHandler(body=body)
        if token:
            h.headers["X-Portal-Token"] = "tok"
        return h

    # ---- GET ----
    def test_health(self):
        h = self._h()
        mod.handle_get(h, "/api/analysis/dialog/health", "")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertEqual(h.obj["gateway"], {"ok": True, "db": True})
        self.assertTrue(self.svc.store.closed)

    def test_sessions(self):
        h = self._h()
        mod.handle_get(h, "/api/analysis/dialog/sessions", "")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.obj["sessions"][0]["id"], "sess_1")

    def test_session_missing_id(self):
        h = self._h()
        mod.handle_get(h, "/api/analysis/dialog/session", "")
        self.assertEqual(h.status, 400)
        self.assertIn("缺少 id", h.obj["error"])

    def test_session_not_found(self):
        h = self._h()
        mod.handle_get(h, "/api/analysis/dialog/session", "id=missing")
        self.assertEqual(h.status, 404)
        self.assertFalse(h.obj["ok"])

    def test_unknown_get(self):
        h = self._h()
        mod.handle_get(h, "/api/analysis/dialog/nope", "")
        self.assertEqual(h.status, 404)

    # ---- POST 鉴权与校验 ----
    def test_post_requires_token(self):
        h = self._h(body={"text": "600337.SH"}, token=False)
        mod.handle_post(h, "/api/analysis/dialog/message", "")
        self.assertEqual(h.status, 403)
        self.assertIn("X-Portal-Token", h.obj["error"])
        self.assertEqual(self.svc.calls, [])

    def test_message_empty_text(self):
        h = self._h(body={"text": "  "})
        mod.handle_post(h, "/api/analysis/dialog/message", "")
        self.assertEqual(h.status, 400)
        self.assertIn("text", h.obj["error"])

    def test_message_ok(self):
        h = self._h(body={"text": "分析 600337.SH", "channel": "pc"})
        mod.handle_post(h, "/api/analysis/dialog/message", "")
        self.assertEqual(h.status, 200)
        self.assertTrue(h.obj["ok"])
        self.assertEqual(self.svc.calls[0], ("message", "分析 600337.SH", None, "pc"))
        self.assertTrue(self.svc.store.closed)

    def test_answer_missing_fields(self):
        h = self._h(body={"session_id": "s1"})
        mod.handle_post(h, "/api/analysis/dialog/answer", "")
        self.assertEqual(h.status, 400)
        self.assertIn("question_id", h.obj["error"])

    def test_answer_ok(self):
        h = self._h(body={"session_id": "s1", "question_id": "q1", "option_key": "risk"})
        mod.handle_post(h, "/api/analysis/dialog/answer", "")
        self.assertEqual(h.status, 200)
        self.assertEqual(self.svc.calls[0], ("answer", "s1", "q1", "risk"))

    def test_profile_invalid_field(self):
        h = self._h(body={"foo": 1})
        mod.handle_post(h, "/api/analysis/dialog/profile", "")
        self.assertEqual(h.status, 400)
        self.assertIn("followup_enabled", h.obj["error"])

    def test_profile_ok(self):
        h = self._h(body={"followup_enabled": False})
        mod.handle_post(h, "/api/analysis/dialog/profile", "")
        self.assertEqual(h.status, 200)
        self.assertEqual(self.svc.calls[0], ("profile", (("followup_enabled", False),)))

    def test_bad_json(self):
        h = FakeHandler()
        raw = b"not-json"
        h.headers["Content-Length"] = str(len(raw))
        h.rfile = io.BytesIO(raw)
        h.headers["X-Portal-Token"] = "tok"
        mod.handle_post(h, "/api/analysis/dialog/message", "")
        self.assertEqual(h.status, 400)
        self.assertIn("bad json", h.obj["error"])

    def test_unknown_post(self):
        h = self._h(body={})
        mod.handle_post(h, "/api/analysis/dialog/whatever", "")
        self.assertEqual(h.status, 404)
        self.assertIn("unknown dialog post", h.obj["error"])

    def test_dialog_core_missing_returns_503(self):
        mod._dialog = lambda: None
        h = self._h()
        mod.handle_get(h, "/api/analysis/dialog/health", "")
        self.assertEqual(h.status, 503)
        h2 = self._h(body={"text": "x"})
        mod.handle_post(h2, "/api/analysis/dialog/message", "")
        self.assertEqual(h2.status, 503)


if __name__ == "__main__":
    unittest.main()
