# -*- coding: utf-8 -*-
"""双刀移植自测（K-STOCK-018 补全）：信号模块八重过滤合成验证 + 入场确认模块
纯合成数据，不触碰任何影子链路/真实任务单。运行：venv python（需 numpy/pandas）
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))
from quant_double_knife import PROFILE_CONFIG, quant_double_knife  # noqa: E402
from quant_double_knife_entry import confirm_double_knife_entry  # noqa: E402

PASS = 0


def ok(name, cond=True):
    global PASS
    assert cond, name
    PASS += 1
    print("[PASS] %s" % name)


def make_daily(n=100, start=40.0, drift=0.005):
    """前段缓升 + 末两日小十字星：MA 金叉/MACD 多头/低幅上涨全部预置"""
    closes = [start]
    for i in range(n - 3):
        closes.append(closes[-1] * (1 + drift))
    # 末两日：涨 0.5% 的十字星
    closes.append(closes[-1] * 1.005)
    closes.append(closes[-1] * 1.005)
    closes = np.array(closes)
    opens = np.empty_like(closes)
    opens[0] = closes[0] * 0.995
    opens[1:] = closes[:-1]
    # 末两日改小实体十字星：open 略低于 close，影线对称 0.5%
    for i in (n - 2, n - 1):
        opens[i] = closes[i] / 1.001
    highs = np.maximum(opens, closes) * 1.005
    lows = np.minimum(opens, closes) * 0.995
    amount = np.full(n, 5e5)  # 每日 5e5 千元, 20 日和 1e7 >> 3e6
    idx = pd.date_range("2026-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "amount": amount},
        index=idx,
    )


# ---- 信号模块 ----
df = make_daily()
out = quant_double_knife(df, profile="strict")

ok("输出 21 诊断列", out.shape == (len(df), 21))
ok(
    "末两日为十字星",
    bool(out["cross_star"].iloc[-2]) and bool(out["cross_star"].iloc[-1]),
)
ok("末两日构成双刀(cross_star_2d)", bool(out["cross_star_2d"].iloc[-1]))
ok("合成序列触发信号", int(out["double_knife_signal"].sum()) >= 1)
ok(
    "信号日全部八重条件成立",
    bool(
        out.loc[
            out["double_knife_signal"],
            [
                "range_ok",
                "price_ok",
                "amount_ok",
                "ma_stack_ok",
                "ma_distance_ok",
                "macd_ok",
                "regime_ok",
            ],
        ]
        .all()
        .all()
    ),
)
ok("profile 列回写", (out["double_knife_profile"] == "strict").all())

# trend_down 一票否决：把末两日 amount 反向砸出负面资金平衡难做, 直接验证 blocked_regimes 逻辑
regime_blocked = ~out["regime_label"].isin(PROFILE_CONFIG["strict"]["blocked_regimes"])
ok(
    "blocked_regimes 集合正确(trend_down)",
    PROFILE_CONFIG["strict"]["blocked_regimes"] == {"trend_down"},
)
ok("regime_ok 与 label 一致", bool((out["regime_ok"] == regime_blocked).all()))

# relaxed 口径差异
out_r = quant_double_knife(df, profile="relaxed")
ok(
    "relaxed 阈值更宽",
    PROFILE_CONFIG["relaxed"]["amount_20d_threshold"]
    < PROFILE_CONFIG["strict"]["amount_20d_threshold"],
)
ok("双 profile 独立运行", out_r.shape == out.shape)

# 异常路径
try:
    quant_double_knife(df.drop(columns=["amount"]))
    ok("缺列 raise", False)
except ValueError as e:
    ok("缺列 raise: %s" % e)
try:
    quant_double_knife(df, profile="nope")
    ok("非法 profile raise", False)
except ValueError:
    ok("非法 profile raise")

# ---- 入场确认模块 ----
m = 60
px = np.full(m, 50.0) * (1 + 0.0002 * np.arange(m))  # 微升
hi = px * 1.0005
lo = px * 0.9995
op = np.roll(px, 1)
op[0] = px[0] * 0.999
# 末根放量突破前高
hi[-2] = px[-2] * 1.001
px[-1] = px[-1] * 1.01
hi[-1] = px[-1] * 1.001
vol = np.full(m, 1000.0)
vol[-1] = 2000.0
midx = pd.date_range("2026-01-01 09:30", periods=m, freq="5min")
minute_df = pd.DataFrame(
    {"open": op, "high": hi, "low": lo, "close": px, "volume": vol}, index=midx
)

res = confirm_double_knife_entry(minute_df)
ok(
    "返回 dict 含 9 键",
    set(res)
    == {
        "entry_confirmed",
        "confirm_interval",
        "passes",
        "min_passes",
        "breakout_strength",
        "volume_ratio",
        "vwap_distance",
        "open_relative_strength",
        "note",
    },
)
ok("放量突破场景 confirmed", res["entry_confirmed"] is True)
ok("passes >= min_passes", res["passes"] >= res["min_passes"])
ok("note=confirmed", res["note"] == "confirmed")
ok("interval 标注 5m", res["confirm_interval"] == "5m")

# 低量不确认路径
vol2 = vol.copy()
vol2[-1] = 1.0  # 量比趋零
minute_df2 = minute_df.copy()
minute_df2["volume"] = vol2
res2 = confirm_double_knife_entry(minute_df2)
ok("低量量比 < 阈值", res2["volume_ratio"] < 0.9)
ok("其余三条件仍过, passes=3", res2["passes"] == 3)
# 已知语义(移植原样): note 仅在未确认时列 blockers, confirmed=True 时丢失部分未过条件信息
# -> 改进点挂 T-008 接线时处理, 移植保持零改动
ok(
    "confirmed 时 note=confirmed(移植原样语义)",
    res2["note"] == "confirmed" and res2["entry_confirmed"] is True,
)
ok("低量量比 < 阈值", res2["volume_ratio"] < 0.9)

try:
    confirm_double_knife_entry(minute_df.drop(columns=["volume"]))
    ok("入场缺列 raise", False)
except ValueError:
    ok("入场缺列 raise")
try:
    confirm_double_knife_entry(minute_df.iloc[:5])
    ok("bar 不足 raise", False)
except ValueError:
    ok("bar 不足 raise")

print("\n%d/%d 断言全 PASS" % (PASS, PASS))
