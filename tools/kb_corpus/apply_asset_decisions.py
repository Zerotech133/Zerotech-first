# -*- coding: utf-8 -*-
"""执行治理审批（② manifest 结果记录 + ③ 注册/忽略/暴露决策落册）。"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:/项目/老田的AI助手")
R = REPO / "research/reports"
REG = REPO / "agent_platform/registry"
CFG = REPO / "config"
now = datetime.now().astimezone().isoformat(timespec="seconds")
cov = json.loads((R / "datawarehouse_coverage_p1_20261103.json").read_text(encoding="utf-8"))
cats = cov["categories"]
IGNORE_KEYS = (".git", "probe", "temp", "tmp", "backup", "_bak", "quarantine", "旧", "archive")

unreg = cats.get("unregistered") or []
ignore_list, register_list = [], []
for x in unreg:
    blob = (str(x.get("label", "")) + " " + str(x.get("path", ""))).lower()
    if any(k in blob for k in IGNORE_KEYS):
        ignore_list.append(x)
    else:
        register_list.append(x)
unexp = cats.get("unexposed") or []

decisions = {
    "schema": "data_asset_decisions.v1",
    "generated_at": now,
    "approval": {"approved_by": "老田", "approved_at": now,
                 "scope": "DATA4 注册/忽略清单（P1）"},
    "summary": {"register": len(register_list), "ignore": len(ignore_list),
                "expose_backlog": len(unexp)},
    "register": [{"label": x.get("label"), "domain": x.get("domain"),
                  "suggested_id": x.get("suggested_id"), "size_mb": x.get("size_mb"),
                  "latest": x.get("latest"), "status": "approved_pending_metadata",
                  "owner": "DATA4", "next_step": "补 catalog 元数据(schema/主键/date_col/manifest)后入册"} for x in register_list],
    "ignore": [{"label": x.get("label"), "domain": x.get("domain"), "path": x.get("path"),
                "reason": "probe/.git/临时/备份目录，不作为数据资产管理"} for x in ignore_list],
    "expose_backlog": [{"label": x.get("label"), "domain": x.get("domain"),
                        "suggested_id": x.get("suggested_id"), "size_mb": x.get("size_mb"),
                        "latest": x.get("latest"), "status": "pending_product_review",
                        "owner": "DATA4", "approval": "产品/门户评审"} for x in unexp],
    "note": "本决策不直接修改 catalog/warehouse_registry，避免未补元数据导致 metadata_consistency 漂移；"
            "register 项按批次补元数据后执行注册。",
}
(REG / "data_asset_decisions_p1.json").write_text(json.dumps(decisions, ensure_ascii=False, indent=1), encoding="utf-8")
(CFG / "data_inventory_ignore.json").write_text(json.dumps(
    {"schema": "data_inventory_ignore.v1", "generated_at": now,
     "entries": decisions["ignore"]}, ensure_ascii=False, indent=1), encoding="utf-8")

# 更新行动矩阵
matrix_p = R / "governance_action_matrix_20261103.json"
matrix = json.loads(matrix_p.read_text(encoding="utf-8"))
status = {
    "A1": ("blocked_no_token", "TUSHARE token 需 SOPS/审批；执行包已备"),
    "A2": ("executed_noop", "manifest_repair --apply 已执行；3 个候选均被 lifecycle(deprecated/planned) 跳过 → 0 变更；需 DATA3 处理 lifecycle"),
    "A3": ("approved_recorded", f"注册 {len(register_list)}/忽略 {len(ignore_list)} 已落册（待补元数据入册）"),
    "A4": ("backlog_created", f"暴露 {len(unexp)} 项 backlog 已生成（待产品评审）"),
    "A5": ("blocked_no_valid_candidate", "多源 29/30；唯一 +1 候选为错误合并，不采用"),
}
for a in matrix["actions"]:
    if a["id"] in status:
        a["status"], a["result"] = status[a["id"]]
matrix["updated_at"] = now
matrix_p.write_text(json.dumps(matrix, ensure_ascii=False, indent=1), encoding="utf-8")

exec_report = {
    "generated_at": now,
    "manifest_repair": {"command": "manifest_repair.py --apply", "changed_tables": [],
                        "skipped": ["quarantine_prod_overwrite(lifecycle_deprecated)",
                                    "xq_comment(lifecycle_planned)",
                                    "xq_quote_snap(lifecycle_deprecated)"],
                        "result": "0 变更（lifecycle 保护）；metadata_consistency PASS"},
    "asset_decisions": decay if False else {"register": len(register_list), "ignore": len(ignore_list),
                                            "expose_backlog": len(unexp),
                                            "decision_file": "agent_platform/registry/data_asset_decisions_p1.json",
                                            "ignore_file": "config/data_inventory_ignore.json"},
    "next": ["DATA3 处理 3 个 lifecycle 表（reactivate or keep skipped）",
             "DATA4 分批补元数据后注册 48 项",
             "产品评审 242 项暴露 backlog",
             "DATA2 等 token 执行 26 项抓取",
             "多源需新资料（29/30）"],
}
(R / "governance_exec_20261103.json").write_text(json.dumps(exec_report, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({"register": len(register_list), "ignore": len(ignore_list),
                  "expose": len(unexp)}, ensure_ascii=False))
