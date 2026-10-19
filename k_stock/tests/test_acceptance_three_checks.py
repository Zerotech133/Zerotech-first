# -*- coding: utf-8 -*-
"""每日三查（断档告警 + 消费链活性）守护测试（K-PLAN-V1 批0 任务0.2）。

覆盖 acceptance_check.py 新增两查的核心语义，全部内存 DB / tmp 文件构造、
零触碰真实 paper.db 与真实批次产物：
- 断档查：gap>3 → P0；gap==3 → P2 观察；零 mark → 灰位 P2；日历源不可用 → P2 降级
- 消费链活性：买入批 ts 当日无落单连续 ≥2 批 → P1；无买入批重置链条；
  批次产物文件缺失 → P1（零产物）
- run_daily_checks 端到端：tmp DB + 注入日历 + tmp 批次文件，退出码 2/1/0
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from k_stock.paper import acceptance_check as ac  # noqa: E402

TODAY = "20261020"


def mk_db(marks=(), orders=()):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE equity_marks (date TEXT, account_id TEXT,"
        " total REAL, verify_ok INTEGER)")
    con.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, account_id TEXT,"
        " stock TEXT, qty INTEGER, status TEXT, fill_date TEXT,"
        " filled_price REAL, filled_qty INTEGER, fee REAL, plan_date TEXT,"
        " reason TEXT, ref TEXT DEFAULT '', created_at TEXT)")
    for i, (acct, day) in enumerate(marks, 1):
        con.execute("INSERT INTO equity_marks VALUES (?,?,1.0,1)", (day, acct))
    for i, o in enumerate(orders, 1):
        con.execute("INSERT INTO orders (account_id, status, created_at)"
                    " VALUES (?,?,?)", (o[0], "filled", o[1]))
    return con


def cal_ok(account):
    """注入日历：10-13,14,15,16,19,20 连续六个交易日。"""
    return ["20261013", "20261014", "20261015", "20261016",
            "20261019", "20261020"]


def cal_broken(account):
    raise FileNotFoundError("日历基准票缺失（测试注入）")


# ---- 查一 账本断档 ----

def test_freshness_ok_when_gap_small():
    con = mk_db([("main", "2026-10-16")])
    marks = ac.fetch_last_marks(con.cursor())
    fs = ac.check_ledger_freshness(marks, TODAY, calendar_fn=cal_ok)
    main_f = [f for f in fs if f["account"] == "main"][0]
    assert main_f["severity"] == "P2" and main_f["detail"]["ok"] is True
    assert main_f["detail"]["gap_trading_days"] == 2  # 10-19, 10-20


def test_freshness_p0_when_gap_exceeds():
    con = mk_db([("main", "2026-10-13")])
    marks = ac.fetch_last_marks(con.cursor())
    fs = ac.check_ledger_freshness(marks, TODAY, calendar_fn=cal_ok)
    main_f = [f for f in fs if f["account"] == "main"][0]
    assert main_f["severity"] == "P0"
    assert main_f["detail"]["gap_trading_days"] == 5  # 14,15,16,19,20
    assert "missing_days" in main_f["detail"]


def test_freshness_watch_at_boundary():
    """gap==3（10-15→10-20）不告警只观察——P0 边界=超过而非达到"""
    con = mk_db([("main", "2026-10-15")])
    marks = ac.fetch_last_marks(con.cursor())
    fs = ac.check_ledger_freshness(marks, TODAY, calendar_fn=cal_ok)
    main_f = [f for f in fs if f["account"] == "main"][0]
    assert main_f["severity"] == "P2" and "watch" in main_f["detail"]


def test_freshness_zero_mark_is_shadow_skip():
    """零 mark 账本（HK 灰位）= 灰位期不告警"""
    con = mk_db([])  # 无任何 mark
    marks = ac.fetch_last_marks(con.cursor())
    fs = ac.check_ledger_freshness(marks, TODAY, calendar_fn=cal_ok)
    hk_f = [f for f in fs if f["account"] == "hk_qingping"][0]
    assert hk_f["severity"] == "P2" and "灰位" in hk_f["detail"]["skip"]


def test_freshness_calendar_degrade_no_false_alarm():
    """日历源不可用 → P2 降级不误报（绝不升级为 P0）"""
    con = mk_db([("main", "2026-10-13")])
    marks = ac.fetch_last_marks(con.cursor())
    fs = ac.check_ledger_freshness(marks, TODAY, calendar_fn=cal_broken)
    main_f = [f for f in fs if f["account"] == "main"][0]
    assert main_f["severity"] == "P2" and "degrade" in main_f["detail"]


def test_freshness_covers_all_active_accounts():
    con = mk_db([])
    marks = ac.fetch_last_marks(con.cursor())
    fs = ac.check_ledger_freshness(marks, TODAY, calendar_fn=cal_ok)
    assert sorted(f["account"] for f in fs) == sorted(ac.ACTIVE_ACCOUNTS)


# ---- 查二 消费链活性 ----

def batch(ts, buys=0):
    picks = [{"action": "买入"} for _ in range(buys)]
    picks += [{"action": "观察"}]
    return {"ts": ts, "picks": picks}


def test_chain_healthy_when_orders_same_day():
    batches = [batch("2026-10-17 16:10", buys=2), batch("2026-10-20 16:15", buys=1)]
    fs = ac.check_consumer_chain(batches, {"2026-10-17", "2026-10-20"})
    assert fs[0]["severity"] == "P2" and fs[0]["detail"]["ok"] is True


def test_chain_p1_on_two_consecutive_stall():
    batches = [batch("2026-10-17 16:10", buys=3), batch("2026-10-18 16:12", buys=1)]
    fs = ac.check_consumer_chain(batches, set())  # 两批都无落单
    assert fs[0]["severity"] == "P1"
    assert fs[0]["detail"]["max_stall_run"] == 2


def test_chain_single_stall_is_not_p1():
    batches = [batch("2026-10-17 16:10", buys=3), batch("2026-10-18 16:12", buys=1)]
    fs = ac.check_consumer_chain(batches, {"2026-10-18"})  # 仅后一批落单
    assert fs[0]["severity"] == "P2" and fs[0]["detail"]["max_stall_run"] == 1


def test_chain_reset_by_no_buy_batch():
    """无买入批不欠落单，重置连续计数"""
    batches = [batch("2026-10-17 16:10", buys=3),
               batch("2026-10-18 16:12", buys=0),
               batch("2026-10-19 16:15", buys=2)]
    fs = ac.check_consumer_chain(batches, set())
    assert fs[0]["severity"] == "P2" and fs[0]["detail"]["max_stall_run"] == 1


def test_chain_avoid_action_not_counted_as_buy():
    """「回避（环境闸门）」非买入，全回避批=无落单期望"""
    b = {"ts": "2026-10-17 16:10",
         "picks": [{"action": "回避（环境闸门）"}, {"action": "回避"}]}
    fs = ac.check_consumer_chain([b], set())
    assert fs[0]["severity"] == "P2" and fs[0]["detail"]["buy_batches"] == 0


# ---- 端到端（tmp DB + tmp 批次文件 + 注入日历） ----

def _tmp_batches(tmp_path, batches):
    p = tmp_path / "rps_daily_batches.jsonl"
    p.write_text("\n".join(json.dumps(b, ensure_ascii=False) for b in batches),
                 encoding="utf-8")
    return p


def test_run_daily_all_green(tmp_path):
    con = mk_db(marks=[("main", "2026-10-16"), ("us_qingping", "2026-10-16")],
                orders=[("main", "2026-10-20T09:30:00")])
    bp = _tmp_batches(tmp_path, [batch("2026-10-20 16:15", buys=2)])
    findings, code = ac.run_daily_checks(con, today=TODAY, calendar_fn=cal_ok,
                                         batches_path=bp)
    assert code == 0
    assert not any(f["severity"] in ("P0", "P1") for f in findings)


def test_run_daily_p0_exit2(tmp_path):
    con = mk_db(marks=[("main", "2026-10-13")])   # 断档 5 交易日
    bp = _tmp_batches(tmp_path, [batch("2026-10-20 16:15", buys=0)])
    findings, code = ac.run_daily_checks(con, today=TODAY, calendar_fn=cal_ok,
                                         batches_path=bp)
    assert code == 2
    assert any(f["severity"] == "P0" and f["account"] == "main" for f in findings)


def test_run_daily_p1_exit1(tmp_path):
    con = mk_db(marks=[("main", "2026-10-16")])
    bp = _tmp_batches(tmp_path, [batch("2026-10-20 09:00", buys=2),
                                 batch("2026-10-20 16:15", buys=2)])
    findings, code = ac.run_daily_checks(con, today=TODAY, calendar_fn=cal_ok,
                                         batches_path=bp)
    assert code == 1
    assert any(f["severity"] == "P1" and f["check"] == "consumer_chain"
               for f in findings)


def test_run_daily_p1_missing_batches_file(tmp_path):
    """批次产物缺失=零产物 → P1（跑批异常）"""
    con = mk_db(marks=[("main", "2026-10-16")])
    findings, code = ac.run_daily_checks(
        con, today=TODAY, calendar_fn=cal_ok,
        batches_path=tmp_path / "not_exist.jsonl")
    assert code == 1
    miss = [f for f in findings if f["severity"] == "P1"
            and "零产物" in f["detail"]["verdict"]]
    assert len(miss) == 1


def test_read_batches_tolerates_bad_line(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_text('{"ts": "2026-10-20 16:15", "picks": []}\n{broken\n',
                 encoding="utf-8")
    out = ac.read_batches(p, window=5)
    assert len(out) == 2 and out[1].get("bad_line") is True
