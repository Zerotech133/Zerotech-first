"""双周期趋势回踩方法。

以日线（或 higher timeframe）判定趋势，以当前 bars 判定回踩与企稳。
该插件只消费 ``WatchContext``，60m 数据通过可选属性读取，保持旧版
WatchContext 构造兼容。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from zhice_os_v2.agent_platform.methods.base import MethodVote, WatchContext, WatchMethod
from zhice_os_v2.agent_platform.methods.chan_method import _dif_dea_np


class DualTfPullbackMethod(WatchMethod):
    """日线趋势 + 60m/当前周期回踩共振。

    评分项：市场门/风险、趋势、回踩位置、MACD、量价企稳。趋势未确认时
    不会因为短周期反弹而出票；风险否决始终优先于评分。
    """

    method_id = "dual_tf_pullback"
    display_name = "双周期趋势回踩"
    required_features = ["close", "volume"]

    daily_ma_period = 20
    daily_long_ma_period = 60
    higher_ma_period = 20
    pullback_window = 20
    max_distance = 0.06
    volume_contraction = 0.85
    min_score = 65.0

    def evaluate(self, ctx: WatchContext) -> MethodVote:
        df = ctx.bars_df
        if df is None or len(df) < 35 or not {"close", "volume"}.issubset(df.columns):
            n = 0 if df is None else len(df)
            return self._vote("WAIT", 0.0, [f"当前周期数据不足(len={n}<35)"])

        # 市场门与风险否决必须在评分前执行，避免风险环境误报 HOLD。
        market = str(getattr(ctx, "market", "CN") or "CN").upper()
        if market not in {"CN", "A", "ASHARE", "SZ", "SH"}:
            return self._vote("WAIT", 0.0, [f"市场门未开放: market={market}"])
        if bool(getattr(ctx, "regime_risk_off", False)):
            return self._vote("WAIT", 0.0, ["风险否决: 市场处于risk-off，禁止趋势回踩开仓"])

        closes = ctx.ensure_array("close")
        volumes = ctx.ensure_array("volume")
        if not np.isfinite(closes[-1]) or closes[-1] <= 0:
            return self._vote("WAIT", 0.0, ["最新收盘价无效"])

        trend, trend_reason = self._trend(ctx)
        if trend is None:
            return self._vote("WAIT", 0.25, [trend_reason])
        if not trend:
            return self._vote("WAIT", 0.35, [trend_reason, "趋势门未通过，不追踪逆势回踩"])

        score = 35.0
        reasons = [trend_reason]

        # 可选 higher timeframe（通常 60m）；缺失时明确降级而不阻断旧调用方。
        higher = self._higher_frame(ctx)
        if higher is None:
            score += 8.0
            reasons.append("60m数据不可用，采用日线+当前周期降级")
        elif self._higher_trend(higher):
            score += 15.0
            reasons.append("60m趋势向上（收盘位于EMA20上方）")
        else:
            return self._vote("WAIT", 0.35, [trend_reason, "60m趋势未确认，回踩信号否决"])

        last = float(closes[-1])
        ema = pd.Series(closes).ewm(span=20, adjust=False).mean().to_numpy()
        dist = abs(last / float(ema[-1]) - 1.0) if ema[-1] else 1.0
        recent_high = float(np.nanmax(closes[-self.pullback_window - 1:-1]))
        retraced = last < recent_high * 0.985 and dist <= self.max_distance
        bounced = len(closes) >= 2 and closes[-1] >= closes[-2]
        if retraced and bounced:
            score += 25.0
            reasons.append(f"回踩EMA20后企稳: 距离{dist:.2%}，现价未破前高")
        elif dist <= self.max_distance:
            score += 12.0
            reasons.append(f"价格贴近EMA20: 距离{dist:.2%}，等待企稳确认")
        else:
            reasons.append(f"未处于有效回踩区: 距离EMA20={dist:.2%}")

        dif, dea = _dif_dea_np(closes)
        macd_ok = bool(np.isfinite(dif[-1]) and np.isfinite(dea[-1]) and dif[-1] >= dea[-1])
        if macd_ok:
            score += 15.0
            reasons.append(f"MACD修复: DIF={dif[-1]:.4g}≥DEA={dea[-1]:.4g}")
        else:
            reasons.append(f"MACD偏弱: DIF={dif[-1]:.4g}<DEA={dea[-1]:.4g}")

        vol_ma = float(np.nanmean(volumes[-11:-1])) if len(volumes) > 10 else 0.0
        vol_ratio = float(volumes[-1] / vol_ma) if vol_ma > 0 else 0.0
        prior_ratio = (float(np.nanmean(volumes[-4:-1])) / vol_ma) if vol_ma > 0 else 1.0
        if vol_ratio >= 1.0 and prior_ratio <= self.volume_contraction:
            score += 10.0
            reasons.append(f"量价确认: 回踩缩量后放量，量比{vol_ratio:.2f}")
        elif vol_ratio <= self.volume_contraction:
            score += 4.0
            reasons.append(f"回踩缩量，量比{vol_ratio:.2f}，尚待放量")
        else:
            reasons.append(f"量价未确认，量比{vol_ratio:.2f}")

        score = min(100.0, score)
        if not retraced and dist > self.max_distance:
            return self._vote("WAIT", min(0.55, score / 100.0), reasons + ["风险否决: 未回踩至趋势支撑区"])
        if score >= self.min_score and retraced and bounced and macd_ok:
            return self._vote("HOLD", score / 100.0, reasons + [f"共振评分{score:.0f}/{100}，满足回踩开仓门槛"], action="BUY_CANDIDATE", score=score, state="READY")
        return self._vote("WAIT", score / 100.0, reasons + [f"共振评分{score:.0f}/{100}，未达{self.min_score:.0f}分出票门槛"], action="WAIT", score=score, state="WATCH")

    def _trend(self, ctx: WatchContext) -> tuple[bool | None, str]:
        daily = getattr(ctx, "daily_df", None)
        if daily is None or "close" not in daily.columns:
            return None, "日线数据不可用"
        closes = daily["close"].astype(float).dropna()
        minimum = max(self.daily_ma_period, 35)
        if len(closes) < minimum:
            return None, f"日线有效收盘不足({len(closes)}<{minimum})"
        ma20 = closes.rolling(self.daily_ma_period).mean()
        dif, dea = _dif_dea_np(closes.to_numpy(dtype=float))
        if not (np.isfinite(dif[-1]) and np.isfinite(dea[-1])):
            return None, "日线MACD未就绪"
        macd_ok = bool(dif[-1] > dea[-1] and dif[-1] > 0)
        ma60_ready = len(closes) >= self.daily_long_ma_period
        ma60_ok = True
        ma60_text = "MA60不可用（降级）"
        if ma60_ready:
            ma60 = closes.rolling(self.daily_long_ma_period).mean()
            ma60_ok = bool(closes.iloc[-1] >= ma60.iloc[-1] and ma60.iloc[-1] >= ma60.iloc[-2])
            ma60_text = f"收盘{closes.iloc[-1]:.4g}/MA60={ma60.iloc[-1]:.4g}"
        ma20_ok = bool(closes.iloc[-1] >= ma20.iloc[-1] and ma20.iloc[-1] >= ma20.iloc[-2])
        ok = ma20_ok and ma60_ok and macd_ok
        return ok, (f"日线趋势{'向上' if ok else '未向上'}: {ma60_text}; "
                    f"MA20={ma20.iloc[-1]:.4g}; MACD DIF={dif[-1]:.4g}, DEA={dea[-1]:.4g}")

    @staticmethod
    def _higher_frame(ctx: WatchContext):
        for name in ("higher_df", "bars_60m_df", "df_60m", "bars60m_df"):
            value = getattr(ctx, name, None)
            if value is not None and len(value):
                return value
        return None

    def _higher_trend(self, df) -> bool:
        if "close" not in df.columns or len(df) < self.higher_ma_period:
            return False
        close = df["close"].astype(float)
        ema = close.ewm(span=self.higher_ma_period, adjust=False).mean()
        return bool(close.iloc[-1] >= ema.iloc[-1] and ema.iloc[-1] >= ema.iloc[-2])

    def _vote(self, direction: str, confidence: float, reasons: list[str], **state) -> MethodVote:
        params = self.params()
        params.setdefault("action", "BUY_CANDIDATE" if direction == "HOLD" else "WAIT")
        params.setdefault("state", "READY" if direction == "HOLD" else "WATCH")
        params.update(state)
        return MethodVote(self.method_id, direction, confidence, reasons, params_snapshot=params)

    def params(self) -> dict:
        return {**super().params(), "daily_ma_period": self.daily_ma_period,
                "daily_long_ma_period": self.daily_long_ma_period,
                "higher_ma_period": self.higher_ma_period,
                "pullback_window": self.pullback_window, "max_distance": self.max_distance,
                "min_score": self.min_score}
