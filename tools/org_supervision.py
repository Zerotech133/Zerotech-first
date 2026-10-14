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


def check_rules():
    """规则分层体检（K-ORG-087/T1）：三项黄级检查——
    ① org_rules.md 在位且铁律 ≤50 行（防规则膨胀，086 拍板）
    ② 29 岗 rules 字段类型合法（缺省或 list[str]）
    ③ 抽验 3 岗 persona 拼接含「组织铁律」锚行（注入链通断）"""
    problems = []
    rules_path = os.path.join(ROOT, "agent_platform", "rules", "org_rules.md")
    n_rules = 0
    if not os.path.exists(rules_path):
        problems.append("org_rules.md 缺失（铁律未注入，fail-open 静默降级中）")
    else:
        n_rules = sum(1 for ln in open(rules_path, encoding="utf-8")
                      if ln.strip().startswith("- "))
        if n_rules == 0:
            problems.append("org_rules.md 零铁律条目（格式错误？）")
        elif n_rules > 50:
            problems.append("org_rules.md %d 条超 50 上限（需蒸馏进 knowledge_docs）" % n_rules)
    b = _read_json(os.path.join(ROOT, "portal", "agent_ide_bundle.json"), {}) or {}
    agents = b.get("agents", {})
    bad = [aid for aid, a in agents.items()
           if not (a.get("rules") is None
                   or (isinstance(a.get("rules"), list)
                       and all(isinstance(r, str) for r in a["rules"])))]
    if bad:
        problems.append("rules 字段类型非法: %s" % ",".join(sorted(bad)[:8]))
    # ③ 抽验注入链（import agent_coder 触发 _ORG_RULES 模块级加载）
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import agent_coder  # noqa: PLC0415
        for aid in ("QA1", "DEV1", "D1"):
            if aid in agents and "组织铁律" not in agent_coder._persona(aid):
                problems.append("%s persona 未含铁律锚行（注入链断）" % aid)
    except Exception as e:  # noqa: BLE001 体检自身异常不误报岗
        problems.append("persona 抽验失败: %s" % str(e)[:50])
    return n_rules, problems


def check_persona_size():
    """Persona 体积体检（K-ORG-090 token 总闸）：盯防注入增量膨胀——
    岗卡铁律段（T1≈268t）+ 记忆回注段（G1≤150t）+ 岗级 rules 合计折算 token
    （1.5 字/t 保守口径），>600t 黄灯（087 预算 350t + G1 150t + 裕量）。
    返回 (最大增量 tokens, 超阈岗列表, 异常项)。"""
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import agent_coder  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return None, [], ["persona 体积探测失败: %s" % str(e)[:50]]
    over, worst = [], 0
    for aid in agent_coder.BUNDLE["agents"]:
        try:
            p = agent_coder._persona(aid)
        except Exception:  # noqa: BLE001
            continue
        inc = 0
        if "## 组织铁律" in p:
            inc += len(p[p.index("## 组织铁律"):])
        if "## 本岗近期判断" in p:
            end = p.index("## 组织铁律") if "## 组织铁律" in p else len(p)
            inc += len(p[p.index("## 本岗近期判断"):end])
        tok = int(inc / 1.5)
        worst = max(worst, tok)
        if tok > 600:
            over.append("%s≈%dt" % (aid, tok))
    return worst, over, []


def check_usage():
    """LLM 用量台账判读（K-ORG-038 判例配套）：统计 portal/data/llm_usage.jsonl
    近 24h（ts 字符串比较，格式可排序）调用次数 / token 总量 / channel 分布。
    fail-open：台账不存在或为空返回 None（G3 未接线或零调用，不算异常不告警）；
    坏行跳过不抛异常。"""
    p = os.path.join(ROOT, "portal", "data", "llm_usage.jsonl")
    if not os.path.exists(p):
        return None
    # now-24h 截断线（ts 格式可排序，字符串比较即可）；不加导入，用 timestamp 回退一天
    cutoff = datetime.fromtimestamp(
        datetime.now().timestamp() - 24 * 3600).strftime("%Y-%m-%d %H:%M:%S")
    n, in_tok, out_tok = 0, 0, 0
    chans = Counter()
    parsed = 0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue  # 坏行跳过
                parsed += 1
                if str(e.get("ts", "")) >= cutoff:
                    n += 1
                    in_tok += int(e.get("in_tok") or 0)
                    out_tok += int(e.get("out_tok") or 0)
                    chans[e.get("channel", "?")] += 1
    except Exception:
        return None  # 台账不可读等同无台账，fail-open
    if parsed == 0:
        return None  # 空文件/全坏行视为无台账
    return n, in_tok, out_tok, chans


def check_llm_health():
    """通道健康判读（K-ORG-090/G2）：统计 portal/data/llm_health.jsonl 近 24h
    各通道失败次数。fail-open：文件不存在返回 None（零降级事件≠故障）。
    默认请求档 anthropic（K-ORG-050）连续失败 ≥3 次 = 已在依赖降级链，黄灯。"""
    p = os.path.join(ROOT, "portal", "data", "llm_health.jsonl")
    if not os.path.exists(p):
        return None
    cutoff = datetime.fromtimestamp(
        datetime.now().timestamp() - 24 * 3600).strftime("%Y-%m-%d %H:%M:%S")
    fails = Counter()
    last_err = {}
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if str(e.get("ts", "")) >= cutoff:
                    ch = e.get("channel", "?")
                    fails[ch] += 1
                    last_err[ch] = str(e.get("err", ""))[:60]
    except Exception:
        return None
    return fails, last_err


def check_tool_audit():
    """工具调用审计判读（K-ORG-090/T4·observe 先行）：统计 agent_platform/data/
    llm_tool_audit.jsonl 近 24h 调用次数与 caller 分布。observe 模式只记不拦，
    攒 3 天数据后由 087/T4 拍板白名单切 enforce（排期 10-18）。
    fail-open：文件不存在或空返回 None（observe 零流量≠故障）。"""
    p = os.path.join(ROOT, "agent_platform", "data", "llm_tool_audit.jsonl")
    if not os.path.exists(p):
        return None
    cutoff = datetime.fromtimestamp(
        datetime.now().timestamp() - 24 * 3600).strftime("%Y-%m-%d %H:%M:%S")
    callers, endpoints = Counter(), Counter()
    n, n_err = 0, 0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if str(e.get("ts", "")) >= cutoff:
                    n += 1
                    if not e.get("ok", True):
                        n_err += 1
                    callers[str(e.get("caller"))] += 1
                    endpoints[str(e.get("endpoint", "?"))] += 1
    except Exception:
        return None
    if n == 0:
        return None
    return n, n_err, callers, endpoints


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
    lines.append("")

    usage = check_usage()
    if usage is None:
        lines.append("## LLM 用量（近24h）: 无台账（G3 未接线或零调用）")
    else:
        n_calls, u_in, u_out, chans = usage
        lines.append("## LLM 用量（近24h）: %d 次调用 · in %d tok · out %d tok"
                     % (n_calls, u_in, u_out))
        if n_calls:
            lines.append("- 通道分布: %s" % " · ".join(
                "%s %d" % (k, v) for k, v in chans.most_common()))
        lines.append("- <font color='grey'>智谱积分参考: anthropic 通道单工单 ≈4~5 积分"
                     "（K-ORG-038 判例）</font>")
    # K-ORG-080/N7: 工单成本台账（近7天·cost_report.summary_line 接线）
    try:
        import cost_report
        lines.append("- <font color='grey'>工单成本台账: %s</font>"
                     % cost_report.summary_line("近7天",
                                                cost_report.usage_total(7)))
    except ImportError:
        pass  # cost_report 未部署环境降级（N7 fail-open）
    lines.append("")

    # K-ORG-090/T4: 工具调用审计（observe 段）
    audit = check_tool_audit()
    if audit is None:
        lines.append("## 工具调用审计（observe·近24h）: 无记录（T4 observe 零流量）")
    else:
        n_a, n_err, callers, endpoints = audit
        lines.append("## 工具调用审计（observe·近24h）: %d 次调用 · 异常 %d"
                     % (n_a, n_err))
        lines.append("- caller 分布: %s" % " · ".join(
            "%s %d" % (k, v) for k, v in callers.most_common(6)))
        lines.append("- 热点端点: %s" % " · ".join(
            "%s %d" % (k, v) for k, v in endpoints.most_common(4)))
        lines.append("- <font color='grey'>observe 只记不拦·白名单 10-18 拍板（087/T4）</font>")
    lines.append("")

    n_rules, rule_problems = check_rules()
    lines.append("## 规则分层（org_rules %d 条）: %s"
                 % (n_rules, "通过" if not rule_problems else "%d 项异常" % len(rule_problems)))
    if rule_problems:
        lines += ["- " + p for p in rule_problems[:6]]
        yellows.append("规则分层异常 %d 项（087/T1：铁律未注入=岗行为靠主控人脑带，需修复注入链）"
                       % len(rule_problems))
    # 090 token 总闸：注入增量体积盯防
    worst_tok, over_jobs, size_problems = check_persona_size()
    if worst_tok is not None and not size_problems:
        lines.append("- 注入增量峰值: ≈%dt/岗（阈 600t·090 总闸）%s"
                     % (worst_tok, "，超阈: " + " ".join(over_jobs[:4]) if over_jobs else ""))
        if over_jobs:
            yellows.append("persona 注入增量超 600t: %s（087 防膨胀红线，需蒸馏）" % " ".join(over_jobs))
    lines.append("")

    health = check_llm_health()
    lines.append("## 通道健康（近24h 降级事件）: %s"
                 % ("零失败" if health is None
                    else "%d 次失败" % sum(health[0].values())))
    if health is not None:
        fails, last_err = health
        for ch, cnt in fails.most_common():
            lines.append("- %s 失败 %d 次（末次: %s）" % (ch, cnt, last_err[ch]))
        if fails.get("anthropic", 0) >= 3:
            yellows.append("anthropic（默认产码档）24h 失败 %d 次——已在依赖降级链，"
                           "需排查包年端点（限额/网络）" % fails["anthropic"])
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
