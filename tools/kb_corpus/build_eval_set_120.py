# -*- coding: utf-8 -*-
"""构建 120 题评测集（task-184 / W24）：W13 40 + held-out 40 + citation_clean 40。

数据源（只读，本地离线）：
  - W13 40：research/reports/answer_eval_20261104.json（ctx 由 ctx_ids 重建；gold 由 W13 构建器同源获得）
  - held-out 40：research/finetune/heldout/quant_kb_heldout_105.jsonl
      （A_concept_qa 20 + C_reading_comprehension 10 + C_heldout_note 10）
  - citation_clean 40：research/finetune/sft/citation_clean.jsonl（4 类型各 10）
输出：research/reports/eval_set_120_20261104.json
  items: {id, source, type, question, contexts[{doc_id,text,source}], gold_doc_ids, answerable, reference_answer, meta}
去重（问题归一化）后均衡；只读、不联网。
CLI::
    python tools/kb_corpus/build_eval_set_120.py build --write
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R = REPO / "research" / "reports"
OUT = R / "eval_set_120_20261104.json"
W13_JSON = R / "answer_eval_20261104.json"
HELDOUT = REPO / "research" / "finetune" / "heldout" / "quant_kb_heldout_105.jsonl"
CLEAN = REPO / "research" / "finetune" / "sft" / "citation_clean.jsonl"
W13_TOOL = REPO / "tools" / "kb_corpus" / "answer_eval.py"
W6_TOOL = REPO / "tools" / "kb_corpus" / "rag_baseline_eval.py"
DATE = "20261104"
CJK = re.compile(r"[\u4e00-\u9fff]+")
NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def bigrams(text: str) -> set[str]:
    chars = "".join(CJK.findall(str(text or "")))
    return {chars[i:i + 2] for i in range(max(0, len(chars) - 1))}


def norm(text: str) -> str:
    return NOISE.sub("", str(text or "")).lower()


def load_docs() -> list[dict]:
    return _load("bes_w6", W6_TOOL).load_docs()


def w13_items(docs: list[dict]) -> list[dict]:
    data = json.loads(W13_JSON.read_text(encoding="utf-8"))
    w13 = _load("bes_w13", W13_TOOL)
    base = w13.build_questions(w13.W10MOD.load_questions(), 30)
    gold_by_qid = {q["qid"]: q["gold_doc_ids"] for q in base}
    doc_map = {d["doc_id"]: d for d in docs}
    items = []
    for r in data.get("results") or []:
        notes = r.get("ctx_ids") or []
        ctx = [{"doc_id": x, "text": (doc_map[x].get("text") or "")[:500],
                "source": doc_map[x].get("source") or ""} for x in notes if x in doc_map]
        items.append({
            "id": f"w13_{r.get('qid')}", "source": "w13", "type": r.get("type") or "",
            "question": r.get("question") or "", "contexts": ctx,
            "gold_doc_ids": gold_by_qid.get(r.get("qid"), []),
            "answerable": bool(r.get("answerable")),
            "reference_answer": r.get("answer") or "",
            "meta": {"qid": r.get("qid"), "w13_citation_ok": r.get("citation_ok")},
        })
    return items


def heldout_items(docs: list[dict]) -> list[dict]:
    rows = [json.loads(l) for l in HELDOUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(str((r.get("meta") or {}).get("category") or ""), []).append(r)
    pick: list[dict] = []
    for cat, k in (("A_concept_qa", 20), ("C_reading_comprehension", 20), ("C_heldout_note", 20)):
        pick.extend(by_cat.get(cat, [])[:k])
    cards = [d for d in docs if d.get("kind") == "card"]
    items = []
    for i, r in enumerate(pick):
        meta = r.get("meta") or {}
        msgs = r.get("messages") or []
        question = next((m.get("content") for m in msgs if m.get("role") == "user"), "")
        ref = next((m.get("content") for m in msgs if m.get("role") == "assistant"), "")
        concept = str(meta.get("concept") or "")
        key = concept or norm(question)[:12]
        gold = [d["doc_id"] for d in cards if key and key in str(d.get("text") or "")][:2]
        qb = bigrams(question)
        scored = sorted(cards, key=lambda d: -len(qb & bigrams(d.get("text") or "")))[:4]
        ctx = []
        for did in gold + [d["doc_id"] for d in scored]:
            if did not in [c["doc_id"] for c in ctx]:
                d = next((x for x in cards if x["doc_id"] == did), None)
                if d:
                    ctx.append({"doc_id": did, "text": (d.get("text") or "")[:500],
                                "source": d.get("source") or ""})
        items.append({
            "id": f"heldout_{i+1:03d}", "source": "heldout", "type": str(meta.get("category") or ""),
            "question": question, "contexts": ctx[:5], "gold_doc_ids": gold,
            "answerable": True, "reference_answer": ref,
            "meta": {"concept": concept, "origin": meta.get("origin"), "source": meta.get("source")},
        })
    return items


def clean_items() -> list[dict]:
    rows = [json.loads(l) for l in CLEAN.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        by_type.setdefault(str(r.get("type") or ""), []).append(r)
    pick = []
    for t in sorted(by_type):
        pick.extend(by_type[t][:15])
    items = []
    for i, r in enumerate(pick):
        ctx = [{"doc_id": c.get("doc_id"), "text": str(c.get("text") or "")[:500],
                "source": c.get("source") or ""} for c in (r.get("contexts") or [])][:5]
        items.append({
            "id": str(r.get("id") or f"clean_{i+1:03d}"), "source": "citation_clean",
            "type": r.get("type") or "", "question": r.get("question") or "",
            "contexts": ctx, "gold_doc_ids": r.get("gold_doc_ids") or [],
            "answerable": True, "reference_answer": r.get("answer") or "",
            "meta": {"groundedness": r.get("groundedness"), "cited_docs": r.get("cited_docs")},
        })
    return items


def build(write: bool = False) -> dict:
    docs = load_docs()
    items = w13_items(docs) + heldout_items(docs) + clean_items()
    # 去重（问题归一化），保留首次出现
    uniq, seen = [], set()
    for it in items:
        k = norm(it["question"])
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    # 均衡：若超过 120 按 source 轮转截断；不足则如实记录
    by_source: dict[str, list[dict]] = OrderedDict()
    for it in uniq:
        by_source.setdefault(it["source"], []).append(it)
    balanced = []
    idx = 0
    while len(balanced) < 120 and any(len(v) > idx for v in by_source.values()):
        for src in by_source:
            if len(by_source[src]) > idx and len(balanced) < 120:
                balanced.append(by_source[src][idx])
        idx += 1
    payload = {
        "schema_version": "eval_set_120.v1", "date": DATE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": "W24-引用校验器 + 120 题评测集",
        "sources": {
            "w13": {"path": str(W13_JSON), "sha256": hashlib.sha256(W13_JSON.read_bytes()).hexdigest()},
            "heldout": {"path": str(HELDOUT), "sha256": hashlib.sha256(HELDOUT.read_bytes()).hexdigest()},
            "citation_clean": {"path": str(CLEAN), "sha256": hashlib.sha256(CLEAN.read_bytes()).hexdigest()},
        },
        "composition": {
            "total": len(balanced),
            "by_source": dict(Counter(x["source"] for x in balanced)),
            "by_type": dict(Counter(x["type"] for x in balanced)),
            "answerable": sum(1 for x in balanced if x["answerable"]),
            "unanswerable": sum(1 for x in balanced if not x["answerable"]),
            "with_ctx": sum(1 for x in balanced if x["contexts"]),
            "with_gold": sum(1 for x in balanced if x["gold_doc_ids"]),
        },
        "dedupe": {"before": len(items), "after": len(uniq), "dropped": len(items) - len(uniq)},
        "items": balanced,
        "policy": {"ctx": "3-5 条（held-out 为卡片检索重建；W13 为其 hybrid top5；clean 原样）",
                   "gold": "W13=构建器 gold；held-out=概念匹配卡片；clean=gold_doc_ids",
                   "dedupe": "问题归一化去重；按 source 轮转均衡到 120"},
        "disclaimer": "评测集为研究用途；问题/答案为源数据整理，不构成投资建议。",
    }
    if write:
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        payload["_output"] = str(OUT)
    return payload


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    ap = argparse.ArgumentParser(description="构建 120 题引用评测集")
    ap.add_argument("command", nargs="?", default="build", choices=["build"])
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    p = build(write=args.write)
    print(f"[build_eval_set_120] total={p['composition']['total']} "
          f"by_source={json.dumps(p['composition']['by_source'], ensure_ascii=False)} "
          f"answerable={p['composition']['answerable']}/{p['composition']['unanswerable']} "
          f"with_ctx={p['composition']['with_ctx']} with_gold={p['composition']['with_gold']} "
          f"dedupe_dropped={p['dedupe']['dropped']}")
    if p.get("_output"):
        print("output:", p["_output"], "|", OUT.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
