# -*- coding: utf-8 -*-
"""W3-4 cross 韧性单测（20261014 cross-resilience-dev）。

覆盖五条路径（全部合成数据，不读真实源、不碰网络/数据库/推送）：
1. 单源 error 降级不崩：loader 抛异常 → build_cross 正常返回，
   src_state 显式 error，其余源照常聚合；
2. degraded 标注：任一源非 ok → degraded=True + 中文 degrade_note，
   且 sig4 解耦（longs 缺失时从 sigs 现算）；
3. k 门槛不自动放水：可用源=3 保持 k=3（degraded_k3）；可用源<3 →
   blocked + topn 产空，k 仍=3 不降档；
4. 健康检查三态：ok/stale/missing 依锚日期与过期阈值判定；
5. 开关关闭旧行为：config cross.source_health_alert=false →
   push_source_health_alert 不推（且 dry 模式不写 outbox）。

纪律：临时目录合成产物；mock 点与 test_ui_drilldown 同口径
（REPORTS/DATA/_CACHE/_src_chanlun_load/CONFIG_FP）。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cross_poll           # noqa: E402


def _rps_row(code, name, longs=None, sigs=None, action="买入"):
    """合成 rps_latest.json res[] 行（longs/sigs 字段级可控）。"""
    r = {"code": code, "name": name, "market": "CN", "action": action,
         "score": 80}
    if longs is not None:
        r["longs"] = longs
    if sigs is not None:
        r["sigs"] = sigs
    return r


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


class _Base(unittest.TestCase):
    """合成产物沙箱：REPORTS/DATA/CONFIG_FP 全部指向临时目录。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cross_res_")
        self.reports = os.path.join(self.tmp, "reports")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.reports, exist_ok=True)
        os.makedirs(self.data, exist_ok=True)
        self._orig = (cross_poll.REPORTS, cross_poll.DATA,
                      cross_poll._CACHE, cross_poll._src_chanlun_load,
                      cross_poll.CONFIG_FP)
        cross_poll.REPORTS = self.reports
        cross_poll.DATA = self.data
        cross_poll._CACHE = {"t": 0, "d": None}
        cross_poll._src_chanlun_load = lambda: []     # chanlun 默认空池
        cross_poll.CONFIG_FP = os.path.join(self.tmp, "config.json")
        # 默认合成：rps 产物（qpzm+sig4 共源）+ 9dim 新鲜锚
        self.ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        _write_json(os.path.join(self.reports, "rps_latest.json"), {
            "ts": self.ts, "res": [
                _rps_row("600519.SH", "贵州茅台", longs=4),
                _rps_row("000001.SZ", "平安银行", longs=1,
                         sigs={"ma_cross": 1, "trend_follow": 0,
                               "rsi_reversal": 0, "boll_breakout": 0}),
                _rps_row("300750.SZ", "宁德时代",
                         sigs={"ma_cross": 1, "trend_follow": 1,
                               "rsi_reversal": 1, "boll_breakout": 1})]})
        _write_json(os.path.join(self.data, "nine_dim_scores.json"), {
            "generated_at": self.ts,
            "trade_date": datetime.now().strftime("%Y%m%d"),
            "rows": [{"code": "600519.SH", "name": "贵州茅台",
                      "score": 85.0, "cov": 0.9}]})

    def tearDown(self):
        (cross_poll.REPORTS, cross_poll.DATA, cross_poll._CACHE,
         cross_poll._src_chanlun_load, cross_poll.CONFIG_FP) = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSig4Decouple(_Base):
    """路径 1+2 前半：sig4 解耦 + 单源异常降级不崩。"""

    def test_sig4_from_sigs_when_longs_missing(self):
        """longs 缺失 → 从 sigs 现算持仓数（3/4 → long）。"""
        def sig4_hit(d, code):
            st = {s["code"]: s for s in d["stocks"]}[code]
            return next(h for h in st["hits"] if h["sid"] == "sig4")

        d = cross_poll.build_cross(force=True)
        self.assertEqual(sig4_hit(d, "300750.SZ")["dir"], "long")    # sigs 4/4
        self.assertEqual(sig4_hit(d, "000001.SZ")["dir"], "neutral")  # sigs 1/4
        self.assertEqual(sig4_hit(d, "600519.SH")["dir"], "long")     # longs=4

    def test_single_source_error_degrades_no_crash(self):
        """sig4 loader 抛异常 → 整体不崩，src_state=error，其余源照常。"""
        orig = cross_poll._src_sig4

        def _boom():
            raise RuntimeError("sig4 炸了")

        cross_poll._src_sig4 = _boom
        try:
            for s in cross_poll.STRATEGIES:
                if s["id"] == "sig4":
                    s["loader"] = _boom
            d = cross_poll.build_cross(force=True)
        finally:
            cross_poll._src_sig4 = orig
            for s in cross_poll.STRATEGIES:
                if s["id"] == "sig4":
                    s["loader"] = orig
        self.assertEqual(d["src_state"]["sig4"]["status"], "error")
        self.assertTrue(d["degraded"])
        self.assertIn("sig4", d["degrade_note"])
        # 其余三源照常出票（qpzm/chanlun 空池但 9dim 有票）
        self.assertEqual(d["src_state"]["9dim"]["status"], "ok")


class TestDegradedMark(_Base):
    """路径 2：degraded 标注 + 降级说明。"""

    def test_missing_source_marks_degraded(self):
        """chanlun 空池（missing）→ degraded=True + note 含源名。"""
        d = cross_poll.build_cross(force=True)
        self.assertTrue(d["degraded"])
        self.assertEqual(d["src_state"]["chanlun"]["status"], "missing")
        self.assertIn("chanlun", d["degrade_note"])

    def test_all_ok_not_degraded(self):
        """四源齐整（chanlun 有票）→ degraded=False，mode=normal。"""
        cross_poll._src_chanlun_load = lambda: [
            {"code": "600519.SH", "type": "3买", "detect_date":
             datetime.now().strftime("%Y-%m-%d"), "name": "贵州茅台",
             "market": "CN", "dt": "2026-10-13"}]
        d = cross_poll.build_cross(force=True)
        self.assertFalse(d["degraded"])
        self.assertEqual(d["k_gate"]["mode"], "normal")
        self.assertEqual(d["k_gate"]["n_sources_ok"], 4)


class TestKGate(_Base):
    """路径 3：k=3 门槛 degraded 降档——不自动降 k 防放水。"""

    def test_three_sources_keep_k3(self):
        """可用源=3（degraded_k3）：k 仍=3，仅 n_hit≥3 票进 topn。"""
        d = cross_poll.build_cross(force=True)   # chanlun 空池 → 可用 3 源
        self.assertEqual(d["k_gate"]["mode"], "degraded_k3")
        self.assertEqual(d["k_gate"]["k"], 3)
        # 合成里 600519 有 qpzm+sig4+9dim 三源命中 → 进 topn；两源票不进
        codes = [s["code"] for s in d["topn"]]
        self.assertIn("600519.SH", codes)
        self.assertNotIn("000001.SZ", codes)
        self.assertTrue(all(s["n_hit"] >= 3 for s in d["topn"]))

    def test_two_sources_blocked_empty_topn_k_unchanged(self):
        """可用源<3（blocked）：topn 产空 + 醒目 DEGRADED，k 不降为 2。"""
        # 只留 9dim 一个源：rps 产物删掉（qpzm+sig4 齐灭）+ chanlun 空池
        os.remove(os.path.join(self.reports, "rps_latest.json"))
        d = cross_poll.build_cross(force=True)
        kg = d["k_gate"]
        self.assertEqual(kg["mode"], "blocked")
        self.assertEqual(kg["k"], 3)              # 不自动降 k
        self.assertLess(kg["n_sources_ok"], 3)
        self.assertEqual(d["topn"], [])           # 产空结果
        self.assertIn("DEGRADED", kg["note"])     # 醒目标注
        self.assertIn("DEGRADED", d["degrade_note"])


class TestSourceHealth(_Base):
    """路径 4：健康检查三态 ok/stale/missing。"""

    def _stale_rps(self, days_ago):
        old = (datetime.now() - timedelta(days=days_ago)) \
            .strftime("%Y-%m-%d %H:%M")
        snap = json.load(open(os.path.join(self.reports, "rps_latest.json"),
                              encoding="utf-8"))
        snap["ts"] = old
        _write_json(os.path.join(self.reports, "rps_latest.json"), snap)

    def test_three_states(self):
        h = cross_poll.source_health()
        self.assertEqual(h["qpzm"]["status"], "ok")       # 新鲜锚
        self.assertEqual(h["sig4"]["status"], "ok")
        self.assertEqual(h["chanlun"]["status"], "missing")  # 池空=missing
        self.assertEqual(h["9dim"]["status"], "ok")
        self.assertTrue(all("anchor" in v for v in h.values()))

    def test_stale_by_age(self):
        """sig4 锚超阈值（>5 日历日）→ stale；qpzm 超 3 日 → stale。"""
        days = 6
        self._stale_rps(days_ago=days)
        h = cross_poll.source_health()
        self.assertEqual(h["qpzm"]["status"], "stale")
        self.assertEqual(h["sig4"]["status"], "stale")
        self.assertEqual(h["qpzm"]["anchor"],
                         (datetime.now() - timedelta(days=days))
                         .strftime("%Y-%m-%d"))

    def test_missing_when_file_absent(self):
        """rps 产物缺失 → qpzm/sig4 missing（锚空）；9dim 文件缺 → missing。"""
        os.remove(os.path.join(self.reports, "rps_latest.json"))
        os.remove(os.path.join(self.data, "nine_dim_scores.json"))
        h = cross_poll.source_health()
        self.assertEqual(h["qpzm"]["status"], "missing")
        self.assertEqual(h["sig4"]["status"], "missing")
        self.assertEqual(h["9dim"]["status"], "missing")


class TestAlertToggle(_Base):
    """路径 5：开关关闭旧行为（不推）；dry 模式不写 outbox。"""

    def test_toggle_off_no_push(self):
        """config cross.source_health_alert=false → 不推（旧行为）。"""
        _write_json(cross_poll.CONFIG_FP,
                    {"cross": {"source_health_alert": False}})
        sent, text = cross_poll.push_source_health_alert()
        self.assertFalse(sent)
        # 存在 stale/missing 源时仍返回文本（人工可查），但不发送
        self.assertTrue(text)

    def test_dry_mode_no_outbox(self):
        """send=False 显式 dry：返回 (False, 文本)，不 import runner 不落盘。"""
        import tempfile as _tf
        outbox = os.path.join(_tf.gettempdir(), "pending_bt.txt")
        if os.path.exists(outbox):
            os.remove(outbox)
        sent, text = cross_poll.push_source_health_alert(send=False)
        self.assertFalse(sent)
        self.assertIn("源健康", text)
        self.assertFalse(os.path.exists(outbox))      # 不写 outbox

    def test_all_ok_no_alert_even_if_on(self):
        """全源 ok → 即便开关开也不告警。"""
        cross_poll._src_chanlun_load = lambda: [
            {"code": "600519.SH", "type": "3买",
             "detect_date": datetime.now().strftime("%Y-%m-%d"),
             "name": "贵州茅台", "market": "CN", "dt": "2026-10-13"}]
        h = cross_poll.source_health()
        self.assertTrue(all(v["status"] == "ok" for v in h.values()))
        sent, text = cross_poll.push_source_health_alert(h, send=None)
        self.assertFalse(sent)
        self.assertEqual(text, "")

    def test_toggle_on_would_push_via_runner(self):
        """开关开 + 存在异常源 → 走 runner.push_feishu（此处 mock 验证路径）。"""
        _write_json(cross_poll.CONFIG_FP,
                    {"cross": {"source_health_alert": True}})
        calls = []

        class _FakeRunner:
            @staticmethod
            def push_feishu(text, card=False):
                calls.append(text)
                return True

        sys_mod = sys.modules
        sys.modules["runner"] = _FakeRunner
        try:
            h = cross_poll.source_health()            # chanlun missing
            sent, _ = cross_poll.push_source_health_alert(h, send=None)
        finally:
            sys.modules.pop("runner", None)
            sys.modules.update({k: v for k, v in sys_mod.items()
                                if k != "runner"} if False else {})
        self.assertTrue(sent)
        self.assertEqual(len(calls), 1)
        self.assertIn("chanlun missing", calls[0])    # 推送含各源锚状态


if __name__ == "__main__":
    unittest.main(verbosity=2)
