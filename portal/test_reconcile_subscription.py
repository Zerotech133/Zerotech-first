# -*- coding: utf-8 -*-
"""订阅账本勾稽自检单测（P3.7-lite · L-D / WO-FS-104）。

零碰生产：每个用例用 tmp_path 建库，并通过 env ``SUBSCRIPTION_DB`` 把
``subscription_store._db_path()`` 锚到临时文件（沿 test_subscription_store.py 判例）。
建表复用 ``subscription_store._connect(create=True)``——此时 env 已指向 tmp，
不会落到 portal/work/subscription.sqlite3。

覆盖：干净库零 issue／孤儿 open_id／未知 plan／start>end／active 但已过期／
expired 但未到期／paid 无订阅漏单（含反向：有订阅不报）／orphan_entitlement
（含 allowed=0 不报、news.* 前缀放行不报）／表缺失注入 check_failed 且其余
校验继续／库缺失全量 check_failed／退出码 0/1／运行前后库文件字节不变（只读实证）。
"""

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "portal"), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import subscription_store as ss  # noqa: E402
import reconcile_subscription as rc  # noqa: E402

NOW = "2026-10-20 12:00:00"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """临时库：5 表 + 种子档位（internal ["*"] / basic_monthly ["news.*"]）。"""
    p = tmp_path / "ledger.sqlite3"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(p))
    monkeypatch.delenv("SUBSCRIPTION_OWN_DOMAIN", raising=False)
    ss._connect(create=True).close()
    return p


def run_sql(path, statement, params=()):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def add_subscriber(path, open_id):
    run_sql(
        path,
        "INSERT INTO subscriber(open_id, display_name, created_at) VALUES (?,?,?)",
        (open_id, "测试用户", NOW),
    )


def add_sub(
    path,
    open_id,
    plan_id="basic_monthly",
    start="2026-10-01 00:00:00",
    end="2026-11-01 00:00:00",
    status="active",
):
    run_sql(
        path,
        "INSERT INTO subscription(subscriber_open_id, plan_id, start,"
        " end, status) VALUES (?,?,?,?,?)",
        (open_id, plan_id, start, end, status),
    )


def add_log(path, open_id, kind, allowed=1, reason="subscription_active"):
    run_sql(
        path,
        "INSERT INTO entitlement_log(ts, open_id, kind, allowed, reason)"
        " VALUES (?,?,?,?,?)",
        (NOW, open_id, kind, allowed, reason),
    )


def add_order(path, order_id, open_id, status="paid"):
    run_sql(
        path,
        "INSERT INTO payment_order(order_id, open_id, amount, channel,"
        " status, created_at) VALUES (?,?,?,?,?,?)",
        (order_id, open_id, "0.00", "test", status, NOW),
    )


def codes(result):
    return [i["code"] for i in result["issues"]]


# ---------------------------------------------------------------------------
# 1. 干净库
# ---------------------------------------------------------------------------


def test_01_clean_ledger_is_ok(db):
    """干净库（只有种子档位）→ ok=True、零 issue、checked=2 行 plan。"""
    result = rc.reconcile(now=NOW)
    assert result == {"ok": True, "checked": 2, "issues": []}


# ---------------------------------------------------------------------------
# 2~7. 六类账本内容问题
# ---------------------------------------------------------------------------


def test_02_orphan_open_id(db):
    """subscription 引用的 open_id 不在 subscriber → orphan_open_id。"""
    add_sub(db, "ou_ghost", "basic_monthly")
    result = rc.reconcile(now=NOW)
    assert result["ok"] is False
    assert codes(result) == ["orphan_open_id"]
    assert "ou_ghost" in result["issues"][0]["detail"]


def test_03_unknown_plan(db):
    """plan_id 不在 plan 表 → unknown_plan。"""
    add_subscriber(db, "ou_a")
    add_sub(db, "ou_a", "premium_yearly")
    result = rc.reconcile(now=NOW)
    assert result["ok"] is False
    assert codes(result) == ["unknown_plan"]
    assert "premium_yearly" in result["issues"][0]["detail"]


def test_04_bad_range(db):
    """start > end → bad_range。"""
    add_subscriber(db, "ou_a")
    add_sub(db, "ou_a", start="2026-11-10 00:00:00", end="2026-11-01 00:00:00")
    result = rc.reconcile(now=NOW)
    assert codes(result) == ["bad_range"]


def test_05_stale_active(db):
    """status=active 但 end < now → stale_active（待清扫），不算矛盾。"""
    add_subscriber(db, "ou_a")
    add_sub(db, "ou_a", start="2026-09-01 00:00:00", end="2026-10-01 00:00:00")
    result = rc.reconcile(now=NOW)
    assert codes(result) == ["stale_active"]


def test_06_status_contradiction(db):
    """status=expired 但 end >= now → status_contradiction。"""
    add_subscriber(db, "ou_a")
    add_sub(
        db,
        "ou_a",
        start="2026-10-01 00:00:00",
        end="2026-10-25 00:00:00",
        status="expired",
    )
    result = rc.reconcile(now=NOW)
    assert codes(result) == ["status_contradiction"]


def test_07_paid_without_subscription(db):
    """已支付订单的 open_id 无任何订阅 → 漏单；有订阅则不报；未支付不报。"""
    add_order(db, "ORD-1", "ou_paid")
    add_order(db, "ORD-2", "ou_ok")
    add_order(db, "ORD-3", "ou_ok", status="pending")
    add_subscriber(db, "ou_ok")
    add_sub(db, "ou_ok")

    result = rc.reconcile(now=NOW)
    paid = [i for i in result["issues"] if i["code"] == "paid_without_subscription"]
    assert len(paid) == 1
    assert "ORD-1" in paid[0]["detail"]
    assert "ou_paid" in paid[0]["detail"]


# ---------------------------------------------------------------------------
# 8. orphan_entitlement
# ---------------------------------------------------------------------------


def test_08_orphan_entitlement_kind_not_declared(db):
    """plan.kinds 不覆盖的放行 kind 要报；news.* 覆盖与 allowed=0 不报。

    去掉 internal（kinds=["*"]）以隔离通配档位对本项的兜底，
    否则任何 kind 都会被 "*" 覆盖（见工具 docstring 的已知边界）。
    """
    run_sql(db, "DELETE FROM plan WHERE plan_id='internal'")
    add_subscriber(db, "ou_a")
    add_log(db, "ou_a", "stock.daily")
    add_log(db, "ou_a", "news.brief")
    add_log(db, "ou_a", "stock.denied", allowed=0)

    result = rc.reconcile(now=NOW)
    ent = [i for i in result["issues"] if i["code"] == "orphan_entitlement"]
    assert len(ent) == 1
    assert "stock.daily" in ent[0]["detail"]
    assert "news.brief" not in ent[0]["detail"]
    assert "stock.denied" not in ent[0]["detail"]


# ---------------------------------------------------------------------------
# 9~10. fail-visible
# ---------------------------------------------------------------------------


def test_09_missing_table_yields_check_failed_and_continues(db):
    """表缺失 → check_failed；其余校验仍继续跑并报出真实问题。"""
    add_sub(db, "ou_ghost")  # 故意制造孤儿，验证不是短路退出
    run_sql(db, "DROP TABLE payment_order")

    result = rc.reconcile(now=NOW)
    assert result["ok"] is False
    assert "orphan_open_id" in codes(result)
    failed = [i for i in result["issues"] if i["code"] == "check_failed"]
    assert failed, "表缺失必须落 check_failed，不能静默归零"
    assert any("paid_without_subscription" in i["detail"] for i in failed)


def test_10_missing_db_yields_check_failed_not_clean(tmp_path, monkeypatch):
    """库文件不存在 → 每个校验项各一条 check_failed，ok=False、checked=0。"""
    monkeypatch.setenv("SUBSCRIPTION_DB", str(tmp_path / "absent.sqlite3"))
    result = rc.reconcile(now=NOW)
    assert result["ok"] is False
    assert result["checked"] == 0
    assert len(result["issues"]) == len(rc.CHECKS)
    assert set(codes(result)) == {"check_failed"}
    # 不得顺手建库（只读 URI + 不新建文件）
    assert not (tmp_path / "absent.sqlite3").exists()


def test_11_broken_plan_kinds_yields_check_failed(db):
    """plan.kinds 非法 JSON → orphan_entitlement 落 check_failed，不谎报无覆盖/干净。"""
    run_sql(db, "DELETE FROM plan WHERE plan_id='internal'")
    run_sql(db, "UPDATE plan SET kinds='not-json' WHERE plan_id='basic_monthly'")
    result = rc.reconcile(now=NOW)
    assert result["ok"] is False
    failed = [i for i in result["issues"] if i["code"] == "check_failed"]
    assert any("orphan_entitlement" in i["detail"] for i in failed)


# ---------------------------------------------------------------------------
# 12~13. 退出码 / 只读实证
# ---------------------------------------------------------------------------


def test_12_exit_codes(db, capsys):
    """干净 → 0；发现问题 → 1；--json 输出契约三键。"""
    assert rc.main(["--json"]) == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out and '"checked": 2' in out and '"issues": []' in out

    add_sub(db, "ou_ghost")
    assert rc.main(["--json"]) == 1
    capsys.readouterr()


def test_13_readonly_no_file_change(db):
    """运行前后库文件字节一致、无 -wal/-shm 旁生文件（只读实证）。"""
    add_subscriber(db, "ou_a")
    add_sub(db, "ou_a", start="2026-10-01 00:00:00", end="2026-10-31 00:00:00")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    before_files = sorted(p.name for p in db.parent.iterdir())

    rc.reconcile(now=NOW)

    after = hashlib.sha256(db.read_bytes()).hexdigest()
    after_files = sorted(p.name for p in db.parent.iterdir())
    assert before == after
    assert before_files == after_files == ["ledger.sqlite3"]
