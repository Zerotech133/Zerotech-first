# -*- coding: utf-8 -*-
"""问答候选问题库：从知识单元批量生成 20–40 条候选问题（默认不调 LLM）。

用途：为 `tools/kb_corpus/kb_qa.py`（研报知识库 RAG 问答）准备可批量评测/演示的候选问题集，
      每条问题都带来源（unit_id/书名/章节/页码/主题），供 retrieval-only 检索自检与后续扩样。

生成规则（确定性，无 LLM）：
  1) 读取 498 知识单元（463 研报 + 35 书籍，来自 kb_read_api.load_units）；
  2) 主题分类：concept+claim+chapter 关键词规则 → AI算力/半导体、新能源/电池、消费/医药、
     金融/资本市场、宏观/策略/贸易、量化/技术分析、其他/综合；
  3) 模板问题：「是什么 / 影响如何 / 数据多少 / 风险」四类 × 主题专属模板（用 concept 填充）；
  4) 去重：问题文本归一化后全局唯一；同主题同概念最多 2 条；按主题轮转取满 limit（钳制 20..40）；
  5) 每条记录：id/question/type/theme/source_unit_id/book/chapter/page_start/page_end/
     concept/claim_snippet/source。

CLI：
  python tools/kb_corpus/kb_qa_bank.py build --dry-run          # 生成问题库 JSON（research+portal），不调 LLM
  python tools/kb_corpus/kb_qa_bank.py build --answer 5         # 额外对前 5 条调 kb_qa 生成答案（默认不跑）
  python tools/kb_corpus/kb_qa_bank.py build --limit 32 --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_RESEARCH = REPO / "research" / "reports" / "kb_qa_questions_20261101.json"
OUT_PORTAL = REPO / "portal" / "static" / "kb_qa_questions.json"
DEFAULT_LIMIT = 32
MIN_QUESTIONS = 20
MAX_QUESTIONS = 40
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_TOPK = 8
DEFAULT_TIMEOUT = 300
CLAIM_SNIPPET = 140
EVIDENCE_SNIPPET = 120

THEME_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("AI算力/半导体", ("ai", "算力", "gpu", "pcb", "覆铜板", "服务器", "光模块", "半导体", "芯片",
                    "存储", "mlcc", "封装", "数据中心", "大模型", "智能体", "机器人", "液冷", "先进封装")),
    ("新能源/电池", ("电池", "固态", "锂", "钠", "光伏", "储能", "新能源", "充电", "风电", "电力设备", "硅料")),
    ("消费/医药", ("白酒", "消费", "食品", "医", "药", "零售", "家电", "动销", "渠道", "集采", "创新药", "cxo")),
    ("金融/资本市场", ("资本市场", "ipo", "债券", "并购", "投行", "券商", "银行", "保险", "利率",
                    "汇率", "改革", "注册制", "分红", "退市")),
    ("宏观/策略/贸易", ("宏观", "贸易", "经济", "gdp", "cpi", "出口", "进口", "关税", "东盟",
                     "跨境", "电商", "供应链", "海外", "通胀", "汇率")),
    ("量化/技术分析", ("量学", "量柱", "量线", "涨停", "k线", "技术分析", "回测", "因子", "策略",
                    "指标", "形态", "均线", "选股", "操盘")),
]
DEFAULT_THEME = "其他/综合"

# 四类问题模板（concept 填充）；主题可追加专属模板
QUESTION_TYPES = ("是什么", "影响如何", "数据多少", "风险")
BASE_TEMPLATES = {
    "是什么": ["{concept}的核心结论与主要依据是什么？",
               "{concept}具体指什么，研报如何定义？",
               "{concept}的关键逻辑链条是什么？"],
    "影响如何": ["{concept}对产业链或行业格局的影响如何？",
                 "{concept}对相关标的的业绩/估值影响如何？",
                 "{concept}的传导路径与受益环节是什么？"],
    "数据多少": ["关于{concept}，知识库中有哪些可验证的数据或证据？",
                 "{concept}涉及哪些关键量化指标或时间表？",
                 "{concept}的规模/增速/份额等数据是多少？"],
    "风险": ["{concept}的主要风险与不确定性有哪些？",
             "{concept}的结论在什么条件下会被证伪？",
             "研报对{concept}的风险提示是否充分？"],
}
THEME_TEMPLATES = {
    "AI算力/半导体": ["{concept}的技术升级路径与价值量变化如何？"],
    "新能源/电池": ["{concept}的产业化进度与量产时间表如何？"],
    "消费/医药": ["{concept}的景气度与需求变化如何？"],
    "金融/资本市场": ["{concept}的政策演进与市场影响如何？"],
    "宏观/策略/贸易": ["{concept}的宏观影响与数据表现如何？"],
    "量化/技术分析": ["{concept}的识别规则与实战用法是什么？"],
    DEFAULT_THEME: ["{concept}在知识库中的相关记载与结论是什么？"],
}


class BankError(RuntimeError):
    """问题库生成异常（知识库不可读等）。"""


def _load_kb_read_api():
    path = Path(__file__).with_name("kb_read_api.py")
    spec = importlib.util.spec_from_file_location("kb_read_api_for_bank", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise BankError(f"无法加载 kb_read_api.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_kb_qa():
    path = Path(__file__).with_name("kb_qa.py")
    spec = importlib.util.spec_from_file_location("kb_qa_for_bank", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise BankError(f"无法加载 kb_qa.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_units() -> list[dict]:
    try:
        api = _load_kb_read_api()
        units = [u for u in api.load_units() if u.get("claim")]
    except Exception as exc:  # noqa: BLE001
        raise BankError(f"知识单元读取失败：{type(exc).__name__}: {exc}") from exc
    if not units:
        raise BankError("知识单元为空，无法生成候选问题")
    return units


# ---------------------------------------------------------------------------
# ① 主题分类
# ---------------------------------------------------------------------------
def classify_theme(unit: dict) -> str:
    text = " ".join(str(unit.get(k) or "") for k in ("concept", "claim", "chapter", "book")).lower()
    for theme, keywords in THEME_RULES:
        if any(kw in text for kw in keywords):
            return theme
    return DEFAULT_THEME


def normalize_question(text: str) -> str:
    return re.sub(r"[\s，。；：、（）()【】\[\]“”\"'’‘·…—\-?？!！]+", "", str(text or ""))


def _uid_hash(text: str, n: int = 10) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def _richness(unit: dict) -> int:
    return len(str(unit.get("claim") or "")) + len(str(unit.get("evidence") or ""))


def build_candidates(units: list[dict]) -> list[dict]:
    """按主题/概念生成候选问题（未做全局 limit）：同主题同概念最多 2 条，问题文本去重。"""
    by_theme: dict[str, list[dict]] = {}
    for theme, _ in THEME_RULES + [(DEFAULT_THEME, ())]:
        by_theme[theme] = []
    # 每主题内按信息量排序；同概念计数控制
    buckets: dict[str, list[dict]] = {theme: [] for theme in by_theme}
    for unit in units:
        theme = classify_theme(unit)
        buckets.setdefault(theme, []).append(unit)
    candidates: list[dict] = []
    seen_questions: set[str] = set()
    for theme in [t for t, _ in THEME_RULES] + [DEFAULT_THEME]:
        concept_counts: dict[str, int] = {}
        for unit in sorted(buckets.get(theme, []), key=_richness, reverse=True):
            concept = str(unit.get("concept") or "").strip()
            if not concept:
                continue
            ckey = normalize_question(concept)
            if concept_counts.get(ckey, 0) >= 2:
                continue
            # 概念 → 确定性模板类型轮转（同概念第 2 条换类型）
            attempt = concept_counts.get(ckey, 0)
            types = list(QUESTION_TYPES) + [f"主题专属{attempt}"]
            template_key = types[(attempt + _uid_int(ckey)) % len(types)]
            if template_key.startswith("主题专属"):
                extra = THEME_TEMPLATES.get(theme) or THEME_TEMPLATES[DEFAULT_THEME]
                template = extra[_uid_int(ckey + str(attempt)) % len(extra)]
                qtype = "主题专属"
            else:
                options = BASE_TEMPLATES[template_key] + (
                    THEME_TEMPLATES.get(theme, []) if THEME_TEMPLATES.get(theme) else [])
                template = options[_uid_int(ckey + template_key) % len(options)]
                qtype = template_key
            question = template.format(concept=concept)
            nkey = normalize_question(question)
            if not nkey or nkey in seen_questions:
                continue
            seen_questions.add(nkey)
            concept_counts[ckey] = concept_counts.get(ckey, 0) + 1
            claim = str(unit.get("claim") or "").strip()
            evidence = str(unit.get("evidence") or "").strip()
            candidates.append({
                "id": "qk_" + _uid_hash(nkey),
                "question": question,
                "type": qtype,
                "theme": theme,
                "source_unit_id": unit.get("id"),
                "book": unit.get("book") or "",
                "chapter": unit.get("chapter") or "",
                "page_start": unit.get("page_start"),
                "page_end": unit.get("page_end"),
                "concept": concept,
                "claim_snippet": _clip(claim, CLAIM_SNIPPET),
                "evidence_snippet": _clip(evidence, EVIDENCE_SNIPPET) if evidence else "",
                "source": unit.get("source") or "",
            })
    return candidates


def _uid_int(text: str) -> int:
    return int(_uid_hash(text, 8), 16)


def _clip(text, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# ② 分组 + 截取 20–40
# ---------------------------------------------------------------------------
def build_bank(units: list[dict] | None = None, limit: int = DEFAULT_LIMIT,
               answer_n: int = 0, ask_fn=None, model: str = DEFAULT_MODEL,
               topk: int = DEFAULT_TOPK, timeout: float = DEFAULT_TIMEOUT,
               title: str = "") -> dict:
    """生成候选问题库 payload；answer_n>0 时对前 N 条调 kb_qa 生成答案（显式开启）。"""
    units = units if units is not None else load_units()
    limit = max(MIN_QUESTIONS, min(MAX_QUESTIONS, int(limit)))
    candidates = build_candidates(units)
    if not candidates:
        raise BankError("未生成任何候选问题")
    # 主题轮转取满 limit（保证主题分布均衡）
    theme_order = [t for t, _ in THEME_RULES] + [DEFAULT_THEME]
    by_theme: dict[str, list[dict]] = {}
    for cand in candidates:
        by_theme.setdefault(cand["theme"], []).append(cand)
    selected: list[dict] = []
    idx = 0
    while len(selected) < limit:
        moved = False
        for theme in theme_order:
            pool = by_theme.get(theme) or []
            if idx < len(pool):
                selected.append(pool[idx])
                moved = True
                if len(selected) >= limit:
                    break
        if not moved:
            break
        idx += 1
    selected = selected[:limit]
    for i, cand in enumerate(selected, 1):
        cand["rank"] = i
    answers: dict[str, dict] = {}
    if answer_n > 0:
        answers, answer_meta = _answer_questions(selected[:min(answer_n, len(selected))],
                                                 ask_fn=ask_fn, model=model, topk=topk, timeout=timeout)
    else:
        answer_meta = {"requested": 0, "answered": 0, "model": None, "note": "默认 dry-run：未调用 LLM（--answer N 才生成答案）"}
    for cand in selected:
        if cand["id"] in answers:
            cand["answer"] = answers[cand["id"]]
    grouped = []
    for theme in theme_order + [t for t in by_theme if t not in theme_order]:
        qs = [c for c in selected if c["theme"] == theme]
        if qs:
            grouped.append({"theme": theme, "count": len(qs), "questions": qs})
    stats = {"total": len(selected), "themes": len(grouped),
             "by_theme": {g["theme"]: g["count"] for g in grouped},
             "candidates_generated": len(candidates),
             "deduped_removed": len(candidates) - len(selected)}
    return {"schema_version": 1, "kind": "kb_qa_question_bank",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "title": title or "研报知识库问答候选问题库",
            "source": {"units_total": len(units),
                       "units_research": sum(1 for u in units if u.get("source") == "研报"),
                       "units_books": sum(1 for u in units if u.get("source") == "书籍"),
                       "path": "C:/项目/PDF/_ingest_reports + _ingest（kb_read_api.load_units）"},
            "generation": {"rule": "主题关键词分类 → 概念模板问题（是什么/影响如何/数据多少/风险）→ "
                                   "问题文本归一化去重 → 同主题同概念≤2 条 → 主题轮转取满 limit 数量",
                           "limit": limit, "min": MIN_QUESTIONS, "max": MAX_QUESTIONS,
                           "question_types": list(QUESTION_TYPES) + ["主题专属"]},
            "answers": answer_meta,
            "stats": stats, "themes": grouped,
            "questions": selected,
            "disclaimer": "候选问题由知识单元模板生成，仅用于检索/问答评测与研究；引用以知识单元页码为准，不构成投资建议。"}


def _answer_questions(selected: list[dict], ask_fn=None, model: str = DEFAULT_MODEL,
                      topk: int = DEFAULT_TOPK, timeout: float = DEFAULT_TIMEOUT) -> tuple[dict, dict]:
    """对前 N 条问题调 kb_qa 生成答案（显式 --answer N 时才执行，默认不跑 LLM）。"""
    qa = _load_kb_qa()
    fn = ask_fn or qa.ask
    answers: dict[str, dict] = {}
    errors: list[str] = []
    for cand in selected:
        try:
            result = fn(cand["question"], topk=topk, model=model, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{cand['id']}: {type(exc).__name__}: {_clip(exc, 100)}")
            continue
        answers[cand["id"]] = {
            "answer": result.get("answer"),
            "citations": result.get("citations") or [],
            "confidence": result.get("confidence") or {},
            "fallback": bool(result.get("fallback")),
            "citation_check": result.get("citation_check") or {},
        }
    return answers, {"requested": len(selected), "answered": len(answers), "model": model,
                     "errors": errors[:5],
                     "note": "答案由 kb_qa.ask 生成（知识库材料 + 本地 LLM）"}


def write_bank(payload: dict, out_path: Path | str = OUT_RESEARCH,
               portal_path: Path | str | None = OUT_PORTAL) -> tuple[Path, Path | None]:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    out_path.write_text(text, encoding="utf-8")
    portal_written = None
    if portal_path:
        portal_path = Path(portal_path)
        portal_path.parent.mkdir(parents=True, exist_ok=True)
        portal_path.write_text(text, encoding="utf-8")
        portal_written = portal_path
    return out_path, portal_written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="问答候选问题库生成器（默认不调 LLM）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="生成候选问题库 JSON（research + portal 双份）")
    b.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"问题数 {MIN_QUESTIONS}..{MAX_QUESTIONS}（默认 {DEFAULT_LIMIT}）")
    b.add_argument("--dry-run", action="store_true", help="只生成问题库、不调 LLM（默认行为；显式声明用于定时/测试）")
    b.add_argument("--answer", type=int, default=0, help="对前 N 条问题调 kb_qa 生成答案（默认 0=不跑 LLM）")
    b.add_argument("--topk", type=int, default=DEFAULT_TOPK, help="答案生成的检索条数")
    b.add_argument("--model", default=DEFAULT_MODEL, help="答案生成使用的 Ollama 模型")
    b.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    b.add_argument("--out", default=str(OUT_RESEARCH))
    b.add_argument("--portal-out", default=str(OUT_PORTAL))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd != "build":  # pragma: no cover
        return 2
    answer_n = 0 if args.dry_run else max(0, int(args.answer or 0))
    try:
        payload = build_bank(limit=args.limit, answer_n=answer_n, model=args.model,
                             topk=args.topk, timeout=args.timeout)
        paths = write_bank(payload, args.out, args.portal_out)
    except BankError as exc:
        print(f"[kb_qa_bank] FAILED: {exc}", file=sys.stderr)
        return 1
    stats = payload["stats"]
    print(f"[kb_qa_bank] questions={stats['total']} themes={stats['themes']} "
          f"answers={payload['answers']['answered']} dry_run={answer_n == 0}")
    print(f"[kb_qa_bank] by_theme={json.dumps(stats['by_theme'], ensure_ascii=False)}")
    print(f"[kb_qa_bank] out={paths[0]}")
    print(f"[kb_qa_bank] portal={paths[1]}")
    if answer_n == 0:
        print("[kb_qa_bank] 提示：默认不调 LLM；如需生成答案请加 --answer N")
    return 0


if __name__ == "__main__":
    sys.exit(main())
