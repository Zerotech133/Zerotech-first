# -*- coding: utf-8 -*-
"""quant_backtest/factor_lib/chan_factors.py · W2-6 缠论因子（笔/中枢/背驰/买卖点，轻量自实现）。

背景：W3-2 核查确认 `quant_backtest/data/tushare/stk_mins` 已入库（5,038 代码 / 1 分钟 /
202509–202611，parquet 按代码×月）。本模块把「缠论结构」落成可复用的**研究型因子**：
不依赖 czsc，轻量自实现包含处理/分型/笔/中枢/背驰/买卖点，规则与仓内既有
`quant_backtest/chanlun_fractal.py`、`chanlun_bi.py` 的「新笔口径」对齐（不 import、不改它们）。

四个纯函数族（输入统一为升序 OHLCV bar DataFrame：dt/open/high/low/close/vol）
--------------------------------------------------------------------------
1. `merge_inclusion(bars)`       包含处理 → 处理后 K 线（升向 max(h)/max(l)、降向 min(h)/min(l)）；
2. `find_fractals(bars)`         简化分型（处理后相邻三根：顶=中间高低点均最高；底=均最低）；
   `find_bi(bars)`               笔端点（相邻异性分型相连，处理后索引差 ≥ 4；同性分型保留更极值）
3. `find_zhongshu(bars)`         中枢区间与宽度（连续三笔重叠：ZD=三笔低点最高，ZG=三笔高点最低；
   后续笔与区间重叠则延伸；宽度=(ZG−ZD)/ZD）
4. `beichi_strength(bars)`       背驰强度（同向相邻两笔：价格创新高/新低 vs MACD 面积缩小；
   强度=1−面积后/面积前，>0 即背驰）
   `buy_sell_points(bars)`       三类买卖点（简化规则：B1/S1=底/顶背驰；B2/S2=一买后不破前低/高；
   B3/S3=离开中枢后回抽不破 ZG/反抽不上 ZD）；**标注非交易信号**

PIT 口径（重要）
---------------
  · 包含处理/分型/笔/中枢/背驰/买卖点全部只用「当前 bar 及以前」的数据，不使用未来 bar 做输入；
  · **确认型结构必须落在确认 bar**：分型在右侧处理后 K 线完成时确认（`confirm_idx` = 右侧组末根原始
    bar 索引）；中枢在第三笔完成时确认；背驰在第二段同向笔完成时确认；买卖点标记使用确认 bar 索引；
    这与 MEETING.md 判例 H29（确认型形态信号必须放在确认完成日）一致；
  · 笔端点允许被后续「更极端同性分型」延伸/修正（缠论标准行为）：本模块对**未闭合**的最后一笔/中枢
    标 `closed=False`，PIT 快照应只用 `closed=True` 的结构；`analyze_chan` 同时给出 unclosed 结构；
  · **本模块只产出结构因子，不包含未来收益检验**：报告中的买卖点仅为简化标注，属研究因子，
    「是否可交易 / 是否 PIT 交易信号」须另行回测验证后才可使用。

用法
----
    from chan_factors import analyze_chan, resample_30min
    python -X utf8 quant_backtest/factor_lib/chan_factors.py list
    python -X utf8 quant_backtest/factor_lib/chan_factors.py sample \
        --out-prefix research/reports/chan_factors_20261103 --n-codes 50 --n-days 60
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
DEFAULT_DATA_ROOT = REPO / "quant_backtest" / "data" / "tushare" / "stk_mins"
OUT_DIR = REPO / "research" / "reports"

LIB_VERSION = "1.0.0"
DEFAULT_NCODES = 50
DEFAULT_NDAYS = 60
DEFAULT_SEED = 42
BI_MIN_GAP = 4          # 笔最小跨度：两分型中点「处理后 K 线」索引差 ≥ 4（新笔口径）
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BAR_COLUMNS = ("dt", "open", "high", "low", "close", "vol")
FACTORS = ["bi_endpoints", "zhongshu", "beichi", "buy_sell_points"]


# ============================================================ 基础工具
def _num(x, nd: int = 4):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(f) else round(f, nd)


def norm_bars(bars: pd.DataFrame | list[dict]) -> pd.DataFrame:
    """统一 bar 输入：DataFrame 或 [{dt,open,high,low,close,vol}] → 升序、数值化、去坏行。"""
    if isinstance(bars, list):
        df = pd.DataFrame(bars)
    elif isinstance(bars, pd.DataFrame):
        df = bars.copy()
    else:
        raise TypeError("bars 必须是 DataFrame 或 list[dict]")
    if df.empty:
        return pd.DataFrame(columns=list(BAR_COLUMNS))
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "vol" not in df.columns:
        df["vol"] = 0.0
    df["vol"] = pd.to_numeric(df["vol"], errors="coerce").fillna(0.0)
    if "dt" not in df.columns:
        df["dt"] = pd.Index(np.arange(len(df)))
    df = df.dropna(subset=["high", "low", "close"]).reset_index(drop=True)
    order = np.argsort(pd.Index(df["dt"]).astype(str).to_numpy(), kind="stable")
    return df.iloc[order].reset_index(drop=True)


# ============================================================ 1. 包含处理 + 分型
def merge_inclusion(bars: pd.DataFrame | list[dict]) -> pd.DataFrame:
    """K 线包含处理（缠论标准）：升向取 max(high)/max(low)，降向取 min(high)/min(low)。

    返回处理后 bars：dt=合并组内首根 bar 的 dt，high/low=合并极值，close=组内最后一根 close，
    n=合并根数，raw_start/raw_end=组内首/末原始 bar 索引，last_dt=组内末根 dt。
    """
    df = norm_bars(bars)
    out: list[dict] = []
    direction = 1
    for i in range(len(df)):
        row = df.iloc[i]
        item = {"dt": str(row["dt"]), "last_dt": str(row["dt"]), "open": float(row["open"]),
                "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"]),
                "vol": float(row["vol"]), "n": 1, "raw_start": i, "raw_end": i}
        if not out:
            out.append(item)
            continue
        prev = out[-1]
        contained = ((prev["high"] >= item["high"] and prev["low"] <= item["low"])
                     or (item["high"] >= prev["high"] and item["low"] <= prev["low"]))
        if contained:
            if direction >= 0:
                prev["high"] = max(prev["high"], item["high"])
                prev["low"] = max(prev["low"], item["low"])
            else:
                prev["high"] = min(prev["high"], item["high"])
                prev["low"] = min(prev["low"], item["low"])
            prev["close"] = item["close"]
            prev["vol"] += item["vol"]
            prev["n"] += 1
            prev["raw_end"] = i
            prev["last_dt"] = item["last_dt"]
            continue
        direction = 1 if item["high"] > prev["high"] else -1
        out.append(item)
    return pd.DataFrame(out, columns=["dt", "last_dt", "open", "high", "low", "close", "vol",
                                     "n", "raw_start", "raw_end"])


def find_fractals(bars: pd.DataFrame | list[dict], merged: pd.DataFrame | None = None) -> list[dict]:
    """简化分型：处理后相邻三根，中间 K 线高低点均为三者最高（顶）/最低（底）。

    返回 [{type:"top"/"bottom", idx:中间处理后索引, price, dt, bar_idx(中间组末根原始索引),
           confirm_idx(右侧组末根原始索引=确认时点)}]（PIT：confirm_idx 当日收盘后才可知）。
    """
    m = merge_inclusion(bars) if merged is None else merged
    if len(m) < 3:
        return []
    h = m["high"].to_numpy(dtype=float)
    l = m["low"].to_numpy(dtype=float)
    out = []
    for i in range(1, len(m) - 1):
        top = h[i] > h[i - 1] and h[i] > h[i + 1] and l[i] > l[i - 1] and l[i] > l[i + 1]
        bottom = h[i] < h[i - 1] and h[i] < h[i + 1] and l[i] < l[i - 1] and l[i] < l[i + 1]
        if top or bottom:
            out.append({"type": "top" if top else "bottom", "idx": int(i),
                        "price": float(h[i] if top else l[i]),
                        "dt": str(m.iloc[i]["dt"]),
                        "bar_idx": int(m.iloc[i]["raw_end"]),
                        "confirm_idx": int(m.iloc[i + 1]["raw_end"])})
    return out


# ============================================================ 2. 笔端点
def find_bi(bars: pd.DataFrame | list[dict], merged: pd.DataFrame | None = None,
            fractals: list[dict] | None = None) -> list[dict]:
    """笔端点（简化新笔口径）：相邻异性分型相连，处理后索引差 ≥ BI_MIN_GAP。

    同性分型出现时保留更极值者（顶取更高/底取更低）；若上一笔已终结于被替换端点，同步修正该笔终点
    （缠论标准的「笔延伸」）。返回 [{s_idx,e_idx,s_bar_idx,e_bar_idx,s_dt,e_dt,s_price,e_price,
    dir(1=上笔,-1=下笔), confirm_idx, closed}]；closed=False 表示最后一笔仍可能被后续极值延伸。
    """
    m = merge_inclusion(bars) if merged is None else merged
    fr = find_fractals(bars, merged=m) if fractals is None else fractals
    if len(fr) < 2:
        return []
    pts = list(fr)
    bis: list[dict] = []
    pend = pts[0]
    for f in pts[1:]:
        if f["type"] == pend["type"]:
            more_extreme = (f["price"] > pend["price"] if pend["type"] == "top"
                            else f["price"] < pend["price"])
            if not more_extreme:
                continue
            if bis and bis[-1]["e_idx"] == pend["idx"]:
                bis[-1].update({"e_idx": f["idx"], "e_bar_idx": f["bar_idx"], "e_dt": f["dt"],
                                "e_price": f["price"], "confirm_idx": f["confirm_idx"]})
            pend = f
            continue
        if f["idx"] - pend["idx"] < BI_MIN_GAP:
            continue
        direction = 1 if pend["type"] == "bottom" else -1
        bis.append({
            "s_idx": int(pend["idx"]), "e_idx": int(f["idx"]),
            "s_bar_idx": int(pend["bar_idx"]), "e_bar_idx": int(f["bar_idx"]),
            "s_dt": str(pend["dt"]), "e_dt": str(f["dt"]),
            "s_price": float(pend["price"]), "e_price": float(f["price"]),
            "dir": int(direction), "confirm_idx": int(f["confirm_idx"]), "closed": True,
        })
        pend = f
    if bis:
        bis[-1]["closed"] = False       # 最后一笔端点可能被后续更极端同性分型延伸
    return bis


# ============================================================ 3. 中枢
def _bi_range(bi: dict) -> tuple[float, float]:
    return (min(bi["s_price"], bi["e_price"]), max(bi["s_price"], bi["e_price"]))


def find_zhongshu(bars: pd.DataFrame | list[dict], bis: list[dict] | None = None,
                  merged: pd.DataFrame | None = None) -> list[dict]:
    """中枢（连续三笔重叠）：ZD=max(三笔低点)，ZG=min(三笔高点)；后续笔与区间重叠则延伸。

    返回 [{s_idx,e_idx,s_dt,e_dt,zg,zd,width,width_pct=(zg−zd)/zd,n_bi,confirm_idx,closed}]；
    confirm_idx=第三笔完成的右侧原始 bar 索引（PIT 确认时点）；closed=False 表示中枢仍可延伸。
    """
    if bis is None:
        bis = find_bi(bars, merged=merged)
    out: list[dict] = []
    i = 0
    n = len(bis)
    while i + 2 < n:
        ranges = [_bi_range(bis[i + k]) for k in range(3)]
        zd = max(r[0] for r in ranges)
        zg = min(r[1] for r in ranges)
        if not (zg > zd):
            i += 1
            continue
        e = i + 2
        j = i + 3
        while j < n:
            lo, hi = _bi_range(bis[j])
            if hi < zd or lo > zg:          # 完全离开区间 → 中枢终结
                break
            e = j
            j += 1
        closed = j < n
        confirm_idx = int(bis[e]["confirm_idx"])
        end_bar_idx = int(bis[e]["e_bar_idx"])
        out.append({
            "s_idx": int(bis[i]["s_idx"]), "e_idx": int(bis[e]["e_idx"]),
            "s_bar_idx": int(bis[i]["s_bar_idx"]), "e_bar_idx": end_bar_idx,
            "s_dt": str(bis[i]["s_dt"]), "e_dt": str(bis[e]["e_dt"]),
            "zg": float(zg), "zd": float(zd), "width": float(zg - zd),
            "width_pct": _num((zg - zd) / zd, 6) if zd > 0 else None,
            "n_bi": int(e - i + 1), "confirm_idx": confirm_idx, "closed": bool(closed),
        })
        i = e + 1 if closed else n   # 结束后从离开笔/末端继续寻找新中枢
    return out


# ============================================================ 4. 背驰 / 三类买卖点
def _macd(close: pd.Series) -> pd.DataFrame:
    ema_fast = close.ewm(span=MACD_FAST, adjust=False, min_periods=1).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False, min_periods=1).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False, min_periods=1).mean()
    hist = 2.0 * (dif - dea)
    return pd.DataFrame({"dif": dif, "dea": dea, "hist": hist})


def _macd_area(merged: pd.DataFrame, start_idx: int, end_idx: int, direction: int) -> float:
    """笔内 MACD 面积（简化：同向柱体面积和；无同向柱时回落为 |hist| 和）。"""
    if merged.empty or end_idx <= start_idx:
        return 0.0
    macd = _macd(merged["close"].astype(float))
    hist = macd["hist"].to_numpy(dtype=float)[start_idx: end_idx + 1]
    same = hist[hist > 0] if direction == 1 else hist[hist < 0]
    area = float(np.abs(same).sum()) if same.size else float(np.abs(hist).sum())
    return area


def beichi_strength(bars: pd.DataFrame | list[dict], bis: list[dict] | None = None,
                    merged: pd.DataFrame | None = None) -> list[dict]:
    """背驰强度（简化）：相邻同向两笔比较「价格新极值 vs MACD 面积缩小」。

    上笔 pair：若后笔价格创新高而面积缩小 → 顶背驰（strength=1−面积后/面积前，>0）；
    下笔 pair 对称 → 底背驰。返回 [{type:"顶背驰"/"底背驰", strength, divergence(bool),
    end_idx, end_bar_idx, confirm_idx, price, price_prev, area_prev, area_curr, closed}]。
    """
    if bis is None:
        bis = find_bi(bars, merged=merged)
    m = merge_inclusion(bars) if merged is None else merged
    out: list[dict] = []
    for k in range(len(bis) - 2):
        b1, b2 = bis[k], bis[k + 2]
        if b1["dir"] != b2["dir"]:
            continue
        area1 = _macd_area(m, b1["s_idx"], b1["e_idx"], b1["dir"])
        area2 = _macd_area(m, b2["s_idx"], b2["e_idx"], b2["dir"])
        if area1 <= 0 or area2 <= 0:
            continue
        new_extreme = (b2["e_price"] > b1["e_price"]) if b2["dir"] == 1 else (b2["e_price"] < b1["e_price"])
        strength = float(1.0 - area2 / area1) if new_extreme else 0.0
        out.append({
            "type": "顶背驰" if b2["dir"] == 1 else "底背驰",
            "strength": _num(strength, 4),
            "divergence": bool(new_extreme and strength > 0),
            "end_idx": int(b2["e_idx"]), "end_bar_idx": int(b2["e_bar_idx"]),
            "confirm_idx": int(b2["confirm_idx"]),
            "price": float(b2["e_price"]), "price_prev": float(b1["e_price"]),
            "area_prev": _num(area1, 4), "area_curr": _num(area2, 4),
            "closed": bool(b1["closed"] and b2["closed"]),
        })
    return out


def buy_sell_points(bars: pd.DataFrame | list[dict], bis: list[dict] | None = None,
                    zhongshu: list[dict] | None = None,
                    beichi: list[dict] | None = None) -> list[dict]:
    """三类买卖点（简化规则，**非交易信号**）。

    B1/S1 = 底/顶背驰确认（confirm bar）；
    B2 = 一买之后下一同向下笔低点不破一买低点；S2 = 一卖之后下一同向上笔高点不破一卖高点；
    B3 = 中枢结束后首个「下笔低点 > ZG」（回抽不破中枢上沿）；S3 = 首个「上笔高点 < ZD」。
    返回按确认 bar 升序的事件 [{point: B1/B2/B3/S1/S2/S3, bar_idx, dt, price, detail}]。
    """
    if bis is None:
        bis = find_bi(bars, merged=None)
    if zhongshu is None:
        zhongshu = find_zhongshu(bars, bis=bis)
    if beichi is None:
        beichi = beichi_strength(bars, bis=bis)
    m = merge_inclusion(bars)
    dt_by_bar = dict(zip(m["raw_end"].tolist(), m["dt"].tolist()))
    events: list[dict] = []

    def _dt(bar_idx: int) -> str:
        return str(dt_by_bar.get(int(bar_idx), ""))

    # 一买/一卖（背驰）
    first_buy: list[dict] = []
    first_sell: list[dict] = []
    for e in beichi:
        if not e.get("closed") or not e.get("divergence"):
            continue
        if e["type"] == "底背驰":
            ev = {"point": "B1", "bar_idx": int(e["confirm_idx"]), "dt": _dt(e["confirm_idx"]),
                  "price": e["price"], "detail": f"底背驰 strength={e['strength']}"}
            events.append(ev)
            first_buy.append(e)
        else:
            ev = {"point": "S1", "bar_idx": int(e["confirm_idx"]), "dt": _dt(e["confirm_idx"]),
                  "price": e["price"], "detail": f"顶背驰 strength={e['strength']}"}
            events.append(ev)
            first_sell.append(e)

    # 二买/二卖：一买后下一同向下笔不破前低 / 一卖后下一同向上笔不破前高
    for e in first_buy:
        for k in range(len(bis) - 1):
            b = bis[k]
            if b["dir"] == -1 and b["e_idx"] == e["end_idx"] and k + 2 < len(bis):
                nxt = bis[k + 2]
                if nxt["dir"] == -1 and nxt["closed"] and nxt["e_price"] > b["e_price"]:
                    events.append({"point": "B2", "bar_idx": int(nxt["confirm_idx"]),
                                   "dt": _dt(nxt["confirm_idx"]), "price": nxt["e_price"],
                                   "detail": "回调低点不破一买低点"})
                break
    for e in first_sell:
        for k in range(len(bis) - 1):
            b = bis[k]
            if b["dir"] == 1 and b["e_idx"] == e["end_idx"] and k + 2 < len(bis):
                nxt = bis[k + 2]
                if nxt["dir"] == 1 and nxt["closed"] and nxt["e_price"] < b["e_price"]:
                    events.append({"point": "S2", "bar_idx": int(nxt["confirm_idx"]),
                                   "dt": _dt(nxt["confirm_idx"]), "price": nxt["e_price"],
                                   "detail": "反抽高点不破一卖高点"})
                break

    # 三买/三卖：中枢结束后突破/跌破不回中枢
    for zs in zhongshu:
        if not zs["closed"]:
            continue
        for b in bis:
            if b["e_idx"] <= zs["e_idx"] or not b["closed"]:
                continue
            if b["dir"] == -1 and b["e_price"] > zs["zg"]:
                events.append({"point": "B3", "bar_idx": int(b["confirm_idx"]),
                               "dt": _dt(b["confirm_idx"]), "price": b["e_price"],
                               "detail": f"回抽不破中枢上沿 ZG={zs['zg']}"})
                break
        for b in bis:
            if b["e_idx"] <= zs["e_idx"] or not b["closed"]:
                continue
            if b["dir"] == 1 and b["e_price"] < zs["zd"]:
                events.append({"point": "S3", "bar_idx": int(b["confirm_idx"]),
                               "dt": _dt(b["confirm_idx"]), "price": b["e_price"],
                               "detail": f"反抽不上中枢下沿 ZD={zs['zd']}"})
                break

    # 去重 + 按确认 bar 排序
    seen = set()
    uniq = []
    for ev in events:
        key = (ev["point"], ev["bar_idx"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(ev)
    uniq.sort(key=lambda x: (x["bar_idx"], x["point"]))
    return uniq


# ============================================================ 组合分析（PIT 快照）
def analyze_chan(bars: pd.DataFrame | list[dict]) -> dict:
    """一次输出全部缠论结构 + 最新快照（JSON 可序列化）。

    PIT：所有结构只用当前 bar 及以前数据；分型/中枢/背驰/买卖点均带 confirm_idx（确认 bar）。
    快照（summary）给「截至最后一根 bar 已知」的结构，未闭合结构用 closed 标记。
    """
    df = norm_bars(bars)
    if df.empty:
        return {"n_bars": 0, "merged_n": 0, "fractals": [], "bis": [], "zhongshu": [],
                "beichi": [], "buy_sell_points": [], "summary": None}
    merged = merge_inclusion(df)
    fractals = find_fractals(df, merged=merged)
    bis = find_bi(df, merged=merged, fractals=fractals)
    zhongshu = find_zhongshu(df, bis=bis, merged=merged)
    beichi = beichi_strength(df, bis=bis, merged=merged)
    points = buy_sell_points(df, bis=bis, zhongshu=zhongshu, beichi=beichi)
    for e in beichi:
        e["end_dt"] = str(merged.iloc[e["end_idx"]]["dt"]) if 0 <= e["end_idx"] < len(merged) else ""
    closed_bis = [b for b in bis if b["closed"]]
    last = df.iloc[-1]
    summary = {
        "last_bar": {"dt": str(last["dt"]), "close": float(last["close"])},
        "n_fractals": len(fractals), "n_bis": len(closed_bis), "n_zhongshu": len([z for z in zhongshu if z["closed"]]),
        "n_beichi": len([b for b in beichi if b.get("divergence")]),
        "n_points": len(points),
        "last_bi": ({"dir": closed_bis[-1]["dir"], "e_dt": closed_bis[-1]["e_dt"],
                     "e_price": closed_bis[-1]["e_price"]} if closed_bis else None),
        "last_zhongshu": ({k: zhongshu[-1][k] for k in ("zg", "zd", "width_pct", "e_dt")}
                          if zhongshu else None),
        "last_beichi": ({k: beichi[-1][k] for k in ("type", "strength", "divergence", "end_dt")
                         if k in beichi[-1]} if beichi else None),
        "last_point": points[-1] if points else None,
        "last_point_bar_is_latest": bool(points and points[-1]["bar_idx"] >= len(df) - 3) if points else False,
    }
    for b in bis:
        b["s_dt"] = str(b["s_dt"])
        b["e_dt"] = str(b["e_dt"])
    return {"n_bars": int(len(df)), "merged_n": int(len(merged)), "fractals": fractals,
            "bis": bis, "zhongshu": zhongshu, "beichi": beichi,
            "buy_sell_points": points, "summary": summary}


# ============================================================ 1分钟 → 30分钟 bar
def resample_30min(mins: pd.DataFrame) -> pd.DataFrame:
    """1 分钟 bar → 30 分钟 bar（A股两段交易：9:30–11:30、13:00–15:00，每日 8 根）。

    输入列：trade_time（可转 datetime）、open/high/low/close/vol/amount；
    输出列：dt(YYYYMMDD HH:MM 标签)、open/high/low/close/vol/amount，按 dt 升序。
    """
    if not isinstance(mins, pd.DataFrame) or mins.empty:
        return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "vol", "amount"])
    df = mins.copy()
    tt = pd.to_datetime(df["trade_time"], errors="coerce")
    df = df[tt.notna()].copy()
    if df.empty:
        return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "vol", "amount"])
    tt = pd.to_datetime(df["trade_time"])
    df["_date"] = tt.dt.strftime("%Y%m%d")
    hh = tt.dt.hour.to_numpy()
    mm = tt.dt.minute.to_numpy()
    minute_of_day = hh * 60 + mm
    morning = minute_of_day <= 11 * 60 + 30
    session_start = np.where(morning, 9 * 60 + 30, 13 * 60)
    bucket = np.floor_divide(minute_of_day - session_start, 30)
    bucket = np.clip(bucket, 0, 3)
    df["_bucket"] = bucket.astype(int)
    # 标签 = 时段起点 + (bucket+1)*30min（bar 结束时间）
    label_base = np.where(morning, 9 * 60 + 30, 13 * 60) + (bucket.astype(int) + 1) * 30
    df["_label"] = [f"{int(v) // 60:02d}:{int(v) % 60:02d}" for v in label_base]
    df["dt"] = df["_date"] + " " + df["_label"]
    for col in ("open", "high", "low", "close", "vol", "amount"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df = df.sort_values("trade_time")
    grouped = df.groupby(["dt"], sort=True).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), vol=("vol", "sum"), amount=("amount", "sum"))
    grouped = grouped.reset_index()
    return grouped[["dt", "open", "high", "low", "close", "vol", "amount"]]


# ============================================================ 真实数据抽样
def _code_files(root: Path) -> dict[str, list[Path]]:
    """代码 → 月度文件列表（文件名形如 000001_SZ_202610.parquet）。"""
    out: dict[str, list[Path]] = {}
    for f in sorted(glob.glob(str(root / "*_2026*.parquet"))):
        stem = Path(f).stem
        parts = stem.split("_")
        if len(parts) < 3:
            continue
        code = f"{parts[0]}.{parts[1]}"
        out.setdefault(code, []).append(Path(f))
    return out


def load_minute_sample(data_root: str | Path | None = None, n_codes: int = DEFAULT_NCODES,
                       n_days: int = DEFAULT_NDAYS, seed: int = DEFAULT_SEED) -> dict:
    """抽样最近 n_days 个交易日 × n_codes 的 30 分钟 bar（由 stk_mins 1 分钟聚合）。

    读取最近 4 个月度文件（覆盖 ~60 交易日），先确定全市场交易日集合，再按 seed 确定性抽代码；
    某代码某月文件缺失 → 跳过该月（记录缺口），bars 不足时返回空表由调用方降级。
    """
    root = Path(data_root) if data_root else DEFAULT_DATA_ROOT
    mapping = _code_files(root)
    if not mapping:
        raise FileNotFoundError(f"stk_mins 无数据: {root}")
    months = sorted({f.stem.split("_")[-1] for files in mapping.values() for f in files})
    months = months[-4:] if len(months) > 4 else months
    codes = sorted(mapping)
    rng = np.random.default_rng(seed)
    pick = sorted(rng.choice(codes, size=min(int(n_codes), len(codes)), replace=False).tolist())

    # 用样本代码的 ts 集合确定交易日（并集），再取最近 n_days
    all_dates: set[str] = set()
    frames: dict[str, list[pd.DataFrame]] = {}
    for code in pick:
        parts = [f for f in mapping[code] if f.stem.split("_")[-1] in months]
        if not parts:
            frames[code] = []
            continue
        parts = sorted(parts)
        dfs = []
        for f in parts:
            d = pq.read_table(f, columns=["trade_time", "open", "high", "low", "close", "vol", "amount"]).to_pandas()
            dfs.append(d)
        df = pd.concat(dfs, ignore_index=True)
        df["trade_time"] = pd.to_datetime(df["trade_time"], errors="coerce")
        df = df[df["trade_time"].notna()]
        frames[code] = [df]
        all_dates |= set(df["trade_time"].dt.strftime("%Y%m%d").unique().tolist())
    dates = sorted(all_dates)
    window_dates = dates[-int(n_days):] if dates else []
    win = set(window_dates)

    bars_by_code: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for code in pick:
        dfs = frames.get(code) or []
        if not dfs:
            bars_by_code[code] = resample_30min(pd.DataFrame())
            missing.append(f"{code}: 无月度文件")
            continue
        df = dfs[0]
        df = df[df["trade_time"].dt.strftime("%Y%m%d").isin(win)]
        bars = resample_30min(df)
        bars_by_code[code] = bars
        if len(bars) < 80:
            missing.append(f"{code}: 30分钟 bar 仅 {len(bars)} 根（<80，结构不可判）")
    return {"bars_by_code": bars_by_code, "window_dates": window_dates, "codes": pick,
            "months": months, "data_root": str(root), "missing": missing}


def summarize_sample(sample: dict) -> dict:
    """对抽样 bars 逐代码跑 analyze_chan → 覆盖率/分布/样例（JSON 可序列化）。"""
    t0 = time.time()
    rows: dict[str, dict] = {}
    coverage = {"codes_requested": len(sample["codes"]), "codes_loaded": 0, "codes_with_bars": 0,
                "codes_with_bi": 0, "codes_with_zhongshu": 0, "codes_with_beichi": 0,
                "codes_with_points": 0, "codes_insufficient": 0}
    zs_widths: list[float] = []
    bc_strengths: list[float] = []
    point_counts: dict[str, int] = {}
    examples: list[dict] = []
    for code in sample["codes"]:
        bars = sample["bars_by_code"].get(code, pd.DataFrame())
        coverage["codes_loaded"] += 1
        if bars.empty or len(bars) < 80:
            coverage["codes_insufficient"] += 1
            rows[code] = {"bars": int(len(bars)), "status": "insufficient"}
            continue
        coverage["codes_with_bars"] += 1
        res = analyze_chan(bars)
        summ = res["summary"] or {}
        n_bi = int(summ.get("n_bis", 0))
        n_zs = int(summ.get("n_zhongshu", 0))
        n_bc = int(summ.get("n_beichi", 0))
        n_pt = int(summ.get("n_points", 0))
        if n_bi:
            coverage["codes_with_bi"] += 1
        if n_zs:
            coverage["codes_with_zhongshu"] += 1
        if n_bc:
            coverage["codes_with_beichi"] += 1
        if n_pt:
            coverage["codes_with_points"] += 1
        for z in res["zhongshu"]:
            if z["closed"] and z.get("width_pct") is not None:
                zs_widths.append(float(z["width_pct"]))
        for b in res["beichi"]:
            if b.get("divergence") and b.get("strength") is not None:
                bc_strengths.append(float(b["strength"]))
        for ev in res["buy_sell_points"]:
            point_counts[ev["point"]] = point_counts.get(ev["point"], 0) + 1
        rows[code] = {
            "bars": int(len(bars)), "merged": int(res["merged_n"]),
            "fractals": int(len(res["fractals"])), "bis_closed": n_bi,
            "zhongshu_closed": n_zs, "beichi_divergence": n_bc, "points": n_pt,
            "last_bi": summ.get("last_bi"), "last_zhongshu": summ.get("last_zhongshu"),
            "last_beichi": summ.get("last_beichi"), "last_point": summ.get("last_point"),
            "status": "ok",
        }
        if len(examples) < 8:
            examples.append({"ts_code": code, **{k: rows[code][k] for k in
                                                 ("bars", "merged", "bis_closed", "zhongshu_closed",
                                                  "beichi_divergence", "points")},
                             "last_zhongshu": summ.get("last_zhongshu"),
                             "last_point": summ.get("last_point")})
    def _stats(vals: list[float]) -> dict:
        if not vals:
            return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None, "min": None, "max": None}
        a = np.asarray(vals, dtype=float)
        return {"n": int(a.size), "mean": _num(a.mean(), 4), "median": _num(np.median(a), 4),
                "p25": _num(np.percentile(a, 25), 4), "p75": _num(np.percentile(a, 75), 4),
                "min": _num(a.min(), 4), "max": _num(a.max(), 4)}
    n_loaded = max(1, coverage["codes_loaded"])
    coverage_pct = {
        "bi": _num(100.0 * coverage["codes_with_bi"] / n_loaded, 2),
        "zhongshu": _num(100.0 * coverage["codes_with_zhongshu"] / n_loaded, 2),
        "beichi": _num(100.0 * coverage["codes_with_beichi"] / n_loaded, 2),
        "buy_sell_points": _num(100.0 * coverage["codes_with_points"] / n_loaded, 2),
    }
    payload = {
        "schema_version": "chan_factors.v1", "lib": "chan_factors", "version": LIB_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "meta": {
            "n_codes": int(sample["codes"].__len__()), "n_days_requested": int(len(sample["window_dates"])),
            "window_start": sample["window_dates"][0] if sample["window_dates"] else None,
            "window_end": sample["window_dates"][-1] if sample["window_dates"] else None,
            "months": sample["months"], "data_root": sample["data_root"],
            "bar_freq": "30 分钟（由 stk_mins 1 分钟聚合，每日 8 根）",
            "runtime_sec": _num(time.time() - t0, 2),
        },
        "factors": {
            "bi_endpoints": {
                "title": "笔端点", "formula": "包含处理 → 简化分型 → 相邻异性分型成笔（处理后索引差≥4）",
                "definition": "笔端点价格序列（上笔/下笔交替）；端点可被后续更极端同性分型延伸",
                "pit": "分型在右侧处理后 K 线完成时确认（confirm_idx）；未闭合最后一笔 closed=False，PIT 快照只用 closed=True",
                "coverage": coverage, "coverage_pct": coverage_pct["bi"],
                "distribution": {"bis_closed_per_code": _stats([r.get("bis_closed", 0) for r in rows.values()
                                                               if r.get("status") == "ok"])},
            },
            "zhongshu": {
                "title": "中枢区间/宽度", "formula": "连续三笔重叠 ZD=max(低点), ZG=min(高点)；宽度=(ZG−ZD)/ZD",
                "definition": "中枢区间 [ZD,ZG]、宽度、延伸笔数；某笔完全离开区间则中枢终结",
                "pit": "第三笔完成时确认；未闭合中枢可能延伸（closed=False）",
                "coverage": coverage, "coverage_pct": coverage_pct["zhongshu"],
                "distribution": {"width_pct_closed": _stats(zs_widths)},
            },
            "beichi": {
                "title": "背驰强度", "formula": "相邻同向两笔：价格创新高/低 且 MACD 面积缩小；strength=1−面积后/面积前",
                "definition": "strength>0 且价格创新极值 → 背驰成立；顶背驰/底背驰分别标记",
                "pit": "第二段同向笔完成时确认（confirm_idx）；closed 结构才可判",
                "coverage": coverage, "coverage_pct": coverage_pct["beichi"],
                "distribution": {"strength_divergence": _stats(bc_strengths)},
            },
            "buy_sell_points": {
                "title": "三类买卖点（简化，非交易信号）",
                "formula": "B1/S1=底/顶背驰；B2/S2=一买/一卖后不破前低/高；B3/S3=离开中枢回抽不破 ZG/反抽不上 ZD",
                "definition": "按确认 bar 标记的事件序列（B1/B2/B3/S1/S2/S3）",
                "pit": "全部落在确认 bar（判例 H29 纪律）；**研究型因子，非交易信号，须未来收益验证后才可使用**",
                "coverage": coverage, "coverage_pct": coverage_pct["buy_sell_points"],
                "distribution": {"point_counts": point_counts},
            },
        },
        "coverage": coverage,
        "examples": examples,
        "data_gaps": list(sample.get("missing", [])) + [
            "1 分钟数据按代码×月分文件，缺月/停牌会导致 bars 不足（<80 根无法判结构，已计入 codes_insufficient）",
            "30 分钟聚合按 A 股两段交易（9:30–11:30 / 13:00–15:00）切 8 根，与交易所分钟 bar 口径一致",
        ],
        "notes": [
            "规则与仓内 chanlun_fractal.py / chanlun_bi.py 的「新笔口径」对齐（不 import、不改既有工具）。",
            "PIT：所有结构只用当前 bar 及以前数据；确认型结构（分型/中枢/背驰/买卖点）一律落在确认 bar（判例 H29）。",
            "**本模块为研究型因子：结构标注本身不是收益信号，未做未来收益检验；用于交易前必须过 PIT/样本外/成本/容量验证。",
            "笔端点允许被后续更极端同性分型延伸（缠论标准行为）：报告统计仅计 closed=True 结构。",
        ],
        "unimplemented": [],
    }
    return payload


# ============================================================ 报告 / CLI
def render_markdown(payload: dict) -> str:
    meta = payload.get("meta", {})
    cov = payload.get("coverage", {})
    lines = [
        "# W2-6 缠论因子报告（笔/中枢/背驰/买卖点）", "",
        f"- 生成：{payload.get('generated_at')}；版本 v{payload.get('version')}；"
        f"实现 {len(payload.get('factors', {}))}/4 个因子族",
        f"- 抽样：{meta.get('n_codes')} 代码 × 最近 {meta.get('n_days_requested')} 交易日"
        f"（{meta.get('window_start')}~{meta.get('window_end')}）；bar 频率 {meta.get('bar_freq')}",
        f"- 数据根：`{meta.get('data_root')}`（月份 {meta.get('months')}）；计算耗时 {meta.get('runtime_sec')}s", "",
        "## 一、因子清单（实现/公式/PIT）", "",
        "| 因子 | 公式 | PIT 口径 | 代码覆盖 |", "|---|---|---|---|",
    ]
    for name, f in payload.get("factors", {}).items():
        lines.append(f"| {name} | {f.get('formula')} | {f.get('pit')} | {f.get('coverage_pct')}% |")
    lines += ["", "## 二、覆盖率与分布", "",
              f"- 请求 {cov.get('codes_requested')} 只 / 载入 {cov.get('codes_loaded')} 只 / 有 bar "
              f"{cov.get('codes_with_bars')} 只 / 结构不足 {cov.get('codes_insufficient')} 只",
              f"- 有笔 {cov.get('codes_with_bi')} / 有中枢 {cov.get('codes_with_zhongshu')} / "
              f"有背驰 {cov.get('codes_with_beichi')} / 有买卖点 {cov.get('codes_with_points')}", ""]
    for name in ("bi_endpoints", "zhongshu", "beichi", "buy_sell_points"):
        dist = payload["factors"][name].get("distribution") or {}
        for key, stat in dist.items():
            if isinstance(stat, dict) and "n" in stat:
                lines.append(f"- {name}.{key}: n={stat.get('n')}, mean={stat.get('mean')}, "
                             f"median={stat.get('median')}, p25={stat.get('p25')}, p75={stat.get('p75')}")
            else:
                lines.append(f"- {name}.{key}: {stat}")
    lines += ["", "## 三、样例代码（结构统计与最新快照）", "",
              "| 代码 | bars | merged | 笔 | 中枢 | 背驰 | 买卖点 | 最新中枢 | 最新买卖点 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for ex in payload.get("examples", []):
        zs = ex.get("last_zhongshu") or {}
        pt = ex.get("last_point") or {}
        lines.append(f"| {ex.get('ts_code')} | {ex.get('bars')} | {ex.get('merged')} | {ex.get('bis_closed')} | "
                     f"{ex.get('zhongshu_closed')} | {ex.get('beichi_divergence')} | {ex.get('points')} | "
                     f"ZG/Zd {zs.get('zg')}/{zs.get('zd')} | {pt.get('point')} @ {pt.get('dt')} |")
    lines += ["", "## 四、数据缺口 / 降级记录", ""]
    gaps = payload.get("data_gaps", [])
    lines += [f"- {g}" for g in gaps] if gaps else ["- 无"]
    lines += ["", "## 五、未实现项与原因", ""]
    unimpl = payload.get("unimplemented", [])
    lines += [f"- {u}" for u in unimpl] if unimpl else ["- 无：4 个因子族全部实现"]
    lines += ["", "## 六、口径与使用纪律", ""]
    lines += [f"- {n}" for n in payload.get("notes", [])]
    lines += ["", "## 七、测试", "",
              "- `tools/tests/test_chan_factors.py`（≥10 用例）：包含处理/分型/笔/中枢/背驰/三类买卖点/"
              "PIT 截断不变性/空数据降级/30分钟聚合。",
              "- 运行：`C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_chan_factors.py`", ""]
    return "\n".join(lines)


def write_outputs(payload: dict, prefix: str | Path) -> tuple[Path, Path]:
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    js = prefix.with_suffix(".json")
    md = prefix.with_suffix(".md")
    js.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    md.write_text(render_markdown(payload), encoding="utf-8")
    return js, md


FACTOR_SPECS = {
    "bi_endpoints": {"title": "笔端点",
                     "formula": "包含处理→简化分型→相邻异性分型成笔（处理后索引差≥4）",
                     "pit": "确认 bar（右侧处理后K线完成）；未闭合笔不用于 PIT 快照"},
    "zhongshu": {"title": "中枢区间/宽度",
                 "formula": "连续三笔重叠：ZD=max(低点)，ZG=min(高点)；宽度=(ZG−ZD)/ZD",
                 "pit": "第三笔完成确认；未闭合中枢可延伸"},
    "beichi": {"title": "背驰强度",
               "formula": "相邻同向两笔：价格新极值 且 MACD 面积缩小；strength=1−面积后/面积前",
               "pit": "第二段同向笔完成确认"},
    "buy_sell_points": {"title": "三类买卖点（非交易信号）",
                        "formula": "B1/S1=背驰；B2/S2=一买/卖后不破前低/高；B3/S3=离开中枢回抽不破 ZG/不上 ZD",
                        "pit": "全部落在确认 bar（判例 H29）"},
}


def cmd_list(as_json: bool) -> int:
    rows = [{"factor": name, **spec} for name, spec in FACTOR_SPECS.items()]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(f"# chan_factors v{LIB_VERSION} · {len(rows)} 个因子族（研究型，非交易信号）")
        for row in rows:
            print(f"  {row['factor']:<18} {row['title']:<18} {row['formula']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="chan_factors.py", description="W2-6 缠论因子（研究型）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="列出因子族")
    p_list.add_argument("--json", action="store_true")
    p_sample = sub.add_parser("sample", help="真实数据抽样并输出报告")
    p_sample.add_argument("--out-prefix", default=str(OUT_DIR / "chan_factors_20261103"))
    p_sample.add_argument("--n-codes", type=int, default=DEFAULT_NCODES)
    p_sample.add_argument("--n-days", type=int, default=DEFAULT_NDAYS)
    p_sample.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p_sample.add_argument("--data-root", default="")
    p_sample.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args.json)
    try:
        sample = load_minute_sample(data_root=(args.data_root or None), n_codes=args.n_codes,
                                    n_days=args.n_days, seed=args.seed)
        payload = summarize_sample(sample)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[chan_factors] 抽样失败: {exc}", file=sys.stderr)
        return 2
    js, md = write_outputs(payload, args.out_prefix)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        meta = payload["meta"]
        cov = payload["coverage"]
        print(f"# chan_factors v{LIB_VERSION} | {meta['n_codes']} 代码 × {meta['n_days_requested']} 日 "
              f"| {meta['window_start']}~{meta['window_end']} | 30分钟 bars | 耗时 {meta['runtime_sec']}s")
        print(f"# 覆盖：有bar {cov['codes_with_bars']} / 笔 {cov['codes_with_bi']} / 中枢 {cov['codes_with_zhongshu']}"
              f" / 背驰 {cov['codes_with_beichi']} / 买卖点 {cov['codes_with_points']}"
              f" | 数据不足 {cov['codes_insufficient']}")
        for gap in payload["data_gaps"]:
            print(f"# gap: {gap}")
    print(f"# 输出: {js} / {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
