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
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pandas as pd  # noqa: E402
import tdx_read as tdxr  # noqa: E402
import linkage  # noqa: E402

# 输出在项目根 reports（⚠️ 与 strong 的 quant_backtest/reports 不同根）——
# 下游 portal/build_todays_picks 按此路径消费 weak_scan_*.csv，勿改。
REP = os.path.abspath(os.path.join(HERE, "..", "reports"))

# 生产者完成标记（2026-10-26 OS-FIX-MEASURE2）：api_common 的 weak 源只认
# "整轮扫描跑完"的证据；CSV/案例 jsonl 的 mtime 只能证明局部写入，零命中扫描
# 更是毫无痕迹。扫描成功出口（含零命中）原子写本文件，供 _alert_source_health
# 的 dated() 判当期。格式 schema=weak_scan_last/v1。
LAST_MARKER = os.path.join(REP, "weak_scan_last.json")
CST = timezone(timedelta(hours=8))


def write_scan_marker(last_day, case_count, source="quant_backtest/weak_scan.py"):
    """写整轮扫描完成标记（原子写）→ payload。

    · completed_at：本轮扫描完成时刻（北京时间 ISO8601 +08:00）；
    · trade_date：本轮扫描覆盖的行情日（YYYY-MM-DD）；
    · case_count：命中案例数（0 = 合法零命中，仍证明"跑完了"）。
    写失败不致命：扫描结果照常返回，由调用方决定是否告警（本函数只做 IO）。
    """
    day = str(last_day or "").strip()
    if len(day) == 8 and day.isdigit():
        day = "%s-%s-%s" % (day[:4], day[4:6], day[6:])
    payload = {
        "schema": "weak_scan_last/v1",
        "completed_at": datetime.now(CST).isoformat(timespec="seconds"),
        "trade_date": day[:10],
        "source": source,
        "case_count": int(case_count),
    }
    os.makedirs(REP, exist_ok=True)
    tmp_path = LAST_MARKER + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp_path, LAST_MARKER)
    return payload


# 行情日期护栏（K-QUANT-20261019-WEAKGUARD）日历基准：本地 TuShare
# trade_cal 只读快照（判例 build_history_backfill_plan.py 同源目录）。
CAL_DIR = os.path.join(HERE, "data", "tushare", "trade_cal")


def expected_trade_day(now=None, cal_dir=None):
    """护栏期望日：今日之前的最近交易日（YYYYMMDD，SSE is_open=1）。

    口径依据 T-Q3 定因（2026-10-19）：日线入湖唯一链路 tdx_sync 每日
    17:30 触发，weak_scan 15:35 档口当日行情必然未入湖 → 期望=上一
    交易日（17:30 后补跑/周末跑同样成立：湖面日期 ≥ 期望即放行）。
    日历快照不可用或无早于今天的交易日 → 返回 None（调用方降级 WARN
    不硬拒：护栏误杀比放行更伤产线节奏）。
    """
    now = now or datetime.now()
    cal_dir = cal_dir or CAL_DIR
    try:
        parts = sorted(glob.glob(os.path.join(cal_dir, "*.parquet")))
        if not parts:
            return None
        df = pd.concat(
            [
                pd.read_parquet(p, columns=["exchange", "cal_date", "is_open"])
                for p in parts
            ]
        )
        d = df[(df["exchange"] == "SSE") & (df["is_open"].astype(int) == 1)]
        days = sorted(
            set(d["cal_date"].astype(str).str.replace("-", "", regex=False).str[:8])
        )
        today = now.strftime("%Y%m%d")
        prev = [x for x in days if x < today]
        return prev[-1] if prev else None
    except Exception as e:
        sys.stderr.write(" ! trade_cal 读取失败，护栏降级: %s\n" % e)
        return None


def check_trade_day(last_day, exp=None, now=None):
    """快照落盘/推送前行情日期校验。滞后 → 写明确日志并返回退出码 3
    （拒绝出快照，不动数据；判例 K-STOCK 守卫 exit 3 中止）；日历缺失
    → 降级 WARN 放行；正常 → 返回 None。"""
    last_ymd = str(last_day).replace("-", "")
    exp = exp if exp is not None else expected_trade_day(now=now)
    if exp is None:
        sys.stderr.write(
            " ! trade_cal 快照不可用/未覆盖今日——行情日期护栏"
            "降级 WARN（last_day=%s），请检查 data/tushare/"
            "trade_cal 同步\n" % last_day
        )
        return None
    if last_ymd < exp:
        sys.stderr.write(
            " ! [WEAKGUARD] 行情日期滞后：ts_daily 最新 %s < 预期交易日 %s"
            "（数据湖滞后）——拒绝出快照/推送（数据未动）。请先补增量同步"
            "（tushare_sync）后重跑 weak_scan。\n" % (last_day, exp)
        )
        return 3
    return None


def stock_universe(sb):
    """在市 A 股白名单：剔 ST/退/北交所/B股，返回 df[industry]."""
    df = (
        sb[sb.get("list_status", "L").astype(str) == "L"].copy()
        if "list_status" in sb.columns
        else sb.copy()
    )
    bad = df["name"].astype(str).str.contains("ST|退", na=False)
    bj = df["ts_code"].astype(str).str.endswith(".BJ")
    bsh = df["ts_code"].astype(str).str.startswith(("9", "4"))
    df = df[~(bad | bj | bsh)]
    return df[df["industry"].notna() & (df["industry"].astype(str) != "")]


def r20_table(days):
    """全市场 r20% + 区间日均额（亿元）。返回 DataFrame。"""
    start = (datetime.now() - timedelta(days=int(days * 2.2) + 20)).strftime("%Y-%m-%d")
    df = tdxr.read_range(
        "ts_daily", start=start, columns=["ts_code", "trade_date", "close", "amount"]
    )
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
    stats = g.apply(
        lambda x: pd.Series(
            {
                "r": (x.iloc[-1]["close"] / x[x["d"] == base_day]["close"].iloc[0] - 1)
                * 100
                if len(x[x["d"] == base_day])
                else None,
                "amt": x[x["d"] >= base_day]["amount"].mean() / 1e5,  # 千元→亿元
                "n": int((x["d"] >= base_day).sum()),
            }
        ),
        include_groups=False,
    ).reset_index()
    stats["last_day"] = last_day
    # 单位防呆哨兵（077g）：amount 千元口径 /1e5=亿元。若中位额 <0.1 亿
    # （全市场正常中位约 1~3 亿），大概率数据源单位已变，警告防 10 倍级错判。
    _med = stats["amt"].median()
    if pd.notna(_med) and _med < 0.1:
        sys.stderr.write(
            " ! 全市场日均额中位 %.4f 亿异常偏低——请核对 amount"
            " 单位口径（预期 tushare 千元）\n" % _med
        )
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
        with open(
            os.path.join(HERE, "data", "nine_dim_scores.json"), encoding="utf-8"
        ) as f:
            snap = json.loads(f.read().lstrip("\ufeff"))
        for r in snap.get("rows") or []:
            out[str(r.get("code", "")).split(".")[0]] = "snap"
    except Exception as e:
        # 静默吞掉=强转弱/强失速标签无声消失（077g 审计）：留痕但不炸扫描
        sys.stderr.write(
            " ! nine_dim_scores.json 读取失败，强转弱/强失速标签缺失: %s\n" % e
        )
    fp = os.path.join(HERE, "data", "nine_dim_history.jsonl")
    if os.path.exists(fp):
        _bad = 0
        with open(fp, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line.lstrip("\ufeff"))
                except Exception:
                    _bad += 1
                    continue
                if "alert" in str(r.get("pool_type", "")):
                    out[str(r.get("code", "")).split(".")[0]] = "pool"
        if _bad:
            sys.stderr.write(" ! nine_dim_history.jsonl %d 行解析失败跳过\n" % _bad)
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
    return (0 if "强转弱" in f else 1 if "强失速" in f else 2 if f else 3, h["ex"])


def main():
    ap = argparse.ArgumentParser(description="信号2 扫描器：个股弱于板块候选")
    ap.add_argument("--win", type=int, default=20, help="区间（默认 r20）")
    ap.add_argument(
        "--gap", type=float, default=-5.0, help="超额阈值 pp（默认 -5，≤ 即命中）"
    )
    ap.add_argument(
        "--amt",
        type=float,
        default=2.0,
        help="区间日均额下限亿元（默认 2，对齐九维漏斗）",
    )
    ap.add_argument("--top", type=int, default=20, help="控制台展示前 N")
    ap.add_argument(
        "--min-n",
        type=int,
        default=10,
        help="行业内有效票下限（默认 10，小样本分位无意义）",
    )
    ap.add_argument(
        "--deep",
        type=float,
        default=-20.0,
        help="二道筛·深度垫底超额阈值 pp（默认 -20，≤ 即命中）",
    )
    ap.add_argument(
        "--pct",
        type=float,
        default=90.0,
        help="二道筛·深度垫底分位阈值（默认 90，≥ 即命中）",
    )
    ap.add_argument(
        "--focus-top", type=int, default=15, help="二道筛·重点候选展示前 N（默认 15）"
    )
    ap.add_argument(
        "--send",
        action="store_true",
        help="推送新增候选卡片（与上一份扫描 CSV 对比增量）",
    )
    a = ap.parse_args()
    days = a.win

    print("== 信号2 扫描（r%d，超额≤%.0fpp，日均额≥%.0f亿）==" % (days, a.gap, a.amt))
    st, last_day = r20_table(days)
    if st is None:
        return 1
    print("个股数据日 %s，有效票 %d" % (last_day, len(st)))
    # 行情日期护栏（K-QUANT-20261019-WEAKGUARD）：滞后者不出快照不推送
    # （exit 3，start_weak_scan.ps1 ≥1 即技术告警），拒绝不动数据。
    _g = check_trade_day(last_day)
    if _g is not None:
        return _g

    sb = tdxr.read_all("ts_stock_basic")
    uni = stock_universe(sb)
    nm = dict(zip(uni["ts_code"], uni["name"]))
    print("板块解析（%d 个行业）…" % uni["industry"].nunique())
    br = block_r20(days)
    ok_blk = sum(1 for v in br.values() if v[1] is not None)
    print("板块 r%d 就绪 %d/%d（tdx_sector_daily 本地快照）" % (days, ok_blk, len(br)))

    # 行业内全部有效票的分位（含未过流动性过滤的，口径同 sector_rank）
    valid = st[st["r"].notna() & st["ts_code"].isin(set(uni["ts_code"]))]
    valid = valid.merge(uni[["ts_code", "industry"]], on="ts_code")
    valid = valid.sort_values(
        "r", ascending=False
    )  # 最强在前（0=最强，同 sector_rank）
    valid["rank_in"] = valid.groupby("industry").cumcount() + 1
    valid["n_in"] = valid.groupby("industry")["ts_code"].transform("count")
    valid["pct_in"] = (valid["rank_in"] - 1) / (valid["n_in"] - 1).clip(lower=1) * 100

    hits = []
    for _i, row in valid.iterrows():
        blk, brk = br.get(row["industry"], (None, None))
        if brk is None:
            continue
        ex = row["r"] - brk
        if ex <= a.gap and row["amt"] >= a.amt and row["n_in"] >= a.min_n:
            hits.append(
                {
                    "code": row["ts_code"],
                    "name": nm.get(row["ts_code"], "?"),
                    "industry": row["industry"],
                    "block": blk["code"] if blk else "?",
                    "block_name": blk["name"] if blk else "?",
                    "r": round(row["r"], 2),
                    "r_block": round(brk, 2),
                    "ex": round(ex, 2),
                    "pct_in": round(row["pct_in"], 1),
                    "rank_in": "%d/%d" % (row["rank_in"], row["n_in"]),
                    "amt": round(row["amt"], 2),
                }
            )
    hits.sort(key=lambda x: x["ex"])

    # 二道筛打标（focus 列随 CSV 落盘，控制台/推送重点优先）
    strong = strong_codes()
    for h in hits:
        h["focus"] = focus_tag(h, strong, a.deep, a.pct)
    foc = [h for h in hits if h["focus"]]
    foc.sort(key=focus_rank)

    # 生产者完成标记（2026-10-26 OS-FIX-MEASURE2）：扫描已成功走到这里（含
    # 零命中）就落盘——这是 weak 源"整轮完成"的唯一证据；写失败不影响扫描。
    try:
        write_scan_marker(last_day, len(hits))
    except OSError as e:
        sys.stderr.write(" ! weak_scan_last.json 写入失败（不影响扫描结果）: %s\n" % e)

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
    print(
        "%-12s %-8s %-8s %7s %7s %7s %6s %9s %5s"
        % ("code", "name", "block", "r20", "r板块", "超额", "分位", "额(亿)", "名次")
    )
    for h in hits[: a.top]:
        print(
            "%-12s %-8s %-8s %7.2f %7.2f %7.2f %6.1f %9.2f %5s"
            % (
                h["code"],
                h["name"][:4],
                h["block"],
                h["r"],
                h["r_block"],
                h["ex"],
                h["pct_in"],
                h["amt"],
                h["rank_in"],
            )
        )

    print("\n== 二道筛：重点候选 %d/%d ==" % (len(foc), len(hits)))
    print(
        "规则：强转弱=九维警报池史×现弱｜强失速=当日九维在榜×现弱"
        "｜深度垫底=超额≤%.0fpp 且分位≥%.0f" % (a.deep, a.pct)
    )
    if foc:
        for h in foc[: a.focus_top]:
            print(
                "  %-12s %-8s %-8s 超额%7.2f 分位%5.1f %6.2f亿  [%s]"
                % (
                    h["code"],
                    h["name"][:4],
                    h["block"],
                    h["ex"],
                    h["pct_in"],
                    h["amt"],
                    h["focus"],
                )
            )
        if len(foc) > a.focus_top:
            print("  …另有 %d 只见 CSV focus 列" % (len(foc) - a.focus_top))
        t = foc[0]
    else:
        print("  无（本档候选均不满足任一规则）")
        t = hits[0]
    print("\n建案示例（newcase，重点优先）：")
    print(
        '  python -X utf8 "%s\\weak_track_newcase.py" %s --name "%s"'
        % (HERE, t["code"], t["name"])
    )

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
        meta = {
            "last_day": last_day,
            "n_total": len(hits),
            "n_new": len(new_hits),
            "fp": fp,
            "scope": "r%d×超额≤%.0fpp×额≥%.0f亿×n≥%d" % (days, a.gap, a.amt, a.min_n),
            "n_focus": len([h for h in new_hits if h["focus"]]),
        }
        print(
            "\n新增候选 %d 只（对比 %s）"
            % (len(new_hits), os.path.basename(prev[-1]) if prev else "无前档")
        )
        if not new_hits:
            print("无新增，跳过推送。")
            return 0
        print("发送：" + send_card(scan_card(new_hits, meta)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
