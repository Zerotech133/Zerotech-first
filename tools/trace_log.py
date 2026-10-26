# -*- coding: utf-8 -*-
"""observability-trace：本地 JSONL trace（工具链/时延/成本/状态）。

用法：
  python -X utf8 tools/trace_log.py --summary [--json]
  python -X utf8 tools/trace_log.py --tail 20
  python -X utf8 tools/trace_log.py --cmd python -X utf8 tools/eval_harness.py run
  # 代码内：from tools.trace_log import record, span
"""
from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TRACE = REPO / "work" / "traces" / "agent_trace.jsonl"


def _write(entry: dict, path: Path | None = None) -> None:
    p = Path(path) if path else TRACE
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def record(kind: str, name: str, status: str = "ok", duration_ms: int = 0,
           meta: dict | None = None, path: Path | None = None) -> dict:
    e = {"schema": "agent_trace/v1", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
         "kind": str(kind), "name": str(name), "status": str(status),
         "duration_ms": int(duration_ms or 0), "meta": meta or {}}
    _write(e, path)
    return e


@contextlib.contextmanager
def span(kind: str, name: str, meta: dict | None = None, path: Path | None = None):
    t0 = time.time()
    status = "ok"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        record(kind, name, status, int((time.time() - t0) * 1000), meta, path)


def tail(n: int = 20, path: Path | None = None) -> list:
    p = Path(path) if path else TRACE
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    out = []
    for ln in lines[-max(1, n):]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def summary(path: Path | None = None) -> dict:
    rows = tail(10 ** 9, path)
    by_kind = {}
    for r in rows:
        k = r.get("kind", "?")
        by_kind.setdefault(k, {"count": 0, "errors": 0, "total_ms": 0})
        by_kind[k]["count"] += 1
        by_kind[k]["total_ms"] += int(r.get("duration_ms") or 0)
        if r.get("status") != "ok":
            by_kind[k]["errors"] += 1
    return {"total": len(rows), "by_kind": by_kind, "path": str(path or TRACE)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 observability-trace（本地 JSONL）")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--tail", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cmd", nargs=argparse.REMAINDER, default=[])
    args = ap.parse_args(argv)
    if args.cmd:
        t0 = time.time()
        rc = subprocess.call(args.cmd)
        record("cmd", " ".join(args.cmd)[:300], "ok" if rc == 0 else "error",
               int((time.time() - t0) * 1000), {"rc": rc})
        return rc
    if args.summary:
        s = summary()
        print(json.dumps(s, ensure_ascii=False, indent=1) if args.json else
              "total=%d kinds=%s" % (s["total"], json.dumps(s["by_kind"], ensure_ascii=False)))
        return 0
    rows = tail(args.tail or 20)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for r in rows:
            print("%s %-6s %-30s %s %dms" % (r.get("at"), r.get("kind"), r.get("name"),
                                              r.get("status"), r.get("duration_ms", 0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
