# -*- coding: utf-8 -*-
# DEPRECATED（2026-11-02）：实验失败，禁止在生产路径引用；证据见 docs/plans/2026-11-02_提示词清单_活跃与废弃_v1.md
"""multi 答案质量提升：prompt v2 A/B（独立实验，不改变现有 kb_qa/kb_qa_multi 行为）。

prompt v2 强化约束（相对现 multi prompt 的增量）：
  1) 每条论断必须**紧跟** `[unit_id]` 引用（不可集中放末尾）；
  2) **优先直接摘录** claim/evidence 原文（原文优先于改写）；
  3) 推断必须显式标【推断】，且推断标记占比 **≤40%**；
  4) 引用至少覆盖 **2 个不同单元**；
  5) 不确定时直接拒答（不猜）。

A/B（真实 LLM 串行）：
  - v1 arm：现有 `kb_qa.ask(mode="multi", guard=True, on_guard_fail="fallback")`（结果可复用 wave24 验收记录）；
  - v2 arm：`kb_qa_multi.retrieve_clusters` 取同等材料 → prompt v2 → 本地 LLM → 同一 `kb_qa_guard` 后处理；
  - 统计：可答率 / guard 通过率 / fallback 率 / 引用数 / 推断比例 / 平均耗时；
输出 `research/reports/kb_qa_prompt_v2_20261101.{json,md}`，结论：v2 是否值得合入。

CLI：
  python tools/kb_corpus/kb_qa_prompt_v2.py run --n 12
  python tools/kb_corpus/kb_qa_prompt_v2.py run --n 8 --deadline 300   # LLM 慢时减条
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
ACCEPTANCE_JSON = REPO / "research" / "reports" / "qa_acceptance_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_prompt_v2_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_prompt_v2_20261101.md"
DEFAULT_N = 12
DEFAULT_K = 8
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 480
REFUSAL_MARK = "没有足够依据"


class PromptV2Error(RuntimeError):
    """prompt v2 A/B 异常。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise PromptV2Error(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_cache: dict = {}


def mod(name: str):
    if name not in _cache:
        _cache[name] = _load_module(f"{name}_for_prompt_v2", f"{name}.py")
    return _cache[name]


# ---------------------------------------------------------------------------
# ① prompt v2
# ---------------------------------------------------------------------------
def build_prompt_v2(question: str, clusters: list[dict]) -> str:
    """prompt v2：引用紧跟/原文优先/推断≤40%/≥2 单元/不确定拒答。"""
    materials = mod("kb_qa_multi").format_clusters(clusters)
    return (
        "你是研报知识库问答助手，必须严格遵守以下规则（v2 质量约束）：\n"
        "1) **每条论断必须紧跟出处**：写完一个论点立刻在句末标注 [unit_id] 与页码（如 [3ade35288309] p.2），"
        "不允许把所有引用堆在末尾；\n"
        "2) **原文优先**：优先直接摘录给定知识单元的 claim/evidence 原文表述，不得改写关键数据；\n"
        "3) **推断受限**：允许基于多条单元综合，但推断必须显式标注【推断】，且【推断】条数占全部论点比例 **≤40%**；"
        "直接事实用【事实】标注；\n"
        "4) **多单元覆盖**：答案必须引用 **至少 2 个不同知识单元**（unit_id）；\n"
        "5) **不确定就拒答**：材料不足以支撑结论时，直接回答「知识库中没有足够依据回答该问题。」，不要猜测。\n"
        "6) 中文，先结论后分点依据，不超过 400 字。\n\n"
        f"【问题】{question}\n\n【检索材料（按主题/章节聚类）】\n{materials}\n\n"
        "【回答（每个论点后紧跟 [unit_id] + 页码；推断标【推断】）】"
    )


# ---------------------------------------------------------------------------
# ② 两条 arm 的答案生成
# ---------------------------------------------------------------------------
def _classify(payload: dict) -> dict:
    answer = str(payload.get("answer") or "")
    confidence = payload.get("confidence") or {}
    fallback = bool(payload.get("fallback"))
    refusal = bool(payload.get("refusal")) or confidence.get("level") == "none" or REFUSAL_MARK in answer
    if fallback:
        status = "fallback"
    elif refusal:
        status = "no_evidence"
    else:
        status = "success"
    return {"status": status, "success": status == "success",
            "no_evidence": status == "no_evidence", "fallback": status == "fallback",
            "guard_passed": payload.get("guard_passed"),
            "guard_action": payload.get("guard_action"),
            "guard_issues": payload.get("guard_issues") or [],
            "citations": len(payload.get("citations") or []),
            "citation_resolvable_rate": payload.get("citation_resolvable_rate"),
            "inference_ratio": payload.get("inference_ratio"),
            "distinct_cited_units": payload.get("distinct_cited_units"),
            "answer_length": len(answer),
            "answer_preview": answer[:160],
            "elapsed_seconds": payload.get("elapsed_seconds"),
            "error": payload.get("error")}


def answer_v1(question: str, k: int = DEFAULT_K, chat_fn=None, model: str = DEFAULT_MODEL,
              timeout: float = DEFAULT_TIMEOUT) -> dict:
    """v1 arm：现有 multi+guard CLI 链路。"""
    started = time.monotonic()
    try:
        payload = mod("kb_qa").ask(question, topk=k, model=model, timeout=timeout,
                                   mode="multi", guard=True, on_guard_fail="fallback")
    except Exception as exc:  # noqa: BLE001
        payload = {"answer": None, "citations": [], "confidence": {"level": "none"},
                   "fallback": True, "refusal": False,
                   "error": f"{type(exc).__name__}: {exc}"}
    out = _classify(payload)
    out["elapsed_seconds"] = round(time.monotonic() - started, 2)
    out["arm"] = "v1"
    return out


def _raw_from_answer(question: str, answer: str, clusters: list[dict], multi) -> dict:
    """把 v2 答案规整为 multi 结果结构（引用/检索材料/标记），供 guard 使用。"""
    check = multi.validate_multi_answer(answer, clusters)
    retrieved = []
    for cluster in clusters:
        for unit in cluster.get("units") or []:
            retrieved.append({"unit_id": unit.get("unit_id"), "book": unit.get("book"),
                              "chapter": unit.get("chapter"), "page": unit.get("page"),
                              "kind": "units", "text": unit.get("text")})
    return {"question": question, "answer": answer,
            "citations": check.get("citations") or [],
            "retrieved": retrieved,
            "fallback": False, "refusal": bool(check.get("refusal")),
            "inference_markers": int(check.get("inference_markers") or 0),
            "direct_fact_markers": int(check.get("direct_fact_markers") or 0),
            "cluster_count": len(clusters),
            "citation_check": {"hallucinated_ids": check.get("hallucinated_ids") or [],
                               "citation_missing": bool(check.get("citation_missing"))},
            "clusters": [{"label": c.get("label")} for c in clusters]}


def answer_v2(question: str, k: int = DEFAULT_K, chat_fn=None, search_fn=None,
              model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """v2 arm：同材料 + prompt v2 → LLM → 同一 guard 后处理。"""
    started = time.monotonic()
    multi = mod("kb_qa_multi")
    guard_mod = mod("kb_qa_guard")
    try:
        clusters = multi.retrieve_clusters(question, k=k, search_fn=search_fn)
        prompt = build_prompt_v2(question, clusters)
        chat = chat_fn or multi.call_ollama
        try:
            answer = chat(prompt, model=model, timeout=timeout)
            raw = _raw_from_answer(question, answer, clusters, multi)
        except Exception as exc:  # noqa: BLE001 - LLM 失败 → 规则摘录降级
            raw = _raw_from_answer(question, multi.rule_based_multi(clusters), clusters, multi)
            raw.update({"fallback": True, "error": f"{type(exc).__name__}: {exc}"})
        unit_ids, book_ids = guard_mod.load_kb_ids()
        guarded = guard_mod.apply_guard(raw, unit_ids, book_ids, on_fail="fallback")
    except Exception as exc:  # noqa: BLE001
        guarded = {"answer": None, "citations": [], "confidence": {"level": "none"},
                   "fallback": True, "refusal": False,
                   "error": f"{type(exc).__name__}: {exc}"}
    out = _classify(guarded)
    if guarded.get("error"):
        out["error"] = guarded["error"]
    out["elapsed_seconds"] = round(time.monotonic() - started, 2)
    out["arm"] = "v2"
    out["cluster_count"] = len(guarded.get("clusters") or [])
    return out


def load_v1_reuse(path: Path | str = ACCEPTANCE_JSON) -> dict:
    """复用 wave24 验收的 v1 结果（成功/拒答/fallback、guard、引用、推断比例）。"""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    reuse = {}
    for row in (data.get("cli") or {}).get("rows") or []:
        if not isinstance(row, dict) or not row.get("question"):
            continue
        reuse[row.get("id")] = {
            "status": row.get("status"), "success": row.get("status") == "success",
            "no_evidence": row.get("status") == "no_evidence",
            "fallback": row.get("status") == "fallback",
            "guard_passed": row.get("guard_passed"), "guard_action": row.get("guard_action"),
            "guard_issues": row.get("guard_issues") or [],
            "citations": int(row.get("citations") or 0),
            "citation_resolvable_rate": row.get("citation_resolvable_rate"),
            "inference_ratio": row.get("inference_ratio"),
            "distinct_cited_units": row.get("distinct_cited_units"),
            "answer_length": int(row.get("answer_length") or 0),
            "answer_preview": row.get("answer_preview"),
            "elapsed_seconds": row.get("elapsed_seconds"),
            "arm": "v1", "reused": True,
        }
    return reuse


# ---------------------------------------------------------------------------
# ③ 统计 / 对比 / 结论
# ---------------------------------------------------------------------------
def arm_stats(rows: list[dict]) -> dict:
    total = len(rows)
    success = sum(1 for r in rows if r.get("success"))
    no_evidence = sum(1 for r in rows if r.get("no_evidence"))
    fallback = sum(1 for r in rows if r.get("fallback"))
    guard_passed = sum(1 for r in rows if r.get("guard_passed") is True)
    guard_failed = sum(1 for r in rows if r.get("guard_passed") is False)
    cites = [int(r.get("citations") or 0) for r in rows]
    infer = [float(r.get("inference_ratio") or 0.0) for r in rows]
    elapsed = [float(r.get("elapsed_seconds") or 0.0) for r in rows]
    return {"total": total, "success": success, "no_evidence": no_evidence, "fallback": fallback,
            "answerable_rate": round(success / total, 4) if total else 0.0,
            "fallback_rate": round(fallback / total, 4) if total else 0.0,
            "guard_passed": guard_passed, "guard_failed": guard_failed,
            "guard_pass_rate": round(guard_passed / total, 4) if total else 0.0,
            "avg_citations": round(sum(cites) / total, 2) if total else 0.0,
            "avg_inference_ratio": round(sum(infer) / total, 4) if total else 0.0,
            "avg_elapsed_seconds": round(sum(elapsed) / total, 2) if total else 0.0,
            "errors": [r.get("error") for r in rows if r.get("error")]}


def build_conclusion(v1: dict, v2: dict) -> dict:
    d_success = round(v2["answerable_rate"] - v1["answerable_rate"], 4)
    d_guard = round(v2["guard_pass_rate"] - v1["guard_pass_rate"], 4)
    d_infer = round(v2["avg_inference_ratio"] - v1["avg_inference_ratio"], 4)
    d_elapsed = round(v2["avg_elapsed_seconds"] - v1["avg_elapsed_seconds"], 2)
    if v2["errors"]:
        verdict, merge = "v2 存在调用错误，先修稳定性再评估合入", "hold"
    elif d_success >= 0.10 and d_guard >= 0.05:
        verdict, merge = "v2 显著提升可答率与 guard 通过率，建议合入（灰度）", "merge"
    elif d_success > 0 and (d_guard >= 0 or d_infer < 0):
        verdict, merge = "v2 有小幅质量提升，建议扩样（30–50 条）验证后再合入", "expand"
    elif d_success == 0 and d_infer < 0:
        verdict, merge = "可答率持平且推断比例下降，建议扩样验证后合入", "expand"
    else:
        verdict, merge = "v2 未优于 v1，不建议合入；保留现有 prompt 继续调优", "keep"
    reasons = [
        f"可答率：v1 {v1['answerable_rate']:.0%}（{v1['success']}/{v1['total']}）→ "
        f"v2 {v2['answerable_rate']:.0%}（{v2['success']}/{v2['total']}），Δ{d_success:+.0%}。",
        f"guard 通过率：v1 {v1['guard_pass_rate']:.0%} → v2 {v2['guard_pass_rate']:.0%}，Δ{d_guard:+.0%}；"
        f"fallback 率：{v1['fallback_rate']:.0%} → {v2['fallback_rate']:.0%}。",
        f"平均引用：{v1['avg_citations']} → {v2['avg_citations']}；"
        f"平均推断比例：{v1['avg_inference_ratio']} → {v2['avg_inference_ratio']}（Δ{d_infer:+.2f}）；"
        f"平均耗时：{v1['avg_elapsed_seconds']}s → {v2['avg_elapsed_seconds']}s（Δ{d_elapsed:+.1f}s）。",
    ]
    risks = [
        "样本受限（LLM 慢时先 12 条），结论为 effective 子集，需扩样 30–50 条复核。",
        "v2 prompt 更长，耗时可能上升；本 A/B 的 v1 部分结果复用 wave24，时间点不同。",
        "guard 后处理对两条 arm 相同，质量差异主要来自 prompt；推断标记依赖模型遵守指令。",
        "若合入，需在 kb_qa_multi 中新增 prompt 版本开关并灰度，保留 v1 回退。",
    ]
    return {"merge_recommendation": merge, "verdict": verdict, "reasons": reasons, "risks": risks,
            "delta": {"success_rate": d_success, "guard_pass_rate": d_guard,
                      "avg_inference_ratio": d_infer, "avg_elapsed_seconds": d_elapsed}}


# ---------------------------------------------------------------------------
# ④ 运行 + 输出
# ---------------------------------------------------------------------------
def run_ab(n: int = DEFAULT_N, k: int = DEFAULT_K, model: str = DEFAULT_MODEL,
           timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
           reuse_v1: bool = True, chat_fn_v1=None, chat_fn_v2=None, search_fn=None,
           v3_bank: Path | str = V3_BANK, acceptance_json: Path | str = ACCEPTANCE_JSON,
           clock=time.monotonic) -> dict:
    started = clock()
    questions = _load_questions(v3_bank, n)
    reuse = load_v1_reuse(acceptance_json) if reuse_v1 else {}
    v1_rows, v2_rows = [], []
    live_v1 = live_v2 = skipped = 0
    for q in questions:
        reused = reuse.get(q.get("id"))
        if reused:
            v1_rows.append({**reused, "id": q.get("id"), "question": q["question"],
                            "category": q.get("category")})
        else:
            remaining = deadline_seconds - (clock() - started)
            if remaining <= 1:
                skipped += 1
            else:
                row = answer_v1(q["question"], k=k, chat_fn=chat_fn_v1, model=model,
                                timeout=min(timeout, remaining))
                row.update({"id": q.get("id"), "question": q["question"], "category": q.get("category")})
                v1_rows.append(row)
                live_v1 += 1
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            continue
        row2 = answer_v2(q["question"], k=k, chat_fn=chat_fn_v2, search_fn=search_fn,
                         model=model, timeout=min(timeout, remaining))
        row2.update({"id": q.get("id"), "question": q["question"], "category": q.get("category")})
        v2_rows.append(row2)
        live_v2 += 1
    v1, v2 = arm_stats(v1_rows), arm_stats(v2_rows)
    conclusion = build_conclusion(v1, v2)
    return {"schema_version": 1, "kind": "kb_qa_prompt_v2_ab",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"n_requested": int(n), "n_actual": len(v2_rows), "k": k, "model": model,
                       "timeout_seconds": timeout, "deadline_seconds": deadline_seconds,
                       "reuse_v1": bool(reuse_v1), "live_v1": live_v1, "live_v2": live_v2,
                       "skipped": skipped,
                       "prompt_v2_rules": ["引用紧跟", "原文优先", "推断≤40% 且标【推断】",
                                           "最少 2 个单元", "不确定拒答"]},
            "v1": {"summary": v1, "rows": v1_rows},
            "v2": {"summary": v2, "rows": v2_rows},
            "comparison": conclusion,
            "disclaimer": "prompt A/B 结果仅供研究参考，不构成投资建议；答案需人工复核。",
            "elapsed_seconds": round(clock() - started, 2)}


def _load_questions(path, n):
    path = Path(path)
    if not path.exists():
        raise PromptV2Error(f"v3 问题库不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    qs = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if not qs:
        raise PromptV2Error(f"v3 问题库 questions 为空：{path}")
    return qs[:max(1, int(n))]


def _pct(v):
    return f"{v:.0%}" if isinstance(v, (int, float)) else "—"


def render_markdown(payload: dict) -> str:
    v1 = payload["v1"]["summary"]
    v2 = payload["v2"]["summary"]
    cmp = payload["comparison"]
    cfg = payload["config"]
    lines = [f"# multi prompt v2 A/B（{payload['generated_at'][:10]}）", "",
             f"> v3 问题库前 **{cfg['n_actual']}** 条 · v1 = 现 multi prompt（`kb_qa.ask(mode=multi, guard)`，"
             f"复用 {v1['total'] - cfg['live_v1']} 条 wave24 结果）· v2 = prompt v2（引用紧跟/原文优先/"
             f"推断≤40%/≥2 单元/不确定拒答）+ 同一 guard · qwen3.5:9b 串行。", "",
             "## 结论", "",
             f"**{cmp['verdict']}** —— 建议：`{cmp['merge_recommendation']}`（merge=合入 / expand=扩样 / keep=保留 v1 / hold=先修）", ""]
    lines += [f"- {r}" for r in cmp["reasons"]]
    lines += ["", "## A/B 对比", "",
              "| 指标 | v1（现 multi prompt） | v2（严格 prompt） | Δ |", "|---|---|---|---|",
              f"| 可答率 | {v1['answerable_rate']:.0%}（{v1['success']}/{v1['total']}） | "
              f"{v2['answerable_rate']:.0%}（{v2['success']}/{v2['total']}） | {cmp['delta']['success_rate']:+.0%} |",
              f"| guard 通过率 | {v1['guard_pass_rate']:.0%}（{v1['guard_passed']}/{v1['total']}） | "
              f"{v2['guard_pass_rate']:.0%}（{v2['guard_passed']}/{v2['total']}） | "
              f"{cmp['delta']['guard_pass_rate']:+.0%} |",
              f"| fallback 率 | {v1['fallback_rate']:.0%} | {v2['fallback_rate']:.0%} | — |",
              f"| 拒答数 | {v1['no_evidence']} | {v2['no_evidence']} | — |",
              f"| 平均引用数 | {v1['avg_citations']} | {v2['avg_citations']} | — |",
              f"| 平均推断比例 | {v1['avg_inference_ratio']} | {v2['avg_inference_ratio']} | "
              f"{cmp['delta']['avg_inference_ratio']:+.2f} |",
              f"| 平均耗时(s) | {v1['avg_elapsed_seconds']} | {v2['avg_elapsed_seconds']} | "
              f"{cmp['delta']['avg_elapsed_seconds']:+.1f}s |",
              "", "## 逐条对比", "",
              "| # | 类别 | 问题 | v1 状态/guard/引用 | v2 状态/guard/引用 | v1 推断 | v2 推断 |",
              "|---|---|---|---|---|---|---|"]
    for idx, (r1, r2) in enumerate(zip(payload["v1"]["rows"], payload["v2"]["rows"]), 1):
        lines.append(f"| {idx} | {r1.get('category')} | {str(r1.get('question'))[:36]} | "
                     f"{r1.get('status')}/{r1.get('guard_passed')}/{r1.get('citations')} | "
                     f"{r2.get('status')}/{r2.get('guard_passed')}/{r2.get('citations')} | "
                     f"{r1.get('inference_ratio')} | {r2.get('inference_ratio')} |")
    lines += ["", "## prompt v2 规则", ""]
    lines += [f"- {r}" for r in cfg["prompt_v2_rules"]]
    lines += ["", "## 风险与说明", ""]
    lines += [f"- 风险：{r}" for r in cmp["risks"]]
    lines += ["", f"- {payload['disclaimer']}", f"- 总耗时 {payload['elapsed_seconds']}s（含 LLM）"]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict, out_json: Path | str = OUT_JSON,
                  out_md: Path | str = OUT_MD) -> tuple[Path, Path]:
    out_json, out_md = Path(out_json), Path(out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    return out_json, out_md


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="multi prompt v2 A/B（引用紧跟/推断≤40%/多单元/拒答）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑 prompt v1 vs v2 A/B 并输出 JSON+MD")
    run.add_argument("--n", type=int, default=DEFAULT_N, help=f"样本条数（默认 {DEFAULT_N}）")
    run.add_argument("--k", type=int, default=DEFAULT_K)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    run.add_argument("--no-reuse-v1", dest="reuse", action="store_false", default=True)
    run.add_argument("--v3", default=str(V3_BANK))
    run.add_argument("--acceptance-json", default=str(ACCEPTANCE_JSON))
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_ab(args.n, k=args.k, model=args.model, timeout=args.timeout,
                         deadline_seconds=args.deadline, reuse_v1=args.reuse,
                         v3_bank=args.v3, acceptance_json=args.acceptance_json)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except PromptV2Error as exc:
        print(f"[kb_qa_prompt_v2] FAILED: {exc}", file=sys.stderr)
        return 1
    v1, v2, cmp = payload["v1"]["summary"], payload["v2"]["summary"], payload["comparison"]
    print(f"[kb_qa_prompt_v2] n={payload['config']['n_actual']} live_v1={payload['config']['live_v1']} "
          f"live_v2={payload['config']['live_v2']} skipped={payload['config']['skipped']}")
    print(f"[kb_qa_prompt_v2] v1: answerable={v1['answerable_rate']:.0%} guard={v1['guard_pass_rate']:.0%} "
          f"fallback={v1['fallback_rate']:.0%} cites={v1['avg_citations']} infer={v1['avg_inference_ratio']} "
          f"elapsed={v1['avg_elapsed_seconds']}s")
    print(f"[kb_qa_prompt_v2] v2: answerable={v2['answerable_rate']:.0%} guard={v2['guard_pass_rate']:.0%} "
          f"fallback={v2['fallback_rate']:.0%} cites={v2['avg_citations']} infer={v2['avg_inference_ratio']} "
          f"elapsed={v2['avg_elapsed_seconds']}s")
    print(f"[kb_qa_prompt_v2] verdict={cmp['verdict']} recommendation={cmp['merge_recommendation']}")
    print(f"[kb_qa_prompt_v2] json={paths[0]}")
    print(f"[kb_qa_prompt_v2] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
