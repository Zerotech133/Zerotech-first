"""缠论选股策略 v2（CHAN_V2）— 规则书驱动的三类买点候选生成。

自 V1 modules.candidate_engine.strategies.chan.strategy_v2 移植（语义原样）。
与 v1（strategy.py，几何位置法）并行运行做 A/B 对照：
  v1 = 对照组（CHAN），v2 = 实验组（CHAN_V2）。

不移植 V1 的 CHAN_V3~V6 入口（weekly/market_gate/intraday 变体，A/B 实验用）；
intraday（分钟确认）路径依赖 V1 分钟库，V2 无此数据，整体删去。

评分（规则书 §5）:
  score = 0.40×信号基数 + 0.30×背驰质量 + 0.20×结构质量 + 0.10×量价同步
"""
from __future__ import annotations

import logging
from datetime import date

from zhice_os_v2.domain.strategy.strategies.chan import engine as chan_engine
from zhice_os_v2.domain.strategy.strategies.chan import rules_v2
from zhice_os_v2.domain.strategy.strategies.chan.strategy import (
    _load_ohlcv,
    _load_universe,
)

logger = logging.getLogger(__name__)

_CONF_SCORE = {"HIGH": 1.0, "MID": 0.6, "LOW": 0.3}
_POINT_LABEL = {"b1": "一买", "b2": "二买", "b3": "三买"}


def _signal_base(sig: dict) -> float:
    """信号基数：b3新鲜=1.0 | b2b3重合=1.0 | b2=0.85 | b1=0.70；第2中枢三买×0.6。"""
    point = sig["point"]
    if point == "b3":
        return 1.0 * sig.get("penalty", 1.0)
    if point == "b2":
        return 1.0 if sig.get("b2b3_overlap") else 0.85
    return 0.70


def _structure_score(sig: dict) -> float:
    """结构质量：中枢笔数5笔=1.0封顶；三买再按距ZG远近折算（越近越好）。"""
    bis_score = min(1.0, sig.get("pivot_bis", 3) / 5.0)
    if sig["point"] == "b3":
        dist_score = 1.0 - sig.get("dist_to_zg", 0.0) / rules_v2.B3_MAX_DIST
        return round(0.5 * bis_score + 0.5 * max(0.0, dist_score), 4)
    return bis_score


def _reason(sig: dict, snap: rules_v2.ChanV2Signal) -> str:
    """大白话信号说明（可追溯规则书条款）。"""
    pivot = sig.get("pivot") or {}
    zone = f"[{pivot.get('zd', 0):.2f},{pivot.get('zg', 0):.2f}]"
    parts = [f"缠论{_POINT_LABEL[sig['point']]}(规则版)"]
    if sig["point"] == "b3":
        parts.append(
            f"中枢{zone}({sig['pivot_bis']}笔)突破后首次回踩不破上沿，"
            f"回踩距上沿{sig['dist_to_zg']:.1%}"
        )
        if sig.get("is_second_pivot"):
            parts.append("第2个上移中枢(降权)")
        parts.append("离开段力度足" if sig["conf"] == "HIGH" else "离开段力度一般")
    elif sig["point"] == "b2":
        parts.append(f"末中枢{zone}下方背驰新低后首次回抽企稳(高于前低)")
        if sig.get("b2b3_overlap"):
            parts.append("回抽守住中枢上沿，与三买重合(最强)")
    else:
        parts.append(
            f"两中枢下跌趋势背驰，MACD绿柱面积萎缩至前段{sig.get('area_ratio', 0):.0%}"
        )
        if sig["conf"] == "HIGH":
            parts.append("黄白线不创新低双确认")
    if snap.vol_price_sync:
        parts.append("量价同步")
    return ", ".join(parts)


def select_candidates(trade_date: date, max_candidates: int = 20,
                      weekly_filter: bool = False,
                      market_gate: bool = False,
                      intraday_confirm: bool = False,
                      market: str = "CN") -> list[dict]:
    """入口：与 v1 同宇宙同数据窗口 → 逐股 v2 规则评估 → 评分排序。

    weekly_filter=True：个股周线过滤（V1 已证明基本无效，保留参数作对照）。
    market_gate=True：大盘级别方向门——全市场周线转弱时整体停止建仓（空仓）。
    intraday_confirm=True：30min 小级别确认（v5/v6）——只对头部 4×N 候选做，
    凑够 max_candidates 即止（V1 限成本语义；缺数据放行不误拦）。
    """
    if market_gate:
        from zhice_os_v2.domain.strategy.strategies.chan.market_regime import (
            market_regime_permits_entry,
        )
        if not market_regime_permits_entry(trade_date, market):
            logger.info("chan_v2.market_halt trade_date=%s", trade_date)
            return []

    codes = _load_universe(trade_date, market)
    if not codes:
        logger.warning("chan_v2.empty_universe trade_date=%s", trade_date)
        return []

    bars = _load_ohlcv(trade_date, codes, market)
    if bars.empty:
        return []
    sec_id_map = bars.groupby("ts_code")["security_id"].first().to_dict()

    picks = []
    analyzed = 0
    weekly_blocked = 0
    by_point = {"b1": 0, "b2": 0, "b3": 0}
    for ts_code, g in bars.groupby("ts_code"):
        df = g.reset_index(drop=True)
        if len(df) < chan_engine.MIN_BARS_REQUIRED:
            continue
        try:
            bi_list, _ = chan_engine.extract_bi_list(ts_code, df)
        except Exception as e:
            logger.debug("chan_v2.bi_extract_failed ts_code=%s error=%s", ts_code, e)
            continue
        analyzed += 1
        snap = rules_v2.evaluate(ts_code, bi_list, df)
        sig = snap.signal
        if sig is None:
            continue
        # 多级别联立：买点须过周线大级别方向门（大级别定方向）
        if weekly_filter and not chan_engine.weekly_trend_permits_long(df):
            weekly_blocked += 1
            continue
        by_point[sig["point"]] += 1

        base = _signal_base(sig)
        div = _CONF_SCORE.get(sig["conf"], 0.3)
        structure = _structure_score(sig)
        sync = 1.0 if snap.vol_price_sync else 0.0
        score = round(0.40 * base + 0.30 * div + 0.20 * structure + 0.10 * sync, 4)

        picks.append({
            "security_id": str(sec_id_map[ts_code]),
            "ts_code": ts_code,
            "score": score,
            "reason": _reason(sig, snap),
            # 确定性二级排序键：中枢笔数（结构强度连续量，不入 score 口径）。
            # 与 v1(strategy.py) 对称补键，保持 CHAN vs CHAN_V2 的 A/B 排序公平：
            # 评分档饱和后同分候选不再退化到 ts_code 字母序被任意截断。
            "_tiebreak": float(sig.get("pivot_bis", 0)),
        })

    picks.sort(key=lambda p: (p["score"], p["_tiebreak"]), reverse=True)
    # 30min 小级别确认（v5/v6）：只对头部 4×N 候选做，凑够即止（V1 限成本）
    intraday_blocked = 0
    if intraday_confirm and picks:
        from zhice_os_v2.domain.strategy.strategies.chan.intraday import (
            intraday_confirms_buy,
        )
        confirmed: list[dict] = []
        for p in picks[: max_candidates * 4]:
            if len(confirmed) >= max_candidates:
                break
            if intraday_confirms_buy(p["ts_code"], trade_date, market):
                confirmed.append(p)
            else:
                intraday_blocked += 1
        picks = confirmed
    logger.info("chan_v2.select_done universe=%s analyzed=%s signals=%s by_point=%s "
                "weekly_blocked=%s intraday_blocked=%s kept=%s",
                len(codes), analyzed, len(picks), by_point, weekly_blocked,
                intraday_blocked, min(len(picks), max_candidates))
    return picks[:max_candidates]
