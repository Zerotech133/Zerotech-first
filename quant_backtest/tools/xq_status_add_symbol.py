# -*- coding: utf-8 -*-
"""xq_status 补 symbol 列幂等迁移工具（WO-XQ，设计+工具，不代跑生产）。

背景：
  xq_status（雪球分类帖流，pk=status_id）无 symbol 列，导致雪球 054
  消歧只能三级模糊回退、情报中枢关联上限 4.6%。本工具补一列确定性
  symbol，让两条链路可直连。

做什么：
  1. ALTER TABLE xq_status ADD COLUMN symbol TEXT（先 PRAGMA table_info
     探测，列已存在则跳过并打印 "already"）——原表只增不改；
  2. 回填策略（只做确定性映射，不能确定的留 NULL 并统计打印）：
     a. $XXX$ 记号：title/desc 中的 $名称(代码)$ / $代码$ 记号，
        代码括号带 SH/SZ/BJ 前缀 → 前缀式；5 位数字 → 港股裸码；
        6 位数字走 xq_ticker_normalize 段表路由。一行只含一个唯一
        canonical 才回填，多 symbol 歧义行留 NULL（单列无法表达多值，
        歧义留给消费方走原消歧链）；
     b. 已有关联表兜底：无记号或记号不可映射的行，查
        xq_status_classified.norm_tickers（缺列时回退 tickers 归一化），
        唯一 canonical 才回填，同样留 NULL 否则；
  3. 幂等：列存在跳过 ALTER；已等于新值的行跳过 UPDATE（零写入）。

安全纪律：
  - --dry-run 默认开：只读连接（mode=ro URI），打印将执行的 SQL 与
    预计回填数，不执行任何写；--execute 才真跑；
  - execute 走 WAL + busy_timeout 30s + busy 重试（生产分类双实例
    在跑同库，对齐 xq_norm_migrate 生产纪律）；每批 commit 短事务；
  - 无任何密钥/凭据；缺表/缺列显式报错不补建。

数据库路径来源（实读 build_catalog.py:43 XQ_INFO_DB）：
  DEFAULT_DB = <repo>/data/xueqiu/xueqiu_info.db（本文件位于
  quant_backtest/tools/，REPO_ROOT=quant_backtest/）。
  不符时用 --db 覆盖。

用法：
  python tools/xq_status_add_symbol.py                    # dry-run（默认）
  python tools/xq_status_add_symbol.py --execute          # 真跑迁移+回填
  python tools/xq_status_add_symbol.py --db <path>        # 指定库
  python tools/xq_status_add_symbol.py --verify           # 只读验证分布
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import xq_ticker_normalize as xtn  # noqa: E402
import logging

# 实读自 build_catalog.py:43 XQ_INFO_DB（data/xueqiu/xueqiu_info.db）
DEFAULT_DB = REPO_ROOT / "data" / "xueqiu" / "xueqiu_info.db"

TABLE = "xq_status"
SYMBOL_COL = "symbol"
CLASSIFIED_TABLE = "xq_status_classified"
BATCH_SIZE = 200

# $..$ 记号（雪球正文标的记号，如 $融创中国(01918)$ / $00700$）
MARKER_RE = re.compile(r"\$([^$\n]{1,60}?)\$")
# 记号内代码括号：名称(前缀码)，前缀可空/SH/SZ/BJ/其他字母
CODE_PAREN_RE = re.compile(r"\(([A-Za-z]{0,2})(\d{5,6})\)")
_BARE_5 = re.compile(r"\d{5}$")
_BARE_6 = re.compile(r"\d{6}$")

# 只读验证 SQL（一行，迁移后分布汇报用）
SYMBOL_DIST_SQL = (
    "SELECT CASE WHEN " + SYMBOL_COL + " IS NULL THEN 'NULL' "  # nosec B608 - 值参数化/内部标识符（已抽查）
    "WHEN " + SYMBOL_COL + " GLOB '[0-9][0-9][0-9][0-9][0-9]' "
    "  THEN 'hk_bare' ELSE 'prefixed' END AS bucket, COUNT(*) AS n "
    "FROM " + TABLE + " GROUP BY bucket"
)


# ---------------------------------------------------------------------------
# 确定性 symbol 推导（纯函数）
# ---------------------------------------------------------------------------


def _canonical_from_digits(digits: str):
    """无前缀数字码 → canonical；不可映射返回 None。

    5 位 → 港股裸码；6 位 → xq_ticker_normalize 段表路由
    （stock_a/fund 可映射，段外 unknown 不冒造）。
    """
    if _BARE_5.fullmatch(digits):
        return digits
    if _BARE_6.fullmatch(digits):
        entry = xtn.classify_token(digits)
        if entry["kind"] in ("stock_a", "fund") and entry["canonical"]:
            return entry["canonical"]
    return None


def symbol_from_marker_text(text) -> list:
    """title/desc 文本 → 按出现序去重的 canonical 列表。

    只有能确定性映射的记号进列表；名称词/美股词/未收录词不猜。
    """
    if not text:
        return []
    syms = []
    for m in MARKER_RE.finditer(text):
        inner = m.group(1)
        c = None
        pm = CODE_PAREN_RE.search(inner)
        if pm:
            pre, digits = pm.group(1).upper(), pm.group(2)
            if pre:
                # 带字母前缀：只认本仓库 canonical 词表内的 SH/SZ/BJ
                c = (pre + digits) if pre in ("SH", "SZ", "BJ") else None
            else:
                c = _canonical_from_digits(digits)
        else:
            t = xtn._compress(inner) if hasattr(xtn, "_compress") else inner.strip()
            if t.isdigit():
                c = _canonical_from_digits(t)
        if c and c not in syms:
            syms.append(c)
    return syms


def _load_norm_items(norm_json) -> list:
    """norm_tickers 串 → [{raw, kind, canonical}, ...]。

    - None/空串/纯空白/[] → []；
    - 合法 JSON 数组 → 原样对象列表（容忍缺键，get 取值）；
    - 畸形/非数组 → []（不猜，消费方有原始 tickers 可自查）。
    """
    if norm_json is None:
        return []
    s = str(norm_json).strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return parsed


def symbol_from_classified(norm_json) -> list:
    """xq_status_classified.norm_tickers JSON → 去重 canonical 列表。

    norm_tickers 是 [{raw, kind, canonical}] 对象数组（不是原始词形
    数组），直接 json.loads 解析；畸形串走 normalize_tickers 的
    unknown 兜底（kind 不可信自然滤除，保底不冒造）。
    kind 只认 stock_hk/stock_a/fund（指数没有唯一股票语义，
    canonical 为 None 的自然被滤除）。
    """
    items = _load_norm_items(norm_json)
    syms = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("kind") in ("stock_hk", "stock_a", "fund") and it.get("canonical"):
            if it["canonical"] not in syms:
                syms.append(it["canonical"])
    return syms


def derive_symbol(title, desc, classified_norm) -> str | None:
    """单行确定性 symbol；唯一才返回，零个或多个（歧义）返回 None。"""
    syms = symbol_from_marker_text(title) or []
    if not syms:
        syms = symbol_from_marker_text(desc) or []
    if not syms and classified_norm is not None:
        syms = symbol_from_classified(classified_norm)
    return syms[0] if len(syms) == 1 else None


# ---------------------------------------------------------------------------
# sqlite 基建（对齐 xq_ticker_normalize 生产纪律）
# ---------------------------------------------------------------------------


def _is_busy(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _with_retry(fn, retries: int = 3, base_delay: float = 0.5):
    for attempt in range(retries + 1):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if attempt >= retries or not _is_busy(exc):
                raise
            time.sleep(base_delay * (2**attempt))


def _connect_ro(db_path) -> sqlite3.Connection:
    """只读 URI 连接；缺失库显式失败而不新建。"""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _connect_rw(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _columns(conn, table) -> list:
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]


def ensure_symbol_column(conn: sqlite3.Connection, retries: int = 3) -> bool:
    """幂等加列：symbol 不存在才 ALTER ADD COLUMN；已存在返回 False。"""
    if SYMBOL_COL in _columns(conn, TABLE):
        print("already")
        return False
    _with_retry(
        lambda: conn.execute("ALTER TABLE %s ADD COLUMN %s TEXT" % (TABLE, SYMBOL_COL)),
        retries=retries,
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# 迁移主体
# ---------------------------------------------------------------------------


def migrate(
    db_path, batch_size: int = BATCH_SIZE, dry_run: bool = True, execute: bool = False
) -> dict:
    """幂等迁移。dry-run 默认开（只读连接，只打印不写）；execute 才真跑。

    返回 report dict；dry_run=True 时 {"dry_run": True, "sql": [...],
    "total", "backfillable", "null_left", ...}，execute 时含
    added_column/updated/skipped 与来源分解。
    """
    do_write = execute and not dry_run
    if do_write:
        conn = _connect_rw(db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_status_add_symbol.py", exc)
    else:
        conn = _connect_ro(db_path)

    try:
        cols = _columns(conn, TABLE)
        col_exists = SYMBOL_COL in cols

        # 关联表兜底数据（norm_tickers 缺列时回退 tickers 原词形）
        cl_cols = (
            _columns(conn, CLASSIFIED_TABLE)
            if CLASSIFIED_TABLE
            in [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            else []
        )
        cl_source = None
        cl_rows = {}
        if cl_cols:
            if "norm_tickers" in cl_cols:
                cl_source = "norm_tickers"
                cl_rows = dict(
                    conn.execute(
                        "SELECT status_id, norm_tickers FROM %s" % CLASSIFIED_TABLE  # nosec B608 - 值参数化/内部标识符（已抽查）
                    )
                )
            elif "tickers" in cl_cols:
                cl_source = "tickers"
                cl_rows = dict(
                    conn.execute("SELECT status_id, tickers FROM %s" % CLASSIFIED_TABLE)  # nosec B608 - 值参数化/内部标识符（已抽查）
                )

        sql_plan = ["ALTER TABLE %s ADD COLUMN %s TEXT" % (TABLE, SYMBOL_COL)]
        total = backfillable = ambiguous = null_left = 0
        from_marker = from_classified = 0
        rows = conn.execute("SELECT status_id, title, desc FROM %s" % TABLE).fetchall()  # nosec B608 - 值参数化/内部标识符（已抽查）

        updates = []
        for status_id, title, desc in rows:
            total += 1
            # 计算本行目标值（dry-run 与 execute 同一推导）
            cl_norm = cl_rows.get(status_id)
            sym = derive_symbol(title, desc, cl_norm)
            # 来源分解：有记号命中算 marker，否则算 classified 兜底
            m_syms = symbol_from_marker_text(title) or symbol_from_marker_text(desc)
            if sym is not None:
                backfillable += 1
                if m_syms:
                    from_marker += 1
                else:
                    from_classified += 1
                updates.append((status_id, sym))
            elif len(m_syms) > 1 or (
                not m_syms
                and cl_norm is not None
                and len(symbol_from_classified(cl_norm)) > 1
            ):
                ambiguous += 1
            else:
                null_left += 1

        report = {
            "db": str(db_path),
            "dry_run": not do_write,
            "sql": sql_plan if not col_exists else [],
            "column_exists": col_exists,
            "total": total,
            "backfillable": backfillable,
            "from_marker": from_marker,
            "from_classified": from_classified,
            "ambiguous_left_null": ambiguous,
            "unmappable_left_null": null_left,
            "classified_source": cl_source,
        }

        if not do_write:
            return report

        # --execute 路径
        added = ensure_symbol_column(conn)
        report["added_column"] = added
        existing = (
            dict()
            if added
            else dict(
                conn.execute("SELECT status_id, %s FROM %s" % (SYMBOL_COL, TABLE))  # nosec B608 - 值参数化/内部标识符（已抽查）
            )
        )
        updated = skipped = 0
        pending = 0
        for status_id, sym in updates:
            if existing.get(status_id) == sym:
                skipped += 1
                continue
            _with_retry(
                lambda sid=status_id, s=sym: conn.execute(
                    "UPDATE %s SET %s=? WHERE status_id=?" % (TABLE, SYMBOL_COL),  # nosec B608 - 值参数化/内部标识符（已抽查）
                    (s, sid),
                )
            )
            updated += 1
            pending += 1
            if pending >= batch_size:
                conn.commit()
                pending = 0
        if pending:
            conn.commit()
        report["updated"] = updated
        report["skipped_unchanged"] = skipped
        return report
    finally:
        conn.close()


def verify(db_path) -> list:
    """只读验证 symbol 分布；缺失库显式失败不新建。"""
    conn = _connect_ro(db_path)
    try:
        conn.execute("PRAGMA query_only=ON")
        return conn.execute(SYMBOL_DIST_SQL).fetchall()
    finally:
        conn.close()


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="xq_status 补 symbol 列幂等迁移（WO-XQ）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 库路径")
    ap.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help="每批 commit 行数（默认 200）",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="只打印 SQL 与预计回填数（默认）",
    )
    mode.add_argument(
        "--execute", action="store_true", help="真跑迁移+回填（缺省不执行）"
    )
    ap.add_argument("--verify", action="store_true", help="只读验证 symbol 分布后退出")
    return ap


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.verify:
        for bucket, n in verify(args.db):
            print("symbol_dist %s=%d" % (bucket, n))
        return 0
    report = migrate(
        args.db, batch_size=args.batch_size, dry_run=args.dry_run, execute=args.execute
    )
    print("migrate " + json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report.get("dry_run"):
        print("DRY-RUN：以上为预计值，未写库。加 --execute 才真跑。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
