from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd

from zhice_os_v2.application.services.backtest_service import BacktestService, _rule_satisfied
from zhice_os_v2.domain.strategy.spec import FactorSpec, RiskSpec, RuleSpec, StrategySpec


def test_rule_satisfied():
    rules = [RuleSpec(feature="rsi", op="<", value=30, action="enter")]
    assert _rule_satisfied(rules, {"rsi": 20.0}) is True
    assert _rule_satisfied(rules, {"rsi": 50.0}) is False


def test_rule_satisfied_empty_returns_false():
    """空规则列表应返回 False（持有到期末语义），修复前错误返回 True 导致立即平仓。"""
    assert _rule_satisfied([], {}) is False
    assert _rule_satisfied([], {"rsi": 20.0, "momentum": 0.05}) is False


def _make_df(prices: list[float], start: date = date(2024, 1, 1), ts_code: str = "TEST.SZ") -> pd.DataFrame:
    """构造含 ts_code/trade_date/close 列的价格 DataFrame，供 load_recent_bars 打桩使用。"""
    dates = [str(start + timedelta(days=i)) for i in range(len(prices))]
    return pd.DataFrame({"ts_code": [ts_code] * len(prices), "trade_date": dates, "close": prices})


def _base_spec(stop_loss: float = 0.0, take_profit: float = 0.0) -> StrategySpec:
    """最小化 StrategySpec：momentum(window=5) > 0 入场，无信号出场（依赖止损/止盈）。"""
    return StrategySpec(
        spec_version=1,
        market="CN",
        universe={"type": "single"},
        factors=[FactorSpec(name="momentum", params={"window": 5})],
        rules=[RuleSpec(feature="momentum", op=">", value=0.0, action="enter")],
        risk=RiskSpec(stop_loss=stop_loss, take_profit=take_profit),
    )


def test_no_exit_rules_holds_position_to_end():
    """无 exit_rules 且无风控时，持仓应持有到回测结束（trade_count=0，equity 反映最终价格）。"""
    # 价格：前 5 根上涨触发入场，之后持续下跌——若 exit_rules=[] 误返回 True 则每根都会平仓重开
    prices = [100.0, 101.0, 102.0, 103.0, 104.0, 99.0, 97.0, 95.0, 93.0, 97.0]
    df = _make_df(prices)
    start = date(2024, 1, 1)
    end = date(2024, 1, 10)

    spec = _base_spec(stop_loss=0.0, take_profit=0.0)  # 无风控，完全依赖信号出场（为空）

    with patch("zhice_os_v2.application.services.backtest_service.load_recent_bars", return_value=df):
        result = BacktestService().run(spec, "TEST.SZ", start, end)

    assert "error" not in result
    # 无 exit_rules 且无风控 → 进场后持有到最后一根，无已平仓交易记录
    assert result["trade_count"] == 0, f"预期 0 笔交易，实际 {result['trade_count']}"
    # 新成交语义：第5根收盘产生信号，第6根开盘（缺失 open 时回退 close=99）成交。
    assert result["total_return"] < 0, "持仓跌至 97，回报应为负"
    # 关键：最终净值反映 next-bar open 入场后的末棒价格，而非同bar close成交。
    expected_final = round(100_000.0 / 99.0 * 97.0, 2)
    assert abs(result["final_equity"] - expected_final) < 1.0, (
        f"final_equity={result['final_equity']} 偏离预期 {expected_final}"
    )


def test_exit_lab_variants_produce_different_results():
    """FIXED/TIGHT/LOOSE 三变体因止损线不同应在相同数据上产生不同结果。

    价格设计：
    - bars 0-4：[100→104] 上涨，momentum(5)>0 触发入场（entry_price=104）
    - bar  5：99  → TIGHT 止损(<=104*0.96=99.84) 触发，FIXED/LOOSE 不触发
    - bar  6：97  → FIXED 止损(<=104*0.94=97.76) 触发，LOOSE 不触发
    - bar  8：93  → LOOSE 止损(<=104*0.90=93.6)  触发
    三者在不同时点出场，pnl 分别为 -4.8% / -6.7% / -10.6%。
    """
    prices = [100.0, 101.0, 102.0, 103.0, 104.0, 99.0, 97.0, 95.0, 93.0, 97.0]
    df = _make_df(prices)
    start = date(2024, 1, 1)
    end = date(2024, 1, 10)

    variants = {
        "FIXED": RiskSpec(stop_loss=0.06, take_profit=0.20),
        "TIGHT": RiskSpec(stop_loss=0.04, take_profit=0.08),
        "LOOSE": RiskSpec(stop_loss=0.10, take_profit=0.30),
    }

    results = {}
    with patch("zhice_os_v2.application.services.backtest_service.load_recent_bars", return_value=df):
        for name, risk in variants.items():
            spec = _base_spec()
            spec.risk = risk
            results[name] = BacktestService().run(spec, "TEST.SZ", start, end)

    for name, r in results.items():
        assert "error" not in r, f"{name} 出错：{r}"
    # 新语义下入场在下一根 open=99，TIGHT/FIXED 会在后续 OHLC 触发；
    # LOOSE 的止损线为89.1，本样本未触发，持仓到期没有已平仓交易。
    assert results["TIGHT"]["trade_count"] == 1
    assert results["FIXED"]["trade_count"] == 1
    assert results["LOOSE"]["trade_count"] == 0
    assert results["TIGHT"]["total_return"] < results["FIXED"]["total_return"]
