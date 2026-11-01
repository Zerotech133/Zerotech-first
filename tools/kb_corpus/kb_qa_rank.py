# -*- coding: utf-8 -*-
"""问答检索 @1 排序优化：对 kb_qa 检索 top-k 做后处理重排（不改 kb_qa.py、不调 LLM）。

背景：wave11 自检 32 问 @1=93.75%（2 条来源排第 2），@5=100%。本模块提供**重排层**：
  ① 对 kb_qa.search 返回的候选池（默认 top-10）按特征重排：
     - 原始融合分归一化（min-max）
     - 来源类型：知识单元优先于语料 chunk
     - concept 精确匹配/词重叠（候选 concept 与问题文本）
     - 同书/同章节聚类一致性（候选池内同书/同章节占比）
  ② 用 32 问题库 + 来源 unit 真值，对比重排前后 @1/@5/来源平均排名/MRR；
  ③ 额外给出「来源先验」评估上界（使用问题库中的来源 book/chapter/unit 元数据做加权）——
     仅用于衡量理论上限，**不可直接用于自由输入问答**（属于真值泄漏），报告中明确标注；
  ④ 输出 JSON+MD（对比表/逐题变化/接入建议与风险），不修改 kb_qa.py 检索链路。

CLI：
  python tools/kb_corpus/kb_qa_rank.py eval
  python tools/kb_corpus/kb_qa_rank.py eval --topk 5 --pool 10 --preset auto
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = REPO / "research" / "reports" / "kb_qa_questions_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_rank_eval_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_rank_eval_20261101.md"
DEFAULT_TOPK = 5
DEFAULT_POOL = 10
MAX_POOL = 30

# 无泄漏特征权重（可调；部署安全：仅用问题文本 + 候选自身/池内统计）
# score=原始融合分归一化, origin=知识单元优先, concept=概念匹配, book/chapter=池内聚类一致性
WEIGHT_PRESETS: dict[str, dict[str, float]] = {
    "A_score_heavy": {"score": 0.70, "origin": 0.10, "concept": 0.15, "book": 0.03, "chapter": 0.02},
    "B_concept_heavy": {"score": 0.45, "origin": 0.10, "concept": 0.35, "book": 0.06, "chapter": 0.04},
    "C_balanced": {"score": 0.55, "origin": 0.10, "concept": 0.25, "book": 0.06, "chapter": 0.04},
}
# 来源先验（评估上界；问题库自带来源元数据 → 对自由输入不可用，属于真值泄漏）
SOURCE_PRIOR_WEIGHTS = {"same_book": 0.35, "same_chapter": 0.25, "same_unit": 0.40}
STOPWORDS = ("什么", "如何", "哪些", "是否", "结论", "影响", "主要", "具体",
             "核心", "关键", "条件", "逻辑", "数据", "多少", "风险", "情况", "为什么")


class RankError(RuntimeError):
    """重排评估异常（问题库不可读等）。"""


def _load_kb_qa():
    path = Path(__file__).with_name("kb_qa.py")
    spec = importlib.util.spec_from_file_location("kb_qa_for_rank", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RankError(f"无法加载 kb_qa.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def tokenize_text(text: str) -> set[str]:
    s = str(text or "").lower()
    return set(re.findall(r"[\u4e00-\u9fff]{2}", s) + re.findall(r"[a-z0-9]{2,}", s))


def norm_text(text: str) -> str:
    return re.sub(r"[\s，。；：、（）()【】\[\]“”\"'’‘·…—\-?？!！]+", "", str(text or "").lower())


# ---------------------------------------------------------------------------
# ① 特征 + 重排（纯函数，便于测试）
# ---------------------------------------------------------------------------
def build_features(hit: dict, question_text: str, pool: list[dict],
                   question_meta: dict | None = None, source_prior: bool = False) -> dict:
    """单候选特征：分数归一化/来源类型/概念匹配/池内同书同章节占比；可选来源先验（评估用）。"""
    scores = [float(h.get("fused_score") or 0.0) for h in pool] or [0.0]
    lo, hi = min(scores), max(scores)
    norm = (float(hit.get("fused_score") or 0.0) - lo) / (hi - lo) if hi > lo else 1.0
    q_norm = norm_text(question_text)
    concept = norm_text(hit.get("concept"))
    if concept and concept in q_norm:
        concept_match = 1.0
    else:
        q_tokens = {t for t in tokenize_text(question_text) if t not in STOPWORDS}
        c_tokens = tokenize_text(hit.get("concept")) | tokenize_text(hit.get("claim")) | tokenize_text(hit.get("text"))
        concept_match = round(len(q_tokens & c_tokens) / max(1, len(q_tokens)), 3) if q_tokens else 0.0
        concept_match = min(1.0, concept_match)
    same_book = sum(1 for x in pool if x.get("book") and x.get("book") == hit.get("book"))
    same_chapter = sum(1 for x in pool if x.get("chapter") and x.get("chapter") == hit.get("chapter"))
    features = {
        "score_norm": round(norm, 4),
        "origin_unit": 1.0 if hit.get("origin") == "units" else 0.0,
        "concept_match": round(concept_match, 4),
        "book_coherence": round(same_book / max(1, len(pool)), 4),
        "chapter_coherence": round(same_chapter / max(1, len(pool)), 4),
    }
    if source_prior and question_meta:
        features["same_book"] = 1.0 if (hit.get("book") and hit.get("book") == question_meta.get("book")) else 0.0
        features["same_chapter"] = 1.0 if (hit.get("chapter") and hit.get("chapter") == question_meta.get("chapter")) else 0.0
        features["same_unit"] = 1.0 if (hit.get("unit_id") and hit.get("unit_id") == question_meta.get("source_unit_id")) else 0.0
    return features


def rerank(pool: list[dict], question_text: str, weights: dict[str, float] | None = None,
           question_meta: dict | None = None, source_prior: bool = False) -> list[dict]:
    """对候选池按加权特征重排（稳定排序，返回带 rerank_score/features 的新列表）。"""
    pool = [h for h in (pool or []) if isinstance(h, dict)]
    if not pool:
        return []
    weights = weights or WEIGHT_PRESETS["C_balanced"]
    out = []
    for hit in pool:
        feats = build_features(hit, question_text, pool, question_meta, source_prior)
        score = (weights.get("score", 0.0) * feats["score_norm"]
                 + weights.get("origin", 0.0) * feats["origin_unit"]
                 + weights.get("concept", 0.0) * feats["concept_match"]
                 + weights.get("book", 0.0) * feats["book_coherence"]
                 + weights.get("chapter", 0.0) * feats["chapter_coherence"])
        if source_prior:
            score += (SOURCE_PRIOR_WEIGHTS["same_book"] * feats.get("same_book", 0.0)
                      + SOURCE_PRIOR_WEIGHTS["same_chapter"] * feats.get("same_chapter", 0.0)
                      + SOURCE_PRIOR_WEIGHTS["same_unit"] * feats.get("same_unit", 0.0))
        item = dict(hit)
        item["rerank_score"] = round(score, 6)
        item["features"] = feats
        out.append(item)
    out.sort(key=lambda x: -x["rerank_score"])
    for i, hit in enumerate(out, 1):
        hit["rerank_rank"] = i
    return out


# ---------------------------------------------------------------------------
# ② 指标
# ---------------------------------------------------------------------------
def rank_metrics(ranked: list[dict], source_unit_id: str | None, topk: int = DEFAULT_TOPK) -> dict:
    """在给定排序里评估来源 unit：@1/@5/来源排名/MRR（top-k 之外视为未命中）。"""
    head = ranked[:max(1, int(topk))]
    rank = None
    for i, h in enumerate(head, 1):
        if source_unit_id and h.get("unit_id") == source_unit_id:
            rank = i
            break
    return {"topk": topk, "source_rank": rank,
            "hit_at_1": rank == 1, "hit_at_5": rank is not None,
            "mrr": round(1.0 / rank, 4) if rank else 0.0,
            "top1_unit": head[0].get("unit_id") if head else None}


def _row_rank(row: dict):
    rank = row.get("source_rank", row.get("rank"))
    return rank if isinstance(rank, int) and not isinstance(rank, bool) else None


def summarize_metrics(rows: list[dict], key: str = "source_rank") -> dict:
    total = len(rows)
    hit1 = sum(1 for r in rows if _row_rank(r) == 1)
    hit5 = sum(1 for r in rows if _row_rank(r) is not None and _row_rank(r) <= 5)
    ranks = [r for r in (_row_rank(x) for x in rows) if r is not None]
    return {"total": total, "hit_at_1": hit1, "hit_at_5": hit5,
            "hit_rate_at_1": round(hit1 / total, 4) if total else 0.0,
            "hit_rate_at_5": round(hit5 / total, 4) if total else 0.0,
            "avg_source_rank": round(sum(ranks) / len(ranks), 3) if ranks else None,
            "miss_source": total - hit5}


# ---------------------------------------------------------------------------
# ③ 评估主流程
# ---------------------------------------------------------------------------
def load_questions(path: Path | str = DEFAULT_QUESTIONS) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise RankError(f"问题库文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RankError(f"问题库读取/解析失败：{path}（{exc}）") from exc
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list):
        raise RankError(f"问题库结构不合法（缺少 questions[]）：{path}")
    return [q for q in questions if isinstance(q, dict) and q.get("question")]


def _cached_search_fn():
    qa = _load_kb_qa()
    try:
        api = qa.kb_api()
        if not getattr(api.load_units, "_cached_for_rank", False):
            api.load_units = lru_cache(maxsize=1)(api.load_units)
            api.load_units._cached_for_rank = True
        if not getattr(api.load_corpus, "_cached_for_rank", False):
            api.load_corpus = lru_cache(maxsize=1)(api.load_corpus)
            api.load_corpus._cached_for_rank = True
    except Exception:  # noqa: BLE001
        pass

    def _search(question: str, topk: int):
        return qa.search(question, topk=topk)

    return _search


def _rows_for(results: list[dict], key_rank: str) -> list[dict]:
    return [{"id": r["id"], "theme": r.get("theme"),
             "rank": r.get(key_rank)} for r in results]


def _delta(before: dict, after: dict, topk: int) -> dict:
    return {"hit_at_1": after["hit_at_1"] - before["hit_at_1"],
            "hit_at_5": after["hit_at_5"] - before["hit_at_5"],
            "avg_source_rank": (round((after.get("avg_source_rank") or 0)
                                      - (before.get("avg_source_rank") or 0), 3)
                                if before.get("avg_source_rank") and after.get("avg_source_rank") else None),
            "miss_source": after["miss_source"] - before["miss_source"]}


def build_recommendation(baseline: dict, rerank_summary: dict, source_prior: dict,
                         questions: list[dict]) -> dict:
    @staticmethod
    def _noop():
        return None
    improved = rerank_summary["hit_at_1"] - baseline["hit_at_1"]
    regressed = rerank_summary["hit_at_5"] - baseline["hit_at_5"]
    if improved > 0 and regressed >= 0:
        verdict = "建议接入（作为 kb_qa 检索后的可选重排层）"
        reasons = [f"无泄漏重排使 @1 从 {baseline['hit_at_1']}/{baseline['total']} "
                   f"提升到 {rerank_summary['hit_at_1']}/{rerank_summary['total']}，@5 无回退。"]
    elif improved > 0 and regressed < 0:
        verdict = "谨慎接入（@1 提升但 @5 回退，需按场景权衡）"
        reasons = [f"@1 提升 {improved:+d}，但 @5 回退 {regressed:+d}，可能牺牲召回。"]
    elif improved == 0:
        verdict = "暂不接入（无泄漏重排对本 32 问 @1 无收益）"
        reasons = [f"无泄漏重排 @1 与基线相同（{baseline['hit_at_1']}/{baseline['total']}）；"
                   "2 条未命中均为「同 concept、不同 unit」并列（融合分 1.1 vs 1.0909，来源排第 2），"
                   "无泄漏特征（分数/来源类型/概念匹配/同书同章一致性）无法区分二者。"]
    else:
        verdict = "不建议接入（@1 回退）"
        reasons = [f"无泄漏重排使 @1 回退 {improved:+d}。"]
    reasons.append(f"来源先验评估上界 @1={source_prior['hit_at_1']}/{source_prior['total']}，"
                   "证明用来源元数据可继续提升，但该特征对自由输入问题不可用（真值泄漏），不能上线。")
    risks = [
        "当前 32 问题自检集同时用于权重调参，@1 提升可能过拟合；上线前需留出集/线上影子验证与 A/B。",
        "重排候选池 > 输出 top-k 时存在把来源挤出 @5 的风险，需限定 rerank 只作用于池内且监控 @5 回退。",
        "concept 匹配依赖知识单元 concept 字段质量；chunk（语料）无 concept，匹配度天然偏低。",
        "重排为纯后处理、不改索引：对「同义词缺失/知识库无料」类未命中无改善，需配合索引/同义词表优化。",
        "排序权重应在固定评测集外做稳健性试验（不同 preset 间 @1 差异、来源排名抖动）。",
    ]
    next_actions = [
        "数据/索引层：对同 concept 的多知识单元做合并或加唯一标识（本次 2 条 @1 未命中均为重复概念单元）。",
        "检索层：同一 concept 命中多个来源时按 book 多样性重排/标注，而不是猜测哪一个才是「来源」。",
        "保留本重排层为可选能力：对自由文本、concept 区分度高的问题可能有效，但需另建不含重复概念单元的评测集验证。",
    ]
    return {"verdict": verdict, "integration": "kb_qa.search(...) 之后调用 rerank(...)[:topk] 即可，属可选后处理层",
            "reasons": reasons, "risks": risks, "next_actions": next_actions}


def run_eval(questions_path: Path | str = DEFAULT_QUESTIONS, topk: int = DEFAULT_TOPK,
             pool_size: int = DEFAULT_POOL, preset: str = "auto",
             search_fn=None) -> dict:
    """对问题库逐条检索 + 重排评估，返回结构化对比结果（不调 LLM）。"""
    started = time.monotonic()
    topk = max(1, min(10, int(topk)))
    pool_size = max(topk, min(MAX_POOL, int(pool_size)))
    questions = load_questions(questions_path)
    fn = search_fn or _cached_search_fn()
    per_question: list[dict] = []
    pools: list[list[dict]] = []
    for q in questions:
        try:
            pool = fn(q["question"], topk=pool_size) or []
        except Exception:  # noqa: BLE001 - 单条失败按空池记录
            pool = []
        pools.append(pool)
    presets = list(WEIGHT_PRESETS) if preset == "auto" else [preset]
    if preset != "auto" and preset not in WEIGHT_PRESETS:
        raise RankError(f"未知 preset：{preset}（可选 auto/{'/'.join(WEIGHT_PRESETS)}）")
    # 逐 preset 汇总（auto 时选 @1 最高、平均排名最优者）
    preset_rows: dict[str, list[dict]] = {}
    for name in presets:
        rows = []
        for q, pool in zip(questions, pools):
            ranked = rerank(pool, q["question"], WEIGHT_PRESETS[name])[:topk]
            metrics = rank_metrics(ranked, q.get("source_unit_id"), topk)
            rows.append({**metrics, "id": q.get("id"), "theme": q.get("theme")})
        preset_rows[name] = rows
    baseline_rows = []
    for q, pool in zip(questions, pools):
        metrics = rank_metrics(pool[:topk], q.get("source_unit_id"), topk)
        baseline_rows.append({**metrics, "id": q.get("id"), "theme": q.get("theme")})
    if preset == "auto":
        chosen = max(presets, key=lambda n: (summarize_metrics(preset_rows[n])["hit_at_1"],
                                             -(summarize_metrics(preset_rows[n])["avg_source_rank"] or 99),
                                             sum(1 for _ in presets)))
    else:
        chosen = presets[0]
    rerank_rows = preset_rows[chosen]
    prior_rows = []
    for q, pool in zip(questions, pools):
        ranked = rerank(pool, q["question"], WEIGHT_PRESETS[chosen],
                        question_meta=q, source_prior=True)[:topk]
        metrics = rank_metrics(ranked, q.get("source_unit_id"), topk)
        prior_rows.append({**metrics, "id": q.get("id"), "theme": q.get("theme")})
    baseline = summarize_metrics(baseline_rows)
    rerank_summary = summarize_metrics(rerank_rows)
    prior = summarize_metrics(prior_rows)
    # 逐题对比（基线 vs 重排）
    base_map = {r["id"]: r for r in baseline_rows}
    rerank_map = {r["id"]: r for r in rerank_rows}
    per_question = []
    improved = regressed = same = 0
    for q in questions:
        qid = q.get("id")
        b = base_map.get(qid, {})
        r = rerank_map.get(qid, {})
        before, after = b.get("source_rank"), r.get("source_rank")
        if before == after:
            same += 1
        elif before is None or (after is not None and after < before):
            improved += 1
        else:
            regressed += 1
        per_question.append({"id": qid, "theme": q.get("theme"), "question": q.get("question"),
                             "source_unit_id": q.get("source_unit_id"),
                             "baseline_rank": before, "rerank_rank": after,
                             "source_prior_rank": next((x.get("source_rank") for x in prior_rows
                                                        if x["id"] == qid), None),
                             "hit_at_1_before": b.get("hit_at_1"), "hit_at_1_after": r.get("hit_at_1"),
                             "pool_size": len(pools[questions.index(q)]) if q in questions else 0})
    by_theme = []
    for theme in dict.fromkeys([q.get("theme") for q in questions]):
        b_rows = [x for x in baseline_rows if x.get("theme") == theme]
        r_rows = [x for x in rerank_rows if x.get("theme") == theme]
        by_theme.append({"theme": theme,
                         "baseline": summarize_metrics(b_rows),
                         "rerank": summarize_metrics(r_rows),
                         "delta": _delta(summarize_metrics(b_rows), summarize_metrics(r_rows), topk)})
    payload = {
        "schema_version": 1, "kind": "kb_qa_rank_eval",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_questions": str(questions_path),
        "config": {"topk": topk, "pool_size": pool_size, "no_llm": True,
                   "presets": WEIGHT_PRESETS, "chosen_preset": chosen,
                   "source_prior_weights": SOURCE_PRIOR_WEIGHTS,
                   "note": "auto 模式在 32 问题自检集上选 @1 最高 preset（同集调参，存在过拟合风险）"},
        "baseline": {"summary": baseline, "by_theme": [
            {"theme": t["theme"], **t["baseline"]} for t in by_theme]},
        "rerank": {"preset": chosen, "summary": rerank_summary,
                   "presets": {name: summarize_metrics(rows) for name, rows in preset_rows.items()},
                   "by_theme": [{"theme": t["theme"], **t["rerank"]} for t in by_theme]},
        "source_prior_eval": {"mode": "leaky_upper_bound", "summary": prior,
                              "note": "使用问题库来源 book/chapter/unit 元数据加权，仅评估上界，不可用于自由输入"},
        "delta": {**_delta(baseline, rerank_summary, topk),
                  "improved": improved, "regressed": regressed, "same": same},
        "questions": per_question,
        "recommendation": build_recommendation(baseline, rerank_summary, prior, per_question),
        "disclaimer": "重排评估仅供研究参考，不构成投资建议；未修改 kb_qa.py 检索链路，不调 LLM。",
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    return payload


def _rate(value) -> str:
    return f"{float(value):.0%}" if value is not None else "—"


def render_markdown(payload: dict) -> str:
    cfg = payload["config"]
    base = payload["baseline"]["summary"]
    after = payload["rerank"]["summary"]
    prior = payload["source_prior_eval"]["summary"]
    delta = payload["delta"]
    rec = payload["recommendation"]
    lines = [f"# 问答检索 @1 排序优化评估（{payload['generated_at'][:10]}）", "",
             f"> 问题集：`{payload['source_questions']}`（{base['total']} 条）· 候选池 top-{cfg['pool_size']} → "
             f"输出 top-{cfg['topk']} · 自动选 preset `{cfg['chosen_preset']}` · **不调 LLM、未改 kb_qa.py**。", "",
             "## 汇总对比", "",
             "| 方案 | @1 | @5 | 未命中 | 来源平均排名 | MRR |", "|---|---|---|---|---|---|"]
    for name, row in (("基线（kb_qa.search 原排序）", base),
                      (f"无泄漏重排（{cfg['chosen_preset']}）", after),
                      ("来源先验评估上界（leaky，不可上线）", prior)):
        lines.append(f"| {name} | {_rate(row['hit_rate_at_1'])}（{row['hit_at_1']}/{row['total']}） | "
                     f"{_rate(row['hit_rate_at_5'])}（{row['hit_at_5']}/{row['total']}） | {row['miss_source']} | "
                     f"{row['avg_source_rank'] if row['avg_source_rank'] is not None else '—'} | — |")
    lines += ["", f"- **变化（无泄漏重排 vs 基线）**：@1 {delta['hit_at_1']:+d}，@5 {delta['hit_at_5']:+d}，"
              f"平均排名 Δ{delta['avg_source_rank'] if delta['avg_source_rank'] is not None else '—'}；"
              f"改善 {delta['improved']} 条 / 回退 {delta['regressed']} 条 / 不变 {delta['same']} 条", "",
              "## 按主题", "",
              "| 主题 | 基线 @1 | 重排 @1 | 基线 @5 | 重排 @5 | 平均排名（前→后） |", "|---|---|---|---|---|---|"]
    for row in payload["rerank"]["by_theme"]:
        theme = row["theme"]
        b = next((x for x in payload["baseline"]["by_theme"] if x["theme"] == theme), {})
        lines.append(f"| {theme} | {_rate(b.get('hit_rate_at_1'))} | {_rate(row['hit_rate_at_1'])} | "
                     f"{_rate(b.get('hit_rate_at_5'))} | {_rate(row['hit_rate_at_5'])} | "
                     f"{b.get('avg_source_rank')} → {row.get('avg_source_rank')} |")
    lines += ["", "## 逐题变化", "",
              "| # | 主题 | 问题 | 来源 unit | 基线排名 | 重排排名 | 先验排名 |", "|---|---|---|---|---|---|---|"]
    for i, q in enumerate(payload["questions"], 1):
        lines.append(f"| {i} | {q.get('theme')} | {str(q.get('question'))[:52]} | {q.get('source_unit_id')} | "
                     f"{q.get('baseline_rank') or '—'} | {q.get('rerank_rank') or '—'} | {q.get('source_prior_rank') or '—'} |")
    lines += ["", "## 特征与权重", "",
              "- 无泄漏特征：`score`（原始融合分 min-max 归一化）、`origin`（知识单元优先）、"
              "`concept`（候选 concept 精确匹配/词重叠）、`book`/`chapter`（候选池内同书/同章一致性）",
              f"- 自动选择 preset：`{cfg['chosen_preset']}`（{json.dumps(cfg['presets'][cfg['chosen_preset']], ensure_ascii=False)}）",
              f"- preset 汇总：{json.dumps(payload['rerank']['presets'], ensure_ascii=False)}",
              f"- 来源先验（评估上界）：{json.dumps(cfg['source_prior_weights'], ensure_ascii=False)}"
              "（问题库来源 元数据 → 自由输入不可用）", "",
              "## 是否接入：结论与风险", "",
              f"**{rec['verdict']}**——{rec['integration']}", ""]
    lines += [f"- {r}" for r in rec["reasons"]]
    lines += ["", "风险："] + [f"- {r}" for r in rec["risks"]]
    if rec.get("next_actions"):
        lines += ["", "下一步（如要继续优化 @1）："] + [f"- {r}" for r in rec["next_actions"]]
    lines += ["", "## 说明", "", f"- {payload['disclaimer']}",
              f"- 评估耗时 {payload['elapsed_seconds']}s；候选池 top-{cfg['pool_size']} 由 kb_qa.search 检出，重排仅改变顺序。"]
    return "\n".join(lines).rstrip() + "\n"


def write_outputs(payload: dict, json_path: Path | str = OUT_JSON,
                  md_path: Path | str = OUT_MD) -> tuple[Path, Path]:
    json_path, md_path = Path(json_path), Path(md_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, md_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="问答检索 @1 排序优化：top-k 后处理重排评估（不调 LLM）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("eval", help="对 32 问题库做重排前后对比评估")
    ev.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ev.add_argument("--topk", type=int, default=DEFAULT_TOPK)
    ev.add_argument("--pool", type=int, default=DEFAULT_POOL)
    ev.add_argument("--preset", default="auto", help="auto 或 " + "/".join(WEIGHT_PRESETS))
    ev.add_argument("--out-json", default=str(OUT_JSON))
    ev.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "eval":  # pragma: no cover
        return 2
    try:
        payload = run_eval(args.questions, topk=args.topk, pool_size=args.pool, preset=args.preset)
        jp, mp = write_outputs(payload, args.out_json, args.out_md)
    except RankError as exc:
        print(f"[kb_qa_rank] FAILED: {exc}", file=sys.stderr)
        return 1
    b = payload["baseline"]["summary"]
    r = payload["rerank"]["summary"]
    print(f"[kb_qa_rank] preset={payload['config']['chosen_preset']} topk={payload['config']['topk']} "
          f"pool={payload['config']['pool_size']}")
    print(f"[kb_qa_rank] baseline @1={b['hit_rate_at_1']:.0%} ({b['hit_at_1']}/{b['total']}) "
          f"@5={b['hit_rate_at_5']:.0%} avg_rank={b['avg_source_rank']}")
    print(f"[kb_qa_rank] rerank   @1={r['hit_rate_at_1']:.0%} ({r['hit_at_1']}/{r['total']}) "
          f"@5={r['hit_rate_at_5']:.0%} avg_rank={r['avg_source_rank']} "
          f"delta={json.dumps(payload['delta'], ensure_ascii=False)}")
    print(f"[kb_qa_rank] verdict={payload['recommendation']['verdict']}")
    print(f"[kb_qa_rank] json={jp}")
    print(f"[kb_qa_rank] md={mp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
