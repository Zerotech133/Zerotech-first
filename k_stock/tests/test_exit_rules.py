# -*- coding: utf-8 -*-
"""K-STOCK-122 M7：手机端止盈止损指令层测试。
临时 db + 假行情 + exit_rules.json 重定向到 tmp，不碰真库。"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "paper"))

import exit_rules                      # noqa: E402
from engine import PaperEngine         # noqa: E402
from market import ParquetMarket       # noqa: E402

ACCT = "t7x"


class FakeBar(dict):
    pass


class FakeMarket:
    """最小行情桩：last_bar 可控收盘价；next_trading_day 返回固定日。"""

    def __init__(self, close=11.0, trade_date="2026-10-12"):
        self.close = close
        self.trade_date = trade_date

    def last_bar(self, stock):
        return {"close": self.close, "trade_date": self.trade_date}

    def next_trading_day(self, ts):
        return "2026-10-13"

    def trading_days(self):
        return ["2026-10-09", "2026-10-12", "2026-10-13"]


def _eng(tmp_path, close=11.0):
    m = FakeMarket(close)
    eng = PaperEngine(tmp_path / "paper.db", m, account=ACCT)
    eng.con.execute(
        "INSERT INTO positions(account_id,stock,qty,avg_cost,updated_at,"
        "strategy,stop_price) VALUES(?,?,?,?,?,?,?)",
        (ACCT, "605100.SH", 1000, 10.0, "2026-10-11T15:00:00", "test", None))
    eng.con.commit()
    return eng


@pytest.fixture(autouse=True)
def _iso_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(exit_rules, "RULES_PATH", tmp_path / "exit_rules.json")
    monkeypatch.setattr(exit_rules, "DEFAULT_DB", tmp_path / "paper.db")


def test_set_tp_writes_rule(tmp_path):
    eng = _eng(tmp_path)
    r = exit_rules.set_line(ACCT, "605100", "tp", "8")
    assert r["ok"] and r["line"] == 10.8 and r["pct"] == 8.0
    d = json.loads(exit_rules.RULES_PATH.read_text(encoding="utf-8"))
    assert d["rules"][0]["stock"] == "605100.SH"


def test_set_sl_updates_engine_stop(tmp_path):
    eng = _eng(tmp_path)
    r = exit_rules.set_line(ACCT, "605100", "sl", "5")
    assert r["ok"] and r["line"] == 9.5      # 10.0 × (1-5%)
    pos = eng.positions(ACCT)[0]
    assert pos["stop_price"] == 9.5


def test_set_rejects_no_position(tmp_path):
    _eng(tmp_path)
    r = exit_rules.set_line(ACCT, "000001", "tp", 8)
    assert not r["ok"] and "无持仓" in r["error"]


def test_check_tp_triggers_sell_pending(tmp_path):
    eng = _eng(tmp_path, close=11.0)          # 收盘 11 >= 触发 10.8
    exit_rules.set_line(ACCT, "605100", "tp", 8)
    out = exit_rules.check_tps(ACCT, eng=eng)
    assert len(out["triggered"]) == 1
    t = out["triggered"][0]
    assert t["status"] == "pending" and t["qty"] == 1000
    # 幂等（规则清除式）：触发后规则就地清除，二次检查零触发
    out2 = exit_rules.check_tps(ACCT, eng=eng)
    assert out2["triggered"] == []
    # 触发后规则就地清除
    assert exit_rules.list_lines(ACCT)["tp_rules"] == []


def test_check_tp_watches_below_line(tmp_path):
    eng = _eng(tmp_path, close=10.5)          # 未触线
    exit_rules.set_line(ACCT, "605100", "tp", 8)
    out = exit_rules.check_tps(ACCT, eng=eng)
    assert out["triggered"] == [] and out["watch"]


def test_clear_tp_and_sl(tmp_path):
    eng = _eng(tmp_path)
    exit_rules.set_line(ACCT, "605100", "tp", 8)
    exit_rules.set_line(ACCT, "605100", "sl", 5)
    assert exit_rules.clear_line(ACCT, "605100", "tp")["ok"]
    assert exit_rules.list_lines(ACCT)["tp_rules"] == []
    assert exit_rules.clear_line(ACCT, "605100", "sl")["ok"]
    assert eng.positions(ACCT)[0]["stop_price"] is None


def test_invalid_pct_rejected(tmp_path):
    _eng(tmp_path)
    for bad in ("0", "95", "-3"):
        assert not exit_rules.set_line(ACCT, "605100", "tp", bad)["ok"]
