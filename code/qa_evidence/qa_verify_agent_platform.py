# -*- coding: utf-8 -*-
"""QA 独立深度验证脚本（严过关 · 2026-10-16）
覆盖：D1 numpy/pandas 数值对拍 | D2 regime 适配器语义等价 | D3 令牌桶护栏
     D4 REGIME_VETO 真实状态+构造翻转 | D6 看跌 fixture 全票 SELL | E1 毕业权重断链实证
"""
from __future__ import annotations

import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, "src")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print(("  PASS " if cond else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))


print("=" * 72)
print("D1. 热路径 numpy 指标 vs pandas ewm(adjust=False) 随机数据对拍（容差 1e-9）")
print("=" * 72)
rng = np.random.default_rng(20261016)
closes = 100.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.01, 500))
s = pd.Series(closes)

from zhice_os_v2.agent_platform.methods.chan_method import ema_np

for span in (12, 26, 9):
    np_ema = ema_np(closes, span)
    pd_ema = s.ewm(span=span, adjust=False).mean().to_numpy()
    m = float(np.max(np.abs(np_ema - pd_ema)))
    check(f"EMA(span={span}) numpy ≡ pandas ewm", m < 1e-9, f"maxdiff={m:.3e}")

# MACD hist 口径：numpy 分支 (dif-dea)*2 vs zhice compute_macd_hist
from zhice_os_v2.agent_platform.methods.chan_method import _dif_dea_np
from zhice_os_v2.domain.strategy.strategies.chan.engine import compute_macd_hist

dif_np, dea_np = _dif_dea_np(closes)
hist_np_last = float((dif_np[-1] - dea_np[-1]) * 2.0)
hist_zhice = compute_macd_hist(s)
check("MACD hist 末值 numpy ≡ zhice (DIF-DEA)×2",
      abs(hist_np_last - float(hist_zhice.iloc[-1])) < 1e-9,
      f"{hist_np_last:.10f} vs {float(hist_zhice.iloc[-1]):.10f}")

# RSI Wilder：numpy 递推 vs pandas ewm(alpha=1/period, adjust=False)
from zhice_os_v2.agent_platform.methods.rsi_spec_method import rsi_wilder_np

delta = np.diff(closes)
gain = np.where(delta > 0, delta, 0.0)
loss = np.where(delta < 0, -delta, 0.0)
g_pd = pd.Series(gain).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
l_pd = pd.Series(loss).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
rsi_ref = np.full(closes.size, np.nan)
for i in range(13, delta.size):
    g, l = g_pd[i], l_pd[i]
    rsi_ref[i + 1] = 100.0 if l == 0 else 100.0 - 100.0 / (1.0 + g / l)
rsi_np = rsi_wilder_np(closes, 14)
mask = ~np.isnan(rsi_ref)
m = float(np.max(np.abs(rsi_np[mask] - rsi_ref[mask])))
check("RSI(14) Wilder numpy ≡ pandas ewm(alpha=1/14)", m < 1e-9, f"maxdiff={m:.3e}")

print()
print("=" * 72)
print("D2. 本地 regime 适配器 vs zhice market_regime_permits_entry 同输入对拍")
print("=" * 72)
import zhice_os_v2.domain.strategy.strategies.chan.market_regime as mr_mod
from zhice_os_v2.agent_platform.backtest.data_loader import make_regime_fn
from zhice_os_v2.domain.strategy.strategies.chan.market_regime import (
    market_regime_permits_entry,
)


def synth_daily(n_sym: int, n_days: int, trend: float) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=n_days, freq="D", tz="UTC")
    rows = []
    for k in range(n_sym):
        base = 100.0 * (1 + 0.001 * k)
        for i, d in enumerate(dates):
            rows.append((f"S{k}", d.date(), base * (1 + trend * i)))
    return pd.DataFrame(rows, columns=["symbol", "trade_date", "close"])


for trend, label in [(-0.004, "下跌趋势(risk_off预期)"), (0.004, "上涨趋势(放行预期)"),
                     (0.0, "零趋势")]:
    daily = synth_daily(5, 300, trend)          # 300 天 ≈ 43 周 > WEEKLY_MIN_BARS=30
    local_fn = make_regime_fn(daily, "CRYPTO")
    # zhice 路径：向其进程缓存注入由同一份数据构造的等权指数（绕 DB，纯逻辑对拍）
    df = daily.copy()
    df["close"] = pd.to_numeric(df["close"])
    df["ret"] = df.sort_values(["symbol", "trade_date"]).groupby("symbol")["close"].pct_change()
    daily_eq = df.groupby("trade_date")["ret"].mean().dropna().reset_index()
    daily_eq["dt"] = pd.to_datetime(daily_eq["trade_date"])
    daily_eq["idx"] = (1 + daily_eq["ret"]).cumprod()
    old_cache = mr_mod._MKT_CACHE["index"]
    mr_mod._MKT_CACHE["index"] = daily_eq[["dt", "idx"]].copy()
    try:
        d_test = daily_eq["trade_date"].iloc[-1]
        zhice_res = market_regime_permits_entry(d_test, "CRYPTO")
    finally:
        mr_mod._MKT_CACHE["index"] = old_cache
    local_res = local_fn(d_test, "CRYPTO")
    check(f"适配器 ≡ zhice（{label}）", local_res == zhice_res,
          f"local={local_res} zhice={zhice_res}")

# 真实 DB 当前状态（D4 前置）
real_permits = market_regime_permits_entry(date(2026, 10, 16), "CRYPTO")
print(f"  [INFO] 真实数据当前 CRYPTO permits_entry(2026-10-16) = {real_permits}"
      f"  → risk_off = {not real_permits}")
real_permits_0916 = market_regime_permits_entry(date(2026, 9, 16), "CRYPTO")
print(f"  [INFO] 回测窗起点 2026-09-16 permits = {real_permits_0916}")

print()
print("=" * 72)
print("D3. 令牌桶 660/h 护栏（超限场景 + 滑窗 refill + DROPPED 链路）")
print("=" * 72)
from zhice_os_v2.agent_platform.arbitration.llm_arbiter import (
    LlmArbiter,
    LlmArbiterConfig,
    MockLlmClient,
    TokenBucket,
)

T = [0.0]
bucket = TokenBucket(660, now_fn=lambda: T[0])
allowed = sum(1 for _ in range(700) if bucket.allow())
check("660 桶首批限流 660/700，超限 40 次 DROPPED 计数",
      allowed == 660 and bucket.dropped == 40,
      f"allowed={allowed} dropped={bucket.dropped}")
check("refill 速率 = 660/3600/s", abs(bucket.rate - 660 / 3600.0) < 1e-12,
      f"rate={bucket.rate:.6f}/s")
T[0] = 3600.0  # 推进 1 小时 → 最多回满 660
check("1h 后令牌恢复", bucket.allow() is True, f"tokens={bucket.tokens:.1f}")

T2 = [0.0]
b2 = TokenBucket(3600, now_fn=lambda: T2[0])   # rate=1/s 便于验证滑动窗
b2.tokens = 0.0
T2[0] = 2.0
ok1 = b2.allow()  # 恢复 2 枚 → 消耗 1
ok2 = b2.allow()  # 消耗 1 → 0
ok3 = b2.allow()  # 无令牌
check("滑动窗精确补给（Δ2s→2枚）", ok1 and ok2 and (not ok3) and b2.dropped == 1)

# DROPPED 链路：真 LlmArbiter + 桶榨干 → 平票进 LLM → DROPPED
from zhice_os_v2.agent_platform.arbitration.regime_arbiter import RegimeArbiter
from zhice_os_v2.agent_platform.methods.base import MethodVote, WatchContext
from zhice_os_v2.agent_platform.tests_support import synth_bars

df_ctx = synth_bars(200, seed=1)
ctx = WatchContext("T/USDT", "CRYPTO", df_ctx.tail(3), df_ctx)
tie_votes = [MethodVote("chan", "SELL", 0.5, []), MethodVote("volume", "HOLD", 0.5, []),
             MethodVote("rsi_spec", "WAIT", 0.2, [])]
llm = LlmArbiter(client=MockLlmClient(), cfg=LlmArbiterConfig())
llm.bucket.tokens = 0.0
d = RegimeArbiter(llm_arbiter=llm).arbitrate(tie_votes, ctx)
check("桶耗尽 → path=DROPPED + 保守 WAIT",
      d.arbitration_path == "DROPPED" and d.action == "WAIT"
      and d.model_verdict["source"] == "FALLBACK_TOKENS")

print()
print("=" * 72)
print("D4. REGIME_VETO：构造 risk_off 翻转票走全链（仲裁器级 + 引擎级）")
print("=" * 72)


def flip_votes():
    return [MethodVote("chan", "SELL", 0.6, ["背驰"]),
            MethodVote("volume", "HOLD", 0.55, ["缩量"]),
            MethodVote("rsi_spec", "HOLD", 0.55, ["中性"])]


d_on = RegimeArbiter().arbitrate(flip_votes(),
                                 WatchContext("T/USDT", "CRYPTO", df_ctx.tail(3), df_ctx,
                                              regime_risk_off=False))
d_off = RegimeArbiter().arbitrate(flip_votes(),
                                  WatchContext("T/USDT", "CRYPTO", df_ctx.tail(3), df_ctx,
                                               regime_risk_off=True))
check("risk_off=true 翻转 HOLD→SELL → REGIME_VETO 触发",
      d_on.action == "HOLD" and d_off.action == "SELL"
      and d_off.arbitration_path == "REGIME_VETO",
      f"on={d_on.action}/{d_on.arbitration_path} off={d_off.action}/{d_off.arbitration_path}")

# 引擎级：注入恒 risk_off regime_fn 跑合成回测，统计 REGIME_VETO 次数
from zhice_os_v2.agent_platform.backtest.engine import BacktestEngine
from zhice_os_v2.agent_platform.config import AgentPlatformConfig
from zhice_os_v2.agent_platform.methods import create_default_registry

cfg = AgentPlatformConfig()
cfg.warmup_bars = 60
cfg.window_bars = 120
eng = BacktestEngine(cfg, registry=create_default_registry())
res_off = eng.run_on_bars({"AAA/USDT": synth_bars(200, seed=11, pattern="chan_div")},
                          regime_fn=lambda d, m: False)   # 恒禁止入场 = 恒 risk_off
veto_n = sum(1 for x in res_off.decisions if x["arbitration_path"] == "REGIME_VETO")
check("引擎级 risk_off 回测出现 REGIME_VETO", veto_n > 0,
      f"REGIME_VETO={veto_n}/{len(res_off.decisions)}")
check("引擎级 regime 标志贯通到决策", all(x["regime_risk_off"] for x in res_off.decisions))

print()
print("=" * 72)
print("D6. 看跌 fixture：三方法全票 SELL → WEIGHTED 路径输出 SELL")
print("=" * 72)
from zhice_os_v2.agent_platform.methods import ChanMethod, RsiSpecMethod, VolumeMethod

votes_bear = []
for method, pat, seed in [(ChanMethod(), "chan_div", 11),
                          (VolumeMethod(), "vol_div", 11),
                          (RsiSpecMethod(), "rsi_dull", 5)]:
    dfx = synth_bars(200, seed=seed, pattern=pat)
    v = method.evaluate(WatchContext("BEAR/USDT", "CRYPTO", dfx.tail(3), dfx))
    votes_bear.append(v)
    print(f"    {v.method_id:9s} → {v.direction} conf={v.confidence}")
check("看跌 fixture 三方法各自投出 SELL",
      all(v.direction == "SELL" for v in votes_bear))
d_bear = RegimeArbiter().arbitrate(votes_bear, ctx)
check("全票 SELL → WEIGHTED 路径最终 SELL（PENDING_CONFIRM）",
      d_bear.action == "SELL" and d_bear.arbitration_path == "WEIGHTED"
      and d_bear.status == "PENDING_CONFIRM",
      f"{d_bear.action}/{d_bear.arbitration_path}")

print()
print("=" * 72)
print("E1. 毕业权重是否贯通仲裁（代码审读疑点：MethodVote 无 weight 字段）")
print("=" * 72)
arb_probe = RegimeArbiter()
w = arb_probe._weights_of([MethodVote("chan", "SELL", 0.7, [])])
check("MethodVote 携带 weight 属性", hasattr(MethodVote("chan", "SELL", 0.7, []), "weight"),
      f"_weights_of={w}（getattr 默认 1.0 恒命中）")
# 实证：chan 权重 3.0 时若贯通应得 SELL，断链则 HOLD
from zhice_os_v2.agent_platform.methods import create_default_registry as _cdr

reg = _cdr()
reg.load_weights({"chan": 3.0})
w_votes = [MethodVote("chan", "SELL", 0.7, ["背驰"]),
           MethodVote("volume", "HOLD", 0.5, ["缩量"]),
           MethodVote("rsi_spec", "HOLD", 0.5, ["中性"])]
d_w = RegimeArbiter().arbitrate(w_votes, ctx)
exp_w = (0.7 * 3.0) / 5.0   # 若贯通：sell=0.42 > hold=0.2 → SELL
check("set_weight(3.0) 影响仲裁结果（期望 SELL）", d_w.action == "SELL",
      f"实际 {d_w.action}（score_sell={d_w.score_sell}）；若贯通应≈{exp_w:.2f}")

print()
print("=" * 72)
print("汇总")
print("=" * 72)
n_pass = sum(1 for _, ok, _ in RESULTS if ok)
n_fail = len(RESULTS) - n_pass
for name, ok, detail in RESULTS:
    if not ok:
        print(f"  ✗ FAIL: {name} [{detail}]")
print(f"TOTAL {len(RESULTS)} | PASS {n_pass} | FAIL {n_fail}")
sys.exit(0 if n_fail == 0 else 1)
