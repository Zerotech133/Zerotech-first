# -*- coding: utf-8 -*-
"""ACAD1 研究卡 · 基本面证据预览（只读）。

用途
----
把 task-9 的 ``C:\\Temp\\acad1_fundamentals.json``（申万 L1 行业 PIT / 公告 / 新闻证据）
按「最新 ACAD1 池 × v2_stock_research 研究卡」逐卡拼成只读预览，供人工复核引用：

- ``C:\\Temp\\acad1_card_evidence_preview.json``：结构化预览（含口径、覆盖、缺口、证据引用）
- ``C:\\Temp\\acad1_card_evidence_preview.md``：人读版（卡片总表 + 逐卡证据摘录 + 缺口清单）

输入（全部只读）
----------------
- ``--fundamentals``：task-9 证据适配器输出 JSON（默认 ``C:\\Temp\\acad1_fundamentals.json``）；
- ``v2_acad1_pool``：最新池日（PG 只读会话 SELECT，口径同 task-9）；
- ``v2_stock_research``：池内研究卡（PG 只读会话 SELECT；``--cards-json`` 可离线替代）；
- 可选：研究卡表该池日下不在最新池的卡（候选池同步残留），若 fundamentals 有对应证据，
  作为 ``supplementary`` 补充预览并明确标注“非最新池”；无证据的卡只记入对账清单，不预览。

硬约束
------
- 仅 SELECT ``v2_acad1_pool`` / ``v2_stock_research``，不执行任何 INSERT/UPDATE/DELETE；
- 不写 ``v2_stock_research``，不改 ``status`` / ``reviewer`` / ``reviewed_at`` / ``direction`` / ``rating``；
- 输出里 ``card`` 仅为读取时的只读快照（含 sha256 供事后比对），不是写回内容；
- 不生成评级/方向/买卖建议，不含“通过/否决”结论，缺口一律如实标注 kind（不推断、不补造）；
- 唯一写操作 = 两个 ``--output-*`` 文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_FUNDAMENTALS = Path(r"C:\Temp\acad1_fundamentals.json")
DEFAULT_OUT_JSON = Path(r"C:\Temp\acad1_card_evidence_preview.json")
DEFAULT_OUT_MD = Path(r"C:\Temp\acad1_card_evidence_preview.md")

CST = timezone(timedelta(hours=8))
MARKETS = ("CN", "HK", "US", "CRYPTO")
MARKET_ORDER = {market: index for index, market in enumerate(MARKETS)}

GAP_KIND_LEGEND = {
    "not_applicable": "数据源不覆盖该市场（非抓取失败）",
    "missing": "数据源存在但该票无有效记录（真缺失）",
    "source_missing": "数据源缺失（文件/表不存在）",
    "no_data": "窗口内 0 条（真实无记录/未落仓，非缺失值）",
    "no_match": "新闻关键词未命中（可能确无新闻或别名不全）",
    "no_alias": "无名称别名，无法归属新闻",
    "stale": "输入与当前池日不一致（需重跑上游适配器）",
}


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(CST).replace(microsecond=0).isoformat()


def _iso(value) -> str | None:
    """date/datetime/Decimal/str -> JSON 安全字符串。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _num(value):
    """numeric（Decimal 等）-> float，保持 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _db_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("DB_MAIN_URL")
    if not url:
        env_file = Path(__file__).resolve().parent.parent / ".env.local"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("DB_MAIN_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not url:
        raise SystemExit("未找到 DB_MAIN_URL（环境变量或 code/.env.local）；或用 --cards-json 离线卡数据")
    return url.replace("postgresql+psycopg2://", "postgresql://", 1)


def _normalize_code(code: str, market: str) -> str:
    """与 scripts/dw_fundamentals.py 的代码归一保持一致：sh688004 -> 688004.SH；00700 -> 00700.HK。"""
    raw = str(code or "").strip()
    if market == "CN":
        match = re.fullmatch(r"(?i)(sh|sz|bj)(\d{6})", raw)
        if match:
            return f"{match.group(2)}.{match.group(1).upper()}"
        return raw.upper()
    if market == "HK":
        match = re.fullmatch(r"(?i)(\d{4,5})(?:\.HK)?", raw)
        if match:
            return f"{int(match.group(1)):05d}.HK"
        return raw.upper()
    return raw


def _guess_market(code: str) -> str | None:
    raw = str(code or "").strip()
    if re.fullmatch(r"(?i)(sh|sz|bj)\d{6}", raw) or re.fullmatch(r"(?i)\d{6}\.(SH|SZ|BJ)", raw):
        return "CN"
    if re.fullmatch(r"\d{4,5}(?:\.HK)?", raw):
        return "HK"
    if "/" in raw:
        return "CRYPTO"
    if re.fullmatch(r"[A-Za-z][A-Za-z.\-]*", raw):
        return "US"
    return None


def _short(text, limit: int = 70) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _md_escape(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


# --------------------------------------------------------------------------- #
# 只读加载：PG 池 + 研究卡
# --------------------------------------------------------------------------- #
CARD_FIELDS = (
    "id", "pool_date", "strategy_id", "code", "name", "direction", "score", "rating",
    "status", "reviewer", "review_note", "reviewed_at", "handoff_at", "data_date", "updated_at",
)


def load_db_state(db_url: str, pool_date: str | None) -> dict:
    """只读会话读取：最新池 + 该池日研究卡 + 表规模 + 只读快照哈希。"""
    import psycopg2

    conn = psycopg2.connect(db_url, connect_timeout=10)
    try:
        conn.set_session(readonly=True, autocommit=True)  # 只读事务，物理禁止写入
        conn.set_client_encoding("UTF8")
        with conn.cursor() as cur:
            if pool_date:
                day = date.fromisoformat(pool_date)
            else:
                cur.execute("SELECT max(pool_date) FROM v2_acad1_pool")
                day = cur.fetchone()[0]
            cur.execute(
                """
                SELECT pool_date, market, code, name, strategy_id, signal, score,
                       data_date, status
                FROM v2_acad1_pool
                WHERE pool_date = %s
                ORDER BY market, code
                """,
                (day,),
            )
            pool_columns = [desc[0] for desc in cur.description]
            pool_rows = [dict(zip(pool_columns, row)) for row in cur.fetchall()]
            cur.execute(
                f"""
                SELECT {", ".join(CARD_FIELDS)}
                FROM v2_stock_research
                WHERE pool_date = %s
                ORDER BY strategy_id, code
                """,
                (day,),
            )
            card_columns = [desc[0] for desc in cur.description]
            card_rows = [dict(zip(card_columns, row)) for row in cur.fetchall()]
            cur.execute("SELECT count(*) FROM v2_stock_research")
            table_total = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM v2_stock_research WHERE status = 'draft'")
            draft_total = cur.fetchone()[0]
    finally:
        conn.close()

    pool = []
    for row in pool_rows:
        pool.append({
            "pool_date": _iso(row["pool_date"]),
            "market": row["market"],
            "code": row["code"],
            "name": row["name"],
            "strategy_id": row["strategy_id"],
            "signal": row["signal"],
            "score": _num(row["score"]),
            "data_date": _iso(row["data_date"]),
            "status": row["status"],
        })
    cards = []
    for row in card_rows:
        cards.append({
            "card_id": int(row["id"]),
            "pool_date": _iso(row["pool_date"]),
            "strategy_id": row["strategy_id"],
            "code": row["code"],
            "name": row["name"],
            "direction": row["direction"],
            "score": _num(row["score"]),
            "rating": row["rating"],
            "status": row["status"],
            "reviewer": row["reviewer"],
            "review_note": row["review_note"],
            "reviewed_at": _iso(row["reviewed_at"]),
            "handoff_at": _iso(row["handoff_at"]),
            "data_date": _iso(row["data_date"]),
            "updated_at": _iso(row["updated_at"]),
        })
    snapshot = json.dumps(
        [{key: card.get(key) for key in ("card_id", "strategy_id", "code", "status", "reviewer",
                                         "reviewed_at", "direction", "rating", "updated_at")}
         for card in sorted(cards, key=lambda item: (item["strategy_id"], item["code"]))],
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "source": "postgres:zhice_os_v2.v2_acad1_pool + v2_stock_research（只读会话）",
        "pool_date": str(day),
        "pool": pool,
        "cards": cards,
        "table_total": int(table_total),
        "draft_total": int(draft_total),
        "card_snapshot_sha256": hashlib.sha256(snapshot.encode("utf-8")).hexdigest(),
    }


def load_cards_json(path: Path) -> dict:
    """离线卡数据（--cards-json），schema 兼容 cards[]/裸 list；只读。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("cards", data) if isinstance(data, dict) else data
    cards = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cards.append({
            "card_id": _num(row.get("card_id") or row.get("id")),
            "pool_date": _iso(row.get("pool_date")),
            "strategy_id": row.get("strategy_id") or "",
            "code": row.get("code"),
            "name": row.get("name") or "",
            "direction": row.get("direction") or "",
            "score": _num(row.get("score")),
            "rating": row.get("rating") or "",
            "status": row.get("status") or "",
            "reviewer": row.get("reviewer") or "",
            "review_note": row.get("review_note") or "",
            "reviewed_at": _iso(row.get("reviewed_at")),
            "handoff_at": _iso(row.get("handoff_at")),
            "data_date": _iso(row.get("data_date")),
            "updated_at": _iso(row.get("updated_at")),
        })
    snapshot = json.dumps(
        [{key: card.get(key) for key in ("card_id", "strategy_id", "code", "status", "reviewer",
                                         "reviewed_at", "direction", "rating", "updated_at")}
         for card in sorted(cards, key=lambda item: (item["strategy_id"], item["code"]))],
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "source": f"json:{path}",
        "pool_date": _iso(data.get("pool_date")) if isinstance(data, dict) else None,
        "pool": [],
        "cards": cards,
        "table_total": len(cards),
        "draft_total": sum(1 for card in cards if card["status"] == "draft"),
        "card_snapshot_sha256": hashlib.sha256(snapshot.encode("utf-8")).hexdigest(),
    }


# --------------------------------------------------------------------------- #
# fundamentals 输入
# --------------------------------------------------------------------------- #
def load_fundamentals(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"fundamentals 证据文件不存在：{path}（先跑 scripts/dw_fundamentals.py）")
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items") or []
    index: dict[tuple[str, str], dict] = {}
    for position, item in enumerate(items):
        key = (item.get("market"), item.get("dw_code"))
        index[key] = {"item": item, "index": position}
    return {
        "path": str(path),
        "generated_at": data.get("generated_at"),
        "schema": data.get("schema"),
        "pool_date": (data.get("pool") or {}).get("pool_date"),
        "item_count": len(items),
        "index": index,
        "warnings": data.get("warnings") or [],
        "pit": data.get("pit") or {},
        "sources": data.get("sources") or {},
    }


# --------------------------------------------------------------------------- #
# 卡片证据拼装
# --------------------------------------------------------------------------- #
def _block(block: dict | None, gap: dict | None) -> dict:
    """把 fundamentals 的字段块转成预览块（保留原始事实，附 gap）。"""
    if not block:
        return {"status": "source_missing", "value": None, "gap": gap}
    return {
        "status": block.get("status"),
        "source": block.get("source"),
        "value": block.get("value"),
        "pit_lag_days": block.get("pit_lag_days"),
        "history_check": block.get("history_check"),
        "crosscheck_index_member_all": block.get("crosscheck_index_member_all"),
        "gap": gap,
    }


def _announcement_value(block: dict | None) -> dict | None:
    if not block or not block.get("value"):
        return None
    return block["value"]


def _card_block(card: dict | None) -> dict | None:
    if card is None:
        return None
    return {
        "card_id": card.get("card_id"),
        "strategy_id": card.get("strategy_id"),
        "code": card.get("code"),
        "name": card.get("name"),
        "direction": card.get("direction"),
        "score": card.get("score"),
        "rating": card.get("rating"),
        "status": card.get("status"),
        "reviewer": card.get("reviewer"),
        "review_note": card.get("review_note"),
        "reviewed_at": card.get("reviewed_at"),
        "handoff_at": card.get("handoff_at"),
        "data_date": card.get("data_date"),
        "updated_at": card.get("updated_at"),
    }


def build_preview_item(sequence: str, pool_row: dict | None, card: dict | None,
                       fund_entry: dict | None, fundamentals: dict) -> dict:
    """单张卡预览：卡面只读快照 + 行业/公告/新闻 + 缺口（只做事实整理，不做结论）。"""
    market = (pool_row or {}).get("market")
    code = (pool_row or {}).get("code") or (card or {}).get("code")
    if market is None and fund_entry is not None:
        market = fund_entry["item"].get("market")
    if market is None:
        market = _guess_market(code)

    fund_item = fund_entry["item"] if fund_entry else None
    gaps: list[dict] = []
    if fund_item is None:
        gaps.append({"field": "fundamentals", "kind": "missing",
                     "reason": "C:\\Temp\\acad1_fundamentals.json 无该票证据（需先补跑适配器）"})
    if card is None:
        gaps.append({"field": "card", "kind": "missing",
                     "reason": "v2_stock_research 无该 (pool_date, strategy_id, code) 研究卡"})

    base_gaps = (fund_item or {}).get("gaps") or {}
    for field in ("industry", "announcements", "news"):
        gap = base_gaps.get(field)
        if gap:
            gaps.append({"field": field, "kind": gap.get("kind"), "reason": gap.get("reason")})

    industry = (fund_item or {}).get("industry")
    announcements = (fund_item or {}).get("announcements")
    news = (fund_item or {}).get("news")
    aliases = (fund_item or {}).get("aliases") or []

    industry_preview = _block(industry, base_gaps.get("industry"))
    announcements_preview = _block(announcements, base_gaps.get("announcements"))
    announcements_preview["window"] = (announcements or {}).get("window")
    announcements_preview["pit_note"] = (announcements or {}).get("pit_note")
    news_preview = _block(news, base_gaps.get("news"))
    news_preview["aliases"] = aliases
    news_preview["per_source"] = (news or {}).get("per_source") or {}
    news_preview["match_method"] = (news or {}).get("match_method")
    news_preview["caveat"] = (news or {}).get("caveat")

    applicable = ["news"] if market != "CN" else ["industry", "announcements", "news"]
    covered = []
    for field in applicable:
        status = {"industry": industry_preview["status"],
                  "announcements": announcements_preview["status"],
                  "news": news_preview["status"]}.get(field)
        if status == "covered":
            covered.append(field)
    if fund_item is None:
        evidence_level = "none"
    elif len(covered) == len(applicable):
        evidence_level = "full"
    elif covered:
        evidence_level = "partial"
    else:
        evidence_level = "none"

    return {
        "seq": sequence,
        "in_primary_pool": bool(pool_row),
        "scope": "primary_pool" if pool_row else "supplementary",
        "market": market,
        "code": code,
        "dw_code": _normalize_code(code, market) if market else code,
        "name": (fund_item or {}).get("name") or (pool_row or {}).get("name") or (card or {}).get("name"),
        "pool_name": (pool_row or {}).get("name"),
        "card_name": (card or {}).get("name"),
        "pool": None if pool_row is None else {
            **pool_row,
            "score": _num(pool_row.get("score")),
        },
        "card": _card_block(card),
        "card_status_snapshot": "read_only_snapshot" if card is not None else None,
        "industry": industry_preview,
        "announcements": announcements_preview,
        "news": news_preview,
        "gaps": gaps,
        "evidence_level": evidence_level,
        "applicable_fields": applicable,
        "not_applicable_fields": [field for field in ("industry", "announcements", "news")
                                  if {"industry": industry_preview["status"],
                                      "announcements": announcements_preview["status"],
                                      "news": news_preview["status"]}.get(field) == "not_applicable"],
        "evidence_ref": None if fund_entry is None else {
            "file": fundamentals["path"],
            "generated_at": fundamentals["generated_at"],
            "item_index": fund_entry["index"],
            "item_key": f"{market}:{_normalize_code(code, market) if market else code}",
        },
        "no_conclusion": True,
    }


# --------------------------------------------------------------------------- #
# 覆盖 / 缺口统计
# --------------------------------------------------------------------------- #
def _status_count(items: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = item[field].get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _coverage(items: list[dict]) -> dict:
    total = len(items)
    gap_list = [gap for item in items for gap in item["gaps"]]
    by_kind: dict[str, int] = {}
    by_field: dict[str, int] = {}
    for gap in gap_list:
        by_kind[gap["kind"]] = by_kind.get(gap["kind"], 0) + 1
        by_field[gap["field"]] = by_field.get(gap["field"], 0) + 1
    industry_status = _status_count(items, "industry")
    ann_status = _status_count(items, "announcements")
    news_status = _status_count(items, "news")
    levels = {"full": 0, "partial": 0, "none": 0}
    for item in items:
        levels[item["evidence_level"]] += 1
    return {
        "cards": total,
        "by_market": {market: sum(1 for item in items if item["market"] == market) for market in MARKETS},
        "cards_with_pool_row": sum(1 for item in items if item["pool"] is not None),
        "cards_with_research_card": sum(1 for item in items if item["card"] is not None),
        "cards_with_fundamentals": sum(1 for item in items if item["evidence_ref"] is not None),
        "industry": {
            "covered": industry_status.get("covered", 0),
            "missing": industry_status.get("missing", 0),
            "not_applicable": industry_status.get("not_applicable", 0),
            "status": industry_status,
        },
        "announcements": {
            "covered": ann_status.get("covered", 0),
            "missing": ann_status.get("missing", 0),
            "no_data": ann_status.get("no_data", 0),
            "not_applicable": ann_status.get("not_applicable", 0),
            "status": ann_status,
        },
        "news": {
            "covered": news_status.get("covered", 0),
            "no_match": news_status.get("no_match", 0),
            "no_alias": news_status.get("no_alias", 0),
            "missing": news_status.get("missing", 0),
            "status": news_status,
        },
        "evidence_level": levels,
        "gaps": {
            "cards_with_gaps": sum(1 for item in items if item["gaps"]),
            "total_gap_entries": len(gap_list),
            "by_kind": by_kind,
            "by_field": by_field,
            "true_missing_entries": by_kind.get("missing", 0) + by_kind.get("source_missing", 0),
        },
    }


def _missing_report(items: list[dict]) -> dict:
    only_gaps = [
        {
            "seq": item["seq"],
            "market": item["market"],
            "code": item["code"],
            "name": item["name"],
            "in_primary_pool": item["in_primary_pool"],
            "evidence_level": item["evidence_level"],
            "gaps": item["gaps"],
        }
        for item in items if item["gaps"]
    ]
    coverage = _coverage(items)
    return {
        "summary": {
            "cards": coverage["cards"],
            "cards_with_gaps": coverage["gaps"]["cards_with_gaps"],
            "by_kind": coverage["gaps"]["by_kind"],
            "by_field": coverage["gaps"]["by_field"],
            "true_missing_entries": coverage["gaps"]["true_missing_entries"],
            "legend": GAP_KIND_LEGEND,
        },
        "items": only_gaps,
    }


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="ACAD1 研究卡基本面证据预览（只读）")
    parser.add_argument("--fundamentals", default=str(DEFAULT_FUNDAMENTALS))
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    parser.add_argument("--pool-date", default=None, help="池日 YYYY-MM-DD；默认 PG 最新")
    parser.add_argument("--db-url", default=None, help="PG DSN；默认 DB_MAIN_URL")
    parser.add_argument("--cards-json", default=None, help="离线研究卡 JSON（替代 PG，只读）")
    parser.add_argument("--no-supplementary", action="store_true",
                        help="不预览研究卡表中不在最新池的卡（严格 30 只主池口径）")
    parser.add_argument("--codes", default=None, help="仅预览指定代码（逗号分隔，冒烟用）")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    fundamentals = load_fundamentals(Path(args.fundamentals))

    if args.cards_json:
        state = load_cards_json(Path(args.cards_json))
        state["pool"] = []
        state["pool_date"] = args.pool_date or state.get("pool_date") or fundamentals["pool_date"]
        state["table_total"] = len(state["cards"])
        state.setdefault("draft_total", sum(1 for card in state["cards"] if card["status"] == "draft"))
        state.setdefault("card_snapshot_sha256", None)
        state["source"] = f"json:{args.cards_json}（未连 PG）"
        # 离线模式没有池行：用 pool_date 匹配卡；池信息由 fundamentals 补
        state["offline"] = True
    else:
        state = load_db_state(_db_url(args.db_url), args.pool_date)
        state["offline"] = False

    pool_date = state["pool_date"] or fundamentals["pool_date"]
    asof = fundamentals.get("pool_date")
    stale_fundamentals = bool(asof and pool_date and asof != pool_date)

    # 卡索引（按 (strategy_id, code) 精确 + 归一代码兜底）
    card_index: dict[tuple[str, str], dict] = {}
    card_by_code: dict[str, dict] = {}
    for card in state["cards"]:
        card_index[(card["strategy_id"], card["code"])] = card
        card_by_code.setdefault(str(card["code"]), card)

    def find_card(row: dict) -> dict | None:
        market = row.get("market") or _guess_market(row.get("code") or "")
        card = card_index.get((row.get("strategy_id"), row.get("code")))
        if card is not None:
            return card
        normalized = _normalize_code(row.get("code") or "", market or "CN")
        for candidate in state["cards"]:
            if (candidate["strategy_id"] == row.get("strategy_id")
                    and _normalize_code(candidate["code"], market or "CN") == normalized):
                return candidate
        return card_by_code.get(str(row.get("code")))

    def find_fund(code: str, market: str | None) -> dict | None:
        market = market or _guess_market(code) or "CN"
        entry = fundamentals["index"].get((market, _normalize_code(code, market)))
        if entry is not None:
            return entry
        for (item_market, item_code), candidate in fundamentals["index"].items():
            if item_code == _normalize_code(code, market):
                return candidate
        return None

    # 主口径：最新 ACAD1 池 × 研究卡（被 --codes 过滤时仍按池全量读，仅展示过滤后子集）
    if state["pool"]:
        pool_rows = state["pool"]
    elif state.get("offline"):
        pool_rows = fundamentals_items_pool(fundamentals)
    else:
        raise SystemExit(f"v2_acad1_pool 在 pool_date={pool_date} 无数据，无法生成预览")
    matched_keys: set[tuple[str, str]] = set()
    primary: list[dict] = []
    card_missing: list[dict] = []
    for row in sorted(pool_rows, key=lambda item: (MARKET_ORDER.get(item["market"], 9), item["code"])):
        card = find_card(row)
        if card is not None:
            matched_keys.add((card["strategy_id"], card["code"]))
        else:
            card_missing.append({"market": row["market"], "code": row["code"], "strategy_id": row["strategy_id"]})
        fund_entry = find_fund(row["code"], row["market"])
        primary.append(build_preview_item(f"P{len(primary) + 1:02d}", row, card, fund_entry, fundamentals))

    # 补充口径：研究卡表该池日存在、但不在最新池的卡；fundamentals 有对应证据才预览
    supplementary: list[dict] = []
    cards_without_evidence: list[dict] = []
    if not args.no_supplementary:
        for card in state["cards"]:
            if (card["strategy_id"], card["code"]) in matched_keys:
                continue
            market = _guess_market(card["code"])
            fund_entry = find_fund(card["code"], market)
            if fund_entry is None:
                cards_without_evidence.append({
                    "strategy_id": card["strategy_id"],
                    "code": card["code"],
                    "name": card["name"],
                    "market": market,
                    "reason": "fundamentals 无该票证据（非最新池，未预览）",
                })
                continue
            supplementary.append(
                build_preview_item(f"S{len(supplementary) + 1:02d}", None, card, fund_entry, fundamentals))

    supplementary.sort(key=lambda item: (MARKET_ORDER.get(item["market"], 9), item["code"]))
    for order, item in enumerate(supplementary, start=1):
        item["seq"] = f"S{order:02d}"
    primary_total = len(primary)
    supplementary_total = len(supplementary)
    if args.codes:
        wanted = {_normalize_code(part.strip(), _guess_market(part.strip()) or "CN")
                  for part in args.codes.split(",") if part.strip()}
        primary = [item for item in primary if item["dw_code"] in wanted or item["code"] in wanted]
        supplementary = [item for item in supplementary
                         if item["dw_code"] in wanted or item["code"] in wanted]

    all_items = primary + supplementary
    coverage = {
        "primary_pool": _coverage(primary),
        "supplementary": _coverage(supplementary),
        "all_previewed": _coverage(all_items),
    }
    missing = {
        "primary_pool": _missing_report(primary),
        "supplementary": _missing_report(supplementary),
        "all_previewed": _missing_report(all_items),
    }

    # 研究卡表对账（只读）：该池日下多少卡、多少进入预览、多少无证据
    cards_at_pool_date = len(state["cards"])
    warnings = []
    warnings.append(
        f"PIT 滞后：sw_l1_member 最新成分事件 {fundamentals.get('pit', {}).get('sw_member_latest_event_date')}，"
        f"池日 {pool_date}，滞后 {fundamentals.get('pit', {}).get('sw_member_lag_days')} 天；"
        "2026-08-27 之后的申万成分调整不可见，预览不推断。")
    if stale_fundamentals:
        warnings.append(
            f"fundamentals 证据池日={asof} 与当前池日={pool_date} 不一致（stale）；请重跑 "
            "scripts/dw_fundamentals.py 后再预览。")
    if cards_at_pool_date:
        warnings.append(
            f"v2_stock_research 在 pool_date={pool_date} 共 {cards_at_pool_date} 张卡："
            f"最新池内 {primary_total} 张全部进入预览；非池内 {cards_at_pool_date - primary_total} 张中 "
            f"{supplementary_total} 张有 fundamentals 证据并作为 supplementary 预览，"
            f"{len(cards_without_evidence)} 张无证据仅列入对账清单（未预览）。")
    if card_missing:
        warnings.append(f"最新池有 {len(card_missing)} 行未匹配到研究卡：{card_missing}")
    if not state.get("offline"):
        warnings.append(
            "只读：仅 SELECT v2_acad1_pool / v2_stock_research；未执行 INSERT/UPDATE/DELETE，"
            "未改 status / reviewer / reviewed_at / direction / rating；卡面字段为读取时只读快照。")
    warnings.append(
        "新闻为关键词匹配（股票名/英文核心名/括号代码），非数据仓原生个股映射，可能漏配/误配；"
        "tushare/news 与 news.db 两源有重叠、count 不相加；引用前需人工复核。")
    warnings.append(
        "HK/US/CRYPTO 无申万行业、无 A 股公告源：对应字段为 not_applicable（数据源不覆盖，非抓取失败），不编造。")
    if len(fundamentals["warnings"]) > 0:
        warnings.append(
            f"上游 fundamentals 共 {len(fundamentals['warnings'])} 条 warning（含 anns_d 最新文件日、"
            "hk_basic 缺码、新闻两源口径等），明细见 C:\\Temp\\acad1_fundamentals.json 的 warnings。")

    output = {
        "schema": "acad1_card_evidence_preview/v1",
        "generated_at": _now(),
        "read_only": True,
        "purpose": "把基本面证据按最新 ACAD1 池 × 研究卡拼成只读预览，供人工复核引用；不含评级/方向/买卖建议。",
        "disclaimer": "证据预览，不是投资结论；卡面 status/reviewer/reviewed_at/direction/rating 未被修改；"
                      "缺口按 kind 如实标注，不推断、不补造、不自动通过/否决。",
        "generator": {
            "script": "scripts/card_evidence_preview.py",
            "command": " ".join([Path(sys.executable).name] + sys.argv),
            "python": sys.version.split()[0],
        },
        "scope": {
            "pool_date": pool_date,
            "pool_source": state["source"],
            "primary_pool_rows": len(pool_rows),
            "primary_preview_cards": len(primary),
            "supplementary_cards": len(supplementary),
            "all_previewed": len(all_items),
            "cards_at_pool_date": cards_at_pool_date if not state.get("offline") else None,
            "cards_without_evidence": cards_without_evidence,
            "card_missing_for_pool_rows": card_missing,
            "supplementary_enabled": not args.no_supplementary,
            "codes_filter": [part.strip() for part in (args.codes or "").split(",") if part.strip()],
        },
        "inputs": {
            "fundamentals": {
                "path": fundamentals["path"],
                "schema": fundamentals["schema"],
                "generated_at": fundamentals["generated_at"],
                "pool_date": fundamentals["pool_date"],
                "item_count": fundamentals["item_count"],
                "stale_vs_pool_date": stale_fundamentals,
            },
            "research_cards": {
                "source": state["source"],
                "pool_date": state["pool_date"],
                "cards_at_pool_date": cards_at_pool_date if not state.get("offline") else None,
                "table_total": state.get("table_total"),
                "draft_total": state.get("draft_total"),
            },
        },
        "read_only_proof": {
            "db_writes": False,
            "research_table_written": False,
            "status_or_reviewed_changed": False,
            "sql_executed": [] if state.get("offline") else [
                "SELECT max(pool_date) / pool rows FROM v2_acad1_pool",
                "SELECT cards FROM v2_stock_research WHERE pool_date = ?",
                "SELECT count(*) / count(draft) FROM v2_stock_research",
            ],
            "card_snapshot_sha256": state.get("card_snapshot_sha256"),
            "card_snapshot_scope": "pool_date 下全部研究卡的可变字段（status/reviewer/reviewed_at/direction/rating/updated_at）",
            "outputs_written": [str(Path(args.out_json)), str(Path(args.out_md))],
        },
        "pit": {
            "source": fundamentals.get("pit", {}).get("sw_member_source"),
            "latest_event_date": fundamentals.get("pit", {}).get("sw_member_latest_event_date"),
            "lag_days": fundamentals.get("pit", {}).get("sw_member_lag_days"),
            "method": fundamentals.get("pit", {}).get("method"),
            "caveat": fundamentals.get("pit", {}).get("caveat"),
            "crosscheck": fundamentals.get("pit", {}).get("crosscheck"),
        },
        "evidence_level_definition": {
            "full": "该市场所有适用字段均有数据",
            "partial": "部分适用字段有数据",
            "none": "无适用字段数据",
            "applicable_fields": {"CN": ["industry", "announcements", "news"],
                                  "HK/US/CRYPTO": ["news"]},
        },
        "coverage": coverage,
        "missing": missing,
        "items": all_items,
        "warnings": warnings,
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(output), encoding="utf-8")

    # 控制台摘要（证据自检用；不构成结论）
    primary_cov = coverage["primary_pool"]
    print("== ACAD1 研究卡 · 基本面证据预览（只读） ==")
    print(f"pool_date={pool_date} pool_cards={primary_cov['cards']} "
          f"research_cards_at_date={cards_at_pool_date} supplementary={len(supplementary)}")
    print(f"卡匹配：池行 {len(pool_rows)} / 预览 {len(primary)} / 缺卡 {len(card_missing)}；"
          f"supplementary {len(supplementary)} / 无证据卡 {len(cards_without_evidence)}")
    print(f"coverage[primary] industry {primary_cov['industry']['covered']} covered / "
          f"{primary_cov['industry']['not_applicable']} n/a | "
          f"ann {primary_cov['announcements']['covered']} covered / "
          f"{primary_cov['announcements']['not_applicable']} n/a | "
          f"news {primary_cov['news']['covered']}/{primary_cov['cards']} | "
          f"evidence full={primary_cov['evidence_level']['full']} partial={primary_cov['evidence_level']['partial']}")
    print(f"gaps[primary] kinds={primary_cov['gaps']['by_kind']} true_missing={primary_cov['gaps']['true_missing_entries']}")
    print(f"PIT lag={fundamentals.get('pit', {}).get('sw_member_lag_days')}d "
          f"latest_event={fundamentals.get('pit', {}).get('sw_member_latest_event_date')}")
    print(f"card_snapshot_sha256={state.get('card_snapshot_sha256')}")
    if not args.quiet:
        for item in all_items:
            industry = item["industry"].get("value") or {}
            industry_text = industry.get("sw_l1_name") or item["industry"].get("status") or "-"
            ann = (item["announcements"].get("value") or {})
            news = (item["news"].get("value") or {})
            gap_text = ",".join(sorted({gap["kind"] for gap in item["gaps"]})) or "无"
            print(f"  {item['seq']:3s} {item['market']:6s} {item['code']:10s} "
                  f"{str(item['name'] or '-'):12s} card={'Y' if item['card'] else 'N'} "
                  f"industry={industry_text:8s} ann={str(ann.get('count')):>3s}@"
                  f"{ann.get('latest_date') or '-':>8s} news={str(news.get('count')):>3s}@"
                  f"{str(news.get('latest', {}).get('time') if isinstance(news.get('latest'), dict) else '-'):19s} "
                  f"gaps={gap_text}")
    print(f"saved {out_json} ({out_json.stat().st_size} bytes)")
    print(f"saved {out_md} ({out_md.stat().st_size} bytes)")
    return 0


def fundamentals_items_pool(fundamentals: dict) -> list[dict]:
    """离线模式兜底：用 fundamentals 主池 items 还原池行。"""
    rows = []
    for item in (fundamentals.get("items") or []):
        if not item.get("in_primary_pool"):
            continue
        rows.append({
            "pool_date": fundamentals.get("pool_date"),
            "market": item.get("market"),
            "code": item.get("code"),
            "name": item.get("name"),
            "strategy_id": item.get("pool_strategy") or "",
            "signal": item.get("pool_signal"),
            "score": None,
            "data_date": item.get("pool_data_date"),
            "status": "selected",
        })
    return rows


# --------------------------------------------------------------------------- #
# Markdown 渲染
# --------------------------------------------------------------------------- #
def _fmt_industry(item: dict) -> str:
    industry = item["industry"]
    value = industry.get("value") or {}
    if industry.get("status") == "covered":
        return f"{value.get('sw_l1_name') or '-'}（PIT {value.get('sw_l1_code') or '-'}）"
    return f"—（{industry.get('status')}）"


def _fmt_ann(item: dict) -> str:
    block = item["announcements"]
    value = block.get("value") or {}
    if block.get("status") == "covered":
        return f"{value.get('count')} 条 / {value.get('latest_date') or '-'}"
    return f"—（{block.get('status')}）"


def _fmt_news(item: dict) -> str:
    block = item["news"]
    value = block.get("value") or {}
    if block.get("status") == "covered":
        latest = value.get("latest") or {}
        return f"{value.get('count')} 条 / {latest.get('time') or '-'}"
    return f"—（{block.get('status')}）"


def _fmt_gaps(item: dict) -> str:
    if not item["gaps"]:
        return "无"
    parts = []
    for gap in item["gaps"]:
        parts.append(f"{gap['field']}:{gap['kind']}")
    return "<br>".join(parts)


def render_markdown(payload: dict) -> str:
    scope = payload["scope"]
    coverage = payload["coverage"]
    primary = coverage["primary_pool"]
    supp = coverage["supplementary"]
    lines: list[str] = []
    lines.append("# ACAD1 研究卡 · 基本面证据预览（只读）")
    lines.append("")
    lines.append(f"- 生成时间：`{payload['generated_at']}`；池日：`{scope['pool_date']}`")
    lines.append(f"- 输入：`{payload['inputs']['fundamentals']['path']}`（"
                 f"generated_at=`{payload['inputs']['fundamentals']['generated_at']}`）")
    lines.append(f"- 研究卡来源：`{payload['inputs']['research_cards']['source']}`")
    lines.append("- **只读**：仅 SELECT `v2_acad1_pool` / `v2_stock_research`；"
                 "未写 `v2_stock_research`，未改 `status`/`reviewer`/`reviewed_at`/`direction`/`rating`；"
                 "卡面字段为读取时只读快照。")
    lines.append("- **非结论**：本文件只是基本面证据预览，不含评级/方向/买卖建议，不自动通过/否决；"
                 "缺口按 kind 如实标注。")
    lines.append("")
    lines.append("## 1. 范围与覆盖")
    lines.append("")
    lines.append(f"- 最新 ACAD1 池：**{scope['primary_pool_rows']}** 行（"
                 f"CN{primary['by_market'].get('CN', 0)}/HK{primary['by_market'].get('HK', 0)}/"
                 f"US{primary['by_market'].get('US', 0)}/CRYPTO{primary['by_market'].get('CRYPTO', 0)}），"
                 f"预览 **{scope['primary_preview_cards']}** 张卡，缺卡 **{len(scope['card_missing_for_pool_rows'])}**。")
    lines.append(f"- 研究卡表中该池日共 {scope['cards_at_pool_date']} 张卡；非最新池 "
                 f"{scope['cards_at_pool_date'] - scope['primary_preview_cards'] if scope['cards_at_pool_date'] else 0} 张，"
                 f"其中 {scope['supplementary_cards']} 张有证据作为补充预览（非最新池），"
                 f"{len(scope['cards_without_evidence'])} 张无证据仅记录对账。")
    lines.append(f"- 行业（申万 L1 PIT）：covered **{primary['industry']['covered']}**，"
                 f"not_applicable **{primary['industry']['not_applicable']}**，missing **{primary['industry']['missing']}**。")
    lines.append(f"- 公告（anns_d 90d）：covered **{primary['announcements']['covered']}**，"
                 f"not_applicable **{primary['announcements']['not_applicable']}**，"
                 f"no_data **{primary['announcements']['no_data']}**，missing **{primary['announcements']['missing']}**。")
    lines.append(f"- 新闻（30d 关键词）：covered **{primary['news']['covered']}/{primary['cards']}**，"
                 f"no_match **{primary['news']['no_match']}**，no_alias **{primary['news']['no_alias']}**。")
    lines.append(f"- 证据完整度：full **{primary['evidence_level']['full']}**，"
                 f"partial **{primary['evidence_level']['partial']}**，none **{primary['evidence_level']['none']}**"
                 "（口径：CN 适用「行业+公告+新闻」；非 CN 无行业/公告源，仅「新闻」适用，"
                 "故非 CN 有新闻即记为 full）。")
    gaps = primary["gaps"]
    gap_kind_text = "、".join(f"{kind} {count}" for kind, count in sorted(gaps["by_kind"].items())) or "无"
    lines.append(f"- 缺口：共 {gaps['total_gap_entries']} 处 → {gap_kind_text}；"
                 f"其中真缺失（missing/source_missing）**{gaps['true_missing_entries']}** 处。")
    if scope.get("codes_filter"):
        lines.append(f"- 注意：本次运行按 `--codes` 过滤，仅展示 {scope['primary_preview_cards']} 张主池卡"
                     f"（过滤条件：{', '.join(scope['codes_filter'])}）；池内其它卡本次未列出。")
    pit = payload.get("pit") or {}
    lines.append(f"- PIT 滞后：sw_l1_member 最新成分事件 `{pit.get('latest_event_date')}`，"
                 f"相对池日滞后 **{pit.get('lag_days')} 天**；该日之后成分调整不可见，预览不推断。")
    lines.append("")
    lines.append("## 2. 卡片证据总表（最新 ACAD1 池）")
    lines.append("")
    lines.append("| # | 市场 | 代码 | 名称 | 来源策略 | 卡状态(只读) | 行业(PIT) | 公告 90d | 新闻 30d | 缺口 |")
    lines.append("|---|------|------|------|----------|-------------|-----------|----------|----------|------|")
    for item in [row for row in payload["items"] if row["in_primary_pool"]]:
        card = item["card"] or {}
        card_text = f"{card.get('status') or '无卡'}/{card.get('direction') or '-'}"
        lines.append(
            f"| {item['seq']} | {item['market']} | {_md_escape(item['code'])} | "
            f"{_md_escape(item['name'] or '-')} | {_md_escape(card.get('strategy_id') or item.get('pool', {}).get('strategy_id') or '-')} | "
            f"{_md_escape(card_text)} | {_md_escape(_fmt_industry(item))} | "
            f"{_md_escape(_fmt_ann(item))} | {_md_escape(_fmt_news(item))} | {_fmt_gaps(item)} |")
    lines.append("")
    lines.append("## 3. 逐卡证据摘录（最近公告/新闻原文标题）")
    lines.append("")
    for item in payload["items"]:
        if not item["in_primary_pool"]:
            continue
        card = item["card"] or {}
        lines.append(f"### {item['seq']} {item['market']} `{item['code']}` {item['name'] or ''}".rstrip())
        lines.append("")
        lines.append(f"- 卡：`{(item.get('pool') or {}).get('strategy_id')}` / status=`{card.get('status')}` / "
                     f"direction=`{card.get('direction')}` / rating=`{card.get('rating')}` / "
                     f"reviewer=`{card.get('reviewer') or '空'}`（只读快照，未修改）")
        industry = item["industry"]
        industry_value = industry.get("value") or {}
        if industry.get("status") == "covered":
            lines.append(f"- 行业：{industry_value.get('sw_l1_name')}（`{industry_value.get('sw_l1_code')}`，"
                         f"成员 {industry_value.get('member_in_date')} 起，PIT as-of {item['pool']['pool_date']}，"
                         f"sw_l1_member 滞后 {industry.get('pit_lag_days')} 天）")
        else:
            lines.append(f"- 行业：—（{industry.get('status')}；{(industry.get('gap') or {}).get('reason') or ''}）")
        ann = item["announcements"]
        ann_value = ann.get("value") or {}
        if ann.get("status") == "covered":
            lines.append(f"- 公告（90d）：{ann_value.get('count')} 条，最近 {ann_value.get('latest_date')}"
                         f"「{_short(ann_value.get('latest_title'), 90)}」")
            history = ann.get("history_check") or {}
            if history.get("count") is not None:
                lines.append(f"  - 全历史交叉校验：{history.get('count')} 条（{history.get('source')}，"
                             f"最近 {history.get('latest_date')}）")
        else:
            lines.append(f"- 公告：—（{ann.get('status')}；{(ann.get('gap') or {}).get('reason') or ''}）")
        news = item["news"]
        news_value = news.get("value") or {}
        if news.get("status") == "covered":
            per_source = news.get("per_source") or {}
            source_counts = "；".join(
                f"{name} {block.get('count')}" for name, block in per_source.items() if block.get("available"))
            latest = news_value.get("latest") or {}
            lines.append(f"- 新闻（30d）：{news_value.get('count')} 条（{source_counts}），"
                         f"最近 {latest.get('time')}「{_short(latest.get('title'), 90)}」")
        else:
            lines.append(f"- 新闻：—（{news.get('status')}；{(news.get('gap') or {}).get('reason') or ''}）")
        if item["gaps"]:
            lines.append("- 缺口：" + "；".join(
                f"{gap['field']} [{gap['kind']}] {gap.get('reason') or ''}" for gap in item["gaps"]))
        else:
            lines.append("- 缺口：无")
        lines.append("")
    lines.append("## 4. 补充预览（研究卡表中非最新池的卡，仅当有证据）")
    lines.append("")
    supplementary_items = [row for row in payload["items"] if not row["in_primary_pool"]]
    if supplementary_items:
        lines.append("| # | 市场 | 代码 | 名称 | 卡状态(只读) | 行业(PIT) | 公告 90d | 新闻 30d | 缺口 |")
        lines.append("|---|------|------|------|-------------|-----------|----------|----------|------|")
        for item in supplementary_items:
            card = item["card"] or {}
            card_text = f"{card.get('status') or '-'}/{card.get('direction') or '-'}"
            lines.append(
                f"| {item['seq']} | {item['market']} | {_md_escape(item['code'])} | "
                f"{_md_escape(item['name'] or '-')} | {_md_escape(card_text)} | "
                f"{_md_escape(_fmt_industry(item))} | {_md_escape(_fmt_ann(item))} | "
                f"{_md_escape(_fmt_news(item))} | {_fmt_gaps(item)} |")
        lines.append("")
        lines.append(f"> 补充说明：这 {supp['cards']} 张卡存在于 `v2_stock_research`（pool_date={scope['pool_date']}），"
                     "但不在最新 ACAD1 池；仅作证据补充，不代表池归属，未改任何卡字段。")
        lines.append("")
    else:
        lines.append("- 无（--no-supplementary，或研究卡表中没有带 fundamentals 证据的非池卡）。")
        lines.append("")
    lines.append("## 5. 缺口清单")
    lines.append("")
    missing_primary = payload["missing"]["primary_pool"]
    if not missing_primary["items"]:
        lines.append("- 无。")
    else:
        for entry in missing_primary["items"]:
            gap_text = "；".join(
                f"{gap['field']} [{gap['kind']}] {gap.get('reason') or ''}" for gap in entry["gaps"])
            lines.append(f"- {entry['market']} `{entry['code']}` {entry['name'] or ''}"
                         f"（证据完整度 {entry['evidence_level']}）：{gap_text}")
    if len(scope["cards_without_evidence"]):
        lines.append("")
        lines.append("对账：以下研究卡在卡表中但 fundamentals 无证据（未预览，只读记录）：" )
        for card in scope["cards_without_evidence"]:
            lines.append(f"- `{card['strategy_id']}` `{card['code']}`"
                         f"（{card.get('market') or '?'}）—— {card['reason']}")
    lines.append("")
    lines.append("## 6. 口径与只读声明")
    lines.append("")
    lines.append("1. 行业：`sw_l1_member` PIT（in_date<=asof 且 out_date 为空或>=asof）+ `sw_classify_l1` 的 L1 名称；"
                 "申万仅覆盖 A 股，非 A 股 not_applicable 不是抓取失败。")
    lines.append("2. 公告：`tushare/anns_d` 90 天窗口；`ann_history.db` 全历史仅作交叉校验。")
    lines.append("3. 新闻：30 天窗口、时间<=asof 23:59:59；关键词匹配（股票名/英文核心名/括号代码），"
                 "两源（tushare/news、news_db）有重叠、count 不相加，可能漏配/误配，引用前人工复核。")
    lines.append("4. 缺口 kind：" + "、".join(f"`{kind}`={text}" for kind, text in GAP_KIND_LEGEND.items()) + "。")
    lines.append("5. 只读证明：本次未执行任何写 SQL；研究卡快照 sha256=`"
                 f"{payload['read_only_proof'].get('card_snapshot_sha256')}`"
                 "（pool_date 下全部研究卡可变字段）；事后可用同口径复算比对是否被改动。")
    lines.append("6. 本文件由 `scripts/card_evidence_preview.py` 生成；"
                 f"输出仅 `{payload['read_only_proof']['outputs_written'][0]}`、"
                 f"`{payload['read_only_proof']['outputs_written'][1]}` 两个文件。")
    lines.append("")
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("### 附：运行告警（如实记录）")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
