# -*- coding: utf-8 -*-
"""问答问题模板 v2：7 类模板生成 40–60 条候选问题 + retrieval-only 自检 + 可答率验证。

背景：wave13 扩量显示 12 条中 6 条「无依据」（LLM 按规则拒答），根因是 v1 模板（「证伪条件/数据多少」）
超出单条知识单元信息量。本模块在 v1（`kb_qa_bank.py`，不改动）基础上扩展模板并加入两级验证：

  ① 模板 v2：7 类 × 4–5 条（共 31 条模板）
     是什么 / 影响如何 / 竞争格局 / 关键数据 / 风险限制 / 如何识别 / 驱动因素；
     问题以「研报提供了哪些可验证数据/指标」「识别规则/形态特征」等更贴 claim 的措辞生成；
  ② 从 498 知识单元按 concept 生成候选（主题分类复用 v1；每 (主题,概念) ≤2 条；问题文本归一化去重），
     按「主题轮转 + 类别均衡」选取 40–60 条（默认 50）；
  ③ retrieval-only 自检（不调 LLM）：逐条 kb_qa.search top-5，统计来源命中 @1/@5 与按类别/主题分布；
  ④ `--answer N`（默认 0，建议 5–8）：对前 N 条调 kb_qa.ask 生成答案，统计成功/降级/无依据 → 可答率；
  ⑤ 输出 `research/reports/kb_qa_questions_v2_20261101.{json,md}` + `portal/static/kb_qa_questions_v2.json`
     （**不覆盖 v1 问题库**）。

CLI：
  python tools/kb_corpus/kb_qa_bank_v2.py build                 # 生成 + 自检（不调 LLM）
  python tools/kb_corpus/kb_qa_bank_v2.py build --answer 6      # 额外验证前 6 条可答率
  python tools/kb_corpus/kb_qa_bank_v2.py build --limit 40
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from safe_json import SafeJsonError, backup_then_atomic_write

REPO = Path(__file__).resolve().parents[2]
OUT_JSON = REPO / "research" / "reports" / "kb_qa_questions_v2_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_questions_v2_20261101.md"
OUT_PORTAL = REPO / "portal" / "static" / "kb_qa_questions_v2.json"
V1_BANK = REPO / "tools" / "kb_corpus" / "kb_qa_bank.py"
DEFAULT_LIMIT = 50
MIN_QUESTIONS = 40
MAX_QUESTIONS = 60
DEFAULT_TOPK = 5
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TIMEOUT = 300

# 6–8 类模板（本版 7 类，共 31 条）
TEMPLATES: dict[str, list[str]] = {
    "是什么": [
        "{concept}的核心结论与主要依据是什么？",
        "{concept}具体指什么，研报如何定义或描述？",
        "{concept}的关键逻辑链条是什么？",
        "{concept}在研报中的核心观点是什么？",
        "{concept}的结论由哪些证据支撑？",
    ],
    "影响如何": [
        "{concept}对产业链或行业格局的影响如何？",
        "{concept}的传导路径与受益环节是什么？",
        "{concept}对相关标的基本面或估值的影响如何？",
        "{concept}变化会带来哪些连锁影响？",
        "{concept}的影响范围与持续性如何？",
    ],
    "竞争格局": [
        "{concept}的竞争格局与主要参与者有哪些？",
        "{concept}的市场份额或集中度如何？",
        "{concept}的进入壁垒与差异化优势是什么？",
        "{concept}相关的国产化或替代进展如何？",
    ],
    "关键数据": [
        "{concept}中研报提供了哪些可验证的关键数据或指标？",
        "{concept}涉及哪些量化指标（规模/增速/份额/价格）？",
        "{concept}有哪些可核验的统计口径或样本？",
        "{concept}的关键参数或阈值有哪些？",
        "{concept}的证据链由哪些数据构成？",
    ],
    "风险限制": [
        "{concept}的主要风险与不确定性有哪些？",
        "{concept}的结论在什么条件下可能不成立？",
        "{concept}的限制条件与适用边界是什么？",
        "{concept}的风险提示是否充分？",
    ],
    "如何识别": [
        "如何识别{concept}相关的机会或信号？",
        "{concept}的识别规则或形态特征是什么？",
        "{concept}需要跟踪哪些指标或事件？",
        "{concept}的验证方式与观测窗口是什么？",
    ],
    "驱动因素": [
        "{concept}的核心驱动因素是什么？",
        "{concept}的催化因素与时间节点有哪些？",
        "{concept}的景气度受哪些变量影响？",
        "{concept}的产业趋势与演进方向如何？",
    ],
}
CATEGORIES = list(TEMPLATES)


class BankV2Error(RuntimeError):
    """问题库 v2 生成异常。"""


def _load_module(name: str, filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise BankV2Error(f"无法加载 {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_v1 = None
_kb_qa = None


def v1_bank():
    global _v1
    if _v1 is None:
        _v1 = _load_module("kb_qa_bank_v1_for_v2", "kb_qa_bank.py")
    return _v1


def kb_qa():
    global _kb_qa
    if _kb_qa is None:
        _kb_qa = _load_module("kb_qa_for_bank_v2", "kb_qa.py")
    return _kb_qa


def _uid_int(text: str) -> int:
    return int(v1_bank()._uid_hash(text, 8), 16)


def norm_question(text: str) -> str:
    return v1_bank().normalize_question(text)


# ---------------------------------------------------------------------------
# ① 候选生成（7 类模板 × 概念，类别均衡）
# ---------------------------------------------------------------------------
def build_candidates(units: list[dict]) -> list[dict]:
    """从知识单元生成候选问题：模板类别确定性轮转，问题文本去重，同(主题,概念)≤2 条。"""
    buckets: dict[str, list[dict]] = {}
    for unit in units:
        theme = v1_bank().classify_theme(unit)
        buckets.setdefault(theme, []).append(unit)
    candidates: list[dict] = []
    seen: set[str] = set()
    for theme, pool in buckets.items():
        concept_counts: dict[str, int] = {}
        for unit in sorted(pool, key=v1_bank()._richness, reverse=True):
            concept = str(unit.get("concept") or "").strip()
            if not concept:
                continue
            ckey = norm_question(concept)
            if concept_counts.get(ckey, 0) >= 2:
                continue
            attempt = concept_counts.get(ckey, 0)
            cat = CATEGORIES[(_uid_int(ckey) + attempt) % len(CATEGORIES)]
            options = TEMPLATES[cat]
            template = options[(_uid_int(ckey + cat + str(attempt)) + attempt) % len(options)]
            question = template.format(concept=concept)
            nkey = norm_question(question)
            if not nkey or nkey in seen:
                continue
            seen.add(nkey)
            concept_counts[ckey] = attempt + 1
            candidates.append({
                "id": "q2_" + v1_bank()._uid_hash(nkey),
                "question": question, "category": cat, "theme": theme,
                "source_unit_id": unit.get("id"), "book": unit.get("book") or "",
                "chapter": unit.get("chapter") or "",
                "page_start": unit.get("page_start"), "page_end": unit.get("page_end"),
                "concept": concept,
                "claim_snippet": v1_bank()._clip(unit.get("claim"), v1_bank().CLAIM_SNIPPET),
                "source": unit.get("source") or "",
            })
    return candidates


def select_balanced(candidates: list[dict], limit: int = DEFAULT_LIMIT) -> list[dict]:
    """主题轮转 + 类别均衡选取：每轮每主题至多 1 条，优先补齐使用最少的模板类别。"""
    limit = max(MIN_QUESTIONS, min(MAX_QUESTIONS, int(limit)))
    themes: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for c in candidates:
        theme = c.get("theme") or "其他/综合"
        if theme not in grouped:
            grouped[theme] = []
            themes.append(theme)
        grouped[theme].append(c)
    cat_count = {cat: 0 for cat in CATEGORIES}
    used: set[str] = set()
    selected: list[dict] = []
    idx = 0
    while len(selected) < limit:
        moved = False
        for theme in themes:
            pool = [c for c in grouped.get(theme, []) if c["id"] not in used]
            if not pool:
                continue
            pool.sort(key=lambda c: (cat_count.get(c["category"], 0),
                                     _uid_int(c["id"])))
            pick = pool[0]
            selected.append(pick)
            used.add(pick["id"])
            cat_count[pick["category"]] = cat_count.get(pick["category"], 0) + 1
            moved = True
            if len(selected) >= limit:
                break
        if not moved:
            break
        idx += 1
    return selected[:limit]


def generate_bank(units: list[dict], limit: int = DEFAULT_LIMIT) -> tuple[list[dict], list[dict]]:
    candidates = build_candidates(units)
    if not candidates:
        raise BankV2Error("未生成任何候选题（知识单元为空或无 concept）")
    selected = select_balanced(candidates, limit)
    if len(selected) < MIN_QUESTIONS:
        raise BankV2Error(f"候选题不足：仅 {len(selected)} 条（要求 ≥{MIN_QUESTIONS}）")
    for i, q in enumerate(selected, 1):
        q["rank"] = i
    return selected, candidates


# ---------------------------------------------------------------------------
# ② retrieval-only 自检
# ---------------------------------------------------------------------------
def _cached_search():
    qa = kb_qa()
    try:
        api = qa.kb_api()
        if not getattr(api.load_units, "_cached_for_bank_v2", False):
            api.load_units = lru_cache(maxsize=1)(api.load_units)
            api.load_units._cached_for_bank_v2 = True
        if not getattr(api.load_corpus, "_cached_for_bank_v2", False):
            api.load_corpus = lru_cache(maxsize=1)(api.load_corpus)
            api.load_corpus._cached_for_bank_v2 = True
    except Exception:  # noqa: BLE001
        pass

    def _search(question: str, topk: int):
        return qa.search(question, topk=topk)

    return _search


def selfcheck_questions(questions: list[dict], topk: int = DEFAULT_TOPK, search_fn=None) -> dict:
    """逐条 retrieval-only 检索，统计来源命中 @1/@5（按类别/主题分组）。"""
    fn = search_fn or _cached_search()
    rows = []
    for q in questions:
        try:
            hits = fn(q["question"], topk=topk) or []
        except Exception:  # noqa: BLE001
            hits = []
        rank = None
        for i, h in enumerate(hits[:topk], 1):
            if h.get("unit_id") == q.get("source_unit_id"):
                rank = i
                break
        rows.append({"id": q["id"], "question": q["question"], "category": q["category"],
                     "theme": q["theme"], "source_unit_id": q.get("source_unit_id"),
                     "retrieval_hits": len(hits), "source_rank": rank,
                     "hit_at_1": rank == 1, "hit_at_5": rank is not None,
                     "top1_unit": hits[0].get("unit_id") if hits else None})
    return {"summary": _summarize(rows), "by_category": _group_stats(rows, "category"),
            "by_theme": _group_stats(rows, "theme"), "questions": rows}


def _summarize(rows: list[dict]) -> dict:
    total = len(rows)
    hit1 = sum(1 for r in rows if r.get("hit_at_1"))
    hit5 = sum(1 for r in rows if r.get("hit_at_5"))
    return {"total": total, "hit_at_1": hit1, "hit_at_5": hit5,
            "hit_rate_at_1": round(hit1 / total, 4) if total else 0.0,
            "hit_rate_at_5": round(hit5 / total, 4) if total else 0.0,
            "miss_source": total - hit5,
            "retrieval_no_hits": sum(1 for r in rows if r.get("retrieval_hits") == 0)}


def _group_stats(rows: list[dict], key: str) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get(key) or "未知", []).append(r)
    out = []
    for name, group in groups.items():
        summary = _summarize(group)
        out.append({key: name, **summary})
    out.sort(key=lambda g: (-g["hit_rate_at_5"], g[key]))
    return out


# ---------------------------------------------------------------------------
# ③ 答案可答率验证（--answer N）
# ---------------------------------------------------------------------------
def validate_answers(questions: list[dict], n: int = 0, ask_fn=None,
                     model: str = DEFAULT_MODEL, timeout: float = DEFAULT_TIMEOUT,
                     topk: int = 8, deadline_seconds: float = 600,
                     clock=time.monotonic) -> dict:
    """对前 N 条调用 kb_qa.ask，统计成功/降级/无依据 → 可答率（默认 0=不跑）。"""
    n = max(0, min(int(n), len(questions)))
    if n == 0:
        return {"requested": 0, "success": 0, "fallback": 0, "no_evidence": 0,
                "answerable_rate": None, "samples": [],
                "note": "默认不跑 LLM（--answer N 才验证可答率）"}
    qa = kb_qa()
    fn = ask_fn or qa.ask
    started = clock()
    samples = []
    success = fallback = no_evidence = 0
    for q in questions[:n]:
        remaining = deadline_seconds - (clock() - started)
        if remaining <= 1:
            samples.append({"id": q["id"], "category": q["category"], "question": q["question"],
                            "status": "skipped_deadline", "answer": None})
            continue
        try:
            result = fn(q["question"], topk=topk, model=model,
                        timeout=max(5.0, min(float(timeout), remaining)))
        except Exception as exc:  # noqa: BLE001
            result = {"answer": None, "citations": [], "confidence": {"level": "none"},
                      "fallback": True, "error": f"{type(exc).__name__}: {exc}"}
        answer = result.get("answer") or ""
        is_fallback = bool(result.get("fallback"))
        is_no_evidence = (result.get("confidence") or {}).get("level") == "none" or "没有足够依据" in answer
        if is_fallback:
            fallback += 1
            status = "fallback"
        elif is_no_evidence:
            no_evidence += 1
            status = "no_evidence"
        else:
            success += 1
            status = "success"
        samples.append({"id": q["id"], "category": q["category"], "theme": q["theme"],
                        "question": q["question"], "status": status,
                        "answer": answer[:400] if answer else None,
                        "citations": result.get("citations") or [],
                        "confidence": result.get("confidence") or {},
                        "fallback": is_fallback,
                        "no_evidence": is_no_evidence})
    return {"requested": n, "success": success, "fallback": fallback,
            "no_evidence": no_evidence,
            "answerable_rate": round(success / n, 4) if n else None,
            "elapsed_seconds": round(clock() - started, 2),
            "note": "可答率 = success / requested（success=有答案+引用+非降级+非拒答）",
            "samples": samples}


# ---------------------------------------------------------------------------
# ④ 输出
# ---------------------------------------------------------------------------
def build_payload(units: list[dict], limit: int = DEFAULT_LIMIT, topk: int = DEFAULT_TOPK,
                  answer_n: int = 0, ask_fn=None, search_fn=None, model: str = DEFAULT_MODEL,
                  timeout: float = DEFAULT_TIMEOUT) -> dict:
    started = time.monotonic()
    questions, candidates = generate_bank(units, limit)
    check = selfcheck_questions(questions, topk=topk, search_fn=search_fn)
    answer_validation = validate_answers(questions, answer_n, ask_fn=ask_fn, model=model,
                                         timeout=timeout)
    cat_counts = {cat: 0 for cat in CATEGORIES}
    theme_counts: dict[str, int] = {}
    for q in questions:
        cat_counts[q["category"]] = cat_counts.get(q["category"], 0) + 1
        theme_counts[q["theme"]] = theme_counts.get(q["theme"], 0) + 1
    grouped = [{"category": cat, "count": cat_counts[cat],
                "questions": [q for q in questions if q["category"] == cat]}
               for cat in CATEGORIES if cat_counts.get(cat)]
    suggestions = _suggestions(check, answer_validation, cat_counts)
    return {
        "schema_version": 2, "kind": "kb_qa_question_bank_v2",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"units_total": len(units),
                   "units_research": sum(1 for u in units if u.get("source") == "研报"),
                   "units_books": sum(1 for u in units if u.get("source") == "书籍")},
        "templates": {"categories": CATEGORIES,
                      "templates_per_category": {c: len(t) for c, t in TEMPLATES.items()},
                      "total_templates": sum(len(t) for t in TEMPLATES.values())},
        "stats": {"total": len(questions), "categories": len([c for c in cat_counts if cat_counts[c]]),
                  "by_category": cat_counts, "by_theme": theme_counts,
                  "candidates_generated": len(candidates),
                  "deduped_removed": len(candidates) - len(questions)},
        "selfcheck": check,
        "answer_validation": answer_validation,
        "themes": [{"theme": t, "count": n, "questions": [q for q in questions if q["theme"] == t]}
                   for t, n in theme_counts.items()],
        "category_groups": grouped,
        "questions": questions,
        "suggestions": suggestions,
        "disclaimer": "问题由知识单元模板生成，仅用于检索/问答评测与研究；引用以知识单元页码为准，不构成投资建议。",
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def _suggestions(check: dict, answer_validation: dict, cat_counts: dict) -> list[str]:
    s = check["summary"]
    out = []
    if s["hit_rate_at_5"] < 0.9:
        out.append(f"自检 @5={s['hit_rate_at_5']:.0%} 低于 90%：检查低命中类别/主题的模板措辞，或补充知识单元。")
    else:
        out.append(f"自检 @5={s['hit_rate_at_5']:.0%}（{s['hit_at_5']}/{s['total']}）：模板可检索性好。")
    if s["hit_rate_at_1"] < s["hit_rate_at_5"]:
        out.append(f"@1={s['hit_at_1']} < @5={s['hit_at_5']}：部分来源被同类单元挤到 2–5 位，可评估概念去重/来源加权。")
    if answer_validation.get("requested", 0) > 0:
        rate = answer_validation.get("answerable_rate") or 0
        out.append(f"--answer {answer_validation['requested']} 可答率={rate:.0%}"
                   f"（成功 {answer_validation['success']} / 降级 {answer_validation['fallback']} / 无依据 {answer_validation['no_evidence']}）。")
        if rate < 0.6:
            out.append("可答率偏低：优先改写「关键数据/风险限制」类模板为更贴 claim 的措辞，或从问题库移除超覆盖问题。")
    else:
        out.append("本次未跑 --answer（默认 0）：可答率请用 `build --answer 6` 抽验 5–8 条。")
    weak_cats = [c for c, n in cat_counts.items() if n < 3]
    if weak_cats:
        out.append("类别覆盖偏少：" + "、".join(weak_cats) + "（每题类别均衡后可再扩样）。")
    return out


def render_markdown(payload: dict) -> str:
    s = payload["stats"]
    chk = payload["selfcheck"]["summary"]
    av = payload["answer_validation"]
    lines = [f"# 问答问题模板 v2（{payload['generated_at'][:10]}）", "",
             f"> 7 类模板（{payload['templates']['total_templates']} 条）从 {payload['source']['units_total']} 知识单元生成 "
             f"**{s['total']} 条候选问题**；retrieval-only 自检 top-{DEFAULT_TOPK}，不调 LLM。", "",
             "## 概览", "",
             f"- 问题数：**{s['total']}**（{MIN_QUESTIONS}–{MAX_QUESTIONS} 区间）；类别 {s['categories']} 类；"
             f"模板候选 {s['candidates_generated']} 条 → 去重/均衡后入池 {s['total']}",
             f"- 自检命中：@1 **{chk['hit_rate_at_1']:.0%}**（{chk['hit_at_1']}/{chk['total']}）、"
             f"@5 **{chk['hit_rate_at_5']:.0%}**（{chk['hit_at_5']}/{chk['total']}）；"
             f"检索无结果 {chk['retrieval_no_hits']} 条"]
    if av.get("requested", 0) > 0:
        lines.append(f"- 可答率（--answer {av['requested']}）：**{av['answerable_rate']:.0%}**"
                     f"（成功 {av['success']} / 降级 {av['fallback']} / 无依据 {av['no_evidence']}）")
    else:
        lines.append("- 可答率：本次未跑 --answer（默认 0）；建议 `build --answer 6` 抽验。")
    lines += ["", "## 模板类别与问题分布", "",
              "| 类别 | 模板数 | 问题数 | @1 | @5 |", "|---|---|---|---|---|"]
    cat_stat = {g["category"]: g for g in payload["selfcheck"]["by_category"]}
    for cat in payload["templates"]["categories"]:
        g = cat_stat.get(cat, {})
        lines.append(f"| {cat} | {payload['templates']['templates_per_category'][cat]} | "
                     f"{s['by_category'].get(cat, 0)} | {g.get('hit_rate_at_1', 0):.0%} | "
                     f"{g.get('hit_rate_at_5', 0):.0%} |")
    lines += ["", "## 主题覆盖与自检", "",
              "| 主题 | 问题数 | @1 | @5 |", "|---|---|---|---|"]
    theme_stat = {g["theme"]: g for g in payload["selfcheck"]["by_theme"]}
    for theme, n in s["by_theme"].items():
        g = theme_stat.get(theme, {})
        lines.append(f"| {theme} | {n} | {g.get('hit_rate_at_1', 0):.0%} | {g.get('hit_rate_at_5', 0):.0%} |")
    if av.get("requested", 0) > 0:
        lines += ["", "## 可答率抽验（--answer N）", "",
                  "| # | 类别 | 问题 | 状态 | 置信 | 引用数 |", "|---|---|---|---|---|---|"]
        for i, x in enumerate(av.get("samples") or [], 1):
            lines.append(f"| {i} | {x.get('category')} | {str(x.get('question'))[:52]} | {x.get('status')} | "
                         f"{(x.get('confidence') or {}).get('level', '—')} | {len(x.get('citations') or [])} |")
        lines += ["", "示例答案："]
        for x in (av.get("samples") or [])[:3]:
            if x.get("answer"):
                lines.append(f"- [{x.get('status')}] {x.get('question')} → {str(x['answer'])[:180]}")
    lines += ["", "## 候选问题样例（每类 1 条）", ""]
    seen = set()
    for q in payload["questions"]:
        if q["category"] in seen:
            continue
        seen.add(q["category"])
        lines.append(f"- **{q['category']}** [{q['theme']}] {q['question']}（来源 `{q['source_unit_id']}` p.{q['page_start']}）")
    lines += ["", "## 改进建议", ""] + [f"{i}. {x}" for i, x in enumerate(payload["suggestions"], 1)]
    lines += ["", "## 说明", "", f"- {payload['disclaimer']}",
              "- v2 问题库独立输出（`kb_qa_questions_v2_20261101.json` + `portal/static/kb_qa_questions_v2.json`），"
              "**不覆盖 v1**；自检为检索-only，可答率为 --answer 抽验。"]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict, out_json: Path | str = OUT_JSON, out_md: Path | str = OUT_MD,
                  portal_out: Path | str = OUT_PORTAL) -> tuple[Path, Path, Path]:
    out_json, out_md, portal_out = Path(out_json), Path(out_md), Path(portal_out)
    for path in (out_json, out_md, portal_out):
        path.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    backup_then_atomic_write(portal_out, payload)
    return out_json, out_md, portal_out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="问答问题模板 v2：7 类模板 + 自检 + 可答率验证")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="生成 v2 问题库 + retrieval-only 自检（可选 --answer 抽验）")
    b.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"问题数 {MIN_QUESTIONS}..{MAX_QUESTIONS}（默认 {DEFAULT_LIMIT}）")
    b.add_argument("--topk", type=int, default=DEFAULT_TOPK, help="自检检索 top-k")
    b.add_argument("--answer", type=int, default=0, help="对前 N 条调 kb_qa 验证可答率（默认 0，建议 5–8）")
    b.add_argument("--model", default=DEFAULT_MODEL)
    b.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    b.add_argument("--out-json", default=str(OUT_JSON))
    b.add_argument("--out-md", default=str(OUT_MD))
    b.add_argument("--portal-out", default=str(OUT_PORTAL))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "build":  # pragma: no cover
        return 2
    try:
        units = v1_bank().load_units()
        payload = build_payload(units, limit=args.limit, topk=args.topk,
                                answer_n=args.answer, model=args.model, timeout=args.timeout)
        paths = write_outputs(payload, args.out_json, args.out_md, args.portal_out)
    except BankV2Error as exc:
        print(f"[kb_qa_bank_v2] FAILED: {exc}", file=sys.stderr)
        return 1
    s = payload["stats"]
    chk = payload["selfcheck"]["summary"]
    av = payload["answer_validation"]
    print(f"[kb_qa_bank_v2] questions={s['total']} categories={s['categories']} "
          f"selfcheck @1={chk['hit_rate_at_1']:.0%} ({chk['hit_at_1']}/{chk['total']}) "
          f"@5={chk['hit_rate_at_5']:.0%} ({chk['hit_at_5']}/{chk['total']}) "
          f"answer={av.get('requested', 0)} answerable_rate={av.get('answerable_rate')}")
    print(f"[kb_qa_bank_v2] by_category={json.dumps(s['by_category'], ensure_ascii=False)}")
    print(f"[kb_qa_bank_v2] json={paths[0]}")
    print(f"[kb_qa_bank_v2] md={paths[1]}")
    print(f"[kb_qa_bank_v2] portal={paths[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
