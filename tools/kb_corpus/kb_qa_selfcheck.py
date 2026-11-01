# -*- coding: utf-8 -*-
"""问答问题库 retrieval-only 自检：命中率@1/@5、无命中与缺口归因、按主题覆盖统计。

用途：对 `research/reports/kb_qa_questions_20261101.json`（32 条由知识单元模板生成的候选问题）
      逐条做 **retrieval-only** 检索（复用 `kb_qa.search`，不调 LLM、无网络），检验问题能否召回其来源单元：
  ① 记录：来源 unit 是否进 top-k、top1 是否为来源 unit、来源排名、命中率@1/@5、检索无结果数；
  ② 归因（来源未进 top-k 时）：模板失配（措辞含泛词/排序不足）/ 同义词缺（问题与来源词汇不匹配）/ 知识库无料；
  ③ 按主题统计覆盖率与缺口；④ 输出 JSON+MD（逐条表 + 汇总 + 规则式改进建议）。

CLI：
  python tools/kb_corpus/kb_qa_selfcheck.py selfcheck
  python tools/kb_corpus/kb_qa_selfcheck.py selfcheck --topk 5 --limit 10
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_QUESTIONS = REPO / "research" / "reports" / "kb_qa_questions_20261101.json"
OUT_JSON = REPO / "research" / "reports" / "kb_qa_selfcheck_20261101.json"
OUT_MD = REPO / "research" / "reports" / "kb_qa_selfcheck_20261101.md"
DEFAULT_TOPK = 5
MIN_TOPK = 1
MAX_TOPK = 20

CAUSE_TEMPLATE = "模板失配/排序不足"
CAUSE_SYNONYM = "同义词缺/表述不匹配"
CAUSE_NO_MATERIAL = "知识库无料/检索无命中"

TEMPLATE_STOPWORDS = ("什么", "如何", "哪些", "是否", "结论", "影响", "主要", "具体",
                      "核心", "关键", "条件", "逻辑", "数据", "多少", "风险", "情况")


class SelfcheckError(RuntimeError):
    """自检流程异常（问题库不可读等）。"""


def _load_kb_qa():
    path = Path(__file__).with_name("kb_qa.py")
    spec = importlib.util.spec_from_file_location("kb_qa_for_selfcheck", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise SelfcheckError(f"无法加载 kb_qa.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def tokenize_text(text: str) -> set[str]:
    """中文 2-gram + 英文/数字词（用于问题与来源单元的词汇重叠判断，测试纯函数）。"""
    s = str(text or "").lower()
    zh = re.findall(r"[\u4e00-\u9fff]{2}", s)
    en = re.findall(r"[a-z0-9]{2,}", s)
    return set(zh + en)


def overlap_count(question: str, source_text: str) -> int:
    """问题与来源单元文本的词汇重叠数（去掉通用疑问词以降低噪声）。"""
    qt = {t for t in tokenize_text(question) if t not in TEMPLATE_STOPWORDS}
    st = tokenize_text(source_text)
    return len(qt & st)


# ---------------------------------------------------------------------------
# ① 命中判定 + ② 归因
# ---------------------------------------------------------------------------
def classify_miss(question_record: dict, hits: list[dict]) -> str:
    """来源单元未进 top-k 时归因：模板失配 / 同义词缺 / 知识库无料。"""
    if not hits:
        return CAUSE_NO_MATERIAL
    source_text = " ".join(str(question_record.get(k) or "") for k in
                           ("concept", "claim_snippet", "evidence_snippet"))
    overlap = overlap_count(question_record.get("question") or "", source_text)
    if overlap >= 2:
        return CAUSE_TEMPLATE
    return CAUSE_SYNONYM


def evaluate_question(question_record: dict, hits: list[dict], topk: int = DEFAULT_TOPK) -> dict:
    """判定单条问题：来源是否命中/top1 是否来源/来源排名/检索是否有结果/未命中归因。"""
    source_id = question_record.get("source_unit_id")
    hits = hits or []
    top_hits = []
    source_rank = None
    for i, h in enumerate(hits, 1):
        is_source = bool(source_id) and h.get("unit_id") == source_id
        if is_source and source_rank is None:
            source_rank = i
        top_hits.append({"rank": i, "unit_id": h.get("unit_id"),
                         "book": h.get("book"), "page": h.get("page_start"),
                         "origin": h.get("origin"),
                         "fused_score": h.get("fused_score"),
                         "is_source": is_source})
    hit_source = source_rank is not None
    item = {
        "id": question_record.get("id"), "question": question_record.get("question"),
        "type": question_record.get("type"), "theme": question_record.get("theme"),
        "source_unit_id": source_id, "book": question_record.get("book"),
        "concept": question_record.get("concept"),
        "retrieval_hits": len(hits),
        "hit_source": hit_source,
        "top1_is_source": bool(hits) and hits[0].get("unit_id") == source_id,
        "source_rank": source_rank,
        "miss": not hit_source,
        "cause": None if hit_source else classify_miss(question_record, hits),
        "top_hits": top_hits,
    }
    return item


# ---------------------------------------------------------------------------
# ③ 汇总 / 主题统计 / 改进建议
# ---------------------------------------------------------------------------
def summarize(items: list[dict]) -> dict:
    total = len(items)
    hit1 = sum(1 for x in items if x["top1_is_source"])
    hit5 = sum(1 for x in items if x["hit_source"])
    miss = total - hit5
    no_hits = sum(1 for x in items if x["retrieval_hits"] == 0)
    ranks = [x["source_rank"] for x in items if x["source_rank"]]
    causes = Counter(x["cause"] for x in items if x.get("cause"))
    return {
        "total": total,
        "hit_at_1": hit1, "hit_at_5": hit5,
        "miss_source": miss,
        "retrieval_no_hits": no_hits,
        "hit_rate_at_1": round(hit1 / total, 4) if total else 0.0,
        "hit_rate_at_5": round(hit5 / total, 4) if total else 0.0,
        "avg_source_rank": round(sum(ranks) / len(ranks), 2) if ranks else None,
        "causes": dict(causes),
    }


def by_theme(items: list[dict]) -> list[dict]:
    themes: list[str] = []
    for x in items:
        theme = x.get("theme") or "未知"
        if theme not in themes:
            themes.append(theme)
    out = []
    for theme in themes:
        group = [x for x in items if (x.get("theme") or "未知") == theme]
        summary = summarize(group)
        out.append({"theme": theme, "total": summary["total"],
                    "hit_at_1": summary["hit_at_1"], "hit_at_5": summary["hit_at_5"],
                    "miss_source": summary["miss_source"],
                    "hit_rate_at_1": summary["hit_rate_at_1"],
                    "hit_rate_at_5": summary["hit_rate_at_5"],
                    "causes": summary["causes"]})
    out.sort(key=lambda g: (-g["hit_rate_at_5"], -g["total"], g["theme"]))
    return out


def build_suggestions(summary: dict, themes: list[dict], items: list[dict]) -> list[str]:
    """规则式改进建议：补卡 / 补同义词 / 改模板 / 排序增强。"""
    total = summary["total"]
    if not total:
        return ["问题库为空，无法自检；请先运行 kb_qa_bank.py build --dry-run 生成问题库。"]
    suggestions: list[str] = []
    causes = summary["causes"]
    rate5 = summary["hit_rate_at_5"]
    if rate5 >= 0.9:
        suggestions.append(f"整体命中率@5={rate5:.0%}（{summary['hit_at_5']}/{total}），检索质量良好；"
                           "优先关注未命中问题与 @1 排序。")
    else:
        suggestions.append(f"整体命中率@5={rate5:.0%}（{summary['hit_at_5']}/{total}），低于 90%："
                           "建议补同义词/改模板并回补知识单元后复检。")
    if causes.get(CAUSE_SYNONYM):
        examples = [x["id"] for x in items if x.get("cause") == CAUSE_SYNONYM][:3]
        suggestions.append(f"同义词缺 {causes[CAUSE_SYNONYM]} 条（如 {', '.join(examples)}）："
                           "在 kb_qa/kb_read_api 的检索同义词表中补充概念别名，或用问题里的概念词做查询扩展。")
    if causes.get(CAUSE_TEMPLATE):
        examples = [x["id"] for x in items if x.get("cause") == CAUSE_TEMPLATE][:3]
        suggestions.append(f"模板失配/排序不足 {causes[CAUSE_TEMPLATE]} 条（如 {', '.join(examples)}）："
                           "检查模板措辞是否引入泛词；可为来源单元的概念字段加权，或让问题直接使用 claim 关键词。")
    if causes.get(CAUSE_NO_MATERIAL):
        examples = [x["id"] for x in items if x.get("cause") == CAUSE_NO_MATERIAL][:3]
        suggestions.append(f"知识库无料/检索无命中 {causes[CAUSE_NO_MATERIAL]} 条（如 {', '.join(examples)}）："
                           "对应知识单元可能抽取失败或问题超出知识库覆盖，建议补卡或从问题库移除。")
    weak_themes = [g for g in themes if g["total"] >= 2 and g["hit_rate_at_5"] < 0.8]
    if weak_themes:
        suggestions.append("低覆盖主题：" + "；".join(
            f"{g['theme']}（@5 {g['hit_rate_at_5']:.0%}, {g['hit_at_5']}/{g['total']}）" for g in weak_themes)
            + "：建议优先补充该主题知识单元或调整模板。")
    if summary["hit_at_1"] < summary["hit_at_5"]:
        suggestions.append(f"@1={summary['hit_at_1']} < @5={summary['hit_at_5']}：来源单元常被同类单元挤到第 2–5 位，"
                           "可评估在融合排序中对「来源/来自同一单元」加权重，或增大 topk 后再评估。")
    suggestions.append("复检命令：python tools/kb_corpus/kb_qa_selfcheck.py selfcheck（检索-only，不调 LLM）。")
    return suggestions


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def load_questions(path: Path | str = DEFAULT_QUESTIONS) -> list[dict]:
    path = Path(path)
    if not path.exists():
        raise SelfcheckError(f"问题库文件不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelfcheckError(f"问题库读取/解析失败：{path}（{exc}）") from exc
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list):
        raise SelfcheckError(f"问题库结构不合法（缺少 questions[]）：{path}")
    return [q for q in questions if isinstance(q, dict) and q.get("question")]


def _cached_search_fn():
    """默认检索：复用 kb_qa.search，并对 kb_read_api 的单元/语料加载做进程内缓存（加速 32 条批量）。"""
    qa = _load_kb_qa()
    try:
        api = qa.kb_api()
        if not getattr(api.load_units, "_cached_for_selfcheck", False):
            api.load_units = lru_cache(maxsize=1)(api.load_units)
            api.load_units._cached_for_selfcheck = True
        if not getattr(api.load_corpus, "_cached_for_selfcheck", False):
            api.load_corpus = lru_cache(maxsize=1)(api.load_corpus)
            api.load_corpus._cached_for_selfcheck = True
    except Exception:  # noqa: BLE001 - 缓存加速失败不影响正确性
        pass

    def _search(question: str, topk: int):
        return qa.search(question, topk=topk)

    return _search


def run_selfcheck(questions_path: Path | str = DEFAULT_QUESTIONS,
                  topk: int = DEFAULT_TOPK, limit: int | None = None,
                  search_fn=None, no_llm: bool = True) -> dict:
    """逐条 retrieval-only 自检 → 结构化结果（不调 LLM）。"""
    started = time.monotonic()
    topk = max(MIN_TOPK, min(MAX_TOPK, int(topk)))
    questions = load_questions(questions_path)
    if limit:
        questions = questions[:max(1, int(limit))]
    fn = search_fn or _cached_search_fn()
    items = []
    for q in questions:
        try:
            hits = fn(q["question"], topk=topk) or []
        except Exception as exc:  # noqa: BLE001 - 单条检索失败按无命中记录
            hits = []
            q = dict(q)
            q["_search_error"] = f"{type(exc).__name__}: {exc}"
        item = evaluate_question(q, hits, topk)
        if q.get("_search_error"):
            item["search_error"] = q["_search_error"]
        items.append(item)
    summary = summarize(items)
    themes = by_theme(items)
    payload = {
        "schema_version": 1, "kind": "kb_qa_selfcheck",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_questions": str(questions_path),
        "retrieval": {"mode": "retrieval_only", "no_llm": bool(no_llm),
                      "topk": topk, "engine": "kb_qa.search（units search + corpus-search 融合）"},
        "summary": summary, "by_theme": themes, "questions": items,
        "suggestions": build_suggestions(summary, themes, items),
        "disclaimer": "自检仅验证「候选问题能否召回其来源知识单元」，不代表问答答案正确性；仅供研究参考，不构成投资建议。",
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
    return payload


def render_markdown(payload: dict) -> str:
    s = payload["summary"]
    lines = [f"# 问答问题库 retrieval-only 自检（{payload['generated_at'][:10]}）", "",
             f"> 问题库：`{payload['source_questions']}`（{s['total']} 条）· 检索 top-{payload['retrieval']['topk']} · "
             f"**不调 LLM**（retrieval-only）。", "",
             "## 汇总", "",
             f"- 命中率 @1：**{s['hit_rate_at_1']:.0%}**（{s['hit_at_1']}/{s['total']}）",
             f"- 命中率 @5：**{s['hit_rate_at_5']:.0%}**（{s['hit_at_5']}/{s['total']}）",
             f"- 未命中来源 unit：{s['miss_source']} 条；检索无结果：{s['retrieval_no_hits']} 条；"
             f"来源平均排名：{s['avg_source_rank'] if s['avg_source_rank'] is not None else '—'}",
             f"- 未命中归因：{json.dumps(s['causes'], ensure_ascii=False)}", "",
             "## 按主题覆盖", "",
             "| 主题 | 条数 | @1 | @5 | 未命中 | 归因 |", "|---|---|---|---|---|---|"]
    for g in payload["by_theme"]:
        lines.append(f"| {g['theme']} | {g['total']} | {g['hit_rate_at_1']:.0%} | {g['hit_rate_at_5']:.0%} | "
                     f"{g['miss_source']} | {json.dumps(g['causes'], ensure_ascii=False)} |")
    lines += ["", "## 逐条明细", "",
              "| # | 主题 | 问题 | 来源 unit | @1 | @5 | 来源排名 | 归因 |", "|---|---|---|---|---|---|---|---|"]
    for i, x in enumerate(payload["questions"], 1):
        mark1 = "✅" if x["top1_is_source"] else "—"
        mark5 = "✅" if x["hit_source"] else "❌"
        lines.append(f"| {i} | {x.get('theme') or '—'} | {str(x.get('question'))[:60]} | "
                     f"{x.get('source_unit_id')} | {mark1} | {mark5} | {x.get('source_rank') or '—'} | "
                     f"{x.get('cause') or '—'} |")
    misses = [x for x in payload["questions"] if x["miss"]]
    lines += ["", "## 未命中明细与缺口", ""]
    if misses:
        for x in misses:
            lines.append(f"- `{x['id']}` [{x.get('theme')}] {x.get('question')}（来源 `{x.get('source_unit_id')}`，"
                         f"归因：{x.get('cause')}）")
    else:
        lines.append("全部问题均命中来源 unit（top-5），无未命中。")
    lines += ["", "## 改进建议", ""]
    lines += [f"{i}. {sug}" for i, sug in enumerate(payload["suggestions"], 1)]
    lines += ["", "## 说明", "",
              f"- {payload['disclaimer']}",
              "- 自检为检索链路（kb_qa.search）复用，不调用 LLM、不写知识库；结果用于问题库/检索改进。"]
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
    ap = argparse.ArgumentParser(description="问答问题库 retrieval-only 自检（命中率@1/@5 + 缺口归因）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("selfcheck", help="逐条检索自检并输出 JSON+MD")
    sc.add_argument("--questions", default=str(DEFAULT_QUESTIONS), help="问题库 JSON")
    sc.add_argument("--topk", type=int, default=DEFAULT_TOPK, help=f"检索 top-k（默认 {DEFAULT_TOPK}）")
    sc.add_argument("--limit", type=int, default=0, help="只自检前 N 条（0=全部）")
    sc.add_argument("--out-json", default=str(OUT_JSON))
    sc.add_argument("--out-md", default=str(OUT_MD))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "selfcheck":  # pragma: no cover
        return 2
    try:
        payload = run_selfcheck(args.questions, topk=args.topk,
                                limit=args.limit or None)
        jp, mp = write_outputs(payload, args.out_json, args.out_md)
    except SelfcheckError as exc:
        print(f"[kb_qa_selfcheck] FAILED: {exc}", file=sys.stderr)
        return 1
    s = payload["summary"]
    print(f"[kb_qa_selfcheck] total={s['total']} hit@1={s['hit_at_1']} ({s['hit_rate_at_1']:.0%}) "
          f"hit@5={s['hit_at_5']} ({s['hit_rate_at_5']:.0%}) miss={s['miss_source']} "
          f"no_hits={s['retrieval_no_hits']} elapsed={payload['elapsed_seconds']}s")
    print(f"[kb_qa_selfcheck] causes={json.dumps(s['causes'], ensure_ascii=False)}")
    print(f"[kb_qa_selfcheck] json={jp}")
    print(f"[kb_qa_selfcheck] md={mp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
