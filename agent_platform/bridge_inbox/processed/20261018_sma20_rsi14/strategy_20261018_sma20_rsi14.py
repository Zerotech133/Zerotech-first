# -*- coding: utf-8 -*-
"""Codex 试点策略：SMA20 趋势过滤 + RSI14 超买超卖双向门（军团-M2）。

逻辑（一句话）：价格相对 SMA20 的偏离方向定顺势侧，RSI14 超买/超卖做
反向确认——顺势且未极端→顺势建议；顺势但 RSI 极端→反向建议；带内→hold。

阈值来源：
- SMA 带 ±0.5%：shadow_trend ±1% 的收紧试点（小带提高触发频率便于
  首跑观察对账，非参数调优结论）；
- RSI14 70/30：Wilder 经典阈值；RSI 用 15 日收盘序列（14 个差分）；
- 样本门槛 21 日（SMA20 窗口 + RSI 首个差分），不足降级 hold。

提交：2026-10-18 老田拍板试点（submit_id=20261018_sma20_rsi14），
Codex 侧零凭据、纯标准库（statistics 白名单内）。
"""
from statistics import fmean
from strategy_registry import StrategyAgent, Verdict

SMA_WIN = 20            # SMA 窗口（日）
RSI_WIN = 14            # RSI 差分窗口
TREND_BAND = 0.005      # SMA 偏离带 ±0.5%
RSI_OVERBOUGHT = 70.0   # 超买
RSI_OVERSOLD = 30.0     # 超卖
MIN_BARS = SMA_WIN + 1  # 样本门槛 21


def _rsi(closes, n=RSI_WIN):
    """Wilder 简化 RSI：n 段差分均值比。closes 长度须 >= n+1。"""
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    ag = sum(gains[-n:]) / n
    al = sum(losses[-n:]) / n
    if al <= 1e-12:
        return 100.0 if ag > 1e-12 else 50.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


class CodexSma20Rsi14Agent(StrategyAgent):
    """SMA20 趋势过滤 + RSI14 反转确认（见模块 docstring 阈值来源）。"""
    strategy_id = "codex_sma20_rsi14"
    stance = "neutral"

    def propose(self, context):
        closes = [float(x) for x in (context.get("closes") or [])
                  if x is not None]
        symbol = context.get("symbol", "")
        if len(closes) < MIN_BARS:
            return Verdict(strategy_id=self.strategy_id, symbol=symbol,
                           recommendation="hold", confidence=0.0,
                           stance="neutral", score=0.0,
                           rationale="样本不足（<%d 日）" % MIN_BARS)
        price = closes[-1]
        sma = fmean(closes[-SMA_WIN:])
        dev = (price - sma) / sma if sma > 1e-12 else 0.0
        rsi = _rsi(closes[-(RSI_WIN + 1):])

        if dev > TREND_BAND:
            if rsi >= RSI_OVERBOUGHT:   # 顺势但超买 → 反向确认卖出
                rec, conf, st = "sell", 0.55, "bear"
                why = "price 高于 SMA20 %.2f%% 且 RSI=%.1f 超买" % (dev * 100, rsi)
            else:                        # 顺势未极端 → 顺趋势买入
                rec, conf, st = "buy", min(0.5 + dev, 0.9), "bull"
                why = "price 高于 SMA20 %.2f%%，RSI=%.1f 未极端" % (dev * 100, rsi)
        elif dev < -TREND_BAND:
            if rsi <= RSI_OVERSOLD:      # 逆势但超卖 → 反转买入
                rec, conf, st = "buy", 0.55, "bull"
                why = "price 低于 SMA20 %.2f%% 且 RSI=%.1f 超卖" % (dev * 100, rsi)
            else:                        # 逆势未极端 → 顺趋势卖出
                rec, conf, st = "sell", min(0.5 - dev, 0.9), "bear"
                why = "price 低于 SMA20 %.2f%%，RSI=%.1f 未极端" % (dev * 100, rsi)
        else:
            rec, conf, st = "hold", 0.4, "neutral"
            why = "SMA 带内（±%.1f%%），RSI=%.1f" % (TREND_BAND * 100, rsi)
        return Verdict(strategy_id=self.strategy_id, symbol=symbol,
                       recommendation=rec, confidence=round(conf, 4),
                       stance=st, score=round(rsi / 100, 4), rationale=why)
