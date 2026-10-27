# -*- coding: utf-8 -*-
"""product-metrics 最小版：P2 产品数据闭环（本地聚合 + 反馈）。

聚合：eval 最近结果、trace 错误、审批 pending、workflow 进度、browser 最近结果、Key 池状态。
反馈：`work/product/feedback.jsonl`（score 1-5 + note + source），只本地。
用法：
  python -X utf8 tools/product_metrics.py report [--json]
  python -X utf8 tools/product_metrics.py feedback --score 4 --note "..." [--source 老田]
  python -X utf8 tools/product_metrics.py list [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA = REPO / "work" / "product"
FEED = DATA / "feedback.jsonl"
REPORT = DATA / "metrics.json"


def feedback(score: int, note: str = "", source: str = "老田", path: Path | None = None) -> dict:
    score = int(score)
    if not 1 <= score <= 5:
        raise ValueError("score 必须 1-5")
    p = Path(path) if path else FEED
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"schema": "product_feedback/v1", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "score": score, "note": str(note or "")[:500], "source": str(source or "")}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def list_feedback(path: Path | None = None) -> list:
    p = Path(path) if path else FEED
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _latest_eval() -> dict:
    fs = sorted((REPO / "work" / "eval").glob("result-*.json")) if (REPO / "work" / "eval").exists() else []
    if not fs:
        return {}
    try:
        d = json.loads(fs[-1].read_text(encoding="utf-8"))
        return {"total": d.get("total"), "passed": d.get("passed"), "score": d.get("score")}
    except Exception:
        return {}


def _traces() -> dict:
    p = REPO / "work" / "traces" / "agent_trace.jsonl"
    if not p.exists():
        return {"count": 0, "errors": 0}
    rows = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    errs = 0
    for ln in rows:
        try:
            if json.loads(ln).get("status") != "ok":
                errs += 1
        except ValueError:
            pass
    return {"count": len(rows), "errors": errs}


def _approvals() -> dict:
    p = REPO / "work" / "approvals" / "approvals.json"
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
        return {"total": len(rows),
                "pending": sum(1 for r in rows if r.get("status") == "pending")}
    except Exception:
        return {"total": 0, "pending": 0}


def _workflows() -> dict:
    fs = list((REPO / "work" / "workflows").glob("*.json")) if (REPO / "work" / "workflows").exists() else []
    done = total = 0
    for f in fs:
        try:
            wf = json.loads(f.read_text(encoding="utf-8"))
            total += len(wf.get("steps") or [])
            done += sum(1 for s in wf.get("steps") or [] if s.get("status") == "done")
        except Exception:
            pass
    return {"workflows": len(fs), "steps_done": done, "steps_total": total}


def _browser() -> dict:
    fs = sorted((REPO / "work" / "browser").glob("browser-*.json")) if (REPO / "work" / "browser").exists() else []
    if not fs:
        return {}
    try:
        d = json.loads(fs[-1].read_text(encoding="utf-8"))
        return {"ok": d.get("ok"), "url": d.get("url")}
    except Exception:
        return {}


def _keypool() -> dict:
    try:
        sys.path.insert(0, str(REPO / "agent_platform"))
        from legion_command import keypool
        st = keypool.state()
        return {sid: {"keys": len(s.get("keys") or []), "active": s.get("active_key_id"),
                      "degraded": s.get("degraded")}
                for sid, s in (st.get("seats") or {}).items()}
    except Exception:
        return {}


def report(path: Path | None = None) -> dict:
    fb = list_feedback()
    avg = round(sum(x.get("score", 0) for x in fb) / len(fb), 2) if fb else None
    out = {"schema": "product_metrics/v1", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "eval": _latest_eval(), "traces": _traces(), "approvals": _approvals(),
           "workflows": _workflows(), "browser": _browser(), "keypool": _keypool(),
           "feedback": {"count": len(fb), "avg_score": avg}}
    p = Path(path) if path else REPORT
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    out["report_path"] = str(p)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 product-metrics（P2 产品数据闭环）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--json", action="store_true")
    f = sub.add_parser("feedback")
    f.add_argument("--score", type=int, required=True)
    f.add_argument("--note", default="")
    f.add_argument("--source", default="老田")
    l = sub.add_parser("list")
    l.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "report":
        res = report()
        print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else
              "eval=%s traces=%s approvals=%s feedback=%s"
              % (res["eval"], res["traces"], res["approvals"], res["feedback"]))
        return 0
    if args.cmd == "feedback":
        print(json.dumps(feedback(args.score, args.note, args.source), ensure_ascii=False, indent=1))
        return 0
    rows = list_feedback()
    print(json.dumps(rows, ensure_ascii=False, indent=1) if args.json else
          "\n".join("%s %s %s" % (x.get("at"), x.get("score"), x.get("note", "")[:40]) for x in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
