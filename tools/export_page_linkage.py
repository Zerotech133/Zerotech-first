# -*- coding: utf-8 -*-
"""export_page_linkage.py — 从导航注册表生成「全量前端页面联动矩阵」。

源：portal/api_common.py 的 SECTIONS / DOMAIN_NAV_GROUPS（AST，无 import 副作用）。
输出：docs/plans/2026-10-27_前端页面联动矩阵.csv（全部入口逐条上下游/关键ID）。

说明：这是**方案级映射**，用于 UI 重构评审与埋点/路由改造清单；
不是运行时行为，不改页面、不跳转、不执行。
"""
from __future__ import annotations

import argparse
import ast
import csv
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "portal" / "api_common.py"

HUB = {"quant": "/quant-drill", "intel": "/intel", "creator": "/creator",
       "dev": "/dev", "ops": "/ops"}
SECTION_CN = {"quant": "量化", "intel": "情报", "creator": "创作",
              "dev": "研发", "ops": "运维"}


def _extract(name):
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
    raise SystemExit("api_common.py 找不到 %s" % name)


def _level(section, href, group):
    if href in (HUB[section], "/os-entry", "/os-board"):
        return "L0/L1"
    if href.startswith("http"):
        return "外部"
    if any(k in href for k in ("/bt-", "/kg/", "/sa", "/screen", "/premarket",
                               "/afterhours", "/picks", "/paper", "/signals")):
        return "L2 工作台"
    return "L3 明细/工具"


def _key_ids(section, href):
    ids = ["date"]
    if section == "quant":
        if any(k in href for k in ("/sa", "/picks", "/bt-signals", "/bt-positions",
                                   "/paper", "/sellpoint", "/screen", "/premarket",
                                   "/afterhours", "/bt-report")):
            ids += ["symbol", "signal_id", "report_id"]
        if any(k in href for k in ("/bt-", "/strategies", "/sim", "/council", "/debate")):
            ids += ["strategy_id", "backtest_id"]
    elif section == "intel":
        ids = ["date", "event_id", "source_id", "evidence_id"]
        if "kg" in href:
            ids += ["entity_id", "relation_id"]
        if "push" in href or "notify" in href:
            ids += ["trace_id", "subscription_id"]
        if "jev" in href or "brief" in href:
            ids += ["judgment_id", "report_id"]
    elif section == "dev":
        ids = ["task_id", "trace_id", "worktree_id", "commit"]
    elif section == "ops":
        ids = ["task_id", "trace_id", "agent_id", "approval_id"]
        if any(k in href for k in ("/audit", "/approvals", "/capability")):
            ids += ["decision_id", "evidence_id"]
    elif section == "creator":
        ids = ["content_id", "draft_id", "asset_id"]
    return ids


def _downstream(section, group, href):
    if href in ("/os-entry",):
        return ["/os-board", "/quant-drill", "/intel", "/creator", "/dev", "/ops"]
    if href == "/os-board":
        return ["/os-entry", "/audit", "/sys-health"]
    if section == "quant":
        if "盘前" in group or "驾驶" in group:
            return ["/picks.html", "/bt-signals", "/afterhours", "/bt-report"]
        if "指挥" in group:
            return ["/bridge", "/org", "/audit"]
        if "策略" in group:
            return ["/bt", "/bt-opt", "/sim", "/bt-report"]
        if "实盘" in group:
            return ["/bt-report", "/sellpoint", "/audit"]
        return ["/bt", "/bt-report"]
    if section == "intel":
        if "情报流" in group:
            return ["/intel/stream", "/bt-jev", "/kg", "/push/"]
        if "雪球" in group:
            return ["/xq_intel.html", "/news_intel.html", "/intel"]
        if "知识图谱" in group:
            return ["/kg", "/kg/canvasv2", "/intel"]
        if "变现" in group:
            return ["/push/", "/notify-command", "/my-subscription"]
        return ["/intel", "/bt-report"]
    if section == "creator":
        return ["/creator", "/gzh", "/vd"]
    if section == "dev":
        return ["/dev", "/academy", "/org"]
    if section == "ops":
        if "数据运营" in group:
            return ["/dataops", "/bt-data-health", "/dashboard"]
        if "军团" in group:
            return ["/org", "/approvals", "/capability", "/audit"]
        return ["/ops", "/sys-health", "/audit", "/flow"]
    return []


def _upstream(section, href):
    if href in ("/os-entry", "/os-board", "/screen"):
        return ["/os-entry"]
    return ["/os-entry", HUB[section]]


def build_rows():
    sections = _extract("SECTIONS")
    groups = _extract("DOMAIN_NAV_GROUPS")
    rows = []
    for sec, _label, _href in sections:
        for glb, items in groups.get(sec, []):
            for label, href, _key in items:
                external = href.startswith("http")
                rows.append({
                    "领域": SECTION_CN.get(sec, sec),
                    "分组": glb or "(未分组)",
                    "页面": label,
                    "路由": href,
                    "层级": _level(sec, href, glb or ""),
                    "上游": " / ".join(_upstream(sec, href)),
                    "下游": " / ".join(_downstream(sec, glb or "", href)),
                    "关键ID": ",".join(_key_ids(sec, href)),
                    "备注": ("外部链接，需新窗口/不参与站内联动" if external
                             else ("重复入口，需合并到同一路由" if href == "/org" else "")),
                })
    return rows


def to_markdown(rows):
    lines = ["# 全量前端页面联动矩阵（方案 v1）", "",
             "由 `tools/export_page_linkage.py` 从 `portal/api_common.py` 生成；共 %d 条入口。" % len(rows),
             "", "| 领域 | 分组 | 页面 | 路由 | 层级 | 上游 | 下游 | 关键ID | 备注 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| %s | %s | %s | `%s` | %s | %s | %s | %s | %s |" % (
            r["领域"], r["分组"], r["页面"], r["路由"], r["层级"], r["上游"],
            r["下游"], r["关键ID"], r["备注"]))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="generate page linkage matrix")
    ap.add_argument("--out-dir", default=str(REPO / "docs" / "plans"))
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = build_rows()
    csv_path = out / "2026-10-27_前端页面联动矩阵.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    md_path = out / "2026-10-27_前端页面联动矩阵.md"
    md_path.write_text(to_markdown(rows), encoding="utf-8")
    print("rows=%d -> %s ; %s" % (len(rows), csv_path, md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
