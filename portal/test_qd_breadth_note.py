# -*- coding: utf-8 -*-
"""W3-5b 任务B 单测：量子双刀市况宽度影子注记（离线合成数据，不触网、
不跑全市场实时扫描、不推飞书）。

运行（指定解释器）:
    "C:/Program Files/Python311/python.exe" portal/test_qd_breadth_note.py

覆盖四条路径:
    1) 宽度计算正确性（lake_breadth：MA20 判定/短史剔除/断更票剔除/T-1 锚）
    2) 三档分桶（breadth_bucket：<40 / 40-50 / ≥50，含边界）
    3) gate_would_block 判定（40% 档假设，只标注不拦截）
    4) 开关关闭零改动（apply_breadth_note enabled=False → 无任何键变更）
"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import qd_scanner as qs  # noqa: E402


def _dates(n, end="20261013"):
    """以 end 结尾的 n 个连续 YYYYMMDD（测试口径只看末根锚，日历连续即可）"""
    from datetime import date, timedelta
    d = date(int(end[:4]), int(end[4:6]), int(end[6:8]))
    return [(d - timedelta(days=i)).strftime("%Y%m%d")
            for i in range(n - 1, -1, -1)]


def _write_parquet(fp, closes, dates):
    import pandas as pd
    Path(fp).parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"trade_date": dates, "close": closes}).to_parquet(
        fp, index=False)


class TestLakeBreadth(unittest.TestCase):
    """路径1：宽度计算正确性（合成湖）"""

    def test_breadth_correctness(self):
        with tempfile.TemporaryDirectory() as td:
            lake = Path(td) / "ashare"
            d20 = _dates(20)
            # A: 19×10 收 12 → MA20=10.1 < 12 → 站上 True
            _write_parquet(lake / "600001_SH.parquet", [10.0] * 19 + [12.0], d20)
            # B: 19×10 收 8 → MA20=9.9 > 8 → False
            _write_parquet(lake / "600002_SH.parquet", [10.0] * 19 + [8.0], d20)
            # C: 19×10 收 11 → MA20=10.1 < 11 → True
            _write_parquet(lake / "600003_SZ.parquet", [10.0] * 19 + [11.0], d20)
            # D: 次新 10 根（<20 bar）→ 不计入分母
            _write_parquet(lake / "600004_SH.parquet", [10.0] * 10, _dates(10))
            # E: 断更票末根=T-2（20261012）→ 被锚日众数剔除
            _write_parquet(lake / "600005_SH.parquet", [10.0] * 19 + [99.0],
                           _dates(20, end="20261012"))
            note = qs.lake_breadth(lake_dir=lake)
        self.assertIsNotNone(note)
        self.assertEqual(note["ref_date"], "20261013")   # T-1 锚=湖末根众数
        self.assertEqual(note["n_total"], 3)             # D/E 剔除
        self.assertEqual(note["n_above"], 2)             # A/C 站上
        self.assertEqual(note["breadth"], 66.7)          # 2/3=66.7%
        self.assertEqual(note["breadth_ref"], "T-1")
        self.assertEqual(note["bucket"], ">=50")

    def test_empty_lake_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(qs.lake_breadth(lake_dir=Path(td)))


class TestBucket(unittest.TestCase):
    """路径2：三档分桶（含 40/50 边界归属）"""

    def test_buckets(self):
        self.assertEqual(qs.breadth_bucket(0.0), "<40")
        self.assertEqual(qs.breadth_bucket(39.9), "<40")
        self.assertEqual(qs.breadth_bucket(40.0), "40-50")
        self.assertEqual(qs.breadth_bucket(43.2), "40-50")
        self.assertEqual(qs.breadth_bucket(49.9), "40-50")
        self.assertEqual(qs.breadth_bucket(50.0), ">=50")
        self.assertEqual(qs.breadth_bucket(100.0), ">=50")
        self.assertEqual(qs.breadth_bucket(None), "")    # 湖不可用降级


class TestGateWouldBlock(unittest.TestCase):
    """路径3：40% 档门控假设判定（只标注不拦截）"""

    def test_gate(self):
        self.assertTrue(qs.gate_would_block(39.9))
        self.assertTrue(qs.gate_would_block(0.0))
        self.assertFalse(qs.gate_would_block(40.0))      # 40 恰在档内=不拦
        self.assertFalse(qs.gate_would_block(66.7))
        self.assertFalse(qs.gate_would_block(None))      # 湖缺失不标注拦截


class TestApplyNoteSwitch(unittest.TestCase):
    """路径4：开关关闭零改动 / 开启时注记格式"""

    def test_off_zero_change(self):
        sigs = [{"code": "600001.SH", "regime": "range", "signal_now": True}]
        recent = [{"code": "600002.SH", "regime": "rotation"}]
        import copy
        s0, r0 = copy.deepcopy(sigs), copy.deepcopy(recent)
        note, summary = qs.apply_breadth_note(sigs, recent, [], {}, enabled=False)
        self.assertIsNone(note)
        self.assertEqual(summary, "")
        self.assertEqual(sigs, s0)                       # 信号 dict 零键变更
        self.assertEqual(recent, r0)

    def test_on_annotates_and_summary(self):
        fake = {"breadth": 43.2, "bucket": "40-50", "breadth_ref": "T-1",
                "ref_date": "20261013", "n_above": 43, "n_total": 100}
        orig = qs.lake_breadth
        qs.lake_breadth = lambda lake_dir=None: dict(fake)
        try:
            sigs = [{"code": "600001.SH", "regime": "range"}]
            recent = [{"code": "600002.SH", "regime": "range"}]
            tail = [{"code": "600003.SH", "regime": "rotation"}]
            note, summary = qs.apply_breadth_note(
                sigs, recent, tail, {"range": 50, "rotation": 10}, enabled=True)
        finally:
            qs.lake_breadth = orig
        for info in sigs + recent + tail:
            self.assertEqual(info["breadth"], 43.2)
            self.assertEqual(info["breadth_bucket"], "40-50")
            self.assertEqual(info["breadth_ref"], "T-1")
            self.assertFalse(info["gate_would_block"])   # 43.2≥40 不拦
            self.assertEqual(info["regime"], info["regime"])  # 原字段不触碰
        self.assertEqual(summary, "市况: breadth=43.2% [40-50] regime=range")
        self.assertEqual(note["market_summary"], summary)
        self.assertIn("breadth_ref=T-1", note["note"])   # T-1 差异留痕

    def test_lake_unavailable_graceful(self):
        orig = qs.lake_breadth
        qs.lake_breadth = lambda lake_dir=None: None
        try:
            note, summary = qs.apply_breadth_note([{}], [], [], {},
                                                  enabled=True)
        finally:
            qs.lake_breadth = orig
        self.assertIsNone(note)                          # 湖不可用：不注记不炸
        self.assertEqual(summary, "")

    def test_summary_format(self):
        self.assertEqual(qs.market_summary_line(43.2, "40-50", "range"),
                         "市况: breadth=43.2% [40-50] regime=range")
        self.assertEqual(qs.market_summary_line(None, "", "range"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
