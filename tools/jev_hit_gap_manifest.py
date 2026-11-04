# -*- coding: utf-8 -*-
"""JEV hit 空后验缺口优先清单（只读 worklist，task-11 / WO-JEV-OPS-20261103）。

背景（2026-11-04 实测）：`dec_audit` 116,735 行中 `hit` 为 NULL 91,715 行
（78.57%），最老 trade_day 20260914（51 天）——后验未回填，校准/门控长期缺样本。
本工具从 `jev_audit.db::dec_audit` + `news.db` **只读**生成可执行的补后验
worklist：按 trade_day / model / 方向(bias) / 置信度(conf) / 陈旧度 分组，
带 provenance（news_id / trade_day / model / source / title），供后续回填排期。

优先级口径（可复现、可解释）：
  - 以库内 max(trade_day) 为“最新交易日”基准，age_days = max_day - trade_day；
  - 陈旧度桶 `>=14d / 7-14d / 3-7d / 1-3d / <1d` 对应 `P0/P1/P2/P3/P4`；
  - worklist 先 P0 后 P4；同档按组内样本量降序、trade_day 升序——
    「积压最久 + 量最大」先补；`<1d` 可能尚未到 T+1 回填窗口，排最后；
  - `--min-age-days` 可只看 P0 等阈值以上；`--limit` 截断 worklist 条数。
  - 注意：**实际回填要写 `dec_audit`（生产库写入）= L2/另单**，本工具只出清单，
    不执行任何写库动作（JSON `fill_route` 字段明确标注）。

用法：
  python -X utf8 -B tools/jev_hit_gap_manifest.py                 # 人读文本
  python -X utf8 -B tools/jev_hit_gap_manifest.py --json
  python -X utf8 -B tools/jev_hit_gap_manifest.py --md --out outputs/hit_gap.md
  python -X utf8 -B tools/jev_hit_gap_manifest.py --min-age-days 14 --limit 10
退出码：0=成功（provenance 缺失降级）；1=--out 写失败；2=前置错误（库/表/列缺失、
        参数非法、--json/--md 同给、--require-provenance 但 news.db 不可用）；
        4=存在坏行且 --fail-on-bad。
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
DEFAULT_AUDIT_DB = Path(
    os.environ.get("JEV_AUDIT_DB")
    or (REPO / "agent_platform" / "data" / "jev_audit" / "jev_audit.db"))
DEFAULT_NEWS_DB = Path(
    os.environ.get("JEV_NEWS_DB")
    or (REPO / "news_collector" / "news.db"))
AGE_BUCKETS = ("<1d", "1-3d", "3-7d", "7-14d", ">=14d")
TIER_OF = {"<1d": "P4", "1-3d": "P3", "3-7d": "P2", "7-14d": "P1", ">=14d": "P0"}
TIER_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}
CONF_BUCKETS = ("<0.5", "0.5-0.6", "0.6-0.7", "0.7-0.8", "0.8-0.9", ">=0.9")
AUDIT_REQUIRED = ("news_id", "hit", "trade_day", "model", "bias", "conf",
                  "source")
NEWS_REQUIRED = ("id",)
NEWS_OPTIONAL = ("source", "ts", "judged_at", "title")
CHUNK = 400


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


def require_audit_schema(conn: sqlite3.Connection, db_path: Path) -> list[str]:
    if not table_exists(conn, "dec_audit"):
        raise PreconditionError(
            "缺表 dec_audit：%s 不是 jev_audit.db 或 schema 未初始化（表=%s）"
            % (db_path, [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]))
    cols = table_columns(conn, "dec_audit")
    missing = [c for c in AUDIT_REQUIRED if c not in cols]
    if missing:
        raise PreconditionError(
            "dec_audit 缺列 %s（实际列=%s）" % (missing, cols))
    return cols


def require_news_schema(conn: sqlite3.Connection, db_path: Path) -> list[str]:
    if not table_exists(conn, "news"):
        raise PreconditionError(
            "缺表 news：%s 不是 news.db 或 schema 未初始化" % db_path)
    cols = table_columns(conn, "news")
    missing = [c for c in NEWS_REQUIRED if c not in cols]
    if missing:
        raise PreconditionError("news 缺列 %s（实际列=%s）" % (missing, cols))
    return cols


# --------------------------------------------------------------------------- #
# 解析辅助
# --------------------------------------------------------------------------- #
def parse_trade_day(value) -> date | None:
    text = str(value or "").strip()
    if len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return None


def bucket_of(age_days: float) -> str:
    if age_days < 1:
        return "<1d"
    if age_days < 3:
        return "1-3d"
    if age_days < 7:
        return "3-7d"
    if age_days < 14:
        return "7-14d"
    return ">=14d"


def conf_bucket(value) -> tuple[str, str]:
    """返回 (bucket, err)；err 非空 = conf 非法（坏行），bucket='missing'。"""
    if value is None:
        return "missing", ""
    if isinstance(value, bool):
        return "missing", "invalid"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "missing", "invalid"
    if num < 0.5:
        return "<0.5", ""
    if num < 0.6:
        return "0.5-0.6", ""
    if num < 0.7:
        return "0.6-0.7", ""
    if num < 0.8:
        return "0.7-0.8", ""
    if num < 0.9:
        return "0.8-0.9", ""
    return ">=0.9", ""


def norm_key(value, fallback: str = "(unknown)") -> str:
    text = str(value or "").strip()
    return text if text else fallback


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


def _new_counter() -> dict:
    return {b: 0 for b in AGE_BUCKETS}


# --------------------------------------------------------------------------- #
# dec_audit 扫描 + 分组
# --------------------------------------------------------------------------- #
def scan_audit(conn: sqlite3.Connection, cols: list[str],
               sample_limit: int) -> dict:
    select = ['"news_id" AS news_id', '"hit" AS hit',
              '"trade_day" AS trade_day', '"model" AS model',
              '"bias" AS bias', '"conf" AS conf', '"source" AS source']
    field_names = ["news_id", "hit", "trade_day", "model", "bias", "conf",
                   "source"]
    if "ts" in cols:
        select.append('"ts" AS ts')
        field_names.append("ts")
    sql = "SELECT %s FROM dec_audit" % ", ".join(select)
    has_ts = "ts" in cols

    total = labeled = hit_null = hit_invalid = 0
    bad_invalid_hit: list[dict] = []
    bad_invalid_day: list[dict] = []
    bad_invalid_conf: list[dict] = []
    bad_invalid_hit_n = bad_invalid_day_n = bad_invalid_conf_n = 0
    rows: list[dict] = []
    max_day: date | None = None
    for row in conn.execute(sql):
        rec = dict(zip(field_names, row))
        total += 1
        news_id = str(rec.get("news_id") or "")
        state = _hit_state(rec.get("hit"))
        day = parse_trade_day(rec.get("trade_day"))
        if day is not None and (max_day is None or day > max_day):
            max_day = day
        if state == "null":
            hit_null += 1
            bucket, conf_err = conf_bucket(rec.get("conf"))
            if conf_err:
                bad_invalid_conf_n += 1
                if len(bad_invalid_conf) < sample_limit:
                    bad_invalid_conf.append({"news_id": news_id,
                                             "conf": repr(rec.get("conf"))})
            rows.append({
                "news_id": news_id,
                "trade_day_raw": str(rec.get("trade_day") or ""),
                "day": day,
                "model": norm_key(rec.get("model")),
                "bias": norm_key(rec.get("bias")),
                "conf": rec.get("conf"),
                "conf_bucket": bucket,
                "source": norm_key(rec.get("source"), "(blank)"),
                "ts": str(rec.get("ts") or "") if has_ts else "",
            })
        elif state in ("hit", "miss"):
            labeled += 1
        else:
            hit_invalid += 1
            bad_invalid_hit_n += 1
            if len(bad_invalid_hit) < sample_limit:
                bad_invalid_hit.append({"news_id": news_id,
                                        "hit": repr(rec.get("hit"))})
    # 二次遍历：invalid trade_day 的 hit 空行无法入桶 → 坏行可见
    for item in rows:
        if item["day"] is None:
            bad_invalid_day_n += 1
            if len(bad_invalid_day) < sample_limit:
                bad_invalid_day.append({
                    "news_id": item["news_id"],
                    "trade_day": repr(item["trade_day_raw"]),
                    "model": item["model"]})
    valid_rows = [r for r in rows if r["day"] is not None and max_day is not None]
    for item in valid_rows:
        age = float((max_day - item["day"]).days)
        item["age_days"] = age
        item["staleness_bucket"] = bucket_of(age)
        item["tier"] = TIER_OF[item["staleness_bucket"]]

    trade_groups: dict[str, dict] = {}
    model_diag: dict[str, dict] = {}
    bias_diag: dict[str, dict] = {}
    conf_diag: dict[str, dict] = {b: {"n": 0, "models": {}, "biases": {}}
                                  for b in CONF_BUCKETS}
    conf_diag["missing"] = {"n": 0, "models": {}, "biases": {}}
    stale_diag: dict[str, dict] = {b: {"n": 0, "models": {}, "biases": {},
                                       "conf": {}, "trade_days": set()}
                                   for b in AGE_BUCKETS}
    all_ids: list[str] = []
    for item in valid_rows:
        td = item["trade_day_raw"][:8]
        g = trade_groups.setdefault(td, {
            "trade_day": td, "age_days": int(item["age_days"]),
            "staleness_bucket": item["staleness_bucket"],
            "tier": item["tier"], "n": 0, "models": {}, "biases": {},
            "conf": {}, "sources": {}, "news_id_samples": [],
        })
        g["n"] += 1
        g["models"][item["model"]] = g["models"].get(item["model"], 0) + 1
        g["biases"][item["bias"]] = g["biases"].get(item["bias"], 0) + 1
        g["conf"][item["conf_bucket"]] = (
            g["conf"].get(item["conf_bucket"], 0) + 1)
        g["sources"][item["source"]] = g["sources"].get(item["source"], 0) + 1
        if len(g["news_id_samples"]) < sample_limit:
            g["news_id_samples"].append(item["news_id"])
        all_ids.append(item["news_id"])
        _bump(model_diag, item["model"], item)
        _bump(bias_diag, item["bias"], item)
        conf_diag[item["conf_bucket"]]["n"] += 1
        _inc(conf_diag[item["conf_bucket"]]["models"], item["model"])
        _inc(conf_diag[item["conf_bucket"]]["biases"], item["bias"])
        st = stale_diag[item["staleness_bucket"]]
        st["n"] += 1
        _inc(st["models"], item["model"])
        _inc(st["biases"], item["bias"])
        _inc(st["conf"], item["conf_bucket"])
        st["trade_days"].add(td)

    for g in trade_groups.values():
        g["conf_high_share"] = round(
            sum(v for k, v in g["conf"].items() if k in ("0.8-0.9", ">=0.9"))
            / g["n"], 4) if g["n"] else None
        g["sources"] = _top_dict(g["sources"], 10)
    by_trade_day = sorted(
        trade_groups.values(),
        key=lambda g: (TIER_RANK[g["tier"]], -g["n"], g["trade_day"]))
    model_diag_out = [_finish_diag(k, v) for k, v in
                      sorted(model_diag.items(),
                             key=lambda kv: (-kv[1]["n"], kv[0]))]
    bias_diag_out = [_finish_diag(k, v) for k, v in
                     sorted(bias_diag.items(),
                            key=lambda kv: (-kv[1]["n"], kv[0]))]
    conf_diag_out = [
        {"bucket": k, "n": v["n"], "models": _top_dict(v["models"], 10),
         "biases": _top_dict(v["biases"], 10)}
        for k, v in conf_diag.items()]
    stale_diag_out = [
        {"staleness_bucket": k, "tier": TIER_OF[k], "n": v["n"],
         "models": _top_dict(v["models"], 10), "biases": _top_dict(v["biases"], 10),
         "conf": v["conf"], "trade_day_count": len(v["trade_days"]),
         "oldest_trade_day": min(v["trade_days"]) if v["trade_days"] else "",
         "newest_trade_day": max(v["trade_days"]) if v["trade_days"] else ""}
        for k, v in stale_diag.items()]
    oldest_day = min((r["day"] for r in valid_rows), default=None)
    return {
        "total": total,
        "hit_null": hit_null,
        "hit_labeled": labeled,
        "hit_invalid": hit_invalid,
        "hit_null_pct": (round(hit_null / total, 4) if total else None),
        "hit_coverage": (round(labeled / total, 4) if total else None),
        "distinct_news_ids": len(set(all_ids)),
        "max_trade_day": max_day.strftime("%Y%m%d") if max_day else "",
        "oldest_trade_day": oldest_day.strftime("%Y%m%d") if oldest_day else "",
        "oldest_age_days": ((max_day - oldest_day).days
                            if (max_day and oldest_day) else None),
        "dimensions": {
            "by_trade_day": by_trade_day,
            "by_model": model_diag_out,
            "by_bias": bias_diag_out,
            "by_conf": conf_diag_out,
            "by_staleness": stale_diag_out,
        },
        "bad_rows": {
            "n": (bad_invalid_hit_n + bad_invalid_day_n + bad_invalid_conf_n),
            "invalid_hit": bad_invalid_hit_n,
            "invalid_trade_day": bad_invalid_day_n,
            "invalid_conf": bad_invalid_conf_n,
            "samples": {
                "invalid_hit": bad_invalid_hit,
                "invalid_trade_day": bad_invalid_day,
                "invalid_conf": bad_invalid_conf,
            },
        },
    }


def _inc(counter: dict, key: str) -> None:
    counter[key] = counter.get(key, 0) + 1


def _bump(diag: dict, key: str, item: dict) -> None:
    rec = diag.setdefault(key, {"n": 0, "age": _new_counter(),
                                "trade_days": set(), "conf": {},
                                "biases": {}, "models": {}})
    rec["n"] += 1
    rec["age"][item["staleness_bucket"]] += 1
    rec["trade_days"].add(item["trade_day_raw"][:8])
    _inc(rec["conf"], item["conf_bucket"])
    _inc(rec["biases"], item["bias"])
    _inc(rec["models"], item["model"])


def _finish_diag(key: str, rec: dict) -> dict:
    days = sorted(rec["trade_days"])
    return {
        "name": key,
        "n": rec["n"],
        "age": rec["age"],
        "trade_day_count": len(days),
        "oldest_trade_day": days[0] if days else "",
        "newest_trade_day": days[-1] if days else "",
        "conf": _top_dict(rec["conf"], 10),
        "biases": _top_dict(rec["biases"], 10),
    }


def _top_dict(counter: dict, top: int) -> dict:
    return {k: v for k, v in sorted(counter.items(),
                                    key=lambda kv: (-kv[1], kv[0]))[:top]}


# --------------------------------------------------------------------------- #
# provenance（news.db 只读 join）
# --------------------------------------------------------------------------- #
def news_select(cols: list[str]) -> str:
    parts = ["id"]
    for name in NEWS_OPTIONAL:
        parts.append('"%s"' % name if name in cols else "NULL AS %s" % name)
    return "SELECT %s FROM news" % ", ".join(parts)


def load_provenance(news_db: Path, all_ids: list[str], sample_ids: list[str],
                    sample_limit: int) -> dict:
    """chunked 只读 check：全局覆盖率 + 指定样例的 provenance 明细。"""
    conn = ro_connect(news_db)
    try:
        cols = require_news_schema(conn, news_db)
        sql = news_select(cols)
        checked = len(all_ids)
        found_n = 0
        missing_n = 0
        missing_samples: list[str] = []
        seen = set()
        for start in range(0, checked, CHUNK):
            chunk = all_ids[start:start + CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                "SELECT id FROM news WHERE id IN (%s)" % placeholders,
                chunk).fetchall()
            found = {str(r[0]) for r in rows}
            found_n += len(found)
            if len(found) < len(chunk):
                for nid in chunk:
                    if nid not in found:
                        missing_n += 1
                        if len(missing_samples) < sample_limit:
                            missing_samples.append(nid)
            seen |= found
        details = {}
        wanted = [x for x in dict.fromkeys(sample_ids) if x in seen]
        for start in range(0, len(wanted), CHUNK):
            part = wanted[start:start + CHUNK]
            placeholders = ",".join("?" * len(part))
            for row in conn.execute(
                    "%s WHERE id IN (%s)" % (sql, placeholders), part):
                details[str(row[0])] = {
                    "news_id": str(row[0]),
                    "source": str(row[1] or ""),
                    "ts": str(row[2] or ""),
                    "judged_at": str(row[3] or ""),
                    "title": str(row[4] or "")[:100],
                }
        return {
            "available": True,
            "checked_ids": checked,
            "found_ids": found_n,
            "missing_ids": missing_n,
            "coverage": (round(found_n / checked, 4) if checked else 1.0),
            "missing_samples": missing_samples,
            "details": details,
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def _fmt_int(n) -> str:
    return "{:,}".format(int(n))


def _fmt_pct(rate) -> str:
    return "-" if rate is None else "%.2f%%" % (rate * 100.0)


def build_manifest(audit_view: dict, provenance: dict | None,
                   limit: int, min_age_days: float,
                   sample_limit: int) -> dict:
    worklist = []
    for group in audit_view["dimensions"]["by_trade_day"]:
        if group["age_days"] < min_age_days:
            continue
        worklist.append(group)
    worklist = worklist[:limit]
    entries = []
    for rank, group in enumerate(worklist, start=1):
        sample_ids = group["news_id_samples"][:sample_limit]
        prov_sample = []
        if provenance:
            for nid in sample_ids:
                item = provenance["details"].get(nid)
                if item:
                    prov_sample.append(item)
        entries.append({
            "rank": rank,
            "priority": group["tier"],
            "staleness_bucket": group["staleness_bucket"],
            "trade_day": group["trade_day"],
            "age_days": group["age_days"],
            "n": group["n"],
            "models": group["models"],
            "biases": group["biases"],
            "conf": group["conf"],
            "conf_high_share": group["conf_high_share"],
            "sources": group["sources"],
            "news_id_samples": sample_ids,
            "provenance_sample": prov_sample,
        })
    bad = audit_view["bad_rows"]
    missing = provenance["missing_ids"] if provenance else 0
    return {
        "tool": "jev_hit_gap_manifest",
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "read_only": True,
        "criteria": {
            "reference": "库内 max(trade_day) 为最新交易日基准",
            "age_days": "max_trade_day - trade_day（自然日）",
            "staleness_buckets": list(AGE_BUCKETS),
            "tiers": {b: TIER_OF[b] for b in AGE_BUCKETS},
            "order": "tier(P0→P4) → 样本量降序 → trade_day 升序",
            "limit": limit,
            "min_age_days": min_age_days,
            "conf_buckets": list(CONF_BUCKETS) + ["missing"],
        },
        "reference": {
            "max_trade_day": audit_view["max_trade_day"],
            "oldest_trade_day": audit_view["oldest_trade_day"],
            "oldest_age_days": audit_view["oldest_age_days"],
        },
        "overall": {
            "dec_audit_total": audit_view["total"],
            "hit_null": audit_view["hit_null"],
            "hit_labeled": audit_view["hit_labeled"],
            "hit_invalid": audit_view["hit_invalid"],
            "hit_null_pct": audit_view["hit_null_pct"],
            "hit_coverage": audit_view["hit_coverage"],
            "distinct_news_ids": audit_view["distinct_news_ids"],
        },
        "provenance": {
            "available": bool(provenance),
            "checked_ids": provenance["checked_ids"] if provenance else 0,
            "found_ids": provenance["found_ids"] if provenance else 0,
            "missing_ids": missing,
            "coverage": provenance["coverage"] if provenance else None,
            "missing_samples": provenance["missing_samples"] if provenance else [],
        },
        "dimensions": audit_view["dimensions"],
        "priority_worklist": entries,
        "bad_rows": {
            "n": bad["n"] + missing,
            "invalid_hit": bad["invalid_hit"],
            "invalid_trade_day": bad["invalid_trade_day"],
            "invalid_conf": bad["invalid_conf"],
            "provenance_missing_ids": missing,
            "samples": bad["samples"],
        },
        "fill_route": {
            "writes_db": True,
            "l2_required": True,
            "executed": False,
            "note": "实际回填需写 dec_audit（生产库写入）→ L2/另单；本清单只读，未执行任何写库",
            "suggested_commands": [
                "python -X utf8 -B tools/jev_audit.py --days 45   # 触发 T+1 后验回填（L2/另单批准后执行）",
                "python -X utf8 -B tools/jev_hit_gap_manifest.py --min-age-days 14 --limit 10  # 回填后复跑核对",
            ],
        },
        "warnings": [],
    }


def render_text(rep: dict) -> str:
    o = rep["overall"]
    r = rep["reference"]
    lines = [
        "JEV hit 空后验缺口优先清单（只读 worklist）",
        "dec_audit: %s" % rep.get("audit_db", ""),
        "news.db: %s" % rep.get("news_db", ""),
        "总行数 %s ｜ hit 空 %s（%s）｜ hit 可用 %s ｜ 最新 trade_day %s ｜ 最老 %s（%s 天）"
        % (_fmt_int(o["dec_audit_total"]), _fmt_int(o["hit_null"]),
           _fmt_pct(o["hit_null_pct"]), _fmt_int(o["hit_labeled"]),
           r["max_trade_day"] or "-", r["oldest_trade_day"] or "-",
           "-" if r["oldest_age_days"] is None else r["oldest_age_days"]),
        "provenance: %s"
        % ("可用，%s/%s（%s）" % (
            _fmt_int(rep["provenance"]["found_ids"]),
            _fmt_int(rep["provenance"]["checked_ids"]),
            _fmt_pct(rep["provenance"]["coverage"]))
           if rep["provenance"]["available"] else "不可用（news.db 缺失/--no-provenance）"),
        "优先级: %s" % rep["criteria"]["order"],
    ]
    for entry in rep["priority_worklist"]:
        lines.append(
            "  #%d %s %s age=%sd n=%s conf>=0.8占%s | models=%s | bias=%s"
            % (entry["rank"], entry["priority"], entry["trade_day"],
               entry["age_days"], _fmt_int(entry["n"]),
               _fmt_pct(entry["conf_high_share"]),
               ",".join("%s:%s" % (k, _fmt_int(v))
                        for k, v in entry["models"].items()),
               ",".join("%s:%s" % (k, _fmt_int(v))
                        for k, v in entry["biases"].items())))
    bad = rep["bad_rows"]
    lines.append("坏行: %s（非法 hit %s / trade_day 非法 %s / conf 非法 %s / provenance 缺失 id %s）"
                 % (_fmt_int(bad["n"]), _fmt_int(bad["invalid_hit"]),
                    _fmt_int(bad["invalid_trade_day"]),
                    _fmt_int(bad["invalid_conf"]),
                    _fmt_int(bad["provenance_missing_ids"])))
    lines.append("回填路线: %s（本清单未执行）" % rep["fill_route"]["note"])
    for warn in rep["warnings"]:
        lines.append("[WARN] %s" % warn)
    lines.append("模式: 只读（mode=ro + query_only=1），未写任何 DB")
    return "\n".join(lines)


def render_md(rep: dict) -> str:
    o = rep["overall"]
    r = rep["reference"]
    dims = rep["dimensions"]
    lines = [
        "# JEV hit 空后验缺口优先清单（只读 worklist，%s）" % rep["generated_at"],
        "",
        "- audit_db：`%s`" % rep.get("audit_db", ""),
        "- news_db：`%s`" % rep.get("news_db", ""),
        "- 只读连接（`mode=ro`+`query_only=1`）；**未写任何 DB；实际回填属 L2/另单**",
        "- 生成时间：%s ｜ 基准交易日：%s" % (rep["generated_at"],
                                       r["max_trade_day"] or "-"),
        "",
        "## 一、缺口总览",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| dec_audit 总行数 | %s |" % _fmt_int(o["dec_audit_total"]),
        "| hit 空（后验未回填） | %s（%s） |" % (
            _fmt_int(o["hit_null"]), _fmt_pct(o["hit_null_pct"])),
        "| hit 可用（0/1） | %s（覆盖率 %s） |" % (
            _fmt_int(o["hit_labeled"]), _fmt_pct(o["hit_coverage"])),
        "| hit 非法 | %s |" % _fmt_int(o["hit_invalid"]),
        "| distinct news_id | %s |" % _fmt_int(o["distinct_news_ids"]),
        "| 最老 trade_day | %s（%s 天） |" % (
            r["oldest_trade_day"] or "-",
            "-" if r["oldest_age_days"] is None else r["oldest_age_days"]),
        "| provenance 覆盖 | %s |" % (
            "%s/%s（%s）" % (_fmt_int(rep["provenance"]["found_ids"]),
                            _fmt_int(rep["provenance"]["checked_ids"]),
                            _fmt_pct(rep["provenance"]["coverage"]))
            if rep["provenance"]["available"] else "不可用"),
        "",
        "## 二、优先级口径",
        "",
        "- age_days = max(trade_day) − trade_day（自然日）；桶 `>=14d/7-14d/3-7d/1-3d/<1d` → `P0/P1/P2/P3/P4`；",
        "- worklist 排序：tier 升序（P0 先）→ 组内样本量降序 → trade_day 升序；",
        "- `--min-age-days` 可只看 P0 等阈值以上；`--limit` 截断条数；`<1d` 可能尚未到 T+1 回填窗口，排最后。",
        "",
        "## 三、优先 worklist（Top %d）" % len(rep["priority_worklist"]),
        "",
        "| # | 优先级 | trade_day | 陈旧 | n | conf≥0.8 占比 | models | bias | news_id 样例 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for e in rep["priority_worklist"]:
        lines.append("| %d | %s | %s | %sd | %s | %s | %s | %s | %s |" % (
            e["rank"], e["priority"], e["trade_day"], e["age_days"],
            _fmt_int(e["n"]), _fmt_pct(e["conf_high_share"]),
            ",".join("%s:%s" % (k, _fmt_int(v)) for k, v in e["models"].items()),
            ",".join("%s:%s" % (k, _fmt_int(v)) for k, v in e["biases"].items()),
            ", ".join(e["news_id_samples"][:3]) or "-"))
    lines += ["", "## 四、维度分组（model / 方向 / 置信度 / 陈旧度）", ""]
    lines += ["### 按 model", "",
              "| model | hit 空 | 占 hit 空 | 最老 trade_day | 最新 trade_day | aging 分布 |",
              "|---|---|---|---|---|---|"]
    for d in dims["by_model"]:
        pct = round(d["n"] / o["hit_null"], 4) if o["hit_null"] else None
        lines.append("| `%s` | %s | %s | %s | %s | %s |" % (
            d["name"], _fmt_int(d["n"]), _fmt_pct(pct),
            d["oldest_trade_day"] or "-", d["newest_trade_day"] or "-",
            " / ".join("%s %s" % (b, _fmt_int(d["age"][b]))
                       for b in AGE_BUCKETS if d["age"].get(b))))
    lines += ["", "### 按方向 bias", "", "| bias | hit 空 | 占 hit 空 | 最老 trade_day |",
              "|---|---|---|---|"]
    for d in dims["by_bias"]:
        pct = round(d["n"] / o["hit_null"], 4) if o["hit_null"] else None
        lines.append("| `%s` | %s | %s | %s |" % (
            d["name"], _fmt_int(d["n"]), _fmt_pct(pct),
            d["oldest_trade_day"] or "-"))
    lines += ["", "### 按置信度 conf", "", "| conf 桶 | hit 空 | 占 hit 空 | model 分布 |",
              "|---|---|---|---|"]
    for d in dims["by_conf"]:
        pct = round(d["n"] / o["hit_null"], 4) if o["hit_null"] else None
        lines.append("| `%s` | %s | %s | %s |" % (
            d["bucket"], _fmt_int(d["n"]), _fmt_pct(pct),
            ",".join("%s:%s" % (k, _fmt_int(v))
                     for k, v in list(d["models"].items())[:4]) or "-"))
    lines += ["", "### 按陈旧度（=优先级档）", "",
              "| 桶 | tier | hit 空 | 交易日数 | 最老 | 最新 |",
              "|---|---|---|---|---|---|"]
    for d in dims["by_staleness"]:
        lines.append("| `%s` | %s | %s | %s | %s | %s |" % (
            d["staleness_bucket"], d["tier"], _fmt_int(d["n"]),
            _fmt_int(d["trade_day_count"]), d["oldest_trade_day"] or "-",
            d["newest_trade_day"] or "-"))
    lines += ["", "## 五、provenance（回源样例）", ""]
    if rep["provenance"]["available"]:
        lines.append("- 覆盖：%s/%s（%s）；缺失 %s"
                     % (_fmt_int(rep["provenance"]["found_ids"]),
                        _fmt_int(rep["provenance"]["checked_ids"]),
                        _fmt_pct(rep["provenance"]["coverage"]),
                        _fmt_int(rep["provenance"]["missing_ids"])))
        lines.append("")
        lines.append("| # | trade_day | news_id | source | ts | title |")
        lines.append("|---|---|---|---|---|---|")
        for e in rep["priority_worklist"]:
            for p in e["provenance_sample"][:1]:
                lines.append("| %d | %s | `%s` | %s | %s | %s |" % (
                    e["rank"], e["trade_day"], p["news_id"], p["source"],
                    p["ts"], p["title"].replace("|", "\\|")[:60]))
    else:
        lines.append("- provenance 不可用（news.db 缺失或 `--no-provenance`）")
    bad = rep["bad_rows"]
    lines += ["", "## 六、坏行（fail-visible）", "",
              "- 非法 hit：%s；hit 空但 trade_day 非法：%s；conf 非法：%s；provenance 缺失 id：%s；合计 %s"
              % (_fmt_int(bad["invalid_hit"]),
                 _fmt_int(bad["invalid_trade_day"]),
                 _fmt_int(bad["invalid_conf"]),
                 _fmt_int(bad["provenance_missing_ids"]),
                 _fmt_int(bad["n"])), ""]
    lines += ["## 七、回填路线（写库 = L2/另单，本单未执行）", "",
              "- 本清单**只读**，未修改 `dec_audit`/`news.db`；",
              "- 实际补后验需写 `dec_audit`（生产库写入）→ **L2 五要素另单**，执行窗口内由批准人操作：", ""]
    for cmd in rep["fill_route"]["suggested_commands"]:
        lines.append("  ```text\n  %s\n  ```" % cmd)
    lines += ["", "## 八、口径与边界", "",
              "- age 以库内 max(trade_day) 为基准（自然日），非 wall clock；",
              "- 方向 bias / conf / model 均来自 `dec_audit` 原始字段（不推断）；",
              "- `<1d` 分组可能是 T+1 行情尚未回填，不等同“漏填”；",
              "- 建议先做 P0（>=14d）中 n 最大的交易日，回填后复跑本清单核对覆盖率。",
              ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 非 TTY / 旧解释器忽略
        pass
    ap = argparse.ArgumentParser(
        description="JEV hit 空后验缺口优先清单（只读 worklist）")
    ap.add_argument("--audit-db", default=str(DEFAULT_AUDIT_DB),
                    help="jev_audit.db 路径（默认 %s）" % DEFAULT_AUDIT_DB)
    ap.add_argument("--news-db", default=str(DEFAULT_NEWS_DB),
                    help="news.db 路径（默认 %s）" % DEFAULT_NEWS_DB)
    ap.add_argument("--no-provenance", action="store_true",
                    help="不读 news.db（provenance 置不可用）")
    ap.add_argument("--require-provenance", action="store_true",
                    help="news.db 不可用时 rc=2（默认降级 warning）")
    ap.add_argument("--limit", type=int, default=20,
                    help="worklist 条数（默认 20，1~200）")
    ap.add_argument("--min-age-days", type=float, default=0.0,
                    help="只输出 age_days >= 该值的组（默认 0）")
    ap.add_argument("--sample", type=int, default=20,
                    help="每组 provenance 样例数（默认 20，0~50）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--md", action="store_true", help="输出 Markdown")
    ap.add_argument("--out", default="", help="把所选格式写入文件（原子写）")
    ap.add_argument("--fail-on-bad", action="store_true",
                    help="存在坏行时 rc=4")
    a = ap.parse_args(argv)

    if a.json and a.md:
        print("[FAIL] --json 与 --md 只能二选一", file=sys.stderr)
        return 2
    if a.limit < 1 or a.limit > 200:
        print("[FAIL] --limit 取值 1~200", file=sys.stderr)
        return 2
    if a.sample < 0 or a.sample > 50:
        print("[FAIL] --sample 取值 0~50", file=sys.stderr)
        return 2
    if a.min_age_days < 0:
        print("[FAIL] --min-age-days 不能为负", file=sys.stderr)
        return 2
    audit_path = Path(a.audit_db)
    if not audit_path.is_file():
        print("[FAIL] audit DB 不存在：%s" % audit_path, file=sys.stderr)
        return 2
    warnings: list[str] = []
    try:
        aconn = ro_connect(audit_path)
        try:
            acols = require_audit_schema(aconn, audit_path)
            audit_view = scan_audit(aconn, acols, a.sample)
        finally:
            aconn.close()
    except PreconditionError as exc:
        print("[FAIL] %s" % exc, file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print("[FAIL] dec_audit 只读查询失败：%s" % exc, file=sys.stderr)
        return 2

    # provenance
    provenance = None
    all_ids: list[str] = []
    try:
        aconn = ro_connect(audit_path)
        try:
            all_ids = [str(r[0]) for r in aconn.execute(
                "SELECT DISTINCT news_id FROM dec_audit WHERE hit IS NULL")]
        finally:
            aconn.close()
    except sqlite3.Error:
        warnings.append("hit 空 news_id 列表读取失败，provenance 覆盖按样例计算")
    if all_ids:
        audit_view["distinct_news_ids"] = len(all_ids)
    sample_ids = []
    for group in audit_view["dimensions"]["by_trade_day"]:
        sample_ids.extend(group["news_id_samples"])
    if not a.no_provenance:
        news_path = Path(a.news_db)
        if not news_path.is_file():
            msg = "news.db 不存在，provenance 未加载：%s" % news_path
            if a.require_provenance:
                print("[FAIL] %s（--require-provenance）" % msg,
                      file=sys.stderr)
                return 2
            warnings.append(msg)
        else:
            try:
                provenance = load_provenance(
                    news_path, all_ids,
                    list(dict.fromkeys(sample_ids)), a.sample)
            except PreconditionError as exc:
                if a.require_provenance:
                    print("[FAIL] %s（--require-provenance）" % exc,
                          file=sys.stderr)
                    return 2
                warnings.append("provenance 不可用：%s" % exc)
                provenance = None
            except sqlite3.Error as exc:
                if a.require_provenance:
                    print("[FAIL] news.db 只读查询失败：%s（--require-provenance）"
                          % exc, file=sys.stderr)
                    return 2
                warnings.append("news.db 查询失败，provenance 未加载：%s" % exc)
                provenance = None

    rep = build_manifest(audit_view, provenance, a.limit, a.min_age_days,
                         a.sample)
    rep["audit_db"] = str(audit_path)
    rep["news_db"] = str(Path(a.news_db)) if not a.no_provenance else ""
    rep["warnings"] = warnings
    if a.json:
        text = json.dumps(rep, ensure_ascii=False, indent=2) + "\n"
    elif a.md:
        text = render_md(rep) + "\n"
    else:
        text = render_text(rep) + "\n"
    if a.out:
        try:
            _atomic_write(Path(a.out), text)
        except OSError as exc:
            print("[FAIL] --out 写入失败：%s" % exc, file=sys.stderr)
            return 1
    sys.stdout.write(text)
    if a.fail_on_bad and rep["bad_rows"]["n"] > 0:
        print("[FAIL] 存在 %d 行坏行，--fail-on-bad 置 rc=4"
              % rep["bad_rows"]["n"], file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
