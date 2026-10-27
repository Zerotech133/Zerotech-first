# -*- coding: utf-8 -*-
"""赵云可观测：最小 trace/span 存储（stdlib sqlite3，字段兼容 OTel GenAI semconv）。

设计：
  · 表 spans(trace_id, span_id, parent_span_id, name, kind, start_ts, end_ts,
    status, attrs_json, created_at)；
  · 不做异步队列：P0 直写 SQLite（WAL），单条 span 写入 <5ms；
  · 关闭开关 ZHAOYUN_TRACE=off；
  · 成本为**估算**：价格配置 data/model_prices.json（人工维护，非密钥）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
DB = PLATFORM / "data" / "observability" / "traces.sqlite"
PRICES = PLATFORM / "data" / "model_prices.json"
DEFAULT_PRICES = {
    "deepseek/deepseek-flash": {"in": 0.5, "out": 2.0},
    "deepseek/deepseek-v4-pro": {"in": 2.0, "out": 8.0},
    "ollama/qwen3.5:9b": {"in": 0.0, "out": 0.0},
}
KEEP_DAYS = 30
KEEP_SPANS = 50000


def enabled() -> bool:
    return str(os.environ.get("ZHAOYUN_TRACE") or "").strip().lower() not in (
        "off",
        "0",
        "false",
        "no",
    )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def trace_id() -> str:
    return uuid.uuid4().hex[:32]


def span_id() -> str:
    return uuid.uuid4().hex[:16]


def _connect(db: Path | None = None) -> sqlite3.Connection:
    p = Path(db) if db else DB
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS spans(
        trace_id TEXT NOT NULL, span_id TEXT NOT NULL, parent_span_id TEXT,
        name TEXT NOT NULL, kind TEXT NOT NULL,
        start_ts TEXT NOT NULL, end_ts TEXT, status TEXT,
        attrs_json TEXT, created_at TEXT NOT NULL,
        PRIMARY KEY (trace_id, span_id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trace ON spans(trace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON spans(created_at)")
    conn.commit()
    return conn


def record(
    trace: str,
    name: str,
    kind: str = "tool",
    start: float | None = None,
    end: float | None = None,
    status: str = "ok",
    attrs: dict | None = None,
    parent: str | None = None,
    db: Path | None = None,
) -> dict:
    if not enabled():
        return {"ok": False, "skipped": "trace_off"}
    sid = span_id()
    st = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start or time.time()))
    et = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(end or time.time()))
    try:
        conn = _connect(db)
        conn.execute(
            "INSERT INTO spans VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                trace,
                sid,
                parent,
                name,
                kind,
                st,
                et,
                status,
                json.dumps(attrs or {}, ensure_ascii=False),
                time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            ),
        )
        conn.commit()
        conn.close()
        return {"ok": True, "span_id": sid}
    except sqlite3.Error as e:
        return {"ok": False, "error": "sqlite:%s" % str(e)[:120]}


@contextmanager
def span(
    name: str,
    kind: str = "tool",
    attrs: dict | None = None,
    trace: str | None = None,
    parent: str | None = None,
    db: Path | None = None,
):
    """用法：with span('cap.drill','tool',{'capability':cap}) as sp: ...; sp.set('x',1)"""
    tid = trace or trace_id()
    sid = span_id()
    start = time.time()
    data = dict(attrs or {})
    opened = enabled()

    class _Span:
        trace = tid
        span_id = sid

        def set(self, key, value):
            data[key] = value

    sp = _Span()
    err = ""
    try:
        yield sp
    except Exception as e:  # noqa: BLE001
        err = "%s: %s" % (type(e).__name__, str(e)[:120])
        raise
    finally:
        if opened:
            st = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start))
            et = _now()
            try:
                conn = _connect(db)
                conn.execute(
                    "INSERT INTO spans VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        tid,
                        sid,
                        parent,
                        name,
                        kind,
                        st,
                        et,
                        "error" if err else "ok",
                        json.dumps(data, ensure_ascii=False),
                        _now(),
                    ),
                )
                conn.commit()
                conn.close()
            except sqlite3.Error:
                pass


def _prices() -> dict:
    try:
        return json.loads(PRICES.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return DEFAULT_PRICES


def estimate_cost(attrs: dict) -> float:
    """按 token 估算成本；无 token 数据返回 0.0。"""
    try:
        model = str(attrs.get("gen_ai.request.model") or "")
        pin = int(attrs.get("gen_ai.usage.input_tokens") or 0)
        pout = int(attrs.get("gen_ai.usage.output_tokens") or 0)
        price = (_prices() or {}).get(model) or {}
        return round(
            pin / 1e6 * float(price.get("in", 0))
            + pout / 1e6 * float(price.get("out", 0)),
            6,
        )
    except (TypeError, ValueError):
        return 0.0


def list_traces(limit: int = 50, db: Path | None = None) -> dict:
    try:
        conn = _connect(db)
        rows = conn.execute(
            "SELECT trace_id, COUNT(*), MIN(start_ts), MAX(end_ts), "
            "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) "
            "FROM spans GROUP BY trace_id ORDER BY MAX(created_at) DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"ok": False, "error": "sqlite:%s" % str(e)[:120]}
    items = []
    for tid, n, st, et, errs in rows:
        items.append(
            {
                "trace_id": tid,
                "spans": n,
                "start": st,
                "end": et,
                "errors": int(errs or 0),
            }
        )
    return {"ok": True, "count": len(items), "items": items}


def get_trace(trace: str, db: Path | None = None) -> dict:
    try:
        conn = _connect(db)
        rows = conn.execute(
            "SELECT span_id,parent_span_id,name,kind,start_ts,end_ts,status,attrs_json "
            "FROM spans WHERE trace_id=? ORDER BY start_ts",
            (trace,),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"ok": False, "error": "sqlite:%s" % str(e)[:120]}
    spans = []
    total = 0.0
    for sid, pid, name, kind, st, et, status, attrs in rows:
        try:
            attrs = json.loads(attrs or "{}")
        except ValueError:
            attrs = {}
        cost = estimate_cost(attrs)
        total += cost
        spans.append(
            {
                "span_id": sid,
                "parent_span_id": pid,
                "name": name,
                "kind": kind,
                "start": st,
                "end": et,
                "status": status,
                "attrs": attrs,
                "cost": cost,
            }
        )
    return {
        "ok": bool(spans),
        "trace_id": trace,
        "spans": spans,
        "cost": round(total, 6),
        "count": len(spans),
    }


def retention(
    days: int = KEEP_DAYS, max_spans: int = KEEP_SPANS, db: Path | None = None
) -> dict:
    try:
        conn = _connect(db)
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - days * 86400)
        )
        d = conn.execute("DELETE FROM spans WHERE start_ts < ?", (cutoff,)).rowcount
        n = conn.execute("SELECT COUNT(*) FROM spans").fetchone()[0]
        if n > max_spans:
            conn.execute(
                "DELETE FROM spans WHERE rowid IN "
                "(SELECT rowid FROM spans ORDER BY created_at LIMIT ?)",
                (n - max_spans,),
            )
        conn.commit()
        conn.close()
        return {"ok": True, "deleted_old": d, "remaining": n}
    except sqlite3.Error as e:
        return {"ok": False, "error": "sqlite:%s" % str(e)[:120]}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--retention", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.retention:
        print(json.dumps(retention(), ensure_ascii=False))
    else:
        print(json.dumps(list_traces(), ensure_ascii=False, indent=1))
