# -*- coding: utf-8 -*-
"""可转债溢价率 & 大宗交易折价率探针（只读）

验证两件事：
  1. 可转债：用 ts_cb_basic.conv_price 能否算出合理的转股溢价率
     （已知坑：conv_price 是快照不是时序，下修/分红会让历史溢价率失真）
  2. 大宗交易：折价率分布 + "机构专用" vs 营业部席位的差异
     （ts_block_trade.buyer/seller 是营业部全名，这是龙虎榜之外第二个席位源）
"""
import os
import duckdb

BASE = r"C:/项目/老田的AI助手/quant_backtest/data/tdx"


def main():
    con = duckdb.connect()

    print("=" * 68)
    print("一、可转债转股溢价率（当日横截面，conv_price 用当前快照）")
    print("=" * 68)
    q = f"""
    WITH cb AS (
        SELECT ts_code, stk_code, conv_price, remain_size, delist_date
        FROM read_parquet('{BASE}/ts_cb_basic/**/*.parquet')
        WHERE conv_price > 0 AND (delist_date IS NULL OR delist_date >= '20261101')
    ), px AS (
        SELECT ts_code, close AS cb_close, trade_date
        FROM read_parquet('{BASE}/ts_cb_daily/**/*.parquet')
        WHERE trade_date = '20261104'
    ), st AS (
        SELECT ts_code, close AS stk_close
        FROM read_parquet('{BASE}/ts_daily/**/*.parquet')
        WHERE trade_date = '20261104'
    )
    SELECT cb.ts_code, cb.stk_code, cb.conv_price, px.cb_close, st.stk_close,
           ROUND(100.0 / cb.conv_price * st.stk_close, 3) AS conv_value,
           ROUND(px.cb_close / (100.0 / cb.conv_price * st.stk_close) - 1, 4) AS premium
    FROM cb JOIN px ON cb.ts_code = px.ts_code
            JOIN st ON cb.stk_code = st.ts_code
    WHERE st.stk_close > 0 AND px.cb_close > 0
    """
    df = con.execute(q).df()
    if df.empty:
        print("无匹配（可能正股代码格式不一致）")
    else:
        n = len(df)
        p = df["premium"]
        print(f"可匹配转债 {n} 只")
        print(f"溢价率  中位 {p.median()*100:+.2f}%   "
              f"p10 {p.quantile(.1)*100:+.2f}%   p90 {p.quantile(.9)*100:+.2f}%")
        print(f"负溢价（套利机会）{int((p<0).sum())} 只  "
              f"极值 min {p.min()*100:+.2f}% / max {p.max()*100:+.2f}%")
        print("\n样例（溢价率最低 5 只，即最接近套利）:")
        for _, r in df.nsmallest(5, "premium").iterrows():
            print(f"  {r['ts_code']:12s} 正股{r['stk_code']:12s} 转股价{r['conv_price']:7.3f} "
                  f"转债{r['cb_close']:7.2f} 正股{r['stk_close']:7.2f} 溢价{r['premium']*100:+6.2f}%")
        conv_ok = (df["conv_value"] > 5).mean()
        print(f"\n转股价值 > 5 元的占比 {conv_ok*100:.1f}%（过低说明转股价快照失真）")

    print()
    print("=" * 68)
    print("二、大宗交易折价率（2026 全年）")
    print("=" * 68)
    q2 = f"""
    WITH bt AS (
        SELECT ts_code, trade_date, price, vol, amount, buyer, seller
        FROM read_parquet('{BASE}/ts_block_trade/**/*.parquet')
        WHERE trade_date >= '20260101'
    ), dl AS (
        SELECT ts_code, trade_date, close
        FROM read_parquet('{BASE}/ts_daily/**/*.parquet')
        WHERE trade_date >= '20260101'
    )
    SELECT bt.trade_date, bt.ts_code, bt.amount, bt.buyer, bt.seller,
           bt.price, dl.close,
           bt.price / dl.close - 1 AS disc
    FROM bt LEFT JOIN dl USING (ts_code, trade_date)
    WHERE dl.close > 0
    """
    b = con.execute(q2).df()
    if b.empty:
        print("无数据")
        return
    d = b["disc"].dropna()
    print(f"样本 {len(b):,} 笔（有收盘价可算折价 {len(d):,} 笔）")
    print(f"折价率 中位 {d.median()*100:+.2f}%  p10 {d.quantile(.1)*100:+.2f}%  "
          f"p90 {d.quantile(.9)*100:+.2f}%")
    print(f"溢价成交（>0）{int((d>0).sum()):,} 笔  平价 {int((d.abs()<0.001).sum()):,} 笔  "
          f"折价 {int((d<0).sum()):,} 笔")
    deep = (d < -0.09).sum()
    print(f"深度折价（<-9%，通常意味着急售）{deep:,} 笔 占比 {deep/len(d)*100:.1f}%")

    b["is_inst_buy"] = b["buyer"].fillna("").str.contains("机构专用")
    b["is_inst_sell"] = b["seller"].fillna("").str.contains("机构专用")
    g = b.dropna(subset=["disc"]).groupby(["is_inst_buy", "is_inst_sell"])["disc"]
    print("\n按买卖方是否机构专用分组（折价率中位）:")
    for (ib, isl), s in g:
        tag = f"买方{'机构' if ib else '营业部'}-卖方{'机构' if isl else '营业部'}"
        print(f"  {tag}  中位 {s.median()*100:+6.2f}%  n={len(s):>6,}")

    print("\n折价最深的 5 笔:")
    for _, r in b.dropna(subset=["disc"]).nsmallest(5, "disc").iterrows():
        print(f"  {r['trade_date']} {r['ts_code']:10s} 折价{r['disc']*100:+7.2f}% "
              f"金额{r['amount']/1e4:8.1f}万  买方[{str(r['buyer'])[:20]}] 卖方[{str(r['seller'])[:20]}]")

    p = r"C:/项目/老田的AI助手/quant_backtest/reports/block_trade_discount_2026.csv"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    b.to_csv(p, index=False, encoding="utf-8-sig")
    print("\nCSV ->", p)


if __name__ == "__main__":
    main()
