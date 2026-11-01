# -*- coding: utf-8 -*-
"""multi 答案质量守卫（P0）：引用可解析硬门 + 推断比例限制 + 被引单元集中度。

背景：wave18 BB 发现 multi 质量代价（引用可解析率 100%→55%、有答案子集支撑率 75%→25.9%、
幻觉率上升），需要 multi_with_guard。本模块对 multi 答案做**后处理守卫**（不新增 LLM 调用）：

  a) 引用可解析硬门：答案中的 [unit_id] 必须能在知识库单元集合（或 alias：book ID）中解析；
     不可解析引用从 citations 剔除并记入 citation_invalid；
  b) 推断比例限制：inference_ratio = 推断标记 /（直接事实+推断标记），>0.6 记 inference_heavy；
  c) 被引单元集中度：有效引用必须覆盖 ≥2 个不同单元（防单单元复制）；
  d) payload 增加：citation_valid / citation_invalid / citation_resolvable_rate /
     inference_ratio / inference_heavy / distinct_cited_units / guard_passed / guard_issues；
  e) `--on-guard-fail mark|fallback|refuse`：不通过时仅标记 / 回退为「可核验单元摘录」/
     拒答（默认 fallback，恢复引用可解析率与低幻觉）。

评估（`run`）：重跑 wave15 v3 的 12 条抽验问题（multi），对比 guard 前后
可答率 / 引用可解析率 / 支撑率（引用在本次检索材料内）/ 幻觉率（不可解析引用占比）。
输出 `research/reports/kb_qa_guard_20261101.{json,md}`。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_guard_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_guard_20261101.md"
DEFAULT_N = 12
DEFAULT_K = 8
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 480
INFERENCE_RATIO_MAX = 0.6
MIN_DISTINCT_UNITS = 2
REFUSAL_ANSWER = "知识库中没有足够依据回答该问题。"
GUARD_FALLBACK_PREFIX = "（质量守卫回退：多单元答案引用/集中度/推断比例未达标，摘录可核验知识单元）"
ACTIONS = ("mark", "fallback", "refuse")
_CITE_RE = re.compile(r"[\[【]([0-9a-fA-F]{8,12})[\]】]")


class GuardError(RuntimeError):
    """质量守卫/评估异常。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise GuardError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_kb_qa = None
_kb_api = None


def kb_qa():
    global _kb_qa
    if _kb_qa is None:
        _kb_qa = _load_module("kb_qa_for_guard", "kb_qa.py")
    return _kb_qa


def kb_api():
    global _kb_api
    if _kb_api is None:
        _kb_api = _load_module("kb_read_api_for_guard", "kb_read_api.py")
    return _kb_api


def load_kb_ids() -> tuple[set[str], set[str]]:
    """知识库单元 ID 集合 + alias（book ID）集合。"""
    try:
        units = kb_api().load_units()
    except Exception as exc:  # noqa: BLE001
        raise GuardError(f"知识库单元加载失败：{type(exc).__name__}: {exc}") from exc
    unit_ids = {str(u.get("id")) for u in units if u.get("id")}
    book_ids = {str(u.get("book")) for u in units if u.get("book")}
    return unit_ids, book_ids


# ---------------------------------------------------------------------------
# a) 引用可解析硬门
# ---------------------------------------------------------------------------
def parse_answer_citations(answer: str) -> list[str]:
    seen, out = set(), []
    for cid in _CITE_RE.findall(answer or ""):
        cid = cid.strip().lower()
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def resolve_citations(cited: list[str], unit_ids: set[str], book_ids: set[str] | None = None) -> dict:
    """引用解析：unit 集合有效；book ID 视为 alias（valid_alias）；其余为 invalid。"""
    book_ids = book_ids or set()
    valid, invalid, aliases = [], [], []
    for cid in cited:
        norm = str(cid).strip().lower().lstrip("u_")
        if cid in unit_ids or norm in unit_ids:
            valid.append(cid)
        elif cid in book_ids or norm in book_ids:
            valid.append(cid)
            aliases.append(cid)
        else:
            invalid.append(cid)
    return {"valid": valid, "invalid": invalid, "aliases": aliases,
            "total": len(cited), "valid_count": len(valid), "invalid_count": len(invalid)}


# ---------------------------------------------------------------------------
# b) 推断比例 / c) 集中度 / d) payload
# ---------------------------------------------------------------------------
def compute_guard_metrics(result: dict, unit_ids: set[str], book_ids: set[str] | None = None) -> dict:
    """计算质量指标（不修改 result）：引用解析、推断比例、被引单元集中度。"""
    answer = str(result.get("answer") or "")
    provided_citations = [str(c.get("unit_id")).lower() for c in (result.get("citations") or [])
                          if isinstance(c, dict) and c.get("unit_id")]
    parsed = parse_answer_citations(answer)
    cited = list(dict.fromkeys(parsed + provided_citations))
    resolved = resolve_citations(cited, unit_ids, book_ids)
    direct = int(result.get("direct_fact_markers") or 0)
    infer = int(result.get("inference_markers") or 0)
    total_marks = direct + infer
    inference_ratio = round(infer / total_marks, 4) if total_marks else 0.0
    inference_heavy = inference_ratio > INFERENCE_RATIO_MAX
    retrieved = [str(r.get("unit_id")).lower() for r in (result.get("retrieved") or [])
                 if isinstance(r, dict) and r.get("unit_id")]
    retrieved_set = set(retrieved)
    support_valid = [c for c in resolved["valid"] if c in retrieved_set or c in unit_ids]
    support_rate = round(len(support_valid) / resolved["total"], 4) if resolved["total"] else 0.0
    hallucination_rate = round(resolved["invalid_count"] / resolved["total"], 4) if resolved["total"] else 0.0
    distinct_units = len(set(resolved["valid"]))
    refusal = "没有足够依据" in answer
    answerable = bool(answer) and not refusal and not bool(result.get("fallback"))
    issues = []
    if resolved["invalid_count"] > 0:
        issues.append("citation_invalid")
    if resolved["total"] == 0:
        issues.append("citation_missing")
    if distinct_units < MIN_DISTINCT_UNITS:
        issues.append("insufficient_cited_units")
    if inference_heavy:
        issues.append("inference_heavy")
    return {
        "answerable": answerable, "refusal": refusal,
        "citation_total": resolved["total"], "citation_valid": resolved["valid"],
        "citation_invalid": resolved["invalid"], "citation_valid_count": resolved["valid_count"],
        "citation_invalid_count": resolved["invalid_count"],
        "citation_resolvable_rate": round(resolved["valid_count"] / resolved["total"], 4) if resolved["total"] else 0.0,
        "support_rate": support_rate, "hallucination_rate": hallucination_rate,
        "direct_fact_markers": direct, "inference_markers": infer,
        "inference_ratio": inference_ratio, "inference_heavy": inference_heavy,
        "distinct_cited_units": distinct_units, "guard_issues": issues,
        "guard_passed": not issues,
    }


def _guard_fallback_answer(result: dict, unit_ids: set[str], book_ids: set[str] | None = None) -> tuple[str, list[dict]]:
    """回退：只保留可解析引用并摘录其原文；无有效引用时用检索材料前 2 条。"""
    metrics = compute_guard_metrics(result, unit_ids, book_ids)
    citations = [c for c in (result.get("citations") or [])
                 if isinstance(c, dict) and str(c.get("unit_id")).lower() in
                 {str(x).lower() for x in metrics["citation_valid"]}]
    if not citations:
        citations = [r for r in (result.get("retrieved") or [])[:2] if isinstance(r, dict)]
    parts = [GUARD_FALLBACK_PREFIX]
    for item in citations[:3]:
        page = item.get("page")
        text = item.get("text") or item.get("preview") or ""
        parts.append(f"- [{item.get('unit_id')}] p.{page if page is not None else '?'} {str(text)[:160]}")
    if len(parts) == 1:
        parts.append("- 无可核验引用，建议人工复核或补检索。")
    return "\n".join(parts), [{**c, "unit_id": str(c.get("unit_id"))} for c in citations[:3]]


def apply_guard(result: dict, unit_ids: set[str], book_ids: set[str] | None = None,
                on_fail: str = "fallback") -> dict:
    """对 multi 结果执行质量守卫；返回新 result（不修改入参），payload 增加 guard 字段。"""
    on_fail = (on_fail or "fallback").strip().lower()
    if on_fail not in ACTIONS:
        raise GuardError(f"on_fail 只能是 {'/'.join(ACTIONS)}（收到 {on_fail!r}）")
    metrics = compute_guard_metrics(result, unit_ids, book_ids)
    guarded = dict(result)
    guarded.update(metrics)
    guarded["guard_action"] = "pass" if metrics["guard_passed"] else on_fail
    if metrics["guard_passed"]:
        return guarded
    if on_fail == "mark":
        return guarded
    if on_fail == "refuse":
        guarded.update({"answer": REFUSAL_ANSWER, "citations": [], "confidence": {"level": "none",
                        "reasons": ["质量守卫拒答：" + "、".join(metrics["guard_issues"])]},
                        "fallback": False, "refusal": True, "guarded": True})
        return guarded
    fallback_answer, fallback_citations = _guard_fallback_answer(result, unit_ids, book_ids)
    guarded.update({"answer": fallback_answer, "citations": fallback_citations,
                    "confidence": {"level": "low",
                                   "reasons": ["质量守卫回退：" + "、".join(metrics["guard_issues"])]},
                    "fallback": True, "refusal": False, "guarded": True})
    return guarded


# ---------------------------------------------------------------------------
# 评估：12 条 v3 抽验 multi 前后对比
# ---------------------------------------------------------------------------
def load_v3_questions(path: Path | str = V3_BANK, limit: int = DEFAULT_N) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise GuardError(f"v3 结果文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GuardError(f"v3 结果读取失败：{path}（{exc}）") from exc
    samples = (data.get("answer_validation") or {}).get("samples") or []
    questions = [{"id": s.get("id"), "question": s.get("question"), "category": s.get("category"),
                  "theme": s.get("theme")} for s in samples
                 if isinstance(s, dict) and s.get("question")]
    if not questions:
        raise GuardError(f"v3 answer_validation.samples 为空：{path}")
    return questions[:max(1, int(limit))]


def _summarize(rows: list[dict], prefix: str) -> dict:
    n = len(rows)
    answerable = sum(1 for r in rows if r.get(prefix, {}).get("answerable"))
    cites = sum(r.get(prefix, {}).get("citation_total") or 0 for r in rows)
    valid = sum(r.get(prefix, {}).get("citation_valid_count") or 0 for r in rows)
    support_num = sum((r.get(prefix, {}).get("support_rate") or 0)
                      * (r.get(prefix, {}).get("citation_total") or 0) for r in rows)
    infer = [r.get(prefix, {}).get("inference_ratio") or 0 for r in rows]
    return {"total": n, "answerable": answerable,
            "answerable_rate": round(answerable / n, 4) if n else 0.0,
            "citations_total": cites, "citations_valid": valid,
            "citation_resolvable_rate": round(valid / cites, 4) if cites else 1.0,
            "support_rate": round(support_num / cites, 4) if cites else 1.0,
            "hallucination_rate": round((cites - valid) / cites, 4) if cites else 0.0,
            "avg_inference_ratio": round(sum(infer) / n, 4) if n else 0.0}


def run_eval(n: int = DEFAULT_N, k: int = DEFAULT_K, on_guard_fail: str = "fallback",
             model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
             deadline_seconds: float = DEFAULT_DEADLINE, multi_fn=None,
             unit_ids: set[str] | None = None, book_ids: set[str] | None = None,
             bank_path: Path | str = V3_BANK, clock=time.monotonic) -> dict:
    """重跑 N 条 multi 并做 guard 前后对比（multi_fn 可注入 mock）。"""
    started = clock()
    if unit_ids is None or book_ids is None:
        unit_ids, book_ids = load_kb_ids()
    questions = load_v3_questions(bank_path, n)
    fn = multi_fn or (lambda q, **kw: kb_qa().ask(q, **kw))
    rows = []
    skipped = 0
    for q in questions:
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            continue
        item_started = clock()
        try:
            raw = fn(q["question"], topk=k, model=model, timeout=max(5.0, min(timeout, remaining)),
                     mode="multi")
        except Exception as exc:  # noqa: BLE001
            raw = {"question": q["question"], "answer": None, "citations": [], "retrieved": [],
                   "fallback": True, "inference_markers": 0, "direct_fact_markers": 0,
                   "error": f"{type(exc).__name__}: {exc}"}
        before = compute_guard_metrics(raw, unit_ids, book_ids)
        guarded = apply_guard(raw, unit_ids, book_ids, on_fail=on_guard_fail)
        after = compute_guard_metrics(guarded, unit_ids, book_ids)
        rows.append({"id": q.get("id"), "question": q.get("question"), "category": q.get("category"),
                     "theme": q.get("theme"),
                     "before": {**before, "guard_issues": before["guard_issues"]},
                     "after": {**after, "guard_action": guarded.get("guard_action"),
                               "guard_issues": guarded.get("guard_issues")},
                     "raw_answer": str(raw.get("answer") or "")[:300],
                     "guarded_answer": str(guarded.get("answer") or "")[:300],
                     "elapsed_seconds": round(clock() - item_started, 2)})
    before_sum = _summarize(rows, "before")
    after_sum = _summarize(rows, "after")
    guarded_rows = [r for r in rows if r.get("after", {}).get("guard_action") in ("fallback", "refuse")]
    issues: dict[str, int] = {}
    for row in rows:
        for issue in row["before"].get("guard_issues") or []:
            issues[issue] = issues.get(issue, 0) + 1
    delta = {"citation_resolvable_rate": round(after_sum["citation_resolvable_rate"] - before_sum["citation_resolvable_rate"], 4),
             "support_rate": round(after_sum["support_rate"] - before_sum["support_rate"], 4),
             "hallucination_rate": round(after_sum["hallucination_rate"] - before_sum["hallucination_rate"], 4),
             "answerable_rate": round(after_sum["answerable_rate"] - before_sum["answerable_rate"], 4)}
    restored = (after_sum["citation_resolvable_rate"] >= 0.99
                and after_sum["hallucination_rate"] <= 0.01
                and after_sum["support_rate"] >= before_sum["support_rate"])
    conclusion = {
        "restored_quality": bool(restored),
        "verdict": ("guard 恢复了引用可解析率/压低幻觉率，代价为可答率小幅下降，建议默认 fallback"
                    if restored else "guard 改善了部分质量指标但未完全恢复，建议先 mark 观察再收紧"),
        "reasons": [
            f"引用可解析率：{before_sum['citation_resolvable_rate']:.0%} → {after_sum['citation_resolvable_rate']:.0%}；"
            f"幻觉率：{before_sum['hallucination_rate']:.0%} → {after_sum['hallucination_rate']:.0%}；"
            f"支撑率：{before_sum['support_rate']:.0%} → {after_sum['support_rate']:.0%}。",
            f"可答率：{before_sum['answerable_rate']:.0%} → {after_sum['answerable_rate']:.0%}"
            f"（guard 拦截 {len(guarded_rows)} 条，action={on_guard_fail}）。",
            f"拦截原因分布：{json.dumps(issues, ensure_ascii=False)}。",
        ],
        "risks": [
            "fallback 答案虽引用可解析，但信息密度低于原 multi 答案；需人工抽查回退质量。",
            "inference_ratio 依赖模型输出【直接事实】/【推断】标记；模型不标注时比率为 0，可能漏检。",
            "引用可解析只验证 unit_id 存在性，不验证语义支撑（支撑率按「在本次检索材料内」近似）。",
            "n=12 为小样本，需扩样到 30–50 条复核 guard 影响。",
        ],
        "next_actions": [
            "在 kb_qa.ask(mode='multi') 出口接入 apply_guard（默认 on_fail='fallback'），payload 暴露 guard 字段。",
            "对 guard 拦截样本抽样人工评估回退答案质量，必要时改为 mark 灰度观察。",
            "扩样重跑 30–50 条 guard 前后指标。",
        ],
    }
    return {"schema_version": 1, "kind": "kb_qa_guard_eval",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"n_requested": int(n), "n_actual": len(rows), "skipped": skipped, "k": k,
                       "model": model, "timeout_seconds": timeout,
                       "deadline_seconds": deadline_seconds, "on_guard_fail": on_guard_fail,
                       "inference_ratio_max": INFERENCE_RATIO_MAX,
                       "min_distinct_units": MIN_DISTINCT_UNITS},
            "guard_rules": ["a) 引用可解析硬门（unit 集合/alias）", "b) 推断比例 ≤0.6",
                            "c) 有效被引单元 ≥2", "d) guard 字段写入 payload",
                            "e) on_guard_fail: mark/fallback/refuse"],
            "before": before_sum, "after": after_sum, "delta": delta,
            "issues": issues, "guarded_count": len(guarded_rows),
            "comparison": rows, "conclusion": conclusion,
            "disclaimer": "质量守卫评估仅供研究参考，不构成投资建议；引用/事实仍需人工复核。",
            "elapsed_seconds": round(clock() - started, 2)}


def render_markdown(payload: dict) -> str:
    b = payload["before"]
    a = payload["after"]
    d = payload["delta"]
    cfg = payload["config"]
    concl = payload["conclusion"]
    lines = [f"# multi 质量守卫评估（{payload['generated_at'][:10]}）", "",
             f"> v3 抽验 {cfg['n_actual']} 条 · multi（kb_qa.ask mode=multi）+ 后处理 guard · "
             f"on_guard_fail={cfg['on_guard_fail']} · 规则：引用可解析硬门 / 推断比例≤{cfg['inference_ratio_max']} / "
             f"≥{cfg['min_distinct_units']} 个不同单元。", "",
             "## guard 结论", "",
             f"**{'质量已恢复' if concl['restored_quality'] else '部分改善'}** —— {concl['verdict']}", ""]
    lines += [f"- {r}" for r in concl["reasons"]]
    lines += ["", "## guard 前后质量对比", "",
              "| 指标 | guard 前（raw multi） | guard 后 | Δ |", "|---|---|---|---|",
              f"| 可答率 | {b['answerable_rate']:.0%}（{b['answerable']}/{b['total']}） | "
              f"{a['answerable_rate']:.0%}（{a['answerable']}/{a['total']}） | {d['answerable_rate']:+.0%} |",
              f"| 引用可解析率 | {b['citation_resolvable_rate']:.0%} | **{a['citation_resolvable_rate']:.0%}** | "
              f"{d['citation_resolvable_rate']:+.0%} |",
              f"| 支撑率（引用在本次材料内） | {b['support_rate']:.0%} | **{a['support_rate']:.0%}** | "
              f"{d['support_rate']:+.0%} |",
              f"| 幻觉率（不可解析引用占比） | {b['hallucination_rate']:.0%} | **{a['hallucination_rate']:.0%}** | "
              f"{d['hallucination_rate']:+.0%} |",
              f"| 引用总数/有效数 | {b['citations_total']}/{b['citations_valid']} | "
              f"{a['citations_total']}/{a['citations_valid']} | — |",
              f"| 平均推断比例 | {b['avg_inference_ratio']:.0%} | {a['avg_inference_ratio']:.0%} | — |", "",
              f"guard 拦截：**{payload['guarded_count']}** 条（action={cfg['on_guard_fail']}）；"
              f"拦截原因分布：{json.dumps(payload['issues'], ensure_ascii=False)}", "",
              "## 逐条对比", "",
              "| # | 类别 | 问题 | 前：可答/解析/支撑/推断比 | guard 结果 | 后：可答/解析/幻觉 |",
              "|---|---|---|---|---|---|"]
    for i, row in enumerate(payload["comparison"], 1):
        rb = row.get("before") or {}
        ra = row.get("after") or {}
        lines.append(f"| {i} | {row.get('category')} | {str(row.get('question'))[:40]} | "
                     f"{'是' if rb.get('answerable') else '否'}/{rb.get('citation_resolvable_rate', 0):.0%}/"
                     f"{rb.get('support_rate', 0):.0%}/{rb.get('inference_ratio', 0):.0%} | "
                     f"{'pass' if ra.get('guard_action') == 'pass' else ra.get('guard_action')} | "
                     f"{'是' if ra.get('answerable') else '否'}/{ra.get('citation_resolvable_rate', 0):.0%}/"
                     f"{ra.get('hallucination_rate', 0):.0%} |")
    lines += ["", "## 风险与下一步", ""]
    lines += [f"- 风险：{r}" for r in concl["risks"]]
    lines += [f"- 下一步：{r}" for r in concl["next_actions"]]
    lines += ["", f"- {payload['disclaimer']}",
              f"- guard 规则：{'；'.join(payload['guard_rules'])}",
              f"- 耗时 {payload['elapsed_seconds']}s"]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict, out_json: Path | str = OUT_JSON,
                  out_md: Path | str = OUT_MD) -> tuple[Path, Path]:
    out_json, out_md = Path(out_json), Path(out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    return out_json, out_md


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="multi 答案质量守卫（引用硬门/推断比例/集中度）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="重跑 N 条 multi 并做 guard 前后对比")
    run.add_argument("--n", type=int, default=DEFAULT_N, help=f"样本条数（默认 {DEFAULT_N}）")
    run.add_argument("--k", type=int, default=DEFAULT_K)
    run.add_argument("--on-guard-fail", choices=list(ACTIONS), default="fallback")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    run.add_argument("--bank", default=str(V3_BANK))
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_eval(n=args.n, k=args.k, on_guard_fail=args.on_guard_fail,
                           model=args.model, timeout=args.timeout,
                           deadline_seconds=args.deadline, bank_path=args.bank)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except GuardError as exc:
        print(f"[kb_qa_guard] FAILED: {exc}", file=sys.stderr)
        return 1
    b, a = payload["before"], payload["after"]
    print(f"[kb_qa_guard] n={payload['config']['n_actual']} on_fail={payload['config']['on_guard_fail']} "
          f"guarded={payload['guarded_count']} issues={json.dumps(payload['issues'], ensure_ascii=False)}")
    print(f"[kb_qa_guard] before: answerable={b['answerable_rate']:.0%} resolve={b['citation_resolvable_rate']:.0%} "
          f"support={b['support_rate']:.0%} hallucination={b['hallucination_rate']:.0%}")
    print(f"[kb_qa_guard] after:  answerable={a['answerable_rate']:.0%} resolve={a['citation_resolvable_rate']:.0%} "
          f"support={a['support_rate']:.0%} hallucination={a['hallucination_rate']:.0%}")
    print(f"[kb_qa_guard] restored={payload['conclusion']['restored_quality']} "
          f"verdict={payload['conclusion']['verdict']}")
    print(f"[kb_qa_guard] json={paths[0]}")
    print(f"[kb_qa_guard] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
