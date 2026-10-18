from zhice_os_v2.domain.strategy.spec import RuleSpec, StrategySpec


def test_strategy_spec_valid():
    spec = StrategySpec(
        spec_version=1,
        market="CRYPTO",
        universe={"codes": ["BTC/USDT"]},
        factors=[{"name": "rsi", "params": {"window": 14}}],
        rules=[
            RuleSpec(feature="rsi", op="<", value=30, action="enter"),
            RuleSpec(feature="rsi", op=">", value=70, action="exit"),
        ],
        research_ref="research-1",
    )
    assert spec.market == "CRYPTO"
    assert len(spec.rules) == 2
    assert spec.research_ref == "research-1"


def test_strategy_spec_defaults():
    spec = StrategySpec(
        spec_version=1,
        market="CN",
        universe={"codes": ["600519.SH"]},
    )
    assert spec.execution.frequency == "daily"
    assert spec.risk.stop_loss == 0.08
