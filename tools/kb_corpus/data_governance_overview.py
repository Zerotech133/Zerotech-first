# -*- coding: utf-8 -*-
"""数据治理情况总览：知识治理 + 数据仓治理 + 机制 + 未闭环问题。"""
from __future__ import annotations

import csv
import json
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
multi = json.loads((R / "concept_merge_review_b_20261103.json").read_text(encoding="utf-8"))
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

dw_size = {"files": 269276, "gb": 55.8}   # 实测 Get-ChildItem 汇总
kb_size = {"files": 4355, "gb": 5.79}

open_items = [
    ("P0", "指标说明书排除清单未应用到下游（候选/多源/微调）", "exclusions_today_20261103.json 已生成，871 单元/823 候选待下游过滤", "DATA1", "立即"),
    ("P1", "第二期 46 张有效候选未审", "869 候选扣除 823 垃圾后仅 46，尚未预审/发布", "DATA1", "1 天"),
    ("P1", "多源概念 27/30（差 3）", "已扩 3 来源仍不足，需补主题重叠资料或人工复核", "DATA1/Lead", "1 周"),
    ("P1", "知识治理脚本仅覆盖一期", "knowledge_governance.py 固定 PID，第二期未进治理日报", "DATA1", "2 天"),
    ("P1", "cyq_chips 覆盖不足", "23 文件/0.2MB，筹码因子抽样覆盖 0-0.27%", "DATA2", "排期"),
    ("P2", "17 个数据集 >30 天未更新", "港股利润/现金流/财务指标等 2026-09-28，及 probe 类", "DATA2", "排期"),
    ("P2", "59 个数据集无 latest 字段", "inventory 元数据不完整", "DATA4", "2 周"),
    ("P2", "数据仓注册率/暴露率偏低", f"注册 {registered}/{len(ds)}（84.4%）、暴露 {exposed}/{len(ds)}（22.9%）", "DATA4", "季度"),
    ("P2", "laomu 1 组重复单元未清", "288 单元中 1 个 exact dup 组", "DATA5", "下次治理"),
    ("P2", "manifest 覆盖缺口", "tdx missing 86 / tushare missing 192（manifest_check code 0 但覆盖不足）", "DATA3", "季度"),
]
kpis = [
    ("元数据行占比（一期）", "≤5%", "0%", "✅"),
    ("重复率（一期）", "≤2%", "0%", "✅"),
    ("溯源核验率（一期）", "100%", "100%", "✅"),
    ("有效知识单元（今日文件）", "—", "1,045（800+245）", "✅"),
    ("学习卡", "≥400", "400（审计 0 问题/可追溯 100%）", "✅"),
    ("有效候选", "—", "1,372（已排除 823 垃圾）", "✅"),
    ("多源概念", "≥30", "27（人工双签）", "⚠️ 差 3"),
    ("数据仓质量检查", "8/8 PASS", "8/8 PASS（C6/C7/C8 复检 true）", "✅"),
    ("元数据一致性", "PASS", "PASS（catalog v35/256 表/registry 256）", "✅"),
    ("注册率/暴露率", "—", "84.4% / 22.9%", "⚠️"),
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
csv_p = REPO / "deliverables" / f"2026-11-03_数据治理情况.csv"
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
      f"| 注册 / 暴露 | {registered}（84.4%）/ {exposed}（22.9%） |",
      f"| 数据量 | 269,276 文件 / 55.8GB（知识库另 4,355 文件 / 5.79GB） |",
      f"| 质量检查 | **8/8 PASS**（ts_forecast/report_rc 去重后 C6/C7/C8 复检 true） |",
      f"| 元数据一致性 | **PASS**（catalog v35 / 256 表 / registry 256 物理） |",
      f"| 陈旧 >30 天 | **{len(stale)}**（港股利润/现金流/财务指标 2026-09-28 等；probe 类） |",
      f"| 无 latest 字段 | {nodate} |",
      f"| manifest 覆盖 | tdx missing 86 / tushare missing 192（检查 code 0，覆盖不足） |", ""]
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
