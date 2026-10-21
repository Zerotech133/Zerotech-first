# -*- coding: utf-8 -*-
"""G3 发布前离线安全闸门：仅使用临时房间和内存 HTTP handler。"""
import ast
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))
sys.path.insert(0, str(ROOT / "tools"))

import api_legion_ops_v2 as ops
import legion_meeting_room as rooms


class CapturedHandler(ops.H2):
    def send_response(self, code, message=None):
        self.response_code = code

    def send_header(self, key, value):
        pass

    def end_headers(self):
        pass


class LegionG3ReleaseGate(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="legion_g3_gate_")
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        room_patch = patch.object(rooms, "ROOM_DIR", self.temp)
        token_patch = patch.object(ops, "_load_token", return_value="offline-test-token")
        room_patch.start()
        token_patch.start()
        self.addCleanup(token_patch.stop)
        self.addCleanup(room_patch.stop)

        room = rooms.create_room("离线闸门", "只读验证", created_by="USER",
                                 room_id="room-release-gate", data_dir=self.temp)
        room = rooms.board_add_card(room["room_id"], "decision", "离线决议正文-不可批准",
                                    author="USER", data_dir=self.temp)
        room = rooms.board_resolve_card(room["room_id"], room["board"][0]["card_id"],
                                        author="USER", data_dir=self.temp)
        self.room = room
        self.room_id = room["room_id"]
        self.resolution_id = room["resolutions"][0]["resolution_id"]
        self.room_file = self.temp / (self.room_id + ".json")
        self.query = "/api/legion/room/resolution?room_id=%s&resolution_id=%s" % (
            self.room_id, self.resolution_id)

    def request(self, path, *, method="GET", headers=None, payload=None, peer="127.0.0.1"):
        handler = object.__new__(CapturedHandler)
        handler.path = path
        handler.client_address = (peer, 12345)
        handler.headers = dict(headers or {})
        raw = json.dumps(payload or {}).encode("utf-8")
        handler.headers["Content-Length"] = str(len(raw))
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        getattr(handler, "do_" + method)()
        data = handler.wfile.getvalue()
        return handler.response_code, json.loads(data) if data else None, data

    def test_self_reported_user_and_portal_token_do_not_approve(self):
        self.assertEqual(self.room["created_by"], "USER")
        self.assertEqual(self.room["resolutions"][0]["proposed_by"], "USER")
        self.assertEqual(self.room["resolutions"][0]["status"], "proposed")
        self.assertNotIn("approved", rooms.RESOLUTION_STATUSES)
        before = self.room_file.read_bytes()
        for headers in ({}, {"X-Portal-Token": "offline-test-token"},
                        {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1",
                         "X-Portal-Token": "offline-test-token"}):
            with self.subTest(headers=headers):
                code, body, _ = self.request(self.query, headers=headers)
                self.assertEqual(code, 200)
                self.assertIs(body["ok"], True)
                self.assertEqual(body["resolution"]["text"], "离线决议正文-不可批准")
                self.assertEqual(body["resolution"]["status"], "proposed")
                self.assertEqual(body["integrity"], {
                    "resolution_id": self.resolution_id, "integrity": "valid",
                    "approval_status": "not_verified", "actionable": False,
                })
        self.assertEqual(self.room_file.read_bytes(), before)

    def test_invalid_or_duplicate_associations_hide_resolution_body(self):
        changes = {
            "cross_room": lambda r: r["resolutions"][0].update(room_id="another-room"),
            "missing_card": lambda r: r["resolutions"][0].update(card_id="card-missing"),
            "changed_text": lambda r: r["board"][0].update(text="被篡改的正文"),
            "fake_approval": lambda r: r["resolutions"][0].update(status="approved"),
            "duplicate_resolution": lambda r: r["resolutions"].append(copy.deepcopy(r["resolutions"][0])),
            "duplicate_card": lambda r: r["board"].append(copy.deepcopy(r["board"][0])),
            "duplicate_binding": lambda r: r["resolutions"].append({
                **r["resolutions"][0], "resolution_id": "res-other"}),
        }
        before = self.room_file.read_bytes()
        for name, change in changes.items():
            with self.subTest(change=name):
                altered = copy.deepcopy(self.room)
                change(altered)
                self.assertEqual(rooms.resolution_integrity(altered, self.resolution_id), {
                    "resolution_id": self.resolution_id, "integrity": "invalid",
                    "approval_status": "not_verified", "actionable": False,
                })
                with patch.object(ops, "load_room", return_value=altered):
                    code, body, raw = self.request(self.query)
                self.assertEqual(code, 200)
                self.assertIs(body["ok"], True)
                self.assertIsNone(body["resolution"])
                self.assertEqual(body["integrity"]["integrity"], "invalid")
                self.assertEqual(body["integrity"]["approval_status"], "not_verified")
                self.assertIs(body["integrity"]["actionable"], False)
                self.assertNotIn("离线决议正文-不可批准", raw.decode("utf-8"))
                self.assertNotIn("被篡改的正文", raw.decode("utf-8"))
        self.assertEqual(self.room_file.read_bytes(), before)

    def test_forged_proxy_headers_never_bypass_token(self):
        spoof = {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"}
        for peer in ("127.0.0.1", "10.9.8.7", "172.17.0.1", ops.PROXY_PEER_IPS[0]):
            for headers in (spoof, {**spoof, "X-Portal-Token": "wrong"}):
                with self.subTest(peer=peer, headers=headers):
                    handler = object.__new__(CapturedHandler)
                    handler.client_address = (peer, 12345)
                    handler.headers = headers
                    self.assertFalse(handler._authorized_private_read())
                    self.assertFalse(handler._authorized_mutation())
                    code, body, raw = self.request(self.query, peer=peer, headers=headers)
                    self.assertEqual((code, body), (403, {"ok": False, "err": "read unauthorized"}))
                    self.assertNotIn("离线决议正文", raw.decode("utf-8"))
        code, body, _ = self.request(self.query, headers={**spoof, "X-Portal-Token": "offline-test-token"})
        self.assertEqual(code, 200)
        self.assertEqual(body["integrity"]["approval_status"], "not_verified")

    def test_no_post_approval_route_in_this_batch(self):
        source = ast.parse((ROOT / "portal" / "api_legion_ops_v2.py").read_text(encoding="utf-8"))
        post_methods = [method for cls in source.body if isinstance(cls, ast.ClassDef)
                        and cls.name in ("H", "H2") for method in cls.body
                        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and method.name == "do_POST"]
        self.assertTrue(post_methods, "缺少 POST 路由定义，不能验证审批边界")
        paths = {node.value for method in post_methods for node in ast.walk(method)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)
                 and node.value.startswith("/api/legion/")}
        self.assertIn("/api/legion/room/board", paths)
        self.assertFalse([path for path in paths if "approv" in path.lower() or "resolution" in path.lower()], paths)
        for path in ("/api/legion/room/resolution", "/api/legion/room/approve",
                     "/api/legion/room/resolution/approve", "/api/legion/approvals"):
            with self.subTest(path=path):
                code, body, _ = self.request(path, method="POST",
                                             headers={"X-Portal-Token": "offline-test-token"})
                self.assertEqual((code, body), (404, {"err": "not found"}))
        self.assertEqual(self.room["resolutions"][0]["status"], "proposed")

    def test_missing_get_and_page_fail_explicitly(self):
        for path, expected in (("/api/legion/room/resolution", 400),
                               ("/api/legion/room/resolution?room_id=room-release-gate", 400),
                               ("/api/legion/room/resolution?room_id=missing&resolution_id=" + self.resolution_id, 404),
                               ("/api/legion/room/resolution?room_id=" + self.room_id + "&resolution_id=missing", 404),
                               ("/api/legion/unknown", 404)):
            with self.subTest(path=path):
                code, body, _ = self.request(path)
                self.assertEqual(code, expected)
                self.assertFalse(body.get("ok", False))
                self.assertNotIn("resolution", body)
        with patch.object(ops, "ROOT", self.temp):
            for path in ("/", "/legion.html"):
                with self.subTest(page=path):
                    code, body, _ = self.request(path)
                    self.assertEqual(code, 500)
                    self.assertTrue(body.get("err"))


if __name__ == "__main__":
    unittest.main()
