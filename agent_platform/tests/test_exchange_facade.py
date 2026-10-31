# -*- coding: utf-8 -*-
"""Paper Exchange facade 只读路由测试（WO-EXCHANGE-FACADE-20261031，Phase 1-C）。

覆盖：
- official（paper_ledger.jsonl 聚合）/ legacy（paper.db 只读 SQLite）双向路由；
- 两本账隔离（book 不混、账户之间不串、跨租户/跨账本拒绝）；
- 未知/非法身份 closed-on-failure（TenantNotFound/AccountNotFound/
  CrossTenantError/TenantValidationError）；
- 缺失/坏数据 available 语义：status=unavailable/degraded + reason + null，
  不造数、不补零、不猜（坏行/schema 漂移/缺 state/断 hash-chain）；
- 只读守卫：AST 无写盘/写库/执行调用、不 import engine/risk_gate/paper_book/
  k_stock、SQLite mode=ro + query_only=ON、跑遍全部 API 后存储字节不变；
- list_accounts / route / health / CLI 输出。
"""
import ast
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from agent_platform.paper import exchange_facade as ef  # noqa: E402
from agent_platform.paper import tenant_model as tm  # noqa: E402

MODULE_SRC = REPO / "agent_platform" / "paper" / "exchange_facade.py"

# ---------------------------------------------------------------------------
# 测试夹具（fixture 制造「账本样张」；facade 只读，写盘全部由测试自己干）
# ---------------------------------------------------------------------------
_DEFAULT_STATE = {
    "positions": {
        "600519": {"weight": 0.1, "entry_price": 100.0, "last_price": 110.0,
                   "industry": "白酒", "updated_at": "2026-10-30"},
        "000858": {"weight": 0.05, "entry_price": 50.0, "last_price": 52.0,
                   "industry": "白酒", "updated_at": "2026-10-30"},
    },
    "cash_weight": 0.85,
    "nav": 1.01,
    "updated_at": "2026-10-30 18:12:12",
}

_LEGACY_SCHEMA = """
CREATE TABLE account (
  account_id TEXT PRIMARY KEY,
  cash REAL NOT NULL,
  initial_cash REAL NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE positions (
  account_id TEXT NOT NULL DEFAULT 'main',
  stock TEXT NOT NULL,
  qty INTEGER NOT NULL,
  avg_cost REAL NOT NULL,
  updated_at TEXT NOT NULL,
  strategy TEXT NOT NULL DEFAULT 'manual',
  stop_price REAL,
  PRIMARY KEY (account_id, stock)
);
CREATE TABLE equity_marks (
  date TEXT NOT NULL,
  account_id TEXT NOT NULL DEFAULT 'main',
  total REAL NOT NULL,
  cash REAL NOT NULL,
  market_value REAL NOT NULL,
  pos_pct REAL NOT NULL,
  pending INTEGER NOT NULL,
  verify_ok INTEGER NOT NULL,
  ts TEXT NOT NULL,
  PRIMARY KEY (date, account_id)
);
CREATE TABLE orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  source TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '',
  stock TEXT NOT NULL,
  side TEXT NOT NULL,
  qty INTEGER NOT NULL,
  status TEXT NOT NULL,
  plan_date TEXT,
  filled_price REAL,
  filled_qty INTEGER,
  fee REAL,
  created_at TEXT NOT NULL,
  strategy TEXT NOT NULL DEFAULT 'manual',
  account_id TEXT NOT NULL DEFAULT 'main'
);
CREATE TABLE ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,
  stock TEXT,
  qty INTEGER,
  amount REAL NOT NULL,
  cash_after REAL NOT NULL,
  account_id TEXT NOT NULL DEFAULT 'main'
);
"""

_LEGACY_SEED = """
INSERT INTO account (account_id, cash, initial_cash, created_at) VALUES
 ('main', 500000.0, 1000000.0, '2026-10-09T01:08:38'),
 ('taishan', 400000.0, 1000000.0, '2026-10-09T13:09:25'),
 ('combo_jijin@aggressive_direct', 1000000.0, 1000000.0, '2026-10-21T19:38:42');
INSERT INTO positions
 (account_id, stock, qty, avg_cost, updated_at, strategy, stop_price) VALUES
 ('main', '600519', 100, 100.0, '2026-10-30', 'manual', 90.0),
 ('main', '000858', 200, 50.0, '2026-10-30', 'manual', 45.0),
 ('taishan', '300750', 300, 200.0, '2026-10-30', 'manual', 180.0);
INSERT INTO equity_marks
 (date, account_id, total, cash, market_value, pos_pct, pending, verify_ok, ts)
VALUES
 ('2026-10-29', 'main', 510000.0, 500000.0, 10000.0, 0.02, 0, 1, '2026-10-29T18:00:00'),
 ('2026-10-30', 'main', 511000.0, 500000.0, 11000.0, 0.022, 0, 1, '2026-10-30T18:00:00'),
 ('2026-10-30', 'taishan', 399000.0, 400000.0, 0.0, 0.0, 0, 1, '2026-10-30T18:00:00');
INSERT INTO orders
 (id, ts, source, ref, stock, side, qty, status, plan_date,
  filled_price, filled_qty, fee, created_at, strategy, account_id) VALUES
 (1, '2026-10-29T09:30:00', 'ai', 'run#600519', '600519', 'buy', 100, 'filled',
  '2026-10-29', 100.0, 100, 5.0, '2026-10-29T09:30:00', 'manual', 'main'),
 (2, '2026-10-29T10:00:00', 'ai', 'run#000858', '000858', 'buy', 200, 'pending',
  NULL, NULL, NULL, NULL, '2026-10-29T10:00:00', 'manual', 'main'),
 (3, '2026-10-29T11:00:00', 'manual', '', '000858', 'sell', 50, 'cancelled',
  NULL, NULL, NULL, NULL, '2026-10-29T11:00:00', 'manual', 'main'),
 (4, '2026-10-30T09:30:00', 'ai', 'run#300750', '300750', 'buy', 300, 'filled',
  '2026-10-30', 200.0, 300, 6.0, '2026-10-30T09:30:00', 'manual', 'taishan');
INSERT INTO ledger (id, ts, type, stock, qty, amount, cash_after, account_id) VALUES
 (1, '2026-10-09T01:08:38', 'deposit', NULL, NULL, 1000000.0, 1000000.0, 'main'),
 (2, '2026-10-29T09:30:00', 'buy', '600519', 100, 10000.0, 990000.0, 'main'),
 (3, '2026-10-09T13:09:25', 'deposit', NULL, NULL, 1000000.0, 1000000.0, 'taishan');
"""

_PARTIAL_SCHEMA = """
CREATE TABLE account (account_id TEXT PRIMARY KEY);
CREATE TABLE positions (account_id TEXT, stock TEXT);
"""


def _paths(tmp_path):
    return {
        "official_ledger": tmp_path / "paper_ledger.jsonl",
        "official_state": tmp_path / "state.json",
        "official_kill": tmp_path / "KILL",
        "legacy_db": tmp_path / "paper.db",
    }


def _facade(tmp_path):
    p = _paths(tmp_path)
    facade = ef.PaperExchangeFacade(
        official_ledger=p["official_ledger"],
        official_state=p["official_state"],
        official_kill=p["official_kill"],
        legacy_db=p["legacy_db"],
    )
    return facade, p


def _decision(index, date="2026-10-30", verdict="pass", action="hold"):
    return {
        "date": date,
        "ts": 1793000000.0 + index,
        "symbol": "60051%d" % index,
        "name": "样张%d" % index,
        "recommendation": "WATCH",
        "confidence": 0.5,
        "verdict": verdict,
        "action": action,
        "target_weight": 0.0,
        "weight_before": 0.0,
        "weight_after": 0.0,
        "price": 10.0 + index,
        "industry": "测试",
        "risk_level": "LOW",
        "reasons": [],
        "nav": 1.0,
        "mode": "paper",
        "trading_permitted": False,
    }


def _chain(rows):
    """按 facade 的断链规则串 hash-chain（hash 值本身不重算，只看 prev_hash）。"""
    prev = ""
    out = []
    for index, row in enumerate(rows):
        item = dict(row)
        item["prev_hash"] = prev
        item["hash"] = "h%04d" % index
        prev = item["hash"]
        out.append(item)
    return out


def _official_rows():
    return _chain([
        _decision(0, "2026-10-29", verdict="pass", action="hold"),
        _decision(1, "2026-10-29", verdict="block", action="hold"),
        _decision(2, "2026-10-30", verdict="reduce", action="buy"),
        _decision(3, "2026-10-30", verdict="pass", action="hold"),
    ])


def _write_official(p, rows=None, state=_DEFAULT_STATE, bad_lines=0):
    rows = _official_rows() if rows is None else rows
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    for index in range(bad_lines):
        lines.insert(1, "not-json-%d" % index)
    text = "\n".join(lines)
    p["official_ledger"].write_text(text + "\n" if text else "",
                                    encoding="utf-8")
    if state is not None:
        p["official_state"].write_text(json.dumps(state, ensure_ascii=False),
                                       encoding="utf-8")


def _write_legacy(p, schema=_LEGACY_SCHEMA, seed=_LEGACY_SEED):
    con = sqlite3.connect(str(p["legacy_db"]))
    try:
        con.executescript(schema)
        if seed:
            con.executescript(seed)
        con.commit()
    finally:
        con.close()


def _write_both(p, rows=None, state=_DEFAULT_STATE):
    _write_official(p, rows=rows, state=state)
    _write_legacy(p)


def _ref(tenant_id="t1", account_id="main", book="official", combo_id=None):
    return tm.AccountRef(tenant_id, account_id, combo_id=combo_id, book=book)


def _registry(*refs):
    reg = tm.TenantRegistry()
    seen = set()
    for ref in refs:
        key = (ref.book, ref.tenant_id)
        if key in seen:
            continue
        reg.register_tenant(tm.Tenant(ref.tenant_id, book=ref.book))
        seen.add(key)
    for ref in refs:
        reg.register(ref)
    return reg


def _dir_digest(root):
    """目录内全部文件的内容摘要（验证 facade 零写入副作用）。"""
    digest = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return digest


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
def test_route_official_is_readonly_jsonl_backend(tmp_path):
    facade, _ = _facade(tmp_path)
    doc = facade.route("official")
    assert doc["book"] == "official"
    assert doc["backend"] == "official_ledger_jsonl"
    assert doc["namespace_prefix"] == "sim/official"
    assert doc["storage"]["kind"] == "jsonl"
    assert doc["readonly"] is True and doc["writable"] is False
    assert doc["trading_permitted"] is False
    caps = doc["capabilities"]
    assert caps["write_ledger"] is False and caps["place_order"] is False
    assert caps["start_matching"] is False and caps["migrate_ledger"] is False
    assert caps["read_account_snapshot"] is True


def test_route_legacy_is_readonly_sqlite_backend(tmp_path):
    facade, _ = _facade(tmp_path)
    doc = facade.route("legacy")
    assert doc["book"] == "legacy"
    assert doc["backend"] == "legacy_sqlite"
    assert doc["namespace_prefix"] == "sim/legacy"
    assert doc["storage"]["kind"] == "sqlite"
    assert doc["storage"]["mode"] == "ro"
    assert doc["readonly"] is True and doc["writable"] is False


@pytest.mark.parametrize("bad_book", ["paper", "official2", "sim/official", "",
                                      "OFFICIAL", "system"])
def test_route_unknown_book_closed(bad_book, tmp_path):
    facade, _ = _facade(tmp_path)
    with pytest.raises(tm.TenantValidationError):
        facade.route(bad_book)


def test_boundaries_declared_no_write(tmp_path):
    facade, _ = _facade(tmp_path)
    assert all(value is False for value in ef.READONLY_BOUNDARIES.values())
    assert facade.writable is False and facade.trading_permitted is False
    doc = facade.health()
    assert all(value is False for value in doc["boundaries"].values())
    assert doc["writable"] is False


# ---------------------------------------------------------------------------
# official：JSONL 聚合
# ---------------------------------------------------------------------------
def test_official_snapshot_aggregates_ledger(tmp_path):
    facade, p = _facade(tmp_path)
    _write_official(p)
    doc = facade.account_snapshot("t1", "a1")
    assert doc["schema_version"] == ef.SCHEMA_VERSION
    assert doc["book"] == "official" and doc["backend"] == "official_ledger_jsonl"
    assert doc["namespace"] == "sim/official/t1/a1"
    assert doc["status"] == "ok" and doc["ok"] is True
    assert doc["registry_verified"] is False
    section = doc["official"]
    assert doc["legacy"] is None
    assert section["rows"] == 4 and section["bad_lines"] == 0
    assert section["first_date"] == "2026-10-29"
    assert section["last_date"] == "2026-10-30"
    assert section["dates"] == {"2026-10-29": 2, "2026-10-30": 2}
    assert section["verdicts"] == {"block": 1, "pass": 2, "reduce": 1}
    assert section["actions"] == {"buy": 1, "hold": 3}
    assert section["symbols_count"] == 4
    assert section["hash_chain_ok"] is True
    assert section["trading_permitted"] is False and section["mode"] == "paper"
    assert len(section["recent_decisions"]) == 4
    assert section["recent_decisions"][0]["symbol"] == "600513"  # 最新在前
    state = section["state"]
    assert state["nav"] == 1.01 and state["cash_weight"] == 0.85
    assert state["positions_count"] == 2
    assert state["positions"][0]["symbol"] == "600519"
    assert state["positions"][0]["qty"] is None  # official 不记股数 → null（不猜）


def test_official_snapshot_namespace_and_account_ref_inputs(tmp_path):
    facade, p = _facade(tmp_path)
    _write_official(p)
    by_ns = facade.account_snapshot("sim/official/t1/a1")
    assert by_ns["namespace"] == "sim/official/t1/a1"
    assert by_ns["account"] == {"tenant_id": "t1", "account_id": "a1",
                                "combo_id": None}
    by_ref = facade.account_snapshot(_ref("t1", "a1", "official"))
    assert by_ref["official"]["rows"] == 4
    by_kw = facade.account_snapshot("t1", "a1", book="official")
    assert by_kw["official"]["rows"] == 4


def test_official_snapshot_missing_ledger_unavailable(tmp_path):
    facade, _ = _facade(tmp_path)  # 不写 ledger
    doc = facade.account_snapshot("t1", "a1")
    assert doc["status"] == "unavailable" and doc["ok"] is False
    assert doc["official"] is None and doc["legacy"] is None
    assert doc["unavailable"]["code"] == "official_ledger_missing"
    assert doc["unavailable"]["reason"]
    assert str(Path(tmp_path / "paper_ledger.jsonl")) in doc["unavailable"]["missing"]


def test_official_snapshot_empty_ledger_is_empty_not_fake_data(tmp_path):
    facade, p = _facade(tmp_path)
    _write_official(p, rows=[])
    doc = facade.account_snapshot("t1", "a1")
    assert doc["status"] == "empty" and doc["ok"] is True
    assert doc["official"]["rows"] == 0
    assert doc["official"]["last_date"] is None
    assert doc["official"]["state"]["positions_count"] == 2  # state 独立于台账行数


def test_official_state_missing_is_degraded_with_null_fields(tmp_path):
    facade, p = _facade(tmp_path)
    _write_official(p, state=None)
    doc = facade.account_snapshot("t1", "a1")
    assert doc["status"] == "degraded" and doc["ok"] is False
    assert doc["official"]["state"] is None
    assert doc["official"]["nav"] if "nav" in doc["official"] else True
    assert any("state" in warning for warning in doc["warnings"])


def test_official_bad_lines_and_broken_chain_degraded(tmp_path):
    facade, p = _facade(tmp_path)
    _write_official(p, bad_lines=1)
    doc = facade.account_snapshot("t1", "a1")
    assert doc["status"] == "degraded"
    assert doc["official"]["bad_lines"] == 1
    assert doc["official"]["rows"] == 4  # 坏行跳过、不猜内容
    assert any("坏行" in warning for warning in doc["warnings"])
    # 断链：第二条 prev_hash 对不上
    rows = _official_rows()
    rows[1]["prev_hash"] = "deadbeef"
    _write_official(p, rows=rows)
    broken = facade.account_snapshot("t1", "a1")
    assert broken["status"] == "degraded"
    assert broken["official"]["hash_chain_ok"] is False


def test_official_combo_level_unavailable(tmp_path):
    facade, p = _facade(tmp_path)
    _write_official(p)
    doc = facade.account_snapshot("sim/official/t1/a1/combo_x")
    assert doc["status"] == "unavailable"
    assert doc["official"] is None and doc["legacy"] is None
    assert doc["unavailable"]["code"] == "official_combo_dimension_unsupported"


def test_official_tenant_level_is_book_aggregate(tmp_path):
    facade, p = _facade(tmp_path)
    _write_official(p)
    doc = facade.account_snapshot("sim/official/t1")
    assert doc["status"] == "ok"
    assert doc["account"]["account_id"] is None
    assert doc["namespace"] == "sim/official/t1"
    assert doc["official"]["rows"] == 4
    assert doc["attribution"]["scope"] == "book_level"


# ---------------------------------------------------------------------------
# legacy：只读 SQLite
# ---------------------------------------------------------------------------
def test_legacy_snapshot_reads_account_views(tmp_path):
    facade, p = _facade(tmp_path)
    _write_legacy(p)
    doc = facade.account_snapshot("t1", "main", book="legacy")
    assert doc["book"] == "legacy" and doc["backend"] == "legacy_sqlite"
    assert doc["namespace"] == "sim/legacy/t1/main"  # 显式 book 参数才走 legacy
    assert doc["official"] is None
    assert doc["status"] == "ok" and doc["ok"] is True
    section = doc["legacy"]
    assert section["schema_version"] == ef.LEGACY_SCHEMA_VERSION
    assert section["account"]["cash"] == 500000.0
    assert section["account"]["initial_cash"] == 1000000.0
    assert section["equity"]["date"] == "2026-10-30"
    assert section["equity"]["total"] == 511000.0
    assert section["positions_count"] == 2
    assert {row["stock"] for row in section["positions"]} == {"600519", "000858"}
    assert section["orders"]["total"] == 3
    assert section["orders"]["by_status"] == {"cancelled": 1, "filled": 1,
                                              "pending": 1}
    assert len(section["orders"]["recent"]) == 3
    assert section["ledger"]["rows"] == 2
    assert section["ledger"]["by_type"] == {"buy": 1, "deposit": 1}
    assert section["ledger"]["amount_by_type"]["buy"] == 10000.0
    assert section["trading_permitted"] is False


def test_legacy_snapshot_via_namespace_uses_legacy_prefix(tmp_path):
    facade, p = _facade(tmp_path)
    _write_legacy(p)
    doc = facade.account_snapshot("sim/legacy/t1/main")
    assert doc["book"] == "legacy"
    assert doc["namespace"] == "sim/legacy/t1/main"
    assert doc["legacy"]["positions_count"] == 2


def test_legacy_account_isolation_between_accounts(tmp_path):
    facade, p = _facade(tmp_path)
    _write_legacy(p)
    main = facade.account_snapshot("t1", "main", book="legacy")
    taishan = facade.account_snapshot("t1", "taishan", book="legacy")
    main_stocks = {row["stock"] for row in main["legacy"]["positions"]}
    taishan_stocks = {row["stock"] for row in taishan["legacy"]["positions"]}
    assert main_stocks == {"600519", "000858"}
    assert taishan_stocks == {"300750"}
    assert not (main_stocks & taishan_stocks)  # 账户之间不串
    assert main["legacy"]["orders"]["total"] == 3
    assert taishan["legacy"]["orders"]["total"] == 1
    assert main["legacy"]["equity"]["total"] == 511000.0
    assert taishan["legacy"]["equity"]["total"] == 399000.0


def test_legacy_unknown_account_closed(tmp_path):
    facade, p = _facade(tmp_path)
    _write_legacy(p)
    with pytest.raises(tm.AccountNotFound):
        facade.account_snapshot("t1", "ghost", book="legacy")


def test_legacy_non_namespace_account_id_closed(tmp_path):
    """combo_...@... 这类 account_id 不符合 namespace 文法 → 拒绝（不归一/不猜）。"""
    facade, p = _facade(tmp_path)
    _write_legacy(p)
    with pytest.raises(tm.TenantValidationError):
        facade.account_snapshot("t1", "combo_jijin@aggressive_direct",
                                book="legacy")


def test_legacy_missing_db_unavailable(tmp_path):
    facade, _ = _facade(tmp_path)
    doc = facade.account_snapshot("t1", "main", book="legacy")
    assert doc["status"] == "unavailable" and doc["ok"] is False
    assert doc["legacy"] is None and doc["official"] is None
    assert doc["unavailable"]["code"] == "legacy_db_missing"


def test_legacy_schema_mismatch_unavailable(tmp_path):
    facade, p = _facade(tmp_path)
    _write_legacy(p, schema=_PARTIAL_SCHEMA, seed=None)
    doc = facade.account_snapshot("t1", "main", book="legacy")
    assert doc["status"] == "unavailable"
    assert doc["legacy"] is None
    assert doc["unavailable"]["code"] == "legacy_schema_mismatch"
    assert doc["unavailable"]["missing"]  # 明确列出缺哪张表/哪些列，不猜


def test_legacy_tenant_aggregate_and_combo_unavailable(tmp_path):
    facade, p = _facade(tmp_path)
    _write_legacy(p)
    tenant_doc = facade.account_snapshot("sim/legacy/t1")
    assert tenant_doc["status"] == "unavailable"
    assert tenant_doc["unavailable"]["code"] == \
        "legacy_tenant_aggregate_unsupported"
    combo_doc = facade.account_snapshot("sim/legacy/t1/main/combo_x")
    assert combo_doc["status"] == "unavailable"
    assert combo_doc["unavailable"]["code"] == \
        "legacy_combo_dimension_unsupported"


# ---------------------------------------------------------------------------
# 身份/隔离：unknown/cross-tenant/cross-book closed-on-failure
# ---------------------------------------------------------------------------
def test_unknown_tenant_closed_when_registry_given(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)
    reg = _registry(_ref("t1", "main", "legacy"))
    with pytest.raises(tm.TenantNotFound):
        facade.account_snapshot(_ref("t9", "main", "legacy"), registry=reg)


def test_cross_tenant_closed_when_registry_given(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)
    reg = _registry(_ref("t1", "main", "legacy"),
                    _ref("t2", "taishan", "legacy"))
    # t2 借 t1 的账户 → 跨租户拒绝
    with pytest.raises(tm.CrossTenantError):
        facade.account_snapshot(_ref("t2", "main", "legacy"), registry=reg)
    # 同账本下未登记的账户 → AccountNotFound（不静默给空）
    with pytest.raises(tm.AccountNotFound):
        facade.account_snapshot(_ref("t2", "ghost", "legacy"), registry=reg)
    assert facade.account_snapshot(_ref("t1", "main", "legacy"),
                                   registry=reg)["legacy"]["positions_count"] == 2


def test_cross_book_closed_when_registry_given(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)
    reg = _registry(_ref("t1", "a1", "official"))
    # t1 只在 official 账登记 → legacy 账跨账本访问拒绝（两账不合并）
    with pytest.raises(tm.CrossTenantError):
        facade.account_snapshot(_ref("t1", "main", "legacy"), registry=reg)


def test_registry_verified_flag_and_tenant_attribution_null(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)
    plain = facade.account_snapshot("t1", "main", book="legacy")
    assert plain["registry_verified"] is False
    assert plain["attribution"]["owner_tenant_id"] is None
    reg = _registry(_ref("t1", "main", "legacy"))
    verified = facade.account_snapshot(_ref("t1", "main", "legacy"),
                                       registry=reg)
    assert verified["registry_verified"] is True
    assert verified["attribution"]["registry_verified_tenant_id"] == "t1"
    # 存储层没有 tenant 维度：tenant 归属恒 null（不猜）
    assert verified["attribution"]["owner_tenant_id"] is None
    assert verified["attribution"]["storage_tenant_dimension"] is False
    assert verified["attribution"]["scope"] == "account_id"


def test_conflicting_arguments_rejected(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)
    with pytest.raises(ef.ExchangeFacadeError):
        facade.account_snapshot(_ref("t1", "main", "legacy"), "main")
    with pytest.raises(ef.ExchangeFacadeError):
        facade.account_snapshot("sim/legacy/t1/main", book="legacy")


@pytest.mark.parametrize("bad_ref", [None, 123, "", " t1", 3.14, b"t1", {},
                                     [], "T1"])
def test_invalid_identity_inputs_closed(bad_ref, tmp_path):
    facade, _ = _facade(tmp_path)
    with pytest.raises(tm.TenantValidationError):
        facade.account_snapshot(bad_ref, "a1")


@pytest.mark.parametrize("bad_ns", [
    "sim/paper/t1", "paper/official/t1", "sim/official/", "sim/official/T1",
    "sim/official/t1/a1/x/y", "sim/official/official", "sim/legacy/",
])
def test_invalid_namespace_closed(bad_ns, tmp_path):
    facade, _ = _facade(tmp_path)
    with pytest.raises(tm.TenantValidationError):
        facade.account_snapshot(bad_ns)


def test_book_isolation_snapshot_sections(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)
    official = facade.account_snapshot("t1", "a1")
    legacy = facade.account_snapshot("t1", "main", book="legacy")
    assert official["book"] == "official" and official["legacy"] is None
    assert legacy["book"] == "legacy" and legacy["official"] is None
    assert official["namespace"].startswith("sim/official/")
    assert legacy["namespace"].startswith("sim/legacy/")
    assert not official["namespace"].startswith("sim/legacy")
    assert not legacy["namespace"].startswith("sim/official")
    assert official["official"]["backend"] != legacy["legacy"]["backend"]


def test_snapshot_document_contract_keys(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)
    doc = facade.account_snapshot("t1", "a1")
    for key in ("schema_version", "facade", "readonly", "writable",
                "trading_permitted", "book", "namespace", "account", "backend",
                "status", "ok", "registry_verified", "attribution", "route",
                "sources", "official", "legacy", "unavailable", "warnings",
                "generated_at"):
        assert key in doc, key
    assert doc["facade"] == ef.FACADE_NAME
    assert doc["schema_version"] == ef.SCHEMA_VERSION
    assert doc["route"]["book"] == "official"
    assert doc["sources"]["ledger"]["exists"] is True


# ---------------------------------------------------------------------------
# list_accounts
# ---------------------------------------------------------------------------
def test_list_accounts_legacy_storage_discovery(tmp_path):
    facade, p = _facade(tmp_path)
    _write_legacy(p)
    doc = facade.list_accounts(book="legacy")
    assert doc["readonly"] is True and doc["writable"] is False
    legacy = doc["books"]["legacy"]
    assert legacy["available"] is True
    ids = [entry["account_id"] for entry in legacy["storage_accounts"]]
    assert {"main", "taishan", "combo_jijin@aggressive_direct"} <= set(ids)
    by_id = {entry["account_id"]: entry for entry in legacy["storage_accounts"]}
    assert by_id["main"]["tenant_id"] is None
    assert by_id["main"]["namespace"] is None  # tenant 归属未落库 → null（不猜）
    assert by_id["main"]["addressable"] is False
    assert by_id["main"]["id_grammar_ok"] is True
    assert by_id["combo_jijin@aggressive_direct"]["id_grammar_ok"] is False
    assert "@" in by_id["combo_jijin@aggressive_direct"]["reason"]
    assert doc["count"] == len(doc["accounts"]) == 3


def test_list_accounts_official_storage_has_no_account_dimension(tmp_path):
    facade, p = _facade(tmp_path)
    _write_official(p)
    official = facade.list_accounts(book="official")["books"]["official"]
    assert official["available"] is True
    assert official["storage_accounts"] is None  # 存储无账户维度 → null（不猜）
    assert official["attribution"] == "book_level"
    assert "tenant/account" in official["storage_note"]
    assert official["entries"] == []


def test_list_accounts_with_registry_namespaces(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)
    reg = _registry(_ref("t1", "a1", "official"), _ref("t1", "main", "legacy"))
    doc = facade.list_accounts(registry=reg)
    entries = [entry for entry in doc["accounts"]
               if entry["source"] == "registry"]
    namespaces = {entry["namespace"] for entry in entries}
    assert namespaces == {"sim/official/t1/a1", "sim/legacy/t1/main"}
    for entry in entries:
        assert entry["source"] == "registry"
        assert entry["addressable"] is True
        assert entry["tenant_id"] == "t1"
        assert entry["id_grammar_ok"] is True
    assert doc["books"]["legacy"]["storage_accounts"]  # 存储侧仍在


def test_list_accounts_missing_storage_unavailable(tmp_path):
    facade, _ = _facade(tmp_path)
    doc = facade.list_accounts()
    assert doc["books"]["official"]["available"] is False
    assert doc["books"]["official"]["unavailable"]["code"] == \
        "official_ledger_missing"
    assert doc["books"]["legacy"]["available"] is False
    assert doc["books"]["legacy"]["unavailable"]["code"] == "legacy_db_missing"
    assert doc["count"] == 0


def test_list_accounts_unknown_book_closed(tmp_path):
    facade, _ = _facade(tmp_path)
    with pytest.raises(tm.TenantValidationError):
        facade.list_accounts(book="paper")


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------
def test_health_ok_with_both_books(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)
    doc = facade.health()
    assert doc["status"] == "ok" and doc["ok"] is True
    assert doc["readonly"] is True and doc["writable"] is False
    official = doc["books"]["official"]
    assert official["status"] == "ok"
    assert official["rows"] == 4 and official["bad_lines"] == 0
    assert official["hash_chain_ok"] is True and official["state_ok"] is True
    legacy = doc["books"]["legacy"]
    assert legacy["status"] == "ok"
    assert legacy["tables"]["account"] == 3
    assert legacy["tables"]["positions"] == 3
    assert legacy["tables"]["orders"] == 4
    assert legacy["max_equity_date"] == "2026-10-30"
    assert doc["isolation"]["books_merged"] is False


def test_health_missing_storage_unavailable(tmp_path):
    facade, _ = _facade(tmp_path)
    doc = facade.health()
    assert doc["status"] == "unavailable" and doc["ok"] is False
    assert doc["books"]["official"]["status"] == "unavailable"
    assert doc["books"]["official"]["reasons"]
    assert doc["books"]["legacy"]["status"] == "unavailable"
    assert doc["books"]["legacy"]["reasons"]


def test_health_degraded_on_bad_ledger(tmp_path):
    facade, p = _facade(tmp_path)
    _write_official(p, bad_lines=1)
    _write_legacy(p)
    doc = facade.health()
    assert doc["status"] == "degraded" and doc["ok"] is False
    assert doc["books"]["official"]["status"] == "degraded"
    assert doc["books"]["official"]["bad_lines"] == 1
    assert doc["books"]["legacy"]["status"] == "ok"


def test_health_legacy_schema_mismatch_unavailable(tmp_path):
    facade, p = _facade(tmp_path)
    _write_official(p)
    _write_legacy(p, schema=_PARTIAL_SCHEMA, seed=None)
    doc = facade.health()
    legacy = doc["books"]["legacy"]
    assert legacy["status"] == "unavailable"
    assert legacy["schema_ok"] is False and legacy["missing"]
    assert doc["status"] == "degraded"  # official 还活着


def test_health_kill_switch_is_observation_only(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)
    p["official_kill"].write_text("kill", encoding="utf-8")
    before = _dir_digest(tmp_path)
    doc = facade.health()
    assert doc["books"]["official"]["killed"] is True
    assert doc["books"]["official"]["status"] == "ok"  # 只读观测，不改账
    assert p["official_kill"].exists()  # 没有删除/改写 KILL 文件
    assert _dir_digest(tmp_path) == before


def test_health_internal_failure_is_visible(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)

    def boom():
        raise RuntimeError("模拟内部异常")

    facade._official_health = boom
    doc = facade.health()
    assert doc["books"]["official"]["status"] == "unavailable"
    assert any("内部异常" in reason
               for reason in doc["books"]["official"]["reasons"])
    assert doc["status"] == "degraded"


# ---------------------------------------------------------------------------
# 只读守卫（AST / 运行时零写入）
# ---------------------------------------------------------------------------
def test_module_ast_readonly_no_write_paths():
    source = MODULE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    allowed = {"__future__", "argparse", "datetime", "json", "pathlib",
               "sqlite3", "sys", "agent_platform.paper"}
    assert imported <= allowed, imported
    banned_roots = ("engine", "risk_gate", "paper_book", "k_stock", "subprocess",
                    "shutil", "socket", "requests", "urllib", "pandas")
    for module in imported:
        lowered = module.lower()
        assert not any(token in lowered for token in banned_roots), module

    called_names, called_attrs = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called_names.add(func.id)
        elif isinstance(func, ast.Attribute):
            called_attrs.add(func.attr)
    assert "open" not in called_names  # 只读用 Path.read_text
    assert not ({"eval", "exec", "__import__"} & called_names)
    forbidden_attrs = {
        "write_text", "write_bytes", "append_ledger", "save_state",
        "apply_decision", "execute_order", "place_order", "executemany",
        "executescript", "commit", "rollback", "remove", "unlink", "rename",
        "rmdir", "mkdir", "makedirs",
    }
    assert not (forbidden_attrs & called_attrs), forbidden_attrs & called_attrs

    # SQL 里只允许读：出现 SELECT/PRAGMA 的字符串不得含写关键字
    write_sql = re.compile(
        r"\b(INSERT|UPDATE|DELETE|REPLACE|DROP|ALTER|CREATE|ATTACH|DETACH|"
        r"VACUUM|REINDEX)\b")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not write_sql.search(node.value), node.value

    # SQLite 只读双保险 + 无写入口
    assert "mode=ro" in source
    assert "query_only=ON" in source
    for banned_name in ("append_ledger", "save_state", "apply_decision",
                        "execute_order", "place_order", "migrate_ledger"):
        assert not hasattr(ef, banned_name)


def test_readonly_sqlite_connection_rejects_writes(tmp_path):
    facade, p = _facade(tmp_path)
    _write_legacy(p)
    con = ef._connect_ro(p["legacy_db"])
    try:
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("CREATE TABLE forbidden_write_test (x INTEGER)")
    finally:
        con.close()
    assert "forbidden_write_test" not in {
        row[0] for row in sqlite3.connect(str(p["legacy_db"])).execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}


def test_operations_do_not_mutate_storage(tmp_path):
    facade, p = _facade(tmp_path)
    _write_both(p)
    before = _dir_digest(tmp_path)
    facade.route("official")
    facade.route("legacy")
    facade.health()
    facade.list_accounts()
    facade.account_snapshot("t1", "a1")
    facade.account_snapshot("t1", "main", book="legacy")
    facade.account_snapshot("sim/official/t1")
    facade.account_snapshot("sim/legacy/t1/main")
    assert _dir_digest(tmp_path) == before  # 字节级零写入


def test_real_repo_smoke_skip_when_runtime_data_absent():
    """主仓实数据 smoke（worktree 内 data/ 被 gitignore → skip，不造数）。"""
    official = REPO / "agent_platform" / "data" / "paper" / "paper_ledger.jsonl"
    legacy = REPO / "k_stock" / "data" / "paper.db"
    if not official.exists() and not legacy.exists():
        pytest.skip("当前工作区无运行时账本数据（data/ 不进 git）")
    doc = ef.health()
    assert doc["readonly"] is True and doc["writable"] is False
    assert doc["status"] in ef.STATUSES
    if official.exists():
        assert doc["books"]["official"]["exists"] is True
        snapshot = ef.account_snapshot("sim/official/smoke/check")
        assert snapshot["book"] == "official"
        assert snapshot["official"]["rows"] >= 0
    if legacy.exists():
        assert doc["books"]["legacy"]["exists"] is True


# ---------------------------------------------------------------------------
# CLI（只读 JSON；身份错误 → exit 2）
# ---------------------------------------------------------------------------
def test_cli_health_and_route(tmp_path, monkeypatch, capsys):
    facade, p = _facade(tmp_path)
    _write_both(p)
    monkeypatch.setattr(ef, "OFFICIAL_LEDGER", p["official_ledger"])
    monkeypatch.setattr(ef, "OFFICIAL_STATE", p["official_state"])
    monkeypatch.setattr(ef, "OFFICIAL_KILL", p["official_kill"])
    monkeypatch.setattr(ef, "LEGACY_DB", p["legacy_db"])
    assert ef.main(["health"]) == 0
    health_doc = json.loads(capsys.readouterr().out)
    assert health_doc["status"] == "ok"
    assert ef.main(["route", "official"]) == 0
    route_doc = json.loads(capsys.readouterr().out)
    assert route_doc["backend"] == "official_ledger_jsonl"
    assert "sim/official" in health_doc["isolation"]["official_prefix"]


def test_cli_snapshot_and_list(tmp_path, monkeypatch, capsys):
    facade, p = _facade(tmp_path)
    _write_both(p)
    monkeypatch.setattr(ef, "OFFICIAL_LEDGER", p["official_ledger"])
    monkeypatch.setattr(ef, "OFFICIAL_STATE", p["official_state"])
    monkeypatch.setattr(ef, "OFFICIAL_KILL", p["official_kill"])
    monkeypatch.setattr(ef, "LEGACY_DB", p["legacy_db"])
    assert ef.main(["snapshot", "--tenant", "t1", "--account", "main",
                    "--book", "legacy"]) == 0
    legacy_doc = json.loads(capsys.readouterr().out)
    assert legacy_doc["legacy"]["account"]["account_id"] == "main"
    assert ef.main(["list", "--book", "legacy"]) == 0
    list_doc = json.loads(capsys.readouterr().out)
    assert list_doc["count"] >= 2


def test_cli_closed_identity_returns_json_error(tmp_path, monkeypatch, capsys):
    facade, p = _facade(tmp_path)
    _write_both(p)
    monkeypatch.setattr(ef, "OFFICIAL_LEDGER", p["official_ledger"])
    monkeypatch.setattr(ef, "OFFICIAL_STATE", p["official_state"])
    monkeypatch.setattr(ef, "OFFICIAL_KILL", p["official_kill"])
    monkeypatch.setattr(ef, "LEGACY_DB", p["legacy_db"])
    assert ef.main(["snapshot", "sim/paper/t1"]) == 2
    error_doc = json.loads(capsys.readouterr().out)
    assert error_doc["ok"] is False
    assert error_doc["error_type"] == "TenantValidationError"
    assert "minimal" not in error_doc  # 只回显失败类型，不伪造数据
