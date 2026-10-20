# -*- coding: utf-8 -*-
"""订阅批量列表端点离线契约测试（飞书中台 P2.6 · FE-B 工位）。

沿 test_subscription_store.py / test_my_subscription.py 判例：
env SUBSCRIPTION_DB 把 DB 锚指向 tmp 临时文件（零碰生产），
页面/端点契约读文件断言、零网络、零服务启动。

覆盖派工单 FE-B 清单：空库/有数据/ORDER BY open_id 稳定序/
limit 钳制 [1,200]/异常 closed on failure（ok=False 不抛）/
读路径不建库/used_today 口径/portal_api branch 存在且零写参数/
sub_admin.html 引用端点且全 fetch 显式 GET、无 POST。
"""

import re
import sqlite3
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent
sys.path.insert(0, str(PORTAL))
import subscription_store as ss

API = PORTAL / "portal_api.py"
PAGE = PORTAL / "static" / "sub_admin.html"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """SUBSCRIPTION_DB 指向 tmp 临时文件，零碰生产库。"""
    p = tmp_path / "sub_list_test.sqlite3"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(p))
    monkeypatch.delenv("SUBSCRIPTION_OWN_DOMAIN", raising=False)
    yield p


# ---------------------------------------------------------------------------
# list_subscribers 契约
# ---------------------------------------------------------------------------

def test_01_empty_db(db):
    """空库（已建表无行）→ ok=True/count=0/subscribers=[]，不冒充有数据。"""
    ss._connect(create=True).close()
    r = ss.list_subscribers()
    assert r == {"ok": True, "count": 0, "subscribers": []}


def test_02_with_data_fields_and_stable_order(db):
    """有数据 → 字段齐全、ORDER BY open_id 稳定序、无订阅行诚实空。"""
    ss._connect(create=True).close()
    ss.upsert_subscriber("ou_b2", "乙二")
    ss.upsert_subscriber("ou_a1", "甲一")
    ss.grant_subscription("ou_a1", "basic_monthly", 30)
    r = ss.list_subscribers()
    assert r["ok"] is True
    assert r["count"] == len(r["subscribers"]) == 2
    assert [s["open_id"] for s in r["subscribers"]] == ["ou_a1", "ou_b2"]
    a = r["subscribers"][0]
    assert set(a) == {"open_id", "display_name", "plan_id", "status",
                      "start", "end", "daily_quota", "used_today"}
    assert a["plan_id"] == "basic_monthly" and a["status"] == "active"
    assert a["daily_quota"] == 20 and a["used_today"] == 0
    assert a["start"] and a["end"]
    b = r["subscribers"][1]  # 无订阅行：订阅字段诚实空，不编造
    assert b["plan_id"] is None and b["status"] is None
    assert b["start"] is None and b["end"] is None
    assert b["daily_quota"] is None and b["used_today"] == 0


def test_03_store_missing_no_db_created(db, monkeypatch):
    """store 文件缺失 → ok=False/store_unavailable，且读路径不建库。"""
    missing = db.parent / "never.sqlite3"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(missing))
    assert not missing.exists()
    r = ss.list_subscribers()
    assert r == {"ok": False, "error": "store_unavailable"}
    assert not missing.exists()  # 调用后无文件（读路径不建库）


def test_04_store_broken_closed_on_failure(db, monkeypatch):
    """store 异常（损坏文件）→ ok=False 不抛（closed on failure）。"""
    bad = db.parent / "bad.sqlite3"
    bad.write_bytes(b"not a sqlite file at all")
    monkeypatch.setenv("SUBSCRIPTION_DB", str(bad))
    r = ss.list_subscribers()
    assert r["ok"] is False and "store_unavailable" in r["error"]


def test_05_limit_clamp_low_and_nonint(db):
    """limit 钳制：0→1（下界）、负数→1、非整数→默认 50。"""
    ss._connect(create=True).close()
    for i in range(3):
        ss.upsert_subscriber("ou_c%d" % i, "用户%d" % i)
    r = ss.list_subscribers(limit=0)
    assert r["count"] == 1 and len(r["subscribers"]) == 1
    r = ss.list_subscribers(limit=-5)
    assert r["count"] == 1
    r = ss.list_subscribers(limit="abc")
    assert r["count"] == 3  # 非法回落默认 50，3 行全出


def test_06_limit_clamp_upper_200(db):
    """limit 上界 200：201 个订阅者只出 200 行，count 与列表严格一致。"""
    ss._connect(create=True).close()
    for i in range(201):
        ss.upsert_subscriber("ou_x%03d" % i, "批量%d" % i)
    r = ss.list_subscribers(limit=999)
    assert r["count"] == 200 == len(r["subscribers"])
    assert [s["open_id"] for s in r["subscribers"]] == \
        ["ou_x%03d" % i for i in range(200)]  # 稳定序不被截断打乱
    r_all = ss.list_subscribers(limit=201)
    assert r_all["count"] == 200  # 201 → 钳到 200


def test_07_used_today_matches_is_entitled_pool(db):
    """used_today 口径：当日 allowed=1 计数（跨前缀订阅者级合计），
    与 is_entitled 的当日额度计数同源；allowed=0 不计、他日不计。"""
    from datetime import datetime, timedelta
    ss._connect(create=True).close()
    ss.upsert_subscriber("ou_u1", "用一")
    ss.grant_subscription("ou_u1", "basic_monthly", 30)
    ss.entitlement_log("ou_u1", "news.brief", True, "subscription_active")
    ss.entitlement_log("ou_u1", "market.morning", True, "subscription_active")
    ss.entitlement_log("ou_u1", "news.brief", False, "daily_quota_exhausted")
    r = ss.list_subscribers()
    assert r["subscribers"][0]["used_today"] == 2  # allowed=0 不计
    # 同源锚：同日再放行 19 条 news.*（news 前缀合计 1+19=20 达上限）后
    # is_entitled 判额度尽
    for _ in range(19):
        ss.entitlement_log("ou_u1", "news.brief", True, "subscription_active")
    now = datetime.now() + timedelta(minutes=1)
    ok, reason = ss.is_entitled("ou_u1", "news.brief", now=now)
    assert ok is False and reason == "daily_quota_exhausted"
    r2 = ss.list_subscribers()
    assert r2["subscribers"][0]["used_today"] == 21  # news 20 + market 1


def test_08_signature_and_all_and_readonly(db):
    """签名逐字锚 + __all__ 追加 + 函数体纯只读（无 INSERT/UPDATE/CREATE）。"""
    src = _read(PORTAL / "subscription_store.py")
    assert "def list_subscribers(limit=50, now=None):" in src, \
        "契约签名必须逐字为 def list_subscribers(limit=50, now=None):"
    assert '"list_subscribers"' in src
    import ast
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef)
              and n.name == "list_subscribers")
    body_src = ast.get_source_segment(src, fn)
    for banned in ("INSERT INTO", "UPDATE ", "CREATE TABLE"):
        assert banned not in body_src, f"list_subscribers 应纯只读，发现 {banned}"


# ---------------------------------------------------------------------------
# portal_api.py branch 契约（读文件断言）
# ---------------------------------------------------------------------------

def test_09_portal_api_list_branch():
    """portal_api 有 /api/subscription/list branch：调 list_subscribers、
    ?limit= 可选默认 50 钳 200、零写参数、不调任何写函数。"""
    src = _read(API)
    assert 'path == "/api/subscription/list"' in src, "缺 list branch"
    m = re.search(
        r'path == "/api/subscription/list".*?(?=\n        elif path == )',
        src, re.S)
    assert m, "list branch 结构异常"
    block = m.group(0)
    assert "list_subscribers" in block
    assert "_limit = 50" in block and "200" in src, "limit 默认/钳制缺失"
    for banned in ("upsert_subscriber", "grant_subscription",
                   "expire_sweep", "entitlement_log"):
        assert banned not in block, f"list branch 不得调用写函数 {banned}"
    assert "POST" not in block, "list branch 应只处理 GET 语境"


def test_10_sub_admin_references_endpoint_get_only():
    """sub_admin.html：引用 /api/subscription/list、全 fetch 显式 GET、
    零 POST、含诚实空态文案（账本不可用/暂无订阅者）。"""
    src = _read(PAGE)
    assert "/api/subscription/list" in src, "页面必须引用批量列表端点"
    assert "loadSubscribers" in src
    assert "POST" not in src, "页面不得出现 POST（零写操作契约）"
    fetches = re.findall(r"fetch\(([^)]*)\)", src)
    assert fetches, "页面应有 fetch 调用"
    for f in fetches:
        assert "'GET'" in f or '"GET"' in f, f"fetch 非 GET: {f}"
    assert "账本不可用" in src and "暂无订阅者" in src, "缺诚实空态文案"
    assert "挂账" not in src, "挂账文案应已改为已上线口径"
    assert "open-id-input" in src, "逐查输入框必须保留"


def test_11_sub_admin_debt_wording_replaced():
    """:86/119 挂账口径已改为已上线，且全集表列头齐全。"""
    src = _read(PAGE)
    assert "批量列表已上线" in src, ":86 挂账文案未改已上线口径"
    for col in ("open_id", "显示名", "档位", "状态", "到期", "今日已用"):
        assert col in src, f"全集表缺列头 {col}"
