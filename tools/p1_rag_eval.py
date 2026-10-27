# -*- coding: utf-8 -*-
"""P1-2 M1 recall@3 评测：8 条 factual，检查相关证据是否进入 BM25 Top3。

用法：python tools/p1_rag_eval.py [--k 3]
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES = REPO_ROOT / "ops" / "p0" / "eval" / "golden_cases.jsonl"

# 每题的相关证据词（出现在 path/title/snippet 任一即算命中）
EXPECT = {
    "F01": ["8795", "llm/chat", "网关", "gateway"],
    "F02": ["OpenClaw", "载体", "deepseek", "openclaw"],
    "F03": ["cards.jsonl", "质询", "challenge"],
    "F04": ["binding", "llm/chat", "请求体"],
    "F05": ["plans", "沙盘", "W5", "快照"],
    "F06": ["zhugeliang-review", "board", "质询"],
    "F07": ["tasks_read", "MCP", "只读工具"],
    "F08": ["P2", "3 问", "质询", "真模型"],
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P1-2 BM25 recall@k")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    import sys
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import zhuge_rag as rag  # noqa: E402
    rag.build_index()
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    factual = [c for c in cases if c.get("category") == "factual"]
    rows = []
    for case in factual:
        t0 = time.time()
        hits = rag.search(case["input"], k=args.k)
        ms = round((time.time() - t0) * 1000, 1)
        blob = " ".join(h.get("path", "") + " " + h.get("title", "") + " "
                        + h.get("snippet", "") for h in hits)
        expect = EXPECT.get(case["id"], [])
        hit = any(e.lower() in blob.lower() for e in expect)
        rows.append({"id": case["id"], "hit": hit, "recall_ms": ms,
                     "expect": expect,
                     "top": [{"path": h["path"], "line": h["line"],
                              "score": h["score"]} for h in hits]})
        print("[%s] %s expect=%s ms=%s top=%s" % (
            "PASS" if hit else "FAIL", case["id"], expect, ms,
            [(h["path"], h["line"]) for h in hits]))
    total = len(rows)
    hits = sum(1 for r in rows if r["hit"])
    rate = hits / total if total else 0
    summary = {"generated_at": datetime.now(timezone(timedelta(hours=8))).strftime(
                   "%Y-%m-%d %H:%M:%S"),
               "k": args.k, "total": total, "hits": hits,
               "recall": round(rate, 4), "target": 0.8, "target_met": rate >= 0.8,
               "avg_ms": round(sum(r["recall_ms"] for r in rows) / total, 1) if total else 0,
               "cases": rows}
    out_dir = REPO_ROOT / "ops" / "p1" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("rag_recall_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSUMMARY recall@%d=%d/%d=%.1f%% target>=80%% met=%s avg_ms=%.1f" % (
        args.k, hits, total, 100 * rate, rate >= 0.8, summary["avg_ms"]))
    print("wrote", out)
    return 0 if rate >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
