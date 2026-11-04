# -*- coding: utf-8 -*-
"""JEV 复核队列运营视图（只读，task-8 / WO-JEV-OPS-20261103）。

用途：给「复核队列是否在滚动、谁在审、有没有卡住、后验有没有回填」一个
只读的日常运营面。数据源：
  - `news.db::jev_review_queue`（唯一标准库）：pending / resolved / legacy 分层、
    human / AI 复核来源、aging 桶（1/3/7/14d）、stuck（pending 超时）、
    队列对已判读新闻的覆盖率；
  - `jev_audit.db::dec_audit`（可选、只读）：hit 空后验 backlog（78% 未回填现状）、
    按 model 分布、按 trade_day 的积压时效。
**只读**：全部连接 `mode=ro` + `PRAGMA query_only=1`，不写任何 DB；
`--out` 仅写指定的运营报告文件（可选）。

口径：
  - 分层：pending（status=pending，含 legacy 建队的 150 条）/ human（review_type=human）
    / ai（review_type=ai_first_pass）/ legacy_unreviewed（status=unreviewed_legacy）
    / other（其余）；
  - aging：对未解决记录（pending + unreviewed_legacy）按 queued_at 距 now 的天数分桶；
  - stuck：status=pending 且 age > --stuck-days（默认 3 天）——只算待审，不含 legacy 批量；
  - hit 空 backlog：dec_audit 中 hit IS NULL 的记录数，按 trade_day 相对最新交易日分桶。

用法：
  python -X utf8 -B tools/jev_queue_ops.py                 # 人读文本
  python -X utf8 -B tools/jev_queue_ops.py --json          # JSON
  python -X utf8 -B tools/jev_queue_ops.py --md            # Markdown
  python -X utf8 -B tools/jev_queue_ops.py --out C:\\Temp\\jev_queue_ops_20261104.md --md
退出码：0=成功（含 audit 缺失降级）；1=--out 写失败；2=前置错误（库/表/列缺失、
        参数非法、--json 与 --md 同时给）；4=存在坏行且 --fail-on-bad。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(os.environ.get("JEV_NEWS_DB")
                  or (REPO / "news_collector" / "news.db"))
DEFAULT_AUDIT_DB = Path(
    os.environ.get("JEV_AUDIT_DB")
    or (REPO / "agent_platform" / "data" / "jev_audit" / "jev_audit.db"))
KNOWN_STATUSES = ("pending", "resolved", "unreviewed_legacy")
LAYER_ORDER = ("pending", "human", "ai", "legacy_unreviewed", "other")
AGE_BUCKETS = ("<1d", "1-3d", "3-7d", "7-14d", ">=14d")
QUEUE_REQUIRED = ("id", "queued_at", "status")
QUEUE_OPTIONAL = ("conf", "bias", "title", "review_type", "reviewer",
                  "reviewed_at", "decision")
NEWS_REQUIRED = ("id", "jev", "judged_at")
AUDIT_REQUIRED = ("hit", "model", "trade_day")
TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")
NOW_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
               "%Y-%m-%d")


class PreconditionError(RuntimeError):
    """前置条件不满足（缺库/缺表/缺列/参数非法）——fail-visible，rc=2。"""


# --------------------------------------------------------------------------- #
# 只读连接 / schema
# --------------------------------------------------------------------------- #
def ro_connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        "file:%s?mode=ro" % Path(db_path).as_posix(), uri=True, timeout=30)
    conn.execute("PRAGMA query_only=1")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in conn.execute('PRAGMA table_info("%s")' % table)]


def require_queue_schema(conn: sqlite3.Connection, db_path: Path) -> tuple[
        list[str], list[str]]:
    if not table_exists(conn, "news"):
        raise PreconditionError(
            "缺表 news：%s 不是 news.db 或 schema 未初始化（表=%s）"
            % (db_path, [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]))
    if not table_exists(conn, "jev_review_queue"):
        raise PreconditionError(
            "缺表 jev_review_queue：%s 无复核队列表（先经 news_collector/"
            "jev_review_queue.py 建队）" % db_path)
    qcols = table_columns(conn, "jev_review_queue")
    missing = [c for c in QUEUE_REQUIRED if c not in qcols]
    if missing:
        raise PreconditionError(
            "jev_review_queue 缺列 %s（实际列=%s）" % (missing, qcols))
    ncols = table_columns(conn, "news")
    missing = [c for c in NEWS_REQUIRED if c not in ncols]
    if missing:
        raise PreconditionError("news 缺列 %s（实际列=%s）" % (missing, ncols))
    return qcols, ncols


# --------------------------------------------------------------------------- #
# 解析辅助
# --------------------------------------------------------------------------- #
def parse_ts(value) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in TS_FORMATS:
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def parse_now(value: str) -> datetime:
    text = str(value or "").strip()
    for fmt in NOW_FORMATS:
        try:
            return datetime.strptime(text[:19] if fmt != "%Y-%m-%d" else text[:10],
                                     fmt)
        except ValueError:
            continue
    raise PreconditionError(
        "--now 格式非法（应为 YYYY-MM-DD[ HH:MM:SS] 或 YYYY-MM-DDTHH:MM:SS）：%r"
        % value)


def parse_trade_day(value) -> date | None:
    text = str(value or "").strip()
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return None


def bucket_of(age_days: float | None) -> str | None:
    if age_days is None:
        return None
    if age_days < 1:
        return "<1d"
    if age_days < 3:
        return "1-3d"
    if age_days < 7:
        return "3-7d"
    if age_days < 14:
        return "7-14d"
    return ">=14d"


def layer_of(status: str, review_type: str) -> str:
    status = str(status or "").strip()
    review_type = str(review_type or "").strip().lower()
    if status == "pending":
        return "pending"
    if review_type == "human":
        return "human"
    if review_type in ("ai_first_pass", "ai", "ai_pass", "ai-first-pass"):
        return "ai"
    if status == "unreviewed_legacy":
        return "legacy_unreviewed"
    return "other"


def _new_aging() -> dict[str, int]:
    return {b: 0 for b in AGE_BUCKETS}


def _age_days(when: datetime | None, now: datetime) -> float | None:
    if when is None:
        return None
    return max(0.0, (now - when).total_seconds() / 86400.0)


# --------------------------------------------------------------------------- #
# 队列扫描
# --------------------------------------------------------------------------- #
def scan_queue(conn: sqlite3.Connection, db_path: Path, now: datetime,
               stuck_days: float, samples: int) -> dict:
    qcols, ncols = require_queue_schema(conn, Path(db_path))
    select_parts = []
    for name in QUEUE_REQUIRED:
        select_parts.append('"%s" AS "%s"' % (name, name))
    for name in QUEUE_OPTIONAL:
        if name in qcols:
            select_parts.append('"%s" AS "%s"' % (name, name))
        else:
            default = "''" if name in ("title", "review_type") else "NULL"
            select_parts.append('%s AS "%s"' % (default, name))
    sql = "SELECT %s FROM jev_review_queue" % ", ".join(select_parts)

    by_status: dict[str, int] = {}
    by_review_type: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    by_reviewer: dict[str, int] = {}
    layers: dict[str, dict] = {
        name: {"total": 0, "by_status": {}, "resolved": 0}
        for name in LAYER_ORDER}
    unresolved_aging = _new_aging()
    pending_aging = _new_aging()
    layer_aging = {name: _new_aging() for name in LAYER_ORDER}
    bad_invalid_ts: list[dict] = []
    bad_unknown_status: list[dict] = []
    bad_invalid_conf: list[dict] = []
    bad_invalid_ts_n = 0
    bad_unknown_status_n = 0
    bad_invalid_conf_n = 0
    aged_rows = 0
    unparsable_unresolved = 0
    pending_n = 0
    claimed_no_decision = 0
    resolved_without_decision = 0
    resolved_n = 0
    last_refresh_dt: datetime | None = None
    queue_ids: list[str] = []
    for row in conn.execute(sql):
        rec = dict(zip([name for name in QUEUE_REQUIRED]
                       + [name for name in QUEUE_OPTIONAL], row))
        qid = str(rec["id"])
        status = str(rec["status"] or "").strip()
        review_type = str(rec["review_type"] or "").strip()
        decision = str(rec["decision"] or "").strip()
        reviewer = str(rec["reviewer"] or "").strip()
        queue_ids.append(qid)
        by_status[status or "(blank)"] = by_status.get(status or "(blank)", 0) + 1
        by_review_type[review_type or "(blank)"] = (
            by_review_type.get(review_type or "(blank)", 0) + 1)
        by_decision[decision or "(none)"] = (
            by_decision.get(decision or "(none)", 0) + 1)
        if reviewer:
            by_reviewer[reviewer] = by_reviewer.get(reviewer, 0) + 1
        if status not in KNOWN_STATUSES:
            bad_unknown_status_n += 1
            if len(bad_unknown_status) < samples:
                bad_unknown_status.append({"id": qid, "status": status})
        conf = rec.get("conf")
        if conf is not None and not isinstance(conf, (int, float)):
            bad_invalid_conf_n += 1
            if len(bad_invalid_conf) < samples:
                bad_invalid_conf.append({"id": qid, "conf": repr(conf)})
        ts_dt = parse_ts(rec.get("queued_at"))
        if ts_dt is None:
            bad_invalid_ts_n += 1
            if len(bad_invalid_ts) < samples:
                bad_invalid_ts.append({"id": qid,
                                       "queued_at": repr(rec.get("queued_at"))})
        elif last_refresh_dt is None or ts_dt > last_refresh_dt:
            last_refresh_dt = ts_dt
        layer = layer_of(status, review_type)
        layers[layer]["total"] += 1
        layers[layer]["by_status"][status or "(blank)"] = (
            layers[layer]["by_status"].get(status or "(blank)", 0) + 1)
        if status == "pending":
            pending_n += 1
            if reviewer and not decision:
                claimed_no_decision += 1
            age = _age_days(ts_dt, now)
            bucket = bucket_of(age)
            if bucket:
                pending_aging[bucket] += 1
        if status == "resolved":
            resolved_n += 1
            layers[layer]["resolved"] += 1
            if not decision:
                resolved_without_decision += 1
        if status in ("pending", "unreviewed_legacy"):
            age = _age_days(ts_dt, now)
            bucket = bucket_of(age)
            if bucket:
                aged_rows += 1
                unresolved_aging[bucket] += 1
                layer_aging[layer][bucket] += 1
            else:
                unparsable_unresolved += 1

    # stuck：只算 pending（legacy 批量单独看），age > stuck_days
    stuck_samples = []
    stuck_n = 0
    oldest_pending_age = None
    for row in conn.execute(sql):
        rec = dict(zip([name for name in QUEUE_REQUIRED]
                       + [name for name in QUEUE_OPTIONAL], row))
        status = str(rec["status"] or "").strip()
        if status != "pending":
            continue
        age = _age_days(parse_ts(rec.get("queued_at")), now)
        if age is None:
            continue
        if oldest_pending_age is None or age > oldest_pending_age:
            oldest_pending_age = age
        if age > float(stuck_days):
            stuck_n += 1
            if len(stuck_samples) < samples:
                title = str(rec.get("title") or "")[:80]
                stuck_samples.append({
                    "id": str(rec["id"]),
                    "queued_at": str(rec.get("queued_at") or ""),
                    "age_days": round(age, 2),
                    "conf": rec.get("conf"),
                    "bias": str(rec.get("bias") or ""),
                    "title": title,
                })

    # 覆盖率：队列 id 是否都在 news、其中多少是已判读
    news_ids = {str(r[0]) for r in conn.execute("SELECT id FROM news")}
    judged_ids = {str(r[0]) for r in conn.execute(
        "SELECT id FROM news WHERE (jev IS NOT NULL AND trim(jev) <> '') "
        "OR (judged_at IS NOT NULL AND trim(judged_at) <> '')")}
    queue_id_set = set(queue_ids)
    in_news = queue_id_set & news_ids
    judged_queued = queue_id_set & judged_ids
    judged_total = len(judged_ids)
    orphan = queue_id_set - news_ids
    bad_orphan_samples = [{"id": qid} for qid in sorted(orphan)[:samples]]

    last_refresh = None
    if last_refresh_dt is not None:
        last_refresh = {
            "queued_at": last_refresh_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "age_days": round(_age_days(last_refresh_dt, now) or 0.0, 2),
        }
    unresolved_total = int(by_status.get("pending", 0)) + int(
        by_status.get("unreviewed_legacy", 0))
    bad_n = (bad_invalid_ts_n + bad_unknown_status_n + bad_invalid_conf_n
             + len(orphan))
    return {
        "total": len(queue_ids),
        "by_status": dict(sorted(by_status.items())),
        "by_review_type": dict(sorted(by_review_type.items())),
        "by_decision": dict(sorted(by_decision.items())),
        "by_reviewer_top": [
            {"reviewer": k, "n": v}
            for k, v in sorted(by_reviewer.items(),
                               key=lambda kv: (-kv[1], kv[0]))[:10]],
        "layers": layers,
        "last_refresh": last_refresh,
        "pending": {
            "n": pending_n,
            "claimed_no_decision": claimed_no_decision,
            "oldest_age_days": (round(oldest_pending_age, 2)
                                if oldest_pending_age is not None else None),
            "aging": pending_aging,
        },
        "resolved": {
            "n": resolved_n,
            "without_decision": resolved_without_decision,
        },
        "unresolved": {
            "n": unresolved_total,
            "aged_rows": aged_rows,
            "unparsable_queued_at": unparsable_unresolved,
            "aging": unresolved_aging,
            "aging_by_layer": layer_aging,
        },
        "stuck": {
            "threshold_days": float(stuck_days),
            "n": stuck_n,
            "samples": stuck_samples,
        },
        "coverage": {
            "judged": judged_total,
            "queued_in_news": len(in_news),
            "judged_queued": len(judged_queued),
            "coverage": (round(len(judged_queued) / judged_total, 4)
                         if judged_total else None),
            "candidate_backlog": judged_total - len(judged_queued),
        },
        "bad_rows": {
            "n": bad_n,
            "invalid_queued_at": bad_invalid_ts_n,
            "unknown_status": bad_unknown_status_n,
            "invalid_conf": bad_invalid_conf_n,
            "queue_id_not_in_news": len(orphan),
            "samples": {
                "invalid_queued_at": bad_invalid_ts,
                "unknown_status": bad_unknown_status,
                "invalid_conf": bad_invalid_conf,
                "queue_id_not_in_news": bad_orphan_samples,
            },
        },
    }


# --------------------------------------------------------------------------- #
# 后验 hit 空 backlog（只读 dec_audit）
# --------------------------------------------------------------------------- #
def scan_audit(conn: sqlite3.Connection, now: datetime, samples: int) -> dict:
    cols = table_columns(conn, "dec_audit")
    missing = [c for c in AUDIT_REQUIRED if c not in cols]
    if missing:
        raise PreconditionError(
            "dec_audit 缺列 %s（实际列=%s）" % (missing, cols))
    has_posterior = "posterior_at" in cols
    sql = "SELECT hit, model, trade_day%s FROM dec_audit" % (
        ", posterior_at" if has_posterior else "")
    total = labeled = hit1 = hit0 = hit_null = hit_invalid = 0
    posterior_filled = 0
    bad_invalid_hit: list[dict] = []
    bad_trade_day: list[dict] = []
    model_null: dict[str, int] = {}
    day_null: dict[date, int] = {}
    bad_trade_day_n = 0
    max_day: date | None = None
    oldest_day: date | None = None
    for row in conn.execute(sql):
        total += 1
        hit, model, trade_day = row[0], row[1], row[2]
        if has_posterior and row[3] not in (None, ""):
            posterior_filled += 1
        state = _hit_state(hit)
        if state == "hit":
            labeled += 1
            hit1 += 1
        elif state == "miss":
            labeled += 1
            hit0 += 1
        elif state == "null":
            hit_null += 1
            model_key = str(model or "(unknown)")
            model_null[model_key] = model_null.get(model_key, 0) + 1
        else:
            hit_invalid += 1
            if len(bad_invalid_hit) < samples:
                bad_invalid_hit.append({"hit": repr(hit),
                                        "model": str(model or "")})
        day = parse_trade_day(trade_day)
        raw_day = str(trade_day or "").strip()
        if day is None:
            if raw_day:
                bad_trade_day_n += 1
                if len(bad_trade_day) < samples:
                    bad_trade_day.append({"trade_day": repr(trade_day),
                                          "hit": repr(hit)})
        else:
            if max_day is None or day > max_day:
                max_day = day
            if state == "null":
                day_null[day] = day_null.get(day, 0) + 1
                if oldest_day is None or day < oldest_day:
                    oldest_day = day
    backlog_aging = _new_aging()
    if max_day is not None:
        for day, n in day_null.items():
            days = max(0, (max_day - day).days)
            bucket = bucket_of(float(days))
            if bucket:
                backlog_aging[bucket] += n
    by_model = [
        {"model": k, "hit_missing": v,
         "pct_of_missing": (round(v / hit_null, 4) if hit_null else None)}
        for k, v in sorted(model_null.items(),
                           key=lambda kv: (-kv[1], kv[0]))[:10]]
    bad_n = hit_invalid + bad_trade_day_n
    return {
        "available": True,
        "total": total,
        "labeled": labeled,
        "hit_1": hit1,
        "hit_0": hit0,
        "hit_missing": hit_null,
        "hit_invalid": hit_invalid,
        "hit_coverage": (round(labeled / total, 4) if total else None),
        "direction_hit_rate": (round(hit1 / labeled, 4) if labeled else None),
        "hit_missing_pct": (round(hit_null / total, 4) if total else None),
        "posterior_filled": posterior_filled if has_posterior else None,
        "backlog": {
            "n": hit_null,
            "pct": (round(hit_null / total, 4) if total else None),
            "max_trade_day": max_day.strftime("%Y%m%d") if max_day else "",
            "oldest_trade_day": (oldest_day.strftime("%Y%m%d")
                                 if oldest_day else ""),
            "oldest_age_days": (round(float((max_day - oldest_day).days), 1)
                                if (max_day and oldest_day) else None),
            "aging": backlog_aging,
            "by_model_top": by_model,
        },
        "bad_rows": {
            "n": bad_n,
            "invalid_hit": hit_invalid,
            "invalid_trade_day": bad_trade_day_n,
            "samples_invalid_hit": bad_invalid_hit,
            "samples_invalid_trade_day": bad_trade_day,
        },
    }


def _hit_state(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "invalid"
    if isinstance(value, (int, float)):
        if value == 1:
            return "hit"
        if value == 0:
            return "miss"
        return "invalid"
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return "null"
        try:
            num = float(text)
        except ValueError:
            return "invalid"
        if num == 1:
            return "hit"
        if num == 0:
            return "miss"
    return "invalid"


# --------------------------------------------------------------------------- #
# 报告渲染
# --------------------------------------------------------------------------- #
def _fmt_int(n) -> str:
    return "{:,}".format(int(n))


def _fmt_pct(rate) -> str:
    return "-" if rate is None else "%.2f%%" % (rate * 100.0)


def _fmt_aging(aging: dict[str, int]) -> str:
    return " / ".join("%s %s" % (b, _fmt_int(aging.get(b, 0)))
                      for b in AGE_BUCKETS)


def build_report(now: datetime, db_path: Path, audit_path: Path,
                 queue_view: dict, audit_view: dict | None,
                 warnings: list[str]) -> dict:
    return {
        "tool": "jev_queue_ops",
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
        "db": str(db_path),
        "audit_db": str(audit_path) if audit_view else str(audit_path or ""),
        "read_only": True,
        "queue": queue_view,
        "hit_empty": audit_view,
        "warnings": warnings,
    }


def render_text(rep: dict, q: dict, audit: dict | None) -> str:
    c = q["coverage"]
    lines = [
        "JEV 复核队列运营视图（只读）",
        "as_of: %s" % rep["as_of"],
        "news.db: %s" % rep["db"],
    ]
    if q["last_refresh"]:
        lines.append("最近刷新: %s（%.2f 天前；队列自建队后是否滚动看此值）"
                     % (q["last_refresh"]["queued_at"],
                        q["last_refresh"]["age_days"]))
    else:
        lines.append("最近刷新: 无记录（空队列）")
    lines += [
        "队列: 总 %s ｜ 状态 %s"
        % (_fmt_int(q["total"]),
           " / ".join("%s %s" % (k, _fmt_int(v))
                      for k, v in q["by_status"].items()) or "-"),
        "分层: " + " / ".join("%s %s" % (name, _fmt_int(q["layers"][name]["total"]))
                              for name in LAYER_ORDER),
        "复核来源: %s"
        % (" / ".join("%s %s" % (k, _fmt_int(v))
                      for k, v in q["by_review_type"].items()) or "-"),
        "pending: %s ｜ 认领未决(claimed_no_decision): %s ｜ 最老 pending: %s 天"
        % (_fmt_int(q["pending"]["n"]),
           _fmt_int(q["pending"]["claimed_no_decision"]),
           "-" if q["pending"]["oldest_age_days"] is None
           else "%.2f" % q["pending"]["oldest_age_days"]),
        "Aging(未解决 %s 条): %s"
        % (_fmt_int(q["unresolved"]["n"]),
           _fmt_aging(q["unresolved"]["aging"])),
        "Stuck(pending > %s 天): %s 条"
        % (q["stuck"]["threshold_days"], _fmt_int(q["stuck"]["n"])),
        "已判读覆盖: judged %s ｜ 在队 %s ｜ 覆盖率 %s ｜ 待入队 backlog %s"
        % (_fmt_int(c["judged"]), _fmt_int(c["judged_queued"]),
           _fmt_pct(c["coverage"]), _fmt_int(c["candidate_backlog"])),
    ]
    if q["stuck"]["samples"]:
        lines.append("Stuck 样例:")
        for item in q["stuck"]["samples"][:5]:
            lines.append("  id=%s age=%sd conf=%s bias=%s title=%s"
                         % (item["id"], item["age_days"], item["conf"],
                            item["bias"], item["title"]))
    if audit is not None and audit.get("available"):
        b = audit["backlog"]
        lines += [
            "hit-empty backlog: %s / %s（%s）｜ 方向命中率 %s ｜ 最新交易日 %s ｜ 最老 %s（%s 天）"
            % (_fmt_int(b["n"]), _fmt_int(audit["total"]),
               _fmt_pct(b["pct"]), _fmt_pct(audit["direction_hit_rate"]),
               b["max_trade_day"] or "-", b["oldest_trade_day"] or "-",
               "-" if b["oldest_age_days"] is None
               else "%.0f" % b["oldest_age_days"]),
            "hit-empty aging: %s" % _fmt_aging(b["aging"]),
            "hit-empty by model: %s"
            % " / ".join("%s %s" % (m["model"], _fmt_int(m["hit_missing"]))
                         for m in b["by_model_top"][:5]),
        ]
    else:
        lines.append("hit-empty backlog: 未加载（audit db 缺失或 --no-audit）")
    bad = q["bad_rows"]
    lines.append("坏行: %s（非法 queued_at %s / 未知状态 %s / 非法 conf %s / "
                 "队列 id 不在 news %s）"
                 % (_fmt_int(bad["n"]), _fmt_int(bad["invalid_queued_at"]),
                    _fmt_int(bad["unknown_status"]),
                    _fmt_int(bad["invalid_conf"]),
                    _fmt_int(bad["queue_id_not_in_news"])))
    if audit is not None and audit.get("available"):
        lines.append("audit 坏行: %s（非法 hit %s / trade_day 非法 %s）"
                     % (_fmt_int(audit["bad_rows"]["n"]),
                        _fmt_int(audit["bad_rows"]["invalid_hit"]),
                        _fmt_int(audit["bad_rows"]["invalid_trade_day"])))
    for warn in rep["warnings"]:
        lines.append("[WARN] %s" % warn)
    lines.append("模式: 只读（mode=ro + query_only=1），未写任何 DB")
    return "\n".join(lines)


def render_md(rep: dict, q: dict, audit: dict | None) -> str:
    c = q["coverage"]
    lines = [
        "# JEV 复核队列运营视图（只读，%s）" % rep["as_of"],
        "",
        "- news.db：`%s`" % rep["db"],
        "- 生成时间：%s；只读连接（mode=ro + query_only=1）" % rep["generated_at"],
        "",
        "## 一、队列总览",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 队列总行数 | %s |" % _fmt_int(q["total"]),
        "| 状态分布 | %s |" % (" / ".join("%s %s" % (k, _fmt_int(v))
                                          for k, v in q["by_status"].items()) or "-"),
        "| 最近刷新 | %s（%s 天前） |" % (
            q["last_refresh"]["queued_at"] if q["last_refresh"] else "-",
            "%.2f" % q["last_refresh"]["age_days"] if q["last_refresh"] else "-"),
        "| 已判读 | %s |" % _fmt_int(c["judged"]),
        "| 已判读且在队 | %s（覆盖率 %s） |" % (
            _fmt_int(c["judged_queued"]), _fmt_pct(c["coverage"])),
        "| 待入队 backlog | %s |" % _fmt_int(c["candidate_backlog"]),
        "",
        "## 二、复核分层（pending / human / AI / legacy）",
        "",
        "| 层 | 行数 | 状态分布 |",
        "|---|---|---|",
    ]
    for name in LAYER_ORDER:
        layer = q["layers"][name]
        lines.append("| `%s` | %s | %s |" % (
            name, _fmt_int(layer["total"]),
            " / ".join("%s %s" % (k, _fmt_int(v))
                       for k, v in layer["by_status"].items()) or "-"))
    lines += [
        "",
        "复核来源：%s" % (" / ".join("%s %s" % (k, _fmt_int(v))
                                    for k, v in q["by_review_type"].items()) or "-"),
        "",
        "## 三、Aging / Stuck",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 未解决行数 | %s |" % _fmt_int(q["unresolved"]["n"]),
        "| aging 分桶（<1d / 1-3d / 3-7d / 7-14d / >=14d） | %s |"
        % _fmt_aging(q["unresolved"]["aging"]),
        "| pending 数 | %s |" % _fmt_int(q["pending"]["n"]),
        "| pending aging | %s |" % _fmt_aging(q["pending"]["aging"]),
        "| 认领未决（claimed_no_decision） | %s |"
        % _fmt_int(q["pending"]["claimed_no_decision"]),
        "| stuck（pending > %s 天） | %s |"
        % (q["stuck"]["threshold_days"], _fmt_int(q["stuck"]["n"])),
        "| 最老 pending | %s 天 |" % (
            "-" if q["pending"]["oldest_age_days"] is None
            else "%.2f" % q["pending"]["oldest_age_days"]),
        "",
    ]
    if q["stuck"]["samples"]:
        lines += ["### Stuck 样例（前 %d）" % len(q["stuck"]["samples"]), ""]
        for item in q["stuck"]["samples"]:
            lines.append("- `%s` age=%.2fd conf=%s bias=%s %s"
                         % (item["id"], item["age_days"], item["conf"],
                            item["bias"], item["title"]))
        lines.append("")
    lines += ["## 四、hit 空后验 backlog（dec_audit，只读）", ""]
    if audit is not None and audit.get("available"):
        b = audit["backlog"]
        lines += [
            "| 指标 | 值 |",
            "|---|---|",
            "| dec_audit 总行数 | %s |" % _fmt_int(audit["total"]),
            "| hit 0/1（可用） | %s |" % _fmt_int(audit["labeled"]),
            "| hit 空 backlog | %s（%s） |" % (
                _fmt_int(b["n"]), _fmt_pct(b["pct"])),
            "| 方向命中率（hit 可用样本） | %s |"
            % _fmt_pct(audit["direction_hit_rate"]),
            "| posterior_at 已回填 | %s |" % (
                "-" if audit["posterior_filled"] is None
                else _fmt_int(audit["posterior_filled"])),
            "| 最新 tick 交易日 | %s |" % (b["max_trade_day"] or "-"),
            "| 最老 hit 空 trade_day | %s（%s 天） |" % (
                b["oldest_trade_day"] or "-",
                "-" if b["oldest_age_days"] is None
                else "%.0f" % b["oldest_age_days"]),
            "| backlog aging | %s |" % _fmt_aging(b["aging"]),
            "",
            "| model | hit 空 | 占 hit 空比例 |",
            "|---|---|---|",
        ]
        for item in b["by_model_top"]:
            lines.append("| `%s` | %s | %s |" % (
                item["model"], _fmt_int(item["hit_missing"]),
                _fmt_pct(item["pct_of_missing"])))
        lines.append("")
    else:
        lines.append("- 未加载（audit db 缺失或 `--no-audit`）")
        lines.append("")
    bad = q["bad_rows"]
    lines += [
        "## 五、坏行与数据质量（fail-visible）",
        "",
        "| 项 | 数量 |",
        "|---|---|",
        "| 非法 queued_at | %s |" % _fmt_int(bad["invalid_queued_at"]),
        "| 未知 status | %s |" % _fmt_int(bad["unknown_status"]),
        "| 非法 conf | %s |" % _fmt_int(bad["invalid_conf"]),
        "| 队列 id 不在 news | %s |" % _fmt_int(bad["queue_id_not_in_news"]),
        "| 合计 | %s |" % _fmt_int(bad["n"]),
    ]
    if audit is not None and audit.get("available"):
        lines.append("| audit 坏行（非法 hit / trade_day 非法） | %s |"
                     % _fmt_int(audit["bad_rows"]["n"]))
    lines += ["", "## 六、备注", ""]
    for warn in rep["warnings"]:
        lines.append("- [WARN] %s" % warn)
    lines += [
        "- 口径：pending = status=pending；human/ai = review_type；"
        "legacy = unreviewed_legacy；aging 基于 queued_at 距 as_of 天数；",
        "- stuck 只统计 pending（不含 10,275 条 legacy 批量债务，后者单列）；",
        "- hit 空 backlog = `dec_audit.hit IS NULL`（后验未回填，不是“未命中”）；",
        "- 本报告只读，不写任何 DB。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #
def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 非 TTY / 旧解释器忽略
        pass
    ap = argparse.ArgumentParser(
        description="JEV 复核队列运营视图（只读；支持 --json/--md）")
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help="news.db 路径（默认 %s）" % DEFAULT_DB)
    ap.add_argument("--audit-db", default=str(DEFAULT_AUDIT_DB),
                    help="jev_audit.db 路径（默认 %s）" % DEFAULT_AUDIT_DB)
    ap.add_argument("--no-audit", action="store_true",
                    help="不读 jev_audit.db（hit-empty backlog 置为未加载）")
    ap.add_argument("--require-audit", action="store_true",
                    help="audit db 缺失/不可读时 rc=2（默认降级为 warning）")
    ap.add_argument("--now", default="",
                    help="as-of 时间（YYYY-MM-DD HH:MM:SS；默认本机当前时间）")
    ap.add_argument("--stuck-days", type=float, default=3.0,
                    help="pending 超时阈值天数（默认 3）")
    ap.add_argument("--samples", type=int, default=10,
                    help="样例条数（默认 10，上限 50）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--md", action="store_true", help="输出 Markdown")
    ap.add_argument("--out", default="", help="把所选格式报告写入文件（原子写）")
    ap.add_argument("--fail-on-bad", action="store_true",
                    help="存在坏行时 rc=4")
    a = ap.parse_args(argv)

    if a.json and a.md:
        print("[FAIL] --json 与 --md 只能二选一", file=sys.stderr)
        return 2
    if a.samples < 0 or a.samples > 50:
        print("[FAIL] --samples 取值 0~50", file=sys.stderr)
        return 2
    try:
        now = parse_now(a.now) if a.now else datetime.now()
    except PreconditionError as exc:
        print("[FAIL] %s" % exc, file=sys.stderr)
        return 2
    if a.stuck_days < 0:
        print("[FAIL] --stuck-days 不能为负", file=sys.stderr)
        return 2
    db_path = Path(a.db)
    if not db_path.is_file():
        print("[FAIL] DB 不存在：%s" % db_path, file=sys.stderr)
        return 2

    warnings: list[str] = []
    try:
        conn = ro_connect(db_path)
        try:
            queue_view = scan_queue(conn, db_path, now, a.stuck_days, a.samples)
        finally:
            conn.close()
    except PreconditionError as exc:
        print("[FAIL] %s" % exc, file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print("[FAIL] news.db 只读查询失败：%s" % exc, file=sys.stderr)
        return 2

    audit_path = Path(a.audit_db)
    audit_view = None
    if not a.no_audit:
        if not audit_path.is_file():
            msg = "audit db 不存在，hit-empty backlog 未加载：%s" % audit_path
            if a.require_audit:
                print("[FAIL] %s（--require-audit）" % msg, file=sys.stderr)
                return 2
            warnings.append(msg)
        else:
            try:
                acon = ro_connect(audit_path)
                try:
                    audit_view = scan_audit(acon, now, a.samples)
                finally:
                    acon.close()
            except PreconditionError as exc:
                if a.require_audit:
                    print("[FAIL] %s（--require-audit）" % exc, file=sys.stderr)
                    return 2
                warnings.append("audit db 不可用，hit-empty backlog 未加载：%s"
                                % exc)
                audit_view = None
            except sqlite3.Error as exc:
                if a.require_audit:
                    print("[FAIL] audit db 只读查询失败：%s（--require-audit）"
                          % exc, file=sys.stderr)
                    return 2
                warnings.append("audit db 查询失败，hit-empty backlog 未加载：%s"
                                % exc)
                audit_view = None

    rep = build_report(now, db_path, audit_path, queue_view, audit_view,
                       warnings)
    rep["bad_rows_total"] = queue_view["bad_rows"]["n"] + (
        audit_view["bad_rows"]["n"] if audit_view else 0)
    if a.json:
        text = json.dumps(rep, ensure_ascii=False, indent=2) + "\n"
    elif a.md:
        text = render_md(rep, queue_view, audit_view) + "\n"
    else:
        text = render_text(rep, queue_view, audit_view) + "\n"
    if a.out:
        try:
            _atomic_write(Path(a.out), text)
        except OSError as exc:
            print("[FAIL] --out 写入失败：%s" % exc, file=sys.stderr)
            return 1
    sys.stdout.write(text)
    if a.fail_on_bad and rep["bad_rows_total"] > 0:
        print("[FAIL] 存在 %d 行坏行，--fail-on-bad 置 rc=4"
              % rep["bad_rows_total"], file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
