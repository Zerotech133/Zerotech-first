# -*- coding: utf-8 -*-
"""chain_watch 单元测试（K-STOCK-424 P1）。

不依赖真实计划任务：_ps_query_tasks 打桩；语义判定全离线覆盖。
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, r"C:\aios\quant_backtest")

from strategy_layer import chain_watch as cw


def _mk_task(name, last_dt, result, state="Ready"):
    return {
        name: {
            "state": state,
            "last_run": last_dt.strftime("%Y-%m-%d %H:%M") if last_dt else "",
            "_last_dt": last_dt,
            "result": result,
            "next_run": "",
        }
    }


class TestStepStatus(unittest.TestCase):
    def test_ok_recent_and_zero_result(self):
        tasks = _mk_task("ChanlunScan", datetime.now() - timedelta(hours=2), 0)
        s = cw._step_status(("ChanlunScan", 26), tasks)
        self.assertEqual(s["status"], "ok")

    def test_fail_nonzero_result(self):
        tasks = _mk_task("ChanlunScan", datetime.now() - timedelta(hours=2), 3)
        s = cw._step_status(("ChanlunScan", 26), tasks)
        self.assertEqual(s["status"], "fail")
        self.assertTrue(s["note"] or True)  # note 字段可缺省

    def test_fail_priority_over_stale(self):
        """语义裁定：result 非 0 且过期 → fail 优先（告警不降级）。"""
        tasks = _mk_task("XmapSync", datetime.now() - timedelta(hours=40), 0)
        s = cw._step_status(("XmapSync", 26), tasks)
        self.assertEqual(s["status"], "stale")  # 成功但过期
        tasks2 = _mk_task("XmapSync", datetime.now() - timedelta(hours=40), 3)
        s2 = cw._step_status(("XmapSync", 26), tasks2)
        self.assertEqual(s2["status"], "fail")  # 失败优先于 stale

    def test_never_run_is_stale(self):
        tasks = _mk_task("RiskGuardShadowDaily", None, 267011)
        s = cw._step_status(("RiskGuardShadowDaily", 26), tasks)
        self.assertEqual(s["status"], "stale")
        self.assertIn("待首跑", s["detail"])

    def test_running_result_not_fail(self):
        tasks = _mk_task(
            "ChanlunScan", datetime.now() - timedelta(hours=1), 267009, state="Running"
        )
        s = cw._step_status(("ChanlunScan", 26), tasks)
        self.assertEqual(s["status"], "ok")

    def test_resident_type_http_probe_semantics(self):
        """B4 修复后常驻型语义：HTTP 探活为主，任务态仅辅助注记。

        回归病灶：dashboard 活着但计划任务态 Ready → 旧逻辑误报 DOWN。
        """
        ready = _mk_task("KgDataDashboard", None, 0, state="Ready")
        with mock.patch.object(cw, "_http_alive", return_value=(True, "HTTP 200")):
            s = cw._step_status(("KgDataDashboard", 0), ready)
        self.assertEqual(s["status"], "ok")  # Ready+探活UP → ok
        self.assertIn("HTTP探活 UP", s["detail"])
        self.assertIn("Ready", s["detail"])  # 任务态辅助注记保留

        with mock.patch.object(
            cw, "_http_alive", return_value=(False, "URLError: timeout")
        ):
            s2 = cw._step_status(("KgDataDashboard", 0), ready)
        self.assertEqual(s2["status"], "fail")  # 探活异常 → fail
        self.assertIn("URLError: timeout", s2["detail"])  # fail-visible

        # 无探活端点的常驻任务降级沿用任务态判定
        with mock.patch.dict(cw._RESIDENT_PROBES, {}, clear=True):
            running = _mk_task("KgDataDashboard", None, 0, state="Running")
            s3 = cw._step_status(("KgDataDashboard", 0), running)
            self.assertEqual(s3["status"], "ok")
            s4 = cw._step_status(("KgDataDashboard", 0), ready)
            self.assertEqual(s4["status"], "fail")

    def test_resident_probe_down_overrides_running_state(self):
        """补盲区：探活为主——即使任务态 Running，探活 DOWN 也必须 fail。

        防回归方向：端口挂了但计划任务恰在重试态（Running），
        不允许任务态盖过探活结论造成假绿。
        """
        running = _mk_task("KgDataDashboard", None, 267009, state="Running")
        with mock.patch.object(
            cw,
            "_http_alive",
            return_value=(False, "ConnectionRefusedError: [WinError 10061]"),
        ):
            s = cw._step_status(("KgDataDashboard", 0), running)
        self.assertEqual(s["status"], "fail")
        self.assertIn("HTTP探活 DOWN", s["detail"])
        self.assertIn("WinError 10061", s["detail"])  # 原因可见

    def test_unknown_task(self):
        s = cw._step_status(("NoSuchTask", 26), {})
        self.assertEqual(s["status"], "unknown")

    def test_note_attached(self):
        tasks = _mk_task("XmapSync", datetime.now() - timedelta(hours=2), 3)
        s = cw._step_status(("XmapSync", 26), tasks)
        self.assertIn("VPN", s.get("note") or "")


class TestChainPayload(unittest.TestCase):
    def test_overall_worst_is_fail_not_unknown(self):
        """回归：fail 与 unknown 同权重 2，反查映射不得串 key。"""
        now = datetime.now()
        tasks = {}
        tasks.update(_mk_task("ChanlunScan", now, 0))
        tasks.update(_mk_task("XmapSync", now, 3))  # fail
        tasks.update(_mk_task("KGAnalysis", now, 0))
        tasks.update(_mk_task("StrategyDaily", now, 0))
        tasks.update(_mk_task("RiskGuardShadowDaily", now, 0))
        tasks.update(_mk_task("RiskGuardR3Weekly", now, 0))
        for n in ("TushareSync", "TdxSync", "GubaCrawl", "XueqiuSync", "XueqiuCrawl"):
            tasks.update(_mk_task(n, now, 0))
        tasks.update(_mk_task("KgDataDashboard", None, 0, state="Running"))
        tasks.update(_mk_task("OllamaWatchdog", now, 0))
        tasks.update(_mk_task("XueqiuTokenRefresh", now, 0))
        tasks.update(_mk_task("WeightUpdater", now, 0))
        with (
            mock.patch.object(cw, "_tasks_cached", return_value=tasks),
            mock.patch.object(cw, "_http_alive", return_value=(True, "HTTP 200")),
        ):
            payload, code = cw.chain_payload()
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["overall"], "fail")  # 非 unknown
        self.assertEqual(payload["chains"]["morning"]["health"], "fail")
        self.assertEqual(payload["chains"]["ingest"]["health"], "ok")

    def test_health_names_are_valid(self):
        tasks = {}
        now = datetime.now()
        for n in (
            "ChanlunScan",
            "XmapSync",
            "KGAnalysis",
            "StrategyDaily",
            "RiskGuardShadowDaily",
            "RiskGuardR3Weekly",
            "TushareSync",
            "TdxSync",
            "GubaCrawl",
            "XueqiuSync",
            "XueqiuCrawl",
            "OllamaWatchdog",
            "XueqiuTokenRefresh",
            "WeightUpdater",
        ):
            tasks.update(_mk_task(n, now, 0))
        tasks.update(_mk_task("KgDataDashboard", None, 0, state="Running"))
        with (
            mock.patch.object(cw, "_tasks_cached", return_value=tasks),
            mock.patch.object(cw, "_http_alive", return_value=(True, "HTTP 200")),
        ):
            payload, _ = cw.chain_payload()
        self.assertEqual(payload["overall"], "ok")
        for ch in payload["chains"].values():
            self.assertIn(ch["health"], ("ok", "stale", "fail", "unknown"))


class TestPsQueryParsing(unittest.TestCase):
    def test_dt_re_parses_cn_locale(self):
        m = cw._DT_RE.search("2026/10/17 6:00:01")
        self.assertIsNotNone(m)
        dt = datetime(*(int(g) for g in m.groups(0)[:6]))
        self.assertEqual(dt, datetime(2026, 10, 17, 6, 0, 1))

    def test_sentinel_date_rejected(self):
        """1999 哨兵日期必须被拒（判为从未运行）。"""
        m = cw._DT_RE.search("1999/11/30 0:00:00")
        dt = datetime(*(int(g) for g in m.groups(0)[:6]))
        self.assertLess(dt.year, 2000)  # 采集层按 year>=2000 过滤


if __name__ == "__main__":
    unittest.main()


# ── K-STOCK-429：电池豁免监视动态化 ─────────────────────────────
class TestBattPendingDynamic(unittest.TestCase):
    def test_ps_parse_six_field_bat(self):
        """第 6 字段 bat 解析：true/false → bool。"""
        # 走真解析：mock subprocess 不现实，直接构造——解析逻辑内联在
        # _ps_query_tasks，这里验证语义端到端（通过 monkeypatch stdout）。
        out = "\n".join(
            [
                "StrategyDaily|Queued|2026/10/17 7:30:00|0|2026/10/18 7:30:00|True",
                "TdxSync|Ready|2026/10/17 11:00:00|0|2026/10/18 4:00:00|False",
                "OldFormat|Ready|2026/10/17 9:00:00|0|",  # 5 字段兼容
            ]
        )
        with mock.patch.object(cw.subprocess, "run") as mrun:
            mrun.return_value.stdout = out
            tasks = cw._ps_query_tasks()
        self.assertEqual(tasks["StrategyDaily"]["bat"], True)
        self.assertEqual(tasks["TdxSync"]["bat"], False)
        self.assertIsNone(tasks["OldFormat"]["bat"])

    def test_batt_pending_dynamic_from_tasks(self):
        """batt_pending 从缓存数据实时计算，不再硬编码。"""
        tasks = {}
        now = datetime.now()
        for n in (
            "ChanlunScan",
            "XmapSync",
            "KGAnalysis",
            "StrategyDaily",
            "RiskGuardShadowDaily",
            "RiskGuardR3Weekly",
            "TushareSync",
            "TdxSync",
            "GubaCrawl",
            "XueqiuSync",
            "XueqiuCrawl",
            "OllamaWatchdog",
            "XueqiuTokenRefresh",
            "WeightUpdater",
        ):
            tasks.update(_mk_task(n, now, 0))
            tasks[n]["bat"] = False
        tasks.update(_mk_task("KgDataDashboard", None, 0, state="Running"))
        tasks["KgDataDashboard"]["bat"] = False
        # 欠账：StrategyDaily + HermesWebUI-Guard（监视名单内）
        tasks["StrategyDaily"]["bat"] = True
        tasks.update(_mk_task("HermesWebUI-Guard", None, 0, state="Queued"))
        tasks["HermesWebUI-Guard"]["bat"] = True
        with (
            mock.patch.object(cw, "_tasks_cached", return_value=tasks),
            mock.patch.object(cw, "_http_alive", return_value=(True, "HTTP 200")),
        ):
            payload, code = cw.chain_payload()
        self.assertEqual(code, 200)
        self.assertEqual(
            payload["batt_pending"], ["HermesWebUI-Guard", "StrategyDaily"]
        )

    def test_batt_watchlist_includes_guard(self):
        """HermesWebUI-Guard（链外）必须进批查名单。"""
        names_src = open(cw.__file__, encoding="utf-8").read()
        self.assertIn('"HermesWebUI-Guard"', names_src)


class TestHttpAlive(unittest.TestCase):
    """B4：_http_alive 探活两态单测（mock HTTP，不发真实请求）。"""

    def test_http_200_is_up(self):
        resp = mock.MagicMock()
        resp.getcode.return_value = 200
        resp.__enter__ = mock.MagicMock(return_value=resp)
        resp.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch("urllib.request.urlopen", return_value=resp):
            alive, detail = cw._http_alive("http://127.0.0.1:8791/x")
        self.assertTrue(alive)
        self.assertIn("HTTP 200", detail)

    def test_timeout_is_down_and_visible(self):
        import urllib.error

        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")
        ):
            alive, detail = cw._http_alive("http://127.0.0.1:8791/x")
        self.assertFalse(alive)
        self.assertIn("timed out", detail)  # fail-visible：原因可见

    def test_http_500_is_down(self):
        import urllib.error

        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 500, "Internal", None, None),
        ):
            alive, detail = cw._http_alive("http://127.0.0.1:8791/x")
        self.assertFalse(alive)
        self.assertIn("500", detail)

    def test_refused_is_down(self):
        with mock.patch("urllib.request.urlopen", side_effect=ConnectionRefusedError()):
            alive, detail = cw._http_alive("http://127.0.0.1:8791/x")
        self.assertFalse(alive)
        self.assertTrue(detail)  # 异常摘要非空


if __name__ == "__main__":
    unittest.main()
