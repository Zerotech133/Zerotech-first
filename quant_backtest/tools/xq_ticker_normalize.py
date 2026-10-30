# -*- coding: utf-8 -*-
"""雪球标的词形归一化（P0-1）：xq_status_classified.tickers 四种词形混装治理。

背景（2026-10-19 数据标准线拍板）：
  xq_status_classified.tickers（JSON 数组字符串）由 xq_classify 正则提取器产出，
  四种词形混装污染门户 top_codes 榜单：
    - 港股 5 位码（01810/00700）
    - A股 6 位码（600519/000838）
    - 中文指数（沪深300指数/红利指数）
    - 基金类目词（债券基金/货币基金）+ ETF 名称词（沪深300ETF）+ 场内基金码（512910）
  拍板方案：kind 标记不排除，门户按 kind 分栏；指数 canonical 对接
  xq_index_map 既有 17 条映射；基金/股票代码口径见 data/xueqiu/ticker_map.json。

设计（对齐 xq_classify「原表只增不改」红线）：
  - 唯一数据源：data/xueqiu/ticker_map.json（规则 + 映射表），本模块加载执行；
  - classify_token(token) -> {raw, kind, canonical}，纯函数无 IO；
  - normalize_tickers(raw_json_str)：整个 tickers 列 JSON 串 → list[dict]；
    空串/None → []；畸形 JSON（非数组/解析失败）→ 单条 unknown 兜底（保留现场）；
  - backfill(conn)：全表回填 norm_tickers 新列，WAL 短事务分批 commit
    （生产分类双实例在跑，遇 sqlite busy 重试 3 次指数退避）；
  - 幂等：ensure_norm_column 先查 PRAGMA table_info 列不存在才 ALTER；
    backfill 已等于新值的行跳过（零写入），重复执行零副作用。

canonical 口径（三套既有口径对接，不新造，详见 ticker_map.json notes）：
  stock_hk  → 裸 5 位码（雪球行情接口原生口径，00700）
  stock_a   → SH/SZ/BJ 前缀式（雪球行情接口口径，SH600519）
  fund 场内 → SH/SZ 前缀式（SH510300）；类目词/未收录名称词 → None
  index     → xq_index_map 后缀式（000300.SH）；未收录指数词 → None

用法：
  from xq_ticker_normalize import normalize_tickers, backfill, ensure_norm_column
  normalize_tickers('["01810","沪深300指数"]')
    -> [{"raw": "01810", "kind": "stock_hk", "canonical": "01810"},
        {"raw": "沪深300指数", "kind": "index", "canonical": "000300.SH"}]
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
MAP_PATH = REPO_ROOT / "data" / "xueqiu" / "ticker_map.json"

# xq_index_map 在仓库根，tools/ 子目录需显式挂根（对齐 tests/ 既有 sys.path 判例）
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import xq_index_map as _xim
except Exception:  # pragma: no cover - 仓库完整性依赖，正常路径必命中
    _xim = None

with open(MAP_PATH, encoding="utf-8") as _f:
    _MAP = json.load(_f)

_SEGMENTS = _MAP["code_segments"]
_FUND_NAME_CODES = dict(_MAP["fund_name_codes"])
# 名称词查找同时备一份小写键（词尾 ETF/LOF 大小写不敏感匹配后取码）
_FUND_NAME_CODES_LOWER = {k.lower(): v for k, v in _FUND_NAME_CODES.items()}
_FUND_SUFFIXES = tuple(
    s.lower() for s in _MAP.get("fund_suffixes", ("基金", "ETF", "LOF", "联接"))
)
_INDEX_SUFFIXES = tuple(_MAP.get("index_suffixes", ("指数",)))

_TABLE = "xq_status_classified"
_NORM_COL = "norm_tickers"

KINDS = ("stock_hk", "stock_a", "index", "fund", "unknown")

# kind 分布验证 SQL（一行，门户分栏/汇报用；json_each 对 NULL 行天然空集）
KIND_DIST_SQL = (
    "SELECT json_extract(je.value, '$.kind') AS kind, COUNT(*) AS n "  # nosec B608 - 值参数化/内部标识符（已抽查）
    "FROM " + _TABLE + ", json_each(" + _TABLE + "." + _NORM_COL + ") je "
    "GROUP BY kind ORDER BY n DESC"
)


# ---------------------------------------------------------------------------
# 词形判定（纯函数）
# ---------------------------------------------------------------------------


def _compress(text: str) -> str:
    """NFKC 全角→半角 + 去首尾空白。"""
    return unicodedata.normalize("NFKC", text or "").strip()


def _route_6digit(code: str):
    """6 位码段白名单路由 → (kind, canonical)。

    段表取自 ticker_map.json code_segments（与 xq_classify._CODE_SEGS 同源口径）；
    段外 6 位码（232020 编号形 / 200009 年月形等）→ ("unknown", None)。
    """
    for exch, groups in _SEGMENTS.items():
        for kind, segs in groups.items():
            for seg in segs:
                if code.startswith(seg):
                    return kind, exch + code
    return "unknown", None


def _lookup_index(token: str):
    """中文指数词 → xq_index_map 代码；未收录返回 None。

    先整词查（别名表已含「沪深300指数」等全称），未命中再去掉「指数」
    词尾再查一次（「医药指数」→「医药」仍不在表 → None，不冒造代码）。
    """
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

    判定顺序（见 ticker_map.json rules）：纯数字 → 指数词形 → 基金词形 → unknown。
    非字符串元素（LLM 产出的数字/None 等）先 str() 保真进 raw，再按同规则判。
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
        # 其余位数（金额/点位形）保持 unknown
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

    - None/空串/纯空白 → []；
    - JSON 解析失败或顶层非数组 → [单条 unknown 兜底]，raw 保留原始串现场
      （畸形数据可见、可分栏隔离，不静默丢弃）；
    - 数组内空白词形跳过（无信息量）；其余逐词 classify_token。
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


# ---------------------------------------------------------------------------
# SQLite 落库（幂等迁移 + 批量回填）
# ---------------------------------------------------------------------------


def _is_busy(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _with_retry(fn, retries: int = 3, base_delay: float = 0.5):
    """sqlite busy/locked 重试 3 次指数退避（0.5s/1s/2s）；其余异常直抛。

    生产分类双实例在跑同库，短事务 + 退避是共享仓库纪律（2026-10-19 拍板）。
    """
    for attempt in range(retries + 1):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if attempt >= retries or not _is_busy(exc):
                raise
            time.sleep(base_delay * (2**attempt))


def ensure_norm_column(conn: sqlite3.Connection, retries: int = 3) -> bool:
    """幂等加列：norm_tickers 不存在才 ALTER ADD COLUMN。返回是否新增。

    铁律「原表只增不改」：ADD COLUMN 属增；先 PRAGMA table_info 探测，
    已存在直接返回 False（重复执行零副作用）。
    """
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
    """全表回填 norm_tickers（列不存在时自动 ensure_norm_column）。

    - 读 classified 全表逐条 normalize_tickers，JSON 串落新列；
    - 每 batch_size 条写后 commit 一次（短事务，生产共享库友好）；
    - 已等于新值的行跳过（零写入）→ 重复执行零副作用、秒级；
    - 返回 {"total", "updated", "skipped", "kinds"}（kinds 为词形计数，
      口径=每行数组元素逐个计数，与 KIND_DIST_SQL 的 json_each 口径一致）。
    """
    ensure_norm_column(conn, retries=retries)
    rows = conn.execute(
        "SELECT status_id, tickers, %s FROM %s" % (_NORM_COL, _TABLE)  # nosec B608 - 值参数化/内部标识符（已抽查）
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
                "UPDATE %s SET %s=? WHERE status_id=?" % (_TABLE, _NORM_COL), (p, sid)  # nosec B608 - 值参数化/内部标识符（已抽查）
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
