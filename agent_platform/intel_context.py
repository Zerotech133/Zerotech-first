# -*- coding: utf-8 -*-
"""N1 情报上下文：reviewed JEV → news_event（2026-10-31 WO-INTEL-N1AB / task-13）。

定位：`make_context()["news_event"]` 的**可选**证据源（A/B 默认关）。
  · flag：环境变量 `INTEL_TO_N1`（未设置 / 0 / off = 旧行为 price_proxy）；
  · 开启后只读 `news_collector/news.db` 的 `jev_review_queue` 中
    `status='resolved'` 的记录，按股票 code 过滤，生成带来源标签的 news_event 文本：
    `source=reviewed_jev` + `event_id` / `review_id`（review_id 由 id+reviewer+
    reviewed_at 派生，表内无独立复核 id 列）；
  · 无可用记录 → 返回 `source=price_proxy` + `reason`（调用方保留旧情绪代理文本，
    不伪造“已核验”）；DB 缺失/损坏/队列缺表同样返回 price_proxy + reason；
  · 坏行（JSON 坏、缺 id、结构未知）**不抛出**：跳过并收入 `bad_rows`（fail-visible）。

只读纪律：SQLite 一律 `file:...?mode=ro`；不建表、不写入、不触发回填。
A/B 设计/指标/回滚见 `docs/audit/2026-10-31_INTEL_TO_N1_AB_v1.md`。

CLI（A/B 采样/探针）：
    python -m agent_platform.intel_context --code 600519 --json
    INTEL_N1_DB=<tmp.db> python -m agent_platform.intel_context --code 600519
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

FLAG_ENV = "INTEL_TO_N1"
DB_ENV = "INTEL_N1_DB"
LIMIT_ENV = "INTEL_N1_LIMIT"
NAMES_FILE_ENV = "INTEL_N1_STOCK_NAMES"

ON_VALUES = ("1", "true", "yes", "on", "y")
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
MAX_SCAN = 500                      # 只扫最近复核的 N 条（review 产能 200/日量级）
SUMMARY_CHARS = 120                 # 每条事件摘要截断（prompt 防膨胀）
TEXT_ITEMS = 5                      # 文本内最多展示条数

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "news_collector" / "news.db"
WATCHLIST_PATH = REPO_ROOT / "news_collector" / "news_config.json"
NAME_FILE_CANDIDATES = (
    REPO_ROOT / "quant_backtest" / "data" / "stock_names.json",
    REPO_ROOT / "agent_platform" / "data" / "stock_names.json",
)
REJECTED_DECISIONS = frozenset({"wrong", "reject", "rejected", "invalid", "false"})
USABLE_DECISIONS = frozenset({"", "correct", "accept", "accepted", "uncertain",
                              "needs_evidence", "ok", "yes"})
# 2026-10-31 N1 AI 初筛（WO-INTEL-AI1ST-20261031）：review_type 分类，不伪装人工。
REVIEW_TYPE_HUMAN = "human"
REVIEW_TYPE_AI_FIRST_PASS = "ai_first_pass"
_BIAS_CN = {"bull": "看多", "bear": "看空", "neutral": "中性"}
_SQL_COLS = ("id", "queued_at", "conf", "bias", "title", "stocks",
             "original_jev", "status", "reviewer", "reviewed_at",
             "decision", "note", "review_type")
_REQUIRED_COLS = ("id", "status")


def _fetch_resolved(con: sqlite3.Connection, max_scan: int):
    """读最近 resolved 行；列集合按实际 schema 动态适配（兼容旧/最小表结构）。

    返回 (rows_as_dicts, order_col) ；表缺失抛 sqlite3.OperationalError。
    """
    info = con.execute("PRAGMA table_info(jev_review_queue)").fetchall()
    cols = [str(r[1]) for r in info if len(r) > 1]
    if not cols:
        raise sqlite3.OperationalError("no such table: jev_review_queue")
    if any(c not in cols for c in _REQUIRED_COLS):
        raise sqlite3.OperationalError(
            "no such column: 缺少必填列 id/status（实际列=%s）" % ",".join(cols))
    select_cols = [c for c in _SQL_COLS if c in cols]
    order = "reviewed_at" if "reviewed_at" in cols else (
        "queued_at" if "queued_at" in cols else "id")
    sql = ("SELECT %s FROM jev_review_queue WHERE status='resolved' "
           "ORDER BY %s DESC LIMIT ?" % (",".join(select_cols), order))
    rows = con.execute(sql, (max(1, int(max_scan)),)).fetchall()
    out = []
    for raw in rows:
        row = {c: None for c in _SQL_COLS}
        row.update(dict(zip(select_cols, raw)))
        out.append(row)
    return out

log = logging.getLogger("intel_context")
_NAME_CACHE: dict = {"key": None, "names": set(), "code2name": {}}


def enabled(env: dict | None = None) -> bool:
    """`INTEL_TO_N1` 是否开启（默认 off）。"""
    env = os.environ if env is None else env
    return str(env.get(FLAG_ENV) or "").strip().lower() in ON_VALUES


def default_db_path() -> Path:
    raw = str(os.environ.get(DB_ENV) or "").strip()
    return Path(raw) if raw else DEFAULT_DB


# ---------------------------------------------------------------- 规范化
def norm_code(raw) -> str:
    """宽松提 6 位股票代码（兼容 600519 / sh600519 / 600519.SH / '600519'）。"""
    text = str(raw or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.split(".", 1)[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:6] if len(digits) >= 6 else ""


def _suffix(code6: str) -> str:
    if code6.startswith(("60", "68", "90")):
        return ".SH"
    if code6.startswith(("00", "30", "20")):
        return ".SZ"
    if code6.startswith(("43", "83", "87", "92")):
        return ".BJ"
    return ""


# ---------------------------------------------------------------- 名称映射（可选）
def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("name map 读取失败（忽略）：%s: %s", path, exc)
        return None


def _watchlist_names() -> dict:
    """news_config.watchlist → {norm_code6: set(名称/别名)}（文件缺失/坏不阻塞）。"""
    cfg = _read_json(WATCHLIST_PATH)
    out: dict = {}
    if not isinstance(cfg, dict):
        return out
    for item in cfg.get("watchlist") or []:
        if not isinstance(item, dict):
            continue
        code6 = norm_code(item.get("code"))
        if not code6:
            continue
        bucket = out.setdefault(code6, set())
        for term in [item.get("name")] + list(item.get("aliases") or []):
            term = str(term or "").strip()
            if len(term) >= 2:
                bucket.add(term)
    return out


def _stock_name_file() -> Path | None:
    override = str(os.environ.get(NAMES_FILE_ENV) or "").strip()
    if override:
        return Path(override)
    for cand in NAME_FILE_CANDIDATES:
        if cand.is_file():
            return cand
    return None


def _load_name_map():
    """→ (code6 → names set, code6 → 现用名)。带 mtime 缓存；失败空映射。"""
    path = _stock_name_file()
    key = None
    if path is not None:
        try:
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
    if _NAME_CACHE["key"] == key and key is not None:
        return _NAME_CACHE["names"], _NAME_CACHE["code2name"]
    names: dict = {}
    code2name: dict = {}
    if path is not None and key is not None:
        data = _read_json(path)
        if isinstance(data, dict):
            raw_c2n = data.get("code2name") or {}
            for code, name in raw_c2n.items():
                code6 = norm_code(code)
                name = str(name or "").strip()
                if not code6 or len(name) < 2:
                    continue
                code2name.setdefault(code6, name)
                names.setdefault(code6, set()).add(name)
            for name, code in (data.get("name2code") or {}).items():
                code6 = norm_code(code)
                name = str(name or "").strip()
                if code6 and len(name) >= 2:
                    names.setdefault(code6, set()).add(name)
    for code6, bucket in _watchlist_names().items():
        names.setdefault(code6, set()).update(bucket)
    _NAME_CACHE.update(key=key, names=names, code2name=code2name)
    return names, code2name


# ---------------------------------------------------------------- 归属匹配
def _entry_hits(entry, code6: str, names: set) -> bool:
    """stocks / companies 单条目是否归属 code（兼容 str / 6 位 / ts_code / dict）。"""
    if isinstance(entry, (int, float)):
        entry = str(int(entry)) if float(entry).is_integer() else str(entry)
    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            return False
        if norm_code(text) == code6:
            return True
        return text in names
    if isinstance(entry, dict):
        for key in ("code", "symbol", "ts_code", "stock_code", "sec_code",
                    "name", "公司", "股票", "股票名称", "简称"):
            if key in entry and _entry_hits(entry.get(key), code6, names):
                return True
        return False
    if isinstance(entry, (list, tuple)):
        return any(_entry_hits(x, code6, names) for x in entry)
    return False


def _title_hits(title: str, code6: str, names: set) -> bool:
    text = str(title or "")
    for name in names:
        if name and name in text:
            return True
    # 6 位代码独立 token（避免 1600519 / 长数字串误命中）
    start = 0
    while True:
        idx = text.find(code6, start)
        if idx < 0:
            return False
        before = text[idx - 1] if idx > 0 else ""
        after = text[idx + len(code6)] if idx + len(code6) < len(text) else ""
        if not before.isdigit() and not after.isdigit():
            return True
        start = idx + 1


def _row_match(code6: str, names: set, row: dict) -> list:
    """返回命中通道列表；不抛（坏字段只影响对应通道）。"""
    matched = []
    raw_stocks = row.get("stocks")
    stocks, _err = _parse_json(raw_stocks)
    if isinstance(stocks, list) and any(_entry_hits(x, code6, names)
                                        for x in stocks):
        matched.append("stocks")
    jev, _err2 = _parse_json(row.get("original_jev"))
    if isinstance(jev, dict):
        companies = jev.get("companies")
        if isinstance(companies, list) and any(
                _entry_hits(x, code6, names) for x in companies):
            matched.append("companies")
    if _title_hits(row.get("title"), code6, names):
        matched.append("title")
    return matched


def _parse_json(raw):
    if raw in (None, ""):
        return None, None
    try:
        return json.loads(raw), None
    except (TypeError, ValueError):
        return None, "bad_json"


# ---------------------------------------------------------------- 复核 id / 渲染
def review_id(row: dict) -> str:
    """由 (事件 id, reviewer, reviewed_at) 派生稳定复核 id（表内无独立 id 列）。"""
    seed = "%s|%s|%s" % (row.get("id") or "", row.get("reviewer") or "",
                         row.get("reviewed_at") or row.get("queued_at") or "")
    return "rv-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _review_type(row: dict) -> str:
    """review_type 归一：ai_first_pass 保持；其余（含旧库 '' / 无列）视为 human。

    历史 resolved 行只可能来自人工 resolve（AI 初筛是 2026-10-31 新增档位），
    因此 ''→human 不伪造 AI 档；ai_first_pass 明确透出，绝不伪装人工。
    """
    raw = str(row.get("review_type") or "").strip().lower()
    return REVIEW_TYPE_AI_FIRST_PASS if raw == REVIEW_TYPE_AI_FIRST_PASS \
        else REVIEW_TYPE_HUMAN


def _render(code6: str, display_name: str, items: list,
            review_type_counts: dict | None = None) -> str:
    counts = review_type_counts or {}
    ai_n = int(counts.get(REVIEW_TYPE_AI_FIRST_PASS) or 0)
    human_n = int(counts.get(REVIEW_TYPE_HUMAN) or 0)
    if ai_n:
        lines = ["[reviewed_jev] reviewed 情报（source=reviewed_jev；"
                 "含 AI 初筛 %d 条 review_type=ai_first_pass，非人工复核；"
                 "人工 %d 条 review_type=human）" % (ai_n, human_n)]
    else:
        lines = ["[reviewed_jev] 人工复核情报（source=reviewed_jev；"
                 "只含 status=resolved 记录，全部 review_type=human）"]
    label = ("%s %s" % (display_name, code6)).strip() if display_name else code6
    lines.append("标的：%s｜可用复核事件 %d 条" % (label, len(items)))
    for idx, item in enumerate(items, 1):
        conf = item.get("conf")
        conf_s = "?" if conf is None else ("%.2f" % float(conf))
        rt = item.get("review_type") or REVIEW_TYPE_HUMAN
        lines.append(
            "%d) bias=%s 置信=%s 复核=%s 复核人=%s 复核时间=%s review_type=%s｜%s"
            % (idx, item.get("bias_cn") or item.get("bias") or "?",
               conf_s, item.get("decision") or "resolved",
               item.get("reviewer") or "-",
               item.get("reviewed_at") or "-", rt,
               item.get("title") or "(无标题)"))
        if rt == REVIEW_TYPE_AI_FIRST_PASS:
            lines.append("   [AI 初筛] review_type=ai_first_pass（机器映射，非人工复核）")
        lines.append("   event_id=%s review_id=%s"
                     % (item.get("event_id"), item.get("review_id")))
        if item.get("summary"):
            lines.append("   事件摘要：%s" % item.get("summary"))
        if item.get("note"):
            lines.append("   复核备注：%s" % item.get("note"))
    if ai_n:
        lines.append("（说明：以上为已 resolved 历史情报，其中 %d 条为 AI 初筛"
                     "（review_type=ai_first_pass，机器映射），不等同人工复核；"
                     "不代表未来走势。）" % ai_n)
    else:
        lines.append("（说明：以上为人工复核过的历史情报，不含未复核事件，"
                     "不代表未来走势。）")
    return "\n".join(lines[:2 + len(items) * 5 + 1])


# ---------------------------------------------------------------- 主入口
def reviewed_news_event(code, db_path=None, limit=None, names=None,
                        max_scan: int = MAX_SCAN) -> dict:
    """按 code 生成 reviewed JEV 版 news_event；无可用数据返回 price_proxy + reason。

    返回 dict（恒不抛）：
      {source, reason, text, code, event_ids, review_ids, items, bad_rows,
       rejected, counts, db}
    source=reviewed_jev 时 text 含 `source=reviewed_jev` 与 event_id/review_id；
    source=price_proxy 时 text 为空串，reason 说明原因（调用方保留旧文）。
    """
    code6 = norm_code(code)
    out = {
        "source": "price_proxy",
        "reason": "",
        "text": "",
        "code": code6,
        "event_ids": [],
        "review_ids": [],
        "items": [],
        "bad_rows": [],
        "errors": [],
        "rejected": 0,
        "counts": {"scanned": 0, "matched": 0, "usable": 0},
        "review_type_counts": {REVIEW_TYPE_HUMAN: 0,
                               REVIEW_TYPE_AI_FIRST_PASS: 0},
        "db": str(db_path or default_db_path()),
    }
    if not code6:
        out["reason"] = "bad_code"
        return out
    path = Path(db_path or default_db_path())
    out["db"] = str(path)
    if not path.is_file():
        out["reason"] = "news_db_missing"
        out["errors"].append({"error": "news_db_missing", "db": str(path)})
        log.warning("INTEL_TO_N1 news.db 不存在：%s", path)
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % str(path).replace("\\", "/"),
                              uri=True, timeout=5)
    except sqlite3.Error as exc:
        out["reason"] = "db_error"
        out["errors"].append({"error": "db_error", "detail": str(exc)[:160]})
        log.warning("INTEL_TO_N1 news.db 打开失败：%s", exc)
        return out
    try:
        try:
            rows = _fetch_resolved(con, max_scan)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "no such table" in msg:
                reason = "queue_missing"
            elif "no such column" in msg:
                reason = "queue_schema_invalid"
            else:
                reason = "db_error"
            out["reason"] = reason
            out["errors"].append({"error": reason, "detail": str(exc)[:160]})
            log.warning("INTEL_TO_N1 复核队列表读取失败（%s）：%s", reason, exc)
            return out
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass
    out["counts"]["scanned"] = len(rows)

    if names is None:
        name_map, code2name = _load_name_map()
        names_set = name_map.get(code6) or set()
        display_name = code2name.get(code6) or ""
    else:
        names_set = {str(x).strip() for x in names if str(x).strip()}
        display_name = ""
    try:
        want = int(limit if limit is not None else os.environ.get(LIMIT_ENV)
                   or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        want = DEFAULT_LIMIT
    want = max(1, min(MAX_LIMIT, want))

    matched = usable = 0
    for row in rows:
        row_id = str(row.get("id") or "").strip()
        jev, jev_err = _parse_json(row.get("original_jev"))
        if jev_err:
            out["bad_rows"].append({"id": row_id, "error": "bad_original_jev"})
            continue
        if not isinstance(jev, dict) and row.get("original_jev") not in ("", None):
            out["bad_rows"].append({"id": row_id, "error": "bad_original_jev_type"})
            continue
        if not row_id:
            out["bad_rows"].append({"id": "", "error": "missing_id"})
            continue
        hits = _row_match(code6, names_set, row)
        if not hits:
            continue
        matched += 1
        out["counts"]["matched"] = matched
        decision = str(row.get("decision") or "").strip().lower()
        if decision in REJECTED_DECISIONS:
            out["rejected"] += 1
            continue
        jev = jev if isinstance(jev, dict) else {}
        conf = row.get("conf")
        if conf is None:
            conf = jev.get("conf")
        item = {
            "event_id": row_id,
            "review_id": review_id(row),
            "title": _clean(row.get("title"), 160),
            "bias": str(row.get("bias") or jev.get("bias") or ""),
            "bias_cn": _BIAS_CN.get(str(row.get("bias") or jev.get("bias") or ""), ""),
            "conf": conf,
            "decision": decision or "resolved",
            "reviewer": str(row.get("reviewer") or ""),
            "reviewed_at": str(row.get("reviewed_at") or row.get("queued_at") or ""),
            "note": _clean(row.get("note"), 80),
            "summary": _clean(jev.get("reason"), SUMMARY_CHARS),
            "event_type": str(jev.get("event_type") or ""),
            "review_type": _review_type(row),
            "matched_by": hits,
        }
        # 坏 stocks 但 title/companies 命中：仍可用，但坏字段 fail-visible
        _stocks, stocks_err = _parse_json(row.get("stocks"))
        if stocks_err:
            out["bad_rows"].append({"id": row_id, "error": "bad_stocks_json"})
        usable += 1
        out["counts"]["usable"] = usable
        rt = item["review_type"]
        out["review_type_counts"][rt] = out["review_type_counts"].get(rt, 0) + 1
        if usable <= want:
            out["items"].append(item)

    if not usable:
        if matched:
            out["reason"] = ("only_rejected_reviews"
                             if out["rejected"] else "reviewed_unusable")
        else:
            out["reason"] = "no_reviewed_jev"
        if out["bad_rows"]:
            log.warning("INTEL_TO_N1 code=%s 坏行 %d 条（skip，fail-visible）：%s",
                        code6, len(out["bad_rows"]),
                        ", ".join(x.get("id") or "?" for x in out["bad_rows"][:5]))
        return out
    out["source"] = "reviewed_jev"
    out["reason"] = ""
    out["text"] = _render(code6, display_name, out["items"],
                          out["review_type_counts"])
    out["event_ids"] = [x["event_id"] for x in out["items"]]
    out["review_ids"] = [x["review_id"] for x in out["items"]]
    log.info("INTEL_TO_N1 code=%s source=reviewed_jev items=%d rejected=%d bad=%d",
             code6, len(out["items"]), out["rejected"], len(out["bad_rows"]))
    return out


# 别名（可读性/探针脚本用）
news_event_for_code = reviewed_news_event


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="INTEL_TO_N1 reviewed JEV 探针（只读）")
    ap.add_argument("--code", required=True, help="6 位股票代码或 ts_code")
    ap.add_argument("--db", default="", help="news.db 路径（默认 INTEL_N1_DB/仓内 news.db）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--json", action="store_true", help="输出完整 JSON（默认只出摘要）")
    args = ap.parse_args(argv)
    started = time.time()
    res = reviewed_news_event(args.code, db_path=args.db or None,
                              limit=args.limit)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(json.dumps({
            "code": res.get("code"), "source": res.get("source"),
            "reason": res.get("reason"),
            "event_ids": res.get("event_ids"),
            "review_ids": res.get("review_ids"),
            "bad_rows": len(res.get("bad_rows") or []),
            "elapsed_ms": int((time.time() - started) * 1000),
        }, ensure_ascii=False))
    return 0 if res.get("source") == "reviewed_jev" else 0


if __name__ == "__main__":
    sys.exit(main())
