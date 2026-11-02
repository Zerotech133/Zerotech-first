# -*- coding: utf-8 -*-
"""基础信号规则引擎：买入候选 + 持仓卖出。

- BUY：全市场 MA 趋势买点；
- SELL：只对 v2_position.status=OPEN 的持仓（实盘/模拟同一口径）；
- 不对未持仓股票发卖出预警；
- 不自动交易、不自动复核。
"""
from __future__ import annotations

import argparse
import json
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.application.services.security_names import resolve_name

OUT = Path(r"C:\Temp\rule_base_signals.json")


def rsi14(close: pd.Series) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    if pd.isna(gain) or pd.isna(loss):
        return float("nan")
    if loss == 0:
        return 100.0
    return float(100 - 100 / (1 + gain / loss))


def ma_state(close: pd.Series):
    if len(close) < 25:
        return None
    ma5, ma10, ma20 = close.rolling(5).mean().iloc[-1], close.rolling(10).mean().iloc[-1], close.rolling(20).mean().iloc[-1]
    ma20_prev = close.rolling(20).mean().iloc[-2]
    if any(pd.isna(x) for x in (ma5, ma10, ma20, ma20_prev)):
        return None
    return float(ma5), float(ma10), float(ma20), float(ma20_prev), float(close.iloc[-1])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true")
    args = p.parse_args()
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    rows: list[dict] = []
    dfs: dict[str, pd.DataFrame] = {}
    inst_names = {}
    with e.connect() as c:
        for m, sym, nm in c.execute(sa.text("SELECT market, symbol, name FROM v2_instrument WHERE name IS NOT NULL AND name <> ''")).all():
            inst_names[(str(m), str(sym))] = str(nm)
        for market in ("CN", "HK", "US", "CRYPTO"):
            latest = c.execute(sa.text("SELECT max(trade_date) FROM v2_bar_daily WHERE market=:m"), {"m": market}).scalar()
            if not latest:
                continue
            df = pd.read_sql(sa.text("""
                SELECT symbol, trade_date, close FROM v2_bar_daily
                WHERE market=:m AND trade_date >= :start ORDER BY symbol, trade_date
            """), c, params={"m": market, "start": latest - pd.Timedelta(days=90)})
            if df.empty:
                continue
            dfs[market] = df
            for sym, g in df.groupby("symbol"):
                g = g.sort_values("trade_date")
                st = ma_state(g["close"].astype(float))
                if not st:
                    continue
                ma5, ma10, ma20, ma20_prev, last = st
                if last > ma20 and ma5 > ma10 > ma20 and ma20 >= ma20_prev:
                    rows.append({"market": market, "code": str(sym), "name": resolve_name(inst_names, market, str(sym)),
                                 "trade_date": latest, "data_date": latest, "strategy_id": "RULE_TREND",
                                 "signal_type": "规则引擎-买入", "base_state": "BUY",
                                 "trigger_rule": "MA5>MA10>MA20，收盘>MA20，MA20向上；RSI14=%.1f" % rsi14(g["close"].astype(float)),
                                 "trigger_price": last, "entry_low": None, "entry_high": None,
                                 "stop_price": None, "target_price": None,
                                 "exit_rule": "MA5<MA10 或收盘<MA20", "source": "rule_engine"})
        positions = c.execute(sa.text("""
            SELECT market, ts_code, strategy_version_id, avg_cost, open_price, stop_loss_price, account_id, opened_at
            FROM v2_position WHERE status='OPEN'
        """)).mappings().all()
    # 持仓卖出：只对 OPEN 持仓计算；实盘/模拟同一规则；A 股强制 T+1。
    latest_bar = {m: max(df["trade_date"]) for m, df in dfs.items()}
    t1_blocked = []
    for pos in positions:
        m = pos["market"]
        df = dfs.get(m)
        if df is None:
            continue
        if m == "CN" and pos["opened_at"] is not None:
            session_date = latest_bar.get(m)
            opened_cn = pos["opened_at"].astimezone(ZoneInfo("Asia/Shanghai")).date() if pos["opened_at"].tzinfo else pos["opened_at"].date()
            if session_date is not None and opened_cn >= session_date:
                t1_blocked.append({"code": pos["ts_code"], "opened": str(opened_cn), "session": str(session_date)})
                continue
        g = df[df["symbol"] == pos["ts_code"]].sort_values("trade_date")
        if len(g) < 25:
            continue
        st = ma_state(g["close"].astype(float))
        if not st:
            continue
        ma5, ma10, ma20, ma20_prev, last = st
        stop = float(pos["stop_loss_price"]) if pos["stop_loss_price"] is not None else None
        state = rule = None
        if last < ma20 and ma5 < ma10:
            state = "SELL"
            rule = "持仓卖出：收盘<MA20 且 MA5<MA10（趋势破位）；RSI14=%.1f" % rsi14(g["close"].astype(float))
        elif stop is not None and last <= stop:
            state = "SELL"
            rule = f"持仓卖出：收盘 {last} 跌破止损价 {stop}"
        if not state:
            continue
        rows.append({"market": m, "code": str(pos["ts_code"]), "name": resolve_name(inst_names, m, str(pos["ts_code"])),
                     "trade_date": max(g["trade_date"]), "data_date": max(g["trade_date"]),
                     "strategy_id": "RULE_POSITION", "signal_type": "持仓卖出规则", "base_state": state,
                     "trigger_rule": rule + ("；T+1 已满足" if m == "CN" else ""),
                     "trigger_price": last, "entry_low": None, "entry_high": None,
                     "stop_price": stop, "target_price": None,
                     "exit_rule": "已触发卖出条件" + ("（A股T+1）" if m == "CN" else ""),
                     "source": "position_rule"})
    if args.write and rows:
        with e.begin() as c:
            # 清理旧规则行，避免未持仓股票残留 SELL 预警
            c.execute(sa.text("DELETE FROM v2_base_signal WHERE source IN ('rule_engine','position_rule')"))
            t = sa.table("v2_base_signal", *[sa.column(x) for x in (
                "market", "code", "name", "trade_date", "data_date", "strategy_id", "signal_type",
                "base_state", "trigger_rule", "trigger_price", "entry_low", "entry_high",
                "stop_price", "target_price", "exit_rule", "source", "updated_at")])
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            stmt = pg_insert(t).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["market", "code", "strategy_id", "trade_date"],
                set_={x: getattr(stmt.excluded, x) for x in (
                    "name", "data_date", "signal_type", "base_state", "trigger_rule", "trigger_price",
                    "entry_low", "entry_high", "stop_price", "target_price", "exit_rule", "source", "updated_at")}
            )
            c.execute(stmt)
    counts = {}
    for r in rows:
        counts[r["base_state"]] = counts.get(r["base_state"], 0) + 1
    out = {"schema": "zhice.rule_base_signals/v2", "read_only": not args.write,
           "count": len(rows), "states": counts, "items": rows}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"count": len(rows), "states": counts, "open_positions": len(positions),
                      "t1_blocked": t1_blocked, "written": args.write}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
