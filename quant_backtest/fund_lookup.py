# -*- coding: utf-8 -*-
"""资金流向与融资查询（K35-b §2② 命令块）：主力资金 + 融资余额。

数据源：ts_moneyflow（金额单位万元）、ts_margin_detail（单位元）。
口径：主力净流入 = 大单+特大单买入额 - 卖出额（lg+elg，万元）；
     散户(小单)净流入 = buy_sm_amount - sell_sm_amount，与主力互为镜像；
     融资余额 rzye、融资买入 rzmre、融资偿还 rzche（元）。

用法:
  python fund_lookup.py 301216              # 默认近 30 交易日
  python fund_lookup.py 301216.SZ --days 60
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
    ap = argparse.ArgumentParser(description="资金流向+融资查询")
    ap.add_argument("code")
    ap.add_argument("--days", type=int, default=30, help="统计交易日窗口，默认 30")
    ap.add_argument("--tail", type=int, default=10, help="逐日明细展示尾部 N 日")
    a = ap.parse_args()
    code = _norm(a.code)
    days = a.days
    start = (datetime.now() - timedelta(days=int(days * 2.2) + 30)).strftime("%Y-%m-%d")

    # --- A. 主力资金（ts_moneyflow，万元） ---
    mf = tdxr.read_range("ts_moneyflow", start=start)
    mf = mf[mf["ts_code"] == code].sort_values("trade_date").tail(days)
    print("=== A. 主力资金（ts_moneyflow，万元）近 %d 交易日 ===" % len(mf))
    if not len(mf):
        print("  无数据")
    else:
        mf = mf.copy()
        mf["主力"] = (
            mf["buy_lg_amount"]
            + mf["buy_elg_amount"]
            - mf["sell_lg_amount"]
            - mf["sell_elg_amount"]
        ).round(1)
        mf["散户"] = (mf["buy_sm_amount"] - mf["sell_sm_amount"]).round(1)
        print("  区间 %s ~ %s" % (mf["trade_date"].iloc[0], mf["trade_date"].iloc[-1]))
        tot_main = float(mf["主力"].sum())
        tot_sm = float(mf["散户"].sum())
        tot_net = float(mf["net_mf_amount"].sum())
        n_out = int((mf["主力"] < 0).sum())
        # 最长连续净流出
        run = mx = 0
        for v in mf["主力"]:
            run = run + 1 if v < 0 else 0
            mx = max(mx, run)
        print(
            "  主力净流入合计 %+.0f 万元（日均 %+.0f，净流出 %d/%d 日，"
            "最长连续 %d 日）" % (tot_main, tot_main / len(mf), n_out, len(mf), mx)
        )
        print(
            "  散户(小单)合计 %+.0f 万元  全单净流入合计 %+.0f 万元" % (tot_sm, tot_net)
        )
        show = mf[["trade_date", "主力", "散户", "net_mf_amount"]].tail(a.tail)
        show.columns = ["日期", "主力", "散户", "全单净"]
        print(show.to_string(index=False))

    # --- B. 融资融券（ts_margin_detail，元） ---
    mg = tdxr.read_range("ts_margin_detail", start=start)
    mg = mg[mg["ts_code"] == code].sort_values("trade_date").tail(days)
    print("\n=== B. 融资融券（ts_margin_detail）近 %d 交易日 ===" % len(mg))
    if not len(mg):
        print("  无数据（非两融标的？）")
    else:
        r0, r1 = float(mg["rzye"].iloc[0]), float(mg["rzye"].iloc[-1])
        d = r1 - r0
        pct = (d / r0 * 100) if r0 else 0.0
        net_rz = float((mg["rzmre"] - mg["rzche"]).sum())
        n_repay = int((mg["rzmre"] - mg["rzche"] < 0).sum())
        print(
            "  融资余额 %s %.2f 亿 → %s %.2f 亿（%+.0f 万元，%+.2f%%）"
            % (
                mg["trade_date"].iloc[0],
                r0 / 1e8,
                mg["trade_date"].iloc[-1],
                r1 / 1e8,
                d / 1e4,
                pct,
            )
        )
        print(
            "  区间净融资（买入-偿还）合计 %+.0f 万元，净偿还 %d/%d 日"
            % (net_rz / 1e4, n_repay, len(mg))
        )
        show = mg[["trade_date", "rzye", "rzmre", "rzche"]].tail(a.tail).copy()
        for c in ("rzye", "rzmre", "rzche"):
            show[c] = (show[c] / 1e4).round(0)
        show.columns = ["日期", "融资余额(万)", "融资买入(万)", "融资偿还(万)"]
        print(show.to_string(index=False))

    # --- C. 判读 ---
    print("\n=== C. §2② 判读（资金撤离假设） ===")
    if len(mf):
        avg = tot_main / len(mf)
        share_out = round(n_out / len(mf) * 100)
        if avg <= -300:
            m_j = "主力系统性撤离（日均 %+.0f 万），② 成立" % avg
        elif abs(avg) < 300:
            m_j = (
                "主力量级中性（日均 %+.0f 万；净流出日占 %d%% 但绝对量"
                "小，不足 %d 万/日门槛）" % (avg, share_out, 300)
            )
        else:
            m_j = "主力净流入，② 不成立"
        print("  主力资金：" + m_j)
        avg_net = tot_net / len(mf)
        n_j = (
            ("显著净流出（日均 %+.0f 万），总口径资金在跑" % avg_net)
            if avg_net <= -500
            else ("基本平衡（日均 %+.0f 万）" % avg_net)
        )
        print("  全单净流入：区间 %+.0f 万元 → %s" % (tot_net, n_j))
    if len(mg):
        cond_r = pct < -5
        print(
            "  融资余额：区间 %+.2f%% → %s"
            % (
                pct,
                "杠杆资金退潮（佐证撤离）"
                if cond_r
                else "杠杆资金未退潮（若价跌+融资增=杠杆逆势接盘，注意踩踏风险）",
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
