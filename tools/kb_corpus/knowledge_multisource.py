# -*- coding: utf-8 -*-
"""知识多源交叉验证与置信分级 v1.0（W1-1，2026-11-03）。

背景：治理显示「多源概念=0」——每个概念只来自单一博主，无法交叉验证真伪。
本模块把多个输入（知识单元 / 学习卡 / 候选卡）按**归一化概念**聚类，统计**独立来源**
（博主 / 书 / 研报），给出置信分级，并识别同概念答案的方向/否定/相反事实矛盾 → 待仲裁清单。

输入（只读；默认全部存在则读，缺失自动跳过并在报告标注）：
  - portal/static/kb_units_all.json                 知识单元（source=书籍/策略经典/研报，带 book/concept/claim）
  - portal/static/kb_learning_cards.json            学习卡（concept/q/a/source，含博主/研报/书）
  - research/reports/card_candidates_视频号学习_8博主_llm_20261103.json   博主候选卡
  - research/reports/card_candidates_laomu_notes_20261102.json            老木笔记候选卡
  - tools/kb_corpus/synonyms_v2.json                概念同义词/别名表（归一化用）

输出：
  - research/reports/knowledge_multisource_20261103.json
  - research/reports/knowledge_multisource_20261103.md

置信口径：
  - 高 = ≥2 个**独立来源**（不同博主 / 不同书 / 不同研报）且跨来源答案一致（3-gram Jaccard ≥ 阈值，
         或关键数值集合重合 ≥60%）；
  - 中 = ≥2 独立来源但措辞不同（一致性未达阈值）；若同时命中冲突 → 中 + 待仲裁标记；
  - 低 = 单源。
独立来源 = (source_type, source_key)：同一博主的多个候选/单元只算 1 个来源；同一本书/同一篇研报同理
（研报按 rp 编号计独立来源，书按书名计，书籍版次括号归一）。

冲突识别（同概念跨来源，输出「待仲裁清单」，不自动改数据）：
  - 方向冲突：一方看多词占优、另一方看空词占优；
  - 否定冲突：一方为否定式表述（不要/不必/禁止/不宜…），另一方为肯定式（必须/应当/务必…）；
  - 相反事实：同一数值维度一方「≥/大于/至少」、另一方「≤/小于/最多」。

只读声明：本模块对输入只做读取；run() 会记录每个输入文件前后 sha256 并断言不变；
除 --out/--md 两个输出文件外不写任何文件；不改学习卡、不写数据库。

用法：
  python tools/kb_corpus/knowledge_multisource.py                # 默认输入 → 默认输出
  python tools/kb_corpus/knowledge_multisource.py --out X.json --md X.md
  python tools/kb_corpus/knowledge_multisource.py --input units=path --input cards=path
  python tools/kb_corpus/knowledge_multisource.py --json-stdout   # stdout 打印完整 JSON
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SYNONYMS = REPO / "tools" / "kb_corpus" / "synonyms_v2.json"
OUT_JSON = REPO / "research" / "reports" / "knowledge_multisource_20261103.json"
OUT_MD = REPO / "research" / "reports" / "knowledge_multisource_20261103.md"

# 默认输入（kind, path）；kind ∈ units / cards / candidates
DEFAULT_INPUTS: list[tuple[str, Path]] = [
    ("units", REPO / "portal" / "static" / "kb_units_all.json"),
    ("cards", REPO / "portal" / "static" / "kb_learning_cards.json"),
    ("candidates", REPO / "research" / "reports" /
     "card_candidates_视频号学习_8博主_llm_20261103.json"),
    ("candidates", REPO / "research" / "reports" /
     "card_candidates_laomu_notes_20261102.json"),
]

VERSION = "1.1.0"
SOURCE_TYPE_LABEL = {"blogger": "博主", "book": "书", "research_report": "研报", "other": "其他"}
CONFIDENCE_ORDER = {"高": 0, "中": 1, "低": 2}
# 一致性阈值（3-gram Jaccard；同义改写常见 0.35~0.55，同句复述 >0.6）
SIM_CONSISTENT = 0.45
NUM_OVERLAP_CONSISTENT = 0.60
MIN_ANSWER_CHARS = 12          # 短于此的答案不参与一致性比较（避免“是/否”类噪声）
SNIPPET_CHARS = 90

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
MD_MARKS_RE = re.compile(r"[`*_>#|]+")
MD_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
BULLET_RE = re.compile(r"^[（(]?[一二三四五六七八九十0-9]+[）)、.．]\s*")
NON_WORD_RE = re.compile(r"[^\w\u4e00-\u9fff]+")
LEAD_Q_RE = re.compile(r"^(什么是|什么叫|如何|怎样|为什么|啥是)")
EDITION_RE = re.compile(r"[（(]\d+[）)]$")

GENERIC_SUFFIXES = ("战法", "公式", "指标", "参数", "定义", "概念", "策略", "方法", "技巧", "体系", "理论")

UP_WORDS = re.compile(r"看多|买入|加仓|上涨|走强|新高|突破|机会|低吸|看涨|做多|强势")
DOWN_WORDS = re.compile(r"看空|卖出|减仓|下跌|走弱|新低|跌破|风险|止损|出货|看跌|做空|弱势")
NEG_PAT = re.compile(r"不要|不必|无需|没有必要|禁止|切勿|不宜|不应|不能|并非|不是")
POS_PAT = re.compile(r"必须|务必|一定要|应当|应该|需要|可以|能够")
CMP_UP = re.compile(r"(?:大于|高于|超过|至少|不止|≥|>=|>)\s*(\d+(?:\.\d+)?)\s*(%|日|天|倍|个|根)?")
CMP_DOWN = re.compile(r"(?:小于|低于|不足|最多|至多|≤|<=|<)\s*(\d+(?:\.\d+)?)\s*(%|日|天|倍|个|根)?")


# ---------------------------------------------------------------- 文本/概念归一化
def normalize_text(text) -> str:
    """去 Markdown 标记/链接/多余空白，保留中文与语义字符（用于展示与相似度）。"""
    s = str(text or "")
    s = MD_LINK_RE.sub(" ", s)
    s = MD_MARKS_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def cjk_only(text) -> str:
    return "".join(CJK_RE.findall(normalize_text(text)))


def _base_concept(concept) -> str:
    """概念基础归一：去序号/疑问前缀/标点/空白，小写。不做同义词与后缀归一。"""
    s = normalize_text(concept)
    s = BULLET_RE.sub("", s)
    s = LEAD_Q_RE.sub("", s)
    s = NON_WORD_RE.sub("", s).lower()
    return s


def strip_generic_suffix(base: str) -> str:
    """去掉「战法/公式/指标…」等泛化后缀（仅当剩余长度≥2，避免把词根削光）。"""
    for suffix in GENERIC_SUFFIXES:
        if base.endswith(suffix) and len(base) - len(suffix) >= 2:
            return base[: -len(suffix)]
    return base


def _compatible_alias(alias: str, canonical: str) -> bool:
    """保守同义合并：只接受长度接近且字符重合度高的别名（避免把“搜索同义词组”
    里的近义但不同概念，如 涨停/涨停基因/伏击涨停，误并成同一概念）。"""
    if not alias or not canonical or alias == canonical:
        return False
    if abs(len(alias) - len(canonical)) > 2:
        return False
    common = len(set(alias) & set(canonical))
    return common / max(len(alias), len(canonical)) >= 0.6


def load_synonym_aliases(path: Path | None = SYNONYMS) -> dict[str, str]:
    """读取同义词组表 → {归一后别名: 归一后规范词}（仅保守别名，供概念聚类）。"""
    aliases: dict[str, str] = {}
    if path is None or not Path(path).is_file():
        return aliases
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return aliases
    for group in doc.get("groups") or []:
        if not isinstance(group, list) or not group:
            continue
        canonical = strip_generic_suffix(_base_concept(group[0]))
        if not canonical:
            continue
        for alias in group:
            key = strip_generic_suffix(_base_concept(alias))
            if _compatible_alias(key, canonical):
                aliases[key] = canonical
    return aliases


def normalize_concept(concept, aliases: dict[str, str] | None = None, strict: bool = False) -> str:
    """概念归一化（聚类 key）。

    strict=True：只做基础归一（保留原词，供“严格精确聚类”对照统计）；
    strict=False：基础归一 + 去泛化后缀 + 同义词组映射（默认，提升跨来源召回）。
    """
    base = _base_concept(concept)
    if strict:
        return base
    stripped = strip_generic_suffix(base)
    return (aliases or {}).get(base) or (aliases or {}).get(stripped) or stripped


def ngram_set(text, n: int = 3) -> set[str]:
    s = cjk_only(text)
    if len(s) < n:
        return set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def number_set(text) -> set[str]:
    """提取答案中的数值（去重），用于“关键数值一致”判定。"""
    return set(re.findall(r"\d+(?:\.\d+)?", normalize_text(text)))


def answer_consistency(ans_a: str, ans_b: str) -> dict:
    """两来源答案一致性：3-gram Jaccard + 关键数值重合率。"""
    sa, sb = ngram_set(ans_a), ngram_set(ans_b)
    sim = jaccard(sa, sb)
    na, nb = number_set(ans_a), number_set(ans_b)
    num_overlap = jaccard(na, nb) if na and nb else 0.0
    consistent = bool(sim >= SIM_CONSISTENT or (num_overlap >= NUM_OVERLAP_CONSISTENT and len(na) >= 2))
    return {"similarity": round(sim, 4), "number_overlap": round(num_overlap, 4),
            "consistent": consistent}


# ---------------------------------------------------------------- 输入解析
def _strip_edition(name: str) -> str:
    return EDITION_RE.sub("", str(name or "").strip())


def parse_blogger(source: str) -> str:
    """从 source/evidence 字符串提取博主名（视频号学习项目文件名 20261103_博主.md）。"""
    s = str(source or "")
    m = re.search(r"\d{8}_([^·\.\s/]+?)(?:\(\d+\))?\.md", s)
    if m:
        return m.group(1)
    if "laomu_notes" in s:
        return "laomu_notes"
    m = re.search(r"视频号学习_8博主\s*[·・]\s*([^·\s]+)", s)
    if m:
        return m.group(1)
    return ""


def classify_source(source: str) -> tuple[str, str, str]:
    """来源分类 → (source_type, source_key, source_label)。

    优先级：研报 rp 编号 > 《书名》 > 视频号博主文件名 > laomu_notes > 其他。
    """
    s = str(source or "")
    m = re.search(r"rp_[A-Za-z0-9]+", s)
    if m:
        return "research_report", m.group(0), f"研报·{m.group(0)}"
    m = re.search(r"《([^》]+)》", s)
    if m:
        book = _strip_edition(m.group(1).strip())
        return "book", book, f"书·{book}"
    blogger = parse_blogger(s)
    if blogger:
        return "blogger", blogger, f"博主·{blogger}"
    return "other", normalize_text(s)[:40] or "unknown", "其他"


def _record(concept, answer, source_type: str, source_key: str, source_label: str,
            origin: str, evidence: str = "") -> dict | None:
    """构造一条内部记录（概念/答案过短则丢弃）。"""
    concept = normalize_text(concept)
    answer = normalize_text(answer)
    if len(_base_concept(concept)) < 2 or len(cjk_only(answer)) < 4:
        return None
    return {"concept": concept, "answer": answer, "source_type": source_type,
            "source_key": source_key, "source_label": source_label,
            "origin": origin, "evidence": normalize_text(evidence)[:120]}


def _key_other(source: str) -> tuple[str, str, str]:
    return "other", normalize_text(source)[:40] or "unknown", "其他"


def _source_from_unit(unit: dict) -> tuple[str, str, str]:
    """知识单元（kb_units_all）来源：source 字段已分 研报/书籍/策略经典。"""
    source = str(unit.get("source") or "")
    book = str(unit.get("book") or "")
    if source == "研报" or book.startswith("rp_"):
        key = book or str(unit.get("report_id") or "report")
        return "research_report", key, f"研报·{key}"
    if source in ("书籍", "策略经典") and book:
        book = _strip_edition(book)
        return "book", book, f"书·{book}"
    if book:
        parsed = classify_source(book)
        if parsed[0] != "other":
            return parsed
    return _key_other(source or book)


def _source_from_card(card: dict) -> tuple[str, str, str]:
    """学习卡/候选卡来源：博主字段 > source 字符串解析。"""
    blogger = str(card.get("blogger") or "").strip()
    source = str(card.get("source") or "")
    if blogger:
        return "blogger", blogger, f"博主·{blogger}"
    return classify_source(source)


def collect_records(inputs: list[tuple[str, Path]] | None = None) -> tuple[list[dict], list[dict]]:
    """读取全部输入 → (records, input_meta)。只读，不修改输入。

    支持输入格式：
      units      : {"units": [{source, book, concept, claim, evidence}]} 或 [{...}]
      cards      : {"cards": [{concept, q, a, source, blogger?}]} 或 [{...}]
      candidates : {"candidates": [{concept, a, source, blogger?, project?}]} 或 [{...}]
    统一转成 {concept, answer, source_type, source_key, source_label, origin, evidence}。
    """
    records: list[dict] = []
    meta: list[dict] = []
    for kind, path in (inputs if inputs is not None else DEFAULT_INPUTS):
        path = Path(path)
        entry = {"kind": kind, "path": str(path), "exists": path.is_file(),
                 "sha256": None, "records": 0, "skipped_reason": None}
        if not path.is_file():
            entry["skipped_reason"] = "文件不存在（跳过）"
            meta.append(entry)
            continue
        raw = path.read_bytes()
        entry["sha256"] = hashlib.sha256(raw).hexdigest()
        n_before = len(records)
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            entry["skipped_reason"] = f"JSON 解析失败: {exc}"
            meta.append(entry)
            continue
        if kind == "units":
            items = doc.get("units") if isinstance(doc, dict) else doc
            for unit in items or []:
                stype, skey, slabel = _source_from_unit(unit)
                rec = _record(unit.get("concept"), unit.get("claim"), stype, skey, slabel,
                              origin=f"units:{path.name}", evidence=str(unit.get("evidence") or ""))
                if rec:
                    records.append(rec)
        elif kind in ("cards", "candidates"):
            items = (doc.get("cards") if kind == "cards" else doc.get("candidates")) \
                if isinstance(doc, dict) else doc
            for card in items or []:
                stype, skey, slabel = _source_from_card(card)
                rec = _record(card.get("concept"), card.get("a") or card.get("claim"),
                              stype, skey, slabel, origin=f"{kind}:{path.name}",
                              evidence=str(card.get("evidence") or ""))
                if rec:
                    records.append(rec)
        else:
            raise ValueError(f"未知输入 kind: {kind}（可选 units/cards/candidates）")
        entry["records"] = max(0, len(records) - n_before)
        meta.append(entry)
    return records, meta


def verify_read_only(input_meta: list[dict]) -> list[dict]:
    """只读校验：重算输入 sha256，标注 unchanged（True=运行期间未被修改）。"""
    for entry in input_meta:
        path = Path(entry["path"])
        entry["sha256_after"] = (hashlib.sha256(path.read_bytes()).hexdigest()
                                 if entry.get("exists") and path.is_file() else None)
        entry["unchanged"] = (entry["sha256"] is None or
                              entry["sha256"] == entry["sha256_after"])
    return input_meta


# ---------------------------------------------------------------- 聚类 / 置信 / 冲突
def _dedupe_records(records: list[dict]) -> tuple[list[dict], int]:
    """同一来源内近似重复的答案只留一条（同 source_key 且答案 3-gram Jaccard≥0.9）。"""
    by_source: dict[tuple, list[dict]] = defaultdict(list)
    for rec in records:
        by_source[(rec["source_type"], rec["source_key"])].append(rec)
    kept: list[dict] = []
    dropped = 0
    for recs in by_source.values():
        sigs: list[tuple[dict, set]] = []
        for rec in recs:
            sig = ngram_set(rec["answer"])
            dup = any(jaccard(sig, s2) >= 0.9 for _, s2 in sigs)
            if dup:
                dropped += 1
                continue
            sigs.append((rec, sig))
            kept.append(rec)
    return kept, dropped


def cluster_records(records: list[dict], aliases: dict[str, str] | None = None,
                    strict: bool = False) -> dict[str, dict]:
    """按归一化概念聚类；返回 {concept_key: cluster}。"""
    clusters: dict[str, dict] = {}
    for rec in records:
        key = normalize_concept(rec["concept"], aliases, strict=strict)
        if not key:
            continue
        cluster = clusters.setdefault(key, {"concept_key": key, "records": [],
                                            "concept_raw": Counter()})
        cluster["records"].append(rec)
        cluster["concept_raw"][rec["concept"]] += 1
    for cluster in clusters.values():
        cluster["display"] = cluster["concept_raw"].most_common(1)[0][0]
    return clusters


def _cluster_sources(cluster: dict) -> dict[tuple, dict]:
    """cluster → {(source_type, source_key): {label, answers, types}}（独立来源去重）。"""
    sources: dict[tuple, dict] = {}
    for rec in cluster["records"]:
        key = (rec["source_type"], rec["source_key"])
        src = sources.setdefault(key, {"source_type": rec["source_type"],
                                       "source_key": rec["source_key"],
                                       "source_label": rec["source_label"],
                                       "answers": [], "origins": set()})
        src["answers"].append(rec["answer"])
        src["origins"].add(rec["origin"])
    return sources


def _detect_conflicts(cluster: dict, sources: dict[tuple, dict]) -> list[dict]:
    """同概念跨来源冲突检测（方向/否定/相反事实）→ 供人工仲裁的条目列表。"""
    conflicts: list[dict] = []
    if len(sources) < 2:
        return conflicts
    items = [(key, src) for key, src in sources.items()]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (ka, sa), (kb, sb) = items[i], items[j]
            text_a = " ".join(sa["answers"])
            text_b = " ".join(sb["answers"])
            up_a, down_a = len(UP_WORDS.findall(text_a)), len(DOWN_WORDS.findall(text_a))
            up_b, down_b = len(UP_WORDS.findall(text_b)), len(DOWN_WORDS.findall(text_b))
            net_a, net_b = up_a - down_a, up_b - down_b
            pair = {"a": {"source_type": sa["source_type"], "source_label": sa["source_label"],
                          "answer": normalize_text(sa["answers"][0])[:SNIPPET_CHARS]},
                    "b": {"source_type": sb["source_type"], "source_label": sb["source_label"],
                          "answer": normalize_text(sb["answers"][0])[:SNIPPET_CHARS]}}
            # 1) 方向冲突
            if (net_a > 0 > net_b) or (net_b > 0 > net_a):
                conflicts.append({"concept": cluster["display"], "conflict_type": "方向相反",
                                  "severity": "high", **pair})
                continue
            # 2) 否定 vs 肯定式表述
            neg_a, pos_a = bool(NEG_PAT.search(text_a)), bool(POS_PAT.search(text_a))
            neg_b, pos_b = bool(NEG_PAT.search(text_b)), bool(POS_PAT.search(text_b))
            if (neg_a and not neg_b and pos_b) or (neg_b and not neg_a and pos_a):
                conflicts.append({"concept": cluster["display"],
                                  "conflict_type": "否定/肯定表述冲突",
                                  "severity": "medium", **pair})
                continue
            # 3) 相反事实：同一数值维度一方“≥/大于”、另一方“≤/小于”
            ups_a = {(v, u) for v, u in CMP_UP.findall(text_a)}
            downs_b = {(v, u) for v, u in CMP_DOWN.findall(text_b)}
            ups_b = {(v, u) for v, u in CMP_UP.findall(text_b)}
            downs_a = {(v, u) for v, u in CMP_DOWN.findall(text_a)}
            if (ups_a and downs_b) or (ups_b and downs_a):
                conflicts.append({"concept": cluster["display"],
                                  "conflict_type": "相反事实（阈值方向相反）",
                                  "severity": "medium", **pair})
    return conflicts


def assess_cluster(cluster: dict) -> dict:
    """单概念聚类 → 来源统计 + 一致性 + 置信等级 + 冲突。"""
    sources = _cluster_sources(cluster)
    answers = [a for a in (rec["answer"] for rec in cluster["records"])
               if len(cjk_only(a)) >= MIN_ANSWER_CHARS]
    best = {"similarity": 0.0, "number_overlap": 0.0, "consistent": False}
    src_list = list(sources.values())
    for i in range(len(src_list)):
        for j in range(i + 1, len(src_list)):
            for ans_a in src_list[i]["answers"]:
                for ans_b in src_list[j]["answers"]:
                    if len(cjk_only(ans_a)) < MIN_ANSWER_CHARS or len(cjk_only(ans_b)) < MIN_ANSWER_CHARS:
                        continue
                    stat = answer_consistency(ans_a, ans_b)
                    if stat["similarity"] > best["similarity"]:
                        best = stat
    conflicts = _detect_conflicts(cluster, sources)
    n_sources = len(sources)
    n_types = len({s["source_type"] for s in sources.values()})
    if n_sources >= 2:
        confidence = "高" if (best["consistent"] and not conflicts) else "中"
    else:
        confidence = "低"
    return {
        "concept": cluster["display"],
        "concept_key": cluster["concept_key"],
        "n_sources": n_sources,
        "n_source_types": n_types,
        "source_types": sorted({s["source_type"] for s in sources.values()}),
        "confidence": confidence,
        "consistency": {"max_similarity": best["similarity"],
                        "number_overlap": best["number_overlap"],
                        "consistent": best["consistent"],
                        "answers_compared": len(answers)},
        "sources": [{"source_type": src["source_type"],
                     "source_key": src["source_key"],
                     "source_label": src["source_label"],
                     "n_records": len(src["answers"]),
                     "origins": sorted(src["origins"]),
                     "answer_snippets": [normalize_text(a)[:SNIPPET_CHARS]
                                         for a in src["answers"][:2]]}
                    for src in src_list],
        "conflicts": conflicts,
    }


def build_concepts(records: list[dict], aliases: dict[str, str] | None = None,
                   strict: bool = False) -> tuple[dict[str, dict], dict]:
    """聚类 + 评估；返回 (clusters, stats)。"""
    clusters = cluster_records(records, aliases, strict=strict)
    assessments = {}
    for key, cluster in clusters.items():
        assessments[key] = assess_cluster(cluster)
    conf_counts = Counter(a["confidence"] for a in assessments.values())
    multi = [a for a in assessments.values() if a["n_sources"] >= 2]
    stats = {
        "concepts_total": len(assessments),
        "multi_source_concepts": len(multi),
        "confidence_counts": {k: int(conf_counts.get(k, 0)) for k in ("高", "中", "低")},
        "conflict_count": sum(len(a["conflicts"]) for a in assessments.values()),
        "conflict_concepts": sum(1 for a in assessments.values() if a["conflicts"]),
        "source_type_counts": dict(Counter(s["source_type"] for a in assessments.values()
                                           for s in a["sources"])),
    }
    return assessments, stats


# ---------------------------------------------------------------- 报告组装
def build_payload(inputs: list[tuple[str, Path]] | None = None,
                  aliases: dict[str, str] | None = None) -> dict:
    """主流程（纯内存）：读取输入 → 聚类 → 置信/冲突 → 报告 dict。不写文件。"""
    inputs = list(inputs if inputs is not None else DEFAULT_INPUTS)
    aliases = load_synonym_aliases() if aliases is None else aliases
    records, input_meta = collect_records(inputs)
    input_meta = verify_read_only(input_meta)          # 只读声明：输入前后 sha256 一致
    records, dedup_dropped = _dedupe_records(records)
    # 严格精确聚类（仅基础归一，用于对照“归一化到底提升多少召回”）
    _, strict_stats = build_concepts(records, aliases={}, strict=True)
    assessments, stats = build_concepts(records, aliases, strict=False)
    ordered = sorted(assessments.values(),
                     key=lambda a: (CONFIDENCE_ORDER[a["confidence"]], -a["n_sources"],
                                    -len(a["conflicts"]), a["concept"]))
    multi = [a for a in ordered if a["n_sources"] >= 2]
    conflicts = [dict(c, concept_key=a["concept_key"])
                 for a in ordered for c in a["conflicts"]]
    # 低置信概念：保留紧凑字段 + 全量 sources[]（W1-2b：含 source_key，供下游回源确认独立来源）
    low_compact = [{"concept": a["concept"], "concept_key": a["concept_key"],
                    "n_sources": a["n_sources"], "confidence": a["confidence"],
                    "source_types": a["source_types"],
                    "sources": [{"source_type": s["source_type"],
                                 "source_key": s["source_key"],
                                 "source_label": s["source_label"],
                                 "n_records": s["n_records"],
                                 "origins": s["origins"]}
                                for s in a["sources"]]}
                   for a in ordered if a["n_sources"] < 2]
    lines = [
        "口径：概念归一=去标点/疑问前缀/泛化后缀+同义词表映射；独立来源=(来源类型,来源标识)，"
        "同博主/同书/同研报只算 1 源；一致性=跨来源答案 3-gram Jaccard≥"
        f"{SIM_CONSISTENT} 或关键数值重合≥{NUM_OVERLAP_CONSISTENT}。",
        "研报按 rp 编号计独立来源（同一 rp 的多张卡只算 1 源）；书按书名计（版次括号归一）；"
        "博主按文件名/博主字段计。",
        "置信=高（≥2 源且一致且无冲突）/中（≥2 源但措辞不同，或存在待仲裁冲突）/低（单源）。",
        "冲突仅做关键词级提示（方向/否定/相反事实），输出「待仲裁清单」供人工复核，不自动改卡/删数据。",
        "注意：研报按 rp 编号计独立来源，同一机构不同期报告常高度模板化——高置信里若为「周度/月度表现」类"
        "时点事实，只代表两份报告同期方向一致，不代表可复用的稳定规律；此类条目应按“事实型”而非“规律型”使用。",
        "只读：输入文件前后 sha256 一致；本报告由 run() 写到指定输出路径，不改学习卡、不写数据库。",
        "W1-2b 契约：`concepts` 全量条目（含低置信单源概念）均输出 sources[]（source_key/source_type/"
        "source_label），下游 concept_synonyms 可据此对合并簇回源确认独立来源，不再出现「多源-待确认」。",
    ]
    payload = {
        "meta": {
            "report": "knowledge_multisource",
            "version": VERSION,
            "task": "W1-1 多源交叉验证与置信分级",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "read_only": True,
            "read_only_verified": all(i["unchanged"] for i in input_meta),
            "params": {"sim_consistent": SIM_CONSISTENT,
                       "num_overlap_consistent": NUM_OVERLAP_CONSISTENT,
                       "min_answer_chars": MIN_ANSWER_CHARS,
                       "snippet_chars": SNIPPET_CHARS,
                       "synonym_table": str(SYNONYMS.relative_to(REPO)).replace("\\", "/"),
                       "normalization": "基础归一+去泛化后缀+同义词组映射",
                       "source_independence": "同博主/同书/同研报各计 1 独立来源"},
            "inputs": input_meta,
            "records_total": len(records) + dedup_dropped,
            "records_used": len(records),
            "records_deduped": dedup_dropped,
            "concepts_total": stats["concepts_total"],
            "concepts_total_strict": strict_stats["concepts_total"],
            "multi_source_concepts": stats["multi_source_concepts"],
            "multi_source_concepts_strict": strict_stats["multi_source_concepts"],
            "confidence_counts": stats["confidence_counts"],
            "conflict_count": stats["conflict_count"],
            "conflict_concepts": stats["conflict_concepts"],
            "source_type_counts": stats["source_type_counts"],
            "limitations": lines,
        },
        "multi_source_concepts": multi,
        "concepts": multi + low_compact,
        "conflicts": conflicts,
    }
    return payload


def render_markdown(payload: dict) -> str:
    """JSON → Markdown 报告（摘要/多源清单/置信分布/待仲裁/口径）。"""
    meta = payload["meta"]
    multi = payload["multi_source_concepts"]
    conflicts = payload["conflicts"]
    p = meta["params"]
    lines: list[str] = []
    add = lines.append
    add("# 知识多源交叉验证与置信分级（W1-1，2026-11-03）")
    add("")
    add(f"- 生成：{meta['generated_at']} ｜ 版本 v{meta['version']} ｜ 只读输入：**{meta['read_only']}**")
    add(f"- 输入：{len(meta['inputs'])} 个（" + "、".join(
        f"{Path(i['path']).name} {i['records']} 条" if i["exists"] else f"{Path(i['path']).name} 缺失"
        for i in meta["inputs"]) + "）")
    add(f"- 记录：读入 {meta['records_total']} 条 → 去重后 **{meta['records_used']}** 条"
        f"（同来源近似重复剔除 {meta['records_deduped']} 条）")
    add(f"- 概念聚类：**{meta['concepts_total']}** 个（严格精确归一对照：{meta['concepts_total_strict']} 个）")
    cc = meta["confidence_counts"]
    add(f"- 多源概念：**{meta['multi_source_concepts']}** 个（严格归一对照：{meta['multi_source_concepts_strict']} 个）")
    add(f"- 置信分布：**高 {cc.get('高', 0)} / 中 {cc.get('中', 0)} / 低 {cc.get('低', 0)}**")
    add(f"- 待仲裁冲突：**{meta['conflict_count']}** 条（涉及 {meta['conflict_concepts']} 个概念）")
    add(f"- 来源类型分布（概念×来源计数）：{json.dumps(meta['source_type_counts'], ensure_ascii=False)}")
    add("")
    add("## 一、多源概念清单（≥2 独立来源）")
    add("")
    if multi:
        add("| # | 概念 | 独立来源数 | 来源类型 | 来源 | 置信 | 答案一致性 | 待仲裁 |")
        add("|---:|---|---:|---|---|---|---|---|")
        for i, a in enumerate(multi, 1):
            labels = "；".join(sorted(s["source_label"] for s in a["sources"]))
            c = a["consistency"]
            add(f"| {i} | {a['concept']} | {a['n_sources']} | "
                f"{'/'.join(SOURCE_TYPE_LABEL.get(t, t) for t in a['source_types'])} | "
                f"{labels} | **{a['confidence']}** | "
                f"sim={c['max_similarity']:.2f}/num={c['number_overlap']:.2f} | "
                f"{len(a['conflicts'])} |")
    else:
        add("（本批 0 个：所有概念均为单源，跨来源答案无法交叉验证 —— 与治理基线一致。"
            "严格归一对照见 meta；需先统一概念命名/同义表或人工归并。）")
    add("")

    add("## 二、置信分布")
    add("")
    add("| 置信 | 概念数 | 说明 |")
    add("|---|---:|---|")
    add(f"| 高 | {cc.get('高', 0)} | ≥2 独立来源且答案一致（同一事实/参数） |")
    add(f"| 中 | {cc.get('中', 0)} | ≥2 独立来源但措辞不同（含待仲裁冲突） |")
    add(f"| 低 | {cc.get('低', 0)} | 单源，无法交叉验证 |")
    add("")

    add("## 三、待仲裁清单（冲突识别）")
    add("")
    if conflicts:
        add("| # | 概念 | 冲突类型 | 严重度 | A 来源 / 答案 | B 来源 / 答案 | 建议 |")
        add("|---:|---|---|---|---|---|---|")
        for i, c in enumerate(conflicts, 1):
            a, b = c["a"], c["b"]
            add(f"| {i} | {c['concept']} | {c['conflict_type']} | {c['severity']} | "
                f"{a['source_label']}：{a['answer'][:60]} | "
                f"{b['source_label']}：{b['answer'][:60]} | "
                "人工仲裁：核对原文出处，拆分为条件化结论 |")
    else:
        add("（本批无方向/否定/相反事实冲突；单源概念无跨源冲突可比。）")
    add("")

    add("## 四、多源概念详情")
    add("")
    if multi:
        for a in multi:
            c = a["consistency"]
            add(f"### {a['concept']}（置信 {a['confidence']}，{a['n_sources']} 源，"
                f"一致性 sim={c['max_similarity']:.2f}）")
            for s in a["sources"]:
                label = SOURCE_TYPE_LABEL.get(s["source_type"], s["source_type"])
                add(f"- **[{label}] {s['source_label']}**（{s['n_records']} 条）："
                    + " ／ ".join(s["answer_snippets"]))
            if a["conflicts"]:
                add(f"- ⚠️ 待仲裁：{len(a['conflicts'])} 条冲突（见第三节）")
            add("")
    else:
        add("（无多源概念，暂无详情。）")
        add("")

    add("## 五、单源概念（Top 15，按来源类型）")
    add("")
    lows = [c for c in payload["concepts"] if c.get("n_sources", 0) < 2][:15]
    add("| 概念 | 来源类型 |")
    add("|---|---|")
    for c in lows:
        add(f"| {c['concept']} | {'/'.join(c.get('source_types') or [])} |")
    add("")

    add("## 六、口径与局限")
    add("")
    add(f"1. 概念归一：{p['normalization']}；同义表 `{p['synonym_table']}`；严格精确归一对照见 meta。")
    add(f"2. 独立来源：{p['source_independence']}；同一 rp 研报/同一本书的多张卡不重复计数。")
    add(f"3. 一致性：跨来源答案 3-gram Jaccard ≥ {p['sim_consistent']} 或关键数值重合 ≥ "
        f"{p['num_overlap_consistent']}；低于阈值判「措辞不同」（中），属保守口径。")
    add("4. 冲突识别为关键词级提示（方向词/否定词/阈值比较），存在误报与漏报可能，只作人工仲裁线索，"
        "**不自动改卡、不改学习卡库**。")
    add("5. 单源概念无法交叉验证；多源=0 时不代表知识错误，只代表「跨来源证据不足」，需统一概念命名/同义表"
        "或人工归并后复测。")
    add("6. 只读：输入前后 sha256 已记录在 JSON `meta.inputs`；未写数据库/学习卡。")
    add("")
    add("## 七、复现")
    add("")
    add("```powershell")
    add("C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/knowledge_multisource.py")
    add("C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_knowledge_multisource.py")
    add("```")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 落盘 / CLI
def run(inputs: list[tuple[str, Path]] | None = None,
        out_json: str | Path | None = None, out_md: str | Path | None = None,
        aliases: dict[str, str] | None = None) -> dict:
    """主流程（写文件）：只写 --out/--md 两个输出；返回 payload。"""
    payload = build_payload(inputs, aliases)
    jp = Path(out_json) if out_json else OUT_JSON
    mp = Path(out_md) if out_md else OUT_MD
    jp.parent.mkdir(parents=True, exist_ok=True)
    mp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    mp.write_text(render_markdown(payload), encoding="utf-8")
    return payload


def _parse_input_arg(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--input 需形如 kind=path（kind=units/cards/candidates）")
    kind, path = value.split("=", 1)
    if kind not in ("units", "cards", "candidates"):
        raise argparse.ArgumentTypeError(f"未知 kind: {kind}")
    return kind, Path(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="knowledge_multisource",
        description="知识多源交叉验证与置信分级（W1-1）：概念聚类/来源计数/置信分级/冲突仲裁清单")
    ap.add_argument("--input", action="append", type=_parse_input_arg, default=None,
                    metavar="kind=path",
                    help="输入（可多次；kind=units/cards/candidates；默认用内置默认输入）")
    ap.add_argument("--out", default=str(OUT_JSON), help="输出 JSON 路径")
    ap.add_argument("--md", default=str(OUT_MD), help="输出 Markdown 路径")
    ap.add_argument("--json-stdout", action="store_true", help="stdout 打印完整 JSON")
    args = ap.parse_args(argv)
    inputs = args.input if args.input else None
    try:
        payload = run(inputs, args.out, args.md)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"[knowledge_multisource] 运行失败: {exc}", file=sys.stderr)
        return 2
    meta = payload["meta"]
    print(f"# 多源交叉验证 v{meta['version']}：记录 {meta['records_used']} 条 → "
          f"概念 {meta['concepts_total']} 个；多源概念 {meta['multi_source_concepts']} 个；"
          f"置信 高{meta['confidence_counts']['高']}/中{meta['confidence_counts']['中']}/"
          f"低{meta['confidence_counts']['低']}；待仲裁 {meta['conflict_count']} 条")
    print(f"# 输出: {Path(args.out).resolve()}")
    print(f"# 输出: {Path(args.md).resolve()}")
    if args.json_stdout:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
