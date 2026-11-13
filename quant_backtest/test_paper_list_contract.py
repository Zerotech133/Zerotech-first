# -*- coding: utf-8 -*-
"""v2.3 名单时点契约金测（名单时序倒挂 A+B 的防复发护栏）。

背景（2026-11-13 实锤）：账户里 12 单隔夜挂单来自 11-12 名单，
cmd_open 却拿实时 live 名单（11-13 18:20）写 signal.picks_generated →
票来自 A 名单、快照标 B 名单，字段齐全但归因错，是最隐蔽的训练数据污染。

本文件把三件事固化成回归护栏：
  ① 挂单逐单带名单基准日，生成逻辑 review/重建共用；
  ② open 硬拒「缺时点」与「基准日落后」的挂单；
  ③ signal 快照只读挂单自带时点，不读 live。
另外必须验证「周末重跑刷新 generated_at 但基准不变」不会被误杀
（TodaysPicks 周末照跑，basis_day 仍是上个交易日）。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paper_trade as P  # noqa: E402
from test_paper_open_guard import _setup, _trades, DAY, MON  # noqa: E402


def _iso(d):
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:])


def _live(g, basis, stocks=None, weak=False):
    return {
        "generated_at": g,
        "trade_basis": _iso(basis) + " 收盘（行情湖 ts_daily 最新可得交易日）",
        "risk_gate": {"weak_day": weak},
        "stocks": stocks or [],
    }


def test_rules_version_v23():
    assert P.RULES["version"] == "v2.3"


def test_picks_meta_uses_basis_not_timestamp():
    """周末重跑会刷新 generated_at，但基准日不变 → 不能按时间戳判倒挂。"""
    a = P._picks_meta(_live("2026-01-01 09:00:00", DAY))
    b = P._picks_meta(_live("2099-12-31 23:59:59", DAY))
    assert a[1] == b[1] == DAY
    assert a[2] == b[2] == "trade_basis"


def test_picks_meta_fallback_marked_when_lake_unreachable():
    """行情湖不可达时 trade_basis 无日期 → 回退口径必须被标记。"""
    d = {"generated_at": "2026-11-14 15:10:00",
         "trade_basis": "行情湖不可达，涨跌幅以策略源自带为准"}
    g, basis, src = P._picks_meta(d)
    assert src == "generated_at_fallback" and len(basis) == 8


def test_build_buy_orders_stamps_each_order(tmp_path, monkeypatch):
    """弱势日截 6 只，且每单都带来源名单时点（首仓/加仓一视同仁）。"""
    monkeypatch.setattr(P, "load_suppression", lambda day: None)
    stocks = [
        {"code": "%06d.SZ" % i, "name": "X%d" % i, "tier": "T2",
         "sources": [{"s": "chanlun", "tag": "3买", "dt": DAY}]}
        for i in range(8)
    ]
    orders, gate, meta = P.build_buy_orders(
        _live("2026-11-13 18:20:16", DAY, stocks, weak=True),
        {"positions": {}}, DAY)
    assert len(orders) == 6, "弱势日应只保留前 6 只"
    assert all(o["list_basis_date"] == DAY for o in orders)
    assert all(o["list_generated_at"] == "2026-11-13 18:20:16" for o in orders)
    assert meta["basis_date"] == DAY and meta["weak_day"] is True


def test_open_rejects_orders_without_stamp(tmp_path, monkeypatch):
    """缺 list_basis_date 的旧挂单（正是倒挂产物）必须硬拒，不成交。"""
    _setup(tmp_path, monkeypatch)
    acc = json.load(open(P.ACC, encoding="utf-8"))
    for b in acc["pending_buys"]:
        b.pop("list_basis_date", None)
    json.dump(acc, open(P.ACC, "w", encoding="utf-8"), ensure_ascii=False)
    with pytest.raises(RuntimeError, match="缺 list_basis_date"):
        P.cmd_open(day=DAY, decision_time="09:42")
    assert _trades() == [], "拒绝时不得写任何流水"


def test_open_rejects_stale_basis(tmp_path, monkeypatch):
    """挂单基准日落后于 live 名单（真倒挂）→ 拒绝成交。"""
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(P, "_load_live_picks",
                        lambda: _live("2026-11-16 18:20:00", MON))
    with pytest.raises(RuntimeError, match="名单时序倒挂"):
        P.cmd_open(day=DAY, decision_time="09:42")
    assert _trades() == []


def test_open_allows_weekend_regenerated_list(tmp_path, monkeypatch):
    """周末重跑只刷新 generated_at、基准日不变 → 放行，绝不误杀首跑。"""
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(P, "_load_live_picks",
                        lambda: _live("2099-12-31 23:59:59", DAY))
    P.cmd_open(day=DAY, decision_time="09:42")
    assert any(t["action"] == "BUY" for t in _trades())


def test_signal_snapshot_uses_order_stamp_not_live(tmp_path, monkeypatch):
    """核心：BUY 的 signal.picks_generated 必须等于挂单时点，不是 live。"""
    _setup(tmp_path, monkeypatch)
    acc = json.load(open(P.ACC, encoding="utf-8"))
    stamp = acc["pending_buys"][0]["list_generated_at"]
    monkeypatch.setattr(P, "_load_live_picks",
                        lambda: _live("2099-12-31 23:59:59", DAY))
    P.cmd_open(day=DAY, decision_time="09:42")
    buys = [t for t in _trades() if t["action"] == "BUY"]
    assert buys, "应有首仓成交"
    for t in buys:
        assert t["signal"]["picks_generated"] == stamp
        assert t["signal"]["list_basis_date"] == DAY
        assert "2099" not in json.dumps(t["signal"], ensure_ascii=False)
