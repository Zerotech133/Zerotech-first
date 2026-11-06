# -*- coding: utf-8 -*-
"""P3 事件研究引擎（WP-PIT-v1 范围 B）.

接口（方法论 P3 承诺）:
  study(event_key, window=(-20,20), benchmark='market_ew')
    -> 超额收益曲线 + t 值 + PIT 证明

T0 语义（防 look-ahead 的核心）:
  T0 = 事件日 + lag_days 个交易日（可见首日, cells.yaml: publish→1）;
  基点 = T0 前一交易日收盘;
  窗口 = 该标的自身行情日序列的相对偏移（停牌自动滑过）;
  超额 = 个股 ret(h) − 基准 ret(h)（同窗口对齐）。

基准:
  market_ew  全市场等权日收益（ts_daily 聚合, v0 主基准——申万指数日线缺失）
  sw_l1      预留: 801xxx 指数行情入库后启用（sw_l1_member 映射已在仓）

首批事件: 龙虎榜上榜 / 大宗折价<-9% / 目标价上修>5% / 停牌。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from warehouse_reader import WarehouseReader  # noqa: E402
from pit_visibility import resolve_lag_days, visibility_proof  # noqa: E402


# ---------------------------------------------------------------- #
# 事件定义
# ---------------------------------------------------------------- #

@dataclass
class EventDef:
    key: str
    name: str
    table: str
    date_col: str                      # 事件日列
    kind: str = "publish"              # event_date_kind(cells.yaml)
    lag_days: Optional[int] = None     # None → 按 kind 默认
    select: Optional[Callable[[pd.DataFrame], pd.DataFrame]] = None
    desc: str = ""


def _ev_lhb(df: pd.DataFrame) -> pd.DataFrame:
    return df  # 上榜即事件


def _ev_block_discount(df: pd.DataFrame, threshold: float = -0.09):
    """大宗折价: price / 当日收盘 - 1 < threshold. 收盘价 join ts_daily."""
    r = WarehouseReader()
    dl = r.read("ts_daily", columns=["ts_code", "trade_date", "close"])
    m = df.merge(dl.rename(columns={"trade_date": "trade_date_px"}),
                 left_on=["ts_code", "trade_date"],
                 right_on=["ts_code", "trade_date_px"], how="left")
    with np.errstate(all="ignore"):
        disc = m["price"] / m["close"] - 1
    out = m[disc < threshold].copy()
    out["discount"] = disc[disc < threshold]
    return out


def _ev_tp_upgrade(df: pd.DataFrame, threshold: float = 0.05):
    """目标价上修: 同标的 tp 相对上一条有效研报 tp 提升 > threshold."""
    d = df.sort_values(["ts_code", "report_date"]).copy()
    d["tp"] = pd.to_numeric(d["tp"], errors="coerce")
    prev = d.groupby("ts_code")["tp"].shift(1)
    d["tp_prev"] = prev
    m = (d["tp"].notna() & d["tp_prev"].notna() & (d["tp_prev"] > 0))
    out = d[m & (d["tp"] / d["tp_prev"] - 1 > threshold)].copy()
    out["tp_change"] = out["tp"] / out["tp_prev"] - 1
    return out


def _ev_suspend(df: pd.DataFrame) -> pd.DataFrame:
    return df  # 停牌起始即事件


BUILTIN_EVENTS: Dict[str, EventDef] = {
    "lhb_uplist": EventDef("lhb_uplist", "龙虎榜上榜", "ts_top_list",
                           "trade_date", "publish", 1, _ev_lhb,
                           "进入龙虎榜个股明细(top_list)"),
    "block_discount9": EventDef("block_discount9", "大宗折价<-9%", "ts_block_trade",
                                "trade_date", "publish", 1,
                                lambda df: _ev_block_discount(df, -0.09),
                                "大宗成交价较当日收盘折价超9%"),
    "tp_upgrade5": EventDef("tp_upgrade5", "目标价上修>5%", "report_rc",
                            "report_date", "publish", 1,
                            lambda df: _ev_tp_upgrade(df, 0.05),
                            "研报目标价较上一条有效目标价上修超5%"),
    "suspend_start": EventDef("suspend_start", "停牌起始", "ts_suspend_d",
                              "trade_date", "publish", 1, _ev_suspend,
                              "个股停牌起始日"),
}


# ---------------------------------------------------------------- #
# 引擎
# ---------------------------------------------------------------- #

class EventStudy:
    def __init__(self, benchmark: str = "market_ew"):
        self.reader = WarehouseReader()
        self.benchmark = benchmark
        self._stock_days: Optional[pd.DataFrame] = None   # ts_code → 交易日序列
        self._stock_px: Optional[Dict[str, pd.Series]] = None
        self._bench_ret: Optional[pd.Series] = None       # date → 市场等权日收益

    # ---- 行情底座 ----
    def _load_px(self):
        if self._stock_px is not None:
            return
        dl = self.reader.read("ts_daily", columns=["ts_code", "trade_date", "close"])
        dl = dl.dropna().drop_duplicates(["ts_code", "trade_date"], keep="last")
        dl = dl.sort_values(["ts_code", "trade_date"])
        self._stock_days = dl.groupby("ts_code")["trade_date"].agg(list).to_dict()
        self._stock_px = {k: g.set_index("trade_date")["close"]
                          for k, g in dl.groupby("ts_code")}
        # 全市场等权日收益
        dl = dl.sort_values(["ts_code", "trade_date"])
        dl["ret"] = dl.groupby("ts_code")["close"].pct_change()
        self._bench_ret = dl.groupby("trade_date")["ret"].mean()
        print(f"  行情底座: {len(self._stock_px)} 标的 / "
              f"{self._bench_ret.index.min()}~{self._bench_ret.index.max()}")

    @staticmethod
    def _window_rets(px: pd.Series, days: List[str], t0: str,
                     window: Tuple[int, int]) -> Dict[int, float]:
        """以 T0 前一交易日收盘为基点的逐窗收益. days=T0 所在标的行情日列表."""
        if t0 not in days:
            return {}
        i0 = days.index(t0)
        if i0 == 0:
            return {}
        base_px = px.get(days[i0 - 1])
        if base_px is None or not base_px or np.isnan(base_px):
            return {}
        out = {}
        for h in range(window[0], window[1] + 1):
            j = i0 + h
            if 0 <= j < len(days) and h != 0:
                p = px.get(days[j])
                if p and not np.isnan(p):
                    out[h] = p / base_px - 1
        return out

    def _bench_rets(self, days: List[str], t0: str,
                    window: Tuple[int, int]) -> Dict[int, float]:
        """基准同口径(基点=市场 T0-1 收盘):
        正窗 h>=0: 市场(T0+h 收盘)/市场(T0-1 收盘)-1 = T0..T0+h 日收益累计
                  (教训: 必须含 T0 当日, 自测手工对照曾抓出漏算首日 bug);
        负窗 h<0: 市场(T0+h)/市场(T0-1)-1 = (T0+h+1)..(T0-1) 日收益累计."""
        br = self._bench_ret
        if t0 not in days:
            return {}
        i0 = days.index(t0)
        if i0 == 0:
            return {}
        out: Dict[int, float] = {}
        acc = 0.0
        for h in range(0, window[1] + 1):
            j = i0 + h
            if j >= len(days):
                break
            r = br.get(days[j])
            if r is not None and not np.isnan(r):
                acc += np.log1p(r)
            out[h] = float(np.expm1(acc))
        for h in range(window[0], 0, -1):
            j = i0 + h
            if j < 0:
                break
            acc2 = 0.0
            for k in range(i0 + h + 1, i0):
                r = br.get(days[k])
                if r is not None and not np.isnan(r):
                    acc2 += np.log1p(r)
            out[h] = float(np.expm1(acc2))
        return out

    # ---- 主入口 ----
    def study(self, event_key: str, window: Tuple[int, int] = (-20, 20),
              event_def: Optional[EventDef] = None,
              max_events: int = 20000) -> Dict:
        ev = event_def or BUILTIN_EVENTS.get(event_key)
        if ev is None:
            raise ValueError(f"未知事件: {event_key}")
        self._load_px()
        lag = resolve_lag_days(ev.kind, ev.lag_days)

        df = self.reader.read(ev.table)
        n_total = len(df)
        sel = ev.select(df) if ev.select else df
        cols_keep = [c for c in (ev.date_col, "ts_code") if c in sel.columns]
        sel = sel[cols_keep + [c for c in ("discount", "tp_change", "reason",
                                           "suspend_type") if c in sel.columns]]
        # PIT: 事件日压缩为 d8 字符串
        sel = sel.dropna(subset=[ev.date_col]).copy()
        sel["_d"] = sel[ev.date_col].astype(str).str[:10].str.replace("-", "")
        sel = sel[sel["_d"].str.fullmatch(r"\d{8}")]
        if len(sel) > max_events:
            sel = sel.sample(max_events, random_state=42)

        # h=0 是基点日(无收益语义), 不进曲线
        hs = [h for h in range(window[0], window[1] + 1) if h != 0]
        rows = []
        skipped = {"no_t0": 0, "no_px_base": 0}
        for _, e in sel.iterrows():
            code = str(e["ts_code"])
            days = self._stock_days.get(code)
            px = self._stock_px.get(code)
            if not days or px is None:
                skipped["no_px_base"] += 1
                continue
            t0 = pit_t0(days, str(e["_d"]), lag)
            if t0 is None:
                skipped["no_t0"] += 1
                continue
            sr = self._window_rets(px, days, t0, window)
            if not sr:
                skipped["no_px_base"] += 1
                continue
            br = self._bench_rets(days, t0, window)
            for h in hs:
                if h in sr and h in br and not np.isnan(br.get(h, np.nan)):
                    rows.append({"h": h, "excess": sr[h] - br[h],
                                 "raw": sr[h]})
        if not rows:
            return {"event": ev.key, "error": "no_settled_events"}

        r = pd.DataFrame(rows)
        g = r.groupby("h")["excess"]
        curve = pd.DataFrame({
            "n": g.count(),
            "mean_excess": g.mean(),
            "win_rate": g.apply(lambda s: (s > 0).mean()),
            "t_stat": g.apply(lambda s: _tstat(s)),
        }).reindex(hs)
        curve.index.name = "h"

        raw_g = r.groupby("h")["raw"]
        raw_curve = pd.DataFrame({"n": raw_g.count(),
                                  "mean_raw": raw_g.mean()}).reindex(hs)

        return {
            "event": ev.key, "name": ev.name, "desc": ev.desc,
            "table": ev.table, "benchmark": self.benchmark,
            "window": list(window), "lag_days": lag,
            "pit": visibility_proof(n_total, len(sel), 0, kind=ev.kind,
                                    lag_days=lag),
            "skipped": skipped,
            "curve": curve.round(6).to_dict(orient="index"),
            "raw_curve": raw_curve.round(6).to_dict(orient="index"),
        }


def pit_t0(days: List[str], event_d: str, lag: int) -> Optional[str]:
    """可见首日 T0 = 事件日后第 lag 个交易日(标的自身行情日历)."""
    later = [d for d in days if d > event_d]
    if len(later) < lag:
        return None
    return later[lag - 1] if lag >= 1 else None


def _tstat(s: pd.Series) -> float:
    s = s.dropna()
    if len(s) < 2:
        return float("nan")
    se = s.std(ddof=1) / np.sqrt(len(s))
    return float(s.mean() / se) if se else float("nan")


def run_all(window: Tuple[int, int] = (-20, 20)) -> Dict:
    """四事件全跑, 结果落 reports/p3_event_study_results.json."""
    es = EventStudy()
    out = {}
    for key in BUILTIN_EVENTS:
        print(f"[{key}] …")
        try:
            out[key] = es.study(key, window=window)
        except Exception as e:  # noqa: BLE001
            out[key] = {"event": key, "error": f"{type(e).__name__}: {e}"}
        c = out[key].get("curve") or {}
        h5 = c.get(5) or {}
        print(f"  事件数={out[key].get('pit', {}).get('n_visible')} "
              f"T+5 mean_excess={h5.get('mean_excess')} t={h5.get('t_stat')} "
              f"err={out[key].get('error', '-')}")
    path = os.path.join(_HERE, "reports", "p3_event_study_results.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print(f"saved -> {path}")
    return out


if __name__ == "__main__":
    run_all()
