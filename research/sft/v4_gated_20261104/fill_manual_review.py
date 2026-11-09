# -*- coding: utf-8 -*-
"""全量 206 完成后：回填人工抽检 CSV + 汇总对比。"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

G = Path(r"C:\项目\老田的AI助手\research\sft\v4_gated_20261104")
REVIEW = G / "manual_review_40.csv"


def load_jsonl(p: Path) -> dict[str, dict]:
    if not p.is_file():
        return {}
    return {r["id"]: r for r in (json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip())}


def main() -> int:
    base = load_jsonl(G / "holdout_base_206.jsonl")
    rag = load_jsonl(G / "rag_kb_206.jsonl")
    if not base or not rag:
        print("206 结果未齐，不能回填")
        return 2
    rows = list(csv.DictReader(REVIEW.open(encoding="utf-8-sig")))
    for r in rows:
        b, q = base.get(r["id"], {}), rag.get(r["id"], {})
        if b:
            r["base_prediction"] = b.get("prediction", "")
            r["base_char_f1"] = b.get("char_f1", "")
        if q:
            r["prod_rag_prediction"] = q.get("prediction", "")
            r["rag_char_f1"] = q.get("char_f1", "")
            r["prod_rag_sources"] = "; ".join(x.get("source", "") for x in q.get("retrieved", []))
    with REVIEW.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("filled", REVIEW, "rows", len(rows))
    # 汇总
    bs = json.loads((G / "holdout_base_206_summary.json").read_text(encoding="utf-8"))
    rs = json.loads((G / "rag_kb_206_summary.json").read_text(encoding="utf-8"))
    print(json.dumps({"base": bs, "rag": rs}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
