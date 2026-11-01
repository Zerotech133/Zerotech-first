# -*- coding: utf-8 -*-
# DEPRECATED（2026-11-02）：实验失败，禁止在生产路径引用；证据见 docs/plans/2026-11-02_提示词清单_活跃与废弃_v1.md
"""35b 模型 prompt v3 A/B：验证「模型能力是否 v3 失败瓶颈」。

背景：wave27 修复 guard 标记解析（strict_line）后，9b + prompt v3 仍只有 10% guard 通过率
（平均推断比例 0.8），根因被归为「9b 输出行为/能力」。本模块用本机 **qwen3.6:35b-a3b**
（`/api/chat`，think=false，单条 300s）跑同一 prompt v3、同聚类材料、同 guard strict_line，
与两条 9b 基线对比：

  - 9b + v1 prompt（现 multi prompt，基线 50% 可答率）；
  - 9b + v3 prompt（wave27 离线重评：strict_line 下 10% 通过率）；
  - 35b + v3 prompt（本任务现场生成）。

结论：35b 是否使 v3 可用 / 是否值得作为可选模型。
输出 `research/reports/kb_qa_prompt_v3_35b_20261101.{json,md}`。
约束：不改现有模块（全部复用）；不启动服务；35b 慢时自动跳过剩余并注明。

CLI：
  python tools/kb_corpus/kb_qa_prompt_v3_35b.py run --n 10
  python tools/kb_corpus/kb_qa_prompt_v3_35b.py run --n 8 --deadline 420   # 35b 慢时减条
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
V1_BASELINE = REPO / "research" / "reports" / "kb_qa_prompt_v2_20261101.json"
V3_9B_BASELINE = REPO / "research" / "reports" / "guard_marker_fix_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_prompt_v3_35b_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_prompt_v3_35b_20261101.md"
DEFAULT_N = 10
DEFAULT_K = 8
DEFAULT_MODEL = "qwen3.6:35b-a3b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 480


class Prompt35bError(RuntimeError):
    """35b A/B 异常。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise Prompt35bError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_cache: dict = {}


def mod(name: str):
    if name not in _cache:
        _cache[name] = _load_module(f"{name}_for_35b", f"{name}.py")
    return _cache[name]


# ---------------------------------------------------------------------------
# ① 样本 / 基线
# ---------------------------------------------------------------------------
def load_questions(path: Path | str = V3_BANK, n: int = DEFAULT_N) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise Prompt35bError(f"v3 问题库不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Prompt35bError(f"v3 问题库读取失败：{path}（{exc}）") from exc
    qs = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if not qs:
        raise Prompt35bError(f"v3 问题库 questions 为空：{path}")
    return qs[:max(1, int(n))]


def _norm_arm(status: str, guard_passed, citations: int, inference_ratio, elapsed,
              error=None) -> dict:
    return {"status": status, "success": status == "success",
            "no_evidence": status == "no_evidence", "fallback": status == "fallback",
            "guard_passed": guard_passed, "citations": int(citations or 0),
            "inference_ratio": inference_ratio if inference_ratio is not None else 0.0,
            "elapsed_seconds": elapsed if elapsed is not None else 0.0,
            "error": error}


def load_9b_v1_rows(ids: set[str], path: Path | str = V1_BASELINE) -> dict:
    """9b + v1 prompt 基线（wave25 A/B 的 v1 rows，复用，不调 LLM）。"""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for row in (data.get("v1") or {}).get("rows") or []:
        if row.get("id") in ids:
            out[row["id"]] = _norm_arm(row.get("status"), row.get("guard_passed"),
                                       row.get("citations"), row.get("inference_ratio"),
                                       row.get("elapsed_seconds"))
    return out


def load_9b_v3_rows(ids: set[str], path: Path | str = V3_9B_BASELINE) -> dict:
    """9b + v3 prompt 基线（wave27 guard 标记修复后的 strict_line 离线重评 rows）。"""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for row in data.get("rows") or []:
        if row.get("id") not in ids:
            continue
        strict = row.get("strict_line") or {}
        out[row["id"]] = _norm_arm(strict.get("status_after") or "fallback",
                                   strict.get("guard_passed"),
                                   strict.get("citations_after"),
                                   strict.get("inference_ratio"),
                                   row.get("elapsed_seconds"),
                                   error=row.get("error"))
    return out


# ---------------------------------------------------------------------------
# ② 35b + prompt v3
# ---------------------------------------------------------------------------
def answer_v3_35b(question: str, k: int = DEFAULT_K, chat_fn=None, search_fn=None,
                  model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """35b + v3 prompt：同聚类材料 + 同 guard（strict_line）；复用 v3 prompt 与 v2 工具函数。"""
    started = time.monotonic()
    pv3 = mod("kb_qa_prompt_v3")
    v2 = mod("kb_qa_prompt_v2")
    multi = mod("kb_qa_multi")
    guard_mod = mod("kb_qa_guard")
    try:
        clusters = multi.retrieve_clusters(question, k=k, search_fn=search_fn)
        prompt = pv3.build_prompt_v3(question, clusters)
        chat = chat_fn or multi.call_ollama
        try:
            answer = chat(prompt, model=model, timeout=timeout)
            raw = v2._raw_from_answer(question, answer, clusters, multi)
            error = None
        except Exception as exc:  # noqa: BLE001
            raw = v2._raw_from_answer(question, multi.rule_based_multi(clusters), clusters, multi)
            raw.update({"fallback": True})
            error = f"{type(exc).__name__}: {exc}"
        unit_ids, book_ids = guard_mod.load_kb_ids()
        guarded = guard_mod.apply_guard(raw, unit_ids, book_ids, on_fail="fallback",
                                        marker_mode="strict_line")
    except Exception as exc:  # noqa: BLE001
        guarded = {"answer": None, "citations": [], "confidence": {"level": "none"},
                   "fallback": True, "refusal": False}
        error = f"{type(exc).__name__}: {exc}"
    out = v2._classify(guarded)
    if error:
        out["error"] = error
    out["arm"] = "35b_v3"
    out["model"] = model
    out["elapsed_seconds"] = round(time.monotonic() - started, 2)
    out["raw_answer_length"] = len(str(guarded.get("answer") or ""))
    return out


# ---------------------------------------------------------------------------
# ③ 对比 / 结论
# ---------------------------------------------------------------------------
def compare_arms(v1: dict, v3_9b: dict, v3_35b: dict) -> dict:
    base = v3_9b.get("guard_pass_rate") or 0
    v1_guard = v1.get("guard_pass_rate") or 0
    v1_ans = v1.get("answerable_rate") or 0
    arm_guard = v3_35b.get("guard_pass_rate") or 0
    arm_ans = v3_35b.get("answerable_rate") or 0
    d_vs_v3 = round(arm_guard - base, 4)
    d_vs_v1_guard = round(arm_guard - v1_guard, 4)
    d_vs_v1_ans = round(arm_ans - v1_ans, 4)
    if v3_35b.get("errors"):
        verdict, recommendation = "35b 存在调用错误，结论暂缓（先修稳定性/重跑）", "hold"
    elif arm_guard >= 0.5 or arm_ans >= 0.4:
        verdict = "35b 使 v3 prompt 可用（guard 通过率/可答率显著回升），建议作为可选模型（35b+v3）扩样验证"
        recommendation = "optional_model"
    elif d_vs_v3 >= 0.2:
        verdict = "35b 明显缓解 v3 的推断标记问题，但仍低于 v1 基线；可作为可选模型灰度并继续调优"
        recommendation = "optional_model_limited"
    else:
        verdict = "35b 未能让 v3 可用（未见显著提升）；不建议引入 35b 作为可选模型，保留 9b+v1"
        recommendation = "keep_v1"
    reasons = [
        f"guard 通过率：9b+v3 {base:.0%} → 35b+v3 {arm_guard:.0%}（Δ{d_vs_v3:+.0%}）；"
        f"9b+v1 {v1_guard:.0%}（Δ vs v1 {d_vs_v1_guard:+.0%}）。",
        f"可答率：9b+v3 {v3_9b.get('answerable_rate', 0):.0%} → 35b+v3 {arm_ans:.0%}；"
        f"9b+v1 {v1_ans:.0%}（Δ vs v1 {d_vs_v1_ans:+.0%}）。",
        f"平均推断比例：9b+v3 {v3_9b.get('avg_inference_ratio')} → 35b+v3 {v3_35b.get('avg_inference_ratio')}；"
        f"平均引用：{v3_9b.get('avg_citations')} → {v3_35b.get('avg_citations')}；"
        f"平均耗时：{v3_9b.get('avg_elapsed_seconds')}s → {v3_35b.get('avg_elapsed_seconds')}s。",
    ]
    risks = [
        "35b 首次加载/推理耗时远高于 9b（冷加载可达 2–3 分钟），作为默认模型成本高，需评估吞吐与并发。",
        "样本小（默认 10 条，35b 慢时可能更少），结论需扩样 30–50 条复核。",
        "35b 输出更长，token 成本与响应时延需真实压测（单条 300s 上限）。",
        "本 A/B 仅换模型不换 prompt，若 35b 仍不达标说明 v3 prompt 需重写而非模型升级。",
    ]
    return {"recommendation": recommendation, "verdict": verdict, "reasons": reasons, "risks": risks,
            "delta": {"guard_pass_vs_9b_v3": d_vs_v3, "guard_pass_vs_9b_v1": d_vs_v1_guard,
                      "answerable_vs_9b_v1": d_vs_v1_ans}}


# ---------------------------------------------------------------------------
# ④ 运行 + 输出
# ---------------------------------------------------------------------------
def run_ab(n: int = DEFAULT_N, k: int = DEFAULT_K, model: str = DEFAULT_MODEL,
           timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
           chat_fn=None, search_fn=None, v3_bank: Path | str = V3_BANK,
           v1_baseline: Path | str = V1_BASELINE, v3_baseline: Path | str = V3_9B_BASELINE,
           clock=time.monotonic) -> dict:
    started = clock()
    questions = load_questions(v3_bank, n)
    ids = {q.get("id") for q in questions}
    v1_base = load_9b_v1_rows(ids, v1_baseline)
    v3_base = load_9b_v3_rows(ids, v3_baseline)
    live, skipped = 0, 0
    v1_rows, v3_rows, b_rows = [], [], []
    for q in questions:
        qid = q.get("id")
        if qid in v1_base:
            v1_rows.append({**v1_base[qid], "id": qid, "question": q["question"],
                            "category": q.get("category")})
        if qid in v3_base:
            v3_rows.append({**v3_base[qid], "id": qid, "question": q["question"],
                            "category": q.get("category")})
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            continue
        row = answer_v3_35b(q["question"], k=k, chat_fn=chat_fn, search_fn=search_fn,
                            model=model, timeout=min(timeout, remaining))
        row.update({"id": qid, "question": q["question"], "category": q.get("category")})
        b_rows.append(row)
        live += 1
    v2 = mod("kb_qa_prompt_v2")
    v1_sum = v2.arm_stats(v1_rows)
    v3_sum = v2.arm_stats(v3_rows)
    b_sum = v2.arm_stats(b_rows)
    comparison = compare_arms(v1_sum, v3_sum, b_sum)
    return {"schema_version": 1, "kind": "kb_qa_prompt_v3_35b_ab",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"n_requested": int(n), "n_actual": live, "skipped": skipped,
                       "k": k, "model_35b": model, "timeout_seconds": timeout,
                       "deadline_seconds": deadline_seconds,
                       "guard_marker_mode": "strict_line",
                       "models": {"9b_v1": "qwen3.5:9b", "9b_v3": "qwen3.5:9b",
                                  "35b_v3": model}},
            "sources": {"v3_bank": str(v3_bank), "v1_baseline": str(v1_baseline),
                        "v3_9b_baseline": str(v3_baseline)},
            "note": ("35b+v3 现场生成（同聚类材料+同 guard strict_line）；9b+v1 / 9b+v3 复用已有结果；"
                     "35b 冷加载可能较慢，超时/预算用尽时余下跳过并注明"),
            "arms": {"9b_v1": {"summary": v1_sum, "rows": v1_rows},
                     "9b_v3": {"summary": v3_sum, "rows": v3_rows},
                     "35b_v3": {"summary": b_sum, "rows": b_rows}},
            "rows": [{"id": q.get("id"), "category": q.get("category"),
                      "question": q["question"],
                      "9b_v1": next(({k2: r.get(k2) for k2 in
                                      ("status", "guard_passed", "citations", "inference_ratio", "elapsed_seconds")}
                                     for r in v1_rows if r["id"] == q.get("id")), None),
                      "9b_v3": next(({k2: r.get(k2) for k2 in
                                      ("status", "guard_passed", "citations", "inference_ratio", "elapsed_seconds")}
                                     for r in v3_rows if r["id"] == q.get("id")), None),
                      "35b_v3": next(({k2: r.get(k2) for k2 in
                                       ("status", "guard_passed", "citations", "inference_ratio",
                                        "elapsed_seconds", "error")}
                                      for r in b_rows if r["id"] == q.get("id")), None)}
                     for q in questions],
            "comparison": comparison,
            "elapsed_seconds": round(clock() - started, 2),
            "disclaimer": "35b A/B 结果仅供研究参考，不构成投资建议；答案需人工复核。"}


def render_markdown(payload: dict) -> str:
    arms = payload["arms"]
    v1, v3, b = arms["9b_v1"]["summary"], arms["9b_v3"]["summary"], arms["35b_v3"]["summary"]
    cmp = payload["comparison"]
    cfg = payload["config"]
    lines = [f"# 35b 模型 prompt v3 A/B（{payload['generated_at'][:10]}）", "",
             f"> v3 问题库前 {cfg['n_actual']} 条（申请 {cfg['n_requested']}，跳过 {cfg['skipped']}）· "
             f"9b+v1 / 9b+v3（复用已有结果）vs **35b+v3**（`{cfg['model_35b']}` 现场生成）· "
             f"同聚类材料 + guard strict_line。", "",
             "## 结论", "",
             f"**{cmp['verdict']}** —— 建议：`{cmp['recommendation']}`"
             f"（optional_model=可作可选模型 / optional_model_limited=灰度继续调优 / keep_v1=保留 9b+v1 / hold=先修）", ""]
    lines += [f"- {r}" for r in cmp["reasons"]]
    lines += ["", "## 三臂对比", "",
              "| 指标 | 9b+v1（基线） | 9b+v3 | **35b+v3** |", "|---|---|---|---|",
              f"| 可答率 | {v1['answerable_rate']:.0%}（{v1['success']}/{v1['total']}） | "
              f"{v3['answerable_rate']:.0%}（{v3['success']}/{v3['total']}） | "
              f"**{b['answerable_rate']:.0%}**（{b['success']}/{b['total']}） |",
              f"| guard 通过率 | {v1['guard_pass_rate']:.0%} | {v3['guard_pass_rate']:.0%} | "
              f"**{b['guard_pass_rate']:.0%}** |",
              f"| fallback 率 | {v1['fallback_rate']:.0%} | {v3['fallback_rate']:.0%} | {b['fallback_rate']:.0%} |",
              f"| 平均推断比例 | {v1['avg_inference_ratio']} | {v3['avg_inference_ratio']} | {b['avg_inference_ratio']} |",
              f"| 平均引用数 | {v1['avg_citations']} | {v3['avg_citations']} | {b['avg_citations']} |",
              f"| 平均耗时(s) | {v1['avg_elapsed_seconds']} | {v3['avg_elapsed_seconds']} | {b['avg_elapsed_seconds']} |",
              f"| 错误数 | {len(v1['errors'])} | {len(v3['errors'])} | {len(b['errors'])} |", "",
              "## 逐条对比", "",
              "| # | 类别 | 问题 | 9b+v1 状态/guard | 9b+v3 状态/guard | 35b+v3 状态/guard/推断 |",
              "|---|---|---|---|---|---|"]
    for i, r in enumerate(payload["rows"], 1):
        a1, a3, a35 = r.get("9b_v1") or {}, r.get("9b_v3") or {}, r.get("35b_v3") or {}
        lines.append(f"| {i} | {r.get('category')} | {str(r.get('question'))[:34]} | "
                     f"{a1.get('status')}/{a1.get('guard_passed')} | "
                     f"{a3.get('status')}/{a3.get('guard_passed')} | "
                     f"{a35.get('status')}/{a35.get('guard_passed')}/{a35.get('inference_ratio')} |")
    lines += ["", "## 风险与说明", ""]
    lines += [f"- 风险：{r}" for r in cmp["risks"]]
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
    ap = argparse.ArgumentParser(description="35b 模型 prompt v3 A/B（验证模型能力瓶颈）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑 35b+v3 并与 9b 两基线对比")
    run.add_argument("--n", type=int, default=DEFAULT_N, help=f"样本条数（默认 {DEFAULT_N}）")
    run.add_argument("--k", type=int, default=DEFAULT_K)
    run.add_argument("--model", default=DEFAULT_MODEL, help=f"35b 模型（默认 {DEFAULT_MODEL}）")
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    run.add_argument("--v3-bank", default=str(V3_BANK))
    run.add_argument("--v1-baseline", default=str(V1_BASELINE))
    run.add_argument("--v3-baseline", default=str(V3_9B_BASELINE))
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_ab(args.n, k=args.k, model=args.model, timeout=args.timeout,
                         deadline_seconds=args.deadline, v3_bank=args.v3_bank,
                         v1_baseline=args.v1_baseline, v3_baseline=args.v3_baseline)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except Prompt35bError as exc:
        print(f"[kb_qa_prompt_v3_35b] FAILED: {exc}", file=sys.stderr)
        return 1
    arms = payload["arms"]
    v1, v3, b = arms["9b_v1"]["summary"], arms["9b_v3"]["summary"], arms["35b_v3"]["summary"]
    print(f"[kb_qa_prompt_v3_35b] n={payload['config']['n_actual']} skipped={payload['config']['skipped']} "
          f"model={payload['config']['model_35b']}")
    print(f"[kb_qa_prompt_v3_35b] 9b+v1 : answerable={v1['answerable_rate']:.0%} guard={v1['guard_pass_rate']:.0%} "
          f"infer={v1['avg_inference_ratio']} cites={v1['avg_citations']} elapsed={v1['avg_elapsed_seconds']}s")
    print(f"[kb_qa_prompt_v3_35b] 9b+v3 : answerable={v3['answerable_rate']:.0%} guard={v3['guard_pass_rate']:.0%} "
          f"infer={v3['avg_inference_ratio']} cites={v3['avg_citations']} elapsed={v3['avg_elapsed_seconds']}s")
    print(f"[kb_qa_prompt_v3_35b] 35b+v3: answerable={b['answerable_rate']:.0%} guard={b['guard_pass_rate']:.0%} "
          f"infer={b['avg_inference_ratio']} cites={b['avg_citations']} elapsed={b['avg_elapsed_seconds']}s")
    print(f"[kb_qa_prompt_v3_35b] verdict={payload['comparison']['verdict']} "
          f"recommendation={payload['comparison']['recommendation']}")
    print(f"[kb_qa_prompt_v3_35b] json={paths[0]}")
    print(f"[kb_qa_prompt_v3_35b] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
