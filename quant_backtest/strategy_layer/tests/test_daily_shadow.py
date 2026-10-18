# -*- coding: utf-8 -*-
"""S4 每日采集编排测试（daily_shadow，合成 adapter 注入）。

覆盖（S4 开闸就绪验收）：
1. 编排主流程：四策略合成 adapter 一次跑齐，summary 汇总
   recorded/replayed/skipped_non_event（事件帧口径：hold 不落档）；
2. fail-visible：全策略零产出 → exit 6；adapter.score 抛错 → error
   上报 + exit 6；单策略零产出 → WARN 不致命（exit 0）；
3. stats 落盘：compute_shadow_stats（fwd_fn 注入）结果进 summary.stats，
   日档不含 samples（瘦身），daily_shadow_summary_<date>.json 存在；
4. 幂等复跑：同 out_dir 二次跑 recorded=0、replayed=前轮 recorded；
5. 交易日解析：T+1 口径取池标的最近 bar 日期；数据不可用/过期分支
   （DailyShadowError no_data/stale）。

纪律：零 LLM、零网络（数据/前向收益全部注入）；零全局 patch；
唯一写副作用限定 pytest tmp_path；测试时间由合成数据决定，不依赖今日。
"""
import json
import os

import pandas as pd
import pytest

from strategy_layer.onboarding import daily_shadow as ds
from strategy_layer.onboarding import shadow_collect as sc
from strategy_layer.signal import Signal

TD = "20261016"          # 合成采集日（固定，不依赖真实日历）
POOL = ["600519.SH"]


# ---------------------------------------------------------------------- #
# 合成 adapter（duck-type ClassicStrategyAdapter）
# ---------------------------------------------------------------------- #
class _ScriptedAdapter:
    """按脚本吐 Signal；hold 用于验证事件帧过滤；raise_on_score 模拟抛错。"""

    def __init__(self, strategy_id, signals_by_date=None,
                 raise_on_score=False):
        self.strategy_id = strategy_id
        self._by_date = signals_by_date or {}
        self._raise = raise_on_score

    def score(self, trade_date):
        if self._raise:
            raise RuntimeError("合成 adapter 打分失败")
        td8 = str(trade_date).replace("-", "")
        return list(self._by_date.get(td8, []))


def _sig(action, td8=TD, score=70.0, symbol=POOL[0]):
    return Signal(symbol=symbol, action=action, score=score,
                  confidence=0.5, rationale="合成信号", strategy="t",
                  trade_date=td8)


def _four(signals_by_date=None, raise_names=()):
    """按 STRATEGIES 顺序造四策略合成 adapter（默认首日 buy 事件）。"""
    out = []
    for name in ds.STRATEGIES:
        by_date = dict(signals_by_date or {TD: [_sig("buy")]})
        out.append(_ScriptedAdapter(name, by_date,
                                    raise_on_score=name in raise_names))
    return out


_FWD = lambda symbol, td: {5: 1.0, 20: 2.0}  # noqa: E731 注入式前向收益


# ---------------------------------------------------------------------- #
# 1. 四策略汇总 + 事件帧过滤
# ---------------------------------------------------------------------- #
def test_run_once_four_strategy_summary(tmp_path):
    # buy/sell 用不同标的：shadow_collect 幂等键 strategy_id|symbol|trade_date
    # 不含 action，同日同标的第二信号按 replayed 收口（既有口径）
    adapters = _four({TD: [_sig("buy"), _sig("hold"),
                           _sig("sell", score=30.0, symbol="000001.SZ")]})
    s = ds.run_once(TD, adapters, out_dir=str(tmp_path), fwd_fn=_FWD)
    assert s["exit_code"] == ds.EXIT_OK
    assert set(s["strategies"]) == set(ds.STRATEGIES)
    for name in ds.STRATEGIES:
        e = s["strategies"][name]
        assert e["recorded"] == 2            # buy+sell 落档
        assert e["skipped_non_event"] == 1   # hold 被事件帧口径收口
        assert e["replayed"] == 0
        assert os.path.exists(e["path"])
        assert e["path"].startswith(str(tmp_path))  # 未落业务目录


# ---------------------------------------------------------------------- #
# 2. fail-visible
# ---------------------------------------------------------------------- #
def test_all_zero_exit6(tmp_path):
    s = ds.run_once(TD, _four({TD: []}), out_dir=str(tmp_path), fwd_fn=_FWD)
    assert s["exit_code"] == ds.EXIT_ZERO
    assert s["zero_strategies"] == list(ds.STRATEGIES)


def test_score_error_fail_visible(tmp_path):
    s = ds.run_once(TD, _four(raise_names=set(ds.STRATEGIES)),
                    out_dir=str(tmp_path), fwd_fn=_FWD)
    assert s["exit_code"] == ds.EXIT_ZERO
    for name in ds.STRATEGIES:
        assert "合成 adapter 打分失败" in s["strategies"][name]["error"]


def test_single_zero_warn_not_fatal(tmp_path):
    by_date = {TD: [_sig("buy")]}
    adapters = _four(by_date)
    adapters[1] = _ScriptedAdapter(ds.STRATEGIES[1], {TD: []})  # 单策略零产出
    s = ds.run_once(TD, adapters, out_dir=str(tmp_path), fwd_fn=_FWD)
    assert s["exit_code"] == ds.EXIT_OK
    assert s["zero_strategies"] == [ds.STRATEGIES[1]]


# ---------------------------------------------------------------------- #
# 3. stats 落盘
# ---------------------------------------------------------------------- #
def test_stats_written_without_samples(tmp_path):
    s = ds.run_once(TD, _four(), out_dir=str(tmp_path), fwd_fn=_FWD)
    for name in ds.STRATEGIES:
        st = s["stats"][name]
        assert st["n_total"] == 1
        assert "gate" in st and "status" in st["gate"]
        assert "samples" not in st           # 日档瘦身
    sum_path = s["summary_path"]
    assert os.path.basename(sum_path) == "daily_shadow_summary_%s.json" % TD
    with open(sum_path, "r", encoding="utf-8") as f:
        reloaded = json.load(f)
    assert reloaded["exit_code"] == ds.EXIT_OK
    assert set(reloaded["strategies"]) == set(ds.STRATEGIES)


def test_stats_nmin_branch_consistent_with_shadow_collect(tmp_path):
    """gate 三态判据沿用 shadow_collect 原函数（n<N_MIN → 样本积累中）。"""
    s = ds.run_once(TD, _four(), out_dir=str(tmp_path), fwd_fn=_FWD)
    rows = sc.load_rows(s["strategies"][ds.STRATEGIES[0]]["path"])
    assert len(rows) == 1
    st = sc.compute_shadow_stats(rows, fwd_fn=_FWD)
    assert st["gate"]["status"] == s["stats"][ds.STRATEGIES[0]]["gate"]["status"]
    assert "样本积累中" in st["gate"]["status"]


# ---------------------------------------------------------------------- #
# 4. 幂等复跑
# ---------------------------------------------------------------------- #
def test_idempotent_rerun(tmp_path):
    adapters = _four()
    first = ds.run_once(TD, adapters, out_dir=str(tmp_path), fwd_fn=_FWD)
    second = ds.run_once(TD, adapters, out_dir=str(tmp_path), fwd_fn=_FWD)
    for name in ds.STRATEGIES:
        assert first["strategies"][name]["recorded"] == 1
        assert second["strategies"][name]["recorded"] == 0
        assert second["strategies"][name]["replayed"] == 1
    assert second["exit_code"] == ds.EXIT_OK  # replayed 不算零产出


# ---------------------------------------------------------------------- #
# 5. 交易日解析（T+1；数据驱动，不硬编码）
# ---------------------------------------------------------------------- #
def _fake_loader(dates):
    idx = pd.to_datetime(dates)
    df = pd.DataFrame({"close": [100.0] * len(dates)}, index=idx)

    def loader(market, symbols, start, end):
        assert market == ds.MARKET
        assert list(symbols) == POOL
        out = {s: df for s in symbols}
        if end:
            out = {s: d[d.index <= pd.Timestamp(end)] for s, d in out.items()}
        return out
    return loader


def test_resolve_prev_trading_day_t_plus_1():
    # 周一(2026-10-19)盘前跑 → 上一交易日应为周五 20261016
    td = ds.resolve_prev_trading_day(
        today=pd.Timestamp("2026-10-19").date(),
        pool=POOL,
        loader=_fake_loader(["2026-10-13", "2026-10-14", "2026-10-15",
                             "2026-10-16", "2026-10-19"]))
    assert td == "20261016"   # end=参考日-1，10-19 的 bar 被截掉


def test_resolve_no_data_fail_visible():
    with pytest.raises(ds.DailyShadowError) as ei:
        ds.resolve_prev_trading_day(today=pd.Timestamp("2026-10-18").date(),
                                    pool=POOL, loader=_fake_loader([]))
    assert ei.value.code == "no_data"


def test_resolve_stale_fail_visible():
    # 最近 bar 距参考日-1 超过 STALE_DAYS → stale
    with pytest.raises(ds.DailyShadowError) as ei:
        ds.resolve_prev_trading_day(
            today=pd.Timestamp("2026-11-15").date(),
            pool=POOL, loader=_fake_loader(["2026-10-16"]))
    assert ei.value.code == "stale"


# ---------------------------------------------------------------------- #
# 6. main 参数面（不触网：--date 非法即退，不建 adapter）
# ---------------------------------------------------------------------- #
def test_main_bad_date_exit2():
    assert ds.main(["--date", "2026-1x01"]) == ds.EXIT_ARGS
