# -*- coding: utf-8 -*-
"""G3 决议只读投影专项测试：隔离房间数据、临时端口运行真实 H2 路由。"""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))
sys.path.insert(0, str(ROOT / "tools"))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402

import api_legion_ops_v2 as ops
import legion_meeting_room as mr


class ResolutionProjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="legion_g3_projection_")
        cls.room_dir = Path(cls.tmp.name)
        cls.room_patch = patch.object(mr, "ROOM_DIR", cls.room_dir)
        cls.token_patch = patch.object(ops, "_load_token", return_value="g3-test-token")
        cls.room_patch.start()
        cls.token_patch.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ops.H2)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        room = mr.create_room("G3", "只读决议", created_by="USER", room_id="room-g3-api")
        room = mr.board_add_card(room["room_id"], "decision", "冻结的议事结论", author="USER")
        room = mr.board_resolve_card(room["room_id"], room["board"][0]["card_id"], author="USER")
        cls.room_id = room["room_id"]
        cls.resolution_id = room["resolutions"][0]["resolution_id"]
        cls.room_file = cls.room_dir / (cls.room_id + ".json")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.token_patch.stop()
        cls.room_patch.stop()
        cls.tmp.cleanup()

    def request(self, room_id=None, resolution_id=None, headers=None, method="GET"):
        query = urllib.parse.urlencode({
            "room_id": self.room_id if room_id is None else room_id,
            "resolution_id": self.resolution_id if resolution_id is None else resolution_id,
        })
        req = urllib.request.Request(
            "http://127.0.0.1:%d/api/legion/room/resolution?%s" % (self.server.server_port, query),
            headers=headers or {}, method=method,
        )
        try:
            with safe_urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_existing_resolution_is_read_only_and_unapproved(self):
        before = self.room_file.read_bytes()
        code, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(body["integrity"], {
            "resolution_id": self.resolution_id, "integrity": "valid",
            "approval_status": "not_verified", "actionable": False,
        })
        self.assertEqual(body["resolution"]["text"], "冻结的议事结论")
        self.assertEqual(body["resolution"]["room_id"], self.room_id)
        self.assertEqual(self.room_file.read_bytes(), before)

    def test_private_read_authorization_precedes_lookup(self):
        # 临时服务绑定环回；带反代标记的环回 TCP 对端同样必须持 token。
        remote = {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.5"}
        code, body = self.request(room_id="room-missing", headers=remote)
        self.assertEqual((code, body), (403, {"ok": False, "err": "read unauthorized"}))
        code, body = self.request(headers={**remote, "X-Portal-Token": "wrong"})
        self.assertEqual(code, 403)
        code, body = self.request(headers={**remote, "X-Portal-Token": "g3-test-token"})
        self.assertEqual(code, 200)
        self.assertEqual(body["integrity"]["approval_status"], "not_verified")

    def test_missing_parameters_room_and_resolution_never_project(self):
        for room_id, resolution_id, expected in [
            ("", self.resolution_id, 400), (self.room_id, "", 400),
            ("room-missing", self.resolution_id, 404),
            (self.room_id, "res-missing", 404),
            ("../room-g3-api", self.resolution_id, 404),
        ]:
            with self.subTest(room_id=room_id, resolution_id=resolution_id):
                code, body = self.request(room_id, resolution_id)
                self.assertEqual(code, expected)
                self.assertFalse(body["ok"])
                self.assertNotIn("integrity", body)

    def test_tampering_and_fake_approval_do_not_become_actionable(self):
        original = self.room_file.read_bytes()
        try:
            for mutation in ("text", "status", "card", "duplicate"):
                with self.subTest(mutation=mutation):
                    room = json.loads(original)
                    if mutation == "text":
                        room["resolutions"][0]["text"] = "更换结论"
                    elif mutation == "status":
                        room["resolutions"][0]["status"] = "approved"
                        room["board"][0]["status"] = "done"
                    elif mutation == "card":
                        room["board"][0]["text"] = "更换白板卡"
                    else:
                        room["resolutions"].append(dict(room["resolutions"][0]))
                    self.room_file.write_text(json.dumps(room, ensure_ascii=False), encoding="utf-8")
                    code, body = self.request()
                    self.assertEqual(code, 200)
                    self.assertEqual(body["integrity"]["integrity"], "invalid")
                    self.assertEqual(body["integrity"]["approval_status"], "not_verified")
                    self.assertIs(body["integrity"]["actionable"], False)
                    self.assertIsNone(body["resolution"])
        finally:
            self.room_file.write_bytes(original)

    def test_no_post_approval_route(self):
        code, body = self.request(method="POST")
        self.assertEqual(code, 404)
        self.assertEqual(body, {"err": "not found"})


if __name__ == "__main__":
    unittest.main()
