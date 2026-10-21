"""门 N1 universe 圈选登记（休眠默认）单元测试。

仿 tests/test_freeze_alignment.py 约定：
- QBT 为仓根（tests 的上一级），将 str(QBT) 与 str(QBT/"strategy_layer") 加入 sys.path。
- 全部使用合成断言，不依赖真实数据、不触生产。
- 核心契约：resolve_universe 与 backtest_engine_v2.py:946 行为一致
  （在 choices 内原样返回，否则回退 "market"）；A–E 候选只登记不启用。
"""
import sys
from pathlib import Path

QBT = Path(__file__).resolve().parents[1]
if str(QBT) not in sys.path:
    sys.path.insert(0, str(QBT))
if str(QBT / "strategy_layer") not in sys.path:
    sys.path.insert(0, str(QBT / "strategy_layer"))

from strategy_layer.liudu_universe import (  # noqa: E402
    CURRENT_UNIVERSE_CHOICES,
    LIUDU_UNIVERSE_ENABLED,
    UniverseCandidate,
    apply_universe_filter,
    enumerate_candidates,
    is_ashare_symbol,
    resolve_universe,
)


def test_resolve_known_market_passthrough():
    """已知 choice 'market' 原样返回。"""
    assert resolve_universe("market") == "market"


def test_resolve_known_pool76_passthrough():
    """已知 choice 'pool76' 原样返回。"""
    assert resolve_universe("pool76") == "pool76"


def test_resolve_unknown_falls_back_to_market():
    """未知请求一律回退 'market'（保持现状，不启用任何 A–E 候选）。"""
    assert resolve_universe("anything_else") == "market"
    assert resolve_universe("candidate_A") == "market"
    assert resolve_universe("") == "market"
    assert resolve_universe("EXTERNAL_PRODUCT") == "market"


def test_current_universe_choices_matches_engine():
    """CURRENT_UNIVERSE_CHOICES 必须与 backtest_engine_v2.py:1178 完全一致。"""
    assert CURRENT_UNIVERSE_CHOICES == ("market", "pool76")


def test_universe_candidate_enum_covers_wo025_five():
    """UniverseCandidate 覆盖 WO-025 考证的 5 个候选 A–E，且默认不启用。"""
    assert set(UniverseCandidate.__members__) == {"A", "B", "C", "D", "E"}
    assert UniverseCandidate.A.value == "A"
    assert UniverseCandidate.E.value == "E"


def test_enumerate_candidates_are_documentation_only():
    """enumerate_candidates 仅文档化登记 A–E，全部标记待老田拍板，无自动选择逻辑。"""
    cands = enumerate_candidates()
    assert isinstance(cands, dict)
    assert set(cands) == {"A", "B", "C", "D", "E"}
    for code, doc in cands.items():
        assert doc["code"] == code
        assert doc["status"] == "待老田拍板"
    # 关键不变量：登记字典不得夹带任何 "enabled"/"selected"/"active" 之类的启用标记。
    flat = " ".join(str(v) for d in cands.values() for v in d.values())
    assert "启用" not in flat and "selected" not in flat


def test_universe_switch_activated():
    """路径 A 授权：LIUDU_UNIVERSE_ENABLED 已翻 True，universe=market 真实链生效。"""
    assert LIUDU_UNIVERSE_ENABLED is True


def test_is_ashare_symbol_classification():
    """A 股判定：6 位 + .SH/.SZ 为真；北交所(.BJ) 与美/外股字母 ticker 为假。"""
    assert is_ashare_symbol("603057.SH") is True
    assert is_ashare_symbol("300750.SZ") is True
    assert is_ashare_symbol("920190.BJ") is False   # 北交所
    assert is_ashare_symbol("ON") is False          # 美/外股
    assert is_ashare_symbol("TXN") is False
    assert is_ashare_symbol("") is False


def test_apply_universe_filter_market_keeps_ashare_only():
    """market 域：仅保留 A 股，剔除北交所与美/外股；不改入参、返回 (kept, removed)。"""
    rows = [
        {"symbol": "603057.SH", "score": 100.0},
        {"symbol": "300750.SZ", "score": 99.0},
        {"symbol": "920190.BJ", "score": 97.0},   # 北交所
        {"symbol": "ON", "score": 96.0},          # 美/外股
        {"symbol": "TXN", "score": 95.0},         # 美/外股
    ]
    kept, removed = apply_universe_filter(rows)
    assert [r["symbol"] for r in kept] == ["603057.SH", "300750.SZ"]
    assert sorted(r["symbol"] for r in removed) == ["920190.BJ", "ON", "TXN"]
    # 不改入参
    assert len(rows) == 5


def test_apply_universe_filter_pool76_missing_list_passthrough():
    """pool76 静态清单未提供：原样返回未过滤（绝不臆造过滤）。"""
    rows = [{"symbol": "ON", "score": 96.0}, {"symbol": "603057.SH", "score": 100.0}]
    kept, removed = apply_universe_filter(rows, requested="pool76")
    assert kept == rows
    assert removed == []


def test_apply_universe_filter_uses_selected_universe_by_default():
    """未传 requested 时采用已拍板的 SELECTED_UNIVERSE=market。"""
    rows = [{"symbol": "603057.SH"}, {"symbol": "RVSB"}]
    kept, removed = apply_universe_filter(rows)
    assert [r["symbol"] for r in kept] == ["603057.SH"]
    assert [r["symbol"] for r in removed] == ["RVSB"]
