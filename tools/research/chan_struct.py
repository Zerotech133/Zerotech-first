# -*- coding: utf-8 -*-
"""日线结构位止损（缠论语义）：中枢 ZG / 笔低点（=分型低点）。

为什么不用 ATR 当主口径：ATR 是「波动率兜底」，它不知道结构在哪。缠论的止损
语义是**结构失效**——3买站上中枢，跌回 ZG 就不是 3买了；中枢内运行，破了最近
往上笔的起点（笔低点）就是笔破坏。这两种失效点由结构定义，与波动率无关。

复用项目既有算法（不重写）：quant_backtest/chanlun_bi.py 的
find_bis / find_pivots，口径与 portal/api_chanlun_sl_tp.py 完全一致：
  · 止损：现价 > 日线上枢 zg → 止损=zg（跌回中枢上沿即失效）
          否则取最近 5 笔中、低于现价的最低者之上的那个笔低点（笔破坏）
  · 无中枢/无笔 → 返回 None，由调用方退化到 ATR

as-of 铁律：只喂**建仓日之前**的 K 线。建仓发生在 T+1 开盘，T 日收盘才出信号，
所以结构必须在 T 日及之前就已形成，不能用建仓日当天的 K 线。
"""
import bisect
import os
import sys

import pandas as pd

QB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "quant_backtest")
ASH = os.path.join(QB, "data", "ashare")
if QB not in sys.path:
    sys.path.insert(0, QB)

import chanlun_bi as CB              # noqa: E402
from chanlun_fractal import find_fractals as _ff      # noqa: E402
from chanlun_fractal import _merge_inclusion as _mi   # noqa: E402

LOOKBACK = 260      # 结构回看根数：够形成 2~3 个中枢，再多只是浪费
MIN_DIST = 0.015    # 结构位离现价 < 1.5% 记为"过紧"，只标注不改值
_CACHE = {}         # code -> (dates, h, l)
_LVL = {}           # (code, before_d, px) -> 结果，对照盘会重复建仓同一票


def _hl(code):
    """只读三列，按票缓存。中枢/笔只需要高低点，不需要量额。"""
    if code in _CACHE:
        return _CACHE[code]
    f = os.path.join(ASH, code.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        _CACHE[code] = None
        return None
    d = pd.read_parquet(f, columns=["trade_date", "high", "low"])
    td = d["trade_date"].astype(str).str.replace("-", "").str[:8]
    dates = td.tolist()
    h = d["high"].tolist()
    l = d["low"].tolist()
    rows = sorted(zip(dates, h, l), key=lambda x: x[0])
    dates = [r[0] for r in rows]
    h = [float(r[1]) if r[1] == r[1] else 0.0 for r in rows]
    l = [float(r[2]) if r[2] == r[2] else 0.0 for r in rows]
    _CACHE[code] = (dates, h, l)
    return _CACHE[code]


def struct_level(code, before_d, px, lookback=LOOKBACK):
    """返回建仓时的结构止损位。

    before_d: 建仓日（严格不含）。px: 建仓价（与 K 线同尺度）。
    返回 None 表示结构不足，调用方退化 ATR。
    """
    ck = (code, str(before_d), round(float(px), 3))
    if ck in _LVL:
        return _LVL[ck]
    got = _hl(code)
    if not got:
        _LVL[ck] = None
        return None
    dates, h, l = got
    j = bisect.bisect_left(dates, str(before_d))       # 严格小于建仓日
    if j < 40:
        _LVL[ck] = None
        return None
    lo = max(0, j - lookback)
    bars = [{"dt": dates[k], "h": h[k], "l": l[k]} for k in range(lo, j)]

    merged = _mi(bars)
    frs = _ff(merged)
    bis = CB.find_bis(frs, merged)
    piv = CB.find_pivots(bis)
    if not piv and not bis:
        _LVL[ck] = None
        return None

    out = {"px": round(px, 3), "n_bis": len(bis), "n_piv": len(piv)}
    if piv:
        # 优先取现价参与的中枢（zd < px），否则取最后一个
        cand = [p for p in piv if float(p["zd"]) < px] or piv
        dpv = cand[-1]
        zg, zd = float(dpv["zg"]), float(dpv["zd"])
        out.update({"zg": round(zg, 3), "zd": round(zd, 3)})
        if px > zg:
            sl = zg
            out["kind"] = "pivot_zg"
            out["basis"] = "3买结构：跌回日线中枢上沿 ZG %.2f 即失效" % zg
        else:
            lows = [(b["s_price"] if b["dir"] == 1 else b["e_price"]) for b in bis[-5:]]
            under = [x for x in lows if x < px]
            sl = max(under) if under else (min(lows) if lows else None)
            out["kind"] = "bi_low"
            out["basis"] = ("中枢内：最近笔低点（分型低点）%.2f，跌破即笔破坏"
                            % sl) if sl else "中枢内但无可用笔低点"
    else:
        lows = [(b["s_price"] if b["dir"] == 1 else b["e_price"]) for b in bis[-5:]]
        under = [x for x in lows if x < px]
        sl = max(under) if under else (min(lows) if lows else None)
        out["kind"] = "bi_low"
        out["basis"] = "无中枢：最近笔低点 %.2f" % sl if sl else "无中枢无笔"

    if not sl or sl <= 0 or sl >= px:
        _LVL[ck] = None
        return None
    out["sl"] = round(sl, 3)
    out["dist"] = round(px / sl - 1, 4)          # 距止损（正数）
    out["tight"] = out["dist"] < MIN_DIST
    _LVL[ck] = out
    return out


def warm(codes):
    """预加载，避免逐笔 IO 抖动。"""
    for c in codes:
        _hl(c)
    return len(_CACHE)


if __name__ == "__main__":
    # 冒烟：拿已知票在最近一个交易日验一次
    import json
    r = struct_level("000001.SZ", "20261106", 11.5)
    print(json.dumps(r, ensure_ascii=False, indent=1))
