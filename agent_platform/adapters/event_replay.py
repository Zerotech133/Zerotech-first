# -*- coding: utf-8 -*-
"""M1-③ 事件回放统一查询层（只读数据面，供门户 /api/platform/replay 与 CLI）。

设计约定（承 M0 判例）：
- 单一事实读取器：复用 event_adapter.build_report 的 envelope 记录，
  不另写读取逻辑；source_errors / canonical 校验原样透传。
- 只读：不写任何事实源；查询参数全部白名单化，无路径/SQL 注入面。
- fail-visible：源缺失/坏记录进 errors 显式上报，不静默吞掉。
- 时间语义：naive 时间不补 UTC（M0 判例）；ts 缺失记录沉底，不伪造。

GET /api/platform/replay?source=&kind=&actor=&object_id=&trace_id=
                  &since=&until=&limit=&order=
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPLAY_VERSION = "1.0.0"

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

# 白名单过滤字段 → envelope 字段（完全相等匹配；trace_id/object_id 等可空）
_FILTER_FIELDS = ("source", "kind", "actor", "object_id", "object_type",
                  "trace_id", "causation_id")

_ORDER_CHOICES = ("asc", "desc")


def _parse_bound(value: str | None) -> str | None:
    """宽松校验 since/until：接受 ISO 8601 或 'YYYY-MM-DD[ HH:MM[:SS]]'。

    返回规范化比较键（字典序比较）。**纯日期输入保持 10 字符粒度**
    （不补 00:00:00）——since 语义天然正确（≥ 当日零点），until 的
    「全天」扩展由 build_replay 依 len==10 追加 23:59:59。
    非法输入抛 ValueError（端点层转 400，不静默忽略）。
    """
    if value is None or value == "":
        return None
    text = value.strip().replace("T", " ")
    for fmt, out in (("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"),
                     ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"),
                     ("%Y-%m-%d", "%Y-%m-%d"),
                     ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"),
                     ("%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S"),
                     ("%Y/%m/%d", "%Y-%m-%d")):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime(out)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text)
        if len(text) <= 10:  # 纯日期形态的 ISO 输入
            return dt.strftime("%Y-%m-%d")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise ValueError(f"非法时间参数: {value!r}（期望 ISO 8601 或 YYYY-MM-DD[ HH:MM[:SS]]）")


def _norm_ts(ts: str | None) -> str:
    """envelope ts → 可比较键；None/'naive' 沉底为空串（不补 UTC）。"""
    if not ts:
        return ""
    return str(ts).replace("T", " ").replace("/", "-")[:19]


def build_replay(root: str | Path, *, source: str | None = None,
                 kind: str | None = None, actor: str | None = None,
                 object_id: str | None = None, object_type: str | None = None,
                 trace_id: str | None = None, causation_id: str | None = None,
                 since: str | None = None, until: str | None = None,
                 limit: int = DEFAULT_LIMIT, order: str = "desc") -> dict[str, Any]:
    _here = Path(__file__).resolve().parents[1]
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from adapters.event_adapter import build_report

    if order not in _ORDER_CHOICES:
        raise ValueError(f"order 须为 {'/'.join(_ORDER_CHOICES)}，收到 {order!r}")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError(f"limit 须为整数，收到 {limit!r}")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit 须在 1..{MAX_LIMIT}，收到 {limit}")

    since_key = _parse_bound(since)
    until_key = _parse_bound(until)
    # 纯日期边界按「当天全天」语义（until=2026-10-17 → 含 17 日 23:59:59 前）
    until_inclusive = until_key + " 23:59:59" if (until_key and len(until_key) == 10) else until_key
    filters = {}
    for name in _FILTER_FIELDS:
        value = locals().get(name)
        if value not in (None, ""):
            filters[name] = str(value)

    root = Path(root).resolve()
    report = build_report(root)

    matched = []
    for event in report.get("events") or []:
        if any(event.get(field) != want for field, want in filters.items()):
            continue
        ts_key = _norm_ts(event.get("ts"))
        if since_key and (not ts_key or ts_key < since_key):
            continue
        if until_inclusive and (not ts_key or ts_key > until_inclusive):
            continue
        matched.append(event)

    matched.sort(
        key=lambda e: (_norm_ts(e.get("ts")), str(e.get("source_id") or ""),
                       str(e.get("event_hash"))),
        reverse=(order == "desc"),
    )
    truncated = len(matched) > limit
    page = matched[:limit]

    # 时间线摘要：按 source×kind 计数（全量命中集，非当前页）
    timeline: dict[str, int] = {}
    for event in matched:
        key = f"{event.get('source') or 'unknown'}::{event.get('kind') or 'unknown'}"
        timeline[key] = timeline.get(key, 0) + 1

    return {
        "replay_version": REPLAY_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "read_only": True,
        "query": {
            "filters": filters,
            "since": since_key, "until": until_key,
            "limit": limit, "order": order,
        },
        "sources": report.get("sources"),
        "canonical_valid": report.get("canonical_valid"),
        "source_errors": report.get("source_errors"),
        "matched_total": len(matched),
        "returned": len(page),
        "truncated": truncated,
        "timeline": dict(sorted(timeline.items(), key=lambda kv: -kv[1])),
        "events": page,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1-③ 事件回放统一查询（只读）")
    ap.add_argument("--root", default=".")
    for name in _FILTER_FIELDS:
        ap.add_argument(f"--{name.replace('_', '-')}", default=None)
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--order", default="desc", choices=_ORDER_CHOICES)
    ap.add_argument("--output", default="")
    args = ap.parse_args(argv)
    try:
        replay = build_replay(
            args.root, source=args.source, kind=args.kind, actor=args.actor,
            object_id=args.object_id, object_type=args.object_type,
            trace_id=args.trace_id, causation_id=args.causation_id,
            since=args.since, until=args.until,
            limit=args.limit, order=args.order)
    except ValueError as exc:
        print(json.dumps({"error": "invalid_query", "message": str(exc)},
                         ensure_ascii=False))
        return 2
    rendered = json.dumps(replay, ensure_ascii=False, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
