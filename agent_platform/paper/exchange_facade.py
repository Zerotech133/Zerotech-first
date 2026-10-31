# -*- coding: utf-8 -*-
"""Paper Exchange facade（只读路由，不迁移账）—— WO-EXCHANGE-FACADE-20261031，Phase 1-C。

本模块是模拟交易所的**只读门面**：把 ``tenant_model`` 的 namespace
（``sim/<book>/<tenant>[/<account>[/<combo>]]``）路由到两本**物理隔离**的纸面账：

================  ==========================================  ======================
book              存储（single_source_of_truth）               本 facade 的读法
================  ==========================================  ======================
official          ``agent_platform/data/paper/paper_ledger.jsonl``  JSONL 逐行聚合
legacy            ``k_stock/data/paper.db``（K-STOCK 训练沙盘）   SQLite ``mode=ro``
================  ==========================================  ======================

边界（本单硬约束，测试锁定）：
- **只读**：不写 ledger、不下单、不启动撮合、不迁移账、不 merge 两账；
  JSONL 只读文本，SQLite 以 ``mode=ro`` + ``PRAGMA query_only=ON`` 打开。
- **不猜数**：两本账的存储层都**没有 tenant 维度**（official 行只有
  date/symbol 级决策；legacy 只有 ``account_id``）。facade 因此把归属显式标成
  ``attribution.scope=book_level`` / ``account_id`` 且 ``owner_tenant_id=null``，
  绝不把「本账整本聚合」冒充成「某个租户的私有数据」。
- **缺失数据显式化**：文件/表缺失、坏行、schema 漂移一律返回
  ``status=unavailable/degraded`` + ``reason``/``missing``，相关字段为 null；
  不补零、不补默认值、不做归一化。
- **closed-on-failure**：非法/未知/跨租户的 tenant、account、book、namespace
  直接抛 ``tenant_model`` 的错误族（与 tenant_model / combo_registry 同判例），
  不静默降级——错参数按「本账没有该身份」处理比猜一个更安全。
- 不 import ``paper_book``/``risk_gate``/``engine``/``k_stock`` 的任何写路径；
  本模块只有 ``tenant_model`` + 标准库依赖。

命令行（只读，输出 JSON）::

    python -X utf8 -m agent_platform.paper.exchange_facade health
    python -X utf8 -m agent_platform.paper.exchange_facade route official
    python -X utf8 -m agent_platform.paper.exchange_facade list [--book legacy]
    python -X utf8 -m agent_platform.paper.exchange_facade snapshot sim/legacy/t1/main

身份语义：
- ``registered``：调用方传入的 ``tenant_model.TenantRegistry``（可选）。传了就在
  facade 入口强制「租户已登记 / 账户属于该租户」——跨租户/未知身份 closed；
  不传则只能校验 namespace 文法，``registry_verified=false``，归属一律 null。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

try:  # 包内正常导入（仓库根在 sys.path 上）
    from agent_platform.paper import tenant_model as tm
except ImportError:  # 直接 `python agent_platform/paper/exchange_facade.py` 运行时兜底
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent_platform.paper import tenant_model as tm

# 结构变更（字段语义/路由口径）必须升版本，读侧按版本拒绝未知结构。
SCHEMA_VERSION = 1
FACADE_NAME = "paper_exchange"

REPO = Path(__file__).resolve().parents[2]
PAPER_DIR = REPO / "agent_platform" / "data" / "paper"
OFFICIAL_LEDGER = PAPER_DIR / "paper_ledger.jsonl"
OFFICIAL_STATE = PAPER_DIR / "state.json"
OFFICIAL_KILL = PAPER_DIR / "KILL"
LEGACY_DB = REPO / "k_stock" / "data" / "paper.db"

# book -> 后端标识（路由口径；新增第三本账必须同时改 tenant_model.BOOKS）。
BOOK_BACKENDS = {
    "official": "official_ledger_jsonl",
    "legacy": "legacy_sqlite",
}

# 状态语义：ok=数据齐全；empty=存储存在但暂无记录（合法空态）；
# degraded=能读但缺维度/有坏行（字段 null）；unavailable=读不到（不猜数）。
STATUSES = ("ok", "degraded", "empty", "unavailable")

RECENT_DECISIONS_LIMIT = 10
RECENT_ORDERS_LIMIT = 10

# legacy SQLite 的最低 schema 契约（少表/少列 → unavailable，不做兼容猜测）。
# 2026-10-31 实测 k_stock/data/paper.db：8 表，5 张账本表 + decision_snapshots/
# positions_snapshot/sqlite_sequence（后三张本 facade 不读，故不列入硬契约）。
LEGACY_SCHEMA_VERSION = 1
LEGACY_REQUIRED_COLUMNS = {
    "account": ("account_id", "cash", "initial_cash", "created_at"),
    "positions": ("account_id", "stock", "qty", "avg_cost", "updated_at",
                  "strategy", "stop_price"),
    "equity_marks": ("date", "account_id", "total", "cash", "market_value",
                     "pos_pct", "pending", "verify_ok", "ts"),
    "orders": ("id", "ts", "source", "ref", "stock", "side", "qty", "status",
               "created_at", "strategy", "account_id", "filled_price",
               "filled_qty"),
    "ledger": ("id", "ts", "type", "stock", "qty", "amount", "cash_after",
               "account_id"),
}

# 读取边界（facade 永不做的事，写进 route()/health() 供下游断言）。
READONLY_BOUNDARIES = {
    "ledger_write": False,
    "place_order": False,
    "start_matching": False,
    "migrate_ledger": False,
    "merge_books": False,
    "real_trading": False,
}

_WRITE_CAPABILITY_KEYS = ("write_ledger", "place_order", "start_matching",
                          "migrate_ledger")


class ExchangeFacadeError(ValueError):
    """facade 自身的调用错误（不是数据缺失——数据缺失用 status=unavailable）。"""


# ---------------------------------------------------------------------------
# 通用小工具（全部只读）
# ---------------------------------------------------------------------------
def _stat(path):
    """只读 stat：返回 {path, exists, bytes, mtime}；缺失/不可读不抛错。"""
    info = {"path": str(path), "exists": False, "bytes": None, "mtime": None}
    try:
        st = path.stat()
    except OSError:
        return info
    info["exists"] = True
    info["bytes"] = st.st_size
    info["mtime"] = datetime.fromtimestamp(st.st_mtime).strftime(
        "%Y-%m-%d %H:%M:%S")
    return info


def _read_jsonl(path):
    """只读 JSONL → (rows, bad_lines, error)。坏行跳过计数，绝不补内容。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], 0, "读取失败：%s" % exc
    rows, bad = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            bad += 1
    return rows, bad, None


def _read_json_file(path):
    """只读 JSON 文件 → (data, error)；缺失/坏 JSON 不抛错。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, "读取失败：%s" % exc
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, "JSON 非法：%s" % exc


def _num(value):
    """数值字段归一：bool/None/非数值 → None（缺失就是 null，不补 0）。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _generated_at():
    """只读生成时间戳（不影响业务字段；仅仅用于观测）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _book_or_raise(book):
    """book 合法性委托 tenant_model（未知/第三本账 → TenantValidationError）。"""
    tm.namespace_prefix(book)  # 非法 book 直接抛错（closed-on-failure）
    return book


def _check_registry(registry):
    """registry 只接受 duck-type 的 TenantRegistry（get_tenant/resolve/tenants）。"""
    if registry is None:
        return None
    required = ("get_tenant", "resolve", "tenants", "accounts_of")
    if not all(callable(getattr(registry, name, None)) for name in required):
        raise tm.TenantValidationError(
            "registry 须为 tenant_model.TenantRegistry（缺 %s）: %r"
            % ("/".join(required), registry))
    return registry


def _id_grammar_ok(account_id, book):
    """account_id 是否落 tenant_model namespace 文法（只判文法，不猜归属）。"""
    try:
        tm.tenant_namespace("t1", book, account_id=account_id)
        return True
    except tm.TenantContractError:
        return False


def _make_unavailable(code, reason, missing=None):
    """缺失数据统一形状：code + reason + missing 列表（不造数）。"""
    return {"code": code, "reason": reason, "missing": list(missing or [])}


def _iso_attribution(book, account_id=None):
    """归属显式化：两账存储层均无 tenant 维度 → tenant 归属恒 null（不猜）。"""
    if book == "official":
        note = ("official ledger 只记录 date/symbol 级决策，未记录 tenant/account "
                "维度：本快照是整本 official 账聚合，tenant 归属为 null（不猜）")
        scope = "book_level"
    else:
        note = ("legacy SQLite 以 account_id 分区，未落 tenant 归属：tenant 归属为 "
                "null（不猜）；隔离按 account_id 生效")
        scope = "account_id"
    return {
        "scope": scope,
        "storage_tenant_dimension": False,
        "owner_tenant_id": None,
        "owner_account_id": account_id,
        "registry_verified_tenant_id": None,
        "note": note,
    }


# ---------------------------------------------------------------------------
# official 后端（JSONL 只读聚合）
# ---------------------------------------------------------------------------
def _official_positions(state):
    """从 state.json 抽持仓（官方只记录权重口径；数量字段一律 null，不造数）。"""
    positions = []
    for symbol, item in ((state or {}).get("positions") or {}).items():
        if not isinstance(item, dict):
            continue
        positions.append({
            "symbol": str(symbol),
            "weight": _num(item.get("weight")),
            "entry_price": _num(item.get("entry_price")),
            "last_price": _num(item.get("last_price")),
            "industry": item.get("industry"),
            "updated_at": item.get("updated_at"),
            "qty": None,  # official 账不记股数（缺失=null，不猜）
        })
    positions.sort(key=lambda row: (-(row["weight"] or 0.0), row["symbol"]))
    return positions


def _chain_ok(rows):
    """校验 append-only hash chain（prev_hash 必须指向上一条 hash）。"""
    prev = ""
    for row in rows:
        if row.get("prev_hash") != prev:
            return False
        prev = row.get("hash") or ""
    return True


def _official_view(rows, bad_lines, state):
    """official 只读视图：逐日/逐结论聚合 + state.json 持仓快照。"""
    dates, verdicts, actions = {}, {}, {}
    symbols = set()
    for row in rows:
        day = row.get("date")
        if day is not None:
            day = str(day)
            dates[day] = dates.get(day, 0) + 1
        verdict = row.get("verdict")
        if verdict is not None:
            verdict = str(verdict)
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
        action = row.get("action")
        if action is not None:
            action = str(action)
            actions[action] = actions.get(action, 0) + 1
        symbol = row.get("symbol")
        if symbol is not None:
            symbols.add(str(symbol))
    recent = []
    for row in rows[-RECENT_DECISIONS_LIMIT:]:
        recent.append({
            "date": row.get("date"),
            "symbol": row.get("symbol"),
            "verdict": row.get("verdict"),
            "action": row.get("action"),
            "weight_after": _num(row.get("weight_after")),
            "price": _num(row.get("price")),
            "hash": (row.get("hash") or "")[:12],
        })
    recent.reverse()
    state_view = None
    if isinstance(state, dict):
        state_view = {
            "nav": _num(state.get("nav")),
            "cash_weight": _num(state.get("cash_weight")),
            "positions": _official_positions(state),
            "updated_at": state.get("updated_at"),
        }
        if state_view["positions"]:
            state_view["positions_count"] = len(state_view["positions"])
        else:
            state_view["positions_count"] = 0
    return {
        "backend": BOOK_BACKENDS["official"],
        "rows": len(rows),
        "bad_lines": bad_lines,
        "dates": dict(sorted(dates.items())),
        "first_date": min(dates) if dates else None,
        "last_date": max(dates) if dates else None,
        "verdicts": dict(sorted(verdicts.items())),
        "actions": dict(sorted(actions.items())),
        "symbols_count": len(symbols),
        "hash_chain_ok": _chain_ok(rows),
        "last_hash": (rows[-1].get("hash") if rows else None),
        "recent_decisions": recent,
        "state": state_view,
        "mode": "paper",
        "trading_permitted": False,
    }


def _official_status(rows, bad_lines, state, chain_ok):
    """official 状态：坏行/缺 state/断链 → degraded；空账 → empty；否则 ok。"""
    if bad_lines or state is None or not chain_ok:
        return "degraded"
    if not rows:
        return "empty"
    return "ok"


# ---------------------------------------------------------------------------
# legacy 后端（SQLite 只读；schema 漂移 → unavailable）
# ---------------------------------------------------------------------------
def _sqlite_uri(db_path):
    """SQLite 只读 URI（mode=ro 从打开方式上禁写，绝不 create）。"""
    return "file:%s?mode=ro" % Path(db_path).as_posix()


def _connect_ro(db_path):
    """只读连接：mode=ro + query_only=ON（双保险，本模块永不回滚/提交写）。"""
    con = sqlite3.connect(_sqlite_uri(db_path), uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
    except sqlite3.DatabaseError:
        pass
    return con


def _table_columns(con, table):
    """取表列名集合；表名来自模块常量（非用户输入），不拼接外部字符串。"""
    try:
        return {row[1] for row in con.execute("PRAGMA table_info(%s)" % table)}
    except sqlite3.DatabaseError:
        return set()


def _legacy_schema_check(con):
    """按 LEGACY_REQUIRED_COLUMNS 逐表逐列核对 → (ok, missing)。"""
    missing = []
    for table, columns in LEGACY_REQUIRED_COLUMNS.items():
        have = _table_columns(con, table)
        if not have:
            missing.append("%s（整表缺失）" % table)
            continue
        lack = [col for col in columns if col not in have]
        if lack:
            missing.append("%s.%s" % (table, "/".join(lack)))
    return (not missing), missing


def _legacy_account_row(con, account_id):
    try:
        row = con.execute(
            "SELECT account_id, cash, initial_cash, created_at FROM account "
            "WHERE account_id=?", (account_id,)).fetchone()
    except sqlite3.DatabaseError:
        return None
    if row is None:
        return None
    return {
        "account_id": row[0],
        "cash": _num(row[1]),
        "initial_cash": _num(row[2]),
        "created_at": row[3],
    }


def _legacy_positions(con, account_id):
    try:
        rows = con.execute(
            "SELECT stock, qty, avg_cost, updated_at, strategy, stop_price "
            "FROM positions WHERE account_id=? ORDER BY stock",
            (account_id,)).fetchall()
    except sqlite3.DatabaseError:
        return []
    return [{
        "stock": row[0],
        "qty": _num(row[1]),
        "avg_cost": _num(row[2]),
        "updated_at": row[3],
        "strategy": row[4],
        "stop_price": _num(row[5]),
    } for row in rows]


def _legacy_equity(con, account_id):
    try:
        row = con.execute(
            "SELECT date, total, cash, market_value, pos_pct, pending, "
            "verify_ok, ts FROM equity_marks WHERE account_id=? "
            "ORDER BY date DESC, ts DESC LIMIT 1", (account_id,)).fetchone()
    except sqlite3.DatabaseError:
        return None
    if row is None:
        return None
    return {
        "date": row[0],
        "total": _num(row[1]),
        "cash": _num(row[2]),
        "market_value": _num(row[3]),
        "pos_pct": _num(row[4]),
        "pending": row[5],
        "verify_ok": row[6],
        "ts": row[7],
    }


def _legacy_orders(con, account_id):
    try:
        total = con.execute(
            "SELECT COUNT(*) FROM orders WHERE account_id=?",
            (account_id,)).fetchone()[0]
        by_status = dict(con.execute(
            "SELECT status, COUNT(*) FROM orders WHERE account_id=? "
            "GROUP BY status ORDER BY status", (account_id,)).fetchall())
        recent = con.execute(
            "SELECT id, ts, source, stock, side, qty, status, plan_date, "
            "filled_price, filled_qty, fee, strategy FROM orders "
            "WHERE account_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (account_id, RECENT_ORDERS_LIMIT)).fetchall()
    except sqlite3.DatabaseError:
        return None
    return {
        "total": total,
        "by_status": by_status,
        "recent_limit": RECENT_ORDERS_LIMIT,
        "recent": [{
            "id": row[0],
            "ts": row[1],
            "source": row[2],
            "stock": row[3],
            "side": row[4],
            "qty": row[5],
            "status": row[6],
            "plan_date": row[7],
            "filled_price": _num(row[8]),
            "filled_qty": row[9],
            "fee": _num(row[10]),
            "strategy": row[11],
        } for row in recent],
    }


def _legacy_ledger(con, account_id):
    try:
        total = con.execute(
            "SELECT COUNT(*) FROM ledger WHERE account_id=?",
            (account_id,)).fetchone()[0]
        grouped = con.execute(
            "SELECT type, COUNT(*), SUM(amount) FROM ledger WHERE account_id=? "
            "GROUP BY type ORDER BY type", (account_id,)).fetchall()
    except sqlite3.DatabaseError:
        return None
    by_type, amount_by_type = {}, {}
    for kind, count, amount in grouped:
        by_type[kind] = count
        amount_by_type[kind] = _num(amount)
    return {"rows": total, "by_type": by_type, "amount_by_type": amount_by_type}


def _legacy_has_any_data(con, account_id):
    """账户是否还有任何存证（account/positions/equity_marks/orders/ledger）。"""
    for table in ("account", "positions", "equity_marks", "orders", "ledger"):
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM %s WHERE account_id=?" % table,
                (account_id,)).fetchone()[0]
        except sqlite3.DatabaseError:
            continue
        if count:
            return True
    return False


def _legacy_account_ids(con):
    """联合五表发现账户 id（只读 UNION；不补、不归一）。"""
    sql = ("SELECT account_id FROM account "
           "UNION SELECT account_id FROM positions "
           "UNION SELECT account_id FROM equity_marks "
           "UNION SELECT account_id FROM orders "
           "UNION SELECT account_id FROM ledger "
           "ORDER BY account_id")
    try:
        rows = con.execute(sql).fetchall()
    except sqlite3.DatabaseError:
        return []
    ids = []
    for (account_id,) in rows:
        if isinstance(account_id, str) and account_id:
            ids.append(account_id)
    return ids


def _legacy_counts(con):
    """health 用：五张账本表的行数（只读 COUNT）。"""
    counts = {}
    for table in LEGACY_REQUIRED_COLUMNS:
        try:
            counts[table] = con.execute(
                "SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        except sqlite3.DatabaseError:
            counts[table] = None
    return counts


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------
class PaperExchangeFacade:
    """只读 Paper Exchange 门面：按 book 路由 official/legacy 的只读视图。

    全部方法只读；路径可注入（测试/旁路使用），缺省取模块常量。
    """

    readonly = True
    writable = False
    trading_permitted = False

    def __init__(self, official_ledger=None, official_state=None,
                 official_kill=None, legacy_db=None):
        self.official_ledger = Path(official_ledger) if official_ledger \
            else Path(OFFICIAL_LEDGER)
        self.official_state = Path(official_state) if official_state \
            else Path(OFFICIAL_STATE)
        self.official_kill = Path(official_kill) if official_kill \
            else Path(OFFICIAL_KILL)
        self.legacy_db = Path(legacy_db) if legacy_db else Path(LEGACY_DB)

    # -------------------- 路由 --------------------
    def route(self, book):
        """book → 只读后端路由描述；未知 book closed-on-failure。"""
        _book_or_raise(book)
        backend = BOOK_BACKENDS[book]
        if book == "official":
            storage = {
                "kind": "jsonl",
                "ledger": _stat(self.official_ledger),
                "state": _stat(self.official_state),
                "kill_switch": str(self.official_kill),
                "kill_active": self.official_kill.exists(),
            }
        else:
            storage = {"kind": "sqlite", "db": _stat(self.legacy_db),
                       "mode": "ro"}
        capabilities = {
            "read_account_snapshot": True,
            "read_list_accounts": True,
            "read_health": True,
            "write_ledger": False,
            "place_order": False,
            "start_matching": False,
            "migrate_ledger": False,
        }
        return {
            "book": book,
            "namespace_prefix": tm.namespace_prefix(book),
            "backend": backend,
            "storage": storage,
            "readonly": True,
            "writable": False,
            "trading_permitted": False,
            "capabilities": capabilities,
        }

    # -------------------- 账户快照 --------------------
    def account_snapshot(self, ref, account_id=None, *, combo_id=None,
                         book=None, registry=None):
        """按 tenant/account（或 namespace / AccountRef）取只读快照。

        参数三选一：
        - ``ref`` 为 ``tenant_model.AccountRef``（自带 book/namespace）；
        - ``ref`` 为 namespace 字符串（``sim/<book>/<tenant>[/<account>[/<combo>]]``）；
        - ``ref`` 为 tenant_id，配 ``account_id``/``combo_id``/``book`` 关键字。
        非法/未知/跨租户身份 → tenant_model 错误（closed-on-failure）；
        存储缺失/schema 漂移 → ``status=unavailable`` + ``unavailable.reason``。
        """
        registry = _check_registry(registry)
        target = _resolve_target(ref, account_id, combo_id, book)
        if target["combo_id"] is not None:
            return self._unsupported_combo_snapshot(target, registry)
        if registry is not None:
            if target["account_id"] is None:
                registry.get_tenant(target["tenant_id"], target["book"])
            else:
                registry.resolve(target["tenant_id"], target["account_id"],
                                 target["combo_id"], target["book"])
        if target["book"] == "official":
            section, status, unavailable, warnings = self._official_snapshot(
                target)
        else:
            section, status, unavailable, warnings = self._legacy_snapshot(
                target)
        return self._snapshot_document(target, registry, section, status,
                                       unavailable, warnings)

    def _unsupported_combo_snapshot(self, target, registry):
        """combo 级：两账存储均无 combo 维度列 → 显式 unavailable（不猜）。"""
        if registry is not None:
            registry.resolve(target["tenant_id"], target["account_id"],
                             target["combo_id"], target["book"])
        status = "unavailable"
        if target["book"] == "official":
            reason = ("official ledger 未记录 combo 维度，不能给出组合级快照；"
                      "组合维度归属为 null（不猜）")
            code = "official_combo_dimension_unsupported"
        else:
            reason = ("legacy SQLite 无 combo 维度列（combo 仅编码在部分 "
                      "account_id 字符串里），不能按 namespace 拆组合级快照；"
                      "归属为 null（不猜）")
            code = "legacy_combo_dimension_unsupported"
        unavailable = _make_unavailable(code, reason, ["combo_id 维度"])
        return self._snapshot_document(target, registry, None, status,
                                       unavailable, [reason])

    def _official_snapshot(self, target):
        """official：JSONL 聚合；缺失 → unavailable，坏行/缺 state → degraded。"""
        ledger = _stat(self.official_ledger)
        if not ledger["exists"]:
            unavailable = _make_unavailable(
                "official_ledger_missing",
                "official ledger 文件不存在：不造数、不返回空账冒充真实账",
                [str(self.official_ledger)])
            return None, "unavailable", unavailable, []
        rows, bad_lines, error = _read_jsonl(self.official_ledger)
        if error:
            unavailable = _make_unavailable(
                "official_ledger_unreadable", error,
                [str(self.official_ledger)])
            return None, "unavailable", unavailable, []
        state, state_error = _read_json_file(self.official_state)
        if not self.official_state.exists():
            state_error = "state.json 不存在"
        warnings = []
        if bad_lines:
            warnings.append("坏行 %d 行已跳过（不猜内容）" % bad_lines)
        if state is None:
            warnings.append("state 不可用（%s）：nav/cash_weight/positions 为 null"
                            % (state_error or "原因未知"))
        view = _official_view(rows, bad_lines, state)
        status = _official_status(rows, bad_lines, state,
                                  view["hash_chain_ok"])
        return view, status, None, warnings

    def _legacy_snapshot(self, target):
        """legacy：SQLite 只读；db/schema 缺失 → unavailable；未知账户 closed。"""
        if target["account_id"] is None:
            unavailable = _make_unavailable(
                "legacy_tenant_aggregate_unsupported",
                "legacy SQLite 未落 tenant 归属，无法按租户聚合账户（不猜）",
                ["tenant_id 维度"])
            return None, "unavailable", unavailable, []
        db = _stat(self.legacy_db)
        if not db["exists"]:
            unavailable = _make_unavailable(
                "legacy_db_missing", "legacy paper.db 不存在：不造数",
                [str(self.legacy_db)])
            return None, "unavailable", unavailable, []
        warnings = []
        try:
            con = _connect_ro(self.legacy_db)
        except sqlite3.Error as exc:
            unavailable = _make_unavailable(
                "legacy_db_unreadable", "只读打开失败：%s" % exc,
                [str(self.legacy_db)])
            return None, "unavailable", unavailable, []
        try:
            schema_ok, missing = _legacy_schema_check(con)
            if not schema_ok:
                unavailable = _make_unavailable(
                    "legacy_schema_mismatch",
                    "legacy schema 与契约 v%d 不符：不猜数、不兼容读"
                    % LEGACY_SCHEMA_VERSION, missing)
                return None, "unavailable", unavailable, []
            account = _legacy_account_row(con, target["account_id"])
            has_data = _legacy_has_any_data(con, target["account_id"])
            if account is None and not has_data:
                raise tm.AccountNotFound(
                    "legacy 无该账户任何存证: %s/%s/%s"
                    % (target["book"], target["tenant_id"],
                       target["account_id"]))
            positions = _legacy_positions(con, target["account_id"])
            equity = _legacy_equity(con, target["account_id"])
            orders = _legacy_orders(con, target["account_id"])
            ledger = _legacy_ledger(con, target["account_id"])
        except sqlite3.Error as exc:
            unavailable = _make_unavailable(
                "legacy_read_failed", "只读查询失败：%s" % exc,
                [str(self.legacy_db)])
            return None, "unavailable", unavailable, []
        finally:
            con.close()
        if account is None:
            warnings.append("account 表无该账户行（数据存在但不补默认账户，cash=null）")
        if equity is None:
            warnings.append("equity_marks 无该账户权益快照（total/cash=null，不猜）")
        view = {
            "backend": BOOK_BACKENDS["legacy"],
            "schema_version": LEGACY_SCHEMA_VERSION,
            "account": account,
            "equity": equity,
            "positions": positions,
            "positions_count": len(positions),
            "orders": orders,
            "ledger": ledger,
            "trading_permitted": False,
        }
        status = "degraded" if (account is None or equity is None) else "ok"
        return view, status, None, warnings

    def _snapshot_document(self, target, registry, section, status, unavailable,
                           warnings):
        """统一快照文档：book/namespace/归属/路由/存储证据 + 单账 section。"""
        book = target["book"]
        document = {
            "schema_version": SCHEMA_VERSION,
            "facade": FACADE_NAME,
            "readonly": True,
            "writable": False,
            "trading_permitted": False,
            "book": book,
            "namespace": target["namespace"],
            "tenant_id": target["tenant_id"],
            "account": {
                "tenant_id": target["tenant_id"],
                "account_id": target["account_id"],
                "combo_id": target["combo_id"],
            },
            "backend": BOOK_BACKENDS[book],
            "status": status,
            "ok": status in ("ok", "empty"),
            "registry_verified": registry is not None,
            "attribution": _iso_attribution(book, target["account_id"]),
            "route": self.route(book),
            "sources": self._sources(book),
            "official": section if book == "official" else None,
            "legacy": section if book == "legacy" else None,
            "unavailable": unavailable,
            "warnings": list(warnings),
            "generated_at": _generated_at(),
        }
        if registry is not None:
            document["attribution"]["registry_verified_tenant_id"] = \
                target["tenant_id"]
        return document

    def _sources(self, book):
        """只读存储证据（路径/存在/大小/mtime；不暴露其他 book 的读写面）。"""
        if book == "official":
            return {
                "ledger": _stat(self.official_ledger),
                "state": _stat(self.official_state),
                "kill_active": self.official_kill.exists(),
            }
        return {"db": _stat(self.legacy_db)}

    # -------------------- 账户目录 --------------------
    def list_accounts(self, book=None, registry=None):
        """只读账户目录：存储侧发现 + 可选 registry 侧（namespace 口径）。

        存储侧条目 ``tenant_id=null``、``namespace=null``：两本账都未落 tenant
        归属，facade 不猜谁是谁；registry 侧条目才有完整 namespace。
        """
        registry = _check_registry(registry)
        selected = list(tm.BOOKS)
        if book is not None:
            _book_or_raise(book)
            selected = [book]
        books, accounts = {}, []
        for name in selected:
            if name == "official":
                book_doc = self._official_account_dir(registry)
            else:
                book_doc = self._legacy_account_dir(registry)
            books[name] = book_doc
            accounts.extend(book_doc["entries"])
        return {
            "schema_version": SCHEMA_VERSION,
            "facade": FACADE_NAME,
            "readonly": True,
            "writable": False,
            "books": books,
            "accounts": tuple(accounts),
            "count": len(accounts),
            "generated_at": _generated_at(),
        }

    def _official_account_dir(self, registry):
        """official 目录：存储无账户维度（account 列表恒 null），registry 可有。"""
        ledger = _stat(self.official_ledger)
        entries = []
        if registry is not None:
            entries.extend(_registry_entries(registry, "official"))
        return {
            "book": "official",
            "backend": BOOK_BACKENDS["official"],
            "attribution": "book_level",
            "available": ledger["exists"],
            "storage_accounts": None,  # 存储层无 tenant/account 维度 → null（不猜）
            "storage_note": ("official ledger 未记录 tenant/account 维度，"
                             "存储侧账户数恒 null；账户口径只能来自 registry"),
            "entries": entries,
            "unavailable": None if ledger["exists"] else _make_unavailable(
                "official_ledger_missing", "official ledger 文件不存在",
                [str(self.official_ledger)]),
        }

    def _legacy_account_dir(self, registry):
        """legacy 目录：SQLite 存储侧发现（account_id）+ registry 侧 namespace。"""
        base = {
            "book": "legacy",
            "backend": BOOK_BACKENDS["legacy"],
            "attribution": "account_id",
            "available": False,
            "storage_accounts": None,
            "storage_note": "",
            "entries": [],
            "unavailable": None,
        }
        db = _stat(self.legacy_db)
        if not db["exists"]:
            base["unavailable"] = _make_unavailable(
                "legacy_db_missing", "legacy paper.db 不存在",
                [str(self.legacy_db)])
        else:
            try:
                con = _connect_ro(self.legacy_db)
            except sqlite3.Error as exc:
                base["unavailable"] = _make_unavailable(
                    "legacy_db_unreadable", "只读打开失败：%s" % exc,
                    [str(self.legacy_db)])
            else:
                try:
                    schema_ok, missing = _legacy_schema_check(con)
                    if not schema_ok:
                        base["unavailable"] = _make_unavailable(
                            "legacy_schema_mismatch",
                            "legacy schema 与契约 v%d 不符"
                            % LEGACY_SCHEMA_VERSION, missing)
                    else:
                        rows = {}
                        for row in con.execute(
                                "SELECT account_id, cash, initial_cash, "
                                "created_at FROM account"):
                            rows[row[0]] = row[1:]
                        storage = []
                        for account_id in _legacy_account_ids(con):
                            grammar_ok = _id_grammar_ok(account_id, "legacy")
                            if grammar_ok:
                                reason = ("account_id 文法可寻址，但 tenant 归属"
                                          "未落库：namespace=null（不猜）")
                            else:
                                reason = ("account_id 含 namespace 文法外字符"
                                          "（如 @），不能按 tenant_model "
                                          "namespace 寻址（不归一/不猜）")
                            cash, initial, created = rows.get(
                                account_id, (None, None, None))
                            storage.append({
                                "book": "legacy",
                                "source": "storage",
                                "tenant_id": None,
                                "account_id": account_id,
                                "combo_id": None,
                                "namespace": None,
                                "id_grammar_ok": grammar_ok,
                                "addressable": False,
                                "status": "listed",
                                "reason": reason,
                                "meta": {
                                    "account_row_present":
                                        account_id in rows,
                                    "cash": _num(cash),
                                    "initial_cash": _num(initial),
                                    "created_at": created,
                                },
                            })
                        base["storage_accounts"] = tuple(storage)
                        base["available"] = True
                        base["storage_note"] = (
                            "legacy 以 account_id 分区；tenant 归属未落库，"
                            "namespace/tenant_id 恒 null（不猜）")
                except sqlite3.Error as exc:
                    base["unavailable"] = _make_unavailable(
                        "legacy_read_failed", "只读查询失败：%s" % exc,
                        [str(self.legacy_db)])
                finally:
                    con.close()
        entries = list(base["storage_accounts"] or ())
        if registry is not None:
            entries.extend(_registry_entries(registry, "legacy"))
        base["entries"] = entries
        return base

    # -------------------- 健康检查 --------------------
    def health(self):
        """两本账的只读健康检查：ok/degraded/unavailable + 边界声明。"""
        try:
            official = self._official_health()
        except Exception as exc:  # noqa: BLE001 - health 必须可返回，不吞成静默
            official = _broken_health("official", exc)
        try:
            legacy = self._legacy_health()
        except Exception as exc:  # noqa: BLE001
            legacy = _broken_health("legacy", exc)
        books = {"official": official, "legacy": legacy}
        statuses = [doc["status"] for doc in books.values()]
        if all(item == "ok" for item in statuses):
            status = "ok"
        elif all(item == "unavailable" for item in statuses):
            status = "unavailable"
        else:
            status = "degraded"
        return {
            "schema_version": SCHEMA_VERSION,
            "facade": FACADE_NAME,
            "readonly": True,
            "writable": False,
            "status": status,
            "ok": status == "ok",
            "books": books,
            "isolation": {
                "official_prefix": tm.namespace_prefix("official"),
                "legacy_prefix": tm.namespace_prefix("legacy"),
                "books_merged": False,
                "cross_book_merge": False,
            },
            "boundaries": dict(READONLY_BOUNDARIES),
            "generated_at": _generated_at(),
        }

    def _official_health(self):
        """official 健康：ledger 可读性/行数/坏行/hash-chain/state/KILL。"""
        ledger = _stat(self.official_ledger)
        checks = [{"name": "ledger_exists", "ok": ledger["exists"],
                   "detail": ledger["path"]}]
        reasons = []
        if not ledger["exists"]:
            reasons.append("official ledger 文件不存在")
            return _health_doc("official", "unavailable", ledger, checks,
                               reasons)
        rows, bad_lines, error = _read_jsonl(self.official_ledger)
        checks.append({"name": "ledger_readable", "ok": error is None,
                       "detail": error or "ok"})
        if error:
            reasons.append(error)
            return _health_doc("official", "unavailable", ledger, checks,
                               reasons, rows=None, bad_lines=None)
        chain_ok = _chain_ok(rows)
        checks.append({"name": "hash_chain", "ok": chain_ok,
                       "detail": "append-only hash chain"})
        state_path = _stat(self.official_state)
        checks.append({"name": "state_exists", "ok": state_path["exists"],
                       "detail": state_path["path"]})
        state, state_error = _read_json_file(self.official_state)
        checks.append({"name": "state_parsed", "ok": state is not None,
                       "detail": state_error or "ok"})
        killed = self.official_kill.exists()
        checks.append({"name": "kill_switch", "ok": True,
                       "detail": "PAPER_KILL 文件存在（只读观测，不干预）"
                       if killed else "未触发"})
        view = _official_view(rows, bad_lines, state)
        if bad_lines:
            reasons.append("坏行 %d 行已跳过" % bad_lines)
        if not chain_ok:
            reasons.append("hash chain 断裂")
        if state is None:
            reasons.append("state 不可用：%s" % (state_error or "未知"))
        status = "ok"
        if bad_lines or state is None or not chain_ok:
            status = "degraded"
        return _health_doc(
            "official", status, ledger, checks, reasons, rows=len(rows),
            bad_lines=bad_lines, last_date=view["last_date"],
            hash_chain_ok=chain_ok, state_ok=state is not None,
            killed=killed)

    def _legacy_health(self):
        """legacy 健康：db 存在/可读/schema 契约/各表行数/最新权益日。"""
        db = _stat(self.legacy_db)
        checks = [{"name": "db_exists", "ok": db["exists"],
                   "detail": db["path"]}]
        reasons = []
        if not db["exists"]:
            reasons.append("legacy paper.db 文件不存在")
            return _health_doc("legacy", "unavailable", db, checks, reasons)
        try:
            con = _connect_ro(self.legacy_db)
        except sqlite3.Error as exc:
            checks.append({"name": "db_connect_ro", "ok": False,
                           "detail": str(exc)})
            reasons.append("只读打开失败：%s" % exc)
            return _health_doc("legacy", "unavailable", db, checks, reasons)
        try:
            schema_ok, missing = _legacy_schema_check(con)
            checks.append({"name": "schema_v%d" % LEGACY_SCHEMA_VERSION,
                           "ok": schema_ok,
                           "detail": "ok" if schema_ok
                           else "缺：%s" % "；".join(missing)})
            if not schema_ok:
                reasons.append("schema 与契约 v%d 不符"
                               % LEGACY_SCHEMA_VERSION)
                return _health_doc("legacy", "unavailable", db, checks,
                                   reasons, schema_ok=False, missing=missing)
            counts = _legacy_counts(con)
            try:
                latest = con.execute(
                    "SELECT MAX(date) FROM equity_marks").fetchone()[0]
            except sqlite3.DatabaseError:
                latest = None
        except sqlite3.Error as exc:
            checks.append({"name": "db_queries", "ok": False,
                           "detail": str(exc)})
            reasons.append("只读查询失败：%s" % exc)
            return _health_doc("legacy", "unavailable", db, checks, reasons)
        finally:
            con.close()
        return _health_doc("legacy", "ok", db, checks, reasons,
                           schema_ok=True, missing=[], tables=counts,
                           max_equity_date=latest)


def _health_doc(book, status, storage, checks, reasons, **extra):
    """health 单本账统一文档（storage=stat dict）。"""
    document = {
        "book": book,
        "backend": BOOK_BACKENDS[book],
        "readonly": True,
        "status": status,
        "path": storage["path"],
        "exists": storage["exists"],
        "bytes": storage["bytes"],
        "mtime": storage["mtime"],
        "checks": checks,
        "reasons": reasons,
    }
    document.update(extra)
    return document


def _broken_health(book, exc):
    """health 内部异常也必须有返回（fail-visible，不静默当 ok）。"""
    return {
        "book": book,
        "backend": BOOK_BACKENDS[book],
        "readonly": True,
        "status": "unavailable",
        "path": None,
        "exists": None,
        "bytes": None,
        "mtime": None,
        "checks": [],
        "reasons": ["facade 内部异常：%s" % exc],
    }


def _registry_entries(registry, book):
    """registry 侧账户目录条目（namespace 口径；tenant 归属由调用方声明）。"""
    entries = []
    for tenant in registry.tenants(book):
        for ref in registry.accounts_of(tenant.tenant_id, book):
            entries.append({
                "book": book,
                "source": "registry",
                "tenant_id": ref.tenant_id,
                "account_id": ref.account_id,
                "combo_id": ref.combo_id,
                "namespace": ref.namespace,
                "id_grammar_ok": True,
                "addressable": True,
                "status": "listed",
                "reason": "",
                "meta": {"tenant_status": tenant.status},
            })
    entries.sort(key=lambda row: (
        row["account_id"], row["combo_id"] or ""))
    return entries


def _resolve_target(ref, account_id, combo_id, book):
    """把三选一入参归一成路由目标 dict（namespace 由 tenant_model 生成/校验）。"""
    if isinstance(ref, tm.AccountRef):
        if account_id is not None or combo_id is not None or book is not None:
            raise ExchangeFacadeError(
                "ref 已是 AccountRef 时不得再传 account_id/combo_id/book")
        return {
            "book": ref.book,
            "tenant_id": ref.tenant_id,
            "account_id": ref.account_id,
            "combo_id": ref.combo_id,
            "namespace": ref.namespace,
        }
    if not isinstance(ref, str) or not ref or ref != ref.strip():
        raise tm.TenantValidationError(
            "tenant/namespace 须为非空字符串或 AccountRef: %r" % (ref,))
    if "/" in ref:
        if account_id is not None or combo_id is not None or book is not None:
            raise ExchangeFacadeError(
                "namespace 形式不得再传 account_id/combo_id/book")
        parsed = tm.parse_namespace(ref)
        return {
            "book": parsed["book"],
            "tenant_id": parsed["tenant_id"],
            "account_id": parsed["account_id"],
            "combo_id": parsed["combo_id"],
            "namespace": parsed["namespace"],
        }
    target_book = "official" if book is None else book
    namespace = tm.tenant_namespace(ref, target_book, account_id=account_id,
                                    combo_id=combo_id)
    return {
        "book": target_book,
        "tenant_id": ref,
        "account_id": account_id,
        "combo_id": combo_id,
        "namespace": namespace,
    }


# ---------------------------------------------------------------------------
# 模块级便捷函数（默认路径实例；测试/调用方可注入 PaperExchangeFacade）
# ---------------------------------------------------------------------------
def route(book):
    """book → 只读后端路由描述（未知 book → TenantValidationError）。"""
    return PaperExchangeFacade().route(book)


def account_snapshot(ref, account_id=None, *, combo_id=None, book=None,
                     registry=None):
    """按 tenant/account（或 namespace / AccountRef）取只读账户快照。"""
    return PaperExchangeFacade().account_snapshot(
        ref, account_id, combo_id=combo_id, book=book, registry=registry)


def list_accounts(book=None, registry=None):
    """列两本账的只读账户目录（存储侧发现 + 可选 registry 侧）。"""
    return PaperExchangeFacade().list_accounts(book=book, registry=registry)


def health():
    """两本账只读健康检查。"""
    return PaperExchangeFacade().health()


# ---------------------------------------------------------------------------
# CLI（只读；输出 JSON。身份错误 → JSON 错误 + 退出码 2）
# ---------------------------------------------------------------------------
def _jsonable(value):
    """把 Path/tuple/嵌套结构转 JSON 友好（只读输出用）。"""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def main(argv=None):
    """CLI 入口：health / route / list / snapshot（全部只读）。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agent_platform.paper.exchange_facade",
        description="Paper Exchange 只读 facade（不写账/不下单/不迁移账）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health", help="两本账只读健康检查")
    p_route = sub.add_parser("route", help="查看某本账的只读后端路由")
    p_route.add_argument("book", choices=list(tm.BOOKS))
    p_list = sub.add_parser("list", help="列只读账户目录")
    p_list.add_argument("--book", choices=list(tm.BOOKS), default=None)
    p_snap = sub.add_parser("snapshot", help="取只读账户快照")
    p_snap.add_argument("namespace", nargs="?", default=None,
                        help="sim/<book>/<tenant>[/<account>[/<combo>]]")
    p_snap.add_argument("--tenant", default=None)
    p_snap.add_argument("--account", default=None)
    p_snap.add_argument("--combo", default=None)
    p_snap.add_argument("--book", choices=list(tm.BOOKS), default=None)
    args = parser.parse_args(argv)

    try:
        if args.command == "health":
            result = health()
        elif args.command == "route":
            result = route(args.book)
        elif args.command == "list":
            result = list_accounts(book=args.book)
        else:
            if args.namespace:
                result = account_snapshot(args.namespace)
            elif args.tenant:
                result = account_snapshot(
                    args.tenant, args.account, combo_id=args.combo,
                    book=args.book)
            else:
                parser.error("snapshot 需要 namespace 参数或 --tenant")
    except tm.TenantContractError as exc:
        print(json.dumps({"ok": False, "error": str(exc),
                          "error_type": type(exc).__name__},
                         ensure_ascii=False))
        return 2
    except ExchangeFacadeError as exc:
        print(json.dumps({"ok": False, "error": str(exc),
                          "error_type": type(exc).__name__},
                         ensure_ascii=False))
        return 2
    print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "SCHEMA_VERSION",
    "LEGACY_SCHEMA_VERSION",
    "FACADE_NAME",
    "BOOK_BACKENDS",
    "STATUSES",
    "READONLY_BOUNDARIES",
    "LEGACY_REQUIRED_COLUMNS",
    "REPO",
    "OFFICIAL_LEDGER",
    "OFFICIAL_STATE",
    "OFFICIAL_KILL",
    "LEGACY_DB",
    "ExchangeFacadeError",
    "PaperExchangeFacade",
    "route",
    "account_snapshot",
    "list_accounts",
    "health",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
