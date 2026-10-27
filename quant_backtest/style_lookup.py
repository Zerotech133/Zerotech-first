# -*- coding: utf-8 -*-
"""风格错位查询（K35-b §2⑤ 命令块）：市值档 × 三线对照 × 板块权重拉动度。

三问：
  A. 目标是什么市值档（ts_daily_basic.circ_mv，万元→亿）；
  B. 板块指数 rN vs 全A等权 880008 vs 上证 000001.SH——板块相对全市场
     强弱 + 市场大小盘风格（全A等权-上证 差值，正=小盘占优）；
  C. 板块指数 rN vs 板块内个股中位 rN（ts_stock_basic 同行业，同
     sector_rank 口径）——差值=权重拉动度，高则板块指数虚高。

判读 ⑤「小盘票 vs 权重拉板块」：
  权重拉动明显（指数-中位 ≥5pp）且市场偏权重风格（等权<上证）→
  小盘票落后板块指数有风格成分，⑤ 部分成立；
  市场小盘占优或权重拉动弱 → 弱非风格造成，⑤ 不成立。

用法:
  python style_lookup.py 301216              # 默认 r20
  python style_lookup.py 301216.SZ --days 60
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import linkage
import tdx_read as tdxr

EQ_BLOCK = "880008"  # 全Ａ等权
IDX = "000001.SH"  # 上证（加权）


from codes import normalize_code as _norm  # noqa: E402  P1-2 统一代码归一（北交所 8/4/92 开头 → .BJ）


def _r(bars, days):
    if len(bars) < days + 1:
        return None
    # 板块 bar 键 close；load_kline bar 键单字母 c（判例）
    cl = lambda b: float(b.get("close", b.get("c")))  # noqa: E731
    return (cl(bars[-1]) / cl(bars[-days - 1]) - 1) * 100


def main():
    ap = argparse.ArgumentParser(description="风格错位查询")
    ap.add_argument("code")
    ap.add_argument("--days", type=int, default=20, help="rN 窗口，默认 20")
    a = ap.parse_args()
    code = _norm(a.code)
    days = a.days

    # --- A. 市值档 ---
    print("=== A. 目标市值档 ===")
    db = tdxr.read_range(
        "ts_daily_basic",
        start=(datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d"),
        columns=["ts_code", "trade_date", "circ_mv"],
    )
    row = db[db["ts_code"] == code].sort_values("trade_date").tail(1)
    if len(row):
        mv = float(row["circ_mv"].iloc[0]) / 1e4  # 万元 → 亿
        tier = (
            "小盘(<100亿)"
            if mv < 100
            else "中盘(100-500亿)"
            if mv < 500
            else "中大盘(500-1000亿)"
            if mv < 1000
            else "大盘(>1000亿)"
        )
        print("  流通市值 %.1f 亿（%s）→ %s" % (mv, row["trade_date"].iloc[0], tier))
    else:
        mv, tier = None, "未知（ts_daily_basic 无记录）"
        print("  " + tier)

    # --- B. 三线 rN ---
    print("\n=== B. 三线 r%d 对照 ===" % days)
    blk = linkage.resolve_block(code)
    r_blk = r_eq = r_idx = None
    if blk:
        r_blk = _r(linkage.block_bars(blk["code"], "1d", days * 2 + 10), days)
    r_eq = _r(linkage.block_bars(EQ_BLOCK, "1d", days * 2 + 10), days)
    from chanlun_data import load_kline

    ri = load_kline(IDX, "1d", days * 2 + 10)
    r_idx = _r(ri.get("bars") or [], days)
    print(
        "  板块指数 %s %s：%+.2f%%"
        % (blk.get("name"), blk.get("code"), r_blk if r_blk is not None else 0)
    )
    print("  全A等权 %s：%+.2f%%   上证 %s：%+.2f%%" % (EQ_BLOCK, r_eq, IDX, r_idx))
    style = r_eq - r_idx
    print(
        "  板块-全A等权 %+.2fpp（板块相对全市场）；等权-上证 %+.2fpp"
        "（%s）" % (r_blk - r_eq, style, "小盘占优" if style > 0 else "权重占优")
    )

    # --- C. 板块内分化（权重拉动度） ---
    print("\n=== C. 板块权重拉动度 ===")
    sb = tdxr.read_all("ts_stock_basic")
    ind = (
        str(sb.loc[sb["ts_code"] == code, "industry"].iloc[0])
        if (sb["ts_code"] == code).any()
        else ""
    )
    peers = sorted(sb.loc[sb["industry"] == ind, "ts_code"].tolist())
    start = (datetime.now() - timedelta(days=int(days * 2.2) + 20)).strftime("%Y-%m-%d")
    df = tdxr.read_range(
        "ts_daily", start=start, columns=["ts_code", "trade_date", "close"]
    )
    df = df[df["ts_code"].isin(peers)].dropna(subset=["close"])
    df["d"] = df["trade_date"].astype(str).str.slice(0, 10)
    last_day = df["d"].max()
    base_day = sorted(df[df["d"] < last_day]["d"].unique())[-days]
    last = df[df["d"] == last_day].set_index("ts_code")["close"]
    base = df[df["d"] == base_day].set_index("ts_code")["close"]
    r = ((last / base - 1) * 100).dropna()
    med = float(r.median())
    r_self = float(r.get(code, float("nan")))
    pull = r_blk - med
    print(
        "  行业「%s」%d 票：个股中位 %+.2f%%，板块指数 %+.2f%%"
        % (ind, len(r), med, r_blk)
    )
    print(
        "  权重拉动度（指数-中位）%+.2fpp %s"
        % (pull, "（权重拉动明显）" if pull >= 5 else "（指数贴近成分股）")
    )
    print(
        "  目标 %+.2f%% vs 中位 %+.2f%% → 相对中位 %+.2fpp"
        % (r_self, med, r_self - med)
    )

    # --- D. 判读 ---
    print("\n=== D. §2⑤ 判读（风格错位假设） ===")
    if pull >= 5 and style < 0:
        v = (
            "权重拉动明显且市场偏权重风格 → 小盘票落后板块指数有风格"
            "成分，⑤ 部分成立；但相对中位仍 %+.2fpp → 票特异性弱势"
            "为主因" % (r_self - med)
        )
    elif style > 0:
        v = (
            "市场小盘占优（等权-上证 %+.2fpp）而目标（%s）仍弱 → 弱非"
            "市场风格造成，⑤ 不成立" % (style, tier)
        )
    else:
        v = (
            "权重拉动 %+.2fpp 未达 5pp 门槛 → 板块指数基本反映成分股，"
            "⑤ 不成立（弱势主因在票自身）" % pull
        )
    print("  " + v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
