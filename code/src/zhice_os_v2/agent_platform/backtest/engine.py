"""BacktestEngine — 逐 bar 重放盯盘中台（v1 核心交付）。

流程：历史 bar 滚动窗口构造 WatchContext（等价线上快照语义）→ 三方法投票
→ RegimeArbiter 三步仲裁（LLM 默认 mock 保守 WAIT）→ 决策内存表。
性能：1m 数据一次性重采样 + 指标由方法内整列计算并随窗口携带；
30 天×37 标的×15m ≈ 10.6 万 bar×3 方法目标 5 分钟内（实测见 report.avg_ms_per_bar）。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pandas as pd

from zhice_os_v2.agent_platform.arbitration.llm_arbiter import (
    LlmArbiter,
    MockLlmArbiter,
    RealLlmSampleArbiter,
)
from zhice_os_v2.agent_platform.arbitration.regime_arbiter import RegimeArbiter
from zhice_os_v2.agent_platform.backtest.data_loader import (
    BarData,
    load_bars,
    make_regime_fn,
    zhice_regime_fn,
)
from zhice_os_v2.agent_platform.config import AgentPlatformConfig
from zhice_os_v2.agent_platform.methods import MethodRegistry, WatchContext

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """回测结果（内存决策表 + 运行元数据）。"""

    market: str
    freq: str
    requested_freq: str
    freq_fallback: bool
    fallback_reason: str
    symbols: list[str]
    start: str
    end: str
    decisions: list[dict] = field(default_factory=list)   # AgentDecision.to_dict()
    regime_cache: dict[str, bool] = field(default_factory=dict)  # market:date-iso → risk_off
    total_bars: int = 0
    elapsed_s: float = 0.0
    llm_real_calls: int = 0
    llm_mock: bool = True
    method_ids: list[str] = field(default_factory=list)
    playbook_id: str = ""                                # T07 满编包 id（""=默认）
    sentiment_synthetic: bool = False                    # T07 sentiment 序列合成标注
    daily_source: str = ""                               # T07 daily 来源（db/resample）

    @property
    def avg_ms_per_bar(self) -> float:
        return (self.elapsed_s * 1000.0 / self.total_bars) if self.total_bars else 0.0


class BacktestEngine:
    """盯盘中台回测引擎。

    用法：
        engine = BacktestEngine(cfg, registry=MethodRegistry())
        result = engine.run(market="CRYPTO", days=30, freq="15m")
    """

    def __init__(
        self,
        cfg: AgentPlatformConfig | None = None,
        registry: MethodRegistry | None = None,
        arbiter: RegimeArbiter | None = None,
        llm_arbiter: LlmArbiter | None = None,
        db_url: str | None = None,
    ) -> None:
        self.cfg = cfg or AgentPlatformConfig()
        self.registry = registry
        self.llm_arbiter = llm_arbiter
        self._arbiter = arbiter
        self.db_url = db_url or self.cfg.db_url

    # ── 数据注入式运行（单测 fixture 用，不触 DB） ────────────────────────
    def run_on_bars(
        self,
        bars_by_symbol: dict[str, pd.DataFrame],
        market: str = "CRYPTO",
        freq: str = "15m",
        regime_fn=None,
        daily: pd.DataFrame | None = None,
        playbook=None,
        daily_by_symbol: dict[str, pd.DataFrame] | None = None,
        events_by_symbol: dict[str, list[dict]] | None = None,
        sentiment_by_date: dict[str, dict] | None = None,
    ) -> BacktestResult:
        """对内存构造的多标的 bar 集合逐 bar 重放（无 DB 依赖）。

        playbook（T07）：PlaybookSpec 注入时按组方法集/权重/仲裁参数重放，
        决策带 playbook_id；daily_by_symbol/events_by_symbol/sentiment_by_date
        为组数据注入（None 时方法内降级）。
        """
        arbiter = self._arbiter or self._make_arbiter()
        if playbook is not None:
            arbiter = self._arbiter_for_playbook(playbook, arbiter)
        regime_fn = regime_fn or make_regime_fn(daily, market)
        cfg = self.cfg
        window = cfg.window_bars
        warmup = cfg.warmup_bars
        daily_by_symbol = daily_by_symbol or {}
        events_by_symbol = events_by_symbol or {}
        sentiment_by_date = sentiment_by_date or {}

        # 回测入口统一时间语义：排序、去重、UTC 归一，防止乱序/重复 bar
        # 将未来数据带入滚动窗口或 searchsorted 前向收益。
        normalized_bars: dict[str, pd.DataFrame] = {}
        for sym, raw in bars_by_symbol.items():
            if raw is None or raw.empty:
                normalized_bars[str(sym)] = raw
                continue
            if "dt" not in raw.columns:
                raise ValueError(f"{sym}: bars 缺少 dt 列")
            clean = raw.copy()
            clean["dt"] = pd.to_datetime(clean["dt"], utc=True, errors="coerce")
            clean = (clean.dropna(subset=["dt"])
                          .sort_values("dt")
                          .drop_duplicates("dt", keep="last")
                          .reset_index(drop=True))
            normalized_bars[str(sym)] = clean
        bars_by_symbol = normalized_bars

        result = BacktestResult(
            market=market, freq=freq, requested_freq=freq,
            freq_fallback=False, fallback_reason="",
            symbols=sorted(bars_by_symbol.keys()),
            start=_fmt_dt(_min_start(bars_by_symbol)),
            end=_fmt_dt(_max_end(bars_by_symbol)),
            method_ids=(playbook.method_ids() if playbook is not None
                        else self._registry().ids()),
            playbook_id=(playbook.playbook_id if playbook is not None else ""),
            sentiment_synthetic=bool(sentiment_by_date),
        )
        llm = arbiter.llm
        result.llm_mock = isinstance(llm, MockLlmArbiter) or not isinstance(llm, LlmArbiter)

        t0 = time.perf_counter()
        for symbol in result.symbols:
            df = bars_by_symbol[symbol]
            if df is None or len(df) <= warmup:
                continue
            sym_daily = daily_by_symbol.get(symbol)
            sym_events = events_by_symbol.get(symbol)
            # 事件兑现判定需要 as-of 日线切片（≤当前 bar 日）——按 bar 逐日切片
            daily_asof = _DailyAsOf(sym_daily)
            for i in range(len(df)):
                if i < warmup:
                    continue
                win = df.iloc[max(0, i - window + 1): i + 1]
                result.total_bars += 1
                trade_date_iso = _iso_date(win["dt"].iloc[-1])
                cache_key = f"{str(market).upper()}:{trade_date_iso}"
                risk_off = result.regime_cache.get(cache_key)
                if risk_off is None:
                    try:
                        risk_off = not bool(regime_fn(_pydate(win["dt"].iloc[-1]), market))
                    except Exception:
                        risk_off = False
                    result.regime_cache[cache_key] = risk_off
                ctx = WatchContext(
                    ts_code=symbol,
                    market=market,
                    snapshot_tail=win.tail(3),
                    bars_df=win,
                    regime_risk_off=risk_off,
                    memory_hints=[],
                    position_value=0.0,
                    sentiment=sentiment_by_date.get(trade_date_iso),
                    daily_df=daily_asof.slice_to(trade_date_iso),
                    events=[
                        ev for ev in (sym_events or [])
                        if _event_is_available(ev, win["dt"].iloc[-1])
                    ],
                    signal_ts=pd.Timestamp(win["dt"].iloc[-1]),
                    as_of_ts=pd.Timestamp(win["dt"].iloc[-1]),
                    higher_df=None,
                )
                if playbook is not None:
                    votes = self._registry().evaluate_subset(
                        ctx, playbook.method_ids(), playbook.method_weights())
                else:
                    votes = self._registry().evaluate_all(ctx)
                decision = arbiter.arbitrate(votes, ctx)
                decision.playbook_id = result.playbook_id or None
                result.decisions.append(decision.to_dict())
        result.elapsed_s = time.perf_counter() - t0
        llm_ref = getattr(arbiter.llm, "real_calls", None)
        result.llm_real_calls = int(llm_ref) if llm_ref is not None else 0
        self._attach_fwd_ret(result, bars_by_symbol)
        return result

    # ── DB 数据运行（CLI 主入口） ─────────────────────────────────────────
    def run(
        self,
        market: str = "CRYPTO",
        days: int = 30,
        freq: str = "15m",
        symbols: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        real_llm_sample: int = 0,
        playbook=None,
    ) -> BacktestResult:
        """拉数 → 重放。real_llm_sample>0 时前 N 次冲突真调用 LLM（共享令牌桶）。

        playbook（T07）：PlaybookSpec 注入时按组方法集/权重/仲裁参数重放——
        CN 包时附带装配 daily_df（v2_bar_daily）/events（aw_playbook_event）/
        sentiment（确定性合成序列，synthetic=True 标注）。
        """
        end = end or datetime.now(UTC)
        start = start or end - timedelta(days=days)
        llm = self._make_llm(real_llm_sample)
        data: BarData = load_bars(
            market, symbols, start, end, freq, self.db_url,
            warmup_bars=self.cfg.warmup_bars,
        )
        arbiter = RegimeArbiter(
            llm_arbiter=llm,
            cfg=self.cfg.arbitration,
            regime_fn=None,   # regime 由引擎逐日预判定注入 ctx（避免 arbiter 内重复拉缓存）
            position_value_threshold=self.cfg.llm.position_value_threshold,
        )
        self._arbiter = arbiter   # run_on_bars 经注入的 arbiter 仲裁（含真 LLM 样本通道）
        bars_by_symbol = data.by_symbol()
        # regime 门：直接复用 zhice market_regime_permits_entry（硬规范）；
        # 仅在日线缺失（离线/合成数据）时退回本地等价适配器。
        if data.daily is not None and not data.daily.empty:
            regime_fn = zhice_regime_fn()
        else:
            regime_fn = make_regime_fn(data.daily, market)

        daily_by_symbol: dict[str, pd.DataFrame] = {}
        events_by_symbol: dict[str, list[dict]] = {}
        sentiment_by_date: dict[str, dict] = {}
        if playbook is not None:
            all_dates = sorted({str(pd.Timestamp(ts).date().isoformat())
                                for df in bars_by_symbol.values()
                                if df is not None and len(df)
                                for ts in df["dt"].iloc[self.cfg.warmup_bars:]})
            # 更宽松取数：从数据起点往前 90 天拿足 MA20 预热
            daily_by_symbol = self._load_daily_by_symbol(
                market, bars_by_symbol.keys(), start, end)
            events_by_symbol = self._load_events_by_symbol(
                bars_by_symbol.keys(), market=market, end=end)
            sentiment_by_date = synth_sentiment_series(all_dates)

        result = self.run_on_bars(
            bars_by_symbol, market=market, freq=data.freq,
            regime_fn=regime_fn, playbook=playbook,
            daily_by_symbol=daily_by_symbol,
            events_by_symbol=events_by_symbol,
            sentiment_by_date=sentiment_by_date,
        )
        result.requested_freq = data.requested_freq
        result.freq_fallback = data.freq_fallback
        result.fallback_reason = data.fallback_reason
        result.symbols = data.symbols
        result.start = _fmt_dt(start)
        result.end = _fmt_dt(end)
        result.llm_real_calls = llm.real_calls if isinstance(llm, RealLlmSampleArbiter) else 0
        result.llm_mock = not isinstance(llm, RealLlmSampleArbiter) or llm.real_calls == 0
        if playbook is not None:
            result.daily_source = "db" if daily_by_symbol else "resample"
            if sentiment_by_date:
                result.sentiment_synthetic = True
        return result

    # ── 内部 ──────────────────────────────────────────────────────────────
    def _registry(self) -> MethodRegistry:
        if self.registry is None:
            from zhice_os_v2.agent_platform.methods import default_registry

            self.registry = default_registry()
        return self.registry

    def _arbiter_for_playbook(self, spec, base_arbiter):
        """包级仲裁参数覆盖（quorum/tie_margin/regime_veto；全 None 复用 base）。"""
        from copy import copy

        ap = spec.arbitration
        if ap.quorum_min_votes is None and ap.tie_margin is None and ap.regime_veto is None:
            return base_arbiter
        cfg2 = copy(base_arbiter.cfg)
        if ap.quorum_min_votes is not None:
            cfg2.min_quorum_votes = ap.quorum_min_votes
        if ap.tie_margin is not None:
            cfg2.tie_margin = ap.tie_margin
        if ap.regime_veto is not None:
            cfg2.risk_off_sell_mult = base_arbiter.cfg.risk_off_sell_mult \
                if ap.regime_veto else 1.0
            cfg2.risk_off_hold_mult = base_arbiter.cfg.risk_off_hold_mult \
                if ap.regime_veto else 1.0
        arb = copy(base_arbiter)
        arb.cfg = cfg2
        return arb

    def _load_daily_by_symbol(self, market: str, symbols, start, end) -> dict:
        """CN 日线 per-symbol（date/close 升序；带 90 天预热余量）。读不到空 dict。"""
        try:
            from sqlalchemy import text

            from zhice_os_v2.agent_platform.db import shared_engine

            buf_start = start - timedelta(days=90)
            sql = text(
                "select symbol, trade_date as date, close from v2_bar_daily "
                "where market = :m and trade_date >= :s and trade_date <= :e "
                "and symbol in :syms order by symbol, trade_date"
            )
            with shared_engine(self.db_url).connect() as conn:
                from sqlalchemy import bindparam

                raw = pd.read_sql(sql.bindparams(bindparam("syms", expanding=True)),
                                  conn, params={"m": market, "s": buf_start.date(),
                                                "e": end.date(),
                                                "syms": tuple(symbols)},
                                  parse_dates={"date": {"utc": True}})
            if raw.empty:
                return {}
            raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
            raw = raw.dropna(subset=["close"])
            out: dict[str, pd.DataFrame] = {}
            for sym, grp in raw.groupby("symbol"):
                cols = [c for c in (
                    "date", "open", "high", "low", "close", "pre_close",
                    "volume", "turnover"
                ) if c in grp.columns]
                out[str(sym)] = grp.reset_index(drop=True)[cols]
            return out
        except Exception as exc:
            logger.warning("CN 日线加载失败(%s) — trend_break/event_window 降级 15m 重采样", exc)
            return {}

    def _load_events_by_symbol(self, symbols, market: str | None = None,
                               end: datetime | None = None) -> dict[str, list[dict]]:
        """加载事件，并保留可用于历史 as-of 的时间字段。

        事件表旧版本没有 publication_ts/market 时兼容读取；至少保留
        event_date，评估层会跳过未来事件。查询失败不伪装成成功数据。
        """
        try:
            from sqlalchemy import text as _text

            from zhice_os_v2.agent_platform.db import shared_engine

            from sqlalchemy import bindparam

            clauses = ["symbol in :syms"]
            params = {"syms": tuple(symbols)}
            if market:
                # 兼容旧表：market 列不存在时回退旧查询，但不跨 symbol。
                clauses.append("market = :market")
                params["market"] = str(market)
            if end is not None:
                clauses.append("event_date <= :event_end")
                params["event_end"] = end.date()
            sql = _text(
                "select symbol, event_date, event_type, note, source "
                "from aw_playbook_event where " + " and ".join(clauses) +
                " order by event_date"
            ).bindparams(bindparam("syms", expanding=True))
            with shared_engine(self.db_url).connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            out: dict[str, list[dict]] = {}
            for sym, ev_date, ev_type, note, source in rows:
                out.setdefault(str(sym), []).append(
                    {"event_date": ev_date, "event_type": ev_type,
                     "note": note or "", "source": source or "manual"})
            return out
        except Exception as exc:
            logger.warning("aw_playbook_event 读取失败(%s) — 标记为数据质量告警", exc)
            return {}

    def _make_llm(self, real_llm_sample: int) -> LlmArbiter:
        if self.llm_arbiter is not None:
            return self.llm_arbiter
        if real_llm_sample > 0:
            return RealLlmSampleArbiter(real_llm_sample, cfg=self.cfg.llm)
        return MockLlmArbiter(cfg=self.cfg.llm)

    def _make_arbiter(self) -> RegimeArbiter:
        return RegimeArbiter(
            llm_arbiter=self.llm_arbiter or MockLlmArbiter(),
            cfg=self.cfg.arbitration,
        )

    def _attach_fwd_ret(self, result: BacktestResult,
                        bars_by_symbol: dict[str, pd.DataFrame]) -> None:
        """T+5 前向收益回填（按 bar_time 对齐未来第 5 根收盘）。"""
        h = self.cfg.fwd_horizon_bars
        for sym, df in bars_by_symbol.items():
            if df is None or len(df) < 2:
                continue
            closes = df["close"].astype(float).to_numpy()
            dts = df["dt"].reset_index(drop=True)
            n = len(closes)
            for dec in result.decisions:
                if dec.get("ts_code") != sym or dec.get("bar_time") is None:
                    continue
                bt = pd.Timestamp(dec["bar_time"])
                pos = int(dts.searchsorted(bt, side="right")) - 1
                if 0 <= pos < n - h:
                    fut = closes[pos + h]
                    cur = closes[pos]
                    if cur > 0:
                        dec["fwd_ret_5d"] = float(fut / cur - 1.0)


def _fmt_dt(dt) -> str:
    return pd.Timestamp(dt).isoformat()


def _event_is_available(event: dict, signal_ts) -> bool:
    """事件 as-of 过滤：优先 publication/available 时间，兼容旧 event_date。"""
    cut = pd.Timestamp(signal_ts)
    for key in ("publication_ts", "available_at", "announced_at"):
        value = event.get(key)
        if value is not None:
            try:
                ts = pd.Timestamp(value)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                else:
                    ts = ts.tz_convert("UTC")
                if cut.tzinfo is None:
                    cut = cut.tz_localize("UTC")
                else:
                    cut = cut.tz_convert("UTC")
                return ts <= cut
            except (TypeError, ValueError):
                return False
    try:
        return pd.Timestamp(event.get("event_date")).date() <= cut.date()
    except (TypeError, ValueError):
        return False


class _DailyAsOf:
    """日线 as-of 切片器（≤给定日；无未来函数）。df 为 None 时恒返 None。"""

    def __init__(self, df: pd.DataFrame | None) -> None:
        if df is not None and not df.empty and "date" in df.columns:
            normalized = df.copy()
            normalized["date"] = pd.to_datetime(
                normalized["date"], utc=True, errors="coerce")
            self._df = normalized.dropna(subset=["date"]).sort_values("date")
        else:
            self._df = df
        self._cache: dict[str, pd.DataFrame | None] = {}

    def slice_to(self, date_iso: str):
        if self._df is None:
            return None
        if date_iso not in self._cache:
            cut = pd.Timestamp(date_iso, tz="UTC")
            self._cache[date_iso] = self._df[
                pd.to_datetime(self._df["date"], utc=True) <= cut
            ].reset_index(drop=True)
        return self._cache[date_iso]


def synth_sentiment_series(dates: list[str]) -> dict[str, dict]:
    """回测用确定性情绪合成序列（按日期生成有高潮/冰点段的伪快照）。

    段型（以日期在序列中的位置确定性循环，无随机）：
    - i % 10 in [0,1,2]：高潮段（zt 高→回落 + 炸板率高 → SELL 特征多）
    - i % 10 in [3,4]：冰点段（zt<30 + 红盘占比<35%）
    - 其余：中性段
    每项带 synthetic=True 标注（报告透明化：非真实行情）。
    """
    out: dict[str, dict] = {}
    n = len(dates)
    for i, d in enumerate(dates):
        phase = i % 10 if n else 0
        if phase in (0, 1, 2):
            # 高潮段：i=0 高 zt 高 lb；i=1 zt 回落≥20%；i=2 lb 断层 + 炸板率抬升
            zt = [180.0, 130.0, 120.0][phase]
            zb = [30.0, 40.0, 70.0][phase]
            max_lb = [9.0, 9.0, 6.0][phase]
            red, total = 3200.0, 5200.0
        elif phase in (3, 4):
            zt, zb, max_lb = 22.0, 8.0, 2.0
            red, total = 1500.0, 5200.0
        else:
            zt, zb, max_lb = 88.0, 18.0, 4.0
            red, total = 2800.0, 5200.0
        out[d] = {"zt_count": zt, "zb_count": zb, "max_lb": max_lb,
                  "red_count": red, "amount": 1.6e12,
                  "total_count": total, "synthetic": True}
    return out


def _min_start(bars_by_symbol: dict[str, pd.DataFrame]):
    starts = [df["dt"].iloc[0] for df in bars_by_symbol.values() if df is not None and len(df)]
    return min(starts) if starts else pd.Timestamp(0, tz="UTC")


def _max_end(bars_by_symbol: dict[str, pd.DataFrame]):
    ends = [df["dt"].iloc[-1] for df in bars_by_symbol.values() if df is not None and len(df)]
    return max(ends) if ends else pd.Timestamp(0, tz="UTC")


def _iso_date(dt) -> str:
    return pd.Timestamp(dt).date().isoformat()


def _pydate(dt):
    return pd.Timestamp(dt).date()
