# -*- coding: utf-8 -*-
"""guard 阈值 0.6 vs 0.7 扩样 A/B：同一批 multi 答案离线重算两种阈值的 guard 结果。

背景：wave22 在 12 条上推荐 inference_ratio 阈值 0.7（通过率不变、inference 误拦截 4→1），
需扩样到 30 条验证。本模块：
  ① 样本：v3 问题库前 N 条（默认 30，类别均衡选择由问题库顺序给出）；
  ② 复用已有结果：wave19 `kb_qa_guard_20261101.json` 前 12 条原始 multi 指标；
     其余问题用 `kb_qa.ask(mode="multi", guard=True, on_guard_fail="fallback")` 生成
     （payload 保留 raw guard 指标：answerable/citation_total/invalid/distinct/inference_ratio 等）；
  ③ 对**同一批答案**离线分别按 inference 阈值 0.6 与 0.7（min units=2）重算 guard：
     通过率 / fallback 率 / inference 误拦截数 / 引用可解析率 / 支撑率 / 幻觉率；
  ④ 输出 `research/reports/guard_threshold_ab_20261101.{json,md}`，
     结论：0.7 是否更优、是否回退 0.6。
  不调 LLM 重算 guard（只对已生成答案做离线模拟），不改 guard 代码。

CLI：
  python tools/kb_corpus/guard_threshold_ab.py run --n 30
  python tools/kb_corpus/guard_threshold_ab.py run --n 20 --deadline 300   # LLM 慢时缩样
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
GUARD_JSON = REPO / "research" / "reports" / "kb_qa_guard_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "guard_threshold_ab_20261101.json"
OUT_MD = REPO / "research" / "reports" / "guard_threshold_ab_20261101.md"
THRESHOLDS = (0.6, 0.7)
MIN_UNITS = 2
DEFAULT_N = 30
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 480


class AbError(RuntimeError):
    """阈值 A/B 异常（输入缺失等）。"""


def _load_kb_qa():
    path = Path(__file__).with_name("kb_qa.py")
    spec = importlib.util.spec_from_file_location("kb_qa_for_threshold_ab", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise AbError(f"无法加载 kb_qa.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# ① 样本 + ② 复用/生成 raw multi 指标
# ---------------------------------------------------------------------------
def load_v3_questions(path: Path | str = V3_BANK, n: int = DEFAULT_N) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise AbError(f"v3 问题库不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AbError(f"v3 问题库读取失败：{path}（{exc}）") from exc
    questions = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if not questions:
        raise AbError(f"v3 问题库 questions 为空：{path}")
    return questions[:max(1, int(n))]


def load_reused_rows(path: Path | str = GUARD_JSON) -> dict:
    """从 wave19 guard 结果复用前 12 条 raw multi 指标（before 字段完整）。"""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = {}
    for row in data.get("comparison") or []:
        if not isinstance(row, dict):
            continue
        before = row.get("before") or {}
        if not before:
            continue
        rows[row.get("id")] = {
            "id": row.get("id"), "question": row.get("question"),
            "category": row.get("category"), "theme": row.get("theme"),
            "answerable": bool(before.get("answerable")),
            "citation_total": int(before.get("citation_total") or 0),
            "citation_invalid_count": int(before.get("citation_invalid_count") or 0),
            "distinct_cited_units": int(before.get("distinct_cited_units") or 0),
            "inference_ratio": float(before.get("inference_ratio") or 0.0),
            "citation_resolvable_rate": before.get("citation_resolvable_rate"),
            "support_rate": before.get("support_rate"),
            "hallucination_rate": before.get("hallucination_rate"),
            "source": "reused_wave19",
        }
    return rows


def _row_from_payload(question: dict, payload: dict) -> dict:
    """从 kb_qa.ask(mode=multi, guard) payload 提取 raw guard 指标（守卫只换答案，指标保留）。"""
    confidence = payload.get("confidence") or {}
    fallback = bool(payload.get("fallback"))
    refusal = bool(payload.get("refusal")) or confidence.get("level") == "none"
    answerable = bool(payload.get("answerable"))
    if "answerable" not in payload:
        answerable = bool(payload.get("answer")) and not fallback and not refusal
    citations_valid = payload.get("citation_valid") or []
    citations_invalid = payload.get("citation_invalid") or []
    return {
        "id": question.get("id"), "question": question.get("question"),
        "category": question.get("category"), "theme": question.get("theme"),
        "answerable": answerable,
        "citation_total": int(payload.get("citation_total") or (len(citations_valid) + len(citations_invalid))),
        "citation_invalid_count": int(payload.get("citation_invalid_count") or len(citations_invalid)),
        "distinct_cited_units": int(payload.get("distinct_cited_units") or len(set(citations_valid))),
        "inference_ratio": float(payload.get("inference_ratio") or 0.0),
        "citation_resolvable_rate": payload.get("citation_resolvable_rate"),
        "support_rate": payload.get("support_rate"),
        "hallucination_rate": payload.get("hallucination_rate"),
        "source": "live",
        "guard_passed_live": payload.get("guard_passed"),
        "guard_actions_live": payload.get("guard_action"),
    }


# ---------------------------------------------------------------------------
# ③ 离线模拟两种阈值
# ---------------------------------------------------------------------------
def issues_for(row: dict, inference_max: float, min_units: int = MIN_UNITS) -> list[str]:
    issues = []
    if int(row.get("citation_invalid_count") or 0) > 0:
        issues.append("citation_invalid")
    if int(row.get("citation_total") or 0) == 0:
        issues.append("citation_missing")
    if int(row.get("distinct_cited_units") or 0) < min_units:
        issues.append("insufficient_cited_units")
    if float(row.get("inference_ratio") or 0.0) > inference_max:
        issues.append("inference_heavy")
    return issues


def simulate(rows: list[dict], inference_max: float, min_units: int = MIN_UNITS) -> dict:
    """按指定 inference 阈值离线模拟 guard：通过/拦截 + 代理质量（被拦截行按 fallback 后指标）。"""
    passed = blocked = 0
    reasons: dict[str, int] = {}
    citations_total = 0
    resolvable_num = support_num = halluc_num = 0.0
    inference_post: list[float] = []
    answerable_preserved = 0
    for row in rows:
        issues = issues_for(row, inference_max, min_units)
        c_total = int(row.get("citation_total") or 0)
        citations_total += c_total
        if issues:
            blocked += 1
            for issue in issues:
                reasons[issue] = reasons.get(issue, 0) + 1
            # fallback 后：仅保留可解析引用，推断密度记 0（保守代理）
            resolvable_num += 1.0 * c_total
            support_num += 1.0 * c_total
            inference_post.append(0.0)
        else:
            passed += 1
            resolvable_num += float(row.get("citation_resolvable_rate") or 0.0) * c_total
            support_num += float(row.get("support_rate") or 0.0) * c_total
            halluc_num += float(row.get("hallucination_rate") or 0.0) * c_total
            inference_post.append(float(row.get("inference_ratio") or 0.0))
            answerable_preserved += 1 if row.get("answerable") else 0
    total = len(rows)
    return {
        "inference_max": inference_max, "min_units": min_units, "total": total,
        "passed": passed, "blocked": blocked,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "fallback_rate": round(blocked / total, 4) if total else 0.0,
        "block_reasons": reasons,
        "answerable_preserved": answerable_preserved,
        "answerable_rate_post": round(answerable_preserved / total, 4) if total else 0.0,
        "citation_resolvable_rate_post": round(resolvable_num / citations_total, 4) if citations_total else None,
        "support_rate_post": round(support_num / citations_total, 4) if citations_total else None,
        "hallucination_rate_post": round(halluc_num / citations_total, 4) if citations_total else None,
        "avg_inference_density_post": round(sum(inference_post) / len(inference_post), 4) if inference_post else None,
    }


def inference_false_blocks(rows: list[dict], min_units: int = MIN_UNITS) -> list[dict]:
    """0.6 下仅因 inference_heavy 被拦、而 0.7 下本可通过的样本（误拦截）。"""
    out = []
    for row in rows:
        issues06 = issues_for(row, 0.6, min_units)
        issues07 = issues_for(row, 0.7, min_units)
        if "inference_heavy" in issues06 and "inference_heavy" not in issues07 and not issues07:
            out.append({"id": row.get("id"), "inference_ratio": row.get("inference_ratio"),
                        "question": row.get("question")})
    return out


def compare_thresholds(rows: list[dict]) -> dict:
    s06 = simulate(rows, 0.6)
    s07 = simulate(rows, 0.7)
    false_blocks = inference_false_blocks(rows)
    delta = {
        "pass_rate": round(s07["pass_rate"] - s06["pass_rate"], 4),
        "fallback_rate": round(s07["fallback_rate"] - s06["fallback_rate"], 4),
        "answerable_rate_post": round(s07["answerable_rate_post"] - s06["answerable_rate_post"], 4),
        "citation_resolvable_rate_post": (round((s07["citation_resolvable_rate_post"] or 0)
                                                - (s06["citation_resolvable_rate_post"] or 0), 4)
                                          if s06["citation_resolvable_rate_post"] is not None else None),
        "support_rate_post": (round((s07["support_rate_post"] or 0) - (s06["support_rate_post"] or 0), 4)
                              if s06["support_rate_post"] is not None else None),
        "hallucination_rate_post": (round((s07["hallucination_rate_post"] or 0)
                                          - (s06["hallucination_rate_post"] or 0), 4)
                                    if s06["hallucination_rate_post"] is not None else None),
    }
    worse_quality = ((delta["citation_resolvable_rate_post"] or 0) < -0.01
                     or (delta["support_rate_post"] or 0) < -0.01
                     or (delta["hallucination_rate_post"] or 0) > 0.01)
    if worse_quality:
        verdict = "回退 0.6：0.7 虽减少误拦截，但代理质量指标变差"
        recommend = 0.6
    elif delta["pass_rate"] > 0 or len(false_blocks) > 0:
        verdict = "维持 0.7：通过/可答率提升（或误拦截减少）且代理质量不降"
        recommend = 0.7
    else:
        verdict = "维持 0.7 安全（本批与 0.6 完全等价，无回退必要）"
        recommend = 0.7
    return {"threshold_0_6": s06, "threshold_0_7": s07, "delta": delta,
            "inference_false_blocks": false_blocks,
            "inference_false_block_count": len(false_blocks),
            "recommended_inference_max": recommend,
            "verdict": verdict,
            "reasons": [
                f"通过率：0.6 → {s06['pass_rate']:.0%}（{s06['passed']}/{s06['total']}）；"
                f"0.7 → {s07['pass_rate']:.0%}（{s07['passed']}/{s07['total']}），Δ{delta['pass_rate']:+.0%}。",
                f"fallback 率：{s06['fallback_rate']:.0%} → {s07['fallback_rate']:.0%}；"
                f"仅因 inference_heavy 的误拦截：{len(false_blocks)} 条。",
                f"代理质量：可解析率 {s06['citation_resolvable_rate_post']} → {s07['citation_resolvable_rate_post']}；"
                f"支撑率 {s06['support_rate_post']} → {s07['support_rate_post']}；"
                f"幻觉率 {s06['hallucination_rate_post']} → {s07['hallucination_rate_post']}。",
            ],
            "risks": [
                "被拦截行的代理质量按 fallback 后指标（可解析 100%/推断 0）估计，属保守假设，非真实答案质量。",
                "样本受限：若 LLM 慢只跑前 N 条，结论为 effective 子集，需注明。",
                "inference_ratio 依赖模型输出【直接事实】/【推断】标记，未标注时比率 0 可能低估计。",
                "阈值切换属参数调整，不改 guard 代码；建议灰度并保留 0.6 回退能力。",
            ]}


# ---------------------------------------------------------------------------
# ④ 运行 + 输出
# ---------------------------------------------------------------------------
def run_ab(n: int = DEFAULT_N, model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
           deadline_seconds: float = DEFAULT_DEADLINE, ask_fn=None,
           v3_bank: Path | str = V3_BANK, guard_json: Path | str = GUARD_JSON,
           clock=time.monotonic) -> dict:
    started = clock()
    questions = load_v3_questions(v3_bank, n)
    reused = load_reused_rows(guard_json)
    fn = ask_fn or (lambda q, **kw: _load_kb_qa().ask(q, **kw))
    rows = []
    live_calls = 0
    skipped = 0
    for q in questions:
        reused_row = reused.get(q.get("id"))
        if reused_row:
            rows.append({**reused_row, "question": q.get("question"), "category": q.get("category"),
                         "theme": q.get("theme")})
            continue
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            continue
        item_started = clock()
        try:
            payload = fn(q["question"], topk=8, model=model, timeout=max(5.0, min(timeout, remaining)),
                         mode="multi", guard=True, on_guard_fail="fallback")
        except Exception as exc:  # noqa: BLE001 - 单条失败记入行（按无答案处理）
            payload = {"answer": None, "citations": [], "confidence": {"level": "none"},
                       "fallback": True, "refusal": False,
                       "error": f"{type(exc).__name__}: {exc}"}
        row = _row_from_payload(q, payload)
        row["elapsed_seconds"] = round(clock() - item_started, 2)
        if payload.get("error"):
            row["error"] = payload["error"]
        rows.append(row)
        live_calls += 1
    comparison = compare_thresholds(rows)
    return {
        "schema_version": 1, "kind": "guard_threshold_ab",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources": {"v3_bank": str(v3_bank), "reused_guard_json": str(guard_json)},
        "config": {"n_requested": int(n), "n_actual": len(rows), "skipped": skipped,
                   "reused_rows": len(rows) - live_calls, "live_calls": live_calls,
                   "model": model, "timeout_seconds": timeout,
                   "deadline_seconds": deadline_seconds,
                   "thresholds": list(THRESHOLDS), "min_units": MIN_UNITS},
        "note": ("同一批 multi 答案离线重算 0.6/0.7 两种阈值；前 12 条复用 wave19 guard 原始指标，"
                 "其余现场生成（guard=True/fallback，payload 保留 raw 指标）；不重复调 LLM 做阈值模拟"),
        "comparison": {k: v for k, v in comparison.items() if k in
                       ("threshold_0_6", "threshold_0_7", "delta", "inference_false_block_count",
                        "inference_false_blocks", "recommended_inference_max", "verdict",
                        "reasons", "risks")},
        "rows": rows,
        "disclaimer": "阈值 A/B 结果仅供研究参考，不构成投资建议；代理质量指标需人工复核。",
        "elapsed_seconds": round(clock() - started, 2),
    }


def render_markdown(payload: dict) -> str:
    cmp = payload["comparison"]
    s06, s07 = cmp["threshold_0_6"], cmp["threshold_0_7"]
    delta = cmp["delta"]
    cfg = payload["config"]
    lines = [f"# guard 阈值 0.6 vs 0.7 扩样 A/B（{payload['generated_at'][:10]}）", "",
             f"> v3 问题库前 **{cfg['n_actual']}** 条（申请 {cfg['n_requested']}，跳过 {cfg['skipped']}）· "
             f"复用已有结果 {cfg['reused_rows']} 条 · 现场生成 {cfg['live_calls']} 条 · "
             f"同一批答案离线重算 guard 阈值 {cmp['threshold_0_6']['inference_max']} vs "
             f"{cmp['threshold_0_7']['inference_max']}（min units={cfg['min_units']}）。", "",
             "## 结论", "",
             f"**{cmp['verdict']}** —— 推荐 inference_ratio 阈值 = **{cmp['recommended_inference_max']}**", ""]
    lines += [f"- {r}" for r in cmp["reasons"]]
    lines += ["", "## 0.6 vs 0.7 对比", "",
              "| 指标 | 0.6（原默认） | 0.7（wave22 推荐） | Δ |", "|---|---|---|---|",
              f"| 通过率 | {s06['pass_rate']:.0%}（{s06['passed']}/{s06['total']}） | "
              f"{s07['pass_rate']:.0%}（{s07['passed']}/{s07['total']}） | {delta['pass_rate']:+.0%} |",
              f"| fallback 率 | {s06['fallback_rate']:.0%} | {s07['fallback_rate']:.0%} | {delta['fallback_rate']:+.0%} |",
              f"| 仅 inference_heavy 误拦截 | {cmp['inference_false_block_count']} 条 | 0 条 | — |",
              f"| 可答率（保留） | {s06['answerable_rate_post']:.0%} | {s07['answerable_rate_post']:.0%} | "
              f"{delta['answerable_rate_post']:+.0%} |",
              f"| 引用可解析率（代理） | {s06['citation_resolvable_rate_post']} | {s07['citation_resolvable_rate_post']} | "
              f"{delta['citation_resolvable_rate_post']} |",
              f"| 支撑率（代理） | {s06['support_rate_post']} | {s07['support_rate_post']} | {delta['support_rate_post']} |",
              f"| 幻觉率（代理） | {s06['hallucination_rate_post']} | {s07['hallucination_rate_post']} | "
              f"{delta['hallucination_rate_post']} |",
              f"| 平均推断密度（代理） | {s06['avg_inference_density_post']} | {s07['avg_inference_density_post']} | — |",
              "", "拦截原因分布：", "",
              f"- 0.6：{json.dumps(s06['block_reasons'], ensure_ascii=False)}",
              f"- 0.7：{json.dumps(s07['block_reasons'], ensure_ascii=False)}", ""]
    if cmp["inference_false_blocks"]:
        lines += ["## 被 0.6 误拦截、0.7 放行的样本", "",
                  "| # | 问题 | inference_ratio |", "|---|---|---|"]
        for i, row in enumerate(cmp["inference_false_blocks"], 1):
            lines.append(f"| {i} | {str(row.get('question'))[:60]} | {row.get('inference_ratio')} |")
        lines.append("")
    lines += ["## 逐条指标", "",
              "| # | 类别 | 问题 | 可答 | 引用 | 无效 | 单元 | infer | 0.6 | 0.7 | 来源 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, row in enumerate(payload["rows"], 1):
        i06 = issues_for(row, 0.6)
        i07 = issues_for(row, 0.7)
        lines.append(f"| {i} | {row.get('category')} | {str(row.get('question'))[:38]} | "
                     f"{'是' if row.get('answerable') else '否'} | {row.get('citation_total')} | "
                     f"{row.get('citation_invalid_count')} | {row.get('distinct_cited_units')} | "
                     f"{row.get('inference_ratio')} | {'pass' if not i06 else 'block'} | "
                     f"{'pass' if not i07 else 'block'} | {row.get('source')} |")
    lines += ["", "## 风险与说明", ""]
    lines += [f"- 风险：{r}" for r in cmp["risks"]]
    lines += ["", f"- {payload['disclaimer']}", f"- 说明：{payload['note']}",
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
    ap = argparse.ArgumentParser(description="guard 阈值 0.6 vs 0.7 扩样 A/B（同一批答案离线重算）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑 30 条（或指定 n）阈值 A/B")
    run.add_argument("--n", type=int, default=DEFAULT_N, help=f"样本条数（默认 {DEFAULT_N}）")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    run.add_argument("--v3", default=str(V3_BANK))
    run.add_argument("--guard-json", default=str(GUARD_JSON))
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_ab(args.n, model=args.model, timeout=args.timeout,
                         deadline_seconds=args.deadline, v3_bank=args.v3,
                         guard_json=args.guard_json)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except AbError as exc:
        print(f"[guard_threshold_ab] FAILED: {exc}", file=sys.stderr)
        return 1
    cmp = payload["comparison"]
    print(f"[guard_threshold_ab] n={payload['config']['n_actual']} reused={payload['config']['reused_rows']} "
          f"live_calls={payload['config']['live_calls']} skipped={payload['config']['skipped']}")
    print(f"[guard_threshold_ab] 0.6: pass={cmp['threshold_0_6']['pass_rate']:.0%} "
          f"fallback={cmp['threshold_0_6']['fallback_rate']:.0%} | "
          f"0.7: pass={cmp['threshold_0_7']['pass_rate']:.0%} fallback={cmp['threshold_0_7']['fallback_rate']:.0%} "
          f"| false_blocks={cmp['inference_false_block_count']}")
    print(f"[guard_threshold_ab] verdict={cmp['verdict']} recommended={cmp['recommended_inference_max']}")
    print(f"[guard_threshold_ab] json={paths[0]}")
    print(f"[guard_threshold_ab] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
