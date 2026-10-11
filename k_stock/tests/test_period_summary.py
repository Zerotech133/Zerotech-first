# -*- coding: utf-8 -*-
"""period_summary 多组合口径对齐测试网（K-STOCK-154）。

覆盖：cn_accounts 推导（正常/订阅文件损坏兜底/USD 账本排除）、
净值 IN 过滤（us_qingping 不入）、orders 同口径过滤、
合并权益（逐账户最新 mark 求和）、--pf 单账户模式。
monkeypatch DB/DATA 到 tmp，不碰真实 paper.db，不触发飞书推送。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PAPER_DIR = Path(__file__).resolve().parent.parent / "paper"
sys.path.insert(0, str(PAPER_DIR))

import judge  # noqa: E402
import period_summary as ps  # noqa: E402

START, END, TAG = "2026-10-05", "2026-10-11", "weekly-2026-W41"

EQ_COLS = "date,account_id,total,cash,market_value,pos_pct,pending,verify_ok,ts"
ORD_COLS = ("ts,source,ref,stock,side,qty,plan_date,price_base,manual_price,"
            "status,fill_date,filled_price,filled_qty,fee,reason,conf,"
            "created_at,strategy,account_id,order_kind")


def _eq(date, acct, total):
    return (date, acct, total, 0.0, total, 0.0, 0, 1, date + "T15:00:00")


def _ord(ts, status, side, acct, fill_date=None, reason=None):
    return (ts, "ai", "", "600519.SH", side, 100, None, "open", None,
            status, fill_date, None, None, 0.0, reason, None,
            ts, "chanlun", acct, "market")


EQ_ROWS = [
    _eq("2026-10-05", "main", 100.0),
    _eq("2026-10-09", "main", 105.0),
    _eq("2026-10-05", "chaser", 50.0),
    _eq("2026-10-09", "chaser", 55.0),
    _eq("2026-10-05", "taishan", 200.0),
    _eq("2026-10-09", "taishan", 190.0),
    _eq("2026-10-05", "us_qingping", 100.0),
    _eq("2026-10-09", "us_qingping", 200.0),  # USD 账本：任何口径都不得出现
]

ORD_ROWS = [
    _ord("2026-10-06T09:31:00", "filled", "BUY", "main", "2026-10-06"),
    _ord("2026-10-07T09:31:00", "filled", "SELL", "chaser", "2026-10-07"),
    _ord("2026-10-07T10:00:00", "filled", "BUY", "us_qingping",
         "2026-10-07"),  # 必须被过滤
    _ord("2026-10-06T09:30:00", "rejected", "BUY", "main",
         reason="触发纪律门槛拦截"),
    _ord("2026-10-07T09:30:00", "rejected", "BUY", "us_qingping",
         reason="纪律门槛"),  # 必须被过滤
]

SUB_GOOD = {"portfolios": [
    {"id": "pf_001", "trader": "chaser"},
    {"id": "pf_002", "trader": "taishan"},
    {"id": "pf_003", "trader": None},
]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "paper.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE equity_marks(%s, PRIMARY KEY(date,account_id))"
                % EQ_COLS)
    con.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "%s)" % ORD_COLS)
    con.executemany("INSERT INTO equity_marks(%s) VALUES(?,?,?,?,?,?,?,?,?)"
                    % EQ_COLS, EQ_ROWS)
    con.executemany("INSERT INTO orders(%s) VALUES(%s)"
                    % (ORD_COLS, ",".join("?" * len(ORD_COLS.split(",")))),
                    ORD_ROWS)
    con.commit()
    con.close()
    sub = tmp_path / "strategy_subscriptions.json"
    sub.write_text(json.dumps(SUB_GOOD), encoding="utf-8")
    monkeypatch.setattr(judge, "DB", db)
    monkeypatch.setattr(ps, "DATA", tmp_path)
    monkeypatch.setattr(ps, "SUB_PATH", sub)
    monkeypatch.setattr(ps, "_period", lambda kind: (START, END, TAG))
    return tmp_path


# ---------- cn_accounts 推导 ----------

def test_cn_accounts_normal():
    assert ps.cn_accounts() == {"main", "chaser", "taishan"}


def test_cn_accounts_usd_ledger_excluded(env):
    sub = ps.SUB_PATH
    sub.write_text(json.dumps({"portfolios": [
        {"id": "pf_x", "trader": "us_qingping"}]}), encoding="utf-8")
    # us_qingping 即使被误绑为 trader 也强制排除（USD 独立账本口径）
    assert ps.cn_accounts() == {"main"}


def test_cn_accounts_missing_file_fallback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ps, "SUB_PATH", tmp_path / "nope.json")
    assert ps.cn_accounts() == {"main", "chaser"}
    assert "WARN" in capsys.readouterr().err


def test_cn_accounts_corrupt_file_fallback(env, capsys):
    ps.SUB_PATH.write_text("{oops not json", encoding="utf-8")
    assert ps.cn_accounts() == {"main", "chaser"}
    assert "WARN" in capsys.readouterr().err


# ---------- 净值口径 ----------

def test_equity_in_filter_excludes_usd(env):
    out = ps.build("weekly")
    eqa = out["performance"]["equity_by_account"]
    assert set(eqa) == {"chaser", "main", "taishan"}  # us_qingping 不入
    assert all(r["account_id"] != "us_qingping"
               for rows in eqa.values() for r in rows)
    # equity 兼容字段仍为 main 曲线
    assert [r["total"] for r in out["performance"]["equity"]] == [100.0, 105.0]
    assert out["performance"]["change_pct"] == 5.0


def test_total_equity_sum_of_latest_marks(env):
    out = ps.build("weekly")
    # 逐账户周期内最新 mark 求和：105 + 55 + 190（不含 us_qingping 的 200）
    assert out["performance"]["total_equity"] == 350.0


# ---------- orders 口径 ----------

def test_orders_filtered_and_split(env):
    perf = ps.build("weekly")["performance"]
    # main 1 买 + chaser 1 卖；us_qingping 的成交被剔除
    assert perf["fills"] == 2 and perf["buys"] == 1
    assert perf["accounts"] == {"chaser": {"fills": 1, "buys": 0},
                                "main": {"fills": 1, "buys": 1}}
    probs = ps.build("weekly")["problems"]
    assert probs["discipline_blocks"] == 1  # 只计 main，不含 us_qingping


# ---------- --pf 单账户模式 ----------

def test_pf_single_account(env):
    out = ps.build("weekly", account="chaser")
    perf = out["performance"]
    assert [r["total"] for r in perf["equity"]] == [50.0, 55.0]
    assert perf["change_pct"] == 10.0
    assert perf["fills"] == 1 and perf["buys"] == 0
    assert set(perf["equity_by_account"]) == {"chaser"}
    assert perf["total_equity"] == 55.0


def test_pf_cli_flag_passthrough(env, capsys):
    rc = ps.main(["--weekly", "--pf", "taishan"])
    assert rc == 0
    blob = capsys.readouterr().out
    assert '"tag": "%s"' % TAG in blob or '"tag":"%s"' % TAG in blob
    # 落盘到 tmp summary 目录
    fp = Path(ps.DATA / "summary" / (TAG + ".json"))
    d = json.loads(fp.read_text(encoding="utf-8"))
    assert d["performance"]["total_equity"] == 190.0
