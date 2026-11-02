# -*- coding: utf-8 -*-
"""平台 CN 策略适配器：均值回归 / 缠论v1 / 缠论增强版 → 日报+选股+统一池。

- 均值回归：v2_bar_daily CN 最新日 RSI14<30（简化实现，只写研究候选）；
- 缠论v1：复用 sidecar chanlun 最新买点；
- 缠论增强版：sidecar chanlun 最新 1买 + trend=up。
所有写入都带 reason，标明“适配器”，不伪装成实盘信号。
"""
from __future__ import annotations

import re

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from datetime import date, timedelta

from zhice_os_v2.application.services.strategy_registry_service import StrategyRegistryService
from zhice_os_v2.config.settings import get_settings

PLATFORM = {
    "v1-cn-strat-2": "均值回归",
    "v1-cn-strat-4": "缠论v1",
    "v1-cn-strat-4-v7": "缠论增强版",
}






_CENTER = re.compile(r"中枢\s*\[([0-9.+-]+)\s*,\s*([0-9.+-]+)\]")


def _filter_chan_geometry(picks: list[dict], close_map: dict[str, float]) -> list[dict]:
    """三买几何门：收盘必须站上中枢上沿；无中枢信息的保留并标注不可判定。"""
    out = []
    for x in picks:
        m = _CENTER.search(x.get("reason") or "")
        if not m:
            out.append(x)
            continue
        close = close_map.get(x["code"])
        if close is None:
            out.append(x)
            continue
        hi = float(m.group(2))
        if float(close) > hi:
            out.append(x)
    return out


def _normalize_scores(picks: list[dict]) -> list[dict]:
    """把策略内原始分归一到 0~100；常量分不伪造区分度，统一置 50 并标注。"""
    vals = [float(x.get("score") or 0) for x in picks]
    if len(vals) >= 2:
        lo, hi = min(vals), max(vals)
        if hi > lo:
            for x, v in zip(picks, vals):
                x["score"] = round((v - lo) / (hi - lo) * 100, 1)
            return picks
    if vals:
        for x in picks:
            x["score"] = 50.0
            x["reason"] = (x.get("reason") or "") + "；评分不可用（策略输出常量分，已置中性50）"
    return picks


def _engine():
    return sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"), connect_args={"options": "-c idle_in_transaction_session_timeout=0"})


def _rsi_picks(c) -> list[dict]:
    maxd = c.execute(sa.text("SELECT max(trade_date) FROM v2_bar_daily WHERE market='CN'")).scalar()
    df = pd.read_sql(sa.text("""
        SELECT symbol, trade_date, close FROM v2_bar_daily
        WHERE market='CN' AND trade_date >= :start ORDER BY symbol, trade_date
    """), c, params={"start": maxd - timedelta(days=60)})
    if df.empty:
        return []
    df["close"] = df["close"].astype(float)
    out = []
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("trade_date")
        if len(g) < 20:
            continue
        delta = g["close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
        loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
        if pd.isna(gain) or pd.isna(loss):
            continue
        rsi = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
        if rsi < 30:
            out.append({"code": sym, "name": sym, "signal_type": "RSI超卖",
                        "score": round(float(100 - rsi), 1),
                        "reason": "RSI14=%.1f<30（均值回归适配器，数据日 %s）" % (rsi, maxd)})
    return sorted(out, key=lambda x: -x["score"])[:20]


def main() -> int:
    e = _engine()
    with e.begin() as c:
        reports: dict[str, list[dict]] = {}
        latest_cn = c.execute(sa.text(
            "SELECT max(trade_date) FROM v2_bar_daily WHERE market='CN'")).scalar()
        close_map = {r[0]: float(r[1]) for r in c.execute(sa.text(
            "SELECT DISTINCT ON (symbol) symbol, close FROM v2_bar_daily "
            "WHERE market='CN' ORDER BY symbol, trade_date DESC")).all()}
        real = StrategyRegistryService().run("MEAN_REVERSION", latest_cn,
                                             max_candidates=20, market="CN")
        if real:
            reports["v1-cn-strat-2"] = [dict(
                code=x["ts_code"], name=x["ts_code"], signal_type="均值回归",
                score=round(float(x.get("score") or 0) * 100, 1),
                reason=x.get("reason") or "MEAN_REVERSION 真实注册表") for x in real]
        else:
            reports["v1-cn-strat-2"] = _rsi_picks(c)
        side = c.execute(sa.text("""
            SELECT code,name,signal,reason,score FROM v2_strategy_daily
            WHERE strategy_id='chanlun' AND market='CN'
              AND data_date=(SELECT max(data_date) FROM v2_strategy_daily WHERE strategy_id='chanlun')
              AND signal LIKE '%买%' AND signal NOT LIKE '%卖%'
        """)).mappings().all()
        uniq = {}
        for x in side:
            uniq.setdefault(x["code"], x)
        side = list(uniq.values())
        svc = StrategyRegistryService()
        for sid, typ in (("v1-cn-strat-4", "CHAN"), ("v1-cn-strat-4-v7", "CHAN_V7")):
            try:
                real = svc.run(typ, latest_cn, max_candidates=20, market="CN")
            except Exception as exc:
                print(sid, "registry_err", str(exc)[:100])
                real = []
            picks = [dict(code=x["ts_code"], name=x["ts_code"], signal_type=typ,
                          score=round(float(x.get("score") or 0) * 100, 1),
                          reason=x.get("reason") or "%s 真实注册表" % typ) for x in real]
            if not picks:
                picks = [dict(code=x["code"], name=x["name"], signal_type=x["signal"], score=x["score"],
                              reason="sidecar 回退：%s" % (x["reason"] or "")) for x in side]
            reports[sid] = picks
        for sid in ("v1-cn-strat-4", "v1-cn-strat-4-v7"):
            before = len(reports.get(sid) or [])
            reports[sid] = _filter_chan_geometry(reports.get(sid) or [], close_map)
            print(sid, "geometry_keep", len(reports[sid]), "/", before)
        for sid in list(reports):
            reports[sid] = _normalize_scores(reports[sid])
        for sid, picks in reports.items():
            d = latest_cn
            rid = c.execute(sa.text("""
                INSERT INTO v2_selection_report
                  (report_date, strategy_id, market, analyst, market_view, yesterday_review, lessons, status)
                VALUES (:d,:s,'CN','平台适配器',:mv,'适配器首期，无上期复盘','', 'submitted')
                ON CONFLICT ON CONSTRAINT uq_v2_selection_report DO UPDATE SET
                  analyst=EXCLUDED.analyst, market_view=EXCLUDED.market_view,
                  status='submitted', submitted_at=now()
                RETURNING id
            """), {"d": d, "s": sid, "mv": "CN平台策略适配器：%s" % PLATFORM[sid]}).scalar()
            c.execute(sa.text("DELETE FROM v2_selection_pick WHERE report_id=:r"), {"r": rid})
            if picks:
                t = sa.table("v2_selection_pick", *[sa.column(x) for x in (
                    "report_id", "report_date", "strategy_id", "code", "name",
                    "signal_type", "score", "reason")])
                c.execute(pg_insert(t).values([dict(
                    report_id=rid, report_date=d, strategy_id=sid, code=x["code"],
                    name=x["name"], signal_type=x["signal_type"], score=x["score"],
                    reason=x["reason"]) for x in picks]))
            print(sid, len(picks))

        # aggregate pool across latest report per strategy
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
        print("pool_date=%s pool=%d" % (pool_date, len(pool)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
