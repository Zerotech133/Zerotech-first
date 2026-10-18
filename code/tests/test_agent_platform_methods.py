"""三方法对合成 K 线 fixture 的投票快照测试（背驰/量价背离/RSI 钝化/中性）。"""
from __future__ import annotations

import pandas as pd
import pytest

from zhice_os_v2.agent_platform.methods import (
    ChanMethod,
    EventWindowMethod,
    MethodRegistry,
    RsiSpecMethod,
    SentimentRegimeMethod,
    SentimentTracker,
    VolumeMethod,
    WatchContext,
    create_default_registry,
    DualTfPullbackMethod,
)
from zhice_os_v2.agent_platform.tests_support import synth_bars


def make_ctx(df: pd.DataFrame, ts_code: str = "TEST/USDT") -> WatchContext:
    return WatchContext(
        ts_code=ts_code,
        market="CRYPTO",
        snapshot_tail=df.tail(3),
        bars_df=df,
        regime_risk_off=False,
        memory_hints=[],
    )


class TestRegistry:
    def test_default_registry_three_methods(self):
        """T07 满编：原三插件 + CN 四件（sentiment/trend/event/chan_structure）。"""
        reg = create_default_registry()
        assert sorted(reg.ids()) == [
            "chan", "chan_structure", "event_window", "rsi_spec",
            "sentiment_regime", "trend_break", "volume",
        ]
        assert len(reg) == 7
        assert "dual_tf_pullback" in __import__(
            "zhice_os_v2.agent_platform.methods", fromlist=["create_cn_registry"]
        ).create_cn_registry().ids()

    def test_duplicate_register_rejected(self):
        reg = MethodRegistry()
        reg.register(ChanMethod())
        with pytest.raises(ValueError):
            reg.register(ChanMethod())

    def test_load_weights(self):
        reg = create_default_registry()
        reg.load_weights({"chan": 2.5, "rsi_spec": 0.1})   # 0.1 低于地板 0.3 → 夹到 0.3
        assert reg.by_id("chan").weight == 2.5
        assert reg.by_id("rsi_spec").weight == 0.3
        assert reg.by_id("volume").weight == 1.0           # 未提到的保持默认


def make_cn_ctx(df: pd.DataFrame, daily: pd.DataFrame | None = None,
                higher: pd.DataFrame | None = None,
                risk_off: bool = False) -> WatchContext:
    return WatchContext(
        ts_code="000001.SZ", market="CN", snapshot_tail=df.tail(3), bars_df=df,
        regime_risk_off=risk_off, memory_hints=[], daily_df=daily, higher_df=higher,
    )


class TestDualTfPullbackMethod:
    def _daily_up(self, n=70):
        close = pd.Series([100 + i * 0.6 for i in range(n)], dtype=float)
        return pd.DataFrame({"date": pd.date_range("2026-01-01", periods=n), "close": close})

    def test_data_insufficient_waits(self):
        vote = DualTfPullbackMethod().evaluate(make_cn_ctx(synth_bars(20, seed=1)))
        assert vote.direction == "WAIT" and vote.confidence == 0.0

    def test_risk_off_vetoes(self):
        df = synth_bars(80, seed=1, pattern="neutral")
        vote = DualTfPullbackMethod().evaluate(make_cn_ctx(df, self._daily_up(), risk_off=True))
        assert vote.direction == "WAIT"
        assert "risk-off" in vote.reasons[0]

    def test_missing_daily_is_unknown(self):
        vote = DualTfPullbackMethod().evaluate(make_cn_ctx(synth_bars(80, seed=1)))
        assert vote.direction == "WAIT"
        assert "日线数据不可用" in vote.reasons[0]

    def test_higher_frame_future_rows_are_not_consumed_by_provider_contract(self):
        df = synth_bars(80, seed=1, pattern="neutral")
        higher = synth_bars(40, seed=2, pattern="neutral")
        higher["dt"] = pd.date_range("2026-01-01", periods=40, freq="1h")
        vote = DualTfPullbackMethod().evaluate(make_cn_ctx(df, self._daily_up(), higher))
        assert vote.method_id == "dual_tf_pullback"
        assert vote.params_snapshot["state"] in {"WATCH", "READY"}


class TestChanMethod:
    def test_divergence_fixture_votes_sell(self):
        """chan_div fixture（30 根顶背驰尾段：两次死叉+价新高 DIF 未新高）→ chan SELL。"""
        df = synth_bars(200, seed=11, pattern="chan_div")
        vote = ChanMethod().evaluate(make_ctx(df))
        assert vote.method_id == "chan"
        assert vote.direction == "SELL"
        assert vote.confidence == pytest.approx(0.7)
        assert vote.reasons and "背驰" in vote.reasons[0]

    def test_insufficient_data_waits(self):
        df = synth_bars(30, seed=1, pattern="neutral")   # < 35 根 MACD 稳定门槛
        vote = ChanMethod().evaluate(make_ctx(df))
        assert vote.direction == "WAIT"
        assert vote.confidence == 0.0

    def test_neutral_fixture_not_sell(self):
        df = synth_bars(200, seed=42, pattern="neutral")
        vote = ChanMethod().evaluate(make_ctx(df))
        assert vote.direction in ("HOLD", "WAIT")
        assert vote.direction != "SELL"

    def test_params_snapshot_complete(self):
        vote = ChanMethod().evaluate(make_ctx(synth_bars(120, seed=3, pattern="rsi_dull")))
        assert vote.params_snapshot["macd"] == "12/26/9"
        assert vote.params_snapshot["semantics"] == "simplified_chan_v1"


class TestVolumeMethod:
    def test_div_divergence_votes_sell(self):
        """vol_div fixture：末根缩量小新高 → 量价背离 SELL 0.6。"""
        df = synth_bars(200, seed=11, pattern="vol_div")
        vote = VolumeMethod().evaluate(make_ctx(df))
        assert vote.direction == "SELL"
        assert vote.confidence == pytest.approx(0.6)
        assert "量价背离" in vote.reasons[0]

    def test_insufficient_data_waits(self):
        df = synth_bars(15, seed=1)
        vote = VolumeMethod().evaluate(make_ctx(df))
        assert vote.direction == "WAIT"
        assert vote.confidence == 0.0

    def test_neutral_fixture_hold_or_wait(self):
        df = synth_bars(200, seed=42, pattern="neutral")
        vote = VolumeMethod().evaluate(make_ctx(df))
        assert vote.direction in ("HOLD", "WAIT")
        assert vote.direction != "SELL"

    def test_shrinking_volume_decline_holds(self):
        """缩量阴跌 → HOLD 0.5（抛压衰竭继续持有）。"""
        m = VolumeMethod()
        n = 60
        rets = [0.0] * (n - 3) + [-0.005, -0.004, -0.003]
        closes = [100.0]
        for r in rets:
            closes.append(closes[-1] * (1 + r))
        closes = closes[1:]
        vols = [100.0] * (n - 3) + [30.0, 25.0, 20.0]    # 明显缩量
        dt = pd.date_range("2026-09-01", periods=n, freq="15min", tz="UTC")
        df = pd.DataFrame({
            "dt": dt, "open": closes, "high": [c * 1.001 for c in closes],
            "low": [c * 0.999 for c in closes], "close": closes, "volume": vols,
        })
        vote = m.evaluate(make_ctx(df))
        assert vote.direction == "HOLD"
        assert vote.confidence == pytest.approx(0.5)


class TestRsiSpecMethod:
    def test_rsi_dull_fixture_votes_sell(self):
        """RSI 钝化 fixture（末段连续急涨）→ SELL 0.5。"""
        df = synth_bars(200, seed=5, pattern="rsi_dull")
        vote = RsiSpecMethod().evaluate(make_ctx(df))
        assert vote.direction == "SELL"
        assert vote.confidence == pytest.approx(0.5)
        assert "钝化" in vote.reasons[0]

    def test_oversold_fixture_waits(self):
        """超卖 fixture（末段连续急跌）→ WAIT。"""
        df = synth_bars(200, seed=6, pattern="oversold")
        vote = RsiSpecMethod().evaluate(make_ctx(df))
        assert vote.direction == "WAIT"
        assert "超卖" in vote.reasons[0]

    def test_neutral_fixture_hold(self):
        """微幅上行锯齿 → RSI 中性偏强区 → HOLD。"""
        df = synth_bars(300, seed=42, pattern="neutral")
        vote = RsiSpecMethod().evaluate(make_ctx(df))
        assert vote.direction == "HOLD"

    def test_insufficient_data_waits(self):
        df = synth_bars(15, seed=1)
        vote = RsiSpecMethod().evaluate(make_ctx(df))
        assert vote.direction == "WAIT"
        assert vote.confidence == 0.0


class TestEventWindowMethod:
    def test_fulfilled_ignores_future_daily_close(self):
        df = synth_bars(5, seed=3)
        df["dt"] = pd.date_range("2026-09-11", periods=len(df), freq="15min", tz="UTC")
        daily = pd.DataFrame({
            "date": pd.to_datetime(["2026-09-10", "2026-09-11", "2026-09-12"]),
            "close": [100.0, 100.0, 120.0],
        })
        ctx = make_ctx(df)
        ctx.daily_df = daily
        ctx.events = [{"event_date": "2026-09-10", "event_type": "theme"}]
        vote = EventWindowMethod().evaluate(ctx)
        assert vote.direction == "WAIT"

    def test_remaining_zero_is_expired(self):
        df = synth_bars(5, seed=4)
        df["dt"] = pd.date_range("2026-09-10", periods=len(df), freq="15min", tz="UTC")
        daily = pd.DataFrame({
            "date": pd.to_datetime(["2026-09-10"]),
            "close": [100.0],
        })
        ctx = make_ctx(df)
        ctx.daily_df = daily
        ctx.events = [{"event_date": "2026-09-20", "event_type": "theme"}]
        # 评估日为事件日+10 天，即 remaining=0。
        ctx.bars_df["dt"] = pd.date_range("2026-09-20", periods=len(df), freq="15min", tz="UTC")
        vote = EventWindowMethod().evaluate(ctx)
        assert vote.direction == "SELL"
        assert "到期未兑现" in vote.reasons[0]


class TestSentimentRegimeMethod:
    def test_tracker_baseline_is_read_before_current_observation(self):
        tracker = SentimentTracker()
        method = SentimentRegimeMethod(tracker=tracker)
        first = make_ctx(synth_bars(10, seed=1), ts_code="TEST/A")
        first.sentiment = {"zt_count": 100, "zb_count": 0, "max_lb": 8,
                           "red_count": 60, "total_count": 100}
        assert method.evaluate(first).direction == "HOLD"

        second = make_ctx(synth_bars(10, seed=1), ts_code="TEST/A")
        second.bars_df = second.bars_df.copy()
        second.bars_df["dt"] = second.bars_df["dt"] + pd.Timedelta(days=1)
        second.sentiment = {"zt_count": 70, "zb_count": 50, "max_lb": 8,
                            "red_count": 60, "total_count": 100}
        vote = method.evaluate(second)
        assert vote.direction == "SELL"
        assert any("涨停家数回落" in reason for reason in vote.reasons)
        assert any("炸板率" in reason for reason in vote.reasons)

    def test_tracker_peak_is_read_before_current_observation(self):
        tracker = SentimentTracker()
        method = SentimentRegimeMethod(tracker=tracker)
        first = make_ctx(synth_bars(10, seed=2), ts_code="TEST/B")
        first.sentiment = {"zt_count": 80, "zb_count": 0, "max_lb": 8,
                           "red_count": 60, "total_count": 100}
        method.evaluate(first)

        second = make_ctx(synth_bars(10, seed=2), ts_code="TEST/B")
        second.bars_df = second.bars_df.copy()
        second.bars_df["dt"] = second.bars_df["dt"] + pd.Timedelta(days=1)
        second.sentiment = {"zt_count": 80, "zb_count": 50, "max_lb": 5,
                            "red_count": 60, "total_count": 100}
        vote = method.evaluate(second)
        assert vote.direction == "SELL"
        assert any("连板高度断层3板" in reason for reason in vote.reasons)
        assert any("炸板率" in reason for reason in vote.reasons)


class TestVoteContract:
    def test_vote_validation(self):
        from zhice_os_v2.agent_platform.methods.base import MethodVote

        with pytest.raises(ValueError):
            MethodVote("x", "BUY", 0.5)               # 非法方向
        v = MethodVote("x", "SELL", 1.7)              # 置信度夹到 [0,1]
        assert v.confidence == 1.0
        assert v.to_dict()["confidence"] == 1.0

    def test_evaluate_all_returns_three_votes(self):
        """T07 满编：evaluate_all 返回全部已注册方法票（7 件）。"""
        reg = create_default_registry()
        ctx = make_ctx(synth_bars(200, seed=42))
        votes = reg.evaluate_all(ctx)
        assert len(votes) == len(reg) == 7
        assert all(v.direction in ("SELL", "HOLD", "WAIT") for v in votes)
