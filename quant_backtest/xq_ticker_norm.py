# -*- coding: utf-8 -*-
r"""雪球标的归一化 · 单一真源（D① 合一：xq_ticker_norm + tools/xq_ticker_normalize）。

两大职责（原两模块互补，非重复）：
  A. 格式互转（原 xq_ticker_norm.py，WO-QT-20261020-079）：
     雪球前缀式 canonical（SZ300750）↔ 下游后缀式（300750.SZ）双轨互转；
     纯函数、零 IO；港股/美股/指数后缀不支持互转 → None，不造数。
  B. 词形分类 + canonical 映射（原 tools/xq_ticker_normalize.py，P0-1）：
     xq_status_classified.tickers 四种词形（港股5位/A股6位/中文指数/基金词）
     → {raw, kind, canonical}；唯一数据源 data/xueqiu/ticker_map.json；
     附幂等落库（ensure_norm_column / backfill，WAL 短事务+busy 退避）。

兼容：tools/xq_ticker_normalize.py 已降级为薄 shim（re-export），四个既有
引用方（xq_classify / xq_daily_report / 两个迁移工具 / 测试）零改动。

用法：
  norm_to_suffix("SZ300750")            -> "300750.SZ"
  norm_from_suffix("600519.SH")         -> "SH600519"
  normalize_tickers('["01810","沪深300指数"]')
    -> [{"raw":"01810","kind":"stock_hk","canonical":"01810"},
        {"raw":"沪深300指数","kind":"index","canonical":"000300.SH"}]
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable, List, Tuple

HERE = Path(__file__).resolve().parent          # quant_backtest/
MAP_PATH = HERE / "data" / "xueqiu" / "ticker_map.json"

# ══════════════════════════════════════════════════════════════════
# A. 前缀式 ↔ 后缀式 格式互转（原 xq_ticker_norm.py，纯函数）
# ══════════════════════════════════════════════════════════════════

__all__ = [
    # A. 格式互转
    "norm_to_suffix",
    "norm_from_suffix",
    "norm_to_suffix_many",
    "norm_from_suffix_many",
    # B. 词形分类 + 落库
    "classify_token",
    "normalize_tickers",
    "ensure_norm_column",
    "backfill",
    "KIND_DIST_SQL",
    "KINDS",
]

# A 股三交易所：沪 SH / 深 SZ / 北 BJ（北交所 6 位码 82xxxx/83xxxx/87xxxx 等）
_EXCH = r"(?:SH|SZ|BJ)"
_PREFIX_RE = re.compile(r"^(%s)(\d{6})$" % _EXCH)
_SUFFIX_RE = re.compile(r"^(\d{6})\.(%s)$" % _EXCH)


def _to_text(value) -> str:
    """任意输入 → 规整文本（None→''；非 str 先 str()；upper+去首尾空白）。"""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.strip().upper()


def norm_to_suffix(value) -> str | None:
    """前缀式 canonical → 后缀式代码；不支持/畸形 → None（幂等）。"""
    text = _to_text(value)
    if not text:
        return None
    m = _PREFIX_RE.match(text)
    if m:
        return "%s.%s" % (m.group(2), m.group(1))
    if _SUFFIX_RE.match(text):
        return text
    return None


def norm_from_suffix(value) -> str | None:
    """后缀式代码 → 前缀式 canonical；不支持/畸形 → None（幂等）。"""
    text = _to_text(value)
    if not text:
        return None
    m = _SUFFIX_RE.match(text)
    if m:
        return "%s%s" % (m.group(2), m.group(1))
    if _PREFIX_RE.match(text):
        return text
    return None


def norm_to_suffix_many(values: Iterable) -> Tuple[List[str | None], int]:
    """批量前缀式→后缀式。返回 (逐条结果, invalid 计数)，不抛异常。"""
    out: List[str | None] = []
    invalid = 0
    for v in values:
        r = norm_to_suffix(v)
        out.append(r)
        if r is None:
            invalid += 1
    return out, invalid


def norm_from_suffix_many(values: Iterable) -> Tuple[List[str | None], int]:
    """批量后缀式→前缀式。返回 (逐条结果, invalid 计数)，语义同上。"""
    out: List[str | None] = []
    invalid = 0
    for v in values:
        r = norm_from_suffix(v)
        out.append(r)
        if r is None:
            invalid += 1
    return out, invalid


# ══════════════════════════════════════════════════════════════════
# B. 词形分类 + canonical 映射 + 幂等落库（原 tools/xq_ticker_normalize.py）
# ══════════════════════════════════════════════════════════════════

# xq_index_map 在仓库根（本模块已在 quant_backtest/ 根，天然可达）
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

try:
    import xq_index_map as _xim
except Exception:  # pragma: no cover - 仓库完整性依赖，正常路径必命中
    _xim = None

with open(MAP_PATH, encoding="utf-8") as _f:
    _MAP = json.load(_f)

_SEGMENTS = _MAP["code_segments"]
_FUND_NAME_CODES = dict(_MAP["fund_name_codes"])
_FUND_NAME_CODES_LOWER = {k.lower(): v for k, v in _FUND_NAME_CODES.items()}
_FUND_SUFFIXES = tuple(
    s.lower() for s in _MAP.get("fund_suffixes", ("基金", "ETF", "LOF", "联接"))
)
_INDEX_SUFFIXES = tuple(_MAP.get("index_suffixes", ("指数",)))

_TABLE = "xq_status_classified"
_NORM_COL = "norm_tickers"

KINDS = ("stock_hk", "stock_a", "index", "fund", "unknown")

KIND_DIST_SQL = (
    "SELECT json_extract(je.value, '$.kind') AS kind, COUNT(*) AS n "
    "FROM " + _TABLE + ", json_each(" + _TABLE + "." + _NORM_COL + ") je "
    "GROUP BY kind ORDER BY n DESC"
)


def _compress(text: str) -> str:
    """NFKC 全角→半角 + 去首尾空白。"""
    return unicodedata.normalize("NFKC", text or "").strip()


def _route_6digit(code: str):
    """6 位码段白名单路由 → (kind, canonical)；段外 → ("unknown", None)。"""
    for exch, groups in _SEGMENTS.items():
        for kind, segs in groups.items():
            for seg in segs:
                if code.startswith(seg):
                    return kind, exch + code
    return "unknown", None


def _lookup_index(token: str):
    """中文指数词 → xq_index_map 代码；未收录返回 None（不冒造代码）。"""
    if _xim is None:
        return None
    code = _xim.lookup_index(token)
    if code:
        return code
    for suf in _INDEX_SUFFIXES:
        if token.endswith(suf) and len(token) > len(suf):
            code = _xim.lookup_index(token[: -len(suf)])
            if code:
                return code
    return None


def classify_token(token) -> dict:
    """单词形分类 → {raw, kind, canonical}。

    判定顺序：纯数字 → 指数词形 → 基金词形 → unknown。
    """
    raw = token if isinstance(token, str) else str(token)
    t = _compress(raw)
    entry = {"raw": raw, "kind": "unknown", "canonical": None}
    if not t:
        return entry
    if t.isdigit():
        if len(t) == 5:
            entry["kind"], entry["canonical"] = "stock_hk", t
        elif len(t) == 6:
            entry["kind"], entry["canonical"] = _route_6digit(t)
        return entry
    if t.endswith(_INDEX_SUFFIXES) or (
        _xim is not None and _xim.normalize_index_name(t) is not None
    ):
        entry["kind"] = "index"
        entry["canonical"] = _lookup_index(t)
        return entry
    low = t.lower()
    if low.endswith(_FUND_SUFFIXES):
        entry["kind"] = "fund"
        entry["canonical"] = _FUND_NAME_CODES.get(t) or _FUND_NAME_CODES_LOWER.get(low)
        return entry
    return entry


def normalize_tickers(raw_json_str) -> list:
    """tickers 列原始 JSON 串 → [{raw, kind, canonical}, ...]，保序不去重。

    - None/空串 → []；畸形 JSON/非数组 → 单条 unknown 兜底（保留现场）；
    - 数组内空白词形跳过；其余逐词 classify_token。
    """
    if raw_json_str is None:
        return []
    s = str(raw_json_str).strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
    except (ValueError, TypeError):
        return [{"raw": raw_json_str, "kind": "unknown", "canonical": None}]
    if not isinstance(parsed, list):
        return [{"raw": raw_json_str, "kind": "unknown", "canonical": None}]
    out = []
    for el in parsed:
        if isinstance(el, str) and not el.strip():
            continue
        out.append(classify_token(el))
    return out


# ── SQLite 落库（幂等迁移 + 批量回填） ────────────────────────

def _is_busy(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _with_retry(fn, retries: int = 3, base_delay: float = 0.5):
    """sqlite busy/locked 重试 3 次指数退避；其余异常直抛（共享库纪律）。"""
    for attempt in range(retries + 1):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if attempt >= retries or not _is_busy(exc):
                raise
            time.sleep(base_delay * (2**attempt))


def ensure_norm_column(conn: sqlite3.Connection, retries: int = 3) -> bool:
    """幂等加列：norm_tickers 不存在才 ALTER ADD COLUMN。返回是否新增。"""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % _TABLE)]
    if _NORM_COL in cols:
        return False
    _with_retry(
        lambda: conn.execute("ALTER TABLE %s ADD COLUMN %s TEXT" % (_TABLE, _NORM_COL)),
        retries=retries,
    )
    conn.commit()
    return True


def backfill(conn: sqlite3.Connection, batch_size: int = 200, retries: int = 3) -> dict:
    """全表回填 norm_tickers（列不存在时自动 ensure）。

    已等于新值的行跳过（零写入）→ 重复执行零副作用；
    返回 {"total", "updated", "skipped", "kinds"}。
    """
    ensure_norm_column(conn, retries=retries)
    rows = conn.execute(
        "SELECT status_id, tickers, %s FROM %s" % (_NORM_COL, _TABLE)
    ).fetchall()
    total = updated = skipped = 0
    pending = 0
    kinds: Counter = Counter()
    for status_id, tickers_raw, norm_existing in rows:
        total += 1
        items = normalize_tickers(tickers_raw)
        for it in items:
            kinds[it["kind"]] += 1
        payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        if norm_existing == payload:
            skipped += 1
            continue
        _with_retry(
            lambda sid=status_id, p=payload: conn.execute(
                "UPDATE %s SET %s=? WHERE status_id=?" % (_TABLE, _NORM_COL), (p, sid)
            ),
            retries=retries,
        )
        updated += 1
        pending += 1
        if pending >= batch_size:
            conn.commit()
            pending = 0
    if pending:
        conn.commit()
    return {
        "total": total,
        "updated": updated,
        "skipped": skipped,
        "kinds": dict(kinds),
    }
