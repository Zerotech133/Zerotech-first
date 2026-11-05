# -*- coding: utf-8 -*-
"""WO-OS-04 诸葛亮建议引擎（规则版 v1）；WO-OS-20 扩第七源 pushflow。
纯函数、零副作用：不调模型、不写库、不派单、不发请求。
输入：七源只读投影原始 dict + 观察时钟；输出：结构化"今日建议草案"。
SOURCES = os / home / alerts / notify / tasks / capability / pushflow
（pushflow = 当日通知投递成败统计，用于暴露"当日实际失败率"，与"历史失败簇"划清口径）。
每条建议必须带 source 与 as_of（生产者时点，未知为 None）；
缺源/坏源显式进 gaps（"待核"开头），绝不把接口可达当业务健康。
依赖：WO-OS-03 增强后的 os_integrated_projection 语义（blocked=1/overdue=1/今日到期=2）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BEIJING = timezone(timedelta(hours=8))
ENGINE = "rule-based"
SOURCES = ("os", "home", "alerts", "notify", "tasks", "capability", "pushflow")


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


def _pct_text(rate: float) -> str:
    """失败率转百分比文本：0.55 → '55'，0.075 → '7.5'（不补无意义小数）。"""
    text = "%.1f" % (rate * 100)
    return text[:-2] if text.endswith(".0") else text


def _count_text(value: Any) -> str:
    """计数文本：非整数一律 '?'（缺字段不猜 0）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return "?"
    return str(value)


def _trend_text(recent: Any) -> str:
    """近 4 日失败率趋势（WO-OS-25）：与前一有效日比较，±5pp 内视为持平。

    目的：单看"今日 24%"读者无法判断在恶化还是恢复。此处在建议里直接给方向。
    缺 recent / 不足两日 / 字段异常 → 返回空串（不给趋势，也不猜）。
    """
    if not isinstance(recent, list) or len(recent) < 2:
        return ""
    today = recent[-1]
    prev = recent[-2]
    if not isinstance(today, dict) or not isinstance(prev, dict):
        return ""
    try:
        cur = float(today.get("fail_rate"))
        before = float(prev.get("fail_rate"))
    except (TypeError, ValueError):
        return ""
    if not (0 <= cur <= 1 and 0 <= before <= 1):
        return ""
    delta = cur - before
    # WO-OS-25-FIX：先量化到 0.01 个百分点再比较。直接比 float 会因二进制表示
    # 让同为 5pp 的变化随机落两侧（0.05 精确 vs 0.25-0.20 有误差），实测 40 组
    # 5pp 边界里 20 组判非持平、20 组判持平，属不可复现的判定。
    pp = round(delta * 100, 4)
    if pp < -5:
        verdict = "较前一日改善"
    elif pp > 5:
        verdict = "较前一日恶化"
    else:
        verdict = "与前一日基本持平"
    return "；%s（前一日 %s%%，变化 %+.1f 个百分点）" % (
        verdict, _pct_text(before), pp)


def _pushflow_rate_or_none(stats: Dict[str, Any]) -> Optional[float]:
    """取当日真实投递失败率；口径不成立时返回 None（交调用方记待核）。

    WO-OS-22 自洽校验（防静默退回旧口径）：
      1. fail_rate 必须是数值；
      2. attempted 必须显式给出（旧载荷只有 today，无法区分抑制与失败）；
      3. total - attempted 必须等于 policy_suppressed（账本行数自洽）。
    任一不成立 → None：宁可待核，也不产出含策略抑制的假失败率。
    """
    raw_rate = stats.get("fail_rate")
    try:
        rate = float(raw_rate)
    except (TypeError, ValueError):
        return None
    if rate != rate or rate < 0 or rate > 1:  # NaN / 越界一律不可信
        return None
    attempted = stats.get("attempted")
    if isinstance(attempted, bool) or not isinstance(attempted, int) or attempted <= 0:
        return None  # 缺 attempted 或为 0：口径不成立
    total = stats.get("today")
    if isinstance(total, bool) or not isinstance(total, int):
        return None
    suppressed = stats.get("policy_suppressed")
    suppressed = 0 if suppressed is None else suppressed
    if isinstance(suppressed, bool) or not isinstance(suppressed, int) or suppressed < 0:
        return None
    if total - attempted != suppressed:
        return None  # 行数与尝试数对不上：账本口径不可信
    return rate


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


def _decision_suggestion_map() -> Dict[str, str]:
    """决策记录声明的 suggestion_ids → decision_id（精确映射，不猜）。

    只读 `work/p1/decisions/decisions.jsonl`；可在测试中用
    ZHUGE_DECISION_REPO 指向临时仓库。
    """
    root = os.environ.get("ZHUGE_DECISION_REPO", "").strip()
    base = Path(root) if root else Path(__file__).resolve().parent.parent
    path = base / "work" / "p1" / "decisions" / "decisions.jsonl"
    if not path.is_file():
        return {}
    records: Dict[str, Dict[str, Any]] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("event")
            rid = ev.get("id")
            if kind == "created" and isinstance(ev.get("record"), dict):
                rec = ev["record"]
                if rec.get("id"):
                    records[rec["id"]] = dict(rec)
            elif kind == "updated" and rid in records and "suggestion_ids" in ev:
                records[rid]["suggestion_ids"] = ev["suggestion_ids"]
    except OSError:
        return {}
    mapping: Dict[str, str] = {}
    for rid, rec in records.items():
        for sid in rec.get("suggestion_ids") or []:
            sid = str(sid).strip()
            if sid:
                mapping[sid] = rid
    return mapping


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

    # notify：blocked 按「失败类型」聚合（WO-OS-23 降噪），review 优先级 3。
    #
    # 背景：notify 的 blocked 是 T+7 滚窗的历史失败簇，同一个通道故障会展开成
    # 十几条（实测 21 条全是"发送流水 ok=0"）。逐条列 P1 会让首页变成噪声墙，
    # 老田看到 22 条 P1 却无从下手。故：
    #   - 同 kind 前缀（"投递失败"类）合并成 1 条簇建议，reason 给簇大小与类型数；
    #   - 簇建议优先级降为 2（历史簇不等于当前故障；当前真实失败率由 pushflow 源
    #     以 P1-P3 分级呈现，两口径分工明确，不再互相冒充）；
    #   - deadline / 数据锚类（kind 含 'deadline'）属"到点未就绪"，保持 P1 单列；
    #   - review 仍 P3 逐条（待确认事项需逐项处理，不能聚合）。
    if usable("notify"):
        history_cluster: List[Dict[str, Any]] = []   # 历史投递失败簇成员
        for idx, row in enumerate(raw["notify"].get("items") or []):
            if not isinstance(row, dict):
                continue
            state = row.get("state")
            title = str(row.get("title") or "未命名")
            if state == "blocked":
                if "deadline" in title.lower() or "未就绪" in title:
                    suggestions.append(_suggestion(
                        f"notify:{row.get('id', idx)}", "通知", 1,
                        f"到点未就绪《{title}》需优先核对：{row.get('reason', '原因未提供')}",
                        "/api/notify/queue", _parse_as_of(row.get("created_at"))))
                else:
                    history_cluster.append(row)
            elif state == "review":
                suggestions.append(_suggestion(
                    f"notify:{row.get('id', idx)}", "通知", 3,
                    f"通知《{title}》待确认（非待本人审批）",
                    "/api/notify/queue", _parse_as_of(row.get("created_at"))))
        if history_cluster:
            kinds = set()
            latest = None
            for row in history_cluster:
                text = str(row.get("title") or "")
                # "hub 投递失败" → "hub"；"auto:send_feishu 投递失败" → "auto:send_feishu"
                kinds.add(text.replace(" 投递失败", "").strip() or "未命名")
            for row in history_cluster:
                ts = _parse_as_of(row.get("created_at"))
                if ts and (latest is None or ts > latest):
                    latest = ts
            sample = "、".join(sorted(kinds)[:3])
            more = "等" if len(kinds) > 3 else ""
            suggestions.append(_suggestion(
                "notify:history_cluster", "通知", 2,
                "近 7 天投递失败簇 %s 条，涉及 %s 类（%s%s）：这是 T+7 滚窗的历史簇，"
                "不代表此刻发不出去；当日真实失败率见另一条建议"
                % (_count_text(len(history_cluster)), _count_text(len(kinds)),
                   sample, more),
                "/api/notify/queue", latest))

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
                status = row.get("status")
                if status in ("retired", "disabled"):
                    # 2026-11-05 OPS-COUNCIL-RETIRE-20261105：已退役源不再参与
                    # 晨报建议；历史页仍可访问，避免每天重复“需回原账本核对”。
                    continue
                if status not in ("ok", "empty"):
                    suggestions.append(_suggestion(
                        sid, "情报", 2,
                        f"信号源 {name} 状态 {status or 'unknown'}，需回原账本核对",
                        "/api/alerts", as_of))
                elif bucket != "ok":
                    label = "偏旧(>48h)" if bucket == "stale" else "未知/过期"
                    suggestions.append(_suggestion(
                        sid, "情报", 3,
                        f"信号源 {name} 数据新鲜度 {label}",
                        "/api/alerts", as_of))

    # pushflow（WO-OS-20 第七源）：当日通知投递失败率分级；
    # 口径红线——只看当日账本实际成败，不拿历史失败簇冒充当前状态。
    # WO-OS-20-FIX：分母是真实投递尝试（attempted），免打扰窗口的策略抑制
    # （policy_suppressed）既不算成功也不算失败，只作为附注说明。
    # WO-OS-22：先做账本自洽校验——total 必须等于 attempted+suppressed，
    # 且必须提供 attempted；否则口径不可信（旧载荷会静默退回含抑制的分母，
    # 把 27.4% 虚报成 53.9%），一律记待核、不产失败率建议。
    if usable("pushflow"):
        stats = raw["pushflow"]
        rate = _pushflow_rate_or_none(stats)
        if rate is None:
            gaps.append({"source": "pushflow",
                         "note": "待核：fail_rate 非数值或账本行数不自洽，当日失败率未知。"})
        else:
            if rate >= 0.5:
                priority = 1
            elif rate >= 0.3:
                priority = 2
            elif rate >= 0.1:
                priority = 3
            else:
                priority = 0  # <10% 视为正常投递水平，不产建议
            if priority:
                attempted = stats.get("attempted")
                suppressed = stats.get("policy_suppressed") or 0
                tail = ("；另有 %s 条静默窗口策略抑制，不计入失败"
                        % _count_text(suppressed)) if suppressed else ""
                trend = _trend_text(stats.get("recent"))
                suggestions.append(_suggestion(
                    "pushflow:daily_rate", "通知", priority,
                    "今日真实投递失败率 %s%%（失败 %s/尝试 %s）：这是当日真实投递尝试的"
                    "失败率，不是历史失败簇%s%s；来源 pushflow.jsonl"
                    % (_pct_text(rate), _count_text(stats.get("fail_count")),
                       _count_text(attempted), trend, tail),
                    "/api/zhuge/brief.pushflow", None))

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

    # 建议→决策：只按决策记录的 suggestion_ids 精确 join；无映射不编造
    dmap = _decision_suggestion_map()
    for s in deduped:
        rid = dmap.get(str(s.get("id", "")))
        if rid:
            s["decision_id"] = rid

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
