# -*- coding: utf-8 -*-
"""P1-1 引用率评测：对 factual 用例跑 grounded_chat，检查 [S#] 引用。

目标：citation 覆盖率 ≥80%（8 条 factual 中 ≥7 条带 [S#]）。
用法：
  python tools/p1_citation_eval.py --binding ollama/qwen3.5:9b
  python tools/p1_citation_eval.py --binding openai/gpt-6.0 --limit 8
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))  # PYTHONSAFEPATH=1 时脚本目录不自动入 sys.path

CASES = REPO_ROOT / "ops" / "p0" / "eval" / "golden_cases.jsonl"
CITATION_RE = re.compile(r"\[S\d+\]")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P1-1 citation eval")
    ap.add_argument("--binding", default="ollama/qwen3.5:9b")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "work" / "p1" / "eval"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    factual = [c for c in cases if c.get("category") == "factual"][: args.limit]
    from zhuge_grounding import grounded_chat  # noqa: E402
    rows = []
    for case in factual:
        res = grounded_chat(case["input"], binding=args.binding)
        text = res.get("text") or ""
        cited = bool(text.strip()) and bool(CITATION_RE.search(text))
        ok = bool(res.get("ok")) and bool(text.strip())
        rows.append({"id": case["id"], "input": case["input"],
                     "grounded": res.get("grounded"), "cited": cited, "ok_answer": ok,
                     "sources": [s.get("id") for s in res.get("sources") or []],
                     "ok": res.get("ok"), "error": res.get("error"),
                     "answer": text[:300]})
        print("[%s] %-3s grounded=%s cited=%s sources=%s err=%s" % (
            "PASS" if cited else "FAIL", case["id"], res.get("grounded"),
            cited, [s.get("id") for s in res.get("sources") or []],
            res.get("error")))
    total = len(rows)
    cited_n = sum(1 for r in rows if r["cited"])
    grounded_n = sum(1 for r in rows if r["grounded"])
    rate = cited_n / total if total else 0
    summary = {"generated_at": datetime.now(timezone(timedelta(hours=8))).strftime(
                   "%Y-%m-%d %H:%M:%S"),
               "binding": args.binding, "total": total, "grounded": grounded_n,
               "cited": cited_n, "citation_rate": round(rate, 4),
               "target": 0.8, "target_met": rate >= 0.8,
               "cases": rows}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("citation_%s_%s.json" % (
        datetime.now().strftime("%Y%m%d_%H%M%S"),
        re.sub(r"[^A-Za-z0-9_.-]+", "_", args.binding)))
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSUMMARY binding=%s total=%d grounded=%d cited=%d rate=%.1f%% target>=80%% met=%s" % (
        args.binding, total, grounded_n, cited_n, 100 * rate, rate >= 0.8))
    print("wrote", out)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    return 0 if rate >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
