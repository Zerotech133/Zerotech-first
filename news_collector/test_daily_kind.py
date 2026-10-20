# -*- coding: utf-8 -*-
"""P3.6-lite K-A：daily:alert/daily:report 发送侧 kind 透传回归。

纯 mock 隔离：feishu_client.send_msg / subprocess.run / push_flow 全部
打桩，绝不真发、不写流水账、不落真实 outbox。
"""
import inspect
import json
import os
import re
import runpy
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(HERE)
for _p in (HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import card_build
import send_feishu


def _ok_run(stdout="SEND_OK"):
    return Mock(return_value=SimpleNamespace(stdout=stdout, stderr=""))


class SendOpenapiKind(unittest.TestCase):
    """_send_openapi → feishu_client.send_msg 透传契约。"""

    def test_kind_none_keeps_legacy_call(self):
        with patch("feishu_client.send_msg", return_value=(True, "")) as m:
            ok, why = send_feishu._send_openapi("{}", "interactive")
        self.assertTrue(ok)
        self.assertEqual(m.call_args.args[:2], ("{}", "interactive"))
        self.assertNotIn("kind", m.call_args.kwargs)

    def test_kind_passthrough_as_kwarg(self):
        with patch("feishu_client.send_msg", return_value=(True, "")) as m:
            ok, _ = send_feishu._send_openapi("{}", "interactive",
                                              kind="daily:alert")
        self.assertTrue(ok)
        self.assertEqual(m.call_args.kwargs.get("kind"), "daily:alert")

    def test_send_content_to_kind_passthrough(self):
        with patch("feishu_client.send_msg", return_value=(True, "")) as m:
            ok = send_feishu.send_content_to(
                "oc_grp", "chat_id", "{}", "interactive", kind="daily:report")
        self.assertTrue(ok)
        self.assertEqual(m.call_args.args,
                         ("{}", "interactive", "oc_grp", "chat_id"))
        self.assertEqual(m.call_args.kwargs.get("kind"), "daily:report")


class SendContentKind(unittest.TestCase):
    """send_content 主入口 kind 语义 + 失败回退路径不受 kind 影响。"""

    def test_send_content_kind_none_no_kind_kwarg(self):
        with patch("feishu_client.send_msg", return_value=(True, "")) as m:
            ok = send_feishu.send_content("{}", "interactive")
        self.assertTrue(ok)
        self.assertNotIn("kind", m.call_args.kwargs)

    def test_send_content_kind_openapi_success(self):
        with patch("feishu_client.send_msg", return_value=(True, "")) as m:
            ok = send_feishu.send_content("{}", "interactive",
                                          kind="daily:report")
        self.assertTrue(ok)
        self.assertEqual(m.call_args.kwargs.get("kind"), "daily:report")

    def test_send_content_kind_falls_back_to_cli_without_error(self):
        # openapi 败 + 无 lark-cli 可执行 → SEND_FAIL，kind 不改变回退语义
        with patch("feishu_client.send_msg", return_value=(False, "boom")), \
             patch.object(send_feishu, "find_lark_cli", return_value=None):
            ok = send_feishu.send_content("{}", "interactive",
                                          kind="daily:alert")
        self.assertFalse(ok)


class CliKindFlag(unittest.TestCase):
    """send_feishu.py __main__：--kind=X 解析（runpy 驱动，send_msg 打桩）。"""

    def _run_cli(self, argv, kind_expected, msg_type_expected):
        fd, path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("{}")
            with patch.object(sys, "argv",
                              ["send_feishu.py", path] + argv), \
                 patch("feishu_client.send_msg", return_value=(True, "")) as m:
                with self.assertRaises(SystemExit) as ctx:
                    runpy.run_path(
                        os.path.join(HERE, "send_feishu.py"),
                        run_name="__main__")
            self.assertEqual(ctx.exception.code, 0)
            self.assertEqual(m.call_args.args[1], msg_type_expected)
            self.assertEqual(m.call_args.kwargs.get("kind"), kind_expected)
        finally:
            os.unlink(path)

    def test_cli_legacy_two_args_unchanged(self):
        self._run_cli(["interactive"], None, "interactive")

    def test_cli_kind_flag_interactive(self):
        self._run_cli(["interactive", "--kind=daily:report"],
                      "daily:report", "interactive")

    def test_cli_kind_flag_text(self):
        self._run_cli(["--kind=daily:alert"], "daily:alert", "text")


class CardBuildKind(unittest.TestCase):
    """card_build.push_card：subprocess argv 追加契约。"""

    CARD = {"header": {}, "elements": []}

    def _push(self, kind):
        tmp = tempfile.mkdtemp()
        with patch.object(card_build, "HERE", tmp), \
             patch.object(card_build.subprocess, "run", _ok_run()) as m:
            ok = card_build.push_card(dict(self.CARD), kind=kind)
        self.assertTrue(ok)
        return m.call_args.args[0]

    def test_push_card_kind_none_argv_unchanged(self):
        argv = self._push(None)
        self.assertFalse(any(a.startswith("--kind=") for a in argv))
        self.assertEqual(argv[-1], "interactive")
        self.assertEqual(len(argv), 4)  # [python, send_feishu.py, tmp, interactive]

    def test_push_card_kind_appended_once(self):
        argv = self._push("daily:report")
        self.assertEqual(
            [a for a in argv if a.startswith("--kind=")],
            ["--kind=daily:report"])
        self.assertEqual(argv[-1], "--kind=daily:report")
        self.assertEqual(argv[-2], "interactive")
        self.assertEqual(len(argv), 5)  # 原 4 元素 + kind 追加 1


class PushCardMultiKind(unittest.TestCase):
    """alert_push.push_card_multi：首腿/次腿各透传 kind。"""

    TARGETS = [{"name": "个人", "type": "open_id", "id": None},
               {"name": "量化群", "type": "chat_id", "id": "oc_x"}]

    def test_both_legs_receive_kind(self):
        card = {"header": {}, "elements": []}
        with patch.object(card_build, "push_card",
                          return_value=True) as first, \
             patch.object(send_feishu, "send_content_to",
                          return_value=True) as second, \
             patch.dict(sys.modules, {"push_flow": Mock()}):
            import alert_push
            res = alert_push.push_card_multi(card, targets=self.TARGETS,
                                             kind="daily:alert")
        self.assertEqual(res, {"个人": True, "量化群": True})
        self.assertEqual(first.call_args.kwargs.get("kind"), "daily:alert")
        self.assertEqual(second.call_args.kwargs.get("kind"), "daily:alert")
        self.assertEqual(second.call_args.args[3], "interactive")

    def test_default_kind_is_daily_alert(self):
        card = {"header": {}, "elements": []}
        with patch.object(card_build, "push_card", return_value=True) as first, \
             patch.object(send_feishu, "send_content_to", return_value=True), \
             patch.dict(sys.modules, {"push_flow": Mock()}):
            import alert_push
            alert_push.push_card_multi(card, targets=self.TARGETS)
        self.assertEqual(first.call_args.kwargs.get("kind"), "daily:alert")


class DailyReportContract(unittest.TestCase):
    """daily_report.py 零改动核对：:256 已传 kind，静态守护。"""

    def test_daily_report_passes_kind_report(self):
        with open(os.path.join(HERE, "daily_report.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertRegex(
            src, r"push_card_multi\(card, targets=_targets\(cfg\),"
                 r" kind=\"daily:report\"\)")

    def test_push_card_multi_signature_has_kind(self):
        import alert_push
        self.assertIn("kind", inspect.signature(
            alert_push.push_card_multi).parameters)

    def test_send_msg_wrapper_contract_documented(self):
        # feishu_client.py 零改动红线：本工位透传仅靠 kwargs 追加，
        # 回归确认 _send_openapi 在 kind=None 时绝不追加 kwargs。
        with patch("feishu_client.send_msg", return_value=(True, "")) as m:
            send_feishu._send_openapi("{}", "text", "ou_a", "open_id")
        self.assertEqual(m.call_args.kwargs, {})


if __name__ == "__main__":
    unittest.main()
