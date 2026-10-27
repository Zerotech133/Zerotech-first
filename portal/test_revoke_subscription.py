# -*- coding: utf-8 -*-
"""撤销订阅方案 a 单测（2026-10-21 老田拍板，WO-FS-145 主会话实施）。

沿 test_subscription_store.py 判例：env SUBSCRIPTION_DB 锚 tmp 临时文件，
零碰生产 portal/work/subscription.sqlite3。revoke 一律传固定 now（跨日
假红判例：落账时刻必须注入，不吃真实时钟）。

覆盖：
  · 撤销成功：行数、status=expired、end 不动、留痕行（allowed=0 /
    reason=revoked）；
  · 重复撤销 ValueError、无生效行 ValueError 且零写入（失败不留半截痕）、
    档位不存在 ValueError；
  · 撤销后 is_entitled 判定（无生效行 → no_subscription）；
  · 勾稽咬合：有撤销留痕的提前 expired 行不被 status_contradiction 逮；
    无留痕的改库式提前 expired 仍被逮（豁免不成为改库后门）。
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
_TOOLS = _HERE.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import subscription_store as ss  # noqa: E402
import reconcile_subscription as rc  # noqa: E402

NOW = datetime(2026, 10, 21, 9, 0, 0)


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "revoke_test.sqlite3"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(p))
    monkeypatch.delenv("SUBSCRIPTION_OWN_DOMAIN", raising=False)
    ss._connect(create=True)
    yield p


def _grant_here(
    open_id, plan_id="basic_monthly", days=30, start=datetime(2026, 10, 20, 8, 0, 0)
):
    # 先建主档：无 subscriber 行会被勾稽 orphan_open_id 逮（数据构造
    # 缺陷非被测行为），统一在此补齐，避免逐用例遗漏。
    ss.upsert_subscriber(open_id)
    end = start + timedelta(days=days)
    with ss._connect(create=True) as conn:
        conn.execute(
            "INSERT INTO subscription(subscriber_open_id, plan_id, start,"
            " end, status) VALUES (?,?,?,?,?)",
            (
                open_id,
                plan_id,
                start.strftime("%Y-%m-%d %H:%M:%S"),
                end.strftime("%Y-%m-%d %H:%M:%S"),
                "active",
            ),
        )
        conn.commit()


def _sub_rows(open_id):
    conn = sqlite3.connect(str(ss._db_path()))
    rows = conn.execute(
        "SELECT plan_id, status, start, end FROM subscription"
        " WHERE subscriber_open_id=? ORDER BY plan_id",
        (open_id,),
    ).fetchall()
    conn.close()
    return rows


def _log_rows(open_id):
    conn = sqlite3.connect(str(ss._db_path()))
    rows = conn.execute(
        "SELECT kind, allowed, reason FROM entitlement_log WHERE open_id=? ORDER BY ts",
        (open_id,),
    ).fetchall()
    conn.close()
    return rows


def test_01_revoke_success_row_expired_log_left(db):
    """撤销成功：返回行数、status 置 expired、end 不动、留痕 reason=revoked。"""
    _grant_here("ou_r1")
    n = ss.revoke_subscription("ou_r1", "basic_monthly", now=NOW)
    assert n == 1
    rows = _sub_rows("ou_r1")
    assert len(rows) == 1
    assert rows[0][1] == "expired"  # status
    assert rows[0][3] == "2026-11-19 08:00:00"  # end 不动（end>=now 形态）
    logs = _log_rows("ou_r1")
    assert logs == [("subscription.revoke", 0, "revoked")]


def test_02_revoke_twice_second_raises(db):
    """重复撤销：第二次无生效行 → ValueError；不留第二条留痕。"""
    _grant_here("ou_r2")
    assert ss.revoke_subscription("ou_r2", "basic_monthly", now=NOW) == 1
    with pytest.raises(ValueError):
        ss.revoke_subscription("ou_r2", "basic_monthly", now=NOW)
    assert len(_log_rows("ou_r2")) == 1


def test_03_revoke_unknown_plan_raises(db):
    """档位不存在 → ValueError（照 grant_subscription 判例）。"""
    with pytest.raises(ValueError, match="plan 不存在"):
        ss.revoke_subscription("ou_r3", "no_such_plan", now=NOW)
    assert _log_rows("ou_r3") == []


def test_04_revoke_without_active_row_zero_write(db):
    """从未开通 → ValueError 且零写入（失败=零写入，不留半截痕）。"""
    with pytest.raises(ValueError, match="无生效订阅行"):
        ss.revoke_subscription("ou_r4", "basic_monthly", now=NOW)
    assert _sub_rows("ou_r4") == []
    assert _log_rows("ou_r4") == []


def test_05_entitled_after_revoke(db):
    """撤销后判定：无生效行 → (False, no_subscription)。"""
    _grant_here("ou_r5")
    ss.revoke_subscription("ou_r5", "basic_monthly", now=NOW)
    ok, reason = ss.is_entitled("ou_r5", "news.brief", now=NOW)
    assert ok is False and reason == "no_subscription"


def test_06_reconcile_exempts_revoked_row(db):
    """勾稽咬合：有撤销留痕的提前 expired 行不被 status_contradiction 逮。"""
    _grant_here("ou_r6")  # end=2026-11-19（未来），撤销后即触发矛盾形态
    ss.revoke_subscription("ou_r6", "basic_monthly", now=NOW)
    r = rc.reconcile(db_path=str(ss._db_path()), now=NOW)
    codes = {i["code"] for i in r["issues"]}
    assert "status_contradiction" not in codes
    assert r["ok"] is True


def test_07_reconcile_still_catches_untraced_early_expired(db):
    """无留痕的改库式提前 expired 仍被逮——豁免不成为改库后门。"""
    _grant_here("ou_r7")
    conn = sqlite3.connect(str(ss._db_path()))
    conn.execute("UPDATE subscription SET status='expired'")
    conn.commit()
    conn.close()
    r = rc.reconcile(db_path=str(ss._db_path()), now=NOW)
    codes = {i["code"] for i in r["issues"]}
    assert "status_contradiction" in codes
    assert r["ok"] is False


def test_08_revoke_in_all(db):
    """revoke_subscription 进 __all__（契约可见性）。"""
    assert "revoke_subscription" in ss.__all__
