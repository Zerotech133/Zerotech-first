# -*- coding: utf-8 -*-
"""IL-13 · 情报「证据—标的」只读接口工具 v1（tools 层，供 portal API 后半复用）。

背景 / 定位
-----------
情报板块的「证据—标的关系」需要稳定契约：一条新闻（证据）对应哪些 A 股标的
（target），且必须区分**可当真的精确代码**与**推断/模型候选**，避免把名称匹配或
模型猜测升级成"精确"。本工具是 IL-13（情报证据—标的关系只读接口 v1）的工具层：
只读 news.db + 既有 staging JSONL，输出 exact/candidate 严格分离的关系与回源证据，
portal `portal/api_intel_evidence*` 属 IL-13 后半（另单）。

契约（v1）
----------
1. **目标两分类，严格分离**：
   - ``exact``：证据本身含 A 股 6 位代码字面。来源层仅两个：
     * ``text_extract``：title/content 中显式代码（``SH600519`` / ``600519.SH`` / ``600519`` 等）；
     * ``stocks_code``：news.stocks 字段中本身就是 A 股代码形态的条目（采集侧结构化精确代码）。
   - ``candidate``：需要**经名称映射推断**或来自**模型判定**的标的，一律候选：
     * ``text_name_map``：title/content 命中名称表股票名 → 代码；
     * ``stocks_name_map``：stocks 字段里的名称条目经名称表映射；
     * ``jev_model_code``：JEV（LLM 判定）companies 里的代码字面——虽是代码，但属模型层，
       按 IL-13「名称/模型仅候选」**仍为 candidate，不升级 exact**；
     * ``jev_model_name``：JEV companies 里的名称经名称表映射。
   同一新闻内某标的存在 exact 证据时，候选条目被丢弃（不重复、不升级）。
2. **provenance**：每条关系带证据 ``news_id / source / ts / title``（news 表无 url 字段，
   url 置 null；后续接源表再补）+ origin（layer/rule/derived/matched_name/model）。
3. **fail-visible**：空结果正常返回空列表；坏 stocks/jev JSON、staging 坏行/不一致、
   名称表不可用、回源抽样 <95% 均写入 ``failures``/``warnings`` 并置 ``partial_failure``；
   ``--strict`` 时 partial_failure → rc=3。
4. **只读**：sqlite 以 ``file:<abs>?mode=ro`` URI 打开 + ``PRAGMA query_only=1``；
   本模块无任何写 SQL，不写 news.db / 生产库，不起停服务。
5. **稳定分页**：``--code`` 查询的 exact/candidate 两个列表各自独立按
   ``(ts DESC, news_id ASC, target ASC)`` 排序，``--limit/--offset`` 分页可复跑。

CLI
---
    python -X utf8 tools/intel_evidence_targets.py --stats            # 默认：覆盖/关系计数
    python -X utf8 tools/intel_evidence_targets.py --code 600519 --since 2026-10-01
    python -X utf8 tools/intel_evidence_targets.py --code SH600519 --limit 20 --offset 0
    python -X utf8 tools/intel_evidence_targets.py --sample 50        # 回源可定位抽样
    python -X utf8 tools/intel_evidence_targets.py --stats --json
    python -X utf8 tools/intel_evidence_targets.py --code 600519 --sample 20

数据源默认解析：news.db 与名称表沿用 tools/intel_entity_extract.py 的自动定位
（worktree 内 gitignored → 回落主工作树）；staging 默认
``<repo>/deliverables/2026-11-03_intel_entity_staging_v1.jsonl``（已随分支入库）。
可用 ``--no-staging`` / ``--staging <path>``、``--no-name-map`` / ``--name-map <path>`` 覆盖。

退出码：0 成功（partial_failure 也返回 0，但 fail-visible）；2 参数/数据源致命错误；
3 ``--strict`` 下存在 partial_failure。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

TOOL_NAME = "intel_evidence_targets"
TOOL_VERSION = "v1"

HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import intel_entity_extract as _ix  # noqa: E402  （同目录工具，复用抽取/归一/名称匹配口径）

DEFAULT_STAGING_REL = "deliverables/2026-11-03_intel_entity_staging_v1.jsonl"
SAMPLE_LOCATABLE_MIN_PCT = 95.0
#: 每行候选标的上限（与前序 staging v1 的 MAX_CANDIDATES_PER_ROW=8 口径一致，
#: 保证 staging 对拍可复现、输出稳定）
MAX_CANDIDATES_PER_ROW = 8
TITLE_MAX = 200
MAX_FAILURE_SAMPLES = 5

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2})(?::(\d{2}))?)?$")


# ---------------------------------------------------------------------------
# 只读 sqlite
# ---------------------------------------------------------------------------
def readonly_uri(db_path: Path) -> str:
    """构造 sqlite 只读 URI（mode=ro），供断言/日志留痕。"""
    return "file:%s?mode=ro" % Path(db_path).resolve().as_posix()


def open_readonly(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(readonly_uri(db_path), uri=True)
    con.execute("PRAGMA query_only=1")
    return con


def inspect_columns(con: sqlite3.Connection) -> list[str]:
    return [str(r[1]) for r in con.execute("PRAGMA table_info(news)").fetchall()]


# ---------------------------------------------------------------------------
# 数据源定位
# ---------------------------------------------------------------------------
def _candidate_roots(repo_root: Path) -> list[Path]:
    roots = [repo_root]
    for wt in _ix.git_worktrees(repo_root):
        if wt not in roots:
            roots.append(wt)
    return roots


def resolve_default_db(repo_root: Path) -> Optional[Path]:
    return _ix.resolve_default_db(repo_root)


def resolve_default_name_map(repo_root: Path) -> Optional[Path]:
    return _ix.resolve_default_name_map(repo_root)


def resolve_default_staging(repo_root: Path) -> Optional[Path]:
    for root in _candidate_roots(repo_root):
        cand = root / DEFAULT_STAGING_REL
        if cand.is_file():
            return cand
    return None


# ---------------------------------------------------------------------------
# 时间范围
# ---------------------------------------------------------------------------
def parse_date_bound(raw: Optional[str], *, upper: bool) -> Optional[str]:
    """把 YYYY-MM-DD[ HH:MM[:SS]] 归一为可与 news.ts 字典序比较的字符串。

    upper=True 时日期补 ``23:59:59``（含当天），否则补 ``00:00:00``。
    """
    if raw is None or not str(raw).strip():
        return None
    s = str(raw).strip().replace("T", " ")
    m = _DATE_RE.match(s)
    if not m:
        raise ValueError("日期格式非法: %r（应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM）" % raw)
    day, hm, sec = m.group(1), m.group(2), m.group(3)
    if hm is None:
        return day + (" 23:59:59" if upper else " 00:00:00")
    return day + " " + hm + (":" + sec if sec else (":59" if upper else ":00"))


# ---------------------------------------------------------------------------
# staging JSONL（前序工具产物，作为可选输入 + 一致性对拍）
# ---------------------------------------------------------------------------
def load_staging(path: Path) -> dict[str, Any]:
    """加载 staging JSONL；坏行 fail-visible 但不中断。"""
    rows: dict[str, dict[str, Any]] = {}
    bad: list[dict[str, Any]] = []
    required = ("id", "exact", "candidates")
    first: Optional[dict[str, Any]] = None
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                bad.append({"line": lineno, "reason": "invalid_json"})
                continue
            if not isinstance(item, dict) or any(k not in item for k in required):
                bad.append({"line": lineno, "reason": "missing_required_fields"})
                continue
            if first is None:
                first = item
            nid = str(item.get("id"))
            rows[nid] = item
    return {
        "path": str(path),
        "rows": rows,
        "num_rows": len(rows),
        "bad_lines": bad,
        "sample": first,
    }


# ---------------------------------------------------------------------------
# 关系构建
# ---------------------------------------------------------------------------
def _norm_ts(ts: Any) -> str:
    return str(ts) if ts is not None else ""


def _truncate(title: Any) -> str:
    s = "" if title is None else str(title)
    return s[:TITLE_MAX]


def _relation(nid: Any, source: Any, ts: Any, title: Any, target: str,
              target_class: str, origin: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": target,
        "target_class": target_class,
        "evidence": {
            "news_id": nid,
            "source": source,
            "ts": _norm_ts(ts),
            "title": _truncate(title),
            "url": None,
        },
        "origins": [origin],
        "ambiguous": False,
        "staging_confirmed": False,
    }


def _add_origin(rel: dict[str, Any], origin: dict[str, Any]) -> None:
    for cur in rel["origins"]:
        if cur.get("layer") == origin.get("layer") and cur.get("rule") == origin.get("rule") \
                and cur.get("matched_name") == origin.get("matched_name"):
            return
    rel["origins"].append(origin)


def _origin(layer: str, rule: Optional[str], derived: str,
            matched_name: Optional[str] = None, model: bool = False) -> dict[str, Any]:
    return {
        "layer": layer,
        "rule": rule,
        "derived": derived,
        "matched_name": matched_name,
        "model": model,
    }


def _parse_list_field(raw: Any, kind: str, failures: list[dict[str, Any]],
                      counts: Counter) -> list[Any]:
    """解析 stocks/companies 这类 JSON 列表字段；坏行 fail-visible。"""
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    try:
        val = json.loads(s)
    except (ValueError, TypeError):
        counts["bad_%s_json" % kind] += 1
        if len(failures) < 64:
            failures.append({"kind": "bad_%s_json" % kind, "reason": "invalid_json",
                             "sample": s[:120]})
        return []
    if val is None:
        return []
    if not isinstance(val, list):
        counts["bad_%s_type" % kind] += 1
        if len(failures) < 64:
            failures.append({"kind": "bad_%s_type" % kind, "reason": "not_list",
                             "sample": s[:120]})
        return []
    return val


def _parse_jev_companies(raw: Any, failures: list[dict[str, Any]],
                         counts: Counter) -> list[str]:
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    try:
        val = json.loads(s)
    except (ValueError, TypeError):
        counts["bad_jev_json"] += 1
        if len(failures) < 64:
            failures.append({"kind": "bad_jev_json", "reason": "invalid_json",
                             "sample": s[:120]})
        return []
    if not isinstance(val, dict):
        counts["bad_jev_type"] += 1
        if len(failures) < 64:
            failures.append({"kind": "bad_jev_type", "reason": "not_dict",
                             "sample": s[:120]})
        return []
    comps = val.get("companies") or []
    if not isinstance(comps, list):
        counts["bad_jev_companies_type"] += 1
        if len(failures) < 64:
            failures.append({"kind": "bad_jev_companies_type", "reason": "not_list",
                             "sample": s[:120]})
        return []
    return [str(x) for x in comps if str(x).strip()]


def _select_sql(columns: list[str], *, since: Optional[str], until: Optional[str],
                code6: Optional[str], like_terms: list[str]) -> tuple[str, list[Any]]:
    def col(name: str) -> str:
        return name if name in columns else "NULL"

    select = "SELECT {id}, {source}, {ts}, title, {content}, {stocks}, {jev} FROM news".format(
        id=col("id"), source=col("source"), ts=col("ts"),
        content=col("content"), stocks=col("stocks"), jev=col("jev"),
    )
    where: list[str] = []
    params: list[Any] = []
    if "ts" in columns and (since or until):
        if since:
            where.append("ts >= ?")
            params.append(since)
        if until:
            where.append("ts <= ?")
            params.append(until)
    if code6:
        patterns = ["%" + code6 + "%"] + ["%" + t + "%" for t in like_terms]
        ors: list[str] = []
        for p in patterns:
            ors.append("coalesce(title,'') LIKE ?")
            params.append(p)
            if "content" in columns:
                ors.append("coalesce(content,'') LIKE ?")
                params.append(p)
            if "stocks" in columns:
                ors.append("coalesce(stocks,'') LIKE ?")
                params.append(p)
            if "jev" in columns:
                ors.append("coalesce(jev,'') LIKE ?")
                params.append(p)
        where.append("(" + " OR ".join(ors) + ")")
    sql = select
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rowid ASC"
    return sql, params


def build_index(con: sqlite3.Connection, columns: list[str], *, matcher: Optional[_ix.NameMatcher],
                staging_ids: Optional[set] = None,
                since: Optional[str] = None, until: Optional[str] = None,
                code_filter: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """只读扫描 news 表，构建 exact/candidate 关系索引。"""
    fail_counts: Counter = Counter()
    failures: list[dict[str, Any]] = []
    warnings: list[str] = []

    if "id" not in columns or "title" not in columns:
        raise ValueError("news 表缺少必需列 id/title（实际列: %s）" % ",".join(columns))

    relations: dict[tuple, dict[str, Any]] = {}
    before_ids: set = set()
    exact_ids: set = set()
    candidate_ids: set = set()
    ambiguous_news: dict = {}
    rows_scanned = 0
    rows_missing_ts = 0
    candidate_truncated_rows = 0
    staging_ids = staging_ids or set()
    live_text: dict[str, dict[str, set]] = {}

    sql, params = _select_sql(
        columns, since=since, until=until,
        code6=(code_filter or {}).get("code6"),
        like_terms=(code_filter or {}).get("names") or [],
    )
    cur = con.execute(sql, params)
    for row in cur:
        nid, source, ts, title, content, stocks_raw, jev_raw = row
        rows_scanned += 1
        ts_s = _norm_ts(ts)
        if (since or until) and not ts_s:
            rows_missing_ts += 1
        title_s = "" if title is None else str(title)
        content_s = "" if content is None else str(content)
        text = title_s + "\n" + content_s

        stocks = _parse_list_field(stocks_raw, "stocks", failures, fail_counts)
        if stocks:
            before_ids.add(nid)
        companies = _parse_jev_companies(jev_raw, failures, fail_counts)

        # ---- exact 层 1：stocks 字段里的代码字面
        stocks_codes: set[str] = set()
        stocks_names: list[str] = []
        for item in stocks:
            sym = _ix.normalize_symbol(item)
            if sym:
                stocks_codes.add(sym)
            else:
                stocks_names.append(str(item).strip())
        # ---- exact 层 2：title/content 显式代码
        ent = _ix.extract_entities(text)
        text_exact: list[str] = list(ent["exact"])
        exact_symbols = set(text_exact) | stocks_codes
        if exact_symbols:
            exact_ids.add(nid)
            for sym in text_exact:
                _add_origin(
                    relations.setdefault((nid, sym, "exact"), _relation(
                        nid, source, ts, title, sym, "exact",
                        _origin("text_extract", ent["rules"].get(sym), "code_literal"))),
                    _origin("text_extract", ent["rules"].get(sym), "code_literal"))
            for sym in sorted(stocks_codes):
                _add_origin(
                    relations.setdefault((nid, sym, "exact"), _relation(
                        nid, source, ts, title, sym, "exact",
                        _origin("stocks_code", "stocks_item", "code_literal"))),
                    _origin("stocks_code", "stocks_item", "code_literal"))

        # ---- candidate 层 1：text 名称映射
        name_matches: dict[str, str] = {}
        if matcher:
            matched, _unmapped = matcher.find_symbols(text)
            for name, sym in matched:
                name_matches.setdefault(sym, name)
        # 契约候选：exact（含 stocks_code）已覆盖的标的剔除，不升级不重复
        text_candidates = {
            sym: name for sym, name in name_matches.items() if sym not in exact_symbols
        }
        text_candidates = dict(
            sorted(text_candidates.items())[:MAX_CANDIDATES_PER_ROW])
        # 前序 staging 对拍口径：只按 text_exact 压制（不感知 stocks_code 层）
        staging_text_candidates = dict(sorted({
            sym: name for sym, name in name_matches.items() if sym not in set(text_exact)
        }.items())[:MAX_CANDIDATES_PER_ROW])
        # ---- candidate 层 2：stocks 名称映射
        stocks_candidates: dict[str, str] = {}
        for name in stocks_names:
            if not name or len(name) < 2:
                continue
            sym = _name_to_symbol(matcher, name)
            if sym and sym not in exact_symbols:
                stocks_candidates.setdefault(sym, name)
        # ---- candidate 层 3/4：JEV 模型判定
        jev_candidates: dict[str, dict[str, Any]] = {}
        for item in companies:
            sym = _ix.normalize_symbol(item)
            if sym:
                if sym not in exact_symbols:
                    jev_candidates.setdefault(sym, {
                        "name": None, "origin": _origin("jev_model_code", "companies_item",
                                                       "model_code", model=True)})
                continue
            name = str(item).strip()
            if len(name) < 2:
                continue
            sym = _name_to_symbol(matcher, name)
            if sym and sym not in exact_symbols:
                jev_candidates.setdefault(sym, {
                    "name": name, "origin": _origin("jev_model_name", "companies_item",
                                                    "model_name_map", matched_name=name,
                                                    model=True)})

        merged_candidates: dict[str, dict[str, Any]] = {}
        for sym, name in text_candidates.items():
            merged_candidates.setdefault(sym, {"name": name, "origins": []})["origins"].append(
                _origin("text_name_map", "title_or_content", "name_map", matched_name=name))
        for sym, name in stocks_candidates.items():
            merged_candidates.setdefault(sym, {"name": name, "origins": []})["origins"].append(
                _origin("stocks_name_map", "stocks_item", "name_map", matched_name=name))
        for sym, payload in jev_candidates.items():
            merged_candidates.setdefault(sym, {"name": payload["name"], "origins": []})[
                "origins"].append(payload["origin"])
        if len(merged_candidates) > MAX_CANDIDATES_PER_ROW:
            candidate_truncated_rows += 1
            merged_candidates = {
                k: merged_candidates[k]
                for k in sorted(merged_candidates)[:MAX_CANDIDATES_PER_ROW]
            }

        if merged_candidates:
            candidate_ids.add(nid)
            if len(merged_candidates) > 1:
                ambiguous_news[nid] = len(merged_candidates)
            for sym, payload in merged_candidates.items():
                rel = _relation(nid, source, ts, title, sym, "candidate",
                                payload["origins"][0])
                for o in payload["origins"][1:]:
                    _add_origin(rel, o)
                relations.setdefault((nid, sym, "candidate"), rel)

        if staging_ids and nid in staging_ids:
            live_text[str(nid)] = {
                "exact": set(text_exact),
                "candidates": set(staging_text_candidates),
            }

    for rel in relations.values():
        if rel["target_class"] == "candidate":
            rel["ambiguous"] = bool(ambiguous_news.get(rel["evidence"]["news_id"]))

    return {
        "relations": list(relations.values()),
        "before_ids": before_ids,
        "exact_ids": exact_ids,
        "candidate_ids": candidate_ids,
        "ambiguous_news": ambiguous_news,
        "rows_scanned": rows_scanned,
        "rows_missing_ts": rows_missing_ts,
        "candidate_truncated_rows": candidate_truncated_rows,
        "fail_counts": dict(fail_counts),
        "failures": failures[:64],
        "warnings": warnings,
        "live_text": live_text,
    }


def _name_to_symbol(matcher: Optional[_ix.NameMatcher], name: str) -> Optional[str]:
    if not matcher:
        return None
    return matcher.name_to_symbol.get(name)


# ---------------------------------------------------------------------------
# staging 一致性对拍（fail-visible）
# ---------------------------------------------------------------------------
def check_staging(index: dict[str, Any], staging: dict[str, Any]) -> dict[str, Any]:
    live = index.get("live_text") or {}
    exact_match = exact_mismatch = cand_match = cand_mismatch = 0
    missing_rows = 0
    mismatch_samples: list[dict[str, Any]] = []
    for nid, item in staging["rows"].items():
        if nid not in live:
            missing_rows += 1
            continue
        st_exact = set(item.get("exact") or [])
        st_cands = set()
        for c in item.get("candidates") or []:
            if isinstance(c, dict) and c.get("symbol"):
                st_cands.add(str(c["symbol"]))
        lv = live[nid]
        if st_exact == lv["exact"]:
            exact_match += 1
        else:
            exact_mismatch += 1
            if len(mismatch_samples) < MAX_FAILURE_SAMPLES:
                mismatch_samples.append({
                    "news_id": nid, "field": "exact",
                    "staging": sorted(st_exact), "live": sorted(lv["exact"]),
                })
        # 前序 staging 对 exact 非空行不计算候选（候选策略：exact 优先），故仅对
        # staging exact 为空的行做候选对拍，避免口径误报。
        if not st_exact:
            if st_cands == lv["candidates"]:
                cand_match += 1
            else:
                cand_mismatch += 1
                if len(mismatch_samples) < MAX_FAILURE_SAMPLES:
                    mismatch_samples.append({
                        "news_id": nid, "field": "candidates",
                        "staging": sorted(st_cands), "live": sorted(lv["candidates"]),
                    })
    return {
        "staging_rows": staging["num_rows"],
        "staging_bad_lines": len(staging["bad_lines"]),
        "matched_rows": len(live),
        "missing_rows": missing_rows,
        "exact_match": exact_match,
        "exact_mismatch": exact_mismatch,
        "candidate_match": cand_match,
        "candidate_mismatch": cand_mismatch,
        "mismatch_samples": mismatch_samples,
    }


def annotate_staging(relations: Iterable[dict[str, Any]], staging: dict[str, Any]) -> None:
    rows = staging["rows"]
    for rel in relations:
        nid = str(rel["evidence"]["news_id"])
        item = rows.get(nid)
        if not item:
            continue
        target = rel["target"]
        if rel["target_class"] == "exact" and target in set(item.get("exact") or []):
            rel["staging_confirmed"] = True
        elif rel["target_class"] == "candidate":
            for c in item.get("candidates") or []:
                if isinstance(c, dict) and c.get("symbol") == target:
                    rel["staging_confirmed"] = True
                    break


# ---------------------------------------------------------------------------
# 回源校验（抽样用）
# ---------------------------------------------------------------------------
def _row_for_verify(con: sqlite3.Connection, columns: list[str], nid: Any) -> Optional[dict[str, Any]]:
    def col(name: str) -> str:
        return name if name in columns else "NULL"

    sql = "SELECT {id}, {source}, {ts}, title, {content}, {stocks}, {jev} FROM news WHERE id = ?".format(
        id=col("id"), source=col("source"), ts=col("ts"), content=col("content"),
        stocks=col("stocks"), jev=col("jev"),
    )
    row = con.execute(sql, (str(nid),)).fetchone()
    if not row:
        return None
    return {"id": row[0], "source": row[1], "ts": row[2], "title": row[3],
            "content": row[4], "stocks": row[5], "jev": row[6]}


def verify_relation(con: sqlite3.Connection, columns: list[str],
                    rel: dict[str, Any]) -> dict[str, Any]:
    """回源：按 news_id 重取该行，校验证据锚点是否可定位（逐 origin 尝试）。"""
    row = _row_for_verify(con, columns, rel["evidence"]["news_id"])
    if row is None:
        return {"locatable": False, "reason": "news_row_missing", "anchor": None}
    title = "" if row["title"] is None else str(row["title"])
    content = "" if row["content"] is None else str(row["content"])
    text = title + "\n" + content
    sym = rel["target"]
    code6 = sym[-6:]

    def _stocks() -> list:
        return _parse_list_field(row["stocks"], "verify_stocks", [], Counter())

    def _companies() -> list[str]:
        return _parse_jev_companies(row["jev"], [], Counter())

    anchor: Optional[str] = None
    for o in rel.get("origins") or []:
        layer = str(o.get("layer"))
        name = str(o.get("matched_name") or "")
        if layer == "text_extract" and code6 in text:
            anchor = "code_in_text"
        elif layer == "stocks_code" and any(
                _ix.normalize_symbol(x) == sym for x in _stocks()):
            anchor = "code_in_stocks"
        elif layer == "text_name_map" and len(name) >= 2 and name in text:
            anchor = "name_in_text"
        elif layer == "stocks_name_map" and len(name) >= 2 and any(
                str(x).strip() == name for x in _stocks()):
            anchor = "name_in_stocks"
        elif layer == "jev_model_code" and any(
                _ix.normalize_symbol(x) == sym for x in _companies()):
            anchor = "code_in_jev_companies"
        elif layer == "jev_model_name" and len(name) >= 2 and any(
                str(x).strip() == name for x in _companies()):
            anchor = "name_in_jev_companies"
        if anchor:
            break
    if anchor is None:
        return {"locatable": False, "reason": "anchor_not_found", "anchor": None}
    src_ok = (rel["evidence"]["source"] == row["source"])
    ts_ok = (_norm_ts(rel["evidence"]["ts"]) == _norm_ts(row["ts"]))
    if not (src_ok and ts_ok):
        return {"locatable": False, "reason": "evidence_meta_mismatch", "anchor": anchor}
    return {"locatable": True, "reason": "ok", "anchor": anchor}


def relation_sort_key(rel: dict[str, Any]) -> tuple:
    ev = rel["evidence"]
    return (-_ts_ordinal(ev.get("ts")), str(ev.get("news_id")), str(rel["target"]))


def _ts_ordinal(ts: Any) -> int:
    """ts 文本 → 可比较整数（无法解析置 0，保证排序稳定）。"""
    s = _norm_ts(ts)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", s)
    if not m:
        return 0
    y, mo, d, h, mi = (int(x) for x in m.groups())
    return (((y * 13 + mo) * 32 + d) * 24 + h) * 60 + mi


def stratified_sample(relations: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """按 exact/candidate 分层、均匀间隔取 n 条（确定性、可复跑）。"""
    if n <= 0:
        return []
    exact = sorted((r for r in relations if r["target_class"] == "exact"), key=relation_sort_key)
    cand = sorted((r for r in relations if r["target_class"] == "candidate"), key=relation_sort_key)
    n_exact = n // 2 if cand else n
    n_cand = n - n_exact if exact else n
    picked: list[dict[str, Any]] = []
    for pool, k in ((exact, n_exact), (cand, n_cand)):
        if k <= 0 or not pool:
            continue
        if len(pool) <= k:
            picked.extend(pool)
        else:
            for i in range(k):
                idx = int(round(i * (len(pool) - 1) / max(k - 1, 1)))
                picked.append(pool[idx])
    return picked[:n] if picked else relations[:min(n, len(relations))]


# ---------------------------------------------------------------------------
# 统计 / 分页
# ---------------------------------------------------------------------------
def paginate(relations: list[dict[str, Any]], limit: int, offset: int) -> dict[str, Any]:
    total = len(relations)
    start = max(0, offset)
    if limit and limit > 0:
        items = relations[start:start + limit]
        has_more = start + len(items) < total
    else:
        items = relations[start:]
        has_more = False
    return {
        "total": total,
        "limit": limit,
        "offset": start,
        "has_more": bool(has_more),
        "items": items,
    }


def build_stats(index: dict[str, Any], *, staging_check: Optional[dict[str, Any]],
                name_map: dict[str, Any], relation_total: int) -> dict[str, Any]:
    before = len(index["before_ids"])
    exact_rows = len(index["exact_ids"])
    after_rows = len(index["before_ids"] | index["exact_ids"])
    scanned = index["rows_scanned"] or 1
    exact_layers: Counter = Counter()
    cand_layers: Counter = Counter()
    exact_rel = cand_rel = 0
    exact_targets: set = set()
    cand_targets: set = set()
    by_source: Counter = Counter()
    for rel in index["relations"]:
        if rel["target_class"] == "exact":
            exact_rel += 1
            exact_targets.add(rel["target"])
        else:
            cand_rel += 1
            cand_targets.add(rel["target"])
        by_source[str(rel["evidence"].get("source"))] += 1
        for o in rel["origins"]:
            if rel["target_class"] == "exact":
                exact_layers[str(o.get("layer"))] += 1
            else:
                cand_layers[str(o.get("layer"))] += 1
    pct = lambda a, b: round(a * 100.0 / b, 2) if b else 0.0
    return {
        "news_total": None,  # 由调用方补（全表 count）
        "news_scanned": index["rows_scanned"],
        "rows_missing_ts": index["rows_missing_ts"],
        "before_rows": before,
        "before_pct": pct(before, scanned),
        "exact_rows": exact_rows,
        "exact_pct": pct(exact_rows, scanned),
        "after_rows": after_rows,
        "after_pct": pct(after_rows, scanned),
        "delta_pp": round(pct(after_rows, scanned) - pct(before, scanned), 2),
        "candidate_rows": len(index["candidate_ids"]),
        "candidate_pct": pct(len(index["candidate_ids"]), scanned),
        "ambiguous_candidate_rows": len(index["ambiguous_news"]),
        "candidate_truncated_rows": index.get("candidate_truncated_rows", 0),
        "relation_total": relation_total,
        "exact_relations": exact_rel,
        "candidate_relations": cand_rel,
        "distinct_exact_targets": len(exact_targets),
        "distinct_candidate_targets": len(cand_targets),
        "exact_relation_layers": dict(sorted(exact_layers.items())),
        "candidate_relation_layers": dict(sorted(cand_layers.items())),
        "relation_by_source": dict(by_source.most_common(15)),
        "failure_counts": dict(index["fail_counts"]),
        "staging_check": staging_check,
        "name_map_entries": int(name_map.get("entries") or 0),
    }


# ---------------------------------------------------------------------------
# JSON 输出（schema 稳定：键集合固定，不用场景为 null）
# ---------------------------------------------------------------------------
def _json_result(*, mode: str, query: dict[str, Any], sources: dict[str, Any],
                 stats: Optional[dict[str, Any]], exact: Optional[dict[str, Any]],
                 candidate: Optional[dict[str, Any]], sample: Optional[dict[str, Any]],
                 failures: list[dict[str, Any]], warnings: list[str],
                 partial_failure: bool) -> dict[str, Any]:
    return {
        "tool": TOOL_NAME,
        "version": TOOL_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "readonly": True,
        "db": {"path": sources["db"]["path"], "mode": "ro",
               "uri": readonly_uri(Path(sources["db"]["path"]))},
        "sources": {
            "db": sources["db"],
            "staging": sources["staging"],
            "name_map": sources["name_map"],
        },
        "mode": mode,
        "query": {
            "code": query.get("code"),
            "since": query.get("since"),
            "until": query.get("until"),
            "limit": query.get("limit"),
            "offset": query.get("offset"),
            "sample": query.get("sample"),
        },
        "stats": stats,
        "exact": exact,
        "candidate": candidate,
        "sample": sample,
        "failures": failures,
        "warnings": warnings,
        "partial_failure": bool(partial_failure),
    }


def _human_result(result: dict[str, Any], *, elapsed: float) -> str:
    lines: list[str] = []
    lines.append("[metadata]")
    lines.append("tool=%s version=%s readonly=%s" % (
        result["tool"], result["version"], "true" if result["readonly"] else "false"))
    lines.append("db=%s" % result["db"]["path"])
    lines.append("db_mode=%s" % result["db"]["mode"])
    lines.append("mode=%s" % result["mode"])
    q = result["query"]
    lines.append("query_code=%s" % (q.get("code") or ""))
    lines.append("query_since=%s" % (q.get("since") or ""))
    lines.append("query_until=%s" % (q.get("until") or ""))
    lines.append("query_limit=%s query_offset=%s" % (q.get("limit"), q.get("offset")))
    lines.append("[sources]")
    lines.append("staging_loaded=%s" % ("true" if result["sources"]["staging"]["loaded"] else "false"))
    lines.append("staging_path=%s" % (result["sources"]["staging"]["path"] or ""))
    lines.append("staging_rows=%d" % int(result["sources"]["staging"].get("rows") or 0))
    lines.append("name_map_loaded=%s" % (
        "true" if result["sources"]["name_map"]["loaded"] else "false"))
    lines.append("name_map_entries=%d" % int(result["sources"]["name_map"].get("entries") or 0))
    st = result.get("stats")
    if st:
        lines.append("[stats]")
        lines.append("news_total=%s" % st.get("news_total"))
        lines.append("news_scanned=%d" % st["news_scanned"])
        lines.append("before_rows=%d" % st["before_rows"])
        lines.append("before_pct=%.2f%%" % st["before_pct"])
        lines.append("exact_rows=%d" % st["exact_rows"])
        lines.append("exact_pct=%.2f%%" % st["exact_pct"])
        lines.append("after_rows=%d" % st["after_rows"])
        lines.append("after_pct=%.2f%%" % st["after_pct"])
        lines.append("delta_pp=%.2f" % st["delta_pp"])
        lines.append("candidate_rows=%d" % st["candidate_rows"])
        lines.append("ambiguous_candidate_rows=%d" % st["ambiguous_candidate_rows"])
        lines.append("candidate_truncated_rows=%d" % st["candidate_truncated_rows"])
        lines.append("relation_total=%d" % st["relation_total"])
        lines.append("exact_relations=%d" % st["exact_relations"])
        lines.append("candidate_relations=%d" % st["candidate_relations"])
        lines.append("distinct_exact_targets=%d" % st["distinct_exact_targets"])
        lines.append("distinct_candidate_targets=%d" % st["distinct_candidate_targets"])
        for k, v in (st.get("exact_relation_layers") or {}).items():
            lines.append("exact_layer[%s]=%d" % (k, v))
        for k, v in (st.get("candidate_relation_layers") or {}).items():
            lines.append("candidate_layer[%s]=%d" % (k, v))
        sc = st.get("staging_check")
        if sc:
            lines.append("staging_check_rows=%d" % sc["staging_rows"])
            lines.append("staging_exact_match=%d" % sc["exact_match"])
            lines.append("staging_exact_mismatch=%d" % sc["exact_mismatch"])
            lines.append("staging_candidate_mismatch=%d" % sc["candidate_mismatch"])
            lines.append("staging_missing_rows=%d" % sc["missing_rows"])
    for cls in ("exact", "candidate"):
        page = result.get(cls)
        if not page:
            continue
        lines.append("[%s] total=%d offset=%d limit=%s has_more=%s" % (
            cls, page["total"], page["offset"], page["limit"],
            "true" if page["has_more"] else "false"))
        for rel in page["items"]:
            ev = rel["evidence"]
            origins = ",".join(str(o.get("layer")) for o in rel["origins"])
            lines.append("%s target=%s news_id=%s source=%s ts=%s origins=%s ambiguous=%s staging_confirmed=%s url=%s title=%s" % (
                rel["target_class"], rel["target"], ev["news_id"], ev["source"], ev["ts"],
                origins, "true" if rel["ambiguous"] else "false",
                "true" if rel["staging_confirmed"] else "false", ev.get("url") or "",
                ev["title"].replace("\n", " ")[:80]))
    smp = result.get("sample")
    if smp:
        lines.append("[sample]")
        lines.append("sample_requested=%d" % smp["requested"])
        lines.append("sample_returned=%d" % smp["returned"])
        lines.append("sample_locatable=%d" % smp["locatable"])
        lines.append("sample_locatable_pct=%.2f%%" % smp["locatable_pct"])
        lines.append("sample_threshold_pct=%.2f" % smp["threshold_pct"])
        for it in smp["items"]:
            lines.append("sample target=%s class=%s news_id=%s anchor=%s locatable=%s reason=%s" % (
                it["target"], it["target_class"], it["evidence"]["news_id"],
                it["verify"].get("anchor") or "", "true" if it["verify"]["locatable"] else "false",
                it["verify"].get("reason")))
    lines.append("[failures]")
    lines.append("failure_count=%d partial_failure=%s" % (
        len(result["failures"]), "true" if result["partial_failure"] else "false"))
    for f in result["failures"]:
        lines.append("failure kind=%s news_id=%s reason=%s" % (
            f.get("kind"), f.get("news_id", ""), f.get("reason", "")))
    for w in result["warnings"]:
        lines.append("WARN %s" % w)
    lines.append("[done]")
    lines.append("elapsed_s=%.1f" % elapsed)
    lines.append("rc=%d" % (3 if result["partial_failure"] and result.get("_strict") else 0))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="intel_evidence_targets.py",
        description="IL-13 情报证据—标的只读接口工具 v1（exact/candidate 严格分离）",
    )
    p.add_argument("--db", help="news.db 路径（默认自动定位）")
    p.add_argument("--staging", help="staging JSONL 路径（默认 deliverables/2026-11-03_intel_entity_staging_v1.jsonl）")
    p.add_argument("--no-staging", action="store_true", help="不加载 staging")
    p.add_argument("--name-map", help="名称→代码表 JSON（默认自动定位 stock_names.json）")
    p.add_argument("--no-name-map", action="store_true", help="不加载名称表（candidate 层不可用，fail-visible）")
    p.add_argument("--stats", action="store_true", help="覆盖/关系计数（默认模式）")
    p.add_argument("--code", help="按 A 股代码查询关系（如 600519 / SH600519 / 600519.SH）")
    p.add_argument("--since", help="起始日期 YYYY-MM-DD[ HH:MM]（含）")
    p.add_argument("--until", help="截止日期 YYYY-MM-DD[ HH:MM]（含）")
    p.add_argument("--sample", type=int, help="回源可定位抽样条数（按 exact/candidate 分层）")
    p.add_argument("--limit", type=int, default=50, help="--code 每类关系分页大小（默认 50，0=全部）")
    p.add_argument("--offset", type=int, default=0, help="--code 分页偏移（默认 0）")
    p.add_argument("--json", action="store_true", help="输出稳定 JSON schema（stdout 仅 JSON）")
    p.add_argument("--strict", action="store_true", help="partial_failure 时返回 rc=3")
    return p


def _fatal(msg: str) -> int:
    sys.stderr.write("ERROR %s\n" % msg)
    sys.stderr.write("rc=2\n")
    return 2


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any], float]:
    t0 = time.time()
    failures: list[dict[str, Any]] = []
    warnings: list[str] = []
    partial = False

    # ---- 参数校验 / 模式
    if args.sample is not None and args.sample <= 0:
        return _fatal("--sample 必须为正整数"), {}, time.time() - t0
    if args.stats and (args.code or args.sample is not None):
        return _fatal("--stats 不能与 --code/--sample 组合（模式互斥）"), {}, time.time() - t0
    if args.sample is not None and args.limit != 50:
        return _fatal("--sample 模式不用 --limit（分层抽样固定取 N 条）"), {}, time.time() - t0
    mode = "stats" if args.stats or not (args.code or args.sample is not None) else (
        "sample" if args.sample is not None else "code")
    if mode == "code" and not args.code:
        return _fatal("--code 模式必须给 --code"), {}, time.time() - t0
    if args.offset < 0 or args.limit < 0:
        return _fatal("--limit/--offset 不能为负"), {}, time.time() - t0
    try:
        since = parse_date_bound(args.since, upper=False)
        until = parse_date_bound(args.until, upper=True)
    except ValueError as exc:
        return _fatal(str(exc)), {}, time.time() - t0
    if since and until and since > until:
        return _fatal("--since 晚于 --until"), {}, time.time() - t0

    # ---- 数据源：DB
    if args.db:
        db_path = Path(args.db)
        if not db_path.is_file():
            return _fatal("--db 指向的文件不存在: %s" % db_path), {}, time.time() - t0
    else:
        db_path = resolve_default_db(REPO_ROOT)
        if db_path is None:
            return _fatal("未找到 news_collector/news.db（可 --db 指定）"), {}, time.time() - t0

    # ---- 数据源：staging（可选）
    staging: Optional[dict[str, Any]] = None
    staging_meta: dict[str, Any] = {"loaded": False, "path": "", "rows": 0, "bad_lines": 0,
                                    "requested": False}
    if not args.no_staging:
        if args.staging:
            st_path: Optional[Path] = Path(args.staging)
            staging_meta["requested"] = True
            if not st_path.is_file():
                return _fatal("--staging 指向的文件不存在: %s" % st_path), {}, time.time() - t0
        else:
            st_path = resolve_default_staging(REPO_ROOT)
        if st_path is None:
            warnings.append("staging_not_found:可选数据源缺失（--staging 指定）")
        else:
            try:
                staging = load_staging(st_path)
            except OSError as exc:
                warnings.append("staging_load_failed:%s" % str(exc)[:120])
                if staging_meta["requested"]:
                    return _fatal("staging 加载失败: %s" % exc), {}, time.time() - t0
            else:
                staging_meta.update({
                    "loaded": True, "path": staging["path"], "rows": staging["num_rows"],
                    "bad_lines": len(staging["bad_lines"]),
                })
                if staging["bad_lines"]:
                    partial = True
                    warnings.append("staging_bad_lines=%d（fail-visible）" % len(staging["bad_lines"]))
                    for b in staging["bad_lines"][:MAX_FAILURE_SAMPLES]:
                        failures.append({"kind": "staging_bad_line", "reason": b["reason"],
                                         "line": b["line"]})

    # ---- 数据源：名称表（candidate 层）
    name_map_meta: dict[str, Any] = {"loaded": False, "path": "", "entries": 0}
    matcher: Optional[_ix.NameMatcher] = None
    if not args.no_name_map:
        if args.name_map:
            nm_path: Optional[Path] = Path(args.name_map)
            if not nm_path.is_file():
                return _fatal("--name-map 指向的文件不存在: %s" % nm_path), {}, time.time() - t0
        else:
            nm_path = resolve_default_name_map(REPO_ROOT)
        if nm_path is None:
            warnings.append("name_map_not_found:candidate 层不可用（fail-visible）")
            partial = True
        else:
            try:
                loaded = _ix.load_name_map(nm_path)
            except (OSError, ValueError) as exc:
                warnings.append("name_map_load_failed:%s" % str(exc)[:120])
                partial = True
            else:
                name_map_meta = {"loaded": True, "path": loaded["path"],
                                 "entries": loaded["entries"],
                                 "skipped_unsupported": loaded["skipped_unsupported"]}
                if loaded["entries"]:
                    matcher = _ix.NameMatcher(loaded["name_to_symbol"])
    else:
        partial = True
        warnings.append("candidate_off:--no-name-map（candidate 层不可用，fail-visible）")

    # ---- 打开 DB（只读）
    try:
        con = open_readonly(db_path)
    except sqlite3.Error as exc:
        return _fatal("无法只读打开 DB: %s (%s)" % (db_path, exc)), {}, time.time() - t0
    try:
        columns = inspect_columns(con)
        if not columns:
            return _fatal("news 表不存在: %s" % db_path), {}, time.time() - t0
        missing = [c for c in ("id", "title") if c not in columns]
        if missing:
            return _fatal("news 表缺少必需列: %s" % ",".join(missing)), {}, time.time() - t0
        if "source" not in columns:
            warnings.append("source_column_missing:evidence.source=null")
        if "ts" not in columns:
            warnings.append("ts_column_missing:since/until 不可用")
            if since or until:
                return _fatal("表中无 ts 列，无法按 since/until 过滤"), {}, time.time() - t0
        if "stocks" not in columns:
            warnings.append("stocks_column_missing:before_rows=0")
        if "jev" not in columns:
            warnings.append("jev_column_missing:jev_model_* 层不可用")
            partial = True

        news_total = int(con.execute("SELECT COUNT(*) FROM news").fetchone()[0])

        # ---- 单代码查询：先校验代码，构造 SQL 预筛（代码字面 + 名称表映射名）
        code_filter: Optional[dict[str, Any]] = None
        query_code: Optional[str] = None
        if mode in ("code", "sample") and args.code:
            query_code = _ix.normalize_symbol(args.code)
            if query_code is None:
                return _fatal("非法/不支持的 A 股代码: %r（口径 60/68/90→SH、00/30→SZ、"
                              "8/4→BJ；92 开头北交所新码当前不支持）" % args.code), {}, time.time() - t0
            names: list[str] = []
            if matcher:
                names = [n for n, s in matcher.name_to_symbol.items() if s == query_code]
                names = sorted(names, key=len, reverse=True)[:32]
            code_filter = {"code6": query_code[-6:], "names": names}

        index = build_index(con, columns, matcher=matcher,
                            staging_ids=set(staging["rows"]) if staging else set(),
                            since=since, until=until, code_filter=code_filter)

        # ---- staging 一致性对拍（仅 stats 模式：全量扫描才覆盖 staging 样本行；
        #      对拍仅 text_extract 口径，staging 由前序工具生成）
        staging_check = None
        if mode == "stats" and staging is not None and index["live_text"]:
            staging_check = check_staging(index, staging)
            if staging_check["exact_mismatch"] or staging_check["candidate_mismatch"] \
                    or staging_check["missing_rows"]:
                partial = True
                warnings.append("staging_mismatch:exact=%d candidate=%d missing=%d（fail-visible）" % (
                    staging_check["exact_mismatch"], staging_check["candidate_mismatch"],
                    staging_check["missing_rows"]))
                for s in staging_check["mismatch_samples"][:MAX_FAILURE_SAMPLES]:
                    failures.append({"kind": "staging_mismatch", "reason": s.get("field"),
                                     "news_id": s.get("news_id"),
                                     "staging": s.get("staging"), "live": s.get("live")})
        if staging is not None:
            annotate_staging(index["relations"], staging)

        # ---- 关系集（全量或单代码过滤）
        relations = index["relations"]
        if code_filter is not None:
            relations = [r for r in relations if r["target"] == query_code]
        exact_rels = sorted((r for r in relations if r["target_class"] == "exact"),
                            key=relation_sort_key)
        cand_rels = sorted((r for r in relations if r["target_class"] == "candidate"),
                           key=relation_sort_key)

        sources = {
            "db": {"path": str(db_path), "mode": "ro", "columns": columns},
            "staging": staging_meta,
            "name_map": name_map_meta,
        }

        result = _json_result(
            mode=mode,
            query={"code": query_code, "since": since, "until": until,
                   "limit": args.limit, "offset": args.offset, "sample": args.sample},
            sources=sources,
            stats=None, exact=None, candidate=None, sample=None,
            failures=[], warnings=[], partial_failure=False,
        )

        if mode == "stats":
            stats = build_stats(index, staging_check=staging_check,
                                name_map=name_map_meta, relation_total=len(relations))
            stats["news_total"] = news_total
            result["stats"] = stats
        elif mode == "code":
            result["exact"] = paginate(exact_rels, args.limit, args.offset)
            result["candidate"] = paginate(cand_rels, args.limit, args.offset)
        else:  # sample
            picked = stratified_sample(relations, int(args.sample))
            sample_items = []
            locatable = 0
            for rel in picked:
                verify = verify_relation(con, columns, rel)
                if verify["locatable"]:
                    locatable += 1
                item = {
                    "target": rel["target"],
                    "target_class": rel["target_class"],
                    "evidence": rel["evidence"],
                    "origins": rel["origins"],
                    "staging_confirmed": rel["staging_confirmed"],
                    "verify": verify,
                }
                sample_items.append(item)
            pct = round(locatable * 100.0 / len(picked), 2) if picked else 0.0
            result["sample"] = {
                "requested": int(args.sample),
                "returned": len(picked),
                "locatable": locatable,
                "locatable_pct": pct,
                "threshold_pct": SAMPLE_LOCATABLE_MIN_PCT,
                "items": sample_items,
            }
            if picked and pct < SAMPLE_LOCATABLE_MIN_PCT:
                partial = True
                warnings.append("sample_locatable_below_95pct:%.2f%%（fail-visible）" % pct)

        if (since or until) and index["rows_missing_ts"]:
            warnings.append("rows_missing_ts=%d（按时间过滤时已排除）" % index["rows_missing_ts"])

        # ---- 汇总失败
        if index["fail_counts"]:
            partial = True
            warnings.append("bad_rows:%s（fail-visible，不影响其余行）" % json.dumps(
                index["fail_counts"], ensure_ascii=False, sort_keys=True))
            for f in index["failures"][:MAX_FAILURE_SAMPLES]:
                failures.append(f)
        result["failures"] = failures
        result["warnings"] = warnings
        result["partial_failure"] = bool(partial)
        result["_strict"] = bool(args.strict)
        return 0, result, time.time() - t0
    finally:
        con.close()


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    rc, result, elapsed = run(args)
    if rc != 0 or not result:
        return rc
    if args.json:
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n")
    else:
        sys.stdout.write(_human_result(result, elapsed=elapsed))
    if result.get("_strict") and result.get("partial_failure"):
        sys.stderr.write("partial_failure=true（--strict → rc=3）\n")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
