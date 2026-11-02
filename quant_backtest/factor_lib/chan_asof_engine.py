# -*- coding: utf-8 -*-
"""quant_backtest/factor_lib/chan_asof_engine.py · W2-6b 缠论 PIT 安全引擎（task-153）。

背景（task-152 结论）：旧前筛用「全窗口一次 analyze_chan + 按确认日 as-of 过滤」生成日频特征。
抽样截断重算显示 8 个特征全部 PIT 不通过（不一致率 16.7%–55.6% > 10%），原因是**未来数据修订**：
  · 一笔的 closed 状态依赖后续是否出现新笔，全样本 as-of 会把「未来才闭合」的笔在当日计入；
  · 中枢的 closed/延伸状态同样依赖未来笔，全样本 as-of 会在当日计入「未来才终结」的中枢；
  · 背驰 pair 的 closed 状态同理。
本模块给出 **PIT 安全引擎**：
  · 按交易日增量维护「包含处理后的 K 线」→ 每日收盘后，只用截至当日 bar 重算分型/笔/中枢/背驰/买卖点；
  · 日频特征只统计 **closed=True 且确认日 ≤ 当日** 的结构（与旧 as-of 口径同名同义，但数据边界严格到当日）；
  · 8 个特征：bi_count_20d / bi_last_dir / zs_count_20d / zs_width_last /
    beichi_last_strength / beichi_div_count_20d / bs_net_5d / bs_last_sign；
  · 严格可复现：无随机、无未来数据；同一 bars 两次运行输出逐位一致。

验证（PIT 一致性）
-------------------
`verify_truncation(bars, sample_days)`：对每个抽样 code-day，把 bars 截断到该日收盘 →
`chan_factors.analyze_chan` 暴力重算 → 独立计算同口径特征 → 与引擎当日特征比较；
不一致率 ≤ 10% 视为通过（task 目标）。预期引擎不一致率 = 0（按日截断重算即 PIT 定义）。

重跑前筛（复用 tools/research/chan_factor_prescreen.py，只读 import）
---------------------------------------------------------------------
`run_prescreen_v2()` 复用其数据加载 / IC / 分层 / 分级逻辑，仅替换特征来源为 PIT 安全引擎，
输出 research/reports/chan_factor_prescreen_20261103_v2.{json,md}（含 v1 vs v2 对照）。

用法
----
    python -X utf8 quant_backtest/factor_lib/chan_asof_engine.py verify --n-codes 6 --n-days 60
    python -X utf8 quant_backtest/factor_lib/chan_asof_engine.py prescreen
    python -X utf8 quant_backtest/factor_lib/chan_asof_engine.py list

约束：数据根只读；不改 chan_factors.py / 不改前筛脚本；不 commit。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
DATA = REPO / "quant_backtest" / "data" / "tushare"
MINS = DATA / "stk_mins"
OUT = REPO / "research" / "reports"
sys.path.insert(0, str(_HERE))
import chan_factors as cf  # noqa: E402

LIB_VERSION = "2.0.0"
TASK_ID = "task-153"
N_CODES, N_DAYS, SEED = 60, 120, 42
HORIZONS = (5, 10, 20)
PRIMARY_H = 20
LAYERS = 5
MIN_NAMES = 20
PIT_MISMATCH_MAX = 0.10
MIN_BARS_FOR_STRUCTURE = 80

# 8 个按日特征（与旧前筛同名同义；方向先验统一 0=探索性）
FEATURES = [
    ("bi_count_20d", "近20日闭笔数", "笔"),
    ("bi_last_dir", "最后一笔方向（+1上/−1下）", "笔"),
    ("zs_count_20d", "近20日闭中枢数", "中枢"),
    ("zs_width_last", "最新闭中枢宽度 (ZG−ZD)/ZD", "中枢"),
    ("beichi_last_strength", "最近背驰强度（无则0）", "背驰"),
    ("beichi_div_count_20d", "近20日背驰数", "背驰"),
    ("bs_net_5d", "近5日买卖点净信号（B+1/S−1）", "买卖点"),
    ("bs_last_sign", "最新买卖点方向（B+1/S−1/0）", "买卖点"),
]
FEAT_NAMES = [f[0] for f in FEATURES]
FEATURE_LABELS = {f[0]: f[1] for f in FEATURES}
FEATURE_FAMILIES = {f[0]: f[2] for f in FEATURES}

# 复用 chan_factors 的公共/内部原语（只读；不修改）
_EMPTY_BARS = pd.DataFrame(columns=["dt", "open", "high", "low", "close", "vol"])


def _num(x, nd: int = 4):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(f) else round(f, nd)


def _day_of(dt: str) -> str:
    return str(dt)[:8]


# ============================================================ PIT 安全引擎
class ChanAsOfEngine:
    """按交易日增量喂入 bars，每个交易日收盘后输出 PIT 安全的 8 个日频特征。

    实现要点：
      · `merged`（包含处理后的 K 线）增量维护：新 bar 逐根按缠论包含规则合并（升向 max/降向 min）；
      · 每日收盘后：用当前 merged 重算分型 → 笔 → 中枢 → 背驰 → 买卖点（只用截至当日数据）；
      · 特征只统计 closed=True 的结构，结构确认日 = confirm_idx 所在交易日（≤ 当日）。
    """

    def __init__(self) -> None:
        self.raw_n = 0
        self.raw_dts: list[str] = []
        self.raw_days: list[str] = []
        self.days: list[str] = []
        self.merged: list[dict] = []
        self._direction = 1
        self.features: dict[str, list[float]] = {name: [] for name in FEAT_NAMES}
        self.structure_counts: list[dict] = []
        self.last_structures: dict = {"bis": [], "zhongshu": [], "beichi": [], "points": []}

    # ---- 增量包含处理
    def _append_raw(self, dt: str, o: float, h: float, l: float, c: float, v: float) -> None:
        item = {"dt": str(dt), "last_dt": str(dt), "open": float(o), "high": float(h),
                "low": float(l), "close": float(c), "vol": float(v), "n": 1,
                "raw_start": int(self.raw_n), "raw_end": int(self.raw_n)}
        self.raw_n += 1
        if not self.merged:
            self.merged.append(item)
            return
        prev = self.merged[-1]
        contained = ((prev["high"] >= item["high"] and prev["low"] <= item["low"])
                     or (item["high"] >= prev["high"] and item["low"] <= prev["low"]))
        if contained:
            if self._direction >= 0:
                prev["high"] = max(prev["high"], item["high"])
                prev["low"] = max(prev["low"], item["low"])
            else:
                prev["high"] = min(prev["high"], item["high"])
                prev["low"] = min(prev["low"], item["low"])
            prev["close"] = item["close"]
            prev["vol"] += item["vol"]
            prev["n"] += 1
            prev["raw_end"] = item["raw_end"]
            prev["last_dt"] = item["last_dt"]
            return
        self._direction = 1 if item["high"] > prev["high"] else -1
        self.merged.append(item)

    def append_day(self, day: str, day_bars: pd.DataFrame) -> None:
        """喂入一个交易日的 bars（按时间升序），收盘后计算当日 PIT 特征。"""
        if day_bars is None or len(day_bars) == 0:
            return
        for row in day_bars.itertuples(index=False):
            self.raw_dts.append(str(row.dt))
            self.raw_days.append(day)
            self._append_raw(row.dt, row.open, row.high, row.low, row.close, row.vol)
        self.days.append(day)
        self._snapshot()

    # ---- 结构计算（只用当前 merged）
    def _structures(self) -> dict:
        if len(self.merged) < 3:
            return {"bis": [], "zhongshu": [], "beichi": [], "points": []}
        merged_df = pd.DataFrame(self.merged)
        fractals = cf.find_fractals(_EMPTY_BARS, merged=merged_df)
        bis = cf.find_bi(_EMPTY_BARS, merged=merged_df, fractals=fractals)
        zhongshu = cf.find_zhongshu(_EMPTY_BARS, bis=bis, merged=merged_df)
        beichi = self._beichi_fast(merged_df, bis)
        points = self._points(bis, zhongshu, beichi)
        # end_dt 便于阅读
        for b in bis:
            b["s_dt"], b["e_dt"] = str(b["s_dt"]), str(b["e_dt"])
        for e in beichi:
            e["end_dt"] = str(merged_df.iloc[e["end_idx"]]["dt"]) if 0 <= e["end_idx"] < len(merged_df) else ""
        return {"bis": bis, "zhongshu": zhongshu, "beichi": beichi, "points": points}

    @staticmethod
    def _beichi_fast(merged_df: pd.DataFrame, bis: list[dict]) -> list[dict]:
        """背驰强度（与 chan_factors.beichi_strength 同规则，但 MACD 只算一次 + 前缀和面积）。"""
        if len(bis) < 3 or len(merged_df) < 2:
            return []
        hist = cf._macd(merged_df["close"].astype(float))["hist"].to_numpy(dtype=float)
        pos = np.clip(hist, 0.0, None)
        neg = np.clip(-hist, 0.0, None)
        absh = np.abs(hist)
        cum_pos = np.concatenate([[0.0], np.cumsum(pos)])
        cum_neg = np.concatenate([[0.0], np.cumsum(neg)])
        cum_abs = np.concatenate([[0.0], np.cumsum(absh)])

        def area(s: int, e: int, direction: int) -> float:
            a = cum_pos[e + 1] - cum_pos[s] if direction == 1 else cum_neg[e + 1] - cum_neg[s]
            if a <= 0:
                a = cum_abs[e + 1] - cum_abs[s]
            return float(a)

        out: list[dict] = []
        for k in range(len(bis) - 2):
            b1, b2 = bis[k], bis[k + 2]
            if b1["dir"] != b2["dir"]:
                continue
            area1 = area(b1["s_idx"], b1["e_idx"], b1["dir"])
            area2 = area(b2["s_idx"], b2["e_idx"], b2["dir"])
            if area1 <= 0 or area2 <= 0:
                continue
            new_extreme = (b2["e_price"] > b1["e_price"]) if b2["dir"] == 1 else (b2["e_price"] < b1["e_price"])
            strength = float(1.0 - area2 / area1) if new_extreme else 0.0
            out.append({"type": "顶背驰" if b2["dir"] == 1 else "底背驰",
                        "strength": _num(strength, 4),
                        "divergence": bool(new_extreme and strength > 0),
                        "end_idx": int(b2["e_idx"]), "end_bar_idx": int(b2["e_bar_idx"]),
                        "confirm_idx": int(b2["confirm_idx"]),
                        "price": float(b2["e_price"]), "price_prev": float(b1["e_price"]),
                        "area_prev": _num(area1, 4), "area_curr": _num(area2, 4),
                        "closed": bool(b1["closed"] and b2["closed"])})
        return out

    def _points(self, bis: list[dict], zhongshu: list[dict], beichi: list[dict]) -> list[dict]:
        """三类买卖点（与 chan_factors.buy_sell_points 同规则；确认 bar → 交易日映射用 raw_days）。"""
        if not bis:
            return []

        def _dt(bar_idx: int) -> str:
            return self.raw_dts[int(bar_idx)] if 0 <= int(bar_idx) < len(self.raw_dts) else ""

        events: list[dict] = []
        first_buy, first_sell = [], []
        for e in beichi:
            if not e.get("closed") or not e.get("divergence"):
                continue
            if e["type"] == "底背驰":
                events.append({"point": "B1", "bar_idx": int(e["confirm_idx"]),
                               "dt": _dt(e["confirm_idx"]), "price": e["price"],
                               "detail": f"底背驰 strength={e['strength']}"})
                first_buy.append(e)
            else:
                events.append({"point": "S1", "bar_idx": int(e["confirm_idx"]),
                               "dt": _dt(e["confirm_idx"]), "price": e["price"],
                               "detail": f"顶背驰 strength={e['strength']}"})
                first_sell.append(e)
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
        seen, uniq = set(), []
        for ev in events:
            key = (ev["point"], ev["bar_idx"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(ev)
        uniq.sort(key=lambda x: (x["bar_idx"], x["point"]))
        return uniq

    # ---- 特征快照（PIT：只用 closed 且确认日 ≤ 当日）
    def _snapshot(self) -> None:
        k = len(self.days) - 1
        day_pos = {d: i for i, d in enumerate(self.days)}
        struct = self._structures()
        bis = [b for b in struct["bis"] if b["closed"]]
        zs = [z for z in struct["zhongshu"] if z["closed"]]
        beichi = [e for e in struct["beichi"] if e["closed"]]
        points = struct["points"]

        def dpos(raw_idx: int):
            if 0 <= int(raw_idx) < len(self.raw_days):
                return day_pos.get(self.raw_days[int(raw_idx)])
            return None

        bis_d = [(dpos(b["confirm_idx"]), b) for b in bis]
        zs_d = [(dpos(z["confirm_idx"]), z) for z in zs]
        be_d = [(dpos(e["confirm_idx"]), e) for e in beichi]
        pt_d = [(dpos(p["bar_idx"]), p) for p in points]

        def _in_window(dp, lo, hi):
            return dp is not None and lo <= dp <= hi

        feats = {}
        feats["bi_count_20d"] = sum(1 for dp, _ in bis_d if _in_window(dp, k - 19, k))
        last_bi = [b for dp, b in bis_d if dp is not None and dp <= k]
        feats["bi_last_dir"] = int(last_bi[-1]["dir"]) if last_bi else 0
        feats["zs_count_20d"] = sum(1 for dp, _ in zs_d if _in_window(dp, k - 19, k))
        last_zs = [z for dp, z in zs_d if dp is not None and dp <= k]
        feats["zs_width_last"] = (last_zs[-1].get("width_pct")
                                  if last_zs and last_zs[-1].get("width_pct") is not None else np.nan)
        recent_be = [e for dp, e in be_d if _in_window(dp, k - 19, k) and e.get("divergence")]
        feats["beichi_last_strength"] = recent_be[-1]["strength"] if recent_be else 0.0
        feats["beichi_div_count_20d"] = len(recent_be)
        feats["bs_net_5d"] = sum(1 if p["point"].startswith("B") else -1
                                for dp, p in pt_d if _in_window(dp, k - 4, k))
        last_pts = [p for dp, p in pt_d if _in_window(dp, k - 9, k)]
        feats["bs_last_sign"] = (1 if last_pts[-1]["point"].startswith("B") else -1) if last_pts else 0
        for name in FEAT_NAMES:
            self.features[name].append(float(feats[name]))
        self.last_structures = struct
        self.structure_counts.append({"day": self.days[-1],
                                      "bis_closed": len(bis), "zs_closed": len(zs),
                                      "beichi_closed": len(beichi), "points": len(points)})

    # ---- 输出
    def features_frame(self) -> pd.DataFrame:
        return pd.DataFrame({name: self.features[name] for name in FEAT_NAMES},
                            index=pd.Index(self.days, name="trade_date"))

    def to_payload(self) -> dict:
        return {"days": list(self.days),
                "features": {name: [float(v) for v in self.features[name]] for name in FEAT_NAMES},
                "structure_counts": self.structure_counts,
                "raw_bars": int(self.raw_n), "merged_bars": len(self.merged)}


def day_features(bars: pd.DataFrame) -> dict:
    """一次性喂入完整 bars（逐日）→ PIT 安全的日频特征 payload。

    严格等价于「每个交易日收盘后用当日及以前 bars 重算结构」；未来 bar 不影响历史行。
    """
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        return {"days": [], "features": {name: [] for name in FEAT_NAMES},
                "structure_counts": [], "raw_bars": 0, "merged_bars": 0}
    df = cf.norm_bars(bars)
    engine = ChanAsOfEngine()
    day_series = df["dt"].astype(str).str[:8]
    for day in sorted(day_series.unique().tolist()):
        engine.append_day(day, df[day_series == day])
    return engine.to_payload()


# ============================================================ PIT 截断一致性验证
def _bruteforce_last_day_features(bars: pd.DataFrame) -> dict:
    """独立暴力参考：对截断 bars 跑 analyze_chan，按同口径计算「最后一日」特征。"""
    res = cf.analyze_chan(bars)
    days = sorted({_day_of(d) for d in bars["dt"].astype(str)})
    if not days:
        return {name: np.nan for name in FEAT_NAMES}
    k = len(days) - 1
    daypos = {d: i for i, d in enumerate(days)}
    bar_days = [_day_of(d) for d in bars["dt"].astype(str)]

    def dpos(raw_idx: int):
        i = int(raw_idx)
        return daypos[bar_days[i]] if 0 <= i < len(bar_days) else None

    bis = [{"dpos": dpos(b["confirm_idx"]), "dir": int(b["dir"])} for b in res["bis"] if b.get("closed")]
    zs = [{"dpos": dpos(z["confirm_idx"]), "width_pct": z.get("width_pct")}
          for z in res["zhongshu"] if z.get("closed")]
    be = [{"dpos": dpos(e["confirm_idx"]), "strength": e.get("strength"),
           "div": bool(e.get("divergence"))} for e in res["beichi"] if e.get("closed")]
    pts = []
    for p in res["buy_sell_points"]:
        dp = dpos(p["bar_idx"])
        if dp is not None:
            pts.append({"dpos": dp, "sign": 1 if p["point"].startswith("B") else -1})

    def _in(dp, lo, hi):
        return dp is not None and lo <= dp <= hi

    out = {}
    out["bi_count_20d"] = sum(1 for b in bis if _in(b["dpos"], k - 19, k))
    last_bi = [b for b in bis if b["dpos"] is not None and b["dpos"] <= k]
    out["bi_last_dir"] = int(last_bi[-1]["dir"]) if last_bi else 0
    out["zs_count_20d"] = sum(1 for z in zs if _in(z["dpos"], k - 19, k))
    last_zs = [z for z in zs if z["dpos"] is not None and z["dpos"] <= k]
    out["zs_width_last"] = last_zs[-1]["width_pct"] if last_zs and last_zs[-1]["width_pct"] is not None else np.nan
    recent_be = [e for e in be if _in(e["dpos"], k - 19, k) and e["div"]]
    out["beichi_last_strength"] = recent_be[-1]["strength"] if recent_be else 0.0
    out["beichi_div_count_20d"] = len(recent_be)
    out["bs_net_5d"] = sum(p["sign"] for p in pts if _in(p["dpos"], k - 4, k))
    last_pts = [p for p in pts if _in(p["dpos"], k - 9, k)]
    out["bs_last_sign"] = last_pts[-1]["sign"] if last_pts else 0
    return out


def verify_truncation(bars: pd.DataFrame, sample_days: list[str] | None = None,
                      tol: float = 1e-9) -> dict:
    """抽样 code-day 截断重算一致性：引擎当日特征 vs 截断到当日暴力重算特征。

    不一致率 = 不一致特征数 / 抽样 code-day 数；≤ PIT_MISMATCH_MAX 视为 PIT 通过。
    """
    payload = day_features(bars)
    days = payload["days"]
    if not days or not sample_days:
        if not days:
            return {"checked_code_days": 0, "mismatch_counts": {n: 0 for n in FEAT_NAMES},
                    "mismatch_rates": {n: None for n in FEAT_NAMES},
                    "pass_by_feature": {n: False for n in FEAT_NAMES}, "details": [],
                    "pass": False}
        sample_days = [days[k] for k in (len(days) // 3, len(days) * 2 // 3, len(days) - 1)]
    engine_df = pd.DataFrame(payload["features"], index=payload["days"])
    dt = bars["dt"].astype(str)
    mismatches = {name: 0 for name in FEAT_NAMES}
    checked = 0
    details = []
    for day in sample_days:
        if day not in engine_df.index:
            continue
        trunc = bars[dt.str[:8] <= str(day)]
        if len(trunc) < MIN_BARS_FOR_STRUCTURE:
            continue
        ref = _bruteforce_last_day_features(trunc)
        if ref is None:
            continue
        checked += 1
        row_mis = []
        for name in FEAT_NAMES:
            a = engine_df.loc[day, name]
            b = ref[name]
            a_nan = (a is None) or (isinstance(a, float) and math.isnan(a))
            b_nan = (b is None) or (isinstance(b, float) and math.isnan(b))
            if a_nan and b_nan:
                continue
            if a_nan or b_nan or abs(float(a) - float(b)) > max(tol, abs(float(a)) * 1e-6):
                mismatches[name] += 1
                row_mis.append(name)
        details.append({"date": str(day), "mismatch": row_mis})
    rates = {name: (mismatches[name] / checked if checked else None) for name in FEAT_NAMES}
    rates_ok = {n: (v is not None and v <= PIT_MISMATCH_MAX) for n, v in rates.items()}
    return {"checked_code_days": checked, "mismatch_counts": mismatches,
            "mismatch_rates": rates, "pass_by_feature": rates_ok,
            "pass": bool(all(rates_ok.values())), "details": details}


# ============================================================ 前筛复用（只读 import）
def _load_prescreen():
    tools_dir = str(REPO / "tools" / "research")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import chan_factor_prescreen as ps  # noqa: E402
    return ps


def build_feature_matrices(bars_by_code: dict[str, pd.DataFrame],
                           window_dates: list[str]) -> tuple[dict[str, np.ndarray], dict, dict]:
    """PIT 引擎特征 → 窗口日 × 代码矩阵（与旧前筛同形状）；返回 (mats, feats, engine_payloads)。"""
    codes_ok = [c for c, b in bars_by_code.items() if isinstance(b, pd.DataFrame) and len(b) >= MIN_BARS_FOR_STRUCTURE]
    day_index = {d: i for i, d in enumerate(window_dates)}
    feats: dict[str, dict] = {}
    mats = {name: np.full((len(window_dates), len(codes_ok)), np.nan) for name in FEAT_NAMES}
    for j, code in enumerate(codes_ok):
        payload = day_features(bars_by_code[code])
        feats[code] = payload
        for k, day in enumerate(payload["days"]):
            row = day_index.get(day)
            if row is None:
                continue
            for name in FEAT_NAMES:
                val = payload["features"][name][k]
                if np.isfinite(val):
                    mats[name][row, j] = val
    return mats, feats, {"codes_ok": codes_ok}


# ============================================================ v2 前筛重跑
def run_prescreen_v2(n_codes: int = N_CODES, n_days: int = N_DAYS, seed: int = SEED,
                     out_prefix: str | Path | None = None, verify_codes: int = 6,
                     verify_days: tuple[int, ...] = (30, 60, 90)) -> dict:
    """PIT 安全特征重跑前筛（IC/分层/分级复用 chan_factor_prescreen.py 的纯函数）。"""
    t0 = time.time()
    ps = _load_prescreen()
    mapping = ps._code_files(ps.MINS)
    codes_all = sorted(mapping)
    rng = np.random.default_rng(ps.SEED if seed == SEED else seed)
    codes = sorted(rng.choice(codes_all, size=min(int(n_codes), len(codes_all)),
                              replace=False).tolist())
    bars_by_code, window_dates, missing = ps.load_bars(codes, mapping, n_days=n_days)
    print(f"[asof-v2] bars 加载 {len(window_dates)} 日 × {len(codes)} 只 {time.time()-t0:.1f}s", flush=True)
    adj = ps.load_adj_close(codes, window_dates)
    fwd = ps.forward_returns(adj, window_dates)
    print(f"[asof-v2] 标签就绪 {time.time()-t0:.1f}s", flush=True)

    mats, feats, meta = build_feature_matrices(bars_by_code, window_dates)
    codes_ok = meta["codes_ok"]
    print(f"[asof-v2] PIT 引擎特征就绪（{len(codes_ok)} 只）{time.time()-t0:.1f}s", flush=True)

    # ---- PIT 截断验证：6 只 × 3 窗口日（与 v1 同抽样口径）
    pit_codes = codes_ok[::max(1, len(codes_ok) // max(1, verify_codes))][:verify_codes]
    sample_days = [window_dates[k] for k in verify_days if k < len(window_dates)]
    pit_rows, pit_pass = [], {name: True for name in FEAT_NAMES}
    for code in pit_codes:
        chk = verify_truncation(bars_by_code[code], sample_days)
        pit_rows.append({"ts_code": code, **chk})
        for name, ok in chk["pass_by_feature"].items():
            if not ok:
                pit_pass[name] = False
    agg_rates = {}
    for name in FEAT_NAMES:
        vals = [r["mismatch_rates"][name] for r in pit_rows if r["mismatch_rates"][name] is not None]
        agg_rates[name] = float(np.mean(vals)) if vals else None
    print(f"[asof-v2] PIT 截断一致性检查完成 {time.time()-t0:.1f}s "
          f"passed={sum(pit_pass.values())}/{len(FEAT_NAMES)}", flush=True)

    # ---- v1 对照（只读旧报告）
    v1_path = ps.OUT / "chan_factor_prescreen_20261103.json"
    v1 = {}
    if v1_path.exists():
        v1 = json.loads(v1_path.read_text(encoding="utf-8"))

    # ---- IC / 分层 / 衰减 / 分级（复用 ps 纯函数）
    features_payload: dict[str, dict] = {}
    for name in FEAT_NAMES:
        mat = mats[name]
        coverage = round(100.0 * np.isfinite(mat).sum() / mat.size, 2) if mat.size else 0.0
        ic_by_h, layers, decay = {}, {}, {}
        for h in HORIZONS:
            ic = ps.rank_ic(mat, fwd[h])
            st = ps.ic_stats(ic)
            ic_by_h[f"h{h}"] = st
            decay[f"h{h}"] = st["ic_mean"]
            layers[f"h{h}"] = ps.layer_stats(mat, fwd[h], h)
        pit_info = {"pass": bool(pit_pass[name]),
                    "sampled_mismatch_rate": _num(agg_rates[name], 4) if agg_rates[name] is not None else None,
                    "method": "PIT 引擎按日截断重算 vs 截断到当日暴力重算（同口径特征）"}
        grade, reasons = ps.grade_factor(ic_by_h, coverage, pit_info["pass"])
        v1_ic = ((v1.get("features", {}) or {}).get(name, {}) or {}).get("ic", {})
        v1_pit = ((v1.get("features", {}) or {}).get(name, {}) or {}).get("pit", {})
        features_payload[name] = {
            "name": name, "label": FEATURE_LABELS[name], "family": FEATURE_FAMILIES[name],
            "coverage": {"valid": int(np.isfinite(mat).sum()), "expected": int(mat.size), "pct": coverage},
            "ic": ic_by_h, "decay": decay, "layers": layers,
            "pit": pit_info, "grade": grade, "grade_reasons": reasons,
            "v1": {"ic20": ((v1_ic.get("h20") or {}).get("ic_mean")),
                   "ir20": ((v1_ic.get("h20") or {}).get("ir")),
                   "pit_pass": v1_pit.get("pass"),
                   "pit_mismatch_rate": v1_pit.get("sampled_mismatch_rate"),
                   "grade": ((v1.get("features", {}) or {}).get(name, {}) or {}).get("grade")},
        }
        print(f"[asof-v2] {name}: cov={coverage}% IC20={ic_by_h['h20']['ic_mean']} "
              f"IR20={ic_by_h['h20']['ir']} pit={pit_info['pass']} grade={grade}", flush=True)

    rep = {
        "schema_version": "chan_factor_prescreen.v2",
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "researcher": "factor-dev", "task": "task-153 W2-6b 缠论 PIT 安全引擎 + 前筛 v2",
            "source": "quant_backtest/factor_lib/chan_asof_engine.py（PIT 安全）"
                      " + chan_factor_prescreen.py（IC/分层逻辑只读复用，未修改）",
            "pit_method": "按交易日增量喂入 30 分钟 bars，每日收盘后只用截至当日 bar 重算"
                          "分型/笔/中枢/背驰/买卖点，仅统计 closed 且确认日 ≤ 当日的结构",
            "sample": {"n_codes_requested": n_codes, "n_codes_loaded": len(codes_ok),
                       "n_days": len(window_dates), "window_start": window_dates[0],
                       "window_end": window_dates[-1], "seed": seed,
                       "bar_freq": "30min（1min→30min 聚合，每日 8 根）"},
            "horizons": list(HORIZONS), "primary_horizon": PRIMARY_H,
            "data_root": str(MINS), "codes_insufficient": missing,
            "engine_version": LIB_VERSION, "runtime_sec": None,
            "v1_report": str(v1_path) if v1 else None,
            "v1_pit_mismatch_max": PIT_MISMATCH_MAX,
        },
        "thresholds": ps.THRESHOLDS,
        "pit_check": {
            "sampled": pit_rows,
            "aggregate_mismatch_rates": agg_rates,
            "pass_by_feature": pit_pass,
            "method": "对 6 只 × 3 个窗口日：bars 截断到当日收盘 → analyze_chan 暴力重算特征 vs "
                      "PIT 引擎当日特征（不一致率 ≤10% 视为通过）",
            "v1_reported_aggregate_mismatch_rates": (v1.get("pit_check", {}) or {}).get(
                "aggregate_mismatch_rates"),
            "note": "v1 的不一致来自「全样本 analyze_chan + 按确认日 as-of」快照：closed 状态"
                    "依赖未来 bar；v2 引擎按日截断重算后结构性修正。",
        },
        "features": features_payload,
        "summary": {},
        "limitations": [
            "样本 60 只 × 120 交易日（seed=42），横截面小、5 层每组约 12 只，RankIC/分层噪声大；",
            "PIT 引擎从窗口首日开始累积结构（无窗口前历史），前 ~20 日覆盖偏低；",
            "30 分钟 bar 由 1 分钟聚合；未来收益为日频收盘复权、未做 T+1 入场/成本/涨跌停可成交性；",
            "特征方向无先验（缠论结构本身不带方向），前筛按 |IC|/|IR| 判强度，方向需正式四关确认；",
            "v2 已修复结构性 PIT 缺陷；但覆盖 60 只 × 120 日的前筛不等于四关，正式验证仍需样本外/成本/容量。",
            "不构成投资建议；前筛不代替正式四关。",
        ],
    }
    summary = {"通过": [], "观察": [], "不通过": []}
    for name, f in features_payload.items():
        summary[f["grade"]].append(name)
    rep["summary"] = summary
    rep["meta"]["runtime_sec"] = round(time.time() - t0, 1)

    prefix = Path(out_prefix) if out_prefix else (OUT / "chan_factor_prescreen_20261103_v2")
    prefix.parent.mkdir(parents=True, exist_ok=True)
    js = prefix.with_suffix(".json")
    md = prefix.with_suffix(".md")
    js.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    md.write_text(render_v2_markdown(rep), encoding="utf-8")
    print(f"[asof-v2] 输出 {js} / {md}（{rep['meta']['runtime_sec']}s）", flush=True)
    return rep


def render_v2_markdown(rep: dict) -> str:
    meta = rep["meta"]
    feats = rep["features"]
    pit = rep["pit_check"]
    L = [
        "# 缠论因子前筛 v2（PIT 安全引擎重跑 · task-153）", "",
        f"- 生成：{meta['generated_at']}；样本 **{meta['sample']['n_codes_loaded']} 只 × "
        f"{meta['sample']['n_days']} 交易日**（{meta['sample']['window_start']}~"
        f"{meta['sample']['window_end']}，seed={meta['sample']['seed']}）；30 分钟 bar",
        "- 来源：`quant_backtest/factor_lib/chan_asof_engine.py`（新，PIT 安全）+ "
        "`tools/research/chan_factor_prescreen.py`（IC/分层逻辑只读复用，未修改）",
        f"- PIT 方法：{meta['pit_method']}；运行时 {meta['runtime_sec']}s；数据根只读", "",
        "## 一、PIT 截断一致性（v1 → v2 对照）", "",
        f"- 抽样 6 只 × 3 窗口日，截断到当日重算 → 特征不一致率 ≤ {PIT_MISMATCH_MAX:.0%} 视为通过；",
        "| 特征 | v1 不一致率（旧快照） | v2 不一致率（PIT 引擎） | v2 通过 |",
        "|---|---|---|---|"]
    v1_rates = pit.get("v1_reported_aggregate_mismatch_rates") or {}
    for name in FEAT_NAMES:
        L.append(f"| {name} | {v1_rates.get(name, '—')} | {_num(pit['aggregate_mismatch_rates'].get(name), 4)} | "
                 f"{'✅' if pit['pass_by_feature'].get(name) else '❌'} |")
    L += ["", "## 二、汇总（v2 PIT 安全特征；v1 值仅对照）", "",
          "| 特征 | 家族 | 覆盖率 | v1 IC20 | v2 IC20 | v2 IR20 | 半窗同号 | 5层多空(20d年化) | 单调性 | PIT | 分级 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, f in feats.items():
        st = f["ic"]["h20"]
        lay = f["layers"]["h20"]
        ls = (lay.get("long_short") or {}).get("ann_pct")
        v1 = f.get("v1") or {}
        L.append("| {} | {} | {}% | {} | {} | {} | {} | {} | {} | {} | **{}** |".format(
            name, f["family"], f["coverage"]["pct"], v1.get("ic20"), st["ic_mean"], st["ir"],
            (st["half_split"] or {}).get("same_sign"),
            "—" if ls is None else f"{ls}%",
            "—" if lay.get("monotonicity") is None else lay["monotonicity"],
            "✅" if f["pit"]["pass"] else "❌", f["grade"]))
    L += ["", "### 衰减曲线（v2 RankIC 均值）", "",
          "| 特征 | 5d | 10d | 20d |", "|---|---|---|---|"]
    for name, f in feats.items():
        L.append(f"| {name} | {f['decay'].get('h5')} | {f['decay'].get('h10')} | {f['decay'].get('h20')} |")
    L += ["", "## 三、v2 分级汇总", ""]
    for grade, names in rep["summary"].items():
        L.append(f"- **{grade}**（{len(names)}）：{', '.join(names) if names else '—'}")
    L += ["", "## 四、逐特征结论", ""]
    for name, f in feats.items():
        L.append(f"### {name}（{f['label']}）— **{f['grade']}**")
        L.append("")
        for r in f["grade_reasons"]:
            L.append(f"- {r}")
        v1 = f.get("v1") or {}
        L.append(f"- 对照 v1：IC20 {v1.get('ic20')} → v2 {f['ic']['h20']['ic_mean']}；"
                 f"IR20 {v1.get('ir20')} → v2 {f['ic']['h20']['ir']}；"
                 f"PIT {v1.get('pit_pass')} → {f['pit']['pass']}（不一致率 "
                 f"{v1.get('pit_mismatch_rate')} → {f['pit']['sampled_mismatch_rate']}）")
        L.append("")
    L += ["## 五、局限", ""] + [f"- {x}" for x in rep["limitations"]]
    return "\n".join(L) + "\n"


# ============================================================ CLI
def cmd_list(as_json: bool) -> int:
    rows = [{"name": n, "label": FEATURE_LABELS[n], "family": FEATURE_FAMILIES[n],
             "pit_safe": True} for n in FEAT_NAMES]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        print(f"# chan_asof_engine v{LIB_VERSION} · {len(rows)} 个 PIT 安全日频缠论特征")
        for row in rows:
            print(f"  {row['name']:<26} {row['label']:<28} [{row['family']}]")
    return 0


def cmd_verify(args) -> int:
    ps = _load_prescreen()
    mapping = ps._code_files(ps.MINS)
    codes_all = sorted(mapping)
    rng = np.random.default_rng(args.seed)
    codes = sorted(rng.choice(codes_all, size=min(args.n_codes, len(codes_all)),
                              replace=False).tolist())
    bars_by_code, window_dates, missing = ps.load_bars(codes, mapping, n_days=args.n_days)
    sample_days = [window_dates[k] for k in (30, 60, 90) if k < len(window_dates)]
    print(f"# 验证：{len(codes)} 只 × 窗口 {window_dates[0]}~{window_dates[-1]}，抽样日 {sample_days}")
    all_rates = []
    for code in codes:
        bars = bars_by_code.get(code)
        if bars is None or len(bars) < MIN_BARS_FOR_STRUCTURE:
            continue
        chk = verify_truncation(bars, sample_days)
        all_rates.append(chk)
        print(f"  {code}: checked={chk['checked_code_days']} pass={chk['pass']} "
              f"rates={ {k: _num(v, 4) for k, v in chk['mismatch_rates'].items()} }")
    if not all_rates:
        print("[chan-asof] 无可用代码", file=sys.stderr)
        return 2
    agg = {name: (_num(float(np.mean([r["mismatch_rates"][name] for r in all_rates
                                      if r["mismatch_rates"][name] is not None])), 4)
                  if any(r["mismatch_rates"][name] is not None for r in all_rates) else None)
           for name in FEAT_NAMES}
    passed = all(v is not None and v <= PIT_MISMATCH_MAX for v in agg.values())
    print(f"# 聚合不一致率：{agg}")
    print(f"# PIT 一致性：{'通过' if passed else '不通过'}（阈值 ≤{PIT_MISMATCH_MAX:.0%}）")
    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="chan_asof_engine.py",
                                 description="W2-6b 缠论 PIT 安全引擎")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list")
    p_list.add_argument("--json", action="store_true")
    p_verify = sub.add_parser("verify", help="抽样验证 PIT 截断一致性")
    p_verify.add_argument("--n-codes", type=int, default=6)
    p_verify.add_argument("--n-days", type=int, default=60)
    p_verify.add_argument("--seed", type=int, default=SEED)
    p_run = sub.add_parser("prescreen", help="重跑前筛 v2")
    p_run.add_argument("--n-codes", type=int, default=N_CODES)
    p_run.add_argument("--n-days", type=int, default=N_DAYS)
    p_run.add_argument("--seed", type=int, default=SEED)
    p_run.add_argument("--out-prefix", default=str(OUT / "chan_factor_prescreen_20261103_v2"))
    p_run.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args.json)
    if args.cmd == "verify":
        return cmd_verify(args)
    rep = run_prescreen_v2(n_codes=args.n_codes, n_days=args.n_days, seed=args.seed,
                           out_prefix=args.out_prefix)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(f"# v2 分级：{ {k: len(v) for k, v in rep['summary'].items()} }")
    return 0


if __name__ == "__main__":
    sys.exit(main())
