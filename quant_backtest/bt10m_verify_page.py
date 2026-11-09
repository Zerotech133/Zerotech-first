# -*- coding: utf-8 -*-
"""把 reports/bt10m_verify_1y.json 渲染成一页报告（深色，与 bt10m_sweep_page 同一套观感）。

用法：python bt10m_verify_page.py
产出：reports/bt10m_verify_1y.html
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "reports", "bt10m_verify_1y.json")
DST = os.path.join(BASE, "reports", "bt10m_verify_1y.html")

ORDER = ["A", "B", "C", "D", "E", "F"]
SHORT = {"A": "基准三买", "B": "关失效线+20日", "C": "≤3%+关+20日",
         "D": "C+移动止损", "E": "分层加仓", "F": "只首笔5只"}
WLAB = {"IN": "样本内年 2025-10-13 → 2026-10-12（242 日）",
        "OOS": "样本外年 2024-10-08 → 2025-10-10（246 日）"}


def cls(v):
    return "up" if v > 0 else ("dn" if v < 0 else "fl")


def sgn(v, nd=2, suffix="%"):
    if v is None:
        return "—"
    return ("%+.*f" % (nd, v)) + suffix


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    rows = d["rows"]

    def get(cid, tag, cost="30bp"):
        return rows.get("v%s_%s_%s" % (cid, tag, cost))

    # ---------- KPI ----------
    d_in, d_oos = get("D", "IN"), get("D", "OOS")
    d_in12, d_oos12 = get("D", "IN", "122bp"), get("D", "OOS", "122bp")
    cum30 = ((1 + d_in["ret"] / 100) * (1 + d_oos["ret"] / 100) - 1) * 100
    cum122 = ((1 + d_in12["ret"] / 100) * (1 + d_oos12["ret"] / 100) - 1) * 100
    bench_cum = ((1 + d_in["bench_ret"] / 100) * (1 + d_oos["bench_ret"] / 100) - 1) * 100

    kpi = """
<div class="kpis">
  <div class="kpi"><div class="k">两年累计 · 30bp</div>
    <div class="v up">{cum30}</div><div class="s">样本外 {o} / 样本内 {i}</div></div>
  <div class="kpi"><div class="k">同期等权基准</div>
    <div class="v up">{bench}</div><div class="s">+45.11% 之后再 +6.22%</div></div>
  <div class="kpi"><div class="k">两年最大回撤</div>
    <div class="v dn">{dd1} / {dd2}</div><div class="s">OOS / IN · 基准 −16.75% / −20.59%</div></div>
  <div class="kpi"><div class="k">选股 alpha 显著性</div>
    <div class="v fl">t={t1} / {t2}</div><div class="s">两年都过不了 |t|≥2</div></div>
</div>""".replace("{cum30}", sgn(cum30)).replace("{o}", sgn(d_oos["ret"])).replace(
        "{i}", sgn(d_in["ret"])).replace("{bench}", sgn(bench_cum)).replace(
        "{dd1}", sgn(d_oos["dd"])).replace("{dd2}", sgn(d_in["dd"])).replace(
        "{t1}", "%.2f" % d_oos["ex_t"]).replace("{t2}", "%.2f" % d_in["ex_t"])

    # ---------- 并排表 ----------
    tr = []
    for cid in ORDER:
        a, b = get(cid, "IN"), get(cid, "OOS")
        if not a or not b:
            continue
        tr.append(
            "<tr><td class='cid'>%s</td><td class='nm'>%s</td>"
            "<td class='%s'>%s</td><td class='dim'>%s</td><td class='dim'>%d</td>"
            "<td class='%s'>%s</td><td class='dim'>%s</td><td class='dim'>%d</td>"
            "<td class='%s'>%s</td><td class='%s'>%s</td>"
            "<td class='%s'>%s</td><td class='%s'>%s</td></tr>"
            % (cid, a["name"],
               cls(a["ret"]), sgn(a["ret"]), sgn(a["ret_full"]), a["n"],
               cls(b["ret"]), sgn(b["ret"]), sgn(b["ret_full"]), b["n"],
               cls(a["ex_bp"]), sgn(a["ex_bp"], 1, "bp"),
               cls(b["ex_bp"]), sgn(b["ex_bp"], 1, "bp"),
               cls(a["dd"]), sgn(a["dd"]), cls(b["dd"]), sgn(b["dd"])))
    table1 = """
<table class="tbl">
<thead><tr>
  <th rowspan="2"></th><th rowspan="2">配置</th>
  <th colspan="3" class="grp">样本内年（参数就是这年挑的）</th>
  <th colspan="3" class="grp">样本外年（紧邻其前·零重叠）</th>
  <th colspan="2">笔均超额</th><th colspan="2">最大回撤</th>
</tr><tr>
  <th>组合</th><th>满仓等价</th><th>笔数</th>
  <th>组合</th><th>满仓等价</th><th>笔数</th>
  <th>IN</th><th>OOS</th><th>IN</th><th>OOS</th>
</tr></thead>
<tbody>""" + "".join(tr) + "</tbody></table>"

    # ---------- 哑铃图：组合（实心点）· 满仓等价（菱形）· 基准（竖虚线） ----------
    LO, HI = -45.0, 50.0

    def svg_panel(tag, x0, bench):
        x1 = x0 + 372
        yy = 100
        g = []
        g.append("<text class='pt' x='%d' y='%d'>%s</text>"
                 % (x0, 34, WLAB[tag].split("（")[0]))
        g.append("<text class='ps' x='%d' y='%d'>%s</text>"
                 % (x0, 52, WLAB[tag].split("（")[1].rstrip("）")))
        for v in (-40, -20, 0, 20, 40):
            xx = x0 + (v - LO) / (HI - LO) * 372
            g.append("<line class='grid' x1='%.1f' y1='66' x2='%.1f' y2='%d'/>"
                     % (xx, xx, yy + 6 * 34 - 8))
            g.append("<text class='ax' x='%.1f' y='%d' text-anchor='middle'>%+d%%</text>"
                     % (xx, 78, v))
        xb = x0 + (bench - LO) / (HI - LO) * 372
        g.append("<line class='bl' x1='%.1f' y1='68' x2='%.1f' y2='%d'/>"
                 % (xb, xb, yy + 6 * 34 - 10))
        g.append("<text class='blt' x='%.1f' y='%d' text-anchor='middle'>基准 %+.1f%%</text>"
                 % (xb, yy + 6 * 34 + 6, bench))
        for i, cid in enumerate(ORDER):
            r = get(cid, tag)
            if not r:
                continue
            y = yy + i * 34
            xa = x0 + (r["ret"] - LO) / (HI - LO) * 372
            xf = x0 + (r["ret_full"] - LO) / (HI - LO) * 372
            g.append("<text class='cl' x='%d' y='%d'>%s %s</text>"
                     % (x0 - 6, y + 4, cid, SHORT.get(cid, r["name"][:8])))
            g.append("<line class='lk' x1='%.1f' y1='%d' x2='%.1f' y2='%d'/>"
                     % (xa, y, xf, y))
            g.append("<circle class='p1' cx='%.1f' cy='%d' r='5'/>" % (xa, y))
            g.append("<rect class='p2' x='%.1f' y='%d' width='8' height='8' "
                     "transform='rotate(45 %.1f %d)'/>" % (xf - 4, y - 4, xf, y))
            g.append("<text class='vt' x='%.1f' y='%d' text-anchor='end'>%+.1f</text>"
                     % (xa - 9, y + 4, r["ret"]))
        return "".join(g)

    chart = ("<div class='chart'><svg viewBox='0 0 1120 320' width='100%' "
             "style='max-height:340px'>"
             + svg_panel("IN", 120, d_in["bench_ret"])
             + svg_panel("OOS", 620, d_oos["bench_ret"]) +
             "</svg><div class='lg'>"
             "<span><i class='d1'></i>组合收益</span>"
             "<span><i class='d2'></i>满仓等价（收益÷平均仓位）</span>"
             "<span><i class='d3'></i>等权全 A 基准</span></div>"
             "<div class='cap'>点的位置按 ±50% 归一。看两件事："
             "① 蓝点（真实组合）在基准线左边 → 那一年跑输；"
             "② 紫菱形（满仓等价）离蓝点越远 → 越多的涨幅被「只投 40% 仓位」让掉了。</div></div>")

    # ---------- alpha 表 ----------
    ar = []
    for cid in ORDER:
        a, b = get(cid, "IN"), get(cid, "OOS")
        if not a or not b:
            continue
        sig = (abs(a["ex_t"]) >= 2) + (abs(b["ex_t"]) >= 2)
        if sig == 0:
            verdict, vc = "两年都不显著", "fl"
        elif (a["ex_bp"] or 0) * (b["ex_bp"] or 0) > 0:
            verdict, vc = "同向", "up"
        else:
            verdict, vc = "翻转", "dn"
        ar.append("<tr><td class='cid'>%s</td><td class='nm'>%s</td>"
                  "<td class='%s'>%s</td><td class='dim'>%.2f</td>"
                  "<td class='%s'>%s</td><td class='dim'>%.2f</td>"
                  "<td class='%s'>%s</td></tr>"
                  % (cid, a["name"], cls(a["ex_bp"]), sgn(a["ex_bp"], 1, "bp"),
                     a["ex_t"], cls(b["ex_bp"]), sgn(b["ex_bp"], 1, "bp"),
                     b["ex_t"], vc, verdict))
    table2 = ("<table class='tbl'><thead><tr><th></th><th>配置</th>"
              "<th>IN 笔均超额</th><th>t</th><th>OOS 笔均超额</th><th>t</th>"
              "<th>判定</th></tr></thead><tbody>" + "".join(ar) + "</tbody></table>")

    # ---------- 逐季 ----------
    qs = []
    for tag in ("IN", "OOS"):
        r = get("D", tag)
        if not r:
            continue
        cells = "".join("<td class='%s'>%s</td>" % (cls(q["ret_pct"]), sgn(q["ret_pct"], 1))
                        for q in r["quarterly"])
        qs.append("<tr><td class='nm'>%s</td>%s</tr>" % (tag, cells))
    qhead = "".join("<th>%s</th>" % q["q"] for q in get("D", "IN")["quarterly"])
    table3 = ("<table class='tbl q'><thead><tr><th></th>%s</tr></thead><tbody>%s</tbody></table>"
              % (qhead, "".join(qs)))

    # ---------- 加仓 E vs F ----------
    lad = []
    for tag in ("IN", "OOS"):
        e, f = get("E", tag), get("F", tag)
        lad.append("<tr><td class='nm'>%s</td><td class='%s'>%s</td><td class='%s'>%s</td>"
                   "<td class='%s'>%s</td><td class='dim'>%s / %s</td>"
                   "<td class='dim'>%s%% / %s%%</td><td class='dim'>%d</td></tr>"
                   % (tag,
                      cls(e["ret"]), sgn(e["ret"]), cls(f["ret"]), sgn(f["ret"]),
                      cls(e["ret"] - f["ret"]), sgn(e["ret"] - f["ret"]),
                      sgn(e["ret_full"], 1), sgn(f["ret_full"], 1),
                      ("%.1f" % e["avg_inv"]), ("%.1f" % f["avg_inv"]),
                      e["n_add"]))
    table4 = ("<table class='tbl'><thead><tr><th></th><th>E 分层加仓</th>"
              "<th>F 只首笔</th><th>加仓拖累</th><th>满仓等价 E / F</th>"
              "<th>平均仓位 E / F</th><th>加仓笔（E）</th></tr></thead><tbody>"
              + "".join(lad) + "</tbody></table>")

    html = TEMPLATE.replace("{{KPI}}", kpi).replace("{{T1}}", table1).replace(
        "{{CHART}}", chart).replace("{{T2}}", table2).replace(
        "{{T3}}", table3).replace("{{T4}}", table4).replace(
        "{{CUM30}}", sgn(cum30)).replace("{{CUM122}}", sgn(cum122)).replace(
        "{{BENC}}", sgn(bench_cum)).replace(
        "{{DIN}}", sgn(d_in["ret"])).replace("{{DOOS}}", sgn(d_oos["ret"]))
    open(DST, "w", encoding="utf-8").write(html)
    print("wrote %s" % DST)


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>1 年数据二次验证 · 样本内 vs 样本外</title>
<style>
:root{--bg:#0d1117;--pn:#131a24;--pn2:#182231;--bd:#243044;--tx:#e6edf7;
--dim:#8b9bb4;--up:#ff5b5b;--dn:#3ddc97;--fl:#c9a227;--ac:#4ea1ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:38px 22px 70px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:.5px}
.sub{color:var(--dim);font-size:13.5px;margin-bottom:26px}
h2{font-size:17px;margin:38px 0 12px;padding-left:10px;border-left:3px solid var(--ac)}
h3{font-size:14.5px;margin:22px 0 8px;color:var(--ac)}
p{margin:10px 0}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0 8px}
.kpi{background:var(--pn);border:1px solid var(--bd);border-radius:10px;padding:14px 15px}
.kpi .k{color:var(--dim);font-size:12px}
.kpi .v{font-size:22px;font-weight:700;margin:4px 0 2px}
.kpi .s{color:var(--dim);font-size:11.5px}
.up{color:var(--up)}.dn{color:var(--dn)}.fl{color:var(--fl)}.dim{color:var(--dim)}
.tbl{width:100%;border-collapse:collapse;margin:12px 0 6px;font-size:13px}
.tbl th,.tbl td{padding:7px 8px;text-align:right;border-bottom:1px solid var(--bd)}
.tbl th{color:var(--dim);font-weight:500;font-size:12px;background:var(--pn2)}
.tbl th.grp{color:var(--tx);background:#1c2838;border-bottom:1px solid var(--bd)}
.tbl td.cid{color:var(--dim);text-align:center}
.tbl td.nm{text-align:left;color:var(--tx)}
.tbl tbody tr:hover{background:#151e29}
.q td{text-align:center;font-size:13.5px}
.chart{background:var(--pn);border:1px solid var(--bd);border-radius:10px;padding:14px 16px}
svg text{font-family:inherit}
.pt{fill:var(--tx);font-size:13.5px;font-weight:600}
.ps{fill:var(--dim);font-size:11px}
.ax{fill:#5f6f88;font-size:10.5px}
.grid{stroke:#1e2937;stroke-width:1}
.bl{stroke:#8b5cf6;stroke-width:1.4;stroke-dasharray:4 4}
.blt{fill:#a78bfa;font-size:11px}
.cl{fill:var(--dim);font-size:11.5px;text-anchor:end}
.lk{stroke:#2f3d52;stroke-width:6;stroke-linecap:round}
.p1{fill:#4ea1ff;stroke:#0d1117;stroke-width:1.5}
.p2{fill:#a08bff;stroke:#0d1117;stroke-width:1.2}
.vt{fill:#9fb3cc;font-size:11px;font-variant-numeric:tabular-nums}
.lg{display:flex;gap:18px;color:var(--dim);font-size:12px;margin-top:6px;flex-wrap:wrap}
.lg i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.d1{background:#4ea1ff}.d2{background:#a08bff;border-radius:1px!important}
.d3{background:#8b5cf6;height:2px!important;border-radius:0!important;vertical-align:3px!important}
.cap{color:var(--dim);font-size:11.5px;margin-top:8px}
.note{background:#161f2b;border:1px solid #2b3a4f;border-left:3px solid var(--ac);
border-radius:8px;padding:12px 15px;margin:14px 0;font-size:13.5px}
.warn{border-left-color:var(--fl)}
.bad{border-left-color:var(--up)}
ul{margin:8px 0 8px 20px;padding:0}li{margin:5px 0}
code{background:#1b2532;padding:1px 5px;border-radius:4px;font-size:12.5px}
.foot{color:var(--dim);font-size:12px;margin-top:34px;border-top:1px solid var(--bd);padding-top:14px}
@media(max-width:820px){.kpis{grid-template-columns:1fr 1fr}.brow{grid-template-columns:100px 1fr 66px}}
</style></head><body><div class="wrap">

<h1>1 年数据 · 二次验证</h1>
<div class="sub">2026-11-10 · 1000 万本金 · 缠论三买轮动 ·
样本内年（已用）＋ 样本外年（新增）并排 · 单边成本 30bp / 122bp 两档</div>

{{KPI}}

<h2>一句话结论</h2>
<div class="note bad">
<b>「+11.45%」不是选股 alpha，是「低仓位 + 快止损」在弱市里的防守结果。</b>
换成样本外那一年（大盘 +45.11%），同一套规则只做出 {{DOOS}}（满仓等价 34.67%），
<b>跑输基准 10.4pp</b>；两年选股超额 t 值分别是 0.93 和 −1.27，<b>都够不上显著</b>。
连跑两年：组合 {{CUM30}}（30bp）/ {{CUM122}}（122bp），同期等权基准 {{BENC}}。
</div>
<p>但反过来看，这套规则有一项指标两年高度一致：<b>最大回撤 −5.48% 对 −5.47%</b>，
而基准是 −20.59% 和 −16.75%。它不是「能赚更多的策略」，是「用收益换回撤的策略」——
这个定位两年都成立，而「跑赢基准」两年都不成立。</p>

<h2>两年并排</h2>
{{T1}}
<div class="cap">A 基准三买（跌破上沿次日走）· B 关失效线+20 日强平 · C 再加「距上沿≤3%」·
D 再加移动止损 5%/3.5% · E 老田分层加仓（5 只×100 万 + 涨 5% 加 60/40 万）·
F E 的对照（同样选 5 只，永不加仓）。</div>

{{CHART}}

<h2>选股 alpha：唯一能跨年比的量</h2>
<p>笔均超额 = 每笔收益 − 该笔持有期的等权全 A 收益。它把仓位和当年大盘都剔掉了，
所以是判断「信号本身有没有用」的唯一尺子。</p>
{{T2}}
<div class="note warn">
A 那两行（−1581bp / +647bp）不要当结论：笔数只有 29 和 16，
持仓高度重叠、彼此不独立，t 值的前提根本不成立，两年方向还完全相反。
真正稳定的读数是：<b>B / C / D 在两年里没有一条能站住 |t|≥2 的正超额</b>，
而 C、E 在样本外是<b>显著为负</b>（t=−2.12 / −2.45）——牛市里「贴近中枢上沿才买」这条门槛，
系统性把仓位从强势股上挪开了。
</div>

<h2>样本外年做对了什么、做错了什么</h2>
<div class="note">
<b>行为特征两年几乎一致</b>：换手 6.70 vs 6.88 倍、平均持仓 16.0 vs 16.7 日、
平均仓位 42.0% vs 39.5%、单笔盈亏分布形状相同。说明参数不是拟合出来的——
规则在两年的「行为」稳定，变的是环境，不是规则。
</div>
<div class="note bad">
<b>但收益全被环境决定</b>：牛市里 3 买事件 3924 只/年（弱市 1436 只，2.7 倍），
可成交笔数只有 85 笔（弱市 96 笔）——信号多出来全被
<code>top_n</code> 排序和 8% 槽位挡掉了；能买上的那 85 笔胜率 63.5%、PF 2.46，
看着漂亮，但笔均超额 −124.7bp：<b>赚的是普涨的钱，不是选股的钱</b>。
再叠加「20 日强平 + 5% 启动/3.5% 回撤」的移动止损，牛市里的主升段被反复砍在半路。
</div>

<h3>D 的逐季收益（30bp）：不是靠某一季撑起来的</h3>
{{T3}}
<p>样本外年 5 个季度全部为正、样本内年 3 正 2 负，<b>没有单季贡献全年</b>。
这一点比单年总额更值得信——收益是「每个季度都小赚一点」累积出来的，
而不是某一波行情碰运气。</p>

<h2>分层加仓：第二年再次否证</h2>
{{T4}}
<div class="note bad">
同一批选股、同一换手（IN 5.30 倍 / OOS 6.10 倍，E 与 F 完全相同），
唯一差别是「涨 5% 后加 60 万 + 再涨加 40 万」。
样本内拖累 4.3pp，样本外拖累 <b>11.9pp</b>——两年方向一致，且第二年更糟。
样本内那年上一轮已逐笔核过：加过仓的 17 个持仓、共 24 笔加仓单，<b>没有一笔净改善</b>；
样本外这轮只做聚合口径，逐笔反事实要单独跑，但 11.9pp 的差距已经不需要逐笔也能定性。
机制：加仓阈值 +5% 与移动止损布防 +5% 撞在同一价位，加完就没有任何缓冲，
价格一回头同时砍掉两层。
</div>

<h2>口径与数据版本核查</h2>
<ul>
<li><b>样本外窗口</b>：2024-10-08 → 2025-10-10，246 个交易日，与样本内年零重叠、等长。
事件缓存为今日重扫（<code>sig_events_market_v3_20241008_20251010.json</code>，5170 只 / 13561 事件）。</li>
<li><b>数据版本</b>：<code>data/ashare</code> 全部 5816 个文件在 11-09 16:05 被整体刷新过，
其中 210 只（3.6%）丢了 <code>pct_chg</code> 列。已给 <code>market_scan.scan_stock()</code>
补上与引擎 <code>_load_px()</code> 同一推导式（close/pre_close），否则扫描器直接崩。</li>
<li><b>重扫一致性</b>：样本内年重扫结果与 10-13 那次<b>逐条一致</b>——13635 事件、
类型分布完全相同、同一事件 zg 漂移中位 0.000%，漂移 &gt;3% 的 0 条。
<b>之前的结论没有被数据刷新污染</b>，旧缓存已备份为
<code>*.json.bak-20261013scan</code>。</li>
<li><b>口径</b>：T 日收盘确认、T+1 开盘成交；<code>slot_pct=0.08</code>；
<code>cap_mode=regime_8050</code>（高 80/中 50/低 30）；成本单边扣买卖各一次，
买卖各 0.15%（30bp 往返）或各 0.61%（122bp 往返）。</li>
<li><b>「满仓等价」是估算</b>：组合收益 ÷ 平均仓位，用来和满仓的等权基准同尺度比，
不代表真能按此杠杆放大的可实现收益。</li>
</ul>

<h2>下一步该改什么</h2>
<ul>
<li><b>别再加仓，先修退出</b>：20 日强平 + 5%/3.5% 移动止损在牛市里是负贡献。
需要一条「趋势还在就不砍」的规则——例如跌破 20 日均线才走、或把强平日限
从 20 日放宽到「直到卖点或跌破均线」。这一项只需重跑现有引擎，不用重扫。</li>
<li><b>仓位要能升到 100%</b>：现在均仓 40%，牛市里等于自愿放弃一半涨幅。
<code>cap_mode</code> 的高档只给到 80%、且 8% 槽位 × top_n 也填不满。</li>
<li><b>「距上沿≤3%」要分市况</b>：弱市里它把买点从追涨挪回回抽，是正的；
牛市里它把强势股筛掉，显著为负（t=−2.12）。用 regime 做开关，而不是全局常数。</li>
<li><b>修 <code>score_3buy</code> 位置分的量纲 bug</b>：<code>close</code> 用复权价、
<code>zg</code> 用原始价，位置分恒满分 40，top_n 排序里 40% 权重是常量。</li>
</ul>

<div class="foot">
产物：<code>reports/bt10m_verify_1y.json</code> ·
<code>data/runlogs/log_verify_1y_20261110.txt</code> ·
脚本 <code>bt10m_verify.py</code> / <code>bt10m_verify_page.py</code> / <code>bt10m_scan_diff.py</code><br>
所有数字均可在 JSON 中逐项复核；「笔均超额」与「t」由 FIFO 配对逐笔对基准折算。
</div>
</div></body></html>"""


if __name__ == "__main__":
    main()
