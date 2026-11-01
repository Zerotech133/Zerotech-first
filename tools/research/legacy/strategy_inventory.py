# -*- coding: utf-8 -*-
"""策略/方法论全景盘点（可复现）：注册表 + 台账 + paper + 脚本/文档计数 → JSON。

用法：python tools/research/strategy_inventory.py
产出：research/reports/strategy_inventory_<YYYYMMDD>.json
"""
from __future__ import annotations

import glob
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:/项目/老田的AI助手")
OUT_DIR = REPO / "research" / "reports"


def main() -> int:
    inv = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    reg = json.loads((REPO / "agent_platform/catalogs/strategy_registry.json").read_text(encoding="utf-8"))
    inv["strategy_registry"] = {
        "owner": reg["library"]["owner"], "count": len(reg["strategies"]),
        "items": [{"id": s["id"], "name": s["name"], "type": s["type"], "status": s["status"]}
                  for s in reg["strategies"]],
    }
    con = sqlite3.connect(str(REPO / "research/hypotheses.db"))
    inv["hypotheses"] = {
        "total": con.execute("select count(*) from hypotheses").fetchone()[0],
        "by_status": dict(con.execute("select status,count(*) from hypotheses group by status").fetchall()),
        "validated": [{"id": r[0], "title": r[1], "status": r[2]} for r in
                      con.execute("select id,title,status from hypotheses where status in ('backtested','in_progress') order by id")],
    }
    con.close()
    pdb = REPO / "k_stock/data/paper.db"
    if pdb.exists():
        c = sqlite3.connect(str(pdb))
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        inv["paper"] = {t: c.execute(f"select count(*) from {t}").fetchone()[0]
                        for t in ("account", "positions", "equity_marks", "orders") if t in tables}
        c.close()
    scripts = sorted(glob.glob(str(REPO / "tools/research/*.py")))
    inv["research_scripts"] = {"total": len(scripts),
                               "h_series": len([p for p in scripts if Path(p).name.lower().startswith(("h1", "h2", "h3"))])}
    inv["reports"] = {"research_reports": len(glob.glob(str(REPO / "research/reports/*.md"))),
                      "docs_plans": len(glob.glob(str(REPO / "docs/plans/*.md"))),
                      "deliverables": len(glob.glob(str(REPO / "deliverables/*.md")))}
    inv["knowledge"] = {"units_snapshot": 498, "rag_records": 466, "report_docs": 100, "learning_cards": 1}
    (OUT_DIR / f"strategy_inventory_{datetime.now():%Y%m%d}.json").write_text(
        json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"registry": inv["strategy_registry"]["count"],
                      "hypotheses": inv["hypotheses"]["by_status"],
                      "paper": inv.get("paper"), "scripts": inv["research_scripts"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
