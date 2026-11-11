# -*- coding: utf-8 -*-
"""1000 万模拟盘 × 全部有效方法论 × 逐月收益/持仓/手法（2026-11-10，老田指令）。

「用 1000 万把所有方法论对比每个月的收益变换、持仓情况、交易手法。」

七条腿（全部现役与候选方法论；regime/已死亡变体不再占用算力）：
  V3            基线（现行固化口径）
  S2_stop12     止损 −8%→−12%        （合并 α+179bp t=1.99 候选池头名）
  A3_slotscore  slot_mode=score      （score 加权）
  D1_score_cap25 score + cap25
  C5_A3+S2      score + 止损−12%     （收益最猛组合）
  C6_D1+S2      score+cap25+止损−12% （最均衡组合）
  P4_part50_trail 分批止盈+trail     （弱市 regime 选项）

口径（与 bt10m_10m_cand.py 完全一致）：M1 = 开盘 + 流动性帽 1%，
冲击平方根 k=100bp，真实费率，100 股取整，复权价记账。
窗口 RY 20251110→20261109，按自然月切 13 段（首尾半月）。

每月三组指标：
  收益：月收益率 %（盈亏万元）＋ 月内最大回撤 %
  持仓：月内平均仓位 % ＋ 月末持仓只数（小字：月内最高仓位 %）
  手法：买入笔数 ＋ 卖出构成（止损/止盈/到期/规则）（小字：成交额万元）

自检门：
  G1  每腿 extra 经 apply_preset 展开后键值原样在位；
  G2  V3 全年 NAV 必须复现 bt10m_10m_cand.json M1 = 106.32（±0.05pp）；
  G3  各腿月收益连乘（未舍入）复现全年 NAV（±0.01pp）；
  G4  逐腿对账偏差 < 0.05pp（复用 build_legs 的 recon）；
  G5  含 S2 的腿 stop_loss 笔数必须异于 V3（防参数静默回落）。

用法：python bt10m_10m_monthly2.py
产出：reports/bt10m_10m_monthly2.json + reports/bt10m_10m_monthly2.html
"""
import json
import math
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

import backtest_engine_v2 as eng          # noqa: E402
import bt10m                              # noqa: E402
import bt10m_10m_cand as cand             # noqa: E402

OUT_JSON = os.path.join(BASE, "reports", "bt10m_10m_monthly2.json")
OUT_HTML = os.path.join(BASE, "reports", "bt10m_10m_monthly2.html")

CAPITAL = cand.CAPITAL
D0, D1 = cand.D0, cand.D1
K_BP = cand.K_BP
LOT = cand.LOT
LIQ_CAP = cand.LIQ_CAP
TP_PCT, TP_SLIP = cand.TP_PCT, cand.TP_SLIP
fee_buy, fee_sell = cand.fee_buy, cand.fee_sell
impact_bp = cand.impact_bp
day_amount = cand.day_amount
_take_shares = cand._take_shares

LEGS = [
    ("V3", {}),
    ("S2_stop12", {"stop_pct": -0.12}),
    ("A3_slotscore", {"slot_mode": "score"}),
    ("D1_score_cap25", {"slot_mode": "score", "slot_score_cap": 0.25}),
    ("C5_A3+S2", {"slot_mode": "score", "stop_pct": -0.12}),
    ("C6_D1+S2", {"slot_mode": "score", "slot_score_cap": 0.25,
                  "stop_pct": -0.12}),
    ("P4_part50_trail", {"tp_partial": 0.5,
                         "trail_activate": 0.20, "trail_drawdown": 0.05}),
]
NAMES = {
    "V3": "V3 基线（现行）",
    "S2_stop12": "S2 止损−12%",
    "A3_slotscore": "A3 score 加权",
    "D1_score_cap25": "D1 score+cap25",
    "C5_A3+S2": "C5 A3+止损−12%",
    "C6_D1+S2": "C6 D1+止损−12%",
    "P4_part50_trail": "P4 分批+trail",
}


def sell_cat(how):
    if how == "stop_loss":
        return "止损"
    if how.startswith("take_profit"):
        return "止盈"
    if how == "time_stop":
        return "到期"
    return "规则"      # trail_stop / ma_break / 3b_fail / exit_open / dk_*


def sim_m(method, k, days, legq, buy_recs):
    """单腿模拟——2026-11-10 审计后委托 bt10m_sim_core 单一内核
    （含月度台账；卖出尾巴成交额回退量纲修复在内核里）。"""
    import bt10m_sim_core as core
    st = core.simulate(
        days, legq, buy_recs,
        px=eng.px, px_raw=eng.px_raw, day_amount=day_amount,
        capital=CAPITAL, liq_cap=LIQ_CAP, k_bp=k, lot=LOT,
        tp_ratio=(1 + TP_PCT) * (1 - TP_SLIP),
        rearm_mult=(1 + 0.20) * (1 - TP_SLIP),
        method=method,
    )
    return {"eq": st["eq"], "impact_yuan": st["impact_yuan"],
            "fees": st["fees"], "min_cash": st["min_cash"],
            "months": st["months"]}


def main():
    t0 = time.time()
    bench, _ = bt10m.load_market_bench(D0, D1)

    # G1：每腿 extra 经 apply_preset 展开后原样在位
    for tag, extra in LEGS:
        ap = eng.apply_preset(dict({"preset": "v3"}, **extra))
        for key, want in extra.items():
            assert ap.get(key) == want, \
                "G1 失败：%s %s=%r（期望 %r）" % (tag, key, ap.get(key), want)
    print("G1 通过：%d 腿参数全部在位" % len(LEGS), flush=True)

    legs_out = {}
    days_ref = None
    stop_counts = {}
    for tag, extra in LEGS:
        days, engine_nav, buy_recs, legq, recon = cand.build_legs(tag, extra)
        if days_ref is None:
            days_ref = days
        assert days == days_ref, "%s 交易日历不一致" % tag
        if recon:
            dev = max(recon)
            assert dev < 0.05, "G4 失败：%s 对账偏差 %.4fpp" % (tag, dev)
        st = sim_m("M1_liqcap1", K_BP, days,
                   dict((kk, [dict(l) for l in vv]) for kk, vv in legq.items()),
                   buy_recs)
        stop_counts[tag] = sum(mm["sells"]["止损"] for mm in st["months"].values())

        # 月度切分
        slices = []
        cur_mk, cur_eqs = None, []
        start_val = None
        for d, v in zip(days, st["eq"]):
            mk = d[:6]
            if mk != cur_mk:
                if cur_mk is not None:
                    slices.append((cur_mk, start_val, cur_eqs[-1], cur_eqs))
                cur_mk, cur_eqs = mk, [v]
                start_val = slices[-1][2] if slices else float(CAPITAL)
            cur_eqs.append(v)
        if cur_mk is not None:
            slices.append((cur_mk, start_val, cur_eqs[-1], cur_eqs))

        rows = []
        comp = 1.0
        for mk, s0, s1, eqs in slices:
            ret = (s1 / s0 - 1) * 100
            comp *= (1 + ret / 100.0)
            peak, mdd = s0, 0.0
            for v in eqs:
                peak = max(peak, v)
                mdd = min(mdd, v / peak - 1)
            mm = st["months"][mk]
            sel = mm["sells"]
            rows.append({
                "month": "%s-%s" % (mk[:4], mk[4:]),
                "ret_pct": round(ret, 2),
                "pnl_wan": round((s1 - s0) / 10000.0, 1),
                "mdd_pct": round(mdd * 100, 2),
                "_raw_ret": ret,
                "avg_deploy_pct": round(mm["deploy_sum"] / max(mm["days"], 1), 1),
                "max_deploy_pct": round(mm["max_deploy"], 1),
                "holdings_eom": mm["holdings_eom"],
                "n_buy": mm["n_buy"],
                "sells": dict(sel),
                "turnover_wan": round(mm["turnover"] / 10000, 0),
                "fees_wan": round(mm["fees"] / 10000, 2),
                "n_carry": mm["n_carry"], "n_downsize": mm["n_downsize"],
            })
        final = st["eq"][-1] / CAPITAL * 100
        # G3：月连乘复现全年
        assert abs(comp * 100 - final) <= 0.01, \
            "G3 失败：%s 月连乘 %.4f vs 全年 %.4f" % (tag, comp * 100, final)
        legs_out[tag] = {"final_nav": round(final, 2),
                         "engine_nav_flat122": round(engine_nav, 2),
                         "n_buys": len(buy_recs),
                         "stop_loss_total": stop_counts[tag],
                         "months": rows}
        print("%-16s NAV %7.2f%%（引擎122bp %6.2f%%） 止损笔数 %d  月连乘 %.2f%%"
              % (tag, final, engine_nav, stop_counts[tag], comp * 100), flush=True)

    # G2：V3 复现 cand 报告
    cand_j = json.load(open(os.path.join(BASE, "reports", "bt10m_10m_cand.json"),
                            encoding="utf-8"))
    v3_ref = [r for r in cand_j["legs"]["V3"]["rows"]
              if r["method"] == "M1_liqcap1"][0]["nav"]
    assert abs(legs_out["V3"]["final_nav"] - v3_ref) <= 0.05, \
        "G2 失败：V3 %.2f vs 期望 %.2f" % (legs_out["V3"]["final_nav"], v3_ref)
    print("G2 通过：V3 复现 cand M1 值 %.2f" % v3_ref, flush=True)

    # G5：含 S2 的腿止损笔数必须异于 V3（防 stop_pct 静默回落）
    for tag in ("S2_stop12", "C5_A3+S2", "C6_D1+S2"):
        assert stop_counts[tag] != stop_counts["V3"], \
            "G5 失败：%s 止损笔数与 V3 相同（%d），stop_pct 疑未生效" % (
                tag, stop_counts["V3"])
    print("G5 通过：S2 系止损笔数均异于 V3（V3=%d，S2=%d）"
          % (stop_counts["V3"], stop_counts["S2_stop12"]), flush=True)

    # 等权基准同月
    bdays = sorted(bench)
    bslices = []
    cur_mk, cur_eqs = None, []
    start_val = None
    for d in bdays:
        v = bench[d]
        mk = d[:6]
        if mk != cur_mk:
            if cur_mk is not None:
                bslices.append((cur_mk, start_val, cur_eqs[-1]))
            cur_mk, cur_eqs = mk, [v]
            start_val = bslices[-1][2] if bslices else float(v)
        cur_eqs.append(v)
    if cur_mk is not None:
        bslices.append((cur_mk, start_val, cur_eqs[-1]))
    bench_rows = [{"month": "%s-%s" % (mk[:4], mk[4:]),
                   "ret_pct": round((s1 / s0 - 1) * 100, 2)}
                  for mk, s0, s1 in bslices]

    out = {"window": "%s-%s" % (D0, D1), "capital": CAPITAL,
           "method": "M1_liqcap1", "k_bp": K_BP,
           "legs": legs_out, "bench": bench_rows,
           "secs": round(time.time() - t0, 1)}
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("落盘 %s" % OUT_JSON)
    write_html(out)
    print("落盘 %s（%.1fs）" % (OUT_HTML, time.time() - t0))
    return out


def write_html(out):
    legs = out["legs"]
    order = [t for t, _ in LEGS]
    months = [r["month"] for r in legs["V3"]["months"]]
    bmap = {r["month"]: r["ret_pct"] for r in out["bench"]}

    def cls(v):
        return "up" if v > 0 else ("dn" if v < 0 else "")

    th = "".join("<th>%s</th>" % NAMES[t] for t in order)

    # ---- 表1 收益 ----
    rows1 = []
    for i, mo in enumerate(months):
        tds = ['<td class="m">%s</td>' % mo]
        b = bmap.get(mo)
        tds.append('<td class="%s">%+.2f%%</td>' % (cls(b), b) if b is not None
                   else "<td>—</td>")
        for t in order:
            r = legs[t]["months"][i]
            tds.append('<td class="%s"><b>%+.2f%%</b>（%+.1f 万）'
                       '<span class="dd">回撤 %.2f%%</span></td>'
                       % (cls(r["ret_pct"]), r["ret_pct"], r["pnl_wan"],
                          r["mdd_pct"]))
        rows1.append("<tr>%s</tr>" % "".join(tds))
    comp_b = 1.0
    for mo in months:
        v = bmap.get(mo)
        if v is not None:
            comp_b *= (1 + v / 100.0)
    tds = ['<td class="m"><b>全年</b></td>',
           '<td class="%s"><b>%+.2f%%</b></td>' % (cls((comp_b - 1) * 100),
                                                   (comp_b - 1) * 100)]
    for t in order:
        f = legs[t]["final_nav"] - 100
        pnl = (legs[t]["final_nav"] / 100.0 - 1) * CAPITAL / 10000.0
        tds.append('<td class="%s"><b>%+.2f%%</b>（%+.1f 万）</td>'
                   % (cls(f), f, pnl))
    rows1.append('<tr class="total">%s</tr>' % "".join(tds))

    # ---- 表2 持仓 ----
    rows2 = []
    for i, mo in enumerate(months):
        tds = ['<td class="m">%s</td>' % mo, "<td>—</td>"]
        for t in order:
            r = legs[t]["months"][i]
            tds.append('<td><b>%.1f%%</b>／%d 只'
                       '<span class="dd">最高 %.1f%%</span></td>'
                       % (r["avg_deploy_pct"], r["holdings_eom"],
                          r["max_deploy_pct"]))
        rows2.append("<tr>%s</tr>" % "".join(tds))
    tds = ['<td class="m"><b>全年</b></td>', "<td>—</td>"]
    for t in order:
        ms = legs[t]["months"]
        avg = sum(r["avg_deploy_pct"] for r in ms) / len(ms)
        mx = max(r["max_deploy_pct"] for r in ms)
        tds.append('<td><b>%.1f%%</b>均<span class="dd">最高 %.1f%%</span></td>'
                   % (avg, mx))
    rows2.append('<tr class="total">%s</tr>' % "".join(tds))

    # ---- 表3 手法 ----
    rows3 = []
    for i, mo in enumerate(months):
        tds = ['<td class="m">%s</td>' % mo, "<td>—</td>"]
        for t in order:
            r = legs[t]["months"][i]
            s = r["sells"]
            tds.append('<td>买 %d｜损%d 盈%d 期%d 规%d'
                       '<span class="dd">成交 %.0f 万</span></td>'
                       % (r["n_buy"], s["止损"], s["止盈"], s["到期"],
                          s["规则"], r["turnover_wan"]))
        rows3.append("<tr>%s</tr>" % "".join(tds))
    tds = ['<td class="m"><b>全年</b></td>', "<td>—</td>"]
    for t in order:
        ms = legs[t]["months"]
        nb = sum(r["n_buy"] for r in ms)
        sl = sum(r["sells"]["止损"] for r in ms)
        tp = sum(r["sells"]["止盈"] for r in ms)
        ex = sum(r["sells"]["到期"] for r in ms)
        rl = sum(r["sells"]["规则"] for r in ms)
        to = sum(r["turnover_wan"] for r in ms)
        tds.append('<td><b>买 %d</b>｜损%d 盈%d 期%d 规%d'
                   '<span class="dd">成交 %.0f 万</span></td>'
                   % (nb, sl, tp, ex, rl, to))
    rows3.append('<tr class="total">%s</tr>' % "".join(tds))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>1000万模拟盘 · 全方法论逐月对比（{D0} ~ {D1}）</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
         background: #fafafa; color: #222; margin: 24px; }}
  h1 {{ font-size: 20px; }} h2 {{ font-size: 15px; color: #555;
       font-weight: normal; margin-top: 28px; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff;
          box-shadow: 0 1px 4px rgba(0,0,0,.12); margin-bottom: 8px; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 7px; text-align: center;
           font-size: 12px; }}
  th {{ background: #f0f0f0; font-weight: bold; }}
  td.m {{ font-weight: bold; background: #fcfcfc; white-space: nowrap; }}
  .up {{ color: #c0392b; }}
  .dn {{ color: #1e8449; }}
  .dd {{ display: block; font-size: 10px; color: #888; margin-top: 1px; }}
  tr.total td {{ background: #fffbe6; font-size: 13px; }}
  .foot {{ font-size: 12px; color: #666; margin-top: 14px; line-height: 1.7; }}
</style></head><body>
<h1>1000 万模拟盘 · 全部方法论逐月对比（收益 / 持仓 / 手法）</h1>
<h2>{D0} ~ {D1} · 7 方案 × M1 流动性帽执行 · 冲击 k={out['k_bp']}bp</h2>

<h2>① 每月收益（收益率 / 盈亏金额 / 月内回撤）</h2>
<table><thead><tr><th>月份</th><th>等权基准</th>{th}</tr></thead><tbody>
{''.join(rows1)}
</tbody></table>

<h2>② 每月持仓（平均仓位% / 月末持仓只数，小字=月内最高仓位%）</h2>
<table><thead><tr><th>月份</th><th>—</th>{th}</tr></thead><tbody>
{''.join(rows2)}
</tbody></table>

<h2>③ 每月交易手法（买入笔数｜卖出构成：损=止损 盈=止盈 期=到期 规=规则离场）</h2>
<table><thead><tr><th>月份</th><th>—</th>{th}</tr></thead><tbody>
{''.join(rows3)}
</tbody></table>

<div class="foot">
口径：1000 万账户，100 股取整，真实费率（佣金 2.5bp + 印花 5bp 卖 + 过户 0.1bp），
冲击 = 平方根模型 k={out['k_bp']}bp；M1 执行 = 开盘下单 + 单笔 ≤ 当日成交额 1%（超出次日续卖）。
复权价记账。首月（2025-11 上半月）与末月（2026-11 下半月）为半月段。涨红跌绿为 A 股惯例。<br>
七方案覆盖全部现役与候选方法论：V3 基线；S2 止损放宽（候选池头名，合并 t=1.99）；
A3/D1 score 加权（含可执行性折损）；C5/C6 组合；P4 分批止盈（弱市选项）。
已死亡的变体（regime 网格、trail 前置、d_legacy 等）不再占用对比位。<br>
自检门：G1 参数在位 / G2 V3 复现 106.32 / G3 月连乘=全年 / G4 对账&lt;0.05pp /
G5 S2 系止损笔数异于基线。
</div></body></html>"""
    open(OUT_HTML, "w", encoding="utf-8").write(html)


if __name__ == "__main__":
    main()
