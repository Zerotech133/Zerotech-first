# -*- coding: utf-8 -*-
"""P64 自检：combine_score 新增 w4 入参的向后兼容性。

断言三条：
  1) w4=None 与 w4=W4 的 score 逐元素一致（含 NaN 位置）
  2) buy/obs 掩码逐元素一致
  3) 提权后 buy 笔数不应为 0（防止权重写错导致全判空）
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"C:/项目/老田的AI助手/quant_backtest")
import qpzm_backtest as Q  # noqa: E402

rng = np.random.RandomState(7)
D, N = 40, 25
idx = pd.date_range("2026-01-01", periods=D, freq="B")
cols = ["%06d" % i for i in range(N)]

tech = pd.DataFrame(rng.uniform(0, 100, (D, N)), index=idx, columns=cols)
tech = tech.where(rng.rand(D, N) > 0.1)
mix = pd.DataFrame(rng.uniform(0, 100, (D, N)), index=idx, columns=cols)
mix = mix.where(rng.rand(D, N) > 0.05)
env = pd.DataFrame(
    {
        "score": rng.uniform(20, 90, D),
        "breadth": rng.uniform(0.2, 0.8, D),
        "med20": rng.uniform(-0.05, 0.05, D),
        "upfrac": rng.uniform(0.2, 0.8, D),
        "safe": rng.rand(D) > 0.2,
    },
    index=idx,
)

s0, b0, o0 = Q.combine_score(tech, mix, env)
s1, b1, o1 = Q.combine_score(tech, mix, env, w4=Q.W4)
s2, b2, _ = Q.combine_score(
    tech, mix, env, w4={"tech": 0.35, "jev": 0.25, "rps": 0.35, "env": 0.15}
)

ok_sc = bool(np.allclose(s0.to_numpy(), s1.to_numpy(), equal_nan=True))
ok_buy = bool((b0.to_numpy() == b1.to_numpy()).all())
ok_obs = bool((o0.to_numpy() == o1.to_numpy()).all())
print("A1 none vs W4 score allclose :", ok_sc)
print("A2 none vs W4 buy  identical :", ok_buy)
print("A3 none vs W4 obs  identical :", ok_obs)
print("A4 nan count rps25 / rps35   :", int(s0.isna().sum().sum()), "/", int(s2.isna().sum().sum()))
print("A5 buy count rps25 / rps35   :", int(b0.sum().sum()), "/", int(b2.sum().sum()))
print("A6 score mean rps25 / rps35  :", round(float(s0.stack().mean()), 4),
      "/", round(float(s2.stack().mean()), 4))

assert ok_sc and ok_buy and ok_obs, "w4=None 未保持向后兼容"
assert int(b2.sum().sum()) > 0, "提权后无买入信号，权重疑似写错"
print("SELFCHECK_OK")
