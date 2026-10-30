# -*- coding: utf-8 -*-
"""O6 P1/P2d：从现有 traces.sqlite **只读派生** Run/Event 契约视图（支持任务级聚合）。

- 只读打开 sqlite（?mode=ro），不改 trace 表/运行时；
- **任务级**：同 `run_id` 的多个 span（attempt）聚合成一个 Run（P2b 起写入 run_id）；
- **兼容旧数据**：`run_id` 列不存在/为 NULL 时，按 trace_id 派生 attempt 级 Run；
- 输出：runs.jsonl / events.jsonl / summary.json（含任务级/attempt 级成功率）。

用法：
  python -X utf8 tools/run_contract_view.py
  python -X utf8 tools/run_contract_view.py --traces <path> --out <dir> --limit 50
  python -X utf8 tools/run_contract_view.py --json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
import sys
from datetime import datetime
import logging

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TRACES = os.path.join(REPO, "agent_platform", "data", "observability", "traces.sqlite")
DEFAULT_OUT = os.path.join(REPO, "agent_platform", "data", "dev", "run_contract")
CONTRACT_VERSION = "1.0.0"

EVENT_TYPE = {
    "llm": "llm.request",
    "tool": "tool.call",
    "agent": "run.started",
    "chain": "run.started",
}


def parse_ts(s):
    """把 trace 里的 +0800 形式规范成带冒号的 ISO8601（失败则原样返回）。"""
    s = str(s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/run_contract_view.py", exc)
    return s


def _attrs(raw):
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def _status(raw):
    s = str(raw or "").lower()
    if s in ("ok", "success", "succeeded"):
        return "succeeded"
    if s in ("error", "failed", "fail"):
        return "failed"
    return "running"


def _usage(attrs):
    out = {}
    pairs = (("gen_ai.usage.input_tokens", "prompt_tokens"),
             ("gen_ai.usage.output_tokens", "completion_tokens"),
             ("zhaoyun.cost_usd_est", "cost_usd_est"))
    for src, dst in pairs:
        v = attrs.get(src)
        if v is None:
            continue
        try:
            out[dst] = int(v) if "tokens" in dst else float(v)
        except (TypeError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/run_contract_view.py", exc)
    return out


def _merge_usage(total, add):
    if not add:
        return total
    total = dict(total or {})
    for k, v in add.items():
        total[k] = round((total.get(k) or 0) + v, 6) if isinstance(v, float) else (total.get(k) or 0) + v
    return total


def _row_run_id(row):
    return row[8] if len(row) >= 9 else None


def derive_rows(rows):
    """rows: 8-tuple 旧格式或 9-tuple（含 run_id）；返回 (runs, events)。"""
    groups = {}
    order = []
    for row in rows:
        trace_id, span_id, name, kind, start_ts, end_ts, status, attrs_json = row[:8]
        run_id = _row_run_id(row)
        key = ("run:" + str(run_id)) if run_id else ("trace:" + str(trace_id))
        if key not in groups:
            groups[key] = {
                "run_id": str(run_id) if run_id else ("run_trace_" + str(trace_id)),
                "level": "task" if run_id else "attempt",
                "spans": [],
            }
            order.append(key)
        groups[key]["spans"].append({
            "trace_id": str(trace_id), "span_id": str(span_id), "name": str(name or ""),
            "kind": str(kind or ""), "start": parse_ts(start_ts), "end": parse_ts(end_ts),
            "status": _status(status), "raw_status": str(status or ""),
            "attrs": _attrs(attrs_json),
        })

    runs, events = [], []
    for key in order:
        g = groups[key]
        spans = g["spans"]
        attrs0 = spans[0]["attrs"]
        models = [s["attrs"].get("gen_ai.request.model") for s in spans]
        agent = (attrs0.get("zhaoyun.agent") or attrs0.get("service.name")
                 or next((s["attrs"].get("zhaoyun.agent_id") for s in spans
                          if s["attrs"].get("zhaoyun.agent_id")), "unknown"))
        kinds = {s["kind"] for s in spans}
        kind = "chat" if "llm" in kinds else ("tool" if "tool" in kinds else "task")
        if len(spans) > 1:
            kind = "task"
        statuses = [s["status"] for s in spans]
        if "succeeded" in statuses:
            status = "succeeded"
        elif statuses and all(x == "failed" for x in statuses):
            status = "failed"
        else:
            status = "running"
        starts = [s["start"] for s in spans if s["start"]]
        ends = [s["end"] for s in spans if s["end"]]
        usage = {}
        for s in spans:
            usage = _merge_usage(usage, _usage(s["attrs"]))
        err = None
        if status == "failed":
            last = next((s for s in reversed(spans) if s["status"] == "failed"), spans[-1])
            err = {"code": "attempt_failed", "message": str(last["attrs"].get("error") or "")[:500],
                   "retryable": None}
        runs.append({
            "contract_version": CONTRACT_VERSION,
            "run_id": g["run_id"],
            "conversation_id": None,
            "agent": {
                "name": str(agent),
                "runtime_id": str(attrs0.get("zhaoyun.runtime") or "agent_platform"),
            },
            "kind": kind,
            "status": status,
            "started_at": min(starts) if starts else None,
            "ended_at": max(ends) if ends else None,
            "input": {"summary": spans[0]["name"]},
            "output": None,
            "error": err,
            "trace_id": spans[0]["trace_id"],
            "usage": usage or None,
            "artifacts": [],
            "meta": {
                "level": g["level"],
                "attempts": len(spans),
                "trace_ids": sorted({s["trace_id"] for s in spans}),
                "models": sorted({m for m in models if m}),
            },
        })
        for seq, s in enumerate(spans):
            events.append({
                "contract_version": CONTRACT_VERSION,
                "event_id": "evt_" + s["span_id"],
                "run_id": g["run_id"],
                "seq": seq,
                "type": EVENT_TYPE.get(s["kind"], "run.started"),
                "ts": s["start"],
                "payload": {"span_name": s["name"], "kind": s["kind"],
                            "status": s["raw_status"], "level": g["level"]},
                "trace_id": s["trace_id"],
                "span_id": s["span_id"],
            })
    return runs, events


def read_traces(path, limit=None):
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    try:
        cur = con.cursor()
        cols = [r[1] for r in cur.execute("PRAGMA table_info(spans)").fetchall()]
        rid_expr = "run_id" if "run_id" in cols else "NULL AS run_id"
        q = ("select trace_id, span_id, name, kind, start_ts, end_ts, status, attrs_json, "  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
             + rid_expr + " from spans order by start_ts")
        if limit:
            q += " limit %d" % int(limit)
        rows = cur.execute(q).fetchall()
    finally:
        con.close()
    return rows


def write_outputs(out_dir, runs, events):
    os.makedirs(out_dir, exist_ok=True)
    with io.open(os.path.join(out_dir, "runs.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for r in runs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with io.open(os.path.join(out_dir, "events.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    task = [r for r in runs if r["meta"]["level"] == "task"]
    attempt = [r for r in runs if r["meta"]["level"] == "attempt"]
    summary = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runs": len(runs),
        "events": len(events),
        "task_runs": len(task),
        "attempt_runs": len(attempt),
        "status_counts": {s: sum(1 for r in runs if r["status"] == s)
                          for s in ("succeeded", "failed", "running")},
        "task_success_rate": round(sum(1 for r in task if r["status"] == "succeeded") / len(task), 4) if task else None,
        "attempt_success_rate": round(sum(1 for r in attempt if r["status"] == "succeeded") / len(attempt), 4) if attempt else None,
        "by_kind": {k: sum(1 for r in runs if r["kind"] == k)
                    for k in sorted({r["kind"] for r in runs})},
    }
    with io.open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8", newline="") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def validate(runs, events):
    errors = []
    required_run = ("contract_version", "run_id", "agent", "kind", "status", "started_at",
                    "input", "trace_id", "meta")
    for r in runs:
        for k in required_run:
            if k not in r:
                errors.append("run 缺字段 %s (%s)" % (k, r.get("run_id")))
        if r.get("status") not in ("pending", "running", "succeeded", "failed", "cancelled", "timeout"):
            errors.append("run.status 非法: %s" % r.get("status"))
        if (r.get("meta") or {}).get("level") not in ("task", "attempt"):
            errors.append("run.meta.level 非法: %s" % r.get("run_id"))
    required_ev = ("contract_version", "event_id", "run_id", "seq", "type", "ts", "payload")
    for e in events:
        for k in required_ev:
            if k not in e:
                errors.append("event 缺字段 %s (%s)" % (k, e.get("event_id")))
        if e.get("type") not in ("run.started", "llm.request", "llm.response", "tool.call",
                                 "tool.result", "run.finished", "error"):
            errors.append("event.type 非法: %s" % e.get("type"))
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser(description="O6：从 traces 只读派生 Run/Event 契约视图（任务级聚合）")
    ap.add_argument("--traces", default=DEFAULT_TRACES)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.exists(args.traces):
        print("traces 不存在：%s" % args.traces, file=sys.stderr)
        return 2
    runs, events = derive_rows(read_traces(args.traces, args.limit))
    errors = validate(runs, events)
    summary = write_outputs(args.out, runs, events)
    summary["validation_errors"] = errors
    summary["ok"] = not errors
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("run contract view: runs=%d (task=%d attempt=%d) events=%d -> %s" % (
            len(runs), summary["task_runs"], summary["attempt_runs"], len(events), args.out))
        print("  task_success_rate=%s attempt_success_rate=%s validation_errors=%d" % (
            summary["task_success_rate"], summary["attempt_success_rate"], len(errors)))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
