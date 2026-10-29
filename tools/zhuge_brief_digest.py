# -*- coding: utf-8 -*-
"""诸葛亮晨报/晚报两期摘要器（纯格式化，不取数、不发送）。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

MAX_CHARS = 1800
_LINK = "http://127.0.0.1:8734/os-entry"
_TASK_PREFIX = "计划任务失败必响："


def _clip(text: str, n: int = 180) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _short_reason(item: dict) -> str:
    """去掉内部 id/source/as_of 噪声，只留人话。"""
    reason = _clip(item.get("reason") or "")
    reason = re.sub(r"（source=[^）]*）", "", reason)
    reason = re.sub(r"\s+", " ", reason).strip()
    return reason


def _headline(item: dict, n: int = 60) -> str:
    reason = str(item.get("reason") or "")
    m = re.search(r"《([^》]+)》", reason)
    return _clip(m.group(1) if m else reason, n)


def _hour(payload: dict) -> int:
    raw = str(payload.get("generated_at") or "")
    m = re.search(r"T(\d{2}):", raw)
    return int(m.group(1)) if m else 9


def _edition(payload: dict, requested: str = "auto") -> str:
    if requested in ("morning", "evening"):
        return requested
    return "morning" if _hour(payload) < 12 else "evening"


def _date_label(payload: dict) -> str:
    day = str(payload.get("business_day") or "")
    raw = str(payload.get("generated_at") or "")
    week = ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        week = "周" + "一二三四五六日"[dt.weekday()]
    except ValueError:
        pass
    return (day + ("（%s）" % week if week else "")).strip()


def _p4_groups(items: list[dict]) -> dict:
    p4 = [x for x in items if int(x.get("priority") or 0) == 4]
    families: dict[str, int] = {}
    dates: list[str] = []
    for x in p4:
        reason = str(x.get("reason") or "")
        m = re.search(r"《([^》]+)》", reason)
        title = m.group(1) if m else reason
        if title.startswith(_TASK_PREFIX):
            title = title[len(_TASK_PREFIX):]
        title = re.sub(r"（20\d{2}-\d{2}-\d{2}）", "", title).strip()
        families[title] = families.get(title, 0) + 1
        dm = re.search(r"（(20\d{2}-\d{2}-\d{2})）", reason)
        if dm:
            dates.append(dm.group(1))
    top = sorted(families.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return {"count": len(p4), "families": top, "oldest": min(dates) if dates else ""}


def _group_p3(items: list[dict]) -> list[str]:
    p3 = [x for x in items if int(x.get("priority") or 0) == 3
          and str(x.get("id") or "").startswith("notify:decision")]
    groups: dict[str, int] = {}
    for x in p3:
        reason = str(x.get("reason") or "")
        m = re.search(r"《([^》]+)》", reason)
        key = m.group(1) if m else reason
        key = re.sub(r"\s+", " ", key).strip()
        groups[key] = groups.get(key, 0) + 1
    out = []
    for key, n in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))[:3]:
        out.append(("《%s》×%d" % (key, n)) if n > 1 else "《%s》" % key)
    return out


def build_digest(payload: dict, edition: str = "auto") -> dict:
    items = [x for x in (payload.get("suggestions") or []) if isinstance(x, dict)]
    ed = _edition(payload, edition)
    top = sorted(items, key=lambda x: (int(x.get("priority") or 9),
                                       str(x.get("id") or "")))[:8]
    p1p2 = [x for x in top if int(x.get("priority") or 9) <= 2]
    p4 = _p4_groups(items)
    p3 = _group_p3(items)
    title = "诸葛亮 · 晨报" if ed == "morning" else "诸葛亮 · 晚报"
    lines: list[str] = [
        "# %s" % title,
        "%s｜规则引擎 · 未调模型" % _date_label(payload),
        "",
    ]
    head = "今日必看" if ed == "morning" else "今日异常"
    lines.append("**%s**" % head)
    if p1p2:
        for i, x in enumerate(p1p2[:4], 1):
            lines.append("%d. [P%s] %s" % (i, x.get("priority"), _short_reason(x)))
        if len(p1p2) > 4:
            lines.append("（其余 %d 条 P1/P2 已折叠）" % (len(p1p2) - 4))
    else:
        lines.append("- 无 P1/P2 异常。")
    lines.append("")

    lines.append("**待你确认**" if ed == "morning" else "**未闭环**")
    if p3:
        lines.append("- " + "；".join(p3) + " 待确认")
        lines.append("- 处理方式：需要处理时回“确认/忽略”；不处理不影响其他建议。")
    else:
        lines.append("- 无待确认通知。")
    lines.append("")

    lines.append("**研发在制**")
    if p4["count"]:
        fam = "、".join("%s×%d" % (k, v) for k, v in p4["families"])
        oldest = "；最老 %s" % p4["oldest"] if p4["oldest"] else ""
        lines.append("- %d 项进行中；主要族：%s%s。" % (p4["count"], fam, oldest))
        lines.append("- 建议按 owner 批量清理，不在简报逐条展开。")
    else:
        lines.append("- 无在制研发单。")
    lines.append("")

    lines.append("**今天先做**" if ed == "morning" else "**明天先看**")
    actions: list[str] = []
    if p1p2:
        actions.append("先核对「%s」这条最高优先级。" % _headline(p1p2[0], 50))
    if p3:
        actions.append("抽 1 分钟处理待确认通知；不确认不阻塞。")
    if p4["count"]:
        actions.append("研发在制按 owner 清理（当前 %d 项）。" % p4["count"])
    actions.append("完整建议面：%s" % _LINK)
    for i, a in enumerate(actions[:3], 1):
        lines.append("%d. %s" % (i, a))
    lines.append("")
    lines.append("> 只读取数｜不自动派单｜建议采纳/审批仍走人工")
    text = "\n".join(lines).strip()
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 1] + "…"
    counts = {}
    for x in items:
        p = "P%s" % x.get("priority")
        counts[p] = counts.get(p, 0) + 1
    return {
        "ok": True,
        "edition": ed,
        "title": title,
        "text": text,
        "chars": len(text),
        "counts": counts,
        "collapsed_p4": p4["count"],
        "threads": len(p3),
    }


def load_payload(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data.get("ok"):
        raise ValueError("快照 ok=false，拒绝推送")
    if not (data.get("suggestions") or []):
        raise ValueError("快照 suggestions 为空")
    return data


def find_json_for(md_path: Path) -> Path | None:
    cand = Path(md_path).with_suffix(".json")
    return cand if cand.is_file() else None
