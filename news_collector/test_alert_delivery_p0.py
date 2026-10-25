# -*- coding: utf-8 -*-
"""预警 P0 投递与来源故障回归；只使用内存 mock，不访问产线状态/网络。"""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import alert_push
import alert_watch


def healthy_payload(alerts=None, statuses=None):
    """Construct independently timestamped current-period health evidence."""
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    statuses = statuses or {}
    sources = {source: {"status": statuses.get(source, "ok"),
                        "as_of": now, "reason": None}
               for source in alert_push.SOURCE_NAMES}
    for item in sources.values():
        if item["status"] not in ("ok", "empty"):
            item["reason"] = "source_error"
    return {"alerts": alerts if alerts is not None else [],
            "source_health": {"version": 1, "checked_at": now,
                              "sources": sources}}


class WatchDeliveryTest(unittest.TestCase):
    """验证多轮失败可重试且成功后才提交限额及基线。"""

    def setUp(self):
        self.state = {"date": "2026-10-17", "routes": {
            "九维警报池": {"num": 1, "raw": "1 只", "pushed": 0}}}
        self.save = mock.Mock(side_effect=lambda state: setattr(
            self, "state", json.loads(json.dumps(state))))
        self.url = mock.Mock()
        self.url.__enter__ = mock.Mock(return_value=self.url)
        self.url.__exit__ = mock.Mock(return_value=False)
        self.url.read.return_value = json.dumps(healthy_payload([
            {"name": "九维警报池", "value": "2 只"}])).encode("utf-8")
        self.patches = [
            # 心跳/状态文案会调用多种 strftime 格式；未映射的格式必须安全回退
            # 为格式串本身，避免新增格式（如 EXEC-R8 心跳的秒级 ts）直接 KeyError。
            mock.patch.object(alert_watch.time, "strftime", side_effect=lambda fmt: {
                "%Y-%m-%d": "2026-10-17", "%Y-%m-%d %H:%M": "2026-10-17 12:00",
                "%Y-%m-%d %H:%M:%S": "2026-10-17 12:00:00",
                "%m-%d %H:%M": "10-17 12:00"}.get(fmt, fmt)),
            mock.patch.object(alert_watch.urllib.request, "urlopen", return_value=self.url),
            mock.patch.object(alert_watch, "_load_state", side_effect=lambda: json.loads(json.dumps(self.state))),
            mock.patch.object(alert_watch, "_save_state", self.save),
            mock.patch.object(alert_push, "_fallback_alerts", return_value=None),
            mock.patch.object(alert_push, "_cfg", return_value={}),
            mock.patch.object(alert_push, "_targets", return_value=[{"name": "个人"}]),
            mock.patch("card_build.build_card3", side_effect=lambda *args, **kwargs: {"args": args, "kwargs": kwargs}),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_failed_delivery_never_spends_cap_or_advances_baseline(self):
        with mock.patch.object(alert_push, "push_card_multi", return_value={"个人": False}) as card, \
             mock.patch.object(alert_push, "push_text_multi", return_value={"个人": False}) as text, \
             contextlib.redirect_stdout(io.StringIO()):
            for _ in range(4):
                self.assertEqual(alert_watch.main(), 1)
                self.assertEqual(self.state["routes"]["九维警报池"],
                                 {"num": 1, "raw": "1 只", "pushed": 0})
        self.assertEqual(card.call_count, 4)
        self.assertEqual(text.call_count, 4)

    def test_retry_then_success_spends_one_and_advances_once(self):
        with mock.patch.object(alert_push, "push_card_multi", side_effect=[
                {"个人": False}, {"个人": True}, {"个人": True}]) as card, \
             mock.patch.object(alert_push, "push_text_multi", return_value={"个人": False}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_watch.main(), 1)
            self.assertEqual(alert_watch.main(), 0)
            self.assertEqual(alert_watch.main(), 0)
        self.assertEqual(card.call_count, 2)
        self.assertEqual(self.state["routes"]["九维警报池"],
                         {"num": 2, "raw": "2 只", "pushed": 1})

    def test_text_fallback_success_commits_baseline(self):
        with mock.patch.object(alert_push, "push_card_multi", side_effect=RuntimeError("card down")), \
             mock.patch.object(alert_push, "push_text_multi", return_value={"个人": True}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_watch.main(), 0)
        self.assertEqual(self.state["routes"]["九维警报池"]["pushed"], 1)

    def test_normal_daily_cap_blocks_fourth_success(self):
        with mock.patch.object(alert_push, "push_card_multi", return_value={"个人": True}) as card, \
             contextlib.redirect_stdout(io.StringIO()):
            for n in (2, 3, 4, 5):
                self.url.read.return_value = json.dumps(healthy_payload([
                    {"name": "九维警报池", "value": "%d 只" % n}
                ])).encode("utf-8")
                self.assertEqual(alert_watch.main(), 0)
        self.assertEqual(card.call_count, 3)
        self.assertEqual(self.state["routes"]["九维警报池"],
                         {"num": 5, "raw": "5 只", "pushed": 3})

    def test_invalid_source_never_reads_or_writes_state(self):
        self.url.read.return_value = b'{"error":"source down","alerts":null}'
        with mock.patch.object(alert_push, "push_card_multi") as card, \
             mock.patch.object(alert_watch, "_load_state") as load, \
             contextlib.redirect_stdout(io.StringIO()):
            # WO-DEV-20261022-AA：上游不可信=主动拒跑 exit 3（原 1 与崩溃混淆）
            self.assertEqual(alert_watch.main(), alert_watch.EXIT_UPSTREAM_UNTRUSTED)
        card.assert_not_called()
        load.assert_not_called()
        self.save.assert_not_called()

    def test_legacy_empty_payload_never_reads_state(self):
        self.url.read.return_value = b'{"alerts":[]}'
        with mock.patch.object(alert_watch, "_load_state") as load, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_watch.main(), alert_watch.EXIT_UPSTREAM_UNTRUSTED)
        load.assert_not_called()
        self.save.assert_not_called()

    def test_all_routes_bad_never_reads_state(self):
        self.url.read.return_value = json.dumps(healthy_payload(
            statuses={source: "error" for source in alert_push.EOD_SOURCES})).encode()
        with mock.patch.object(alert_watch, "_load_state") as load, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_watch.main(), alert_watch.EXIT_UPSTREAM_UNTRUSTED)
        load.assert_not_called()
        self.save.assert_not_called()

    def test_http_failure_uses_independently_validated_local_fallback(self):
        with mock.patch.object(alert_watch.urllib.request, "urlopen", side_effect=OSError("offline")), \
             mock.patch.object(alert_push, "_fallback_alerts", return_value=healthy_payload([
                 {"name": "九维警报池", "value": "2 只"}])) as fallback, \
             mock.patch.object(alert_push, "push_card_multi", return_value={"个人": True}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_watch.main(), 0)
        fallback.assert_called_once()
        self.assertEqual(self.state["routes"]["九维警报池"]["pushed"], 1)

    def test_legacy_http_and_local_fallback_never_read_state(self):
        self.url.read.return_value = b'{"alerts":[]}'
        with mock.patch.object(alert_push, "_fallback_alerts", return_value={"alerts": []}) as fallback, \
             mock.patch.object(alert_watch, "_load_state") as load, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_watch.main(), alert_watch.EXIT_UPSTREAM_UNTRUSTED)
        fallback.assert_called_once()
        load.assert_not_called()
        self.save.assert_not_called()

    def test_empty_positive_http_and_local_never_read_state(self):
        self.url.read.return_value = json.dumps(healthy_payload([
            {"name": "九维警报池", "value": "3 只"}],
            {"nine_dim": "empty"})).encode()
        with mock.patch.object(alert_push, "_fallback_alerts", return_value=healthy_payload([
                {"name": "缠论信号", "value": "买 2 · 卖 0"}],
                {"chanlun": "empty"})) as fallback, \
             mock.patch.object(alert_watch, "_load_state") as load, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_watch.main(), alert_watch.EXIT_UPSTREAM_UNTRUSTED)
        fallback.assert_called_once()
        load.assert_not_called()
        self.save.assert_not_called()

    def test_valid_empty_nine_dim_zero_updates_watch_baseline(self):
        self.url.read.return_value = json.dumps(healthy_payload([
            {"name": "九维警报池", "value": "0 只"}], {"nine_dim": "empty"})).encode()
        with mock.patch.object(alert_push, "push_card_multi") as card, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_watch.main(), 0)
        card.assert_not_called()
        self.assertEqual(self.state["routes"]["九维警报池"]["num"], 0)
        self.assertEqual(self.state["routes"]["九维警报池"]["pushed"], 0)

    def test_bad_route_does_not_advance_while_healthy_alert_is_sent(self):
        self.url.read.return_value = json.dumps(healthy_payload([
            {"name": "九维警报池", "value": "2 只"},
            {"name": "弱于板块在案", "value": "0 案"}],
            {"weak": "error"})).encode()
        original = json.loads(json.dumps(self.state))
        with mock.patch.object(alert_push, "push_card_multi", return_value={"个人": True}) as card, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_watch.main(), 0)
        self.assertEqual(self.state["routes"]["九维警报池"]["pushed"], 1)
        self.assertEqual(self.state["routes"].get("弱于板块在案"),
                         original["routes"].get("弱于板块在案"))
        self.assertIn("覆盖不足", str(card.call_args))


class ScheduledPushTest(unittest.TestCase):
    """可信空集仍发送空报；双源故障不会假装链路正常。"""

    def setUp(self):
        self.last = mock.Mock()
        self.patches = [
            mock.patch.object(alert_push, "_cfg", return_value={}),
            mock.patch.object(alert_push, "_targets", return_value=[{"name": "个人"}]),
            mock.patch.object(alert_push, "fetch_index", return_value=None),
            mock.patch.object(alert_push, "_save_last", self.last),
            mock.patch("card_build.build_card3", side_effect=lambda *args, **kwargs: {"args": args, "kwargs": kwargs}),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_both_sources_fail_no_normal_empty_card_or_text(self):
        with mock.patch.object(alert_push, "fetch_alerts", side_effect=OSError("portal down")), \
             mock.patch.object(alert_push, "_fallback_alerts", return_value=None), \
             mock.patch.object(alert_push, "push_card_multi") as card, \
             mock.patch.object(alert_push, "push_text_multi") as text, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(alert_push.main(), 1)
        self.assertIn("SOURCE_UNAVAILABLE", output.getvalue())
        self.assertNotIn("数据链路正常", output.getvalue())
        card.assert_not_called()
        text.assert_not_called()
        self.assertEqual(self.last.call_args.kwargs.get("status"), "source_unavailable")

    def test_malformed_fallback_is_unavailable(self):
        with mock.patch.object(alert_push, "fetch_alerts", side_effect=OSError("offline")), \
             mock.patch.object(alert_push, "_fallback_alerts", return_value={"alerts": None}), \
             mock.patch.object(alert_push, "push_card_multi") as card, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_push.main(), 1)
        card.assert_not_called()
        self.assertEqual(self.last.call_args.kwargs.get("status"), "source_unavailable")

    def test_trusted_empty_source_send_success(self):
        with mock.patch.object(alert_push, "fetch_alerts", return_value=healthy_payload()), \
             mock.patch.object(alert_push, "push_card_multi", return_value={"个人": True}) as card, \
             mock.patch.object(alert_push, "push_text_multi") as text, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_push.main(), 0)
        card.assert_called_once()
        text.assert_not_called()
        self.assertEqual(self.last.call_args.kwargs.get("status"), "sent")

    def test_trusted_empty_both_delivery_channels_fail(self):
        with mock.patch.object(alert_push, "fetch_alerts", return_value=healthy_payload()), \
             mock.patch.object(alert_push, "push_card_multi", return_value={"个人": False}), \
             mock.patch.object(alert_push, "push_text_multi", return_value={"个人": False}) as text, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_push.main(), 1)
        self.assertIn("数据链路正常", text.call_args.args[0])
        self.assertEqual(self.last.call_args.kwargs.get("status"), "failed")

    def test_invalid_portal_uses_valid_fallback(self):
        with mock.patch.object(alert_push, "fetch_alerts", return_value={"error": "down"}), \
             mock.patch.object(alert_push, "_fallback_alerts", return_value=healthy_payload()) as fallback, \
             mock.patch.object(alert_push, "push_card_multi", return_value={"个人": True}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_push.main(), 0)
        fallback.assert_called_once()
        self.assertEqual(self.last.call_args.kwargs.get("status"), "sent")

    def test_legacy_http_and_fallback_empty_are_untrusted(self):
        with mock.patch.object(alert_push, "fetch_alerts", return_value={"alerts": []}), \
             mock.patch.object(alert_push, "_fallback_alerts", return_value={"alerts": []}) as fallback, \
             mock.patch.object(alert_push, "push_card_multi") as card, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_push.main(), 1)
        fallback.assert_called_once()
        card.assert_not_called()
        self.assertEqual(self.last.call_args.kwargs["status"], "source_unavailable")

    def test_all_bad_sources_refuse_empty_card(self):
        with mock.patch.object(alert_push, "fetch_alerts", return_value=healthy_payload(
                statuses={source: "error" for source in alert_push.EOD_SOURCES})), \
             mock.patch.object(alert_push, "_fallback_alerts", return_value=None), \
             mock.patch.object(alert_push, "push_card_multi") as card, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_push.main(), 1)
        card.assert_not_called()
        self.assertEqual(self.last.call_args.kwargs["status"], "source_unavailable")

    def test_partial_healthy_alert_is_marked_and_bad_route_excluded(self):
        payload = healthy_payload([
            {"name": "九维警报池", "value": "3 只"},
            {"name": "弱于板块在案", "value": "0 案"}], {"weak": "error"})
        with mock.patch.object(alert_push, "fetch_alerts", return_value=payload), \
             mock.patch.object(alert_push, "push_card_multi", return_value={"个人": True}) as card, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alert_push.main(), 0)
        self.assertIn("覆盖不足", str(card.call_args))
        self.assertNotIn("0 案", str(card.call_args))

    def test_partial_empty_and_morning_missing_both_refuse_delivery(self):
        for statuses, morning in [({"weak": "error"}, False),
                                  ({"chanlun": "error", "nine_dim": "stale"}, True)]:
            with self.subTest(morning=morning), \
                 mock.patch.object(sys, "argv", ["alert_push.py"] + (["--morning"] if morning else [])), \
                 mock.patch.object(alert_push, "fetch_alerts", return_value=healthy_payload(statuses=statuses)), \
                 mock.patch.object(alert_push, "_fallback_alerts", return_value=None), \
                 mock.patch.object(alert_push, "push_card_multi") as card, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(alert_push.main(), 1)
                card.assert_not_called()
                self.assertEqual(self.last.call_args.kwargs["status"], "source_unavailable")

    def test_weak_cross_only_cannot_certify_coverage(self):
        payload = healthy_payload(statuses={source: "error" for source in alert_push.CORE_SOURCES})
        self.assertIsNone(alert_push.validated_alerts(payload))

    def test_unknown_failure_reason_is_rejected(self):
        payload = healthy_payload(statuses={"weak": "error"})
        payload["source_health"]["sources"]["weak"]["reason"] = "arbitrary"
        self.assertIsNone(alert_push.validated_alerts(payload))

    def test_empty_status_requires_matching_zero_card(self):
        cases = (("nine_dim", "九维警报池", "3 只", "0 只"),
                 ("weak", "弱于板块在案", "2 案", "0 案"),
                 ("cross", "共振做多", "2 只", "0 只"),
                 ("chanlun", "缠论信号", "买 2 · 卖 0", "买 0 · 卖 0"),
                 ("premarket", "盘前倾向", "偏多", "暂无"))
        for source, name, contradictory, zero in cases:
            with self.subTest(source=source):
                payload = healthy_payload([{"name": name, "value": contradictory}],
                                          {source: "empty"})
                self.assertIsNone(alert_push.validated_alerts(payload))
                payload["alerts"][0]["value"] = "不可解析"
                self.assertIsNone(alert_push.validated_alerts(payload))
                payload["alerts"][0]["value"] = zero
                self.assertEqual(alert_push.validated_alerts(payload)[0]["alerts"], [])

    def test_empty_contradiction_never_sends_normal_card_on_dual_invalid(self):
        for source, name, positive in (("nine_dim", "九维警报池", "3 只"),
                                       ("weak", "弱于板块在案", "2 案"),
                                       ("chanlun", "缠论信号", "买 2 · 卖 0")):
            with self.subTest(source=source), \
                 mock.patch.object(alert_push, "fetch_alerts", return_value=healthy_payload(
                     [{"name": name, "value": positive}], {source: "empty"})), \
                 mock.patch.object(alert_push, "_fallback_alerts", return_value={"alerts": []}) as fallback, \
                 mock.patch.object(alert_push, "push_card_multi") as card, \
                 mock.patch.object(alert_push, "push_text_multi") as text, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(alert_push.main(), 1)
                fallback.assert_called_once()
                card.assert_not_called()
                text.assert_not_called()
                self.assertEqual(self.last.call_args.kwargs["status"], "source_unavailable")

    def test_non_null_reason_for_healthy_status_is_rejected(self):
        payload = healthy_payload()
        payload["source_health"]["sources"]["nine_dim"]["reason"] = "valid"
        self.assertIsNone(alert_push.validated_alerts(payload))
        payload["source_health"]["sources"]["nine_dim"].update(
            {"status": "empty", "reason": "valid_empty"})
        self.assertIsNone(alert_push.validated_alerts(payload))

    def test_unknown_card_and_naive_checked_at_rejected(self):
        payload = healthy_payload([{"name": "未知源", "value": "8 只"}])
        self.assertIsNone(alert_push.validated_alerts(payload))
        payload = healthy_payload()
        payload["source_health"]["checked_at"] = datetime.now().isoformat()
        self.assertIsNone(alert_push.validated_alerts(payload))

    def test_checked_at_never_substitutes_for_stale_source_as_of(self):
        payload = healthy_payload()
        payload["source_health"]["sources"]["nine_dim"]["as_of"] = (
            datetime.now(timezone(timedelta(hours=8))) - timedelta(days=1)).isoformat()
        self.assertEqual(alert_push.validated_alerts(payload)[1], ("nine_dim",))
        payload["source_health"]["sources"]["nine_dim"]["as_of"] = None
        self.assertEqual(alert_push.validated_alerts(payload)[1], ("nine_dim",))
        payload["source_health"]["sources"]["chanlun"].update(
            {"status": "error", "reason": "source_error"})
        self.assertIsNone(alert_push.validated_alerts(payload, morning=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
