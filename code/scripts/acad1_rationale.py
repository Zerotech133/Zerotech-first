# -*- coding: utf-8 -*-
"""ACAD1 入池逻辑（只读生成；--write 才更新 selection_reason）。

在“已有买卖点”的候选中，说明为什么进入 ACAD1：
信号/共振/证据/风险/时效。不是投资建议，不自动通过/否决。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

OUT = Path(r"C:\Temp\acad1_rationale.json")
EVIDENCE = Path(r"C:\Temp\acad1_evidence_bundle.json")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true")
    args = p.parse_args()

    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    evidence = {}
    if EVIDENCE.exists():
        try:
            for it in json.loads(EVIDENCE.read_text(encoding="utf-8")).get("items") or []:
                evidence[(it.get("market"), it.get("code"))] = it
                if it.get("pool_code"):
                    evidence[(it.get("market"), it.get("pool_code"))] = it
        except Exception:  # noqa: BLE001
            pass

    rows_out = []
    with e.begin() as c:
        pool_date = c.execute(sa.text("SELECT max(pool_date) FROM v2_acad1_pool")).scalar()
        pool = c.execute(sa.text("""
            SELECT market, code, name, strategy_id, signal, score, pool_date, data_date
            FROM v2_acad1_pool WHERE pool_date=:d ORDER BY market, code
        """), {"d": pool_date}).mappings().all()
        for r in pool:
            m, code = r["market"], r["code"]
            card = c.execute(sa.text("""
                SELECT direction, rating, thesis, risks, invalidation, buy_zone_low, buy_zone_high,
                       target_price, stop_price, executable, data_date
                FROM v2_stock_research WHERE code=:c OR code=:m ORDER BY pool_date DESC LIMIT 1
            """), {"c": code, "m": code}).mappings().first()
            sel = c.execute(sa.text("""
                SELECT source_strategies, signal_types, max_score FROM v2_selection_pool
                WHERE market=:m AND code=:c ORDER BY pool_date DESC LIMIT 1
            """), {"m": m, "c": code}).mappings().first()
            ev = evidence.get((m, code)) or {}
            level = ev.get("evidence_level") or "unknown"
            sources = str((sel or {}).get("source_strategies") or r["strategy_id"] or "")
            resonance = len([x for x in sources.replace("，", ",").split(",") if x.strip()])
            # 基础层：先判断买卖点是否存在，不被证据/增强否定。
            sig = str(r["signal"] or "")
            if "回避" in sig:
                base = "回避（无买点）"
            elif "买" in sig:
                base = "买点"
            elif "卖" in sig:
                base = "卖点"
            elif any(k in sig for k in ("均值回归", "趋势动量", "RSI", "CHAN", "缠论")):
                base = "买点"
            else:
                base = "观察"
            base_text = (f"基础信号：{base}；来源[{sources or r['strategy_id']}]；"
                         f"信号[{sig}]；评分{float(r['score'] or 0):.1f}")
            enh = []
            if resonance >= 2:
                enh.append(f"{resonance} 条策略共振")
            if level == "full":
                enh.append("证据完整")
            elif level in ("partial", "none"):
                enh.append(f"证据{level}，只影响复核优先级")
            if card:
                if card["direction"] in ("回避", "avoid", "AVOID"):
                    enh.append("卡方向=回避，提示人工核实（不改变基础信号）")
                if card.get("buy_zone_low") is not None and card.get("buy_zone_high") is not None:
                    enh.append(f"入场区间 {card['buy_zone_low']}~{card['buy_zone_high']}")
                if card.get("stop_price") is not None:
                    enh.append(f"止损 {card['stop_price']}")
                if card.get("invalidation"):
                    enh.append(f"失效条件：{card['invalidation']}")
            if r["data_date"] and pool_date and r["data_date"] < pool_date:
                enh.append("数据日滞后，降级")
            text = (base_text + "。增强判断（只影响选择/优先级，不否定基础信号）："
                    + ("；".join(enh) if enh else "无额外增强")
                    + "。研究池非交易，不自动通过/否决。")
            rows_out.append({"market": m, "code": code, "name": r["name"], "pool_date": str(pool_date),
                             "base_signal": base, "text": text, "evidence": level, "resonance": resonance,
                             "direction": (card or {}).get("direction")})
            if args.write:
                c.execute(sa.text("""
                    UPDATE v2_acad1_pool SET selection_reason=:t
                    WHERE pool_date=:d AND market=:m AND code=:c
                """), {"t": text, "d": pool_date, "m": m, "c": code})

    out = {"schema": "zhice.acad1_rationale/v1", "pool_date": str(pool_date),
           "read_only": not args.write, "count": len(rows_out), "items": rows_out}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pool_date": str(pool_date), "count": len(rows_out), "written": args.write}, ensure_ascii=False))
    for x in rows_out[:3]:
        print(x["market"], x["code"], x["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
