# -*- coding: utf-8 -*-
"""Paper Exchange Gateway 只读路由接入测试（WO-EXCHANGE-GATEWAY-20261031，Phase 2-C）。

覆盖：
- resolve() 路由（official/legacy、namespace 解析、tenant 级、纯路由不做数据 IO）；
- health/accounts/snapshot 透传 facade（同一数据口径，不另造数）；
- 隔离与 closed-on-failure（未知 book/tenant/account、跨账本、跨租户）；
- 缺失数据：HTTP 200 + data.status=unavailable/null（不造数、不假装成功）；
- handle_get() 纯函数契约：(status, content_type, bytes body)、四条 /paper_exchange/*、
  未知路径 404、参数坏/身份闭包 400、内部异常 500 JSON（不起服务、不占端口）；
- 只读守卫：AST 无 server/socket/写盘/写账/engine/risk_gate 依赖、零写入副作用；
- CLI（health/route/accounts/snapshot/get，不绑端口）。
"""
import ast
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from agent_platform.paper import exchange_facade as facade  # noqa: E402
from agent_platform.paper import exchange_gateway as gw  # noqa: E402
from agent_platform.paper import tenant_model as tm  # noqa: E402

MODULE_SRC = REPO / "agent_platform" / "paper" / "exchange_gateway.py"

# ---------------------------------------------------------------------------
# 样张夹具（写盘全部由测试完成；gateway/facade 必须零写入）
# ---------------------------------------------------------------------------
_DEFAULT_STATE = {
    "positions": {
        "600519": {"weight": 0.1, "entry_price": 100.0, "last_price": 110.0,
                   "industry": "白酒", "updated_at": "2026-10-30"},
    },
    "cash_weight": 0.9,
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
 ('taishan', '300750', 300, 200.0, '2026-10-30', 'manual', 180.0);
INSERT INTO equity_marks
 (date, account_id, total, cash, market_value, pos_pct, pending, verify_ok, ts)
VALUES
 ('2026-10-30', 'main', 511000.0, 500000.0, 11000.0, 0.022, 0, 1, '2026-10-30T18:00:00'),
 ('2026-10-30', 'taishan', 399000.0, 400000.0, 0.0, 0.0, 0, 1, '2026-10-30T18:00:00');
INSERT INTO orders
 (id, ts, source, ref, stock, side, qty, status, plan_date,
  filled_price, filled_qty, fee, created_at, strategy, account_id) VALUES
 (1, '2026-10-29T09:30:00', 'ai', 'run#600519', '600519', 'buy', 100, 'filled',
  '2026-10-29', 100.0, 100, 5.0, '2026-10-29T09:30:00', 'manual', 'main'),
 (2, '2026-10-30T09:30:00', 'ai', 'run#300750', '300750', 'buy', 300, 'filled',
  '2026-10-30', 200.0, 300, 6.0, '2026-10-30T09:30:00', 'manual', 'taishan');
INSERT INTO ledger (id, ts, type, stock, qty, amount, cash_after, account_id) VALUES
 (1, '2026-10-09T01:08:38', 'deposit', NULL, NULL, 1000000.0, 1000000.0, 'main'),
 (2, '2026-10-29T09:30:00', 'buy', '600519', 100, 10000.0, 990000.0, 'main');
"""


def _paths(tmp_path):
    return {
        "official_ledger": tmp_path / "paper_ledger.jsonl",
        "official_state": tmp_path / "state.json",
        "official_kill": tmp_path / "KILL",
        "legacy_db": tmp_path / "paper.db",
    }


def _decision(index, date="2026-10-30", verdict="pass", action="hold"):
    return {
        "date": date, "ts": 1793000000.0 + index, "symbol": "60051%d" % index,
        "name": "样张%d" % index, "recommendation": "WATCH", "confidence": 0.5,
        "verdict": verdict, "action": action, "target_weight": 0.0,
        "weight_before": 0.0, "weight_after": 0.0, "price": 10.0 + index,
        "industry": "测试", "risk_level": "LOW", "reasons": [], "nav": 1.0,
        "mode": "paper", "trading_permitted": False,
    }


def _chain(rows):
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
        _decision(0, "2026-10-29", verdict="pass"),
        _decision(1, "2026-10-29", verdict="block"),
        _decision(2, "2026-10-30", verdict="reduce", action="buy"),
    ])


def _write_official(p, rows=None, state=_DEFAULT_STATE):
    rows = _official_rows() if rows is None else rows
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    text = "\n".join(lines)
    p["official_ledger"].write_text(text + "\n" if text else "",
                                    encoding="utf-8")
    if state is not None:
        p["official_state"].write_text(json.dumps(state, ensure_ascii=False),
                                       encoding="utf-8")


def _write_legacy(p):
    con = sqlite3.connect(str(p["legacy_db"]))
    try:
        con.executescript(_LEGACY_SCHEMA)
        con.executescript(_LEGACY_SEED)
        con.commit()
    finally:
        con.close()


def _write_both(p):
    _write_official(p)
    _write_legacy(p)


def _patch_paths(monkeypatch, p):
    """网关不持路径：路径口径统一由 facade 模块常量决定（monkeypatch 注入样张）。"""
    monkeypatch.setattr(facade, "OFFICIAL_LEDGER", p["official_ledger"])
    monkeypatch.setattr(facade, "OFFICIAL_STATE", p["official_state"])
    monkeypatch.setattr(facade, "OFFICIAL_KILL", p["official_kill"])
    monkeypatch.setattr(facade, "LEGACY_DB", p["legacy_db"])


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
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def _call_json(path, query=None):
    status, content_type, body = gw.handle_get(path, query)
    assert isinstance(status, int)
    assert content_type == gw.CONTENT_TYPE
    assert isinstance(body, bytes)
    return status, json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# resolve：只读路由（不做数据 IO）
# ---------------------------------------------------------------------------
def test_resolve_official_route_readonly():
    doc = gw.resolve("official", "t1", "a1")
    assert doc["gateway"] == gw.GATEWAY_NAME
    assert doc["schema_version"] == gw.SCHEMA_VERSION
    assert doc["book"] == "official"
    assert doc["backend"] == "official_ledger_jsonl"
    assert doc["tenant_id"] == "t1" and doc["account_id"] == "a1"
    assert doc["namespace"] == "sim/official/t1/a1"
    assert doc["resolved"] is True
    assert doc["readonly"] is True and doc["writable"] is False
    assert doc["trading_permitted"] is False
    assert doc["route"]["namespace_prefix"] == "sim/official"
    assert doc["route"]["capabilities"]["write_ledger"] is False
    assert doc["route"]["capabilities"]["place_order"] is False
    assert doc["boundaries"]["new_port"] is False
    assert set(doc["boundaries"].values()).issubset({True, False})


def test_resolve_legacy_route_readonly():
    doc = gw.resolve("legacy", "t1", "main")
    assert doc["book"] == "legacy"
    assert doc["backend"] == "legacy_sqlite"
    assert doc["namespace"] == "sim/legacy/t1/main"
    assert doc["route"]["storage"]["kind"] == "sqlite"
    assert doc["route"]["storage"]["mode"] == "ro"


def test_resolve_tenant_level_without_account():
    doc = gw.resolve("official", "t1")
    assert doc["account_id"] is None
    assert doc["namespace"] == "sim/official/t1"
    assert doc["resolved"] is True


def test_resolve_namespace_input_matches_book():
    doc = gw.resolve("legacy", "sim/legacy/t1/main")
    assert doc["tenant_id"] == "t1" and doc["account_id"] == "main"
    assert doc["namespace"] == "sim/legacy/t1/main"


def test_resolve_pure_routing_does_not_touch_storage(tmp_path, monkeypatch):
    """resolve 只解析路由：账本文件不存在也成功（数据缺失留给 snapshot 表达）。"""
    p = _paths(tmp_path)  # 只给路径、不写任何样张
    _patch_paths(monkeypatch, p)
    doc = gw.resolve("official", "t1", "a1")
    assert doc["resolved"] is True
    assert doc["route"]["storage"]["ledger"]["exists"] is False


@pytest.mark.parametrize("bad_book", ["paper", "official2", "sim/official", "",
                                      "OFFICIAL", "system"])
def test_resolve_unknown_book_closed(bad_book):
    with pytest.raises(tm.TenantValidationError):
        gw.resolve(bad_book, "t1", "a1")


@pytest.mark.parametrize("bad_tenant", ["", " ", "T1", "t 1", "t.1",
                                        "official", "a" * 33])
def test_resolve_invalid_tenant_closed(bad_tenant):
    with pytest.raises(tm.TenantValidationError):
        gw.resolve("official", bad_tenant, "a1")


@pytest.mark.parametrize("bad_namespace", ["t/1", "sim/paper/t1",
                                           "paper/official/t1",
                                           "sim/legacy/t1/a/b/c"])
def test_resolve_invalid_namespace_closed(bad_namespace):
    with pytest.raises(tm.TenantValidationError):
        gw.resolve("official", bad_namespace)


def test_resolve_cross_book_layout_closed():
    # namespace 属 legacy 账，却请求 official 路由 → 跨账本 closed-on-failure
    with pytest.raises(tm.CrossTenantError):
        gw.resolve("official", "sim/legacy/t1/main")
    # 反向同理
    with pytest.raises(tm.CrossTenantError):
        gw.resolve("legacy", "sim/official/t1/a1")


def test_resolve_namespace_with_account_conflict_closed():
    with pytest.raises(facade.ExchangeFacadeError):
        gw.resolve("legacy", "sim/legacy/t1/main", "other")


def test_resolve_registry_enforces_isolation(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    reg = _registry(_ref("t1", "main", "legacy"), _ref("t2", "taishan", "legacy"))
    assert gw.resolve("legacy", "t1", "main", registry=reg)["registry_verified"] is True
    with pytest.raises(tm.CrossTenantError):
        gw.resolve("legacy", "t2", "main", registry=reg)
    with pytest.raises(tm.TenantNotFound):
        gw.resolve("legacy", "t9", "main", registry=reg)


def test_route_requires_tenant_for_account():
    with pytest.raises(gw.GatewayBadRequest):
        gw.route("official", account="a1")
    plain = gw.route("official")
    assert plain["resolved"] is False and plain["namespace"] is None
    assert plain["route"]["backend"] == "official_ledger_jsonl"
    with_t = gw.route("official", "t1", "a1")
    assert with_t["resolved"] is True
    assert with_t["namespace"] == "sim/official/t1/a1"


# ---------------------------------------------------------------------------
# health / accounts / snapshot：透传 facade（同一口径）
# ---------------------------------------------------------------------------
def test_health_passthrough_includes_boundaries(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    doc = gw.health()
    assert doc["status"] == "ok" and doc["ok"] is True
    assert doc["readonly"] is True and doc["writable"] is False
    assert doc["books"]["official"]["status"] == "ok"
    assert doc["books"]["official"]["rows"] == 3
    assert doc["books"]["legacy"]["status"] == "ok"
    assert doc["books"]["legacy"]["tables"]["account"] == 3
    assert doc["isolation"]["books_merged"] is False
    # 与 facade 同口径（不另造数据）
    assert doc["books"]["legacy"]["tables"] == facade.health()["books"]["legacy"]["tables"]


def test_accounts_passthrough_and_alias(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    doc = gw.accounts(book="legacy")
    assert doc["readonly"] is True and doc["writable"] is False
    assert doc["books"]["legacy"]["available"] is True
    ids = {entry["account_id"] for entry in doc["books"]["legacy"]["storage_accounts"]}
    assert {"main", "taishan"} <= ids
    assert gw.list_accounts is gw.accounts  # 同一实现，不做第二个写面
    all_books = gw.accounts()
    assert set(all_books["books"]) == set(tm.BOOKS)


def test_snapshot_passthrough_by_namespace_and_identity(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    by_ns = gw.snapshot(namespace="sim/legacy/t1/main")
    by_id = gw.snapshot(book="legacy", tenant="t1", account="main")
    assert by_ns["book"] == "legacy" and by_ns["official"] is None
    assert by_ns["legacy"]["account"]["account_id"] == "main"
    assert by_ns["legacy"]["positions_count"] == 1
    assert by_id["legacy"]["positions_count"] == by_ns["legacy"]["positions_count"]
    official = gw.snapshot(book="official", tenant="t1", account="a1")
    assert official["book"] == "official" and official["legacy"] is None
    assert official["official"]["rows"] == 3
    # 与 facade 同口径
    assert official["official"]["rows"] == facade.account_snapshot("t1", "a1")["official"]["rows"]


def test_snapshot_requires_identity_or_namespace():
    with pytest.raises(gw.GatewayBadRequest):
        gw.snapshot()
    with pytest.raises(facade.ExchangeFacadeError):
        gw.snapshot(namespace="sim/legacy/t1/main", tenant="t1")


def test_snapshot_unknown_and_cross_tenant_closed(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    with pytest.raises(tm.AccountNotFound):
        gw.snapshot(book="legacy", tenant="t1", account="ghost")
    reg = _registry(_ref("t1", "main", "legacy"), _ref("t2", "taishan", "legacy"))
    with pytest.raises(tm.CrossTenantError):
        gw.snapshot(book="legacy", tenant="t2", account="main", registry=reg)


def test_describe_declares_readonly_gateway():
    doc = gw.describe()
    assert doc["gateway"] == gw.GATEWAY_NAME
    assert tuple(doc["endpoints"]) == gw.ENDPOINTS
    assert doc["methods"] == ["GET"]
    assert doc["content_type"] == gw.CONTENT_TYPE
    assert doc["readonly"] is True and doc["writable"] is False
    assert doc["boundaries"]["new_port"] is False
    assert doc["boundaries"]["start_service"] is False


# ---------------------------------------------------------------------------
# handle_get：HTTP 纯函数契约
# ---------------------------------------------------------------------------
def test_handle_get_health_contract(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    status, doc = _call_json(gw.PATH_HEALTH)
    assert status == 200
    assert doc["gateway"] == gw.GATEWAY_NAME
    assert doc["kind"] == "health" and doc["ok"] is True
    assert doc["boundaries"]["readonly"] is True
    assert doc["data"]["status"] == "ok"
    assert doc["error"] == {}


def test_handle_get_route_plain_and_resolved(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    status, doc = _call_json(gw.PATH_ROUTE, {"book": "official"})
    assert status == 200 and doc["ok"] is True
    assert doc["data"]["resolved"] is False
    assert doc["data"]["route"]["backend"] == "official_ledger_jsonl"
    status, doc = _call_json(gw.PATH_ROUTE,
                             {"book": "legacy", "tenant": "t1",
                              "account": "main"})
    assert status == 200
    assert doc["data"]["resolved"] is True
    assert doc["data"]["namespace"] == "sim/legacy/t1/main"


def test_handle_get_accounts_and_query_normalization(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    status, doc = _call_json(gw.PATH_ACCOUNTS, {"book": ["legacy"]})
    assert status == 200
    assert doc["data"]["books"]["legacy"]["available"] is True
    # 原始 query string 也接受
    status, doc = _call_json(gw.PATH_ACCOUNTS, "book=legacy")
    assert status == 200
    # path 内联 query + 显式 query 合并（显式优先）
    status, doc = _call_json(gw.PATH_ACCOUNTS + "?book=official",
                             {"book": "legacy"})
    assert status == 200
    assert "legacy" in doc["data"]["books"]
    assert "official" not in doc["data"]["books"]


def test_handle_get_snapshot_by_tenant_and_namespace(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    status, doc = _call_json(gw.PATH_SNAPSHOT,
                             {"book": "legacy", "tenant": "t1",
                              "account": "main"})
    assert status == 200
    assert doc["data"]["legacy"]["account"]["account_id"] == "main"
    assert doc["data"]["status"] == "ok"
    status, doc = _call_json(gw.PATH_SNAPSHOT,
                             {"namespace": "sim/official/t1/a1"})
    assert status == 200
    assert doc["data"]["official"]["rows"] == 3


def test_handle_get_trailing_slash_and_inline_query(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    status, doc = _call_json(gw.PATH_ROUTE + "/?book=legacy&tenant=t1")
    assert status == 200
    assert doc["data"]["namespace"] == "sim/legacy/t1"


@pytest.mark.parametrize("bad_path", [
    "/paper_exchange", "/paper_exchange/nope", "/api/paper_exchange/health",
    "/paper_exchange/health/extra", "paper_exchange/health",
])
def test_handle_get_unknown_path_404(bad_path):
    status, ctype, body = gw.handle_get(bad_path)
    assert status == 404 and ctype == gw.CONTENT_TYPE
    doc = json.loads(body.decode("utf-8"))
    assert doc["ok"] is False and doc["error"]["code"] == "not_found"


@pytest.mark.parametrize("bad_path", ["", "   ", 123, None, b"/paper_exchange/health"])
def test_handle_get_bad_path_type_400(bad_path):
    status, ctype, body = gw.handle_get(bad_path)
    assert status == 400 and ctype == gw.CONTENT_TYPE
    doc = json.loads(body.decode("utf-8"))
    assert doc["ok"] is False and doc["error"]["code"] == "bad_request"


@pytest.mark.parametrize("bad_query", [123, 3.14, ["book=legacy"]])
def test_handle_get_bad_query_type_400(bad_query):
    status, _ctype, body = gw.handle_get(gw.PATH_HEALTH, bad_query)
    assert status == 400
    assert json.loads(body.decode("utf-8"))["error"]["code"] == "bad_request"


@pytest.mark.parametrize("path,query", [
    (gw.PATH_ROUTE, None),                               # 缺 book
    (gw.PATH_ROUTE, {"book": "paper"}),                  # 未知 book
    (gw.PATH_ROUTE, {"book": ""}),                       # 空 book
    (gw.PATH_SNAPSHOT, None),                            # 缺 namespace/tenant
    (gw.PATH_SNAPSHOT, {"namespace": "sim/legacy/t1/main",
                        "tenant": "t1"}),                # 互斥
    (gw.PATH_SNAPSHOT, {"namespace": "sim/legacy/t1/main",
                        "book": "legacy"}),              # 互斥
    (gw.PATH_SNAPSHOT, {"tenant": "T1"}),                # 非法 tenant
    (gw.PATH_SNAPSHOT, {"tenant": "t1", "book": "legacy",
                        "account": "combo_x@y"}),        # 文法非法 account
    (gw.PATH_ACCOUNTS, {"book": "paper"}),               # 未知 book
])
def test_handle_get_bad_request_400(path, query):
    status, ctype, body = gw.handle_get(path, query)
    assert status == 400 and ctype == gw.CONTENT_TYPE
    doc = json.loads(body.decode("utf-8"))
    assert doc["ok"] is False
    assert doc["error"]["code"]
    assert doc["boundaries"]["readonly"] is True


def test_handle_get_missing_data_is_200_but_not_faked(tmp_path, monkeypatch):
    """缺数据不造数：HTTP 200（请求已处理）+ data.status=unavailable + ok=false。"""
    p = _paths(tmp_path)  # 不写样张 → 两本账存储均缺失
    _patch_paths(monkeypatch, p)
    status, doc = _call_json(gw.PATH_SNAPSHOT,
                             {"namespace": "sim/legacy/t1/main"})
    assert status == 200
    assert doc["data"]["status"] == "unavailable"
    assert doc["data"]["unavailable"]["code"] == "legacy_db_missing"
    assert doc["data"]["legacy"] is None  # 不造空账
    assert doc["ok"] is False  # 信封镜像 data.ok（不假装成功）
    status, doc = _call_json(gw.PATH_HEALTH)
    assert status == 200
    assert doc["data"]["status"] == "unavailable"
    assert doc["ok"] is False


def test_handle_get_internal_error_500_json(monkeypatch):
    def boom():
        raise RuntimeError("模拟网关内部异常")

    monkeypatch.setattr(gw, "health", boom)
    status, ctype, body = gw.handle_get(gw.PATH_HEALTH)
    assert status == 500 and ctype == gw.CONTENT_TYPE
    doc = json.loads(body.decode("utf-8"))
    assert doc["ok"] is False
    assert doc["error"]["code"] == "internal_error"
    assert "RuntimeError" in doc["error"]["message"]
    assert "Traceback" not in body.decode("utf-8")


def test_handle_get_never_returns_other_service_paths():
    """只认四条契约路径：不做任何反向代理/透传（防止成为越权通道）。"""
    for path in ("/api/quant/paper", "/health", "/inbox/receipt/1", "/llm/chat"):
        status, _ctype, body = gw.handle_get(path)
        assert status == 404
        assert json.loads(body.decode("utf-8"))["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# 只读守卫（AST / 零写入 / 不起服务）
# ---------------------------------------------------------------------------
def test_module_ast_readonly_no_server_no_write_paths():
    source = MODULE_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    allowed = {"__future__", "argparse", "json", "pathlib", "sys",
               "urllib.parse", "agent_platform.paper"}
    assert imported <= allowed, imported
    banned_roots = ("http.server", "socketserver", "socket", "threading",
                    "subprocess", "shutil", "requests", "urllib.request",
                    "engine", "risk_gate", "paper_book", "k_stock",
                    "dashboard", "portal", "flask", "fastapi", "uvicorn")
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
    assert "open" not in called_names
    assert not ({"eval", "exec", "__import__"} & called_names)
    forbidden_attrs = {
        "write_text", "write_bytes", "append_ledger", "save_state",
        "apply_decision", "execute_order", "place_order", "commit",
        "executemany", "executescript", "mkdir", "makedirs", "remove",
        "unlink", "rename", "serve_forever", "bind", "listen", "listen_and_serve",
    }
    assert not (forbidden_attrs & called_attrs), forbidden_attrs & called_attrs

    # 无服务/无端口痕迹（不新增端口，也不替 HTTP 层起服务）
    for token in ("serve_forever", "HTTPServer", "ThreadingHTTPServer",
                  "BaseHTTPRequestHandler", "register_routes", "add_url_rule",
                  "127.0.0.1", "0.0.0.0", "socket(", "bind("):
        assert token not in source, token

    # 网关只 import 只读 facade + tenant_model；不直接 import 任何 HTTP 层
    assert "exchange_facade" in source

    for banned_name in ("append_ledger", "save_state", "apply_decision",
                        "execute_order", "place_order", "serve",
                        "build_server", "PORT"):
        assert not hasattr(gw, banned_name), banned_name


def test_boundaries_are_readonly_only():
    assert gw.GATEWAY_BOUNDARIES["readonly"] is True
    assert gw.GATEWAY_BOUNDARIES["writable"] is False
    assert gw.GATEWAY_BOUNDARIES["trading_permitted"] is False
    for key in ("ledger_write", "disk_write", "place_order", "start_matching",
                "migrate_ledger", "new_port", "start_service"):
        assert gw.GATEWAY_BOUNDARIES[key] is False, key


def test_handle_get_does_not_write_storage(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    before = _dir_digest(tmp_path)
    gw.handle_get(gw.PATH_HEALTH)
    gw.handle_get(gw.PATH_ROUTE, {"book": "official", "tenant": "t1",
                                  "account": "a1"})
    gw.handle_get(gw.PATH_ACCOUNTS, {"book": "legacy"})
    gw.handle_get(gw.PATH_SNAPSHOT, {"namespace": "sim/legacy/t1/main"})
    gw.handle_get(gw.PATH_SNAPSHOT, {"book": "official", "tenant": "t1",
                                     "account": "a1"})
    gw.resolve("legacy", "t1", "main")
    gw.health()
    gw.accounts()
    gw.snapshot(namespace="sim/legacy/t1/main")
    assert _dir_digest(tmp_path) == before  # 字节级零写入
    assert not list(tmp_path.glob("*.db-*"))  # 无 -wal/-shm 旁文件


def test_real_repo_handle_get_smoke_skip_when_data_absent():
    """主仓实数据 smoke（worktree 内 data/ 被 gitignore → skip，不造数）。"""
    official = REPO / "agent_platform" / "data" / "paper" / "paper_ledger.jsonl"
    legacy = REPO / "k_stock" / "data" / "paper.db"
    if not official.exists() and not legacy.exists():
        pytest.skip("当前工作区无运行时账本数据（data/ 不进 git）")
    status, ctype, body = gw.handle_get(gw.PATH_HEALTH)
    assert status == 200 and ctype == gw.CONTENT_TYPE
    doc = json.loads(body.decode("utf-8"))
    assert doc["gateway"] == gw.GATEWAY_NAME
    assert doc["data"]["status"] in facade.STATUSES
    if legacy.exists():
        status, doc = _call_json(gw.PATH_SNAPSHOT,
                                 {"namespace": "sim/legacy/smoke/main"})
        assert status == 200
        assert doc["data"]["book"] == "legacy"
        assert doc["data"]["legacy"]["account"]["account_id"] == "main"


# ---------------------------------------------------------------------------
# CLI（只读；不绑端口/不起服务）
# ---------------------------------------------------------------------------
def test_cli_health_and_get(tmp_path, monkeypatch, capsys):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    assert gw.main(["health"]) == 0
    health_doc = json.loads(capsys.readouterr().out)
    assert health_doc["status"] == "ok"
    assert gw.main(["get", gw.PATH_ROUTE, "--query", "book=legacy"]) == 0
    body_doc = json.loads(capsys.readouterr().out)
    assert body_doc["data"]["book"] == "legacy"


def test_cli_snapshot_and_accounts(tmp_path, monkeypatch, capsys):
    p = _paths(tmp_path)
    _write_both(p)
    _patch_paths(monkeypatch, p)
    assert gw.main(["snapshot", "sim/legacy/t1/main"]) == 0
    snap_doc = json.loads(capsys.readouterr().out)
    assert snap_doc["legacy"]["account"]["account_id"] == "main"
    assert gw.main(["accounts", "--book", "legacy"]) == 0
    accounts_doc = json.loads(capsys.readouterr().out)
    assert accounts_doc["books"]["legacy"]["available"] is True


def test_cli_closed_identity_exit_code_two(capsys):
    assert gw.main(["snapshot", "sim/paper/t1"]) == 2
    error_doc = json.loads(capsys.readouterr().out)
    assert error_doc["ok"] is False
    assert error_doc["error_type"] == "TenantValidationError"
    # get 走 HTTP 语义：4xx → 退出码 1（响应体仍为 JSON 错误）
    assert gw.main(["get", "/paper_exchange/nope"]) == 1
    missing_doc = json.loads(capsys.readouterr().out)
    assert missing_doc["error"]["code"] == "not_found"
