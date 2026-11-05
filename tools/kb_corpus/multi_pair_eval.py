# -*- coding: utf-8 -*-
"""v4d vs Ollama multi 配对评测（生产检索 + 同材料生成）。

对同一批问题：
1. 用生产 RAG 检索一次（kb_qa_multi.retrieve_clusters, k=6）；
2. 同一 clusters 分别交给 v4d（llm_router 百炼）与本地 Ollama（qwen3.5:9b）；
3. 两边都走 answer_multi（citation_retry=True, prompt v4），再计算质量守卫指标；
4. 输出逐题对比与聚合指标，供切回 router 的决策使用。

用法：
  python tools/kb_corpus/multi_pair_eval.py --limit 20
  python tools/kb_corpus/multi_pair_eval.py --limit 20 --no-citation-retry
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "kb_corpus"))

import kb_qa_multi as mm  # noqa: E402
import kb_qa_guard as guard  # noqa: E402
import llm_router  # noqa: E402

EVAL_SET = REPO / "research" / "reports" / "eval_set_120_20261104.json"
OUT_JSON = REPO / "research" / "reports" / "multi_pair_eval_20261105.json"
OUT_MD = REPO / "research" / "reports" / "multi_pair_eval_20261105.md"
GATED_OUT_JSON = REPO / "research" / "reports" / "multi_pair_eval_gated_20261105.json"
GATED_OUT_MD = REPO / "research" / "reports" / "multi_pair_eval_gated_20261105.md"

DEFAULT_LIMIT = 20
DEFAULT_K = 6
OLLAMA_MODEL = "qwen3.5:9b"


def _spread(pool: list[dict], n: int) -> list[dict]:
    """从 pool 中均匀取 n 条（保持原顺序，确定性）。"""
    if n <= 0 or not pool:
        return []
    if len(pool) <= n:
        return list(pool)
    idx = [round(i * (len(pool) - 1) / (n - 1)) for i in range(n)] if n > 1 else [0]
    return [pool[i] for i in idx]


def select_questions(items: list[dict], limit: int = DEFAULT_LIMIT) -> list[dict]:
    """分层选 20 题：4 不可答 + 6 W13 + 5 heldout + 5 citation。"""
    unans = [x for x in items if not x.get("answerable", True)]
    answerable = [x for x in items if x.get("answerable", True)]
    by_source = {
        "w13": [x for x in answerable if x.get("source") == "w13"],
        "heldout": [x for x in answerable if x.get("source") == "heldout"],
        "citation_clean": [x for x in answerable if x.get("source") == "citation_clean"],
    }
    quotas = {"unanswerable": 4, "w13": 6, "heldout": 5, "citation_clean": 5}
    # 按 limit 等比例缩放，保证 limit<20 时仍可用
    if limit != 20:
        scale = limit / 20.0
        quotas = {k: max(1, round(v * scale)) for k, v in quotas.items()}
    picks: list[dict] = []
    picks.extend(_spread(unans, quotas["unanswerable"]))
    for src in ("w13", "heldout", "citation_clean"):
        picks.extend(_spread(by_source[src], quotas[src]))
    # 去重并截断
    seen = set()
    out = []
    for item in picks:
        qid = item.get("id")
        if qid in seen:
            continue
        seen.add(qid)
        out.append(item)
    return out[:limit]


def _v4d_chat(collector: list[dict]):
    def chat(prompt: str, model=None, timeout=None) -> str:
        result = llm_router.generate(prompt, temperature=0.1, max_tokens=500,
                                     purpose="multi_pair_eval")
        collector.append(result)
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "router failed"))
        return str(result.get("text") or "")
    return chat


def _ollama_chat(collector: list[dict]):
    def chat(prompt: str, model=None, timeout=None) -> str:
        text = mm.call_ollama(prompt, model=OLLAMA_MODEL,
                             timeout=float(timeout or 180))
        collector.append({"ok": True, "text": text})
        return text
    return chat


def run_one(item: dict, *, k: int, citation_retry: bool,
            v4d_answerability_gate: bool = False) -> dict:
    question = item.get("question") or ""
    t0 = time.monotonic()
    clusters = mm.retrieve_clusters(question, k)
    retrieval_seconds = round(time.monotonic() - t0, 2)
    unit_ids = {str(u.get("unit_id")).lower()
                for c in clusters for u in (c.get("units") or [])
                if u.get("unit_id")}
    book_ids = {str(u.get("book")) for c in clusters for u in (c.get("units") or [])
                if u.get("book")}

    row = {"id": item.get("id"), "source": item.get("source"),
           "type": item.get("type"), "answerable": bool(item.get("answerable", True)),
           "question": question, "retrieval_seconds": retrieval_seconds,
           "clusters": len(clusters), "units": len(unit_ids)}
    for provider, chat_factory in (("v4d", _v4d_chat), ("ollama", _ollama_chat)):
        calls: list[dict] = []
        started = time.monotonic()
        try:
            result = mm.answer_multi(
                question, k=k, clusters=clusters,
                chat_fn=chat_factory(calls),
                citation_retry=citation_retry,
                prompt_version="v4",
                answerability_gate=(v4d_answerability_gate and provider == "v4d"))
            error = None
        except Exception as exc:  # 保留失败证据，不中断整批评测
            result = {"answer": "", "citations": [], "confidence": {"level": "none"},
                      "fallback": False, "refusal": False}
            error = f"{type(exc).__name__}: {exc}"
        elapsed = round(time.monotonic() - started, 2)
        metrics = guard.compute_guard_metrics(result, unit_ids, book_ids)
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        cost = 0.0
        for call in calls:
            u = call.get("usage") or {}
            usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
            usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
            cost += float(call.get("cost_cny") or 0.0)
        row[provider] = {
            "answer": result.get("answer") or "",
            "answer_chars": len(result.get("answer") or ""),
            "elapsed": elapsed,
            "citations": len(result.get("citations") or []),
            "confidence": (result.get("confidence") or {}).get("level"),
            "refusal": bool(result.get("refusal")),
            "fallback": bool(result.get("fallback")),
            "retry_used": bool((result.get("citation_retry") or {}).get("used")),
            "guard_passed": bool(metrics.get("guard_passed")),
            "guard_issues": metrics.get("guard_issues") or [],
            "citation_valid_count": metrics.get("citation_valid_count"),
            "citation_invalid_count": metrics.get("citation_invalid_count"),
            "citation_resolvable_rate": metrics.get("citation_resolvable_rate"),
            "distinct_cited_units": metrics.get("distinct_cited_units"),
            "inference_ratio": metrics.get("inference_ratio"),
            "usage": usage, "cost_cny": round(cost, 6), "error": error,
            "answerability": result.get("answerability"),
        }
    row["verdict"] = _verdict(row)
    return row


def _verdict(row: dict) -> str:
    """启发式偏好：拒答正确性 > 引用/守卫 > 时延。仅用于自动汇总，需人工抽检。"""
    v, o = row["v4d"], row["ollama"]
    score = {}
    for name, r in (("v4d", v), ("ollama", o)):
        s = 0
        if not row["answerable"]:
            s += 3 if r["refusal"] else -3
        else:
            s += -3 if r["refusal"] else 2
            s += 2 if r["guard_passed"] else 0
            s += 1 if int(r["citation_valid_count"] or 0) >= 2 else 0
            s += -2 if int(r["citation_invalid_count"] or 0) > 0 else 0
        if r["fallback"] or r["error"]:
            s -= 2
        score[name] = s
    if score["v4d"] > score["ollama"]:
        return "v4d"
    if score["ollama"] > score["v4d"]:
        return "ollama"
    # 同分用时延/长度做弱 tie-break
    if v["error"] or v["fallback"]:
        return "ollama"
    if o["error"] or o["fallback"]:
        return "v4d"
    return "tie"


def _agg(rows: list[dict], provider: str) -> dict:
    rs = [r[provider] for r in rows]
    n = len(rs) or 1
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    ans_rows = [r[provider] for r in answerable]
    un_rows = [r[provider] for r in unanswerable]
    return {
        "n": len(rs),
        "answerable_n": len(ans_rows),
        "unanswerable_n": len(un_rows),
        "refusal_rate_all": round(sum(1 for r in rs if r["refusal"]) / n, 4),
        "refusal_rate_answerable": round(sum(1 for r in ans_rows if r["refusal"]) / max(1, len(ans_rows)), 4),
        "refusal_rate_unanswerable": round(sum(1 for r in un_rows if r["refusal"]) / max(1, len(un_rows)), 4),
        "guard_pass_rate": round(sum(1 for r in rs if r["guard_passed"]) / n, 4),
        "guard_pass_rate_answerable": round(sum(1 for r in ans_rows if r["guard_passed"]) / max(1, len(ans_rows)), 4),
        "avg_citations": round(sum(r["citations"] for r in rs) / n, 2),
        "avg_citation_valid": round(sum(int(r["citation_valid_count"] or 0) for r in rs) / n, 2),
        "avg_chars": round(sum(r["answer_chars"] for r in rs) / n, 1),
        "avg_elapsed": round(sum(r["elapsed"] for r in rs) / n, 2),
        "p95_elapsed": round(sorted(r["elapsed"] for r in rs)[max(0, int(0.95 * len(rs)) - 1)], 2),
        "fallback_count": sum(1 for r in rs if r["fallback"]),
        "error_count": sum(1 for r in rs if r["error"]),
        "empty_count": sum(1 for r in rs if not r["answer_chars"]),
        "retry_used_count": sum(1 for r in rs if r["retry_used"]),
        "answerability_refusal_count": sum(
            1 for r in rs if (r.get("answerability") or {}).get("decision") == "unanswerable"),
        "total_cost_cny": round(sum(float(r["cost_cny"] or 0) for r in rs), 4),
    }


def build_report(rows: list[dict], meta: dict) -> dict:
    v = _agg(rows, "v4d")
    o = _agg(rows, "ollama")
    verdicts = {"v4d": 0, "ollama": 0, "tie": 0}
    for r in rows:
        verdicts[r["verdict"]] += 1
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "meta": meta,
        "aggregate": {"v4d": v, "ollama": o, "verdicts": verdicts},
        "rows": rows,
    }


def write_md(report: dict) -> str:
    agg = report["aggregate"]
    v, o = agg["v4d"], agg["ollama"]
    lines = [
        "# v4d vs Ollama multi 配对评测（20 题）" + ("（v4d 可答性门控）" if report["meta"].get("v4d_answerability_gate") else ""),
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 检索：生产 RAG，k={report['meta']['k']}，每组问题只检索一次，两模型使用同一材料",
        f"- 生成：prompt_version=v4，citation_retry={report['meta']['citation_retry']}，"
        f"v4d_answerability_gate={report['meta'].get('v4d_answerability_gate')}",
        f"- 问题：{report['meta']['limit']} 题（4 不可答 + 6 W13 + 5 held-out + 5 citation）",
        "",
        "## 聚合对比",
        "",
        "| 指标 | v4d（百炼） | Ollama（qwen3.5:9b） |",
        "|---|---:|---:|",
        f"| 拒答率（全量） | {v['refusal_rate_all']:.0%} | {o['refusal_rate_all']:.0%} |",
        f"| 拒答率（可答） | {v['refusal_rate_answerable']:.0%} | {o['refusal_rate_answerable']:.0%} |",
        f"| 拒答率（不可答，越高越好） | {v['refusal_rate_unanswerable']:.0%} | {o['refusal_rate_unanswerable']:.0%} |",
        f"| 质量守卫通过率 | {v['guard_pass_rate']:.0%} | {o['guard_pass_rate']:.0%} |",
        f"| 平均引用数 | {v['avg_citations']:.2f} | {o['avg_citations']:.2f} |",
        f"| 平均有效引用数 | {v['avg_citation_valid']:.2f} | {o['avg_citation_valid']:.2f} |",
        f"| 平均答案字数 | {v['avg_chars']:.0f} | {o['avg_chars']:.0f} |",
        f"| 平均耗时（秒） | {v['avg_elapsed']:.1f} | {o['avg_elapsed']:.1f} |",
        f"| 降级次数 | {v['fallback_count']} | {o['fallback_count']} |",
        f"| 空答案数 | {v['empty_count']} | {o['empty_count']} |",
        f"| 可答性门控拒答数 | {v.get('answerability_refusal_count', 0)} | - |",
        f"| 成本（元） | {v['total_cost_cny']:.4f} | 0（本地） |",
        "",
        f"启发式偏好：**v4d {agg['verdicts']['v4d']} / Ollama {agg['verdicts']['ollama']} / 平 {agg['verdicts']['tie']}**（仅自动汇总，需人工抽检）。",
        "",
        "## 逐题结果",
        "",
        "| # | 题号 | 类型 | 可答 | v4d 引用/守卫/耗时 | v4d 答案摘要 | Ollama 引用/守卫/耗时 | Ollama 答案摘要 | 偏好 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(report["rows"], 1):
        vd, ol = r["v4d"], r["ollama"]
        vsum = (vd["answer"] or "（空）").replace("\n", " ")[:80]
        osum = (ol["answer"] or "（空）").replace("\n", " ")[:80]
        lines.append(
            f"| {i} | {r['id']} | {r['type']} | {'是' if r['answerable'] else '否'} | "
            f"{vd['citations']}/{'PASS' if vd['guard_passed'] else 'FAIL'}/{vd['elapsed']:.1f}s | {vsum} | "
            f"{ol['citations']}/{'PASS' if ol['guard_passed'] else 'FAIL'}/{ol['elapsed']:.1f}s | {osum} | {r['verdict']} |")
    lines += [
        "",
        "## 备注",
        "",
        "- 自动偏好规则：不可答题拒答 +3；可答题非拒答 +2、守卫 PASS +2、≥2 有效引用 +1、幻觉引用 -2；降级/错误 -2。",
        "- 守卫指标来自 `kb_qa_guard.compute_guard_metrics`（citation_missing/insufficient_cited_units/inference_heavy/citation_invalid）。",
        "- 生产默认仍是 Ollama；本评测不改变线上配置。",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="multi_pair_eval")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--out-json", default=None)
    parser.add_argument("--out-md", default=None)
    parser.add_argument("--no-citation-retry", action="store_true")
    parser.add_argument("--v4d-answerability-gate", action="store_true")
    args = parser.parse_args(argv)

    items = json.loads(EVAL_SET.read_text(encoding="utf-8"))["items"]
    questions = select_questions(items, args.limit)
    rows = []
    for i, item in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {item['id']} {item.get('question','')[:30]} ...", flush=True)
        rows.append(run_one(item, k=args.k,
                            citation_retry=not args.no_citation_retry,
                            v4d_answerability_gate=args.v4d_answerability_gate))
        r = rows[-1]
        print(f"    v4d {r['v4d']['elapsed']:.1f}s c={r['v4d']['citations']} "
              f"guard={'PASS' if r['v4d']['guard_passed'] else 'FAIL'} | "
              f"ollama {r['ollama']['elapsed']:.1f}s c={r['ollama']['citations']} "
              f"guard={'PASS' if r['ollama']['guard_passed'] else 'FAIL'} | {r['verdict']}", flush=True)
    meta = {"limit": len(questions), "k": args.k,
            "citation_retry": not args.no_citation_retry,
            "v4d_answerability_gate": bool(args.v4d_answerability_gate),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    report = build_report(rows, meta)
    default_json = GATED_OUT_JSON if args.v4d_answerability_gate else OUT_JSON
    default_md = GATED_OUT_MD if args.v4d_answerability_gate else OUT_MD
    out_json = Path(args.out_json) if args.out_json else default_json
    out_md = Path(args.out_md) if args.out_md else default_md
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    out_md.write_text(write_md(report), encoding="utf-8")
    print(f"saved: {out_json}")
    print(f"saved: {out_md}")
    print(json.dumps(report["aggregate"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
