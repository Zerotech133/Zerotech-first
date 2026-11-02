"""ACAD1 知识库方法/风险引用层（只读 enrichment，不给结论）。

目标：把张松知识库（C:\\项目\\知识库）接成 ACAD1 研究池的“方法/风险引用”层。
- 复用 scripts/kb_bridge.py 的 search_docs / search_chunks 检索函数；
- 读取 v2_acad1_pool 最新池（市场/策略/信号），为每一只生成方法/风险检索查询，
  每项取 top 3 引用（title / video_id / source / snippet）；
- 输出 C:\\Temp\\acad1_kb_refs.json；
- 只读：不写 DB、不写 v2_stock_research、不改 reviewed/status、不生成投资结论。

用法：
  python scripts/kb_enrich.py
  python scripts/kb_enrich.py --limit 30 --pool-date 2026-10-31
  python scripts/kb_enrich.py --no-db            # DB 不可用时回退 30 卡清单
  python scripts/kb_enrich.py --cards C:\\Temp\\2026-11-02_ACAD1复核清单_30只_normalized_sim.json
  python scripts/kb_enrich.py --verbose          # 打印逐项引用

说明：
- 知识库语料以英文 YouTube 量化/交易频道为主（标题、描述、评论、转写），中文检索命中率
  天然偏低；本脚本按“中文查询 + 英文查询”双语检索，并在 summary 中如实报告中文命中率。
- 引用只作方法/风险参考，不是投资结论；所有引用带 video_id + source + snippet 可回溯。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _path in (_SCRIPT_DIR, _PROJECT_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import kb_bridge  # noqa: E402

OUT_SCHEMA = "acad1_kb_refs/v1"
DEFAULT_OUT = Path(r"C:\Temp\acad1_kb_refs.json")
DEFAULT_CARDS = Path(r"C:\Temp\2026-11-02_ACAD1复核清单_30只_normalized_sim.json")
TOP_K_FETCH = 5  # 每个查询先取 5 条，去重后再按 top-k 选择
MIN_REF_SCORE = 0.2  # 引用最低相关度（kb_bridge 的 token 覆盖率）；过滤中文噪声
METHOD_QUALITY_RATIO = 0.7  # 方法引用只保留 >= 0.7×最佳分 的同档条目，弱方法引用让位给风险/验证
DEFAULT_LIMIT = 30
DEFAULT_K = 3

# DB 不可用 / 使用卡片清单时，用静态映射兜底（DB v2_strategy 是首选来源）
FALLBACK_STRATEGIES: dict[str, dict[str, str]] = {
    "v1-cn-strat-2": {"strategy_name": "[V1] 均值回归v1", "strategy_type": "MEAN_REVERSION"},
    "v1-cn-strat-4": {"strategy_name": "[V1] 缠论v1", "strategy_type": "CHAN"},
    "v1-cn-strat-4-v7": {"strategy_name": "[V7] 缠论增强版", "strategy_type": "CHAN_V7"},
    "v1-crypto-strat-11": {
        "strategy_name": "[V1] 虚拟币vibe均值回归5分钟",
        "strategy_type": "VIBE_EXTERNAL",
    },
    "v1-crypto-strat-9": {"strategy_name": "[V1] 虚拟币趋势动量v1", "strategy_type": "TREND"},
    "v1-hk-strat-7": {"strategy_name": "[V1] 港股趋势动量v1", "strategy_type": "TREND"},
    "v1-hk-strat-8": {"strategy_name": "[V1] 港股vibeRSI反转", "strategy_type": "VIBE_EXTERNAL"},
    "v1-us-strat-6": {"strategy_name": "[V1] 美股趋势动量v1", "strategy_type": "TREND"},
    "qpzm": {"strategy_name": "青萍", "strategy_type": "QPZM"},
}

# 方法检索词：按策略类型给出「中文原生 + 英文方法」查询；en_alt 为补充英文查询（扩召回）
METHOD_QUERIES: dict[str, dict[str, str]] = {
    "MEAN_REVERSION": {
        "zh": "均值回归 超跌 反转",
        "en": "mean reversion trading strategy RSI oversold",
        "en_alt": "mean reversion",
    },
    "CHAN": {
        "zh": "缠论 中枢 买卖点",
        "en": "market structure support resistance technical analysis",
        "en_alt": "support resistance trading strategy",
    },
    "CHAN_V7": {
        "zh": "缠论 中枢 三买 风险",
        "en": "market structure support resistance trend confirmation",
        "en_alt": "support resistance breakout",
    },
    "TREND": {
        "zh": "趋势 动量 跟踪",
        "en": "trend following momentum strategy",
        "en_alt": "trend following",
    },
    "RSI": {
        "zh": "RSI 超卖 反转",
        "en": "RSI oversold mean reversion indicator strategy",
        "en_alt": "RSI indicator strategy",
    },
    "REGIME": {
        "zh": "大盘 环境 过滤 风控",
        "en": "market regime filter",
    },
    "GENERIC": {
        "zh": "量化 交易 方法",
        "en": "quantitative trading strategy",
        "en_alt": "trading strategy",
    },
}

RISK_QUERIES: dict[str, str] = {
    "zh": "止损 仓位 风险管理",
    "en": "stop loss risk control",
}

VALIDATION_QUERIES: dict[str, str] = {
    "zh": "回测 过拟合",
    "en": "backtest overfitting",
}

MARKET_QUERIES: dict[str, dict[str, str]] = {
    "CN": {"zh": "A股 市场 结构", "en": "china a-share stock market"},
    "HK": {"zh": "港股 市场", "en": "hong kong stock market"},
    "US": {"zh": "美股 市场", "en": "us stock market"},
    "CRYPTO": {"zh": "加密货币 波动", "en": "cryptocurrency volatility trading"},
    "": {"zh": "市场 结构", "en": "stock market structure"},
}

_REF_CACHE: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
_QUERY_STATS: dict[tuple[str, str, str], dict[str, Any]] = {}
_VIDEO_META_CACHE: dict[str, dict[str, str]] = {}

TOPIC_LABELS = {
    "method": "方法参考",
    "risk": "风险参考",
    "validation": "验证/过拟合参考",
    "market": "市场背景参考",
}

# 引用选择配额：方法类优先，其次风险；验证/市场仅在缺口时补位
SELECT_QUOTAS: tuple[tuple[str, int], ...] = (
    ("method", 2),
    ("risk", 1),
    ("validation", 1),
    ("market", 1),
)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _clean_text(text: str, limit: int = 600) -> str:
    """压缩空白并截断，保证 snippet 单行可读。"""
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _video_meta(video_id: str) -> dict[str, str]:
    """从 RAG docs 的 md 头行补齐 chunk 引用的 title/channel/date（带缓存）。"""
    if not video_id:
        return {"title": "", "channel_id": "", "upload_date": ""}
    cached = _VIDEO_META_CACHE.get(video_id)
    if cached is not None:
        return cached
    meta = {"title": "", "channel_id": "", "upload_date": ""}
    doc_path = kb_bridge.CORPUS.parent / "docs" / f"{video_id}.md"
    try:
        lines = doc_path.read_text(encoding="utf-8", errors="replace").splitlines()[:10]
    except OSError:
        lines = []
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        meta["title"] = re.split(r"\s+-\s+video_id:", title)[0].strip()
    header = "\n".join(lines)
    for field in ("channel_id", "upload_date"):
        field_match = re.search(field + r":\s*(\S+)", header)
        if field_match:
            meta[field] = field_match.group(1)
    _VIDEO_META_CACHE[video_id] = meta
    return meta


def _doc_ref(hit: dict[str, Any], topic: str, query: str, lang: str) -> dict[str, Any]:
    return {
        "title": hit.get("title") or "",
        "video_id": hit.get("video_id") or "",
        "source": hit.get("source") or "",
        "snippet": _clean_text(hit.get("snippet") or ""),
        "score": round(float(hit.get("score") or 0.0), 4),
        "topic": topic,
        "lang": lang,
        "query": query,
        "retrieval": "doc",
        "doc_kind": hit.get("kind") or "",
        "upload_date": hit.get("upload_date") or "",
        "channel_id": hit.get("channel_id") or "",
    }


def _chunk_ref(hit: dict[str, Any], topic: str, query: str, lang: str) -> dict[str, Any]:
    source = hit.get("source") or ""
    video_id = Path(source).stem if source else ""
    meta = _video_meta(video_id)
    return {
        "title": meta["title"],
        "video_id": video_id,
        "source": source,
        "snippet": _clean_text(hit.get("snippet") or ""),
        "score": round(float(hit.get("score") or 0.0), 4),
        "topic": topic,
        "lang": lang,
        "query": query,
        "retrieval": "chunk",
        "doc_kind": "chunk",
        "upload_date": meta["upload_date"],
        "channel_id": meta["channel_id"],
    }


def _search_refs(query: str, topic: str, lang: str) -> list[dict[str, Any]]:
    """复用 kb_bridge 的检索函数，取文档 + 语料块命中，过滤低分并去重。"""
    key = (query, topic, lang)
    cached = _REF_CACHE.get(key)
    if cached is not None:
        return cached
    doc_hits = kb_bridge.search_docs(query, TOP_K_FETCH)
    chunk_hits = kb_bridge.search_chunks(query, TOP_K_FETCH)
    raw_scores = [float(h.get("score") or 0.0) for h in doc_hits + chunk_hits]
    refs: list[dict[str, Any]] = []
    for hit in doc_hits:
        if float(hit.get("score") or 0.0) >= MIN_REF_SCORE:
            refs.append(_doc_ref(hit, topic, query, lang))
    for hit in chunk_hits:
        if float(hit.get("score") or 0.0) >= MIN_REF_SCORE:
            refs.append(_chunk_ref(hit, topic, query, lang))
    deduped: dict[str, dict[str, Any]] = {}
    for ref in refs:
        dedupe_key = ref.get("video_id") or ref["source"]
        old = deduped.get(dedupe_key)
        if old is None or ref["score"] > old["score"]:
            deduped[dedupe_key] = ref
    out = sorted(deduped.values(), key=lambda r: (-r["score"], r.get("video_id") or ""))
    _REF_CACHE[key] = out
    _QUERY_STATS[key] = {
        "query": query,
        "topic": topic,
        "lang": lang,
        "raw_doc_hits": len(doc_hits),
        "raw_chunk_hits": len(chunk_hits),
        "raw_hits": len(doc_hits) + len(chunk_hits),
        "refs": len(out),
        "max_score": round(max(raw_scores, default=0.0), 4),
    }
    return out


def _select_top(refs_by_topic: dict[str, list[dict[str, Any]]], k: int) -> list[dict[str, Any]]:
    """按配额选 top-k 引用：方法 2 + 风险 1 优先，不足时用验证/市场补位。

    每个 topic 内先按 score 降序，质量优先；同分再按 video_id 稳定排序。
    方法类只保留与最佳方法引用同档（>= METHOD_QUALITY_RATIO×最佳分）的条目，
    弱方法引用让位给风险/验证引用，避免为凑满 2 条方法引用而降低引用质量。
    """
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    for topic, quota in SELECT_QUOTAS:
        taken = 0
        candidates = sorted(
            refs_by_topic.get(topic, []), key=lambda r: (-r["score"], r.get("video_id") or "")
        )
        if topic == "method" and candidates:
            floor = candidates[0]["score"] * METHOD_QUALITY_RATIO
            candidates = [ref for ref in candidates if ref["score"] >= floor]
        for ref in candidates:
            if len(selected) >= k or taken >= quota:
                break
            key = ref.get("video_id") or ref["source"]
            if key in used:
                continue
            selected.append(ref)
            used.add(key)
            taken += 1
    if len(selected) < k:
        remaining = [
            ref
            for refs in refs_by_topic.values()
            for ref in refs
            if (ref.get("video_id") or ref["source"]) not in used
        ]
        remaining.sort(key=lambda r: (-r["score"], r.get("video_id") or ""))
        for ref in remaining:
            if len(selected) >= k:
                break
            key = ref.get("video_id") or ref["source"]
            selected.append(ref)
            used.add(key)
    return selected[:k]


# ---------------------------------------------------------------------------
# ACAD1 池 / 策略画像
# ---------------------------------------------------------------------------
def _strategy_info(
    strategy_id: str, overrides: dict[str, dict[str, str]] | None = None
) -> dict[str, str]:
    info = dict(FALLBACK_STRATEGIES.get(strategy_id) or {})
    if overrides and strategy_id in overrides:
        info.update({k: v for k, v in overrides[strategy_id].items() if v})
    info.setdefault("strategy_name", strategy_id or "")
    info.setdefault("strategy_type", "")
    return info


def _method_key(strategy_type: str, signal: str, strategy_id: str) -> str:
    """策略/信号 → 方法检索词类别。"""
    stype = (strategy_type or "").upper()
    sig = signal or ""
    sid = strategy_id or ""
    if "CHAN" in stype or "CHAN" in sig.upper() or "缠论" in sig or "缠论" in stype:
        return "CHAN_V7" if ("V7" in stype or "V7" in sig.upper()) else "CHAN"
    if "MEAN" in stype or "均值回归" in sig or "均值回归" in stype:
        return "MEAN_REVERSION"
    if "TREND" in stype or "趋势" in sig or "动量" in sig:
        return "TREND"
    if "RSI" in stype or "RSI" in sig.upper() or "反转" in sig:
        return "RSI"
    if "回避" in sig or "环境" in sig or "REGIME" in stype or "QPZM" in stype:
        return "REGIME"
    if "strat-4" in sid:
        return "CHAN"
    if "strat-2" in sid:
        return "MEAN_REVERSION"
    if "strat-11" in sid or "strat-8" in sid:
        return "RSI"
    if "strat-9" in sid or "strat-7" in sid or "strat-6" in sid:
        return "TREND"
    return "GENERIC"


def _profile(row: dict[str, Any]) -> dict[str, Any]:
    """为一只 ACAD1 池标的生成方法/风险检索画像。"""
    method_key = _method_key(
        row.get("strategy_type", ""), row.get("signal", ""), row.get("strategy_id", "")
    )
    market = (row.get("market") or "").upper()
    return {
        "method_key": method_key,
        "strategy_name": row.get("strategy_name") or row.get("strategy_id") or "",
        "strategy_type": row.get("strategy_type") or "",
        "market": market,
        "method_queries": METHOD_QUERIES.get(method_key, METHOD_QUERIES["GENERIC"]),
        "market_queries": MARKET_QUERIES.get(market, MARKET_QUERIES[""]),
    }


def _query_plan(profile: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(topic, lang, query) 列表：方法 / 风险 / 验证 / 市场，各中文+英文。"""
    method = profile["method_queries"]
    market = profile["market_queries"]
    plan = [
        ("method", "zh", method["zh"]),
        ("method", "en", method["en"]),
        ("risk", "zh", RISK_QUERIES["zh"]),
        ("risk", "en", RISK_QUERIES["en"]),
        ("validation", "zh", VALIDATION_QUERIES["zh"]),
        ("validation", "en", VALIDATION_QUERIES["en"]),
        ("market", "zh", market["zh"]),
        ("market", "en", market["en"]),
    ]
    if method.get("en_alt"):
        plan.insert(2, ("method", "en", method["en_alt"]))
    return plan


def _method_coverage(refs: list[dict[str, Any]]) -> str:
    """方法类覆盖：中文原生命中 / 仅英文命中 / 无方法命中。"""
    method_refs = [ref for ref in refs if ref["topic"] == "method"]
    if not method_refs:
        return "none"
    if any(ref["lang"] == "zh" for ref in method_refs):
        return "zh_direct"
    return "en_only"


def _load_pool_from_db(
    pool_date: str | None, limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """只读读取 ACAD1 池（SELECT only，不开事务写）。"""
    import sqlalchemy as sa
    from dotenv import load_dotenv

    env_file = _PROJECT_ROOT / ".env.local"
    if env_file.exists():
        load_dotenv(env_file, override=False)
    from zhice_os_v2.config.settings import get_settings

    settings = get_settings()
    engine = sa.create_engine(
        settings.db_main_url.replace("postgresql://", "postgresql+psycopg2://"),
        pool_pre_ping=True,
    )
    try:
        with engine.connect() as conn:  # 只读连接：本脚本绝不 commit / 写入
            day = (
                pool_date
                or conn.execute(sa.text("SELECT max(pool_date) FROM v2_acad1_pool")).scalar()
            )
            if not day:
                raise RuntimeError("v2_acad1_pool 无数据")
            pool_rows = (
                conn.execute(
                    sa.text("""
                SELECT pool_date, strategy_id, code, name, market, signal, score,
                       selection_reason, data_date
                FROM v2_acad1_pool
                WHERE pool_date = :day
                ORDER BY market, strategy_id, signal, code
                LIMIT :limit
            """),
                    {"day": day, "limit": limit},
                )
                .mappings()
                .all()
            )
            strategy_rows = (
                conn.execute(
                    sa.text("""
                SELECT strategy_id, strategy_name, market, strategy_type, description
                FROM v2_strategy ORDER BY strategy_id
            """)
                )
                .mappings()
                .all()
            )
    finally:
        engine.dispose()

    overrides = {
        r["strategy_id"]: {
            "strategy_name": r["strategy_name"] or "",
            "strategy_type": r["strategy_type"] or "",
            "description": r["description"] or "",
        }
        for r in strategy_rows
        if r["strategy_id"]
    }
    rows: list[dict[str, Any]] = []
    for r in pool_rows:
        sid = r["strategy_id"] or ""
        info = _strategy_info(sid, overrides)
        rows.append(
            {
                "pool_date": str(r["pool_date"]),
                "market": (r["market"] or "").upper(),
                "strategy_id": sid,
                "strategy_name": info["strategy_name"],
                "strategy_type": info["strategy_type"],
                "strategy_description": info.get("description", ""),
                "signal": r["signal"] or "",
                "code": r["code"] or "",
                "name": r["name"] or "",
                "score": float(r["score"]) if r["score"] is not None else None,
                "data_date": str(r["data_date"]) if r["data_date"] else "",
                "selection_reason": r["selection_reason"] or "",
            }
        )
    meta = {
        "source": "db:v2_acad1_pool",
        "pool_date": str(day),
        "rows": len(rows),
        "limit": limit,
        "strategy_info": "db:v2_strategy",
    }
    return rows, meta


def _load_pool_from_cards(
    cards_path: Path, limit: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(cards_path.read_text(encoding="utf-8"))
    cards = data.get("cards") or []
    rows: list[dict[str, Any]] = []
    for card in cards[:limit]:
        sid = card.get("strategy") or ""
        info = _strategy_info(sid)
        code = card.get("candidate_id") or card.get("code") or ""
        rows.append(
            {
                "pool_date": data.get("pool_date") or "",
                "market": (card.get("market") or "").upper(),
                "strategy_id": sid,
                "strategy_name": info["strategy_name"],
                "strategy_type": info["strategy_type"],
                "strategy_description": "",
                "signal": card.get("signal_type") or "",
                "code": code,
                "name": card.get("name") or code,
                "score": float(card["score"]) if card.get("score") is not None else None,
                "data_date": card.get("asof") or "",
                "selection_reason": "",
            }
        )
    meta = {
        "source": f"cards:{cards_path}",
        "pool_date": data.get("pool_date") or "",
        "rows": len(rows),
        "limit": limit,
        "strategy_info": "fallback:static-map",
        "cards_schema": data.get("schema") or "",
    }
    return rows, meta


def _resolve_pool(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if args.pool_date:
        try:
            date.fromisoformat(args.pool_date)
        except ValueError as exc:
            raise RuntimeError(f"--pool-date 需为 YYYY-MM-DD：{args.pool_date}") from exc
    fallback_reason = ""
    if not args.no_db:
        try:
            rows, meta = _load_pool_from_db(args.pool_date, args.limit)
            if rows:
                return rows, meta
            fallback_reason = "DB 池为空"
        except Exception as exc:  # noqa: BLE001 - DB 不可用时允许回退，错误如实记录
            fallback_reason = f"DB 读取失败：{type(exc).__name__}: {exc}"
    else:
        fallback_reason = "--no-db"
    cards_path = Path(args.cards) if args.cards else DEFAULT_CARDS
    if cards_path.exists():
        rows, meta = _load_pool_from_cards(cards_path, args.limit)
        meta["fallback_reason"] = fallback_reason
        return rows, meta
    raise RuntimeError(f"{fallback_reason}；且回退卡片清单不存在：{cards_path}")


# ---------------------------------------------------------------------------
# enrichment 主体
# ---------------------------------------------------------------------------
def _build_enrichment(
    rows: list[dict[str, Any]], k: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        profile = _profile(row)
        plan = _query_plan(profile)
        refs_by_topic: dict[str, list[dict[str, Any]]] = {}
        for topic, lang, query in plan:
            refs_by_topic.setdefault(topic, []).extend(_search_refs(query, topic, lang))
        refs = _select_top(refs_by_topic, k)
        coverage = _method_coverage(refs)

        group_key = (row["market"], row["strategy_id"], row["signal"])
        group = groups.get(group_key)
        if group is None:
            group = {
                "market": row["market"],
                "strategy_id": row["strategy_id"],
                "strategy_name": row["strategy_name"],
                "strategy_type": row["strategy_type"],
                "method_key": profile["method_key"],
                "signal": row["signal"],
                "codes": [],
                "topics": {},
                "queries": plan,
            }
            groups[group_key] = group
        group["codes"].append(row["code"])
        for topic, topic_refs in refs_by_topic.items():
            group["topics"].setdefault(topic, []).extend(topic_refs)

        items.append(
            {
                "pool_date": row["pool_date"],
                "market": row["market"],
                "strategy_id": row["strategy_id"],
                "strategy_name": row["strategy_name"],
                "strategy_type": row["strategy_type"],
                "method_key": profile["method_key"],
                "signal": row["signal"],
                "code": row["code"],
                "name": row["name"],
                "score": row["score"],
                "data_date": row["data_date"],
                "method_coverage": coverage,
                "method_hit": coverage != "none",
                "risk_hit": any(ref["topic"] == "risk" for ref in refs),
                "queries": [
                    {"topic": topic, "lang": lang, "query": query} for topic, lang, query in plan
                ],
                "refs": refs,
            }
        )

    aggregates: list[dict[str, Any]] = []
    for group in groups.values():
        refs = _select_top(group["topics"], k)
        coverage = _method_coverage(refs)
        aggregates.append(
            {
                "market": group["market"],
                "strategy_id": group["strategy_id"],
                "strategy_name": group["strategy_name"],
                "strategy_type": group["strategy_type"],
                "method_key": group["method_key"],
                "signal": group["signal"],
                "items": len(group["codes"]),
                "codes": group["codes"],
                "method_coverage": coverage,
                "method_hit": coverage != "none",
                "risk_hit": any(ref["topic"] == "risk" for ref in refs),
                "queries": [
                    {"topic": topic, "lang": lang, "query": query}
                    for topic, lang, query in group["queries"]
                ],
                "refs": refs,
            }
        )
    return items, aggregates


def _count_refs(refs: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ref in refs:
        key = str(ref.get(field) or "")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _query_stats_summary() -> dict[str, Any]:
    stats = list(_QUERY_STATS.values())
    result: dict[str, Any] = {
        "total_queries": len(stats),
        "total_queries_with_refs": sum(1 for s in stats if s["refs"] > 0),
        "by_lang": {},
        "by_topic": {},
        "rows": sorted(stats, key=lambda s: (s["lang"], s["topic"], s["query"])),
    }
    result["no_hit_queries"] = [
        {"lang": s["lang"], "topic": s["topic"], "query": s["query"]}
        for s in result["rows"]
        if s["refs"] <= 0
    ]
    result["no_hit_queries_count"] = len(result["no_hit_queries"])
    for lang in ("zh", "en"):
        lang_stats = [s for s in stats if s["lang"] == lang]
        hit = [s for s in lang_stats if s["refs"] > 0]
        top_scores = [s["max_score"] for s in hit]
        result["by_lang"][lang] = {
            "queries": len(lang_stats),
            "queries_with_refs": len(hit),
            "query_hit_rate": round(len(hit) / len(lang_stats), 4) if lang_stats else 0.0,
            "raw_hits": sum(s["raw_hits"] for s in lang_stats),
            "refs": sum(s["refs"] for s in lang_stats),
            "best_score": max((s["max_score"] for s in lang_stats), default=0.0),
            "avg_top_ref_score": round(sum(top_scores) / len(top_scores), 4) if top_scores else 0.0,
        }
    for topic in ("method", "risk", "validation", "market"):
        topic_stats = [s for s in stats if s["topic"] == topic]
        hit = [s for s in topic_stats if s["refs"] > 0]
        result["by_topic"][topic] = {
            "queries": len(topic_stats),
            "queries_with_refs": len(hit),
            "query_hit_rate": round(len(hit) / len(topic_stats), 4) if topic_stats else 0.0,
            "refs": sum(s["refs"] for s in topic_stats),
        }
    return result


def _build_summary(
    items: list[dict[str, Any]],
    aggregates: list[dict[str, Any]],
    query_stats: dict[str, Any],
) -> dict[str, Any]:
    all_refs = [ref for item in items for ref in item["refs"]]
    refs_with_market = [dict(ref, market=item["market"]) for item in items for ref in item["refs"]]
    items_with_refs = [item for item in items if item["refs"]]
    items_with_method = [item for item in items if item["method_hit"]]
    items_with_risk = [item for item in items if item["risk_hit"]]
    no_hit_items = [
        {
            "market": item["market"],
            "strategy_id": item["strategy_id"],
            "signal": item["signal"],
            "code": item["code"],
            "name": item["name"],
        }
        for item in items
        if not item["refs"]
    ]
    no_method_groups = [
        {
            "market": g["market"],
            "strategy_id": g["strategy_id"],
            "signal": g["signal"],
            "items": g["items"],
            "codes": g["codes"],
        }
        for g in aggregates
        if not g["method_hit"]
    ]
    coverage_counts: dict[str, int] = {"zh_direct": 0, "en_only": 0, "none": 0}
    for item in items:
        coverage_counts[item["method_coverage"]] = (
            coverage_counts.get(item["method_coverage"], 0) + 1
        )
    total = len(items)
    by_lang = _count_refs(all_refs, "lang")
    return {
        "items_total": total,
        "items_with_refs": len(items_with_refs),
        "item_hit_rate": round(len(items_with_refs) / total, 4) if total else 0.0,
        "items_with_method_refs": len(items_with_method),
        "method_hit_rate": round(len(items_with_method) / total, 4) if total else 0.0,
        "items_with_risk_refs": len(items_with_risk),
        "risk_hit_rate": round(len(items_with_risk) / total, 4) if total else 0.0,
        "refs_total": len(all_refs),
        "unique_videos": len({ref.get("video_id") or ref["source"] for ref in all_refs}),
        "refs_by_market": _count_refs(refs_with_market, "market"),
        "refs_by_topic": _count_refs(all_refs, "topic"),
        "refs_by_lang": by_lang,
        "method_coverage": coverage_counts,
        "query_stats": query_stats,
        "no_hit_items": no_hit_items,
        "no_hit_items_count": len(no_hit_items),
        "no_method_groups": no_method_groups,
        "no_method_groups_count": len(no_method_groups),
        "min_ref_score": MIN_REF_SCORE,
    }


def _build_notes(
    summary: dict[str, Any], pool_meta: dict[str, Any], kb_meta: dict[str, Any]
) -> list[str]:
    zh = summary["query_stats"]["by_lang"]["zh"]
    en = summary["query_stats"]["by_lang"]["en"]
    coverage = summary["method_coverage"]
    notes = [
        "只读 enrichment：本脚本只 SELECT v2_acad1_pool/v2_strategy 并读知识库文件，"
        "不写 DB、不写 v2_stock_research、不改 reviewed/status，不生成方向/结论。",
        "引用只作方法/风险参考，不构成投资建议；每条引用带 video_id + source + snippet，可回溯原始视频/语料块。",
        "知识库语料以英文 YouTube 量化/交易频道为主"
        f"（{kb_meta.get('docs', 0)} 条精选文档 / {kb_meta.get('chunks', 0)} 个 RAG 语料块），"
        "score 为 kb_bridge 的 token 覆盖率（bigram），仅用于排序，不代表语义相似度。",
        f"中文查询 {zh['queries']} 条，命中 {zh['queries_with_refs']} 条"
        f"（{zh['query_hit_rate']:.0%}，命中查询的最好分均值 {zh['avg_top_ref_score']:.2f}）；"
        f"英文查询 {en['queries']} 条，命中 {en['queries_with_refs']} 条"
        f"（{en['query_hit_rate']:.0%}，命中查询的最好分均值 {en['avg_top_ref_score']:.2f}）。"
        "中文查询效果差已如实反映：知识库中文内容仅为少量繁体标题/评论，"
        "中文命中多为只覆盖 1~2 个 bigram 的弱命中（最高分 0.25~0.33），"
        "“缠论/中枢/买卖点”“回测/过拟合”“趋势/动量”等术语直接 0 命中；"
        "引用层按分数选优，因此每项 top3 实际以英文内容为主，中文引用只能作为弱参考。",
        f"方法类覆盖：中文原生直接命中 {coverage.get('zh_direct', 0)} 只、"
        f"仅英文命中 {coverage.get('en_only', 0)} 只、无方法命中 {coverage.get('none', 0)} 只"
        "（每只最多 3 条引用：方法优先 2 条 + 风险 1 条；方法引用须 ≥0.7×最佳分，"
        "弱方法引用让位给风险/验证引用）。",
        "CHAN/CHAN_V7（缠论）组：知识库无缠论专项内容，方法引用来自 market structure / "
        "support resistance / technical analysis 等邻近英文内容，属间接参考，需 ACAD1 人工判断。",
        "市场维度：知识库无 A股/港股/美股 制度与交易规则专项内容，市场引用多为通用市场/交易背景。",
        f"同一（市场, 策略, 信号）组内标的共享同一组方法/风险引用"
        f"（知识库无个股内容，不按个股生成引用）；{summary['items_total']} 只共 "
        f"{summary['refs_total']} 条、去重 {summary['unique_videos']} 个视频源。",
        f"池来源：{pool_meta.get('source', '')}；池日 {pool_meta.get('pool_date', '')}；"
        f"{pool_meta.get('rows', 0)} 行。",
    ]
    zh_no_hit = [
        q["query"] for q in summary["query_stats"].get("no_hit_queries", []) if q["lang"] == "zh"
    ]
    if zh_no_hit:
        notes.append(
            f"中文 0 命中查询（{len(zh_no_hit)} 条）：{'、'.join(zh_no_hit)}；"
            "这些主题在知识库中无对应中文内容，不能据此生成方法引用。"
        )
    if pool_meta.get("fallback_reason"):
        notes.append(f"池回退原因：{pool_meta['fallback_reason']}")
    return notes


def _write_output(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _print_summary(payload: dict[str, Any], elapsed: float, verbose: bool) -> None:
    summary = payload["summary"]
    pool = payload["pool"]
    kb_meta = payload["kb"]
    print(
        f"[kb_enrich] pool={pool.get('source', '')} date={pool.get('pool_date', '')} "
        f"rows={pool.get('rows', 0)}"
    )
    print(
        f"[kb_enrich] kb docs={kb_meta.get('docs', 0)} chunks={kb_meta.get('chunks', 0)} "
        f"min_score={MIN_REF_SCORE:.2f} elapsed={elapsed:.1f}s"
    )
    print(
        f"[kb_enrich] items={summary['items_total']} with_refs={summary['items_with_refs']}"
        f"({summary['item_hit_rate']:.0%}) method={summary['items_with_method_refs']}"
        f"({summary['method_hit_rate']:.0%}) risk={summary['items_with_risk_refs']}"
        f"({summary['risk_hit_rate']:.0%})"
    )
    zh = summary["query_stats"]["by_lang"]["zh"]
    en = summary["query_stats"]["by_lang"]["en"]
    print(
        f"[kb_enrich] queries: zh {zh['queries']} hit {zh['queries_with_refs']} "
        f"({zh['query_hit_rate']:.0%}, avg_top={zh['avg_top_ref_score']:.2f}), en {en['queries']} "
        f"hit {en['queries_with_refs']} ({en['query_hit_rate']:.0%}, "
        f"avg_top={en['avg_top_ref_score']:.2f})"
    )
    coverage = summary["method_coverage"]
    print(
        f"[kb_enrich] coverage: zh_direct={coverage.get('zh_direct', 0)} "
        f"en_only={coverage.get('en_only', 0)} none={coverage.get('none', 0)}; "
        f"no_hit_items={summary['no_hit_items_count']}; "
        f"no_method_groups={summary['no_method_groups_count']}"
    )
    if verbose:
        print("[kb_enrich] --- items ---")
        for item in payload["items"]:
            print(
                f"  [{item['market']}] {item['code']} {item['strategy_id']}/{item['signal']} "
                f"({item['name']}) refs={len(item['refs'])} "
                f"coverage={item['method_coverage']}"
            )
            for ref in item["refs"]:
                print(
                    f"      - {TOPIC_LABELS.get(ref['topic'], ref['topic'])} "
                    f"[{ref['video_id']}] [{ref['score']:.2f}] {ref['title'][:60]}"
                )
    else:
        print("[kb_enrich] samples:")
        for item in payload["items"][:3]:
            titles = " | ".join(ref["title"][:40] for ref in item["refs"])
            print(
                f"  [{item['market']}] {item['code']} {item['strategy_id']}/{item['signal']} "
                f"-> {titles or '(无引用)'}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ACAD1 知识库方法/风险引用层（只读，不写 DB/不改卡/不给结论）"
    )
    parser.add_argument("--pool-date", default=None, help="池日 YYYY-MM-DD（默认最新池）")
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="最多处理多少只（默认 30）"
    )
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="每项引用条数（默认 3）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 JSON 路径")
    parser.add_argument("--cards", default=None, help="DB 不可用时回退的 ACAD1 卡片清单路径")
    parser.add_argument("--no-db", action="store_true", help="跳过 DB，直接用卡片清单")
    parser.add_argument("--verbose", action="store_true", help="打印逐项引用")
    args = parser.parse_args(argv)

    started = time.time()
    try:
        rows, pool_meta = _resolve_pool(args)
    except Exception as exc:  # noqa: BLE001 - 池不可用时报错退出，不静默伪造
        print(f"[kb_enrich] 无法获取 ACAD1 池：{exc}", file=sys.stderr)
        return 2
    if not rows:
        print("[kb_enrich] ACAD1 池为空，无内容可 enrichment", file=sys.stderr)
        return 2
    if args.k < 1:
        print("[kb_enrich] --k 必须 >= 1", file=sys.stderr)
        return 2

    docs = kb_bridge._load_docs()
    chunks = kb_bridge._load_chunks()
    kb_meta = {
        "root": str(kb_bridge.KB),
        "lite_index": str(kb_bridge.LITE),
        "rag_corpus": str(kb_bridge.CORPUS),
        "docs": len(docs),
        "chunks": len(chunks),
        "bridge": "scripts/kb_bridge.py",
        "top_k_fetch": TOP_K_FETCH,
        "min_ref_score": MIN_REF_SCORE,
    }
    if not docs and not chunks:
        print(
            f"[kb_enrich] 知识库索引缺失（{kb_bridge.LITE} / {kb_bridge.CORPUS}），无法生成引用",
            file=sys.stderr,
        )
        return 1

    items, aggregates = _build_enrichment(rows, args.k)
    query_stats = _query_stats_summary()
    summary = _build_summary(items, aggregates, query_stats)
    payload: dict[str, Any] = {
        "schema": OUT_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy": {
            "read_only": True,
            "writes_db": False,
            "writes_v2_stock_research": False,
            "modifies_review_status": False,
            "auto_conclusion": False,
            "disclaimer": "仅方法/风险引用层，不构成投资结论；引用出处以知识库原文为准。",
        },
        "pool": pool_meta,
        "kb": kb_meta,
        "summary": summary,
        "items": items,
        "strategy_aggregate": aggregates,
        "notes": _build_notes(summary, pool_meta, kb_meta),
    }
    out_path = Path(args.out)
    _write_output(out_path, payload)
    elapsed = time.time() - started
    _print_summary(payload, elapsed, args.verbose)
    print(f"[kb_enrich] saved: {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
