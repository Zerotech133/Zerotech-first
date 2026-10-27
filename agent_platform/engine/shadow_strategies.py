# -*- coding: utf-8 -*-
"""军团 shadows 三策略移植（K-ORG-167 第二批）——首批 StrategyAgent 插件。

总纲 v1.0 shadows/ 三策略（trend/momentum/mean-reversion）的 AP 移植版：
- 全部继承 StrategyAgent，propose(context) 只读不写（红线：策略只产建议，
  execution_allowed 恒 False，本模块零凭据）；
- context 契约：{"symbol": str, "closes": [float,...]（升序时间序列，最后
  一项为最新收盘）}——纯 Python 均线/动量/ z 值计算，零 pandas 依赖
  （影子主链 fail-open 纪律：策略层不得引入重型依赖拖垮主链）；
- 产出经 Verdict.validate() 白名单校验（recommendation 五档对齐影子账本）。
"""

from strategy_registry import StrategyAgent, StrategyRegistry, Verdict

WINDOW = 20  # 统计窗口（日）
TREND_BAND = 0.01  # 趋势带 ±1%（带内=hold，对齐 dk_signal 低价带纪律：带是设计）
MOM_STRONG = 0.08  # 动量强阈值 ±8%
MOM_WEAK = 0.03  # 动量弱阈值 ±3%
MR_Z = 2.0  # 均值回归 z 值阈值 ±2


def _tail(closes, n=WINDOW):
    seq = [float(x) for x in (closes or []) if x is not None]
    return seq[-n:] if len(seq) >= n else None


def _mean(seq):
    return sum(seq) / len(seq)


def _stdev(seq):
    m = _mean(seq)
    var = sum((x - m) ** 2 for x in seq) / len(seq)
    return var**0.5


class TrendFollowAgent(StrategyAgent):
    """趋势跟随：价格相对 SMA20 带外→顺趋势，带内→hold（趋势带是设计）。"""

    strategy_id = "shadow_trend"
    stance = "bull"

    def propose(self, context):
        seq = _tail(context.get("closes"))
        symbol = context.get("symbol", "")
        if seq is None:
            return Verdict(
                strategy_id=self.strategy_id,
                symbol=symbol,
                recommendation="hold",
                confidence=0.0,
                stance="neutral",
                score=0.0,
                rationale="样本不足（<%d 日）" % WINDOW,
            )
        price, sma = seq[-1], _mean(seq)
        dev = (price - sma) / sma
        if dev > TREND_BAND:
            return Verdict(
                strategy_id=self.strategy_id,
                symbol=symbol,
                recommendation="buy",
                confidence=min(0.5 + dev, 0.95),
                stance="bull",
                score=round(dev, 4),
                rationale="price 高于 SMA20 %.2f%%" % (dev * 100),
            )
        if dev < -TREND_BAND:
            return Verdict(
                strategy_id=self.strategy_id,
                symbol=symbol,
                recommendation="sell",
                confidence=min(0.5 - dev, 0.95),
                stance="bear",
                score=round(dev, 4),
                rationale="price 低于 SMA20 %.2f%%" % (dev * 100),
            )
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=symbol,
            recommendation="hold",
            confidence=0.4,
            stance="neutral",
            score=round(dev, 4),
            rationale="趋势带内（±1%%）",
        )


class MomentumAgent(StrategyAgent):
    """动量：20 日区间收益过阈值→顺动量，分强/弱两档。"""

    strategy_id = "shadow_momentum"
    stance = "bull"

    def propose(self, context):
        seq = _tail(context.get("closes"))
        symbol = context.get("symbol", "")
        if seq is None or seq[0] <= 0:
            return Verdict(
                strategy_id=self.strategy_id,
                symbol=symbol,
                recommendation="hold",
                confidence=0.0,
                stance="neutral",
                score=0.0,
                rationale="样本不足或价格非法",
            )
        ret = seq[-1] / seq[0] - 1.0
        if ret >= MOM_STRONG:
            rec, conf, st = "strong_buy", min(0.5 + ret, 0.95), "bull"
        elif ret >= MOM_WEAK:
            rec, conf, st = "buy", 0.5, "bull"
        elif ret <= -MOM_STRONG:
            rec, conf, st = "strong_sell", min(0.5 - ret, 0.95), "bear"
        elif ret <= -MOM_WEAK:
            rec, conf, st = "sell", 0.5, "bear"
        else:
            rec, conf, st = "hold", 0.4, "neutral"
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=symbol,
            recommendation=rec,
            confidence=round(conf, 4),
            stance=st,
            score=round(ret, 4),
            rationale="20 日动量 %+.2f%%" % (ret * 100),
        )


class MeanReversionAgent(StrategyAgent):
    """均值回归：z 值（对 SMA20）超 ±2 → 反向；带内 hold。"""

    strategy_id = "shadow_meanrev"
    stance = "neutral"

    def propose(self, context):
        seq = _tail(context.get("closes"))
        symbol = context.get("symbol", "")
        if seq is None:
            return Verdict(
                strategy_id=self.strategy_id,
                symbol=symbol,
                recommendation="hold",
                confidence=0.0,
                stance="neutral",
                score=0.0,
                rationale="样本不足（<%d 日）" % WINDOW,
            )
        m, sd = _mean(seq), _stdev(seq)
        z = (seq[-1] - m) / sd if sd > 1e-9 else 0.0
        if z > MR_Z:
            return Verdict(
                strategy_id=self.strategy_id,
                symbol=symbol,
                recommendation="sell",
                confidence=min(0.5 + z / 4, 0.95),
                stance="bear",
                score=round(z, 4),
                rationale="z=%.2f 超买回归" % z,
            )
        if z < -MR_Z:
            return Verdict(
                strategy_id=self.strategy_id,
                symbol=symbol,
                recommendation="buy",
                confidence=min(0.5 - z / 4, 0.95),
                stance="bull",
                score=round(z, 4),
                rationale="z=%.2f 超卖回归" % z,
            )
        return Verdict(
            strategy_id=self.strategy_id,
            symbol=symbol,
            recommendation="hold",
            confidence=0.4,
            stance="neutral",
            score=round(z, 4),
            rationale="z 带内（±2）",
        )


def build_default_registry():
    """首批三插件注册表（军团 shadows 移植版）。"""
    reg = StrategyRegistry()
    for cls in (TrendFollowAgent, MomentumAgent, MeanReversionAgent):
        reg.register(cls())
    return reg
