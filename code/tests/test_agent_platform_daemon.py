"""T05 watch 常驻化测试：优雅退出信号（stop_event/SIGTERM 语义）、决策落库
（sqlite 内存库，SELL=PENDING_CONFIRM / HOLD·WAIT=ARCHIVED）、循环间隔。
全 mock 数据（合成 ctx 注入 data_provider），不触真 DB。"""
from __future__ import annotations

import threading
import time

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.agent_platform.arbitration.regime_arbiter import AgentDecision
from zhice_os_v2.agent_platform.config import AgentPlatformConfig
from zhice_os_v2.agent_platform.learning import CardBridge
from zhice_os_v2.agent_platform.methods import MethodVote, WatchContext
from zhice_os_v2.agent_platform.models import AwBase, AwDecisionModel
from zhice_os_v2.agent_platform.runtime.shard import ShardPolicy
from zhice_os_v2.agent_platform.runtime.watch_agent import WatchAgent
from zhice_os_v2.agent_platform.runtime.watch_daemon import WatchDaemon
from zhice_os_v2.agent_platform.tests_support import synth_bars
from zhice_os_v2.agent_platform.db import DecisionRecorder


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


def _make_agent(session_factory=None,
                symbols=("DEMO-BTC/USDT", "DEMO-ETH/USDT"), hz="5s") -> WatchAgent:
    cfg = AgentPlatformConfig()
    cfg.snapshot_hz = hz
    shard = ShardPolicy(market="CRYPTO", symbols=list(symbols), snapshot_hz=hz)
    agent = WatchAgent(shard=shard, cfg=cfg)
    if session_factory is not None:
        agent.recorder = DecisionRecorder(
            engine=session_factory.kw["bind"], batch_size=1)
    # 合成数据 data_provider：BTC 用 vol_div（→SELL），ETH 用 neutral（→WAIT/HOLD）
    # T07 满编后 CN 四方法无数据出 WAIT/HOLD 票会稀释表决——本文件聚焦 daemon
    # 生命周期语义，registry 收敛回原生三方法保证投票行为与 T05 时代一致。
    from zhice_os_v2.agent_platform.methods import (
        ChanMethod,
        MethodRegistry,
        RsiSpecMethod,
        VolumeMethod,
    )
    reg = MethodRegistry()
    reg.register(ChanMethod())
    reg.register(VolumeMethod())
    reg.register(RsiSpecMethod())
    agent.registry = reg

    def provider(symbol: str) -> WatchContext:
        pattern = "vol_div" if symbol.endswith("BTC/USDT") else "neutral"
        df = synth_bars(200, seed=7, pattern=pattern)
        return WatchContext(
            ts_code=symbol, market="CRYPTO",
            snapshot_tail=df.tail(3), bars_df=df.tail(120),
            regime_risk_off=False, memory_hints=[], position_value=0.0,
        )
    agent.attach_data_provider(provider)
    return agent


class TestGracefulShutdown:
    def test_stop_event_exits_between_cycles(self, session_factory):
        """stop_event 预置 → 第 1 周期完成后即退（不睡 interval）。"""
        daemon = WatchDaemon(_make_agent(session_factory),
                             interval_s=60.0,   # 若不响应 stop 会挂 60s
                             stop_event=threading.Event())
        daemon.stop_event.set()
        t0 = time.monotonic()
        n = daemon.run()
        assert n == 1
        assert time.monotonic() - t0 < 10.0   # 未等 60s 间隔

    def test_stop_during_interval_waits_return_immediately(self, session_factory):
        """长间隔中途置位 stop_event → wait 提前返回退出。"""
        stop = threading.Event()
        daemon = WatchDaemon(_make_agent(session_factory), interval_s=30.0,
                             stop_event=stop)

        def _stop_soon():
            time.sleep(0.5)
            stop.set()

        threading.Thread(target=_stop_soon, daemon=True).start()
        t0 = time.monotonic()
        n = daemon.run()
        assert n == 1
        assert time.monotonic() - t0 < 10.0   # 30s 间隔被 0.5s 打断

    def test_max_cycles_bounds_loop(self, session_factory):
        daemon = WatchDaemon(_make_agent(session_factory), interval_s=0.01)
        assert daemon.run(max_cycles=2) == 2

    def test_signal_handler_sets_stop(self):
        """_on_signal（SIGTERM/SIGINT 语义）→ stop_event 置位。"""
        daemon = WatchDaemon(_make_agent(), stop_event=threading.Event())
        assert not daemon.stop_event.is_set()
        daemon._on_signal(15, None)            # SIGTERM
        assert daemon.stop_event.is_set()


class TestDecisionPersistence:
    def test_sell_pending_hold_archived(self, session_factory):
        """SELL → PENDING_CONFIRM；HOLD/WAIT → 落库即 ARCHIVED。"""
        daemon = WatchDaemon(_make_agent(session_factory), interval_s=0.0)
        daemon.run(max_cycles=1)
        with session_factory() as s:
            rows = s.query(AwDecisionModel).all()
            assert len(rows) >= 2
            sells = [r for r in rows if r.action == "SELL"]
            others = [r for r in rows if r.action != "SELL"]
            assert sells, "vol_div 形态应产出 SELL"
            assert all(r.status == "PENDING_CONFIRM" for r in sells)
            assert all(r.status == "ARCHIVED" for r in others)

    def test_no_session_factory_no_crash(self):
        """无 session_factory（影子模式）→ 循环正常，无落库。"""
        daemon = WatchDaemon(_make_agent(), interval_s=0.0)
        assert daemon.run(max_cycles=1) == 1

    def test_sell_sends_card_bridge(self, session_factory, tmp_path):
        bridge = CardBridge(outbox_dir=tmp_path / "outbox",
                            ack_inbox_dir=tmp_path / "acks")
        daemon = WatchDaemon(_make_agent(session_factory), interval_s=0.0,
                             card_bridge=bridge)
        daemon.run(max_cycles=1)
        assert bridge.dryrun_cards >= 1
        assert len(list((tmp_path / "outbox").glob("*.json"))) >= 1


class TestLoopInterval:
    def test_interval_respected(self, session_factory):
        """两周期之间等待 interval_s（分片等待实现，测总时长下界）。"""
        daemon = WatchDaemon(_make_agent(session_factory), interval_s=0.3)
        t0 = time.monotonic()
        daemon.run(max_cycles=2)
        assert time.monotonic() - t0 >= 0.3   # 至少一个间隔

    def test_hz_snapshot_interval_default(self, session_factory):
        """缺省 interval = HZ_SECONDS[cfg.snapshot_hz]。"""
        daemon = WatchDaemon(_make_agent(session_factory, hz="10s"))
        assert daemon.interval_s == pytest.approx(10.0)


class TestDataProvider:
    def test_provider_supplies_closed_higher_timeframe(self, monkeypatch):
        """线上 provider 同时提供 LTF/HTF，且丢弃尚未完成的 bucket。"""
        import zhice_os_v2.agent_platform.runtime.watch_daemon as wd

        now = pd.Timestamp("2026-09-20T12:07:00Z")
        ltf = synth_bars(40, seed=1, pattern="neutral")
        ltf["dt"] = pd.date_range("2026-09-20T02:15:00Z", periods=40, freq="15min")
        htf = synth_bars(40, seed=2, pattern="neutral")
        htf["dt"] = pd.date_range("2026-09-18T20:00:00Z", periods=40, freq="1h")
        calls = []

        def fake_load(_market, _symbols, _start, _end, freq, _db, buffer_bars=0):
            calls.append(freq)
            return ltf.copy() if freq == "15m" else htf.copy()

        monkeypatch.setattr(wd, "utc_now", lambda: now)
        monkeypatch.setattr(
            "zhice_os_v2.agent_platform.backtest.data_loader.load_minutes",
            fake_load,
        )
        ctx = wd.make_data_provider("CN", "sqlite://", window_bars=40)("000001.SZ")
        assert ctx is not None
        assert calls == ["15m", "1h"]
        assert ctx.higher_df is not None
        assert ctx.bars_df["dt"].max() <= now - pd.Timedelta(minutes=15)
        assert ctx.higher_df["dt"].max() <= now - pd.Timedelta(hours=1)


class TestCnDailyCache:
    """A1 修复：_cn_daily_cache 只缓存成功结果，失败可重试。"""

    def _daemon(self) -> WatchDaemon:
        return WatchDaemon(_make_agent(), interval_s=0.0)

    def test_none_not_cached_retry_next_call(self, monkeypatch):
        """DB 抖动返 None → 不写缓存，下次调用重查并成功。"""
        import zhice_os_v2.agent_platform.runtime.watch_daemon as wd

        daemon = self._daemon()
        loader = daemon._cn_daily_loader()
        df = pd.DataFrame({"date": [1], "close": [1.0]})

        monkeypatch.setattr(wd, "load_cn_daily", lambda *a, **k: None)
        assert loader("000001.SZ") is None
        assert daemon._cn_daily_cache == {}          # 失败未进缓存

        monkeypatch.setattr(wd, "load_cn_daily", lambda *a, **k: df.copy())
        assert loader("000001.SZ") is not None       # 重试自愈
        assert "000001.SZ" in daemon._cn_daily_cache

    def test_exception_not_cached_retry_next_call(self, monkeypatch):
        """load 抛异常 → 不写缓存，下次调用重查。"""
        import zhice_os_v2.agent_platform.runtime.watch_daemon as wd

        daemon = self._daemon()
        loader = daemon._cn_daily_loader()

        def boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(wd, "load_cn_daily", boom)
        with pytest.raises(RuntimeError):
            loader("000001.SZ")
        assert daemon._cn_daily_cache == {}

        df = pd.DataFrame({"date": [1], "close": [1.0]})
        monkeypatch.setattr(wd, "load_cn_daily", lambda *a, **k: df.copy())
        assert loader("000001.SZ") is not None       # 异常后可重试

    def test_success_result_cached_single_load(self, monkeypatch):
        """成功结果缓存 → 同 symbol 只查库一次。"""
        import zhice_os_v2.agent_platform.runtime.watch_daemon as wd

        daemon = self._daemon()
        loader = daemon._cn_daily_loader()
        df = pd.DataFrame({"date": [1], "close": [1.0]})
        calls = []

        def fake_load(symbol, db_url):
            calls.append(symbol)
            return df.copy()

        monkeypatch.setattr(wd, "load_cn_daily", fake_load)
        first = loader("000001.SZ")
        second = loader("000001.SZ")
        assert calls == ["000001.SZ"]
        assert first is not None and second is not None


class TestBuildDaemon:
    def test_build_memory_mode(self):
        """无 session_factory / 无 DB 数据 → 纯内存影子模式可跑 1 周期。"""
        from zhice_os_v2.agent_platform.runtime.watch_daemon import build_daemon

        cfg = AgentPlatformConfig()
        cfg.snapshot_hz = "5s"
        daemon = build_daemon(cfg=cfg, symbols=[], session_factory=None,
                              use_db_data=False, interval_s=0.0)
        assert daemon.run(max_cycles=1) == 1

    def test_build_with_session(self, session_factory):
        from zhice_os_v2.agent_platform.runtime.watch_daemon import build_daemon

        cfg = AgentPlatformConfig()
        daemon = build_daemon(cfg=cfg, symbols=[], session_factory=session_factory,
                              use_db_data=False, interval_s=0.0,
                              stop_event=threading.Event())
        daemon.stop_event.set()
        assert daemon.run() == 1
        # 落库链路已接通（recorder 挂 engine）
        assert daemon.agent.recorder._session_factory is not None
