# -*- coding: utf-8 -*-
"""tools/tests/test_chan_factors.py · W2-6 缠论因子单元测试（≥10）。

覆盖：
  1. 包含处理（升向 max、降向 min、合并计数/原始索引）；
  2. 简化分型（顶/底、确认 bar confirm_idx）；
  3. 笔端点（异性分型成笔、索引差≥4、同向笔交替、closed 标记、同性极值延伸）；
  4. 中枢（连续三笔重叠 ZD/ZG/宽度；延伸与离开终止；开放中枢 closed=False）；
  5. 背驰强度（价格新极值 vs MACD 面积、strength 公式、非新极值不背驰）；
  6. 三类买卖点（B1/S1 背驰、B2/S2 不破前低/高、B3/S3 离开中枢回抽）；
  7. PIT 截断不变性（分型/闭合笔/事件只用前缀数据）；
  8. 空数据 / 不足数据 / NaN 降级；
  9. 1 分钟 → 30 分钟 bar 聚合；
 10. analyze_chan 组合 schema（JSON 可序列化）；
 11. 真实 stk_mins 小样本抽样（2 代码 × 5 日，缺数据自动 skip）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_chan_factors.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "quant_backtest" / "factor_lib"))

import chan_factors as cf  # noqa: E402

DATA_ROOT = ROOT / "quant_backtest" / "data" / "tushare" / "stk_mins"


# ------------------------------------------------------------------ 构造工具
def bars_from_hl(pairs: list[tuple[float, float]], closes: list[float] | None = None) -> pd.DataFrame:
    """由 (high, low) 列表构造无包含关系的 bars；close 默认取中点。"""
    n = len(pairs)
    dt = [f"2026010{i // 10 + 1:02d}{i % 28 + 1:02d}" for i in range(n)]
    dt = [f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}" for i in range(n)]
    closes = closes or [round((h + l) / 2.0, 4) for h, l in pairs]
    return pd.DataFrame({
        "dt": dt[:n],
        "open": closes,
        "high": [p[0] for p in pairs],
        "low": [p[1] for p in pairs],
        "close": closes,
        "vol": [1000.0] * n,
    })


def zigzag_bars(prices: list[float]) -> pd.DataFrame:
    """价格路径 → 无包含 bars（high=price+0.5, low=price-0.5）。"""
    return bars_from_hl([(p + 0.5, p - 0.5) for p in prices], closes=[float(p) for p in prices])


def mk_bi(s_idx: int, e_idx: int, s_price: float, e_price: float, closed: bool = True,
          confirm_idx: int | None = None) -> dict:
    direction = 1 if e_price > s_price else -1
    return {"s_idx": s_idx, "e_idx": e_idx, "s_bar_idx": s_idx, "e_bar_idx": e_idx,
            "s_dt": f"D{s_idx:04d}", "e_dt": f"D{e_idx:04d}", "s_price": float(s_price),
            "e_price": float(e_price), "dir": direction,
            "confirm_idx": int(e_idx + 1 if confirm_idx is None else confirm_idx), "closed": bool(closed)}


# ------------------------------------------------------------------ 1. 包含处理
def test_merge_inclusion():
    bars = bars_from_hl([(10.0, 9.0), (9.8, 9.2), (11.0, 10.0), (10.8, 10.2), (12.0, 11.0)])
    merged = cf.merge_inclusion(bars)
    assert len(merged) == 3
    # bar0+bar1 合并（首段方向向上 → max(h)/max(l)）
    assert merged.iloc[0]["high"] == pytest.approx(10.0)
    assert merged.iloc[0]["low"] == pytest.approx(9.2)
    assert merged.iloc[0]["n"] == 2
    assert merged.iloc[0]["raw_start"] == 0 and merged.iloc[0]["raw_end"] == 1
    # bar2+bar3 合并（方向向上）
    assert merged.iloc[1]["high"] == pytest.approx(11.0)
    assert merged.iloc[1]["low"] == pytest.approx(10.2)
    assert merged.iloc[1]["n"] == 2 and merged.iloc[1]["raw_end"] == 3
    assert merged.iloc[2]["n"] == 1 and merged.iloc[2]["raw_end"] == 4


# ------------------------------------------------------------------ 2. 分型
def test_find_fractals():
    # 局部极高/极低点：idx1 顶、idx3 底、idx5 顶、idx7 底
    pairs = [(10, 9), (12, 11), (10.5, 9.5), (9, 8), (11, 10), (13, 12), (11, 10), (9.5, 8.5), (12, 11)]
    bars = bars_from_hl(pairs)
    merged = cf.merge_inclusion(bars)
    assert len(merged) == len(bars)                      # 无包含关系
    fr = cf.find_fractals(bars, merged=merged)
    assert [(f["idx"], f["type"]) for f in fr] == [(1, "top"), (3, "bottom"), (5, "top"), (7, "bottom")]
    assert fr[0]["price"] == pytest.approx(12.0)     # 顶分型取 high
    assert fr[1]["price"] == pytest.approx(8.0)      # 底分型取 low
    # PIT：确认时点 = 右侧处理后 K 线末根原始索引
    assert all(f["confirm_idx"] == f["idx"] + 1 for f in fr)


# ------------------------------------------------------------------ 3. 笔端点
def test_find_bi_endpoints():
    prices = [10, 11, 12, 13, 14, 13, 12, 11, 10, 9, 10, 11, 12, 13, 14, 13, 12, 11, 10]
    bars = zigzag_bars(prices)
    merged = cf.merge_inclusion(bars)
    bis = cf.find_bi(bars, merged=merged)
    assert len(bis) == 2
    assert bis[0]["dir"] == -1 and bis[0]["s_price"] == pytest.approx(14.5) and \
        bis[0]["e_price"] == pytest.approx(8.5)
    assert bis[1]["dir"] == 1 and bis[1]["e_price"] == pytest.approx(14.5)
    for b in bis:
        assert b["e_idx"] - b["s_idx"] >= cf.BI_MIN_GAP   # 新笔最小跨度
    assert bis[0]["closed"] is True and bis[1]["closed"] is False
    assert bis[0]["confirm_idx"] == bis[0]["e_idx"] + 1


def test_find_bi_same_type_extension():
    """同性分型更极值时：未成笔端点被替换；已终结笔终点同步延伸。"""
    prices = [10, 11, 12, 13, 14, 13, 12, 11, 10, 9, 10, 11, 12, 13, 16, 15, 14, 13, 12, 11]
    bars = zigzag_bars(prices)
    merged = cf.merge_inclusion(bars)
    # 手工构造：先顶14 → 底9（成笔）；再接更极端顶16（同类型替换/延伸）
    fractals = [{"type": "top", "idx": 4, "price": 14.0, "dt": "d4", "bar_idx": 4, "confirm_idx": 5},
                {"type": "bottom", "idx": 9, "price": 9.0, "dt": "d9", "bar_idx": 9, "confirm_idx": 10},
                {"type": "top", "idx": 14, "price": 16.0, "dt": "d14", "bar_idx": 14, "confirm_idx": 15}]
    bis = cf.find_bi(bars, merged=merged, fractals=fractals)
    assert len(bis) == 2
    assert bis[1]["e_price"] == pytest.approx(16.0)     # 顶被更极端分型延伸
    assert bis[1]["e_idx"] == 14 and bis[1]["closed"] is False


# ------------------------------------------------------------------ 4. 中枢
def test_zhongshu_open_three_bi():
    # 分型：顶14@4 → 底9@9 → 顶14@14 → 底9@19 → 三笔（无离开笔 → 开放中枢）
    prices = [10, 11, 12, 13, 14, 13, 12, 11, 10, 9, 10, 11, 12, 13, 14, 13, 12, 11, 10, 9, 10, 11]
    bars = zigzag_bars(prices)
    bis = cf.find_bi(bars)
    zs = cf.find_zhongshu(bars, bis=bis)
    assert len(zs) == 1
    z = zs[0]
    assert z["zd"] == pytest.approx(8.5) and z["zg"] == pytest.approx(14.5)
    assert z["width"] == pytest.approx(6.0)
    assert z["width_pct"] == pytest.approx(6.0 / 8.5, abs=1e-5)
    assert z["n_bi"] == 3 and z["closed"] is False
    assert z["confirm_idx"] == bis[2]["confirm_idx"]


def test_zhongshu_extension_and_termination():
    bis = [mk_bi(2, 4, 10.0, 12.0),       # [10,12]
           mk_bi(4, 6, 12.0, 10.5),      # [10.5,12]
           mk_bi(6, 8, 10.5, 12.5),      # [10.5,12.5] → ZD=10.5, ZG=12
           mk_bi(8, 10, 12.5, 11.0),     # 重叠 → 延伸
           mk_bi(10, 12, 17.0, 15.0)]    # 完全在 ZG 上方 [15,17] → 离开 → 终结
    zs = cf.find_zhongshu(pd.DataFrame(), bis=bis)
    assert len(zs) == 1
    z = zs[0]
    assert z["zd"] == pytest.approx(10.5) and z["zg"] == pytest.approx(12.0)
    assert z["n_bi"] == 4 and z["closed"] is True
    assert z["e_idx"] == 10
    # 无重叠 → 无中枢
    assert cf.find_zhongshu(pd.DataFrame(), bis=[mk_bi(0, 2, 10, 12), mk_bi(2, 4, 13, 14),
                                                mk_bi(4, 6, 15, 16)]) == []


# ------------------------------------------------------------------ 5. 背驰
def _beichi_case(end2: float) -> list[dict]:
    # 第一段强升 10→26（1..5）；回调 26→19（5..8）；第二段缓升 19→end2（8..12）
    closes = [10, 12, 15, 19, 23, 26, 24, 21, 19, 19.5, 21.5, 24, end2]
    bars = bars_from_hl([(c + 0.4, c - 0.4) for c in closes],
                        closes=[float(c) for c in closes])
    merged = cf.merge_inclusion(bars)
    bis = [mk_bi(1, 5, 10.0, 26.0), mk_bi(5, 8, 26.0, 19.0), mk_bi(8, 12, 19.0, float(end2))]
    return cf.beichi_strength(bars, bis=bis, merged=merged)


def test_beichi_strength():
    recs = _beichi_case(end2=26.5)       # 创新高 26.5 > 26，且 MACD 面积缩小 → 顶背驰
    assert len(recs) == 1
    r = recs[0]
    assert r["type"] == "顶背驰" and r["price"] > r["price_prev"]
    # strength 输出保留 4 位小数，按 1e-3 容差校验公式一致性
    assert r["strength"] == pytest.approx(1.0 - r["area_curr"] / r["area_prev"], abs=1e-3)
    assert r["divergence"] is True and r["strength"] > 0
    # 不创新高 → 不背驰（strength=0, divergence=False）
    recs2 = _beichi_case(end2=25.0)
    assert recs2[0]["divergence"] is False and recs2[0]["strength"] == 0.0


# ------------------------------------------------------------------ 6. 三类买卖点
def test_buy_sell_points_rules():
    n = 14
    closes = [10 + 0.1 * i for i in range(n)]
    bars = bars_from_hl([(c + 0.3, c - 0.3) for c in closes], closes=[float(c) for c in closes])
    bis = [
        mk_bi(1, 4, 14.0, 9.0),        # 下笔（一买端，底背驰）
        mk_bi(4, 6, 9.0, 13.0),        # 上笔
        mk_bi(6, 8, 13.0, 9.5),        # 下笔：低点 9.5 > 9.0 → 二买
        mk_bi(8, 10, 9.5, 14.0),       # 上笔
        mk_bi(10, 12, 14.0, 13.0),     # 下笔：低点 13 > ZG=12 → 三买
    ]
    beichi = [{"type": "底背驰", "strength": 0.4, "divergence": True, "closed": True,
               "end_idx": 4, "end_bar_idx": 4, "confirm_idx": 5, "price": 9.0,
               "price_prev": 10.0, "area_prev": 2.0, "area_curr": 1.2, "end_dt": "d4"},
              {"type": "顶背驰", "strength": 0.3, "divergence": True, "closed": True,
               "end_idx": 3, "end_bar_idx": 3, "confirm_idx": 4, "price": 15.0,
               "price_prev": 14.0, "area_prev": 2.0, "area_curr": 1.4, "end_dt": "d3"}]
    zs = [{"zg": 12.0, "zd": 10.0, "e_idx": 6, "closed": True}]
    pts = cf.buy_sell_points(bars, bis=bis, zhongshu=zs, beichi=beichi)
    kinds = [p["point"] for p in pts]
    assert "B1" in kinds and "B2" in kinds and "B3" in kinds and "S1" in kinds
    # B2 落点 = 二买笔确认 bar；B3 = 三买笔确认 bar
    b2 = next(p for p in pts if p["point"] == "B2")
    assert b2["price"] == pytest.approx(9.5)
    b3 = next(p for p in pts if p["point"] == "B3")
    assert b3["price"] == pytest.approx(13.0) and b3["bar_idx"] == 13   # confirm = e_idx+1
    assert [p["bar_idx"] for p in pts] == sorted(p["bar_idx"] for p in pts)   # 按确认 bar 升序


# ------------------------------------------------------------------ 7. PIT 截断不变性
def test_pit_truncation_prefix_consistency():
    rng = np.random.default_rng(11)
    n = 320
    prices = 10 + np.cumsum(rng.normal(0, 0.15, n))
    bars = zigzag_bars([round(float(p), 3) for p in prices])
    full = cf.analyze_chan(bars)
    full_fr = {(f["idx"], f["type"], round(f["price"], 6)) for f in full["fractals"]}
    full_bis = {(b["s_idx"], b["e_idx"], b["dir"], round(b["e_price"], 6))
                for b in full["bis"] if b["closed"]}
    full_pts = {(p["point"], p["bar_idx"], round(float(p["price"]), 6))
                for p in full["buy_sell_points"]}
    for k in (180, 260):
        part = cf.analyze_chan(bars.iloc[:k].reset_index(drop=True))
        part_fr = {f["idx"] for f in part["fractals"] if f["confirm_idx"] < k}
        assert part_fr <= {f["idx"] for f in full["fractals"]}
        # 前缀中已确认的分型必须与全样本一致（确认 bar < k 的结构已定型）
        full_fr_at = {f["idx"]: (f["type"], round(f["price"], 6)) for f in full["fractals"]}
        for f in part["fractals"]:
            if f["confirm_idx"] < k:
                assert full_fr_at[f["idx"]] == (f["type"], round(f["price"], 6))
        # 前缀中已闭合的笔不得凭空出现（必存在于全样本闭合笔集合）
        for b in part["bis"]:
            if b["closed"] and b["confirm_idx"] < k:
                assert (b["s_idx"], b["e_idx"], b["dir"], round(b["e_price"], 6)) in full_bis
        # 前缀事件必须与全样本事件一致（PIT：不允许前缀出现未来才确认的事件）
        for p in part["buy_sell_points"]:
            if p["bar_idx"] < k:
                assert (p["point"], p["bar_idx"], round(float(p["price"]), 6)) in full_pts
    assert full_pts or full_fr      # 样本足够大，至少分型存在


# ------------------------------------------------------------------ 8. 空数据 / NaN 容错
def test_empty_and_short_data():
    empty = cf.analyze_chan(pd.DataFrame(columns=["dt", "open", "high", "low", "close", "vol"]))
    assert empty["n_bars"] == 0 and empty["summary"] is None
    one = cf.analyze_chan(pd.DataFrame([{"dt": "20260101", "open": 1, "high": 2, "low": 1,
                                         "close": 2, "vol": 1}]))
    assert one["merged_n"] == 1 and one["bis"] == [] and one["summary"] is not None
    bad = pd.DataFrame([{"dt": "20260101", "open": 1, "high": np.nan, "low": 1, "close": 1, "vol": 1},
                        {"dt": "20260102", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 1}])
    res = cf.analyze_chan(bad)
    assert res["n_bars"] == 1                      # 坏行被丢弃
    assert cf.find_fractals(pd.DataFrame()) == []
    assert cf.find_bi(pd.DataFrame()) == []
    assert cf.find_zhongshu(pd.DataFrame(), bis=[]) == []


# ------------------------------------------------------------------ 9. 30 分钟聚合
def test_resample_30min():
    def stamp(hh: int, mm: int) -> str:
        return (pd.Timestamp("2026-10-30") + pd.Timedelta(hours=hh, minutes=mm)).strftime("%Y-%m-%d %H:%M:%S")

    times: list[str] = []
    for hh, mm, cnt in ((9, 30, 30), (10, 0, 30), (10, 30, 30), (11, 0, 31),
                        (13, 0, 30), (13, 30, 30), (14, 0, 30), (14, 30, 31)):
        times += [stamp(hh, mm + i) for i in range(cnt)]
    n = len(times)
    mins = pd.DataFrame({
        "trade_time": times, "open": np.arange(n, dtype=float),
        "high": np.arange(n, dtype=float) + 1.0, "low": np.arange(n, dtype=float) - 1.0,
        "close": np.arange(n, dtype=float) + 0.5, "vol": np.ones(n), "amount": np.ones(n),
    })
    bars = cf.resample_30min(mins)
    assert len(bars) == 8                     # 每日 8 根 30 分钟 bar
    assert list(bars["dt"]) == sorted(bars["dt"])
    assert bars["dt"].iloc[0].endswith("10:00") and bars["dt"].iloc[-1].endswith("15:00")
    first = bars.iloc[0]
    assert first["open"] == pytest.approx(0.0) and first["close"] == pytest.approx(29.5)
    assert first["high"] == pytest.approx(30.0) and first["low"] == pytest.approx(-1.0)
    assert first["vol"] == pytest.approx(30.0)
    assert bars.iloc[-1]["vol"] == pytest.approx(31.0)
    # 空输入降级
    assert cf.resample_30min(pd.DataFrame()).empty


# ------------------------------------------------------------------ 10. analyze_chan schema
def test_analyze_chan_schema():
    prices = [10, 11, 12, 13, 14, 13, 12, 11, 10, 9, 10, 11, 12, 13, 14, 13, 12, 11, 10, 9, 10]
    res = cf.analyze_chan(zigzag_bars(prices))
    for key in ("n_bars", "merged_n", "fractals", "bis", "zhongshu", "beichi",
                "buy_sell_points", "summary"):
        assert key in res
    assert res["n_bars"] == len(prices) and res["merged_n"] == len(prices)
    s = res["summary"]
    for key in ("last_bar", "n_fractals", "n_bis", "n_zhongshu", "n_beichi", "n_points",
                "last_bi", "last_zhongshu", "last_beichi", "last_point"):
        assert key in s
    assert json.dumps(res, ensure_ascii=False)     # JSON 可序列化（无 numpy 类型泄漏）


# ------------------------------------------------------------------ 11. 真实数据小样本
@pytest.mark.skipif(not DATA_ROOT.exists(), reason="stk_mins 数据根不存在")
def test_real_minute_sample_small():
    sample = cf.load_minute_sample(n_codes=2, n_days=5, seed=1)
    assert sample["months"] and sample["window_dates"] and len(sample["window_dates"]) <= 5
    loaded = [c for c in sample["codes"] if not sample["bars_by_code"][c].empty]
    assert loaded, "2 只抽样代码均无 bar（数据/月份缺失）"
    bars = sample["bars_by_code"][loaded[0]]
    assert list(bars.columns) == ["dt", "open", "high", "low", "close", "vol", "amount"]
    payload = cf.summarize_sample(sample)
    assert payload["schema_version"] == "chan_factors.v1"
    assert set(payload["factors"]) == {"bi_endpoints", "zhongshu", "beichi", "buy_sell_points"}
    assert "coverage" in payload and payload["coverage"]["codes_loaded"] == 2
    json.dumps(payload, ensure_ascii=False)
