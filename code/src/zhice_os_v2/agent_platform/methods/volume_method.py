"""量能方法插件：量价背离 / 放量滞涨 / 缩量阴跌（numpy 热路径）。"""
from __future__ import annotations

from zhice_os_v2.agent_platform.methods.base import (
    MethodVote,
    WatchContext,
    WatchMethod,
)


class VolumeMethod(WatchMethod):
    """量能方法：价格新高处量能是否确认。"""

    method_id = "volume"
    display_name = "量价背离"
    required_features = ["vol_ratio", "ret_pct"]
    price_window = 20          # 价格新高窗口
    vol_ratio_window = 20      # 量比均量窗口
    vol_ratio_threshold = 2.0  # 放量：量比 > 2
    stagnation_pct = 0.003     # 滞涨：|涨跌幅| < 0.3%
    shrink_threshold = 0.6     # 缩量：量 < 0.6×均量
    decline_pct = -0.001       # 阴跌：涨跌幅 < -0.1%

    def evaluate(self, ctx: WatchContext) -> MethodVote:
        df = ctx.bars_df
        need = max(self.price_window, self.vol_ratio_window) + 1
        if df is None or len(df) < need:
            return MethodVote(self.method_id, "WAIT", 0.0,
                              [f"数据不足(len={0 if df is None else len(df)}<{need})"])

        closes = ctx.ensure_array("close")
        vols = ctx.ensure_array("volume")
        close_last = float(closes[-1])
        prev_close = float(closes[-2])
        vol_last = float(vols[-1])

        close_win = closes[-self.price_window:]
        vol_win = vols[-self.price_window:]
        vol_ma_win = vols[-(self.vol_ratio_window + 1):-1]
        price_prev_high = float(close_win[:-1].max())
        vol_prev_high = float(vol_win[:-1].max())
        vol_ma = float(vol_ma_win.mean())
        ret_pct = (close_last / prev_close - 1.0) if prev_close else 0.0
        vol_ratio = (vol_last / vol_ma) if vol_ma > 0 else 0.0

        # ① 量价背离：价格创新高（20 根窗口）但量能未创新高 → 卖出预警
        if close_last > price_prev_high and vol_last < vol_prev_high:
            return MethodVote(
                self.method_id, "SELL", 0.6,
                reasons=[
                    f"量价背离: 价格{close_last:.6g}创{self.price_window}根新高"
                    f"(前高{price_prev_high:.6g})，量能{vol_last:.6g}未新高"
                    f"(前高{vol_prev_high:.6g})",
                ],
                params_snapshot=self.params(),
            )

        # ② 放量滞涨：量比>2 且 |涨跌幅|<0.3% → 异动观望
        if vol_ratio > self.vol_ratio_threshold and abs(ret_pct) < self.stagnation_pct:
            return MethodVote(
                self.method_id, "WAIT", 0.5,
                reasons=[
                    f"放量滞涨: 量比{vol_ratio:.2f}>{self.vol_ratio_threshold} "
                    f"且涨跌幅{ret_pct:+.3%}<±{self.stagnation_pct:.1%}",
                ],
                params_snapshot=self.params(),
            )

        # ③ 缩量阴跌：量 < 0.6×均量 且 收阴 → 继续持有（跌不动）
        if vol_last < self.shrink_threshold * vol_ma and ret_pct < self.decline_pct:
            return MethodVote(
                self.method_id, "HOLD", 0.5,
                reasons=[
                    f"缩量阴跌: 量比{vol_ratio:.2f}<{self.shrink_threshold} "
                    f"且跌{ret_pct:.3%}，抛压衰竭继续持有",
                ],
                params_snapshot=self.params(),
            )

        # ④ 中性：趋势未破但无异常量能
        direction = "上涨" if ret_pct > 0 else ("下跌" if ret_pct < 0 else "平盘")
        return MethodVote(
            self.method_id, "HOLD" if ret_pct >= 0 else "WAIT", 0.3,
            reasons=[f"量能中性: {direction}{ret_pct:+.3%}，量比{vol_ratio:.2f}正常区间"],
            params_snapshot=self.params(),
        )

    def params(self) -> dict:
        return {
            **super().params(),
            "price_window": self.price_window,
            "vol_ratio_window": self.vol_ratio_window,
            "vol_ratio_threshold": self.vol_ratio_threshold,
            "stagnation_pct": self.stagnation_pct,
            "shrink_threshold": self.shrink_threshold,
        }
