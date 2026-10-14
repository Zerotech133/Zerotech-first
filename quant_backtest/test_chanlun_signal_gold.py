# -*- coding: utf-8 -*-
"""缠论信号层金样本测试（C3a · 纯合成数据，零 DB 零网络）。

锁定 chanlun_signal.analyze 在固定 K 线序列上的全部输出（结构 + 信号
+ 影子标注字段），防止信号判定口径静默漂移。四组金样本：
· 3买：中枢 [52,60] → 上破 72 → 回踩 65（不破 ZG）→ 延伸确认
· 3卖：中枢 [40,48] → 下破 28 → 反抽 35（不回 ZD）→ 延伸确认
· 1卖：向上线段动量衰减终结（段内同向笔 20→18 收窄）
· 1买：向下线段动量衰减终结（段内同向笔 20→18 收窄）
外加 find_signals 直喂手工结构的 2卖 正/反例（回踩不破极值 vs 破极值）。

数据构造：zigzag() 把分型点序列展开为无包含关系的 K 线（腿内 l 严格
单调；每腿 5 根 ≥ BI_MIN_GAP+1；首点为锚不成总分型，尾点自动加反向
确认 bar）。价格/体积全为确定值，断言逐字段全等（金样本快照口径）。

运行：cd quant_backtest && python -m pytest test_chanlun_signal_gold.py -q
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from chanlun_fractal import analyze as fractal_analyze  # noqa: E402
from chanlun_bi import analyze as bi_analyze  # noqa: E402
from chanlun_signal import analyze, find_signals  # noqa: E402

BAR_RANGE = 2      # 每根 K 线固定 h-l 跨度
LEG_BARS = 5       # 每腿 5 根：相邻分型索引差 5 ≥ BI_MIN_GAP(4)


def zigzag(points, tail=2):
    """分型点 [(price, 'T'|'B'), ...] → 确定 K 线列表。

    首点=锚（位于处理后序列第 0 根，不成总分型）；尾点后追加 tail 根
    反向 bar 确认末分型。顶点 K 线 h=price、底点 l=price，腿内 l 严格
    单调 → 零包含合并，dt 与结构一一对应。
    """
    bars = []
    p0, t0 = points[0]
    lo = p0 - BAR_RANGE if t0 == "T" else p0
    dt = 0
    for pi in range(1, len(points)):
        price, typ = points[pi]
        end_l = price - BAR_RANGE if typ == "T" else price
        for k in range(1, LEG_BARS + 1):
            lk = round(lo + (end_l - lo) * k / LEG_BARS, 6)
            hk = price if (typ == "T" and k == LEG_BARS) else lk + BAR_RANGE
            dt += 1
            bars.append({"dt": "D%03d" % dt, "o": lk + 0.5, "h": hk,
                         "l": lk, "c": (lk + hk) / 2, "v": 1000})
        lo = end_l
    last_p, last_t = points[-1]
    for k in range(1, tail + 1):
        # 尾随 bar 从末点 K 线远端之外继续走（顶点 h=price、底点
        # l=price，均隔开 BAR_RANGE+2k，避免与末点 K 线包含合并）
        lk = (last_p + BAR_RANGE + 2 * k if last_t == "B"
              else last_p - BAR_RANGE - 2 * k)
        dt += 1
        bars.append({"dt": "D%03d" % dt, "o": lk + 0.5, "h": lk + BAR_RANGE,
                     "l": lk, "c": lk + 1, "v": 1000})
    return bars


# ── 四组金样本的分型点序列 ──────────────────────────────────
PTS_B3 = [(55, "B"), (60, "T"), (50, "B"), (60, "T"), (52, "B"),
          (58, "T"), (54, "B"), (70, "T"), (63, "B"), (72, "T"),
          (65, "B"), (71, "T")]
PTS_S3 = [(45, "T"), (40, "B"), (50, "T"), (40, "B"), (48, "T"),
          (42, "B"), (46, "T"), (30, "B"), (37, "T"), (28, "B"),
          (35, "T"), (29, "B")]
PTS_S1 = [(13, "T"), (10, "B"), (30, "T"), (15, "B"), (33, "T"),
          (20, "B"), (32, "T"), (18, "B"), (28, "T")]
PTS_B1 = [(47, "B"), (50, "T"), (30, "B"), (45, "T"), (27, "B"),
          (40, "T"), (28, "B"), (42, "T"), (32, "B")]


class TestFractalGold(unittest.TestCase):
    """P2 分型层金样本：顶/底严格交替、dt/price 逐项锁定。"""

    def test_top_bottom_alternation_and_prices(self):
        bars = zigzag(PTS_B3)
        r = fractal_analyze(bars)
        self.assertEqual(r["merged_n"], len(bars))   # 零包含合并
        self.assertEqual(
            [(f["dt"], f["type"], f["price"]) for f in r["fractals"]],
            [("D005", "顶", 60.0), ("D010", "底", 50.0),
             ("D015", "顶", 60.0), ("D020", "底", 52.0),
             ("D025", "顶", 58.0), ("D030", "底", 54.0),
             ("D035", "顶", 70.0), ("D040", "底", 63.0),
             ("D045", "顶", 72.0), ("D050", "底", 65.0),
             ("D055", "顶", 71.0)])
        self.assertEqual(r["top_n"], 6)
        self.assertEqual(r["bottom_n"], 5)

    def test_single_top_and_bottom_series(self):
        """极简序列：一个上涨顶分型 + 一个下跌底分型。"""
        bars = zigzag([(10, "B"), (30, "T"), (12, "B")])
        r = fractal_analyze(bars)
        self.assertEqual([(f["dt"], f["type"], f["price"])
                          for f in r["fractals"]],
                         [("D005", "顶", 30.0), ("D010", "底", 12.0)])


class TestBiGold(unittest.TestCase):
    """P3 笔层金样本：方向交替、首尾相接、端点锁定。"""

    def test_bis_alternate_and_chain(self):
        bars = zigzag(PTS_B3)
        bis = bi_analyze(bars)["bis"]
        self.assertEqual(len(bis), 10)
        for a, b in zip(bis, bis[1:]):
            self.assertEqual(a["e_dt"], b["s_dt"])       # 首尾相接
            self.assertEqual(a["dir"], -b["dir"])        # 方向交替
        self.assertEqual(bis[0], {"s_dt": "D005", "e_dt": "D010",
                                  "s_price": 60.0, "e_price": 50.0,
                                  "dir": -1})
        self.assertEqual(bis[-1], {"s_dt": "D050", "e_dt": "D055",
                                   "s_price": 65.0, "e_price": 71.0,
                                   "dir": 1})
        # 中枢三笔重叠区间 → [ZD, ZG] = [52, 60]
        pv = bi_analyze(bars)["pivots"]
        self.assertEqual(len(pv), 2)
        self.assertEqual((pv[0]["zg"], pv[0]["zd"]), (60.0, 52.0))


class TestSignalGold(unittest.TestCase):
    """信号层金样本：analyze 全链路输出逐字段全等。"""

    def test_gold_3buy_pivot_breakout_pullback_holds(self):
        r = analyze(zigzag(PTS_B3))
        self.assertEqual(r["signals"], [{
            "type": "3买", "dt": "D050", "price": 65.0,
            "zg": 60.0, "zd": 52.0,   # 10-13 信号流新增中枢上下沿字段（金样本同步）
            "note": "中枢上沿回踩不破",
            "bei": True, "fv": [9.0, 7.0],
            "bei2": False, "fv2": [7.0, 7.0],
            "vv": False, "vv_ev": [1000.0, 1000.0],
            "pt": False, "pt_name": None}])

    def test_gold_3sell_pivot_breakdown_rebound_holds(self):
        r = analyze(zigzag(PTS_S3))
        self.assertEqual(r["signals"], [{
            "type": "3卖", "dt": "D050", "price": 35.0,
            "note": "中枢下沿反抽不破",
            "bei": True, "fv": [9.0, 7.0],
            "bei2": False, "fv2": [7.0, 7.0],
            "vv": False, "vv_ev": [1000.0, 1000.0],
            "pt": False, "pt_name": None}])

    def test_gold_1sell_segment_momentum_decay(self):
        r = analyze(zigzag(PTS_S1))
        self.assertEqual(r["signals"], [{
            "type": "1卖", "dt": "D020", "price": 33.0,
            "note": "向上线段终结·动量衰减",
            "mbei": False, "mfv": [30.0, 33.0, 2.9439, 3.6412],
            "tbe": None, "tfv": None,
            "vv": False, "vv_ev": [1000.0, 1000.0],
            "pt": False, "pt_name": None}])

    def test_gold_1buy_segment_momentum_decay(self):
        r = analyze(zigzag(PTS_B1))
        self.assertEqual(r["signals"], [{
            "type": "1买", "dt": "D020", "price": 27.0,
            "note": "向下线段终结·动量衰减",
            "mbei": False, "mfv": [30.0, 27.0, -2.9439, -3.6412],
            "tbe": None, "tfv": None,
            "vv": False, "vv_ev": [1000.0, 1000.0],
            "pt": False, "pt_name": None}])

    def test_signals_sorted_and_typed(self):
        """多组样本拼合：输出按 dt 升序、类型在六类买卖点内。"""
        bars = (zigzag(PTS_S1) + zigzag(PTS_B1))
        for i, b in enumerate(bars):        # 拼接后 dt 唯一化
            b["dt"] = "X%04d" % i
        sigs = analyze(bars)["signals"]
        dts = [s["dt"] for s in sigs]
        self.assertEqual(dts, sorted(dts))
        for s in sigs:
            self.assertIn(s["type"], ("1买", "2买", "3买",
                                      "1卖", "2卖", "3卖"))


class TestSecondSignalDirect(unittest.TestCase):
    """2类信号直喂结构验证：find_signals 手工 bis/segments（正反例）。"""

    @staticmethod
    def _bi(i, sp, ep):
        d = "B%03d" % i
        return {"s_dt": d, "e_dt": "B%03d" % (i + 1), "s_price": sp,
                "e_price": ep, "dir": 1 if ep > sp else -1}

    def _setup(self, rebound_top):
        """向上线段动量衰减（1卖@B3=33）→ 下一根向上笔反弹。

        rebound_top=28（不破 33）→ 2卖；=35（破 33）→ 无 2卖。
        """
        bis = [self._bi(0, 10, 30), self._bi(1, 30, 15),
               self._bi(2, 15, 33), self._bi(3, 33, 20),
               self._bi(4, 20, rebound_top), self._bi(5, rebound_top, 22),
               self._bi(6, 22, 27), self._bi(7, 27, 23)]
        segs = [
            {"s_dt": "B000", "e_dt": "B003", "s_price": 10.0,
             "e_price": 33.0, "dir": 1, "n_bi": 3},     # 1卖 所在线段
            {"s_dt": "B003", "e_dt": "B004", "s_price": 33.0,
             "e_price": 20.0, "dir": -1, "n_bi": 5},    # 已确认向下段
            {"s_dt": "B007", "e_dt": "B008", "s_price": 27.0,
             "e_price": 23.0, "dir": -1, "n_bi": 1}]    # 末段（截断）
        return bis, segs

    def test_second_sell_when_rebound_holds(self):
        bis, segs = self._setup(rebound_top=28)
        sigs = find_signals(bis, segs, [])
        self.assertEqual([s["type"] for s in sigs], ["1卖", "2卖"])
        self.assertEqual(sigs[0]["price"], 33.0)
        self.assertEqual((sigs[1]["dt"], sigs[1]["price"]),
                         ("B005", 28.0))

    def test_no_second_sell_when_rebound_breaks_extreme(self):
        bis, segs = self._setup(rebound_top=35)
        sigs = find_signals(bis, segs, [])
        self.assertEqual([s["type"] for s in sigs], ["1卖"])  # 破极值不触发


class TestEdgeCases(unittest.TestCase):
    """边界：空输入/None/短序列/缺字段 K 线不崩。"""

    def test_empty_and_none(self):
        for r in (analyze([]), analyze(None)):
            self.assertEqual(r["bis"], [])
            self.assertEqual(r["segments"], [])
            self.assertEqual(r["pivots"], [])
            self.assertEqual(r["signals"], [])

    def test_short_series(self):
        bars = [{"dt": "D001", "o": 1, "h": 3, "l": 1, "c": 2, "v": 1},
                {"dt": "D002", "o": 2, "h": 4, "l": 2, "c": 3, "v": 1}]
        r = analyze(bars)
        self.assertEqual(r["signals"], [])
        self.assertEqual(r["bis"], [])

    def test_bars_missing_hl_skipped(self):
        """缺 h/l 的 K 线被包含处理层跳过，不产生结构也不崩。"""
        bars = [{"dt": "D001", "o": 1, "h": 3, "l": 1, "c": 2, "v": 1},
                {"dt": "D002", "o": 2, "c": 3, "v": 1}]   # 缺 h/l
        r = analyze(bars)
        self.assertEqual(r["merged_n"], 1)
        self.assertEqual(r["signals"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
