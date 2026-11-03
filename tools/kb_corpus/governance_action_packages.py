# -*- coding: utf-8 -*-
"""生成三项治理「审批即可执行」包：DATA2 外部抓取 / DATA3 manifest 补键 / DATA4 注册暴露。"""
import csv
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
R = REPO / "research/reports"
OUT = REPO / "deliverables"
OUT.mkdir(parents=True, exist_ok=True)
cov = json.loads((R / "datawarehouse_coverage_p1_20261103.json").read_text(encoding="utf-8"))
cats = cov["categories"]
ext = cov.get("external_fetch_required") or []
missing = cats.get("missing") or []
unreg = cats.get("unregistered") or []
unexp = cats.get("unexposed") or []
stale = cats.get("stale_30d") or []
nolatest = cats.get("no_latest") or []

# ---------- 1) DATA2 外部抓取执行包 ----------
csv_p = OUT / "2026-11-03_DATA2外部抓取清单.csv"
with csv_p.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["源", "数据集", "类型", "原因", "Owner", "审批", "状态"])
    for x in ext:
        w.writerow([x.get("source"), x.get("dataset"), x.get("type"), x.get("reason"),
                    x.get("owner"), x.get("approval"), "待 token/审批"])
md = ["# DATA2 外部抓取执行包（P1，2026-11-03）", "",
      "> 依据：`datawarehouse_coverage_p1_20261103.json`；共 **26 项**（manifest 缺口 9 + 陈旧 17）",
      "> 前置：**TUSHARE token**（SOPS/审批）+ 采集排期（DATA1 批准）；本包为审批后一键执行依据。", "",
      "## 一、清单", "", "| # | 源 | 数据集 | 类型 | 原因 | Owner |", "|---|---|---|---|---|---|"]
for i, x in enumerate(ext, 1):
    md.append(f"| {i} | {x.get('source')} | {x.get('dataset')} | {x.get('type')} | {str(x.get('reason'))[:60]} | {x.get('owner')} |")
md += ["", "## 二、执行方式（token 就绪后）", "",
       "```powershell",
       "$env:TUSHARE_TOKEN = '<从 SOPS 注入，勿落盘>'",
       "python tools/datawarehouse/fetch_external_p1.py --plan        # 生成抓取计划（dry-run）",
       "python tools/datawarehouse/fetch_external_p1.py --apply       # 执行抓取（限速/断点）",
       "python tools/datawarehouse/metadata_consistency.py            # 复检",
       "```", "",
       "## 三、验收", "",
       "- 26 项全部落盘 + manifest 更新；",
       "- `metadata_consistency` PASS；`manifest_check` 缺口 9→0；",
       "- 17 个陈旧数据集 latest 更新到最近交易日；",
       "- 失败项记录原因，不静默。", "",
       "## 四、风险", "",
       "- 抓取频率/配额超限 → 限速+断点续抓；",
       "- 覆盖历史数据前先备份（目标目录级）；",
       "- 不修改生产策略/回测配置。"]
(OUT / "2026-11-03_DATA2外部抓取执行包.md").write_text("\n".join(md), encoding="utf-8")

# ---------- 2) DATA3 manifest 补键审批包 ----------
dry_manifest = (cov.get("dry_run_drafts") or {}).get("manifest") or {}
fixable = [x for x in missing if x.get("store_exists")]
draft = {"generated_at": cov.get("generated_at"), "mode": "approval_pending",
         "approval_owner": "DATA3", "fixable": fixable,
         "need_external": [x for x in missing if not x.get("store_exists")],
         "dry_run_manifest": dry_manifest}
(R / "manifest_repair_p1_draft_20261103.json").write_text(json.dumps(draft, ensure_ascii=False, indent=1), encoding="utf-8")
md = ["# manifest 补键审批包（DATA3，P1）", "",
      "> 范围：真实 manifest 缺口 **9 项**，其中 **5 项数据已在盘**（可安全补键）、4 项需外部抓取。",
      "> 审批：DATA3 复核后执行；只写 `_manifest.json` 元数据，不改数据文件。", "",
      "## 一、可仓内补键（5 项）", "", "| 源 | 数据集 | 数据在盘 | 动作 |", "|---|---|---|---|"]
for x in fixable:
    md.append(f"| {x.get('source')} | {x.get('dataset')} | ✅ | manifest 补键 |")
md += ["", "## 二、需外部抓取（4 项）", ""]
for x in [x for x in missing if not x.get("store_exists")]:
    md.append(f"- {x.get('source')} / {x.get('dataset')}（转 DATA2 抓取）")
md += ["", "## 三、执行与回滚", "",
       "```powershell",
       "python tools/datawarehouse/manifest_repair.py --json     # dry-run 预览",
       "python tools/datawarehouse/manifest_repair.py --apply    # 写 _manifest.json（自动备份）",
       "python tools/datawarehouse/metadata_consistency.py       # PASS 验收",
       "```", "",
       "回滚：恢复 `_manifest.json` 备份；数据文件零改动。",
       "", "## 四、预期收益", "",
       "- manifest 真实缺口 9 → 0（其中 5 项本次可清）；",
       "- `manifest_check` 覆盖提升；数据仓注册/暴露基础更实。"]
(OUT / "2026-11-03_manifest补键审批包.md").write_text("\n".join(md), encoding="utf-8")

# ---------- 3) DATA4 注册/暴露审批包 ----------
IGNORE = (".git", "probe", "temp", "tmp", "backup", "_bak", "quarantine", "旧", "archive")
ignore_list = [x for x in unreg if any(k in str(x.get("label", "")).lower() or k in str(x.get("path", "")).lower() for k in IGNORE)]
register_list = [x for x in unreg if x not in ignore_list]
csv_p = OUT / "2026-11-03_注册暴露清单.csv"
with csv_p.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["类别", "label", "域", "建议ID", "大小MB", "latest", "建议动作", "Owner", "审批"])
    for x in register_list:
        w.writerow(["未注册", x.get("label"), x.get("domain"), x.get("suggested_id"),
                    x.get("size_mb"), x.get("latest"), x.get("action"), "DATA4", "DATA4+DATA1"])
    for x in ignore_list:
        w.writerow(["忽略", x.get("label"), x.get("domain"), x.get("suggested_id"),
                    x.get("size_mb"), x.get("latest"), "列入忽略清单（probe/.git/临时）", "DATA4", "DATA1 确认"])
    for x in unexp:
        w.writerow(["未暴露", x.get("label"), x.get("domain"), x.get("suggested_id"),
                    x.get("size_mb"), x.get("latest"), "按价值排期暴露", "DATA4", "产品+门户评审"])
md = ["# 注册/暴露审批包（DATA4，P1）", "",
      f"> 未注册 **{len(unreg)}**（其中建议忽略 **{len(ignore_list)}**、可登记 **{len(register_list)}**）；未暴露 **{len(unexp)}**。",
      "> 审批：DATA4 登记/忽略清单需 DATA1 确认；暴露需产品/门户评审。", "",
      "## 一、未注册处理建议", "",
      f"- 可登记：**{len(register_list)}** 项（按 suggested_id 走 registry 登记）；",
      f"- 忽略：**{len(ignore_list)}** 项（probe/.git/临时/备份目录，不登记）；", "",
      "## 二、暴露排期建议（Top 价值，按大小/时效）", "",
      "| label | 域 | 大小MB | latest | 建议ID |", "|---|---|---|---|---|"]
for x in sorted(unexp, key=lambda y: -(y.get("size_mb") or 0))[:20]:
    md.append(f"| {x.get('label')} | {x.get('domain')} | {x.get('size_mb')} | {x.get('latest')} | {x.get('suggested_id')} |")
md += ["", f"（完整 {len(unexp)} 项见 CSV）", "",
       "## 三、执行步骤", "",
       "1. DATA4 审核 `2026-11-03_注册暴露清单.csv`；",
       "2. 登记：按 suggested_id 分批写 registry（每批 10 个，跑 metadata_consistency）；",
       "3. 忽略：写入 ignore 清单，避免重复评估；",
       "4. 暴露：按价值 Top20 先评审，再逐步扩展（当前 22.9% → 目标 50%）。", "",
       "## 四、预期收益", "",
       "- 注册率 84.4% → 95%；暴露率 22.9% → 50%（分两批）；",
       "- 数据资产可见性/可发现性提升，支撑因子/回测取数。"]
(OUT / "2026-11-03_注册暴露审批包.md").write_text("\n".join(md), encoding="utf-8")

# ---------- 4) 行动矩阵 ----------
matrix = {
    "generated_at": cov.get("generated_at"),
    "actions": [
        {"id": "A1", "item": "DATA2 外部抓取 26 项", "owner": "DATA2", "approval": "TUSHARE token(SOPS)+DATA1 排期",
         "package": "2026-11-03_DATA2外部抓取执行包.md", "status": "pending_approval"},
        {"id": "A2", "item": "manifest 补键 5 项（数据已在盘）", "owner": "DATA3", "approval": "DATA3 复核",
         "package": "2026-11-03_manifest补键审批包.md", "status": "pending_approval"},
        {"id": "A3", "item": f"未注册 {len(unreg)}（登记 {len(register_list)}/忽略 {len(ignore_list)}）", "owner": "DATA4",
         "approval": "DATA4+DATA1", "package": "2026-11-03_注册暴露审批包.md", "status": "pending_approval"},
        {"id": "A4", "item": f"未暴露 {len(unexp)} 分批排期", "owner": "DATA4", "approval": "产品/门户评审",
         "package": "2026-11-03_注册暴露审批包.md", "status": "pending_approval"},
        {"id": "A5", "item": "多源 +1（29→30）", "owner": "DATA1", "approval": "需新资料/合法合并；当前唯一候选为错误合并",
         "package": "research/reports/multi_source_plus1_candidates_20261103.json", "status": "blocked_no_valid_candidate"},
    ],
}
(R / "governance_action_matrix_20261103.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=1), encoding="utf-8")
print("written packages:", len(ext), "ext |", len(fixable), "manifest fixable |", len(register_list), "register |", len(unexposed := unexp), "unexposed")
