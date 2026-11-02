"""ACAD1 复核证据包（只读）：KB 方法引用 + 基本面证据 + 研究卡事实。

把上游三份只读产物合成为一份人工复核用证据包：
- C:\\Temp\\acad1_kb_refs.json（P2-6 知识库方法/风险引用层）
- C:\\Temp\\acad1_fundamentals.json（P2-5 行业 PIT / 公告 / 新闻证据）
- C:\\Temp\\acad1_card_evidence_preview.json（P3-2 卡证据预览）
- 最新 ACAD1 池 / 研究卡（postgres，只读 SELECT，读取时快照）

输出：
- C:\\Temp\\acad1_evidence_bundle.json：逐票卡事实 + 行业/公告/新闻 + 方法引用 + 缺口 + 溯源
- C:\\Temp\\acad1_evidence_bundle.md：同一内容的人读版

约束：
- 只读：仅 SELECT v2_acad1_pool / v2_strategy / v2_stock_research，不写 DB、不写
  v2_stock_research、不改 reviewed/status；不生成方向/评级/买卖结论。
- 覆盖最新 ACAD1 池 30 只；缺口按 kind 如实标注，不推断、不补造。
- 卡面 direction/rating/thesis 等字段是上游研究卡的机器事实草稿快照，本脚本原样引用，
  不解读、不修改、不据此给出结论。

用法：
  python scripts/acad1_evidence_bundle.py
  python scripts/acad1_evidence_bundle.py --pool-date 2026-10-31 --limit 30
  python scripts/acad1_evidence_bundle.py --no-db        # DB 不可用时用 preview 快照兜底
  python scripts/acad1_evidence_bundle.py --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
for _path in (_SCRIPT_DIR, _PROJECT_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

OUT_SCHEMA = "acad1_evidence_bundle/v1"
DEFAULT_KB_REFS = Path(r"C:\Temp\acad1_kb_refs.json")
DEFAULT_FUNDAMENTALS = Path(r"C:\Temp\acad1_fundamentals.json")
DEFAULT_PREVIEW = Path(r"C:\Temp\acad1_card_evidence_preview.json")
DEFAULT_OUT_JSON = Path(r"C:\Temp\acad1_evidence_bundle.json")
DEFAULT_OUT_MD = Path(r"C:\Temp\acad1_evidence_bundle.md")
DEFAULT_LIMIT = 30

MARKET_ORDER = {"CN": 0, "HK": 1, "US": 2, "CRYPTO": 3}

CARD_FACT_FIELDS = (
    "id",
    "pool_date",
    "strategy_id",
    "code",
    "name",
    "direction",
    "score",
    "rating",
    "thesis",
    "catalysts",
    "risks",
    "buy_zone_low",
    "buy_zone_high",
    "target_price",
    "stop_price",
    "invalidation",
    "executable",
    "exec_note",
    "status",
    "reviewer",
    "review_note",
    "reviewed_at",
    "handoff_at",
    "data_date",
    "updated_at",
)
CARD_DRIFT_FIELDS = (
    "direction",
    "rating",
    "status",
    "reviewer",
    "review_note",
    "data_date",
    "updated_at",
)
CARD_HASH_FIELDS = (
    "id",
    "strategy_id",
    "code",
    "status",
    "direction",
    "rating",
    "reviewer",
    "review_note",
    "data_date",
    "updated_at",
)

GAP_KIND_LEGEND = {
    "not_applicable": "数据源不覆盖该市场（非抓取失败）",
    "missing": "证据匹配缺失（真缺失）",
    "source_missing": "上游输入文件/数据源缺失（真缺失）",
    "no_data": "窗口内 0 条（真实无记录/未落仓，非缺失值）",
    "no_match": "新闻关键词未命中（可能确无新闻或别名不全）",
    "no_alias": "无名称别名，无法归属新闻",
    "stale": "快照与当前池/卡不一致（需重跑上游）",
    "partial_source": "上游快照字段不完整（如 DB 不可用时的卡面）",
}
TRUE_MISSING_KINDS = {"missing", "source_missing"}

FIELD_LABELS = {
    "industry": "行业",
    "announcements": "公告",
    "news": "新闻",
    "card": "研究卡",
    "kb_refs": "知识库引用",
    "fundamentals": "基本面证据",
    "preview": "卡证据预览",
    "pool_snapshot": "池快照",
}

TOPIC_LABELS = {
    "method": "方法",
    "risk": "风险",
    "validation": "验证/过拟合",
    "market": "市场背景",
}

EVIDENCE_LEVEL_LABELS = {"full": "完整", "partial": "部分", "none": "无"}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _json_safe(value: Any) -> Any:
    """把 DB/输入中的 date/datetime/Decimal 转成 JSON 安全类型。"""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _clip(text: Any, limit: int = 220) -> str:
    """压成单行并截断，用于 markdown 摘要展示。"""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_obj(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _norm_code(market: str, code: str) -> str:
    """代码归一：CN 6 位/带交易所前缀统一为 000001.SZ 形式；HK 5 位；其他原样。"""
    raw = (code or "").strip().upper()
    mkt = (market or "").strip().upper()
    if mkt == "CN":
        if raw.startswith("SH") and len(raw) == 8:
            return raw[2:] + ".SH"
        if raw.startswith("SZ") and len(raw) == 8:
            return raw[2:] + ".SZ"
        if raw.endswith(".SH") or raw.endswith(".SZ"):
            return raw
        if len(raw) == 6 and raw.isdigit():
            return raw + (".SH" if raw.startswith("6") else ".SZ")
        return raw
    if mkt == "HK" and len(raw) == 5 and raw.isdigit():
        return raw
    return raw


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"{label} 不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"{label} 顶层不是 JSON object：{path}")
    return data


def _market_sort_key(market: str) -> int:
    return MARKET_ORDER.get((market or "").upper(), 99)


def _gap(
    field: str,
    kind: str,
    reason: str,
    source: str,
) -> dict[str, Any]:
    return {
        "field": field,
        "field_label": FIELD_LABELS.get(field, field),
        "kind": kind,
        "kind_label": GAP_KIND_LEGEND.get(kind, kind),
        "reason": reason,
        "source": source,
        "true_missing": kind in TRUE_MISSING_KINDS,
    }


def _merge_gaps(*gap_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并多来源缺口；同 (field, kind, reason) 去重并聚合 sources。"""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for group in gap_groups:
        for gap in group:
            key = (gap["field"], gap["kind"], gap["reason"])
            if key in merged:
                sources = merged[key]["sources"]
                if gap.get("source") and gap["source"] not in sources:
                    sources.append(gap["source"])
                continue
            item = dict(gap)
            item["sources"] = [gap["source"]] if gap.get("source") else []
            merged[key] = item
            order.append(key)
    return [merged[key] for key in order]


def _gap_from_fundamentals(fund_item: dict[str, Any] | None) -> list[dict[str, Any]]:
    """fundamentals 的 gaps 是 {field: {kind, reason}} dict。"""
    if not fund_item:
        return []
    gaps = fund_item.get("gaps") or {}
    result = []
    for field, detail in gaps.items():
        if not isinstance(detail, dict):
            continue
        result.append(
            _gap(field, detail.get("kind") or "missing", detail.get("reason") or "", "fundamentals")
        )
    return result


def _gap_from_preview(preview_item: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not preview_item:
        return []
    result = []
    for detail in preview_item.get("gaps") or []:
        if not isinstance(detail, dict):
            continue
        result.append(
            _gap(
                detail.get("field") or "unknown",
                detail.get("kind") or "missing",
                detail.get("reason") or "",
                "preview",
            )
        )
    return result


# ---------------------------------------------------------------------------
# DB 只读快照
# ---------------------------------------------------------------------------
def _load_db_snapshot(pool_date: str | None, limit: int) -> dict[str, Any]:
    """只读读取最新 ACAD1 池 + 研究卡（SELECT only，绝不 commit / 写入）。"""
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
    sql_executed: list[str] = []
    try:
        with engine.connect() as conn:  # 只读连接：本脚本绝不 commit / 写入
            sql_executed.append("SELECT max(pool_date) FROM v2_acad1_pool")
            day = (
                pool_date
                or conn.execute(sa.text("SELECT max(pool_date) FROM v2_acad1_pool")).scalar()
            )
            if not day:
                raise RuntimeError("v2_acad1_pool 无数据")
            sql_executed.append("SELECT ... FROM v2_acad1_pool WHERE pool_date = :day LIMIT :limit")
            pool_rows = (
                conn.execute(
                    sa.text("""
                    SELECT pool_date, strategy_id, code, name, market, signal, score,
                           selection_reason, selected_by, status, data_date
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
            sql_executed.append(
                "SELECT strategy_id, strategy_name, market, strategy_type FROM v2_strategy"
            )
            strategy_rows = (
                conn.execute(
                    sa.text(
                        "SELECT strategy_id, strategy_name, market, strategy_type "
                        "FROM v2_strategy ORDER BY strategy_id"
                    )
                )
                .mappings()
                .all()
            )
            sql_executed.append("SELECT max(pool_date) FROM v2_stock_research")
            card_day = conn.execute(
                sa.text("SELECT max(pool_date) FROM v2_stock_research")
            ).scalar()
            sql_executed.append("SELECT ... FROM v2_stock_research WHERE pool_date = :card_day")
            card_rows = (
                conn.execute(
                    sa.text("""
                    SELECT id, pool_date, strategy_id, code, name, direction, score, rating,
                           thesis, catalysts, risks, buy_zone_low, buy_zone_high, target_price,
                           stop_price, invalidation, executable, exec_note, status, reviewer,
                           review_note, reviewed_at, handoff_at, data_date, updated_at
                    FROM v2_stock_research
                    WHERE pool_date = :card_day
                    ORDER BY id
                """),
                    {"card_day": card_day},
                )
                .mappings()
                .all()
            )
    finally:
        engine.dispose()

    strategies = {
        row["strategy_id"]: {
            "strategy_name": row["strategy_name"] or "",
            "strategy_type": row["strategy_type"] or "",
            "market": row["market"] or "",
        }
        for row in strategy_rows
        if row["strategy_id"]
    }
    return {
        "source": "postgres:zhice_os_v2.v2_acad1_pool + v2_stock_research（只读 SELECT）",
        "pool_date": str(day),
        "pool_rows": [_json_safe(dict(row)) for row in pool_rows],
        "card_pool_date": str(card_day) if card_day else "",
        "cards": [_json_safe(dict(row)) for row in card_rows],
        "strategies": strategies,
        "sql_executed": sql_executed,
    }


def _preview_input_path(preview: dict[str, Any], name: str) -> str:
    value = (preview.get("inputs") or {}).get(name)
    if isinstance(value, dict):
        return str(value.get("path") or value.get("file") or "")
    return str(value or "")


def _load_preview_pool_fallback(preview: dict[str, Any], limit: int) -> dict[str, Any]:
    """DB 不可用：用 preview 的池/卡快照兜底（卡字段不完整，需如实标注）。"""
    primary = [item for item in preview.get("items") or [] if item.get("in_primary_pool")]
    rows = []
    cards = []
    for item in primary[:limit]:
        pool = item.get("pool") or {}
        card = item.get("card") or {}
        rows.append(_json_safe(pool))
        card_record = dict(card)
        card_record["_from_preview_snapshot"] = True
        cards.append(_json_safe(card_record))
    return {
        "source": f"preview_snapshot:{_preview_input_path(preview, 'fundamentals')}",
        "pool_date": (preview.get("scope") or {}).get("pool_date") or "",
        "pool_rows": rows,
        "card_pool_date": (preview.get("scope") or {}).get("pool_date") or "",
        "cards": cards,
        "strategies": {},
        "sql_executed": ["<--no-db> 未访问 DB；池/卡取自 preview 快照"],
        "fallback": True,
    }


def _match_cards(
    pool_rows: list[dict[str, Any]], cards: list[dict[str, Any]]
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """池行 → 研究卡：优先 (策略,原始代码)，其次 (策略,归一代码)，最后 (归一代码)。"""
    exact: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for card in cards:
        key = ((card.get("strategy_id") or ""), (card.get("code") or "").upper())
        exact.setdefault(key, []).append(card)
    matched: dict[tuple[str, str], dict[str, Any]] = {}
    matched_by: dict[tuple[str, str], str] = {}
    for row in pool_rows:
        sid = row.get("strategy_id") or ""
        raw_code = (row.get("code") or "").upper()
        market = row.get("market") or ""
        key = (raw_code, sid)
        candidates = exact.get((sid, raw_code))
        how = "strategy+code"
        if not candidates:
            target = _norm_code(market, row.get("code") or "")
            candidates = [
                card
                for card in cards
                if (card.get("strategy_id") or "") == sid
                and _norm_code(market, card.get("code") or "") == target
            ]
            how = "strategy+normalized_code"
        if not candidates:
            target = _norm_code(market, row.get("code") or "")
            candidates = [
                card for card in cards if _norm_code(market, card.get("code") or "") == target
            ]
            how = "code_only"
        if candidates:
            chosen = sorted(candidates, key=lambda c: c.get("id") or 0)[0]
            matched[key] = chosen
            matched_by[key] = how
    return matched, [
        {
            "pool_key": list(key),
            "matched_by": matched_by[key],
            "card_id": card.get("id"),
            "card_code": card.get("code"),
        }
        for key, card in matched.items()
    ]


def _card_drift(
    db_card: dict[str, Any] | None, preview_card: dict[str, Any] | None
) -> dict[str, Any]:
    """DB 卡快照 vs preview 卡快照 的字段漂移。"""
    drift: dict[str, Any] = {}
    if not db_card or not preview_card:
        return drift
    for field in CARD_DRIFT_FIELDS:
        before = preview_card.get(field)
        after = db_card.get(field)
        if str(before if before is not None else "") != str(after if after is not None else ""):
            drift[field] = {"preview": before, "db": after}
    return drift


# ---------------------------------------------------------------------------
# 逐票证据组装
# ---------------------------------------------------------------------------
def _index_by_code(items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        market = (item.get("market") or "").upper()
        code = _norm_code(market, item.get("code") or "")
        index[(market, code)] = item
    return index


def _index_kb(
    kb: dict[str, Any],
) -> tuple[dict[tuple[str, str, str, str], dict], dict[tuple[str, str], list]]:
    exact: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    by_code: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in kb.get("items") or []:
        market = (item.get("market") or "").upper()
        code = _norm_code(market, item.get("code") or "")
        exact[(market, code, item.get("strategy_id") or "", item.get("signal") or "")] = item
        by_code.setdefault((market, code), []).append(item)
    return exact, by_code


def _industry_view(
    industry: dict[str, Any] | None, fallback_reason: str | None = None
) -> dict[str, Any]:
    if not industry:
        return {"status": "missing", "display": "—（缺失：基本面证据无该票）", "value": None}
    status = industry.get("status") or "missing"
    value = industry.get("value") or {}
    if status == "covered":
        display = "{}（{}）PIT，成分 in_date={}，PIT 滞后 {} 天".format(
            value.get("sw_l1_name") or "—",
            value.get("sw_l1_code") or "—",
            value.get("member_in_date") or "—",
            industry.get("pit_lag_days") if industry.get("pit_lag_days") is not None else "—",
        )
    else:
        display = "—（{}：{}）".format(
            status, industry.get("reason") or fallback_reason or "无行业证据"
        )
    return {
        "status": status,
        "display": display,
        "value": value or None,
        "pit_lag_days": industry.get("pit_lag_days"),
        "crosscheck": industry.get("crosscheck_index_member_all"),
        "reason": industry.get("reason") or fallback_reason,
    }


def _announcements_view(
    ann: dict[str, Any] | None, fallback_reason: str | None = None
) -> dict[str, Any]:
    if not ann:
        return {"status": "missing", "display": "—（缺失：基本面证据无该票）", "value": None}
    status = ann.get("status") or "missing"
    value = ann.get("value") or {}
    if status == "covered":
        display = "{} 条；最近 {}《{}》".format(
            value.get("count"),
            value.get("latest_date") or "—",
            _clip(value.get("latest_title") or "—", 60),
        )
    elif status == "no_data":
        display = "0 条（窗口内真实无记录）"
    else:
        display = "—（{}：{}）".format(status, ann.get("reason") or fallback_reason or "无公告证据")
    return {
        "status": status,
        "display": display,
        "value": value or None,
        "window": ann.get("window"),
        "history_check": ann.get("history_check"),
        "pit_note": ann.get("pit_note"),
        "reason": ann.get("reason") or fallback_reason,
    }


def _news_view(news: dict[str, Any] | None, fallback_reason: str | None = None) -> dict[str, Any]:
    if not news:
        return {"status": "missing", "display": "—（缺失：基本面证据无该票）", "value": None}
    status = news.get("status") or "missing"
    value = news.get("value") or {}
    latest = value.get("latest") or {}
    if status == "covered":
        display = "{} 条（来源 {}）；最近 {}《{}》".format(
            value.get("count"),
            value.get("count_source") or "—",
            latest.get("time") or "—",
            _clip(latest.get("title") or "—", 60),
        )
    elif status == "no_match":
        display = "—（no_match：窗口内关键词未命中，可能确无相关新闻或别名不全，不推断）"
    elif status == "no_data":
        display = "0 条（窗口内真实无记录）"
    else:
        display = "—（{}：{}）".format(
            status, news.get("reason") or fallback_reason or "无新闻证据"
        )
    return {
        "status": status,
        "display": display,
        "value": value or None,
        "per_source": news.get("per_source"),
        "match_method": news.get("match_method"),
        "caveat": news.get("caveat"),
        "window": news.get("window"),
        "reason": news.get("reason") or fallback_reason,
    }


def _method_refs_view(kb_item: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not kb_item:
        return []
    refs = []
    for ref in kb_item.get("refs") or []:
        refs.append(
            {
                "title": ref.get("title") or "",
                "video_id": ref.get("video_id") or "",
                "source": ref.get("source") or "",
                "snippet": ref.get("snippet") or "",
                "score": ref.get("score"),
                "topic": ref.get("topic") or "",
                "topic_label": TOPIC_LABELS.get(ref.get("topic") or "", ref.get("topic") or ""),
                "lang": ref.get("lang") or "",
                "query": ref.get("query") or "",
            }
        )
    return refs


def _card_view(
    card: dict[str, Any] | None, matched_by: str | None, drift: dict[str, Any], fallback: bool
) -> dict[str, Any]:
    if not card:
        return {
            "matched": False,
            "matched_by": None,
            "source": "none",
            "completeness": "none",
            "fields": {},
            "drift_from_preview": {},
        }
    fields = {field: card.get(field) for field in CARD_FACT_FIELDS if field in card}
    if fallback or card.get("_from_preview_snapshot"):
        completeness = "preview_fields_only"
    else:
        completeness = "full_db"
    return {
        "matched": True,
        "matched_by": matched_by,
        "source": "preview_snapshot"
        if card.get("_from_preview_snapshot")
        else "db:v2_stock_research",
        "completeness": completeness,
        "snapshot_card_pool_date": card.get("pool_date"),
        "fields": fields,
        "drift_from_preview": drift,
    }


def _evidence_level_of(
    preview_item: dict[str, Any] | None,
    industry: dict[str, Any],
    announcements: dict[str, Any],
    news: dict[str, Any],
) -> tuple[str, str]:
    if preview_item and preview_item.get("evidence_level"):
        return preview_item["evidence_level"], "preview"
    applicable = (
        ["news"]
        if (industry.get("status") == "not_applicable")
        else [
            "industry",
            "announcements",
            "news",
        ]
    )
    status_map = {
        "industry": industry.get("status"),
        "announcements": announcements.get("status"),
        "news": news.get("status"),
    }
    covered = [f for f in applicable if status_map.get(f) == "covered"]
    if len(covered) == len(applicable):
        return "full", "computed"
    if covered:
        return "partial", "computed"
    return "none", "computed"


def _build_items(
    pool_rows: list[dict[str, Any]],
    db: dict[str, Any],
    kb: dict[str, Any],
    fundamentals: dict[str, Any],
    preview: dict[str, Any],
    limit: int,
    input_paths: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    input_paths = input_paths or {
        "kb_refs": str(DEFAULT_KB_REFS),
        "fundamentals": str(DEFAULT_FUNDAMENTALS),
        "preview": str(DEFAULT_PREVIEW),
    }
    kb_exact, kb_by_code = _index_kb(kb)
    fund_all = _index_by_code(fundamentals.get("items") or [])
    fund_primary = _index_by_code(
        [item for item in fundamentals.get("items") or [] if item.get("in_primary_pool")]
    )
    preview_primary = _index_by_code(
        [item for item in preview.get("items") or [] if item.get("in_primary_pool")]
    )
    preview_all = _index_by_code(preview.get("items") or [])
    card_map, card_match_log = _match_cards(pool_rows, db.get("cards") or [])
    strategies = db.get("strategies") or {}

    ordered = sorted(
        pool_rows[:limit],
        key=lambda row: (
            _market_sort_key(row.get("market") or ""),
            row.get("strategy_id") or "",
            _norm_code(row.get("market") or "", row.get("code") or ""),
        ),
    )
    items: list[dict[str, Any]] = []
    cross_source: dict[str, Any] = {
        "snapshot_pool_dates": {
            "db_pool": db.get("pool_date"),
            "kb_refs": (kb.get("pool") or {}).get("pool_date"),
            "fundamentals": ((fundamentals.get("pool") or {}).get("pool_date")),
            "preview": (preview.get("scope") or {}).get("pool_date"),
            "db_cards": db.get("card_pool_date"),
        },
        "fundamentals_vs_preview_discrepancies": [],
        "card_drift_items": [],
        "kb_strategy_signal_mismatches": [],
    }
    snapshot_dates = {str(value) for value in cross_source["snapshot_pool_dates"].values() if value}
    cross_source["all_pool_dates_consistent"] = len(snapshot_dates) <= 1
    cross_source["db_pool_vs_card_date_consistent"] = (db.get("pool_date") or "") == (
        db.get("card_pool_date") or ""
    )
    pool_codes = {
        (
            (row.get("market") or "").upper(),
            _norm_code(row.get("market") or "", row.get("code") or ""),
        )
        for row in pool_rows
    }
    cross_source["pool_code_set"] = {
        "db_pool": len(pool_codes),
        "fundamentals_primary": len(fund_primary),
        "preview_primary": len(preview_primary),
        "kb_refs": len(kb_by_code),
        "same_set": {
            "fundamentals_primary": set(fund_primary) == pool_codes,
            "preview_primary": set(preview_primary) == pool_codes,
            "kb_refs": set(kb_by_code) == pool_codes,
        },
    }

    for index, row in enumerate(ordered, 1):
        market = (row.get("market") or "").upper()
        raw_code = row.get("code") or ""
        code = _norm_code(market, raw_code)
        sid = row.get("strategy_id") or ""
        signal = row.get("signal") or ""
        key = (market, code)

        kb_item = kb_exact.get((market, code, sid, signal))
        kb_match_note = ""
        if kb_item is None:
            candidates = kb_by_code.get(key) or []
            if candidates:
                kb_item = candidates[0]
                kb_match_note = "strategy/signal 与 KB 引用快照不一致，按代码回退匹配（可能 stale）"
                cross_source["kb_strategy_signal_mismatches"].append(
                    {
                        "market": market,
                        "code": code,
                        "pool": {"strategy_id": sid, "signal": signal},
                        "kb": {
                            "strategy_id": kb_item.get("strategy_id"),
                            "signal": kb_item.get("signal"),
                        },
                    }
                )

        fund_primary_item = fund_primary.get(key)
        fund_any_item = fund_all.get(key)
        preview_item = preview_primary.get(key) or preview_all.get(key)

        db_card = card_map.get((raw_code.upper(), sid))
        matched_by = None
        if db_card is not None:
            for entry in card_match_log:
                if entry["pool_key"] == [raw_code.upper(), sid]:
                    matched_by = entry["matched_by"]
                    break
        preview_card = (preview_item or {}).get("card")

        bundle_gaps: list[dict[str, Any]] = []
        if not kb_item:
            bundle_gaps.append(
                _gap("kb_refs", "missing", "KB 引用快照无该票记录，本票无方法引用", "bundle")
            )
        if not fund_primary_item:
            bundle_gaps.append(
                _gap(
                    "fundamentals",
                    "source_missing",
                    "基本面证据缺该票（主池 30 只应覆盖）",
                    "bundle",
                )
            )
        if not preview_item:
            bundle_gaps.append(
                _gap("preview", "source_missing", "卡证据预览缺该票（主池 30 只应覆盖）", "bundle")
            )
        if not db_card:
            bundle_gaps.append(
                _gap("card", "missing", "v2_stock_research 无该票/该策略研究卡", "bundle")
            )
        elif db.get("fallback"):
            bundle_gaps.append(
                _gap(
                    "card",
                    "partial_source",
                    "DB 不可用，卡面仅 preview 快照字段（无 thesis/catalysts/risks 全文）",
                    "bundle",
                )
            )
        if kb_match_note:
            bundle_gaps.append(_gap("kb_refs", "stale", kb_match_note, "bundle"))

        industry_source = (preview_item or {}).get("industry") or (
            fund_primary_item or fund_any_item or {}
        ).get("industry")
        ann_source = (preview_item or {}).get("announcements") or (
            fund_primary_item or fund_any_item or {}
        ).get("announcements")
        news_source = (preview_item or {}).get("news") or (
            fund_primary_item or fund_any_item or {}
        ).get("news")
        preview_gap_reasons = {
            gap["field"]: gap["reason"] for gap in _gap_from_preview(preview_item)
        }
        industry = _industry_view(industry_source, preview_gap_reasons.get("industry"))
        announcements = _announcements_view(ann_source, preview_gap_reasons.get("announcements"))
        news = _news_view(news_source, preview_gap_reasons.get("news"))

        drift = _card_drift(db_card, preview_card)
        if drift:
            cross_source["card_drift_items"].append(
                {"market": market, "code": code, "strategy_id": sid, "drift": drift}
            )
            bundle_gaps.append(
                _gap(
                    "card",
                    "stale",
                    "DB 卡面与 preview 卡快照不一致：{}".format("、".join(drift.keys())),
                    "bundle",
                )
            )

        # 基本面 vs 预览 交叉核对（只记录差异，不修正任何一边）
        if fund_primary_item and preview_item:
            fund_industry = ((fund_primary_item.get("industry") or {}).get("value") or {}).get(
                "sw_l1_name"
            )
            preview_industry = ((preview_item.get("industry") or {}).get("value") or {}).get(
                "sw_l1_name"
            )
            fund_ann = (fund_primary_item.get("announcements") or {}).get("value") or {}
            preview_ann = (preview_item.get("announcements") or {}).get("value") or {}
            fund_news = (fund_primary_item.get("news") or {}).get("value") or {}
            preview_news = (preview_item.get("news") or {}).get("value") or {}
            diffs = {}
            if fund_industry != preview_industry:
                diffs["industry.sw_l1_name"] = {
                    "fundamentals": fund_industry,
                    "preview": preview_industry,
                }
            if fund_ann.get("count") != preview_ann.get("count"):
                diffs["announcements.count"] = {
                    "fundamentals": fund_ann.get("count"),
                    "preview": preview_ann.get("count"),
                }
            if fund_news.get("count") != preview_news.get("count"):
                diffs["news.count"] = {
                    "fundamentals": fund_news.get("count"),
                    "preview": preview_news.get("count"),
                }
            if diffs:
                cross_source["fundamentals_vs_preview_discrepancies"].append(
                    {"market": market, "code": code, "diffs": diffs}
                )

        gaps = _merge_gaps(
            _gap_from_preview(preview_item),
            _gap_from_fundamentals(fund_primary_item or fund_any_item),
            bundle_gaps,
        )
        evidence_level, level_source = _evidence_level_of(
            preview_item, industry, announcements, news
        )
        strategy_name = (strategies.get(sid) or {}).get("strategy_name") or (
            (kb_item or {}).get("strategy_name") or sid
        )
        name = (
            (fund_primary_item or fund_any_item or {}).get("name")
            or (db_card or {}).get("name")
            or row.get("name")
            or code
        )
        if name in (code, raw_code):
            name = ((preview_item or {}).get("name")) or name

        item = {
            "seq": f"B{index:02d}",
            "market": market,
            "code": code,
            "pool_code": raw_code,
            "dw_code": (fund_primary_item or fund_any_item or {}).get("dw_code") or code,
            "name": name,
            "strategy_id": sid,
            "strategy_name": strategy_name,
            "signal": signal,
            "score": _num(row.get("score")),
            "pool": {
                "pool_date": row.get("pool_date"),
                "market": market,
                "code": raw_code,
                "name": row.get("name"),
                "strategy_id": sid,
                "signal": signal,
                "score": _num(row.get("score")),
                "data_date": row.get("data_date"),
                "status": row.get("status"),
                "selection_reason": row.get("selection_reason"),
            },
            "card": _card_view(
                db_card if not db.get("fallback") else preview_card,
                matched_by if not db.get("fallback") else "preview_snapshot",
                drift,
                bool(db.get("fallback")),
            ),
            "industry": industry,
            "announcements": announcements,
            "news": news,
            "method_refs": _method_refs_view(kb_item),
            "method_coverage": (kb_item or {}).get("method_coverage"),
            "kb_refs_generated_at": kb.get("generated_at"),
            "evidence_level": evidence_level,
            "evidence_level_source": level_source,
            "evidence_level_applicable_fields": (preview_item or {}).get("applicable_fields"),
            "gaps": gaps,
            "gap_count": len(gaps),
            "no_conclusion": True,
            "sources": {
                "kb_refs": {
                    "file": input_paths["kb_refs"],
                    "generated_at": kb.get("generated_at"),
                    "matched": kb_item is not None,
                },
                "fundamentals": {
                    "file": input_paths["fundamentals"],
                    "generated_at": fundamentals.get("generated_at"),
                    "matched": fund_primary_item is not None,
                },
                "preview": {
                    "file": input_paths["preview"],
                    "generated_at": preview.get("generated_at"),
                    "matched": preview_item is not None,
                },
                "card": {
                    "source": "db:v2_stock_research"
                    if not db.get("fallback")
                    else "preview_snapshot",
                    "card_id": (db_card or preview_card or {}).get("id"),
                },
            },
        }
        items.append(item)
    return items, cross_source


# ---------------------------------------------------------------------------
# 汇总 / 覆盖 / 缺口
# ---------------------------------------------------------------------------
def _count_status(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = (item.get(field) or {}).get("status") or "missing"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _build_summary(
    items: list[dict[str, Any]],
    db: dict[str, Any],
    kb: dict[str, Any],
    fundamentals: dict[str, Any],
    preview: dict[str, Any],
    cross_source: dict[str, Any],
) -> dict[str, Any]:
    all_refs = [ref for item in items for ref in item.get("method_refs") or []]
    refs_by_topic: dict[str, int] = {}
    refs_by_lang: dict[str, int] = {}
    for ref in all_refs:
        refs_by_topic[ref["topic"]] = refs_by_topic.get(ref["topic"], 0) + 1
        refs_by_lang[ref["lang"]] = refs_by_lang.get(ref["lang"], 0) + 1
    coverage_counts: dict[str, int] = {}
    for item in items:
        level = item.get("evidence_level") or "none"
        coverage_counts[level] = coverage_counts.get(level, 0) + 1
    gap_by_kind: dict[str, int] = {}
    gap_by_field: dict[str, int] = {}
    gap_by_kind_label: dict[str, str] = {}
    true_missing = 0
    items_with_gaps = 0
    total_gap_entries = 0
    for item in items:
        if item["gaps"]:
            items_with_gaps += 1
        for gap in item["gaps"]:
            total_gap_entries += 1
            gap_by_kind[gap["kind"]] = gap_by_kind.get(gap["kind"], 0) + 1
            gap_by_field[gap["field"]] = gap_by_field.get(gap["field"], 0) + 1
            gap_by_kind_label[gap["kind"]] = gap["kind_label"]
            if gap["true_missing"]:
                true_missing += 1
    by_market: dict[str, int] = {}
    for item in items:
        by_market[item["market"]] = by_market.get(item["market"], 0) + 1

    matched_cards = sum(1 for item in items if item["card"]["matched"])
    matched_kb = sum(1 for item in items if item["sources"]["kb_refs"]["matched"])
    matched_fund = sum(1 for item in items if item["sources"]["fundamentals"]["matched"])
    matched_preview = sum(1 for item in items if item["sources"]["preview"]["matched"])
    preview_scope = preview.get("scope") or {}
    pool_codes = {(item["market"], item["code"]) for item in items}
    return {
        "pool": {
            "source": db.get("source"),
            "pool_date": db.get("pool_date"),
            "rows_requested": len(db.get("pool_rows") or []),
            "bundle_items": len(items),
            "by_market": by_market,
        },
        "matched": {
            "research_cards": matched_cards,
            "kb_refs": matched_kb,
            "fundamentals": matched_fund,
            "preview_items": matched_preview,
        },
        "research_cards_snapshot": {
            "card_pool_date": db.get("card_pool_date"),
            "cards_at_snapshot_date": len(db.get("cards") or []),
            "cards_not_in_latest_pool": max(0, len(db.get("cards") or []) - matched_cards),
            "note": "同池日研究卡多于最新池；非最新池卡不在本证据包主体范围。",
        },
        "supplementary_preview_excluded": [
            {
                "market": item.get("market"),
                "code": item.get("code"),
                "name": item.get("name"),
                "reason": "非最新 ACAD1 池补充卡，本证据包仅覆盖最新池",
            }
            for item in preview.get("items") or []
            if not item.get("in_primary_pool")
        ],
        "industry": _count_status(items, "industry"),
        "announcements": _count_status(items, "announcements"),
        "news": _count_status(items, "news"),
        "method_refs": {
            "items_with_refs": sum(1 for item in items if item.get("method_refs")),
            "refs_total": len(all_refs),
            "unique_videos": len({ref["video_id"] for ref in all_refs if ref.get("video_id")}),
            "by_topic": refs_by_topic,
            "by_lang": refs_by_lang,
            "method_coverage": {},
        },
        "evidence_level": coverage_counts,
        "gaps": {
            "items_with_gaps": items_with_gaps,
            "total_entries": total_gap_entries,
            "by_kind": gap_by_kind,
            "by_kind_label": gap_by_kind_label,
            "by_field": gap_by_field,
            "true_missing_entries": true_missing,
            "legend": GAP_KIND_LEGEND,
        },
        "cross_source": cross_source,
        "pool_code_set": sorted(f"{m}:{c}" for m, c in pool_codes),
        "preview_scope": {
            "cards_at_pool_date": preview_scope.get("cards_at_pool_date"),
            "cards_without_evidence": len(preview_scope.get("cards_without_evidence") or []),
            "supplementary_cards": preview_scope.get("supplementary_cards"),
        },
        "kb_scope": {
            "generated_at": kb.get("generated_at"),
            "items": (kb.get("summary") or {}).get("items_total"),
            "item_hit_rate": (kb.get("summary") or {}).get("item_hit_rate"),
            "method_coverage": (kb.get("summary") or {}).get("method_coverage"),
            "query_zh_hit_rate": (
                ((kb.get("summary") or {}).get("query_stats") or {}).get("by_lang") or {}
            )
            .get("zh", {})
            .get("query_hit_rate"),
            "query_en_hit_rate": (
                ((kb.get("summary") or {}).get("query_stats") or {}).get("by_lang") or {}
            )
            .get("en", {})
            .get("query_hit_rate"),
        },
        "fundamentals_scope": {
            "generated_at": fundamentals.get("generated_at"),
            "coverage": fundamentals.get("coverage", {}).get("primary_pool"),
            "warnings": fundamentals.get("warnings"),
        },
    }


def _method_coverage_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = item.get("method_coverage") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def _build_payload(
    items: list[dict[str, Any]],
    summary: dict[str, Any],
    db: dict[str, Any],
    kb: dict[str, Any],
    fundamentals: dict[str, Any],
    preview: dict[str, Any],
    inputs_meta: dict[str, Any],
    out_json: Path,
    out_md: Path,
) -> dict[str, Any]:
    summary["method_refs"]["method_coverage"] = _method_coverage_counts(items)
    card_hash_input = []
    for item in items:
        card = item["card"]
        fields = card.get("fields") or {}
        card_hash_input.append({field: fields.get(field) for field in CARD_HASH_FIELDS})
    card_hash = _sha256_obj(card_hash_input)
    sql_executed = db.get("sql_executed") or []
    read_only_proof = {
        "db_writes": False,
        "research_table_written": False,
        "status_or_reviewed_changed": False,
        "sql_executed": sql_executed,
        "sql_only_select": all(
            not re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|TRUNCATE)\b", sql, re.IGNORECASE)
            for sql in sql_executed
        ),
        "card_snapshot_sha256": card_hash,
        "card_snapshot_scope": (
            "v2_stock_research 最新池日快照，匹配 "
            f"{sum(1 for item in items if item['card']['matched'])} 张卡；"
            f"哈希字段：{','.join(CARD_HASH_FIELDS)}"
        ),
        "card_fields_hashed": list(CARD_HASH_FIELDS),
        "inputs_sha256": inputs_meta,
        "outputs_written": [str(out_json), str(out_md)],
        "note": (
            "本脚本只 SELECT v2_acad1_pool / v2_strategy / v2_stock_research，"
            "不写 DB、不写 v2_stock_research、不改 status/reviewed；卡面字段为读取时快照。"
        ),
    }
    notes = [
        "只读证据包：本脚本不写 DB、不写 v2_stock_research、不改 reviewed/status，"
        "不生成方向/评级/买卖结论。",
        "卡面 direction/rating/thesis/catalysts/risks/invalidation 是上游研究卡的机器事实草稿快照，"
        "本证据包原样引用，不解读、不修改、不据此给出结论；executable 均为上游值。",
        "缺口按 kind 如实标注：not_applicable=数据源不覆盖该市场；missing/source_missing=真缺失；"
        "no_match=新闻关键词未命中（可能确无新闻或别名不全）；stale=快照不一致；"
        "partial_source=上游快照字段不完整。不推断、不补造。",
        "知识库方法引用来自 P2-6 只读 enrichment：语料以英文 YouTube 量化频道为主，"
        "中文查询命中弱（缠论/中枢等 0 命中），方法引用多为英文邻近内容，仅作方法参考。",
        "行业 PIT 滞后：sw_l1_member 最新成分事件 20260827，相对池日滞后 65 天；"
        "该日之后成分调整不可见，证据包不推断。",
        "非最新池的补充证据卡（preview supplementary）与同池日非本池研究卡未纳入主体，"
        "已记录在 summary 中。",
        "任何引用/卡面字段仅供 ACAD1 人工复核；本文件不是投资结论，不自动通过/否决。",
        "证据完整度口径（沿用 preview 定义）：CN 适用「行业+公告+新闻」，非 CN 仅适用「新闻」；"
        "full=适用字段全部有覆盖，none=适用字段均无覆盖；3 只 HK 票 news no_match 记为 none。",
    ]
    cross = summary.get("cross_source") or {}
    if not cross.get("all_pool_dates_consistent", True):
        notes.append(
            "注意：跨来源池日/卡快照日不完全一致："
            f"{json.dumps(cross.get('snapshot_pool_dates'), ensure_ascii=False)}；"
            "证据包按各自快照原样呈现，不推断。"
        )
    if not cross.get("db_pool_vs_card_date_consistent", True):
        notes.append("注意：DB 池日与研究卡快照日不一致，卡面证据可能来自不同池日，需复核上游。")
    return {
        "schema": OUT_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "read_only": True,
        "purpose": (
            "把 KB 方法引用、行业/公告/新闻基本面证据、研究卡事实合成 ACAD1 人工复核证据包；"
            "不含评级/方向/买卖结论。"
        ),
        "disclaimer": "证据包，不是投资结论；缺口如实标注，不推断、不补造、不自动通过/否决。",
        "inputs": inputs_meta,
        "pool": db.get("pool_date"),
        "summary": summary,
        "read_only_proof": read_only_proof,
        "items": items,
        "notes": notes,
    }


def _md_evidence_line(item: dict[str, Any]) -> list[str]:
    industry = item["industry"]
    announcements = item["announcements"]
    news = item["news"]
    lines = [
        "**卡面事实（只读快照，非结论）**",
    ]
    fields = item["card"].get("fields") or {}
    if item["card"]["matched"]:
        lines.append(
            "- status={}；direction={}；rating={}；reviewer={}；review_note={}；data_date={}".format(
                fields.get("status") or "—",
                fields.get("direction") or "—",
                fields.get("rating") or "—",
                fields.get("reviewer") or "—",
                _clip(fields.get("review_note") or "—", 60),
                fields.get("data_date") or "—",
            )
        )
        for field, label in (
            ("thesis", "thesis"),
            ("catalysts", "catalysts"),
            ("risks", "risks"),
            ("invalidation", "invalidation"),
            ("exec_note", "exec_note"),
        ):
            if fields.get(field):
                lines.append(f"- {label}（摘录）：{_clip(fields[field], 260)}")
        lines.append(f"- executable={fields.get('executable') or '—'}（上游卡面原值）")
        lines.append(
            "- 卡溯源：{}，card_id={}，matched_by={}，completeness={}".format(
                item["card"]["source"],
                fields.get("id") or "—",
                item["card"]["matched_by"] or "—",
                item["card"]["completeness"],
            )
        )
        if item["card"].get("drift_from_preview"):
            lines.append(
                "- 卡面漂移（DB vs preview）：{}".format(
                    json.dumps(item["card"]["drift_from_preview"], ensure_ascii=False)
                )
            )
    else:
        lines.append("- 研究卡匹配：❌ 未找到（缺口 card:missing）")
    lines.extend(
        [
            "",
            "**行业/公告/新闻（PIT 只读证据）**",
            f"- 行业：{industry['display']}",
            f"- 公告（90d）：{announcements['display']}",
            f"- 新闻（30d）：{news['display']}",
            "",
            f"**知识库方法引用（top {len(item['method_refs'])}）**",
        ]
    )
    if item["method_refs"]:
        for ref in item["method_refs"]:
            lines.append(
                f"- [{ref['topic_label']}] [{ref['lang'] or '—'}] "
                f"{ref['score'] or 0.0:.2f} {_clip(ref['title'] or '—', 90)}"
                f"（{ref['video_id'] or '—'}）"
            )
            lines.append(f"  - 摘录：{_clip(ref['snippet'] or '—', 180)}")
    else:
        lines.append("- ❌ 无方法引用（缺口 kb_refs:missing）")
    if item["gaps"]:
        lines.append("")
        lines.append("**缺口（如实标注）**")
        for gap in item["gaps"]:
            lines.append(
                f"- {gap['field_label']} / {gap['kind']}：{_clip(gap['reason'] or '—', 160)}"
                f"（{gap['source']}）"
            )
    else:
        lines.append("")
        lines.append("**缺口**：无")
    lines.append("")
    lines.append(
        "证据完整度：{}（{}；口径=行业/公告/新闻，适用字段：{}）；本票 no_conclusion={}。".format(
            EVIDENCE_LEVEL_LABELS.get(item["evidence_level"], item["evidence_level"]),
            item["evidence_level_source"],
            "、".join(item.get("evidence_level_applicable_fields") or []) or "—",
            item["no_conclusion"],
        )
    )
    return lines


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    proof = payload["read_only_proof"]
    inputs = payload["inputs"]
    lines = [
        "# ACAD1 复核证据包（只读）",
        "",
        f"- 生成时间：`{payload['generated_at']}`；池日：`{payload['pool']}`；"
        f"证据包范围：**{summary['pool']['bundle_items']}** 只（最新 ACAD1 池）",
        "- **只读**：仅 SELECT `v2_acad1_pool` / `v2_strategy` / `v2_stock_research`；"
        "未写 DB、未写 `v2_stock_research`、未改 `status`/`reviewed`。",
        "- **非结论**：本文件是证据包，不是投资结论；卡面 direction/rating/thesis 等字段为"
        "上游机器事实草稿快照，原样引用，不解读、不修改；缺口按 kind 如实标注。",
        "- 输入：KB 引用 `{}`（{}）、基本面 `{}`（{}）、卡证据预览 `{}`（{}）。".format(
            inputs.get("kb_refs", {}).get("path", "—"),
            inputs.get("kb_refs", {}).get("generated_at", "—"),
            inputs.get("fundamentals", {}).get("path", "—"),
            inputs.get("fundamentals", {}).get("generated_at", "—"),
            inputs.get("preview", {}).get("path", "—"),
            inputs.get("preview", {}).get("generated_at", "—"),
        ),
        "",
        "## 1. 覆盖与缺口摘要",
        "",
        "| 维度 | 结果 |",
        "|------|------|",
        "| 最新 ACAD1 池 | {} 行（{}）|".format(
            summary["pool"]["rows_requested"],
            "，".join(f"{k} {v}" for k, v in sorted(summary["pool"]["by_market"].items())),
        ),
        "| 证据包覆盖 | {} 只（研究卡匹配 {} / 基本面匹配 {} / KB 引用匹配 {} / 预览匹配 {}）|".format(
            summary["pool"]["bundle_items"],
            summary["matched"]["research_cards"],
            summary["matched"]["fundamentals"],
            summary["matched"]["kb_refs"],
            summary["matched"]["preview_items"],
        ),
        "| 行业 | {} |".format(_status_text(summary["industry"])),
        "| 公告 90d | {} |".format(_status_text(summary["announcements"])),
        "| 新闻 30d | {} |".format(_status_text(summary["news"])),
        "| 方法引用 | {} 只有引用；共 {} 条（去重 {} 个视频）；{} |".format(
            summary["method_refs"]["items_with_refs"],
            summary["method_refs"]["refs_total"],
            summary["method_refs"]["unique_videos"],
            "，".join(f"{k} {v}" for k, v in sorted(summary["method_refs"]["by_topic"].items())),
        ),
        "| 证据完整度 | {} |".format(_status_text(summary["evidence_level"])),
        "| 缺口 | {} 处（{} 只票有缺口）；真缺失 {} 处 |".format(
            summary["gaps"]["total_entries"],
            summary["gaps"]["items_with_gaps"],
            summary["gaps"]["true_missing_entries"],
        ),
        "| 卡面快照 | 池日 `{}` 共 {} 张研究卡，本池匹配 {} 张 |".format(
            summary["research_cards_snapshot"]["card_pool_date"],
            summary["research_cards_snapshot"]["cards_at_snapshot_date"],
            summary["matched"]["research_cards"],
        ),
        "",
        "缺口分类：{}。".format(
            "；".join(
                f"{kind}={count}（{gap_label}）"
                for kind, count in sorted(summary["gaps"]["by_kind"].items())
                for gap_label in [summary["gaps"]["by_kind_label"].get(kind, kind)]
            )
        ),
        "",
        "## 2. 缺口清单（逐条，真缺失优先）",
        "",
    ]
    gap_rows = []
    for item in payload["items"]:
        for gap in item["gaps"]:
            gap_rows.append((item, gap))
    # 真缺失优先，其次保持逐票顺序
    gap_rows.sort(key=lambda pair: (0 if pair[1]["true_missing"] else 1, pair[0]["seq"]))
    if gap_rows:
        lines.append("| # | 市场 | 代码 | 名称 | 字段 | kind | 说明 | 真缺失 |")
        lines.append("|---|------|------|------|------|------|------|--------|")
        for index, (item, gap) in enumerate(gap_rows, 1):
            lines.append(
                f"| G{index:02d} | {item['market']} | {item['code']} | {item['name']} | "
                f"{gap['field_label']} | {gap['kind']} | "
                f"{_clip(gap['reason'] or '', 90).replace('|', chr(92) + '|')} | "
                f"{'是' if gap['true_missing'] else '否'} |"
            )
    else:
        lines.append("（无缺口）")
    lines.extend(["", "## 3. 逐票证据卡", ""])
    for item in payload["items"]:
        lines.append(
            "### {} [{}] {} {} — {} / {} / score {} / 证据完整度={}".format(
                item["seq"],
                item["market"],
                item["code"],
                item["name"],
                item["strategy_id"],
                item["signal"],
                item["score"] if item["score"] is not None else "—",
                EVIDENCE_LEVEL_LABELS.get(item["evidence_level"], item["evidence_level"]),
            )
        )
        lines.append("")
        lines.extend(_md_evidence_line(item))
        lines.extend(["", "---", ""])
    lines.extend(
        [
            "## 4. 溯源与只读证明",
            "",
            "- 输入文件 sha256：",
        ]
    )
    for name, meta in inputs.items():
        if meta.get("sha256"):
            lines.append(
                "  - `{}`：sha256=`{}`（{}）".format(name, meta.get("sha256"), meta.get("path", ""))
            )
        else:
            lines.append(
                "  - `{}`：source=`{}`（pool_date={}，card_pool_date={}，fallback={}）".format(
                    name,
                    meta.get("source", "—"),
                    meta.get("pool_date", "—"),
                    meta.get("card_pool_date", "—"),
                    meta.get("fallback"),
                )
            )
    lines.extend(
        [
            "- 执行 SQL（全部 SELECT）：{}".format("；".join(proof["sql_executed"])),
            "- 卡面快照 sha256：`{}`（{}）".format(
                proof["card_snapshot_sha256"], proof["card_snapshot_scope"]
            ),
            "- DB 写入：{}；研究卡表写入：{}；status/reviewed 改动：{}".format(
                proof["db_writes"],
                proof["research_table_written"],
                proof["status_or_reviewed_changed"],
            ),
            "- 输出：`{}`、`{}`".format(proof["outputs_written"][0], proof["outputs_written"][1]),
            "",
            "## 5. 说明与限制",
            "",
        ]
    )
    for note in payload["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def _status_text(counts: dict[str, int]) -> str:
    return "，".join(f"{k} {v}" for k, v in sorted(counts.items())) or "—"


def _write_outputs(payload: dict[str, Any], out_json: Path, out_md: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_render_markdown(payload), encoding="utf-8")


def _print_summary(payload: dict[str, Any], elapsed: float, verbose: bool) -> None:
    summary = payload["summary"]
    proof = payload["read_only_proof"]
    print(
        f"[evidence_bundle] pool={summary['pool']['source']} date={payload['pool']} "
        f"items={summary['pool']['bundle_items']}"
    )
    print(
        f"[evidence_bundle] matched cards={summary['matched']['research_cards']} "
        f"fundamentals={summary['matched']['fundamentals']} "
        f"kb_refs={summary['matched']['kb_refs']} "
        f"preview={summary['matched']['preview_items']}"
    )
    print(
        f"[evidence_bundle] industry={_status_text(summary['industry'])} | "
        f"announcements={_status_text(summary['announcements'])} | "
        f"news={_status_text(summary['news'])}"
    )
    print(
        f"[evidence_bundle] method_refs items={summary['method_refs']['items_with_refs']} "
        f"total={summary['method_refs']['refs_total']} "
        f"unique_videos={summary['method_refs']['unique_videos']}"
    )
    print(
        f"[evidence_bundle] evidence_level={_status_text(summary['evidence_level'])} | "
        f"gaps={summary['gaps']['total_entries']} entries / "
        f"{summary['gaps']['items_with_gaps']} items | "
        f"true_missing={summary['gaps']['true_missing_entries']}"
    )
    print(
        "[evidence_bundle] readonly: sql_only_select={} card_sha256={} elapsed={:.1f}s".format(
            proof["sql_only_select"], proof["card_snapshot_sha256"][:16], elapsed
        )
    )
    if verbose:
        print("[evidence_bundle] --- items ---")
        for item in payload["items"]:
            print(
                f"  {item['seq']} [{item['market']}] {item['code']} {item['name']} | "
                f"{item['strategy_id']}/{item['signal']} | "
                f"card={'Y' if item['card']['matched'] else 'N'} | "
                f"refs={len(item['method_refs'])} | gaps={item['gap_count']} | "
                f"{item['evidence_level']}"
            )
    print(
        "[evidence_bundle] saved: json={} md={}".format(
            proof["outputs_written"][0], proof["outputs_written"][1]
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ACAD1 复核证据包（只读：KB 引用 + 基本面证据 + 研究卡事实，不给结论）"
    )
    parser.add_argument("--pool-date", default=None, help="池日 YYYY-MM-DD（默认最新池）")
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="最多处理多少只（默认 30）"
    )
    parser.add_argument("--kb-refs", default=str(DEFAULT_KB_REFS), help="KB 引用 JSON 路径")
    parser.add_argument(
        "--fundamentals", default=str(DEFAULT_FUNDAMENTALS), help="基本面证据 JSON 路径"
    )
    parser.add_argument("--preview", default=str(DEFAULT_PREVIEW), help="卡证据预览 JSON 路径")
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON), help="输出 JSON 路径")
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_MD), help="输出 Markdown 路径")
    parser.add_argument("--no-db", action="store_true", help="跳过 DB，用 preview 快照兜底")
    parser.add_argument("--verbose", action="store_true", help="打印逐票摘要")
    args = parser.parse_args(argv)

    started = time.time()
    kb_path = Path(args.kb_refs)
    fund_path = Path(args.fundamentals)
    preview_path = Path(args.preview)
    try:
        kb = _load_json(kb_path, "KB 引用")
        fundamentals = _load_json(fund_path, "基本面证据")
        preview = _load_json(preview_path, "卡证据预览")
    except Exception as exc:  # noqa: BLE001 - 输入缺失需明确报错
        print(f"[evidence_bundle] 输入加载失败：{exc}", file=sys.stderr)
        return 2
    if args.limit < 1:
        print("[evidence_bundle] --limit 必须 >= 1", file=sys.stderr)
        return 2
    if args.pool_date:
        try:
            date.fromisoformat(args.pool_date)
        except ValueError:
            print("[evidence_bundle] --pool-date 需为 YYYY-MM-DD", file=sys.stderr)
            return 2

    fallback_reason = ""
    if args.no_db:
        db = _load_preview_pool_fallback(preview, args.limit)
        fallback_reason = "--no-db"
    else:
        try:
            db = _load_db_snapshot(args.pool_date, args.limit)
        except Exception as exc:  # noqa: BLE001 - DB 不可用时允许回退，原因如实记录
            fallback_reason = f"DB 读取失败：{type(exc).__name__}: {exc}"
            db = _load_preview_pool_fallback(preview, args.limit)
        if not db.get("pool_rows"):
            fallback_reason = fallback_reason or "DB 池为空"
            db = _load_preview_pool_fallback(preview, args.limit)
    if not db.get("pool_rows"):
        print(f"[evidence_bundle] 无法获取 ACAD1 池：{fallback_reason}", file=sys.stderr)
        return 2
    db["fallback_reason"] = fallback_reason

    items, cross_source = _build_items(
        db["pool_rows"],
        db,
        kb,
        fundamentals,
        preview,
        args.limit,
        input_paths={
            "kb_refs": str(kb_path),
            "fundamentals": str(fund_path),
            "preview": str(preview_path),
        },
    )
    if not items:
        print("[evidence_bundle] 池为空，无内容可合成", file=sys.stderr)
        return 2
    summary = _build_summary(items, db, kb, fundamentals, preview, cross_source)
    if fallback_reason:
        summary["pool"]["fallback_reason"] = fallback_reason
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    inputs_meta = {
        "kb_refs": {
            "path": str(kb_path),
            "sha256": _sha256_file(kb_path),
            "schema": kb.get("schema"),
            "generated_at": kb.get("generated_at"),
        },
        "fundamentals": {
            "path": str(fund_path),
            "sha256": _sha256_file(fund_path),
            "schema": fundamentals.get("schema"),
            "generated_at": fundamentals.get("generated_at"),
        },
        "preview": {
            "path": str(preview_path),
            "sha256": _sha256_file(preview_path),
            "schema": preview.get("schema"),
            "generated_at": preview.get("generated_at"),
        },
        "db_pool_cards": {
            "source": db.get("source"),
            "pool_date": db.get("pool_date"),
            "card_pool_date": db.get("card_pool_date"),
            "fallback": bool(db.get("fallback")),
            "fallback_reason": fallback_reason,
        },
    }
    payload = _build_payload(
        items, summary, db, kb, fundamentals, preview, inputs_meta, out_json, out_md
    )
    _write_outputs(payload, out_json, out_md)
    elapsed = time.time() - started
    _print_summary(payload, elapsed, args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
