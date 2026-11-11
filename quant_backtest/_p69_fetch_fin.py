# -*- coding: utf-8 -*-
"""P69 第一步：抓取全市场季度业绩报表（含公告日），落本地缓存。

为什么不用 stock_financial_abstract：
  它只有报告期没有公告日 —— 直接按报告期对齐价格等于「9 月 30 日就知道
  10 月才公布的财报」，是典型前视偏差，IC 会虚高到不可用。

本接口 stock_yjbb_em(date=报告期) 按季度返回全市场截面，自带「最新公告日期」，
可做到零前视：只用 avail_date <= T 的数据。

缓存：reports/_p69_fin_cache/PERIOD.parquet，已存在则跳过（可重复运行）。
"""
import os

import akshare as ak
import pandas as pd

CACHE = r"C:/项目/老田的AI助手/quant_backtest/reports/_p69_fin_cache"

# 16 期：覆盖回测窗口 20240102~20261105。
# 起点必须早于窗口：20231231 年报要 2024-04 才公告，若从它开始抓，
# 2024 年 1~4 月将没有任何「已公告」财报可用（面板全空，静默丢 52 天）。
PERIODS = [
    "20221231", "20230331", "20230630", "20230930",
    "20231231", "20240331", "20240630", "20240930",
    "20241231", "20250331", "20250630", "20250930",
    "20251231", "20260331", "20260630", "20260930",
]

COLS = {
    "股票代码": "code",
    "股票简称": "name",
    "每股收益": "eps",
    "营业总收入-营业总收入": "revenue",
    "营业总收入-同比增长": "rev_yoy",
    "净利润-净利润": "np",
    "净利润-同比增长": "np_yoy",
    "每股净资产": "bps",
    "净资产收益率": "roe",
    "每股经营现金流量": "cfps",
    "销售毛利率": "gross_margin",
    "所处行业": "industry",
    "最新公告日期": "ann_date",
}


def main():
    os.makedirs(CACHE, exist_ok=True)
    for rp in PERIODS:
        fp = os.path.join(CACHE, "%s.parquet" % rp)
        if os.path.exists(fp):
            n = len(pd.read_parquet(fp))
            print("  skip %s (%d rows)" % (rp, n), flush=True)
            continue
        df = ak.stock_yjbb_em(date=rp)
        df = df.rename(columns=COLS)
        keep = [c for c in COLS.values() if c in df.columns]
        df = df[keep].copy()
        # 同一 (代码, 报告期) 可能有多行（更正公告 / 多次披露）→ 保留最新公告日那条
        df["ann_date"] = pd.to_datetime(df["ann_date"], errors="coerce")
        df["report_period"] = int(rp)
        df = (df.sort_values("ann_date")
                .drop_duplicates(subset=["code"], keep="last"))
        df["code"] = df["code"].astype(str).str.zfill(6)
        df.to_parquet(fp, index=False)
        d = df["ann_date"]
        print("  got  %s  n=%d  ann %s ~ %s" % (
            rp, len(df), d.min().date(), d.max().date()), flush=True)

    files = sorted(os.listdir(CACHE))
    print("\ncache files: %d" % len([f for f in files if f.endswith(".parquet")]))


if __name__ == "__main__":
    main()
