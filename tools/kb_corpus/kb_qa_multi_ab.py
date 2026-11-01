# -*- coding: utf-8 -*-
"""多单元聚合扩样 A/B（single vs multi）：决定 multi 是否设为默认问答模式。

背景：wave16 AT 在 12 条抽验上 multi 可答率 25%→66.7%（+41.7pp），但 n=12 太小。
本模块在 `kb_qa_multi.py`（不改动）基础上做正式 A/B：

  ① 样本：wave15 v3 问题库 50 条（`kb_qa_bank_v3_20261101.json`）前 N 条（默认 30；LLM 慢可减并注明）；
  ② 每问跑 two arms：
     - single：`kb_qa.ask`（单单元/标准检索问答），其中 v3 已抽验的前 12 条**复用已记录结果**（--reuse-v3-single，默认开）；
     - multi：`kb_qa_multi.answer_multi`（top-k 检索+聚类+聚合 prompt）；
     记录 success / refusal / fallback / skipped、引用数、耗时、答案长度/类型；
  ③ 统计：可答率配对 McNemar（精确二项）、平均引用、耗时比、按模板类别/主题拆分、答案类型分布；
  ④ 判定 multi 是否默认：可答率提升 ≥15pp、回退（single success→multi fail）不明显增多、耗时可接受、McNemar p<0.05；
  ⑤ 输出 `research/reports/kb_qa_multi_ab_20261101.{json,md}`。

CLI：
  python tools/kb_corpus/kb_qa_multi_ab.py run --n 30 --k 8          # 默认复用 v3 前 12 条 single 基线
  python tools/kb_corpus/kb_qa_multi_ab.py run --n 30 --no-reuse     # 全部重跑 single（更慢）
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_multi_ab_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_multi_ab_20261101.md"
DEFAULT_N = 30
DEFAULT_K = 8
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 540
RATE_DELTA_THRESHOLD = 0.15
LATENCY_RATIO_MAX = 2.5
MCNEMAR_ALPHA = 0.05


class AbError(RuntimeError):
    """A/B 流程异常（问题库不可读等）。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise AbError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_kb_qa = None
_multi = None


def kb_qa():
    global _kb_qa
    if _kb_qa is None:
        _kb_qa = _load_module("kb_qa_for_ab", "kb_qa.py")
    return _kb_qa


def kb_multi():
    global _multi
    if _multi is None:
        _multi = _load_module("kb_qa_multi_for_ab", "kb_qa_multi.py")
    return _multi


# ---------------------------------------------------------------------------
# ① 样本加载
# ---------------------------------------------------------------------------
def load_v3_bank(path: Path | str = V3_BANK) -> dict:
    """读 v3 问题库：50 条 questions + 前 12 条 single 抽验基线（可复用）。"""
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
    baseline = {}
    for sample in (data.get("answer_validation") or {}).get("samples") or []:
        if isinstance(sample, dict) and sample.get("id"):
            baseline[sample["id"]] = sample
    return {"path": str(path), "questions": questions, "baseline": baseline}


# ---------------------------------------------------------------------------
# ② 单条结果规整
# ---------------------------------------------------------------------------
def classify_single(result: dict) -> dict:
    """single（kb_qa.ask）结果 → 统一状态/引用/答案类型。"""
    answer = str(result.get("answer") or "")
    confidence = result.get("confidence") or {}
    fallback = bool(result.get("fallback"))
    refusal = (confidence.get("level") == "none") or ("没有足够依据" in answer)
    if fallback:
        status = "fallback"
    elif refusal or not answer:
        status = "no_evidence"
    else:
        status = "success"
    citations = result.get("citations") or []
    answer_type = ("refusal" if status == "no_evidence" else
                   "fallback" if status == "fallback" else "direct_answer")
    return {"status": status, "success": status == "success", "refusal": status == "no_evidence",
            "fallback": status == "fallback", "citations": len(citations),
            "answer_type": answer_type, "answer_length": len(answer),
            "elapsed_seconds": result.get("elapsed_seconds")}


def classify_multi(result: dict) -> dict:
    """multi（kb_qa_multi.answer_multi）结果 → 统一状态/引用/答案类型（区分直接事实/推断）。"""
    status = kb_multi().classify_status(result)
    direct = int(result.get("direct_fact_markers") or 0)
    infer = int(result.get("inference_markers") or 0)
    answer = str(result.get("answer") or "")
    if status["status"] == "fallback":
        answer_type = "fallback"
    elif status["status"] == "no_evidence":
        answer_type = "refusal"
    elif direct and infer:
        answer_type = "direct_fact+inference"
    elif direct:
        answer_type = "direct_fact"
    elif infer:
        answer_type = "inference"
    else:
        answer_type = "answer"
    return {"status": status["status"], "success": status["success"],
            "refusal": status["no_evidence"], "fallback": status["fallback"],
            "citations": status["citations"], "answer_type": answer_type,
            "answer_length": len(answer), "cluster_count": result.get("cluster_count", 0),
            "direct_facts": direct, "inferences": infer,
            "elapsed_seconds": result.get("elapsed_seconds")}


def baseline_to_single(sample: dict) -> dict:
    """v3 已记录的 single 抽验结果 → 统一结构（复用，避免重复 LLM 调用）。"""
    status = sample.get("status") or ("no_evidence" if (sample.get("confidence") or {}).get("level") == "none"
                                      else "success")
    citations = sample.get("citations") or []
    return {"status": status, "success": status == "success", "refusal": status == "no_evidence",
            "fallback": status == "fallback", "citations": len(citations),
            "answer_type": "refusal" if status == "no_evidence" else "direct_answer",
            "answer_length": len(str(sample.get("answer") or "")) if sample.get("answer") else 0,
            "elapsed_seconds": 0.0, "reused": True}


# ---------------------------------------------------------------------------
# ③ 统计：McNemar / 汇总 / 判定
# ---------------------------------------------------------------------------
def mcnemar_exact(b: int, c: int) -> float:
    """配对 McNemar 精确检验（双侧二项）：b=single fail→multi success，c=single success→multi fail。"""
    n = int(b) + int(c)
    if n == 0:
        return 1.0
    k = min(int(b), int(c))
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return round(min(1.0, 2 * tail), 6)


def summarize_ab(rows: list[dict]) -> dict:
    total = len(rows)
    a = b = c = d = 0
    for row in rows:
        s_ok = bool((row.get("single") or {}).get("success"))
        m_ok = bool((row.get("multi") or {}).get("success"))
        if s_ok and m_ok:
            a += 1
        elif (not s_ok) and m_ok:
            b += 1
        elif s_ok and (not m_ok):
            c += 1
        else:
            d += 1
    single_success = a + c
    multi_success = a + b
    single_rate = round(single_success / total, 4) if total else 0.0
    multi_rate = round(multi_success / total, 4) if total else 0.0
    single_cites = [row["single"]["citations"] for row in rows
                    if (row.get("single") or {}).get("success")]
    multi_cites = [row["multi"]["citations"] for row in rows
                   if (row.get("multi") or {}).get("success")]
    measured_single = [row["single"].get("elapsed_seconds") or 0 for row in rows
                       if not (row.get("single") or {}).get("reused")]
    measured_single = [x for x in measured_single if x]
    multi_elapsed = [row["multi"].get("elapsed_seconds") or 0 for row in rows]
    single_avg_elapsed = round(sum(measured_single) / len(measured_single), 2) if measured_single else 0.0
    multi_avg_elapsed = round(sum(multi_elapsed) / len(multi_elapsed), 2) if multi_elapsed else 0.0
    latency_ratio = round(multi_avg_elapsed / single_avg_elapsed, 2) if single_avg_elapsed else None
    paired = {"a_both_success": a, "b_single_fail_multi_success": b,
              "c_single_success_multi_fail": c, "d_both_fail": d,
              "mcnemar_p": mcnemar_exact(b, c)}
    by_category: dict[str, dict] = {}
    for row in rows:
        cat = row.get("category") or "未知"
        g = by_category.setdefault(cat, {"total": 0, "single_success": 0, "multi_success": 0})
        g["total"] += 1
        g["single_success"] += 1 if (row.get("single") or {}).get("success") else 0
        g["multi_success"] += 1 if (row.get("multi") or {}).get("success") else 0
    category_rows = []
    for cat, g in by_category.items():
        category_rows.append({"category": cat, "total": g["total"],
                              "single_success": g["single_success"],
                              "multi_success": g["multi_success"],
                              "single_rate": round(g["single_success"] / g["total"], 4),
                              "multi_rate": round(g["multi_success"] / g["total"], 4),
                              "delta": round((g["multi_success"] - g["single_success"]) / g["total"], 4)})
    category_rows.sort(key=lambda x: (-x["delta"], x["category"]))
    answer_types: dict[str, int] = {}
    for row in rows:
        t = (row.get("multi") or {}).get("answer_type") or "unknown"
        answer_types[t] = answer_types.get(t, 0) + 1
    return {
        "total": total,
        "single": {"success": single_success, "rate": single_rate,
                   "no_evidence": total - single_success - sum(1 for r in rows if (r.get("single") or {}).get("fallback")),
                   "fallback": sum(1 for r in rows if (r.get("single") or {}).get("fallback")),
                   "avg_citations": round(sum(single_cites) / len(single_cites), 2) if single_cites else 0.0,
                   "avg_elapsed_seconds": single_avg_elapsed,
                   "reused_count": sum(1 for r in rows if (r.get("single") or {}).get("reused"))},
        "multi": {"success": multi_success, "rate": multi_rate,
                  "no_evidence": sum(1 for r in rows if (r.get("multi") or {}).get("refusal")),
                  "fallback": sum(1 for r in rows if (r.get("multi") or {}).get("fallback")),
                  "avg_citations": round(sum(multi_cites) / len(multi_cites), 2) if multi_cites else 0.0,
                  "avg_elapsed_seconds": multi_avg_elapsed,
                  "avg_clusters": round(sum((r.get("multi") or {}).get("cluster_count") or 0 for r in rows) / total, 2) if total else 0.0,
                  "direct_facts": sum((r.get("multi") or {}).get("direct_facts") or 0 for r in rows),
                  "inferences": sum((r.get("multi") or {}).get("inferences") or 0 for r in rows)},
        "delta": {"answerable_rate": round(multi_rate - single_rate, 4),
                  "avg_citations": round((sum(multi_cites) / len(multi_cites) if multi_cites else 0)
                                         - (sum(single_cites) / len(single_cites) if single_cites else 0), 2),
                  "latency_ratio": latency_ratio},
        "paired": paired, "by_category": category_rows, "answer_types": answer_types,
        "improved_ids": [r["id"] for r in rows if (r.get("improved"))],
        "regressed_ids": [r["id"] for r in rows if (r.get("regressed"))],
    }


def decide(summary: dict, n_actual: int) -> dict:
    delta = summary["delta"]["answerable_rate"]
    paired = summary["paired"]
    regressed = len(summary["regressed_ids"])
    improved = len(summary["improved_ids"])
    latency_ratio = summary["delta"]["latency_ratio"]
    criteria = [
        {"name": f"可答率提升 ≥{RATE_DELTA_THRESHOLD:.0%}", "threshold": f"≥{RATE_DELTA_THRESHOLD:.0%}",
         "actual": f"{delta:+.0%}", "pass": delta >= RATE_DELTA_THRESHOLD},
        {"name": "回退（single success→multi fail）不明显增多",
         "threshold": "回退数 ≤ max(1, 10%·改善数)",
         "actual": f"回退 {regressed} / 改善 {improved}",
         "pass": regressed <= max(1, int(round(improved * 0.1)))},
        {"name": "耗时可接受", "threshold": f"multi/single 单条耗时比 ≤{LATENCY_RATIO_MAX}",
         "actual": f"{latency_ratio if latency_ratio is not None else '—'}",
         "pass": latency_ratio is None or latency_ratio <= LATENCY_RATIO_MAX},
        {"name": "McNemar 配对检验显著", "threshold": f"p<{MCNEMAR_ALPHA}",
         "actual": f"p={paired['mcnemar_p']}",
         "pass": paired["mcnemar_p"] < MCNEMAR_ALPHA},
        {"name": "样本量足够", "threshold": "≥30 条（LLM 慢可减并注明）", "actual": f"{n_actual} 条", "pass": n_actual >= 30},
    ]
    passed = sum(1 for c in criteria if c["pass"])
    core_pass = criteria[0]["pass"] and criteria[1]["pass"] and criteria[3]["pass"]
    if core_pass and criteria[2]["pass"]:
        verdict = "建议将 multi 设为问答默认模式（A/B 通过）"
        default_multi = True
    elif criteria[0]["pass"]:
        verdict = "建议 multi 作为可选模式（有提升但未全部达标，先灰度不设默认）"
        default_multi = False
    else:
        verdict = "不建议切换（可答率提升未达 ≥15pp）"
        default_multi = False
    reasons = [
        f"可答率：single {summary['single']['rate']:.0%}（{summary['single']['success']}/{summary['total']}）→ "
        f"multi {summary['multi']['rate']:.0%}（{summary['multi']['success']}/{summary['total']}），Δ{delta:+.0%}。",
        f"配对：改善 {improved} 条 / 回退 {regressed} 条；McNemar p={paired['mcnemar_p']}。",
        f"平均引用：{summary['single']['avg_citations']} → {summary['multi']['avg_citations']}；"
        f"单条平均耗时：{summary['single']['avg_elapsed_seconds']}s → {summary['multi']['avg_elapsed_seconds']}s"
        f"（比值 {latency_ratio}）。",
    ]
    risks = [
        "multi 聚合允许推断，存在跨单元过度综合/错误拼接风险；引用校验不验证语义支撑，需人工抽查。",
        "single 基线中前 12 条复用 v3 旧结果（不同时间点），与本次 multi 存在时间差，可能影响耗时/状态可比性。",
        "样本受 LLM 时间预算限制（LLM 慢时减条），未达 30 条时统计功效有限。",
        "耗时受 Ollama 负载影响大（v3 single 与本次 multi 测量时点不同），latency_ratio 仅作参考。",
        "若切换默认，建议先灰度（页面/CLI --multi 可选）+ 影子对比，再全量。",
    ]
    next_actions = [
        "若通过：在 kb_qa CLI/HTTP（未接线）增加 --multi 选项并灰度；保留 single fallback（multi 降级时自动回退）。",
        "扩样到 50 条（全量问题库）复跑本 A/B，验证 Δ 与 McNemar 稳定性。",
        "对 multi 的答案抽样人工复核直接事实/推断边界与引用语义支撑。",
    ]
    return {"default_multi": default_multi, "verdict": verdict, "criteria": criteria,
            "passed": passed, "total_criteria": len(criteria),
            "reasons": reasons, "risks": risks, "next_actions": next_actions}


# ---------------------------------------------------------------------------
# ④ 运行 + 输出
# ---------------------------------------------------------------------------
def run_ab(n: int = DEFAULT_N, k: int = DEFAULT_K, model: str = DEFAULT_MODEL,
           timeout: float = DEFAULT_TIMEOUT, deadline_seconds: float = DEFAULT_DEADLINE,
           reuse_v3_single: bool = True, single_fn=None, multi_fn=None,
           search_fn=None, bank_path: Path | str = V3_BANK,
           clock=time.monotonic) -> dict:
    """A/B 主流程：逐条 single vs multi，记录/统计/判定（默认复用 v3 前 12 条 single 基线）。"""
    started = clock()
    bank = load_v3_bank(bank_path)
    questions = bank["questions"][:max(1, int(n))]
    multi = kb_multi()
    single_call = single_fn or (lambda q, **kw: kb_qa().ask(q, **kw))
    multi_call = multi_fn or (lambda q, **kw: multi.answer_multi(q, **kw))
    rows = []
    single_skipped = multi_skipped = 0
    for q in questions:
        row = {"id": q.get("id"), "question": q.get("question"),
               "category": q.get("category"), "theme": q.get("theme")}
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            single_skipped += 1
            multi_skipped += 1
            row.update({"single": {"status": "skipped_deadline", "elapsed_seconds": 0.0},
                        "multi": {"status": "skipped_deadline", "elapsed_seconds": 0.0}})
            rows.append(row)
            continue
        baseline = bank["baseline"].get(q.get("id"))
        if reuse_v3_single and baseline:
            row["single"] = baseline_to_single(baseline)
        else:
            try:
                res = single_call(q["question"], topk=max(5, k), model=model,
                                  timeout=max(5.0, min(float(timeout), remaining)))
            except Exception:  # noqa: BLE001
                res = {"answer": None, "citations": [], "confidence": {"level": "none"},
                       "fallback": True, "elapsed_seconds": 0.0}
            cls = classify_single(res)
            cls.setdefault("reused", False)
            row["single"] = cls
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            multi_skipped += 1
            row["multi"] = {"status": "skipped_deadline", "elapsed_seconds": 0.0}
            rows.append(row)
            continue
        try:
            multi_res = multi_call(q["question"], k=k, search_fn=search_fn, model=model,
                                   timeout=max(5.0, min(float(timeout), remaining)))
        except Exception:  # noqa: BLE001
            multi_res = {"answer": None, "citations": [], "confidence": {"level": "none"},
                         "fallback": True, "refusal": False, "cluster_count": 0,
                         "direct_fact_markers": 0, "inference_markers": 0, "elapsed_seconds": 0.0}
        row["multi"] = classify_multi(multi_res)
        row["improved"] = (not row["single"].get("success")) and row["multi"].get("success")
        row["regressed"] = bool(row["single"].get("success")) and not row["multi"].get("success")
        rows.append(row)
    summary = summarize_ab(rows)
    decision = decide(summary, len(rows))
    return {
        "schema_version": 1, "kind": "kb_qa_multi_ab",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"v3_bank": str(bank_path), "questions_total": len(bank["questions"]),
                   "baseline_records": len(bank["baseline"])},
        "config": {"n_requested": int(n), "n_actual": len(rows),
                   "reuse_v3_single": bool(reuse_v3_single), "k": max(5, min(8, int(k))),
                   "model": model, "timeout_seconds": timeout,
                   "deadline_seconds": deadline_seconds,
                   "single_skipped": single_skipped, "multi_skipped": multi_skipped},
        "note": "single 为 kb_qa.ask（单单元标准问答）；multi 为 kb_qa_multi 多单元聚合；"
                "前 12 条 single 复用 wave15 v3 已记录结果（reused=true，耗时不参与 single 均值）",
        "summary": summary, "comparison": rows, "decision": decision,
        "disclaimer": "A/B 结果仅供研究参考，不构成投资建议；multi 允许推断，引用/事实需人工复核。",
        "elapsed_seconds": round(clock() - started, 2),
    }


def _fmt(v, pct=False):
    if v is None:
        return "—"
    if pct:
        return f"{v:.0%}"
    return str(v)


def render_markdown(payload: dict) -> str:
    s = payload["summary"]
    cfg = payload["config"]
    dec = payload["decision"]
    lines = [f"# 多单元聚合扩样 A/B（single vs multi，{payload['generated_at'][:10]}）", "",
             f"> v3 问题库前 **{cfg['n_actual']}** 条（申请 {cfg['n_requested']}）· top-k={cfg['k']} · "
             f"single=`kb_qa.ask` / multi=`kb_qa_multi` 聚合 · 复用 v3 前 {s['single']['reused_count']} 条 single 基线 · "
             f"qwen3.5:9b 串行", "",
             "## 结论与建议", "",
             f"**{'建议设为默认' if dec['default_multi'] else '不设默认'}** —— {dec['verdict']}", ""]
    lines += [f"- {r}" for r in dec["reasons"]]
    lines += ["", "### 判定阈值", "", "| 判据 | 阈值 | 实测 | 结果 |", "|---|---|---|---|"]
    for c in dec["criteria"]:
        lines.append(f"| {c['name']} | {c['threshold']} | {c['actual']} | {'✅' if c['pass'] else '❌'} |")
    lines += ["", "## A/B 汇总", "",
              "| 指标 | single | multi | Δ |", "|---|---|---|---|",
              f"| 可答率 | {s['single']['rate']:.0%}（{s['single']['success']}/{s['total']}） | "
              f"**{s['multi']['rate']:.0%}**（{s['multi']['success']}/{s['total']}） | "
              f"{s['delta']['answerable_rate']:+.0%} |",
              f"| 成功/拒答/降级 | {s['single']['success']} / {s['single']['no_evidence']} / {s['single']['fallback']} | "
              f"{s['multi']['success']} / {s['multi']['no_evidence']} / {s['multi']['fallback']} | — |",
              f"| 平均引用（成功样本） | {s['single']['avg_citations']} | {s['multi']['avg_citations']} | "
              f"{s['delta']['avg_citations']:+.2f} |",
              f"| 单条平均耗时(s) | {s['single']['avg_elapsed_seconds']} | {s['multi']['avg_elapsed_seconds']} | "
              f"比值 {s['delta']['latency_ratio']} |",
              f"| 平均聚类 | — | {s['multi']['avg_clusters']} | — |",
              f"| 直接事实/推断标记 | — | {s['multi']['direct_facts']} / {s['multi']['inferences']} | — |", "",
              "## 配对 McNemar 检验", "",
              "| 配对 | 数量 |", "|---|---|",
              f"| a：both success | {s['paired']['a_both_success']} |",
              f"| b：single fail → multi success（改善） | {s['paired']['b_single_fail_multi_success']} |",
              f"| c：single success → multi fail（回退） | {s['paired']['c_single_success_multi_fail']} |",
              f"| d：both fail | {s['paired']['d_both_fail']} |",
              f"| **McNemar 精确 p** | **{s['paired']['mcnemar_p']}** |", "",
              "## 按模板类别", "",
              "| 类别 | 条数 | single | multi | Δ |", "|---|---|---|---|---|"]
    for g in s["by_category"]:
        lines.append(f"| {g['category']} | {g['total']} | {g['single_rate']:.0%} | {g['multi_rate']:.0%} | {g['delta']:+.0%} |")
    lines += ["", "## 答案类型分布（multi）", ""]
    for t, c in sorted(s["answer_types"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- {t}：{c} 条")
    if s["improved_ids"]:
        lines += ["", f"改善样本（single 拒答→multi 成功，{len(s['improved_ids'])} 条）：" + "、".join(s["improved_ids"])]
    if s["regressed_ids"]:
        lines += ["", f"回退样本（{len(s['regressed_ids'])} 条）：" + "、".join(s["regressed_ids"])]
    lines += ["", "## 逐条对比", "",
              "| # | 类别 | 问题 | single 状态/引用/耗时 | multi 状态/引用/耗时 | 聚类 | 答案类型 |",
              "|---|---|---|---|---|---|---|"]
    for i, row in enumerate(payload["comparison"], 1):
        si = row.get("single") or {}
        mu = row.get("multi") or {}
        lines.append(f"| {i} | {row.get('category')} | {str(row.get('question'))[:40]} | "
                     f"{si.get('status')}/{si.get('citations')}/{si.get('elapsed_seconds')}s | "
                     f"{mu.get('status')}/{mu.get('citations')}/{mu.get('elapsed_seconds')}s | "
                     f"{mu.get('cluster_count') or '—'} | {mu.get('answer_type')} |")
    lines += ["", "## 风险与后续", ""]
    lines += [f"- 风险：{r}" for r in dec["risks"]]
    lines += [f"- 下一步：{r}" for r in dec["next_actions"]]
    lines += ["", "## 说明", "", f"- {payload['disclaimer']}", f"- {payload['note']}",
              f"- 耗时 {payload['elapsed_seconds']}s；配置 {json.dumps(cfg, ensure_ascii=False)}"]
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
    ap = argparse.ArgumentParser(description="多单元聚合扩样 A/B（single vs multi，决定是否默认）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="跑 A/B 对比并输出 JSON+MD")
    run.add_argument("--n", type=int, default=DEFAULT_N, help=f"样本条数（默认 {DEFAULT_N}）")
    run.add_argument("--k", type=int, default=DEFAULT_K, help=f"multi 检索单元数 5..8（默认 {DEFAULT_K}）")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    run.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    run.add_argument("--reuse-v3-single", dest="reuse", action="store_true", default=True,
                     help="复用 v3 前 12 条 single 基线（默认开）")
    run.add_argument("--no-reuse", dest="reuse", action="store_false", help="不复用，全部重跑 single")
    run.add_argument("--bank", default=str(V3_BANK), help="v3 问题库路径")
    run.add_argument("--out-json", default=str(OUT_JSON))
    run.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "run":  # pragma: no cover
        return 2
    try:
        payload = run_ab(n=args.n, k=args.k, model=args.model, timeout=args.timeout,
                         deadline_seconds=args.deadline, reuse_v3_single=args.reuse,
                         bank_path=args.bank)
        paths = write_outputs(payload, args.out_json, args.out_md)
    except AbError as exc:
        print(f"[kb_qa_multi_ab] FAILED: {exc}", file=sys.stderr)
        return 1
    s = payload["summary"]
    dec = payload["decision"]
    print(f"[kb_qa_multi_ab] n={payload['config']['n_actual']} reused_single={s['single']['reused_count']} "
          f"skipped(single/multi)={payload['config']['single_skipped']}/{payload['config']['multi_skipped']}")
    print(f"[kb_qa_multi_ab] single: rate={s['single']['rate']:.0%} ({s['single']['success']}/{s['total']}) "
          f"avg_cit={s['single']['avg_citations']} avg_t={s['single']['avg_elapsed_seconds']}s")
    print(f"[kb_qa_multi_ab] multi:  rate={s['multi']['rate']:.0%} ({s['multi']['success']}/{s['total']}) "
          f"avg_cit={s['multi']['avg_citations']} avg_t={s['multi']['avg_elapsed_seconds']}s clusters={s['multi']['avg_clusters']}")
    print(f"[kb_qa_multi_ab] delta={json.dumps(s['delta'], ensure_ascii=False)} paired={json.dumps(s['paired'], ensure_ascii=False)}")
    print(f"[kb_qa_multi_ab] decision={dec['verdict']} default_multi={dec['default_multi']} "
          f"passed={dec['passed']}/{dec['total_criteria']}")
    print(f"[kb_qa_multi_ab] json={paths[0]}")
    print(f"[kb_qa_multi_ab] md={paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
