# -*- coding: utf-8 -*-
"""G2a：商业计划（BP v1/v2）隔离生成器。

设计（诚实优先）：
- 只由"本人原话 + 需求冻结 + 推荐卡"生成，**不编造外部事实/数字/引用**；
- 假设台账逐条标 `hypothesis`（待验证）；引用区按三值规则标 `fact/hypothesis/inference`；
- 版本/修订理由来自账本（former verdict feedback），返工后 v2 自动带"修订理由"；
- 产物仍是隔离工作区 index.html，复用 G1 链与只读预览路由。
"""
from __future__ import annotations

import html
from typing import Any, Dict, List

DEFAULT_ASSUMPTIONS = [
    ("A1", "用户是「有经验、有想法、仍在持续创业」的人", "hypothesis", "本人访谈/试用反馈"),
    ("A2", "用户愿意用一句话交付一个可验收成果", "hypothesis", "三句话内完成率"),
    ("A3", "隔离环境产物足以支撑首次方向判断", "hypothesis", "本人是否接受 v1"),
    ("A4", "先出假设版 BP 再迭代，优于先追求完整", "hypothesis", "v2 修订次数与采纳率"),
]


def _esc(x: Any) -> str:
    return html.escape(str(x if x is not None else ""))


def render_bp(title: str, goals: List[str], criteria: List[Dict[str, Any]],
              recommended: Dict[str, Any], risks: List[str],
              non_goals: List[str], constraints: List[str],
              feedback: str, version: str, source_ref: str) -> str:
    goals = [str(g) for g in (goals or [])]
    criteria = criteria or []
    risks = [str(r) for r in (risks or [])]
    non_goals = [str(x) for x in (non_goals or [])]
    constraints = [str(x) for x in (constraints or [])]
    fb = str(feedback or "").strip()
    fb_text = _esc(fb) if fb else "（首版，暂无修订理由；v2 起会记录本人反馈）"
    fb_attr = _esc(fb) if fb else "（首版，暂无返工反馈）"

    assumptions = "".join(
        "<tr><td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td></tr>"
        % (_esc(code), _esc(text), _esc(status), _esc(verify))
        for code, text, status, verify in DEFAULT_ASSUMPTIONS)
    goal_items = "".join("<li>%s</li>" % _esc(g) for g in goals) or "<li>（未填）</li>"
    crit_items = "".join(
        "<li>%s —— 验证：%s</li>" % (_esc(sc.get("statement")), _esc(sc.get("verify_method")))
        for sc in criteria) or "<li>（未填）</li>"
    risk_items = "".join("<li>%s</li>" % _esc(r) for r in risks) or "<li>（未填）</li>"
    non_goal_items = "".join("<li>%s</li>" % _esc(x) for x in non_goals) or "<li>（无）</li>"
    constraint_items = "".join("<li>%s</li>" % _esc(x) for x in constraints) or "<li>（无）</li>"
    person = goals[0] if goals else title

    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>%s</title><style>"
        "body{max-width:860px;margin:0 auto;padding:22px 18px 60px;font:15px/1.75 system-ui,\"Microsoft YaHei\",sans-serif;color:#173a4d}"
        "h1{font-size:24px;margin:0 0 6px}h2{font-size:17px;margin:22px 0 6px;color:#0e7490;border-left:4px solid #0e7490;padding-left:8px}"
        ".meta{color:#5a6f83;font-size:12.5px}table{border-collapse:collapse;width:100%%;font-size:13.5px}"
        "th,td{border:1px solid #dbe5ee;padding:6px 8px;text-align:left;vertical-align:top}th{background:#f4f9fb}"
        ".tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;background:#faf3e6;color:#936224;font-weight:700}"
        ".note{background:#faf3e6;border-left:4px solid #936224;padding:8px 12px;border-radius:6px;font-size:12.5px}"
        "</style></head><body data-artifact=\"%s\">"
        "<h1>%s</h1><p class=\"meta\">商业计划 · 隔离环境生成 · 版本 %s · 生成器 os_bp（规则版）</p>"
        "<div class=\"note\">本文件由一句话 + 需求冻结 + 推荐卡生成：<b>不编造外部事实与数字</b>；"
        "带 <span class=\"tag\">假设</span> 的条目全部待验证；外部证据：无。</div>"
        "<h2>一句话概要</h2><p>%s</p>"
        "<h2>目标与用户</h2><ul>%s</ul>"
        "<h2>价值主张</h2><p><b>%s</b>：%s</p>"
        "<h2>假设台账（全部待验证）</h2>"
        "<table><tr><th>编号</th><th>假设</th><th>类型</th><th>验证方式</th></tr>%s</table>"
        "<h2>依据与引用（三值规则）</h2><ul>"
        "<li><b>事实</b>（fact）：本人原话 —— 来源 <code>%s</code></li>"
        "<li><b>假设</b>（hypothesis）：上表 A1–A4，待本人验证</li>"
        "<li><b>推断</b>（inference）：无（首版不做外部推断，避免把推断当事实）</li>"
        "</ul>"
        "<h2>风险</h2><ul>%s</ul>"
        "<h2>不做（边界）</h2><ul>%s</ul>"
        "<h2>约束</h2><ul>%s</ul>"
        "<h2>里程碑</h2><ol><li>本人通读 v1，说\"接受\"或\"这里改\"</li>"
        "<li>按反馈生成 v2（修订理由写在本节下方）</li><li>拿到 3 条真实反馈后再谈投入/对外</li></ol>"
        "<h2>验收标准</h2><ul>%s</ul>"
        "<h2>修订理由</h2><p data-feedback=\"%s\">%s</p>"
        "<h2>撤回说明</h2><p>删除本目录即回到执行前；指纹见同目录 test.log；事件与授权保留在同一本账。</p>"
        "</body></html>"
        % (_esc(title), _esc(version), _esc(title), _esc(version), _esc(person),
           goal_items, _esc(recommended.get("option")), _esc(recommended.get("why")),
           assumptions, _esc(source_ref), risk_items, non_goal_items,
           constraint_items, crit_items, fb_attr, fb_text))
