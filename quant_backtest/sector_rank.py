# -*- coding: utf-8 -*-
"""板块内分位排名（K35-b §2④ 命令块）：同 tushare 行业个股区间涨幅分位。

口径： peers = ts_stock_basic.industry 与目标同行业的 A 股；
r(N) = ts_daily 最近收盘 / N+1 根前收盘 - 1（数据不足该票置 None）；
分位 = 目标在有效 peers 中按 r 降序的名次百分比（0=最强，100=最弱）。

板块线口径声明：联动页板块线是 TDX 880xxx 指数，本排名的 peers 用
tushare 行业口径（两者经名称映射同源，个别边缘票可能有出入）。

用法:
  python sector_rank.py 301216                # 默认 r20
  python sector_rank.py 301216.SZ --days 5
  python sector_rank.py 301216 --days 60 --top 8
"""

import argparse
import os
import sys
from datetime import datetime, timedelta


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tdx_read as tdxr


from codes import normalize_code as _norm  # noqa: E402  P1-2 统一代码归一（北交所 8/4/92 开头 → .BJ）


def main():
    ap = argparse.ArgumentParser(description="板块内分位排名")
    ap.add_argument("code")
    ap.add_argument("--days", type=int, default=20, help="区间交易日数，默认 20（r20）")
    ap.add_argument("--top", type=int, default=5, help="展示前 N 名")
    a = ap.parse_args()
    code = _norm(a.code)
    days = a.days

    sb = tdxr.read_all("ts_stock_basic")
    if "industry" not in sb.columns or not len(sb):
        print("ts_stock_basic 不可用")
        return 1
    ind = (
        str(sb.loc[sb["ts_code"] == code, "industry"].iloc[0])
        if (sb["ts_code"] == code).any()
        else ""
    )
    name = (
        str(sb.loc[sb["ts_code"] == code, "name"].iloc[0])
        if (sb["ts_code"] == code).any()
        else code
    )
    if not ind:
        print("%s 不在 ts_stock_basic" % code)
        return 1
    peers = sorted(sb.loc[sb["industry"] == ind, "ts_code"].tolist())
    if code not in peers:
        peers.append(code)

    start = (datetime.now() - timedelta(days=int(days * 2.2) + 20)).strftime("%Y-%m-%d")
    df = tdxr.read_range(
        "ts_daily", start=start, columns=["ts_code", "trade_date", "close"]
    )
    df = df.dropna(subset=["close"])
    df["d"] = df["trade_date"].astype(str).str.slice(0, 10)
    df = df[df["ts_code"].isin(peers)]
    last_day = df["d"].max()
    base_day = df[df["d"] < last_day]["d"].unique()
    base_day = sorted(base_day)[-days] if len(base_day) >= days else None
    if base_day is None:
        print("历史不足 %d 根（最新 %s）" % (days, last_day))
        return 1

    last = df[df["d"] == last_day].set_index("ts_code")["close"]
    base = df[df["d"] == base_day].set_index("ts_code")["close"]
    r = ((last / base - 1) * 100).dropna().sort_values(ascending=False)
    data_day = last_day.replace("-", "")

    print(
        "行业「%s」 r%d 分位排名（数据日 %s，基准日 %s，有效 %d/%d 票）"
        % (ind, days, data_day, base_day.replace("-", ""), len(r), len(peers))
    )
    med = float(r.median())
    if code in r.index:
        rank = int(r.index.get_loc(code)) + 1
        pct = round((rank - 1) / max(len(r) - 1, 1) * 100, 1)
        print(
            "目标 %s %s：r%d=%+.2f%%  名次 %d/%d  分位 %.1f（0=最强 "
            "100=最弱）  行业中位 %+.2f%%  相对中位 %+.2fpp"
            % (
                code,
                name,
                days,
                float(r[code]),
                rank,
                len(r),
                pct,
                med,
                float(r[code]) - med,
            )
        )
    else:
        print(
            "目标 %s %s：区间数据缺失（停牌/新股？）行业中位 %+.2f%%"
            % (code, name, med)
        )
    print("\n前 %d 名：" % a.top)
    nm = dict(zip(sb["ts_code"], sb["name"]))
    for i, (c, v) in enumerate(r.head(a.top).items(), 1):
        tag = " ←目标" if c == code else ""
        print("  %2d %-10s %-8s %+.2f%%%s" % (i, c, nm.get(c, ""), v, tag))
    if code in r.index and int(r.index.get_loc(code)) + 1 > a.top:
        rank = int(r.index.get_loc(code)) + 1
        print("  …")
        lo = max(0, rank - 2)
        for i, (c, v) in enumerate(r.iloc[lo : rank + 1].items(), lo + 1):
            tag = " ←目标" if c == code else ""
            print("  %2d %-10s %-8s %+.2f%%%s" % (i, c, nm.get(c, ""), v, tag))
    print("\n后 3 名：")
    for i, (c, v) in enumerate(r.tail(3).items(), len(r) - 2):
        tag = " ←目标" if c == code else ""
        print("  %2d %-10s %-8s %+.2f%%%s" % (i, c, nm.get(c, ""), v, tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
