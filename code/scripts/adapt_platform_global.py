# -*- coding: utf-8 -*-
"""平台 HK/US/Crypto 6 策略适配器 → 日报/选股/统一池。

规则按策略描述做简化实现，全部标“适配器”，不冒充实盘：
- TREND：收盘>MA20、MA5>MA10>MA20、MA20向上、20日动量 5%~50%；
- VIBE_RSI：RSI14<45（HK/US）/ <30（Crypto）；
- VIBE_MA：MA5>MA20。
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from zhice_os_v2.application.services.strategy_registry_service import StrategyRegistryService
from zhice_os_v2.config.settings import get_settings

STRATS = {
    "HK": [("v1-hk-strat-7", "trend"), ("v1-hk-strat-8", "rsi")],
    "US": [("v1-us-strat-6", "trend"), ("v1-us-strat-7", "ma")],
    "CRYPTO": [("v1-crypto-strat-9", "trend"), ("v1-crypto-strat-11", "rsi")],
}


def _engine():
    return sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"), connect_args={"options": "-c idle_in_transaction_session_timeout=0"})


def _market_picks(c, market: str, kind: str) -> tuple[object, list[dict]]:
    maxd = c.execute(sa.text("SELECT max(trade_date) FROM v2_bar_daily WHERE market=:m"),
                     {"m": market}).scalar()
    if not maxd:
        return None, []
    df = pd.read_sql(sa.text("""
        SELECT symbol, trade_date, close, volume FROM v2_bar_daily
        WHERE market=:m AND trade_date >= :start ORDER BY symbol, trade_date
    """), c, params={"m": market, "start": maxd - timedelta(days=90)})
    if df.empty:
        return maxd, []
    df["close"] = df["close"].astype(float)
    picks = []
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("trade_date")
        if len(g) < 25:
            continue
        close = g["close"]
        ma5, ma10, ma20 = close.rolling(5).mean().iloc[-1], close.rolling(10).mean().iloc[-1], close.rolling(20).mean().iloc[-1]
        ma20_prev = close.rolling(20).mean().iloc[-2]
        if any(pd.isna(x) for x in (ma5, ma10, ma20, ma20_prev)):
            continue
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
        loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
        if pd.isna(gain) or pd.isna(loss):
            continue
        rsi = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
        mom = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) > 21 else 0
        if kind == "trend":
            if not (close.iloc[-1] > ma20 and ma5 > ma10 > ma20 and ma20 > ma20_prev):
                continue
            if not (5 <= mom <= 50):
                continue
            score = round(float(mom), 1)
            sig = "趋势动量"
        elif kind == "rsi":
            threshold = 30 if market == "CRYPTO" else 45
            if rsi >= threshold:
                continue
            score = round(float(100 - rsi), 1)
            sig = "RSI反转"
        else:  # ma
            if not (ma5 > ma20):
                continue
            score = round(float((ma5 / ma20 - 1) * 100), 2)
            sig = "均线交叉"
        picks.append(dict(code=sym, name=sym, signal_type=sig, score=score,
                          reason="%s适配器：%s，数据日 %s" % (kind, sig, maxd)))
    picks.sort(key=lambda x: -x["score"])
    return maxd, picks[:20]


def _spec_picks(c, sid: str, market: str) -> tuple[object, list[dict]]:
    """按 v2_strategy_version.spec 的 rules/factors 在日线上求 enter 信号。"""
    import json as _json
    maxd = c.execute(sa.text("SELECT max(trade_date) FROM v2_bar_daily WHERE market=:m"),
                     {"m": market}).scalar()
    row = c.execute(sa.text(
        "SELECT spec FROM v2_strategy_version WHERE strategy_id=:s AND status='ACTIVE' "
        "ORDER BY created_at DESC LIMIT 1"), {"s": sid}).scalar()
    if not row or not maxd:
        return maxd, []
    spec = row if isinstance(row, dict) else _json.loads(row)
    codes = (spec.get("universe") or {}).get("codes") or []
    if not codes:
        return maxd, []
    factors = {f["name"]: f.get("params", {}) for f in spec.get("factors", [])}
    enter = next((r for r in spec.get("rules", []) if r.get("action") == "enter"), {})
    df = pd.read_sql(sa.text("""
        SELECT symbol, trade_date, close FROM v2_bar_daily
        WHERE market=:m AND symbol = ANY(:codes) AND trade_date >= :start
        ORDER BY symbol, trade_date
    """), c, params={"m": market, "codes": codes,
                     "start": maxd - timedelta(days=60)})
    picks = []
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("trade_date")
        close = g["close"].astype(float)
        if len(close) < 25:
            continue
        if "rsi" in factors:
            win = int(factors["rsi"].get("window", 14))
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(win).mean().iloc[-1]
            loss = (-delta.clip(upper=0)).rolling(win).mean().iloc[-1]
            if pd.isna(gain) or pd.isna(loss):
                continue
            val = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
            op = enter.get("op", "<"); threshold = float(enter.get("value", 30))
            ok = (val < threshold) if op == "<" else (val > threshold)
            score = float(round(100 - val, 1)); sig = "RSI"
        elif "ma_cross" in factors:
            fast = int(factors["ma_cross"].get("fast", 5)); slow = int(factors["ma_cross"].get("slow", 20))
            val = close.rolling(fast).mean().iloc[-1] - close.rolling(slow).mean().iloc[-1]
            op = enter.get("op", ">"); threshold = float(enter.get("value", 0))
            ok = (val > threshold) if op == ">" else (val < threshold)
            score = float(round(float(val), 2)); sig = "MA_CROSS"
        else:
            continue
        if ok:
            picks.append(dict(code=sym, name=sym, signal_type=sig, score=score,
                              reason="spec规则(%s)%s，数据日 %s" % (sig, op + str(threshold), maxd)))
    picks.sort(key=lambda x: -x["score"])
    return maxd, picks[:20]


def main() -> int:
    e = _engine()
    with e.begin() as c:
        total = 0
        for market, items in STRATS.items():
            for sid, kind in items:
                if kind == "trend":
                    maxd = c.execute(sa.text("SELECT max(trade_date) FROM v2_bar_daily WHERE market=:m"), {"m": market}).scalar()
                    try:
                        real = StrategyRegistryService().run("TREND", maxd, max_candidates=20, market=market)
                    except Exception as exc:
                        print(sid, "trend_registry_err", str(exc)[:120], flush=True)
                        real = []
                    picks = [dict(code=x["ts_code"], name=x["ts_code"], signal_type="趋势动量",
                                  score=round(float(x.get("score") or 0) * 100, 1),
                                  reason=x.get("reason") or "TREND 真实注册表") for x in real]
                    d = maxd
                    if not picks:
                        d, picks = _market_picks(c, market, kind)
                elif kind in ("rsi", "ma"):
                    d, picks = _spec_picks(c, sid, market)
                    if not picks:
                        d, picks = _market_picks(c, market, kind)
                else:
                    d, picks = _market_picks(c, market, kind)
                if not d:
                    print(sid, "no data")
                    continue
                tname = {"v1-hk-strat-7": "港股趋势动量", "v1-hk-strat-8": "港股vibeRSI",
                         "v1-us-strat-6": "美股趋势动量", "v1-us-strat-7": "美股vibe均线",
                         "v1-crypto-strat-9": "加密趋势动量", "v1-crypto-strat-11": "加密vibe均值回归"}[sid]
                rid = c.execute(sa.text("""
                    INSERT INTO v2_selection_report
                      (report_date, strategy_id, market, analyst, market_view, yesterday_review, lessons, status)
                    VALUES (:d,:s,:market,'平台适配器',:mv,'适配器首期，无上期复盘','', 'submitted')
                    ON CONFLICT ON CONSTRAINT uq_v2_selection_report DO UPDATE SET
                      analyst=EXCLUDED.analyst, market_view=EXCLUDED.market_view,
                      status='submitted', submitted_at=now()
                    RETURNING id
                """), {"d": d, "s": sid, "market": market, "mv": "%s适配器（%s）" % (tname, market)}).scalar()
                c.execute(sa.text("DELETE FROM v2_selection_pick WHERE report_id=:r"), {"r": rid})
                if picks:
                    t = sa.table("v2_selection_pick", *[sa.column(x) for x in (
                        "report_id", "report_date", "strategy_id", "code", "name",
                        "signal_type", "score", "reason")])
                    c.execute(pg_insert(t).values([dict(
                        report_id=rid, report_date=d, strategy_id=sid, code=x["code"],
                        name=x["name"], signal_type=x["signal_type"], score=x["score"],
                        reason=x["reason"]) for x in picks]))
                total += len(picks)
                print(sid, len(picks))
        pool_date = c.execute(sa.text("SELECT max(report_date) FROM v2_selection_report")).scalar()
        c.execute(sa.text("DELETE FROM v2_selection_pool WHERE pool_date=:d"), {"d": pool_date})
        pool = c.execute(sa.text("""
            SELECT code, max(name) name, string_agg(DISTINCT strategy_id, ',') src,
                   string_agg(DISTINCT signal_type, ',') sigs, max(score) mx
            FROM v2_selection_pick p
            WHERE (p.strategy_id,p.report_date) IN (
              SELECT strategy_id,max(report_date) FROM v2_selection_pick GROUP BY strategy_id)
            GROUP BY code
        """)).mappings().all()
        if pool:
            t = sa.table("v2_selection_pool", *[sa.column(x) for x in (
                "pool_date", "code", "name", "source_strategies", "signal_types", "max_score", "status")])
            c.execute(pg_insert(t).values([dict(
                pool_date=pool_date, code=x["code"], name=x["name"] or "",
                source_strategies=x["src"] or "", signal_types=x["sigs"] or "",
                max_score=x["mx"], status="published") for x in pool]))
        print("pool_date=%s picks=%d pool=%d" % (pool_date, total, len(pool)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
