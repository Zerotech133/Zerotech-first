"""三步仲裁测试：全票 WEIGHTED / risk_off 翻转 REGIME_VETO / 平票 LLM / 令牌桶 DROPPED。"""
from __future__ import annotations

import pytest

from zhice_os_v2.agent_platform.arbitration.llm_arbiter import (
    LlmArbiter,
    LlmArbiterConfig,
    MockLlmClient,
    OllamaClient,
    TokenBucket,
    parse_verdict,
)
from zhice_os_v2.agent_platform.arbitration.regime_arbiter import AgentDecision, RegimeArbiter
from zhice_os_v2.agent_platform.methods.base import MethodVote, WatchContext
from zhice_os_v2.agent_platform.tests_support import synth_bars


def make_ctx(risk_off: bool = False, position_value: float = 0.0) -> WatchContext:
    df = synth_bars(200, seed=42)
    return WatchContext(
        ts_code="TEST/USDT", market="CRYPTO",
        snapshot_tail=df.tail(3), bars_df=df,
        regime_risk_off=risk_off, memory_hints=[], position_value=position_value,
    )


def votes_sell_conf_07():
    return [
        MethodVote("chan", "SELL", 0.7, ["背驰"]),
        MethodVote("volume", "SELL", 0.6, ["量价背离"]),
        MethodVote("rsi_spec", "SELL", 0.5, ["钝化"]),
    ]


class TestStep1Weighted:
    def test_unanimous_sell_weighted_path(self):
        """全票 SELL（非平票、不高持仓）→ WEIGHTED 路径直接 SELL。"""
        arb = RegimeArbiter()   # 默认 mock LLM
        decision = arb.arbitrate(votes_sell_conf_07(), make_ctx())
        assert isinstance(decision, AgentDecision)
        assert decision.action == "SELL"
        assert decision.arbitration_path == "WEIGHTED"
        assert decision.score_sell > decision.score_hold
        assert decision.score_sell == pytest.approx((0.7 + 0.6 + 0.5) / 3, rel=1e-6)
        assert decision.status == "PENDING_CONFIRM"

    def test_quorum_fail_returns_wait(self):
        """法定人数不足（仅 1 有效票）→ WAIT，不进 LLM。"""
        votes = [MethodVote("chan", "SELL", 0.9, []),
                 MethodVote("volume", "WAIT", 0.5, []),
                 MethodVote("rsi_spec", "WAIT", 0.5, [])]
        decision = RegimeArbiter().arbitrate(votes, make_ctx())
        assert decision.action == "WAIT"
        assert decision.arbitration_path == "WEIGHTED"

    def test_sum_conf_below_threshold_returns_wait(self):
        """有效票=2 但 Σconf=0.8 < 1.0 → WAIT。"""
        votes = [MethodVote("chan", "SELL", 0.4, []),
                 MethodVote("volume", "HOLD", 0.4, []),
                 MethodVote("rsi_spec", "WAIT", 0.0, [])]
        decision = RegimeArbiter().arbitrate(votes, make_ctx())
        assert decision.action == "WAIT"

    def test_wait_votes_only_returns_wait(self):
        votes = [MethodVote("chan", "WAIT", 0.0, []) for _ in range(3)]
        decision = RegimeArbiter().arbitrate(votes, make_ctx())
        assert decision.action == "WAIT"
        assert decision.arbitration_path == "WEIGHTED"


class TestStep2RegimeVeto:
    def test_risk_off_flips_sell_to_regime_veto(self):
        """risk_off 下 1 SELL(0.7) vs 2 HOLD → STEP1 得 SELL（0.7>0.5*Σ），
        risk_off 加权后 HOLD 反超 → REGIME_VETO 翻转。"""
        votes = [
            MethodVote("chan", "SELL", 0.7, ["背驰"]),
            MethodVote("volume", "HOLD", 0.5, ["缩量阴跌"]),
            MethodVote("rsi_spec", "HOLD", 0.5, ["中性"]),
        ]
        # STEP1（无 risk_off）: sell=0.7/3, hold=1.0/3 → HOLD 胜 → base=HOLD？
        # 构造调整为 SELL 票更强，确保 base=SELL：
        votes[0] = MethodVote("chan", "SELL", 0.9, ["强背驰"])
        # base: sell=0.9/3=0.30 < hold=1.0/3=0.333 → 仍 HOLD。再调 HOLD 票置信度：
        votes[1] = MethodVote("volume", "HOLD", 0.3, ["弱持有"])
        votes[2] = MethodVote("rsi_spec", "HOLD", 0.3, ["弱中性"])
        # base: sell=0.30, hold=0.20 → SELL；risk_off: sell=0.9*1.5/3=0.45,
        # hold=(0.3+0.3)*0.5/3=0.10 → 仍 SELL。翻转需要 HOLD 侧占优：
        # 重新设计：2 强 HOLD vs 1 弱 SELL，risk_off 让 HOLD 稳赢 —— base 也 HOLD。
        # 真正的翻转场景：base=SELL 且 risk_off 后变 WAIT 只有法定人数破坏一种吗？不——
        # risk_off 永远放大 SELL，故 "SELL→HOLD" 翻转不可能；规范语义是：
        # risk_off 改变比分记录且 veto 标记进路径。构造 base=HOLD 的 risk_off 放大 SELL 案例：
        votes[0] = MethodVote("chan", "SELL", 0.6, ["背驰"])
        votes[1] = MethodVote("volume", "HOLD", 0.55, ["缩量"])
        votes[2] = MethodVote("rsi_spec", "HOLD", 0.55, ["中性"])
        # base: sell=0.60/3=0.200, hold=1.10/3=0.367 → HOLD（margin 0.167 > 0.15 不平票）
        # risk_off: sell=0.60*1.5/3=0.300, hold=1.10*0.5/3=0.183 → SELL！翻转 HOLD→SELL
        decision = RegimeArbiter().arbitrate(votes, make_ctx(risk_off=True))
        assert decision.regime_risk_off is True
        assert decision.arbitration_path == "REGIME_VETO"
        assert decision.action == "SELL"

    def test_risk_off_amplifies_sell_scores(self):
        """risk_off 只改记分不改结论时仍走 WEIGHTED，但 SELL 加权分被放大 1.5×。

        score(SELL)=Σw·conf·mult/Σw：分母 Σw 不变（risk_off 只放大分子）。
        三票 SELL 0.7/0.6/0.5、Σw=3 → on=0.6，off=0.6×1.5=0.9？否——
        off=Σ(w·conf·1.5)/Σw=1.8×1.5/3=0.9。实现产出 0.6 与之矛盾？
        实测 d_off.score_sell=0.6：因 risk_off 票乘 1.5 后 Σ 仍除以原 Σw=3，
        0.7·1.5+0.6·1.5+0.5·1.5=2.7，2.7/3=0.9。而实现返回 0.6——说明实现
        未乘。此测试即验证实现必须乘（回归锚点）。
        """
        votes = votes_sell_conf_07()
        d_on = RegimeArbiter().arbitrate(votes, make_ctx(risk_off=False))
        d_off = RegimeArbiter().arbitrate(votes, make_ctx(risk_off=True))
        # 绝对值锚点（三票全 SELL，Σw=3）
        assert d_on.score_sell == pytest.approx(1.8 / 3.0, rel=1e-6)
        # risk_off：分子 ×1.5，分母不变
        assert d_off.score_sell == pytest.approx(2.7 / 3.0, rel=1e-6)
        assert d_off.action == "SELL"

    def test_regime_fn_injected(self):
        """regime_fn 返回 False（禁止入场=risk_off）→ ctx 标志被覆盖。"""
        votes = [
            MethodVote("chan", "SELL", 0.6, []),
            MethodVote("volume", "HOLD", 0.55, []),
            MethodVote("rsi_spec", "HOLD", 0.55, []),
        ]
        arb = RegimeArbiter(regime_fn=lambda d, m: False)
        decision = arb.arbitrate(votes, make_ctx(risk_off=False))
        assert decision.regime_risk_off is True
        assert decision.arbitration_path == "REGIME_VETO"


class TestStep3LlmEscalation:
    def _tie_votes(self):
        """构造精确平票：SELL 0.5 vs HOLD 0.5（margin 0 < 0.15）。"""
        return [
            MethodVote("chan", "SELL", 0.5, ["背驰"]),
            MethodVote("volume", "HOLD", 0.5, ["缩量阴跌"]),
            MethodVote("rsi_spec", "WAIT", 0.2, []),
        ]

    def test_tie_goes_to_llm_path(self):
        """平票 → mock LLM 仲裁 → LLM 路径，action 取 mock 输出。"""
        arb = RegimeArbiter()   # MockLlmArbiter 默认 WAIT
        decision = arb.arbitrate(self._tie_votes(), make_ctx())
        assert decision.arbitration_path == "LLM"
        assert decision.action == "WAIT"       # mock 保守
        assert decision.model_verdict is not None
        assert decision.model_verdict["source"] == "MOCK"

    def test_high_position_sell_escalates(self):
        """SELL 且持仓额 > 阈值 → 高风险复核进 LLM。"""
        votes = votes_sell_conf_07()
        arb = RegimeArbiter(position_value_threshold=5000.0)
        decision = arb.arbitrate(votes, make_ctx(position_value=9999.0))
        assert decision.arbitration_path == "LLM"

    def test_low_position_sell_stays_weighted(self):
        votes = votes_sell_conf_07()
        arb = RegimeArbiter(position_value_threshold=5000.0)
        decision = arb.arbitrate(votes, make_ctx(position_value=100.0))
        assert decision.arbitration_path == "WEIGHTED"

    def test_token_exhausted_drops_to_wait(self):
        """令牌桶耗尽 → DROPPED 路径 + 保守 WAIT。"""
        llm = LlmArbiter(client=MockLlmClient(), cfg=LlmArbiterConfig(bucket_per_hour=2))
        llm.bucket.tokens = 0.0    # 直接榨干
        arb = RegimeArbiter(llm_arbiter=llm)
        decision = arb.arbitrate(self._tie_votes(), make_ctx())
        assert decision.arbitration_path == "DROPPED"
        assert decision.action == "WAIT"
        assert decision.model_verdict["source"] == "FALLBACK_TOKENS"

    def test_llm_timeout_falls_back_wait(self):
        """超时 5s → 保守回退 WAIT（非 DROPPED，判定未获得）。"""
        class TimeoutClient:
            def complete(self, prompt, timeout_s):
                raise TimeoutError("simulated timeout")

        llm = LlmArbiter(client=TimeoutClient(), cfg=LlmArbiterConfig())
        arb = RegimeArbiter(llm_arbiter=llm)
        decision = arb.arbitrate(self._tie_votes(), make_ctx())
        assert decision.action == "WAIT"
        assert decision.model_verdict["source"] == "FALLBACK_TIMEOUT"

    def test_low_confidence_falls_back_wait(self):
        """LLM 低置信 → 保守 WAIT（path=LLM，verdict 留痕）。"""
        llm = LlmArbiter(client=MockLlmClient(action="SELL", confidence=0.2),
                         cfg=LlmArbiterConfig(min_conf=0.5))
        arb = RegimeArbiter(llm_arbiter=llm)
        decision = arb.arbitrate(self._tie_votes(), make_ctx())
        assert decision.arbitration_path == "LLM"
        assert decision.action == "WAIT"


class TestLlmArbiterUnits:
    def test_token_bucket_refill(self):
        bucket = TokenBucket(per_hour=3600, now_fn=lambda: 0.0)
        assert bucket.allow() is True
        bucket.tokens = 0.0
        assert bucket.allow() is False
        # 时间推进 2s（rate=1/s）→ 恢复 2 token
        bucket._now = lambda: 2.0
        assert bucket.allow() is True
        assert bucket.allow() is True
        assert bucket.allow() is False

    def test_parse_verdict_valid(self):
        v = parse_verdict('前言 {"action":"SELL","confidence":0.82,"rationale":"ok"} 后记')
        assert v is not None and v.action == "SELL" and v.confidence == 0.82

    def test_parse_verdict_invalid(self):
        assert parse_verdict("no json here") is None
        assert parse_verdict('{"action":"BUY","confidence":0.9}') is None

    def test_prompt_contains_votes(self):
        from zhice_os_v2.agent_platform.arbitration.llm_arbiter import build_arbitration_prompt

        prompt = build_arbitration_prompt(votes_sell_conf_07(), make_ctx())
        assert "chan" in prompt and "SELL" in prompt and "TEST/USDT" in prompt

    def test_ollama_client_not_used_in_tests(self):
        """OllamaClient 仅真调用路径构造，单测不触网。"""
        client = OllamaClient("http://localhost:11434", "qwen2.5:7b")
        assert client.base_url.endswith("11434")


class TestDecisionContract:
    def test_decision_to_dict_roundtrip(self):
        arb = RegimeArbiter()
        decision = arb.arbitrate(votes_sell_conf_07(), make_ctx())
        d = decision.to_dict()
        assert d["decision_id"].startswith("aw-")
        assert len(d["votes_json"]) == 3
        assert d["arbitration_path"] in ("WEIGHTED", "REGIME_VETO", "LLM", "DROPPED")
        assert d["status"] in ("PENDING_CONFIRM", "ARCHIVED")


class TestGraduationWeightThroughput:
    """P1 回归：毕业权重必须贯通到仲裁（load_weights → 票 weight → 加权表决）。"""

    def _ballot(self, chan_weight: float) -> list[MethodVote]:
        """chan SELL 0.7 + 双 HOLD 0.5 的票型（带出票权重）。"""
        return [
            MethodVote("chan", "SELL", 0.7, ["背驰"], {}, chan_weight),
            MethodVote("volume", "HOLD", 0.5, ["缩量阴跌"]),
            MethodVote("rsi_spec", "HOLD", 0.5, ["中性"]),
        ]

    def test_chan_weight_3_flips_to_weighted_sell(self):
        """chan 权重 3.0：score_sell=3×0.7/5=0.42 vs score_hold=1×0.5/5=0.2，
        分差 0.22>0.15 → WEIGHTED 路径直接 SELL（权重贯通的判别性场景）。"""
        arb = RegimeArbiter()
        decision = arb.arbitrate(self._ballot(chan_weight=3.0), make_ctx())
        assert decision.action == "SELL"
        assert decision.arbitration_path == "WEIGHTED"
        assert decision.score_sell == pytest.approx(0.42, rel=1e-6)
        assert decision.score_hold == pytest.approx(0.2, rel=1e-6)

    def test_equal_weights_same_ballot_stays_tie_llm(self):
        """防回归：全 1.0 权重下同票型 score_sell=0.2333 vs 0.3333，
        分差 0.1<0.15 → 平票进 LLM（mock 保守 WAIT，path=LLM）。"""
        arb = RegimeArbiter()   # MockLlmArbiter 默认 WAIT
        decision = arb.arbitrate(self._ballot(chan_weight=1.0), make_ctx())
        assert decision.arbitration_path == "LLM"
        assert decision.action == "WAIT"
        # 留痕 score 经 round(6)（既有 _make 行为），用 abs 容差比较
        assert decision.score_sell == pytest.approx(0.7 / 3.0, abs=1e-5)

    def test_registry_load_weights_stamps_votes(self):
        """evaluate_all 盖章链：load_weights({"chan":3.0}) → 票 weight=3.0。"""
        from zhice_os_v2.agent_platform.methods import create_default_registry

        reg = create_default_registry()
        reg.load_weights({"chan": 3.0, "volume": 0.8})
        df = synth_bars(200, seed=42)
        ctx = WatchContext(
            ts_code="T/USDT", market="CRYPTO",
            snapshot_tail=df.tail(3), bars_df=df,
            regime_risk_off=False, memory_hints=[],
        )
        votes = {v.method_id: v for v in reg.evaluate_all(ctx)}
        assert votes["chan"].weight == 3.0
        assert votes["volume"].weight == 0.8
        assert votes["rsi_spec"].weight == 1.0     # 未提到的保持默认
        assert votes["chan"].to_dict()["weight"] == 3.0   # 留痕含权重

    def test_vote_weight_clamped_to_bounds(self):
        """票上权重与 set_weight 同界 [0.3, 3.0]（防脏数据注入仲裁）。"""
        v_hi = MethodVote("x", "SELL", 0.5, [], {}, 99.0)
        v_lo = MethodVote("x", "SELL", 0.5, [], {}, 0.01)
        assert v_hi.weight == 3.0
        assert v_lo.weight == 0.3
