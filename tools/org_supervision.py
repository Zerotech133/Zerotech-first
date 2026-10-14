# -*- coding: utf-8 -*-
"""组织监督判读脚本（K-ORG-063 · 055 计划 L2 治理线配套）。

只读零模型：工单看板统计 + 治理账本 24h 体检 + 8093 探活 + 岗卡体检
+ worktree 残留计数，产出判读简报 agent_platform/data/supervision/YYYYMMDD.md。
供 09:15 automation 消费：全绿简短登记，红灯给出处置建议。
"""
import glob
import json
import os
import urllib.request
from collections import Counter
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "agent_platform", "data", "supervision")


def _read_json(path, default=None):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def check_tasks():
    """工单看板：dict 结构（键=TID），按 status/project 统计。"""
    d = _read_json(os.path.join(ROOT, ".worktrees", "tasks.json"), {}) or {}
    statuses = Counter(v.get("status", "?") for v in d.values())
    projects = Counter(
        (v.get("project") or "general") for v in d.values()
        if v.get("status") not in ("done", "merged", "dropped", "cancelled")
    )
    reviewing = [k for k, v in d.items() if v.get("status") in ("in_review", "review")]
    return len(d), statuses, projects, reviewing


def check_events():
    """治理账本 24h kind 计数 + 最后一条时间。"""
    p = os.path.join(ROOT, "portal", "data", "dev_events.jsonl")
    today = datetime.now().strftime("%Y-%m-%d")
    kinds = Counter()
    last = ""
    n = 0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                n += 1
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if str(e.get("ts", "")).startswith(today):
                    kinds[e.get("kind", "?")] += 1
                last = e.get("ts", last)
    except Exception:
        return n, kinds, "账本不可读"
    return n, kinds, last


def check_8093():
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        d = json.load(op.open("http://127.0.0.1:8093/api/org", timeout=5))
        return True, "%d 部门" % len(d["org"].get("departments", []))
    except Exception as e:
        return False, str(e)[:60]


def check_bundle():
    b = _read_json(os.path.join(ROOT, "portal", "agent_ide_bundle.json"), {}) or {}
    agents = b.get("agents", {})
    full = sum(
        1 for a in agents.values()
        if (a.get("attributes") or {}).get("experience")
        and (a.get("attributes") or {}).get("specialties")
    )
    kn = sum(1 for a in agents.values() if (a.get("attributes") or {}).get("knowledge"))
    return len(agents), full, kn


def check_worktrees():
    return len(glob.glob(os.path.join(ROOT, ".worktrees", "task", "*")))


def main():
    lines = ["# 组织监督判读 %s" % datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
    reds, yellows = [], []

    total, statuses, projects, reviewing = check_tasks()
    lines.append("## 工单看板（总 %d）" % total)
    lines.append("- 状态: %s" % dict(statuses))
    lines.append("- 在途项目线: %s" % dict(projects) or "-")
    if reviewing:
        yellows.append(
            "工单卡在评审 %d 张: %s…（需核对：含历史存量可能已实际 merge 仅状态未流转，"
            "与团队线对口径后批量收尾）" % (len(reviewing), ",".join(reviewing[:6]))
        )
    lines.append("")

    n, kinds, last = check_events()
    lines.append("## 治理账本（累计 %d 行·末笔 %s）" % (n, last))
    lines.append("- 今日 kind 分布: %s" % dict(kinds))
    if "review_verdict" not in kinds and ("task_start" in kinds or "dev_done" in kinds):
        yellows.append("今日有产码活动但无复核结论（QA1 环节可能卡住）")
    lines.append("")

    ok, detail = check_8093()
    lines.append("## 8093 组织注册服务: %s（%s）" % ("在位" if ok else "DOWN", detail))
    if not ok:
        reds.append("8093 探活失败——org 图/属性/学习链路全断，需拉起服务（tools/org_registry_server.py）")
    lines.append("")

    total_agents, full, kn = check_bundle()
    lines.append("## 岗卡体检（%d 岗）" % total_agents)
    lines.append("- 属性非缺省: %d/%d · 知识沉淀: %d 岗" % (full, total_agents, kn))
    if full < total_agents:
        yellows.append("属性缺省 %d 岗（基准 29/29）" % (total_agents - full))
    lines.append("")

    wt = check_worktrees()
    lines.append("## worktree 残留: %d 个未清" % wt)
    if wt > 3:
        yellows.append("worktree 残留 %d 个（>3，需 devtask 清场或人工核对）" % wt)
    lines.append("")

    lines.append("## 判读")
    if reds:
        lines.append("**🔴 红灯 %d 项**：" % len(reds))
        lines += ["- " + r for r in reds]
    else:
        lines.append("🔴 红灯 0 项")
    if yellows:
        lines.append("**🟡 黄灯 %d 项**：" % len(yellows))
        lines += ["- " + y for y in yellows]
    else:
        lines.append("🟡 黄灯 0 项")

    report = "\n".join(lines) + "\n"
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, datetime.now().strftime("%Y%m%d") + ".md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print("[written]", out)


if __name__ == "__main__":
    main()
