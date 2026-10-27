# -*- coding: utf-8 -*-
"""W5c 诊断：修复后 replay 命中格点上复合分变化 vs 阈值 vs 闸门。"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np
import qpzm_backtest as anchor  # noqa: E402

wide = anchor.load_wide(os.path.join(HERE, "data", "ashare"))
env_df = anchor.env_gate(wide["above20"], wide["ret20"])
mix_cur, _na = anchor.cross_rps(
    {p: wide["chg%d" % p] for p in anchor.PERIODS}, anchor.RPS_W_CUR
)
tech_w = anchor.tech_score(wide["longs"], wide["above20"])
eligible = wide["eligible"]
jev_mat, meta = anchor.load_jev_replay()

print("jev 覆盖日:", list(jev_mat.index))
print(
    "窗口内覆盖日存在性:",
    {d: (d in set(wide["dates"].tolist())) for d in jev_mat.index},
)
for d in jev_mat.index:
    if d in env_df.index:
        row = env_df.loc[d]
        print(
            "env %d: safe=%s breadth=%s med20=%s"
            % (d, row["safe"], round(row["breadth"], 4), round(row["med20"], 4))
        )

sc_miss, buy_miss, _ = anchor.combine_score(
    tech_w,
    mix_cur,
    env_df,
    buy_th=75.0,
    obs_th=50.0,
    gate_on=True,
    eligible=eligible,
    jev_mode="missing",
)
sc_rep, buy_rep, _ = anchor.combine_score(
    tech_w,
    mix_cur,
    env_df,
    buy_th=75.0,
    obs_th=50.0,
    gate_on=True,
    eligible=eligible,
    jev_mode="replay",
    jev_mat=jev_mat,
)

jm = jev_mat.reindex(index=tech_w.index, columns=tech_w.columns)
hit = ~np.isnan(jm.to_numpy(dtype=float))
el = (eligible & tech_w.notna()).to_numpy(dtype=bool)
hr = el & ~np.isnan(mix_cur.to_numpy(dtype=float))
d_sc = sc_rep.to_numpy(dtype=float) - sc_miss.to_numpy(dtype=float)
n_hit_elig = int((hit & el & hr).sum())
print("\n命中格点: %d, 其中可打分(el&hr): %d" % (int(hit.sum()), n_hit_elig))
print("复合分变化格点数:", int((np.abs(d_sc) > 1e-9).sum()))
# gate_on 下信号差异
bd = int((buy_rep.to_numpy(dtype=bool) ^ buy_miss.to_numpy(dtype=bool)).sum())
print("gate_on 买入信号差异格点:", bd)
# 不设闸门时是否有差异（诊断闸门是否为唯一抑制因素）
_sc2, buy2_rep, _ = anchor.combine_score(
    tech_w,
    mix_cur,
    env_df,
    buy_th=75.0,
    obs_th=50.0,
    gate_on=False,
    eligible=eligible,
    jev_mode="replay",
    jev_mat=jev_mat,
)
_sc3, buy3_miss, _ = anchor.combine_score(
    tech_w,
    mix_cur,
    env_df,
    buy_th=75.0,
    obs_th=50.0,
    gate_on=False,
    eligible=eligible,
    jev_mode="missing",
)
bd_off = int((buy2_rep.to_numpy(dtype=bool) ^ buy3_miss.to_numpy(dtype=bool)).sum())
print("gate_off 买入信号差异格点:", bd_off)
# 命中格点上 miss/replay 复合分与阈值距离
idx, cols = np.where(hit & el & hr)
shown = 0
for i, j in zip(idx, cols):
    if shown < 30:
        print(
            "  %d %-10s miss=%.2f rep=%.2f d=%+.2f buy_m=%s buy_r=%s"
            % (
                tech_w.index[i],
                tech_w.columns[j],
                sc_miss.iat[i, j],
                sc_rep.iat[i, j],
                d_sc[i, j],
                bool(buy_miss.iat[i, j]),
                bool(buy_rep.iat[i, j]),
            )
        )
        shown += 1
