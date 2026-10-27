# -*- coding: utf-8 -*-
"""SignalAggregator 单测：加权融合 / 分歧标注 / 异常降级 / 写库降级。

上游 signal.py / adapter.py 由 adapter-dev 并行交付；本测试全程使用
本地 stub（不 import 真模块、不实现生产 signal.py/adapter.py，避免冲突）。
DB 写入一律 mock 掉，测试不触库。
"""

import os
import sys
import types

import pytest

QUANT_BT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if QUANT_BT not in sys.path:
    sys.path.insert(0, QUANT_BT)

from strategy_layer import aggregator  # noqa: E402
from strategy_layer.aggregator import AggResult, SignalAggregator  # noqa: E402

_REAL_PERSIST = aggregator.persist_combined  # autouse mock 之前留底


class _FakeSignal:
    """最小 duck-type Signal 桩（aggregator 按属性访问）。"""

    def __init__(
        self,
        symbol,
        action="buy",
        score=50.0,
        confidence=0.5,
        rationale="r",
        strategy="s",
    ):
        self.symbol = symbol
        self.action = action
        self.score = score
        self.confidence = confidence
        self.rationale = rationale
        self.strategy = strategy
        self.trade_date = "20260101"


# ── 本地 stub：Signal / Registry / Adapter（契约同 adapter-dev）───────
class StubSignal:
    """signal.Signal 契约替身（dataclass 形态一致）。"""

    def __init__(
        self,
        symbol,
        action,
        score,
        confidence,
        rationale="",
        strategy="",
        trade_date="20260101",
    ):
        self.symbol = symbol
        self.action = action
        self.score = score
        self.confidence = confidence
        self.rationale = rationale
        self.strategy = strategy
        self.trade_date = trade_date


class StubAdapter:
    """StrategyAdapter 契约替身。"""

    strategy_id = "stub"

    def __init__(self, sid, signals=None, error=None, weight=None):
        self.strategy_id = sid
        if weight is not None:
            self.weight = weight
        self._signals = signals or []
        self._error = error

    def score(self, trade_date):
        if self._error is not None:
            raise self._error
        return self._signals


class StubRegistry:
    """Registry 契约替身：.enabled_ids() / .get(id) / .weights。"""

    def __init__(self, adapters, weights=None, enabled=None):
        self._adapters = {a.strategy_id: a for a in adapters}
        self._enabled = list(enabled) if enabled is not None else list(self._adapters)
        self.weights = weights or {}

    def enabled_ids(self):
        return list(self._enabled)

    def get(self, sid):
        return self._adapters.get(sid)


@pytest.fixture(autouse=True)
def _mock_db_write(monkeypatch):
    """默认 mock 掉写库；返回调用记录供断言。"""
    calls = []

    def fake_persist(rows):
        calls.append(list(rows))
        return True

    monkeypatch.setattr(aggregator, "persist_combined", fake_persist)
    return calls


# ── 加权融合 ──────────────────────────────────────────────────────────
class TestFusion:
    def test_weighted_score_and_majority_action(self):
        """组合分 = Σ w_i×score_i（归一化）；action 权重多数决。"""
        a1 = StubAdapter("s1", [StubSignal("000338.SZ", "buy", 80, 0.9)], weight=0.6)
        a2 = StubAdapter("s2", [StubSignal("000338.SZ", "sell", 60, 0.7)], weight=0.4)
        agg = SignalAggregator()
        res = agg.run("20260101", adapters=[a1, a2])
        assert len(res.combined) == 1
        row = res.combined[0]
        # 0.6×80 + 0.4×60 = 72
        assert row["score"] == pytest.approx(72.0, abs=1e-6)
        # buy 权重和 0.6 > sell 0.4 → buy
        assert row["action"] == "buy"
        # confidence = max(0.9,0.7) × 平均权重系数(1/2) = 0.45
        assert row["confidence"] == pytest.approx(0.45, abs=1e-6)
        assert set(row["contributions"]) == {"s1", "s2"}
        assert row["contributions"]["s1"]["weight"] == pytest.approx(0.6)
        assert row["contributions"]["s2"]["weight"] == pytest.approx(0.4)
        # buy+sell 对立 → 分歧标注（详见 TestFusion.test_divergence_buy_vs_sell）
        assert row["divergence"] is True

    def test_divergence_buy_vs_sell(self):
        a1 = StubAdapter("s1", [StubSignal("600000.SH", "buy", 80, 0.9)], weight=0.5)
        a2 = StubAdapter("s2", [StubSignal("600000.SH", "sell", 60, 0.7)], weight=0.5)
        res = SignalAggregator().run("20260101", adapters=[a1, a2])
        row = res.combined[0]
        assert row["divergence"] is True
        assert len(res.divergences) == 1
        dv = res.divergences[0]
        assert dv["symbol"] == "600000.SH"
        assert dv["actions"] == {"s1": "buy", "s2": "sell"}

    def test_no_divergence_same_direction(self):
        a1 = StubAdapter("s1", [StubSignal("600000.SH", "buy", 80, 0.9)])
        a2 = StubAdapter("s2", [StubSignal("600000.SH", "watch", 50, 0.5)])
        res = SignalAggregator().run("20260101", adapters=[a1, a2])
        assert res.combined[0]["divergence"] is False
        assert res.divergences == []

    def test_hold_not_opposite_to_buy(self):
        """buy+hold 不算对立分歧（对立仅 buy↔sell）。"""
        a1 = StubAdapter("s1", [StubSignal("600519.SH", "buy", 90, 0.8)])
        a2 = StubAdapter("s2", [StubSignal("600519.SH", "hold", 55, 0.6)])
        res = SignalAggregator().run("20260101", adapters=[a1, a2])
        assert res.combined[0]["divergence"] is False

    def test_rationale_joined_with_strategy_tags(self):
        a1 = StubAdapter(
            "s1", [StubSignal("000338.SZ", "buy", 80, 0.9, rationale="双刀多头")]
        )
        res = SignalAggregator().run("20260101", adapters=[a1])
        assert "[s1] 双刀多头" in res.combined[0]["rationale"]


# ── 降级行为 ──────────────────────────────────────────────────────────
class TestDegradation:
    def test_adapter_exception_skipped(self, capsys):
        """抛异常 adapter → 降级日志 + 0 贡献，其余策略照常。"""
        boom = StubAdapter("boom", error=RuntimeError("模型挂了"))
        good = StubAdapter("good", [StubSignal("000338.SZ", "buy", 70, 0.8)])
        res = SignalAggregator().run("20260101", adapters=[boom, good])
        out = capsys.readouterr().out
        assert "[DEGRADED]" in out and "boom" in out
        assert len(res.combined) == 1
        assert set(res.combined[0]["contributions"]) == {"good"}
        # 单策略贡献 → 平均权重系数 = 1 → confidence 不折损
        assert res.combined[0]["confidence"] == pytest.approx(0.8)

    def test_empty_return_adapter_ignored(self):
        """空返回 adapter → 无贡献无影响。"""
        empty = StubAdapter("empty", [])
        good = StubAdapter("good", [StubSignal("000338.SZ", "hold", 50, 0.5)])
        res = SignalAggregator().run("20260101", adapters=[empty, good])
        assert len(res.combined) == 1
        assert set(res.combined[0]["contributions"]) == {"good"}

    def test_db_write_failure_warns_but_returns(self, monkeypatch, capsys):
        """写库失败（返回 False）→ 只告警，内存结果照常返回。"""
        monkeypatch.setattr(aggregator, "persist_combined", lambda rows: False)
        good = StubAdapter("good", [StubSignal("000338.SZ", "buy", 70, 0.8)])
        res = SignalAggregator().run("20260101", adapters=[good])
        assert len(res.combined) == 1
        assert "[WARN]" in capsys.readouterr().out

    def test_db_write_exception_warns_but_returns(self, monkeypatch, capsys):
        """写库抛异常（psycopg2+docker 双兜底均炸）→ 只告警不中断。"""

        def boom(rows):
            raise RuntimeError("DB down")

        monkeypatch.setattr(aggregator, "persist_combined", boom)
        good = StubAdapter("good", [StubSignal("000338.SZ", "buy", 70, 0.8)])
        res = SignalAggregator().run("20260101", adapters=[good])
        assert len(res.combined) == 1
        assert "DB down" in capsys.readouterr().out


# ── 空集与注册表驱动 ─────────────────────────────────────────────────
class TestRegistryAndEmpty:
    def test_empty_adapters_returns_empty(self):
        res = SignalAggregator().run("20260101", adapters=[])
        assert isinstance(res, AggResult)
        assert res.combined == [] and res.divergences == []

    def test_registry_empty_enabled_returns_empty(self):
        agg = SignalAggregator(StubRegistry({}, weights={}))
        res = agg.run("20260101")
        assert res.combined == [] and res.divergences == []

    def test_no_adapters_no_registry_returns_empty(self):
        res = SignalAggregator(None).run("20260101")
        assert res.combined == []

    def test_registry_drives_adapters(self):
        """adapters=None 时从 registry.enabled_ids()+get() 推导。"""
        a1 = StubAdapter("kg_boost", [StubSignal("000338.SZ", "buy", 66, 0.6)])
        reg = StubRegistry([a1], weights={"kg_boost": 0.25})
        res = SignalAggregator(reg).run("20260101")
        assert len(res.combined) == 1
        assert res.combined[0]["contributions"]["kg_boost"]["weight"] == 1.0

    def test_registry_missing_adapter_skipped(self, capsys):
        """registry.get 返回 None 的 id → 跳过不致命。"""
        reg = StubRegistry([StubAdapter("a", [StubSignal("x", "buy", 1, 1)])])
        reg._enabled = ["a", "ghost"]
        res = SignalAggregator(reg).run("20260101")
        assert len(res.combined) == 1
        assert "ghost" not in res.combined[0]["contributions"]

    def test_disabled_weight_falls_back_default(self):
        """registry.weights 缺该策略 → 兜底等权 1.0。"""
        a1 = StubAdapter("a", [StubSignal("x", "buy", 60, 0.5)])
        a2 = StubAdapter("b", [StubSignal("x", "buy", 80, 0.5)])
        reg = StubRegistry([a1, a2], weights={})  # 无权重表
        res = SignalAggregator(reg).run("20260101")
        assert res.combined[0]["score"] == pytest.approx(70.0)


# ── 写库链路（mock DB 进程）──────────────────────────────────────────
class TestPersist:
    """persist_combined 兜底链路：需绕过 autouse mock 直测真函数。"""

    @pytest.fixture(autouse=True)
    def _use_real_persist(self, monkeypatch):
        monkeypatch.setattr(aggregator, "persist_combined", _REAL_PERSIST)

    def test_persist_empty_rows_true(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            aggregator, "_persist_psycopg2", lambda rows: called.append(1) or True
        )
        assert aggregator.persist_combined([]) is True
        assert called == []  # 空行短路，不触库

    def test_persist_fallback_to_docker(self, monkeypatch):
        """psycopg2 抛异常 → docker exec 兜底成功。"""

        def boom(rows):
            raise RuntimeError("no psycopg2")

        calls = []
        monkeypatch.setattr(aggregator, "_persist_psycopg2", boom)
        monkeypatch.setattr(
            aggregator, "_persist_docker_exec", lambda rows: calls.append(1) or True
        )
        row = {
            "trade_date": "20260101",
            "symbol": "x",
            "action": "buy",
            "score": 1.0,
            "confidence": 0.5,
            "contributions": {},
            "divergence": False,
            "rationale": "r",
        }
        assert aggregator.persist_combined([row]) is True
        assert len(calls) == 1

    def test_persist_both_fail_raises(self, monkeypatch):
        monkeypatch.setattr(
            aggregator,
            "_persist_psycopg2",
            lambda rows: (_ for _ in ()).throw(RuntimeError("a")),
        )
        monkeypatch.setattr(
            aggregator,
            "_persist_docker_exec",
            lambda rows: (_ for _ in ()).throw(RuntimeError("b")),
        )
        row = {
            "trade_date": "20260101",
            "symbol": "x",
            "action": "buy",
            "score": 1.0,
            "confidence": 0.5,
            "contributions": {},
            "divergence": False,
            "rationale": "r",
        }
        with pytest.raises(RuntimeError, match="写库失败（全链）") as ei:
            aggregator.persist_combined([row])
        # QA-20261017：全链失败须携带每一级根因，不得吞主路径异常
        # （桩为 lambda，函数名显示 <lambda>，故按根因文本断言）
        msg = str(ei.value)
        assert "a" in msg and "b" in msg and msg.count("|") == 1


# ── P2-20261017：upsert 与 top-N ─────────────────────────────────────
class TestUpsertPersist:
    """同日重跑 upsert：主路径 ON CONFLICT、兜底三段式中转。"""

    def test_psycopg2_sql_has_on_conflict(self, monkeypatch):
        captured = {}

        class FakeCur:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                captured["delete_sql"] = sql
                captured["delete_params"] = params

            def execute_values(self, sql, tuples, page_size=200):
                captured["sql"] = sql
                captured["tuples"] = tuples

        class FakeConn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def cursor(self):
                return FakeCur()

            def close(self):
                pass

        class FakeExtras:
            @staticmethod
            def execute_values(cur, sql, tuples, page_size=200):
                cur.execute_values(sql, tuples, page_size)

        import types

        fake = types.ModuleType("psycopg2")
        fake.extras = FakeExtras
        monkeypatch.setitem(sys.modules, "psycopg2", fake)
        monkeypatch.setitem(sys.modules, "psycopg2.extras", FakeExtras)
        monkeypatch.setattr(aggregator, "_pg_connect", lambda: FakeConn())
        row = {
            "trade_date": "20260101",
            "symbol": "x",
            "action": "buy",
            "score": 1.0,
            "confidence": 0.5,
            "contributions": {},
            "divergence": False,
            "rationale": "r",
        }
        assert aggregator._persist_psycopg2([row]) is True
        assert "ON CONFLICT (trade_date, symbol) DO UPDATE" in captured["sql"]
        assert "created_at = now()" in captured["sql"]
        # K-STOCK-420：落库前按当日权威替换
        assert "DELETE FROM" in captured["delete_sql"]
        assert captured["delete_params"] == (["20260101"],) or captured[
            "delete_params"
        ] == (("20260101",),)

    def test_docker_exec_three_step_upsert(self, monkeypatch):
        steps_seen = []

        def fake_run(cmd, input=None, capture_output=True, timeout=None):
            step = cmd[-1]
            steps_seen.append(step)

            class P:
                returncode = 0
                stderr = b""

            return P()

        monkeypatch.setattr(aggregator.subprocess, "run", fake_run)
        row = {
            "trade_date": "20260101",
            "symbol": "x",
            "action": "buy",
            "score": 1.0,
            "confidence": 0.5,
            "contributions": {},
            "divergence": False,
            "rationale": "r",
        }
        assert aggregator._persist_docker_exec([row]) is True
        # K-STOCK-420：TRUNCATE → DELETE 当日 → \copy → merge，四段式
        assert len(steps_seen) == 4
        assert steps_seen[0] == "TRUNCATE strategy._stg_combined"
        assert steps_seen[1] == (
            "DELETE FROM strategy.signals_combined WHERE trade_date IN ('20260101')"
        )
        assert steps_seen[2].startswith("\\copy strategy._stg_combined")
        assert "ON CONFLICT (trade_date, symbol) DO UPDATE" in steps_seen[3]
        # merge 步骤末尾仍带清暂存
        assert "TRUNCATE strategy._stg_combined" in steps_seen[3]


class TestTopNTruncation:
    """单策略 top-N 截断：registry 配置驱动，防排序型策略全市场灌入。"""

    def _mk_adapter(self, sid, n_signals):
        class _A:
            strategy_id = sid

            def score(self, trade_date):
                return [
                    _FakeSignal(score=float(i), symbol="S%03d.SZ" % i)
                    for i in range(n_signals)
                ]

        return _A()

    def test_truncates_to_registry_top_n(self, monkeypatch):
        reg = types.SimpleNamespace(
            enabled_ids=lambda: ["kg_boost"],
            get=lambda sid: self._mk_adapter("kg_boost", 10),
            weights=lambda: {"kg_boost": 1.0},
            top_n=lambda sid: 3,
        )
        agg = aggregator.SignalAggregator(registry=reg)
        res = agg.run("20260101")
        assert len(res.combined) == 3
        scores = sorted((r["score"] for r in res.combined), reverse=True)
        assert scores == [9.0, 8.0, 7.0]  # 按 score 降序保留头部

    def test_no_top_n_passes_all(self, monkeypatch):
        reg = types.SimpleNamespace(
            enabled_ids=lambda: ["kg_boost"],
            get=lambda sid: self._mk_adapter("kg_boost", 5),
            weights=lambda: {"kg_boost": 1.0},
            top_n=lambda sid: None,
        )
        agg = aggregator.SignalAggregator(registry=reg)
        assert len(agg.run("20260101").combined) == 5

    def test_adapter_level_top_n_fallback(self, monkeypatch):
        class _A:
            strategy_id = "kg_boost"
            top_n = 2

            def score(self, trade_date):
                return [
                    _FakeSignal(score=float(i), symbol="S%03d.SZ" % i) for i in range(6)
                ]

        reg = types.SimpleNamespace(
            enabled_ids=lambda: ["kg_boost"],
            get=lambda sid: _A(),
            weights=lambda: {"kg_boost": 1.0},
            top_n=lambda sid: None,
        )
        agg = aggregator.SignalAggregator(registry=reg)
        assert len(agg.run("20260101").combined) == 2


# ── K-STOCK-420：同日重跑权威替换语义 ─────────────────────────────────
def test_persist_psycopg2_rejects_bad_trade_date():
    """trade_date 非纯数字时拒绝删除替换（注入面防线）。"""
    import pytest as _pytest

    _pytest.importorskip(
        "psycopg2.extras",
        reason="本环境未装 psycopg2（生产/判例环境有），"
        "依赖缺失应 skip 而非 fail 污染基线",
    )
    from strategy_layer.aggregator import _persist_psycopg2

    row = {
        "trade_date": "2026-10-17'; DROP TABLE x; --",
        "symbol": "000001.SZ",
        "action": "buy",
        "score": 1.0,
        "confidence": 0.5,
        "contributions": {},
        "divergence": False,
        "rationale": "t",
    }
    with _pytest.raises(ValueError):
        _persist_psycopg2([row])


def test_persist_docker_exec_rejects_bad_trade_date():
    from strategy_layer.aggregator import _persist_docker_exec

    row = {
        "trade_date": "2026101; DELETE",
        "symbol": "000001.SZ",
        "action": "buy",
        "score": 1.0,
        "confidence": 0.5,
        "contributions": {},
        "divergence": False,
        "rationale": "t",
    }
    import pytest as _pytest

    with _pytest.raises(ValueError):
        _persist_docker_exec([row])


# ── N2（dt 契约方案甲反例测试，2026-11 计划）─────────────────────────────
class TestDtContract:
    """经 SignalAggregator().run() 触发 preaggregate_symbol 路径，验证 dt
    契约（WO-QT-20261020-055 方案甲，2026-10-21 老田拍板）：非 None 非
    字符串 dt 显式 ValueError（fail-visible，不降级吞掉，不隐式转换）；
    None 与合法 'YYYY-MM-DD' 字符串放行。

    使用本文件已有 StubSignal/StubAdapter 桩；写库由 autouse
    _mock_db_write 拦截，测试不触库。signal.dt 通过桩实例属性注入
    （StubSignal 默认无 dt → 等价 None）。
    """

    def test_int_dt_raises_valueerror_via_run(self):
        """dt 为整数（如 20260101）→ run() 经 preaggregate_symbol 触发
        契约 ValueError，消息须含 'dt' 与 '契约违例'。"""
        s = StubSignal("000338.SZ", "buy", 80, 0.9)
        s.dt = 20260101  # 整数 dt 契约违例
        a1 = StubAdapter("s1", [s])
        with pytest.raises(ValueError, match="契约违例") as ei:
            SignalAggregator().run("20260101", adapters=[a1])
        msg = str(ei.value)
        assert "dt" in msg and "契约违例" in msg

    def test_datetime_dt_raises_valueerror_via_run(self):
        """dt 为 datetime 对象 → 期望 ValueError（同契约拒收）。"""
        import datetime

        s = StubSignal("000338.SZ", "buy", 80, 0.9)
        s.dt = datetime.datetime(2026, 1, 1, 9, 30, 0)
        a1 = StubAdapter("s1", [s])
        with pytest.raises(ValueError, match="契约违例"):
            SignalAggregator().run("20260101", adapters=[a1])

    def test_none_dt_passes_via_run(self):
        """对照组：dt=None（桩未设 dt）→ 正常通过，产出组合行。"""
        s = StubSignal("000338.SZ", "buy", 80, 0.9)  # 无 dt 属性 → None
        a1 = StubAdapter("s1", [s])
        res = SignalAggregator().run("20260101", adapters=[a1])
        assert len(res.combined) == 1
        assert res.combined[0]["symbol"] == "000338.SZ"

    def test_valid_str_dt_passes_via_run(self):
        """对照组：dt 为合法 'YYYY-MM-DD' 字符串 → 正常通过。"""
        s = StubSignal("000338.SZ", "buy", 80, 0.9)
        s.dt = "2026-01-01"  # 合法 YYYY-MM-DD
        a1 = StubAdapter("s1", [s])
        res = SignalAggregator().run("20260101", adapters=[a1])
        assert len(res.combined) == 1
