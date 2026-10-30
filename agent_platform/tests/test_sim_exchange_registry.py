# -*- coding: utf-8 -*-
"""模拟交易所归属注册表测试（WO-SIM-EXCHANGE-20261030）。"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
REG = REPO / "agent_platform" / "registry" / "sim_exchange.json"


def _load():
    return json.loads(REG.read_text(encoding="utf-8"))


def test_owner_and_manager():
    d = _load()
    assert d["owner_dept"] == "invest"
    assert d["owner"] == "D1"
    assert d["manager"] in ("TR1", "QS1")
    ids = {x["id"] for x in d["assets"]}
    assert {"legacy_ks_paper", "quant_paper_mvp", "sim_market_contract", "chanlun_paper_data"} <= ids


def test_assets_exist_and_no_real_trading():
    d = _load()
    for a in d["assets"]:
        p = a.get("path")
        # 运行时数据/台账不进 worktree，只校验代码/页面等 tracked 资产
        if p and not p.startswith(("quant_backtest/data", "k_stock/data")):
            assert (REPO / p).exists(), p
        for t in a.get("tests") or []:
            assert (REPO / t).exists(), t
    assert d["boundaries"]["real_trading"] is False
    assert d["boundaries"]["real_exchange_adapter"] == "disabled"


def test_single_source_of_truth():
    d = _load()
    s = d["single_source_of_truth"]
    assert s["official_page"] == "/quant/paper"
    assert s["official_daily_ledger"].endswith("paper_ledger.jsonl")
    assert s["legacy_ledger"].endswith("paper.db")
