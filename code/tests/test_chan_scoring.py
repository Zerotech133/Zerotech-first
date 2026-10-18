"""P2-I 保守修复回归测试：CHAN/CHAN_V2 评分饱和后的确定性 tiebreak + 先过滤后截断。

不打桩 _score / sort / [:N] 本体——只桩掉数据加载层（DB/czsc），
让真实的评分公式、排序、截断在受控输入上跑到缺陷本体。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from zhice_os_v2.domain.strategy.strategies.chan import engine as chan_engine
from zhice_os_v2.domain.strategy.strategies.chan import rules_v2
from zhice_os_v2.domain.strategy.strategies.chan import strategy as chan_strategy
from zhice_os_v2.domain.strategy.strategies.chan import strategy_v2 as chan_strategy_v2


def _mini_bars(codes: list[str], n: int = 80) -> pd.DataFrame:
    """构造最小可用 bars（ts_code + security_id + 足量行数，OHLCV 仅占位）。"""
    frames = []
    for i, code in enumerate(codes):
        frames.append(pd.DataFrame({
            "ts_code": [code] * n,
            "security_id": [i + 1] * n,
            "dt": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [10.0] * n, "high": [11.0] * n,
            "low": [9.0] * n, "close": [10.5] * n,
            "volume": [1e6] * n, "amount": [1e7] * n,
        }))
    return pd.concat(frames, ignore_index=True)


def test_v1_tiebreak_prefers_structure_over_alpha(monkeypatch):
    """v1：两只同分候选（AAA 结构弱 / ZZZ 结构强），max_candidates=1。

    RED（无 tiebreak）：稳定排序退化到 groupby 的 ts_code 升序 → 选中字母序在前的 AAA。
    GREEN（补结构强度二级键）：选中结构更强的 ZZZ，与字母序无关。
    """
    monkeypatch.setattr(chan_strategy, "_load_universe",
                        lambda td, market="CN": ["AAA", "ZZZ"])
    monkeypatch.setattr(chan_strategy, "_load_ohlcv",
                        lambda td, codes, market="CN": _mini_bars(["AAA", "ZZZ"]))

    def fake_analyze(ts_code, df):
        bi = 6 if ts_code == "AAA" else 12   # ZZZ 中枢延伸笔数更多=结构更强
        return chan_engine.ChanSnapshot(
            symbol=ts_code, bar_count=len(df), bs_point="b3",
            divergence="medium", macd_divergence=False, vol_price_sync=False,
            prev_zs={"bi_count": bi, "zd": 10.0, "zg": 11.0},
        )

    monkeypatch.setattr(chan_engine, "analyze", fake_analyze)

    picks = chan_strategy.select_candidates(date(2025, 1, 2), max_candidates=1)
    assert len(picks) == 1
    # 两者 score 恒等（离散档全饱和）：0.40*1 + 0.30*0.6 + 0.20*1 + 0.10*0 = 0.78
    assert picks[0]["ts_code"] == "ZZZ", (
        f"tiebreak 未按结构强度决出胜负，被字母序污染：{picks[0]['ts_code']}"
    )


def test_v2_tiebreak_prefers_structure_over_alpha(monkeypatch):
    """v2：对称验证 strategy_v2 也用结构强度二级键（保持 A/B 公平）。"""
    monkeypatch.setattr(chan_strategy_v2, "_load_universe",
                        lambda td, market="CN": ["AAA", "ZZZ"])
    monkeypatch.setattr(chan_strategy_v2, "_load_ohlcv",
                        lambda td, codes, market="CN": _mini_bars(["AAA", "ZZZ"]))
    monkeypatch.setattr(chan_engine, "extract_bi_list",
                        lambda ts_code, df: ([], None))

    def fake_evaluate(ts_code, bi_list, df):
        bis = 6 if ts_code == "AAA" else 12
        snap = rules_v2.ChanV2Signal(symbol=ts_code)
        snap.signal = {
            "point": "b2", "conf": "MID", "pivot_bis": bis,
            "b2b3_overlap": False, "pivot": {"zd": 10.0, "zg": 11.0},
        }
        snap.vol_price_sync = False
        return snap

    monkeypatch.setattr(rules_v2, "evaluate", fake_evaluate)

    picks = chan_strategy_v2.select_candidates(date(2025, 1, 2), max_candidates=1)
    assert len(picks) == 1
    # score 恒等：0.40*0.85 + 0.30*0.6 + 0.20*1 + 0 = 0.72
    assert picks[0]["ts_code"] == "ZZZ", (
        f"v2 tiebreak 被字母序污染：{picks[0]['ts_code']}"
    )


def test_registry_filters_before_truncate_allows_backfill(monkeypatch):
    """先过滤后截断：排名 21 能过基本面，前 20 有 2 只被基本面刷掉 → 21 应回填入选。

    RED（先截断后过滤）：selector 被以 max_candidates=20 调用，只吐前 20；
      基本面刷掉 rank1/rank2 → 最终 18 只，rank21 永远无缘。
    GREEN（截断下沉到过滤后）：selector 拿到放大的配额吐全量 21 只；
      过滤后 rank21 回填进 top-20。
    """
    import zhice_os_v2.application.services.strategy_registry_service as srs

    # 21 只候选，score 严格降序（rank1 最高），确保排序确定
    all_codes = [f"S{i:02d}" for i in range(21)]

    def fake_selector(trade_date, max_candidates=20, market="CN"):
        full = [
            {"security_id": str(i), "ts_code": c, "score": 100 - i, "reason": "x"}
            for i, c in enumerate(all_codes)
        ]
        # 忠实复刻真实 selector 的"内部先截断"语义
        return full[:max_candidates]

    # rank1(S00)、rank2(S01) 亏损被基本面刷；其余无财务数据放行
    funds = {"S00": {"profit_dedt_cny": -1.0}, "S01": {"profit_dedt_cny": -1.0}}

    monkeypatch.setitem(srs.STRATEGY_REGISTRY, "CHAN", fake_selector)
    monkeypatch.setattr(srs, "load_fundamentals_asof",
                        lambda codes, trade_date: funds)

    svc = srs.StrategyRegistryService()
    kept = svc.run("CHAN", date(2025, 1, 2), max_candidates=20)

    kept_codes = [p["ts_code"] for p in kept]
    assert "S20" in kept_codes, "rank21 未能回填——仍是先截断后过滤"
    assert "S00" not in kept_codes and "S01" not in kept_codes
    assert len(kept) <= 20
