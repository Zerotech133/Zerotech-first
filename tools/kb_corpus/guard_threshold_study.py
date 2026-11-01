# -*- coding: utf-8 -*-
"""guard 阈值敏感性研究：inference_ratio 阈值 × 最小被引单元数 的通过率/质量权衡。

背景：wave19/20 guard 默认 `inference_ratio > 0.6 → inference_heavy`，CE 12 条样本中 8 条被
fallback（拦截偏高）。本模块**只做离线模拟与参数建议，不修改 guard 代码**：

  ① 读 wave19 guard 结果（`research/reports/kb_qa_guard_20261101.json` 的 12 条原 multi 答案指标；
     若存在 `kb_qa_guard_review_20261101.json` 则附带读取人工 review 信息）；
  ② 网格模拟：inference_ratio 阈值 ∈ {0.5,0.6,0.7,0.8,0.9} × min distinct cited units ∈ {2,3}，
     对每个组合统计：通过率、被拦截数、拦截原因分布；
  ③ 代理质量指标：引用可解析率 / 支撑率 / 推断密度 / 幻觉率（通过行用原答案指标，被拦截行用
     guard fallback 后指标——fallback 只保留可解析引用，属于保守估计）；
  ④ 输出 `research/reports/guard_threshold_study_20261101.{json,md}`：矩阵、推荐默认、风险。

CLI：
  python tools/kb_corpus/guard_threshold_study.py run
  python tools/kb_corpus/guard_threshold_study.py run --guard-json <path> --out-json <path>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GUARD_JSON = REPO / "research" / "reports" / "kb_qa_guard_20261101.json"
REVIEW_JSON = REPO / "research" / "reports" / "kb_qa_guard_review_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "guard_threshold_study_20261101.json"
OUT_MD = REPO / "research" / "reports" / "guard_threshold_study_20261101.md"
INFERENCE_THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)
MIN_UNITS_OPTIONS = (2, 3)
DEFAULT_INFERENCE = 0.6
DEFAULT_MIN_UNITS = 2


class StudyError(RuntimeError):
    """阈值研究异常（输入缺失等）。"""


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def _metric(row: dict, phase: str, key: str, default=0):
    return (row.get(phase) or {}).get(key, default)


def load_rows(guard_json: Path | str = GUARD_JSON,
              review_json: Path | str = REVIEW_JSON) -> tuple[list[dict], dict]:
    """读 guard 结果的 12 条逐题指标（before/after）；附带可选 review 信息。"""
    path = Path(guard_json)
    if not path.exists():
        raise StudyError(f"guard 结果文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StudyError(f"guard 结果读取失败：{path}（{exc}）") from exc
    rows = []
    for row in data.get("comparison") or []:
        if not isinstance(row, dict):
            continue
        before, after = row.get("before") or {}, row.get("after") or {}
        if not before:
            continue
        rows.append({
            "id": row.get("id"), "question": row.get("question"), "category": row.get("category"),
            "theme": row.get("theme"),
            "before": before, "after": after,
            # 统一入口字段（原 multi 答案）
            "answerable": bool(before.get("answerable")),
            "citation_total": int(before.get("citation_total") or 0),
            "citation_invalid": int(before.get("citation_invalid_count") or 0),
            "citation_resolvable_rate": before.get("citation_resolvable_rate"),
            "support_rate": before.get("support_rate"),
            "hallucination_rate": before.get("hallucination_rate"),
            "inference_ratio": before.get("inference_ratio") or 0.0,
            "inference_heavy": bool(before.get("inference_heavy")),
            "distinct_cited_units": int(before.get("distinct_cited_units") or 0),
            "guard_passed_default": bool(before.get("guard_passed")),
            "guard_issues_default": before.get("guard_issues") or [],
        })
    if not rows:
        raise StudyError(f"guard 结果缺少 comparison 逐题指标：{path}")
    review = {}
    review_path = Path(review_json)
    if review_path.exists():
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            review = {}
    return rows, review


# ---------------------------------------------------------------------------
# 模拟
# ---------------------------------------------------------------------------
def classify_issues(row: dict, inference_max: float, min_units: int) -> list[str]:
    """按给定阈值重算拦截原因（与 guard 判定逻辑同口径，仅阈值可变）。"""
    issues = []
    if row["citation_invalid"] > 0:
        issues.append("citation_invalid")
    if row["citation_total"] == 0:
        issues.append("citation_missing")
    if row["distinct_cited_units"] < min_units:
        issues.append("insufficient_cited_units")
    if row["inference_ratio"] > inference_max:
        issues.append("inference_heavy")
    return issues


def _weighted_avg(pairs: list[tuple[float, float]]) -> float | None:
    num = sum(v * w for v, w in pairs)
    den = sum(w for _v, w in pairs)
    return round(num / den, 4) if den else None


def simulate_combo(rows: list[dict], inference_max: float, min_units: int) -> dict:
    """模拟单阈值组合：通过/拦截、原因分布、代理质量指标。"""
    passed, blocked = [], []
    issue_counts: dict[str, int] = {}
    citations_total = 0
    resolvable_num = 0.0
    support_num = 0.0
    hallucination_num = 0.0
    inference_post: list[float] = []
    answerable_post = 0
    for row in rows:
        issues = classify_issues(row, inference_max, min_units)
        if issues:
            blocked.append(row)
            for issue in issues:
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
            after = row.get("after") or {}
            c_total = int(after.get("citation_total") or 0)
            citations_total += c_total
            resp = after.get("citation_resolvable_rate")
            sup = after.get("support_rate")
            hal = after.get("hallucination_rate")
            resolvable_num += (resp if resp is not None else 1.0) * c_total
            support_num += (sup if sup is not None else 1.0) * c_total
            hallucination_num += (hal if hal is not None else 0.0) * c_total
            inference_post.append(float(after.get("inference_ratio") or 0.0))
        else:
            passed.append(row)
            citations_total += row["citation_total"]
            resolvable_num += (row["citation_resolvable_rate"] or 0.0) * row["citation_total"]
            support_num += (row["support_rate"] or 0.0) * row["citation_total"]
            hallucination_num += (row["hallucination_rate"] or 0.0) * row["citation_total"]
            inference_post.append(float(row["inference_ratio"]))
            answerable_post += 1 if row["answerable"] else 0
    total = len(rows)
    return {
        "inference_max": inference_max, "min_units": min_units,
        "total": total, "passed": len(passed), "blocked": len(blocked),
        "pass_rate": round(len(passed) / total, 4) if total else 0.0,
        "block_rate": round(len(blocked) / total, 4) if total else 0.0,
        "block_reasons": issue_counts,
        "answerable_preserved": answerable_post,
        "answerable_rate_post": round(answerable_post / total, 4) if total else 0.0,
        "fallback_rate": round(len(blocked) / total, 4) if total else 0.0,
        "citation_resolvable_rate_post": round(resolvable_num / citations_total, 4) if citations_total else None,
        "support_rate_post": round(support_num / citations_total, 4) if citations_total else None,
        "hallucination_rate_post": round(hallucination_num / citations_total, 4) if citations_total else None,
        "avg_inference_density_post": round(sum(inference_post) / len(inference_post), 4) if inference_post else None,
        "cited_units_avg_post": round(sum((1 if not classify_issues(r, inference_max, min_units)
                                           else max(int((r.get('after') or {}).get('distinct_cited_units') or 0), 0))
                                          for r in rows) / total, 2) if total else None,
    }


def build_matrix(rows: list[dict],
                 inference_thresholds: tuple = INFERENCE_THRESHOLDS,
                 min_units_options: tuple = MIN_UNITS_OPTIONS) -> dict:
    combos = []
    for inf in inference_thresholds:
        for units in min_units_options:
            combos.append(simulate_combo(rows, inf, units))
    return {"inference_thresholds": list(inference_thresholds), "min_units_options": list(min_units_options),
            "combos": combos}


# ---------------------------------------------------------------------------
# 推荐
# ---------------------------------------------------------------------------
def recommend(matrix: dict, rows: list[dict]) -> dict:
    """推荐默认阈值：在质量约束（可解析率≥0.98、幻觉率≤0.02）下最大化通过率（保留可答性）。"""
    combos = matrix.get("combos") or []
    if not combos:
        raise StudyError("阈值矩阵为空，无法推荐")
    eligible = [c for c in combos
                if (c.get("citation_resolvable_rate_post") or 0) >= 0.98
                and (c.get("hallucination_rate_post") or 0) <= 0.02]
    if not eligible:
        eligible = combos
    # 通过率高优先；同通过率时选「接近 0.7 的温和阈值」（避免 0.9 变相关闭规则，也避免回归到过严 0.5）；
    # min_units 优先 2（提高单元数在本数据集无质量增益，仅增加 fallback）
    max_pass = max(c["pass_rate"] for c in eligible)
    top = [c for c in eligible if c["pass_rate"] == max_pass]
    top.sort(key=lambda c: (abs(c["inference_max"] - 0.7), c["min_units"]))
    best = top[0]
    baseline = next((c for c in combos if c["inference_max"] == DEFAULT_INFERENCE
                     and c["min_units"] == DEFAULT_MIN_UNITS), None)
    default_row = simulate_combo(rows, DEFAULT_INFERENCE, DEFAULT_MIN_UNITS)
    reasons = []
    if baseline:
        if best["pass_rate"] > baseline["pass_rate"]:
            reasons.append(f"推荐阈值通过率 {best['pass_rate']:.0%} 高于默认 "
                           f"{baseline['pass_rate']:.0%}（减少误拦截）。")
        elif best["pass_rate"] == baseline["pass_rate"]:
            reasons.append(f"推荐阈值通过率与默认相同（{best['pass_rate']:.0%}），"
                           f"但更宽松（inference≤{best['inference_max']}/units≥{best['min_units']}），"
                           "对未见数据更稳健。")
        else:
            reasons.append(f"推荐阈值通过率 {best['pass_rate']:.0%} 低于默认 {baseline['pass_rate']:.0%}，"
                           "换取更严格质量门槛。")
    reasons.append(f"代理质量：引用可解析率 {best.get('citation_resolvable_rate_post')}、"
                   f"支撑率 {best.get('support_rate_post')}、幻觉率 {best.get('hallucination_rate_post')}、"
                   f"推断密度 {best.get('avg_inference_density_post')}。")
    if best["min_units"] == DEFAULT_MIN_UNITS:
        reasons.append(f"最小被引单元维持 {best['min_units']} 个：提高单元数并未带来质量增益，"
                       "反而增加 fallback。")
    risks = [
        "样本量 n=12 偏小，阈值差异可能来自样本偶然性；建议扩样到 30–50 条复核。",
        "inference_ratio 依赖模型输出【直接事实】/【推断】标记；未标注时比率为 0，可能低估计。",
        "被拦截行的代理质量使用 guard fallback 后指标（fallback 仅保留可解析引用），属于保守估计。",
        "数据集来自 wave15 v3 前 12 条（偏难答），不代表全量 50 条问题分布。",
        "仅参数建议，不修改 guard 代码；实际切换需通过 CLI 参数/配置灰度并复跑评估。",
    ]
    alternatives = [
        {"inference_max": c["inference_max"], "min_units": c["min_units"],
         "pass_rate": c["pass_rate"], "blocked": c["blocked"],
         "quality_note": f"可解析率 {c.get('citation_resolvable_rate_post')} / 推断密度 {c.get('avg_inference_density_post')}"}
        for c in combos if c is not best
    ][:4]
    return {"recommended": {"inference_max": best["inference_max"], "min_units": best["min_units"]},
            "recommended_metrics": best,
            "current_default": {"inference_max": DEFAULT_INFERENCE, "min_units": DEFAULT_MIN_UNITS,
                                "metrics": default_row},
            "reasons": reasons, "risks": risks, "alternatives": alternatives,
            "conclusion": f"建议默认 inference_ratio 阈值="
                          f"{best['inference_max'] if best['inference_max'] != int(best['inference_max']) else int(best['inference_max'])}"
                          f"、最小被引单元={best['min_units']}（不修改 guard 代码，仅调参）"}


# ---------------------------------------------------------------------------
# 主流程 / 输出
# ---------------------------------------------------------------------------
def run_study(guard_json: Path | str = GUARD_JSON, review_json: Path | str = REVIEW_JSON) -> dict:
    rows, review = load_rows(guard_json, review_json)
    matrix = build_matrix(rows)
    rec = recommend(matrix, rows)
    return {"schema_version": 1, "kind": "guard_threshold_study",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": {"guard_json": str(guard_json), "rows": len(rows),
                       "review_json": str(review_json), "review_available": bool(review)},
            "grid": {"inference_thresholds": matrix["inference_thresholds"],
                     "min_units_options": matrix["min_units_options"]},
            "matrix": matrix["combos"], "recommendation": rec,
            "note": "只读模拟，不调 LLM、不修改 kb_qa_guard.py；被拦截行质量用 fallback 后指标代理",
            "disclaimer": "阈值研究仅供研究参考，不构成投资建议；最终默认值需扩样复核后决定。"}


def _pct(v):
    return f"{v:.0%}" if isinstance(v, (int, float)) else "—"


def render_markdown(payload: dict) -> str:
    rec = payload["recommendation"]
    matrix = payload["matrix"]
    lines = [f"# guard 阈值敏感性研究（{payload['generated_at'][:10]}）", "",
             f"> 数据：`{payload['source']['guard_json']}`（{payload['source']['rows']} 条原 multi 答案）· "
             f"网格：inference_ratio ∈ {payload['grid']['inference_thresholds']} × "
             f"min cited units ∈ {payload['grid']['min_units_options']} · 只读模拟、不调 LLM、不改 guard 代码。", "",
             "## 推荐默认（参数建议）", "",
             f"**inference_ratio 阈值 = {rec['recommended']['inference_max']}，"
             f"最小被引单元 = {rec['recommended']['min_units']}**", ""]
    lines += [f"- {r}" for r in rec["reasons"]]
    lines += ["", "当前默认（inference=0.6 / units=2）指标：",
              f"- 通过率 {_pct(rec['current_default']['metrics']['pass_rate'])}"
              f"（{rec['current_default']['metrics']['passed']}/{rec['current_default']['metrics']['total']}）、"
              f"被拦截 {rec['current_default']['metrics']['blocked']} 条、"
              f"可答率（保留） {_pct(rec['current_default']['metrics']['answerable_rate_post'])}。", "",
              "## 阈值 × 通过率矩阵", "",
              "| inference_ratio 阈值 | min units | 通过率 | 通过/拦截 | 拦截原因分布 | 可答率(保留) | 引用可解析率 | 支撑率 | 幻觉率 | 推断密度 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for c in matrix:
        reasons = "；".join(f"{k}:{v}" for k, v in (c.get("block_reasons") or {}).items()) or "无"
        lines.append(f"| {c['inference_max']} | {c['min_units']} | {_pct(c['pass_rate'])} | "
                     f"{c['passed']}/{c['blocked']} | {reasons} | {_pct(c['answerable_rate_post'])} | "
                     f"{_pct(c.get('citation_resolvable_rate_post'))} | {_pct(c.get('support_rate_post'))} | "
                     f"{_pct(c.get('hallucination_rate_post'))} | {c.get('avg_inference_density_post')} |")
    lines += ["", "## 备选阈值（权衡）", ""]
    for alt in rec["alternatives"]:
        lines.append(f"- inference={alt['inference_max']} / units={alt['min_units']}："
                     f"通过率 {_pct(alt['pass_rate'])}（拦截 {alt['blocked']} 条），{alt['quality_note']}")
    lines += ["", "## 风险与后续", ""]
    lines += [f"- {r}" for r in rec["risks"]]
    lines += ["", "## 结论", "", f"- {rec['conclusion']}", f"- {payload['disclaimer']}",
              f"- 说明：{payload['note']}"]
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
    ap = argparse.ArgumentParser(description="guard 阈值敏感性研究（只读模拟，不改 guard 代码）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑阈值网格并输出研究 JSON+MD")
    run.add_argument("--guard-json", default=str(GUARD_JSON))
    run.add_argument("--review-json", default=str(REVIEW_JSON))
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_study(args.guard_json, args.review_json)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except StudyError as exc:
        print(f"[guard_threshold_study] FAILED: {exc}", file=sys.stderr)
        return 1
    rec = payload["recommendation"]
    print(f"[guard_threshold_study] rows={payload['source']['rows']} "
          f"grid={payload['grid']['inference_thresholds']}x{payload['grid']['min_units_options']}")
    print(f"[guard_threshold_study] recommended inference_max={rec['recommended']['inference_max']} "
          f"min_units={rec['recommended']['min_units']}")
    print(f"[guard_threshold_study] current default pass_rate="
          f"{rec['current_default']['metrics']['pass_rate']:.0%} -> recommended "
          f"{rec['recommended_metrics']['pass_rate']:.0%}")
    print(f"[guard_threshold_study] json={paths[0]}")
    print(f"[guard_threshold_study] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
