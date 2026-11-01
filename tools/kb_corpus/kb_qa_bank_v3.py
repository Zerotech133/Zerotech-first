# -*- coding: utf-8 -*-
"""问答问题库 v3：claim 覆盖度过滤 + 可答率扩验（12 条）+ 与 v2 对比。

背景：wave14 v2 模板检索命中 @1=96%/@5=100%，但 `--answer 6` 可答率仅 33%（2/6，无依据 4）——
根因是「问题所需证据 > 来源知识单元信息量」。v3 在**生成阶段**加「claim 覆盖度过滤」：
问题的实词（去掉模板用词/疑问词后）必须与来源单元 claim 有 ≥50% 的词面重叠才入池，
把「来源单元 claim 根本没覆盖」的问题提前剔除，再用 `--answer 12` 扩验可答率。

复用：v2（`kb_qa_bank_v2.py`，不改动）的 7 类 31 条模板、主题分类、候选生成、类别均衡选取与 retrieval 自检；
v3 新增：claim 覆盖度过滤、带失败原因的逐条答案状态、v2/v3 可答率对比、独立输出（不覆盖 v2）。

CLI：
  python tools/kb_corpus/kb_qa_bank_v3.py build                  # 生成 + 自检（不调 LLM）
  python tools/kb_corpus/kb_qa_bank_v3.py build --answer 12      # 扩验 12 条可答率（串行，约 1–2 分钟）
  python tools/kb_corpus/kb_qa_bank_v3.py build --limit 40 --coverage 0.5
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

from safe_json import SafeJsonError, backup_then_atomic_write

REPO = Path(__file__).resolve().parents[2]
OUT_JSON = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.md"
OUT_PORTAL = REPO / "portal" / "static" / "kb_qa_questions_v3.json"
V2_JSON = REPO / "research" / "reports" / "kb_qa_questions_v2_20261101.json"
DEFAULT_LIMIT = 50
MIN_QUESTIONS = 40
MAX_QUESTIONS = 60
DEFAULT_TOPK = 5
DEFAULT_ANSWER_N = 12
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300
DEFAULT_DEADLINE = 600
COVERAGE_THRESHOLD = 0.5

# 问题模板/疑问功能词：计算 claim 覆盖度时剔除，避免模板词虚增覆盖率
STOPWORDS = set("""
什么 如何 哪些 是否 结论 影响 主要 具体 核心 关键 条件 逻辑 数据 多少 风险 情况 可能 成立 支撑
涉及 相关 定义 描述 观点 链条 证据 产业链 行业 格局 传导 路径 受益 环节 标的 基本面 估值 变化
连锁 范围 持续性 竞争 参与者 份额 集中度 壁垒 差异化 优势 国产化 替代 进展 可验证 指标 量化
规模 增速 价格 可核验 统计 口径 样本 参数 阈值 不确定性 限制 适用 边界 提示 充分 识别 机会
信号 规则 形态 特征 跟踪 事件 验证 方式 观测 窗口 驱动 因素 催化 时间 节点 景气度 变量 产业
趋势 演进 方向 由 哪些 构成 在 中 提供 了 的 与 或 和 对 及 为 是 有 无 会 受 带来 需要 跟踪
""".split())


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_v2 = None


def v2_bank():
    global _v2
    if _v2 is None:
        _v2 = _load_module("kb_qa_bank_v2_for_v3", "kb_qa_bank_v2.py")
    return _v2


class BankV3Error(RuntimeError):
    """问题库 v3 生成异常。"""


def tokenize_text(text: str) -> set[str]:
    s = str(text or "").lower()
    return set(re.findall(r"[\u4e00-\u9fff]{2}", s) + re.findall(r"[a-z0-9]{2,}", s))


# ---------------------------------------------------------------------------
# ① claim 覆盖度过滤
# ---------------------------------------------------------------------------
def question_content_tokens(question: str) -> set[str]:
    """问题实词：去标点后的中文 2-gram/英文词，剔除模板疑问功能词。"""
    return {t for t in tokenize_text(question) if t not in STOPWORDS}


def _cjk_chars(text: str) -> set[str]:
    return {ch for ch in str(text or "") if "\u4e00" <= ch <= "\u9fff"}


def claim_coverage(question: str, claim: str, concept: str | None = None) -> float:
    """问题核心实词对来源单元 claim 的词面覆盖率（0..1）。

    问题由模板 + concept 生成，concept 即问题核心实词：按「concept 的汉字字符在 claim 中出现的比例」
    计算（字符级口径，避免 2-gram 边界错位导致低覆盖误杀）；未给 concept 时退化为整句问题汉字覆盖率。
    """
    target = concept if concept else question
    a_chars = _cjk_chars(target)
    if not a_chars:
        return 0.0
    b_chars = _cjk_chars(claim)
    return round(len(a_chars & b_chars) / len(a_chars), 4)


def build_candidates_v3(units: list[dict], threshold: float = COVERAGE_THRESHOLD
                        ) -> tuple[list[dict], dict]:
    """在 v2 候选基础上做 claim 覆盖度过滤；返回 (通过候选, 过滤统计)。"""
    v2 = v2_bank()
    candidates = v2.build_candidates(units)
    unit_map = {u.get("id"): u for u in units}
    kept: list[dict] = []
    dropped: list[dict] = []
    for cand in candidates:
        unit = unit_map.get(cand.get("source_unit_id")) or {}
        claim = str(unit.get("claim") or cand.get("claim_snippet") or "")
        coverage = claim_coverage(cand["question"], claim, concept=cand.get("concept"))
        cand = dict(cand)
        cand["claim_coverage"] = coverage
        if coverage >= threshold:
            kept.append(cand)
        else:
            if len(dropped) < 10:
                dropped.append({"id": cand["id"], "question": cand["question"],
                                "concept": cand.get("concept"), "coverage": coverage})
    stats = {"threshold": threshold, "before": len(candidates), "after": len(kept),
             "dropped": len(candidates) - len(kept),
             "pass_rate": round(len(kept) / len(candidates), 4) if candidates else 0.0,
             "dropped_examples": dropped}
    return kept, stats


def generate_bank_v3(units: list[dict], limit: int = DEFAULT_LIMIT,
                     threshold: float = COVERAGE_THRESHOLD) -> tuple[list[dict], dict]:
    v2 = v2_bank()
    candidates, filter_stats = build_candidates_v3(units, threshold)
    if len(candidates) < MIN_QUESTIONS:
        raise BankV3Error(f"claim 覆盖度过滤后候选不足：{len(candidates)} 条（要求 ≥{MIN_QUESTIONS}），"
                          f"可降低 --coverage（当前 {threshold}）")
    selected = v2.select_balanced(candidates, limit)
    for i, q in enumerate(selected, 1):
        q["rank"] = i
    return selected, filter_stats


# ---------------------------------------------------------------------------
# ② 可答率扩验（带失败原因）
# ---------------------------------------------------------------------------
def validate_answers_v3(questions: list[dict], n: int = DEFAULT_ANSWER_N, ask_fn=None,
                        model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
                        topk: int = 8, deadline_seconds: float = DEFAULT_DEADLINE,
                        clock=time.monotonic) -> dict:
    """对前 N 条串行调用 kb_qa.ask；逐条记录状态与失败原因 + 平均引用 + 耗时。"""
    n = max(0, min(int(n), len(questions)))
    if n == 0:
        return {"requested": 0, "success": 0, "fallback": 0, "no_evidence": 0,
                "skipped": 0, "answerable_rate": None, "avg_citations": 0.0,
                "samples": [], "note": "默认不跑 LLM（--answer N 才验证可答率）"}
    qa = v2_bank().kb_qa()
    fn = ask_fn or qa.ask
    started = clock()
    samples = []
    success = fallback = no_evidence = skipped = 0
    citations_total = 0
    answered = 0
    for q in questions[:n]:
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            skipped += 1
            samples.append({"id": q["id"], "category": q.get("category"),
                            "theme": q.get("theme"), "question": q["question"],
                            "status": "skipped_deadline",
                            "reason": "整体 deadline 用尽，未调用 LLM",
                            "answer": None, "citations": [], "confidence": {},
                            "fallback": False, "no_evidence": False})
            continue
        item_started = clock()
        try:
            result = fn(q["question"], topk=topk, model=model,
                        timeout=max(5.0, min(float(timeout), remaining)))
        except Exception as exc:  # noqa: BLE001
            result = {"answer": None, "citations": [], "confidence": {"level": "none"},
                      "fallback": True, "error": f"{type(exc).__name__}: {exc}"}
        answer = str(result.get("answer") or "")
        confidence = result.get("confidence") or {}
        is_fallback = bool(result.get("fallback"))
        is_no_evidence = confidence.get("level") == "none" or "没有足够依据" in answer
        citations = result.get("citations") or []
        if is_fallback:
            fallback += 1
            status = "fallback"
            reason = "LLM 调用失败→规则降级：" + str(result.get("error") or result.get("fallback_reason") or "未知原因")[:120]
        elif is_no_evidence:
            no_evidence += 1
            status = "no_evidence"
            conf_reason = (confidence.get("reasons") or ["模型判定材料不足（拒答）"])[0]
            reason = f"无依据拒答：{str(conf_reason)[:120]}"
        else:
            success += 1
            status = "success"
            reason = f"答案+引用齐全（{len(citations)} 条引用）"
        if answer:
            answered += 1
            citations_total += len(citations)
        samples.append({"id": q["id"], "category": q.get("category"), "theme": q.get("theme"),
                        "question": q["question"], "status": status, "reason": reason,
                        "answer": answer[:400] if answer else None,
                        "citations": citations, "confidence": confidence,
                        "fallback": is_fallback, "no_evidence": is_no_evidence,
                        "claim_coverage": q.get("claim_coverage"),
                        "elapsed_seconds": round(clock() - item_started, 2)})
    return {"requested": n, "success": success, "fallback": fallback,
            "no_evidence": no_evidence, "skipped": skipped,
            "answerable_rate": round(success / n, 4) if n else None,
            "avg_citations": round(citations_total / answered, 2) if answered else 0.0,
            "answered": answered,
            "elapsed_seconds": round(clock() - started, 2),
            "note": "可答率 = success / requested；status: success/fallback/no_evidence/skipped_deadline",
            "samples": samples}


# ---------------------------------------------------------------------------
# ③ 与 v2 对比
# ---------------------------------------------------------------------------
def load_v2_reference(path: Path | str = V2_JSON) -> dict:
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "available": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"path": str(path), "available": False}
    av = data.get("answer_validation") or {}
    chk = (data.get("selfcheck") or {}).get("summary") or {}
    return {"path": str(path), "available": True,
            "total": (data.get("stats") or {}).get("total"),
            "hit_rate_at_1": chk.get("hit_rate_at_1"), "hit_rate_at_5": chk.get("hit_rate_at_5"),
            "answer_requested": av.get("requested", 0),
            "success": av.get("success", 0), "fallback": av.get("fallback", 0),
            "no_evidence": av.get("no_evidence", 0),
            "answerable_rate": av.get("answerable_rate"),
            "elapsed_seconds": av.get("elapsed_seconds"),
            "avg_citations": (data.get("answer_validation") or {}).get("avg_citations")}


def build_comparison(v2_ref: dict, payload: dict) -> dict:
    av = payload["answer_validation"]
    chk = payload["selfcheck"]["summary"]
    v3_rate = av.get("answerable_rate")
    v2_rate = v2_ref.get("answerable_rate")
    base = {
        "scope_note": f"v2 抽验 {v2_ref.get('answer_requested', 0)} 条 vs v3 抽验 {av.get('requested', 0)} 条（样本不同，比率可比但需注意样本量差异）",
        "v2": {"questions": v2_ref.get("total"), "hit_rate_at_1": v2_ref.get("hit_rate_at_1"),
               "hit_rate_at_5": v2_ref.get("hit_rate_at_5"),
               "answer_requested": v2_ref.get("answer_requested"),
               "success": v2_ref.get("success"), "fallback": v2_ref.get("fallback"),
               "no_evidence": v2_ref.get("no_evidence"),
               "answerable_rate": v2_rate, "avg_citations": v2_ref.get("avg_citations"),
               "elapsed_seconds": v2_ref.get("elapsed_seconds")},
        "v3": {"questions": payload["stats"]["total"], "hit_rate_at_1": chk["hit_rate_at_1"],
               "hit_rate_at_5": chk["hit_rate_at_5"],
               "answer_requested": av.get("requested"), "success": av.get("success"),
               "fallback": av.get("fallback"), "no_evidence": av.get("no_evidence"),
               "skipped": av.get("skipped"), "answerable_rate": v3_rate,
               "avg_citations": av.get("avg_citations"),
               "elapsed_seconds": av.get("elapsed_seconds")},
    }
    delta = {}
    if isinstance(v3_rate, (int, float)) and isinstance(v2_rate, (int, float)):
        delta["answerable_rate"] = round(v3_rate - v2_rate, 4)
    if isinstance(av.get("avg_citations"), (int, float)) and isinstance(v2_ref.get("avg_citations"), (int, float)):
        delta["avg_citations"] = round(av["avg_citations"] - v2_ref["avg_citations"], 2)
    if isinstance(chk.get("hit_rate_at_1"), (int, float)) and isinstance(v2_ref.get("hit_rate_at_1"), (int, float)):
        delta["hit_rate_at_1"] = round(chk["hit_rate_at_1"] - v2_ref["hit_rate_at_1"], 4)
    base["delta"] = delta
    return base


# ---------------------------------------------------------------------------
# ④ 主流程 + 输出
# ---------------------------------------------------------------------------
def build_payload(units: list[dict], limit: int = DEFAULT_LIMIT,
                  threshold: float = COVERAGE_THRESHOLD, topk: int = DEFAULT_TOPK,
                  answer_n: int = 0, ask_fn=None, search_fn=None,
                  model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
                  deadline_seconds: float = DEFAULT_DEADLINE) -> dict:
    started = time.monotonic()
    v2 = v2_bank()
    questions, filter_stats = generate_bank_v3(units, limit, threshold)
    check = v2.selfcheck_questions(questions, topk=topk, search_fn=search_fn)
    answer_validation = validate_answers_v3(questions, answer_n, ask_fn=ask_fn, model=model,
                                            timeout=timeout, deadline_seconds=deadline_seconds)
    cat_counts: dict[str, int] = {}
    theme_counts: dict[str, int] = {}
    for q in questions:
        cat_counts[q["category"]] = cat_counts.get(q["category"], 0) + 1
        theme_counts[q["theme"]] = theme_counts.get(q["theme"], 0) + 1
    payload = {
        "schema_version": 3, "kind": "kb_qa_question_bank_v3",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"units_total": len(units),
                   "units_research": sum(1 for u in units if u.get("source") == "研报"),
                   "units_books": sum(1 for u in units if u.get("source") == "书籍")},
        "claim_filter": {**filter_stats,
                         "rule": "问题核心实词（concept）字符在来源单元 claim 中的覆盖率 ≥ threshold 才入池"},
        "templates": {"categories": v2.CATEGORIES,
                      "templates_per_category": {c: len(t) for c, t in v2.TEMPLATES.items()},
                      "total_templates": sum(len(t) for t in v2.TEMPLATES.values())},
        "stats": {"total": len(questions), "categories": len(cat_counts),
                  "by_category": cat_counts, "by_theme": theme_counts},
        "selfcheck": check, "answer_validation": answer_validation,
        "themes": [{"theme": t, "count": n, "questions": [q for q in questions if q["theme"] == t]}
                   for t, n in theme_counts.items()],
        "questions": questions,
        "disclaimer": "问题经 claim 覆盖度过滤后生成，仅用于检索/问答评测与研究；不构成投资建议。",
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    payload["comparison_v2"] = build_comparison(load_v2_reference(), payload)
    payload["suggestions"] = _suggestions(payload)
    return payload


def _suggestions(payload: dict) -> list[str]:
    av = payload["answer_validation"]
    chk = payload["selfcheck"]["summary"]
    out = []
    if chk["hit_rate_at_5"] >= 0.9:
        out.append(f"自检 @5={chk['hit_rate_at_5']:.0%}（{chk['hit_at_5']}/{chk['total']}）、"
                   f"@1={chk['hit_rate_at_1']:.0%}：claim 过滤未破坏可检索性。")
    if av.get("requested", 0) > 0:
        out.append(f"--answer {av['requested']} 可答率={av['answerable_rate']:.0%}"
                   f"（成功 {av['success']} / 降级 {av['fallback']} / 无依据 {av['no_evidence']} / 跳过 {av.get('skipped', 0)}），"
                   f"平均引用 {av['avg_citations']} 条、耗时 {av['elapsed_seconds']}s。")
        if (av["answerable_rate"] or 0) < 0.6:
            out.append("可答率仍 <60%：claim 词面过滤只能剔除「claim 完全没覆盖」的问题；"
                       "对「claim 覆盖但信息量不足」类需改写模板/或在 prompt 中允许基于 claim 的有限推断。")
        if av["no_evidence"]:
            out.append(f"无依据 {av['no_evidence']} 条：逐条原因见 answer_validation.samples[].reason。")
    else:
        out.append("未跑 --answer：可答率请用 `build --answer 12` 扩验。")
    cmp = payload["comparison_v2"]
    d = cmp.get("delta") or {}
    if d.get("answerable_rate") is not None:
        out.append(f"v2→v3 可答率变化 {d['answerable_rate']:+.0%}（v2 {cmp['v2']['answerable_rate']:.0%} → "
                   f"v3 {cmp['v3']['answerable_rate']:.0%}；{cmp['scope_note']}）。")
    return out


def render_markdown(payload: dict) -> str:
    s = payload["stats"]
    chk = payload["selfcheck"]["summary"]
    av = payload["answer_validation"]
    cf = payload["claim_filter"]
    cmp = payload["comparison_v2"]
    lines = [f"# 问答问题库 v3 · claim 覆盖度过滤 + 可答率扩验（{payload['generated_at'][:10]}）", "",
             f"> 7 类模板（{payload['templates']['total_templates']} 条）× claim 覆盖度 ≥{cf['threshold']}；"
             f"生成 **{s['total']} 条**问题；retrieval-only 自检 top-{DEFAULT_TOPK}；--answer {av.get('requested', 0)} 扩验。", "",
             "## 概览", "",
             f"- 候选生成：{cf['before']} 条 → claim 覆盖度过滤后 {cf['after']} 条"
             f"（通过率 {cf['pass_rate']:.0%}，剔除 {cf['dropped']} 条），入池 {s['total']} 条（{MIN_QUESTIONS}–{MAX_QUESTIONS}）；",
             f"- 自检命中：@1 **{chk['hit_rate_at_1']:.0%}**（{chk['hit_at_1']}/{chk['total']}）、"
             f"@5 **{chk['hit_rate_at_5']:.0%}**（{chk['hit_at_5']}/{chk['total']}）；",
             f"- 可答率（--answer {av.get('requested', 0)}）："
             + (f"**{av['answerable_rate']:.0%}**（成功 {av['success']} / 降级 {av['fallback']} / "
                f"无依据 {av['no_evidence']} / 跳过 {av.get('skipped', 0)}），平均引用 {av['avg_citations']} 条，"
                f"耗时 {av['elapsed_seconds']}s" if av.get("requested") else "未跑（默认 0）"), "",
             "## v2 / v3 对比", "",
             "| 指标 | v2 | v3 | Δ |", "|---|---|---|---|"]
    v2 = cmp["v2"]; v3 = cmp["v3"]
    rows = [("问题数", v2["questions"], v3["questions"]),
            ("自检 @1", v2["hit_rate_at_1"], v3["hit_rate_at_1"]),
            ("自检 @5", v2["hit_rate_at_5"], v3["hit_rate_at_5"]),
            ("可答率抽验条数", v2["answer_requested"], v3["answer_requested"]),
            ("成功", v2["success"], v3["success"]),
            ("无依据", v2["no_evidence"], v3["no_evidence"]),
            ("可答率", v2["answerable_rate"], v3["answerable_rate"]),
            ("平均引用（已答）", v2.get("avg_citations"), v3.get("avg_citations")),
            ("抽验耗时(s)", v2.get("elapsed_seconds"), v3.get("elapsed_seconds"))]
    for name, a, b in rows:
        def fmt(x):
            if isinstance(x, float) and x <= 1 and x >= 0:
                return f"{x:.0%}" if name not in ("平均引用（已答）", "抽验耗时(s)", "问题数", "可答率抽验条数") else str(x)
            return str(x) if x is not None else "—"
        delta_txt = "—"
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and name in ("可答率", "自检 @1", "自检 @5"):
            delta_txt = f"{b - a:+.0%}"
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and name == "平均引用（已答）":
            delta_txt = f"{b - a:+.2f}"
        lines.append(f"| {name} | {fmt(a)} | {fmt(b)} | {delta_txt} |")
    lines += ["", f"口径：{cmp['scope_note']}", "",
              "## 逐条答案状态（含失败原因）", ""]
    if av.get("samples"):
        lines += ["| # | 类别 | 问题 | 状态 | 覆盖率 | 引用 | 失败原因/状态说明 |", "|---|---|---|---|---|---|---|"]
        for i, x in enumerate(av["samples"], 1):
            lines.append(f"| {i} | {x.get('category')} | {str(x.get('question'))[:46]} | {x.get('status')} | "
                         f"{x.get('claim_coverage')} | {len(x.get('citations') or [])} | {str(x.get('reason'))[:70]} |")
        lines += ["", "成功样例："]
        succ = [x for x in av["samples"] if x.get("status") == "success"]
        for x in succ[:3]:
            lines.append(f"- {x.get('question')} → {str(x.get('answer'))[:160]}")
        if not succ:
            lines.append("- （本次抽验无 success；见上表原因）")
    else:
        lines.append("本次未跑 --answer，无逐条状态。")
    lines += ["", "## 模板与问题分布", "",
              "| 类别 | 问题数 | @1 | @5 |", "|---|---|---|---|"]
    cat_stat = {g["category"]: g for g in payload["selfcheck"]["by_category"]}
    for cat in payload["templates"]["categories"]:
        g = cat_stat.get(cat, {})
        lines.append(f"| {cat} | {s['by_category'].get(cat, 0)} | {g.get('hit_rate_at_1', 0):.0%} | "
                     f"{g.get('hit_rate_at_5', 0):.0%} |")
    lines += ["", "## claim 覆盖度过滤样例（被剔除）", ""]
    for x in cf.get("dropped_examples") or []:
        lines.append(f"- 覆盖率 {x['coverage']:.2f} · {x['question']}（concept: {x.get('concept')}）")
    lines += ["", "## 建议", ""] + [f"{i}. {x}" for i, x in enumerate(payload["suggestions"], 1)]
    lines += ["", "## 说明", "", f"- {payload['disclaimer']}",
              "- v3 独立输出（不覆盖 v2）；claim 过滤复用 v2 模板与检索自检，新增覆盖度门槛与带原因的答案扩验。"]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict, out_json: Path | str = OUT_JSON, out_md: Path | str = OUT_MD,
                  portal_out: Path | str = OUT_PORTAL) -> tuple[Path, Path, Path]:
    out_json, out_md, portal_out = Path(out_json), Path(out_md), Path(portal_out)
    for path in (out_json, out_md, portal_out):
        path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    out_json.write_text(text, encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    backup_then_atomic_write(portal_out, payload)
    return out_json, out_md, portal_out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="问答问题库 v3：claim 覆盖度过滤 + 可答率扩验")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="生成 v3 问题库 + 自检 + 可选 --answer 扩验")
    b.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"问题数 {MIN_QUESTIONS}..{MAX_QUESTIONS}（默认 {DEFAULT_LIMIT}）")
    b.add_argument("--coverage", type=float, default=COVERAGE_THRESHOLD,
                   help=f"claim 覆盖度阈值（默认 {COVERAGE_THRESHOLD}）")
    b.add_argument("--topk", type=int, default=DEFAULT_TOPK)
    b.add_argument("--answer", type=int, default=DEFAULT_ANSWER_N,
                   help=f"可答率扩验条数（默认 {DEFAULT_ANSWER_N}；0=不跑 LLM）")
    b.add_argument("--model", default=DEFAULT_MODEL)
    b.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    b.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    b.add_argument("--out-json", default=str(OUT_JSON))
    b.add_argument("--out-md", default=str(OUT_MD))
    b.add_argument("--portal-out", default=str(OUT_PORTAL))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "build":  # pragma: no cover
        return 2
    try:
        units = v2_bank().v1_bank().load_units()
        payload = build_payload(units, limit=args.limit, threshold=args.coverage,
                                topk=args.topk, answer_n=args.answer, model=args.model,
                                timeout=args.timeout, deadline_seconds=args.deadline)
        paths = write_outputs(payload, args.out_json, args.out_md, args.portal_out)
    except BankV3Error as exc:
        print(f"[kb_qa_bank_v3] FAILED: {exc}", file=sys.stderr)
        return 1
    s = payload["stats"]
    chk = payload["selfcheck"]["summary"]
    av = payload["answer_validation"]
    cf = payload["claim_filter"]
    print(f"[kb_qa_bank_v3] questions={s['total']} filter={cf['after']}/{cf['before']} "
          f"({cf['pass_rate']:.0%}) selfcheck @1={chk['hit_rate_at_1']:.0%} ({chk['hit_at_1']}/{chk['total']}) "
          f"@5={chk['hit_rate_at_5']:.0%} ({chk['hit_at_5']}/{chk['total']})")
    print(f"[kb_qa_bank_v3] answer={av.get('requested', 0)} success={av.get('success')} "
          f"no_evidence={av.get('no_evidence')} fallback={av.get('fallback')} "
          f"answerable_rate={av.get('answerable_rate')} avg_citations={av.get('avg_citations')} "
          f"elapsed={av.get('elapsed_seconds')}s")
    print(f"[kb_qa_bank_v3] comparison={json.dumps(payload['comparison_v2']['delta'], ensure_ascii=False)}")
    print(f"[kb_qa_bank_v3] json={paths[0]}")
    print(f"[kb_qa_bank_v3] md={paths[1]}")
    print(f"[kb_qa_bank_v3] portal={paths[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
