# -*- coding: utf-8 -*-
"""信号 2 扫描器：全市场「个股弱于板块」候选发现（K35-h · weak 产线入口）。

口径（与模板 §1 信号2 一致，显著性阈值 5pp）：
  超额(r20) = 个股 r20% - 所属板块 r20% ≤ --gap（默认 -5pp）

流程：
  1) ts_stock_basic 在市股票（剔 ST/退/B股/北交所）
  2) ts_daily 全市场 r20 + 区间日均成交额（流动性过滤，默认 ≥2 亿对齐九维漏斗）
  3) 按行业分组解析 TDX 板块（industry → resolve_block，组内共享一次解析）
  4) 板块 r20（block_bars 本地日线）→ 超额 → 过滤
  5) 板块内分位（行业内全部有效票，口径同 sector_rank）
  6) 二道筛（重点候选打标，降人工挑案量）：
     强转弱=九维警报池史×现弱（前强补跌型，最优先）
     强失速=当日九维在榜(RPS50>80)×现弱（强票短期失速）
     深度垫底=超额≤--deep 且分位≥--pct
  7) 输出重点池 + Top N 控制台 + 全量 CSV reports/weak_scan_<ymd>.csv
     （focus 列随 CSV 落盘）+ newcase 提示

注意：日线粗筛（ts_daily×tdx_sector_daily 同日快照），命中后走 newcase
建案 + 联动页三周期核验（分钟级确认在模板 §1 做）。
"""
import argparse
import csv
import glob
import json
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pandas as pd  # noqa: E402
import tdx_read as tdxr  # noqa: E402
import linkage  # noqa: E402

REP = os.path.abspath(os.path.join(HERE, "..", "reports"))


def stock_universe(sb):
    """在市 A 股白名单：剔 ST/退/北交所/B股，返回 df[industry]."""
    df = sb[sb.get("list_status", "L").astype(str) == "L"].copy() \
        if "list_status" in sb.columns else sb.copy()
    bad = df["name"].astype(str).str.contains("ST|退", na=False)
    bj = df["ts_code"].astype(str).str.endswith(".BJ")
    bsh = df["ts_code"].astype(str).str.startswith(("9", "4"))
    df = df[~(bad | bj | bsh)]
    return df[df["industry"].notna() & (df["industry"].astype(str) != "")]


def r20_table(days):
    """全市场 r20% + 区间日均额（亿元）。返回 DataFrame。"""
    start = (datetime.now() - timedelta(days=int(days * 2.2) + 20)
             ).strftime("%Y-%m-%d")
    df = tdxr.read_range("ts_daily", start=start,
                         columns=["ts_code", "trade_date", "close", "amount"])
    df = df.dropna(subset=["close"])
    df["d"] = df["trade_date"].astype(str).str.slice(0, 10)
    df = df.sort_values(["ts_code", "d"])
    last_day = df["d"].max()
    ubd = sorted(df.loc[df["d"] < last_day, "d"].unique())
    if len(ubd) < days:
        print("历史不足 %d 根（最新 %s）" % (days, last_day))
        return None, None
    base_day = ubd[-days]

    g = df.groupby("ts_code")
    stats = g.apply(lambda x: pd.Series({
        "r": (x.iloc[-1]["close"] / x[x["d"] == base_day]["close"].iloc[0] - 1)
        * 100 if len(x[x["d"] == base_day]) else None,
        "amt": x[x["d"] >= base_day]["amount"].mean() / 1e5,  # 千元→亿元
        "n": int((x["d"] >= base_day).sum()),
    }), include_groups=False).reset_index()
    stats["last_day"] = last_day
    return stats, last_day


def block_r20(days):
    """行业名 → (板块信息, r20%)。每行业解析一次，组内共享。"""
    sb = tdxr.read_all("ts_stock_basic")
    uni = stock_universe(sb)
    out = {}
    for ind, grp in uni.groupby("industry"):
        code0 = grp["ts_code"].iloc[0]
        blk = linkage.resolve_block(code0)
        if not blk:
            out[ind] = (None, None)
            continue
        bars = linkage.block_bars(blk["code"], "1d", limit=days + 5)
        if not bars or len(bars) < days + 1:
            out[ind] = (blk, None)
            continue
        closes = [b["close"] for b in bars]
        out[ind] = (blk, (closes[-1] / closes[-days - 1] - 1) * 100)
    return out


def strong_codes():
    """中期强势票集合（二道筛数据源）→ {6位码: 'pool'|'snap'}。

    snap = 当日九维快照在榜（RPS50>80 过漏斗，仍强势）；
    pool = 九维警报池史（曾 score≥75 拥挤警报，强势拥挤史）。
    pool 覆盖 snap（更强的强转弱信号）。与 weak 候选交集 =
    前强补跌型（模板 §2③ 主因剧本，比纯垫底弱票更有复盘价值）。
    """
    out = {}
    try:
        with open(os.path.join(HERE, "data", "nine_dim_scores.json"),
                  encoding="utf-8") as f:
            snap = json.loads(f.read().lstrip("\ufeff"))
        for r in snap.get("rows") or []:
            out[str(r.get("code", "")).split(".")[0]] = "snap"
    except Exception:
        pass
    fp = os.path.join(HERE, "data", "nine_dim_history.jsonl")
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line.lstrip("\ufeff"))
                except Exception:
                    continue
                if "alert" in str(r.get("pool_type", "")):
                    out[str(r.get("code", "")).split(".")[0]] = "pool"
    return out


def focus_tag(h, strong, deep, pct):
    """二道筛打标：返回 '强转弱+深度垫底' 样式字符串（无命中为空）。"""
    tags = []
    s = strong.get(h["code"].split(".")[0])
    if s == "pool":
        tags.append("强转弱")
    elif s == "snap":
        tags.append("强失速")
    if h["ex"] <= deep and h["pct_in"] >= pct:
        tags.append("深度垫底")
    return "+".join(tags)


def focus_rank(h):
    """重点优先序：强转弱 > 强失速 > 深度垫底 > 无标记，组内超额升序。"""
    f = h.get("focus", "")
    return (0 if "强转弱" in f else 1 if "强失速" in f
            else 2 if f else 3, h["ex"])


def main():
    ap = argparse.ArgumentParser(description="信号2 扫描器：个股弱于板块候选")
    ap.add_argument("--win", type=int, default=20, help="区间（默认 r20）")
    ap.add_argument("--gap", type=float, default=-5.0,
                    help="超额阈值 pp（默认 -5，≤ 即命中）")
    ap.add_argument("--amt", type=float, default=2.0,
                    help="区间日均额下限亿元（默认 2，对齐九维漏斗）")
    ap.add_argument("--top", type=int, default=20, help="控制台展示前 N")
    ap.add_argument("--min-n", type=int, default=10,
                    help="行业内有效票下限（默认 10，小样本分位无意义）")
    ap.add_argument("--deep", type=float, default=-20.0,
                    help="二道筛·深度垫底超额阈值 pp（默认 -20，≤ 即命中）")
    ap.add_argument("--pct", type=float, default=90.0,
                    help="二道筛·深度垫底分位阈值（默认 90，≥ 即命中）")
    ap.add_argument("--focus-top", type=int, default=15,
                    help="二道筛·重点候选展示前 N（默认 15）")
    ap.add_argument("--send", action="store_true",
                    help="推送新增候选卡片（与上一份扫描 CSV 对比增量）")
    a = ap.parse_args()
    days = a.win

    print("== 信号2 扫描（r%d，超额≤%.0fpp，日均额≥%.0f亿）==" % (days, a.gap, a.amt))
    st, last_day = r20_table(days)
    if st is None:
        return 1
    print("个股数据日 %s，有效票 %d" % (last_day, len(st)))

    sb = tdxr.read_all("ts_stock_basic")
    uni = stock_universe(sb)
    nm = dict(zip(uni["ts_code"], uni["name"]))
    print("板块解析（%d 个行业）…" % uni["industry"].nunique())
    br = block_r20(days)
    ok_blk = sum(1 for v in br.values() if v[1] is not None)
    print("板块 r%d 就绪 %d/%d（tdx_sector_daily 本地快照）"
          % (days, ok_blk, len(br)))

    # 行业内全部有效票的分位（含未过流动性过滤的，口径同 sector_rank）
    valid = st[st["r"].notna() & st["ts_code"].isin(set(uni["ts_code"]))]
    valid = valid.merge(uni[["ts_code", "industry"]], on="ts_code")
    valid = valid.sort_values("r", ascending=False)  # 最强在前（0=最强，同 sector_rank）
    valid["rank_in"] = valid.groupby("industry").cumcount() + 1
    valid["n_in"] = valid.groupby("industry")["ts_code"].transform("count")
    valid["pct_in"] = (valid["rank_in"] - 1) / (valid["n_in"] - 1).clip(
        lower=1) * 100

    hits = []
    for _i, row in valid.iterrows():
        blk, brk = br.get(row["industry"], (None, None))
        if brk is None:
            continue
        ex = row["r"] - brk
        if ex <= a.gap and row["amt"] >= a.amt and row["n_in"] >= a.min_n:
            hits.append({
                "code": row["ts_code"], "name": nm.get(row["ts_code"], "?"),
                "industry": row["industry"],
                "block": blk["code"] if blk else "?",
                "block_name": blk["name"] if blk else "?",
                "r": round(row["r"], 2), "r_block": round(brk, 2),
                "ex": round(ex, 2), "pct_in": round(row["pct_in"], 1),
                "rank_in": "%d/%d" % (row["rank_in"], row["n_in"]),
                "amt": round(row["amt"], 2),
            })
    hits.sort(key=lambda x: x["ex"])

    # 二道筛打标（focus 列随 CSV 落盘，控制台/推送重点优先）
    strong = strong_codes()
    for h in hits:
        h["focus"] = focus_tag(h, strong, a.deep, a.pct)
    foc = [h for h in hits if h["focus"]]
    foc.sort(key=focus_rank)

    if not hits:
        print("无命中候选。")
        return 0

    ymd = last_day.replace("-", "")
    fp = os.path.join(REP, "weak_scan_%s.csv" % ymd)
    os.makedirs(REP, exist_ok=True)
    with open(fp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(hits[0].keys()))
        w.writeheader()
        w.writerows(hits)
    print("\n候选 %d 只（已落盘 %s），超额最差前 %d：" % (len(hits), fp, a.top))
    print("%-12s %-8s %-8s %7s %7s %7s %6s %9s %5s"
          % ("code", "name", "block", "r20", "r板块", "超额", "分位",
             "额(亿)", "名次"))
    for h in hits[:a.top]:
        print("%-12s %-8s %-8s %7.2f %7.2f %7.2f %6.1f %9.2f %5s"
              % (h["code"], h["name"][:4], h["block"], h["r"], h["r_block"],
                 h["ex"], h["pct_in"], h["amt"], h["rank_in"]))

    print("\n== 二道筛：重点候选 %d/%d ==" % (len(foc), len(hits)))
    print("规则：强转弱=九维警报池史×现弱｜强失速=当日九维在榜×现弱"
          "｜深度垫底=超额≤%.0fpp 且分位≥%.0f" % (a.deep, a.pct))
    if foc:
        for h in foc[:a.focus_top]:
            print("  %-12s %-8s %-8s 超额%7.2f 分位%5.1f %6.2f亿  [%s]"
                  % (h["code"], h["name"][:4], h["block"], h["ex"],
                     h["pct_in"], h["amt"], h["focus"]))
        if len(foc) > a.focus_top:
            print("  …另有 %d 只见 CSV focus 列" % (len(foc) - a.focus_top))
        t = foc[0]
    else:
        print("  无（本档候选均不满足任一规则）")
        t = hits[0]
    print("\n建案示例（newcase，重点优先）：")
    print('  python -X utf8 "%s\\weak_track_newcase.py" %s --name "%s"'
          % (HERE, t["code"], t["name"]))

    if a.send:
        # 增量：与最近一份扫描 CSV 对比（同日重跑时 fp 已被覆盖，diff=0 自然跳过）
        prev = sorted(glob.glob(os.path.join(REP, "weak_scan_*.csv")))
        if prev:
            with open(prev[-1], encoding="utf-8-sig") as f:
                old = {r["code"] for r in csv.DictReader(f)}
            new_hits = [h for h in hits if h["code"] not in old]
        else:
            new_hits = list(hits)  # 首跑无前档，全量当新增
        from weak_track_card import scan_card, send_card
        new_hits.sort(key=focus_rank)  # 卡片重点优先（强转弱最前）
        meta = {"last_day": last_day, "n_total": len(hits), "n_new": len(new_hits),
                "fp": fp, "scope": "r%d×超额≤%.0fpp×额≥%.0f亿×n≥%d"
                % (days, a.gap, a.amt, a.min_n),
                "n_focus": len([h for h in new_hits if h["focus"]])}
        print("\n新增候选 %d 只（对比 %s）"
              % (len(new_hits), os.path.basename(prev[-1]) if prev else "无前档"))
        if not new_hits:
            print("无新增，跳过推送。")
            return 0
        print("发送：" + send_card(scan_card(new_hits, meta)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
