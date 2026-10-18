from zhice_os_v2.domain.strategy.fundamental_gate import (
    FundamentalGateConfig,
    evaluate,
    evaluate_forecast,
    evaluate_reduction,
    filter_picks_pure,
)


def test_evaluate_profit_loss():
    cfg = FundamentalGateConfig()
    ok, reason = evaluate({"profit_dedt_cny": -100}, cfg)
    assert not ok
    assert "亏损" in reason


def test_evaluate_forecast_warning():
    cfg = FundamentalGateConfig()
    ok, reason = evaluate_forecast({"forecast_type": "首亏"}, cfg)
    assert not ok


def test_evaluate_reduction():
    cfg = FundamentalGateConfig()
    ok, reason = evaluate_reduction(5.0, cfg)
    assert not ok


def test_filter_picks_pure():
    cfg = FundamentalGateConfig()
    picks = [{"ts_code": "A", "score": 1}, {"ts_code": "B", "score": 2}]
    funds = {"A": {"profit_dedt_cny": 100}, "B": {"profit_dedt_cny": -1}}
    kept, rejected = filter_picks_pure(picks, funds, {}, {}, cfg)
    assert len(kept) == 1
    assert rejected[0]["ts_code"] == "B"
