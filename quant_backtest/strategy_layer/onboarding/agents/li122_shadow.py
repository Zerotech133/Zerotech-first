# -*- coding: utf-8 -*-
"""S4 每日影子采集接入面（li122-shadow 工位）：context 构造 + Signal 垫片。

职责（缺谁补谁的接入件，纯包装层）：
- **context 构造器** ``build_li122_context``：给 symbol + as_of，从只读
  数据接口取 Li122Agent 该 ruleset 所需的三级 bars（档 A 用 30m/5m/1m、
  档 B 用 1d/30m/5m——按 ruleset 的 upper/mid/low_period 决定取哪些，
  避免档 B 白拉 1m），产出与 li122_agent context 契约逐字一致的 dict；
- **Signal 垫片** ``verdict_to_signal``：Li122Agent propose 的 Verdict →
  ``strategy_layer.signal.Signal``（shadow_collect.collect_once 可落档
  形态）。映射：buy → action="buy"、score=confidence*100（便于影子
  统计四分桶）；hold/拦截/fail-closed → action="hold"、score=0.0，
  由 collect_once 事件帧过滤收口（hold 自动 skip 计数，不入统计样本）；
- **每日采集入口** ``collect_li122_shadow``：自足模式 propose → 垫片 →
  ``shadow_collect.collect_once``（幂等落档，唯一写点=影子档 JSONL）。

as_of 语义（S4 影子调度定案，强约束）：
- Li122Agent.shadow_asof_policy="T+1_prev_close"——chanlun 笔终点确认
  存在右沿滞后，S3 对账实测 as_of=信号 dt 时 122/122 现算不出该信号
  （s3_reconcile_li122_20261018/reconciliation_report.md），影子调度
  **禁止 as_of=当日/信号 dt 当日现算**，必须以「前一交易日收盘全序列」
  自足现算（T+1 盘前出 T 日建议，一日滞后为缠论确认的固有代价）；
- 编码：as_of = "T 15:00"（T=前一交易日，A 股收盘时刻）。dt 字典序下
  "T 15:00" 使 30m/5m/1m 会话桶（桶标结束时刻 ≤ 15:00，chanlun_data
  _bucket_label 口径）与日线 bar（dt="T"）全部入选，而 T+1 及当日
  盘中未完成桶（若数据源混入）全部排除——全序列截断到前交易日收盘；
- 日线 dt 当日分钟桶全排口径（回测基线既有，禁止"聪明地"修正）：
  档 B 日线上级信号 dt="T" 时，Agent 内部按 dt 字典序把 T 当日全部
  30m/5m 桶排除（实际评估截至 T-1 收盘）——构造器只负责把 T 收盘前
  全序列喂足，该口径由 Agent/本体保真。

数据接口选型（复用既有路径，禁止第二份实现）：
- 1d/30m/5m：``chanlun_data.load_kline``（1d=v2_bar_daily+TDX 兜底；
  30m/5m=本地 tushare 仓 stk_mins 重采样，CN 缺失才回源远程/db 兜底
  ——既有回退链路，干跑须核对 source 标签为本地仓）；
- 1m：chanlun_data PERIODS 无 1m；走同一底仓 ``datamart.read("stk_mins")``
  原始 1min（chanlun_data._ts_mins_local 与回测 backtest_li122_vs_single
  .load_minutes 同源），1m 即原始桶无需重采样，不产生第二份重采样实现；
- 交易日历：``datamart._recent_trade_days / _expected_trade_day``（仓库
  现有 provider，premarket.py / kg_source_rating.py 同款复用）。

1m 预算纪律：1m 全窗极易超限，构造器尾部切片只保留 **≥3 交易日**
（A 股 240 根/日 ×3=720 根；低级窗口需求 2×low_window≤240 根，3 交易日
富余），采集时按 utf-8 字节数记入 meta，超 1MB 预算在 meta 打
``1m_over_budget`` 告警（干跑记录实际值）。

纪律：零 LLM、零网络（本地只读接口）；不修改 li122_agent.py /
shadow_collect.py / chanlun 本体；导入期零 chanlun/datamart 依赖
（config.json 隐藏 IO 与 DB 配置全部隔离到调用期，对齐 li122_agent
导入期隔离范式）；除影子档 JSONL（collect_once 名下）外零写副作用，
context 与输入 bars 零变更。

CLI（手动补采/干跑）：
    python li122_shadow.py --symbol 600519.SH --date 20261016
"""

import json
import os
import sys
import time
import logging

_HERE = os.path.dirname(os.path.abspath(__file__))
_QB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # quant_backtest/
_AGENTS_DIR = _HERE

# as_of 收盘编码（A 股收盘时刻；影子采集当前仅 CN 口径）
CLOSE_SUFFIX = " 15:00"
# 1m 尾部切片：保留 ≥3 交易日（预算纪律，见模块 docstring）
ONE_MIN_TAIL_TRADE_DAYS = 3
# 1m 读取自然日回看窗（覆盖 ≥3 交易日 + 节假日/停牌余量）
ONE_MIN_LOOKBACK_CAL_DAYS = 21
# 1m 落档前体积预算（utf-8 字节）；超限在 meta 打告警不拦截
ONE_MIN_BUDGET_BYTES = 1024 * 1024
# 各周期拉取根数（load_kline 内部钳制 [50,1000]；30m 500 根 ≈ 62 交易日，
# 覆盖自足模式上级预热 min_upper_bars=100 与档 B 中级窗）
PERIOD_LIMITS = {"1d": 500, "30m": 500, "5m": 500}
DEFAULT_RULESET = "A_30F_5F_1F"
TRACE_ID = "li122-shadow-collect"


# ---------------------------------------------------------------------- #
# 懒加载（导入期零 chanlun/datamind/DB 依赖，对齐 li122_agent 隔离范式）
# ---------------------------------------------------------------------- #
def _chanlun_data():
    if _QB_ROOT not in sys.path:
        sys.path.insert(0, _QB_ROOT)
    import chanlun_data  # noqa: F401  导入期读 config.json（隐藏 IO），必须懒

    return sys.modules["chanlun_data"]


def _datamart():
    if _QB_ROOT not in sys.path:
        sys.path.insert(0, _QB_ROOT)
    return sys.modules["datamart"]


def _li122_agent():
    """Li122Agent 与其 _cut_bars（复用，不自实现截断）。"""
    if _AGENTS_DIR not in sys.path:
        sys.path.insert(0, _AGENTS_DIR)
    if _QB_ROOT not in sys.path:
        sys.path.insert(0, _QB_ROOT)
    return sys.modules["li122_agent"]


def _rulesets():
    """chanlun_li122.RULESETS 只读引用（懒：导入链含 chanlun_data）。"""
    if _QB_ROOT not in sys.path:
        sys.path.insert(0, _QB_ROOT)
    from chanlun_li122 import RULESETS

    return RULESETS


def _signal_cls():
    from strategy_layer.signal import Signal

    return Signal


# ---------------------------------------------------------------------- #
# as_of 解析（前一交易日收盘，强约束）
# ---------------------------------------------------------------------- #
def _ymd8(trade_date):
    d = str(trade_date).replace("-", "")
    if len(d) != 8 or not d.isdigit():
        raise ValueError("trade_date 必须为 YYYYMMDD，收到: %r" % (trade_date,))
    return d


def _dash(d8):
    return "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:])


def resolve_as_of(trade_date=None, calendar_days=None):
    """→ as_of 字符串 "YYYY-MM-DD 15:00"（前一交易日收盘，dt 字典序口径）。

    - trade_date=None：默认=最近已收盘交易日（datamart._expected_trade_day，
      17:30 前视为当日未收盘自动回退前一日）；
    - trade_date 给定：必须是交易日且 ≤ 最近已收盘交易日——**禁止
      as_of=当日/未收盘交易日**（右沿判例，见模块 docstring）；
    - calendar_days：显式交易日序列注入（YYYYMMDD 升序列表，测试用）；
      缺省走 datamart._recent_trade_days（仓库现有 provider，1h 缓存）。
    """
    dm = _datamart()
    days = list(calendar_days) if calendar_days is not None else dm._recent_trade_days()
    if not days:
        raise RuntimeError(
            "交易日历不可用（datamart trade_cal 为空），"
            "as_of 无法定案，fail-visible 拒绝猜测"
        )
    days = sorted(str(d)[:8] for d in days)
    latest_closed = dm._expected_trade_day(days)
    if not latest_closed:
        raise RuntimeError("交易日历无已收盘交易日，as_of 无法定案")
    if trade_date is None:
        d8 = latest_closed
    else:
        d8 = _ymd8(trade_date)
        if d8 not in days:
            raise ValueError("trade_date=%s 不在交易日历（非交易日），拒绝采集" % d8)
        if d8 > latest_closed:
            raise ValueError(
                "trade_date=%s 晚于最近已收盘交易日 %s——影子调度禁止 "
                "as_of=当日/未收盘交易日（右沿判例：as_of=信号 dt 时 "
                "122/122 现算不出信号，S3 对账）" % (d8, latest_closed)
            )
    return _dash(d8) + CLOSE_SUFFIX


# ---------------------------------------------------------------------- #
# 数据获取（复用 chanlun_data / datamart 既有只读路径）
# ---------------------------------------------------------------------- #
def _fetch_kline(code, period, limit):
    cd = _chanlun_data()
    r = cd.load_kline(code, period, limit)
    if not r.get("ok"):
        raise RuntimeError(
            "chanlun_data.load_kline(%s, %s) 失败：%s" % (code, period, r.get("error"))
        )
    return r["bars"], r.get("source", "")


def _fetch_1m(code, as_of):
    """datamart stk_mins 原始 1min → bar dicts（尾部 ≥3 交易日切片）。

    与 chanlun_data._ts_mins_local / backtest_li122_vs_single.load_minutes
    同一底仓同一 bar 形态（dt="YYYY-MM-DD HH:MM" 北京时间、桶标结束时刻），
    1m 即原始桶不做任何重采样。
    """
    dm = _datamart()
    d8 = _ymd8(as_of[:10])
    y, m, d = int(d8[:4]), int(d8[4:6]), int(d8[6:])
    import datetime as _dt

    start = (
        _dt.date(y, m, d) - _dt.timedelta(days=ONE_MIN_LOOKBACK_CAL_DAYS)
    ).strftime("%Y%m%d")
    df = dm.read("stk_mins", codes=code, start=start, end=d8)
    if df is None or df.empty:
        return [], "stk_mins 空（标的可能不在本地仓）"
    rows = []
    for r in df.to_dict("records"):
        try:
            dt = str(r["trade_time"])[:16]
            o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
            v = float(r.get("vol") or 0)
        except (TypeError, ValueError, KeyError):
            continue
        if c != c or c <= 0 or len(dt) != 16:
            continue
        rows.append({"dt": dt, "o": o, "h": h, "l": l, "c": c, "v": v})
    rows.sort(key=lambda b: b["dt"])
    return rows, "datamart stk_mins 本地仓"


def fetch_period_bars(code, period, as_of, limit=None):
    """单周期只读取数（1m 走 datamart 原始桶，其余走 chanlun_data）。"""
    if period == "1m":
        return _fetch_1m(code, as_of)
    bars, source = _fetch_kline(code, period, limit or PERIOD_LIMITS.get(period, 500))
    return bars, source


def _default_fetcher(code, period, as_of):
    return fetch_period_bars(code, period, as_of)


# ---------------------------------------------------------------------- #
# context 构造器
# ---------------------------------------------------------------------- #
def build_li122_context(
    symbol, as_of=None, agent_cls=None, fetcher=None, calendar_days=None
):
    """→ (context, meta)。构造 Li122Agent 自足模式 context（四级只读）。

    - as_of：None → resolve_as_of()（前一交易日收盘，强约束）；
      "YYYYMMDD"/"YYYY-MM-DD" → 自动补收盘时刻并过日历校验；
      完整 "YYYY-MM-DD HH:MM" → 逐字使用（调用方自担口径）。
    - 取数周期按 ruleset 的 upper/mid/low_period 决定（档 A 30m/5m/1m、
      档 B 1d/30m/5m），不白拉 1m；未知 ruleset 键回落默认档（与
      Li122Agent 回落语义一致）。
    - 全部周期按 dt 字典序截断到 as_of（复用 li122_agent._cut_bars），
      保证全序列截至前交易日收盘，杜绝当日盘中未完成桶泄漏。
    - meta：逐周期根数/来源/耗时 + 1m 字节数（预算告警），供干跑与
      采集可观测性；context 本体保持契约最小形态。
    """
    ag = agent_cls if agent_cls is not None else _li122_agent().Li122Agent
    if as_of is None:
        as_of = resolve_as_of(None, calendar_days)
    elif len(str(as_of)) == 8 and str(as_of).isdigit():
        as_of = resolve_as_of(str(as_of), calendar_days)
    elif len(str(as_of)) == 10 and str(as_of)[4] == "-":
        as_of = resolve_as_of(str(as_of).replace("-", ""), calendar_days)
    cut_bars = _li122_agent()._cut_bars
    rule_key = ag.ruleset_key if ag.ruleset_key in _rulesets() else DEFAULT_RULESET
    rule = _rulesets()[rule_key]
    periods = []
    for p in (rule["upper_period"], rule["mid_period"], rule["low_period"]):
        if p not in periods:
            periods.append(p)
    fn = fetcher or _default_fetcher
    bars, meta_periods, t_all = {}, {}, time.time()
    for p in periods:
        t0 = time.time()
        raw, source = fn(symbol, p, as_of)
        n_raw = len(raw or [])
        raw = list(raw or [])
        if p == "1m":
            # 1m 尾部切片 ≥3 交易日（预算纪律：模块 docstring「1m 预算」
            # 段；在构造器层统一执行，注入式 fetcher 同样受控）
            dates = sorted({b["dt"][:10] for b in raw})
            if len(dates) > ONE_MIN_TAIL_TRADE_DAYS:
                keep = set(dates[-ONE_MIN_TAIL_TRADE_DAYS:])
                raw = [b for b in raw if b["dt"][:10] in keep]
        cut = cut_bars(raw, as_of)
        bars[p] = cut
        entry = {
            "n": len(cut),
            "n_raw": n_raw,
            "source": source,
            "elapsed_s": round(time.time() - t0, 3),
        }
        if p == "1m":
            entry["bytes"] = len(json.dumps(cut, ensure_ascii=False).encode("utf-8"))
            entry["over_budget"] = entry["bytes"] > ONE_MIN_BUDGET_BYTES
        meta_periods[p] = entry
    context = {"symbol": symbol, "as_of": as_of, "bars": bars, "trace_id": TRACE_ID}
    meta = {
        "symbol": symbol,
        "as_of": as_of,
        "ruleset": rule_key,
        "periods": periods,
        "bars_meta": meta_periods,
        "elapsed_s": round(time.time() - t_all, 3),
    }
    return context, meta


# ---------------------------------------------------------------------- #
# Signal 垫片：Verdict → shadow_collect 可落档信号
# ---------------------------------------------------------------------- #
def verdict_to_signal(verdict, trade_date):
    """Verdict → Signal（仅 buy 产事件帧；hold/拦截/fail-closed → hold）。

    - buy → action="buy"，score=confidence*100（0.7→70.0，便于影子
      统计四分桶），confidence 原值透传；
    - 非 buy（li122 语义下即 hold/拦截/fail-closed 中性）→
      action="hold"、score=0.0——collect_once 事件帧过滤（EVENT_ACTIONS
      只认 buy/sell）自动 skip 并计数，不入统计样本仅保留可观测性。
    """
    Signal = _signal_cls()
    rec = str(getattr(verdict, "recommendation", "") or "hold")
    conf = float(getattr(verdict, "confidence", 0.0) or 0.0)
    action = "buy" if rec == "buy" else "hold"
    score = round(conf * 100.0, 2) if action == "buy" else 0.0
    rationale = (
        str(getattr(verdict, "rationale", "") or "").strip()
        or "li122 影子采集：%s（垫片缺省依据）" % rec
    )
    return Signal(
        symbol=str(verdict.symbol),
        action=action,
        score=score,
        confidence=round(conf, 4),
        rationale=rationale,
        strategy=str(verdict.strategy_id),
        trade_date=_ymd8(trade_date),
    )


# ---------------------------------------------------------------------- #
# 每日采集适配器（duck-type ClassicStrategyAdapter：strategy_id + score）
# ---------------------------------------------------------------------- #
class Li122ShadowAdapter:
    """Li122Agent 自足模式 Verdict → 每日 Signal 适配器（影子采集专用）。

    降级语义同 chan_monitor/classic_adapter：任何内部失败打印日志返回
    []，绝不 raise 到调用方（collect_once 依赖该约定）。逐 symbol 的
    context/bars 元数据记入 ``self.last_meta``（干跑与可观测性）。
    """

    def __init__(self, symbols, agent_cls=None, fetcher=None, calendar_days=None):
        ag = agent_cls if agent_cls is not None else _li122_agent().Li122Agent
        if not symbols:
            raise ValueError("symbols 不能为空")
        self.agent_cls = ag
        self.strategy_id = ag.strategy_id
        self._symbols = [str(s).strip() for s in symbols if str(s).strip()]
        if not self._symbols:
            raise ValueError("symbols 全为空串")
        self._fetcher = fetcher
        self._calendar_days = calendar_days
        self.last_meta = []

    def score(self, trade_date):
        try:
            return self._score(trade_date)
        except Exception as exc:
            print(
                "[%s] 降级：影子采集打分失败，返回空信号列表: %s"
                % (self.strategy_id, exc)
            )
            return []

    def _score(self, trade_date):
        td8 = _ymd8(trade_date)
        agent = self.agent_cls()
        signals, self.last_meta = [], []
        for symbol in self._symbols:
            context, meta = build_li122_context(
                symbol,
                as_of=td8,
                agent_cls=self.agent_cls,
                fetcher=self._fetcher,
                calendar_days=self._calendar_days,
            )
            verdict = agent.propose(context)
            sig = verdict_to_signal(verdict, td8)
            meta["verdict"] = {
                "recommendation": verdict.recommendation,
                "confidence": verdict.confidence,
            }
            meta["signal"] = {"action": sig.action, "score": sig.score}
            self.last_meta.append(meta)
            signals.append(sig)
        return signals


# ---------------------------------------------------------------------- #
# 每日采集入口
# ---------------------------------------------------------------------- #
def collect_li122_shadow(
    strategy="chanlun_li122",
    symbols=(),
    trade_date=None,
    *,
    agent_cls=None,
    fetcher=None,
    calendar_days=None,
    out_path=None,
):
    """单日影子采集：自足模式 propose → 垫片 → collect_once 幂等落档。

    trade_date=T（YYYYMMDD）：影子行归属日/前向收益基期日；as_of 自动
    取 T 收盘全序列（T 相对 T+1 盘前执行而言即「前一交易日」，T+1 盘前
    出 T 日建议）。返回 collect_once 结果 dict（recorded/replayed/
    skipped_non_event/path）+ contexts 逐标的元数据。
    """
    from strategy_layer.onboarding import shadow_collect as sc

    adapter = Li122ShadowAdapter(
        symbols, agent_cls=agent_cls, fetcher=fetcher, calendar_days=calendar_days
    )
    if strategy and strategy != adapter.strategy_id:
        raise ValueError(
            "strategy=%s 与 agent strategy_id=%s 不一致"
            % (strategy, adapter.strategy_id)
        )
    if trade_date is None:
        raise ValueError("trade_date 必填（影子行归属日 YYYYMMDD）")
    res = sc.collect_once(adapter, trade_date, out_path=out_path)
    res["contexts"] = adapter.last_meta
    return res


# ---------------------------------------------------------------------- #
# CLI（手动补采/干跑）
# ---------------------------------------------------------------------- #
def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/strategy_layer/onboarding/agents/li122_shadow.py", exc)
    import argparse

    ap = argparse.ArgumentParser(description="li122 每日影子采集（干跑/补采）")
    ap.add_argument("--symbol", required=True, help="标的，如 600519.SH")
    ap.add_argument("--date", required=True, help="采集交易日 YYYYMMDD")
    ap.add_argument("--out", default=None, help="影子档路径覆写（干跑用）")
    a = ap.parse_args(argv)
    res = collect_li122_shadow(symbols=[a.symbol], trade_date=a.date, out_path=a.out)
    print(
        json.dumps(
            {k: v for k, v in res.items() if k != "contexts"}, ensure_ascii=False
        )
    )
    for m in res.get("contexts", []):
        print(json.dumps(m, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
