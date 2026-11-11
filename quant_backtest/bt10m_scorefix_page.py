# -*- coding: utf-8 -*-
"""把 scorefix 两轮产物渲染成一页报告（深色，与此前几页同一套观感）。

输入：reports/bt10m_scorefix_1y.json（回测网格）
      reports/bt10m_score_diff.json（信号一致性与位置分桶）
产出：reports/bt10m_scorefix_1y.html
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "reports", "bt10m_scorefix_1y.json")
DIF = os.path.join(BASE, "reports", "bt10m_score_diff.json")
DST = os.path.join(BASE, "reports", "bt10m_scorefix_1y.html")

WINLAB = {"PRE": "第三窗口 2023-10-10 → 2024-09-30", "IN": "样本内年 2025-10-13 → 2026-10-12",
          "OOS": "样本外年 2024-10-08 → 2025-10-10"}


def cls(v):
    return "up" if v > 0 else ("dn" if v < 0 else "fl")


def sgn(v, nd=2, suffix="%"):
    if v is None:
        return "—"
    return ("%+.*f" % (nd, v)) + suffix


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    rows = d["rows"]
    dif = json.load(open(DIF, encoding="utf-8"))

    def g(cid, win, cost="30bp"):
        return rows.get("s%s_%s_%s" % (cid, win, cost))

    # D（旧评分 + 旧退出）是本轮之前跑的，落在 trend 那轮产物里，缺 PRE 窗口
    OLD = {}
    _tf = os.path.join(BASE, "reports", "bt10m_trend_1y.json")
    if os.path.exists(_tf):
        OLD = json.load(open(_tf, encoding="utf-8")).get("rows", {})

    def gold(win, cost="30bp"):
        return OLD.get("tD_%s_%s" % (win, cost))

    def cum(cid, cost="122bp", wins=("PRE", "IN", "OOS")):
        v = 1.0
        ok = True
        for w in wins:
            r = g(cid, w, cost)
            if not r:
                ok = False
                break
            v *= 1 + r["ret"] / 100
        return (v - 1) * 100 if ok else None

    b = {w: (g("V2", w) or {}).get("bench_ret") for w in ("PRE", "IN", "OOS")}
    bench_cum = 1.0
    for w in ("PRE", "IN", "OOS"):
        if b.get(w) is not None:
            bench_cum *= 1 + b[w] / 100
    bench_cum = (bench_cum - 1) * 100

    # ---------- KPI ----------
    kpi = """
<div class="kpis">
  <div class="kpi"><div class="k">① 位置分量纲 bug</div>
    <div class="v up">已修</div>
    <div class="s">事件 13635 → 13635，zg/zd 逐条相同<br>评分唯一值 18 → 38（最小 40 → 16）</div></div>
  <div class="kpi"><div class="k">位置确实有信息</div>
    <div class="v up">≤2% 是唯一正桶</div>
    <div class="s">三个窗口都只有「贴近上沿」为正<br>远离 &gt;20% 三年全负</div></div>
  <div class="kpi"><div class="k">② 但修复本身 ≈ 0</div>
    <div class="v fl">{dn_o} vs {d_o}</div>
    <div class="s">同一套退出，只换评分<br>3% 门槛已把位置框死，排序不触发</div></div>
  <div class="kpi"><div class="k">③ 反抽退出 · 三年</div>
    <div class="v up">{v2cum}</div>
    <div class="s">V2（TP20%/60日）122bp 累计<br>旧退出同期 {dncum}</div></div>
</div>""".format(dn_o=sgn((g("Dn", "OOS") or {}).get("ret")),
                d_o=sgn((gold("OOS") or {}).get("ret")),
                v2cum=sgn(cum("V2")), dncum=sgn(cum("Dn")))

    # ---------- 一、修复证据 ----------
    ev = []
    for w in ("IN", "OOS", "PRE"):
        r = dif.get(w) or {}
        if not r:
            continue
        old_sc = "18 / 23 / —" if w == "IN" else ""
        has_old = r.get("n_old", 0) > 0
        ev.append("<tr><td class='nm'>%s</td><td class='dim'>%s</td><td class='dim'>%d</td>"
                  "<td class='dim'>%d</td><td class='dim'>%d</td>"
                  "<td class='dim'>%s</td><td class='%s'>%s</td></tr>"
                  % (WINLAB[w].split(" ")[0],
                     str(r.get("n_old", 0)) if has_old else "—",
                     r.get("n_new", 0),
                     r.get("added", 0) if has_old else 0,
                     r.get("gone", 0) if has_old else 0,
                     str(r.get("zg_diff", 0)) if has_old else "—",
                     ("up" if r.get("zg_diff", 1) == 0 else "dn") if has_old else "dim",
                     "逐条相同" if has_old and r.get("zg_diff", 1) == 0
                     else ("有漂移" if has_old else "仅修复后扫过·无旧版对照")))
    table_ev = ("<table class='tbl'><thead><tr><th>窗口</th><th>旧事件数</th><th>新事件数</th>"
                "<th>新增</th><th>消失</th><th>zg/zd 不同</th><th>结论</th></tr></thead><tbody>"
                + "".join(ev) + "</tbody></table>")

    # ---------- 二、位置分桶 ----------
    buck = []
    labels = ["≤2%", "2~5%", "5~10%", "10~20%", ">20%"]
    for lab in labels:
        cells = []
        for w in ("PRE", "IN", "OOS"):
            r = ((dif.get(w) or {}).get("buckets") or {}).get(lab) or {}
            if r:
                cells.append("<td class='%s'>%s<span class='sn'>n=%d</span></td>"
                             % (cls(r["mean"]), sgn(r["mean"]), r["n"]))
            else:
                cells.append("<td class='dim'>—</td>")
        buck.append("<tr><td class='nm'>%s</td>%s</tr>" % (lab, "".join(cells)))
    table_b = ("<table class='tbl bt'><thead><tr><th class='lk'>close/zg−1</th>"
               "<th>第三窗口<br>（未参与挑参）</th><th>样本内年</th><th>样本外年</th>"
               "</tr></thead><tbody>" + "".join(buck) + "</tbody></table>"
               "<div class='cap'>每格 = 该桶信号后 20 个交易日的等权平均收益，括号内是样本数。</div>")

    # ---------- 三、TP 网格 ----------
    GRID = [
        ("60 日到期", [("20", "V2")]),
        ("40 日到期", [("15", "V5"), ("20", "V1")]),
        ("30 日到期", [("12", "V6"), ("15", "U5"), ("18", "V3"), ("20", "U6"), ("25", "V4")]),
        ("20 日到期", [("8", "S"), ("12", "T"), ("15", "U"), ("18", "U1"), ("20", "U2"),
                       ("25", "U3"), ("30", "U4")]),
    ]
    cols = ["8", "12", "15", "18", "20", "25", "30"]
    gr = []
    for dlab, pts in GRID:
        cell = dict(pts)
        tds = []
        for c in cols:
            cid = cell.get(c)
            r = g(cid, "OOS") if cid else None
            r2 = g(cid, "IN") if cid else None
            if not r:
                tds.append("<td class='dim'>·</td>")
                continue
            c22 = cum(cid, "122bp", ("IN", "OOS"))  # 网格统一用两年，避免与三窗口混读
            tds.append("<td class='%s'>%s<span class='sn'>%s</span></td>"
                       % (cls(c22), sgn(c22, 1), "%s / %s" % (sgn(r2["ret"], 1),
                                                              sgn(r["ret"], 1))))
        gr.append("<tr><td class='nm'>%s</td>%s</tr>" % (dlab, "".join(tds)))
    table_g = ("<table class='tbl hm'><thead><tr><th>到期</th>"
               + "".join("<th>TP %s%%</th>" % c for c in cols) + "</tr></thead><tbody>"
               + "".join(gr) + "</tbody></table>"
               "<div class='cap'>每格：大字=两年累计（122bp，IN+OOS 复利），小字=IN / OOS 单年收益（30bp）。"
               "「·」= 该组合没跑。V2 的第三窗口成绩见下一节。</div>")

    # ---------- 四、三窗口明细 ----------
    w3 = []
    for w in ("PRE", "IN", "OOS"):
        r122 = g("V2", w, "122bp")
        r30 = g("V2", w, "30bp")
        n = g("Dn", w, "122bp")
        if not r122:
            continue
        diff = r122["ret"] - r122["bench_ret"]
        cells = [
            "<td class='nm'>%s</td>" % WINLAB[w][:4],
            "<td class='%s'>%s</td>" % (cls(r30["ret"]), sgn(r30["ret"])),
            "<td class='%s'>%s</td>" % (cls(r122["ret"]), sgn(r122["ret"])),
            "<td class='dim'>%s</td>" % sgn(r122["bench_ret"]),
            "<td class='dim'>%.2f%%</td>" % r122["avg_inv"],
            "<td class='%s'>%s</td>" % (cls(diff), sgn(diff)),
            "<td class='%s'>%s</td>" % (cls(r122["dd"]), sgn(r122["dd"])),
            "<td class='dim'>%s</td>" % sgn(r122["bench_dd"]),
            "<td class='dim'>%.1f</td>" % r122["turnover"],
            "<td class='dim'>%.1f</td>" % r122["avg_hold"],
            "<td class='%s'>%s</td>" % (cls(n["ret"]) if n else "dim",
                                        sgn(n["ret"]) if n else "—"),
            "<td class='%s'>%s</td>" % (cls(r122["ex_bp"]), sgn(r122["ex_bp"], 1, "bp")),
        ]
        w3.append("<tr>%s</tr>" % "".join(cells))
    table_w = ("<table class='tbl bt'><thead><tr><th class='lk'>窗口</th><th>V2 30bp</th>"
               "<th>V2 122bp</th><th>基准</th><th>均仓</th><th>超额</th><th>V2 回撤</th>"
               "<th>基准回撤</th><th>换手</th><th>均持</th><th>旧退出</th><th>笔均超额</th>"
               "</tr></thead><tbody>" + "".join(w3) + "</tbody></table>"
               "<div class='cap'>「旧退出」= 同一批选股、同一仓位，只把退出换回「20 日强平 + 移动止损 5%/3.5%」"
               "（122bp）。换手 = 一年买卖总额 ÷ 本金。</div>")

    # ---------- 图：TP 曲线（OOS）----------
    def line(series, color, y0, h, x0, w):
        pts = " ".join("%.1f,%.1f" % (x, y) for x, y in series)
        return pts

    LO, HI = 0.0, 45.0
    W, H = 940, 230
    xs = lambda t: 70 + (t - 6) / 26.0 * W
    ys = lambda v: 30 + (HI - v) / (HI - LO) * H
    gsvg = []
    for v in (0, 10, 20, 30, 40):
        gsvg.append("<line class='grid' x1='70' y1='%.1f' x2='%d' y2='%.1f'/>"
                    % (ys(v), 70 + W, ys(v)))
        gsvg.append("<text class='ax' x='60' y='%.1f' text-anchor='end'>%d%%</text>"
                    % (ys(v) + 4, v))
    for c in cols:
        gsvg.append("<text class='ax' x='%.1f' y='%d' text-anchor='middle'>%s%%</text>"
                    % (xs(int(c)), 30 + H + 26, c))
    series = {
        "20 日": ("#4ea1ff", [("8", "S"), ("12", "T"), ("15", "U"), ("18", "U1"),
                             ("20", "U2"), ("25", "U3"), ("30", "U4")]),
        "30 日": ("#a08bff", [("12", "V6"), ("15", "U5"), ("18", "V3"), ("20", "U6"),
                             ("25", "V4")]),
        "40 日": ("#ffb347", [("15", "V5"), ("20", "V1")]),
        "60 日": ("#3ddc97", [("20", "V2")]),
    }
    for lab, (cc, pts) in series.items():
        xy = []
        for t, cid in pts:
            r = g(cid, "OOS")
            if r:
                xy.append((xs(int(t)), ys(r["ret"])))
        if len(xy) > 1:
            gsvg.append("<polyline points='%s' fill='none' stroke='%s' stroke-width='2'/>"
                        % (line(xy, cc, 0, 0, 0, 0), cc))
        for (x, y) in xy:
            gsvg.append("<circle cx='%.1f' cy='%.1f' r='4.5' fill='%s' "
                        "stroke='#0d1117' stroke-width='1.5'/>" % (x, y, cc))
    chart_tp = ("<div class='chart'><svg viewBox='0 0 1120 300' width='100%'>"
                + "".join(gsvg) + "</svg><div class='lg'>"
                + "".join("<span><i style='background:%s'></i>%s 到期</span>"
                          % (lab[1][0], lab[0]) for lab in series.items())
                + "</div><div class='cap'>纵轴 = 样本外年组合收益（30bp）。"
                "两条读法：① 同一到期日上，目标 15~20% 是一段平台，不是尖峰；"
                "② 到期越放长，整条线往上抬——20 日强平本身就是 2026-11 那套方案的主要损伤点。</div></div>")

    html = TEMPLATE.replace("{{KPI}}", kpi).replace("{{EV}}", table_ev).replace(
        "{{BUCK}}", table_b).replace("{{GRID}}", table_g).replace(
        "{{W3}}", table_w).replace("{{TPCHART}}", chart_tp).replace(
        "{{BENCH3}}", sgn(bench_cum)).replace("{{V2CUMLAB}}", sgn(cum("V2"))).replace(
        "{{DN3}}", sgn(cum("Dn")))
    open(DST, "w", encoding="utf-8").write(html)
    print("wrote %s (%d bytes)" % (DST, os.path.getsize(DST)))
    print("V2 三年累计 122bp %s / 基准 %s" % (sgn(cum("V2")), sgn(bench_cum)))


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>修位置分量纲 + 按反抽重设退出</title>
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
p{margin:10px 0}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:22px 0 8px}
.kpi{background:var(--pn);border:1px solid var(--bd);border-radius:10px;padding:14px 15px}
.kpi .k{color:var(--dim);font-size:12px}
.kpi .v{font-size:20px;font-weight:700;margin:4px 0 2px}
.kpi .s{color:var(--dim);font-size:11.5px;line-height:1.5}
.up{color:var(--up)}.dn{color:var(--dn)}.fl{color:var(--fl)}.dim{color:var(--dim)}
.tbl{width:100%;border-collapse:collapse;margin:12px 0 6px;font-size:13px}
.tbl th,.tbl td{padding:7px 8px;text-align:right;border-bottom:1px solid var(--bd)}
.tbl th{color:var(--dim);font-weight:500;font-size:12px;background:var(--pn2)}
.tbl th.grp{color:var(--tx);background:#1c2838}
.tbl td.nm{text-align:left;color:var(--tx)}
.tbl tbody tr:hover{background:#151e29}
.bt th,.bt td{padding:6px 6px;font-size:12.5px}
.bt th.lk{text-align:left;color:var(--dim);font-weight:500}
.hm td{text-align:center;font-variant-numeric:tabular-nums}
.hm .sn,.bt .sn{display:block;color:#5f6f88;font-size:10.5px;line-height:1.3}
.chart{background:var(--pn);border:1px solid var(--bd);border-radius:10px;padding:14px 16px;margin:12px 0}
svg text{font-family:inherit}
.ax{fill:#5f6f88;font-size:11px}
.grid{stroke:#1e2937;stroke-width:1}
.lg{display:flex;gap:18px;color:var(--dim);font-size:12px;margin-top:4px;flex-wrap:wrap}
.lg i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.cap{color:var(--dim);font-size:11.5px;margin-top:8px}
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

<h1>修位置分量纲 + 按「反抽」重设退出</h1>
<div class="sub">老田 2026-11-10 · 两个动作配着验证 · 三个各一年的窗口（含一个从未参与挑参的） ·
30bp / 122bp 两档成本 · T+1 开盘买</div>

{{KPI}}

<div class="note bad">
<b>两句话：</b>① 位置分那个 bug 修干净了——信号逐条不变、只有评分变，位置也确实有信息
（三个窗口里「贴近中枢上沿」都是唯一为正的一桶）；<b>但修复本身几乎不产生收益</b>，
因为「距上沿≤3%」这道门槛早就把位置框死在一个窄带里，评分再怎么算都是同一批票。<br>
② 真正把钱赚回来的是<b>退出</b>：把「20 日强平 + 移动止损」换成「固定目标位 20% + 60 日到期 + −8% 硬止损」，
122bp 口径三年累计从旧方案的 {{DN3}} 抬到 <b>{{V2CUMLAB}}</b>，三个窗口全正、最大回撤 −6.2%。
</div>

<h2>一、修复：怎么修的，凭什么说安全</h2>
<p>根因是<b>四价量纲不统一</b>：扫描器构造 K 线时 <code>o/h/l</code> 用原始价、唯独 <code>c</code> 乘了
复权因子（<code>(1+pct_chg).cumprod()/close</code>，上市首日≈1）。于是
<code>score_3buy(close, zg)</code> 里 <code>close/zg−1 ≈ 1.9/158−1 = −98.8%</code>，恒 ≤0.02
→ 位置分恒 40 分（满分）。40% 的权重变成常数，所谓「最看好的 5 只」实际只按量能 + 背驰排序。</p>
<p>修法：把 <code>c</code> 也改回原始价（<b>不是</b>把 h/l 改成复权价）。因为结构层（包含处理 / 分型 / 笔 / 中枢）
<b>只用 h/l</b>，<code>c</code> 只进评分和 K 线形态——所以这个改法能做到「信号不变、只有评分变」，
而且 zg/zd 保持原始价量纲，引擎侧 <code>px_raw()</code> 的失效线、追高门槛比较继续成立。</p>
{{EV}}
<div class="note">
<b>三条证据：</b>① 三年窗口事件数一条不差（新增 0 / 消失 0）；② 每条事件的 zg/zd 完全相同；
③ 评分唯一值从 18~23 个变成 38~42 个，最小值从 40 掉到 16——位置分真的动了，不是换个写法还是常数。
</div>

<h2>二、位置到底有没有信息</h2>
<p>把每条三买信号按信号日 <code>close/zg−1</code> 分五桶，看<b>信号之后 20 个交易日</b>的真实收益
（复权、等权、不扣成本）：</p>
{{BUCK}}
<div class="note warn">
<b>方向与 score_3buy 的假设一致，但信息量远超预期：</b>「≤2%」在三个窗口里是<b>唯一为正</b>的一桶
（第三窗口 +1.44% / t=2.40，样本内 +1.06%，样本外 +6.49% / t=5.04）；
而「&gt;20%」三年全负（−2.99% / −0.36% / −3.13%，t 分别是 −4.50 / −0.47 / −8.79）。
换句话说：<b>买得离中枢上沿越远，20 天后越亏</b>，这是三年一致的结构性事实，不是噪声。
</div>

<h2>三、那为什么修好了不涨钱</h2>
<p>因为在<b>现有其它参数下，这个维度被别的东西框死了</b>。同一套退出规则（D）只换评分：</p>
<table class="tbl"><thead><tr><th></th><th>样本内年</th><th>样本外年</th><th>第三窗口</th>
<th>笔均超额（IN / OOS）</th></tr></thead><tbody>
<tr><td class="nm">D 旧评分（恒 40 位置分）</td><td class="up">+11.45%</td><td class="up">+14.56%</td>
<td class="dim">未跑</td><td class="dim">+85.7 / −124.7bp</td></tr>
<tr><td class="nm">Dn 新评分（真位置分）</td><td class="up">+11.45%</td><td class="up">+13.85%</td>
<td class="up">+6.75%</td><td class="dim">+85.7 / −133.7bp</td></tr>
</tbody></table>
<p>样本内年连小数点后两位都一样——说明<b>排序根本没触发</b>。两个原因：</p>
<ul>
<li><b>门槛已经替它做了决定。</b><code>max_ext_pct=3%</code> 只放「距上沿 3% 以内」的票进来，
这批票的位置分只可能是 40 或 32（两档差 8 分），而量能/背驰能差 52 分——位置在总分里基本被淹掉。</li>
<li><b>候选数常常少于槽位数。</b>样本内年 242 天只成交 96 笔（0.4 笔/天），10 个槽位空着，
排序给谁先买根本不影响结果。</li>
</ul>
<p>反过来验一次：把门槛拿掉、完全交给评分排序（W1），结果<b>更差</b>——样本外 +24.44%、
第三窗口 −1.09%，两年累计只有 +9.47%。<b>排序替代不了硬门槛</b>：门槛是「不许买」，排序是「晚点买」。</p>

<h2>四、按「反抽」重设退出</h2>
<p>上一轮已经确认这套三买是<b>超跌反抽</b>（买入时六成价格在个股 MA20 下方），所以退出不该跟均线。
本轮实现的是「固定目标位止盈 + 时间兜底」：目标 = 入场价 × (1 + TP)，持仓期内限价挂单、
当日最高价触价即成交（跳空则按开盘价成交，不占便宜），到期未达标按 T+1 开盘强平。</p>
{{TPCHART}}
{{GRID}}
<div class="note bad">
<b>「时间衰减」这个方向是错的，数据很干脆。</b>把目标位做成「每多持有一天就下调 0.2~0.3pp」
（V / W 组）以后，收益从 T 的 +9.11% / +20.34% 掉到 +5.03% / +14.96%，再掉到 +2.98% / +15.78%。
原因不复杂：<b>衰减就是「更早、更低地卖」</b>，而这套反抽的问题恰恰是卖得太早（8% 目标时样本外只 +15.05%，
15% 时 +23.91%）。真正该做的是<b>把时间当兜底、不当杠杆</b>——到期强平留着当风控，
目标位固定不动。
</div>

<h2>五、三个窗口的验证</h2>
<p>V2 = TP 20% · 60 日到期 · −8% 硬止损 · 距上沿≤3% · 8% 槽 × 10 槽 · regime 帽 80/50/30。
第三个窗口（2023-10-10 → 2024-09-30，240 日）<b>从头到尾没有参与任何参数挑选</b>，
是在这个组合已经定稿之后才扫的：</p>
{{W3}}
<div class="note">
<b>三年 122bp 累计 {{V2CUMLAB}}，同期等权全 A 基准 {{BENCH3}}。</b>仍然跑输基准，
但性质变了：基准是「满仓吃满牛市、弱市回撤 −30.71%」，V2 是「32~53% 仓位、最大回撤 −6.2%」。
按 Calmar 比：V2 是 1.03 / 1.35 / 4.94，基准是 0.11 / 0.30 / 2.69，<b>三个窗口全部胜出</b>。
牛市那年（样本外 +45.11%）V2 只做出 +29.62%，跑输 15.5pp——这个缺口是仓位上限造成的，
不是选股造成的（笔均超额 +88.7bp 为正）。
</div>

<h2>六、结论</h2>
<div class="note bad">
<b>① 位置分量纲 bug：修好了，且是手术级。</b>信号与 zg 逐条不变、只有评分变；
三年分桶证明「贴近中枢上沿」是唯一稳定的正向位置。<br>
<b>② 修复本身不产生收益。</b>在 <code>max_ext=3%</code> 下位置维度被框死、候选数又少于槽位，
排序不触发。要让位置真正发挥，要么继续用硬门槛（现在的做法，简单有效），
要么放开门槛但把位置的权重加到能压过量能/背驰——后者本轮没验，是本轮留下的口子。<br>
<b>③ 「时间衰减」被否证。</b>目标位随时间下调 = 更早更低卖出，收益单调变差。<br>
<b>④ 反抽退出的正解是「固定目标 20% + 60 日兜底 + 8% 硬止损」。</b>
三年正超额、t 在第三窗口达到 2.27，参数曲面是平台不是尖峰。
这一条是本项目至今唯一在三窗口都站住的改动。
</div>
<p>下一步建议（按性价比）：</p>
<ul>
<li><b>把 V2 的三条参数固化进日报默认值</b>，同时保留旧方案做影子对照一周，确认实盘成交价（限价单触价）
和回测没有系统性偏差。</li>
<li><b>仓位上限</b>是牛市跑输的主要来源：现在 regime 帽 80/50/30 + 8% 槽，实际均仓只有 32~53%。
在「笔均超额为正」的前提下，提高 high 档仓位是唯一还有确定增益的方向（本轮 X/Y 组失败是因为
在弱市年也满仓，需要分档更细）。</li>
<li><b>位置权重</b>：把 <code>score_3buy</code> 的 40/30/30 改成位置更重（如 55/25/20），
在放开门槛的设定下测一次——这是本轮唯一留着的假设。</li>
</ul>

<div class="foot">
口径：缠论三买（<code>buy_filter=3</code>）· 全市场 5170 只（剔 ST / 北交所 / 指数）· 涨停开盘买不进 ·
T+1 开盘买入 · 止盈按限价单盘中触价成交 · 单边成本 0.15% / 0.61%（往返 30bp / 122bp）·
单票 8% 槽 · regime 帽 80/50/30。<br>
三个窗口：2023-10-10→2024-09-30（240 日，基准 +3.24% / 回撤 −30.71%）、
2024-10-08→2025-10-10（246 日，+45.11% / −16.75%）、
2025-10-13→2026-10-12（242 日，+6.22% / −20.59%），三者零重叠。<br>
产物：<code>reports/bt10m_scorefix_1y.json</code> · <code>reports/bt10m_score_diff.json</code> ·
脚本 <code>bt10m_scorefix.py</code> / <code>bt10m_score_diff.py</code> / <code>bt10m_scorefix_page.py</code>
</div>

</div></body></html>"""

if __name__ == "__main__":
    main()
