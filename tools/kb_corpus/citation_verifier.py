# -*- coding: utf-8 -*-
"""引用校验器（task-184 / W24）：离线校验答案中 [n] 引用是否被上下文支撑。

用法（库调用）::

    from citation_verifier import verify_answer
    res = verify_answer(answer, [{"doc_id": "C:009", "text": "..."}, ...])
    # {"citation_valid_rate", "weak_rate", "unsupported_sentences", "per_sentence", ...}

规则（确定性，不调模型/不联网）：
  1) 按句切分（。！？!?；; 与换行）；
  2) 每句提取 [n] 引用编号；
  3) 对每个引用检查对应 ctx 文本对句子的支撑度：句中 CJK bigram 被该 ctx 覆盖的比例
     （去掉引用标记/标点），阈值默认 0.25（高于阈值=支撑）；
  4) 引用编号越界（>ctx 数或 <1）→ invalid；无引用句 → weak；
  5) 汇总：citation_valid_rate = 有效引用句 / 有引用句；weak_rate = 无引用句 / 总句；
     unsupported_sentences 记录未支撑/越界句。

CLI（离线跑已保存 v4d 结果）::

    python tools/kb_corpus/citation_verifier.py --v4d research/reports/bailian_eval_v4d_strict_20261104.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CJK = re.compile(r"[\u4e00-\u9fff]+")
CITE = re.compile(r"\[(\d{1,2})\]")
SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")
DEFAULT_THRESHOLD = 0.25


def bigrams(text: str) -> set[str]:
    chars = "".join(CJK.findall(str(text or "")))
    return {chars[i:i + 2] for i in range(max(0, len(chars) - 1))}


def split_sentences(answer: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(str(answer or "")) if s.strip()]


def support_score(sentence: str, ctx_text: str) -> float:
    """句子 bigram 被单条 ctx 覆盖的比例（引用标记不计入）。"""
    clean = CITE.sub("", str(sentence or ""))
    sb = bigrams(clean)
    if not sb:
        return 0.0
    cb = bigrams(ctx_text)
    return round(len(sb & cb) / len(sb), 4) if cb else 0.0


def verify_answer(answer: str, contexts: list[dict], threshold: float = DEFAULT_THRESHOLD) -> dict:
    """校验一条答案的引用支撑情况。contexts: [{doc_id, text, ...}]。"""
    ctx_texts = [str(c.get("text") or "") for c in (contexts or [])]
    sentences = split_sentences(answer)
    per_sentence, unsupported = [], []
    cited_n = valid_n = weak_n = 0
    for sent in sentences:
        cites = [int(x) for x in CITE.findall(sent)]
        clean = CITE.sub("", sent).strip()
        if not cites:
            weak_n += 1
            per_sentence.append({"sentence": clean, "citations": [], "support": 0.0,
                                 "valid": False, "weak": True, "reason": "no_citation"})
            continue
        cited_n += 1
        # 多引用：取被引用 ctx 中的最大支撑度
        supports = []
        for n in cites:
            if 1 <= n <= len(ctx_texts):
                supports.append(support_score(clean, ctx_texts[n - 1]))
        ok = bool(supports) and max(supports) >= threshold
        valid_n += 1 if ok else 0
        rec = {"sentence": clean, "citations": cites,
               "support": round(max(supports), 4) if supports else 0.0,
               "valid": ok, "weak": False,
               "reason": "" if ok else ("citation_out_of_range" if not supports else "low_support")}
        per_sentence.append(rec)
        if not ok:
            unsupported.append(rec)
    total = len(sentences)
    return {
        "empty": total == 0,
        "sentences": total,
        "cited_sentences": cited_n,
        "weak_sentences": weak_n,
        "unsupported_count": len(unsupported),
        "citation_valid_rate": round(valid_n / cited_n, 4) if cited_n else 0.0,
        "weak_rate": round(weak_n / total, 4) if total else 0.0,
        "all_supported": bool(cited_n) and not unsupported,
        "threshold": threshold,
        "unsupported_sentences": unsupported,
        "per_sentence": per_sentence,
    }


def _load_docs_map() -> dict[str, dict]:
    import importlib.util
    p = REPO / "tools" / "kb_corpus" / "rag_baseline_eval.py"
    spec = importlib.util.spec_from_file_location("cv_w6", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cv_w6"] = mod
    spec.loader.exec_module(mod)
    return {d["doc_id"]: d for d in mod.load_docs()}


def stats_on_v4d(path: Path, threshold: float = DEFAULT_THRESHOLD) -> dict:
    """离线统计 v4d 40 题结果的引用校验（ctx 由 ctx_ids 从 W6 文档库重建）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    docs = _load_docs_map()
    rows = []
    for r in data.get("results") or []:
        ctx = [docs[x] for x in (r.get("ctx_ids") or []) if x in docs]
        res = verify_answer(r.get("answer") or "", ctx, threshold)
        res.update({"qid": r.get("qid"), "answerable": r.get("answerable"),
                    "type": r.get("type"), "ctx_n": len(ctx)})
        rows.append(res)
    n = max(1, len(rows))
    ans = [x for x in rows if x.get("answerable")]
    unans = [x for x in rows if not x.get("answerable")]
    total_sent = sum(x["sentences"] for x in rows)
    cited_sent = sum(x["cited_sentences"] for x in rows)
    weak_sent = sum(x["weak_sentences"] for x in rows)
    unsupported = sum(x["unsupported_count"] for x in rows)
    return {
        "source": str(path), "threshold": threshold, "answers": len(rows),
        "sentences": total_sent, "cited_sentences": cited_sent, "weak_sentences": weak_sent,
        "unsupported_sentences": unsupported,
        "mean_citation_valid_rate": round(sum(x["citation_valid_rate"] for x in rows) / n, 4),
        "mean_weak_rate": round(sum(x["weak_rate"] for x in rows) / n, 4),
        "answers_all_supported": sum(1 for x in rows if x["all_supported"]),
        "answers_all_supported_rate": round(sum(1 for x in rows if x["all_supported"]) / n, 4),
        "unsupported_answers": [{"qid": x["qid"], "answer": next((r.get("answer") or "" for r in (data.get("results") or []) if r.get("qid") == x["qid"]), ""),
                                 "reason": u.get("reason"), "sentence": u.get("sentence"),
                                 "citations": u.get("citations"), "support": u.get("support")}
                                for x in rows for u in x["unsupported_sentences"]][:10],
        "by_answerable": {
            "answerable": {"n": len(ans), "mean_citation_valid_rate": round(sum(x["citation_valid_rate"] for x in ans) / max(1, len(ans)), 4)},
            "unanswerable": {"n": len(unans), "mean_citation_valid_rate": round(sum(x["citation_valid_rate"] for x in unans) / max(1, len(unans)), 4)},
        },
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    ap = argparse.ArgumentParser(description="引用校验器（离线，确定性）")
    ap.add_argument("command", nargs="?", default="stats", choices=["stats"])
    ap.add_argument("--v4d", default=str(REPO / "research/reports/bailian_eval_v4d_strict_20261104.json"))
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--answer", default="")
    ap.add_argument("--ctx-file", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.answer:
        ctx = json.loads(Path(args.ctx_file).read_text(encoding="utf-8")) if args.ctx_file else []
        print(json.dumps(verify_answer(args.answer, ctx, args.threshold), ensure_ascii=False, indent=2))
        return 0
    st = stats_on_v4d(Path(args.v4d), args.threshold)
    out = {k: v for k, v in st.items() if k != "rows"}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
