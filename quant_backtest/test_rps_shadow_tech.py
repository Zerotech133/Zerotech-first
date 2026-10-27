# -*- coding: utf-8 -*-
"""test_rps_shadow_tech —— 影子技术分（缠论信号 × 技术维）核心函数单测。

只测 rps_resonance 新增的影子函数族（_load_chan_pool / _chan_match_score /
_chan_blend / _shadow_rows / _atomic_write_json），用临时构造的迷你
chanlun_pool.jsonl + 合成 K 线，不依赖真实数据文件与网络；临时文件自清理。
不触碰 resonance_watchlist / --daily 现有流程。
"""

import json
import os
import random
import shutil
import tempfile
import unittest

import pandas as pd

import rps_resonance as rr


def _mk_df(n=60, drift=0.0, seed=7):
    """合成 n 根 K 线（策略仅用 close 列，DatetimeIndex）。"""
    rng = random.Random(seed)
    px, out = 10.0, []
    for _ in range(n):
        px = max(1.0, px * (1 + rng.uniform(-0.02, 0.02) + drift))
        out.append(round(px, 3))
    idx = pd.date_range("2026-08-01", periods=n, freq="D")
    return pd.DataFrame({"close": out}, index=idx)


class ChanPoolLoadTest(unittest.TestCase):
    """pool 读取：BOM 判例 / 坏行跳过计数 / 缺文件兜底。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="qpzm_shadow_")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_bom_and_bad_lines(self):
        p = os.path.join(self.d, "pool.jsonl")
        good1 = json.dumps(
            {"code": "600519.SH", "type": "1买", "detect_date": "2026-09-30"},
            ensure_ascii=False,
        )
        good2 = json.dumps(
            {"code": "600519.SH", "type": "3卖", "detect_date": "2026-09-29"},
            ensure_ascii=False,
        )
        with open(p, "w", encoding="utf-8") as f:
            f.write("\ufeff" + good1 + "\n")  # 首行带 BOM
            f.write("{bad json\n")  # 坏行
            f.write(good2 + "\n")
        by_code, dates, bad = rr._load_chan_pool(p)
        self.assertEqual(bad, 1)
        self.assertEqual(len(by_code["600519.SH"]), 2)
        self.assertEqual(dates, {"2026-09-29", "2026-09-30"})

    def test_missing_file(self):
        by_code, dates, bad = rr._load_chan_pool(os.path.join(self.d, "nope.jsonl"))
        self.assertEqual((by_code, dates, bad), ({}, set(), 0))


class ChanMatchScoreTest(unittest.TestCase):
    RECENT = {"2026-09-30", "2026-09-29", "2026-09-28", "2026-09-25"}

    def test_buy_plus_quality(self):
        by = {
            "600519.SH": [
                {
                    "detect_date": "2026-09-30",
                    "type": "2买",
                    "first_seen": "t2",
                    "mbei": True,
                    "bei": True,
                    "vv": True,
                    "tbe": True,
                    "trend": "up",
                },
                {"detect_date": "2026-09-25", "type": "3卖", "first_seen": "t1"},
            ]
        }
        m = rr._chan_match_score("600519.SH", by, self.RECENT)
        self.assertFalse(m["no_signal"])
        self.assertEqual(m["dir"], "buy")
        self.assertEqual(m["quality_n"], 4)
        self.assertEqual(m["chan_score"], 25 + 15)  # 加分封顶 15
        self.assertEqual(len(m["evidence"]), 2)

    def test_quality_cap_15(self):
        by = {
            "X.SH": [
                {
                    "detect_date": "2026-09-30",
                    "type": "1买",
                    "mbei": True,
                    "bei": True,
                    "bei2": True,
                    "vv": True,
                    "tbe": True,
                    "mt": True,
                }
            ]
        }
        m = rr._chan_match_score("X.SH", by, self.RECENT)
        self.assertEqual(m["chan_score"], 25 + 15)  # 6 个 True 也只 +15

    def test_sell_negative_and_symmetric_bonus(self):
        by = {"Y.SH": [{"detect_date": "2026-09-30", "type": "3卖", "bei2": True}]}
        m = rr._chan_match_score("Y.SH", by, self.RECENT)
        self.assertEqual(m["dir"], "sell")
        self.assertEqual(m["chan_score"], -25 - 5)  # 同向叠加 → 更负

    def test_out_of_window_no_signal(self):
        by = {"Z.SH": [{"detect_date": "2026-08-01", "type": "1买"}]}
        m = rr._chan_match_score("Z.SH", by, self.RECENT)
        self.assertTrue(m["no_signal"])
        self.assertEqual(m["chan_score"], 0)
        self.assertEqual(m["evidence"], [])

    def test_latest_signal_wins(self):
        by = {
            "W.SH": [
                {"detect_date": "2026-09-28", "type": "3卖", "first_seen": "a"},
                {"detect_date": "2026-09-30", "type": "1买", "first_seen": "b"},
                {"detect_date": "2026-09-30", "type": "3卖", "first_seen": "c"},
            ]
        }  # 同日 first_seen 最新
        m = rr._chan_match_score("W.SH", by, self.RECENT)
        self.assertEqual(m["dir"], "sell")
        self.assertEqual(m["latest_date"], "2026-09-30")
        self.assertEqual(len(m["evidence"]), 3)  # 窗口内全量留档

    def test_unknown_code(self):
        m = rr._chan_match_score("NOPE.SH", {}, self.RECENT)
        self.assertTrue(m["no_signal"])
        self.assertEqual(m["chan_score"], 0)


class BlendTest(unittest.TestCase):
    """合成口径：0.6*tech_old + 0.4*(50+chan_score)，clip [0,100]。"""

    def test_formula(self):
        self.assertEqual(rr._chan_blend(80, 0), 68.0)
        self.assertEqual(rr._chan_blend(80, 40), 84.0)
        self.assertEqual(rr._chan_blend(80, -40), 52.0)

    def test_clip(self):
        self.assertEqual(rr._chan_blend(100, 40), 96.0)
        self.assertEqual(rr._chan_blend(0, -40), 4.0)


class ShadowRowsTest(unittest.TestCase):
    """_shadow_rows 集成：迷你 bars + 临时 pool，零 runner/网络依赖。"""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="qpzm_shadow_")
        p = os.path.join(self.d, "pool.jsonl")
        sigs = [
            {
                "market": "CN",
                "code": "600519.SH",
                "name": "贵州茅台",
                "type": "1买",
                "detect_date": "2026-09-30",
                "first_seen": "b",
                "mbei": True,
                "trend": "up",
            },
            {
                "market": "CN",
                "code": "000858.SZ",
                "name": "五粮液",
                "type": "3卖",
                "detect_date": "2026-09-30",
                "first_seen": "a",
                "bei": False,
            },
        ]
        with open(p, "w", encoding="utf-8") as f:
            for s in sigs:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        self.by, self.dates, _bad = rr._load_chan_pool(p)
        self.recent = {"2026-09-30", "2026-09-29"}
        self.bars = {
            ("CN", "600519.SH"): _mk_df(drift=+0.01),
            ("CN", "000858.SZ"): _mk_df(drift=-0.01, seed=3),
            ("CN", "300750.SZ"): _mk_df(drift=+0.005, seed=11),
        }
        self.entries = [
            ("sh600519", "贵州茅台", "CN", "600519.SH"),
            ("sz000858", "五粮液", "CN", "000858.SZ"),
            ("sz300750", "宁德时代", "CN", "300750.SZ"),
        ]

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_rows_structure_and_scores(self):
        rows = rr._shadow_rows(self.entries, self.bars, self.by, self.recent, {})
        self.assertEqual(len(rows), 3)
        m = {r["code"]: r for r in rows}
        r1 = m["sh600519"]  # watchlist 形态 code
        self.assertFalse(r1["no_signal"])  # _to_ts → 600519.SH 命中
        self.assertEqual(r1["chan_score"], 25 + 5)
        self.assertTrue(0 <= r1["tech_old"] <= 100)
        self.assertEqual(
            r1["tech_new_proposed"], rr._chan_blend(r1["tech_old"], r1["chan_score"])
        )
        r2 = m["sz000858"]
        self.assertFalse(r2["no_signal"])
        self.assertEqual(r2["chan_score"], -25)
        r3 = m["sz300750"]  # pool 无信号 → 默认值
        self.assertTrue(r3["no_signal"])
        self.assertEqual(r3["chan_score"], 0)
        self.assertEqual(r3["evidence"], [])
        self.assertEqual(r3["tech_new_proposed"], rr._chan_blend(r3["tech_old"], 0))

    def test_sort_by_abs_delta(self):
        rows = rr._shadow_rows(self.entries, self.bars, self.by, self.recent, {})
        ds = [abs(r["delta"]) for r in rows]
        self.assertEqual(ds, sorted(ds, reverse=True))

    def test_short_bars_skipped(self):
        short = dict(self.bars)
        short[("CN", "600519.SH")] = _mk_df(n=30)  # <55 行不参与
        rows = rr._shadow_rows(self.entries, short, self.by, self.recent, {})
        self.assertEqual({r["code"] for r in rows}, {"sz000858", "sz300750"})


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="qpzm_shadow_")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_write_structure_no_tmp_left(self):
        p = os.path.join(self.d, "out.json")
        payload = {
            "ts": "2026-10-05",
            "rows": [{"a": 1}],
            "summary": {"n": 1, "with_chan": 0, "avg_delta": 0.0},
        }
        rr._atomic_write_json(p, payload)
        self.assertTrue(os.path.exists(p))
        got = json.loads(open(p, encoding="utf-8").read())
        self.assertEqual(got["summary"]["n"], 1)
        self.assertEqual([f for f in os.listdir(self.d) if f.endswith(".tmp")], [])

    def test_subdir_created(self):
        p = os.path.join(self.d, "sub", "dir", "out.json")
        rr._atomic_write_json(p, {"ok": 1})
        self.assertTrue(os.path.exists(p))


if __name__ == "__main__":
    unittest.main()
