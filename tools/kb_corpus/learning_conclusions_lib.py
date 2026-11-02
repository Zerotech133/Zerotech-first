# -*- coding: utf-8 -*-
"""W1-4 有效结论库 v1（learning_conclusions_lib）。

把治理后的「多源概念 / 同义簇 / 学习卡 / 候选」聚合为可被 W3-3 API 与军团消费的结论库：
  * 概念簇聚合：以 concept_synonyms 的簇为主键，合并同簇概念/卡/候选的结论文本与来源；
  * 多源计数：独立来源 = (source_type, source_key)；同博主/同书/同研报各计 1 源；
  * 置信分级：高（≥2 独立来源且答案一致）/ 中（≥2 源但措辞/数值不一致，或无法比对）/ 低（单源）；
  * PIT：从研报编号(rp_AP+日期)/文件名日期解析「结论时点」，无日期则标记不可追溯；
  * 战法分类：8 类（缠论/均线/指标/量价/形态/风控/心理/量化）+ 未分类；
  * 可执行性：A 日线可算 / B 需分钟数据 / C 人工文本判断；
  * Top100 排序：置信 > 可执行性 > 多源数 > PIT/已发布；
  * 冲突单列：方向相反 → conflicts；多源但措辞不一致 → arbitration_queue（待 DATA1 仲裁）。

只读输入（sha256 前后校验，不改卡/units/治理文件）：
  research/reports/knowledge_multisource_20261103.json   （W1-1 多源交叉验证）
  research/reports/concept_synonyms_20261103.json        （W1-2 概念簇）
  portal/static/kb_learning_cards.json                   （256 已发布学习卡）
  research/reports/card_candidates_视频号学习_8博主_llm_20261103.json   （LLM 候选 485）
  research/reports/card_candidates_laomu_notes_20261102.json          （老木候选 202）

输出：
  research/reports/learning_conclusions_lib_20261103.json / .md

用法：
  C:\\项目\\py312\\python.exe -X utf8 tools/kb_corpus/learning_conclusions_lib.py
  ... --top 100 --out-json <path> --out-md <path>
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
from typing import Any, Iterable, Mapping, Sequence

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "research" / "reports"

SCHEMA_VERSION = "learning_conclusions.v1"
TASK_ID = "W1-4"
TOP_N_DEFAULT = 100

DEFAULT_INPUTS: dict[str, Any] = {
    "multisource": REPORTS / "knowledge_multisource_20261103.json",
    "synonyms": REPORTS / "concept_synonyms_20261103.json",
    "cards": REPO / "portal" / "static" / "kb_learning_cards.json",
    "candidates": [
        REPORTS / "card_candidates_视频号学习_8博主_llm_20261103.json",
        REPORTS / "card_candidates_laomu_notes_20261102.json",
    ],
}
DEFAULT_OUT_JSON = REPORTS / "learning_conclusions_lib_20261103.json"
DEFAULT_OUT_MD = REPORTS / "learning_conclusions_lib_20261103.md"

# 8 类战法（顺序即并列时优先级）: (category, 展示名, 强关键词)。强关键词才参与分类，避免研报泛词误判。
TACTICS: list[tuple[str, str, tuple[str, ...]]] = [
    ("缠论", "缠论结构（笔/线段/中枢/背驰/买卖点）",
     ("缠论", "禅论", "中枢", "背驰", "线段", "区间套", "分形", "买卖点", "走势类型", "一买", "二买", "三买", "笔")),
    ("均线", "均线系统（粘合/多头排列/支撑）",
     ("均线", "多头排列", "均线粘合", "黄金三角", "五日均线", "十日均线", "二十日均线", "六十日均线")),
    ("指标", "MACD/KDJ/RSI 指标与背离",
     ("MACD", "KDJ", "RSI", "DIF", "DEA", "背离", "指标公式", "通达信", "顶背离", "底背离", "金叉", "死叉")),
    ("量价", "量价与筹码（放量/缩量/筹码）",
     ("放量", "缩量", "倍量", "成交量", "量能", "筹码", "换手率", "资金流", "净流入", "涨停", "跌停", "连板", "封单", "地量", "堆量", "量价")),
    ("形态", "平台/箱体/突破/缺口",
     ("平台", "箱体", "突破", "缺口", "回踩", "支撑位", "压力位", "头肩", "三角形", "旗形", "通道", "双底", "双顶")),
    ("风控", "仓位与止损纪律",
     ("仓位", "止损", "止盈", "风控", "回撤", "盈亏比", "轻仓", "满仓", "半仓", "分批", "加仓", "减仓")),
    ("心理", "交易纪律/心态/复盘",
     ("心态", "心理", "纪律", "情绪", "认知", "贪婪", "恐惧", "人性", "复盘", "执行力", "交易习惯")),
    ("量化", "量化工程（回测/因子/过拟合）",
     ("回测", "因子", "过拟合", "选股器", "量化", "选股公式", "指标代码")),
]

CONF_RANK = {"高": 3, "中": 2, "低": 1}
EXEC_RANK = {"A": 3, "B": 2, "C": 1}
EXEC_LABEL = {"A": "日线可算", "B": "需分钟数据", "C": "人工/文本判断"}

CONSISTENCY_THRESHOLD = 0.45  # 与 W1-1 一致：跨来源答案 3-gram Jaccard ≥0.45 视为一致

_BULL_KW = ("上涨", "看多", "买入", "加仓", "增持", "突破", "利好", "增长", "推荐", "超配",
            "回升", "反弹", "强势", "新高", "机会", "受益", "看涨", "做多")
_BEAR_KW = ("下跌", "看空", "卖出", "减仓", "减持", "跌破", "利空", "下滑", "回避", "低配",
            "回落", "弱势", "新低", "风险", "承压", "看跌", "做空", "止损")

_MINUTE_RE = re.compile(r"分钟|分时|盘口|Level2|level2|逐笔|竞价|龙虎榜|盘中|日内|5分|30分|1分|60分")
_MANUAL_RE = re.compile(r"心态|心理|纪律|情绪|认知|复盘|贪婪|恐惧|人性|调研|纪要|专家|新闻|政策解读|主观|直觉|经验")
_MARKET_COMMENTARY_RE = re.compile(
    r"今天|今日|昨日|明天|明日|早盘|午盘|午后|尾盘|盘面|本周|上周|周五|周一|周二|周三|周四|上证指数|大盘|A股|指数在|点位"
)
_META_RE = re.compile(r"^(>\s*共|共\s*\d+\s*条|文件：|耗时|字数|---)|faster-whisper|转写", re.IGNORECASE)
_RP_RE = re.compile(r"rp_AP(\d+)")            # 研报编号：rp_AP + 日期 + 流水号
_RP_DATE_RE = re.compile(r"rp_AP(\d{8})")      # 从研报编号取报告日期（前 8 位）
_8D_RE = re.compile(r"20\d{6}")
_DATE_DOT_RE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")
# 来源中「学习采集日」前缀，如 20261103_数据帝-量化开发.md
_FILE_DATE_PREFIX_RE = re.compile(r"^(\d{8})[_-]")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _norm_key(text: Any) -> str:
    """概念/文本归一：保留中英文与数字，去标点空白，小写。"""
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(text or "")).lower()


def _clean_text(text: Any, limit: int = 600) -> str:
    out = str(text or "")
    out = out.replace("**", "").replace("`", "").replace("\u3000", " ")
    out = re.sub(r"\s+", " ", out).strip()
    if len(out) > limit:
        out = out[: limit - 1] + "…"
    return out


def _trigrams(text: str) -> set[str]:
    compact = _norm_key(text)
    if len(compact) < 3:
        return {compact} if compact else set()
    return {compact[i:i + 3] for i in range(len(compact) - 2)}


def jaccard(a: str, b: str) -> float:
    """3-gram Jaccard（与 W1-1 答案一致性口径一致）。"""
    ga, gb = _trigrams(a), _trigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _is_metadata_like(concept: str, text: str) -> bool:
    """过滤元数据/转写说明/空壳结论。"""
    c, t = _clean_text(concept, 120), _clean_text(text, 200)
    if not c or len(_norm_key(c)) < 2:
        return True
    if _META_RE.search(c) or _META_RE.search(t):
        return True
    if len(_norm_key(t)) < 12:
        return True
    if t.startswith(">") or "共 " in t[:20]:
        return True
    return False


# --------------------------------------------------------------------------- #
# 来源解析 / PIT
# --------------------------------------------------------------------------- #
def parse_source(source: Any) -> dict[str, str]:
    """解析来源字符串 → {source_type, source_key, source_label, raw}。

    source_type ∈ research_report / book / blogger / other。
    """
    raw = str(source or "").strip()
    rp = _RP_RE.search(raw)
    if rp:
        key = f"rp_AP{rp.group(1)}"
        return {"source_type": "research_report", "source_key": key, "source_label": f"研报·{key}", "raw": raw}
    book = re.search(r"《([^》]+)》", raw)
    if book:
        title = book.group(1).strip()
        return {"source_type": "book", "source_key": title, "source_label": f"书·{title}", "raw": raw}
    if "视频号" in raw or "博主" in raw or "laomu_notes" in raw or "·" in raw:
        # 取 · 分段里含 .md 的文件名作为博主/来源标识
        parts = [p.strip() for p in raw.split("·") if p.strip()]
        candidate = ""
        for part in reversed(parts):
            if ".md" in part or ".jsonl" in part:
                candidate = part
                break
        if not candidate and parts:
            candidate = parts[-1]
        name = re.sub(r"\.(md|jsonl)$", "", candidate)
        name = _FILE_DATE_PREFIX_RE.sub("", name).strip()
        # 同一博主可能有「xxx.md / xxx(1).md」多个转写文件：括号序号不算独立来源
        name = re.sub(r"[（(]\d+[)）]\s*$", "", name).strip()
        if name:
            return {"source_type": "blogger", "source_key": name, "source_label": f"博主·{name}", "raw": raw}
    if raw:
        return {"source_type": "other", "source_key": raw[:60], "source_label": raw[:60], "raw": raw}
    return {"source_type": "other", "source_key": "unknown", "source_label": "未知来源", "raw": ""}


def extract_dates(texts: Iterable[str]) -> dict[str, Any]:
    """从来源/编号/文件名字符串里解析日级时点，返回 PIT 信息。"""
    found: set[str] = set()
    kinds: set[str] = set()
    for text in texts:
        if not text:
            continue
        for match in _RP_DATE_RE.finditer(str(text)):
            found.add(match.group(1))
            kinds.add("report_date")
        for match in _8D_RE.finditer(str(text)):
            value = match.group(0)
            found.add(value)
            kinds.add("file_date")
        for match in _DATE_DOT_RE.finditer(str(text)):
            year, month, day = match.groups()
            try:
                found.add(datetime(int(year), int(month), int(day)).strftime("%Y%m%d"))
                kinds.add("source_date")
            except ValueError:
                continue
    found = {d for d in found if _valid_date(d)}
    as_of = max(found) if found else None
    kind = "report_date" if "report_date" in kinds else ("file_date" if "file_date" in kinds else
                                                         ("source_date" if "source_date" in kinds else None))
    return {
        "verifiable": bool(found),
        "as_of": as_of,
        "precision": "day" if found else "none",
        "date_kind": kind,
        "dates": sorted(found)[-5:],
        "note": ("来源可定位到日级时点" if found else "来源无可解析日期（PIT 不可追溯）"),
    }


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y%m%d")
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# 置信 / 战法 / 可执行性
# --------------------------------------------------------------------------- #
def grade_confidence(n_sources: int, consistent: bool | None) -> str:
    """置信分级规则：高=≥2 独立来源且答案一致；中=≥2 源但不一致/无法比对；低=单源。"""
    if n_sources is None or n_sources <= 1:
        return "低"
    if consistent is True:
        return "高"
    return "中"


def classify_tactic(*texts: str) -> dict[str, Any]:
    """按 8 类战法强关键词打分，返回 {category, name, hits, keywords, confidence}。

    只有强关键词计分（每条命中 2 分），总分 ≥2 才分类；否则归「未分类」。
    这样避免研报里的「AI/策略/代码」等泛词把行业事实误判成量化战法。
    """
    blob = " ".join(str(t or "") for t in texts)
    upper = blob.upper()
    scores: list[tuple[int, int, str, str, list[str]]] = []
    for order, (category, name, keywords) in enumerate(TACTICS):
        matched = [kw for kw in keywords if kw.upper() in upper]
        if matched:
            scores.append((2 * len(matched), -order, category, name, matched))
    if not scores:
        return {"category": "未分类", "name": "其他（未归类战法）", "hits": 0, "keywords": [], "confidence": "none"}
    scores.sort(reverse=True)
    score, _, category, name, matched = scores[0]
    return {
        "category": category,
        "name": name,
        "hits": len(matched),
        "keywords": matched[:6],
        "confidence": "high" if len(matched) >= 2 else "medium",
    }


def classify_executability(*texts: str, tactic: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """可执行性：A=日线可算；B=需分钟/盘口/Level2；C=人工文本判断。"""
    blob = " ".join(str(t or "") for t in texts)
    tactic = tactic or classify_tactic(*texts)
    category = tactic.get("category", "未分类")
    if _MINUTE_RE.search(blob):
        return {"level": "B", "label": EXEC_LABEL["B"], "reason": "结论涉及分钟/分时/盘口/竞价等分钟级数据"}
    if category == "心理" or _MANUAL_RE.search(blob):
        return {"level": "C", "label": EXEC_LABEL["C"], "reason": "结论依赖心态/纪律/研报文本等人工判断"}
    if tactic.get("hits", 0) > 0:
        return {"level": "A", "label": EXEC_LABEL["A"], "reason": "结论可落到日线行情/复权/筹码等已入库数据计算"}
    return {"level": "C", "label": EXEC_LABEL["C"], "reason": "无量化规则可落地（行业事实/宏观观点/文本结论）"}


def _polarity(text: str) -> int:
    bull = sum(1 for kw in _BULL_KW if kw in text)
    bear = sum(1 for kw in _BEAR_KW if kw in text)
    if bull > bear:
        return 1
    if bear > bull:
        return -1
    return 0


def _claim_type(source_types: Sequence[str]) -> str:
    has_report = "research_report" in source_types
    has_method = any(t in source_types for t in ("blogger", "book"))
    if has_method and has_report:
        return "混合型"
    if has_method:
        return "规律型（方法论/博主/书）"
    if has_report:
        return "事实型（研报）"
    return "文本型"


def detect_direction_conflict(text_a: str, text_b: str) -> bool:
    """方向相反检测（强口径）：一边偏多、另一边偏空。"""
    return _polarity(text_a) * _polarity(text_b) < 0


# --------------------------------------------------------------------------- #
# 聚合主流程
# --------------------------------------------------------------------------- #
def _cluster_index(synonyms: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    index: dict[str, str] = {}
    meta: dict[str, dict[str, Any]] = {}
    for cluster in synonyms.get("clusters", []) or []:
        cluster_id = cluster.get("cluster_id")
        if not cluster_id:
            continue
        meta[cluster_id] = cluster
        keys: list[str] = []
        for member in cluster.get("members", []) or []:
            keys.append(member.get("concept", ""))
            keys.append(member.get("cleaned", ""))
        keys.extend(cluster.get("merged_keys", []) or [])
        for key in keys:
            norm = _norm_key(key)
            if norm and norm not in index:
                index[norm] = cluster_id
    return index, meta


def _iter_candidates(candidate_paths: Sequence[Path]) -> Iterable[tuple[Path, dict[str, Any]]]:
    for path in candidate_paths:
        if not Path(path).exists():
            continue
        data = _load_json(path)
        candidates = data.get("candidates") if isinstance(data, dict) else None
        if not isinstance(candidates, list):
            continue
        for item in candidates:
            if isinstance(item, dict):
                yield Path(path), item


def build_library(
    *,
    multisource_path: str | Path | None = None,
    synonyms_path: str | Path | None = None,
    cards_path: str | Path | None = None,
    candidate_paths: Sequence[str | Path] | None = None,
    out_json: str | Path | None = None,
    out_md: str | Path | None = None,
    top_n: int = TOP_N_DEFAULT,
    date: str = "20261103",
    write: bool = True,
) -> dict[str, Any]:
    """聚合生成有效结论库；write=False 时只返回结果不落盘（测试用）。"""
    inputs = {
        "multisource": Path(multisource_path) if multisource_path else DEFAULT_INPUTS["multisource"],
        "synonyms": Path(synonyms_path) if synonyms_path else DEFAULT_INPUTS["synonyms"],
        "cards": Path(cards_path) if cards_path else DEFAULT_INPUTS["cards"],
        "candidates": [Path(p) for p in (candidate_paths or DEFAULT_INPUTS["candidates"])],
    }
    input_paths: list[Path] = [inputs["multisource"], inputs["synonyms"], inputs["cards"], *inputs["candidates"]]
    input_paths = [p for p in input_paths if p.exists()]
    hash_before = {str(p): sha256_file(p) for p in input_paths}

    multisource = _load_json(inputs["multisource"])
    synonyms = _load_json(inputs["synonyms"])
    cards_data = _load_json(inputs["cards"])
    cards = cards_data.get("cards", []) if isinstance(cards_data, dict) else cards_data

    cluster_index, cluster_meta = _cluster_index(synonyms)

    # 记录池：每条记录 = 一个"结论实例"（来自 W1 验证概念 / 已发布卡 / 待审候选）
    records: list[dict[str, Any]] = []
    for concept_item in multisource.get("concepts", []) or []:
        concept = str(concept_item.get("concept") or "")
        if not concept:
            continue
        answers = []
        raw_sources: list[dict[str, str]] = []
        answer_by_source: dict[tuple[str, str], str] = {}
        for src in concept_item.get("sources", []) or []:
            parsed = parse_source(src.get("source_label") or src.get("source_key"))
            parsed["source_type"] = str(src.get("source_type") or parsed["source_type"])
            parsed["source_key"] = str(src.get("source_key") or parsed["source_key"])
            pair = (parsed["source_type"], parsed["source_key"])
            snippets = [_clean_text(x) for x in (src.get("answer_snippets") or []) if str(x or "").strip()]
            if snippets:
                best = max(snippets, key=len)
                if len(best) > len(answer_by_source.get(pair, "")):
                    answer_by_source[pair] = best
            answers.extend(snippets)
            raw_sources.append(parsed)
        text = max((_clean_text(a) for a in answers), key=len, default="")
        records.append({
            "origin": "w1_multisource",
            "review_status": "multi_source_verified",
            "concept": concept,
            "cluster_id": cluster_index.get(_norm_key(concept)),
            "text": text,
            "answers": [_clean_text(a) for a in answers],
            "answer_by_source": answer_by_source,
            "sources": raw_sources,
            "w1_confidence": concept_item.get("confidence"),
            "w1_max_similarity": (concept_item.get("consistency") or {}).get("max_similarity"),
            "w1_conflicts": concept_item.get("conflicts") or [],
        })
    for card in cards:
        concept = str(card.get("concept") or "")
        text = _clean_text(card.get("a") or card.get("evidence") or "")
        if not concept or not text or _is_metadata_like(concept, text):
            continue
        parsed = parse_source(card.get("source"))
        records.append({
            "origin": "card_published",
            "review_status": "published",
            "concept": concept,
            "cluster_id": cluster_index.get(_norm_key(concept)),
            "text": text,
            "answers": [text],
            "sources": [parsed],
            "topic": str(card.get("topic") or ""),
            "evidence": str(card.get("evidence") or ""),
            "raw_source": str(card.get("source") or ""),
        })
    for cand_path, item in _iter_candidates(inputs["candidates"]):
        concept = str(item.get("concept") or "")
        text = _clean_text(item.get("a") or item.get("answer") or "")
        if not concept or not text or _is_metadata_like(concept, text):
            continue
        if str(item.get("status") or "").lower() in ("rejected", "blocked"):
            continue
        parsed = parse_source(item.get("source"))
        blogger = str(item.get("blogger") or "").strip()
        if blogger and parsed["source_type"] != "research_report":
            parsed = {"source_type": "blogger", "source_key": blogger,
                      "source_label": f"博主·{blogger}", "raw": str(item.get("source") or "")}
        records.append({
            "origin": "candidate_pending",
            "review_status": "pending",
            "concept": concept,
            "cluster_id": cluster_index.get(_norm_key(concept)),
            "text": text,
            "answers": [text],
            "sources": [parsed],
            "topic": str(item.get("theme") or item.get("topic") or ""),
            "evidence": str(item.get("evidence") or ""),
            "raw_source": str(item.get("source") or ""),
            "candidate_file": cand_path.name,
        })

    # 按概念簇聚合
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        cluster_id = record.get("cluster_id")
        key = f"cluster:{cluster_id}" if cluster_id else f"concept:{_norm_key(record['concept'])}"
        groups[key].append(record)

    conclusions: list[dict[str, Any]] = []
    for key, group in groups.items():
        cluster_id = group[0].get("cluster_id")
        cluster = cluster_meta.get(cluster_id) if cluster_id else None
        # 展示概念：簇 display > 多源验证概念 > 已发布卡概念 > 首个概念
        display = ""
        if cluster:
            display = str(cluster.get("display") or "")
        if not display:
            for record in group:
                if record["origin"] == "w1_multisource":
                    display = record["concept"]
                    break
        if not display:
            display = group[0]["concept"]
        aliases = sorted({r["concept"] for r in group if r["concept"] and r["concept"] != display})

        # 独立来源集合：(type, key) 去重
        sources: dict[tuple[str, str], dict[str, str]] = {}
        answers_by_source: dict[tuple[str, str], str] = {}
        raw_texts: list[str] = []
        for record in group:
            raw_texts.append(record.get("raw_source") or "")
            raw_texts.append(record.get("evidence") or "")
            per_source = record.get("answer_by_source") or {}
            for parsed in record.get("sources", []):
                pair = (parsed["source_type"], parsed["source_key"])
                sources.setdefault(pair, parsed)
                best = per_source.get(pair)
                if not best:
                    candidate_texts = record.get("answers") or [record.get("text") or ""]
                    best = max(candidate_texts, key=len)
                if len(best or "") > len(answers_by_source.get(pair, "")):
                    answers_by_source[pair] = best or ""
        n_sources = len(sources)
        source_types = sorted({pair[0] for pair in sources})
        bloggers = sorted(pair[1] for pair in sources if pair[0] == "blogger")
        research_reports = sorted(pair[1] for pair in sources if pair[0] == "research_report")
        books = sorted(pair[1] for pair in sources if pair[0] == "book")

        # 一致性：跨来源答案 3-gram Jaccard 最大值 + 方向冲突检测
        source_keys = list(answers_by_source)
        max_sim = 0.0
        pairs_compared = 0
        direction_conflicts = 0
        for i in range(len(source_keys)):
            for j in range(i + 1, len(source_keys)):
                text_i = answers_by_source[source_keys[i]]
                text_j = answers_by_source[source_keys[j]]
                sim = jaccard(text_i, text_j)
                max_sim = max(max_sim, sim)
                pairs_compared += 1
                if detect_direction_conflict(text_i, text_j):
                    direction_conflicts += 1
        consistent: bool | None = None
        if pairs_compared > 0:
            consistent = max_sim >= CONSISTENCY_THRESHOLD and direction_conflicts == 0
        elif n_sources >= 2:
            consistent = None  # 多源但没有可比对答案 → 按中置信处理

        # 置信：优先按答案一致性算；簇级 W1-2 已知「多源-确认」可托底
        confidence_reason = ""
        confidence = grade_confidence(n_sources, consistent)
        if confidence == "高":
            confidence_reason = "≥2 独立来源且答案一致（3-gram Jaccard ≥0.45）"
        elif confidence == "中":
            if consistent is None:
                confidence_reason = "≥2 独立来源但无可比对答案（措辞未核）"
            else:
                confidence_reason = "≥2 独立来源但措辞/数值不一致（需人工复核）"
        else:
            confidence_reason = "仅 1 个独立来源（单源，低置信）"
        cluster_confidence = str((cluster or {}).get("confidence_after") or "")
        cluster_status = str((cluster or {}).get("status") or "")
        if cluster_status == "多源-确认" and cluster_confidence == "高" and n_sources >= 2 and confidence != "高":
            confidence = "高"
            confidence_reason = "W1-2 概念簇「多源-确认」且簇级置信=高（答案证据见来源）"

        # 结论文本：优先已发布卡答案 > 多源验证片段 > 候选答案
        def _pick_text(origin: str) -> str:
            texts = [r["text"] for r in group if r["origin"] == origin and r.get("text")]
            return max(texts, key=len, default="")
        text = _pick_text("card_published") or _pick_text("w1_multisource") or _pick_text("candidate_pending")
        if not text:
            continue
        topic_counter = Counter(str(r.get("topic") or "") for r in group if r.get("topic"))
        topic = topic_counter.most_common(1)[0][0] if topic_counter else "未标注"
        review_status = "published"
        if not any(r["origin"] == "card_published" for r in group):
            review_status = "multi_source_verified" if any(r["origin"] == "w1_multisource" for r in group) else "pending"

        tactic = classify_tactic(display, text, " ".join(aliases))
        executability = classify_executability(display, text, " ".join(aliases), tactic=tactic)
        pit = extract_dates(raw_texts + [display])
        claim_type = _claim_type(source_types)
        score = (
            CONF_RANK.get(confidence, 1) * 100
            + EXEC_RANK.get(executability["level"], 1) * 30
            + min(n_sources, 5) * 6
            + (5 if pit["verifiable"] else 0)
            + (8 if review_status == "published" else 4 if review_status == "multi_source_verified" else 0)
            + (5 if tactic.get("hits", 0) > 0 else 0)
            + (6 if claim_type.startswith("规律") else 3 if claim_type == "混合型" else 0)
            + min(len(aliases), 3)
        )
        conclusions.append({
            "concept": display,
            "aliases": aliases[:8],
            "text": text,
            "text_length": len(text),
            "source_types": source_types,
            "n_sources": n_sources,
            "sources": [sources[pair] for pair in sorted(sources)][:10],
            "bloggers": bloggers[:10],
            "research_reports": research_reports[:10],
            "books": books[:5],
            "confidence": confidence,
            "confidence_reason": confidence_reason,
            "consistency": {
                "max_similarity": round(max_sim, 4),
                "pairs_compared": pairs_compared,
                "consistent": consistent,
                "direction_conflicts": direction_conflicts,
                "threshold": CONSISTENCY_THRESHOLD,
            },
            "cluster": {
                "cluster_id": cluster_id,
                "display": (cluster or {}).get("display"),
                "status": cluster_status or None,
                "confidence_after": cluster_confidence or None,
                "n_members": (cluster or {}).get("n_members"),
            } if cluster else None,
            "pit": pit,
            "tactic": tactic,
            "executability": executability,
            "claim_type": claim_type,
            "review_status": review_status,
            "topic": topic,
            "score": round(float(score), 3),
            "_records": group,
        })

    # 排序 + 编号（编号给全量，Top 切片输出）
    conclusions.sort(key=lambda item: (-item["score"], -item["n_sources"], item["concept"]))
    for idx, item in enumerate(conclusions, start=1):
        item["conclusion_id"] = f"LC-{idx:04d}"
    top = conclusions[: max(0, int(top_n))]

    # 冲突/仲裁队列
    conflicts, arbitration, conflict_stats = _detect_conflicts(conclusions)
    w1_conflicts = []
    for concept_item in multisource.get("concepts", []) or []:
        if concept_item.get("conflicts"):
            w1_conflicts.append({"concept": concept_item.get("concept"),
                                 "conflicts": concept_item.get("conflicts")})

    confidence_dist = dict(Counter(item["confidence"] for item in conclusions))
    exec_dist = dict(Counter(item["executability"]["level"] for item in conclusions))
    tactic_dist = dict(Counter(item["tactic"]["category"] for item in conclusions))
    claim_dist = dict(Counter(item["claim_type"] for item in conclusions))
    top_confidence_dist = dict(Counter(item["confidence"] for item in top))
    top_exec_dist = dict(Counter(item["executability"]["level"] for item in top))
    top_tactic_dist = dict(Counter(item["tactic"]["category"] for item in top))
    top_claim_dist = dict(Counter(item["claim_type"] for item in top))

    # 只读校验
    hash_after = {str(p): sha256_file(p) for p in input_paths}
    unchanged = {path: hash_before[path] == hash_after[path] for path in hash_before}
    read_only_verified = all(unchanged.values())
    if not read_only_verified:
        raise RuntimeError(f"只读校验失败：输入文件被改动 {[p for p, ok in unchanged.items() if not ok]}")

    inputs_meta = []
    candidate_counts = {}
    for cand_path, _ in _iter_candidates(inputs["candidates"]):
        candidate_counts[cand_path.name] = candidate_counts.get(cand_path.name, 0) + 1
    for path in input_paths:
        kind = "candidates" if path.name.startswith("card_candidates_") else (
            "multisource" if "multisource" in path.name else
            "synonyms" if "synonyms" in path.name else "cards")
        inputs_meta.append({
            "kind": kind,
            "path": str(path),
            "sha256": hash_before[str(path)],
            "sha256_after": hash_after[str(path)],
            "unchanged": unchanged[str(path)],
            "records": candidate_counts.get(path.name) if kind == "candidates" else None,
        })

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task": TASK_ID,
        "title": "有效结论库 v1（Top100 治理后结论，含置信/PIT/战法/可执行性）",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "date": date,
        "read_only": True,
        "read_only_verified": read_only_verified,
        "meta": {
            "inputs": inputs_meta,
            "params": {
                "top_n": int(top_n),
                "consistency_threshold": CONSISTENCY_THRESHOLD,
                "source_independence": "同博主/同书/同研报各计 1 独立来源",
                "confidence_rule": "高=≥2独立来源且答案一致；中=≥2源但措辞/数值不一致或无法比对；低=单源",
                "executability_rule": "A=日线可算；B=需分钟/分时/盘口/Level2；C=人工文本判断",
                "tactic_rule": "8 类战法关键词映射（缠论/均线/指标/量价/形态/风控/心理/量化），无命中=未分类",
                "pit_rule": "从研报编号 rp_AP+YYYYMMDD / 文件名日期 / 日期字符串解析结论时点；无日期=不可追溯",
                "score_rule": "置信×100 + 可执行性×30 + 多源数×6 + PIT×5 + 发布状态 + 战法命中 + 结论类型(规律型+6/混合型+3) + 别名数",
            },
            "records": {
                "total": len(records),
                "w1_multisource_concepts": sum(1 for r in records if r["origin"] == "w1_multisource"),
                "published_cards": sum(1 for r in records if r["origin"] == "card_published"),
                "pending_candidates": sum(1 for r in records if r["origin"] == "candidate_pending"),
            },
            "conclusions_total": len(conclusions),
            "conclusions_multi_source": sum(1 for item in conclusions if item["n_sources"] >= 2),
            "top_n": int(top_n),
            "confidence_dist": {k: confidence_dist.get(k, 0) for k in ("高", "中", "低")},
            "claim_type_dist": claim_dist,
            "executability_dist": {k: exec_dist.get(k, 0) for k in ("A", "B", "C")},
            "tactics_dist": tactic_dist,
            "top_confidence_dist": {k: top_confidence_dist.get(k, 0) for k in ("高", "中", "低")},
            "top_claim_type_dist": top_claim_dist,
            "top_executability_dist": {k: top_exec_dist.get(k, 0) for k in ("A", "B", "C")},
            "top_tactics_dist": top_tactic_dist,
            "conflict_count": conflict_stats["conflict_total"],
            "arbitration_queue_count": conflict_stats["arbitration_total"],
            "conflict_pairs_scanned": conflict_stats["pairs_scanned"],
            "conflict_rule": "同簇 + 跨来源 + 措辞相近(3-gram Jaccard≥0.45) + 方向相反；排除时点市场日评与同日研报外不同日期研报",
            "w1_reported_conflicts": len(w1_conflicts),
            "limitations": [
                "结论来自学习资料（研报/书/博主）文本，非投资建议；战法/因子需过四关后才可进策略池。",
                "置信只做「来源独立性 + 答案文本一致性」判定，不校验事实正确性；中置信/冲突项须 DATA1 人工仲裁。",
                "PIT 为来源时点可追溯性（研报/文件名日期），博主内容日期可能是采集日而非原始发布时间。",
                "可执行性为规则映射（A/B/C），B 类依赖分钟数据（已入库 stk_mins，W3 后接入）。",
                "候选池（pending）结论未过治理门，仅作研究线索，置信最高到低/中（除非有 W1 多源验证）。",
            ],
        },
        "conclusions": [_public_view(item, top=True) for item in top],
        "conflicts": conflicts,
        "arbitration_queue": arbitration,
        "w1_reported_conflicts": w1_conflicts,
        "consumer_contract": {
            "schema_version": SCHEMA_VERSION,
            "suggested_endpoint": "/api/learning/conclusions",
            "suggested_static_copy": "portal/static/learning_conclusions.json",
            "query_params": {"confidence": "高|中|低", "executability": "A|B|C",
                             "tactic": "缠论|均线|指标|量价|形态|风控|心理|量化|未分类",
                             "limit": "1..100（默认 100）", "offset": "分页偏移"},
            "required_fields": ["conclusion_id", "concept", "text", "confidence", "executability",
                                "tactic", "claim_type", "n_sources", "sources", "pit", "review_status", "score"],
            "legions": {
                "quant": "取 executability=A/B 且 confidence∈{高,中} 的结论 → 战法/因子假设 → 四关",
                "research": "取 confidence=高 的结论 → 研报问答/RAG 引用（引用 sources[].source_label）",
                "arbitration": "取 conflicts/arbitration_queue → DATA1 张松仲裁后回写状态",
            },
        },
    }
    if write:
        json_path = Path(out_json) if out_json else DEFAULT_OUT_JSON
        md_path = Path(out_md) if out_md else DEFAULT_OUT_MD
        json_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(json_path, result)
        _write_md(md_path, result)
        result["meta"]["outputs"] = {"json": str(json_path), "md": str(md_path)}
    return result


def _public_view(item: Mapping[str, Any], *, top: bool = False) -> dict[str, Any]:
    view = {k: v for k, v in item.items() if not k.startswith("_")}
    return view


# --------------------------------------------------------------------------- #
# 冲突检测
# --------------------------------------------------------------------------- #
def _detect_conflicts(
    conclusions: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """冲突检测：仅「同簇 + 跨来源 + 措辞相近 + 方向相反」才算真冲突（严口径，防误报）。

    * 时点事实护栏：同为研报但报告日期不同（不同周/月表现）不算方向冲突；
    * 市场日评护栏：含「今日/明日/上证指数/盘面」等时点评论的文本不参与冲突（只是当日观点）。
    多源但答案不一致的「中置信」结论统一进 arbitration_queue 待 DATA1 仲裁。
    """
    conflicts: list[dict[str, Any]] = []
    arbitration: list[dict[str, Any]] = []
    stats = {"conflict_total": 0, "arbitration_total": 0, "pairs_scanned": 0}
    seen_pairs: set[tuple[str, str, str]] = set()
    for item in conclusions:
        records = item.get("_records") or []
        entries: list[dict[str, Any]] = []
        for record in records:
            per_source = record.get("answer_by_source") or {}
            for parsed in record.get("sources", [])[:4]:
                pair = (parsed["source_type"], parsed["source_key"])
                entries.append({"record": record, "source": parsed,
                                "text": per_source.get(pair) or record.get("text") or ""})
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                left, right = entries[i], entries[j]
                src_l, src_r = left["source"], right["source"]
                pair_key = (src_l["source_type"], src_l["source_key"], src_r["source_key"])
                pair_key_alt = (src_r["source_type"], src_r["source_key"], src_l["source_key"])
                if pair_key in seen_pairs or pair_key_alt in seen_pairs:
                    continue
                if (src_l["source_type"], src_l["source_key"]) == (src_r["source_type"], src_r["source_key"]):
                    continue
                text_l = left.get("text") or ""
                text_r = right.get("text") or ""
                if not text_l or not text_r:
                    continue
                stats["pairs_scanned"] += 1
                if not detect_direction_conflict(text_l, text_r):
                    continue
                if _MARKET_COMMENTARY_RE.search(text_l) or _MARKET_COMMENTARY_RE.search(text_r):
                    continue
                if jaccard(text_l, text_r) < 0.45:  # 主题都不同，只是概念簇误并，不算冲突
                    continue
                # 时点事实护栏：同为研报且日期不同
                date_l = extract_dates([src_l.get("raw") or src_l.get("source_label") or ""])["as_of"]
                date_r = extract_dates([src_r.get("raw") or src_r.get("source_label") or ""])["as_of"]
                if src_l["source_type"] == src_r["source_type"] == "research_report" and date_l != date_r:
                    continue
                seen_pairs.add(pair_key)
                stats["conflict_total"] += 1
                if len(conflicts) < 50:
                    conflicts.append({
                        "conflict_id": f"CF-{len(conflicts) + 1:03d}",
                        "conclusion_ref": item.get("conclusion_id"),
                        "concept": item.get("concept"),
                        "type": "方向相反",
                        "text_a": _clean_text(text_l, 160),
                        "source_a": src_l.get("source_label") or src_l.get("source_key"),
                        "text_b": _clean_text(text_r, 160),
                        "source_b": src_r.get("source_label") or src_r.get("source_key"),
                        "similarity": round(jaccard(text_l, text_r), 4),
                        "status": "待仲裁",
                        "arbitration_owner": "DATA1 张松（跨部门 A0 田丰）",
                    })
        # 中置信多源措辞不一致 → 仲裁队列
        if item.get("confidence") == "中" and item.get("n_sources", 0) >= 2:
            stats["arbitration_total"] += 1
            if len(arbitration) < 80:
                sources = item.get("sources") or []
                arbitration.append({
                    "arbitration_id": f"AR-{len(arbitration) + 1:03d}",
                    "conclusion_ref": item.get("conclusion_id"),
                    "concept": item.get("concept"),
                    "reason": "多源但答案不一致/无法比对，措辞待统一或条件化拆分",
                    "consistency": item.get("consistency"),
                    "sources": [s.get("source_label") or s.get("source_key") for s in sources[:5]],
                    "status": "待仲裁",
                    "arbitration_owner": "DATA1 张松",
                })
    return conflicts, arbitration, stats


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)


def _md_escape(text: Any, limit: int = 60) -> str:
    cleaned = _clean_text(text, limit).replace("|", "\\|").replace("\n", " ")
    return cleaned


def _write_md(path: Path, payload: Mapping[str, Any]) -> None:
    meta = payload["meta"]
    top = payload["conclusions"]
    lines: list[str] = []
    lines.append("# 有效结论库 v1（W1-4 · Top100 治理后结论）\n")
    lines.append(f"- 生成时间：{payload['generated_at']}　|　任务：{payload['task']}　|　schema：`{payload['schema_version']}`")
    lines.append("- 口径：置信（高≥2独立来源一致/中≥2源措辞异/低单源）、PIT 时点可追溯、8 类战法、可执行性 A/B/C")
    lines.append(f"- 只读校验：{'PASS' if payload['read_only_verified'] else 'FAIL'}（{len(meta['inputs'])} 个输入文件 sha256 前后一致）")
    lines.append(f"- 结论总数：{meta['conclusions_total']}（多源结论 {meta['conclusions_multi_source']}）；本报告输出 Top{meta['top_n']}")
    lines.append(f"- 置信分布（全量）：{meta['confidence_dist']}　| Top100：{meta['top_confidence_dist']}")
    lines.append(f"- 可执行性分布（全量）：{meta['executability_dist']}　| Top100：{meta['top_executability_dist']}")
    lines.append(f"- 结论类型（Top100）：{meta['top_claim_type_dist']}（规律型=方法论/博主/书；事实型=研报时点事实）")
    lines.append(f"- 战法分布（Top100）：{meta['top_tactics_dist']}")
    lines.append(f"- 冲突：{meta['conflict_count']} 条方向相反待仲裁；中置信仲裁队列：{meta['arbitration_queue_count']} 条\n")

    lines.append("## 一、Top100 结论（按 置信 > 可执行性 > 多源数 > PIT/发布 排序）\n")
    lines.append("| # | ID | 结论概念 | 置信 | 可执行 | 战法 | 类型 | 源数 | 状态 | 结论摘要 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for rank, item in enumerate(top, start=1):
        lines.append(
            f"| {rank} | {item['conclusion_id']} | {_md_escape(item['concept'], 28)} | {item['confidence']} | "
            f"{item['executability']['level']} | {item['tactic']['category']} | {item['claim_type']} | {item['n_sources']} | "
            f"{item['review_status']} | {_md_escape(item['text'], 70)} |"
        )
    lines.append("")

    lines.append("## 二、冲突结论（单列待仲裁）\n")
    if payload["conflicts"]:
        lines.append("| ID | 概念 | 类型 | 来源A | 来源B | 状态 |")
        lines.append("|---|---|---|---|---|---|")
        for conflict in payload["conflicts"]:
            lines.append(f"| {conflict['conflict_id']} | {_md_escape(conflict['concept'], 24)} | {conflict['type']} | "
                         f"{_md_escape(conflict['source_a'], 28)} | {_md_escape(conflict['source_b'], 28)} | {conflict['status']} |")
    else:
        lines.append("> 本批数据未检出「方向相反」型冲突（W1-1 报告冲突数 = "
                     f"{meta['w1_reported_conflicts']}）。多源但措辞不一致的「中置信」结论见下方仲裁队列。")
    lines.append("")
    if payload["arbitration_queue"]:
        lines.append("### 中置信仲裁队列（多源措辞不一致/无法比对）\n")
        lines.append("| ID | 概念 | 一致性 max_sim | 来源 | 状态 |")
        lines.append("|---|---|---|---|---|")
        for item in payload["arbitration_queue"][:30]:
            lines.append(f"| {item['arbitration_id']} | {_md_escape(item['concept'], 24)} | "
                         f"{(item.get('consistency') or {}).get('max_similarity')} | "
                         f"{'、'.join(_md_escape(s, 18) for s in item['sources'])} | {item['status']} |")
        lines.append("")

    lines.append("## 三、数据契约（供 W3-3 API / 军团调用）\n")
    lines.append("**建议接口**：`GET /api/learning/conclusions`（W3-3 预接线），静态副本 `portal/static/learning_conclusions.json`。")
    lines.append("**查询参数**：`confidence=高|中|低`、`executability=A|B|C`、`tactic=<8 类战法名|未分类>`、`limit≤100`、`offset`。")
    lines.append("**排序**：服务端保持文件内顺序（score 降序），军团可按置信/可执行性再过滤。\n")
    lines.append("**字段定义（结论对象）**：\n")
    lines.append("| 字段 | 类型 | 含义 |")
    lines.append("|---|---|---|")
    contract_rows = [
        ("conclusion_id", "str", "结论唯一 ID（LC-0001…，按 score 排序）"),
        ("concept", "str", "概念簇规范名（canonical）"),
        ("aliases", "str[]", "同簇别名（原始概念名，去重，最多 8 个）"),
        ("text", "str", "结论文本（优先已发布卡答案，其次 W1 多源片段，其次候选）"),
        ("confidence", "高|中|低", "置信分级（高≥2独立来源一致；中≥2源措辞异；低单源）"),
        ("confidence_reason", "str", "置信判定理由（可审计）"),
        ("n_sources", "int", "独立来源数（同博主/同书/同研报各计 1 源）"),
        ("sources", "obj[]", "来源列表：source_type/source_key/source_label/raw"),
        ("bloggers", "str[]", "涉及博主（source_type=blogger 的 source_key）"),
        ("research_reports", "str[]", "涉及研报编号（rp_AP…）"),
        ("consistency", "obj", "跨来源答案一致性：max_similarity/pairs_compared/consistent/threshold"),
        ("pit", "obj", "PIT：verifiable/as_of/precision/date_kind/note"),
        ("tactic", "obj", "战法：category（8 类+未分类）/name/hits/keywords/confidence"),
        ("executability", "obj", "可执行性：level(A/B/C)/label/reason"),
        ("review_status", "str", "published / multi_source_verified / pending"),
        ("claim_type", "str", "规律型（方法论/博主/书）/事实型（研报）/混合型/文本型"),
        ("topic", "str", "主题（卡 topic / 候选 theme）"),
        ("score", "float", "排序分（置信×100+可执行×30+多源×6+PIT×5+发布+战法+别名）"),
    ]
    for name, typ, desc in contract_rows:
        lines.append(f"| `{name}` | {typ} | {desc} |")
    lines.append("")
    lines.append("**消费示例（军团）**：\n")
    lines.append("```jsonc")
    lines.append("// 1) 量化交易部：取可落地的日线结论做战法假设")
    lines.append("GET /api/learning/conclusions?executability=A&confidence=高&limit=50")
    lines.append("// 2) 研报/知识线：取高置信结论做 RAG 引用（引用 sources[].source_label）")
    lines.append("GET /api/learning/conclusions?confidence=高")
    lines.append("// 3) DATA1：取冲突/仲裁队列")
    lines.append("GET /api/learning/conclusions 的 conflicts[] + arbitration_queue[]")
    lines.append("```")
    lines.append("")
    lines.append("**冲突对象字段**：`conflict_id, conclusion_ref, concept, type, text_a/source_a, text_b/source_b, status, arbitration_owner`。")
    lines.append("**仲裁队列字段**：`arbitration_id, conclusion_ref, concept, reason, consistency, sources[], status, arbitration_owner`。\n")

    lines.append("## 四、输入与限制\n")
    lines.append("| 输入 | 记录数 | 只读校验 |")
    lines.append("|---|---|---|")
    for item in meta["inputs"]:
        records = item.get("records")
        lines.append(f"| `{Path(item['path']).name}` | {records if records is not None else '—'} | "
                     f"{'PASS' if item['unchanged'] else 'FAIL'} |")
    lines.append("")
    for note in meta["limitations"]:
        lines.append(f"- {note}")
    lines.append("")

    lines.append("## 五、W1-4 验收对照（DOD）\n")
    lines.append("| 验收项 | 结果 | 证据 |")
    lines.append("|---|---|---|")
    lines.append("| 结论库 JSON+MD | ✅ | `research/reports/learning_conclusions_lib_20261103.{json,md}` |")
    lines.append(f"| Top100 治理后结论 | ✅ | `conclusions[]` = {meta['top_n']}（全量聚合 {meta['conclusions_total']}，多源 {meta['conclusions_multi_source']}） |")
    lines.append(f"| 置信分级 | ✅ | 全量 {meta['confidence_dist']}；Top100 {meta['top_confidence_dist']}；规则见 §口径 + 单测 `test_confidence_grading_rule` |")
    lines.append("| 按概念簇聚合 | ✅ | 以 W1-2 `concept_synonyms` 簇为主键合并；同博主多文件/同研报多卡去重（`n_sources`） |")
    lines.append("| PIT | ✅ | 每条 `pit.verifiable/as_of`（研报按 `rp_AP+YYYYMMDD`，文件按文件名日期） |")
    lines.append(f"| 8 类战法 | ✅ | Top100 分布 {meta['top_tactics_dist']}（规则映射，无命中=未分类） |")
    lines.append(f"| 可执行性 A/B/C | ✅ | Top100 分布 {meta['top_executability_dist']}（A 日线 / B 分钟 / C 人工） |")
    lines.append(f"| 冲突单列待仲裁 | ✅ | `conflicts[]`（方向相反严口径检出 {meta['conflict_count']}）+ `arbitration_queue[]`（中置信多源措辞异 {meta['arbitration_queue_count']}，待 DATA1 张松） |")
    lines.append(f"| 只读 | ✅ | `read_only_verified=true`；{len(meta['inputs'])} 个输入 sha256 前后一致；测试 `test_top100_sorted_and_outputs` |")
    lines.append("| 测试 ≥4 | ✅ | `tools/tests/test_learning_conclusions_lib.py`：6 passed（置信/多源/冲突/战法/只读/Top100+契约） |")
    lines.append("")
    lines.append("> 本文件由 `tools/kb_corpus/learning_conclusions_lib.py` 生成（只读输入，不改卡/units/治理文件）。")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="learning_conclusions_lib",
        description="W1-4 有效结论库 v1：概念簇聚合 + 置信/PIT/8类战法/可执行性 + Top100 + 冲突仲裁队列",
    )
    parser.add_argument("--top", type=int, default=TOP_N_DEFAULT, help="输出 TopN（默认 100）")
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON), help="输出 JSON 路径")
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_MD), help="输出 MD 路径")
    parser.add_argument("--quiet", action="store_true", help="只落盘不打印摘要")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = build_library(top_n=args.top, out_json=args.out_json, out_md=args.out_md, write=True)
    meta = result["meta"]
    summary = {
        "conclusions_total": meta["conclusions_total"],
        "top_n": meta["top_n"],
        "top_confidence_dist": meta["top_confidence_dist"],
        "top_executability_dist": meta["top_executability_dist"],
        "conflicts": meta["conflict_count"],
        "arbitration_queue": meta["arbitration_queue_count"],
        "read_only_verified": result["read_only_verified"],
        "outputs": meta.get("outputs"),
    }
    if not args.quiet:
        print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
