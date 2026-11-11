# -*- coding: utf-8 -*-
"""1000 万模拟盘 × 候选池四腿 × 逐月拆解（2026-11-10，老田指令）。

「直接用 1000 万模拟盘模拟前十二个月每个月的情况，各种不同方案，
一张表看明白：赚了多少钱、亏了多少钱、回撤了多少。」

口径（与 bt10m_10m_cand.py 完全一致，直接复用其引擎）：
  四腿 V3/A3/D1/P4；执行 M1 = 开盘 + 流动性帽 1%（单笔 ≤ 当日成交额 1%，
  卖出超出次日续卖）；冲击平方根模型 k=100bp；真实费率（佣金 2.5bp+印花 5bp
  卖+过户 0.1bp）；100 股取整；复权价记账、原始价现金。
  窗口 RY = 20251110→20261109，按自然月切 12 段（首尾两个月为半月）。

每月指标：
  月收益率 %（期末净值/上月末净值−1）＋ 月盈亏金额（万元）
  月内最大回撤 %（月内逐日净值的峰谷差，含上月末作为初始峰）
  另附等权基准同月收益作参照。

自检门：
  G1  四腿交易日历完全一致；
  G2  V3 全年 NAV 必须复现 bt10m_10m_cand.json 的 M1 值 106.32（±0.05pp）；
  G3  各腿「月收益连乘」必须复现全年 NAV（±0.01pp，防月切漏日）。

用法：python bt10m_10m_monthly.py
产出：reports/bt10m_10m_monthly.json + reports/bt10m_10m_monthly.html
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

import bt10m                      # noqa: E402  （等权基准）
import bt10m_10m_cand as cand     # noqa: E402  （复用 build_legs / sim）

OUT_JSON = os.path.join(BASE, "reports", "bt10m_10m_monthly.json")
OUT_HTML = os.path.join(BASE, "reports", "bt10m_10m_monthly.html")

D0, D1 = cand.D0, cand.D1         # 20251110 → 20261109
K_BP = cand.K_BP                  # 100
CAPITAL = cand.CAPITAL


def month_key(day):
    return day[:6]


def month_label(mk):
    return "%s-%s" % (mk[:4], mk[4:])


def monthly_slice(days, eq, first_base="capital"):
    """按自然月切净值：返回 [(mk, start_val, end_val, [月内各日净值])]。
    first_base="capital"：首月起点=建账资金（账户净值口径）；
    first_base="first_close"：首月起点=首日收盘（指数/基准口径，无建账概念）。"""
    slices = []
    cur_mk, cur_eqs = None, []
    start_val = None
    for d, v in zip(days, eq):
        mk = month_key(d)
        if mk != cur_mk:
            if cur_mk is not None:
                slices.append((cur_mk, start_val, cur_eqs[-1], cur_eqs))
            cur_mk, cur_eqs = mk, [v]
            if slices:
                start_val = slices[-1][2]
            else:
                start_val = float(CAPITAL) if first_base == "capital" else float(v)
        cur_eqs.append(v)
    if cur_mk is not None:
        slices.append((cur_mk, start_val, cur_eqs[-1], cur_eqs))
    return slices


def row_stats(slices):
    """每月：ret%（对上月末）、盈亏万元、月内最大回撤%。"""
    out = []
    for mk, s0, s1, eqs in slices:
        ret = (s1 / s0 - 1) * 100
        pnl_wan = (s1 - s0) / 10000.0
        peak, mdd = s0, 0.0
        for v in eqs:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        out.append({"month": month_label(mk), "ret_pct": round(ret, 2),
                    "pnl_wan": round(pnl_wan, 1), "mdd_pct": round(mdd * 100, 2),
                    "_raw_ret": ret})
    return out


def main():
    t0 = time.time()
    bench, ks = bt10m.load_market_bench(D0, D1)

    legs_out = {}
    days_ref = None
    for tag, extra in cand.LEGS:
        days, engine_nav, buy_recs, legq, recon = cand.build_legs(tag, extra)
        if days_ref is None:
            days_ref = days
        # G1：交易日历一致
        assert days == days_ref, "G1 失败：%s 交易日历不一致" % tag
        st = cand.sim("M1_liqcap1", K_BP, days,
                      dict((kk, [dict(l) for l in vv]) for kk, vv in legq.items()),
                      buy_recs)
        slices = monthly_slice(days, st["eq"])
        rows = row_stats(slices)
        final = st["eq"][-1] / CAPITAL * 100
        # G3：月收益连乘复现全年净值（用未舍入原始值，展示层才取整）
        comp = 1.0
        for r in rows:
            comp *= (1 + r["_raw_ret"] / 100.0)
        assert abs(comp * 100 - final) <= 0.01, \
            "G3 失败：%s 月连乘 %.4f vs 全年 %.4f" % (tag, comp * 100, final)
        legs_out[tag] = {"final_nav": round(final, 2), "mdd_total": None,
                         "months": rows}
        print("%-16s 全年 NAV %7.2f%%  月连乘 %.2f%%  月数 %d"
              % (tag, final, comp * 100, len(rows)), flush=True)

    # G2：V3 复现 cand 报告的 M1 值
    cand_j = json.load(open(os.path.join(BASE, "reports", "bt10m_10m_cand.json"),
                            encoding="utf-8"))
    v3_ref = [r for r in cand_j["legs"]["V3"]["rows"]
              if r["method"] == "M1_liqcap1"][0]["nav"]
    assert abs(legs_out["V3"]["final_nav"] - v3_ref) <= 0.05, \
        "G2 失败：V3 %s vs 期望 %s" % (legs_out["V3"]["final_nav"], v3_ref)
    print("G2 通过：V3 复现 cand 报告 M1 值 %.2f" % v3_ref, flush=True)

    # 等权基准同月收益
    bdays = sorted(bench)
    bslices = monthly_slice(bdays, [bench[d] for d in bdays],
                            first_base="first_close")
    bench_rows = []
    for mk, s0, s1, _ in bslices:
        bench_rows.append({"month": month_label(mk),
                           "ret_pct": round((s1 / s0 - 1) * 100, 2)})
    for r in legs_out.values():
        pass
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
    """一张表：行=12个月，列=四腿+基准；腿单元格=月收益%+盈亏万元+月内回撤。"""
    legs = out["legs"]
    order = [t for t, _ in cand.LEGS]
    names = {"V3": "V3 基线（现行）", "A3_slotscore": "A3 score 加权",
             "D1_score_cap25": "D1 score+cap25", "P4_part50_trail": "P4 分批+trail"}
    months = [r["month"] for r in legs["V3"]["months"]]
    bmap = {r["month"]: r["ret_pct"] for r in out["bench"]}

    def cls(v):
        if v > 0:
            return "up"
        if v < 0:
            return "dn"
        return ""

    # 表头
    th = "".join("<th>%s</th>" % names[t] for t in order)
    th = "<th>月份</th><th>等权基准</th>" + th
    body_rows = []
    for i, m in enumerate(months):
        tds = ['<td class="m">%s</td>' % m]
        b = bmap.get(m)
        tds.append('<td class="%s">%+.2f%%</td>' % (cls(b), b) if b is not None
                   else "<td>—</td>")
        for t in order:
            r = legs[t]["months"][i]
            tds.append(
                '<td class="%s"><b>%+.2f%%</b>（%+.1f 万）'
                '<span class="dd">回撤 %.2f%%</span></td>'
                % (cls(r["ret_pct"]), r["ret_pct"], r["pnl_wan"], r["mdd_pct"]))
        body_rows.append("<tr>%s</tr>" % "".join(tds))
    # 全年行
    tds = ['<td class="m"><b>全年合计</b></td>']
    b_all = out["bench"] and bmap
    # 基准全年
    b0 = bmap.get(months[0])
    comp_b = 1.0
    for m in months:
        v = bmap.get(m)
        if v is not None:
            comp_b *= (1 + v / 100.0)
    tds.append('<td class="%s"><b>%+.2f%%</b></td>'
               % (cls((comp_b - 1) * 100), (comp_b - 1) * 100))
    for t in order:
        f = legs[t]["final_nav"] - 100
        pnl = (legs[t]["final_nav"] / 100.0 - 1) * CAPITAL / 10000.0
        tds.append('<td class="%s"><b>%+.2f%%</b>（%+.1f 万）</td>'
                   % (cls(f), f, pnl))
    body_rows.append('<tr class="total">%s</tr>' % "".join(tds))

    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>1000万模拟盘 · 逐月对比（%s ~ %s）</title>
<style>
  body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
         background: #fafafa; color: #222; margin: 24px; }
  h1 { font-size: 20px; } h2 { font-size: 15px; color: #555; font-weight: normal; }
  table { border-collapse: collapse; width: 100%%; background: #fff;
          box-shadow: 0 1px 4px rgba(0,0,0,.12); }
  th, td { border: 1px solid #ddd; padding: 8px 10px; text-align: center;
           font-size: 13px; }
  th { background: #f0f0f0; font-weight: bold; }
  td.m { font-weight: bold; background: #fcfcfc; }
  .up { color: #c0392b; }          /* 涨=红（A股口径） */
  .dn { color: #1e8449; }          /* 跌=绿 */
  .dd { display: block; font-size: 11px; color: #888; margin-top: 2px; }
  tr.total td { background: #fffbe6; font-size: 14px; }
  .foot { font-size: 12px; color: #666; margin-top: 14px; line-height: 1.7; }
</style></head><body>
<h1>1000 万模拟盘 · 过去 12 个月逐月对比</h1>
<h2>%s ~ %s · 四方案 × M1 流动性帽执行 · 冲击 k=%dbp</h2>
<table><thead><tr>%s</tr></thead><tbody>
%s
</tbody></table>
<div class="foot">
口径：1000 万账户，100 股取整，真实费率（佣金 2.5bp + 印花 5bp 卖 + 过户 0.1bp），
冲击 = 平方根模型 k=%dbp；M1 执行 = 开盘下单 + 单笔 ≤ 当日成交额 1%%（超出次日续卖）。
复权价记账。首月（%s 上半月）与末月（%s 下半月）为半月段。<br>
单元格：月收益率（月盈亏金额）；小字 = 月内最大回撤（峰谷，含上月末为初始峰）。
涨红跌绿为 A 股惯例。等权基准为同期全池等权收益。
</div></body></html>""" % (
        D0, D1, D0, D1, out["k_bp"],
        th, "\n".join(body_rows),
        out["k_bp"],
        months[0].split("-")[1] + "月10日起", "11月9日止")
    open(OUT_HTML, "w", encoding="utf-8").write(html)


if __name__ == "__main__":
    main()
