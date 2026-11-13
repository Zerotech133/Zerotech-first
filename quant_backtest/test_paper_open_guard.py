# -*- coding: utf-8 -*-
"""v2.2 换股护栏 / 早盘时段诊断的金测（纯逻辑，不碰生产账户与分钟数据）。

背景：2026-11-13 首次真实 open 前的就绪性 dry-run 发现两个缺陷——
  ① 卖出段 del positions[c] 后，同票若仍在隔夜买入挂单里会被当首仓买回；
  ② 早盘触发 09:28 早于开盘，_assert_session 抛异常、包装脚本却 exit 0。
本文件把这两条固化成回归护栏；成交价/复权/抑制令全部 mock，跑得快且确定。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper_trade as P  # noqa: E402

# 日期字面量一律码点构造（防 Write/Edit 通道串味）
DAY = ''.join(chr(c) for c in (50, 48, 50, 54, 49, 49, 49, 52))  # 20261114


def _src(tag):
    return [{"s": "chanlun", "tag": tag, "dt": DAY}]


def _setup(tmp_path, monkeypatch):
    acc = {
        "created": "test", "cash": 9_000_000.0, "realized_pnl": 0.0,
        "addon_used": 0.0,
        "positions": {
            "A.SZ": {"name": "A", "shares": 10000, "avg_cost": 10.0,
                     "opened": DAY, "cost_adj": 10.0},
            "B.SZ": {"name": "B", "shares": 10000, "avg_cost": 10.0,
                     "opened": DAY, "cost_adj": 10.0},
        },
        "pending_sells": [{"code": "A.SZ", "why": "盘中降级"}],
        "pending_buys": [
            {"code": "A.SZ", "name": "A", "tier": "T2", "sources": _src("3买")},
            {"code": "B.SZ", "name": "B", "tier": "T2", "sources": _src("3买")},
            {"code": "C.SZ", "name": "C", "tier": "T2", "sources": _src("3买")},
        ],
    }
    monkeypatch.setattr(P, "ACC", str(tmp_path / "account.json"))
    monkeypatch.setattr(P, "TRADES", str(tmp_path / "trades.jsonl"))
    monkeypatch.setattr(P, "EQUITY", str(tmp_path / "equity.jsonl"))
    monkeypatch.setattr(P, "load_suppression", lambda day: None)
    monkeypatch.setattr(P, "adj_factors", lambda codes, day: {})

    def fake_px(codes, day, decision_time):
        return {c: {"price": 10.0, "name": "", "price_source": "unit_test",
                    "price_visible_at": "unit_test"}
                for c in codes}

    monkeypatch.setattr(P, "resolve_visible_prices", fake_px)
    (tmp_path / "account.json").write_text(json.dumps(acc), encoding="utf-8")
    return acc


def _trades():
    out = []
    if os.path.exists(P.TRADES):
        for line in open(P.TRADES, encoding="utf-8"):
            if line.strip():
                out.append(json.loads(line))
    return out


def test_rules_version_v22():
    assert P.RULES["version"] == "v2.2"


def test_sold_stock_never_rebought_same_open(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    P.cmd_open(day=DAY, decision_time="09:42")
    acc = json.load(open(P.ACC, encoding="utf-8"))
    acts = _trades()

    assert "A.SZ" not in acc["positions"], "当日卖出的票不得在同一次 open 买回"
    assert not [t for t in acts if t["action"] == "BUY" and t["code"] == "A.SZ"]
    assert [t for t in acts if t["action"] == "SELL" and t["code"] == "A.SZ"]
    assert "C.SZ" in acc["positions"], "无关新票应正常建仓，护栏不能误伤"
    assert acc["rebound_blocked_last"]["codes"] == ["A.SZ"]
    assert acc["rebound_blocked_last"]["why"] == "same_day_sell_then_buy"
    assert len(acc["pending_buys"]) == 0 and len(acc["pending_sells"]) == 0
    assert all(t["rules"] == "v2.2" for t in acts)


def test_preopen_decision_is_actionable_error(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError) as e:
        P.cmd_open(day=DAY, decision_time="09:28")
    msg = str(e.value)
    assert "09:30" in msg and "触发" in msg, "报错必须指向可操作的修复动作"
    acc = json.load(open(P.ACC, encoding="utf-8"))
    assert len(acc["positions"]) == 2, "崩溃前不得改动账户"
    assert _trades() == [], "崩溃前不得写流水"


def test_guard_does_not_block_unrelated_add(tmp_path, monkeypatch):
    """护栏只拦卖出票；已持仓但没卖的票仍可走加仓分支。"""
    _setup(tmp_path, monkeypatch)
    P.cmd_open(day=DAY, decision_time="09:42")
    acts = _trades()
    assert any(t["action"] == "ADD" and t["code"] == "B.SZ" for t in acts)


SAT = ''.join(chr(c) for c in (50, 48, 50, 54, 49, 49, 49, 52))  # 20261114
MON = ''.join(chr(c) for c in (50, 48, 50, 54, 49, 49, 49, 54))  # 20261116
HOL = ''.join(chr(c) for c in (50, 48, 50, 54, 49, 48, 48, 49))  # 20261001


def test_trading_calendar_covers_future():
    """必须用 ts_trade_cal（覆盖未来），不能用只到数据日的历史日历。"""
    assert P.is_trading_day(SAT) == (False, "ts_trade_cal")
    assert P.is_trading_day(MON) == (True, "ts_trade_cal")
    assert P.is_trading_day(HOL) == (False, "ts_trade_cal"), "国庆节假日要挡住"


def test_main_skips_non_trading_day(tmp_path, monkeypatch, capsys):
    """周末 main 直接 SKIP：不调用 cmd_open、不写账户/流水，rc=0。"""
    monkeypatch.setattr(P, "ACC", str(tmp_path / "account.json"))
    monkeypatch.setattr(P, "TRADES", str(tmp_path / "trades.jsonl"))
    monkeypatch.setattr(P, "EQUITY", str(tmp_path / "equity.jsonl"))
    monkeypatch.setattr(P, "cmd_open",
                        lambda *a, **k: pytest.fail("非交易日不应调用 cmd_open"))
    monkeypatch.setattr(sys, "argv", ["paper_trade.py", "open",
                                      "--date", SAT, "--decision-time", "09:42"])
    rc = P.main()
    assert rc == 0
    assert "[SKIP]" in capsys.readouterr().out
    assert not os.path.exists(P.TRADES) and not os.path.exists(P.EQUITY)
