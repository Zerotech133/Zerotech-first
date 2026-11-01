# -*- coding: utf-8 -*-
"""清洗 dry-run 批次台账（DATA-05 / DATA2 刘巴 06:40 证据）。

每天对 2 个代表数据集生成 dry-run 计划（不执行），追加 research/ledger/etl_dryrun.jsonl。
dry-run 计划来自 agent_platform/tools/dw_etl_dryrun_mcp.call_tool（与 Hermes 同源逻辑）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "agent_platform" / "tools"))
LEDGER = REPO / "research" / "ledger" / "etl_dryrun.jsonl"

DATASETS = [("yt_learning_db", "staging_clean"), ("xq_xq_status", "refresh_sample")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(LEDGER))
    args = ap.parse_args(argv)
    import dw_etl_dryrun_mcp as etl  # noqa: E402
    rows = []
    for did, action in DATASETS:
        try:
            plan = etl.call_tool("etl_plan", {"dataset_id": did, "action": action})
            rows.append({"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                         "dataset_id": did, "action": action, "dry_run_only": True,
                         "executed": plan.get("executed"), "approval_required": plan["approval"]["required"],
                         "planned_writes": plan.get("planned_writes")})
        except Exception as e:  # noqa: BLE001
            rows.append({"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                         "dataset_id": did, "action": action, "dry_run_only": True,
                         "executed": False, "error": str(e)[:160]})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"out": str(out), "rows": len(rows), "executed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
