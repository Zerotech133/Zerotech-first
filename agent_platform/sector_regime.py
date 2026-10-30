# -*- coding: utf-8 -*-
"""板块/行业 regime（2026-10-30 WO-METHODS-EXIT）。

优先用同花顺行业指数（TS ts_ths_index type=I + ts_ths_member 映射 + ts_ths_daily）；
板块数据缺失/该日期无数据时回退大盘 index 的 regime。只读、本地、无网络。
"""
from __future__ import annotations

import glob
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TDX = os.path.join(BASE, "quant_backtest", "data", "tdx")
THS_INDEX = os.path.join(TDX, "ts_ths_index")
THS_MEMBER = os.path.join(TDX, "ts_ths_member")
THS_DAILY = os.path.join(TDX, "ts_ths_daily")


def _files(path):
    return sorted(glob.glob(os.path.join(path, "*.parquet")))


def load_map(codes):
    """股票6位代码 → THS 行业指数 code；无映射返回 {}。"""
    try:
        import pandas as pd
    except ImportError:
        return {}
    codes = {str(c)[:6] for c in codes}
    try:
        idx = pd.concat([pd.read_parquet(f, columns=["ts_code", "name", "type"])
                         for f in _files(THS_INDEX)], ignore_index=True)
    except Exception:  # noqa: BLE001
        return {}
    sector_codes = set(idx[idx["type"] == "I"]["ts_code"].tolist())
    if not sector_codes:
        return {}
    out = {}
    try:
        for f in _files(THS_MEMBER):
            df = pd.read_parquet(f, columns=["ts_code", "con_code"])
            df = df[df["ts_code"].isin(sector_codes)]
            df["digits"] = df["con_code"].astype(str).str[:6]
            sub = df[df["digits"].isin(codes)]
            for _, r in sub.iterrows():
                out.setdefault(str(r["digits"]), str(r["ts_code"]))
    except Exception:  # noqa: BLE001
        return out
    return out


def _regime_from_closes(dates, closes, ma=20):
    if len(closes) < ma + 2:
        return {}
    out = {}
    for i in range(ma, len(closes)):
        cur = sum(closes[i - ma + 1:i + 1]) / ma
        prev = sum(closes[i - ma:i]) / ma
        out[str(dates[i])] = bool(closes[i] > cur and cur >= prev)
    return out


def load_regime(codes, ma=20):
    """返回 ({code: {date: bool}}, meta)。板块无数据时返回空 map，调用方回退大盘。"""
    mapping = load_map(codes)
    if not mapping:
        return {}, {"sector_covered": 0, "latest_date": None, "reason": "no_map"}
    sector_codes = set(mapping.values())
    try:
        import pandas as pd
    except ImportError:
        return {}, {"sector_covered": 0, "latest_date": None, "reason": "no_pandas"}
    try:
        frames = []
        for f in _files(THS_DAILY):
            df = pd.read_parquet(f, columns=["ts_code", "trade_date", "close"])
            frames.append(df[df["ts_code"].isin(sector_codes)])
        if not frames:
            return {}, {"sector_covered": 0, "latest_date": None, "reason": "no_daily"}
        daily = pd.concat(frames, ignore_index=True)
    except Exception:  # noqa: BLE001
        return {}, {"sector_covered": 0, "latest_date": None, "reason": "read_error"}
    out = {}
    for sector in sector_codes:
        sub = daily[daily["ts_code"] == sector].sort_values("trade_date")
        if sub.empty:
            continue
        reg = _regime_from_closes(sub["trade_date"].astype(str).tolist(),
                                  sub["close"].astype(float).tolist(), ma)
        for code, sc in mapping.items():
            if sc == sector:
                out[code] = reg
    latest = str(daily["trade_date"].max()) if not daily.empty else None
    return out, {"sector_covered": len(out), "latest_date": latest,
                 "sectors": len(set(mapping.values()))}


def combined(market_regime, sector_map):
    """日期级合并：板块有值用板块，否则用大盘。"""
    market_regime = market_regime or {}
    if not sector_map:
        return dict(market_regime)
    dates = set(market_regime) | set(sector_map)
    return {d: sector_map.get(d, market_regime.get(d, True)) for d in dates}


def _read_sector_daily(sector_codes):
    try:
        import pandas as pd
    except ImportError:
        return None
    try:
        frames = []
        for f in _files(THS_DAILY):
            df = pd.read_parquet(f, columns=["ts_code", "trade_date", "close"])
            frames.append(df[df["ts_code"].isin(sector_codes)])
        return pd.concat(frames, ignore_index=True) if frames else None
    except Exception:  # noqa: BLE001
        return None


def load_relative_strength(bars_by_code, lookback=20):
    """个股相对行业指数强弱：date → bool（个股20日收益 > 行业20日收益）。

    输入 bars_by_code 为 {code: [[date,open,close,...], ...]}；缺行业映射/数据返回空。
    """
    codes = list((bars_by_code or {}).keys())
    mapping = load_map(codes)
    if not mapping:
        return {}, {"relative_covered": 0, "reason": "no_map"}
    daily = _read_sector_daily(set(mapping.values()))
    if daily is None or daily.empty:
        return {}, {"relative_covered": 0, "reason": "no_daily"}
    import pandas as pd
    out = {}
    covered = 0
    for code, bars in bars_by_code.items():
        sector = mapping.get(str(code)[:6])
        if not sector or not bars or len(bars) < lookback + 1:
            continue
        sub = daily[daily["ts_code"] == sector].sort_values("trade_date")
        if sub.empty:
            continue
        sec = {str(r["trade_date"]): float(r["close"]) for _, r in sub.iterrows()}
        sec_dates = sorted(sec)
        stock = {str(b[0]): float(b[2]) for b in bars}
        dates = sorted(stock)
        reg = {}
        for i in range(lookback, len(dates)):
            d = dates[i]
            prev = dates[i - lookback]
            # 行业取 <= 当日 的最近收盘
            cand = [x for x in sec_dates if x <= d]
            cand_prev = [x for x in sec_dates if x <= prev]
            if not cand or not cand_prev:
                continue
            sc_now, sc_prev = sec[cand[-1]], sec[cand_prev[-1]]
            if sc_prev <= 0 or stock[prev] <= 0:
                continue
            reg[d] = (stock[d] / stock[prev] - 1.0) > (sc_now / sc_prev - 1.0)
        if reg:
            out[str(code)] = reg
            covered += 1
    return out, {"relative_covered": covered}


def combine_all(*maps):
    """多 regime 日期级 AND；缺失的 map 不参与（不误杀）。"""
    maps = [m for m in maps if m]
    if not maps:
        return {}
    dates = set()
    for m in maps:
        dates.update(m.keys())
    out = {}
    for d in dates:
        vals = [m.get(d) for m in maps if d in m]
        out[d] = all(vals) if vals else True
    return out
