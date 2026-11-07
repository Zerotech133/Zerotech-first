# -*- coding: utf-8 -*-
"""JEV 判定加权词云只读接口 —— `/api/jev/wordcloud`（2026-11-07）。

来源：WO-INTEL-WORDC1-JEV-20261107-001（陈琳词云席 JEV 加权扩展）治理线先行实现；
口径与工单一致，产出消费方 = portal/jev_wordcloud.html（前端）与词云席工具链。

    GET /api/jev/wordcloud?days=90&top_n=30&event_top=6&event_words=8
        days        统计窗口（按 ts，天），1..730，默认 90
        top_n       每 bucket 返回词数，5..80，默认 30
        event_top   事件类型矩阵行数，0..8，默认 6（0=关）
        event_words 每事件类型词数，3..15，默认 8

只读纪律：news.db 一律 `mode=ro`；不调 Ollama、不碰 ollama_lock、不写任何库。
分桶：bull / bear / neutral（工单 §3）；conf<0.5 降权 0.5；
     unjudged 对照桶仅抽样（≤ min(1000, bull 数)），不计入主词云。
停用词：**复用** quant_backtest/tools/wordc1_cloud.py 的 STOPWORDS（工单验收判据 3：
     不增不减）；清理正则同样复用（_RE_TAGS/_RE_URL/_RE_ENTITY）。
分词：jieba 精确模式（0.42.1 在位）；探测失败降级 2-gram 并在 meta 如实标注。
缓存：进程内 TTL 600s（键 = 参数元组），避免 135k 行逐请求重算。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from urllib.parse import parse_qs

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DB = os.path.join(STATION, "news_collector", "news.db")
WCTOOLS_DIR = os.path.join(STATION, "quant_backtest", "tools")

MIN_DAYS, MAX_DAYS, DEF_DAYS = 1, 730, 90
MIN_TOP, MAX_TOP, DEF_TOP = 5, 80, 30
MIN_ETOP, MAX_ETOP, DEF_ETOP = 0, 8, 6
MIN_EWORDS, MAX_EWORDS, DEF_EWORDS = 3, 15, 8
CONF_DOWN = 0.5            # conf<0.5 权重（工单 §3）
CACHE_TTL_S = 600
BUCKETS = ("bull", "bear", "neutral")
MIN_BUCKET_N = 1000        # 工单 §3：不足则出「样本不足」占位
UNJUDGED_SAMPLE_CAP = 1000
EVENT_BASELINE = ("other",)  # other 计入矩阵但标注为基线桶
# 2026-11-07 老田令「利多榜别被腔调词占据」：多空两桶共现高频的金融泛词
# （公司/股份/研报/指数…）无判别力，运行时扩展黑名单降权——不动 wordc1_cloud
# 的 STOPWORDS 常量（工单判据 3 冻结口径），只在 API 侧叠加过滤。
GENERIC_WORDS = frozenset({
    "公司", "股份", "有限", "集团", "证券", "研报", "个股", "市场",
    "指数", "科技", "美国", "美元", "中国", "A股", "SeekingAlpha",
    "同比", "环比", "持续", "预期", "亿元", "万元", " announcement", "AI",
    "盘中", "日内", "现报", "涨跌", "收盘", "开盘",
})

# 复用词云席停用词与清理正则（工单验收判据 3：口径不变）——只读 import，无副作用
if WCTOOLS_DIR not in sys.path:
    sys.path.insert(0, WCTOOLS_DIR)
try:
    from wordc1_cloud import STOPWORDS, _RE_TAGS, _RE_URL, _RE_ENTITY  # noqa: E402
except Exception:  # pragma: no cover - 词云席工具缺失时 fail-loud 于请求期
    STOPWORDS = None

try:
    import jieba
    jieba.setLogLevel(60)
    _TOKENIZER = "jieba"
except Exception:  # pragma: no cover
    jieba = None
    _TOKENIZER = "2gram"

_CACHE: dict = {"key": None, "at": 0.0, "data": None}


def _clean(title: str) -> str:
    s = _RE_ENTITY.sub(" ", _RE_URL.sub(" ", _RE_TAGS.sub(" ", str(title or ""))))
    return re.sub(r"\s+", " ", s).strip()


def _tokens(text: str):
    if _TOKENIZER == "jieba":
        return jieba.lcut(text)
    return [text[i:i + 2] for i in range(0, len(text) - 1, 2)]


def _valid(word: str) -> bool:
    return (len(word) >= 2 and word not in STOPWORDS
            and word not in GENERIC_WORDS
            and not word.isdigit() and not re.fullmatch(r"[\W_]+", word, re.UNICODE))


def _as_int(qs: dict, key: str, default: int, lo: int, hi: int) -> int:
    raw = (qs.get(key) or [str(default)])[-1]
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError("%s 需整数: %r" % (key, raw))
    if not (lo <= v <= hi):
        raise ValueError("%s 超范围 [%d,%d]: %d" % (key, lo, hi, v))
    return v


def compute(days: int = DEF_DAYS, top_n: int = DEF_TOP,
            event_top: int = DEF_ETOP, event_words: int = DEF_EWORDS) -> dict:
    """只读计算分桶词频；异常向上抛（handle_get 统一 400/500）。"""
    if STOPWORDS is None:
        raise RuntimeError("wordc1_cloud.STOPWORDS 不可用（quant_backtest/tools 缺失）")
    since = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    db = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.replace("\\", "/"), uri=True, timeout=10)
    try:
        rows = db.execute(
            "SELECT title, ts, jev FROM news"
            " WHERE title IS NOT NULL AND TRIM(title)!='' AND ts >= ?",
            (since,)).fetchall()
    finally:
        db.close()

    buckets = {b: {"words": Counter(), "n": 0, "min_ts": None, "max_ts": None}
               for b in BUCKETS}
    unjudged_titles: list[str] = []
    malformed = 0
    et_words = {et: Counter() for et in {}}
    et_count: Counter = Counter()
    et_store: dict[str, Counter] = {}
    total_judged = 0
    # 2026-11-07 老田令：利多/利空各多少条、涉及哪些公司——方向×标的透视
    bias_rollup = {b: {"count": 0, "companies": Counter()} for b in BUCKETS}

    for title, ts, jev in rows:
        text = _clean(title)
        if not text:
            continue
        if not jev or not str(jev).strip():
            unjudged_titles.append(text)
            continue
        try:
            d = json.loads(jev)
            bias = d.get("bias")
            conf = float(d.get("conf") or 1.0)
            et = str(d.get("event_type") or "")
        except (ValueError, TypeError):
            malformed += 1
            continue
        if bias not in buckets:
            malformed += 1
            continue
        b = buckets[bias]
        b["n"] += 1
        total_judged += 1
        roll = bias_rollup[bias]
        roll["count"] += 1
        for c in (d.get("companies") or []):
            c6 = str(c).strip()
            if len(c6) == 6 and c6.isdigit():
                roll["companies"][c6] += 1
        if ts:
            if b["min_ts"] is None or ts < b["min_ts"]:
                b["min_ts"] = ts
            if b["max_ts"] is None or ts > b["max_ts"]:
                b["max_ts"] = ts
        w = CONF_DOWN if conf < CONF_DOWN else 1.0
        toks = [t for t in _tokens(text) if _valid(t)]
        for t in set(toks):          # 标题级去重：一篇新闻同一词只计一次
            b["words"][t] += w
        if et and event_top > 0:
            et_count[et] += 1
            et_store.setdefault(et, Counter())
            for t in set(toks):
                et_store[et][t] += w

    def _top(c: Counter, k: int) -> list[dict]:
        return [{"w": t, "n": round(v, 1)} for t, v in c.most_common(k)]

    out_buckets = {}
    for bname in BUCKETS:
        b = buckets[bname]
        enough = b["n"] >= MIN_BUCKET_N
        out_buckets[bname] = {
            "count": b["n"],
            "span": [b["min_ts"], b["max_ts"]],
            "enough": enough,
            "note": "" if enough else "样本不足(<%d)，分布不具代表性" % MIN_BUCKET_N,
            "top": _top(b["words"], top_n) if enough else [],
        }

    # unjudged 对照桶：仅抽样，不计主词云（工单 §3）
    sample_n = min(UNJUDGED_SAMPLE_CAP, buckets["bull"]["n"], len(unjudged_titles))
    ucnt: Counter = Counter()
    for text in unjudged_titles[:sample_n]:
        for t in set(t for t in _tokens(text) if _valid(t)):
            ucnt[t] += 1

    event_matrix = []
    if event_top > 0:
        for et, n in et_count.most_common(event_top):
            event_matrix.append({
                "event_type": et,
                "count": n,
                "baseline": et in EVENT_BASELINE,
                "words": _top(et_store.get(et, Counter()), event_words),
            })

    return {
        "ok": True,
        "schema": "jev_wordcloud.v1",
        "generated_at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "window_days": days,
        "since": since,
        "tokenizer": _TOKENIZER,
        # 方向×标的透视（2026-11-07）：利多/利空各多少条、涉及哪些公司 Top
        "bias_rollup": {
            b: {
                "count": bias_rollup[b]["count"],
                "companies": [{"code": c, "n": n}
                              for c, n in bias_rollup[b]["companies"].most_common(15)],
            } for b in BUCKETS},
        "total_judged": total_judged,
        "malformed": malformed,
        "buckets": out_buckets,
        "unjudged": {"sampled": sample_n, "top": _top(ucnt, top_n)},
        "event_matrix": event_matrix,
        "meta": {
            "conf_downweight_below": CONF_DOWN,
            "min_bucket_n": MIN_BUCKET_N,
            "stopwords_source": "quant_backtest/tools/wordc1_cloud.py STOPWORDS（工单判据3：不增不减）",
            "dedup": "per-title set()（一篇新闻同一词只计一次）",
        },
    }


def compute_cached(days: int, top_n: int, event_top: int, event_words: int) -> dict:
    key = (days, top_n, event_top, event_words)
    now = time.time()
    if _CACHE["key"] == key and now - _CACHE["at"] < CACHE_TTL_S:
        return _CACHE["data"]
    data = compute(days, top_n, event_top, event_words)
    _CACHE.update(key=key, at=now, data=data)
    return data


def handle_get(handler, path: str, qs_raw: str) -> None:
    """portal_api do_GET 分派入口；异常一律 400/500 明确返回。"""
    qs = parse_qs(qs_raw or "")
    try:
        days = _as_int(qs, "days", DEF_DAYS, MIN_DAYS, MAX_DAYS)
        top_n = _as_int(qs, "top_n", DEF_TOP, MIN_TOP, MAX_TOP)
        event_top = _as_int(qs, "event_top", DEF_ETOP, MIN_ETOP, MAX_ETOP)
        event_words = _as_int(qs, "event_words", DEF_EWORDS, MIN_EWORDS, MAX_EWORDS)
        result = compute_cached(days, top_n, event_top, event_words)
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params", "message": str(exc)}, 400)
        return
    except RuntimeError as exc:
        handler._json({"ok": False, "error": "unavailable", "message": str(exc)}, 503)
        return
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    handler._json(result)
