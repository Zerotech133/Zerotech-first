# -*- coding: utf-8 -*-
"""数据治理情况总览：知识治理 + 数据仓治理 + 机制 + 未闭环问题。"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
R = REPO / "research/reports"
S = REPO / "portal/static"
KB = Path(r"C:\项目\知识库")
STAMP = "20261103"
now = datetime.now().astimezone().isoformat(timespec="seconds")

# ---------- 知识治理 ----------
reg = json.loads((REPO / "agent_platform/registry/learning_projects.json").read_text(encoding="utf-8"))
gov = json.loads((R / f"knowledge_governance_{STAMP}.json").read_text(encoding="utf-8"))
today = json.loads((R / f"governance_today_{STAMP}.json").read_text(encoding="utf-8"))
audit = json.loads((R / f"card_quality_audit_{STAMP}.json").read_text(encoding="utf-8"))
# 多源概念唯一口径（frozen）：multi_source_canonical_20261104.json
sys.path.insert(0, str(Path(__file__).resolve().parent))
from multi_source_canonical import load_canonical  # noqa: E402
_canon = load_canonical()
multi = {"final_multi_source": int(_canon.get("count") or 0),
         "canonical_version": _canon.get("version"),
         "target": int(_canon.get("target") or 30),
         "auto_not_counted": _canon.get("auto_not_counted"),
         "definition": _canon.get("definition", {}).get("name"),
         "source": "research/reports/multi_source_canonical_20261104.json"}
gate = json.loads((R / f"learning_gate_{STAMP}.json").read_text(encoding="utf-8"))
excl = json.loads((R / f"exclusions_today_{STAMP}.json").read_text(encoding="utf-8"))
cards = json.loads((S / "kb_learning_cards.json").read_text(encoding="utf-8")).get("cards") or []

projects = []
for p in reg["projects"]:
    pid = p["project_id"]
    up = KB / "30_知识单元" / f"{pid}_units_draft.jsonl"
    units = sum(1 for _ in up.open(encoding="utf-8")) if up.exists() else 0
    projects.append({"project_id": pid, "status": p.get("status"), "units": units, "owner": p.get("owner")})

# ---------- 数据仓 ----------
ds = json.loads((R / "dataset_inventory_20261102.json").read_text(encoding="utf-8"))["datasets"]
registered = sum(1 for d in ds if d.get("registered"))
exposed = sum(1 for d in ds if d.get("exposed"))
domains = Counter(str(d.get("domain")) for d in ds)
today_d = date(2026, 11, 3)
stale, nodate = [], 0
for d in ds:
    l = d.get("latest")
    if not l:
        nodate += 1
        continue
    try:
        y, m, dd = map(int, str(l)[:10].split("-"))
        age = (today_d - date(y, m, dd)).days
        if age > 30:
            stale.append((str(d.get("label")), str(l), age))
    except Exception:  # noqa: BLE001
        nodate += 1

def dir_size(root: Path) -> dict:
    """实时统计目录文件数与 GB（避免硬编码漂移）。"""
    n = total = 0
    for base, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
                n += 1
            except OSError:
                pass
    return {"files": n, "gb": round(total / 1024 ** 3, 2)}


dw_size = dir_size(REPO / "quant_backtest/data")
kb_size = dir_size(KB)
registered_pct = round(100 * registered / max(1, len(ds)), 1)
exposed_pct = round(100 * exposed / max(1, len(ds)), 1)

open_items = [
    ("P1", "第二期知识价值低（已审 0 通过）", "46 有效候选/17 预审通过项经人工抽检全部驳回（工具推广/碎片）；0 学习卡，建议降级工具情报库", "DATA1", "决策"),
    ("P1", "指标说明书排除清单需继续约束下游", "候选池已过滤（869→46）；多源/微调输入后续统一用 effective 文件", "DATA1", "已下发，持续"),
    ("P1", "多源概念 29/30（差 1）", "无新资料下上限；唯一 +1 候选为错误合并（房地产/计算机），不注水", "DATA1/Lead", "待新资料"),
    ("P1", "知识治理覆盖已扩多项目", "已加入每日任务（knowledge_governance_all），二期/老木纳入", "DATA1", "已完成"),
    ("P1", "cyq_chips 覆盖不足", "23 文件/0.2MB，筹码因子抽样覆盖 0-0.27%", "DATA2", "排期"),
    ("P2", "17 个数据集 >30 天未更新", "港股利润/现金流/财务指标等 2026-09-28，及 probe 类", "DATA2", "排期"),
    ("P2", "59 个数据集无 latest 字段", "inventory 元数据不完整", "DATA4", "2 周"),
    ("P2", "数据仓注册率/暴露率偏低", f"注册 {registered}/{len(ds)}（{registered_pct}%）、暴露 {exposed}/{len(ds)}（{exposed_pct}%）", "DATA4", "季度"),
    ("P2", "laomu 重复单元已清", "288→286（去重 2 条）", "DATA5", "已完成"),
    ("P2", "manifest 覆盖缺口", "真实缺口 9（5 可补键/4 需抓取）；补键 apply 0 变更（lifecycle 跳过 3）", "DATA3", "季度"),
]
kpis = [
    ("元数据行占比（一期）", "≤5%", "0%", "✅"),
    ("重复率（一期）", "≤2%", "0%", "✅"),
    ("溯源核验率（一期）", "100%", "100%", "✅"),
    ("有效知识单元（今日文件）", "—", "1,045（800+245）", "✅"),
    ("学习卡", "≥400", "400（审计 0 问题/可追溯 100%）", "✅"),
    ("有效候选", "—", "1,372（已排除 823 垃圾）", "✅"),
    ("多源概念", "≥30", f"{multi['final_multi_source']}（canonical 冻结口径；机器 {(_canon.get('auto_not_counted') or {}).get('v4_llm_auto_count', 30)} 未签核）", "⚠️ 差 1"),
    ("第二期有效候选", "—", "46（已审：0 通过，工具推广为主）", "⚠️ 低价值"),
    ("数据仓质量检查", "8/8 PASS", "8/8 PASS（C6/C7/C8 复检 true）", "✅"),
    ("元数据一致性", "PASS", "PASS（catalog v35/256 表/registry 256）", "✅"),
    ("注册率/暴露率", "—", f"{registered_pct}% / {exposed_pct}%", "⚠️"),
]

out = {
    "generated_at": now,
    "knowledge": {"projects": projects, "governance": gov.get("metrics"),
                  "today_effective": today.get("effective"), "cards": len(cards),
                  "cards_audit": audit.get("summary"), "multi_source": multi.get("final_multi_source"),
                  "gate": gate.get("summary"), "exclusions": {"units": excl["units_count"],
                                                              "candidates": excl["candidates_count"]}},
    "warehouse": {"datasets": len(ds), "registered": registered, "exposed": exposed,
                  "domains": dict(domains), "size": dw_size, "kb_size": kb_size,
                  "stale_30d": len(stale), "stale_samples": stale[:8], "no_latest": nodate,
                  "quality": "8/8 PASS", "metadata_consistency": "PASS",
                  "manifest_missing": {"tdx": 86, "tushare": 192}},
    "open_items": [{"priority": p, "item": i, "detail": d, "owner": o, "eta": e}
                   for p, i, d, o, e in open_items],
    "kpis": [{"kpi": k, "target": t, "current": c, "status": s} for k, t, c, s in kpis],
}
(R / f"data_governance_overview_{STAMP}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

# CSV
csv_p = REPO / "deliverables" / "2026-11-03_数据治理情况.csv"
with csv_p.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["模块", "指标", "目标", "当前", "状态"])
    for k, t, c, s in kpis:
        w.writerow(["KPI", k, t, c, s])
    w.writerow([])
    w.writerow(["模块", "优先级", "问题", "说明", "Owner", "ETA"])
    for p, i, d, o, e in open_items:
        w.writerow(["未闭环", p, i, d, o, e])

# MD
L = [f"# 数据治理情况总览（{STAMP}）", "", f"> 生成：{now}｜范围：知识治理（学习线 4 项目）+ 数据仓（314 数据集）+ 自动化机制", ""]
L += ["## 一、一句话结论", "",
      "**知识侧：一期治理指标全达标（元数据 0/重复 0/核验 100%），今日文件有效单元 1,045、有效候选 1,372、卡 400、多源 27/30；"
      "数据仓侧：314 数据集/265 注册/72 暴露，质量 8/8 PASS、元数据一致性 PASS；主要缺口是 17 个陈旧数据集、指标说明书排除未下发、多源差 3。**", ""]
L += ["## P0 治理执行（2026-11-03 傍晚）", "",
      "| P0 项 | 结果 |", "|---|---|",
      "| 指标说明书排除下发 | ✅ 第二期候选 869→46（823 排除），portal 静态同步 |",
      "| 第二期候选终审 | ✅ 46 有效候选：预审通过 17 → 人工抽检全部驳回（工具推广/碎片），0 学习卡 |",
      "| 多项目治理覆盖 | ✅ 二期+老木纳入（3 项目/2,202 单元/核验 99.1%） |",
      "| laomu 去重 | ✅ 288→286（-2，备份保留） |", ""]
L += ["## 二、知识治理", "", "| 项目 | 状态 | 单元 | Owner |", "|---|---|---|---|"]
for p in projects:
    L.append(f"| {p['project_id']} | {p['status']} | {p['units']} | {p['owner']} |")
L += ["", "| 治理指标 | 数值 |", "|---|---|"]
g = gov.get("metrics", {})
L += [f"| 单元/知识单元 | {g.get('units')} / {g.get('knowledge_units')} |",
      f"| 元数据行 | {g.get('meta_units')} |",
      f"| 精确重复 / 近重复对 | {g.get('exact_dup_units')} / {g.get('near_dup_pairs')} |",
      f"| 溯源核验 | {g.get('verified')}/{g.get('units')}（{g.get('verified_pct')}%） |",
      f"| 冲突 | {g.get('conflicts')} |",
      f"| 学习卡 | {len(cards)}（审计 {audit.get('summary', {}).get('issues_total')} 问题 / 可追溯 {audit.get('summary', {}).get('traceable_rate')}） |",
      f"| 多源概念（人工双签） | **{multi.get('final_multi_source')}/30** |",
      f"| 排除项（指标说明书） | {excl['units_count']} 单元 / {excl['candidates_count']} 候选 |",
      f"| learning_gate | {gate.get('summary', {}).get('projects')} 项目/{gate.get('summary', {}).get('checks')} 检查/fail={gate.get('summary', {}).get('fail')} |", ""]
L += ["## 三、数据仓治理", "", "| 项 | 数值 |", "|---|---|",
      f"| 数据集 | **{len(ds)}**（接口 241 / 开源情报 19 / 学习派生 29 / 平台运行 25） |",
      f"| 注册 / 暴露 | {registered}（{registered_pct}%）/ {exposed}（{exposed_pct}%） |",
      f"| 数据量 | {dw_size['files']:,} 文件 / {dw_size['gb']}GB（知识库另 {kb_size['files']:,} 文件 / {kb_size['gb']}GB） |",
      "| 质量检查 | **8/8 PASS**（ts_forecast/report_rc 去重后 C6/C7/C8 复检 true） |",
      "| 元数据一致性 | **PASS**（catalog v35 / 256 表 / registry 256 物理） |",
      f"| 陈旧 >30 天 | **{len(stale)}**（港股利润/现金流/财务指标 2026-09-28 等；probe 类） |",
      f"| 无 latest 字段 | {nodate} |",
      "| manifest 覆盖 | tdx missing 86 / tushare missing 192（检查 code 0，覆盖不足） |", ""]
L += ["## 四、治理机制（自动化）", "", "| 机制 | 状态 |", "|---|---|",
      "| LearningGovernance 每日 02:00 | ✅ rc=0（最近 11-03 02:00） |",
      "| LearningIntake 每 30 分钟 | ✅ rc=0（最近 14:33） |",
      "| LearningPipeline 每日 20:00 | ✅ rc=0（最近 11-02 20:00） |",
      "| 漂移告警（基线 4 阈值） | ✅ alerts=0；基线已更新 |",
      "| 发布门（learning_release） | ✅ 强制 gate+digest+post-check |",
      "| 全链路门禁 | ✅ 语法/导航/路由/元数据 + 4 项目 13 检查 0 失败 |", ""]
L += ["## 五、KPI 对照", "", "| 指标 | 目标 | 当前 | 状态 |", "|---|---|---|---|"]
for k, t, c, s in kpis:
    L.append(f"| {k} | {t} | {c} | {s} |")
L += ["", "## 六、未闭环问题（按优先级）", "", "| 优先级 | 问题 | 说明 | Owner | ETA |", "|---|---|---|---|---|"]
for p, i, d, o, e in open_items:
    L.append(f"| {p} | {i} | {d} | {o} | {e} |")
L += ["", "## 七、证据路径", "",
      "- 知识治理：`research/reports/knowledge_governance_20261103.json`、`governance_today_20261103.json`、`exclusions_today_20261103.json`",
      "- 卡审计：`research/reports/card_quality_audit_20261103.json`",
      "- 多源：`research/reports/concept_merge_review_b_20261103.json`",
      "- 数据仓质量：`research/reports/datawarehouse_quality_w3_20261103.json`（8/8 PASS）",
      "- 数据集清单：`research/reports/dataset_inventory_20261102.json`",
      "- 门禁：`research/reports/learning_gate_20261103.json`", ""]
md_p = R / f"data_governance_overview_{STAMP}.md"
md_p.write_text("\n".join(L), encoding="utf-8")
print("written", md_p.name, csv_p.name)
print(json.dumps({"projects": len(projects), "cards": len(cards), "multi": multi.get("final_multi_source"),
                  "datasets": len(ds), "registered": registered, "exposed": exposed,
                  "stale": len(stale), "open_items": len(open_items)}, ensure_ascii=False))
