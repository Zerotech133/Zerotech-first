"""信号型策略 evaluate_signal（spec.rules DSL 执行）测试。"""
from zhice_os_v2.application.services.candidate_service import evaluate_signal
from zhice_os_v2.domain.strategy.spec import StrategySpec

_RULES = [
    {"feature": "rsi", "op": "<", "value": 30, "action": "enter"},
    {"feature": "rsi", "op": ">", "value": 70, "action": "exit"},
]


def _spec(rules=None):
    return StrategySpec.model_validate({
        "spec_version": 1, "market": "CRYPTO", "universe": {"codes": ["BTC/USDT"]},
        "timeframe": "5m", "factors": [{"name": "rsi", "params": {"window": 14}}],
        "rules": rules if rules is not None else _RULES,
    })


def test_timeframe_field_parsed():
    assert _spec().timeframe == "5m"


def test_enter_on_low_rsi():
    closes = [100.0 - i for i in range(30)]  # 单调下跌 → RSI 极低 (<30)
    assert evaluate_signal(_spec(), closes) == "enter"


def test_exit_on_high_rsi():
    closes = [100.0 + i for i in range(30)]  # 单调上涨 → RSI 极高 (>70)
    assert evaluate_signal(_spec(), closes) == "exit"


def test_exit_takes_priority():
    # 同时命中 enter 与 exit（enter 阈值放宽到 <99）→ exit 优先
    rules = [
        {"feature": "rsi", "op": "<", "value": 99, "action": "enter"},
        {"feature": "rsi", "op": ">", "value": 70, "action": "exit"},
    ]
    closes = [100.0 + i for i in range(30)]
    assert evaluate_signal(_spec(rules), closes) == "exit"


def test_none_when_no_rule_hit():
    # 区间内（既不 <30 也不 >70）→ 无信号
    closes = [100.0, 101.0, 100.5, 101.2, 100.8] * 6
    assert evaluate_signal(_spec(), closes) is None


def test_none_when_insufficient_data():
    assert evaluate_signal(_spec(), None) is None
    assert evaluate_signal(_spec(), [100.0, 101.0]) is None  # 太短 → rsi None
