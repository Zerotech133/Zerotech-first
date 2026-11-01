# -*- coding: utf-8 -*-
"""prompt v4 扩样验证（30-40 条 A/B）：决定是否把 v4+segment 切为默认。

背景：wave29 小样本 12 条显示 prompt v4 + guard segment 可答率 50%→75%（+25pp），
需扩样 30-40 条复核后才能切默认。本模块：

  ① 选样：v3 问题库第 13-42 条（30 条，**避开 wave25/29 已用的前 12 条**，覆盖各主题）；
  ② 两臂真实调用（支持并行跑两个进程）：
     - v1：`kb_qa.ask(mode="multi", guard=True, on_guard_fail="fallback")`（现 multi prompt）；
     - v4：prompt v4 + guard **segment** 分段解析（复用 kb_qa_prompt_v4）；
  ③ 统计：可答率、guard 通过率、fallback 率、拒答数、引用可解析率、支撑率、
     推断比例、引用数、平均耗时；
  ④ 与 wave29（12 条）对比一致性；
  ⑤ 判定切默认标准：**可答率提升 ≥10pp 且质量不降（可解析/支撑率不降）且耗时可控**
     → 给出切默认建议与回滚方案。
  输出 `research/reports/kb_qa_prompt_v4_scale_20261101.{json,md}`。

CLI（推荐两臂并行，各自写中间 JSON 后 combine）：
  python tools/kb_corpus/kb_qa_prompt_v4_scale.py arm --which v1 --n 30 --out %TEMP%/v1.json
  python tools/kb_corpus/kb_qa_prompt_v4_scale.py arm --which v4 --n 30 --out %TEMP%/v4.json
  python tools/kb_corpus/kb_qa_prompt_v4_scale.py combine --v1 %TEMP%/v1.json --v4 %TEMP%/v4.json
也可串行：
  python tools/kb_corpus/kb_qa_prompt_v4_scale.py run --n 30
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
WAVE29_JSON = REPO / "research" / "reports" / "kb_qa_prompt_v4_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_prompt_v4_scale_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_prompt_v4_scale_20261101.md"
DEFAULT_N = 30
DEFAULT_OFFSET = 12  # 跳过 wave25/29 已用的前 12 条
DEFAULT_K = 8
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 720


class ScaleError(RuntimeError):
    """扩样验证异常。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ScaleError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_cache: dict = {}


def mod(name: str):
    if name not in _cache:
        _cache[name] = _load_module(f"{name}_for_scale", f"{name}.py")
    return _cache[name]


# ---------------------------------------------------------------------------
# ① 选样
# ---------------------------------------------------------------------------
def select_questions(n: int = DEFAULT_N, offset: int = DEFAULT_OFFSET,
                     path: Path | str = V3_BANK) -> list[dict]:
    """选 v3 问题库 [offset, offset+n) 条（默认 13-42，避开 wave25/29 前 12 条）。"""
    path = Path(path)
    if not path.exists():
        raise ScaleError(f"v3 问题库不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScaleError(f"v3 问题库读取失败：{path}（{exc}）") from exc
    qs = [q for q in data.get("questions") or [] if isinstance(q, dict) and q.get("question")]
    if not qs:
        raise ScaleError(f"v3 问题库 questions 为空：{path}")
    selected = qs[max(0, int(offset)): max(0, int(offset)) + max(1, int(n))]
    if not selected:
        raise ScaleError(f"选样为空（offset={offset}, n={n}, 总 {len(qs)} 条）")
    return selected


# ---------------------------------------------------------------------------
# ② 两臂生成
# ---------------------------------------------------------------------------
def _v1_row(question: dict, payload: dict, elapsed: float) -> dict:
    guard_mod = mod("kb_qa_guard")
    unit_ids, book_ids = guard_mod.load_kb_ids()
    metrics = guard_mod.compute_guard_metrics(payload, unit_ids, book_ids, marker_mode="legacy")
    cls = mod("kb_qa_prompt_v2")._classify(payload)
    return {"id": question.get("id"), "question": question.get("question"),
            "category": question.get("category"), "theme": question.get("theme"),
            "status": cls["status"], "success": cls["success"],
            "no_evidence": cls["no_evidence"], "fallback": cls["fallback"],
            "guard_passed": metrics["guard_passed"], "guard_action": payload.get("guard_action"),
            "guard_issues": metrics["guard_issues"],
            "citation_resolvable_rate": metrics["citation_resolvable_rate"],
            "support_rate": metrics["support_rate"],
            "hallucination_rate": metrics["hallucination_rate"],
            "inference_ratio": metrics["inference_ratio"],
            "citations": int(metrics["citation_total"] or 0),
            "distinct_cited_units": metrics["distinct_cited_units"],
            "answer_length": len(str(payload.get("answer") or "")),
            "elapsed_seconds": round(elapsed, 2), "error": None}


def _v4_row(question: dict, answer: str, clusters: list, elapsed: float,
            error: str | None = None) -> dict:
    v4 = mod("kb_qa_prompt_v4")
    v2 = mod("kb_qa_prompt_v2")
    guard_mod = mod("kb_qa_guard")
    multi = mod("kb_qa_multi")
    unit_ids, book_ids = guard_mod.load_kb_ids()
    raw = v2._raw_from_answer(question["question"], answer, clusters, multi)
    if error:
        raw["fallback"] = True
    metrics = v4.guard_metrics_segment(raw, unit_ids, book_ids)
    guarded = v4.apply_guard_segment(raw, unit_ids, book_ids, on_fail="fallback")
    cls = v2._classify(guarded)
    return {"id": question.get("id"), "question": question.get("question"),
            "category": question.get("category"), "theme": question.get("theme"),
            "status": cls["status"], "success": cls["success"],
            "no_evidence": cls["no_evidence"], "fallback": cls["fallback"],
            "guard_passed": metrics["guard_passed"], "guard_action": guarded.get("guard_action"),
            "guard_issues": metrics["guard_issues"],
            "citation_resolvable_rate": metrics["citation_resolvable_rate"],
            "support_rate": metrics["support_rate"],
            "hallucination_rate": metrics["hallucination_rate"],
            "inference_ratio": metrics["inference_ratio"],
            "citations": int(metrics["citation_total"] or 0),
            "distinct_cited_units": metrics["distinct_cited_units"],
            "segment_direct_facts": guard_mod.count_markers(
                str(raw.get("answer") or ""), "strict_line")[0] if hasattr(guard_mod, "count_markers") else None,
            "answer_length": len(str(guarded.get("answer") or "")),
            "elapsed_seconds": round(elapsed, 2), "error": error}


def run_arm(which: str, n: int = DEFAULT_N, offset: int = DEFAULT_OFFSET,
            k: int = DEFAULT_K, model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
            deadline_seconds: float = DEFAULT_DEADLINE, questions: list[dict] | None = None,
            ask_fn=None, chat_fn=None, search_fn=None, clock=time.monotonic) -> dict:
    """跑单臂（v1 或 v4）→ 中间结果 dict（供 combine 使用）。"""
    which = (which or "").strip().lower()
    if which not in ("v1", "v4"):
        raise ScaleError(f"which 只能是 v1/v4（收到 {which!r}）")
    started = clock()
    qs = questions if questions is not None else select_questions(n, offset)
    rows = []
    live = skipped = 0
    v4mod = mod("kb_qa_prompt_v4")
    multi = mod("kb_qa_multi")
    for q in qs:
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            continue
        t = clock()
        error = None
        if which == "v1":
            fn = ask_fn or (lambda question, **kw: mod("kb_qa").ask(question, **kw))
            try:
                payload = fn(q["question"], topk=k, model=model, timeout=min(timeout, remaining),
                             mode="multi", guard=True, on_guard_fail="fallback")
            except Exception as exc:  # noqa: BLE001
                payload = {"answer": None, "citations": [], "confidence": {"level": "none"},
                           "fallback": True, "refusal": False}
                error = f"{type(exc).__name__}: {exc}"
            row = _v1_row(q, payload, clock() - t)
        else:
            try:
                clusters = multi.retrieve_clusters(q["question"], k=k, search_fn=search_fn)
                prompt = v4mod.build_prompt_v4(q["question"], clusters)
                chat = chat_fn or multi.call_ollama
                answer = chat(prompt, model=model, timeout=min(timeout, remaining))
            except Exception as exc:  # noqa: BLE001
                clusters, answer = [], ""
                error = f"{type(exc).__name__}: {exc}"
                # 降级：用规则摘录（无 LLM）
                try:
                    if not clusters:
                        clusters = multi.retrieve_clusters(q["question"], k=k, search_fn=search_fn)
                    answer = multi.rule_based_multi(clusters)
                except Exception:  # noqa: BLE001
                    pass
            row = _v4_row(q, answer, clusters, clock() - t, error=error)
        if error:
            row["error"] = error
        rows.append(row)
        live += 1
    return {"which": which, "n_requested": len(qs), "n_actual": live, "skipped": skipped,
            "model": model, "k": k, "timeout_seconds": timeout,
            "deadline_seconds": deadline_seconds, "rows": rows,
            "elapsed_seconds": round(clock() - started, 2),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}


# ---------------------------------------------------------------------------
# ③ 统计 / 一致性 / 结论
# ---------------------------------------------------------------------------
def arm_stats(rows: list[dict]) -> dict:
    total = len(rows)
    def _avg(key):
        vals = [float(r.get(key) or 0.0) for r in rows]
        return round(sum(vals) / total, 4) if total else 0.0
    success = sum(1 for r in rows if r.get("success"))
    return {"total": total, "success": success,
            "answerable_rate": round(success / total, 4) if total else 0.0,
            "no_evidence": sum(1 for r in rows if r.get("no_evidence")),
            "fallback": sum(1 for r in rows if r.get("fallback")),
            "fallback_rate": round(sum(1 for r in rows if r.get("fallback")) / total, 4) if total else 0.0,
            "guard_passed": sum(1 for r in rows if r.get("guard_passed")),
            "guard_pass_rate": round(sum(1 for r in rows if r.get("guard_passed")) / total, 4) if total else 0.0,
            "avg_citation_resolvable_rate": _avg("citation_resolvable_rate"),
            "avg_support_rate": _avg("support_rate"),
            "avg_hallucination_rate": _avg("hallucination_rate"),
            "avg_inference_ratio": _avg("inference_ratio"),
            "avg_citations": _avg("citations"),
            "avg_elapsed_seconds": _avg("elapsed_seconds"),
            "errors": [r.get("error") for r in rows if r.get("error")]}


def wave29_reference(path: Path | str = WAVE29_JSON) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    v1 = (data.get("v1") or {}).get("summary") or {}
    v4 = (data.get("v4") or {}).get("summary") or {}
    if not v1 or not v4:
        return {}
    return {"n": v4.get("total"), "v1_answerable_rate": v1.get("answerable_rate"),
            "v4_answerable_rate": v4.get("answerable_rate"),
            "delta_answerable": round((v4.get("answerable_rate") or 0) - (v1.get("answerable_rate") or 0), 4),
            "v1_guard_pass_rate": v1.get("guard_pass_rate"),
            "v4_guard_pass_rate": v4.get("guard_pass_rate")}


def combine(v1: dict, v4: dict, wave29: dict | None = None,
            criteria: dict | None = None) -> dict:
    criteria = criteria or {"min_answerable_gain": 0.10, "max_quality_drop": 0.02,
                            "max_latency_ratio": 1.5}
    s1, s4 = arm_stats(v1.get("rows") or []), arm_stats(v4.get("rows") or [])
    delta_ans = round(s4["answerable_rate"] - s1["answerable_rate"], 4)
    delta_guard = round(s4["guard_pass_rate"] - s1["guard_pass_rate"], 4)
    quality_drop = max(0.0, round(s1["avg_citation_resolvable_rate"] - s4["avg_citation_resolvable_rate"], 4),
                       round(s1["avg_support_rate"] - s4["avg_support_rate"], 4))
    latency_ratio = round((s4["avg_elapsed_seconds"] or 0.0) / (s1["avg_elapsed_seconds"] or 1e-9), 3)
    meets = (delta_ans >= criteria["min_answerable_gain"]
             and quality_drop <= criteria["max_quality_drop"]
             and latency_ratio <= criteria["max_latency_ratio"])
    wave29 = wave29 or {}
    consistent = None
    if wave29.get("delta_answerable") is not None:
        same_direction = (delta_ans >= 0) == (wave29["delta_answerable"] >= 0)
        consistent = bool(same_direction and delta_ans >= 0)
    if meets:
        verdict = (f"扩样 {s4['total']} 条：可答率 {s1['answerable_rate']:.0%}→{s4['answerable_rate']:.0%}"
                   f"（{delta_ans:+.0%}≥10pp）、质量不降、耗时比 {latency_ratio}≤1.5 → **达到切默认标准**，建议切 v4 默认（灰度后全量）")
        decision = "switch_default"
    elif delta_ans >= criteria["min_answerable_gain"] and not meets:
        verdict = (f"扩样 {s4['total']} 条：可答率提升 {delta_ans:+.0%} 达标，但质量/耗时未达标"
                   f"（质量降幅 {quality_drop:.1%}，耗时比 {latency_ratio}）→ 不切默认，继续优化")
        decision = "hold"
    elif delta_ans > 0:
        verdict = (f"扩样 {s4['total']} 条：可答率小幅提升 {delta_ans:+.0%}（<10pp）→ 未达切默认标准，保留 v1 继续观察")
        decision = "keep_v1"
    else:
        verdict = (f"扩样 {s4['total']} 条：可答率未提升（{delta_ans:+.0%}）→ 不回切，保留 v1")
        decision = "keep_v1"
    reasons = [
        f"可答率：{s1['answerable_rate']:.0%}（{s1['success']}/{s1['total']}）→ {s4['answerable_rate']:.0%}"
        f"（{s4['success']}/{s4['total']}），Δ{delta_ans:+.1%}（标准 ≥+10pp）。",
        f"guard 通过率：{s1['guard_pass_rate']:.0%} → {s4['guard_pass_rate']:.0%}（Δ{delta_guard:+.1%}）；"
        f"fallback：{s1['fallback_rate']:.0%} → {s4['fallback_rate']:.0%}；拒答：{s1['no_evidence']} → {s4['no_evidence']}。",
        f"质量：引用可解析率 {s1['avg_citation_resolvable_rate']:.1%} → {s4['avg_citation_resolvable_rate']:.1%}；"
        f"支撑率 {s1['avg_support_rate']:.1%} → {s4['avg_support_rate']:.1%}；"
        f"推断比例 {s1['avg_inference_ratio']} → {s4['avg_inference_ratio']}；引用数 {s1['avg_citations']} → {s4['avg_citations']}。",
        f"耗时：{s1['avg_elapsed_seconds']}s → {s4['avg_elapsed_seconds']}s（比值 {latency_ratio}）。",
    ]
    risks = [
        "扩样条数受 LLM 时间预算限制（本轮 %d 条，可减到 25 条），仍小于全量 50 条；切换默认后需继续监控。" % s4["total"],
        "v1 与 v4 为串行/并行分批采样，模型负载与耗时受同时段其他任务影响。",
        "segment 解析依赖模型输出段标题；若模型不写标题会退化为行首标记统计。",
        "切默认为行为变更：需在 kb_qa_multi 增加 prompt 版本开关 + guard marker_mode 配置，默认 v4/segment，保留 v1 回退开关。",
    ]
    rollback = ("回滚方案：配置 `KB_QA_PROMPT_VERSION=v1` 或将 kb_qa_multi 默认 prompt 切回 v1、"
                "guard `marker_mode=\"legacy\"`（或 strict_line）；静态配置改动无需重启 8091 时对 CLI 即时生效，"
                "HTTP 通道需按既有流程重启/灰度验证。")
    return {"v1": s1, "v4": s4,
            "delta": {"answerable_rate": delta_ans, "guard_pass_rate": delta_guard,
                      "quality_drop": quality_drop, "latency_ratio": latency_ratio},
            "criteria": criteria, "meets_switch_criteria": bool(meets),
            "wave29_consistency": {"reference": wave29, "scale_delta": delta_ans,
                                   "consistent_direction": consistent},
            "decision": decision, "verdict": verdict, "reasons": reasons,
            "risks": risks, "rollback": rollback}


# ---------------------------------------------------------------------------
# ④ 串行 run / 落盘
# ---------------------------------------------------------------------------
def run_scale(n: int = DEFAULT_N, offset: int = DEFAULT_OFFSET, k: int = DEFAULT_K,
              model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
              deadline_seconds: float = DEFAULT_DEADLINE, ask_fn=None, chat_fn=None,
              search_fn=None, clock=time.monotonic) -> dict:
    started = clock()
    questions = select_questions(n, offset)
    v1 = run_arm("v1", questions=questions, k=k, model=model, timeout=timeout,
                 deadline_seconds=deadline_seconds / 2, ask_fn=ask_fn, clock=clock)
    v4 = run_arm("v4", questions=questions, k=k, model=model, timeout=timeout,
                 deadline_seconds=deadline_seconds / 2, chat_fn=chat_fn,
                 search_fn=search_fn, clock=clock)
    combined = combine(v1, v4, wave29_reference())
    return {"schema_version": 1, "kind": "kb_qa_prompt_v4_scale",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"n_requested": int(n), "offset": int(offset), "k": k, "model": model,
                       "timeout_seconds": timeout, "deadline_seconds": deadline_seconds,
                       "selection": "v3 问题库第 %d-%d 条（避开 wave25/29 前 12 条）"
                                    % (offset + 1, offset + n)},
            "note": "v1=现 multi prompt；v4=prompt v4 + guard segment；两臂真实 LLM 串行/并行分批采样",
            "v1_arm": v1, "v4_arm": v4, "comparison": combined,
            "elapsed_seconds": round(clock() - started, 2),
            "disclaimer": "扩样 A/B 结果仅供研究参考，不构成投资建议；答案需人工复核。"}


def render_markdown(payload: dict) -> str:
    cmp = payload["comparison"]
    s1, s4 = cmp["v1"], cmp["v4"]
    cfg = payload["config"]
    lines = [f"# prompt v4 扩样验证 A/B（{payload['generated_at'][:10]}）", "",
             f"> {cfg['selection']}（cover 多主题）· v1 = 现 multi prompt · v4 = prompt v4 + guard segment · "
             f"真实 LLM 两臂（qwen3.5:9b）· 串行/并行分批采样。", "",
             "## 结论", "",
             f"**{cmp['verdict']}** —— 决策：`{cmp['decision']}`"
             f"（switch_default=切默认 / keep_v1=保留 v1 / hold=暂缓）", ""]
    lines += [f"- {r}" for r in cmp["reasons"]]
    lines += ["", "## 切默认标准校验", "",
              "| 标准 | 阈值 | 实测 | 是否满足 |", "|---|---|---|---|",
              f"| 可答率提升 | ≥+10pp | {cmp['delta']['answerable_rate']:+.1%} | "
              f"{'✅' if cmp['delta']['answerable_rate'] >= cmp['criteria']['min_answerable_gain'] else '❌'} |",
              f"| 质量不降（可解析/支撑） | 降幅 ≤2pp | 降幅 {cmp['delta']['quality_drop']:.1%} | "
              f"{'✅' if cmp['delta']['quality_drop'] <= cmp['criteria']['max_quality_drop'] else '❌'} |",
              f"| 耗时可控 | 比值 ≤1.5 | {cmp['delta']['latency_ratio']} | "
              f"{'✅' if cmp['delta']['latency_ratio'] <= cmp['criteria']['max_latency_ratio'] else '❌'} |",
              "", "## 两臂对比", "",
              "| 指标 | v1（现 multi prompt） | v4（prompt v4 + segment） | Δ |", "|---|---|---|---|",
              f"| 可答率 | {s1['answerable_rate']:.0%}（{s1['success']}/{s1['total']}） | "
              f"{s4['answerable_rate']:.0%}（{s4['success']}/{s4['total']}） | {cmp['delta']['answerable_rate']:+.1%} |",
              f"| guard 通过率 | {s1['guard_pass_rate']:.0%} | {s4['guard_pass_rate']:.0%} | {cmp['delta']['guard_pass_rate']:+.1%} |",
              f"| fallback 率 | {s1['fallback_rate']:.0%} | {s4['fallback_rate']:.0%} | — |",
              f"| 拒答数 | {s1['no_evidence']} | {s4['no_evidence']} | — |",
              f"| 引用可解析率 | {s1['avg_citation_resolvable_rate']:.1%} | {s4['avg_citation_resolvable_rate']:.1%} | — |",
              f"| 支撑率 | {s1['avg_support_rate']:.1%} | {s4['avg_support_rate']:.1%} | — |",
              f"| 平均推断比例 | {s1['avg_inference_ratio']} | {s4['avg_inference_ratio']} | — |",
              f"| 平均引用数 | {s1['avg_citations']} | {s4['avg_citations']} | — |",
              f"| 平均耗时(s) | {s1['avg_elapsed_seconds']} | {s4['avg_elapsed_seconds']} | {cmp['delta']['latency_ratio']}x |",
              f"| 错误数 | {len(s1['errors'])} | {len(s4['errors'])} | — |", ""]
    w29 = cmp.get("wave29_consistency") or {}
    ref = w29.get("reference") or {}
    if ref:
        lines += ["## 与 wave29（12 条）一致性", "",
                  f"- wave29：v1 {ref.get('v1_answerable_rate', 0):.0%} → v4 {ref.get('v4_answerable_rate', 0):.0%}"
                  f"（Δ{ref.get('delta_answerable', 0):+.0%}，n={ref.get('n')}）",
                  f"- 本轮扩样：Δ{cmp['delta']['answerable_rate']:+.1%}；方向一致："
                  f"{'是' if w29.get('consistent_direction') else '否'}", ""]
    lines += ["## 逐条对比（前 12 条）", "",
              "| # | 类别 | 问题 | v1 状态/guard/引用 | v4 状态/guard/引用 |",
              "|---|---|---|---|---|"]
    paired = {(r.get("id")): r for r in (payload["v4_arm"].get("rows") or [])}
    for idx, r1 in enumerate((payload["v1_arm"].get("rows") or [])[:12], 1):
        r4 = paired.get(r1.get("id")) or {}
        lines.append(f"| {idx} | {r1.get('category')} | {str(r1.get('question'))[:34]} | "
                     f"{r1.get('status')}/{r1.get('guard_passed')}/{r1.get('citations')} | "
                     f"{r4.get('status')}/{r4.get('guard_passed')}/{r4.get('citations')} |")
    lines += ["", "## 切默认建议与回滚", "", f"- {cmp['rollback']}", "",
              "## 风险与说明", ""]
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


def _read_arm_partial(path: Path | str) -> dict:
    path = Path(path)
    if not path.exists():
        raise ScaleError(f"中间结果不存在：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScaleError(f"中间结果读取失败：{path}（{exc}）") from exc


def combine_files(v1_path: Path | str, v4_path: Path | str,
                  out_json: Path | str = OUT_JSON, out_md: Path | str = OUT_MD) -> dict:
    v1, v4 = _read_arm_partial(v1_path), _read_arm_partial(v4_path)
    combined = combine(v1, v4, wave29_reference())
    payload = {"schema_version": 1, "kind": "kb_qa_prompt_v4_scale",
               "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "config": {"n_requested": int(v4.get("n_requested") or 0),
                          "n_v1_actual": v1.get("n_actual"), "n_v4_actual": v4.get("n_actual"),
                          "offset": DEFAULT_OFFSET, "k": v4.get("k"), "model": v4.get("model"),
                          "timeout_seconds": v4.get("timeout_seconds"),
                          "selection": "v3 问题库第 13-42 条（避开 wave25/29 前 12 条）"},
               "note": "v1/v4 两臂分别采集后合并；均为真实 LLM 调用（可并行跑）",
               "v1_arm": v1, "v4_arm": v4, "comparison": combined,
               "elapsed_seconds": round((v1.get("elapsed_seconds") or 0) + (v4.get("elapsed_seconds") or 0), 1),
               "disclaimer": "扩样 A/B 结果仅供研究参考，不构成投资建议；答案需人工复核。"}
    write_outputs(payload, out_json, out_md)
    return payload


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="prompt v4 扩样验证 A/B（30-40 条）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    arm = sub.add_parser("arm", help="跑单臂（v1 或 v4），写中间 JSON")
    arm.add_argument("--which", required=True, choices=["v1", "v4"])
    arm.add_argument("--n", type=int, default=DEFAULT_N)
    arm.add_argument("--offset", type=int, default=DEFAULT_OFFSET)
    arm.add_argument("--k", type=int, default=DEFAULT_K)
    arm.add_argument("--model", default=DEFAULT_MODEL)
    arm.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    arm.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    arm.add_argument("--out", required=True, help="中间结果 JSON 路径")
    comb = sub.add_parser("combine", help="合并两臂中间结果 → 最终 JSON+MD")
    comb.add_argument("--v1", required=True)
    comb.add_argument("--v4", required=True)
    comb.add_argument("--out-json", default=str(OUT_JSON))
    comb.add_argument("--out-md", default=str(OUT_MD))
    run = sub.add_parser("run", help="串行跑两臂并直接输出最终 JSON+MD")
    run.add_argument("--n", type=int, default=DEFAULT_N)
    run.add_argument("--offset", type=int, default=DEFAULT_OFFSET)
    run.add_argument("--k", type=int, default=DEFAULT_K)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "arm":
            payload = run_arm(args.which, n=args.n, offset=args.offset, k=args.k,
                              model=args.model, timeout=args.timeout,
                              deadline_seconds=args.deadline)
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"[v4-scale] arm={payload['which']} n={payload['n_actual']} "
                  f"skipped={payload['skipped']} elapsed={payload['elapsed_seconds']}s -> {out}")
            return 0
        if args.cmd == "combine":
            payload = combine_files(args.v1, args.v4, args.out_json, args.out_md)
            cmp = payload["comparison"]
            print(f"[v4-scale] combined n_v1={payload['config']['n_v1_actual']} "
                  f"n_v4={payload['config']['n_v4_actual']}")
            print(f"[v4-scale] v1 answerable={cmp['v1']['answerable_rate']:.0%} "
                  f"guard={cmp['v1']['guard_pass_rate']:.0%} | "
                  f"v4 answerable={cmp['v4']['answerable_rate']:.0%} guard={cmp['v4']['guard_pass_rate']:.0%}")
            print(f"[v4-scale] delta={cmp['delta']} meets={cmp['meets_switch_criteria']} "
                  f"decision={cmp['decision']}")
            print(f"[v4-scale] json={args.out_json}")
            print(f"[v4-scale] md={args.out_md}")
            return 0
        payload = run_scale(args.n, args.offset, args.k, args.model, args.timeout,
                            args.deadline)
        write_outputs(payload, args.out_json, args.out_md)
        cmp = payload["comparison"]
        print(f"[v4-scale] n={payload['config']['n_requested']} v1={cmp['v1']['total']} v4={cmp['v4']['total']}")
        print(f"[v4-scale] delta_answerable={cmp['delta']['answerable_rate']:+.1%} "
              f"meets={cmp['meets_switch_criteria']} decision={cmp['decision']}")
        print(f"[v4-scale] json={args.out_json}")
        print(f"[v4-scale] md={args.out_md}")
        return 0
    except ScaleError as exc:
        print(f"[v4-scale] FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
