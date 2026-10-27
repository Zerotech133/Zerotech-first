# -*- coding: utf-8 -*-
"""T1 通道离线模拟 · 形态 B 数据收集（P5c 2026-10-11）。

形态 B = strong∩chanlun 通道只认 1买（2/3买残余共振降 T2）。本工具
离线重放该通道的历史构成与窗口收益，随 chanlun_pool 增长滚动重跑，
为形态 B 启用门槛提供数据：
  门槛① 两层（1买 vs 2/3买残余）n≥10
  门槛② T+10 中位差 ≥ +1pp 且 2/3买残余层中位 < 0
  门槛③ 供给代价知情：通道交集日均/非零日%（离线全量 1买 独占 ≈ 24%）

口径：chanlun_pool 批次日 D，票级归并（同批多信号、卖点排除、P5b
毒层排除 = 生产 cl_buy 口径），strong 领涨 = strong_verdict 逐日重放
（strong_ts_window_research.build_lead，与 strong_scan 生产口径一致）。
收益 = D+2 开盘买入 × T+5/T+10 收盘卖出（ts_adj_factor 复权）。

局限：qpzm 通道无历史留档，本模拟仅覆盖 strong∩chanlun 通道；1买 样本
集中 2026-04 后（反弹期右尾偏差）。只读工具。用法：
python t1_channel_verdicts.py [--since YYYY-MM-DD] [--by-month] [--supply-q]
  --by-month  按月分解：月度通道层收益（T+5/T+10 中位）+ 月度通道供给
  --supply-q  供给分位数：日粒度分布 / 通道空日率（批次日+全交易日双口径）
"""

import bisect
import collections
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "portal")))

import strong_ts_window_research as R  # noqa: E402
import linkage  # noqa: E402

TOXIC_SET = ("3买bei=F", "2买range")
GATE_MEDIAN_DIFF = 1.0  # 门槛②：T+10 中位差 ≥ +1pp
GATE_N = 10  # 门槛①：两层 n≥10


def sig_layer(e):
    """单信号分层键。"""
    t = e["type"]
    if t == "1买":
        return "1买"
    if t == "2买":
        return "2买range" if e["trend"] == "range" else "2买残余"
    if t == "3买":
        if e["bei"] is False:
            return "3买bei=F"
        return "3买bei=T" if e["bei"] is True else "3买bei=None"
    return "卖"


def main():
    since = None
    by_month = "--by-month" in sys.argv  # 按月分解（报告 §3.4）
    supply_q = "--supply-q" in sys.argv  # 供给分位数（报告 §3.5）
    for i, a in enumerate(sys.argv):
        if a == "--since" and i + 1 < len(sys.argv):
            since = sys.argv[i + 1][:10]  # 近期切片验证，如 2026-09-11
    import chanlun_quality_layering as L

    evts = L.load_events_full()
    if since:
        evts = [e for e in evts if e["D"] >= since]
    print(
        "[1/3] 买点事件 %d%s" % (len(evts), "（since=%s）" % since if since else ""),
        flush=True,
    )
    cal = R.trade_calendar()
    stocks = R.load_table_grouped(
        "ts_daily", ["ts_code", "trade_date", "open", "close"], "ts_code"
    )
    blks = R.load_table_grouped(
        "tdx_sector_daily", ["code", "trade_date", "close"], "code"
    )
    adj_all = R.load_table_grouped(
        "ts_adj_factor", ["ts_code", "trade_date", "adj_factor"], "ts_code"
    )

    by_code = collections.defaultdict(list)
    for e in evts:
        by_code[e["code"]].append(e)

    rows = []
    lead_cache = {}
    for code, evts_c in by_code.items():
        stock = stocks.get(code)
        if stock is None or len(stock) < R.MIN_BARS:
            continue
        adf = adj_all.get(code)
        adjmap = (
            {str(t): float(a) for t, a in zip(adf["trade_date"], adf["adj_factor"])}
            if adf is not None and len(adf)
            else None
        )
        for e in evts_c:
            D = R.d8(e["D"])
            ex = R.after_days(cal, D, 2)
            if not ex:
                continue
            wins = {}
            for w in (5, 10):
                d1 = R.after_days(cal, ex, w)
                wins[w] = R.fast_pct(stock, adjmap, ex, d1) if d1 else None
            is_lead = None
            blk = linkage.resolve_block(code)
            if blk:
                key = (code, blk["code"])
                if key not in lead_cache:
                    bdf = blks.get(blk["code"])
                    lead_cache[key] = (
                        R.build_lead(
                            stock, bdf, len(linkage.block_members(blk["code"]))
                        )
                        if bdf is not None
                        else (None, None)
                    )
                dates, lead = lead_cache[key]
                if dates:
                    i = bisect.bisect_right(dates, D) - 1
                    if i >= 0:
                        is_lead = bool(lead[i])
            rows.append(dict(e, layer=sig_layer(e), lead=is_lead, wins=wins))
    print("[2/3] 事件处理完成 %d" % len(rows), flush=True)

    # 票级归并：同批日一票多信号 → lead∩cl_buy 残余/1买/毒层（互斥）
    by_day_code = collections.defaultdict(lambda: collections.defaultdict(set))
    for r in rows:
        by_day_code[r["D"]][r["code"]].add(r["layer"])
    chan_rows = {"1买": [], "2/3买残余": [], "毒层(P5b已砍)": []}
    day_supply = {"resid": [], "first": [], "chan_resid": [], "chan_first": []}
    day_stats = {}  # D → [池残余, 池1买, 通道残余, 通道1买]（--by-month/--supply-q）
    for D, codes in by_day_code.items():
        st = day_stats.setdefault(D, [0, 0, 0, 0])
        leads = {r["code"] for r in rows if r["D"] == D and r["lead"]}
        n_resid = n_first = 0
        for code, lays in codes.items():
            has_sell = any(l.endswith("卖") or "卖" in l for l in lays)
            buys = {l for l in lays if l != "卖"}
            r0 = next(r for r in rows if r["code"] == code and r["D"] == D)
            if not buys or has_sell:
                continue
            toxic = buys & set(TOXIC_SET)
            resid = buys - set(TOXIC_SET)
            if resid:
                n_resid += 1
            if "1买" in resid:
                n_first += 1
            if code in leads:  # 当日 strong 领涨（通道内）
                if resid:
                    day_supply["chan_resid"].append(1)
                    st[2] += 1
                if "1买" in resid:
                    day_supply["chan_first"].append(1)
                    st[3] += 1
                    chan_rows["1买"].append(r0)
                elif toxic and not resid:
                    chan_rows["毒层(P5b已砍)"].append(r0)
                elif resid:
                    chan_rows["2/3买残余"].append(r0)
        day_supply["resid"].append(n_resid)
        day_supply["first"].append(n_first)
        st[0], st[1] = n_resid, n_first
    print("[3/3] 批次日 %d" % len(day_supply["resid"]), flush=True)

    def stat(sel, w):
        vals = [r["wins"][w] for r in sel]
        vals = [v for v in vals if isinstance(v, float) and np.isfinite(v)]
        if not vals:
            return None
        arr = np.array(vals)
        return (
            len(vals),
            round(float(arr.mean()), 2),
            round(float(np.median(arr)), 2),
            round(float((arr > 0).mean() * 100), 1),
        )

    print("\n=== T1 通道（当日领涨∩cl_buy 残余）分层收益 ===")
    print(
        "%-16s %11s | %22s | %22s"
        % ("层", "有效n/事件", "T+5 均值/中位/胜率%", "T+10 均值/中位/胜率%")
    )
    meds = {}
    n_eff = {}
    for k, sel in chan_rows.items():
        s5, s10 = stat(sel, 5), stat(sel, 10)
        if s5 and s10:
            meds[k] = s10[2]
            n_eff[k] = (s5[0], s10[0])
            print(
                "%-16s %4d/%-6d | %6.2f %6.2f %6.1f | %6.2f %6.2f %6.1f"
                % (k, s5[0], len(sel), s5[1], s5[2], s5[3], s10[1], s10[2], s10[3])
            )
        else:
            print("%-16s %5d | 样本不足" % (k, len(sel)))

    print("\n=== 供给频率 ===")
    for k, lab in (
        ("resid", "cl_buy 残余全体（池级）"),
        ("first", "cl_buy 仅1买（池级）"),
        ("chan_resid", "通道内：领涨∩残余"),
        ("chan_first", "通道内：领涨∩1买"),
    ):
        v = np.array(day_supply[k])
        per = "日均%.2f" % (v.sum() / len(day_supply["resid"]))
        print("  %s：%s 总量%d" % (lab, per, int(v.sum())))

    print("\n=== 形态 B 启用门槛判定（本轮数据）===")
    a, b = chan_rows["1买"], chan_rows["2/3买残余"]
    sa, sb = stat(a, 10), stat(b, 10)
    if sa and sb:
        # 门槛①用 T+10 有效样本（满窗）计数，防窗口未满误判
        na, nb = sa[0], sb[0]
        diff = round(sa[2] - sb[2], 2)
        c1 = na >= GATE_N and nb >= GATE_N
        c2 = diff >= GATE_MEDIAN_DIFF and sb[2] < 0
        print(
            "  ① n≥10（T+10 满窗）：1买 n=%d / 残余 n=%d → %s"
            % (na, nb, "过" if c1 else "不过")
        )
        print(
            "  ② T+10 中位差 %.2fpp≥+1 且残余中位 %+.2f<0 → %s"
            % (diff, sb[2], "过" if c2 else "不过")
        )
        print(
            "  ③ 供给代价需知情确认：通道内 1买独占 ≈ 残余的 %.0f%%"
            "（领涨∩残余 总量 %d → 领涨∩1买 %d）"
            % (
                sum(day_supply["chan_first"])
                / max(sum(day_supply["chan_resid"]), 1e-9)
                * 100,
                sum(day_supply["chan_resid"]),
                sum(day_supply["chan_first"]),
            )
        )
        print(
            "  结论：形态 B %s（③由用户拍板）"
            % ("门槛达标，可提启用方案" if c1 and c2 else "门槛未达，继续滚动积累")
        )
        if max(na, nb) < 30:  # SOP 复核可读线（11-02 判定门①）：防误读
            print(
                "  注：本轮 T+10 满窗 n<30，「不过」仅指样本不足，"
                "不构成形态 B 的反向证据（近月复核需 n≥30）"
            )

    if by_month:
        print(
            "\n=== 按月分解：通道内层收益（n=满窗数，格式 n/T+5中位 n/T+10中位，%）==="
        )

        def med_w(sel, w):
            vals = [
                r["wins"][w]
                for r in sel
                if isinstance(r["wins"][w], float) and np.isfinite(r["wins"][w])
            ]
            if not vals:
                return "0/—"
            return "%d/%+.2f" % (len(vals), float(np.median(vals)))

        months = sorted({D[:7] for D in day_stats})
        for m in months:
            fa = [r for r in chan_rows["1买"] if r["D"][:7] == m]
            fb = [r for r in chan_rows["2/3买残余"] if r["D"][:7] == m]
            a10 = med_w(fa, 10).split("/")[1]
            b10 = med_w(fb, 10).split("/")[1]
            diff = "—" if "—" in (a10, b10) else "%+.2f" % (float(a10) - float(b10))
            print(
                "%-9s 1买 %10s %10s | 残余 %10s %10s | T+10差 %s"
                % (m, med_w(fa, 5), med_w(fa, 10), med_w(fb, 5), med_w(fb, 10), diff)
            )
        print("\n=== 按月分解：通道供给（批次日口径）===")
        print("%-9s %8s %9s %7s" % ("月", "残余/日", "1买/日", "空日%"))
        for m in months:
            ds = [v for D, v in day_stats.items() if D[:7] == m]
            nd = len(ds)
            print(
                "%-9s %8.2f %9.2f %6.0f%%"
                % (
                    m,
                    sum(v[2] for v in ds) / nd,
                    sum(v[3] for v in ds) / nd,
                    sum(1 for v in ds if v[2] == 0) / nd * 100,
                )
            )

    if supply_q:
        print("\n=== 供给分位数（日粒度，批次日 n=%d）===" % len(day_stats))
        print(
            "%-12s %5s %5s %5s %5s %4s %6s %6s %6s"
            % ("口径", "p25", "p50", "p75", "p90", "max", "日均", "空日%", "≥2日%")
        )
        for idx, lab in (
            (0, "池级残余/日"),
            (1, "池级1买/日"),
            (2, "通道残余/日"),
            (3, "通道1买/日"),
        ):
            arr = np.array([v[idx] for v in day_stats.values()], dtype=float)
            q = np.percentile(arr, [25, 50, 75, 90])
            print(
                "%-12s %5.1f %5.1f %5.1f %5.1f %4.0f %6.2f "
                "%5.0f%% %5.0f%%"
                % (
                    lab,
                    q[0],
                    q[1],
                    q[2],
                    q[3],
                    arr.max(),
                    arr.mean(),
                    (arr == 0).mean() * 100,
                    (arr >= 2).mean() * 100,
                )
            )
        d8s = sorted(R.d8(D) for D in day_stats)
        t_days = bisect.bisect_right(cal, d8s[-1]) - bisect.bisect_left(cal, d8s[0])
        if t_days > 0:
            nz_r = sum(1 for v in day_stats.values() if v[2] > 0)
            nz_f = sum(1 for v in day_stats.values() if v[3] > 0)
            print(
                "（全交易日口径 n=%d：通道残余空日 %.0f%% · "
                "通道1买空日 %.0f%%）"
                % (t_days, (1 - nz_r / t_days) * 100, (1 - nz_f / t_days) * 100)
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
