# -*- coding: utf-8 -*-
"""O6 P1：从现有 traces.sqlite **只读派生** Run/Event 契约视图。

- 不改 trace 表、不写运行时；只读打开 sqlite（?mode=ro）并输出 JSONL；
- 每个 trace_id 派生一个 Run；每个 span 派生一个 Event（类型映射见下）；
- 输出：runs.jsonl / events.jsonl / summary.json 到 --out 目录。

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
        except ValueError:
            pass
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
        except (TypeError, ValueError):
            pass
    return out or None


def derive_rows(rows):
    """rows: [(trace_id, span_id, name, kind, start_ts, end_ts, status, attrs_json)]"""
    runs = {}
    events = []
    for trace_id, span_id, name, kind, start_ts, end_ts, status, attrs_json in rows:
        attrs = _attrs(attrs_json)
        run = runs.get(trace_id)
        if run is None:
            runs[trace_id] = {
                "contract_version": CONTRACT_VERSION,
                "run_id": "run_" + str(trace_id),
                "conversation_id": None,
                "agent": {
                    "name": str(attrs.get("zhaoyun.agent") or attrs.get("service.name") or "unknown"),
                    "runtime_id": str(attrs.get("zhaoyun.runtime") or "agent_platform"),
                },
                "kind": "chat" if str(kind) == "llm" else ("tool" if str(kind) == "tool" else "task"),
                "status": _status(status),
                "started_at": parse_ts(start_ts),
                "ended_at": parse_ts(end_ts),
                "input": {"summary": str(name or "")},
                "output": None,
                "error": None,
                "trace_id": str(trace_id),
                "usage": _usage(attrs),
                "artifacts": [],
                "meta": {"source": "trace_derived"},
            }
            run = runs[trace_id]
        else:
            # 同 trace 多 span：状态取最差，时间窗扩展，usage 累加
            if _status(status) == "failed":
                run["status"] = "failed"
            for key, val in (("started_at", parse_ts(start_ts)), ("ended_at", parse_ts(end_ts))):
                if val and (not run.get(key) or val < run[key] if key == "started_at" else val > run[key]):
                    run[key] = val
            u = _usage(attrs)
            if u:
                run["usage"] = run.get("usage") or {}
                for k, v in u.items():
                    run["usage"][k] = round((run["usage"].get(k) or 0) + v, 6) if isinstance(v, float) else (run["usage"].get(k) or 0) + v
        if _status(status) == "failed":
            run["error"] = {"code": "span_error", "message": str(attrs.get("error") or "")[:500],
                            "retryable": None}
        events.append({
            "contract_version": CONTRACT_VERSION,
            "event_id": "evt_" + str(span_id),
            "run_id": "run_" + str(trace_id),
            "seq": len([e for e in events if e["run_id"] == "run_" + str(trace_id)]),
            "type": EVENT_TYPE.get(str(kind), "run.started"),
            "ts": parse_ts(start_ts),
            "payload": {"span_name": str(name or ""), "kind": str(kind or ""),
                        "status": str(status or "")},
            "trace_id": str(trace_id),
            "span_id": str(span_id),
        })
    return list(runs.values()), events


def read_traces(path, limit=None):
    con = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    try:
        cur = con.cursor()
        q = "select trace_id, span_id, name, kind, start_ts, end_ts, status, attrs_json from spans order by start_ts"
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
    summary = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "runs": len(runs),
        "events": len(events),
        "status_counts": {s: sum(1 for r in runs if r["status"] == s)
                          for s in ("succeeded", "failed", "running")},
    }
    with io.open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8", newline="") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def validate(runs, events):
    errors = []
    required_run = ("contract_version", "run_id", "agent", "kind", "status", "started_at",
                    "input", "trace_id")
    for r in runs:
        for k in required_run:
            if k not in r:
                errors.append("run 缺字段 %s (%s)" % (k, r.get("run_id")))
        if r.get("status") not in ("pending", "running", "succeeded", "failed", "cancelled", "timeout"):
            errors.append("run.status 非法: %s" % r.get("status"))
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
    ap = argparse.ArgumentParser(description="O6 P1：从 traces 只读派生 Run/Event 契约视图")
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
        print("run contract view: runs=%d events=%d -> %s" % (len(runs), len(events), args.out))
        print("  status:", summary["status_counts"], "validation_errors=", len(errors))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
