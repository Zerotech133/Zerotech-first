# -*- coding: utf-8 -*-
"""WO-OS-04 诸葛亮建议引擎（规则版 v1）
纯函数、零副作用：不调模型、不写库、不派单、不发请求。
输入：六源只读投影原始 dict + 观察时钟；输出：结构化"今日建议草案"。
每条建议必须带 source 与 as_of（生产者时点，未知为 None）；
缺源/坏源显式进 gaps（"待核"开头），绝不把接口可达当业务健康。
依赖：WO-OS-03 增强后的 os_integrated_projection 语义（blocked=1/overdue=1/今日到期=2）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BEIJING = timezone(timedelta(hours=8))
ENGINE = "rule-based"
SOURCES = ("os", "home", "alerts", "notify", "tasks", "capability")


def _beijing_day(clock: datetime) -> str:
    return clock.astimezone(BEIJING).strftime("%Y-%m-%d")


def _parse_as_of(value: Any) -> Optional[datetime]:
    """解析生产者时点；仅接受带时区/可无歧义解析的字符串，失败返回 None。"""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING)
    return parsed


def _freshness_bucket(as_of: Optional[datetime], clock: datetime) -> str:
    if as_of is None:
        return "unknown"
    age_hours = (clock - as_of).total_seconds() / 3600
    if age_hours <= 48:
        return "ok"
    if age_hours <= 168:
        return "stale"
    return "unknown"


def _suggestion(sid: str, domain: str, priority: int, reason: str,
                source: str, as_of: Optional[datetime]) -> Dict[str, Any]:
    return {
        "id": sid,
        "domain": domain,
        "priority": priority,
        "reason": reason,
        "source": source,
        "as_of": as_of.isoformat() if as_of else None,
        "confidence": ENGINE,
    }


def build_brief(raw: Dict[str, Any], clock: datetime) -> Dict[str, Any]:
    """生成建议草案。clock 必须是 tz-aware datetime；坏输入抛 ValueError/TypeError。"""
    if not isinstance(clock, datetime):
        raise TypeError("clock 必须是 datetime")
    if clock.tzinfo is None:
        raise ValueError("clock 必须带时区")
    if not isinstance(raw, dict):
        raise TypeError("raw 必须是 dict")

    suggestions: List[Dict[str, Any]] = []
    gaps: List[Dict[str, str]] = []
    today = _beijing_day(clock)

    def usable(key: str) -> bool:
        entry = raw.get(key)
        ok = isinstance(entry, dict) and entry.get("ok") is not False
        if not ok:
            gaps.append({
                "source": key,
                "note": "待核：该来源缺失或显式 ok:false，不可推断无事。",
            })
        return ok

    # notify：blocked 优先级 1；review 优先级 3（非本人审批）
    if usable("notify"):
        for idx, row in enumerate(raw["notify"].get("items") or []):
            if not isinstance(row, dict):
                continue
            state = row.get("state")
            if state == "blocked":
                suggestions.append(_suggestion(
                    f"notify:{row.get('id', idx)}", "通知", 1,
                    f"阻断通知《{row.get('title', '未命名')}》需优先核对：{row.get('reason', '原因未提供')}",
                    "/api/notify/queue", _parse_as_of(row.get("created_at"))))
            elif state == "review":
                suggestions.append(_suggestion(
                    f"notify:{row.get('id', idx)}", "通知", 3,
                    f"通知《{row.get('title', '未命名')}》待确认（非待本人审批）",
                    "/api/notify/queue", _parse_as_of(row.get("created_at"))))

    # tasks：overdue 优先级 1；今日到期优先级 2；其余 open/doing 优先级 4
    if usable("tasks"):
        for idx, row in enumerate(raw["tasks"].get("tasks") or []):
            if not isinstance(row, dict):
                continue
            status = row.get("status")
            if status not in ("open", "doing"):
                continue
            sid = f"task:{row.get('task_id', idx)}"
            due = row.get("due")
            due = due[:10] if isinstance(due, str) else None
            if due and due < today:
                suggestions.append(_suggestion(
                    sid, "研发", 1,
                    f"工单《{row.get('title', '未命名')}》已逾期至 {due}，需核对是否阻塞",
                    "/api/org/tasks", _parse_as_of(row.get("created_at"))))
            elif due == today:
                suggestions.append(_suggestion(
                    sid, "研发", 2,
                    f"工单《{row.get('title', '未命名')}》今日到期，建议先看",
                    "/api/org/tasks", _parse_as_of(row.get("created_at"))))
            else:
                suggestions.append(_suggestion(
                    sid, "研发", 4,
                    f"工单《{row.get('title', '未命名')}》进行中（{status}）",
                    "/api/org/tasks", _parse_as_of(row.get("created_at"))))

    # alerts：非 ok 源优先级 2；ok 但新鲜度非 ok 优先级 3
    if usable("alerts"):
        health = raw["alerts"].get("source_health")
        sources = health.get("sources") if isinstance(health, dict) else None
        if not isinstance(sources, dict):
            gaps.append({"source": "alerts",
                         "note": "待核：source_health 结构不符，逐源状态未知。"})
        else:
            for name, row in sources.items():
                if not isinstance(row, dict):
                    continue
                as_of = _parse_as_of(row.get("as_of"))
                bucket = _freshness_bucket(as_of, clock)
                sid = f"alert:{name}"
                if row.get("status") != "ok":
                    suggestions.append(_suggestion(
                        sid, "情报", 2,
                        f"信号源 {name} 状态 {row.get('status', 'unknown')}，需回原账本核对",
                        "/api/alerts", as_of))
                elif bucket != "ok":
                    label = "偏旧(>48h)" if bucket == "stale" else "未知/过期"
                    suggestions.append(_suggestion(
                        sid, "情报", 3,
                        f"信号源 {name} 数据新鲜度 {label}",
                        "/api/alerts", as_of))

    # os / home / capability：当前无个人事实可提炼，仅登记覆盖，不产建议
    for key in ("os", "home", "capability"):
        usable(key)

    suggestions.sort(key=lambda s: s["priority"])
    seen = set()
    deduped = []
    for s in suggestions:
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        deduped.append(s)

    return {
        "engine": ENGINE,
        "model_invoked": False,
        "dispatched": False,
        "generated_at": clock.isoformat(),
        "business_day": today,
        "suggestions": deduped,
        "gaps": gaps,
        "note": "规则归集草案，非诸葛亮模型推理；不派单、不代替本人审批；缺源即待核。",
    }
