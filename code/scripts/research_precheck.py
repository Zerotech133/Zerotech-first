# -*- coding: utf-8 -*-
"""ACAD1 机器事实预检：评分饱和/缠论几何/量能异常/数据滞后/极端涨跌。
只读，不修改池、卡、状态；输出红旗供 ACAD1 人工复核。
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

CENTER = re.compile(r"中枢\s*\[([0-9.+-]+)\s*,\s*([0-9.+-]+)\]")


def main() -> int:
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.connect() as c:
        day = c.execute(sa.text("SELECT max(pool_date) FROM v2_acad1_pool")).scalar()
        rows = c.execute(sa.text("""
            SELECT p.market, p.code, p.name, p.strategy_id, p.signal, p.score,
                   p.selection_reason, p.data_date
            FROM v2_acad1_pool p WHERE p.pool_date=:d ORDER BY p.market, p.code
        """), {"d": day}).mappings().all()
        picks = c.execute(sa.text("""
            SELECT code, strategy_id, signal_type, score, reason
            FROM v2_selection_pick p
            WHERE (p.strategy_id,p.report_date) IN (
              SELECT strategy_id,max(report_date) FROM v2_selection_pick GROUP BY strategy_id)
        """)).mappings().all()
        bars = {}
        for r in rows:
            b = c.execute(sa.text("""
                SELECT trade_date, close, volume FROM v2_bar_daily
                WHERE market=:m AND symbol=:s
                  AND (market <> 'CRYPTO' OR trade_date < :today)
                ORDER BY trade_date DESC LIMIT 21
            """), {"m": r["market"], "s": r["code"], "today": date.today()}).mappings().all()
            seq = list(reversed(b))
            if len(seq) >= 21:
                prior = [float(x["volume"]) for x in seq[-21:-1] if x.get("volume") is not None]
                last = float(seq[-1]["volume"]) if seq[-1].get("volume") is not None else 0
                if prior and last / (sum(prior) / len(prior)) < 0.01:
                    seq = seq[:-1]
            bars[(r["market"], r["code"])] = seq

    pick_by_code = {}
    for p in picks:
        pick_by_code.setdefault(p["code"], p)

    flags = []
    # 评分饱和：同策略 >=3 只且仅 1 个不同分数
    groups = {}
    for r in rows:
        groups.setdefault(r["strategy_id"], []).append(r)
    for sid, items in groups.items():
        scores = {str(x["score"]) for x in items if x["score"] is not None}
        if len(items) >= 3 and len(scores) <= 1:
            flags.append({"level": "P0", "type": "score_saturation", "strategy_id": sid,
                          "detail": "%d 只评分全为 %s" % (len(items), next(iter(scores), "None"))})

    for r in rows:
        key = (r["market"], r["code"])
        seq = bars.get(key) or []
        close = float(seq[-1]["close"]) if seq else None
        pick = pick_by_code.get(r["code"], {})
        reason = (pick.get("reason") or "") + " " + (r["selection_reason"] or "")
        m = CENTER.search(reason)
        if m and close is not None and "买" in (r["signal"] or ""):
            lo, hi = float(m.group(1)), float(m.group(2))
            if close <= hi:
                level = "P0" if close < lo else "P1"
                flags.append({"level": level, "type": "chan_geometry", "market": r["market"],
                              "code": r["code"], "detail": "收盘 %.2f 未站上中枢上沿 %.2f（下沿 %.2f）" % (close, hi, lo)})
        if len(seq) >= 21:
            vols = [float(x["volume"]) for x in seq[:-1] if x["volume"] is not None]
            avg = sum(vols) / len(vols) if vols else 0
            lastv = float(seq[-1]["volume"]) if seq[-1]["volume"] is not None else 0
            ratio = lastv / avg if avg else None
            if ratio is not None and (ratio < 0.01 or ratio > 100):
                flags.append({"level": "P1", "type": "volume_anomaly", "market": r["market"],
                              "code": r["code"], "detail": "量比 %.4f（最新量 %.2f / 20日均 %.2f）" % (ratio, lastv, avg)})
        if seq and r["data_date"]:
            lag = (r["data_date"] - seq[-1]["trade_date"]).days
            if lag > 2:
                flags.append({"level": "P2", "type": "data_lag", "market": r["market"],
                              "code": r["code"], "detail": "池日 %s，行情 %s，滞后 %d 天" % (r["data_date"], seq[-1]["trade_date"], lag)})

    out = {"pool_date": str(day), "rows": len(rows), "flags": flags,
           "counts": {}}
    for f in flags:
        out["counts"][f["type"]] = out["counts"].get(f["type"], 0) + 1
    p = Path(r"C:\Temp\acad1_precheck.json")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("saved", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
