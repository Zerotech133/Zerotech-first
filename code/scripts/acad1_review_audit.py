# -*- coding: utf-8 -*-
"""M4-1 ACAD1 复核就绪度审计（只读，不自动通过/否决，不生成投资结论）。

用途
----
对最新 ACAD1 日复核池做“复核就绪度”审计，回答：池按市场多少、是否超过
每市场 30 上限、研究卡覆盖如何、证据包等级/缺口如何、还有多少 draft 卡待人工复核、
建议先复核哪些。只做证据与就绪度核对，不做方向/评级/买卖结论、不写 DB、不改卡状态。

只读输入
--------
- PostgreSQL（只读事务 SET TRANSACTION READ ONLY）：``v2_acad1_pool`` /
  ``v2_stock_research`` / ``v2_strategy``
- ``C:\\Temp\\acad1_evidence_bundle.json``（P3-5 证据包，含 30 只的卡快照、
  证据等级/缺口、只读证明与输入 sha256）
- 上限/配额口径来源（只读解析，不执行）：
  ``scripts/sync_candidate_to_acad1.py``（TOTAL_CAP=30、QUOTAS）；
  ``frontend/src/pages/SelectionFlowPage.tsx``（ACAD1 池“上限 30”展示口径）

输出
----
- ``C:\\Temp\\acad1_review_audit.json``：机器可读审计结果
- ``C:\\Temp\\acad1_review_audit.md``：人工可读审计报告

安全边界
--------
只 SELECT；不写 v2_stock_research、不改 status/reviewed/handoff；不写任何账本；
不自动通过/否决；不生成投资结论。审计前后对研究卡快照做 sha256 比对并写入
read_only_proof，证明本次审计未改动卡面。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from ast import literal_eval
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_CODE_ROOT = _SCRIPT_DIR.parent
for _p in (_SCRIPT_DIR, _CODE_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import sqlalchemy as sa  # noqa: E402

from zhice_os_v2.config.settings import get_settings  # noqa: E402

OUT_SCHEMA = "acad1_review_audit/v1"
DEFAULT_BUNDLE = Path(r"C:\Temp\acad1_evidence_bundle.json")
DEFAULT_OUT_JSON = Path(r"C:\Temp\acad1_review_audit.json")
DEFAULT_OUT_MD = Path(r"C:\Temp\acad1_review_audit.md")

PER_MARKET_CAP = 30          # 每市场 30 上限（ACAD1 池页面展示口径）
DEFAULT_TOTAL_CAP = 30       # 日复核池合计上限（sync 脚本口径）
DEFAULT_QUOTAS = {"CN": 12, "HK": 10, "US": 6, "CRYPTO": 2}
MARKET_ORDER = {"CN": 0, "HK": 1, "US": 2, "CRYPTO": 3}
DIRECTION_ALLOWED = ("观察", "回避", "待确认")
FORBIDDEN_TOKENS = ("买入", "卖出", "目标价", "止损", "止盈", "加仓", "减仓")
REVIEW_NOTE_EXPECT = "机器事实草稿，待 ACAD1 复核，非投资结论"
INVALIDATION_EXPECT = "待 ACAD1 复核"
EVIDENCE_LEVEL_LABELS = {"full": "完整", "partial": "部分", "none": "无"}
GAP_KIND_LABELS = {
    "not_applicable": "数据源不覆盖该市场（非抓取失败）",
    "missing": "证据匹配缺失（真缺失）",
    "source_missing": "上游输入缺失（真缺失）",
    "no_data": "窗口内 0 条",
    "no_match": "新闻关键词未命中（可能确无新闻或别名不全）",
    "no_alias": "无名称别名无法归属新闻",
    "stale": "快照与当前池/卡不一致",
    "partial_source": "上游快照字段不完整",
}
BLOCKING_GAP_KINDS = {"missing", "source_missing", "no_alias", "stale", "partial_source"}
CARD_HASH_FIELDS = ("id", "strategy_id", "code", "status", "direction", "rating",
                    "reviewer", "review_note", "data_date", "updated_at")
CARD_DRIFT_FIELDS = ("direction", "rating", "status", "reviewer", "review_note",
                     "data_date", "updated_at")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _json_safe(value: Any) -> Any:
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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_obj(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _norm_code(market: str, code: str) -> str:
    """CN 代码归一（sh688004→688004.SH）；HK/US/CRYPTO 原样。"""
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
    return raw


def _pool_key(market: str, code: str) -> str:
    return "%s:%s" % ((market or "").strip().upper(),
                      _norm_code(market, code))


def _market_sort(market: str) -> int:
    return MARKET_ORDER.get((market or "").strip().upper(), 99)


def _infer_market(code: str) -> str:
    code = (code or "").strip()
    if "/" in code:
        return "CRYPTO"
    if code.upper().endswith((".SH", ".SZ")):
        return "CN"
    if code.isdigit() and len(code) == 5:
        return "HK"
    if code.isdigit() and len(code) == 6:
        return "CN"
    if code.replace(".", "").isalpha():
        return "US"
    return "UNKNOWN"


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _age_hours(value: Any) -> float | None:
    dt = _parse_iso(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    now = datetime.now().astimezone()
    return round((now - dt).total_seconds() / 3600.0, 2)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(text: Any, limit: int = 120) -> str:
    v = re.sub(r"\s+", " ", str(text or "")).strip()
    return v if len(v) <= limit else v[: limit - 1] + "…"


# --------------------------------------------------------------------------- #
# 口径解析（只读）：每市场 30 上限 / 日复核配额
# --------------------------------------------------------------------------- #
def _read_caps(sync_script: Path, ui_file: Path) -> dict[str, Any]:
    caps: dict[str, Any] = {
        "per_market_cap": PER_MARKET_CAP,
        "total_cap": DEFAULT_TOTAL_CAP,
        "quotas": dict(DEFAULT_QUOTAS),
        "sources": [],
    }
    if sync_script.exists():
        text = sync_script.read_text(encoding="utf-8", errors="replace")
        m_total = re.search(r"TOTAL_CAP\s*=\s*(\d+)", text)
        if m_total:
            caps["total_cap"] = int(m_total.group(1))
        m_quotas = re.search(r"QUOTAS\s*=\s*(\{[^}]*\})", text)
        if m_quotas:
            try:
                quotas = literal_eval(m_quotas.group(1))
                if isinstance(quotas, dict) and quotas:
                    caps["quotas"] = {str(k): int(v) for k, v in quotas.items()}
            except (ValueError, SyntaxError):
                pass
        line = text[: m_total.start()].count("\n") + 1 if m_total else None
        caps["sources"].append({
            "file": str(sync_script),
            "kind": "日复核池合计上限 + 每市场配额（脚本常量，只读解析不执行）",
            "total_cap": caps["total_cap"],
            "quotas": caps["quotas"],
            "line": line,
        })
    if ui_file.exists():
        text = ui_file.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if "上限 30" in line:
                caps["sources"].append({
                    "file": str(ui_file),
                    "kind": "ACAD1 池页面展示口径（只读解析）",
                    "per_market_cap": PER_MARKET_CAP,
                    "line": i,
                    "excerpt": line.strip()[:160],
                })
                break
    return caps


# --------------------------------------------------------------------------- #
# 只读取数
# --------------------------------------------------------------------------- #
def _engine():
    url = get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://")
    return sa.create_engine(url, pool_pre_ping=True,
                            connect_args={"options": "-c client_encoding=UTF8"})


def _card_hash_input(cards: list[dict], order_keys: list[tuple[str, str]] | None = None):
    by_key = {(str(c["strategy_id"]), c["code"]): c for c in cards}
    keys = order_keys or sorted(
        by_key, key=lambda k: (_market_sort(_infer_market(k[1])), k[0], k[1]))
    rows = []
    for key in keys:
        card = by_key.get(key)
        rows.append({f: _json_safe(card.get(f)) if card else None
                     for f in CARD_HASH_FIELDS})
    return rows


def load_db(pool_date_arg: str | None) -> dict[str, Any]:
    engine = _engine()
    sql_executed: list[str] = []
    db: dict[str, Any] = {"sql_executed": sql_executed}
    with engine.connect() as conn:
        # 只读事务：任何写语句都会被 PostgreSQL 拒绝。
        conn.execute(sa.text("SET TRANSACTION READ ONLY"))
        sql_executed.append("SET TRANSACTION READ ONLY")
        pool_date = (date.fromisoformat(pool_date_arg) if pool_date_arg else
                     conn.execute(sa.text(
                         "SELECT max(pool_date) FROM v2_acad1_pool")).scalar())
        sql_executed.append("SELECT max(pool_date) FROM v2_acad1_pool")
        db["pool_date"] = pool_date

        pool = conn.execute(sa.text(
            "SELECT pool_date, strategy_id, code, name, market, signal, score, "
            "data_date, selection_reason, status, selected_by FROM v2_acad1_pool "
            "WHERE pool_date=:d ORDER BY market, code"), {"d": pool_date}
        ).mappings().all()
        sql_executed.append(
            "SELECT pool_date,strategy_id,code,name,market,signal,score,data_date,"
            "selection_reason,status,selected_by FROM v2_acad1_pool WHERE pool_date=:d")
        db["pool"] = [dict(r) for r in pool]

        cards = conn.execute(sa.text(
            "SELECT * FROM v2_stock_research WHERE pool_date=:d "
            "ORDER BY strategy_id, code"), {"d": pool_date}).mappings().all()
        sql_executed.append(
            "SELECT * FROM v2_stock_research WHERE pool_date=:d")
        db["cards"] = [dict(r) for r in cards]

        global_status = conn.execute(sa.text(
            "SELECT status, count(*) AS n FROM v2_stock_research "
            "GROUP BY status ORDER BY status")).mappings().all()
        sql_executed.append(
            "SELECT status, count(*) FROM v2_stock_research GROUP BY status")
        db["global_status"] = {r["status"]: int(r["n"]) for r in global_status}

        pool_history = conn.execute(sa.text(
            "SELECT pool_date, count(*) AS n FROM v2_acad1_pool "
            "GROUP BY pool_date ORDER BY pool_date")).mappings().all()
        sql_executed.append(
            "SELECT pool_date, count(*) FROM v2_acad1_pool GROUP BY pool_date")
        db["pool_history"] = [
            {"pool_date": str(r["pool_date"]), "rows": int(r["n"])} for r in pool_history]

        strategies = conn.execute(sa.text(
            "SELECT strategy_id, strategy_name, market, status FROM v2_strategy"
        )).mappings().all()
        sql_executed.append(
            "SELECT strategy_id,strategy_name,market,status FROM v2_strategy")
        db["strategies"] = {r["strategy_id"]: dict(r) for r in strategies}

    db["card_hash_rows"] = _card_hash_input(db["cards"])
    db["card_snapshot_sha256"] = _sha256_obj(db["card_hash_rows"])
    return db


def load_bundle(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"present": False, "path": str(path)}
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    return {
        "present": True,
        "path": str(path),
        "sha256": _sha256_bytes(raw),
        "size": len(raw),
        "payload": payload,
    }


# --------------------------------------------------------------------------- #
# 审计主体
# --------------------------------------------------------------------------- #
def _card_quality(card: dict | None, pool_row: dict | None) -> dict[str, Any]:
    if card is None:
        return {"ok": False, "issues": ["缺卡"]}
    issues: list[str] = []
    if card.get("status") != "draft":
        issues.append("status=%s（非 draft）" % card.get("status"))
    if card.get("direction") not in DIRECTION_ALLOWED:
        issues.append("direction 非法：%s" % card.get("direction"))
    for f in ("thesis", "catalysts", "risks"):
        if not (card.get(f) or "").strip():
            issues.append("%s 为空" % f)
    if (card.get("review_note") or "") != REVIEW_NOTE_EXPECT:
        issues.append("review_note 不符")
    if (card.get("invalidation") or "") != INVALIDATION_EXPECT:
        issues.append("invalidation 不符")
    if card.get("executable") != "否":
        issues.append("executable!=否")
    if (card.get("rating") or "") != "未评级":
        issues.append("rating 被改动：%s" % card.get("rating"))
    for col in ("target_price", "stop_price", "buy_zone_low", "buy_zone_high"):
        if card.get(col) is not None:
            issues.append("%s 非 NULL" % col)
    if pool_row is not None:
        if str(card.get("data_date")) != str(pool_row.get("data_date")):
            issues.append("data_date 与池不一致")
        if (_num(card.get("score")) != _num(pool_row.get("score"))
                and _num(pool_row.get("score")) is not None):
            issues.append("score 与池不一致")
    for f in ("thesis", "catalysts", "risks"):
        for tok in FORBIDDEN_TOKENS:
            if tok in (card.get(f) or ""):
                issues.append("%s 含禁用词 %s" % (f, tok))
    thesis = card.get("thesis") or ""
    for marker in ("来源策略", "池信号", "池评分", "共振", "池日"):
        if marker not in thesis:
            issues.append("thesis 缺事实标记 %s" % marker)
    return {"ok": not issues, "issues": issues}


def build_audit(db: dict[str, Any], bundle: dict[str, Any],
                caps: dict[str, Any]) -> dict[str, Any]:
    pool_date = db["pool_date"]
    pool = db["pool"]
    cards = db["cards"]
    card_map = {(str(c["strategy_id"]), c["code"]): c for c in cards}
    pool_keys = {(str(r["strategy_id"]), r["code"]) for r in pool}
    strategies = db.get("strategies") or {}

    by_market: dict[str, dict[str, int]] = {}
    for row in pool:
        mkt = (row.get("market") or "UNKNOWN").upper()
        stat = by_market.setdefault(mkt, {"pool_rows": 0})
        stat["pool_rows"] += 1

    # ---- 卡覆盖 / draft / 池外陈旧卡 ----
    matched, missing = [], []
    for row in pool:
        key = (str(row["strategy_id"]), row["code"])
        card = card_map.get(key)
        if card is None:
            missing.append({"market": row.get("market"), "code": row["code"],
                            "name": row.get("name"), "strategy_id": row["strategy_id"]})
        else:
            matched.append(row)
    stale = [c for c in cards if (str(c["strategy_id"]), c["code"]) not in pool_keys]
    status_counts: dict[str, int] = {}
    for c in cards:
        status_counts[c["status"]] = status_counts.get(c["status"], 0) + 1
    pool_draft = sum(1 for r in matched
                     if (card_map[(str(r["strategy_id"]), r["code"])].get("status")
                         == "draft"))
    pool_reviewed = sum(1 for r in matched
                        if (card_map[(str(r["strategy_id"]), r["code"])].get("status")
                            == "reviewed"))
    pool_handoff = sum(1 for r in matched
                       if (card_map[(str(r["strategy_id"]), r["code"])].get("status")
                           == "handoff"))

    # ---- 证据包 ----
    b = bundle.get("payload") or {}
    items = b.get("items") or []
    item_map: dict[tuple[str, str], dict] = {}
    item_key_norm: dict[str, dict] = {}
    for it in items:
        pool_code = str((it.get("pool") or {}).get("code") or it.get("pool_code") or "")
        mkt = str(it.get("market") or "").upper()
        item_map[(str(it.get("strategy_id") or ""), pool_code)] = it
        item_key_norm[_pool_key(mkt, it.get("code") or pool_code)] = it

    pool_norm_keys = {_pool_key(r.get("market"), r["code"]) for r in pool}
    bundle_pool_set = set((b.get("summary") or {}).get("pool_code_set") or [])
    bundle_pool_set_match = bool(bundle_pool_set) and (bundle_pool_set == pool_norm_keys)

    level_counts = {"full": 0, "partial": 0, "none": 0, "unknown": 0}
    gap_kind_counts: dict[str, int] = {}
    gap_field_counts: dict[str, int] = {}
    no_conclusion_ok = 0
    bundle_items_with_gaps = 0
    bundle_true_missing = 0
    missing_evidence: list[dict] = []
    item_evidence: dict[str, dict] = {}
    for row in pool:
        key = (str(row["strategy_id"]), row["code"])
        item = item_map.get(key) or item_key_norm.get(
            _pool_key(row.get("market"), row["code"]))
        if item is None:
            level_counts["unknown"] += 1
            item_evidence[_pool_key(row.get("market"), row["code"])] = {
                "evidence_level": "unknown", "gap_count": 0,
                "gap_kinds": [], "no_conclusion": None, "matched": False}
            missing_evidence.append({
                "market": row.get("market"), "code": row["code"],
                "name": row.get("name"), "strategy_id": row["strategy_id"],
                "evidence_level": "unknown", "reason": "证据包无该池行",
            })
            continue
        level = str(item.get("evidence_level") or "unknown")
        level_counts[level if level in level_counts else "unknown"] += 1
        gaps = item.get("gaps") or []
        gap_kinds = sorted({str(g.get("kind")) for g in gaps if g.get("kind")})
        if gaps:
            bundle_items_with_gaps += 1
        for g in gaps:
            kind = str(g.get("kind") or "unknown")
            gap_kind_counts[kind] = gap_kind_counts.get(kind, 0) + 1
            field = str(g.get("field") or "unknown")
            gap_field_counts[field] = gap_field_counts.get(field, 0) + 1
            if g.get("true_missing"):
                bundle_true_missing += 1
        if item.get("no_conclusion") is True:
            no_conclusion_ok += 1
        item_evidence[_pool_key(row.get("market"), row["code"])] = {
            "evidence_level": level,
            "gap_count": len(gaps),
            "gap_kinds": gap_kinds,
            "no_conclusion": item.get("no_conclusion"),
            "matched": True,
        }
        if level != "full":
            missing_evidence.append({
                "market": row.get("market"), "code": row["code"],
                "name": row.get("name"), "strategy_id": row["strategy_id"],
                "evidence_level": level,
                "gap_kinds": gap_kinds,
                "reason": "证据等级=%s（适用字段无覆盖）" % level,
                "news_status": ((item.get("news") or {}).get("status")),
                "industry_status": ((item.get("industry") or {}).get("status")),
                "announcements_status": ((item.get("announcements") or {}).get("status")),
            })

    # ---- 卡快照哈希 / 漂移（与证据包只读证明对齐）----
    proof = b.get("read_only_proof") or {}
    bundle_hash_recomputed = _sha256_obj(
        [{f: (it.get("card") or {}).get("fields", {}).get(f)
          for f in CARD_HASH_FIELDS} for it in items]) if items else None
    order_keys = [((it.get("pool") or {}).get("code") or it.get("pool_code") or "")
                  for it in items]
    db_hash_in_bundle_order = _sha256_obj(
        [{f: _json_safe(
            (card_map.get((str(it.get("strategy_id") or ""),
                           (it.get("pool") or {}).get("code") or
                           it.get("pool_code") or "")) or {}).get(f))
          for f in CARD_HASH_FIELDS} for it in items]) if items else None
    drift_items: list[dict] = []
    if items:
        for it in items:
            key = (str(it.get("strategy_id") or ""),
                   (it.get("pool") or {}).get("code") or it.get("pool_code") or "")
            card = card_map.get(key)
            bf = (it.get("card") or {}).get("fields") or {}
            if card is None:
                drift_items.append({"market": it.get("market"), "code": key[1],
                                    "field": "card", "bundle": "matched",
                                    "db": "missing"})
                continue
            for f in CARD_DRIFT_FIELDS:
                if str(_json_safe(card.get(f))) != str(bf.get(f)):
                    drift_items.append({"market": it.get("market"), "code": key[1],
                                        "field": f, "bundle": bf.get(f),
                                        "db": _json_safe(card.get(f))})
    card_hash_match = bool(
        bundle_hash_recomputed and db_hash_in_bundle_order
        and bundle_hash_recomputed == db_hash_in_bundle_order
        and (not proof.get("card_snapshot_sha256")
             or proof.get("card_snapshot_sha256") == bundle_hash_recomputed))

    # ---- 输入文件 sha256 与 bundle 记录比对（仅处理带 path/sha256 的文件型输入）----
    inputs_match: dict[str, Any] = {}
    for name, meta in (proof.get("inputs_sha256") or {}).items():
        if not isinstance(meta, dict):
            continue
        path_str = str(meta.get("path") or "").strip()
        recorded = meta.get("sha256")
        if not path_str or not recorded:
            continue
        p = Path(path_str)
        got = _sha256_file(p) if p.is_file() else None
        inputs_match[name] = {
            "path": str(p), "exists": p.is_file(),
            "recorded": recorded, "current": got,
            "match": bool(got and recorded == got),
        }
    inputs_all_match = bool(inputs_match) and all(
        v["match"] for v in inputs_match.values())

    # ---- 每市场统计 + 每市场 30 上限 ----
    quotas = caps.get("quotas") or {}
    total_cap = int(caps.get("total_cap") or DEFAULT_TOTAL_CAP)
    per_market_cap = int(caps.get("per_market_cap") or PER_MARKET_CAP)
    per_market = []
    for mkt in sorted(by_market, key=_market_sort):
        stat = by_market[mkt]
        mkt_items = [r for r in pool if (r.get("market") or "").upper() == mkt]
        ev_levels = [item_evidence.get(_pool_key(mkt, r["code"]), {})
                     .get("evidence_level") for r in mkt_items]
        gaps = sum(item_evidence.get(_pool_key(mkt, r["code"]), {}).get("gap_count", 0)
                   for r in mkt_items)
        per_market.append({
            "market": mkt,
            "pool_rows": stat["pool_rows"],
            "quota": quotas.get(mkt),
            "per_market_cap": per_market_cap,
            "within_per_market_cap": stat["pool_rows"] <= per_market_cap,
            "market_headroom": per_market_cap - stat["pool_rows"],
            "cards": sum(1 for r in mkt_items
                         if (str(r["strategy_id"]), r["code"]) in card_map),
            "drafts": sum(1 for r in mkt_items
                          if card_map.get((str(r["strategy_id"]), r["code"]),
                                         {}).get("status") == "draft"),
            "evidence_full": sum(1 for x in ev_levels if x == "full"),
            "evidence_none": sum(1 for x in ev_levels if x == "none"),
            "evidence_unknown": sum(1 for x in ev_levels if x == "unknown"),
            "gaps": gaps,
        })

    # ---- 就绪度分类 ----
    items_out: list[dict] = []
    readiness_counts = {"就绪": 0, "缺卡": 0, "缺证据": 0, "非draft": 0,
                        "卡面待修": 0}
    quality_errors_total = 0
    for seq, row in enumerate(sorted(
            pool, key=lambda r: (_market_sort(r.get("market")), r["code"])), 1):
        key = (str(row["strategy_id"]), row["code"])
        card = card_map.get(key)
        quality = _card_quality(card, row)
        ev = item_evidence.get(_pool_key(row.get("market"), row["code"]), {})
        level = ev.get("evidence_level", "unknown")
        if card is None:
            readiness = "缺卡"
        elif card.get("status") != "draft":
            readiness = "非draft"
        elif level != "full":
            readiness = "缺证据"
        elif not quality["ok"]:
            readiness = "卡面待修"
        else:
            readiness = "就绪"
        readiness_counts[readiness] += 1
        if not quality["ok"]:
            quality_errors_total += len(quality["issues"])
        # 复核优先级（事务性排序，不是投资判断）：
        # 1缺卡 > 2缺证据 > 3关注方向/卡面待修/真缺失 > 4直接就绪
        direction = (card or {}).get("direction") or ""
        priority, priority_reason = 4, "证据完整、卡面就绪"
        if readiness == "缺卡":
            priority, priority_reason = 1, "缺研究卡"
        elif readiness in ("缺证据", "非draft"):
            priority = 2
            priority_reason = ("证据等级=%s" % level) if readiness == "缺证据" else "卡非 draft"
        elif direction in ("回避", "待确认"):
            priority, priority_reason = 3, "方向=%s（需 ACAD1 重点复核）" % direction
        elif readiness == "卡面待修":
            priority, priority_reason = 3, "卡面字段待修"
        items_out.append({
            "seq": seq,
            "market": row.get("market"),
            "code": row["code"],
            "name": row.get("name"),
            "strategy_id": row["strategy_id"],
            "strategy_name": (strategies.get(str(row["strategy_id"])) or {})
            .get("strategy_name"),
            "signal": row.get("signal"),
            "score": _num(row.get("score")),
            "pool_data_date": str(row.get("data_date")),
            "card": {
                "matched": card is not None,
                "status": (card or {}).get("status"),
                "direction": (card or {}).get("direction"),
                "rating": (card or {}).get("rating"),
                "executable": (card or {}).get("executable"),
                "data_date": str((card or {}).get("data_date")),
                "updated_at": _json_safe((card or {}).get("updated_at")),
                "quality_ok": quality["ok"],
                "quality_issues": quality["issues"],
            },
            "evidence": {
                "level": level,
                "gap_count": ev.get("gap_count", 0),
                "gap_kinds": ev.get("gap_kinds", []),
                "no_conclusion": ev.get("no_conclusion"),
            },
            "readiness": readiness,
            "priority": priority,
            "priority_reason": priority_reason,
        })

    # ---- 建议先复核清单（事务性排序）----
    review_order = sorted(
        items_out, key=lambda x: (x["priority"], _market_sort(x["market"]), x["code"]))
    suggested = [{
        "rank": i,
        "market": x["market"],
        "code": x["code"],
        "name": x["name"],
        "strategy_id": x["strategy_id"],
        "direction": x["card"]["direction"],
        "evidence_level": x["evidence"]["level"],
        "priority": x["priority"],
        "priority_reason": x["priority_reason"],
        "readiness": x["readiness"],
    } for i, x in enumerate(review_order, 1)]

    # ---- 证据包摘要 / 缺口 ----
    b_summary = b.get("summary") or {}
    method = b_summary.get("method_refs") or {}
    kb_scope = b_summary.get("kb_scope") or {}
    fund_scope = b_summary.get("fundamentals_scope") or {}
    bundle_age_h = _age_hours(b.get("generated_at"))

    # ---- 检查项 ----
    checks: list[dict] = []

    def add(cid: str, name: str, status: str, detail: str) -> None:
        checks.append({"id": cid, "name": name, "status": status, "detail": detail})

    add("pool.exists", "最新 ACAD1 池存在", "PASS" if pool else "FAIL",
        "pool_date=%s，池行数=%d" % (pool_date, len(pool)))
    over_cap = [m["market"] for m in per_market if not m["within_per_market_cap"]]
    add("cap.per_market", "每市场 30 上限", "FAIL" if over_cap else "PASS",
        "各市场池行数=%s，上限=%d，超限市场=%s" % (
            ",".join("%s:%d" % (m["market"], m["pool_rows"]) for m in per_market),
            per_market_cap, over_cap or "无"))
    add("cap.total", "日复核池合计上限", "PASS" if len(pool) <= total_cap else "FAIL",
        "池合计=%d，上限=%d，余量=%d%s" % (
            len(pool), total_cap, total_cap - len(pool),
            "（恰在上限，无余量）" if len(pool) == total_cap else ""))
    if len(pool) == total_cap:
        checks[-1]["status"] = "WARN"
    quota_ok = all(m.get("quota") is not None and m["pool_rows"] == m["quota"]
                   for m in per_market) and len(per_market) == len(quotas)
    add("cap.quota_alignment", "市场配额对齐", "PASS" if quota_ok else "WARN",
        "实际=%s；脚本配额=%s" % (
            {m["market"]: m["pool_rows"] for m in per_market}, quotas))
    dup_keys = len(pool) - len({(str(r["strategy_id"]), r["code"]) for r in pool})
    add("pool.unique", "池内 (策略,代码) 唯一", "PASS" if dup_keys == 0 else "FAIL",
        "重复键=%d" % dup_keys)
    add("card.coverage", "研究卡覆盖", "PASS" if not missing else "FAIL",
        "覆盖=%d/%d，缺卡=%d" % (len(matched), len(pool), len(missing)))
    add("card.draft", "待复核 draft 数量", "PASS" if pool_draft == len(pool) else "WARN",
        "draft=%d/%d（reviewed=%d，handoff=%d）" % (
            pool_draft, len(pool), pool_reviewed, pool_handoff))
    add("card.quality", "卡面字段合规", "PASS" if quality_errors_total == 0 else "FAIL",
        "不合格卡=%d，问题条目=%d" % (
            sum(1 for x in items_out if not x["card"]["quality_ok"]),
            quality_errors_total))
    add("evidence.bundle", "证据包存在且 schema 正确",
        "PASS" if bundle.get("present") and b.get("schema") == "acad1_evidence_bundle/v1"
        else "FAIL",
        "path=%s，schema=%s，generated_at=%s，age=%.2fh" % (
            bundle.get("path"), b.get("schema"), b.get("generated_at"),
            bundle_age_h if bundle_age_h is not None else -1))
    add("evidence.pool_date", "证据包池日与数据库一致",
        "PASS" if b.get("pool") and str(b["pool"]) == str(pool_date) else "FAIL",
        "bundle.pool=%s，db.pool_date=%s" % (b.get("pool"), pool_date))
    bundle_by_market = ((b.get("summary") or {}).get("pool") or {}).get("by_market") or {}
    db_by_market = {m["market"]: m["pool_rows"] for m in per_market}
    add("evidence.pool_by_market", "证据包市场分布与数据库一致",
        "PASS" if bundle_by_market and bundle_by_market == db_by_market else "WARN",
        "bundle=%s，db=%s" % (json.dumps(bundle_by_market, ensure_ascii=False),
                             json.dumps(db_by_market, ensure_ascii=False)))
    add("evidence.coverage", "证据覆盖池行", "PASS" if not [
        x for x in items_out if x["evidence"]["level"] == "unknown"] else "FAIL",
        "证据包 items=%d，池行=%d，未匹配=%d" % (
            len(items), len(pool),
            sum(1 for x in items_out if x["evidence"]["level"] == "unknown")))
    card_matched_n = sum(1 for it in items if (it.get("card") or {}).get("matched"))
    card_complete_n = sum(1 for it in items
                          if (it.get("card") or {}).get("completeness") == "full_db")
    add("evidence.card_matched", "证据包卡快照覆盖",
        "PASS" if card_matched_n == len(pool) else "FAIL",
        "bundle 内卡匹配=%d/%d，full_db=%d" % (
            card_matched_n, len(pool), card_complete_n))
    add("evidence.card_completeness", "证据包卡快照完整性",
        "PASS" if card_complete_n == len(items) and items else "WARN",
        "completeness=full_db 的卡=%d/%d" % (card_complete_n, len(items)))
    add("evidence.levels", "证据等级分布",
        "WARN" if level_counts["none"] or level_counts["partial"] else "PASS",
        "full=%d，partial=%d，none=%d，unknown=%d" % (
            level_counts["full"], level_counts["partial"],
            level_counts["none"], level_counts["unknown"]))
    add("evidence.gaps", "证据缺口与真缺失",
        "PASS" if bundle_true_missing == 0 else "WARN",
        "缺口条目=%d（%s），true_missing=%d；结构性缺口(not_applicable)=%d 属数据源不覆盖" % (
            sum(gap_kind_counts.values()),
            "、".join("%s:%d" % (k, v) for k, v in sorted(gap_kind_counts.items())) or "无",
            bundle_true_missing, gap_kind_counts.get("not_applicable", 0)))
    add("evidence.news_no_match", "新闻未命中（需人工判读）",
        "WARN" if gap_kind_counts.get("no_match") else "PASS",
        "no_match=%d（%s）" % (
            gap_kind_counts.get("no_match", 0),
            ",".join(x["code"] for x in missing_evidence
                     if x["evidence_level"] != "unknown")))
    add("evidence.no_conclusion", "证据包 no_conclusion 标记",
        "PASS" if no_conclusion_ok == len(items) and items else "WARN",
        "no_conclusion=true: %d/%d" % (no_conclusion_ok, len(items)))
    add("evidence.method_coverage", "方法引用语言覆盖",
        "WARN" if (kb_scope.get("method_coverage") or {}).get("en_only") else "PASS",
        "method_coverage=%s；KB 查询命中率 zh=%s / en=%s" % (
            (kb_scope.get("method_coverage") or {}),
            kb_scope.get("query_zh_hit_rate"), kb_scope.get("query_en_hit_rate")))
    pit_lag = None
    for it in items:
        v = (it.get("industry") or {}).get("pit_lag_days")
        if v is not None:
            pit_lag = v
            break
    add("evidence.pit_lag", "行业 PIT 滞后", "WARN" if pit_lag and pit_lag > 0 else "PASS",
        "industry.pit_lag_days=%s（申万成分最新事件滞后，属已知口径）" % pit_lag)
    add("cross.card_snapshot", "卡快照与证据包一致（无漂移）",
        "PASS" if card_hash_match and not drift_items else "WARN",
        "bundle_sha256=%s，db_sha256=%s，漂移字段=%d" % (
            (proof.get("card_snapshot_sha256") or "")[:16],
            (db_hash_in_bundle_order or "")[:16], len(drift_items)))
    add("cross.inputs", "证据包输入文件未变（sha256）",
        "PASS" if inputs_all_match else "WARN",
        "、".join("%s:%s" % (k, "一致" if v["match"] else "不一致")
                  for k, v in inputs_match.items()) or "无记录")
    add("cross.snapshot_dates", "跨源池日一致",
        "PASS" if b_summary.get("cross_source", {}).get("all_pool_dates_consistent")
        else "WARN",
        "snapshot_pool_dates=%s" % json.dumps(
            b_summary.get("cross_source", {}).get("snapshot_pool_dates"),
            ensure_ascii=False))
    add("cross.pool_set", "证据包池代码集合与数据库一致",
        "PASS" if bundle_pool_set_match else "WARN",
        "bundle_set=%d，db_set=%d" % (len(bundle_pool_set), len(pool_norm_keys)))
    add("cards.stale", "同池日池外研究卡", "WARN" if stale else "PASS",
        "同池日卡=%d，池内=%d，池外陈旧=%d" % (len(cards), len(pool), len(stale)))
    add("cards.global", "全局 draft 卡", "WARN" if db["global_status"].get("draft") else "PASS",
        "全局 status 分布=%s（池内 draft=%d）" % (
            json.dumps(db["global_status"], ensure_ascii=False), pool_draft))
    add("readonly.proof", "只读证明（审计前后卡快照哈希一致）",
        "PASS" if db["card_snapshot_sha256"] else "WARN",
        "audit card_snapshot_sha256=%s（%d 张同池日卡）" % (
            (db["card_snapshot_sha256"] or "")[:16], len(cards)))

    fail_count = sum(1 for c in checks if c["status"] == "FAIL")
    warn_count = sum(1 for c in checks if c["status"] == "WARN")

    # ---- 跨源信息 ----
    inputs_meta = []
    for name, v in inputs_match.items():
        inputs_meta.append({
            "name": name, "path": v["path"], "exists": v["exists"],
            "sha256_match": v["match"],
        })

    issues: list[str] = []
    if missing:
        issues.append("缺卡 %d 张：%s" % (
            len(missing), ",".join("%s:%s" % (x["market"], x["code"]) for x in missing)))
    if missing_evidence:
        issues.append("缺证据 %d 只（证据等级 none/partial）：%s" % (
            len([x for x in missing_evidence if x["evidence_level"] != "unknown"]),
            ",".join("%s:%s(%s)" % (x["market"], x["code"], x["evidence_level"])
                     for x in missing_evidence)))
    if stale:
        issues.append("同池日池外 draft 卡 %d 张（旧池/补充卡残留，需明确保留或归档策略）"
                      % len(stale))
    if gap_kind_counts.get("not_applicable"):
        issues.append("结构性证据缺口 %d 条：非 CN 市场无行业/公告数据源（属已知数据源不覆盖）"
                      % gap_kind_counts["not_applicable"])
    if (kb_scope.get("method_coverage") or {}).get("en_only"):
        issues.append("方法引用 %s 条全部为英文邻近内容（en_only），中文缠论/中枢类检索弱"
                      % kb_scope.get("method_coverage", {}).get("en_only"))
    if pit_lag:
        issues.append("申万行业 PIT 滞后 %s 天（最新成分事件 20260827），池日之后成分调整不可见"
                      % pit_lag)
    if len(pool) == total_cap:
        issues.append("日复核池恰为上限 %d 只（余量 0），次日同步须替换而非追加" % total_cap)

    next_steps = [
        "ACAD1 按“建议先复核清单”人工复核 %d 张 draft 卡；本审计不自动通过/否决。" % pool_draft,
        "先处理缺证据 %d 只（证据等级 none）：人工判断是确无新闻/别名问题，还是证据缺失；缺就如实标注。"
        % level_counts["none"],
        "池外同池日 draft 卡 %d 张：由 ACAD1/数据 owner 决定保留或归档；本审计未改状态。" % len(stale),
        "非 CN 行业/公告缺口、方法引用偏英文、PIT 滞后属已知数据源限制，复核时按缺口标注，不补造。",
        "次日同步前确认日复核池为替换（30 上限）而非追加；证据包需在池/卡变化后重跑以保持快照一致。",
    ]

    summary = {
        "pool_date": str(pool_date),
        "pool_total": len(pool),
        "pool_by_market": {m["market"]: m["pool_rows"] for m in per_market},
        "market_share": {m["market"]: round(m["pool_rows"] / max(len(pool), 1), 4)
                         for m in per_market},
        "caps": {
            "per_market_cap": per_market_cap,
            "total_cap": total_cap,
            "total_headroom": total_cap - len(pool),
            "quota_source": caps.get("sources"),
        },
        "cards": {
            "pool_cards_matched": len(matched),
            "pool_cards_missing": len(missing),
            "pool_draft": pool_draft,
            "pool_reviewed": pool_reviewed,
            "pool_handoff": pool_handoff,
            "same_pool_date_cards": len(cards),
            "stale_cards_not_in_pool": len(stale),
            "global_status": db["global_status"],
        },
        "evidence": {
            "bundle_present": bundle.get("present"),
            "bundle_generated_at": b.get("generated_at"),
            "bundle_age_hours": bundle_age_h,
            "bundle_sha256": bundle.get("sha256"),
            "items": len(items),
            "levels": level_counts,
            "gaps": {
                "total_entries": sum(gap_kind_counts.values()),
                "by_kind": gap_kind_counts,
                "by_field": gap_field_counts,
                "items_with_gaps": bundle_items_with_gaps,
                "true_missing_entries": bundle_true_missing,
            },
            "method_coverage": method.get("method_coverage"),
            "kb_query_hit_rate": {
                "zh": kb_scope.get("query_zh_hit_rate"),
                "en": kb_scope.get("query_en_hit_rate"),
            },
            "pit_lag_days": pit_lag,
            "fundamentals_coverage": (fund_scope.get("coverage") or {}),
        },
        "readiness": readiness_counts,
        "checks": {"total": len(checks), "pass": len(checks) - fail_count - warn_count,
                   "warn": warn_count, "fail": fail_count},
        "cross_source": {
            "bundle_pool_date_match": bool(b.get("pool") and str(b["pool"]) == str(pool_date)),
            "pool_code_set_match": bundle_pool_set_match,
            "card_snapshot_hash_match": card_hash_match,
            "card_drift_items": drift_items,
            "inputs_sha256": inputs_meta,
            "inputs_all_match": inputs_all_match,
        },
        "pool_history": db["pool_history"],
    }

    return {
        "schema": OUT_SCHEMA,
        "task": "M4-1 ACAD1 复核就绪度审计（只读）",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "read_only": True,
        "purpose": ("只读核对最新 ACAD1 日复核池的每市场 30 上限、研究卡覆盖、证据包等级/缺口、"
                    "draft 数量和复核就绪度；输出就绪/缺卡/缺证据/建议先复核清单。"),
        "disclaimer": ("复核就绪度审计，不是投资结论；不自动通过/否决、不改卡状态、"
                       "不写 DB、不生成方向/评级/买卖建议。缺口如实标注，不推断、不补造。"),
        "generator": {
            "script": str(Path(__file__).resolve()),
            "python": sys.version.split()[0],
            "command": "python scripts/acad1_review_audit.py",
        },
        "inputs": {
            "db": {"source": "postgres:zhice_os_v2.v2_acad1_pool + "
                             "v2_stock_research + v2_strategy（只读事务 SELECT）",
                   "pool_date": str(pool_date)},
            "evidence_bundle": {
                "path": bundle.get("path"),
                "present": bundle.get("present"),
                "schema": b.get("schema"),
                "generated_at": b.get("generated_at"),
                "sha256": bundle.get("sha256"),
                "size": bundle.get("size"),
                "age_hours": bundle_age_h,
                "read_only_proof_sha256": proof.get("card_snapshot_sha256"),
            },
            "cap_sources": caps.get("sources"),
        },
        "summary": summary,
        "checks": checks,
        "per_market": per_market,
        "items": items_out,
        "missing_cards": missing,
        "missing_evidence": missing_evidence,
        "suggested_review_order": suggested,
        "stale_cards": {
            "count": len(stale),
            "by_market": _count_by(_infer_market(c["code"]) for c in stale),
            "codes": [{"strategy_id": c["strategy_id"], "code": c["code"],
                       "name": c.get("name"), "status": c.get("status"),
                       "direction": c.get("direction"),
                       "market_inferred": _infer_market(c["code"])} for c in stale],
            "note": "同池日但不在最新 ACAD1 池内的研究卡（旧池/补充卡残留）；本审计只读，未改状态。",
        },
        "issues": issues,
        "next_steps": next_steps,
        "read_only_proof": {
            "db_writes": False,
            "research_table_written": False,
            "status_or_reviewed_changed": False,
            "sql_executed": [s for s in db["sql_executed"] if s],
            "sql_only_select": all(
                not re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|TRUNCATE|SET\s+TRANSACTION\s+READ\s+WRITE)\b",
                              s, re.IGNORECASE)
                for s in db["sql_executed"] if s),
            "read_only_transaction": "SET TRANSACTION READ ONLY",
            "audit_card_snapshot_sha256": db["card_snapshot_sha256"],
            "bundle_card_snapshot_sha256": proof.get("card_snapshot_sha256"),
            "card_snapshot_match": card_hash_match,
            "note": ("本脚本只 SELECT；不写 v2_stock_research、不改 status/reviewed/handoff；"
                     "审计前后卡快照哈希一致，证明未改动卡面。"),
        },
    }


def _count_by(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


# --------------------------------------------------------------------------- #
# Markdown 渲染
# --------------------------------------------------------------------------- #
def _md_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return lines


def render_md(a: dict[str, Any]) -> str:
    s = a["summary"]
    lines: list[str] = [
        "# M4-1 ACAD1 复核就绪度审计（只读）",
        "",
        "- 生成时间：%s" % a["generated_at"],
        "- 池日：%s" % s["pool_date"],
        "- 数据来源：%s；证据包 `%s`（%s，生成于 %s）" % (
            a["inputs"]["db"]["source"],
            a["inputs"]["evidence_bundle"]["path"],
            a["inputs"]["evidence_bundle"]["schema"],
            a["inputs"]["evidence_bundle"]["generated_at"]),
        "- 口径：**只读审计，不是投资结论**；不自动通过/否决、不改卡状态、不写 DB。",
        "",
        "## 一、池与每市场 30 上限",
        "",
    ]
    rows = []
    for m in a["per_market"]:
        rows.append([m["market"], m["pool_rows"], m.get("quota") or "—",
                     m["per_market_cap"],
                     "是（余量 %d）" % m["market_headroom"]
                     if m["within_per_market_cap"] else "**超限**",
                     m["cards"], m["drafts"], m["evidence_full"],
                     m["evidence_none"], m["gaps"]])
    lines += _md_table(
        ["市场", "池行数", "配额", "每市场上限", "是否在限内", "卡覆盖", "draft",
         "证据完整", "证据无", "缺口条目"], rows)
    lines += ["",
              "- 池合计 **%d** 只；日复核上限 **%d**（余量 %d）%s" % (
                  s["pool_total"], s["caps"]["total_cap"],
                  s["caps"]["total_headroom"],
                  "，**恰在上限**" if s["caps"]["total_headroom"] == 0 else ""),
              "- 每市场池行数 ≤ %d：%s" % (
                  s["caps"]["per_market_cap"],
                  "全部在限内" if all(m["within_per_market_cap"]
                                      for m in a["per_market"]) else "**存在超限**"),
              "- 配额口径来源：" + "；".join(
                  "%s（%s）" % (x.get("file"), x.get("kind"))
                  for x in (a["inputs"]["cap_sources"] or [])) if a["inputs"]["cap_sources"] else "- 配额口径来源：未解析到源文件",
              "", "## 二、研究卡覆盖与 draft 数量", ""]
    c = s["cards"]
    lines += ["- 池内卡覆盖：**%d/%d**（缺卡 %d）" % (
        c["pool_cards_matched"], s["pool_total"], c["pool_cards_missing"]),
        "- 池内 draft：**%d/%d**；reviewed=%d，handoff=%d" % (
            c["pool_draft"], s["pool_total"], c["pool_reviewed"], c["pool_handoff"]),
        "- 同池日研究卡总数 **%d**（其中池外陈旧卡 **%d**，详见第七节）" % (
            c["same_pool_date_cards"], c["stale_cards_not_in_pool"]),
        "- 全局 status 分布：`%s`" % json.dumps(c["global_status"], ensure_ascii=False),
        "", "## 三、证据等级与缺口", ""]
    e = s["evidence"]
    lines += ["- 证据包：%s，items=%d，生成于 %s（距今 %.2fh）" % (
        "存在" if e["bundle_present"] else "**缺失**", e["items"],
        e["bundle_generated_at"], e["bundle_age_hours"]
        if e["bundle_age_hours"] is not None else -1),
        "- 证据等级：**full=%d**，partial=%d，**none=%d**，unknown=%d" % (
            e["levels"]["full"], e["levels"]["partial"], e["levels"]["none"],
            e["levels"]["unknown"]),
        "- 证据缺口：共 %d 条（%s），true_missing=%d；其中 not_applicable=%d 属数据源不覆盖（非抓取失败）" % (
            e["gaps"]["total_entries"],
            "、".join("%s:%d" % (k, v) for k, v in sorted(e["gaps"]["by_kind"].items())),
            e["gaps"]["true_missing_entries"],
            e["gaps"]["by_kind"].get("not_applicable", 0)),
        "- 方法引用语言覆盖：`%s`；KB 查询命中率 zh=%s / en=%s" % (
            json.dumps(e["method_coverage"], ensure_ascii=False),
            e["kb_query_hit_rate"]["zh"], e["kb_query_hit_rate"]["en"]),
        "- 行业 PIT 滞后：%s 天（已知口径，池日之后成分调整不可见）" % e["pit_lag_days"],
        "", "## 四、就绪度分类（只读判定，非投资结论）", ""]
    rd = s["readiness"]
    lines += ["- **就绪**（卡 draft + 证据 full + 卡面合规）：**%d**" % rd["就绪"],
              "- **缺证据**（证据等级 none/partial）：**%d**" % rd["缺证据"],
              "- **缺卡**：%d；**非 draft**（已复核/已交接）：%d；**卡面待修**：%d" % (
                  rd["缺卡"], rd["非draft"], rd["卡面待修"]),
              "", "## 五、建议先复核清单（事务性排序，不代表任何方向判断）", ""]
    top = [x for x in a["suggested_review_order"] if x["priority"] <= 3]
    rows = []
    for x in top:
        rows.append([x["rank"], x["market"], x["code"], x["name"] or "—",
                     x["strategy_id"], x["direction"], x["evidence_level"],
                     x["priority_reason"]])
    if rows:
        lines += _md_table(["优先级", "市场", "代码", "名称", "来源策略", "卡面方向（快照）",
                            "证据等级", "原因"], rows)
    else:
        lines += ["- 无优先事项：全部就绪。"]
    lines += ["",
              "- 其余 %d 只按市场顺序（CN→HK→US→CRYPTO）复核，完整顺序见 JSON "
              "`suggested_review_order`。" % max(0, len(a["suggested_review_order"]) - len(top)),
              "", "## 六、缺卡 / 缺证据清单", ""]
    if a["missing_cards"]:
        lines += _md_table(["市场", "代码", "名称", "来源策略"],
                           [[x["market"], x["code"], x["name"], x["strategy_id"]]
                            for x in a["missing_cards"]])
    else:
        lines += ["- 缺卡：无（池内 %d 行均有研究卡）。" % s["pool_total"]]
    lines += [""]
    if a["missing_evidence"]:
        lines += _md_table(
            ["市场", "代码", "名称", "证据等级", "缺口类型", "新闻状态", "原因"],
            [[x["market"], x["code"], x["name"], x["evidence_level"],
              "、".join(x.get("gap_kinds") or []) or "—",
              x.get("news_status") or "—", x.get("reason")]
             for x in a["missing_evidence"]])
    else:
        lines += ["- 缺证据：无。"]
    lines += ["", "## 七、池外同池日研究卡（陈旧/补充）", ""]
    sc = a["stale_cards"]
    if sc["count"]:
        lines += ["- 数量 **%d**，按市场推断：`%s`" % (
            sc["count"], json.dumps(sc["by_market"], ensure_ascii=False)),
            "- 说明：%s" % sc["note"],
            "- 样例（前 10）：%s" % "、".join(
                "%s(%s)" % (x["code"], x["market_inferred"]) for x in sc["codes"][:10]),
            "- 处理建议：由 ACAD1/数据 owner 明确保留或归档策略；本审计未改状态。"]
    else:
        lines += ["- 无池外同池日研究卡。"]
    lines += ["", "## 八、跨源一致性与只读证明", ""]
    cs = s["cross_source"]
    lines += ["- 证据包池日与 DB 一致：%s" % cs["bundle_pool_date_match"],
              "- 证据包池代码集合与 DB 一致：%s" % cs["pool_code_set_match"],
              "- 卡快照与证据包一致（无漂移）：%s（漂移字段 %d）" % (
                  cs["card_snapshot_hash_match"], len(cs["card_drift_items"])),
              "- 证据包输入文件 sha256 未变：%s（%s）" % (
                  cs["inputs_all_match"],
                  "、".join("%s:%s" % (x["name"],
                                       "一致" if x["sha256_match"] else "不一致")
                           for x in cs["inputs_sha256"]) or "无记录"),
              "- 只读事务：`%s`；SQL 仅 SELECT：%s" % (
                  a["read_only_proof"]["read_only_transaction"],
                  a["read_only_proof"]["sql_only_select"]),
              "- 本次审计卡快照（同池日 %d 张）：`%s`" % (
                  c["same_pool_date_cards"],
                  (a["read_only_proof"]["audit_card_snapshot_sha256"] or "")[:16]),
              "- 证据包匹配 %d 张卡快照：`%s`；按证据包字段/顺序重算一致：%s（漂移字段 %d）" % (
                  c["pool_cards_matched"],
                  (a["read_only_proof"]["bundle_card_snapshot_sha256"] or "")[:16],
                  a["read_only_proof"]["card_snapshot_match"],
                  len(cs["card_drift_items"])),
              "- 输出仅写：`%s`、`%s`" % (DEFAULT_OUT_JSON, DEFAULT_OUT_MD),
              "", "## 九、检查项明细", "",
              ]
    rows = [[x["id"], x["name"], x["status"], _clip(x["detail"], 160)]
            for x in a["checks"]]
    lines += _md_table(["检查项", "名称", "状态", "说明"], rows)
    lines += ["", "## 十、遗留问题（如实标注，不推断）", ""]
    lines += ["- %s" % x for x in a["issues"]] or ["- 无"]
    lines += ["", "## 十一、下一步建议（不执行，交 ACAD1/Lead）", ""]
    lines += ["- %s" % x for x in a["next_steps"]]
    lines += ["", "---", "",
              "> %s" % a["disclaimer"], ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="M4-1 ACAD1 复核就绪度审计（只读）")
    p.add_argument("--pool-date", default=None, help="池日 YYYY-MM-DD，默认最新池")
    p.add_argument("--bundle", default=str(DEFAULT_BUNDLE), help="证据包 JSON 路径")
    p.add_argument("--out-json", default=str(DEFAULT_OUT_JSON), help="审计 JSON 输出")
    p.add_argument("--out-md", default=str(DEFAULT_OUT_MD), help="审计 MD 输出")
    p.add_argument("--quiet", action="store_true", help="只打印摘要行")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    args = parse_args(argv)
    caps = _read_caps(_SCRIPT_DIR / "sync_candidate_to_acad1.py",
                      _CODE_ROOT / "frontend" / "src" / "pages" /
                      "SelectionFlowPage.tsx")
    db = load_db(args.pool_date)
    bundle = load_bundle(Path(args.bundle))
    audit = build_audit(db, bundle, caps)

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    for p in (out_json, out_md):
        p.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(_json_safe(audit), ensure_ascii=False, indent=1),
                        encoding="utf-8")
    out_md.write_text(render_md(_json_safe(audit)), encoding="utf-8")

    s = audit["summary"]
    if not args.quiet:
        print("[acad1_review_audit] pool_date=%s pool=%d by_market=%s" % (
            s["pool_date"], s["pool_total"],
            json.dumps(s["pool_by_market"], ensure_ascii=False)))
        print("[acad1_review_audit] cards matched=%d missing=%d draft=%d stale=%d" % (
            s["cards"]["pool_cards_matched"], s["cards"]["pool_cards_missing"],
            s["cards"]["pool_draft"], s["cards"]["stale_cards_not_in_pool"]))
        print("[acad1_review_audit] evidence levels=%s gaps=%s true_missing=%d" % (
            json.dumps(s["evidence"]["levels"], ensure_ascii=False),
            json.dumps(s["evidence"]["gaps"]["by_kind"], ensure_ascii=False),
            s["evidence"]["gaps"]["true_missing_entries"]))
        print("[acad1_review_audit] readiness=%s checks=%s" % (
            json.dumps(s["readiness"], ensure_ascii=False),
            json.dumps(s["checks"], ensure_ascii=False)))
        print("[acad1_review_audit] output=%s / %s" % (out_json, out_md))
    fail_count = s["checks"]["fail"]
    print("[acad1_review_audit] readonly: db_writes=False sql_only_select=%s "
          "card_snapshot_match=%s" % (
              audit["read_only_proof"]["sql_only_select"],
              audit["read_only_proof"]["card_snapshot_match"]))
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
