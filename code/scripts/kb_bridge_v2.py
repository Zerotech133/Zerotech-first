# -*- coding: utf-8 -*-
"""kb_bridge_v2：知识库中文/缠论检索只读增强层（v1 → v2 同题对比）。

背景
----
v1（scripts/kb_bridge.py）只用「英文词 + 中文 bigram 覆盖率」打分，中文查询命中差：
中文 golden 11 条里多数 0 命中或仅 0.14~0.33 的弱命中。本脚本是只读增强层：
中文关键词/词典 + bigram、繁体→简体归一、标题/短语加权、中文术语→英文术语映射。

只读约束（硬性）
----------------
- 不写 DB、不写 v2_stock_research、不改知识库任何文件；
- 不解析 embedding、不建向量索引、不跑模型；只读 196 docs + 4926 chunks 两个 JSON；
- 唯一写出物是 eval JSON（默认 C:\\Temp\\kb_bridge_v2_eval.json），且禁止写到知识库目录下；
- 不生成投资结论，检索结果只作方法/风险参考。

用法
----
  python scripts/kb_bridge_v2.py "缠论 中枢 三买" -k 5 --chunks
  python scripts/kb_bridge_v2.py "mean reversion" -k 5 --json
  python scripts/kb_bridge_v2.py --eval --out C:\\Temp\\kb_bridge_v2_eval.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

VERSION = "kb_bridge_v2/1.0"
SCRIPT = "scripts/kb_bridge_v2.py"

KB = Path(r"C:\项目\知识库")
LITE = KB / "40_索引" / "kb_lite_index.json"
CORPUS = KB / "50_RAG" / "rag_index_corpus.json"
DOCS_DIR = KB / "50_RAG" / "docs"
DEFAULT_OUT = Path(r"C:\Temp\kb_bridge_v2_eval.json")
BASELINE_REF = Path(r"C:\Temp\acad1_kb_refs.json")

EXPECTED_DOCS = 196
EXPECTED_CHUNKS = 4926

# 11 条中文 golden 查询：直接取自 P2-6 kb_enrich 基线（lang=zh 的 11 条），
# 保证 v1/v2 同题对比；任务描述列举的 均值回归/缠论中枢三买/趋势动量/大盘环境/回测过拟合
# 均在其中或其子集。
GOLDEN_QUERIES = [
    {"id": "zh-01", "query": "A股 市场 结构", "topic": "market"},
    {"id": "zh-02", "query": "加密货币 波动", "topic": "market"},
    {"id": "zh-03", "query": "港股 市场", "topic": "market"},
    {"id": "zh-04", "query": "美股 市场", "topic": "market"},
    {"id": "zh-05", "query": "RSI 超卖 反转", "topic": "method"},
    {"id": "zh-06", "query": "均值回归 超跌 反转", "topic": "method"},
    {"id": "zh-07", "query": "大盘 环境 过滤 风控", "topic": "method"},
    {"id": "zh-08", "query": "缠论 中枢 三买 风险", "topic": "method"},
    {"id": "zh-09", "query": "趋势 动量 跟踪", "topic": "method"},
    {"id": "zh-10", "query": "止损 仓位 风险管理", "topic": "risk"},
    {"id": "zh-11", "query": "回测 过拟合", "topic": "validation"},
]

# 补充探针：任务描述里的短问法（不计入 11 条 headline，仅作稳健性参考）。
SUPPLEMENTARY_QUERIES = ["均值回归", "缠论 中枢 三买", "趋势 动量", "大盘 环境", "回测 过拟合"]

# 繁体→简体映射（金融/交易语料常用字，人工薄表；非完整 OpenCC。
# 只用于「查询侧 + 语料侧」统一到简体，从而让 市場/風險/回測 等繁体原文可被简体查询命中。）
_T2S_SOURCE = (
    "這这 說说 們们 個个 來来 時时 為为 會会 對对 開开 關关 買买 賣卖 點点 線线 圖图 術术 "
    "標标 題题 讚赞 評评 論论 價价 報报 單单 於于 還还 進进 過过 實实 現现 發发 場场 風风 "
    "險险 動动 態态 勢势 趨趋 測测 擬拟 損损 倉仓 盤盘 環环 濾滤 轉转 蹤踪 縱纵 規规 則则 "
    "數数 據据 學学 習习 資资 產产 業业 績绩 營营 銷销 總总 體体 統统 計计 劃划 畫画 復复 "
    "雜杂 簡简 確确 認认 證证 驗验 樣样 讀读 寫写 碼码 網网 頁页 軟软 應应 該该 將将 從从 "
    "麼么 區区 間间 問问 結结 貨货 幣币 虛虚 漲涨 準准 備备 決决 層层 級级 階阶 樞枢 紐纽 "
    "纏缠 馳驰 撐撑 壓压 檔档 歷历 紀纪 錄录 攤摊 穩稳 審审 費费 潤润 虧亏 債债 銀银 黃黄 "
    "銅铜 賺赚 賠赔 運运 條条 側侧 雙双 詢询 閱阅 補补 錢钱 儲储 換换 匯汇 稅税 勝胜 負负 "
    "頂顶 裡里 裏里 邊边"
)
T2S = {pair[0]: pair[1] for pair in _T2S_SOURCE.split()}
T2S_TABLE = str.maketrans(T2S)

CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9_+]*")
EN_SPLIT = re.compile(r"[^a-z0-9]+")

EN_STOP = frozenset(
    "the and for with from that this these those are was were has have had not but you your our "
    "its can will would should could may might must into over under about than then there their "
    "them they what when where which who why how all any each other some such only also more most "
    "much many very just like well back after before between during through because while whereas "
    "however therefore thus via per off out up down new one two three first last next best top use "
    "using used get got make made need needs want wants see seen look looks know knows "
    "is it to in on of at as by or an be do does did if so no".split()
)

# 中文领域词典 → 英文术语映射。权重：1.0=首选等价词，0.5~0.8=邻近/部分等价。
# 说明：知识库本体是英文量化频道语料，映射是「跨语言邻近概念」检索，不等于知识库有中文原词。
GAZETTEER: dict[str, list[tuple[str, float]]] = {
    # 缠论/结构
    "缠论": [("chan theory", 0.9), ("chanlun", 0.8), ("pivot", 0.5),
             ("support resistance", 0.5), ("breakout", 0.5)],
    "中枢": [("pivot", 0.7), ("consolidation", 0.6), ("central zone", 0.5),
             ("trading range", 0.4)],
    "三买": [("third buy", 0.7), ("breakout", 0.5), ("buy signal", 0.5)],
    "背驰": [("divergence", 0.8)],
    "买点": [("buy signal", 0.8), ("entry", 0.6)],
    "卖点": [("sell signal", 0.8), ("exit", 0.6)],
    "均线": [("moving average", 0.9), ("ema", 0.6), ("sma", 0.6)],
    # 趋势/动量/反转
    "趋势": [("trend", 0.9), ("trend following", 0.6)],
    "动量": [("momentum", 0.9)],
    "跟踪": [("following", 0.6), ("tracking", 0.5)],
    "均值回归": [("mean reversion", 0.9), ("reversion", 0.6)],
    "超跌": [("oversold", 0.8), ("sell off", 0.5)],
    "超卖": [("oversold", 0.9)],
    "超买": [("overbought", 0.9)],
    "反转": [("reversal", 0.8), ("reversion", 0.5)],
    "突破": [("breakout", 0.9)],
    "支撑": [("support", 0.9)],
    "阻力": [("resistance", 0.9)],
    "波动": [("volatility", 0.9), ("volatile", 0.6)],
    "波动率": [("volatility", 0.9)],
    # 大盘/环境
    "大盘": [("market index", 0.7), ("market", 0.6), ("regime", 0.5)],
    "环境": [("regime", 0.7), ("market condition", 0.6), ("environment", 0.5)],
    "过滤": [("filter", 0.9)],
    "风控": [("risk control", 0.8), ("risk management", 0.9)],
    "风险": [("risk", 0.8), ("risk management", 0.5)],
    "风险管理": [("risk management", 0.9), ("position sizing", 0.5)],
    # 仓位/风控
    "止损": [("stop loss", 0.9)],
    "仓位": [("position sizing", 0.9), ("position size", 0.8)],
    "回撤": [("drawdown", 0.9)],
    "杠杆": [("leverage", 0.9)],
    "对冲": [("hedging", 0.9), ("hedge", 0.8)],
    "套利": [("arbitrage", 0.9)],
    "滑点": [("slippage", 0.9)],
    "手续费": [("commission", 0.7), ("fee", 0.5)],
    "胜率": [("win rate", 0.9)],
    "期望": [("expectancy", 0.8), ("expected value", 0.6)],
    "资金管理": [("money management", 0.8), ("position sizing", 0.6)],
    "交易心理": [("trading psychology", 0.9)],
    # 回测/验证
    "回测": [("backtest", 0.9), ("backtesting", 0.8)],
    "过拟合": [("overfitting", 0.9), ("overfit", 0.8), ("curve fitting", 0.7),
               ("curve fit", 0.6)],
    "样本外": [("out of sample", 0.9)],
    "前视偏差": [("look ahead bias", 0.9)],
    "未来函数": [("look ahead bias", 0.8)],
    "验证": [("validation", 0.7), ("walk forward", 0.6)],
    # 市场
    "市场": [("market", 0.8), ("market structure", 0.5)],
    "结构": [("structure", 0.8), ("market structure", 0.6)],
    "港股": [("hong kong stock market", 0.9), ("hong kong", 0.8), ("hsi", 0.5)],
    "美股": [("us stock market", 0.9), ("us stocks", 0.8), ("s&p 500", 0.6),
             ("nasdaq", 0.5)],
    "a股": [("china a share", 0.9), ("a share", 0.8), ("chinese stock market", 0.8),
            ("china stock market", 0.7)],
    "加密货币": [("cryptocurrency", 0.9), ("crypto", 0.8)],
    "比特币": [("bitcoin", 0.9)],
    "股票": [("stock", 0.8), ("stocks", 0.7)],
    "期货": [("futures", 0.8)],
    "台指期": [("taiwan index futures", 0.8), ("taiwan futures", 0.7)],
    # 基础
    "策略": [("strategy", 0.8)],
    "交易": [("trading", 0.7)],
    "指标": [("indicator", 0.8)],
    "信号": [("signal", 0.8)],
    "量化": [("quantitative", 0.7), ("quant", 0.6)],
    "选股": [("stock selection", 0.8), ("screening", 0.7)],
    "入场": [("entry", 0.8)],
    "出场": [("exit", 0.8)],
}
PHRASE_SET = {phrase for maps in GAZETTEER.values() for phrase, _ in maps if " " in phrase}

# 语料中文结构标签（用于统计“真正的中文内容”时剔除）。
STRUCT_LABELS = ("标题：", "描述：", "高赞评论", "评论", "视频", "简介")

# v1 原样算法：scripts/kb_bridge.py 的 token 提取（英文 2+ 词 + 中文 bigram）。
V1_EN = re.compile(r"[a-z0-9_]{2,}")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_cjk(ch: str) -> bool:
    return "\u3400" <= ch <= "\u9fff"


def _v1_tokens(text: str) -> set[str]:
    """与 scripts/kb_bridge.py 完全一致的 v1 token 集。"""
    text = (text or "").lower()
    words = set(V1_EN.findall(text))
    words |= {
        text[i:i + 2]
        for i in range(max(0, len(text) - 1))
        if "\u4e00" <= text[i] <= "\u9fff"
    }
    return words


def _en_norm(text: str) -> str:
    """英文归一：小写、连字符/下划线/斜杠变空格，保留数字（s&p 500 → s p 500 不行，保留 &）。"""
    return EN_SPLIT.sub(" ", (text or "").lower()).strip()


def _en_tokens_set(norm: str) -> set[str]:
    return {w for w in norm.split() if w}


def _zh_runs(text: str) -> list[str]:
    """抽中文片段并转简体（保留词内顺序，不跨标点拼接）。"""
    runs = CJK_RUN.findall(text or "")
    return [run.translate(T2S_TABLE) for run in runs]


# ---------------------------------------------------------------------------
# 语料加载（只读，只读两个 JSON，不解析 embedding 使用）
# ---------------------------------------------------------------------------
def load_corpus() -> dict:
    t0 = time.perf_counter()
    lite = json.loads(LITE.read_text(encoding="utf-8")) if LITE.exists() else {}
    docs = lite.get("docs", [])
    corpus = json.loads(CORPUS.read_text(encoding="utf-8")) if CORPUS.exists() else {}
    chunks = corpus.get("records", [])
    elapsed = time.perf_counter() - t0
    return {
        "docs": docs,
        "chunks": chunks,
        "load_seconds": round(elapsed, 3),
        "lite_generated_at": lite.get("generated_at"),
        "corpus_updated_at": corpus.get("updated_at"),
        "corpus_version": corpus.get("version"),
        "corpus_index_version": corpus.get("index_version"),
    }


def _chunk_titles(docs: list[dict], chunks: list[dict]) -> dict[str, str]:
    """chunk source → 标题：优先取 chunk index=0 的 markdown 首行，其次 docs video_id 映射。"""
    by_video = {}
    for d in docs:
        if d.get("kind") == "video" and d.get("video_id"):
            by_video[d["video_id"]] = d.get("title") or ""
    titles: dict[str, str] = {}
    for rec in chunks:
        src = rec.get("source") or ""
        if not src or src in titles or str(rec.get("index")) != "0":
            continue
        text = (rec.get("text") or "").lstrip()
        first = text.splitlines()[0] if text else ""
        if first.startswith("# "):
            titles[src] = first[2:].strip()
    for rec in chunks:
        src = rec.get("source") or ""
        if not src or src in titles:
            continue
        titles[src] = by_video.get(Path(src).stem, "")
    return titles


def _prepare_units(corpus: dict) -> dict:
    """把 docs/chunks 预处理成 scoring unit：英文归一文本 + 中文简体片段 + 标题特征。"""
    docs = corpus["docs"]
    chunks = corpus["chunks"]
    title_map = _chunk_titles(docs, chunks)

    def make_unit(layer: str, text: str, title: str, meta: dict) -> dict:
        en_norm = _en_norm(text)
        t_norm = _en_norm(title)
        return {
            "layer": layer,
            "text": text or "",
            "title": title or "",
            "low": (text or "").lower(),
            "t_low": (title or "").lower(),
            "en_norm": en_norm,
            "en_padded": " " + en_norm + " ",
            "en_words": _en_tokens_set(en_norm),
            "t_norm": t_norm,
            "t_padded": " " + t_norm + " ",
            "t_words": _en_tokens_set(t_norm),
            "runs": _zh_runs(text),
            "t_runs": _zh_runs(title),
            "meta": meta,
        }

    doc_units = [
        make_unit(
            "docs",
            d.get("text") or "",
            d.get("title") or "",
            {
                "title": d.get("title") or "",
                "video_id": d.get("video_id") or "",
                "channel_id": d.get("channel_id") or "",
                "upload_date": d.get("upload_date") or "",
                "kind": d.get("kind") or "",
                "source": str(LITE),
            },
        )
        for d in docs
    ]
    chunk_units = [
        make_unit(
            "chunks",
            rec.get("text") or "",
            title_map.get(rec.get("source") or "", ""),
            {
                "title": title_map.get(rec.get("source") or "", ""),
                "video_id": "",
                "channel_id": "",
                "upload_date": "",
                "kind": "comment_section"
                if "## 高赞评论" in (rec.get("text") or "")
                else "video_chunk",
                "source": rec.get("source") or "",
                "document_hash": rec.get("document_hash") or "",
                "index": rec.get("index"),
            },
        )
        for rec in chunks
    ]
    return {"docs": doc_units, "chunks": chunk_units, "chunk_titles": title_map}


# ---------------------------------------------------------------------------
# 查询解析：中文词典词 / bigram + 英文词 + 中文→英文术语映射
# ---------------------------------------------------------------------------
def _mk_term(comp: str, surface: str, weight: float, extra: dict | None = None) -> dict:
    term = {"comp": comp, "surface": surface, "weight": float(weight)}
    if extra:
        term.update(extra)
    return term


def parse_query(query: str) -> dict:
    """解析查询为术语列表。comp ∈ native(=中文原生)/mapped(=映射英文)/en(=英文原词)。"""
    raw = (query or "").strip()
    q_simp = raw.translate(T2S_TABLE)
    q_low = q_simp.lower()
    covered = [False] * len(q_low)
    terms: list[dict] = []
    native_surfaces: list[str] = []
    mapped_weights: dict[str, float] = {}

    # 1) 领域词典，长词优先，不重叠匹配
    for gterm in sorted(GAZETTEER, key=lambda x: (-len(x), x)):
        start = 0
        while True:
            idx = q_low.find(gterm, start)
            if idx < 0:
                break
            span = range(idx, idx + len(gterm))
            if not any(covered[j] for j in span):
                for j in span:
                    covered[j] = True
                native_surfaces.append(gterm)
                if all(_is_cjk(ch) for ch in gterm):
                    terms.append(_mk_term("native", gterm, 1.0,
                                          {"kind": "zh", "zh": gterm,
                                           "phrase": len(gterm) >= 3}))
                else:  # 混合词（a股）
                    pat = re.compile(
                        r"(?<![a-z0-9])" + r"\s*".join(re.escape(p) for p in gterm.split())
                        + r"(?![a-z0-9])"
                    )
                    terms.append(_mk_term("native", gterm, 1.0,
                                          {"kind": "mixed", "re": pat,
                                           "phrase": len(gterm) >= 3}))
                for en, w in GAZETTEER[gterm]:
                    mapped_weights[en] = max(mapped_weights.get(en, 0.0), w)
            start = idx + 1

    # 2) 未覆盖的连续中文片段 → bigram（v1 的翻车点：跨空格/标点的假 bigram 全部丢弃）
    i = 0
    while i < len(q_low):
        if covered[i] or not _is_cjk(q_low[i]):
            i += 1
            continue
        j = i
        while j < len(q_low) and not covered[j] and _is_cjk(q_low[j]):
            j += 1
        run = q_low[i:j]
        for p in range(len(run) - 1):
            bg = run[p:p + 2]
            terms.append(_mk_term("native", bg, 0.55,
                                  {"kind": "zh", "zh": bg, "phrase": False}))
            for en, w in GAZETTEER.get(bg, []):
                mapped_weights[en] = max(mapped_weights.get(en, 0.0), w * 0.8)
        i = j

    # 3) 英文原词（未被中文词典覆盖的位置）+ 已识别英文短语
    direct_en: set[str] = set()
    word_positions = [(m.start(), m.end(), m.group(0)) for m in ASCII_WORD.finditer(q_low)]
    for start, end, word in word_positions:
        if any(covered[start:end]):
            continue
        if len(word) < 2 or word in EN_STOP:
            continue
        if word in direct_en:
            continue
        direct_en.add(word)
        terms.append(_mk_term("en", word, 1.0, {"kind": "en", "word": word,
                                                "phrase": False}))
    seen_pairs = set()
    for a, b in zip(word_positions, word_positions[1:]):
        if a[1] != b[0]:
            continue
        pair = f"{a[2]} {b[2]}"
        if pair in PHRASE_SET and pair not in seen_pairs:
            seen_pairs.add(pair)
            terms.append(_mk_term("en", pair, 0.8, {"kind": "en", "word": pair,
                                                    "phrase": True}))

    # 4) 映射英文术语（去重取最大权重；与查询里已有的英文原词重复则跳过）
    for en, w in sorted(mapped_weights.items(), key=lambda kv: (-kv[1], kv[0])):
        norm = " ".join(_en_token_list(en))
        if not norm:
            continue
        if norm in direct_en or any(
            norm in t["surface"] or t["surface"] in norm for t in terms
            if t["comp"] == "en"
        ):
            continue
        terms.append(_mk_term("mapped", en, w,
                              {"kind": "en", "word": norm,
                               "phrase": " " in norm}))

    for idx, term in enumerate(terms):
        term["index"] = idx
    return {
        "raw": raw,
        "normalized": q_simp,
        "terms": terms,
        "native_surfaces": native_surfaces,
        "mapped_surfaces": [t["surface"] for t in terms if t["comp"] == "mapped"],
        "en_surfaces": [t["surface"] for t in terms if t["comp"] == "en"],
    }


def _en_token_list(phrase: str) -> list[str]:
    return [w for w in EN_SPLIT.split(phrase.lower()) if w]


# ---------------------------------------------------------------------------
# v2 匹配 / 打分
# ---------------------------------------------------------------------------
def _match_unit(unit: dict, terms: list[dict]) -> tuple[list[int], list[int]]:
    """返回 (命中的 term index, 其中标题也命中的 term index)。"""
    matched: list[int] = []
    title_matched: list[int] = []
    for t in terms:
        kind = t["kind"]
        if kind == "zh":
            zh = t["zh"]
            ok = any(zh in run for run in unit["runs"])
            if not ok:
                continue
            matched.append(t["index"])
            if any(zh in run for run in unit["t_runs"]):
                title_matched.append(t["index"])
        elif kind == "mixed":
            if not t["re"].search(unit["low"]):
                continue
            matched.append(t["index"])
            if t["re"].search(unit["t_low"]):
                title_matched.append(t["index"])
        else:  # 英文原词/映射短语
            word = t["word"]
            if " " in word:
                ok = word in unit["en_padded"]
            else:
                ok = word in unit["en_words"]
            if not ok:
                continue
            matched.append(t["index"])
            if (" " in word and word in unit["t_padded"]) or (
                " " not in word and word in unit["t_words"]
            ):
                title_matched.append(t["index"])
    return matched, title_matched


def _idf(term_df: int, n_units: int) -> float:
    return math.log(1.0 + n_units / (1.0 + term_df))


def _layer_stats(
    units: list[dict],
    terms: list[dict],
    idf: list[float],
    k: int,
) -> dict:
    denoms = {"native": 0.0, "mapped": 0.0, "en": 0.0}
    for t in terms:
        denoms[t["comp"]] += t["weight"] * idf[t["index"]]

    has_native = denoms["native"] > 0
    has_mapped = denoms["mapped"] > 0
    has_en = denoms["en"] > 0
    weights: dict[str, float] = {}
    native_bonus = 0.02
    if has_en:
        weights["en"] = 0.40
        if has_native:
            weights["native"] = 0.30
        if has_mapped:
            weights["mapped"] = 0.30
    else:
        # 中文查询：中文原生命中 0.5 + 英文映射命中 0.5（各按自身术语空间归一），
        # 原生命中额外 +0.02 加成，避免弱中文证据被英文映射完全压过。
        if has_native:
            weights["native"] = 0.50
        if has_mapped:
            weights["mapped"] = 0.50
    if not has_native:
        native_bonus = 0.0
    wsum = sum(weights.values()) or 1.0

    scored: list[dict] = []
    positive = 0
    for unit in units:
        matched, title_matched = _match_unit(unit, terms)
        if not matched:
            continue
        positive += 1
        nums = {"native": 0.0, "mapped": 0.0, "en": 0.0}
        matched_w = 0.0
        title_w = 0.0
        phrase_hits = 0
        for idx in matched:
            t = terms[idx]
            w = t["weight"] * idf[idx]
            nums[t["comp"]] += w
            matched_w += w
            if idx in title_matched:
                title_w += w
            if t.get("phrase"):
                phrase_hits += 1
        cov = {c: (nums[c] / denoms[c] if denoms[c] > 0 else 0.0) for c in nums}
        coverage = sum(cov[c] * weights[c] for c in weights) / wsum
        title_ratio = (title_w / matched_w) if matched_w > 0 else 0.0
        title_bonus = 0.10 * title_ratio
        phrase_bonus = min(0.08, 0.03 * phrase_hits)
        native_bonus_hit = native_bonus if cov["native"] > 0 else 0.0
        score = min(1.0, coverage + title_bonus + phrase_bonus + native_bonus_hit)
        meta = unit["meta"]
        scored.append(
            {
                "score": round(score, 4),
                "coverage": round(coverage, 4),
                "native_coverage": round(cov["native"], 4),
                "mapped_coverage": round(cov["mapped"], 4),
                "en_coverage": round(cov["en"], 4),
                "title_bonus": round(title_bonus, 4),
                "phrase_bonus": round(phrase_bonus, 4),
                "native_bonus": round(native_bonus_hit, 4),
                "layer": unit["layer"],
                "title": meta.get("title") or unit["title"],
                "video_id": meta.get("video_id", ""),
                "channel_id": meta.get("channel_id", ""),
                "upload_date": meta.get("upload_date", ""),
                "kind": meta.get("kind", ""),
                "source": meta.get("source", ""),
                "document_hash": meta.get("document_hash", ""),
                "snippet": (unit["text"] or "")[:600],
                "matched_native": [
                    terms[i]["surface"] for i in matched if terms[i]["comp"] == "native"
                ][:8],
                "matched_mapped": [
                    terms[i]["surface"] for i in matched if terms[i]["comp"] == "mapped"
                ][:8],
                "matched_en": [
                    terms[i]["surface"] for i in matched if terms[i]["comp"] == "en"
                ][:8],
                "_native_matched_count": sum(
                    1 for i in matched if terms[i]["comp"] == "native"
                ),
                "_matched_count": len(matched),
            }
        )
    scored.sort(
        key=lambda h: (h["score"], h["coverage"], h["_native_matched_count"],
                       h["_matched_count"]),
        reverse=True,
    )
    top = scored[:k]
    for hit in top:
        hit.pop("_native_matched_count", None)
        hit.pop("_matched_count", None)
    return {"top": top, "positive_hits": positive, "weights": weights}


def v2_search(
    query: str,
    units: dict,
    k: int = 5,
    layers: tuple[str, ...] = ("docs", "chunks"),
) -> dict:
    """v2 检索：返回解析结果 + 各层 top-k。"""
    parsed = parse_query(query)
    terms = parsed["terms"]
    layers_out: dict[str, dict] = {}
    all_ids: list[float] = []
    if terms:
        df = [0] * len(terms)
        for unit in units["chunks"]:
            matched, _ = _match_unit(unit, terms)
            for idx in matched:
                df[idx] += 1
        n_units = max(1, len(units["chunks"]))
        all_ids = [_idf(d, n_units) for d in df]
        for layer in layers:
            layers_out[layer] = _layer_stats(units[layer], terms, all_ids, k)
    parsed["idf"] = {
        t["surface"]: round(all_ids[t["index"]], 4) for t in terms if all_ids
    }
    parsed["df"] = {
        t["surface"]: df[t["index"]] for t in terms
    } if terms else {}
    parsed["layers"] = layers_out
    return parsed


def v2_best(parsed: dict) -> dict | None:
    hits = []
    for layer in ("docs", "chunks"):
        hits.extend(parsed["layers"].get(layer, {}).get("top", []))
    if not hits:
        return None
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[0]


# ---------------------------------------------------------------------------
# v1 复算（用于同题对比；算法与 scripts/kb_bridge.py 一致）
# ---------------------------------------------------------------------------
def _v1_index(units: dict) -> dict:
    doc_tokens = [_v1_tokens(u["title"] + "\n" + u["text"]) for u in units["docs"]]
    chunk_tokens = [_v1_tokens(u["text"]) for u in units["chunks"]]
    return {"docs": doc_tokens, "chunks": chunk_tokens}


def v1_search(
    query: str,
    units: dict,
    tokens: dict,
    k: int = 5,
    layers: tuple[str, ...] = ("docs", "chunks"),
) -> dict:
    q = _v1_tokens(query)
    n = max(1, len(q))
    out: dict[str, dict] = {}
    for layer in layers:
        scored = []
        tk = tokens[layer]
        for i, t in enumerate(tk):
            score = len(q & t) / n
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda x: -x[0])
        tops = []
        for score, i in scored[:k]:
            unit = units[layer][i]
            meta = unit["meta"]
            tops.append(
                {
                    "score": round(score, 4),
                    "layer": layer,
                    "title": meta.get("title") or unit["title"],
                    "video_id": meta.get("video_id", ""),
                    "kind": meta.get("kind", ""),
                    "source": meta.get("source", ""),
                    "snippet": (unit["text"] or "")[:600],
                }
            )
        out[layer] = {"top": tops, "positive_hits": len(scored)}
    out["query_tokens"] = sorted(q)
    return out


def v1_best(res: dict) -> dict | None:
    hits = res["docs"]["top"] + res["chunks"]["top"]
    if not hits:
        return None
    return max(hits, key=lambda h: h["score"])


# ---------------------------------------------------------------------------
# 语料画像（如实说明中文原生内容有多少）
# ---------------------------------------------------------------------------
def _corpus_profile(corpus: dict, units: dict) -> dict:
    docs = corpus["docs"]
    chunks = corpus["chunks"]
    kinds: dict[str, int] = {}
    for d in docs:
        kinds[d.get("kind") or ""] = kinds.get(d.get("kind") or "", 0) + 1

    chunks_with_cjk = 0
    genuine_zh_chunks = 0
    bigrams: dict[str, int] = {}
    for unit in units["chunks"]:
        text = unit["text"]
        if re.search(CJK_RUN, text):
            chunks_with_cjk += 1
        stripped = text
        for label in STRUCT_LABELS:
            stripped = stripped.replace(label, "")
        runs = CJK_RUN.findall(stripped)
        genuine = [r for r in runs if len(r) >= 2]
        if genuine:
            genuine_zh_chunks += 1
            for run in genuine:
                simp = run.translate(T2S_TABLE)
                for i in range(len(simp) - 1):
                    bg = simp[i:i + 2]
                    bigrams[bg] = bigrams.get(bg, 0) + 1
    top_bigrams = dict(sorted(bigrams.items(), key=lambda kv: -kv[1])[:25])
    return {
        "docs": len(docs),
        "chunks": len(chunks),
        "doc_kinds": kinds,
        "chunk_sources": len({c.get("source") for c in chunks if c.get("source")}),
        "docs_dir_md_files": len(list(DOCS_DIR.glob("*.md"))) if DOCS_DIR.exists() else 0,
        "chunks_with_any_cjk": chunks_with_cjk,
        "chunks_with_genuine_zh": genuine_zh_chunks,
        "genuine_zh_bigram_top25": top_bigrams,
        "note": "真实中文内容仅为少量繁体简体要点/评论；标题/描述/高赞评论 为语料结构标签。",
    }


def _native_availability(units: dict, golden: list[dict]) -> dict:
    """golden 查询用到的中文词在知识库中的真实出现次数（中文原文，已繁简归一）。"""
    wanted: set[str] = set()
    for item in golden:
        for t in parse_query(item["query"])["terms"]:
            if t["comp"] == "native" and t["kind"] == "zh":
                wanted.add(t["zh"])
    avail: dict[str, dict] = {}
    for zh in sorted(wanted):
        chunk_df = sum(1 for u in units["chunks"] if any(zh in run for run in u["runs"]))
        doc_df = sum(1 for u in units["docs"] if any(zh in run for run in u["runs"]))
        avail[zh] = {"chunk_df": chunk_df, "doc_df": doc_df}
    return avail


# ---------------------------------------------------------------------------
# 评测
# ---------------------------------------------------------------------------
def _thresholds(scores: list[float]) -> dict:
    def rate(th: float) -> float:
        return round(sum(1 for s in scores if s >= th) / len(scores), 4) if scores else 0.0

    return {
        "hit_any": round(sum(1 for s in scores if s > 0) / len(scores), 4) if scores else 0.0,
        "hit_ge_0.2": rate(0.2),
        "hit_ge_0.5": rate(0.5),
        "hit_ge_0.6": rate(0.6),
        "mean_max_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "best_score": round(max(scores), 4) if scores else 0.0,
    }


def _eval_query(
    item: dict,
    corpus: dict,
    units: dict,
    v1_tokens: dict,
    k: int,
) -> dict:
    query = item["query"]
    v1_res = v1_search(query, units, v1_tokens, k=k)
    v2_res = v2_search(query, units, k=k)
    best_v1 = v1_best(v1_res)
    best_v2 = v2_best(v2_res)
    v1_max = best_v1["score"] if best_v1 else 0.0
    v2_max = best_v2["score"] if best_v2 else 0.0

    # 查询级中文原生证据：取 top-k 中 native_coverage 最高者（而非总分最高者）。
    # 这样「中文查询实际召回到的中文证据」与「总分最高的英文间接命中」分开如实呈现。
    native_top = None
    for layer in ("docs", "chunks"):
        for hit in v2_res["layers"].get(layer, {}).get("top", []):
            if native_top is None or hit["native_coverage"] > native_top["native_coverage"]:
                native_top = hit
    native_cov = native_top["native_coverage"] if native_top else 0.0
    native_top_score = native_top["score"] if native_top else 0.0
    native_matched = (native_top.get("matched_native") or []) if native_top else []
    mapped_cov = best_v2["mapped_coverage"] if best_v2 else 0.0
    en_cov = best_v2["en_coverage"] if best_v2 else 0.0
    mapped_matched = (best_v2.get("matched_mapped") or []) if best_v2 else []

    if native_cov >= 0.6 and native_top_score >= 0.5:
        verdict = "中文原生强命中"
    elif native_cov > 0:
        verdict = "中文原生弱命中（部分中文词直接命中）"
    elif mapped_cov > 0 or en_cov > 0:
        verdict = "仅术语映射/英文间接命中（非中文原生）"
    else:
        verdict = "无命中"

    return {
        "id": item["id"],
        "query": query,
        "topic": item["topic"],
        "v1": {
            "hit": v1_max > 0,
            "hit_ge_0.2": v1_max >= 0.2,
            "hit_ge_0.5": v1_max >= 0.5,
            "max_score": round(v1_max, 4),
            "doc_layer_hits": len(v1_res["docs"]["top"]),
            "chunk_layer_hits": len(v1_res["chunks"]["top"]),
            "query_tokens": v1_res["query_tokens"],
            "top": best_v1,
        },
        "v2": {
            "hit": v2_max > 0,
            "hit_ge_0.2": v2_max >= 0.2,
            "hit_ge_0.5": v2_max >= 0.5,
            "hit_ge_0.6": v2_max >= 0.6,
            "max_score": round(v2_max, 4),
            "max_coverage": best_v2["coverage"] if best_v2 else 0.0,
            "native_hit": native_cov > 0,
            "native_strong": native_cov >= 0.6 and native_top_score >= 0.5,
            "native_coverage": native_cov,
            "native_terms_matched": native_matched,
            "native_terms_total": len(v2_res["native_surfaces"]),
            "native_evidence": (
                {
                    "layer": native_top.get("layer"),
                    "title": native_top.get("title"),
                    "source": native_top.get("source"),
                    "score": native_top.get("score"),
                    "native_coverage": native_cov,
                    "terms_matched": native_matched,
                }
                if native_top and native_cov > 0
                else None
            ),
            "mapped_hit": mapped_cov > 0,
            "mapped_coverage": mapped_cov,
            "mapped_terms_matched": mapped_matched,
            "en_hit": en_cov > 0,
            "doc_layer_hits": len(v2_res["layers"].get("docs", {}).get("top", [])),
            "chunk_layer_hits": len(v2_res["layers"].get("chunks", {}).get("top", [])),
            "top": best_v2,
        },
        "native_term_chunk_df": {
            t["surface"]: v2_res["df"].get(t["surface"], 0)
            for t in v2_res["terms"]
            if t["comp"] == "native"
        },
        "mapped_term_chunk_df": {
            t["surface"]: v2_res["df"].get(t["surface"], 0)
            for t in v2_res["terms"]
            if t["comp"] == "mapped"
        },
        "verdict": verdict,
    }


def _baseline_crosscheck(rows: list[dict], golden: list[dict]) -> dict:
    """用 P2-6 的 C:\\Temp\\acad1_kb_refs.json 做 v1 记录值交叉校验（若存在）。"""
    if not BASELINE_REF.exists():
        return {"available": False, "reference_path": str(BASELINE_REF)}
    try:
        data = json.loads(BASELINE_REF.read_text(encoding="utf-8"))
        recorded_rows = data["summary"]["query_stats"]["rows"]
    except (json.JSONDecodeError, KeyError, OSError) as exc:  # pragma: no cover
        return {"available": False, "reference_path": str(BASELINE_REF),
                "error": f"{type(exc).__name__}: {exc}"}
    by_query = {r.get("query"): r for r in recorded_rows if r.get("lang") == "zh"}
    compared = 0
    match = 0
    details = []
    for item, row in zip(golden, rows):
        rec = by_query.get(item["query"])
        if not rec:
            continue
        compared += 1
        same = abs(float(rec.get("max_score", 0.0)) - row["v1"]["max_score"]) <= 1e-4
        match += 1 if same else 0
        details.append(
            {
                "query": item["query"],
                "recorded_max_score": rec.get("max_score"),
                "recomputed_v1_max_score": row["v1"]["max_score"],
                "match": same,
            }
        )
    return {
        "available": True,
        "reference_path": str(BASELINE_REF),
        "queries_compared": compared,
        "max_score_reproduced": match,
        "details": details,
    }


def run_eval(out_path: Path, k: int = 5) -> dict:
    t_start = time.perf_counter()
    corpus = load_corpus()
    units = _prepare_units(corpus)
    v1_tokens = _v1_index(units)
    profile = _corpus_profile(corpus, units)
    native_avail = _native_availability(units, GOLDEN_QUERIES)

    rows = [_eval_query(item, corpus, units, v1_tokens, k) for item in GOLDEN_QUERIES]

    v1_scores = [r["v1"]["max_score"] for r in rows]
    v2_scores = [r["v2"]["max_score"] for r in rows]
    native_covs = [r["v2"]["native_coverage"] for r in rows]
    native_strong = sum(1 for r in rows if r["v2"]["native_strong"])
    native_hits = sum(1 for r in rows if r["v2"]["native_hit"])
    mapped_only = sum(
        1 for r in rows
        if r["v2"]["mapped_hit"] and not r["v2"]["native_hit"]
    )
    no_hits = sum(1 for r in rows if not r["v2"]["hit"])

    # 补充短问法探针（不计入 headline）
    supplements = []
    for q in SUPPLEMENTARY_QUERIES:
        v1 = v1_search(q, units, v1_tokens, k=k)
        v2 = v2_search(q, units, k=k)
        v1_b = v1_best(v1)
        v2_b = v2_best(v2)
        supplements.append(
            {
                "query": q,
                "v1_max_score": v1_b["score"] if v1_b else 0.0,
                "v2_max_score": v2_b["score"] if v2_b else 0.0,
                "v2_native_coverage": v2_b["native_coverage"] if v2_b else 0.0,
                "v2_mapped_coverage": v2_b["mapped_coverage"] if v2_b else 0.0,
                "v2_top_title": (v2_b.get("title") if v2_b else None),
            }
        )

    summary = {
        "v1": _thresholds(v1_scores),
        "v2": _thresholds(v2_scores),
        "native_chinese": {
            "queries_with_native_hit": native_hits,
            "native_hit_rate": round(native_hits / len(rows), 4),
            "native_strong_hits": native_strong,
            "native_strong_rate": round(native_strong / len(rows), 4),
            "mean_native_coverage": round(sum(native_covs) / len(native_covs), 4),
        },
        "mapped_only_hits": mapped_only,
        "no_hits": no_hits,
        "delta": {
            "hit_any_pp": round(
                (_thresholds(v2_scores)["hit_any"] - _thresholds(v1_scores)["hit_any"]) * 100, 2
            ),
            "hit_ge_0.2_pp": round(
                (_thresholds(v2_scores)["hit_ge_0.2"]
                 - _thresholds(v1_scores)["hit_ge_0.2"]) * 100, 2
            ),
            "best_score": round(max(v2_scores) - max(v1_scores), 4),
            "mean_max_score": round(
                _thresholds(v2_scores)["mean_max_score"]
                - _thresholds(v1_scores)["mean_max_score"], 4
            ),
        },
        "per_query": [
            {
                "id": r["id"],
                "query": r["query"],
                "v1_max_score": r["v1"]["max_score"],
                "v2_max_score": r["v2"]["max_score"],
                "v2_native_coverage": r["v2"]["native_coverage"],
                "v2_verdict": r["verdict"],
            }
            for r in rows
        ],
    }

    count_check = {
        "expected": {"docs": EXPECTED_DOCS, "chunks": EXPECTED_CHUNKS},
        "actual": {"docs": len(corpus["docs"]), "chunks": len(corpus["chunks"])},
        "match": len(corpus["docs"]) == EXPECTED_DOCS
        and len(corpus["chunks"]) == EXPECTED_CHUNKS,
    }

    truthful_findings = [
        "知识库本体是英文量化/交易 YouTube 语料（196 docs / 4926 chunks，800 源文档），"
        "中文内容仅为少量繁体/简体要点与评论；标题/描述/高赞评论 是结构标签，不是知识内容。",
        f"11 条中文 golden 里，中文原生（汉字）直接命中 {native_hits}/11 条，"
        f"其中达到强命中口径（native_coverage≥0.6 且 score≥0.5）的只有 {native_strong}/11 条。",
        "缠论/中枢/三买/背驰/趋势/动量/止损/过拟合 等中文术语在知识库中几乎没有对应中文原文"
        "（见 native_term_availability），v2 的相关命中来自英文邻近概念映射"
        "（pivot/support resistance/breakout/risk management 等），不等于知识库覆盖缠论。",
        "v2 的提升主要来自：繁简归一恢复少量原生命中、术语映射接入英文内容、IDF/标题/短语加权；"
        "其中映射命中属于跨语言间接命中，已在每条 query 的 mapped_coverage / verdict 中单列。",
        "本层只做词法+映射检索，未使用向量/reranker，也未生成任何投资结论；"
        "检索命中不等于可据此下结论，需 ACAD1 人工判断。",
    ]

    result = {
        "schema": "kb_bridge_v2_eval/v1",
        "version": VERSION,
        "script": SCRIPT,
        "generated_at": _now_iso(),
        "elapsed_seconds": round(time.perf_counter() - t_start, 3),
        "policy": {
            "read_only": True,
            "writes_db": False,
            "writes_knowledge_base": False,
            "modifies_review_status": False,
            "auto_conclusion": False,
            "loads_embeddings": False,
            "builds_vector_index": False,
            "outputs": [str(out_path)],
            "disclaimer": "只读检索评测：不构成投资建议，不生成买卖结论，以知识库原文为准。",
        },
        "kb": {
            "root": str(KB),
            "lite_index": str(LITE),
            "rag_corpus": str(CORPUS),
            "dsl": {
                "docs_count": len(corpus["docs"]),
                "chunks_count": len(corpus["chunks"]),
                "corpus_version": corpus.get("corpus_version"),
                "corpus_index_version": corpus.get("corpus_index_version"),
                "load_seconds": corpus["load_seconds"],
            },
            "count_check": count_check,
            "profile": profile,
        },
        "method": {
            "v1": "scripts/kb_bridge.py 原算法：英文 2+ 词 + 中文 bigram 覆盖率"
                  "（docs=title+text，chunks=text，score=命中 token 数/查询 token 数）。",
            "v2": "繁简归一 + 中文领域词典/剩余 bigram + 中文→英文术语映射 + IDF 加权"
                  " + 标题与短语加成。中文查询：coverage = 0.50*native_cov + 0.50*mapped_cov；"
                  "含英文原词时 0.40/0.30/0.30；英文查询按英文项。另加 title_bonus(≤0.10)"
                  "、phrase_bonus(≤0.08) 与 native_bonus(+0.02，仅当中文原词实际命中)，上限 1.0。",
            "component_note": "native_cov 只在中文术语空间归一、mapped_cov 只在映射英文术语空间归一，"
                              "因此 mapped_cov 高只代表英文邻近概念命中，不代表中文内容命中。",
            "hit_definition": "hit=top-k 最高分>0；hit_ge_0.2 对齐 P2-6 的 min_ref_score=0.2；"
                              "强命中=score≥0.5（评测另列 0.6 阈值）。",
            "golden_set": "11 条中文查询取自 P2-6（task-10 kb_enrich）的 zh 查询基线，"
                          "保证 v1/v2 同题同语料对比；任务描述里列举的主题均被覆盖。",
            "term_mapping_note": "映射表见 term_mapping；映射命中是跨语言邻近概念检索，"
                                 "不是知识库存在对应中文内容。",
        },
        "term_mapping": {
            "traditional_to_simplified_chars": len(T2S),
            "gazetteer_terms": len(GAZETTEER),
            "gazetteer": {k: [[p, w] for p, w in v] for k, v in GAZETTEER.items()},
        },
        "native_term_availability": native_avail,
        "baseline_crosscheck": _baseline_crosscheck(rows, GOLDEN_QUERIES),
        "summary": summary,
        "queries": rows,
        "supplementary_queries": supplements,
        "truthful_findings": truthful_findings,
        "limitations": [
            "检索层为词法+映射，非语义向量检索；中文同义改写（如 中枢 vs 盘整）无法泛化。",
            "映射表人工维护，覆盖有限；映射到的英文内容只是邻近概念，不能当作缠论/中国市场的"
            "原生知识。",
            "知识库无 A股/港股/美股 制度与交易规则专项内容，市场类查询只能命中通用英文市场内容。",
            "评论层（kind=comments / comment_section）含口语噪声，标题加权可能偏向视频标题。",
            "v1 复算结果与 P2-6 记录若不一致，以 baseline_crosscheck 为准并在回执中说明。",
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_hit(hit: dict, idx: int) -> None:
    print(
        "%d. [%.3f] %s | %s %s"
        % (
            idx,
            hit["score"],
            (hit.get("title") or "")[:70],
            hit.get("source", ""),
            hit.get("kind", ""),
        )
    )
    snippet = (hit.get("snippet") or "").replace("\n", " ")
    print("   %s" % snippet[:200])


def _search_cli(query: str, k: int, with_chunks: bool) -> int:
    corpus = load_corpus()
    units = _prepare_units(corpus)
    layers: tuple[str, ...] = ("docs", "chunks") if with_chunks else ("docs",)
    v2 = v2_search(query, units, k=k, layers=layers)
    print("查询：%s（v2 解析：native=%s mapped=%s en=%s）"
          % (query, v2["native_surfaces"], v2["mapped_surfaces"], v2["en_surfaces"]))
    for layer in layers:
        print("--- %s ---" % layer)
        for i, hit in enumerate(v2["layers"].get(layer, {}).get("top", []), 1):
            _print_hit(hit, i)
        if not v2["layers"].get(layer, {}).get("top"):
            print("（无命中）")
    return 0


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    ap = argparse.ArgumentParser(description="知识库中文/缠论检索增强（只读）")
    ap.add_argument("query", nargs="?", help="查询文本")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--chunks", action="store_true", help="同时检索 RAG 语料块")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--eval", action="store_true", help="跑 11 条中文 golden 对比评测")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="eval 输出路径（默认 C:\\Temp）")
    args = ap.parse_args(argv)

    if args.eval:
        out = Path(args.out)
        try:
            out.resolve().relative_to(KB.resolve())
        except ValueError:
            pass
        else:
            print("拒绝写入知识库目录（只读约束）：%s" % out, file=sys.stderr)
            return 2
        result = run_eval(out, k=args.k)
        s = result["summary"]
        print("[kb_bridge_v2] eval done in %.2fs" % result["elapsed_seconds"])
        print("kb: docs=%d chunks=%d count_check=%s"
              % (result["kb"]["profile"]["docs"], result["kb"]["profile"]["chunks"],
                 result["kb"]["count_check"]["match"]))
        print("v1: hit(>0)=%.2f useful(>=0.2)=%.2f strong(>=0.5)=%.2f best=%.4f"
              % (s["v1"]["hit_any"], s["v1"]["hit_ge_0.2"], s["v1"]["hit_ge_0.5"],
                 s["v1"]["best_score"]))
        print("v2: hit(>0)=%.2f useful(>=0.2)=%.2f strong(>=0.5)=%.2f best=%.4f"
              % (s["v2"]["hit_any"], s["v2"]["hit_ge_0.2"], s["v2"]["hit_ge_0.5"],
                 s["v2"]["best_score"]))
        print("v2 native(zh): hit=%d/11 strong=%d/11 mapped_only=%d/11 no_hit=%d/11"
              % (s["native_chinese"]["queries_with_native_hit"],
                 s["native_chinese"]["native_strong_hits"],
                 s["mapped_only_hits"], s["no_hits"]))
        print("wrote %s" % out)
        return 0

    if not args.query:
        ap.print_help()
        return 2

    if args.json:
        corpus = load_corpus()
        units = _prepare_units(corpus)
        layers: tuple[str, ...] = ("docs", "chunks") if args.chunks else ("docs",)
        parsed = v2_search(args.query, units, k=args.k, layers=layers)
        payload = {
            "query": args.query,
            "normalized": parsed["normalized"],
            "native_surfaces": parsed["native_surfaces"],
            "mapped_surfaces": parsed["mapped_surfaces"],
            "en_surfaces": parsed["en_surfaces"],
            "df": parsed["df"],
            "layers": parsed["layers"],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    return _search_cli(args.query, args.k, args.chunks)


if __name__ == "__main__":
    raise SystemExit(main())
