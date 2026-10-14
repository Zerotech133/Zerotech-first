# -*- coding: utf-8 -*-
"""组织监督判读脚本（K-ORG-063 · 055 计划 L2 治理线配套）。

只读零模型：工单看板统计 + 治理账本 24h 体检 + 8093 探活 + 岗卡体检
+ worktree 残留计数，产出判读简报 agent_platform/data/supervision/YYYYMMDD.md。
供 09:15 automation 消费：全绿简短登记，红灯给出处置建议。
"""
import glob
import json
import os
import sys
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


def check_projection():
    """投影一致性守卫（K-ORG-083 判例固化）：本体 team_*.json vs org.json 内联卡
    双向抽验 agent_id 集合 + llm_binding + attributes。判例：本体落库成功≠投影已同步。"""
    reg = os.path.join(ROOT, "agent_platform", "registry")
    org = _read_json(os.path.join(reg, "org.json"), {}) or {}
    proj = {}
    for d in org.get("departments", []):
        for p in d.get("agent_profiles", []) or []:
            proj[p.get("agent_id")] = p
    body = {}
    for fp in glob.glob(os.path.join(reg, "team_*.json")):
        if "team_proj_" in os.path.basename(fp):
            continue  # 059 判例：proj 副本非本体，同名岗以本体为准
        t = _read_json(fp, {}) or {}
        for a in t.get("agents", []):
            body[a.get("agent_id")] = a
    mism = []
    only_body = sorted(set(body) - set(proj))
    only_proj = sorted(set(proj) - set(body))
    # 投影设计面=22 职能岗；A0A(首席助理)+投研 5 岗本体/bundle 全量在册、图上不展示属设计范围
    real_missing = [a for a in only_body
                    if a not in ("A0A", "D1", "F1", "N1", "T1", "T2")]
    if real_missing:
        mism.append("投影缺岗: %s" % ",".join(real_missing))
    if only_proj:
        mism.append("投影多岗: %s" % ",".join(only_proj))
    for aid in sorted(set(body) & set(proj)):
        b, p = body[aid], proj[aid]
        if (b.get("llm_binding") or {}) != (p.get("llm_binding") or {}):
            mism.append("%s llm_binding 不一致" % aid)
        ba = b.get("attributes") or {}
        pa = p.get("attributes") or {}
        for k in ("experience", "specialties", "knowledge"):
            if (ba.get(k) or None) != (pa.get(k) or None):
                mism.append("%s attributes.%s 不一致" % (aid, k))
                break
    return len(proj), mism


def main(push=True):
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

    n_proj, proj_mism = check_projection()
    lines.append("## 投影一致性（org.json %d 卡 vs 本体）: %s"
                 % (n_proj, "一致" if not proj_mism else "%d 项差异" % len(proj_mism)))
    if proj_mism:
        lines += ["- " + m for m in proj_mism[:8]]
        yellows.append("投影同步缺口 %d 项（083 判例：本体落库≠投影同步，需回填或重启 8093 接管写路径）"
                       % len(proj_mism))
    lines.append("- 范围注记: A0A+投研5岗(D1/F1/N1/T1/T2)本体在册、组织图不展示属设计范围")

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

    if push:
        ok_sent, msg = push_verdict_card(report, reds, yellows,
                                         dict(statuses), ok, full, total_agents)
        print("[push]", "SEND_OK" if ok else "SEND_FAIL: %s" % msg)


def build_verdict_card(statuses, ok_8093, full, total_agents, reds, yellows):
    """B2 判读折叠卡：三段式（全局态势 / 异常项 / 处置建议），红黄绿分级头。"""
    if reds:
        title, tmpl = "🛡️ 田小皮监督判读 · 🔴 %d 红 %d 黄" % (len(reds), len(yellows)), "red"
    elif yellows:
        title, tmpl = "🛡️ 田小皮监督判读 · 🟡 %d 黄" % len(yellows), "orange"
    else:
        title, tmpl = "🛡️ 田小皮监督判读 · 全绿", "green"
    stat = " ".join("%s%d" % (k, v) for k, v in sorted(statuses.items()))
    overview = ("**工单** %s\n**服务** 8093 %s · **岗卡** 属性 %d/%d"
                % (stat, "在位" if ok_8093 else "DOWN", full, total_agents))
    alerts = "\n".join(("- 🔴 " + r) for r in reds) + "\n" if reds else ""
    alerts += "\n".join(("- 🟡 " + y) for y in yellows) if yellows else ""
    advice = "立即处置红灯项；黄灯项当日排期收尾。" if reds else \
             "黄灯项当日排期收尾，无需升级。" if yellows else \
             "无异常，正常推进。"
    return {
        "schema": "2.0", "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": title},
                   "template": tmpl},
        "body": {"elements": [
            {"tag": "markdown", "content": overview},
            {"tag": "hr"},
            {"tag": "markdown", "content": alerts or "（无异常项）"},
            {"tag": "hr"},
            {"tag": "markdown", "content": "**建议**：" + advice},
            {"tag": "markdown", "content":
                "<font color='grey'>零界组织监督 · 09:15 判读 · %s</font>"
                % datetime.now().strftime("%m-%d %H:%M")},
        ]}}


def push_verdict_card(report, reds, yellows, statuses, ok_8093, full, total_agents):
    """B2 判读结果推老田飞书折叠卡；fail-open（推送失败不影响体检结论）。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, "..", "portal"))
        from feishu_card_v2 import send_card  # noqa: E402 复用凭证链（实测 SEND_OK 通道）
        card = build_verdict_card(statuses, ok_8093, full, total_agents, reds, yellows)
        return send_card(card)
    except Exception as e:  # noqa: BLE001 推送链任何异常都不阻断体检
        return False, repr(e)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="组织监督判读（可推飞书折叠卡）")
    ap.add_argument("--no-push", action="store_true", help="只落报告不推卡")
    a = ap.parse_args()
    main(push=not a.no_push)
