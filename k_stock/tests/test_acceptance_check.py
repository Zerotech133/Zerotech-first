# -*- coding: utf-8 -*-
"""验收判据器守护测试（K-STOCK-210）。

覆盖 acceptance_check.py 五判据的核心语义，全部内存 DB 构造、
零触碰真实 paper.db（判据器 ro_connect 依赖 DB 路径，测试通过
monkeypatch DB 指向 :memory: 连接的封装绕开——直接把 cur 传入
check_* 函数，不经 main())。

场景锚定 2026-10-12~13 真实形态（K-STOCK-209 判例）：
- fill_date 表存 YYYYMMDD 无连字符
- 批次口径：C1/C2 按 plan_date=--plan-day 圈定，成交日允许跨验收日
- 手工单 plan_date=NULL 不进批次（SQL 三值逻辑）
- C3 SKIP / C4 双日期形态 / C5 空 ref 剔除
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from k_stock.paper import acceptance_check as ac  # noqa: E402

DAY_A = "20261012"   # 落单批次日 / CN 成交日
DAY_B = "20261013"   # 验收日 / US 成交日


def mk_db(rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, account_id TEXT,"
        " stock TEXT, qty INTEGER, status TEXT, fill_date TEXT,"
        " filled_price REAL, filled_qty INTEGER, fee REAL, plan_date TEXT,"
        " reason TEXT, ref TEXT DEFAULT '', source TEXT DEFAULT 'ai')")
    con.execute(
        "CREATE TABLE positions (account_id TEXT, stock TEXT,"
        " stop_price REAL)")
    con.execute(
        "CREATE TABLE positions_snapshot (date TEXT, account_id TEXT,"
        " stock TEXT, qty INTEGER, market_value REAL)")
    con.executemany(
        "INSERT INTO orders (id, account_id, stock, qty, status, fill_date,"
        " filled_price, filled_qty, fee, plan_date, reason, ref)"
        " VALUES (:id, :account_id, :stock, :qty, :status, :fill_date,"
        " :filled_price, :filled_qty, :fee, :plan_date, :reason, :ref)",
        [dict(id=i, account_id=r.get("account_id", "main"),
              stock=r.get("stock", "600000.SH"), qty=r.get("qty", 100),
              status=r.get("status", "filled"),
              fill_date=r.get("fill_date"), filled_price=r.get("filled_price"),
              filled_qty=r.get("filled_qty"), fee=r.get("fee", 1.0),
              plan_date=r.get("plan_date"), reason=r.get("reason", ""),
              ref=r.get("ref", "")) for i, r in enumerate(rows, 1)])
    return con


def filled(account_id, fill_date, plan_date=DAY_A, **kw):
    d = dict(account_id=account_id, status="filled", fill_date=fill_date,
             filled_price=10.0, filled_qty=100, plan_date=plan_date)
    d.update(kw)
    return d


def pending(account_id, plan_date=DAY_A, **kw):
    d = dict(account_id=account_id, status="pending", fill_date=None,
             filled_price=None, filled_qty=None, plan_date=plan_date)
    d.update(kw)
    return d


# ---- C1 批次口径（209 核心） ----

def test_c1_batch_passes_across_days():
    """批次成交日(fill_date=DAY_A)在验收日(--date=DAY_B)之前——跨日 PASS"""
    con = mk_db([filled("main", DAY_A), filled("main", DAY_A),
                 filled("chaser", DAY_A), filled("chaser", DAY_A)])
    ok, det = ac.check_c1_cn_filled(con.cursor(), DAY_B, DAY_A)
    assert ok is True and det["filled"] == 4 and det["batch"] == DAY_A


def test_c1_batch_isolated_from_history_and_null_plan():
    """历史批次 filled(fill_date<=day, plan_date<批次日) 与手工单
    (plan_date=NULL) 都不混入批次明细——201 防混入诉求由批次隔离继承"""
    con = mk_db([
        filled("main", DAY_A), filled("main", DAY_A),
        filled("chaser", DAY_A), filled("chaser", DAY_A),
        filled("main", "20261009", plan_date="20261009"),   # 历史批次
        filled("main", "20261009", plan_date=None),          # 手工单 NULL
    ])
    ok, det = ac.check_c1_cn_filled(con.cursor(), DAY_B, DAY_A)
    assert ok is True and det["filled"] == 4


def test_c1_under_filled_fails():
    con = mk_db([filled("main", DAY_A), filled("chaser", DAY_A)])
    ok, det = ac.check_c1_cn_filled(con.cursor(), DAY_B, DAY_A)
    assert ok is False and det["filled"] == 2


def test_c1_pending_is_watch_not_block():
    con = mk_db([filled("main", DAY_A), filled("main", DAY_A),
                 filled("chaser", DAY_A), filled("chaser", DAY_A),
                 pending("main")])
    ok, det = ac.check_c1_cn_filled(con.cursor(), DAY_B, DAY_A)
    assert ok is True and det["pending_watch"] == 1


def test_c1_missing_fill_fields_not_counted():
    """filled 但 filled_price/qty 缺失=字段不齐，不计入"""
    con = mk_db([filled("main", DAY_A, filled_price=None, filled_qty=None),
                 filled("main", DAY_A), filled("chaser", DAY_A),
                 filled("chaser", DAY_A)])
    ok, det = ac.check_c1_cn_filled(con.cursor(), DAY_B, DAY_A)
    assert ok is False and det["filled"] == 3


# ---- C2 批次口径 + 硬等式 ----

def test_c2_batch_pass_and_pending_left_fail():
    rows = [filled("us_qingping", DAY_B, stock=f"gb_{s}")
            for s in ("pltr", "tsla", "mrvl", "be", "tsm")]
    ok, det = ac.check_c2_us_filled(mk_db(rows).cursor(), DAY_B, DAY_A)
    assert ok is False and det["filled"] == 5 and det["pending_left"] == 0
    rows.append(filled("us_qingping", DAY_B, stock="gb_panw"))
    ok, det = ac.check_c2_us_filled(mk_db(rows).cursor(), DAY_B, DAY_A)
    assert ok is True and det["filled"] == 6


def test_c2_cross_day_fill_counts():
    """US 成交日=验收日(DAY_B)，批次日=DAY_A——跨日计入"""
    rows = [filled("us_qingping", DAY_B, stock=f"gb_{s}")
            for s in ("pltr", "tsla", "mrvl", "be", "tsm", "panw")]
    ok, _ = ac.check_c2_us_filled(mk_db(rows).cursor(), DAY_B, DAY_A)
    assert ok is True


# ---- C3 SKIP 语义（192） ----

def test_c3_skip_when_us_no_position():
    con = mk_db([])
    con.execute("INSERT INTO positions VALUES ('main','600000.SH',9.2)")
    ok, det = ac.check_c3_stops(con.cursor(), DAY_B)
    assert ok is None and "SKIP" in det["skip"] or "无持仓" in det["skip"]


def test_c3_fail_when_us_position_missing_stop():
    con = mk_db([])
    con.execute("INSERT INTO positions VALUES ('us_qingping','gb_pltr',NULL)")
    ok, det = ac.check_c3_stops(con.cursor(), DAY_B)
    assert ok is False


def test_c3_pass_when_all_us_stops():
    con = mk_db([])
    con.execute("INSERT INTO positions VALUES ('us_qingping','gb_pltr',80.0)")
    con.execute("INSERT INTO positions VALUES ('chaser','600593.SH',7.5)")
    con.execute("INSERT INTO positions VALUES ('main','600887.SH',NULL)")
    ok, det = ac.check_c3_stops(con.cursor(), DAY_B)
    assert ok is True  # main 无止损只展示不并判


# ---- C4 双日期形态（189） ----

def test_c4_accepts_both_date_shapes():
    con = mk_db([])
    con.execute("INSERT INTO positions_snapshot VALUES"
                " ('2026-10-13','main','600000.SH',100,1000.0)")
    ok, det = ac.check_c4_snapshot(con.cursor(), DAY_B)
    assert ok is True and det["n_rows"] == 1
    con2 = mk_db([])
    con2.execute("INSERT INTO positions_snapshot VALUES"
                 " ('20261013','main','600000.SH',100,1000.0)")
    ok2, _ = ac.check_c4_snapshot(con2.cursor(), DAY_B)
    assert ok2 is True


def test_c4_fail_on_other_day():
    con = mk_db([])
    con.execute("INSERT INTO positions_snapshot VALUES"
                " ('2026-10-12','main','600000.SH',100,1000.0)")
    ok, _ = ac.check_c4_snapshot(con.cursor(), DAY_B)
    assert ok is False


# ---- C5 空 ref 剔除（189） ----

def test_c5_ignores_empty_ref_dupes():
    """同日两条空 ref 成交（历史手工单形态）不误判重复"""
    con = mk_db([filled("main", DAY_B, ref=""),
                 filled("main", DAY_B, ref="")])
    ok, det = ac.check_c5_idempotent(con.cursor(), DAY_B)
    assert ok is True and det["duplicate_refs"] == []


def test_c5_catches_same_ref_dupes():
    con = mk_db([filled("main", DAY_B, ref="manual-20261012-x"),
                 filled("main", DAY_B, ref="manual-20261012-x")])
    ok, det = ac.check_c5_idempotent(con.cursor(), DAY_B)
    assert ok is False and len(det["duplicate_refs"]) == 1
