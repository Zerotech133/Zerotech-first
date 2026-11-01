# -*- coding: utf-8 -*-
"""citation_retry 扩样 A/B（30 条，决定是否把 citation_retry 切为默认）。

背景：task-102 在 13 条小样本上 citation_retry +15.4pp guard 通过率，需扩样复核。

本模块：
  ① 选样：v3 问题库 50 条中**排除 task-102 已用 11 条**，取 30 条（与 wave94 扩样部分重叠，已注明）；
  ② 两臂真实调用（可并行跑两个进程）：
     - off：`kb_qa_multi.answer_multi(..., prompt_version="v4", citation_retry=False)`；
     - on ：同上 + `citation_retry=True`（答案 <2 单元且未拒答 → 追加一次强化指令重试）；
     两臂统一 `kb_qa_guard.apply_guard(..., marker_mode="segment", on_fail="fallback")`；
  ③ 统计：guard 通过率 / 可答率 / fallback / 引用数 / 去重单元 / 推断比例 / 质量（可解析、支撑）/
     耗时 / 重试触发与改善次数；
  ④ 切默认判定：**on 臂 guard 通过率 ≥85% 且相对 off ≥+8pp 且质量不降（可解析/支撑降幅 ≤2pp）
     且耗时比 ≤1.6** → 建议切默认，并给出透传到 kb_qa.ask/CLI/HTTP 的改法建议；
  ⑤ 输出 `research/reports/citation_retry_scale_20261101.{json,md}`。

CLI（推荐两臂并行）：
  python tools/kb_corpus/citation_retry_scale.py arm --which off --n 30 --out %TEMP%/off.json
  python tools/kb_corpus/citation_retry_scale.py arm --which on  --n 30 --out %TEMP%/on.json
  python tools/kb_corpus/citation_retry_scale.py combine --off %TEMP%/off.json --on %TEMP%/on.json
串行：
  python tools/kb_corpus/citation_retry_scale.py run --n 30
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
TASK102_JSON = REPO / "research" / "reports" / "citation_retry_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "citation_retry_scale_20261101.json"
OUT_MD = REPO / "research" / "reports" / "citation_retry_scale_20261101.md"
DEFAULT_N = 30
DEFAULT_K = 8
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 720
CRITERIA = {"min_on_guard_pass_rate": 0.85, "min_guard_gain": 0.08,
            "max_quality_drop": 0.02, "max_latency_ratio": 1.6}


class ScaleError(RuntimeError):
    """citation_retry 扩样异常。"""


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
        _cache[name] = _load_module(f"{name}_for_retry_scale", f"{name}.py")
    return _cache[name]


# ---------------------------------------------------------------------------
# ① 选样（排除 task-102 已用 11 条）
# ---------------------------------------------------------------------------
def task102_used_ids(path: Path | str = TASK102_JSON) -> set[str]:
    path = Path(path)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    cfg = data.get("config") or {}
    return set((cfg.get("fallback_ids") or []) + (cfg.get("normal_ids") or []))


def select_questions(n: int = DEFAULT_N, path: Path | str = V3_BANK,
                     exclude: set[str] | None = None) -> list[dict]:
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
    used = set(exclude) if exclude is not None else task102_used_ids()
    pool = [q for q in qs if q.get("id") not in used]
    if not pool:
        raise ScaleError("排除已用样本后无可用问题")
    return pool[:max(1, int(n))]


# ---------------------------------------------------------------------------
# ② 单臂生成
# ---------------------------------------------------------------------------
def _run_one(question: dict, use_retry: bool, k: int, model: str, timeout: float) -> dict:
    multi = mod("kb_qa_multi")
    guard_mod = mod("kb_qa_guard")
    v2 = mod("kb_qa_prompt_v2")
    started = time.monotonic()
    error = None
    try:
        raw = multi.answer_multi(question["question"], k=k, model=model, timeout=timeout,
                                 prompt_version="v4", citation_retry=use_retry)
    except Exception as exc:  # noqa: BLE001
        raw = {"answer": None, "citations": [], "fallback": True, "refusal": False,
               "citation_retry": {"used": False}, "elapsed_seconds": 0,
               "error": f"{type(exc).__name__}: {exc}"}
        error = raw["error"]
    unit_ids, book_ids = guard_mod.load_kb_ids()
    guarded = guard_mod.apply_guard(raw, unit_ids, book_ids, on_fail="fallback",
                                    marker_mode="segment")
    cls = v2._classify(guarded)
    info = raw.get("citation_retry") or {}
    return {"id": question.get("id"), "question": question.get("question"),
            "category": question.get("category"), "theme": question.get("theme"),
            "status": cls["status"], "success": cls["success"],
            "no_evidence": cls["no_evidence"], "fallback": cls["fallback"],
            "guard_passed": bool(guarded.get("guard_passed")),
            "guard_issues": guarded.get("guard_issues") or [],
            "citations": len(guarded.get("citations") or []),
            "raw_citations": len(raw.get("citations") or []),
            "distinct_units": int(guarded.get("distinct_cited_units") or 0),
            "citation_resolvable_rate": guarded.get("citation_resolvable_rate"),
            "support_rate": guarded.get("support_rate"),
            "inference_ratio": guarded.get("inference_ratio"),
            "retry_used": bool(info.get("used")),
            "retry_first_units": info.get("first_units"),
            "retry_second_units": info.get("second_units"),
            "retry_improved": bool(info.get("improved")),
            "prompt_version": raw.get("prompt_version") or "v4",
            "guard_marker_mode": "segment",
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "error": error or raw.get("error")}


def run_arm(which: str, n: int = DEFAULT_N, k: int = DEFAULT_K, model: str = DEFAULT_MODEL,
            timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
            questions: list[dict] | None = None, clock=time.monotonic) -> dict:
    which = (which or "").strip().lower()
    if which not in ("off", "on"):
        raise ScaleError(f"which 只能是 off/on（收到 {which!r}）")
    started = clock()
    qs = questions if questions is not None else select_questions(n)
    rows, skipped = [], 0
    for q in qs:
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            continue
        rows.append(_run_one(q, use_retry=(which == "on"), k=k, model=model,
                             timeout=min(timeout, remaining)))
    return {"which": which, "n_requested": len(qs), "n_actual": len(rows), "skipped": skipped,
            "k": k, "model": model, "timeout_seconds": timeout,
            "deadline_seconds": deadline_seconds, "rows": rows,
            "elapsed_seconds": round(clock() - started, 2),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}


# ---------------------------------------------------------------------------
# ③ 统计 / 判定
# ---------------------------------------------------------------------------
def arm_stats(rows: list[dict]) -> dict:
    total = len(rows)

    def _avg(key):
        vals = [float(r.get(key) or 0.0) for r in rows]
        return round(sum(vals) / total, 4) if total else 0.0

    guard_passed = sum(1 for r in rows if r.get("guard_passed"))
    success = sum(1 for r in rows if r.get("success"))
    return {"total": total, "guard_passed": guard_passed,
            "guard_pass_rate": round(guard_passed / total, 4) if total else 0.0,
            "success": success, "answerable_rate": round(success / total, 4) if total else 0.0,
            "no_evidence": sum(1 for r in rows if r.get("no_evidence")),
            "fallback": sum(1 for r in rows if r.get("fallback")),
            "fallback_rate": round(sum(1 for r in rows if r.get("fallback")) / total, 4) if total else 0.0,
            "avg_citations": _avg("citations"), "avg_distinct_units": _avg("distinct_units"),
            "avg_inference_ratio": _avg("inference_ratio"),
            "avg_citation_resolvable_rate": _avg("citation_resolvable_rate"),
            "avg_support_rate": _avg("support_rate"),
            "avg_elapsed_seconds": _avg("elapsed_seconds"),
            "retry_used": sum(1 for r in rows if r.get("retry_used")),
            "retry_improved": sum(1 for r in rows if r.get("retry_improved")),
            "errors": [r.get("error") for r in rows if r.get("error")]}


def combine(off: dict, on: dict, criteria: dict | None = None) -> dict:
    criteria = criteria or dict(CRITERIA)
    s_off, s_on = arm_stats(off.get("rows") or []), arm_stats(on.get("rows") or [])
    # 合并臂级错误（采集中断/整体异常）与行级错误，供 hold 判定
    s_off["errors"] = list(off.get("errors") or []) + list(s_off.get("errors") or [])
    s_on["errors"] = list(on.get("errors") or []) + list(s_on.get("errors") or [])
    d_guard = round(s_on["guard_pass_rate"] - s_off["guard_pass_rate"], 4)
    d_ans = round(s_on["answerable_rate"] - s_off["answerable_rate"], 4)
    d_fb = round(s_on["fallback_rate"] - s_off["fallback_rate"], 4)
    quality_drop = max(0.0,
                       round(s_off["avg_citation_resolvable_rate"] - s_on["avg_citation_resolvable_rate"], 4),
                       round(s_off["avg_support_rate"] - s_on["avg_support_rate"], 4))
    latency_ratio = round((s_on["avg_elapsed_seconds"] or 0.0)
                          / (s_off["avg_elapsed_seconds"] or 1e-9), 3)
    checks = {
        "on_guard_ge_85": s_on["guard_pass_rate"] >= criteria["min_on_guard_pass_rate"],
        "guard_gain_ge_8pp": d_guard >= criteria["min_guard_gain"],
        "quality_not_worse": quality_drop <= criteria["max_quality_drop"],
        "latency_ratio_ok": latency_ratio <= criteria["max_latency_ratio"],
    }
    meets = all(checks.values())
    if s_on.get("errors"):
        decision, verdict = "hold", "on 臂存在调用错误，先修稳定性再评估"
    elif meets:
        decision = "switch_default"
        verdict = (f"扩样 {s_on['total']} 条：on 臂 guard 通过率 {s_on['guard_pass_rate']:.0%}（≥85%）、"
                   f"相对 off Δ{d_guard:+.0%}（≥+8pp）、质量不降、耗时比 {latency_ratio}（≤1.6）"
                   f" → **达到切默认标准**，建议把 citation_retry 透传并默认开启（灰度后全量）")
    elif checks["on_guard_ge_85"] and not checks["guard_gain_ge_8pp"]:
        decision = "keep_off"
        verdict = (f"on 臂 guard 通过率 {s_on['guard_pass_rate']:.0%} 达标，但相对 off 仅 {d_guard:+.0%}"
                   f"（<+8pp）→ 提升不显著，**暂不切默认**（保留参数开关）")
    else:
        decision = "keep_off"
        verdict = (f"on 臂 guard 通过率 {s_on['guard_pass_rate']:.0%} / Δ{d_guard:+.0%}，未达切默认标准"
                   f"（要求 ≥85% 且 Δ≥+8pp、质量不降、耗时比 ≤1.6）→ **不切默认**")
    reasons = [
        f"guard 通过率：off {s_off['guard_pass_rate']:.0%}（{s_off['guard_passed']}/{s_off['total']}）→ "
        f"on {s_on['guard_pass_rate']:.0%}（{s_on['guard_passed']}/{s_on['total']}），Δ{d_guard:+.0%}（标准 ≥+8pp）。",
        f"可答率：off {s_off['answerable_rate']:.0%} → on {s_on['answerable_rate']:.0%}（Δ{d_ans:+.0%}）；"
        f"fallback 率：{s_off['fallback_rate']:.0%} → {s_on['fallback_rate']:.0%}（Δ{d_fb:+.0%}）。",
        f"引用：平均引用 {s_off['avg_citations']} → {s_on['avg_citations']}（去重单元 "
        f"{s_off['avg_distinct_units']} → {s_on['avg_distinct_units']}）；推断比例 {s_off['avg_inference_ratio']} → {s_on['avg_inference_ratio']}。",
        f"质量：可解析率 {s_off['avg_citation_resolvable_rate']:.1%} → {s_on['avg_citation_resolvable_rate']:.1%}，"
        f"支撑率 {s_off['avg_support_rate']:.1%} → {s_on['avg_support_rate']:.1%}（质量降幅 {quality_drop:.1%}）。",
        f"耗时：{s_off['avg_elapsed_seconds']}s → {s_on['avg_elapsed_seconds']}s（比值 {latency_ratio}，标准 ≤1.6）；"
        f"重试触发 {s_on['retry_used']} 次、改善 {s_on['retry_improved']} 次。",
    ]
    rollout = {
        "need_transparent_pass": True,
        "steps": [
            "kb_qa.ask() 增加 citation_retry 参数并透传给 _ask_multi/multi.answer_multi（默认 False，灰度期 True）",
            "CLI：kb_qa.py ask 子命令增加 --citation-retry/--no-citation-retry（默认按 KB_QA_CITATION_RETRY 环境变量）",
            "HTTP（api_kb_qa.handle）：新增 citation_retry=0/1 查询参数，默认 1（灰度），meta.guard 记录 retry 字段",
            "默认开启后观察 1 周 guard 通过率/耗时；回滚：KB_QA_CITATION_RETRY=0（CLI/HTTP 同源）",
        ],
        "env_switch": "KB_QA_CITATION_RETRY=1/0",
        "rollback": "设置 KB_QA_CITATION_RETRY=0 并重启 CLI 通道；HTTP 通道按既有灰度流程回滚（不直接重启 8091）",
    }
    risks = [
        f"样本 {s_on['total']} 条（30 条目标，LLM 慢时减少），与 wave94 扩样/既有实验存在部分题目重叠，非完全独立样本。",
        "重试仅在有效引用 <2 且未拒答时触发；触发样本耗时约 2x，对慢模型/高并发需评估吞吐。",
        "当前重试直接采用第二轮答案，可能丢失首轮有效内容（可按引用数择优作为后续优化）。",
        "两臂分别采集（可并行），模型负载/耗时受同时段其他任务影响。",
    ]
    return {"off": s_off, "on": s_on, "delta": {"guard_pass_rate": d_guard,
                                                 "answerable_rate": d_ans, "fallback_rate": d_fb,
                                                 "quality_drop": quality_drop,
                                                 "latency_ratio": latency_ratio},
            "criteria": criteria, "checks": checks, "meets_switch_criteria": bool(meets),
            "decision": decision, "verdict": verdict, "reasons": reasons,
            "retry_effect": {"used": s_on["retry_used"], "improved": s_on["retry_improved"],
                             "cost": f"+{s_on['retry_used']} 次 LLM 调用"},
            "rollout_suggestion": rollout, "risks": risks}


# ---------------------------------------------------------------------------
# ④ 串行 run / 落盘 / 合并
# ---------------------------------------------------------------------------
def run_scale(n: int = DEFAULT_N, k: int = DEFAULT_K, model: str = DEFAULT_MODEL,
              timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE) -> dict:
    started = time.monotonic()
    questions = select_questions(n)
    off = run_arm("off", questions=questions, k=k, model=model, timeout=timeout,
                  deadline_seconds=deadline_seconds / 2)
    on = run_arm("on", questions=questions, k=k, model=model, timeout=timeout,
                 deadline_seconds=deadline_seconds / 2)
    combined = combine(off, on)
    return _payload(off, on, combined, n, questions,
                    round(time.monotonic() - started, 2))


def _payload(off: dict, on: dict, combined: dict, n: int, questions: list[dict],
             elapsed: float) -> dict:
    return {"schema_version": 1, "kind": "citation_retry_scale",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "config": {"n_requested": int(n), "n_off_actual": off.get("n_actual"),
                       "n_on_actual": on.get("n_actual"), "k": off.get("k"),
                       "model": off.get("model"), "prompt_version": "v4",
                       "guard_marker_mode": "segment",
                       "selection": "v3 问题库排除 task-102 已用 11 条后取 %d 条（与 wave94 部分重叠）" % len(questions),
                       "excluded_task102_ids": sorted(task102_used_ids()),
                       "criteria": dict(CRITERIA)},
            "note": "off/on 两臂真实 LLM 采集（可并行）；同一批问题；guard 统一 segment+fallback",
            "off_arm": off, "on_arm": on, "comparison": combined,
            "elapsed_seconds": elapsed,
            "disclaimer": "citation_retry 扩样 A/B 结果仅供研究参考，不构成投资建议。"}


def render_markdown(payload: dict) -> str:
    cmp = payload["comparison"]
    s_off, s_on = cmp["off"], cmp["on"]
    cfg = payload["config"]
    lines = [f"# citation_retry 扩样 A/B（{payload['generated_at'][:10]}）", "",
             f"> v3 问题库 **{cfg['selection']}**（off {cfg['n_off_actual']} / on {cfg['n_on_actual']} 条）· "
             f"v4 prompt + guard segment · qwen3.5:9b · 真实 LLM 两臂。", "",
             "## 结论", "",
             f"**{cmp['verdict']}** —— 决策：`{cmp['decision']}`"
             f"（switch_default=切默认 / keep_off=保留参数开关 / hold=先修）", ""]
    lines += [f"- {r}" for r in cmp["reasons"]]
    lines += ["", "## 切默认标准校验", "",
              "| 标准 | 阈值 | 实测 | 是否满足 |", "|---|---|---|---|",
              f"| on 臂 guard 通过率 | ≥85% | {s_on['guard_pass_rate']:.0%} | "
              f"{'✅' if cmp['checks']['on_guard_ge_85'] else '❌'} |",
              f"| 相对 off 提升 | ≥+8pp | {cmp['delta']['guard_pass_rate']:+.0%} | "
              f"{'✅' if cmp['checks']['guard_gain_ge_8pp'] else '❌'} |",
              f"| 质量不降 | 降幅 ≤2pp | {cmp['delta']['quality_drop']:.1%} | "
              f"{'✅' if cmp['checks']['quality_not_worse'] else '❌'} |",
              f"| 耗时比 | ≤1.6 | {cmp['delta']['latency_ratio']} | "
              f"{'✅' if cmp['checks']['latency_ratio_ok'] else '❌'} |",
              "", "## off vs on", "",
              "| 指标 | off（默认） | on（citation_retry） | Δ |", "|---|---|---|---|",
              f"| guard 通过率 | {s_off['guard_pass_rate']:.0%}（{s_off['guard_passed']}/{s_off['total']}） | "
              f"{s_on['guard_pass_rate']:.0%}（{s_on['guard_passed']}/{s_on['total']}） | "
              f"{cmp['delta']['guard_pass_rate']:+.0%} |",
              f"| 可答率 | {s_off['answerable_rate']:.0%} | {s_on['answerable_rate']:.0%} | {cmp['delta']['answerable_rate']:+.0%} |",
              f"| fallback 率 | {s_off['fallback_rate']:.0%} | {s_on['fallback_rate']:.0%} | {cmp['delta']['fallback_rate']:+.0%} |",
              f"| 拒答数 | {s_off['no_evidence']} | {s_on['no_evidence']} | — |",
              f"| 平均引用数 | {s_off['avg_citations']} | {s_on['avg_citations']} | — |",
              f"| 平均去重单元 | {s_off['avg_distinct_units']} | {s_on['avg_distinct_units']} | — |",
              f"| 引用可解析率 | {s_off['avg_citation_resolvable_rate']:.1%} | {s_on['avg_citation_resolvable_rate']:.1%} | — |",
              f"| 支撑率 | {s_off['avg_support_rate']:.1%} | {s_on['avg_support_rate']:.1%} | — |",
              f"| 平均推断比例 | {s_off['avg_inference_ratio']} | {s_on['avg_inference_ratio']} | — |",
              f"| 平均耗时(s) | {s_off['avg_elapsed_seconds']} | {s_on['avg_elapsed_seconds']} | {cmp['delta']['latency_ratio']}x |",
              f"| 重试触发/改善 | — | {s_on['retry_used']} / {s_on['retry_improved']} | — |", ""]
    lines += ["## 切默认透传改法建议（本任务只建议，不改默认）", ""]
    lines += [f"- {step}" for step in cmp["rollout_suggestion"]["steps"]]
    lines += [f"- 环境开关：`{cmp['rollout_suggestion']['env_switch']}`",
              f"- 回滚：{cmp['rollout_suggestion']['rollback']}", "",
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


def _read_arm(path: Path | str) -> dict:
    path = Path(path)
    if not path.exists():
        raise ScaleError(f"中间结果不存在：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScaleError(f"中间结果读取失败：{path}（{exc}）") from exc


def combine_files(off_path: Path | str, on_path: Path | str,
                  out_json: Path | str = OUT_JSON, out_md: Path | str = OUT_MD) -> dict:
    off, on = _read_arm(off_path), _read_arm(on_path)
    combined = combine(off, on)
    payload = _payload(off, on, combined, max(off.get("n_requested") or 0, on.get("n_requested") or 0),
                       [{"id": r.get("id")} for r in (on.get("rows") or [])],
                       round((off.get("elapsed_seconds") or 0) + (on.get("elapsed_seconds") or 0), 1))
    write_outputs(payload, out_json, out_md)
    return payload


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="citation_retry 扩样 A/B（30 条）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    arm = sub.add_parser("arm", help="跑单臂（off/on），写中间 JSON")
    arm.add_argument("--which", required=True, choices=["off", "on"])
    arm.add_argument("--n", type=int, default=DEFAULT_N)
    arm.add_argument("--k", type=int, default=DEFAULT_K)
    arm.add_argument("--model", default=DEFAULT_MODEL)
    arm.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    arm.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    arm.add_argument("--out", required=True)
    comb = sub.add_parser("combine", help="合并两臂中间结果 → 最终 JSON+MD")
    comb.add_argument("--off", required=True)
    comb.add_argument("--on", required=True)
    comb.add_argument("--out-json", default=str(OUT_JSON))
    comb.add_argument("--out-md", default=str(OUT_MD))
    run = sub.add_parser("run", help="串行跑两臂并输出最终 JSON+MD")
    run.add_argument("--n", type=int, default=DEFAULT_N)
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
            payload = run_arm(args.which, n=args.n, k=args.k, model=args.model,
                              timeout=args.timeout, deadline_seconds=args.deadline)
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"[retry-scale] arm={payload['which']} n={payload['n_actual']} "
                  f"skipped={payload['skipped']} elapsed={payload['elapsed_seconds']}s -> {out}")
            return 0
        if args.cmd == "combine":
            payload = combine_files(args.off, args.on, args.out_json, args.out_md)
            cmp = payload["comparison"]
            print(f"[retry-scale] off n={payload['config']['n_off_actual']} on n={payload['config']['n_on_actual']}")
            print(f"[retry-scale] guard off={cmp['off']['guard_pass_rate']:.0%} -> on={cmp['on']['guard_pass_rate']:.0%} "
                  f"(delta={cmp['delta']['guard_pass_rate']:+.0%}) latency_ratio={cmp['delta']['latency_ratio']}")
            print(f"[retry-scale] meets={cmp['meets_switch_criteria']} decision={cmp['decision']}")
            print(f"[retry-scale] json={args.out_json}")
            print(f"[retry-scale] md={args.out_md}")
            return 0
        payload = run_scale(args.n, args.k, args.model, args.timeout, args.deadline)
        write_outputs(payload, args.out_json, args.out_md)
        cmp = payload["comparison"]
        print(f"[retry-scale] meets={cmp['meets_switch_criteria']} decision={cmp['decision']}")
        print(f"[retry-scale] json={args.out_json}")
        return 0
    except ScaleError as exc:
        print(f"[retry-scale] FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
