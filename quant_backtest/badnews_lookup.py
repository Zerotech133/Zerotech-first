# -*- coding: utf-8 -*-
"""利空明细查询（K35-b §2① 命令块）：龙虎榜上榜史 + 席位动向 + 公告文本。

数据源：ts_top_list（龙虎榜每日，金额元）、ts_top_inst（席位明细，元）、
ts_daily（上榜后走势对照）、公告文本走 notice_lookup（stock_notice + ts_anns_d）。
公告结论由数据本身给出；只有两条通道都不可用/为空时才降级到人工核查指引。

用法:
  python badnews_lookup.py 301216            # 默认近 365 自然日
  python badnews_lookup.py 301216.SZ --days 180
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tdx_read as tdxr
import notice_lookup as nl


from codes import normalize_code as _norm  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="利空明细查询（龙虎榜+席位）")
    ap.add_argument("code")
    ap.add_argument("--days", type=int, default=365, help="回看自然日窗口，默认 365")
    ap.add_argument("--notice-limit", type=int, default=15, help="公告展示条数，默认 15")
    a = ap.parse_args()
    code = _norm(a.code)
    start = (datetime.now() - timedelta(days=a.days)).strftime("%Y-%m-%d")

    # 日线收盘（上榜后走势对照）
    px = tdxr.read_range(
        "ts_daily", start=start, columns=["ts_code", "trade_date", "close"]
    )
    px = px[px["ts_code"] == code].sort_values("trade_date")
    last_px = float(px["close"].iloc[-1]) if len(px) else 0.0

    # --- A. 龙虎榜上榜史 ---
    tl = tdxr.read_range("ts_top_list", start=start)
    tl = tl[tl["ts_code"] == code].sort_values("trade_date")
    print("=== A. 龙虎榜上榜史（ts_top_list，近 %d 自然日）===" % a.days)
    if not len(tl):
        print("  无上榜记录（无极端异动日，非典型游资票）")
    else:
        for _, r in tl.iterrows():
            d = str(r["trade_date"]).split(" ")[0]
            after = ""
            seg = px[px["trade_date"] > d]
            if len(seg) and last_px and float(r["close"]):
                chg = (last_px / float(r["close"]) - 1) * 100
                after = "  榜后至今 %+.1f%%（%.2f→%.2f）" % (
                    chg,
                    float(r["close"]),
                    last_px,
                )
            print(
                "  %s  %s  收 %.2f  涨跌 %+.2f%%  榜上净买 %+.0f 万  "
                "原因：%s%s"
                % (
                    d,
                    r.get("name", ""),
                    float(r["close"]),
                    float(r["pct_change"]),
                    float(r["net_amount"]) / 1e4,
                    r["reason"],
                    after,
                )
            )

    # --- B. 席位明细 ---
    ti = tdxr.read_range("ts_top_inst", start=start)
    ti = ti[ti["ts_code"] == code].sort_values("trade_date")
    print("\n=== B. 席位明细（ts_top_inst，元） ===")
    if not len(ti):
        print("  无席位记录")
    else:
        for d in ti["trade_date"].unique():
            sub = ti[ti["trade_date"] == d].copy()
            sub["net_buy"] = sub["net_buy"].astype(float)
            sub = sub.sort_values("net_buy", ascending=False)
            print("  --- %s ---" % d)
            n_org = sub["exalter"].astype(str).str.contains("机构专用").sum()
            for _, r in sub.iterrows():
                tag = "买方" if str(r["side"]) in ("1", "1.0") else "卖方"
                print(
                    "    [%s] %-40s 买 %.0f 万 / 卖 %.0f 万 → 净 %+.0f 万"
                    % (
                        tag,
                        str(r["exalter"])[:40],
                        float(r["buy"]) / 1e4,
                        float(r["sell"]) / 1e4,
                        float(r["net_buy"]) / 1e4,
                    )
                )
            if n_org:
                net_org = float(
                    sub[sub["exalter"].astype(str).str.contains("机构专用")][
                        "net_buy"
                    ].sum()
                )
                print("    机构专用 %d 席，合计净 %+.0f 万" % (n_org, net_org / 1e4))

    # --- C. 公告文本（本地库双通道；结论由数据给出，不写死） ---
    notice = nl.collect(code, days=a.days)
    for line in nl.format_section(notice, view_limit=a.notice_limit):
        print(line)
    if not notice["hits"] and notice["degraded"]:
        print("  - 巨潮资讯 http://www.cninfo.com.cn 搜「%s」：减持/质押/问询函 人工复核" % code)

    # --- D. §2① 判读 ---
    print("\n=== D. §2① 判读 ===")
    risk = [h for h in notice["hits"] if nl.has_risk(h)]
    if len(tl):
        print(
            "  龙虎榜有记录：结合 A 段「榜后至今」走势读——上榜后大幅回撤"
            "多为游资一日游/情绪顶，属资金行为利空而非基本面利空；"
            "机构席位净卖方向才指向基本面分歧。"
        )
        if risk:
            print("  另有 %d 条带风险标签的公告，需与资金行为分开定性。" % len(risk))
    elif risk:
        print(
            "  龙虎榜无记录，但公告侧有 %d 条风险标签（%s）：按文本利空定性，"
            "核对 C 段原文后再判。"
            % (
                len(risk),
                "/".join(sorted({t for h in risk for t in h["tags"] if t in nl.RISK_TAGS})),
            )
        )
    elif notice["degraded"]:
        print("  龙虎榜无记录 + 公告通道不可用：① 暂无机查证据，保持「待人工」状态。")
    else:
        print("  龙虎榜无记录 + 公告侧无风险标签：① 暂无机查证据，保持「观察」状态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
