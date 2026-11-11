# -*- coding: utf-8 -*-
"""P69 自检：证明面板无前视偏差 + 分组排名的口径正确。

三条断言：
  A1 面板里任一 (日, 票) 的报告期，其公告日必须 <= 该日（否则就是偷看未来）
  A2 报告期映射单调：同一只票，日期越晚，报告期不会倒退
  A3 分组排名确实消除了「累计口径」的水平差：同一日内不同报告期组
     的原始 ROE 中位数差异显著，但分组分位后各自都是 0~1 均分
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"C:/项目/老田的AI助手/quant_backtest")
import _p69_fin_ic as M

CACHE = M.CACHE


def main():
    fin = M.load_fin()
    ann = (fin.assign(d=fin["ann_date"].dt.strftime("%Y%m%d").astype(int))
              .set_index(["code", "report_period"])["d"])

    # 取一段测试日 + 一部分票，避免全量太慢
    rng = np.random.default_rng(11)
    codes = sorted(fin["full"].unique())
    pick = list(rng.choice(codes, size=300, replace=False))
    dates = [int(x) for x in pd.read_parquet(
        os.path.join(M.DATA, sorted(os.listdir(M.DATA))[0]), columns=["trade_date"]
    )["trade_date"]]
    dates = [d for d in dates if M.START <= d <= M.END]

    pan = M.build_panel(fin, dates, pick)
    per = pan.pop("period")

    # ---- A1 无前视 ----
    bad = 0
    checked = 0
    st = per.stack()
    st = st[st.notna()]
    for (d, code), rp in st.items():
        key = (code, int(rp))
        if key not in ann.index:
            continue
        checked += 1
        if ann.loc[key] > int(d):
            bad += 1
            if bad <= 5:
                print("  违规: %d 用了 %s(公告 %d)" % (int(d), rp, ann.loc[key]))
    print("A1 检查 %d 格，前视违规 %d 格 -> %s" % (
        checked, bad, "PASS" if bad == 0 else "FAIL"))

    # ---- A2 报告期不倒退 ----
    arr = per[[c for c in per.columns]]
    diffs = arr.diff()
    back = int((diffs < 0).sum().sum())
    print("A2 报告期倒退 %d 处 -> %s" % (back, "PASS" if back == 0 else "FAIL"))

    # ---- A3 分组排名消除水平差 ----
    x = M.winsorize(pan["roe"])
    rows = [d for d in dates if per.loc[d].notna().sum() > 200]
    if not rows:
        print("A3 无足够样本，跳过")
        return
    d = rows[len(rows) // 2]
    raw = x.loc[d]
    pp = per.loc[d]
    m = raw.notna() & pp.notna()
    raw, pp = raw[m], pp[m]
    med_raw = raw.groupby(pp).median()
    cnt = pp.value_counts()
    big = cnt[cnt >= 30].index
    rk = M.xs_rank(x.loc[[d]], per.loc[[d]]).droplevel(0)
    med_rank = rk[m][pp.isin(big)].groupby(pp).median()
    print("A3 当日 %d，混有 %d 个报告期（其中 %d 个 >=30 只）" % (
        d, pp.nunique(), len(big)))
    cmp = pd.DataFrame({"原始ROE中位": med_raw.round(2), "组内分位中位": med_rank.round(3),
                        "只数": cnt.sort_index()})
    print(cmp.to_string())
    spread_raw = float(med_raw[big].max() - med_raw[big].min())
    spread_rk = float(med_rank.max() - med_rank.min())
    print("   原始中位极差 %.2f  ->  分组分位极差 %.3f" % (spread_raw, spread_rk))
    print("   %s" % ("PASS（水平差已被消除）" if spread_rk < 0.15 else "FAIL（仍有系统差）"))


if __name__ == "__main__":
    main()
