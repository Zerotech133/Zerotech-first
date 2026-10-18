"""T06 Playbook 层测试：spec 加载校验 / mapping 绑定持久化 / daemon 双包分组
运行（aw_decision.playbook_id 落库）/ graduation 按 playbook 归因 / 向后兼容。

全 mock 数据（synth_bars 合成 ctx），sqlite 内存库——不触真 DB、不依赖 T07。
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.agent_platform.config import AgentPlatformConfig
from zhice_os_v2.agent_platform.methods import WatchContext, default_registry
from zhice_os_v2.agent_platform.models import (
    AwBase,
    AwDecisionModel,
    AwPlaybookBindingModel,
)
from zhice_os_v2.agent_platform.playbooks import (
    DEFAULT_PLAYBOOK_ID,
    MethodBinding,
    PlaybookSpec,
    default_playbook,
    load_playbook,
)
from zhice_os_v2.agent_platform.playbooks.builtin import (
    builtin_playbook_paths,
    load_builtin_playbooks,
)
from zhice_os_v2.agent_platform.playbooks.mapping import PlaybookMapping
from zhice_os_v2.agent_platform.playbooks.spec import (
    IMPLEMENTED_METHOD_IDS,
    RESERVED_METHOD_IDS,
    PlaybookValidationError,
)
from zhice_os_v2.agent_platform.runtime.watch_daemon import build_daemon
from zhice_os_v2.agent_platform.tests_support import synth_bars


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AwBase.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


# ═══════════════════════════════════════════════════════════════════════════
# 1. PlaybookSpec 加载 / 校验
# ═══════════════════════════════════════════════════════════════════════════
class TestPlaybookSpec:
    def test_load_from_dict_full(self):
        spec = load_playbook({
            "playbook_id": "pb1", "name": "测试包", "market": "CRYPTO",
            "snapshot_hz": 5, "bar_freq": "15m",
            "methods": [{"id": "chan", "weight": 1.5, "role": "主结构"},
                        {"id": "volume", "weight": 0.8},
                        {"id": "event_window", "weight": 1.2, "role": "催化"}],
            "arbitration": {"quorum_min_votes": 2, "tie_margin": 0.18,
                            "regime_veto": True},
            "graduation": {"tier": "CN_DEFAULT"},
            "entry_style_binding": True,
            "risk": {"per_trade_risk_pct": 2.0},
        })
        assert spec.playbook_id == "pb1"
        assert spec.arbitration.quorum_min_votes == 2
        assert spec.arbitration.tie_margin == pytest.approx(0.18)
        assert spec.entry_style_binding is True
        # T07 满编：event_window 已注册 → 三方法全部参与投票
        assert spec.method_ids() == ["chan", "volume", "event_window"]
        assert spec.method_weights() == {"chan": 1.5, "volume": 0.8,
                                         "event_window": 1.2}
        assert all(not m.unavailable for m in spec.methods)

    def test_unknown_method_rejected(self):
        with pytest.raises(PlaybookValidationError, match="未知方法"):
            load_playbook({
                "playbook_id": "pb1", "name": "x", "market": "CRYPTO",
                "methods": [{"id": "no_such_method"}],
            })

    def test_duplicate_method_rejected(self):
        with pytest.raises(PlaybookValidationError, match="重复"):
            load_playbook({
                "playbook_id": "pb1", "name": "x", "market": "CRYPTO",
                "methods": [{"id": "chan"}, {"id": "chan"}],
            })

    def test_weight_bounds_enforced(self):
        with pytest.raises(PlaybookValidationError, match="超界"):
            load_playbook({
                "playbook_id": "pb1", "name": "x", "market": "CRYPTO",
                "methods": [{"id": "chan", "weight": 5.0}],
            })

    def test_missing_required_field(self):
        with pytest.raises(PlaybookValidationError, match="必填字段"):
            load_playbook({"playbook_id": "pb1", "market": "CRYPTO",
                           "methods": [{"id": "chan"}]})

    def test_bad_bar_freq_rejected(self):
        with pytest.raises(PlaybookValidationError, match="bar_freq"):
            load_playbook({
                "playbook_id": "pb1", "name": "x", "market": "CRYPTO",
                "bar_freq": "7m", "methods": [{"id": "chan"}],
            })

    def test_reserved_ids_available_but_unavailable_flag(self):
        """T07 动态转正：预留 id 已注册 → 参与投票；未注册时 unavailable（兼容）。

        通过临时清空进程默认 registry 模拟"未注册"的老进程行为。
        """
        import zhice_os_v2.agent_platform.methods as methods_mod
        from zhice_os_v2.agent_platform.playbooks import spec as spec_mod

        spec = load_playbook({
            "playbook_id": "pb2", "name": "x", "market": "CN",
            "methods": [{"id": rid} for rid in RESERVED_METHOD_IDS],
        })
        # 默认 registry 已注册四件 → 满编参与投票
        assert spec.method_ids() == list(RESERVED_METHOD_IDS)
        assert all(not m.unavailable for m in spec.methods)

        # 模拟老进程（registry 无这些 id）→ unavailable 兼容语义
        orig = methods_mod._DEFAULT
        empty = methods_mod.MethodRegistry()
        empty.register(methods_mod.ChanMethod())
        methods_mod._DEFAULT = empty
        try:
            assert not spec_mod._registered_method_ids() & set(RESERVED_METHOD_IDS)
            spec_old = load_playbook({
                "playbook_id": "pb2", "name": "x", "market": "CN",
                "methods": [{"id": rid} for rid in RESERVED_METHOD_IDS],
            })
            assert spec_old.method_ids() == []
            assert all(m.unavailable for m in spec_old.methods)
        finally:
            methods_mod._DEFAULT = orig

    def test_load_from_yaml_file(self, tmp_path):
        src = builtin_playbook_paths()[0]      # 内置 org_style_cn.yaml
        spec = load_playbook(src)
        assert spec.playbook_id == "org_style_cn"
        assert spec.market == "CN"
        assert spec.snapshot_hz == 60.0
        assert spec.bar_freq == "15m"
        assert spec.arbitration.quorum_min_votes == 2
        assert spec.arbitration.tie_margin == pytest.approx(0.18)
        assert spec.arbitration.regime_veto is True
        assert spec.graduation_tier == "CN_DEFAULT"
        assert spec.entry_style_binding is True
        assert spec.risk == {"per_trade_risk_pct": 2.0, "no_averaging_down": True}
        # T07 满编：四方法全部注册 → 4/4 参与投票
        assert spec.method_ids() == ["event_window", "trend_break",
                                     "chan_structure", "sentiment_regime"]
        assert len(spec.methods) == 4
        assert all(not m.unavailable for m in spec.methods)

    def test_default_playbook_v1_equivalent(self):
        """默认包 = v1 现行为等价：三方法全 1.0，仲裁全 None 回退 config。"""
        cfg = AgentPlatformConfig()
        spec = default_playbook("CRYPTO", cfg)
        assert spec.playbook_id == DEFAULT_PLAYBOOK_ID
        assert spec.method_ids() == list(IMPLEMENTED_METHOD_IDS)
        assert all(w == 1.0 for w in spec.method_weights().values())
        assert spec.arbitration.quorum_min_votes is None
        assert spec.arbitration.tie_margin is None
        assert spec.arbitration.regime_veto is None
        assert spec.snapshot_hz == 5.0
        assert spec.bar_freq == cfg.backtest_freq

    def test_builtin_loadable(self):
        pbs = load_builtin_playbooks()
        assert "org_style_cn" in pbs

    def test_pullback_resonance_builtin_loadable(self):
        pbs = load_builtin_playbooks()
        spec = pbs["pullback_resonance_cn"]
        assert spec.market == "CN"
        assert spec.bar_freq == "15m"
        assert spec.snapshot_hz == 60.0
        assert spec.method_ids() == ["dual_tf_pullback"]
        assert spec.method_weights() == {"dual_tf_pullback": 1.0}
        assert spec.arbitration.quorum_min_votes == 1
        assert spec.arbitration.regime_veto is True
        assert spec.entry_style_binding is True
        assert spec.risk["per_trade_risk_pct"] == 1.0
        assert spec.risk["no_averaging_down"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 2. MethodRegistry.evaluate_subset
# ═══════════════════════════════════════════════════════════════════════════
class TestEvaluateSubset:
    def _ctx(self, pattern="vol_div") -> WatchContext:
        df = synth_bars(200, seed=7, pattern=pattern)
        return WatchContext(ts_code="T/USDT", market="CRYPTO",
                            snapshot_tail=df.tail(3), bars_df=df.tail(120),
                            regime_risk_off=False, memory_hints=[], position_value=0.0)

    def test_subset_only_declared_methods(self):
        reg = default_registry()
        votes = reg.evaluate_subset(self._ctx(), ["chan", "volume"])
        assert {v.method_id for v in votes} == {"chan", "volume"}

    def test_subset_weights_applied(self):
        reg = default_registry()
        votes = reg.evaluate_subset(self._ctx(), ["chan", "volume", "rsi_spec"],
                                    weights={"chan": 1.5, "volume": 0.5, "rsi_spec": 2.0})
        wmap = {v.method_id: v.weight for v in votes}
        assert wmap == {"chan": 1.5, "volume": 0.5, "rsi_spec": 2.0}

    def test_subset_skips_unregistered(self):
        """未注册 id 安静跳过，不抛异常（如 hypothetical_future_method）。"""
        reg = default_registry()
        votes = reg.evaluate_subset(self._ctx(), ["chan", "no_such_method"])
        assert {v.method_id for v in votes} == {"chan"}

    def test_subset_falls_back_to_instance_weight(self):
        reg = default_registry()
        votes = reg.evaluate_subset(self._ctx(), ["chan"])
        assert votes[0].weight == pytest.approx(
            reg.by_id("chan").weight)

    def test_evaluate_all_unchanged(self):
        """evaluate_all 语义不变（全方法、实例权重）。"""
        reg = default_registry()
        votes = reg.evaluate_all(self._ctx())
        assert {v.method_id for v in votes} == set(reg.ids())


# ═══════════════════════════════════════════════════════════════════════════
# 3. PlaybookMapping：绑定 / 分组 / 持久化
# ═══════════════════════════════════════════════════════════════════════════
class TestPlaybookMapping:
    def test_default_when_unbound(self):
        m = PlaybookMapping()
        assert m.playbook_for("BTC/USDT") == DEFAULT_PLAYBOOK_ID

    def test_bind_and_group(self):
        m = PlaybookMapping()
        m.bind("BTC/USDT", "org_style_cn")
        m.bind("ETH/USDT", "scalping")
        groups = m.group_by_playbook(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
        assert groups == {"org_style_cn": ["BTC/USDT"],
                          "scalping": ["ETH/USDT"],
                          DEFAULT_PLAYBOOK_ID: ["SOL/USDT"]}

    def test_rebind_overrides(self):
        m = PlaybookMapping()
        m.bind("BTC/USDT", "a")
        m.bind("BTC/USDT", "b")
        assert m.playbook_for("BTC/USDT") == "b"

    def test_unbind_returns_to_default(self):
        m = PlaybookMapping()
        m.bind("BTC/USDT", "a")
        assert m.unbind("BTC/USDT") == "a"
        assert m.playbook_for("BTC/USDT") == DEFAULT_PLAYBOOK_ID

    def test_persistence_roundtrip(self, session_factory):
        m1 = PlaybookMapping(session_factory=session_factory)
        m1.bind("BTC/USDT", "org_style_cn")
        m1.bind("ETH/USDT", "scalping")
        with session_factory() as s:      # 行确实落库
            rows = {r.symbol: r.playbook_id
                    for r in s.query(AwPlaybookBindingModel).all()}
            assert rows == {"BTC/USDT": "org_style_cn", "ETH/USDT": "scalping"}
        m2 = PlaybookMapping(session_factory=session_factory)  # 新实例重载
        assert m2.playbook_for("BTC/USDT") == "org_style_cn"
        assert m2.playbook_for("ETH/USDT") == "scalping"

    def test_unbind_deletes_row(self, session_factory):
        m = PlaybookMapping(session_factory=session_factory)
        m.bind("BTC/USDT", "org_style_cn")
        m.unbind("BTC/USDT")
        with session_factory() as s:
            assert s.query(AwPlaybookBindingModel).count() == 0
        m2 = PlaybookMapping(session_factory=session_factory)
        assert m2.playbook_for("BTC/USDT") == DEFAULT_PLAYBOOK_ID

    def test_create_all_idempotent_with_new_table(self):
        """aw_playbook_binding 建表幂等（二次 create_all 不炸）。"""
        engine = create_engine("sqlite:///:memory:")
        AwBase.metadata.create_all(engine)
        AwBase.metadata.create_all(engine)
        from sqlalchemy import inspect

        assert inspect(engine).has_table("aw_playbook_binding")
        engine.dispose()


# ═══════════════════════════════════════════════════════════════════════════
# 4. build_daemon 双包分组运行
# ═══════════════════════════════════════════════════════════════════════════
def _spec(playbook_id: str, methods: list[str],
          weights: dict[str, float] | None = None) -> PlaybookSpec:
    weights = weights or {}
    return PlaybookSpec(
        playbook_id=playbook_id, name=playbook_id, market="CRYPTO",
        snapshot_hz=5.0, bar_freq="15m",
        methods=[MethodBinding(id=mid, weight=weights.get(mid, 1.0))
                 for mid in methods],
    )


def _attach_provider(daemon, pattern_map: dict[str, str]) -> None:
    def provider(symbol: str) -> WatchContext:
        pattern = pattern_map.get(symbol, "neutral")
        df = synth_bars(200, seed=7, pattern=pattern)
        return WatchContext(
            ts_code=symbol, market="CRYPTO",
            snapshot_tail=df.tail(3), bars_df=df.tail(120),
            regime_risk_off=False, memory_hints=[], position_value=0.0,
        )
    daemon.agent.attach_data_provider(provider)


class TestDaemonGroupedRun:
    def test_two_playbooks_groups(self, session_factory):
        """两标的各绑不同包：各组用各自方法集，playbook_id 落库正确。"""
        pb_a = _spec("pb_chan_only", ["chan"])                     # 单方法包
        pb_b = _spec("pb_full", ["chan", "volume", "rsi_spec"],
                     weights={"chan": 1.5, "rsi_spec": 0.8})
        cfg = AgentPlatformConfig()
        daemon = build_daemon(cfg=cfg, symbols=["DEMO-BTC/USDT", "DEMO-ETH/USDT"],
                              session_factory=session_factory,
                              use_db_data=False, interval_s=0.0,
                              stop_event=threading.Event(),
                              playbooks={"DEMO-BTC/USDT": pb_a,
                                         "DEMO-ETH/USDT": pb_b})
        _attach_provider(daemon, {"DEMO-BTC/USDT": "chan_div",
                                  "DEMO-ETH/USDT": "neutral"})
        decisions = daemon.run_one_cycle()
        assert len(decisions) == 2

        # BTC 走 pb_chan_only：只有 chan 票
        btc = next(d for d in decisions if d.ts_code == "DEMO-BTC/USDT")
        assert btc.playbook_id == "pb_chan_only"
        assert [v.method_id for v in btc.votes] == ["chan"]

        # ETH 走 pb_full：三方法票、权重来自包
        eth = next(d for d in decisions if d.ts_code == "DEMO-ETH/USDT")
        assert eth.playbook_id == "pb_full"
        wmap = {v.method_id: v.weight for v in eth.votes}
        assert wmap == {"chan": 1.5, "volume": 1.0, "rsi_spec": 0.8}

        # 落库核验（batch_size=1 → 已写 aw_decision）
        session_factory().close()
        with session_factory() as s:
            rows = {r.ts_code: r for r in s.query(AwDecisionModel).all()}
            assert len(rows) == 2
            assert rows["DEMO-BTC/USDT"].playbook_id == "pb_chan_only"
            assert rows["DEMO-ETH/USDT"].playbook_id == "pb_full"
            btc_votes = rows["DEMO-BTC/USDT"].votes_json
            assert [v["method_id"] for v in btc_votes] == ["chan"]

    def test_unbound_symbol_falls_to_default_group(self, session_factory):
        """未绑定标的进 default 组（全方法、实例权重）。"""
        pb_a = _spec("pb_vol", ["volume"])
        daemon = build_daemon(cfg=AgentPlatformConfig(),
                              symbols=["A/USDT", "B/USDT"],
                              session_factory=session_factory,
                              use_db_data=False, interval_s=0.0,
                              stop_event=threading.Event(),
                              playbooks={"A/USDT": pb_a})   # B 未绑定
        _attach_provider(daemon, {})
        decisions = daemon.run_one_cycle()
        a = next(d for d in decisions if d.ts_code == "A/USDT")
        b = next(d for d in decisions if d.ts_code == "B/USDT")
        assert a.playbook_id == "pb_vol"
        assert [v.method_id for v in a.votes] == ["volume"]
        assert b.playbook_id == DEFAULT_PLAYBOOK_ID
        assert {v.method_id for v in b.votes} == set(IMPLEMENTED_METHOD_IDS)

    def test_grouped_arbitration_params_applied(self, session_factory):
        """包级仲裁参数生效：vol_div 形态默认路径 WEIGHTED 出 SELL；
        同形态在 tie_margin=0.9 的包下变平票 → 进 LLM 路径。

        T07 满编后 default 组含 4 个 CN 方法（无 sentiment/daily/events 数据
        时出 WAIT/HOLD 低置信票），会稀释 vol_div 分差 → default 对照组改用
        只含原生三方法的包验证 WEIGHTED/SELL，确保对照语义不变。
        """
        from zhice_os_v2.agent_platform.playbooks import ArbitrationParams

        # 对照：原生三方法包（默认仲裁 tie_margin=0.15）→ WEIGHTED/SELL
        pb_base = PlaybookSpec(
            playbook_id="pb_native3", name="native", market="CRYPTO",
            snapshot_hz=5.0, bar_freq="15m",
            methods=[MethodBinding(id=mid) for mid in IMPLEMENTED_METHOD_IDS[:3]],
        )
        daemon_a = build_daemon(cfg=AgentPlatformConfig(), symbols=["A/USDT"],
                                session_factory=session_factory,
                                use_db_data=False, interval_s=0.0,
                                stop_event=threading.Event(),
                                playbooks={"A/USDT": pb_base})
        _attach_provider(daemon_a, {"A/USDT": "vol_div"})
        base = daemon_a.run_one_cycle()[0]
        assert base.action == "SELL" and base.arbitration_path == "WEIGHTED"

        # 包 tie_margin=0.9：|sell-hold| 分差必小于 0.9 → 平票进 LLM
        pb_tie = PlaybookSpec(
            playbook_id="pb_wide_tie", name="wide", market="CRYPTO",
            snapshot_hz=5.0, bar_freq="15m",
            methods=[MethodBinding(id=mid) for mid in IMPLEMENTED_METHOD_IDS[:3]],
            arbitration=ArbitrationParams(tie_margin=0.9),
        )
        daemon_b = build_daemon(cfg=AgentPlatformConfig(), symbols=["B/USDT"],
                                session_factory=session_factory,
                                use_db_data=False, interval_s=0.0,
                                stop_event=threading.Event(),
                                playbooks={"B/USDT": pb_tie})
        _attach_provider(daemon_b, {"B/USDT": "vol_div"})
        tied = daemon_b.run_one_cycle()[0]
        assert tied.arbitration_path == "LLM"      # 包参数改变了仲裁路径
        assert tied.playbook_id == "pb_wide_tie"

    def test_run_max_cycles_grouped(self, session_factory):
        """daemon.run 整循环（分组路径）多周期可跑。"""
        pb = _spec("pb_full", list(IMPLEMENTED_METHOD_IDS))
        stop = threading.Event()
        daemon = build_daemon(cfg=AgentPlatformConfig(),
                              symbols=["A/USDT", "B/USDT"],
                              session_factory=session_factory,
                              use_db_data=False, interval_s=0.0,
                              stop_event=stop, playbooks={"A/USDT": pb,
                                                          "B/USDT": pb})
        _attach_provider(daemon, {})
        assert daemon.run(max_cycles=2) == 2


# ═══════════════════════════════════════════════════════════════════════════
# 5. 兼容性：不传 playbooks 行为与现在完全一致
# ═══════════════════════════════════════════════════════════════════════════
class TestBackwardCompat:
    def test_no_playbooks_runs_old_path(self, session_factory):
        """playbooks=None → playbook_plan 为 None → 旧 run_cycle 路径。"""
        daemon = build_daemon(cfg=AgentPlatformConfig(),
                              symbols=["DEMO-BTC/USDT", "DEMO-ETH/USDT"],
                              session_factory=session_factory,
                              use_db_data=False, interval_s=0.0)
        assert daemon.playbook_plan is None
        _attach_provider(daemon, {"DEMO-BTC/USDT": "vol_div",
                                  "DEMO-ETH/USDT": "neutral"})
        assert daemon.run(max_cycles=1) == 1
        decisions = daemon.run_one_cycle()
        assert len(decisions) == 2
        with session_factory() as s:
            rows = s.query(AwDecisionModel).all()
            assert len(rows) >= 2
            # 旧路径 playbook_id=NULL（默认包语义）
            assert all(r.playbook_id is None for r in rows)
            # 每条决策三方法全票（v1 等价行为）
            for r in rows:
                assert {v["method_id"] for v in r.votes_json} == \
                    set(IMPLEMENTED_METHOD_IDS)

    def test_all_bound_same_playbook(self, session_factory):
        """全部标的绑同一包：单组执行等价于换方法集的全池周期。"""
        pb = _spec("pb_two", ["chan", "volume"])
        daemon = build_daemon(cfg=AgentPlatformConfig(),
                              symbols=["A/USDT", "B/USDT"],
                              session_factory=session_factory,
                              use_db_data=False, interval_s=0.0,
                              stop_event=threading.Event(),
                              playbooks={"A/USDT": pb, "B/USDT": pb})
        assert len(daemon.playbook_plan) == 1
        pid, spec, syms = daemon.playbook_plan[0]
        assert (pid, syms) == ("pb_two", ["A/USDT", "B/USDT"])
        _attach_provider(daemon, {})
        daemon.run(max_cycles=1)
        decisions = daemon.run_one_cycle()
        assert all(d.playbook_id == "pb_two" for d in decisions)
        for d in decisions:
            assert {v.method_id for v in d.votes} == {"chan", "volume"}


# ═══════════════════════════════════════════════════════════════════════════
# 6. GraduationService 按 playbook 归因
# ═══════════════════════════════════════════════════════════════════════════
class TestGraduationPlaybookFilter:
    @staticmethod
    def _seed_decisions(session_factory, n_per_pb: int) -> None:
        """两包各 n_per_pb 条 chan=SELL 决策，fwd_ret_5d 不同符号。"""
        from zhice_os_v2.agent_platform.arbitration.regime_arbiter import (
            AgentDecision,
        )
        from zhice_os_v2.agent_platform.db import decision_to_row
        from zhice_os_v2.agent_platform.methods import MethodVote

        with session_factory() as s:
            for i in range(n_per_pb):
                for pid, ret in [("pb_good", 0.05), ("pb_bad", -0.05)]:
                    dec = AgentDecision(
                        decision_id=f"aw-t06-{pid}-{i}",
                        ts_code="BTC/USDT", market="CRYPTO",
                        action="SELL", score_sell=0.5, score_hold=0.2,
                        votes=[MethodVote(method_id="chan", direction="SELL",
                                          confidence=0.7)],
                        regime_risk_off=False, arbitration_path="WEIGHTED",
                        fwd_ret_5d=ret,
                    )
                    dec.playbook_id = pid
                    s.add(decision_to_row(dec))
            # 旧行：无 playbook_id（默认包），chan=SELL，收益 0.0
            dec_old = AgentDecision(
                decision_id="aw-t06-legacy", ts_code="BTC/USDT", market="CRYPTO",
                action="SELL", score_sell=0.5, score_hold=0.2,
                votes=[MethodVote(method_id="chan", direction="SELL",
                                  confidence=0.7)],
                regime_risk_off=False, arbitration_path="WEIGHTED",
                fwd_ret_5d=0.0,
            )
            s.add(decision_to_row(dec_old))
            s.commit()

    def test_playbook_filter_splits_attribution(self, session_factory):
        from zhice_os_v2.agent_platform.learning import GraduationService

        self._seed_decisions(session_factory, n_per_pb=8)
        svc = GraduationService(session_factory=session_factory,
                                min_samples={"CRYPTO": 6})
        good = svc.evaluate("chan", "CRYPTO", playbook_id="pb_good")
        bad = svc.evaluate("chan", "CRYPTO", playbook_id="pb_bad")
        assert good["sample_size"] == 8
        assert good["mean_fwd_ret"] == pytest.approx(0.05)
        assert good["decision"] == "PROMOTE"
        assert bad["sample_size"] == 8
        assert bad["mean_fwd_ret"] == pytest.approx(-0.05)
        assert bad["decision"] == "DEMOTE"
        assert good["details"].get("playbook_id") == "pb_good"

    def test_playbook_weight_dimension_isolated(self, session_factory):
        """具体打法包毕业读取自身复合键权重，不污染默认权重。"""
        from zhice_os_v2.agent_platform.learning import GraduationService
        from zhice_os_v2.agent_platform.models import AwMethodModel

        self._seed_decisions(session_factory, n_per_pb=8)
        with session_factory() as s:
            s.add(AwMethodModel(method_id="chan", display_name="chan", weight=1.0))
            s.add(AwMethodModel(method_id="chan@pb_good", display_name="chan",
                                weight=2.0, playbook_id="pb_good"))
            s.commit()
        svc = GraduationService(session_factory=session_factory,
                                min_samples={"CRYPTO": 6})
        rep = svc.evaluate("chan", "CRYPTO", playbook_id="pb_good")
        assert rep["old_weight"] == pytest.approx(2.0)
        assert rep["new_weight"] == pytest.approx(2.4)
        legacy = svc.evaluate("chan", "CRYPTO")
        assert legacy["old_weight"] == pytest.approx(1.0)

    def test_none_means_legacy_full_scope(self, session_factory):
        """playbook_id=None=全量旧口径：三组样本合并（17 条）。"""
        from zhice_os_v2.agent_platform.learning import GraduationService

        self._seed_decisions(session_factory, n_per_pb=8)
        svc = GraduationService(session_factory=session_factory,
                                min_samples={"CRYPTO": 6})
        rep = svc.evaluate("chan", "CRYPTO")            # 旧签名（位置参数）
        assert rep["sample_size"] == 17                 # 8+8+1 旧行
        mean = (8 * 0.05 + 8 * -0.05 + 0.0) / 17
        assert rep["mean_fwd_ret"] == pytest.approx(mean)

    def test_legacy_rows_only_match_none_query(self, session_factory):
        """旧行 playbook_id=NULL 不混入任何具体包的归因。"""
        from zhice_os_v2.agent_platform.learning import GraduationService

        self._seed_decisions(session_factory, n_per_pb=8)
        svc = GraduationService(session_factory=session_factory)
        rep = svc.evaluate("chan", "CRYPTO", playbook_id="pb_good")
        assert rep["sample_size"] == 8                  # legacy 0.0 不在内

    def test_no_session_returns_empty_as_before(self):
        from zhice_os_v2.agent_platform.learning import GraduationService

        svc = GraduationService()
        rep = svc.evaluate("chan", "CRYPTO", playbook_id="pb_x")
        assert rep["sample_size"] == 0
