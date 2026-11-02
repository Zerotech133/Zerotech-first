# -*- coding: utf-8 -*-
"""数据底座清单：扫描智策 DB + 关键外部文件，输出可追溯的数据资产盘点。

只读；不写 DB，不改数据仓。
"""
from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

OUT = Path(r"C:\Temp\data_inventory.json")
DATE_COLS = ("pool_date", "report_date", "trade_date", "data_date", "supervision_date", "created_at")
TABLES = [
    ("v2_strategy", "策略主表"),
    ("v2_strategy_sidecar", "sidecar 策略"),
    ("v2_strategy_ownership", "策略 ownership"),
    ("v2_strategy_change", "策略变更审批"),
    ("v2_strategy_version", "策略版本"),
    ("v2_selection_report", "策略日报"),
    ("v2_selection_pick", "策略选股"),
    ("v2_selection_pool", "统一池"),
    ("v2_candidate_pool", "候选池"),
    ("v2_candidate_asset", "候选明细"),
    ("v2_acad1_pool", "ACAD1 池"),
    ("v2_acad1_review", "ACAD1 评审"),
    ("v2_stock_research", "投研卡"),
    ("v2_selection_feedback", "下游反馈"),
    ("v2_strategy_supervision", "策略督导"),
    ("v2_agent_handoff", "智能体交接"),
    ("v2_bar_daily", "日线"),
]


def main() -> int:
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    items = []
    with e.connect() as c:
        existing = {
            r[0]
            for r in c.execute(sa.text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )).all()
        }
        for name, label in TABLES:
            if name not in existing:
                items.append({"table": name, "label": label, "exists": False, "rows": 0, "latest": None, "markets": {}})
                continue
            rows = c.execute(sa.text(f"SELECT count(*) FROM {name}")).scalar()
            cols = {
                r[0]
                for r in c.execute(sa.text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name=:t"
                ), {"t": name}).all()
            }
            latest = None
            for dc in DATE_COLS:
                if dc in cols:
                    try:
                        latest = c.execute(sa.text(f"SELECT max({dc}) FROM {name}")).scalar()
                        if latest is not None:
                            break
                    except Exception:  # noqa: BLE001
                        pass
            markets = {}
            if "market" in cols:
                for r in c.execute(sa.text(f"SELECT market, count(*) FROM {name} GROUP BY market")).all():
                    markets[str(r[0])] = int(r[1])
            items.append({
                "table": name,
                "label": label,
                "exists": True,
                "rows": int(rows or 0),
                "latest": str(latest) if latest is not None else None,
                "date_column": next((d for d in DATE_COLS if d in cols), None),
                "markets": markets,
            })
    external = {}
    dw = Path(r"C:\Temp\dw_enrichment.json")
    external["dw_enrichment"] = {"exists": dw.exists(), "bytes": dw.stat().st_size if dw.exists() else 0}
    ev = Path(r"C:\Temp\acad1_evidence_bundle.json")
    if ev.exists():
        try:
            d = json.loads(ev.read_text(encoding="utf-8"))
            external["evidence"] = {"items": len(d.get("items") or []), "generated_at": d.get("generated_at")}
        except Exception:  # noqa: BLE001
            external["evidence"] = {"items": None, "error": "unreadable"}
    out = {
        "schema": "zhice.data_inventory/v1",
        "read_only": True,
        "db": "zhice_os_v2",
        "tables": items,
        "external": external,
        "summary": {
            "tables": len([x for x in items if x["exists"]]),
            "rows": sum(x["rows"] for x in items),
            "missing": [x["table"] for x in items if not x["exists"]],
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
