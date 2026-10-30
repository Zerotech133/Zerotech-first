# -*- coding: utf-8 -*-
"""P3.1 跟进计划：试用客户 D+1/D+5/D+7 跟进文案（邮件/飞书），可导出。

只生成计划与文案，不自动发送；需要发送时由人工或外部任务执行（避免误发）。
"""
import os
import time
from typing import Any, Dict, List, Optional

from . import audit, tenants

STAGES = [
    (1, "首日回访", "欢迎使用零界个股分析",
     "您好，感谢试用！建议先跑一只您熟悉的股票（如 600337.SH），看看报告结构是否符合研究习惯；如需演示或答疑，回复本消息即可。"),
    (5, "使用提醒", "试试观察池批量分析",
     "试用过半：可把 3-5 只票加入 watchlist 批量跑，或直接用客户门户生成报告；需要我把您关注的行业做成案例吗？"),
    (7, "到期转化", "试用到期与转正方案",
     "您的 7 天试用即将到期。转正后可保留报告历史、提升配额并解锁企业版功能（多租户/审计导出/私有化部署）。"),
]


def plan(path: Optional[str] = None, db: Optional[str] = None, now: Optional[float] = None) -> List[Dict[str, Any]]:
    now = now or time.time()
    # 渠道信息来自审计（trial_started 的 detail.channel）
    chan = {}
    for r in audit.tail(100000, path=path):
        if str(r.get("action") or "") == "trial_started":
            t = r.get("tenant") or ""
            d = r.get("detail") or {}
            if t:
                chan[t] = d.get("channel") or chan.get(t) or "direct"
    out = []
    c = tenants._db(db)
    rows = c.execute("SELECT id, created FROM tenants WHERE id LIKE 'trial-%'").fetchall()
    for tid, created in rows:
        created = float(created or 0)
        age = (now - created) / 86400 if created else 0
        best = None
        for day, stage, subject, body in STAGES:
            if age >= day:
                best = (stage, subject, body)
        if best:
            stage, subject, body = best
            out.append({"tenant": tid, "channel": chan.get(tid, "direct"),
                        "age_days": round(age, 1), "stage": stage, "subject": subject,
                        "message": body})
    return sorted(out, key=lambda x: (-x["age_days"], x["tenant"]))


def render_md(items: List[Dict[str, Any]]) -> str:
    lines = ["# 试用客户跟进计划 · %s" % time.strftime("%F %T"), ""]
    if not items:
        lines.append("（暂无待跟进试用客户）")
    for it in items:
        lines += ["## %s（%s，%s 天）" % (it["tenant"], it["channel"], it["age_days"]), "",
                  "**阶段**：%s ｜ **主题**：%s" % (it["stage"], it["subject"]), "",
                  it["message"], ""]
    return "\n".join(lines)


def export(out_path: str, **kw) -> Dict[str, Any]:
    items = plan(**kw)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    open(out_path, "w", encoding="utf-8").write(render_md(items))
    return {"path": out_path, "count": len(items)}
