# -*- coding: utf-8 -*-
"""H2 私人 GET 路由读鉴权：内存 handler 执行真实 do_GET 与 _send。"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_legion_ops_v2 as ops


PRIVATE_ROUTES = (
    "/api/legion/reviews/today",
    "/api/legion/ops",
    "/api/legion/agent?id=A1",
    "/api/legion/review?tid=T1",
    "/api/legion/academy/topics",
    "/api/legion/schedules",
)


class PrivateGetAuthTests(unittest.TestCase):
    def setUp(self):
        token_patch = patch.object(ops, "_load_token", return_value="s3cret")
        token_patch.start()
        self.addCleanup(token_patch.stop)

    def request(self, path, peer="192.168.1.5", headers=None):
        handler = object.__new__(ops.H2)
        handler.path = path
        handler.client_address = (peer, 50000)
        handler.headers = Message()
        for name, value in (headers or {}).items():
            handler.headers[name] = value
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        status = handler.send_response.call_args.args[0]
        return status, handler.wfile.getvalue(), handler

    def test_denial_precedes_all_private_data_access(self):
        attempts = (
            ("192.168.1.5", {}),
            ("192.168.1.5", {"X-Real-IP": "127.0.0.1"}),
            ("192.168.1.5", {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"}),
            ("172.17.0.1", {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"}),
            ("127.0.0.1", {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"}),
            ("192.168.1.5", {"X-Portal-Token": "wrong"}),
        )
        for route in PRIVATE_ROUTES:
            for peer, headers in attempts:
                with self.subTest(route=route, peer=peer, headers=headers), ExitStack() as stack:
                    guarded = [
                        stack.enter_context(patch.object(ops, name, side_effect=AssertionError(name)))
                        for name in ("build_roster", "build_feed", "build_tasks",
                                     "agent_detail", "review_task", "academy_summary", "schedules_op")
                    ]
                    for name in ("glob", "read_text", "read_bytes"):
                        guarded.append(stack.enter_context(
                            patch.object(Path, name, side_effect=AssertionError("Path." + name))))
                    status, body, _ = self.request(route, peer, headers)
                    self.assertEqual(status, 403)
                    self.assertEqual(json.loads(body), {"ok": False, "err": "read unauthorized"})
                    for operation in guarded:
                        operation.assert_not_called()

    def test_token_and_direct_loopback_preserve_json(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(ops, "ROOT", Path(directory)):
            root = Path(directory) / "agent_platform"
            day = root / "data" / "daily_review" / "2026-10-19"
            day.mkdir(parents=True)
            (day / "_summary.json").write_text("{}", encoding="utf-8")
            (day / "A1.md").write_text("①：有任务\n②：进行中\n③：完成", encoding="utf-8")
            org = root / "registry"
            org.mkdir(parents=True)
            (org / "org.json").write_text(
                json.dumps({"departments": [{"dept": "academy", "research_language": {"x": 1}}]}),
                encoding="utf-8")
            with patch.object(ops, "build_roster", return_value=["roster"]) as roster, \
                 patch.object(ops, "build_feed", return_value=["feed"]) as feed, \
                 patch.object(ops, "build_tasks", return_value=["tasks"]) as tasks, \
                 patch.object(ops, "agent_detail", return_value={"agent": "A1"}) as agent, \
                 patch.object(ops, "review_task", return_value={"review": "T1"}) as review, \
                 patch.object(ops, "academy_summary", return_value={"topics": [], "meta": {"complete": True}}) as topics, \
                 patch.object(ops, "schedules_op", return_value={"schedules": []}) as schedules:
                for peer, headers in (
                    ("192.168.1.5", {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1",
                                     "X-Portal-Token": "s3cret"}),
                    ("127.0.0.1", {}),
                ):
                    for route in PRIVATE_ROUTES:
                        with self.subTest(route=route, peer=peer):
                            status, body, _ = self.request(route, peer, headers)
                            self.assertEqual(status, 200)
                            result = json.loads(body)
                            if route.endswith("today"):
                                self.assertEqual(result, {"day": "2026-10-19", "reviews": {
                                    "A1": {"agent_id": "A1", "q1": "有任务", "q2": "进行中",
                                           "q3": "完成", "has_task": False}}})
                            elif route.endswith("ops"):
                                self.assertEqual(result["roster"], ["roster"])
                                self.assertEqual(result["feed"], ["feed"])
                                self.assertEqual(result["tasks"], ["tasks"])
                                self.assertEqual(result["academy_language"], {"x": 1})
                                self.assertEqual(result["academy_directions"], [])
                                self.assertIn("ts", result)
                            elif "agent?" in route:
                                self.assertEqual(result, {"agent": "A1"})
                            elif route.endswith("academy/topics"):
                                self.assertEqual(result, {"topics": [], "meta": {"complete": True}})
                            elif route.endswith("schedules"):
                                self.assertEqual(result, {"schedules": []})
                            else:
                                self.assertEqual(result, {"review": "T1"})
                self.assertEqual(roster.call_count, 2)
                self.assertEqual(feed.call_count, 2)
                self.assertEqual(tasks.call_count, 2)
                agent.assert_has_calls([unittest.mock.call("A1")] * 2)
                review.assert_has_calls([unittest.mock.call("T1")] * 2)
                self.assertEqual(topics.call_count, 2)
                schedules.assert_has_calls([unittest.mock.call({"op": "list"})] * 2)

    def test_public_get_routes_remain_unchanged(self):
        with patch.object(Path, "read_bytes", return_value=b"<html>legion</html>") as html:
            for path in ("/", "/legion.html"):
                status, body, handler = self.request(path)
                self.assertEqual((status, body), (200, b"<html>legion</html>"))
                self.assertIn(unittest.mock.call("Content-Type", "text/html; charset=utf-8"),
                              handler.send_header.call_args_list)
            self.assertEqual(html.call_count, 2)
            status, body, _ = self.request("/api/legion/unknown")
            self.assertEqual((status, json.loads(body)), (404, {"err": "not found"}))


if __name__ == "__main__":
    unittest.main()
