"""缠论选股策略（CHAN）— 二买/三买候选生成。

自 V1 modules.candidate_engine.strategies.chan.strategy 移植（评分语义原样）。
V1 的 SQL universe/OHLCV 加载改为 V2 ORM（v2_bar_daily + v2_instrument）。

流程：流动性预过滤（全市场→约千余只）→ 逐股缠论结构分析 → 二买/三买筛选评分。

评分 = 0.40×信号强度（三买1.0 / 带MACD背驰二买1.0 / 二买0.75）
     + 0.30×背驰强度（strong 1.0 / medium 0.6 / weak 0.3；MACD背驰再+0.2封顶1.0）
     + 0.20×中枢质量（延伸笔数越多越可靠，5笔封顶）
     + 0.10×量价同步
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.strategy.strategies.chan import engine as chan_engine
from zhice_os_v2.domain.strategy.strategies.chan.engine import (
    weekly_trend_permits_long,
)
from zhice_os_v2.domain.strategy.strategies.chan.intraday import (
    intraday_confirms_buy,
)
from zhice_os_v2.domain.strategy.strategies.chan.market_regime import (
    market_regime_permits_entry,
)
from zhice_os_v2.domain.strategy.strategies.trend_momentum import is_st_like
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session

logger = logging.getLogger(__name__)

LOOKBACK_CALENDAR_DAYS = 400     # 自然日窗口 ≈ 270 根日K，中枢结构需要长历史
MIN_AVG_TURNOVER_20D = 30_000_000
_pmin, _pmax = get_profile("CN").candidate_price_band()
PRICE_MIN, PRICE_MAX = float(_pmin), float(_pmax)   # 3~500

_SIGNAL_BASE = {"b3": 1.0, "b2": 0.75}
_DIV_SCORE = {"strong": 1.0, "medium": 0.6, "weak": 0.3}

_BS_LABEL = {"b2": "二买", "b3": "三买"}


def _load_universe(trade_date: date, market: str = "CN") -> list[str]:
    """交易门槛过滤：近20日均额达标、价格区间内、非ST——覆盖全部可交易股票，不设人工上限。"""
    d20 = trade_date - timedelta(days=35)
    try:
        session = get_session()
        try:
            rows = session.query(
                orm.BarDailyModel.symbol.label("ts_code"),
                orm.InstrumentModel.name,
                orm.BarDailyModel.trade_date,
                orm.BarDailyModel.close,
                orm.BarDailyModel.turnover,
            ).join(
                orm.InstrumentModel,
                orm.InstrumentModel.instrument_id == orm.BarDailyModel.instrument_id,
            ).filter(
                orm.BarDailyModel.market == market,
                orm.BarDailyModel.trade_date > d20,
                orm.BarDailyModel.trade_date <= trade_date,
                orm.InstrumentModel.status == "ACTIVE",
            ).all()
        finally:
            session.close()
    except Exception as e:
        logger.warning("chan.load_universe_failed: %s", e)
        return []

    df = pd.DataFrame(
        [(r[0], r[1] or "", r[2], r[3], r[4]) for r in rows],
        columns=["ts_code", "name", "trade_date", "close", "turnover"],
    )
    if df.empty:
        return []
    for col in ("close", "turnover"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close", "turnover"])
    df = df[df["close"] > 0]

    last = (df.sort_values(["ts_code", "trade_date"])
              .groupby("ts_code").tail(1).set_index("ts_code"))
    g = df.groupby("ts_code").agg(
        avg_turnover=("turnover", "mean"),
        name=("name", "first"),
    )
    g["last_close"] = last["close"]
    ok = g[(g["avg_turnover"] >= MIN_AVG_TURNOVER_20D)
           & g["last_close"].between(PRICE_MIN, PRICE_MAX)]
    ok = ok.sort_values("avg_turnover", ascending=False)
    return [code for code, r in ok.iterrows() if not is_st_like(r["name"])]


def _load_ohlcv(trade_date: date, ts_codes: list[str], market: str = "CN") -> pd.DataFrame:
    """批量加载候选池的 OHLCV 窗口数据（V2 v2_bar_daily，列结构与 V1 一致）。"""
    if not ts_codes:
        return pd.DataFrame()
    start = trade_date - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    try:
        session = get_session()
        try:
            rows = session.query(
                orm.BarDailyModel.symbol.label("ts_code"),
                orm.InstrumentModel.instrument_id.label("security_id"),
                orm.BarDailyModel.trade_date.label("dt"),
                orm.BarDailyModel.open,
                orm.BarDailyModel.high,
                orm.BarDailyModel.low,
                orm.BarDailyModel.close,
                orm.BarDailyModel.volume,
                orm.BarDailyModel.turnover.label("amount"),
            ).join(
                orm.InstrumentModel,
                orm.InstrumentModel.instrument_id == orm.BarDailyModel.instrument_id,
            ).filter(
                orm.BarDailyModel.market == market,
                orm.BarDailyModel.trade_date > start,
                orm.BarDailyModel.trade_date <= trade_date,
                orm.BarDailyModel.symbol.in_(ts_codes),
            ).order_by(orm.BarDailyModel.symbol, orm.BarDailyModel.trade_date).all()
        finally:
            session.close()
    except Exception as e:
        logger.warning("chan.load_ohlcv_failed: %s", e)
        return pd.DataFrame()

    df = pd.DataFrame(
        [tuple(r) for r in rows],
        columns=["ts_code", "security_id", "dt", "open", "high", "low",
                 "close", "volume", "amount"],
    )
    if df.empty:
        return df
    for col in ("open", "high", "low", "close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["dt"] = pd.to_datetime(df["dt"])
    return df.dropna(subset=["open", "high", "low", "close"])


def _score(snap: chan_engine.ChanSnapshot) -> tuple[float, str]:
    """返回 (score, reason)。"""
    signal = _SIGNAL_BASE.get(snap.bs_point or "", 0.0)
    if snap.bs_point == "b2" and snap.macd_divergence:
        signal = 1.0

    div = min(1.0, _DIV_SCORE.get(snap.divergence, 0.3) + (0.2 if snap.macd_divergence else 0.0))
    zs_quality = min(1.0, (snap.prev_zs or {}).get("bi_count", 3) / 5.0)
    sync = 1.0 if snap.vol_price_sync else 0.0

    score = round(0.40 * signal + 0.30 * div + 0.20 * zs_quality + 0.10 * sync, 4)

    zs = snap.prev_zs or {}
    parts = [f"缠论{_BS_LABEL.get(snap.bs_point or '', snap.bs_point or '')}"]
    if zs:
        parts.append(f"中枢[{zs.get('zd', 0):.2f},{zs.get('zg', 0):.2f}]({zs.get('bi_count', 0)}笔)")
    if snap.macd_divergence:
        parts.append("MACD底背驰确认")
    if snap.divergence != "weak":
        parts.append(f"量价背驰{'强' if snap.divergence == 'strong' else '中'}")
    if snap.vol_price_sync:
        parts.append("量价同步")
    return score, ", ".join(parts)


def select_candidates(trade_date: date, max_candidates: int = 20,
                      weekly_filter: bool = False,
                      market_gate: bool = False,
                      intraday_confirm: bool = False,
                      market: str = "CN") -> list[dict]:
    """入口：预过滤 → 逐股结构分析 → 二买/三买评分排序。

    三门默认关闭；CHAN(v1) 走默认路径行为不变，CHAN_V7 三门全开。
    """
    if market_gate and not market_regime_permits_entry(trade_date, market):
        logger.info("chan.market_halt trade_date=%s", trade_date)
        return []

    codes = _load_universe(trade_date, market)
    if not codes:
        logger.warning("chan.empty_universe trade_date=%s", trade_date)
        return []

    bars = _load_ohlcv(trade_date, codes, market)
    if bars.empty:
        return []
    sec_id_map = bars.groupby("ts_code")["security_id"].first().to_dict()

    picks = []
    analyzed = 0
    for ts_code, g in bars.groupby("ts_code"):
        snap = chan_engine.analyze(ts_code, g.reset_index(drop=True))
        analyzed += 1
        if snap.bs_point not in ("b2", "b3"):
            continue
        if weekly_filter and not weekly_trend_permits_long(g.reset_index(drop=True)):
            continue
        score, reason = _score(snap)
        picks.append({
            "security_id": str(sec_id_map[ts_code]),
            "ts_code": ts_code,
            "score": score,
            "reason": reason,
            # 确定性二级排序键：中枢延伸笔数（结构强度连续量，不入 _score 口径）。
            # 评分离散档饱和后大量候选同分，仅靠 score 排序会退化到 ts_code 字母序，
            # 让同分优质候选被任意截断出局；用结构强度决胜避免这种非确定性。
            "_tiebreak": float((snap.prev_zs or {}).get("bi_count", 0)),
        })

    picks.sort(key=lambda p: (p["score"], p["_tiebreak"]), reverse=True)

    if intraday_confirm and picks:
        confirmed: list[dict] = []
        for p in picks[: max_candidates * 4]:
            if len(confirmed) >= max_candidates:
                break
            if intraday_confirms_buy(p["ts_code"], trade_date, market):
                confirmed.append(p)
        picks = confirmed

    logger.info("chan.select_done universe=%s analyzed=%s signals=%s kept=%s",
                len(codes), analyzed, len(picks), min(len(picks), max_candidates))
    return picks[:max_candidates]
