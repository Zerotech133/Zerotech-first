# -*- coding: utf-8 -*-
r"""把零界既有审计事件导出为 OTel GenAI 形态 JSONL（最小可观测闭环，H0-1）。

输入（只读，按需）：
  - portal/data/dev_events.jsonl（开发事件：kind/task/agent/model/detail...）
  - work/tianfeng_dispatch.jsonl（派单台账：to/task/status...）

输出：每行一个 span 的 JSONL（含 trace_id/span_id/name/startUnixNano/attributes），
可直接被 Langfuse/OTel Collector 或自研看板消费；不引第三方依赖、不发网络。

用法：
  python -X utf8 tools/agent_obs_export.py --limit 200 --out C:\Temp\otel_genai_spans.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
def _prefer_live(rel):
    """优先读主仓(生产)路径；worktree 下也能导出真实事件。"""
    live = Path(r"C:\aios") / rel
    return live if live.is_file() else (REPO / rel)


DEFAULT_EVENTS = _prefer_live("portal/data/dev_events.jsonl")
DEFAULT_DISPATCH = _prefer_live("work/tianfeng_dispatch.jsonl")


def _trace_id(day: str) -> str:
    return hashlib.sha256(("zero:" + day).encode("utf-8")).hexdigest()[:32]


def _span_id(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _ns(ts: str):
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1e9)
    except Exception:  # noqa: BLE001
        return int(datetime.now().timestamp() * 1e9)


def event_span(ev: dict, idx: int) -> dict:
    day = str(ev.get("day") or str(ev.get("ts", ""))[:10] or "unknown")
    kind = str(ev.get("kind") or "event")
    task = str(ev.get("task") or ev.get("agent") or "-")
    attrs = {
        "gen_ai.operation.name": kind,
        "gen_ai.agent.name": str(ev.get("agent") or task),
        "gen_ai.request.model": str(ev.get("model") or ""),
        "zero.ide": str(ev.get("ide") or ""),
        "zero.task": task,
        "zero.day": day,
        "zero.detail": str(ev.get("detail") or "")[:500],
    }
    return {
        "trace_id": _trace_id(day),
        "span_id": _span_id("%s:%s:%s:%s" % (day, kind, task, ev.get("ts"))),
        "name": "%s:%s" % (kind, task),
        "kind": "INTERNAL",
        "startUnixNano": _ns(str(ev.get("ts") or "")),
        "attributes": {k: v for k, v in attrs.items() if v != ""},
    }


def dispatch_span(row: dict) -> dict:
    day = str(row.get("at", ""))[:10] or "unknown"
    attrs = {
        "gen_ai.operation.name": "dispatch",
        "zero.dispatch.to": str(row.get("to") or ""),
        "zero.dispatch.status": str(row.get("status") or ""),
        "zero.task": str(row.get("task") or "")[:300],
        "zero.day": day,
        "zero.detail": str(row.get("acceptance") or "")[:300],
    }
    return {
        "trace_id": _trace_id(day),
        "span_id": _span_id("dispatch:%s:%s" % (row.get("id"), row.get("at"))),
        "name": "dispatch:%s" % (row.get("status") or "-"),
        "kind": "INTERNAL",
        "startUnixNano": _ns(str(row.get("at") or "")),
        "attributes": {k: v for k, v in attrs.items() if v != ""},
    }


def _tail(path: Path, limit: int):
    if limit <= 0 or not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-limit:]


def export(events_path=DEFAULT_EVENTS, dispatch_path=DEFAULT_DISPATCH, limit=200):
    spans = []
    for i, ln in enumerate(_tail(Path(events_path), limit)):
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        spans.append(event_span(ev, i))
    for ln in _tail(Path(dispatch_path), limit // 4 or 10):
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        spans.append(dispatch_span(row))
    return spans


def main(argv=None):
    ap = argparse.ArgumentParser(description="导出 OTel GenAI 形态 span JSONL（只读）")
    ap.add_argument("--events", default=str(DEFAULT_EVENTS))
    ap.add_argument("--dispatch", default=str(DEFAULT_DISPATCH))
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--out", default=str(Path(r"C:\Temp\otel_genai_spans.jsonl")))
    a = ap.parse_args(argv)
    spans = export(Path(a.events), Path(a.dispatch), a.limit)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for sp in spans:
            f.write(json.dumps(sp, ensure_ascii=False) + "\n")
    print("spans=%d out=%s" % (len(spans), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
