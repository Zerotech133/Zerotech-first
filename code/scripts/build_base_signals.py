# -*- coding: utf-8 -*-
"""基础信号表：买点/卖点/回避，先于增强判断。

只把“已有策略输出/研究卡”的事实落表；不生成新买卖点，不自动决策。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.application.services.security_names import resolve_name

OUT = Path(r"C:\Temp\base_signals.json")

DDL = """
CREATE TABLE IF NOT EXISTS v2_base_signal (
  id BIGSERIAL PRIMARY KEY,
  market VARCHAR(16) NOT NULL,
  code VARCHAR(64) NOT NULL,
  name VARCHAR(128),
  trade_date DATE NOT NULL,
  data_date DATE,
  strategy_id VARCHAR(64),
  signal_type VARCHAR(128),
  base_state VARCHAR(16) NOT NULL,
  trigger_rule TEXT,
  trigger_price NUMERIC(20,6),
  entry_low NUMERIC(20,6),
  entry_high NUMERIC(20,6),
  stop_price NUMERIC(20,6),
  target_price NUMERIC(20,6),
  exit_rule TEXT,
  source VARCHAR(32),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (market, code, strategy_id, trade_date)
)
"""


def state_of(signal: str) -> str:
    s = str(signal or "")
    if "回避" in s:
        return "AVOID"
    if "卖" in s:
        return "SELL"
    if "买" in s:
        return "BUY"
    if any(k in s for k in ("均值回归", "趋势动量", "RSI", "CHAN", "缠论")):
        return "BUY"
    return "NEUTRAL"


def trigger_of(signal: str) -> str:
    s = str(signal or "")
    if "均值回归" in s:
        return "RSI14<30 均值回归买点"
    if "CHAN" in s or "缠论" in s:
        return "缠论买点（含结构/周线确认，按策略信号）"
    if "趋势动量" in s:
        return "趋势动量买点（MA/动量触发）"
    if "RSI" in s:
        return "RSI 买点"
    if "回避" in s:
        return "环境闸门回避：不产生买点"
    return f"策略信号：{s or '未标注'}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true")
    args = p.parse_args()
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    inst_names = {}
    with e.connect() as ic:
        for m, sym, nm in ic.execute(sa.text("SELECT market, symbol, name FROM v2_instrument WHERE name IS NOT NULL AND name <> ''")).all():
            inst_names[(str(m), str(sym))] = str(nm)
    with e.begin() as c:
        c.execute(sa.text(DDL))
        pool = c.execute(sa.text("""
            SELECT market, code, name, pool_date, source_strategies, signal_types, max_score
            FROM v2_selection_pool
            WHERE pool_date=(SELECT max(pool_date) FROM v2_selection_pool)
            ORDER BY market, code
        """)).mappings().all()
        acad = c.execute(sa.text("""
            SELECT market, code, name, pool_date, data_date, strategy_id, signal
            FROM v2_acad1_pool WHERE pool_date=(SELECT max(pool_date) FROM v2_acad1_pool)
        """)).mappings().all()
        acad_map = {(r["market"], r["code"]): r for r in acad}
        rows = []
        for r in pool:
            sig = str(r["signal_types"] or "")
            sid = str(r["source_strategies"] or "").split(",")[0].strip()
            card = c.execute(sa.text("""
                SELECT direction, buy_zone_low, buy_zone_high, stop_price, target_price, invalidation, data_date
                FROM v2_stock_research WHERE code=:c ORDER BY pool_date DESC LIMIT 1
            """), {"c": r["code"]}).mappings().first()
            bar = c.execute(sa.text("""
                SELECT close, trade_date FROM v2_bar_daily WHERE market=:m AND symbol=:c
                ORDER BY trade_date DESC LIMIT 1
            """), {"m": r["market"], "c": r["code"]}).mappings().first()
            state = state_of(sig)
            a = acad_map.get((r["market"], r["code"]))
            rows.append({
                "market": r["market"], "code": r["code"], "name": resolve_name(inst_names, r["market"], r["code"]),
                "trade_date": (a or {}).get("pool_date") or r["pool_date"],
                "data_date": (a or {}).get("data_date"),
                "strategy_id": sid, "signal_type": sig, "base_state": state,
                "trigger_rule": trigger_of(sig),
                "trigger_price": (bar or {}).get("close"),
                "entry_low": (card or {}).get("buy_zone_low"),
                "entry_high": (card or {}).get("buy_zone_high"),
                "stop_price": (card or {}).get("stop_price"),
                "target_price": (card or {}).get("target_price"),
                "exit_rule": (card or {}).get("invalidation") or "卖点/失效条件触发",
                "source": "selection_pool+acad1+research",
            })
        existing = {(r["market"], r["code"]) for r in rows}
        for key, a in acad_map.items():
            if key in existing:
                continue
            state = state_of(a["signal"])
            rows.append({
                "market": a["market"], "code": a["code"], "name": resolve_name(inst_names, a["market"], a["code"]),
                "trade_date": a["pool_date"], "data_date": a["data_date"],
                "strategy_id": a["strategy_id"], "signal_type": a["signal"], "base_state": state,
                "trigger_rule": trigger_of(a["signal"]), "trigger_price": None,
                "entry_low": None, "entry_high": None, "stop_price": None, "target_price": None,
                "exit_rule": "卖点/失效条件触发", "source": "acad1_pool",
            })
        if args.write and rows:
            t = sa.table("v2_base_signal", *[sa.column(x) for x in (
                "market", "code", "name", "trade_date", "data_date", "strategy_id",
                "signal_type", "base_state", "trigger_rule", "trigger_price",
                "entry_low", "entry_high", "stop_price", "target_price", "exit_rule", "source", "updated_at")])
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
    out = {"schema": "zhice.base_signals/v1", "read_only": not args.write,
           "count": len(rows), "states": counts, "items": rows}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"count": len(rows), "states": counts, "written": args.write}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
