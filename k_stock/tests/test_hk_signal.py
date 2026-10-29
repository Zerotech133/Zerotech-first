# -*- coding: utf-8 -*-
"""hk_signal.py 单测（KSTOCK-HK-SIGNAL v0.1）。

不碰真实湖/账本：用合成 bars 与临时 DB，验证动量轮动逻辑与 run 契约。
"""
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
PAPER = HERE.parent / "paper"
sys.path.insert(0, str(PAPER))

import hk_signal as sig  # noqa: E402


def _bars_uptrend():
    return [
        {"ts_code": "00700", "trade_date": "2026%04d" % (1000 + i), "close": 100.0 + i}
        for i in range(25)
    ]


def test_compute_momentum_uptrend():
    mom, last, asof = sig.compute_momentum(_bars_uptrend())
    assert mom is not None and mom > 0
    assert last == 124.0 and asof == "20261024"


def test_compute_momentum_insufficient_bars():
    mom, last, asof = sig.compute_momentum(_bars_uptrend()[:10])
    assert mom is None and last == 109.0


def test_build_plan_contract(monkeypatch, tmp_path):
    """build_plan 产出 exec_run 可消费的 payload（stock/action/confidence 合法）。"""
    def fake_bars(code):
        base = {"02800": 120.0, "00700": 124.0, "09988": 118.0, "01810": 110.0}.get(code, 100.0)
        return [
            {"ts_code": code, "trade_date": "2026%04d" % (1000 + i), "close": base + i}
            for i in range(25)
        ]

    monkeypatch.setattr(sig, "load_bars", fake_bars)
    monkeypatch.setenv("HK_PAPER_DB", str(tmp_path / "none.db"))
    payload, meta = sig.build_plan(["02800", "00700", "09988", "01810"])
    assert payload, "payload 不应为空"
    for item in payload:
        assert item["stock"].endswith(".HK")
        assert item["outputs"]["D1"]["action"] in ("BUY", "SELL", "HOLD")
        assert 0.0 <= item["outputs"]["D1"]["confidence"] <= 1.0
    actions = {i["stock"]: i["outputs"]["D1"]["action"] for i in payload}
    # 00700 动量最高 → 无持仓时应买入
    assert actions["01810.HK"] == "BUY"
    assert meta["ranked"][0]["code"] == "01810"


def test_build_plan_stop_loss(monkeypatch, tmp_path):
    """持仓浮亏超 8% → 出 SELL。"""
    def fake_bars(code):
        return [
            {"ts_code": code, "trade_date": "2026%04d" % (1000 + i), "close": 200.0 - i * 2}
            for i in range(25)
        ]

    monkeypatch.setattr(sig, "load_bars", fake_bars)
    db = tmp_path / "t.db"
    import sqlite3

    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE positions(account_id TEXT, stock TEXT, qty INTEGER, avg_cost REAL)"
    )
    con.execute("INSERT INTO positions VALUES('hk_qingping','00700',100,260.0)")
    con.commit()
    con.close()
    monkeypatch.setenv("HK_PAPER_DB", str(db))
    payload, meta = sig.build_plan(["00700"])
    item = [i for i in payload if i["stock"] == "00700.HK"][0]
    assert item["outputs"]["D1"]["action"] == "SELL"
    assert any("止损" in d["why"] for d in meta["detail"])


def test_universe_is_15():
    assert len(sig.UNIVERSE) == 15
    assert "02800" in sig.UNIVERSE and "00700" in sig.UNIVERSE
