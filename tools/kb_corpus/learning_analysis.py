# -*- coding: utf-8 -*-
"""学习卡质量与主题覆盖分析（缺口清单）生成器。

输入：
  portal/static/kb_units_all.json     知识单元（498 条：书籍 35 / 研报 463）
  portal/static/kb_learning_cards.json 学习卡（20 张 + daily）

输出：
  research/reports/learning_analysis_20261101.json   结构化分析（统计/覆盖/质量/缺口/建议）
  research/reports/learning_analysis_20261101.md     人读报告（图表化表格 + TOP10 缺口 + 建议新增卡）

CLI::

    python tools/kb_corpus/learning_analysis.py run [--json]
    python tools/kb_corpus/learning_analysis.py run --units ... --cards ... --out-dir ...

说明：本脚本只做分析（不改 learning_digest 生成器）；结尾给出排序权重建议参数。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "portal" / "static"
DEFAULT_UNITS = STATIC / "kb_units_all.json"
DEFAULT_CARDS = STATIC / "kb_learning_cards.json"
DEFAULT_OUT_DIR = REPO / "research" / "reports"
DATE_TAG = "20261101"

SCHEMA_VERSION = 1
TOP_GAP_THEMES = 10
HIGH_FREQ_UNITS = 5  # 高频主题阈值（单元数 >=5 且 0 张卡 → 高频无卡缺口）

DISCLAIMER = "本报告为知识库内容覆盖/质量的分析输出，仅供内部研究，不构成投资建议。"

# 主题词表（按优先级排列：先具体后泛化；命中即归类）
THEME_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("AI算力/硬件", ("算力", "AI", "GPU", "光模块", "PCB", "CCL", "交换机", "HBM", "服务器",
                     "数据中心", "液冷", "铜缆", "CPO", "智能体", "智算")),
    ("半导体/电子", ("半导体", "芯片", "晶圆", "封测", "光刻", "存储", "IC", "EDA", "硅片",
                     "刻蚀", "端侧", "模拟芯片", "射频")),
    ("新能源/电力", ("光伏", "风电", "储能", "电池", "锂", "钠", "电网", "特高压", "核电",
                     "电力", "虚拟电厂", "火电", "绿电", "煤电", "新能源")),
    ("汽车/智驾", ("汽车", "智驾", "智能驾驶", "车规", "整车", "零部件", "充电桩")),
    ("医药/生物", ("医药", "创新药", "生物", "医疗", "CXO", "器械", "疫苗")),
    ("消费/零售", ("消费", "零售", "食品", "饮料", "白酒", "家电", "电商", "旅游", "酒店",
                   "品牌", "二手", "首购")),
    ("金融/地产", ("银行", "证券", "保险", "地产", "房地产", "房贷", "券商", "金融",
                   "贷款利率", "资本市场")),
    ("宏观/政策", ("宏观", "政策", "利率", "通胀", "GDP", "财政", "货币", "汇率", "经济",
                   "央行", "改革", "稳市")),
    ("网络安全/信创", ("网络安全", "信创", "国产化", "自主可控", "网安", "等保", "安全合规")),
    ("化工/材料", ("化工", "材料", "塑料", "PVC", "有机硅", "氟", "钛白", "农药", "化肥")),
    ("机械/制造", ("机械", "工程机械", "机器人", "自动化", "机床", "工业")),
    ("农业", ("农业", "养殖", "生猪", "禽", "种业", "饲料", "转基因")),
    ("通信/互联网", ("通信", "5G", "6G", "互联网", "SaaS", "平台经济", "云服务")),
    ("传媒/文化", ("传媒", "游戏", "影视", "文娱", "广告", "出版")),
    ("军工/航天", ("军工", "航天", "航空", "国防", "卫星", "导弹")),
    ("交运/物流", ("交运", "物流", "航运", "港口", "机场", "快递", "铁路")),
    ("有色/金属", ("有色", "铜", "铝", "黄金", "稀土", "锂矿", "金属")),
    ("建筑/建材", ("建筑", "建材", "水泥", "基建")),
    ("量学/交易", ("量学", "量柱", "涨停", "黄金柱", "伏击", "筹码", "庄家", "阴阳柱", "倍量")),
    ("评级/研报方法论", ("评级", "目标价", "盈利预测", "估值", "市盈率", "ROE", "方法论", "研报")),
]
OTHER_THEME = "其他"

_NOISE = re.compile(r"[\s\u3000（）()【】\[\]《》「」『』“”\"'’·、，,。.：:；;？?！!—-]+")


# ---------------------------------------------------------------------------
# 纯函数：归一化 / 主题分类 / 统计
# ---------------------------------------------------------------------------
def normalize(text) -> str:
    return _NOISE.sub("", str(text or "")).lower()


def classify_theme(item) -> str:
    """按词表把知识单元/学习卡归入主题；兼容 dict 与纯文本。"""
    if isinstance(item, dict):
        text = " ".join(str(item.get(k) or "") for k in ("concept", "claim", "a", "chapter", "book"))
    else:
        text = str(item or "")
    for theme, keywords in THEME_RULES:
        if any(k in text for k in keywords):
            return theme
    return OTHER_THEME


def length_stats(values: list[int]) -> dict:
    """长度分位统计（空列表安全）。"""
    values = sorted(int(v) for v in values if isinstance(v, (int, float)))
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": 0, "min": 0, "p25": 0, "median": 0, "p75": 0, "max": 0}
    return {
        "n": n,
        "mean": round(sum(values) / n, 1),
        "min": values[0],
        "p25": values[n // 4],
        "median": values[n // 2],
        "p75": values[(3 * n) // 4],
        "max": values[-1],
    }


def unit_quality(unit: dict) -> float:
    """知识单元质量代理（用于缺口建议的候选排序）：claim 信息量 + evidence 充分性 + 页码。"""
    claim = str(unit.get("claim") or "")
    evidence = str(unit.get("evidence") or "")
    score = 0.0
    score += min(30.0, len(claim) / 3.0)          # claim 长度（60 字≈20 分）
    score += min(30.0, len(evidence) / 4.0)       # evidence 长度（120 字≈30 分）
    if unit.get("p1") is not None:
        score += 15
    if unit.get("p2") is not None and unit.get("p2") != unit.get("p1"):
        score += 5
    if unit.get("chapter"):
        score += 5
    return round(min(100.0, score), 1)


def card_quality(card: dict) -> dict:
    """学习卡质量代理：答案/证据长度、页码、来源可读性、重复。返回 score + reasons。"""
    answer = str(card.get("a") or "")
    evidence = str(card.get("evidence") or "")
    source = str(card.get("source") or "")
    concept = str(card.get("concept") or "")
    score = 60.0
    reasons: list[str] = []
    if len(answer) < 40:
        score -= 20
        reasons.append("答案过短(<40字)")
    elif len(answer) <= 220:
        score += 10
    if len(evidence) < 40:
        score -= 15
        reasons.append("证据过短(<40字)")
    elif len(evidence) >= 80:
        score += 8
    if not re.search(r"p\d+", source):
        score -= 10
        reasons.append("出处缺页码")
    if "《" not in source:
        score -= 5
        reasons.append("出处缺书名")
    if not (4 <= len(concept) <= 30):
        score -= 5
        reasons.append("主题命名长度异常")
    return {"score": round(max(0.0, min(100.0, score)), 1), "reasons": reasons}


def _match_card_units(card: dict, units: list[dict], unit_norms: list[str]) -> list[int]:
    """卡片覆盖的知识单元下标：主题归一化精确匹配，其次包含匹配（长度≥4）。"""
    key = normalize(card.get("concept") or card.get("q") or "")
    hits: list[int] = []
    if not key:
        return hits
    for idx, unit_norm in enumerate(unit_norms):
        if not unit_norm:
            continue
        if key == unit_norm:
            hits.append(idx)
        elif len(key) >= 4 and len(unit_norm) >= 4 and (key in unit_norm or unit_norm in key):
            hits.append(idx)
    return hits


def _bar(value: float, total: float, width: int = 18) -> str:
    if not total:
        return ""
    n = max(0, min(width, round(value / total * width)))
    return "█" * n


# ---------------------------------------------------------------------------
# 主分析
# ---------------------------------------------------------------------------
def analyze(units: list[dict], cards: list[dict], *, generated_at: str | None = None,
            units_meta: dict | None = None, cards_meta: dict | None = None) -> dict:
    """核心分析（纯函数）：统计 / 覆盖 / 质量 / 缺口 / TOP10 建议。"""
    units = [u for u in units if isinstance(u, dict)]
    cards = [c for c in cards if isinstance(c, dict)]
    generated_at = generated_at or datetime.now().astimezone().isoformat(timespec="seconds")

    unit_norms = [normalize(u.get("concept") or "") for u in units]
    unit_themes = [classify_theme(u) for u in units]

    covered_idx: set[int] = set()
    card_matches: list[dict] = []
    for card in cards:
        hits = _match_card_units(card, units, unit_norms)
        covered_idx.update(hits)
        card_matches.append({
            "concept": card.get("concept"),
            "matched_units": len(hits),
            "themes": sorted({unit_themes[i] for i in hits}),
        })
    card_themes = [classify_theme(c) for c in cards]

    # ---- 主题分布 + 覆盖度 ----
    theme_units = Counter(unit_themes)
    theme_covered = Counter(unit_themes[i] for i in covered_idx)
    theme_cards = Counter(card_themes)
    themed_rows = []
    for theme, count in theme_units.most_common():
        covered = theme_covered.get(theme, 0)
        themed_rows.append({
            "theme": theme,
            "units": count,
            "units_share_pct": round(count / max(1, len(units)) * 100, 1),
            "cards": theme_cards.get(theme, 0),
            "covered_units": covered,
            "uncovered_units": count - covered,
            "unit_coverage_rate": round(covered / max(1, count), 4),
        })

    # ---- 类型/长度分布 ----
    source_types = Counter(str(u.get("source") or "未知") for u in units)
    books = Counter(str(u.get("book") or "") for u in units)
    claim_lens = [len(str(u.get("claim") or "")) for u in units]
    evidence_lens = [len(str(u.get("evidence") or "")) for u in units]

    # ---- 卡片质量 ----
    card_quality_rows = []
    for card in cards:
        q = card_quality(card)
        card_quality_rows.append({
            "concept": card.get("concept"),
            "source": card.get("source"),
            "score": q["score"],
            "reasons": q["reasons"],
            "a_len": len(str(card.get("a") or "")),
            "evidence_len": len(str(card.get("evidence") or "")),
            "has_page": bool(re.search(r"p\d+", str(card.get("source") or ""))),
        })
    low_quality_cards = [c for c in card_quality_rows if c["score"] < 60]
    unique_card_concepts = Counter(normalize(c.get("concept")) for c in cards if normalize(c.get("concept")))
    duplicate_cards = [
        {"concept": concept, "count": n,
         "suggestion": "合并为一张（保留质量分最高/证据最全的一张）"}
        for concept, n in unique_card_concepts.items() if n > 1
    ]

    # ---- 缺口 ----
    covered_themes = {theme for theme, covered in theme_covered.items() if covered > 0}
    uncovered_themes = [
        {"theme": row["theme"], "units": row["units"], "cards": row["cards"]}
        for row in themed_rows if row["theme"] not in covered_themes
    ]
    high_freq_no_card = [
        row for row in themed_rows if row["units"] >= HIGH_FREQ_UNITS and row["cards"] == 0
    ]
    concept_counts = Counter(str(u.get("concept") or "") for u in units)
    card_concept_norms = {normalize(c.get("concept")) for c in cards}
    recurring_concept_gaps = [
        {"concept": concept, "units": n}
        for concept, n in concept_counts.most_common()
        if n >= 2 and normalize(concept) not in card_concept_norms
    ][:15]

    # ---- TOP10 缺口主题 + 建议新增卡片 ----
    gap_candidates = []
    for row in themed_rows:
        if row["units"] == 0:
            continue
        priority = row["uncovered_units"] * (2.0 if row["cards"] == 0 else 1.0)
        priority *= 1.0 + min(row["units"] / 200.0, 0.5)  # 主题体量微调
        gap_candidates.append({**row, "priority_score": round(priority, 1)})
    gap_candidates.sort(key=lambda x: (-x["priority_score"], -x["units"], x["theme"]))
    top_gaps = []
    for rank, row in enumerate(gap_candidates[:TOP_GAP_THEMES], 1):
        # 该主题下未被覆盖、质量最高的知识单元 → 建议新增卡
        candidates_idx = [i for i, theme in enumerate(unit_themes)
                          if theme == row["theme"] and i not in covered_idx]
        candidates_idx.sort(key=lambda i: -unit_quality(units[i]))
        suggestions = []
        for idx in candidates_idx[:2]:
            unit = units[idx]
            suggestions.append({
                "q": f"什么是「{unit.get('concept') or '该主题'}」？",
                "a": str(unit.get("claim") or ""),
                "source": f"《{unit.get('book') or '未知'}》 {unit.get('chapter') or ''} "
                          f"p{unit.get('p1')}-{unit.get('p2')}".strip(),
                "evidence_preview": str(unit.get("evidence") or "")[:120],
                "unit_quality": unit_quality(unit),
                "reason": f"{row['theme']} 主题有 {row['units']} 个知识单元但仅 {row['cards']} 张卡覆盖",
            })
        top_gaps.append({
            "rank": rank,
            "theme": row["theme"],
            "units": row["units"],
            "cards": row["cards"],
            "covered_units": row["covered_units"],
            "uncovered_units": row["uncovered_units"],
            "unit_coverage_rate": row["unit_coverage_rate"],
            "priority_score": row["priority_score"],
            "high_freq_no_card": row["cards"] == 0 and row["units"] >= HIGH_FREQ_UNITS,
            "suggested_cards": suggestions,
        })

    # ---- learning_digest 排序权重建议（不改生成器，仅参数建议） ----
    digest_recommendations = {
        "note": "以下为建议参数，供 learning_digest.select_daily_cards 后续迭代参考；本任务不修改生成器。",
        "quality_score_adjust": {
            "answer_len_bonus_max": 15,
            "evidence_len_bonus_max": 12,
            "page_bonus": 4,
            "topic_gap_bonus_max": 12,
            "duplicate_penalty": 25,
        },
        "daily_selection": {
            "max_cards_per_theme": 2,
            "prefer_zero_card_themes": True,
            "zero_card_theme_quota": 2,
            "require_coverage_rotation_days": 7,
        },
        "reason": f"当前 {len(cards)} 张卡仅覆盖 {len(covered_idx)}/{len(units)} 个知识单元"
                  f"（{round(len(covered_idx) / max(1, len(units)) * 100, 1)}%），"
                  f"{len(uncovered_themes)} 个主题无任何卡；建议提高主题缺口加成并限制单主题卡数。",
    }

    overview = {
        "units": len(units),
        "source_types": dict(source_types),
        "books": len([b for b in books if b]),
        "chapters": len({str(u.get("chapter") or "") for u in units if u.get("chapter")}),
        "concepts_unique": len({c for c in (str(u.get("concept") or "") for u in units) if c}),
        "recurring_concepts": sum(1 for n in concept_counts.values() if n >= 2),
        "cards": len(cards),
        "covered_units": len(covered_idx),
        "unit_coverage_rate": round(len(covered_idx) / max(1, len(units)), 4),
        "covered_themes": len(covered_themes),
        "uncovered_themes": len(uncovered_themes),
        "theme_count": len(theme_units),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "date": DATE_TAG,
        "sources": {
            "units": units_meta or {},
            "cards": cards_meta or {},
        },
        "overview": overview,
        "theme_distribution": themed_rows,
        "type_distribution": {
            "source_types": dict(source_types),
            "books_top10": [{"book": b, "units": n} for b, n in books.most_common(10) if b],
            "claim_len": length_stats(claim_lens),
            "evidence_len": length_stats(evidence_lens),
        },
        "card_coverage": {
            "cards": len(cards),
            "covered_units": len(covered_idx),
            "coverage_rate": round(len(covered_idx) / max(1, len(units)), 4),
            "cards_detail": card_matches,
            "covered_themes": sorted(covered_themes),
            "uncovered_themes": uncovered_themes,
        },
        "quality": {
            "card_quality_avg": round(sum(c["score"] for c in card_quality_rows) / max(1, len(card_quality_rows)), 1),
            "cards_with_page": sum(1 for c in card_quality_rows if c["has_page"]),
            "low_quality_cards": low_quality_cards,
            "duplicate_cards": duplicate_cards,
            "unit_claim_len": length_stats(claim_lens),
            "unit_evidence_len": length_stats(evidence_lens),
        },
        "gaps": {
            "high_freq_no_card_themes": [
                {"theme": r["theme"], "units": r["units"]} for r in high_freq_no_card
            ],
            "uncovered_themes": uncovered_themes,
            "recurring_concept_gaps": recurring_concept_gaps,
            "duplicate_cards": duplicate_cards,
        },
        "top10_gap_themes": top_gaps,
        "digest_recommendations": digest_recommendations,
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------
def render_markdown(report: dict) -> str:
    ov = report["overview"]
    lines = [
        "# 学习卡质量与主题覆盖分析（2026-11-01）",
        "",
        f"> 数据：知识单元 **{ov['units']}** 条 / 学习卡 **{ov['cards']}** 张 · 生成于 {report['generated_at']}",
        f"> 结论速览：卡片覆盖 **{ov['covered_units']}/{ov['units']}** 个单元（{ov['unit_coverage_rate'] * 100:.1f}%）；"
        f"**{ov['uncovered_themes']}** 个主题 0 卡；低质量卡 **{len(report['quality']['low_quality_cards'])}** 张、"
        f"重复卡 **{len(report['quality']['duplicate_cards'])}** 组。",
        "",
        "## 一、概览",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 知识单元 | {ov['units']}（书籍 {ov['source_types'].get('书籍', 0)} / 研报 {ov['source_types'].get('研报', 0)}） |",
        f"| 涉及书目 | {ov['books']} 本 · 章节 {ov['chapters']} 个 · 去重主题 {ov['concepts_unique']} 个 |",
        f"| 重复概念（≥2 单元） | {ov['recurring_concepts']} 个 |",
        f"| 学习卡 | {ov['cards']} 张 · 覆盖单元 {ov['covered_units']} 个（{ov['unit_coverage_rate'] * 100:.1f}%） |",
        f"| 主题覆盖 | 已覆盖 {ov['covered_themes']}/{ov['theme_count']} 个主题 |",
        "",
        "### 知识单元长度代理",
        "",
        "| 指标 | n | mean | p25 | median | p75 | max |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, key in (("claim 长度", "unit_claim_len"), ("evidence 长度", "unit_evidence_len")):
        s = report["quality"][key]
        lines.append(f"| {name} | {s['n']} | {s['mean']} | {s['p25']} | {s['median']} | {s['p75']} | {s['max']} |")

    lines += ["", "## 二、主题分布与卡片覆盖（按单元数降序）", "",
              "| 主题 | 单元数 | 占比 | 卡片数 | 已覆盖单元 | 未覆盖单元 | 单元覆盖率 | 分布 |",
              "|---|---|---|---|---|---|---|---|"]
    total_units = ov["units"]
    for row in report["theme_distribution"]:
        lines.append(
            f"| {row['theme']} | {row['units']} | {row['units_share_pct']}% | {row['cards']} | "
            f"{row['covered_units']} | {row['uncovered_units']} | {row['unit_coverage_rate'] * 100:.1f}% | "
            f"{_bar(row['units'], total_units)} |"
        )

    lines += ["", "## 三、学习卡质量代理", "",
              f"- 卡片质量均分：**{report['quality']['card_quality_avg']}** / 100；含页码出处：**{report['quality']['cards_with_page']}/{ov['cards']}**",
              f"- 低质量卡（score<60）：**{len(report['quality']['low_quality_cards'])}** 张", ""]
    if report["quality"]["low_quality_cards"]:
        lines += ["| 卡片主题 | 质量分 | 问题 |", "|---|---|---|"]
        for c in report["quality"]["low_quality_cards"]:
            lines.append(f"| {c['concept']} | {c['score']} | {'；'.join(c['reasons']) or '—'} |")
        lines.append("")
    if report["quality"]["duplicate_cards"]:
        lines += ["重复卡（建议合并）：", ""]
        for d in report["quality"]["duplicate_cards"]:
            lines.append(f"- ⚠ `{d['concept']}` 出现 {d['count']} 次 → {d['suggestion']}")
        lines.append("")

    lines += ["## 四、覆盖缺口", "",
              f"### 4.1 高频主题无卡（单元数≥{HIGH_FREQ_UNITS} 且 0 卡）", ""]
    hf = report["gaps"]["high_freq_no_card_themes"]
    if hf:
        lines += ["| 主题 | 单元数 |", "|---|---|"]
        for r in hf:
            lines.append(f"| {r['theme']} | {r['units']} |")
    else:
        lines.append("- 无（所有高频主题至少有 1 张卡）")
    lines += ["", "### 4.2 完全无卡主题", ""]
    ut = report["gaps"]["uncovered_themes"]
    if ut:
        lines += ["| 主题 | 单元数 |", "|---|---|"]
        for r in ut:
            lines.append(f"| {r['theme']} | {r['units']} |")
    else:
        lines.append("- 无")
    lines += ["", "### 4.3 重复概念无卡（≥2 单元）", ""]
    rc = report["gaps"]["recurring_concept_gaps"]
    if rc:
        lines += ["| 概念 | 单元数 |", "|---|---|"]
        for r in rc[:10]:
            lines.append(f"| {r['concept']} | {r['units']} |")
    else:
        lines.append("- 无")

    lines += ["", "## 五、TOP10 缺口主题与建议新增卡片", "",
              "| # | 主题 | 单元数 | 卡片数 | 未覆盖单元 | 优先级分 | 无卡 |",
              "|---|---|---|---|---|---|---|"]
    for g in report["top10_gap_themes"]:
        lines.append(f"| {g['rank']} | {g['theme']} | {g['units']} | {g['cards']} | "
                     f"{g['uncovered_units']} | {g['priority_score']} | {'✅' if g['high_freq_no_card'] else ''} |")
    lines += ["", "### 建议新增卡片（每个缺口主题优先 2 张）", ""]
    for g in report["top10_gap_themes"]:
        lines.append(f"#### {g['rank']}. {g['theme']}（{g['units']} 单元 / {g['cards']} 卡）")
        for i, s in enumerate(g["suggested_cards"], 1):
            lines += [f"- **建议卡 {i}**：{s['q']}",
                      f"  - 答：{s['a']}",
                      f"  - 出处：{s['source']}",
                      f"  - 理由：{s['reason']}（单元质量代理 {s['unit_quality']}）"]
        lines.append("")

    rec = report["digest_recommendations"]
    lines += ["## 六、learning_digest 排序权重建议（仅建议，不改生成器）", "",
              f"> {rec['note']}", "",
              "| 参数 | 建议值 |", "|---|---|"]
    for k, v in rec["quality_score_adjust"].items():
        lines.append(f"| quality_score.{k} | {v} |")
    for k, v in rec["daily_selection"].items():
        lines.append(f"| daily_selection.{k} | {v} |")
    lines += ["", f"**依据**：{rec['reason']}", "",
              "## 七、局限与说明", "",
              "1. 主题归类基于关键词词表（20 类），存在漏分/误分；`其他` 类需人工复核后再细分。",
              "2. 覆盖度以「卡片 concept ↔ 单元 concept 精确/包含匹配」计算，未做语义相似度匹配（可能低估覆盖率）。",
              "3. 质量分为长度/页码等**代理指标**，不代表内容正确性；卡片内容仍需人工校核。",
              "4. 建议新增卡片以「该主题下未被覆盖、质量代理最高」的单元生成，仅作为候选，需人工改写为 Q/A 卡。",
              "", f"> {report['disclaimer']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 读取/输出/CLI
# ---------------------------------------------------------------------------
def read_json_tolerant(path: str | Path) -> tuple[dict | None, str | None]:
    p = Path(path)
    try:
        if not p.is_file():
            return None, f"文件不存在: {p}"
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as exc:  # noqa: BLE001
        return None, f"读取/解析失败: {p}: {exc}"


def describe_file(path: str | Path) -> dict:
    p = Path(path)
    info = {"path": str(p), "exists": False, "mtime": None, "size": None}
    try:
        if p.is_file():
            st = p.stat()
            info.update(exists=True, size=int(st.st_size),
                        mtime=datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(timespec="seconds"))
    except OSError:
        pass
    return info


def run(
    units_path: str | Path = DEFAULT_UNITS,
    cards_path: str | Path = DEFAULT_CARDS,
    out_dir: str | Path = DEFAULT_OUT_DIR,
    date_tag: str = DATE_TAG,
    write: bool = True,
) -> dict:
    units_json, units_err = read_json_tolerant(units_path)
    cards_json, cards_err = read_json_tolerant(cards_path)
    units = (units_json or {}).get("units") if isinstance(units_json, dict) else None
    cards = (cards_json or {}).get("cards") if isinstance(cards_json, dict) else None
    units = [u for u in (units or []) if isinstance(u, dict)]
    cards = [c for c in (cards or []) if isinstance(c, dict)]
    report = analyze(
        units, cards,
        units_meta={**describe_file(units_path),
                    "total_declared": (units_json or {}).get("total") if isinstance(units_json, dict) else None,
                    "error": units_err},
        cards_meta={**describe_file(cards_path),
                    "total_declared": len(cards),
                    "daily": (cards_json or {}).get("daily") if isinstance(cards_json, dict) else None,
                    "error": cards_err},
    )
    report["date"] = date_tag
    if write:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        jp = out / f"learning_analysis_{date_tag}.json"
        mp = out / f"learning_analysis_{date_tag}.md"
        jp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        mp.write_text(render_markdown(report), encoding="utf-8")
        report["_outputs"] = {"json": str(jp), "md": str(mp)}
    return report


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="学习卡质量与主题覆盖分析（缺口清单）")
    parser.add_argument("command", nargs="?", default="run", choices=["run"], help="run=分析并输出（默认）")
    parser.add_argument("--units", default=str(DEFAULT_UNITS), help="kb_units_all.json 路径")
    parser.add_argument("--cards", default=str(DEFAULT_CARDS), help="kb_learning_cards.json 路径")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="输出目录")
    parser.add_argument("--date", default=DATE_TAG, help="日期标签 YYYYMMDD")
    parser.add_argument("--no-write", action="store_true", help="只分析不写盘")
    parser.add_argument("--json", action="store_true", help="打印 JSON（不写盘）")
    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = build_parser().parse_args(argv)
    report = run(args.units, args.cards, args.out_dir, args.date, write=not (args.no_write or args.json))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"学习卡分析完成：units={report['overview']['units']} cards={report['overview']['cards']} | "
          f"覆盖 {report['overview']['covered_units']}/{report['overview']['units']} "
          f"({report['overview']['unit_coverage_rate'] * 100:.1f}%) | "
          f"无卡主题 {report['overview']['uncovered_themes']} 个 | "
          f"低质量卡 {len(report['quality']['low_quality_cards'])} 张 | 重复卡 {len(report['quality']['duplicate_cards'])} 组")
    for out in (report.get("_outputs") or {}).values():
        print(f"  输出：{out}")
    print("TOP5 缺口主题：")
    for g in report["top10_gap_themes"][:5]:
        print(f"  {g['rank']}. {g['theme']}（{g['units']} 单元 / {g['cards']} 卡 / 未覆盖 {g['uncovered_units']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
