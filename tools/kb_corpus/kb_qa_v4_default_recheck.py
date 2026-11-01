# -*- coding: utf-8 -*-
"""v4 默认复评（灰度稳定性）：用当前 CLI 默认（prompt v4 + guard segment）实跑 20 条复评。

背景：wave34 已把 prompt v4 + guard marker_mode=segment 切为 CLI 默认（kb_qa.py
DEFAULT_PROMPT_VERSION="v4"、DEFAULT_MARKER_MODE="segment"，均可用环境变量回滚）。
本模块做灰度稳定性复评：

  ① 选样：v3 问题库已用 42 条之外的 **8 条新题（第 43-50 条）** + 从扩样集复测 **12 条**
     （共 20 条；复测部分与扩样重叠，用于「同题对比」稳定性判断，已在 config/notes 注明）；
  ② 真实调用**当前默认**：`kb_qa.ask(mode="multi", guard=True, on_guard_fail="fallback")`；
     记录 prompt_version/guard_marker_mode（来自 payload，验证灰度生效）；
  ③ 统计：可答率、guard 通过率、fallback、拒答、引用可解析率、支撑率、推断比例、
     引用数、平均耗时；与 **v4 扩样 30 条基线** 对比方向/幅度；
  ④ 风险判定：可答率 <80% 或 guard 通过率 <80% → 标记回退风险并建议回滚（配置项已写明）；
  ⑤ 输出 `research/reports/kb_qa_v4_default_recheck_20261101.{json,md}`。

CLI：
  python tools/kb_corpus/kb_qa_v4_default_recheck.py run --n 20
  python tools/kb_corpus/kb_qa_v4_default_recheck.py run --n 15 --deadline 420   # LLM 慢时减条
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
SCALE_JSON = REPO / "research" / "reports" / "kb_qa_prompt_v4_scale_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_v4_default_recheck_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_v4_default_recheck_20261101.md"
DEFAULT_N = 20
DEFAULT_UNSEEN = 8       # 第 43-50 条（未被 wave25/29/33/94 用过）
DEFAULT_RECHECK = 12     # 从扩样集（第 13-42 条）复测 12 条
DEFAULT_OFFSET = 12      # 扩样集起点
DEFAULT_K = 8
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 600
RISK_ANSWERABLE_FLOOR = 0.80
RISK_GUARD_FLOOR = 0.80
STABLE_TOLERANCE = 0.10  # 与基线方向/幅度一致容差（±10pp）


class RecheckError(RuntimeError):
    """v4 默认复评异常。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RecheckError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_cache: dict = {}


def mod(name: str):
    if name not in _cache:
        _cache[name] = _load_module(f"{name}_for_recheck", f"{name}.py")
    return _cache[name]


# ---------------------------------------------------------------------------
# ① 选样（8 新题 + 12 复测）
# ---------------------------------------------------------------------------
def select_questions(n: int = DEFAULT_N, unseen: int = DEFAULT_UNSEEN,
                     recheck: int = DEFAULT_RECHECK, offset: int = DEFAULT_OFFSET,
                     path: Path | str = V3_BANK) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise RecheckError(f"v3 问题库不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecheckError(f"v3 问题库读取失败：{path}（{exc}）") from exc
    qs = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if not qs:
        raise RecheckError(f"v3 问题库 questions 为空：{path}")
    unseen_pool = qs[42:]
    recheck_pool = qs[offset:offset + recheck]
    picked: list[dict] = []
    for q in unseen_pool[:max(0, unseen)]:
        picked.append({**q, "sample_tag": "unseen"})
    for q in recheck_pool:
        if len(picked) >= int(n):
            break
        picked.append({**q, "sample_tag": "recheck"})
    n = max(1, int(n))
    return picked[:n]


# ---------------------------------------------------------------------------
# ② 真实调用当前默认
# ---------------------------------------------------------------------------
def _status(payload: dict) -> str:
    answer = str(payload.get("answer") or "")
    conf = payload.get("confidence") or {}
    if payload.get("fallback"):
        return "fallback"
    if payload.get("refusal") or conf.get("level") == "none" or "没有足够依据" in answer:
        return "no_evidence"
    return "success" if answer else "no_evidence"


def recheck_question(question: dict, ask_fn=None, k: int = DEFAULT_K,
                     model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT) -> dict:
    fn = ask_fn or (lambda q, **kw: mod("kb_qa").ask(q, **kw))
    started = time.monotonic()
    error = None
    try:
        payload = fn(question["question"], topk=k, model=model, timeout=timeout,
                     mode="multi", guard=True, on_guard_fail="fallback")
    except Exception as exc:  # noqa: BLE001
        payload = {"answer": None, "citations": [], "confidence": {"level": "none"},
                   "fallback": True, "refusal": False}
        error = f"{type(exc).__name__}: {exc}"
    return {"id": question.get("id"), "question": question.get("question"),
            "category": question.get("category"), "theme": question.get("theme"),
            "sample_tag": question.get("sample_tag") or "recheck",
            "status": _status(payload),
            "answerable": bool(payload.get("answerable")) if "answerable" in payload else _status(payload) == "success",
            "guard_passed": payload.get("guard_passed"),
            "guard_action": payload.get("guard_action"),
            "guard_issues": payload.get("guard_issues") or [],
            "citations": len(payload.get("citations") or []),
            "citation_resolvable_rate": payload.get("citation_resolvable_rate"),
            "support_rate": payload.get("support_rate"),
            "inference_ratio": payload.get("inference_ratio"),
            "distinct_cited_units": payload.get("distinct_cited_units"),
            "prompt_version": payload.get("prompt_version"),
            "guard_marker_mode": payload.get("guard_marker_mode"),
            "answer_length": len(str(payload.get("answer") or "")),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "error": error}


def run_recheck(n: int = DEFAULT_N, unseen: int = DEFAULT_UNSEEN,
                recheck: int = DEFAULT_RECHECK, offset: int = DEFAULT_OFFSET,
                k: int = DEFAULT_K, model: str = DEFAULT_MODEL,
                timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
                questions: list[dict] | None = None, ask_fn=None,
                clock=time.monotonic) -> dict:
    started = clock()
    qs = questions if questions is not None else select_questions(n, unseen, recheck, offset)
    rows = []
    skipped = 0
    for q in qs:
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            continue
        row = recheck_question(q, ask_fn=ask_fn, k=k, model=model,
                               timeout=min(timeout, remaining))
        rows.append(row)
    return {"n_requested": len(qs), "n_actual": len(rows), "skipped": skipped,
            "model": model, "k": k, "timeout_seconds": timeout,
            "deadline_seconds": deadline_seconds, "rows": rows,
            "elapsed_seconds": round(clock() - started, 2),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}


# ---------------------------------------------------------------------------
# ③ 统计 / 基线对比 / 结论
# ---------------------------------------------------------------------------
def arm_stats(rows: list[dict]) -> dict:
    total = len(rows)

    def _avg(key):
        vals = [float(r.get(key) or 0.0) for r in rows]
        return round(sum(vals) / total, 4) if total else 0.0

    success = sum(1 for r in rows if r.get("status") == "success")
    return {"total": total, "success": success,
            "answerable_rate": round(success / total, 4) if total else 0.0,
            "no_evidence": sum(1 for r in rows if r.get("status") == "no_evidence"),
            "fallback": sum(1 for r in rows if r.get("status") == "fallback"),
            "fallback_rate": round(sum(1 for r in rows if r.get("status") == "fallback") / total, 4) if total else 0.0,
            "guard_passed": sum(1 for r in rows if r.get("guard_passed")),
            "guard_pass_rate": round(sum(1 for r in rows if r.get("guard_passed")) / total, 4) if total else 0.0,
            "avg_citation_resolvable_rate": _avg("citation_resolvable_rate"),
            "avg_support_rate": _avg("support_rate"),
            "avg_inference_ratio": _avg("inference_ratio"),
            "avg_citations": _avg("citations"),
            "avg_elapsed_seconds": _avg("elapsed_seconds"),
            "prompt_versions": sorted({str(r.get("prompt_version")) for r in rows if r.get("prompt_version")}),
            "guard_marker_modes": sorted({str(r.get("guard_marker_mode")) for r in rows if r.get("guard_marker_mode")}),
            "errors": [r.get("error") for r in rows if r.get("error")]}


def load_scale_baseline(path: Path | str = SCALE_JSON) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    v4 = ((data.get("comparison") or {}).get("v4")) or {}
    if not v4:
        return {}
    return {"n": v4.get("total"), "answerable_rate": v4.get("answerable_rate"),
            "guard_pass_rate": v4.get("guard_pass_rate"), "fallback_rate": v4.get("fallback_rate"),
            "no_evidence": v4.get("no_evidence"),
            "avg_citation_resolvable_rate": v4.get("avg_citation_resolvable_rate"),
            "avg_support_rate": v4.get("avg_support_rate"),
            "avg_inference_ratio": v4.get("avg_inference_ratio"),
            "avg_citations": v4.get("avg_citations"),
            "avg_elapsed_seconds": v4.get("avg_elapsed_seconds")}


def evaluate(stats: dict, baseline: dict) -> dict:
    ans = stats.get("answerable_rate") or 0.0
    guard = stats.get("guard_pass_rate") or 0.0
    base_ans = baseline.get("answerable_rate")
    base_guard = baseline.get("guard_pass_rate")
    d_ans = round(ans - base_ans, 4) if base_ans is not None else None
    d_guard = round(guard - base_guard, 4) if base_guard is not None else None
    risk = ans < RISK_ANSWERABLE_FLOOR or guard < RISK_GUARD_FLOOR
    if stats.get("errors"):
        verdict, decision = "复评存在调用错误，结论暂缓（先修稳定性）", "hold"
    elif risk:
        verdict = (f"可答率 {ans:.0%} / guard 通过率 {guard:.0%} 低于 {'80%' if ans < RISK_ANSWERABLE_FLOOR else ''}"
                   f"{'/' if ans < RISK_ANSWERABLE_FLOOR and guard < RISK_GUARD_FLOOR else ''}"
                   f"{'80%' if guard < RISK_GUARD_FLOOR else ''} 阈值 → **标记回退风险**，建议回滚 v1")
        decision = "rollback_risk"
    elif base_ans is not None and abs(d_ans) <= STABLE_TOLERANCE and abs(d_guard) <= STABLE_TOLERANCE:
        verdict = (f"复评可答率 {ans:.0%}、guard {guard:.0%}，与扩样基线（{base_ans:.0%}/{base_guard:.0%}）"
                   f"差异在 ±10pp 内 → **默认稳定**，维持 v4 默认")
        decision = "stable"
    elif d_ans > STABLE_TOLERANCE:
        verdict = (f"复评可答率 {ans:.0%} 高于基线 {base_ans:.0%}（+{d_ans:.0%}）→ 默认表现优于扩样，维持 v4 默认")
        decision = "stable_improved"
    else:
        verdict = (f"复评可答率 {ans:.0%} 低于基线 {base_ans:.0%}（{d_ans:+.0%}，但在 80% 之上）"
                   f"→ 观察，暂不切回")
        decision = "watch"
    risks = []
    if risk:
        risks.append("可答率或 guard 通过率低于 80% 阈值：灰度稳定性不足，建议回滚 v1。")
    if d_ans is not None and d_ans < -STABLE_TOLERANCE:
        risks.append(f"可答率较扩样基线下降 {d_ans:.0%}（>10pp）：可能受题目难度/负载/模型波动影响，需扩样复核。")
    if stats.get("prompt_versions") and "v4" not in stats["prompt_versions"]:
        risks.append("gray 生效校验失败：payload.prompt_version 未包含 v4，默认切换可能未生效。")
    if stats.get("guard_marker_modes") and "segment" not in stats["guard_marker_modes"]:
        risks.append("gray 生效校验失败：payload.guard_marker_mode 未包含 segment。")
    risks.append("复评含 8 条新题 + 12 条复测（与扩样同题），复测部分用于稳定性对比、非独立样本。")
    rollback = ("回滚：`set KB_QA_PROMPT_VERSION=v1` + `set KB_QA_MARKER_MODE=legacy` 后重启 CLI；"
                "HTTP 通道需按既有流程灰度/重启验证（不建议直接重启 8091）。")
    return {"decision": decision, "verdict": verdict,
            "risk_flagged": bool(risk),
            "delta": {"answerable_rate": d_ans, "guard_pass_rate": d_guard},
            "thresholds": {"answerable_floor": RISK_ANSWERABLE_FLOOR,
                           "guard_floor": RISK_GUARD_FLOOR,
                           "stable_tolerance": STABLE_TOLERANCE},
            "risks": risks, "rollback": rollback}


# ---------------------------------------------------------------------------
# ④ 运行 + 输出
# ---------------------------------------------------------------------------
def run_recheck_all(n: int = DEFAULT_N, **kw) -> dict:
    started = time.monotonic()
    arm = run_recheck(n=n, **kw)
    stats = arm_stats(arm["rows"])
    baseline = load_scale_baseline()
    evaluation = evaluate(stats, baseline)
    return {"schema_version": 1, "kind": "kb_qa_v4_default_recheck",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"n_requested": arm["n_requested"], "n_actual": arm["n_actual"],
                       "skipped": arm["skipped"], "k": arm["k"], "model": arm["model"],
                       "timeout_seconds": arm["timeout_seconds"],
                       "deadline_seconds": arm["deadline_seconds"],
                       "selection": "8 条新题（第 43-50 条，未用过）+ 12 条复测（第 13-42 条，与扩样重叠）",
                       "default_prompt_version": "v4", "default_marker_mode": "segment",
                       "rollback_env": ["KB_QA_PROMPT_VERSION=v1", "KB_QA_MARKER_MODE=legacy"]},
            "note": "真实调用当前 CLI 默认（kb_qa.ask mode=multi guard=True）；payload 记录 prompt_version/guard_marker_mode 验证灰度生效",
            "stats": stats, "baseline_scale30": baseline, "evaluation": evaluation,
            "rows": arm["rows"], "elapsed_seconds": round(time.monotonic() - started, 2),
            "disclaimer": "复评结果仅供研究参考，不构成投资建议；答案需人工复核。"}


def render_markdown(payload: dict) -> str:
    st = payload["stats"]
    base = payload["baseline_scale30"]
    ev = payload["evaluation"]
    cfg = payload["config"]
    lines = [f"# v4 默认复评（灰度稳定性，{payload['generated_at'][:10]}）", "",
             f"> 当前默认 = `kb_qa.ask(mode=multi)`（prompt **v4** + guard **segment**，wave34 切默认）· "
             f"复评 {cfg['n_actual']} 条：{cfg['selection']} · qwen3.5:9b 串行。", "",
             "## 结论", "",
             f"**{ev['verdict']}** —— 决策：`{ev['decision']}`"
             f"（stable=稳定 / stable_improved=优于基线 / watch=观察 / rollback_risk=回退风险 / hold=先修）", ""]
    lines += [f"- {r}" for r in ev["risks"]]
    lines += ["", "## 复评 vs 扩样基线", "",
              "| 指标 | 扩样基线（v4，30 条） | 本次复评（v4 默认） | Δ |", "|---|---|---|---|",
              f"| 可答率 | {base.get('answerable_rate', 0):.0%} | **{st['answerable_rate']:.0%}**"
              f"（{st['success']}/{st['total']}） | {ev['delta']['answerable_rate'] if ev['delta']['answerable_rate'] is not None else '—'} |",
              f"| guard 通过率 | {base.get('guard_pass_rate', 0):.0%} | {st['guard_pass_rate']:.0%} | "
              f"{ev['delta']['guard_pass_rate'] if ev['delta']['guard_pass_rate'] is not None else '—'} |",
              f"| fallback 率 | {base.get('fallback_rate', 0):.0%} | {st['fallback_rate']:.0%} | — |",
              f"| 拒答数 | {base.get('no_evidence')} | {st['no_evidence']} | — |",
              f"| 引用可解析率 | {base.get('avg_citation_resolvable_rate')} | {st['avg_citation_resolvable_rate']} | — |",
              f"| 支撑率 | {base.get('avg_support_rate')} | {st['avg_support_rate']} | — |",
              f"| 平均推断比例 | {base.get('avg_inference_ratio')} | {st['avg_inference_ratio']} | — |",
              f"| 平均引用数 | {base.get('avg_citations')} | {st['avg_citations']} | — |",
              f"| 平均耗时(s) | {base.get('avg_elapsed_seconds')} | {st['avg_elapsed_seconds']} | — |",
              "", "## 灰度生效校验", "",
              f"- prompt_version 分布：{st['prompt_versions']}",
              f"- guard_marker_mode 分布：{st['guard_marker_modes']}",
              f"- 错误数：{len(st['errors'])}", "",
              "## 风险与回退", ""]
    wall = "< 80%" if ev["risk_flagged"] else "≥ 80%"
    lines += [f"- 阈值：可答率 ≥80%、guard 通过率 ≥80%（实测 {st['answerable_rate']:.0%} / "
              f"{st['guard_pass_rate']:.0%}，{wall}）",
              f"- {ev['rollback']}", "",
              "## 逐条结果", "",
              "| # | 标签 | 类别 | 问题 | 状态 | guard | 引用 | 推断 | 耗时 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(payload["rows"], 1):
        lines.append(f"| {i} | {r.get('sample_tag')} | {r.get('category')} | "
                     f"{str(r.get('question'))[:34]} | {r.get('status')} | {r.get('guard_passed')} | "
                     f"{r.get('citations')} | {r.get('inference_ratio')} | {r.get('elapsed_seconds')}s |")
    lines += ["", f"- {payload['disclaimer']}", f"- 说明：{payload['note']}",
              f"- 总耗时 {payload['elapsed_seconds']}s"]
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
    ap = argparse.ArgumentParser(description="v4 默认复评（灰度稳定性）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="真实调用当前默认并输出复评 JSON+MD")
    run.add_argument("--n", type=int, default=DEFAULT_N, help=f"复评条数（默认 {DEFAULT_N}）")
    run.add_argument("--k", type=int, default=DEFAULT_K)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_recheck_all(n=args.n, k=args.k, model=args.model,
                                  timeout=args.timeout, deadline_seconds=args.deadline)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except RecheckError as exc:
        print(f"[v4-recheck] FAILED: {exc}", file=sys.stderr)
        return 1
    st, ev = payload["stats"], payload["evaluation"]
    print(f"[v4-recheck] n={payload['config']['n_actual']} skipped={payload['config']['skipped']}")
    print(f"[v4-recheck] answerable={st['answerable_rate']:.0%} guard={st['guard_pass_rate']:.0%} "
          f"fallback={st['fallback_rate']:.0%} refusal={st['no_evidence']}")
    print(f"[v4-recheck] prompt_versions={st['prompt_versions']} marker_modes={st['guard_marker_modes']}")
    print(f"[v4-recheck] decision={ev['decision']} risk={ev['risk_flagged']} | {ev['verdict']}")
    print(f"[v4-recheck] json={paths[0]}")
    print(f"[v4-recheck] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
