# -*- coding: utf-8 -*-
"""订阅账本最小集单测（飞书中台 P2 · FC-B 工位）。

沿 test_api_notify.py 判例：env SUBSCRIPTION_DB 把 DB 锚指向 tmp 临时文件，
零碰生产（portal/work/subscription.sqlite3 运行时数据不动）。

覆盖派工单 FC-B 清单：建表幂等、判定四分支（无订阅/生效/过期/额度尽）、
owner 豁免、store 缺失 fail-open、env 覆盖 DB 路径、expire_sweep、
entitlement_log 落行、并发只读安全说明、种子档位、额度按 kind 前缀计数。
"""

import sqlite3
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subscription_store as ss


@pytest.fixture
def db(tmp_path, monkeypatch):
    """SUBSCRIPTION_DB 指向 tmp 临时文件，保证 env 覆盖生效且零碰生产。"""
    p = tmp_path / "sub_test.sqlite3"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(p))
    monkeypatch.delenv("SUBSCRIPTION_OWN_DOMAIN", raising=False)
    ss._connect(create=True)  # 先建库：后续用例考的是"有账本但无订阅"，非 fail-open
    yield p
    # 临时文件随 tmp_path 清理，无需手工删除


def make_conn():
    return ss._connect(create=True)


def table_names(path):
    conn = sqlite3.connect(str(path))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# 建表 / 路径 / 种子
# ---------------------------------------------------------------------------


def test_01_create_tables_idempotent(db):
    """首次连接自动建 5 表；重复连接幂等不报错。"""
    make_conn()
    tables = table_names(db)
    assert tables == {
        "subscriber",
        "plan",
        "subscription",
        "entitlement_log",
        "payment_order",
    }
    make_conn()  # 二次连接幂等
    assert table_names(db) == tables


def test_02_env_override_db_path(tmp_path, monkeypatch):
    """env SUBSCRIPTION_DB 覆盖默认路径（调用时读取，非 import 时缓存）。"""
    p = tmp_path / "override.sqlite3"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(p))
    assert ss._db_path() == p
    ss.upsert_subscriber("ou_env", "env用户")
    assert p.exists()
    # 换一个路径，锚随 env 移动
    p2 = tmp_path / "override2.sqlite3"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(p2))
    assert ss._db_path() == p2
    assert not p2.exists() or table_names(p2)  # 未写则不存在，写了则有效


def test_03_seed_plans_present(db):
    """首建 plan 表写入两条占位档位：internal（无限）+ basic_monthly（placeholder）。"""
    make_conn()
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT plan_id, daily_quota, price, cycle_days FROM plan ORDER BY plan_id"
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    by_id = {r[0]: r for r in rows}
    assert by_id["internal"][1] == 0  # 0 = 不限额
    assert "placeholder" in by_id["basic_monthly"][2]  # 价格待老田定价标注
    assert by_id["basic_monthly"][3] == 30
    # 幂等：再次连接不重复种
    make_conn()
    conn = sqlite3.connect(str(db))
    cnt = conn.execute("SELECT COUNT(*) FROM plan").fetchone()[0]
    conn.close()
    assert cnt == 2


# ---------------------------------------------------------------------------
# 判定四分支 + 豁免 + fail-open
# ---------------------------------------------------------------------------


def _grant_here(open_id, plan_id="basic_monthly", days=30, start=None):
    """测试内直接插一行订阅（绕过 grant_subscription 的 now 取当前）。"""
    start = start or datetime(2026, 10, 20, 8, 0, 0)
    end = start + timedelta(days=days)
    with make_conn() as conn:
        conn.execute(
            "INSERT INTO subscription(subscriber_open_id, plan_id, start, end,"
            " status) VALUES (?,?,?,?,?)",
            (
                open_id,
                plan_id,
                start.strftime("%Y-%m-%d %H:%M:%S"),
                end.strftime("%Y-%m-%d %H:%M:%S"),
                "active",
            ),
        )
        conn.commit()


def test_04_judge_no_subscription(db):
    """无订阅行 → (False, no_subscription)。"""
    ok, reason = ss.is_entitled("ou_none", "news.brief")
    assert ok is False and reason == "no_subscription"


def test_05_judge_active(db):
    """生效订阅且在期内 → (True, subscription_active)。"""
    _grant_here("ou_ok")
    now = datetime(2026, 10, 20, 9, 0, 0)
    ok, reason = ss.is_entitled("ou_ok", "news.brief", now=now)
    assert ok is True and reason == "subscription_active"


def test_06_judge_expired(db):
    """期外（end<now）→ (False, subscription_expired)。"""
    _grant_here("ou_old", days=1)
    now = datetime(2026, 10, 25, 9, 0, 0)
    ok, reason = ss.is_entitled("ou_old", "news.brief", now=now)
    assert ok is False and reason == "subscription_expired"


def test_07_judge_quota_exhausted(db):
    """当日同前缀放行计数达 daily_quota → (False, daily_quota_exhausted)。"""
    _grant_here("ou_quota")
    now = datetime(2026, 10, 20, 9, 0, 0)
    for _ in range(20):  # basic_monthly 日 20 条
        ss.entitlement_log(
            "ou_quota", "news.brief", True, "subscription_active", now=now
        )
    ok, reason = ss.is_entitled("ou_quota", "news.brief", now=now)
    assert ok is False and reason == "daily_quota_exhausted"


def test_08_owner_domain_exempt(db, monkeypatch):
    """owner/ops 豁免：名单内 open_id 直接 (True, domain_exempt)，无订阅也放行。"""
    monkeypatch.setenv("SUBSCRIPTION_OWN_DOMAIN", "ou_owner_a, ou_owner_b")
    ok, reason = ss.is_entitled("ou_owner_a", "news.brief")
    assert ok is True and reason == "domain_exempt"
    ok, reason = ss.is_entitled("ou_owner_b", "anything.else")
    assert ok is True and reason == "domain_exempt"
    # 名单外仍走订阅判定
    ok, reason = ss.is_entitled("ou_other", "news.brief")
    assert ok is False and reason == "no_subscription"


def test_09_store_missing_fail_open(db, monkeypatch):
    """store 文件缺失 → (True, store_unavailable_fail_open)，且不建库。"""
    monkeypatch.setenv(
        "SUBSCRIPTION_DB", str(tmp_path_missing := db.parent / "never.sqlite3")
    )
    assert not tmp_path_missing.exists()
    ok, reason = ss.is_entitled("ou_any", "news.brief")
    assert ok is True and reason == "store_unavailable_fail_open"
    assert not tmp_path_missing.exists()  # 读路径不建库


def test_10_store_broken_fail_open(db, monkeypatch):
    """store 异常（损坏文件）→ (True, store_unavailable_fail_open)。"""
    bad = db.parent / "bad.sqlite3"
    bad.write_bytes(b"not a sqlite file at all")
    monkeypatch.setenv("SUBSCRIPTION_DB", str(bad))
    ok, reason = ss.is_entitled("ou_any", "news.brief")
    assert ok is True and reason == "store_unavailable_fail_open"


# ---------------------------------------------------------------------------
# 写操作契约
# ---------------------------------------------------------------------------


def test_11_upsert_subscriber(db):
    """upsert：首插建行，再插更新 display_name，不重复建行。"""
    ss.upsert_subscriber("ou_u1", "张三")
    ss.upsert_subscriber("ou_u1", "张三丰")  # 非空名更新
    ss.upsert_subscriber("ou_u1")  # 空名不覆盖
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT display_name FROM subscriber WHERE open_id='ou_u1'"
    ).fetchall()
    conn.close()
    assert rows == [("张三丰",)]  # 锚点：count==1 且名字为最后一次非空


def test_12_grant_subscription(db):
    """grant：档位存在时插入生效行；档位不存在 ValueError；days<=0 ValueError。"""
    ss.upsert_subscriber("ou_g1", "谷一")
    info = ss.grant_subscription("ou_g1", "basic_monthly", 30)
    assert info["status"] == "active"
    ok, reason = ss.is_entitled("ou_g1", "news.brief")
    assert ok is True and reason == "subscription_active"
    with pytest.raises(ValueError):
        ss.grant_subscription("ou_g1", "no_such_plan", 30)
    with pytest.raises(ValueError):
        ss.grant_subscription("ou_g1", "basic_monthly", 0)


def test_13_expire_sweep(db):
    """sweep：把 end<now 的生效行置 expired，返回行数；期内行不动。"""
    _grant_here("ou_s1", days=1, start=datetime(2026, 10, 1))  # 已过期
    _grant_here("ou_s2", days=30, start=datetime(2026, 10, 15))  # 仍生效
    n = ss.expire_sweep(now=datetime(2026, 10, 20))
    assert n == 1
    conn = sqlite3.connect(str(db))
    st1 = conn.execute(
        "SELECT status FROM subscription WHERE subscriber_open_id='ou_s1'"
    ).fetchone()[0]
    st2 = conn.execute(
        "SELECT status FROM subscription WHERE subscriber_open_id='ou_s2'"
    ).fetchone()[0]
    conn.close()
    assert st1 == "expired" and st2 == "active"


def test_14_entitlement_log_row(db):
    """log 落行：allowed 布尔归一为 0/1，锚点 count==1。"""
    ts = ss.entitlement_log("ou_l1", "news.brief", False, "no_subscription")
    assert len(ts) == 19  # 'YYYY-MM-DD HH:MM:SS'
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT open_id, kind, allowed, reason FROM entitlement_log"
    ).fetchall()
    conn.close()
    assert rows == [("ou_l1", "news.brief", 0, "no_subscription")]


def test_15_quota_prefix_isolation(db):
    """额度按 kind 前缀计数：news.* 用满不影响 market.*（同日不同池）。"""
    _grant_here("ou_p1")
    now = datetime(2026, 10, 20, 9, 0, 0)
    for _ in range(20):
        ss.entitlement_log("ou_p1", "news.brief", True, "subscription_active", now=now)
    ok, reason = ss.is_entitled("ou_p1", "news.brief", now=now)
    assert ok is False and reason == "daily_quota_exhausted"
    # 不同前缀不受影响
    ok, reason = ss.is_entitled("ou_p1", "market.morning", now=now)
    assert ok is True and reason == "subscription_active"


def test_16_internal_plan_unlimited(db):
    """internal 档 daily_quota=0 不限额：超大量放行调用仍 entitled。"""
    _grant_here("ou_in1", plan_id="internal")
    now = datetime(2026, 10, 20, 9, 0, 0)
    for _ in range(25):
        ss.entitlement_log("ou_in1", "news.brief", True, "subscription_active")
    ok, reason = ss.is_entitled("ou_in1", "news.brief", now=now)
    assert ok is True and reason == "subscription_active"


# ---------------------------------------------------------------------------
# 并发只读安全（说明性验证）
# ---------------------------------------------------------------------------


def test_17_concurrent_readonly_threads(db):
    """并发只读安全：多线程同时 is_entitled 无异常、结果一致。

    说明（派工单 FC-B）：本模块不用 mode=ro，因为还承担 entitlement_log
    写入；单连接即开即关 + sqlite timeout=10，单库单连接锁可接受。
    """
    _grant_here("ou_t1")
    now = datetime(2026, 10, 20, 9, 0, 0)
    results = []
    errors = []

    def worker():
        try:
            results.append(ss.is_entitled("ou_t1", "news.brief", now=now))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert results == [(True, "subscription_active")] * 8


# ---------------------------------------------------------------------------
# P3.9-lite N-A 追加：offset 默认值不改变旧调用方结果
# ---------------------------------------------------------------------------


def test_18_offset_default_keeps_legacy_result(db):
    """offset 缺省 == offset=0：旧调用方（关键字 / 位置）结果逐字段不变。"""
    for i in range(3):
        ss.upsert_subscriber("ou_off%d" % i, "偏%d" % i)
    _grant_here("ou_off0")
    legacy = ss.list_subscribers(limit=2)
    explicit = ss.list_subscribers(limit=2, offset=0)
    assert legacy["ok"] is True and explicit["ok"] is True
    assert legacy["subscribers"] == explicit["subscribers"]
    assert legacy["count"] == explicit["count"] == 2
    assert legacy["offset"] == explicit["offset"] == 0
    assert legacy["has_more"] is True  # 2 行 == 生效 limit 2，宁松不紧
    # 位置传参仍表示 limit（offset 在签名末尾，不抢位）
    assert (
        ss.list_subscribers(200)["count"]
        == ss.list_subscribers(limit=200)["count"]
        == 3
    )


def test_19_offset_skips_prefix_without_touching_rows(db):
    """offset 只跳过前缀行，行内字段与不跳时完全一致。"""
    for i in range(4):
        ss.upsert_subscriber("ou_pg%d" % i, "页%d" % i)
    _grant_here("ou_pg2")
    full = ss.list_subscribers(limit=4)["subscribers"]
    tail = ss.list_subscribers(limit=2, offset=2)
    assert tail["subscribers"] == full[2:4]
    assert tail["count"] == 2 and tail["has_more"] is True
    assert tail["subscribers"][0]["plan_id"] == "basic_monthly"
