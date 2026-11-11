# -*- coding: utf-8 -*-
"""把 reports/bt10m_trend_1y.json 渲染成一页报告（深色，与 bt10m_verify_1y 同一套观感）。

用法：python bt10m_trend_page.py
产出：reports/bt10m_trend_1y.html
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "reports", "bt10m_trend_1y.json")
DST = os.path.join(BASE, "reports", "bt10m_trend_1y.html")

ORDER = ["D", "G", "H", "I", "J", "K", "L", "N", "O", "P", "Q", "R"]
SHORT = {"D": "旧头号（20日+止损）", "G": "MA20退出·仓位不动", "H": "MA20退出+满仓",
         "I": "MA20+满仓+分档", "J": "MA10版", "K": "MA60版", "L": "MA20+60日兜底",
         "N": "入场过滤+MA20退出", "O": "MA20武装+止损8%", "P": "入场过滤+满仓+分档",
         "Q": "入场过滤（不扩仓）", "R": "满仓+分档（不过滤）"}
GROUP = {"D": "对照", "G": "T1", "H": "T1+T2", "I": "T1+T2+T3", "J": "T1+T2+T3",
         "K": "T1+T2+T3", "L": "T1+T2+T3", "N": "T1+T2+T3+入场", "O": "T1变体",
         "P": "全叠加", "Q": "消融", "R": "消融"}

WLAB = {"IN": "样本内年 2025-10-13 → 2026-10-12 · 242 日 · 基准 +6.22%",
        "OOS": "样本外年 2024-10-08 → 2025-10-10 · 246 日 · 基准 +45.11%"}


def cls(v):
    return "up" if v > 0 else ("dn" if v < 0 else "fl")


def sgn(v, nd=2, suffix="%"):
    if v is None:
        return "—"
    return ("%+.*f" % (nd, v)) + suffix


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    rows = d["rows"]
    diag = d.get("ma_diag") or {}

    def get(cid, tag, cost="30bp"):
        return rows.get("t%s_%s_%s" % (cid, tag, cost))

    def cum(cid):
        a, b = get(cid, "IN", "122bp"), get(cid, "OOS", "122bp")
        if not a or not b:
            return None
        return ((1 + a["ret"] / 100) * (1 + b["ret"] / 100) - 1) * 100

    D_i, D_o = get("D", "IN"), get("D", "OOS")
    G_o, Q_o, R_o = get("G", "OOS"), get("Q", "OOS"), get("R", "OOS")
    Q_i, R_i = get("Q", "IN"), get("R", "IN")

    # ---------- KPI ----------
    kpi = """
<div class="kpis">
  <div class="kpi"><div class="k">① 趋势退出（跌破MA20才走）</div>
    <div class="v dn">否</div>
    <div class="s">OOS {g_o} · IN {g_i}<br>旧方案 {d_o} / {d_i}</div></div>
  <div class="kpi"><div class="k">② 仓位升到 100%</div>
    <div class="v fl">只放大 beta</div>
    <div class="s">均仓 42%→88%，收益率跟着涨<br>满仓等价 34.67%→35.34% 几乎不动</div></div>
  <div class="kpi"><div class="k">②+③ 叠加（满仓 + 分档）</div>
    <div class="v fl">牛市有、弱市反</div>
    <div class="s">OOS {r_o}（旧 {d_o}）<br>IN {r_i}（旧 {d_i}）</div></div>
  <div class="kpi"><div class="k">真实成本两年最优</div>
    <div class="v up">{cum_d}</div>
    <div class="s">仍是旧方案 D（122bp 往返）<br>全部 12 组没有一组超过它</div></div>
</div>""".format(g_o=sgn(G_o["ret"]), g_i=sgn(get("G", "IN")["ret"]),
                d_o=sgn(D_o["ret"]), d_i=sgn(D_i["ret"]),
                r_o=sgn(R_o["ret"]), r_i=sgn(R_i["ret"]),
                cum_d=sgn(cum("D")))

    # ---------- 主表 ----------
    tr = []
    for cid in ORDER:
        a, b = get(cid, "IN"), get(cid, "OOS")
        if not a or not b:
            continue
        c = cum(cid)
        tr.append(
            "<tr><td class='cid'>%s</td><td class='gp'>%s</td><td class='nm'>%s</td>"
            "<td class='%s'>%s</td><td class='dim'>%s</td><td class='dim'>%d</td>"
            "<td class='%s'>%s</td><td class='dim'>%s</td><td class='dim'>%d</td>"
            "<td class='dim'>%s%%</td><td class='dim'>%s%%</td>"
            "<td class='%s'>%s</td></tr>"
            % (cid, GROUP.get(cid, ""), SHORT.get(cid, a["name"]),
               cls(a["ret"]), sgn(a["ret"]), ("%.1f" % a["avg_inv"]), a["n"],
               cls(b["ret"]), sgn(b["ret"]), ("%.1f" % b["avg_inv"]), b["n"],
               ("%.1f" % a["turnover"]), ("%.1f" % b["turnover"]),
               cls(c), sgn(c)))
    table1 = """
<table class="tbl">
<thead><tr>
  <th rowspan="2"></th><th rowspan="2">组</th><th rowspan="2">配置</th>
  <th colspan="3" class="grp">样本内年（弱市 +6.22%）</th>
  <th colspan="3" class="grp">样本外年（牛市 +45.11%）</th>
  <th colspan="2">换手（倍）</th><th rowspan="2">两年累计<br>122bp</th>
</tr><tr>
  <th>收益</th><th>均仓</th><th>笔数</th>
  <th>收益</th><th>均仓</th><th>笔数</th>
  <th>IN</th><th>OOS</th>
</tr></thead>
<tbody>""" + "".join(tr) + "</tbody></table>"

    # ---------- 均线退出为什么不行：分桶图 ----------
    def bucket_rows(tag):
        r = diag.get(tag) or {}
        bs = r.get("buckets") or {}
        labs = ["≤−5%", "−5~−2%", "−2~0%", "0~2%", ">2%"]
        mx = max([v["n"] for v in bs.values()] or [1])
        out = []
        for lab in labs:
            v = bs.get(lab) or {"n": 0, "avg_pnl": None}
            w = max(2.0, v["n"] / mx * 100)
            below = lab in ("≤−5%", "−5~−2%", "−2~0%")
            cc = "b_below" if below else "b_above"
            out.append(
                "<div class='brow'><span class='bl'>%s</span>"
                "<span class='bt'><i class='%s' style='width:%.1f%%'></i>"
                "<em>n=%d</em></span>"
                "<span class='bv %s'>%s</span></div>"
                % (lab, cc, w, v["n"], cls(v["avg_pnl"] or 0),
                   sgn(v["avg_pnl"], 2) if v["avg_pnl"] is not None else "—"))
        return "".join(out)

    hist = ("<div class='two'>"
            "<div class='pan'><h4>样本内年 · %d 笔买入</h4>%s"
            "<div class='cap'>价格在 MA20 下方 <b>%.1f%%</b> · 中位 %+.2f%% · 10 分位 %+.2f%%</div></div>"
            "<div class='pan'><h4>样本外年 · %d 笔买入</h4>%s"
            "<div class='cap'>价格在 MA20 下方 <b>%.1f%%</b> · 中位 %+.2f%% · 10 分位 %+.2f%%</div></div>"
            "</div>"
            "<div class='lg2'><span><i class='b_below'></i>买在均线下方（占多数）</span>"
            "<span><i class='b_above'></i>买在均线上方</span>"
            "<span>右侧数字 = 该桶平均每笔收益</span></div>"
            % (diag.get("IN", {}).get("n", 0), bucket_rows("IN"),
               diag.get("IN", {}).get("below_pct", 0), diag.get("IN", {}).get("median", 0),
               diag.get("IN", {}).get("p10", 0),
               diag.get("OOS", {}).get("n", 0), bucket_rows("OOS"),
               diag.get("OOS", {}).get("below_pct", 0), diag.get("OOS", {}).get("median", 0),
               diag.get("OOS", {}).get("p10", 0)))

    # ---------- 2×2 消融 ----------
    def cell(cid):
        a, b = get(cid, "IN"), get(cid, "OOS")
        return ("<div class='mx'><div class='mh'>%s<span>%s</span></div>"
                "<div class='mr'><b>IN</b> <i class='%s'>%s</i></div>"
                "<div class='mr'><b>OOS</b> <i class='%s'>%s</i></div>"
                "<div class='mf'>两年 122bp <i class='%s'>%s</i></div></div>"
                % (cid, SHORT.get(cid, ""),
                   cls(a["ret"]), sgn(a["ret"]), cls(b["ret"]), sgn(b["ret"]),
                   cls(cum(cid)), sgn(cum(cid))))

    mtx = ("<div class='grid2'>"
           "<div class='gh'></div><div class='gh'>不改仓位</div><div class='gh'>满仓100%+分档</div>"
           "<div class='gl'>不加入场过滤</div>" + cell("D") + cell("R") +
           "<div class='gl'>加「开盘&gt;前一日MA20」</div>" + cell("Q") + cell("P") +
           "</div>"
           "<table class='tbl'><thead><tr><th>对比</th><th>OOS 差</th><th>IN 差</th>"
           "<th>读法</th></tr></thead><tbody>"
           "<tr><td class='nm'>满仓+分档 的贡献（R − D）</td>"
           "<td class='up'>%s</td><td class='dn'>%s</td>"
           "<td class='dim'>牛市加仓加对了，弱市加倍亏</td></tr>"
           "<tr><td class='nm'>入场过滤 的贡献（Q − D）</td>"
           "<td class='dn'>%s</td><td class='dn'>%s</td>"
           "<td class='dim'>两年都变差 → 过滤把好票也滤掉了</td></tr>"
           "<tr><td class='nm'>两者叠加（P − D）</td>"
           "<td class='up'>%s</td><td class='dn'>%s</td>"
           "<td class='dim'>牛市靠仓位，弱市靠空仓，与选股无关</td></tr>"
           "</tbody></table>"
           % (sgn(R_o["ret"] - D_o["ret"]), sgn(R_i["ret"] - D_i["ret"]),
              sgn(Q_o["ret"] - D_o["ret"]), sgn(Q_i["ret"] - D_i["ret"]),
              sgn(get("P", "OOS")["ret"] - D_o["ret"]),
              sgn(get("P", "IN")["ret"] - D_i["ret"])))

    # ---------- 122bp 表 ----------
    cr = []
    for cid in ORDER:
        a, b = get(cid, "IN", "122bp"), get(cid, "OOS", "122bp")
        if not a or not b:
            continue
        cr.append("<tr><td class='cid'>%s</td><td class='nm'>%s</td>"
                  "<td class='%s'>%s</td><td class='%s'>%s</td>"
                  "<td class='dim'>%s%%</td><td class='%s'>%s</td></tr>"
                  % (cid, SHORT.get(cid, a["name"]),
                     cls(a["ret"]), sgn(a["ret"]), cls(b["ret"]), sgn(b["ret"]),
                     ("%.2f" % a["cost_pct"]), cls(cum(cid)), sgn(cum(cid))))
    table4 = ("<table class='tbl'><thead><tr><th></th><th>配置</th><th>IN 122bp</th>"
              "<th>OOS 122bp</th><th>IN 成本损耗</th><th>两年累计</th></tr></thead><tbody>"
              + "".join(cr) + "</tbody></table>")

    html = TEMPLATE.replace("{{KPI}}", kpi).replace("{{T1}}", table1).replace(
        "{{HIST}}", hist).replace("{{MTX}}", mtx).replace("{{T4}}", table4)
    open(DST, "w", encoding="utf-8").write(html)
    print("wrote %s (%d bytes)" % (DST, os.path.getsize(DST)))


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>趋势退出 · 满仓 · 追高分档 —— 三条改动的裁决</title>
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
h4{font-size:13px;margin:0 0 10px;color:var(--dim);font-weight:500}
p{margin:10px 0}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0 8px}
.kpi{background:var(--pn);border:1px solid var(--bd);border-radius:10px;padding:14px 15px}
.kpi .k{color:var(--dim);font-size:12px}
.kpi .v{font-size:21px;font-weight:700;margin:4px 0 2px}
.kpi .s{color:var(--dim);font-size:11.5px;line-height:1.5}
.up{color:var(--up)}.dn{color:var(--dn)}.fl{color:var(--fl)}.dim{color:var(--dim)}
.tbl{width:100%;border-collapse:collapse;margin:12px 0 6px;font-size:13px}
.tbl th,.tbl td{padding:7px 8px;text-align:right;border-bottom:1px solid var(--bd)}
.tbl th{color:var(--dim);font-weight:500;font-size:12px;background:var(--pn2)}
.tbl th.grp{color:var(--tx);background:#1c2838}
.tbl td.cid{color:var(--dim);text-align:center}
.tbl td.gp{color:#5f6f88;text-align:center;font-size:11.5px}
.tbl td.nm{text-align:left;color:var(--tx)}
.tbl tbody tr:hover{background:#151e29}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.pan{background:var(--pn);border:1px solid var(--bd);border-radius:10px;padding:14px 16px}
.brow{display:grid;grid-template-columns:64px 1fr 62px;gap:10px;align-items:center;
padding:4px 0;font-size:12.5px}
.bl{color:var(--dim);font-variant-numeric:tabular-nums}
.bt{position:relative;background:#0b0f15;border-radius:4px;height:15px}
.bt i{display:block;height:100%;border-radius:4px}
.bt em{position:absolute;right:6px;top:0;line-height:15px;font-style:normal;
font-size:10.5px;color:#7d8ea6}
.b_below{background:linear-gradient(90deg,#3b7dd8,#4ea1ff)}
.b_above{background:linear-gradient(90deg,#7a5bd0,#a08bff)}
.bv{text-align:right;font-variant-numeric:tabular-nums}
.cap{color:var(--dim);font-size:11.5px;margin-top:8px}
.lg2{display:flex;gap:18px;color:var(--dim);font-size:11.5px;margin-top:10px;flex-wrap:wrap}
.lg2 i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
.grid2{display:grid;grid-template-columns:150px 1fr 1fr;gap:8px;margin:14px 0}
.gh{color:var(--dim);font-size:12px;text-align:center;padding:4px 0}
.gl{color:var(--dim);font-size:12px;align-self:center}
.mx{background:var(--pn);border:1px solid var(--bd);border-radius:9px;padding:10px 12px}
.mh{font-size:12.5px;color:var(--tx);margin-bottom:6px}
.mh span{display:block;color:#5f6f88;font-size:10.5px}
.mr{display:flex;justify-content:space-between;font-size:12.5px;
font-variant-numeric:tabular-nums}
.mr b{color:var(--dim);font-weight:500;font-size:11px}
.mf{margin-top:6px;padding-top:6px;border-top:1px solid var(--bd);
display:flex;justify-content:space-between;font-size:12px;color:var(--dim)}
.mf i{font-style:normal}
.note{background:#161f2b;border:1px solid #2b3a4f;border-left:3px solid var(--ac);
border-radius:8px;padding:12px 15px;font-size:13.5px;margin:16px 0}
.note.bad{border-left-color:#ff5b5b;background:#1e1618;border-color:#3a2629}
.note.warn{border-left-color:#c9a227;background:#1e1c15;border-color:#3a3524}
.note b{color:#fff}
ul{margin:8px 0 8px 20px;padding:0}
li{margin:5px 0;font-size:13.5px}
code{background:#1b2430;padding:1px 5px;border-radius:4px;font-size:12px;color:#9fd0ff}
.foot{color:#5f6f88;font-size:11.5px;margin-top:34px;padding-top:14px;
border-top:1px solid var(--bd)}
</style></head><body><div class="wrap">

<h1>趋势退出 · 满仓 · 追高按 regime 分档</h1>
<div class="sub">老田 2026-11-10 三条改动 · 同一套三买引擎 · 两个各一年的窗口并排 ·
30bp / 122bp 两档成本 · T+1 开盘成交</div>

{{KPI}}

<div class="note bad">
<b>一句话：三条改动一条都没能站住，但这一轮问出了一个更根本的问题——</b>
这套三买<b>不是趋势策略，是超跌反抽</b>：买入时 <b>72.9%（IN）/ 61.2%（OOS）</b> 的价格
已经在个股 MA20 <b>下方</b>。所以「跌破 20 日均线才走」在这套入场里等于「买入次日就走」，
均持从 16.7 天掉到 3.8 天、换手从 6.9 倍涨到 16 倍，122bp 成本下两年 <b>−18.44%</b>。
</div>

<h2>一、三条改动逐条裁决</h2>
<p><b>T1 趋势退出</b>＝收盘跌破个股 MA20 才走，不设到期、不设移动止损；
<b>T2 满仓</b>＝cap 100/100/100、10% 槽 × 10 槽；
<b>T3 分档</b>＝追高门槛 high 10% / mid 5% / low 2%（原全局 3%）。
每个组合都在 <code>20251013→20261012</code>（弱市）与 <code>20241008→20251010</code>（牛市）各跑一年。</p>
{{T1}}

<h2>二、为什么「跌破 20 日均线才走」不work</h2>
<p>因为它预设了「买入时价格在均线上方、趋势已成立」。先量这个前提，再谈规则好坏。
下图把每一笔三买买入按「买入价相对个股 MA20」分五桶，右侧是该桶的<b>平均每笔收益</b>：</p>
{{HIST}}
<div class="note warn">
<b>数据把假设打翻了两次：</b>① 超过六成的买入发生在均线<b>下方</b>，中位 −2.1%（IN）；
② 均线下方的买入<b>不比上方差</b>——样本外年最深的那一桶（≤−5%）平均每笔 <b>+9.41%</b>，
是五个桶里最好的。所以「加均线过滤只买趋势票」不是修正，是<b>把最赚钱的一类剔掉</b>：
Q 组的笔均超额从 D 的 +85.7bp 掉到 −118.2bp（IN）、−288.3bp（OOS）。
</div>

<h2>三、2×2 消融：谁在挣钱</h2>
<p>三条改动里「满仓」和「分档」经常一起出现，容易把功劳记错。拆成 2×2 后各格子的读数：</p>
{{MTX}}
<div class="note">
<b>读法：</b>R 在牛市把收益从 +14.56% 拉到 +31.10%，靠的是平均仓位从 42% 提到 <b>88%</b>，
而「满仓等价」（收益 ÷ 平均仓位）只从 34.67% 走到 35.34%——<b>基本没动</b>。
意思是<b>没有多赚一分钱选股的钱，只是把同样的东西买得更多</b>。
到了弱市年，同一件事立刻反噬：IN 从 +11.45% 变成 −4.71%。
</div>

<h2>四、真实成本口径（122bp 往返）</h2>
<p>所有改动都在增加换手：D 是 6.9 倍，MA20 退出 16 倍，MA10 版 27 倍。30bp 的摩擦看不出代价，
122bp 才是这 1000 万真正会付的钱：</p>
{{T4}}

<h2>五、结论与下一步</h2>
<div class="note bad">
<b>① 趋势退出：否。</b>前提不成立（六成买入在均线下方），规则一上就把持有期砍到 1/4，换手翻 2~4 倍。
MA10 / MA20 / MA60 / 武装版 / +兜底，五种写法在 122bp 下全部为负（−10% 到 −36%）。<br>
<b>② 仓位升到 100%：能做，但不产生收益。</b>cap 放开后均仓 42%→88%，满仓等价基本不动，
说明收益全由 beta 决定、选股贡献为零。它的真实用途是<b>让你在牛市跟住指数</b>，不是赚超额。<br>
<b>③ 追高按 regime 分档：牛市放大成交、弱市放大亏损。</b>单独叠加（R）成交 85→133 笔、
均仓 42%→88%，样本外从 +14.56% 涨到 +31.10%，但<b>两年 122bp 只有 +0.18%</b>（旧方案 +12.77%）；
若再叠在均线退出上（I）直接 <b>−32.25%</b>。它放大的是低质量成交，不是好机会。<br>
<b>④ 真正要修的是信号本身。</b>12 组配置里，笔均超额（唯一剔掉仓位与大盘的量）<b>全部为负</b>。
在这套三买上继续调退出与仓位，只是在「多买一点 beta / 少买一点 beta」之间来回挑。
</div>

<p>下一步该动的两处，都在入场侧、都不需要重扫行情：</p>
<ul>
<li><b>修 <code>score_3buy</code> 的位置分量纲 bug</b>：它拿归一化 <code>adj_close</code> 比中枢原始价 <code>zg</code>，
位置分恒满分（40% 权重等于常数），所以「最看好的 5 只」实际只按量能+背驰排序。
上面那张分桶图已经证明「位置」是有信息的——只是现在没被用上。</li>
<li><b>按「反抽」而不是「趋势」重设退出</b>：既然六成买入是超跌反抽，退出就该是
「固定目标位止盈 + 时间衰减」，而不是均线跟随。这条要配着第一项一起验证，否则又会在一堆常数里挑。</li>
</ul>

<div class="foot">
口径：缠论三买（<code>buy_filter=3</code>）· 全市场 universe · 涨停开盘买不进 ·
T+1 开盘买卖 · 单边成本 0.15% / 0.61%（往返 30bp / 122bp）· slot 8% 或 10% ·
regime 帽 80/50/30 或 100/100/100。<br>
样本内年 2025-10-13→2026-10-12（242 日）、样本外年 2024-10-08→2025-10-10（246 日），
两者零重叠。等权全 A 基准：+6.22%（回撤 −20.59%）/ +45.11%（回撤 −16.75%）。<br>
产物：<code>reports/bt10m_trend_1y.json</code> · 脚本 <code>bt10m_trend.py</code> ·
<code>bt10m_ma_diag.py</code> · <code>bt10m_trend_page.py</code>
</div>

</div></body></html>"""

if __name__ == "__main__":
    main()
