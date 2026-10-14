# -*- coding: utf-8 -*-
"""chanlun 后复权（W1-2 任务A 20261014）回归测试（纯合成数据）。

覆盖:
  1. use_hfq=true：10送10 除权缺口被因子抹平——调整后单日涨跌幅连续、
     无假分型（分型顶→底落差 <15%）、最新根价格=不复权真实价
  2. use_hfq=false：复现旧行为（原始不复权 bars，缺口日 -50%、假分型在）
  3. 因子整段缺失 → 优雅降级（原样返回 + HFQ_FALLBACK 记名）
  4. 头部/尾部因子缺口 → pre_close 链接续（K254 口径）
  5. _bars_from_parquet 集成（合成 parquet）：OHLC+pc 同步缩放，
     除权日 pc 缺口消失（_exdiv_between 不再误触）
  6. _tdx_topup 补尾 bar 因子链接续（接缝不撕开）
  7. pre_close 异常（缺失/比率出界）→ 守卫继承，不炸

运行:
  "C:/Program Files/Python311/python.exe" quant_backtest/test_chanlun_hfq.py
"""
import os
import sys
import tempfile
from datetime import date, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import chanlun_scan as cs            # noqa: E402
from chanlun_bi import analyze       # noqa: E402

PASS = []


def check(name, cond):
    PASS.append((name, bool(cond)))
    print("[%s] %s" % ("PASS" if cond else "FAIL", name))


# ── 合成数据：10送10 除权缺口 ─────────────────────────────
N = 160
GAP = 80                    # 除权日索引（10送10：价格 ×0.5，因子 ×2）
F_START, F_END = 60, 140    # 因子表只覆盖 [60,140)（测头尾链接续）


def _px(i):
    """复权连续的"真"价格路径（趋势+平滑振荡，单日波动 <2%）。

    坑: 振幅/周期比不能大——振荡过陡时"真"路径自身回撤会污染
    假分型断言（判例: 0.4*sin(i/3) 在 i≈79..88 自跌 23%）。"""
    import math
    return 10.0 + 0.05 * i + 0.2 * math.sin(i / 6.0)


def make_bars():
    """原始不复权 bars（i>=GAP 价格减半）。

    坑: 除权日开盘价同样是除权后口径（真实 10送10 当日 open≈昨收/2），
    若 o 误用除权前昨收，调整后 o 不缩放 → 序列在 o 上仍撕裂。"""
    d0 = date(2026, 1, 1)
    bars = []
    for i in range(N):
        c_raw = _px(i) if i < GAP else _px(i) / 2.0
        c = round(c_raw, 4)
        prev_raw = (_px(i - 1) if i - 1 < GAP else _px(i - 1) / 2.0)
        o = round(prev_raw / 2.0, 4) if i == GAP else round(prev_raw, 4)
        pc = None if i == 0 else (round(prev_raw / 2.0, 4)
                                  if i == GAP else round(prev_raw, 4))
        bars.append({"dt": (d0 + timedelta(days=i)).strftime("%Y%m%d"),
                     "o": o, "h": round(max(o, c) + 0.2, 4),
                     "l": round(min(o, c) - 0.2, 4), "c": c,
                     "v": 1000.0, "pc": pc})
    return bars


def make_fmap(lo=F_START, hi=F_END):
    """因子表片段（td8 升序；累积后复权因子：除权日起 ×2）。"""
    tds = [_td8(b) for b in bars_all[lo:hi]]
    fs = [1.0 if i < GAP else 2.0 for i in range(lo, hi)]
    return ("TEST.SH", (np.array(tds), np.array(fs)))


def _td8(b):
    return b["dt"].replace("-", "")


def max_fx_drop(frs):
    """分型序列中相邻 顶→底 最大落差（假分型度量）。"""
    worst, prev_top = 0.0, None
    for f in frs:
        if f["type"] == "顶":
            prev_top = f["price"]
        elif f["type"] == "底" and prev_top:
            worst = max(worst, (prev_top - f["price"]) / prev_top)
    return worst


def max_day_ret(bars):
    return max(abs(b["c"] / a["c"] - 1)
               for a, b in zip(bars, bars[1:]) if a["c"])


bars_all = make_bars()


# ── 1/2. hfq 开 vs 关 ─────────────────────────────────────
def test_hfq_on_off():
    import copy
    on = copy.deepcopy(bars_all)
    _, fmap = make_fmap()
    cs._ADJ_CACHE = {"TEST.SH": fmap}
    cs._HFQ_CFG = True
    cs.HFQ_FALLBACK.clear()
    ok = cs._hfq_scale(on, "TEST.SH")
    check("hfq: 已复权返回 True", ok)
    check("hfq: 最新根价格=不复权真实价（归一基准）",
          abs(on[-1]["c"] - bars_all[-1]["c"]) < 1e-6)
    check("hfq: 单日最大涨跌幅连续(<5%%, 旧行为≈-50%%)",
          max_day_ret(on) < 0.05)
    check("hfq: 无假分型（分型顶→底落差<15%%）",
          max_fx_drop(analyze(on)["fractals"]) < 0.15)
    # 头部回补（因子表从 i=60 起，i<60 走 pre_close 逆向链 → f=1）
    check("hfq: 头部链接续（i=0 缩放=1/2）",
          abs(on[0]["c"] - bars_all[0]["c"] / 2.0) < 1e-4)
    # 尾部接续（因子表止于 i=140，之后走正向链 → f=2 不变）
    check("hfq: 尾部链接续（i=159 价格不动）",
          abs(on[N - 1]["c"] - bars_all[N - 1]["c"]) < 1e-4)
    check("hfq: v 不缩放", all(a["v"] == b["v"]
                               for a, b in zip(on, bars_all)))
    # 关开关路径：_hfq_scale 不被调用（集成测试覆盖），此处直接验证
    # 原始序列旧行为：缺口日 -50% + 假分型在
    gap_ret = bars_all[GAP]["c"] / bars_all[GAP - 1]["c"] - 1
    check("off: 旧行为复现（缺口日≈-50%%）", abs(gap_ret + 0.5) < 0.02)
    check("off: 旧行为复现（假分型落差>35%%）",
          max_fx_drop(analyze(bars_all)["fractals"]) > 0.35)


# ── 3. 优雅降级 ───────────────────────────────────────────
def test_fallback():
    import copy
    cs._HFQ_CFG = True
    cs.HFQ_FALLBACK.clear()
    cs._ADJ_CACHE = {}                       # 因子表整体缺失
    b = copy.deepcopy(bars_all)
    ok = cs._hfq_scale(b, "TEST.SH")
    check("降级: 返回 False 且 bars 原样",
          (not ok) and b == bars_all)
    check("降级: HFQ_FALLBACK 记名", "TEST.SH" in cs.HFQ_FALLBACK)
    # 全序列日期都无因子命中 → 同样降级
    cs._ADJ_CACHE = {"TEST.SH": (np.array(["00000101"]),
                                 np.array([1.0]))}
    cs.HFQ_FALLBACK.clear()
    ok = cs._hfq_scale(copy.deepcopy(bars_all), "TEST.SH")
    check("降级: 日期全未命中也降级", (not ok)
          and "TEST.SH" in cs.HFQ_FALLBACK)


# ── 4/7. pre_close 链守卫 ─────────────────────────────────
def test_chain_guard():
    check("守卫: pc 缺失 → 比率=1（继承前根）",
          cs._chain_ratio(10.0, None) == 1.0)
    check("守卫: pc 非正 → 比率=1", cs._chain_ratio(10.0, 0) == 1.0)
    check("守卫: 比率出界(>20) → 1", cs._chain_ratio(10.0, 0.1) == 1.0)
    check("守卫: 比率出界(<0.05) → 1", cs._chain_ratio(1000.0, 10.0) == 1.0)
    check("守卫: 正常除权 10送10 → 比率=2",
          cs._chain_ratio(20.0, 10.0) == 2.0)
    check("守卫: 逆向链（头部回补）比率=pc/prev_c",
          cs._chain_ratio(20.0, 10.0, inverse=True) == 0.5)


# ── 5. _bars_from_parquet 集成 ────────────────────────────
def test_bars_from_parquet():
    cs._HFQ_CFG = True
    cs.HFQ_FALLBACK.clear()
    _, fmap = make_fmap()
    cs._ADJ_CACHE = {"TEST.SH": fmap}
    rows = [{"ts_code": "TEST.SH",
             "trade_date": _td8(b),
             "open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"],
             "pre_close": b["pc"], "volume": b["v"], "amount": 1.0}
            for b in bars_all]
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "TEST_SH.parquet")
        pd.DataFrame(rows).to_parquet(p, index=False)
        bars = cs._bars_from_parquet(p)
        check("集成: 加载成功且已复权", bars is not None and len(bars) == N)
        check("集成: 单日最大涨跌幅连续",
              max_day_ret(bars) < 0.05)
        check("集成: 除权日 pc 缺口消失（|pc-prev_c|<1%%）",
              abs(bars[GAP]["pc"] - bars[GAP - 1]["c"])
              < 0.01 * bars[GAP - 1]["c"])
        check("集成: 最新根=真实价",
              abs(bars[-1]["c"] - bars_all[-1]["c"]) < 1e-4)
        # 关开关 → 原始不复权
        cs._HFQ_CFG = False
        bars_raw = cs._bars_from_parquet(p)
        check("集成: 开关关闭复现旧行为",
              all(abs(a["c"] - b["c"]) < 1e-9
                  for a, b in zip(bars_raw, bars_all)))
        cs._HFQ_CFG = True


# ── 6. _tdx_topup 接缝 ────────────────────────────────────
def test_topup():
    import copy
    cs._HFQ_CFG = True
    _, fmap = make_fmap()
    cs._ADJ_CACHE = {"TEST.SH": fmap}
    cs.HFQ_FALLBACK.clear()
    bars = copy.deepcopy(bars_all)
    cs._hfq_scale(bars, "TEST.SH")
    # 补一根新交易日（不复权价延续 _px 路径，pc=前日真实价）
    c_new = round(_px(N) / 2.0, 4)
    pc_new = bars_all[N - 1]["c"]
    maps = {"2601": {"TEST.SH": {"open": c_new, "high": c_new + 0.2,
                                 "low": c_new - 0.2, "close": c_new,
                                 "vol": 1000.0, "pre_close": pc_new}}}
    out = cs._tdx_topup(bars, "TEST.SH", maps)
    check("topup: 补尾成功且已缩放", len(out) == N + 1)
    check("topup: 接缝连续（|Δ|<5%%）",
          abs(out[N]["c"] / out[N - 1]["c"] - (c_new / pc_new)) < 0.05)
    # 无 hfq 开关 → 旧行为原样追加
    cs._HFQ_CFG = False
    out2 = cs._tdx_topup(copy.deepcopy(bars_all), "TEST.SH", maps)
    check("topup: 开关关闭时原样追加",
          len(out2) == N + 1 and abs(out2[N]["c"] - c_new) < 1e-9)
    cs._HFQ_CFG = True


def main():
    test_chain_guard()
    test_hfq_on_off()
    test_fallback()
    test_bars_from_parquet()
    test_topup()
    n_fail = sum(1 for _, ok in PASS if not ok)
    print("\n== chanlun hfq 回归: %d/%d PASS ==" %
          (len(PASS) - n_fail, len(PASS)))
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
