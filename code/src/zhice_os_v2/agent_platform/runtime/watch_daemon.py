"""WatchDaemon — WatchAgent 循环常驻模式（T05）。

与 cli.cmd_watch（跑 N 周期退出）的区别：cycles=None 无限循环、间隔 cfg.snapshot_hz
档位秒数、SIGTERM/SIGINT 置位 threading.Event 优雅退出（本轮完成后收尾，参照
jev_gate install_stop 模式但独立实现，不 import jev 代码）。

每周期决策写 aw_decision（注入 session_factory）：status=PENDING_CONFIRM 的 SELL
经 CardBridge 送卡片区（cards_outbox/），HOLD/WAIT 落库即 ARCHIVED。
data_provider 复用 backtest/data_loader：v2_latest_snapshot 最新批 + v2_bar_minute
最新窗口（load_minutes 尾部 window_bars 根）。

T06 playbook 分组：playbook_plan 注入时 run_one_cycle 按 (playbook_id, spec,
symbols) 分组串行执行——各组用各自的方法集/权重/仲裁参数（不改 WatchAgent 核心
类）；决策落库带 playbook_id 归因。plan=None 走旧路径（行为与 v1 完全一致）。
"""
from __future__ import annotations

import logging
import signal
import threading
from typing import TYPE_CHECKING

from zhice_os_v2.agent_platform.config import AgentPlatformConfig, utc_now
from zhice_os_v2.agent_platform.runtime.watch_agent import HZ_SECONDS

if TYPE_CHECKING:  # 仅类型引用，避免运行时循环导入
    from zhice_os_v2.agent_platform.playbooks.spec import PlaybookSpec

logger = logging.getLogger(__name__)


class WatchDaemon:
    """WatchAgent 常驻驱动器。

    stop_event：外部可注入（测试用 threading.Event() 直接触发优雅退出）；
    缺省自建并由 _install_signal_handlers 挂 SIGTERM/SIGINT 置位。
    playbook_plan：T06 分组执行计划 [(playbook_id, spec, symbols)]；None=旧路径。
    """

    def __init__(self, agent, *, interval_s: float | None = None,
                 stop_event: threading.Event | None = None,
                 card_bridge=None, case_writer=None,
                 poll_ack: bool = True,
                 playbook_plan: list[tuple[str, "PlaybookSpec", list[str]]] | None = None
                 ) -> None:
        self.agent = agent
        self.cfg: AgentPlatformConfig = agent.cfg
        self.interval_s = (interval_s if interval_s is not None
                           else HZ_SECONDS.get(self.cfg.snapshot_hz, 5.0))
        self.stop_event = stop_event or threading.Event()
        self.card_bridge = card_bridge
        self.case_writer = case_writer
        self.poll_ack = poll_ack
        self.cycles_done = 0
        self.playbook_plan = playbook_plan
        self._group_arbiters: dict[str, object] = {}
        self._orig_handlers: dict[int, object] = {}

    # ── 信号处理（参照 jev_gate install_stop 模式，独立实现） ──────────────
    def _on_signal(self, signum, _frame) -> None:
        logger.warning("收到信号 %s，本轮完成后退出", signum)
        self.stop_event.set()

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._orig_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):  # 非主线程/平台不支持 → 跳过
                pass

    def _restore_signal_handlers(self) -> None:
        for sig, handler in self._orig_handlers.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
        self._orig_handlers.clear()

    # ── 主循环 ───────────────────────────────────────────────────────────────
    def run(self, max_cycles: int | None = None) -> int:
        """常驻循环；max_cycles=None 无限，stop_event 置位/max_cycles 耗尽后
        优雅退出（recorder flush + memory 关闭由调用方负责）。返回完成周期数。"""
        self._install_signal_handlers()
        logger.info("watch-daemon 启动 agent=%s interval=%.1fs market=%s symbols=%d",
                    self.agent.agent_id, self.interval_s, self.agent.shard.market,
                    len(self.agent.shard.symbols))
        try:
            while not self.stop_event.is_set():
                self.run_one_cycle()
                self.cycles_done += 1
                if max_cycles is not None and self.cycles_done >= max_cycles:
                    break
                # 分片等待（可被 stop_event 即时打断，避免长间隔迟滞退出）
                if self.stop_event.wait(timeout=self.interval_s):
                    break
            else:
                # stop 预置且一轮未跑：仍跑一个空周期保证初始化/落库链路被验证
                if self.cycles_done == 0 and max_cycles is None:
                    self.run_one_cycle()
                    self.cycles_done += 1
        finally:
            self._restore_signal_handlers()
        logger.info("watch-daemon 退出 cycles=%d", self.cycles_done)
        return self.cycles_done

    def run_one_cycle(self) -> list:
        """单周期：采集 → 投票仲裁落库 → SELL 送审（卡片）→ ack 轮询处理。

        playbook_plan 注入时按组执行（各组方法集/权重/仲裁参数独立），否则旧路径。
        """
        if self.playbook_plan:
            decisions = self._run_grouped_cycle()
        else:
            decisions = self.agent.run_cycle()
        sells = pending = archived = 0
        for dec in decisions:
            if dec.action == "SELL":
                sells += 1
                if self.card_bridge is not None:
                    self.card_bridge.send_decision_card(dec.to_dict())
            else:
                archived += 1
        # SELL 保持 PENDING_CONFIRM（等 ack），HOLD/WAIT 落库即 ARCHIVED
        if self._session_factory() is not None:
            self._archive_non_sells(decisions)
            pending = sells
        if decisions:
            logger.info("cycle %d: %d 决策（SELL=%d HOLD/WAIT=%d → 待确认 %d）",
                        self.cycles_done + 1, len(decisions), sells, archived, pending)
        # ack 轮询（文件模式；HTTP ack 由 ack-serve 独立进程处理）
        if self.poll_ack and self.card_bridge is not None and self.case_writer is not None:
            for ack in self.card_bridge.poll_acks():
                self.case_writer.on_ack(ack)
        return decisions

    # ── T06 playbook 分组执行（组间串行，组内复用 agent 采集/落库链路） ──────
    def _arbiter_for(self, playbook_id: str, spec: "PlaybookSpec"):
        """组的仲裁器：spec.arbitration 覆盖 config 值；缺省复用 agent.arbiter。

        quorum_min_votes/tie_margin/regime_veto=True 覆盖；regime_veto=False
        → risk_off_mult 置 1.0（禁否决）；None → 全回退 config。
        """
        from copy import copy

        from zhice_os_v2.agent_platform.config import ArbitrationConfig

        base: ArbitrationConfig = self.agent.cfg.arbitration
        ap = spec.arbitration
        if ap.quorum_min_votes is None and ap.tie_margin is None and ap.regime_veto is None:
            return self.agent.arbiter                    # 全缺省 → 复用（零新建）
        cfg2 = copy(base)
        if ap.quorum_min_votes is not None:
            cfg2.min_quorum_votes = ap.quorum_min_votes
        if ap.tie_margin is not None:
            cfg2.tie_margin = ap.tie_margin
        if ap.regime_veto is not None:
            cfg2.risk_off_sell_mult = base.risk_off_sell_mult if ap.regime_veto else 1.0
            cfg2.risk_off_hold_mult = base.risk_off_hold_mult if ap.regime_veto else 1.0
        cached = self._group_arbiters.get(playbook_id)
        if cached is not None and cached.cfg is cfg2:
            return cached
        arb = copy(self.agent.arbiter)
        arb.cfg = cfg2
        self._group_arbiters[playbook_id] = arb
        return arb

    def _run_grouped_cycle(self) -> list:
        """分组周期：对每组 (spec, symbols) 用该方法集投票 → 组仲裁器 → 落库。

        T07：CN 组的 ctx 追加注入 sentiment（60s 节流拉取）/daily_df/events
        （CRYPTO 组这些字段 None，方法内已兼容降级）。
        """
        from zhice_os_v2.agent_platform import metrics

        registry = self.agent.registry
        decisions = []
        for playbook_id, spec, symbols in self.playbook_plan:
            method_ids = spec.method_ids()
            weights = spec.method_weights()
            arbiter = self._arbiter_for(playbook_id, spec)
            needs_cn = spec.market.upper() == "CN"
            cn_daily = self._cn_daily_loader() if needs_cn else None
            cn_events = self._cn_events_loader() if needs_cn else None
            for symbol in symbols:
                ctx = self._ctx_for_symbol(symbol)
                if ctx is None:
                    continue
                if needs_cn:
                    self._enrich_cn_ctx(ctx, symbol, cn_daily, cn_events)
                votes = registry.evaluate_subset(ctx, method_ids, weights)
                decision = arbiter.arbitrate(votes, ctx)
                decision.playbook_id = playbook_id
                self.agent.recorder.record(decision)
                decisions.append(decision)
                metrics.inc_decision(decision.action)
        self.agent.cycles += 1
        return decisions

    # ── T07 CN 数据注入（sentiment 节流 / daily_df / events） ───────────────
    def _enrich_cn_ctx(self, ctx, symbol: str, daily_loader, events_loader) -> None:
        """CN 组 ctx 补 sentiment/daily_df/events；单项失败不阻塞（保持 None）。"""
        try:
            from zhice_os_v2.agent_platform.methods.sentiment_regime import (
                fetch_sentiment_snapshot,
            )

            ctx.sentiment = fetch_sentiment_snapshot()   # 60s 内部节流
        except Exception as exc:
            logger.warning("sentiment 拉取失败 %s: %s — 降级 None", symbol, exc)
            ctx.sentiment = None
        if daily_loader is not None:
            try:
                ctx.daily_df = daily_loader(symbol)
            except Exception as exc:
                logger.warning("daily_df 加载失败 %s: %s — 降级 None", symbol, exc)
        if events_loader is not None:
            try:
                ctx.events = events_loader(symbol)
            except Exception as exc:
                logger.warning("events 加载失败 %s: %s — 降级 None", symbol, exc)

    def _cn_daily_loader(self):
        """symbol → 日线 DataFrame（date/close）；成功结果进程内缓存。

        失败（DB 抖动返 None / 抛异常）不写缓存：下次循环重查自愈，
        避免瞬时故障把该 symbol 日线永久打成 15m 降级（A1）。
        无数据的 symbol 反复重查成本可控（空表索引查询），且数据
        到货后当轮即生效。
        """
        if getattr(self, "_cn_daily_cache", None) is None:
            self._cn_daily_cache = {}

        def loader(symbol: str):
            cache = self._cn_daily_cache
            if symbol in cache:
                return cache[symbol]
            df = load_cn_daily(symbol, self.cfg.db_url)
            if df is not None:
                cache[symbol] = df
            return df
        return loader

    def _cn_events_loader(self):
        """symbol → 事件列表（aw_playbook_event；无库时空列表）。"""
        factory = self._session_factory()

        def loader(symbol: str):
            if factory is None:
                return []
            from zhice_os_v2.agent_platform.models import AwPlaybookEventModel

            with factory() as session:
                rows = session.query(AwPlaybookEventModel).filter(
                    AwPlaybookEventModel.symbol == symbol,
                ).order_by(AwPlaybookEventModel.event_date).all()
                return [
                    {"event_date": r.event_date, "event_type": r.event_type,
                     "note": r.note, "source": r.source}
                    for r in rows
                ]
        return loader

    def _ctx_for_symbol(self, symbol: str):
        """经 agent.data_provider 取 ctx；无 provider 时 None 跳过。"""
        provider = getattr(self.agent, "data_provider", None)
        if provider is None:
            return None
        try:
            return provider(symbol)
        except Exception as exc:
            logger.warning("data_provider(%s) 失败(%s) — 跳过", symbol, exc)
            return None

    # ── 状态语义：非 SELL 落库后直接 ARCHIVED（不进人审队列） ────────────────
    def _session_factory(self):
        return getattr(self.agent.recorder, "_session_factory", None)

    def _archive_non_sells(self, decisions) -> int:
        """HOLD/WAIT 决策 status: PENDING_CONFIRM → ARCHIVED（SELL 保留待确认）。"""
        from zhice_os_v2.agent_platform.models import AwDecisionModel

        factory = self._session_factory()
        if factory is None:
            return 0
        ids = [d.decision_id for d in decisions if d.action != "SELL"]
        if not ids:
            return 0
        try:
            with factory() as session:
                rows = session.query(AwDecisionModel).filter(
                    AwDecisionModel.decision_id.in_(ids),
                    AwDecisionModel.status == "PENDING_CONFIRM",
                ).all()
                for row in rows:
                    row.status = "ARCHIVED"
                session.commit()
                return len(rows)
        except Exception as exc:
            logger.warning("ARCHIVED 状态更新失败(%s) — 决策已留痕，不阻塞", exc)
            return 0


# ── data_provider 装配（复用 backtest/data_loader 真实数据接口） ────────────
def load_cn_daily(symbol: str, db_url: str, lookback_days: int = 90):
    """CN 标的日线（date/close 升序；趋势/事件方法输入）。无数据返 None。

    从 v2_bar_daily 读（含当日）；读不到返 None 由方法内降级 15m 重采样。
    """
    import pandas as pd
    from sqlalchemy import text

    from zhice_os_v2.agent_platform.config import utc_now
    from zhice_os_v2.agent_platform.db import shared_engine

    try:
        end = utc_now().date()
        sql = text(
            "select trade_date as date, close from v2_bar_daily "
            "where market = 'CN' and symbol = :sym and trade_date >= :start "
            "order by trade_date"
        )
        with shared_engine(db_url).connect() as conn:
            raw = pd.read_sql(sql, conn,
                              params={"sym": symbol,
                                      "start": end.__class__.fromordinal(
                                          end.toordinal() - lookback_days)},
                              parse_dates={"date": {"utc": True}})
        if raw.empty:
            return None
        raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
        raw = raw.dropna(subset=["close"]).reset_index(drop=True)
        return raw if len(raw) else None
    except Exception as exc:
        logger.warning("load_cn_daily(%s) 失败: %s — 返 None", symbol, exc)
        return None


def make_data_provider(market: str, db_url: str, window_bars: int = 120,
                       freq: str = "15m", higher_freq: str = "1h") -> object:
    """构造线上 WatchContext 供应商，并严格只暴露已闭合的 K 线。

    ``bars_df`` 是当前盯盘周期（默认 15m），``higher_df`` 是高周期（默认
    1h）。两者均按当前 ``now`` 做 as-of 截断：重采样产生的最后一个未闭合
    bucket 会被丢弃，避免盘中部分成交量/收盘价进入信号。
    """
    from datetime import timedelta

    import pandas as pd

    from zhice_os_v2.agent_platform.backtest.data_loader import load_minutes
    from zhice_os_v2.agent_platform.methods import WatchContext

    bar_minutes = {"5m": 5, "15m": 15, "1h": 60}
    minutes_per_bar = bar_minutes.get(freq, 15)
    higher_minutes = bar_minutes.get(higher_freq, 60)
    # 高周期需要更多原始分钟线，且两套窗口统一从同一 as-of 起点读取。
    span = timedelta(minutes=max(minutes_per_bar * window_bars,
                                  higher_minutes * max(window_bars // 4, 40)) * 2)

    def _closed(frame: pd.DataFrame, minutes: int, now) -> pd.DataFrame:
        if frame is None or frame.empty:
            return frame
        out = frame.copy()
        out["dt"] = pd.to_datetime(out["dt"], utc=True, errors="coerce")
        # load_minutes 使用 label=left/closed=left；dt+周期才代表该 bar 完成。
        cutoff = pd.Timestamp(now) - pd.Timedelta(minutes=minutes)
        return out.loc[out["dt"] <= cutoff].sort_values("dt").reset_index(drop=True)

    def provider(symbol: str) -> WatchContext | None:
        now = utc_now()
        try:
            bars = load_minutes(market, [symbol], now - span, now, freq, db_url,
                                buffer_bars=0)
            higher = None
            if higher_freq and higher_freq != freq:
                higher = load_minutes(market, [symbol], now - span, now,
                                      higher_freq, db_url, buffer_bars=0)
        except Exception as exc:
            logger.warning("data_provider 取数失败 %s: %s — 跳过", symbol, exc)
            return None
        bars = _closed(bars, minutes_per_bar, now)
        higher = _closed(higher, higher_minutes, now) if higher is not None else None
        if bars is None or bars.empty or len(bars) < 30:
            return None
        bars = bars.tail(window_bars).reset_index(drop=True)
        if higher is not None:
            higher = higher.tail(max(40, window_bars // 4)).reset_index(drop=True)
            if len(higher) < 20:
                higher = None
        return WatchContext(
            ts_code=symbol, market=market,
            snapshot_tail=bars.tail(3), bars_df=bars,
            higher_df=higher,
            regime_risk_off=False, memory_hints=[], position_value=0.0,
        )

    return provider


def build_daemon(cfg: AgentPlatformConfig | None = None,
                 symbols: list[str] | None = None,
                 session_factory=None, use_db_data: bool = True,
                 registry=None,
                 interval_s: float | None = None,
                 stop_event: threading.Event | None = None,
                 playbooks: dict[str, "PlaybookSpec"] | None = None,
                 playbook_binds: list[tuple[str, str]] | None = None
                 ) -> WatchDaemon:
    """cli watch-daemon 子命令装配入口（测试亦用）。

    - session_factory：注入则决策落库（aw_* sqlite/pg）；None 则纯内存影子模式。
    - use_db_data：True 挂 make_data_provider（真数据）；False 不挂（空转冒烟）。
    - playbooks：T06 {symbol: PlaybookSpec}——注入时构建分组执行计划（各组
      方法集/权重/节奏独立，决策带 playbook_id）；None 走旧路径（v1 等价）。
    - playbook_binds：T07 cli --playbook-bind "SYM=pb_id" 列表（叠加到 playbooks）。
    """
    from zhice_os_v2.agent_platform.learning import CardBridge, CaseWriter
    from zhice_os_v2.agent_platform.runtime.shard import ShardPolicy
    from zhice_os_v2.agent_platform.runtime.watch_agent import WatchAgent

    cfg = cfg or AgentPlatformConfig()
    symbols = list(symbols or [])
    shard = ShardPolicy(market=cfg.market, symbols=symbols,
                        snapshot_hz=cfg.snapshot_hz)
    if registry is None and cfg.market.upper() == "CN":
        from zhice_os_v2.agent_platform.methods import create_cn_registry
        registry = create_cn_registry()
    agent = WatchAgent(shard=shard, cfg=cfg, registry=registry)
    agent.startup_check()

    if use_db_data and symbols:
        agent.attach_data_provider(make_data_provider(
            cfg.market, cfg.db_url, window_bars=cfg.window_bars))

    case_writer = None
    if session_factory is not None:
        case_writer = CaseWriter(session_factory=session_factory)
        from zhice_os_v2.agent_platform.db import DecisionRecorder

        agent.recorder = DecisionRecorder(engine=session_factory.kw["bind"],
                                          batch_size=1)

    # T07：--playbook-bind 显式绑定（合并进 playbooks 映射）
    playbooks = dict(playbooks or {})
    if playbook_binds:
        available = load_available_playbooks(cfg)
        for sym, pid in playbook_binds:
            spec = available.get(pid)
            if spec is None:
                raise ValueError(f"--playbook-bind 未知 playbook_id: {pid}"
                                 f"（可选: {sorted(available)}）")
            playbooks[sym] = spec

    # T06：symbol→spec 映射 → 分组执行计划（同组同 spec 对象复用）
    playbook_plan = None
    if playbooks:
        from zhice_os_v2.agent_platform.playbooks.spec import default_playbook

        fallback = default_playbook(cfg.market, cfg)
        by_pid: dict[str, tuple[str, "PlaybookSpec", list[str]]] = {}
        default_entry: tuple[str, "PlaybookSpec", list[str]] | None = None
        for sym in symbols:
            spec = playbooks.get(sym)
            if spec is None:
                if default_entry is None:
                    default_entry = (fallback.playbook_id, fallback, [])
                    by_pid[default_entry[0]] = default_entry
                default_entry[2].append(sym)
            else:
                entry = by_pid.get(spec.playbook_id)
                if entry is None:
                    entry = (spec.playbook_id, spec, [])
                    by_pid[spec.playbook_id] = entry
                entry[2].append(sym)
        playbook_plan = list(by_pid.values())
        if playbook_plan:
            logger.info("playbook 分组: %s",
                        {pid: syms for pid, _, syms in playbook_plan})
    return WatchDaemon(agent, interval_s=interval_s, stop_event=stop_event,
                       card_bridge=CardBridge(), case_writer=case_writer,
                       playbook_plan=playbook_plan)


def load_available_playbooks(cfg: AgentPlatformConfig | None = None) \
        -> dict[str, "PlaybookSpec"]:
    """内置包 + 默认包（--playbook-bind 校验用）。"""
    from zhice_os_v2.agent_platform.playbooks.builtin import load_builtin_playbooks
    from zhice_os_v2.agent_platform.playbooks.spec import (
        DEFAULT_PLAYBOOK_ID,
        default_playbook,
    )

    cfg = cfg or AgentPlatformConfig()
    playbooks = load_builtin_playbooks()
    playbooks[DEFAULT_PLAYBOOK_ID] = default_playbook(cfg.market, cfg)
    return playbooks


def load_bindings_from_db(session_factory) -> list[tuple[str, str]]:
    """aw_playbook_binding 全量 → [(symbol, playbook_id)]。失败抛异常由调用方降级。"""
    from zhice_os_v2.agent_platform.models import AwPlaybookBindingModel

    with session_factory() as session:
        rows = session.query(AwPlaybookBindingModel).all()
        return [(r.symbol, r.playbook_id) for r in rows]
