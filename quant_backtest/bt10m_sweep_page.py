"""降换手实验报告页（读 reports/bt10m_sweep*.json → reports/bt10m_sweep.html）。

用法：python bt10m_sweep_page.py
"""
import json
import math
import os
import sys
from datetime import datetime

import ui_kit as U

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(BASE, "reports")
OUT = os.path.join(REP, "bt10m_sweep.html")
T = U.TOKENS

# 分组展示顺序 + 一句话导读
GROUP_ORDER = [
    ("基准", "修好失效线量纲后的现状口径"),
    ("A 放松失效线", "只动那条「跌破中枢上沿就走」的线"),
    ("B 最短持有期", "买完不许立刻卖（此前该参数在缠论分支空转）"),
    ("C 组合", "失效线 + 持有期一起动"),
    ("D 纯到期", "彻底不要失效线，只按天数走"),
    ("E 止损替代", "用移动止损替代失效线"),
    ("F 追高门槛", "只在贴近中枢上沿时买"),
    ("G 参数微调", "门槛与到期日的边界搜索"),
]


def pct(v, d=2, sign=True):
    if v is None:
        return "—"
    return ("%+.*f%%" if sign else "%.*f%%") % (d, v)


def cls(v):
    if v is None:
        return ""
    return "up" if v > 0 else ("dn" if v < 0 else "")


def money(v):
    return format(int(round(v)), ",")


# ── 图 1：换手—收益地图（对数换手轴）────────────────────────────────
def scatter_svg(vars_, old, w=1180, h=330):
    pts = [(r["turnover_x"], r["total_return_pct"], r) for r in vars_]
    xs = [p[0] for p in pts] + [old["turnover_x"]]
    ys = [p[1] for p in pts] + [old["total_return_pct"], 0]
    xmax = max(xs) * 1.15
    lo, hi = min(ys) - 3, max(ys) + 3
    L, R, Tp, B = 62, 130, 16, 34
    iw, ih = w - L - R, h - Tp - B

    def X(v):
        return L + iw * math.log1p(max(v, 0)) / math.log1p(xmax)

    def Y(v):
        return Tp + ih * (1 - (v - lo) / (hi - lo))

    o = ['<svg viewBox="0 0 %d %d" class="chart" preserveAspectRatio="none">' % (w, h)]
    # 目标区（换手<10 且 收益>0）
    o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" opacity=".07"/>'
             % (L, Y(hi), X(10) - L, Y(0) - Y(hi), T["up"]))
    for v in range(int(lo // 5) * 5, int(hi) + 1, 5):
        y = Y(v)
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="gl%s"/>'
                 % (L, y, L + iw, y, " base" if v == 0 else ""))
        o.append('<text x="%d" y="%.1f" class="ax" text-anchor="end">%+d%%</text>'
                 % (L - 8, y + 3.5, v))
    for v in (1, 3, 10, 30, 60):
        if v > xmax:
            continue
        x = X(v)
        o.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" class="gl%s"/>'
                 % (x, Tp, x, Tp + ih, " tgt" if v == 10 else ""))
        o.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">%d×</text>'
                 % (x, h - 16, v))
    o.append('<text x="%d" y="%d" class="ax" text-anchor="middle">换手（倍本金·对数轴）</text>'
             % (L + iw // 2, h - 2))
    o.append('<text x="%.1f" y="%.1f" class="lb ob" fill="%s" text-anchor="start">'
             '换手 10 倍线</text>' % (X(10) + 6, Tp + 13, T["warn"]))
    # 旧口径
    o.append('<circle cx="%.1f" cy="%.1f" r="6" fill="none" stroke="%s" stroke-width="1.6" '
             'stroke-dasharray="3 2"/>' % (X(old["turnover_x"]), Y(old["total_return_pct"]),
                                          T["bad"]))
    o.append('<text x="%.1f" y="%.1f" class="lb ob" fill="%s" text-anchor="end">'
             '旧口径 −21.90%%</text>'
             % (X(old["turnover_x"]) - 10, Y(old["total_return_pct"]) + 4, T["bad"]))
    # 变体点
    for turn, ret, r in pts:
        hit = turn < 10 and ret > 0
        col = T["up"] if hit else (T["mu"] if ret < 0 else T["ac"])
        rad = 5.2 if hit else 4
        o.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" opacity="%s"/>'
                 % (X(turn), Y(ret), rad, col, ".95" if hit else ".75"))
    # 只标最优与两个代表点，别的交给表格；多点标注在 2~13 倍这一簇里必然糊在一起
    lab = {"ext03_m20_tr": ("最优 +11.45%", -10, -8, "end"),
           "no3b_m20": ("关失效线+20日强平", 10, 4, "start"),
           "ext03_tr53": ("低换手版 +6.35%", -10, -8, "end")}
    for turn, ret, r in pts:
        if r["job"] in lab:
            text, tx, dy, anc = lab[r["job"]]
            o.append('<text x="%.1f" y="%.1f" class="lb ob" fill="%s" text-anchor="%s">%s</text>'
                     % (X(turn) + tx, Y(ret) + dy, T["tx"], anc, text))
    o.append("</svg>")
    return "".join(o)


# ── 图 2：五级台阶（口径 → 收益）────────────────────────────────────
def steps_svg(steps, w=1180, h=250):
    n = len(steps)
    L, R, Tp, B = 54, 12, 26, 52
    iw, ih = w - L - R, h - Tp - B
    lo, hi = min(s["v"] for s in steps) - 4, max(s["v"] for s in steps) + 6
    bw = iw / n * 0.56
    step = iw / n

    def Y(v):
        return Tp + ih * (1 - (v - lo) / (hi - lo))

    o = ['<svg viewBox="0 0 %d %d" class="chart" preserveAspectRatio="none">' % (w, h)]
    for v in (0, -10, -20):
        if lo < v < hi:
            o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="gl%s"/>'
                     % (L, Y(v), L + iw, Y(v), " base" if v == 0 else ""))
            o.append('<text x="%d" y="%.1f" class="ax" text-anchor="end">%+d%%</text>'
                     % (L - 8, Y(v) + 3.5, v))
    for i, s in enumerate(steps):
        x = L + step * i + (step - bw) / 2
        y0, y1 = Y(0), Y(s["v"])
        top, hgt = min(y0, y1), abs(y1 - y0)
        col = T["up"] if s["v"] > 0 else T["dn"]
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3" fill="%s" '
                 'opacity=".88"/>' % (x, top, bw, max(hgt, 1.5), col))
        o.append('<text x="%.1f" y="%.1f" class="lb" fill="%s" text-anchor="middle">%s</text>'
                 % (x + bw / 2, (y1 - 7) if s["v"] > 0 else (y1 + 15), col, pct(s["v"])))
        for k, line in enumerate(s["name"].split("|")):
            o.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">%s</text>'
                     % (x + bw / 2, h - 30 + k * 13, line))
    o.append("</svg>")
    return "".join(o)


# ── 图 3：零成本 vs 含成本 ─────────────────────────────────────────
def cost_bars_svg(rows, w=1180, h=250):
    L, R, Tp, B = 54, 12, 26, 46
    iw, ih = w - L - R, h - Tp - B
    vals = [x["zero"] for x in rows] + [x["net"] for x in rows]
    lo, hi = min(vals) - 4, max(vals) + 5
    n = len(rows)
    step = iw / n
    bw = step * 0.26

    def Y(v):
        return Tp + ih * (1 - (v - lo) / (hi - lo))

    o = ['<svg viewBox="0 0 %d %d" class="chart" preserveAspectRatio="none">' % (w, h)]
    for v in (0, -10, 10):
        if lo < v < hi:
            o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="gl%s"/>'
                     % (L, Y(v), L + iw, Y(v), " base" if v == 0 else ""))
            o.append('<text x="%d" y="%.1f" class="ax" text-anchor="end">%+d%%</text>'
                     % (L - 8, Y(v) + 3.5, v))
    for i, r in enumerate(rows):
        for j, (key, col, lb) in enumerate((("zero", T["ac"], "零成本"),
                                            ("net", T["up"], "含成本"))):
            v = r[key]
            x = L + step * i + step / 2 - bw + j * bw
            y0, y1 = Y(0), Y(v)
            o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s" '
                     'opacity=".9"/>' % (x + 2, min(y0, y1), bw - 4, max(abs(y1 - y0), 1.5), col))
            o.append('<text x="%.1f" y="%.1f" class="ax" text-anchor="middle" fill="%s">%s</text>'
                     % (x + bw / 2, (y1 - 6) if v > 0 else (y1 + 13), col, pct(v, 1)))
        o.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">%s</text>'
                 % (L + step * i + step / 2, h - 24, r["label"]))
    o.append('<rect x="%d" y="%d" width="9" height="9" fill="%s"/><text x="%d" y="%d" '
             'class="ax">零成本（选股本身）</text>' % (L, h - 10, T["ac"], L + 14, h - 3))
    o.append('<rect x="%d" y="%d" width="9" height="9" fill="%s"/><text x="%d" y="%d" '
             'class="ax">含 0.15%% 单边成本</text>'
             % (L + 150, h - 10, T["up"], L + 164, h - 3))
    o.append("</svg>")
    return "".join(o)


# ── 图 4：最优变体逐月 ────────────────────────────────────────────
def monthly_svg(months, w=1180, h=210):
    L, R, Tp, B = 54, 12, 22, 46
    iw, ih = w - L - R, h - Tp - B
    vals = [m["ret_pct"] for m in months] + [m["bench_pct"] for m in months] + [0]
    lo, hi = min(vals) - 1.5, max(vals) + 2
    n = len(months)
    step = iw / n
    bw = step * 0.5

    def Y(v):
        return Tp + ih * (1 - (v - lo) / (hi - lo))

    o = ['<svg viewBox="0 0 %d %d" class="chart" preserveAspectRatio="none">' % (w, h)]
    o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="base"/>'
             % (L, Y(0), L + iw, Y(0)))
    for i, m in enumerate(months):
        cx = L + step * i + step / 2
        y0 = Y(0)
        y1 = Y(m["bench_pct"])
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" opacity=".22"/>'
                 % (cx - bw / 2 - bw * 0.55, min(y0, y1), bw * 0.5,
                    max(abs(y1 - y0), 1), T["mu"]))
        y2 = Y(m["ret_pct"])
        col = T["up"] if m["ret_pct"] > 0 else T["dn"]
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s" '
                 'opacity=".9"/>' % (cx - bw / 2, min(y0, y2), bw, max(abs(y2 - y0), 1.5), col))
        o.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">%s</text>'
                 % (cx, h - 28, m["month"][4:]))
        if m["month"][4:] == "01" or i == 0:
            o.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle" fill="%s">%s</text>'
                     % (cx, h - 13, T["mu"], m["month"][:4]))
    o.append('<rect x="%d" y="%d" width="9" height="9" fill="%s" opacity=".9"/><text x="%d" '
             'y="%d" class="ax">策略月收益</text>' % (L + iw - 190, Tp - 12, T["up"],
                                                    L + iw - 176, Tp - 4))
    o.append('<rect x="%d" y="%d" width="9" height="9" fill="%s" opacity=".3"/><text x="%d" '
             'y="%d" class="ax">等权基准</text>' % (L + iw - 96, Tp - 12, T["mu"],
                                                  L + iw - 82, Tp - 4))
    o.append("</svg>")
    return "".join(o)


def main():
    sw = json.load(open(os.path.join(REP, "bt10m_sweep.json"), encoding="utf-8"))
    oos = json.load(open(os.path.join(REP, "bt10m_sweep_oos.json"), encoding="utf-8"))
    old = json.load(open(os.path.join(REP, "bt10m_bt10m1y.json"), encoding="utf-8"))
    best = json.load(open(os.path.join(REP, "bt10m_ext03_m20_tr.json"), encoding="utf-8"))
    by_job = {r["job"]: r for r in sw["variants"]}
    B = by_job["ext03_m20_tr"]

    # 零成本实测（data/bt2/bt2_<job>_c0.json）
    def zero_of(job):
        f = os.path.join(BASE, "data", "bt2", "bt2_%s_c0.json" % job)
        if not os.path.exists(f):
            return None
        return json.load(open(f, encoding="utf-8"))["summary"]["total_return_pct"]

    h = []
    h.append("<h1>降换手实验 · 1000 万 · 这一年</h1>")
    h.append(
        '<p class="lead">上一版结论是「一年 −21.90%，换手 60 倍、成本吃掉 18 个点」。'
        '这一版去动卖出规则，问题的答案比预期硬：<b>换手能压到 6.88 倍、成本能压到 '
        '2.06%%（两个目标都达成）</b>，但真正让账户翻正的并不是降换手，而是先修掉一个'
        '<b>量纲 bug</b>——那条「跌破中枢上沿就走」的离场线，一直在拿复权归一化序列去比'
        '真实价格，于是每笔买入当日就被判定失效。修好之后同一年从 −21.90%% 变成 +11.45%%。</p>'
    )

    # ── KPI
    h.append('<div class="grid grid4 kpi">')
    cards = [
        ("累计收益", pct(B["total_return_pct"]), cls(B["total_return_pct"]),
         "旧口径 %s" % pct(old["total_return_pct"])),
        ("换手", "%.2f 倍" % B["turnover_x"], "",
         "达标（目标 &lt;10 倍）· 旧口径 %.1f 倍" % old["cost_breakdown"]["turnover_x"]),
        ("往返成本", pct(B["cost_total_pct"], 2, False), "",
         "达标（目标 &lt;3%%）· 旧口径 %.2f%%" % old["cost_breakdown"]["cost_total_pct"]),
        ("最大回撤", pct(B["max_drawdown_pct"], 2, False), "dn",
         "旧口径 %s" % pct(old["risk"]["max_drawdown_pct"], 2, False)),
        ("胜率 / PF", "%s / %s" % (pct(B["win_rate_pct"], 1, False), B["profit_factor"]), "",
         "%d 笔平仓 · 均持 %s 日" % (B["n_trades"], B["avg_hold_days"])),
        ("平均仓位", pct(B["avg_inv_pct"], 1, False), "",
         "旧口径 %.1f%% · 零成本 %+.2f%%" % (old["exposure"]["avg_inv_pct"],
                                             zero_of("ext03_m20_tr") or 0)),
    ]
    for label, val, c, sub in cards:
        h.append('<div class="panel kc"><div class="k">%s</div><div class="v %s num">%s</div>'
                 '<div class="s">%s</div></div>' % (label, c, val, sub))
    h.append("</div>")

    # ── 一、根因
    h.append('<div class="panel"><h2>一、先修 bug：那条失效线在拿两种量纲比大小</h2>')
    h.append('<p class="sm" style="line-height:1.9">'
             '引擎里的价格有两套：<code>px()</code> 取的是 <code>adj_close</code>——'
             '把每日涨幅累乘出来的<b>归一化序列</b>（上市首日≈1，拓荆科技 2025-10-13 是 1.909）；'
             '而中枢上下沿 <code>zg/zd</code> 由扫描端用<b>原始 high/low</b> 算出，是真实价格'
             '（同一只票 zg=158.30）。「收盘跌破中枢上沿」于是变成 <code>1.909 &lt; 158.30</code>'
             '——恒真。买入当日收盘就判定三买失效，次日开盘走人，一笔交易活不过 1 个交易日。</p>')
    h.append('<div class="scroll-x"><table class="wb"><thead><tr><th>现象</th>'
             '<th class="r">旧口径（比错量纲）</th><th class="r">修好之后</th>'
             '<th>说明</th></tr></thead><tbody>')
    rows = [
        ("平仓笔数", "768 笔", "%d 笔" % B["n_trades"], "旧口径把一年的机会来回刷了 26 遍"),
        ("平均持仓", "1.05 个交易日", "%s 个交易日" % B["avg_hold_days"],
         "765/768 笔恰好 1 日 = 买入当日就被判失效"),
        ("换手", "60.63 倍本金", "%.2f 倍" % B["turnover_x"], "换手本身就是这个 bug 生出来的"),
        ("往返成本", "18.19% 本金", "%.2f%%" % B["cost_total_pct"], "你以为的「成本问题」其实是「刷单问题」"),
        ("胜率", "43.6%", "%s" % pct(B["win_rate_pct"], 1, False),
         "旧口径胜率反而更高，因为只持 1 天、波动没机会展开"),
    ]
    for name, a, b, note in rows:
        h.append('<tr><td>%s</td><td class="r num dn">%s</td><td class="r num up">%s</td>'
                 '<td class="mu sm">%s</td></tr>' % (name, a, b, note))
    h.append("</tbody></table></div>")
    h.append('<p class="sm" style="margin-top:10px;line-height:1.9">'
             '两个反证：① 把失效线从「上沿」放松到「下沿」（<code>zd</code>）、'
             '或给上沿加 3%% 缓冲（<code>zg_b3</code>），结果与基准<b>完全同值</b>——'
             '因为门槛永远在价格上方，放松多少都一样；② 修好之后仍有 '
             '<b>%d/%d</b> 笔在买入当日触发失效，那才是真正「开盘价已在上沿下方」的信号'
             '（随机抽 60 只三买信号，17%% 在信号日收盘就已低于上沿）。</p>'
             % (by_job["base"].get("n_3b_same_day") or 0, by_job["base"]["n_trades"]))
    h.append("</div>")

    # ── 二、换手—收益地图
    h.append('<div class="panel"><h2>二、25 组变体：换手—收益地图 '
             '<span class="mu">红点在目标区内（换手&lt;10 倍 且 收益为正）</span></h2>')
    h.append(scatter_svg(sw["variants"], {
        "turnover_x": old["cost_breakdown"]["turnover_x"],
        "total_return_pct": old["total_return_pct"]}))
    hit = [r for r in sw["variants"] if r["turnover_x"] < 10 and r["total_return_pct"] > 0]
    h.append('<p class="mu sm">25 组里有 <b>%d 组</b>同时满足「换手 &lt;10 倍、往返成本 &lt;3%%、'
             '收益为正」。横向看得很清楚：<b>换手降下来是必要条件，但不是充分条件</b>——'
             '同样把换手压到 2.5 左右的几组（<code>base</code>/<code>zd</code>/'
             '<code>hold10</code>）收益仍在 −12%% 到 −17%% 之间，因为它们保留了那条'
             '「买在上沿上方十几个点、再跌回上沿离场」的结构性亏损。</p>' % len(hit))
    h.append("</div>")

    # ── 三、变体表
    h.append('<div class="panel"><h2>三、逐组对照 <span class="mu">'
             '同区间、同信号缓存、同 8% 单票仓位，只改卖出规则</span></h2>')
    h.append('<div class="scroll-x"><table><thead><tr><th>变体</th><th class="r">收益</th>'
             '<th class="r">回撤</th><th class="r">笔数</th><th class="r">胜率</th>'
             '<th class="r">PF</th><th class="r">换手</th><th class="r">成本</th>'
             '<th class="r">均持</th><th class="r">均仓</th><th>改了什么</th>'
             '</tr></thead><tbody>')
    for grp, gnote in GROUP_ORDER:
        rs = [r for r in sw["variants"] if r["group"] == grp]
        if not rs:
            continue
        rs.sort(key=lambda x: -x["total_return_pct"])
        h.append('<tr class="grp"><td colspan="11">%s · <span class="mu">%s</span></td></tr>'
                 % (grp, gnote))
        for r in rs:
            ok = r["turnover_x"] < 10 and r["cost_total_pct"] < 3 and r["total_return_pct"] > 0
            h.append('<tr%s><td class="num">%s</td><td class="r num %s">%s</td>'
                     '<td class="r num dn">%s</td><td class="r num">%d</td>'
                     '<td class="r num">%s</td><td class="r num">%s</td>'
                     '<td class="r num %s">%.2f 倍</td><td class="r num %s">%.2f%%</td>'
                     '<td class="r num">%s 日</td><td class="r num">%.1f%%</td>'
                     '<td class="mu sm">%s</td></tr>'
                     % (' class="hi"' if ok else "", r["job"],
                        cls(r["total_return_pct"]), pct(r["total_return_pct"]),
                        pct(r["max_drawdown_pct"], 2, False), r["n_trades"],
                        pct(r["win_rate_pct"], 1, False), r["profit_factor"],
                        "up" if r["turnover_x"] < 10 else "dn", r["turnover_x"],
                        "up" if r["cost_total_pct"] < 3 else "dn", r["cost_total_pct"],
                        r["avg_hold_days"], r["avg_inv_pct"], r["note"]))
    h.append("</tbody></table></div>")
    h.append('<p class="mu sm">高亮行 = 同时达成三目标（换手&lt;10 倍、成本&lt;3%%、收益为正）。'
             '注：<code>hold5</code>/<code>hold10</code> 这类「只设最短持有期、不设到期」的组，'
             '被拦下的卖点不会重来（卖点一年只出现 2 次），持仓一直拖到失效线跌破才走，'
             '所以换手极低但也没有出口——必须配 <code>holding_max_days</code> 兜底。</p>')
    h.append("</div>")

    # ── 四、三级台阶
    h.append('<div class="panel"><h2>四、每一刀各值多少 <span class="mu">'
             '从旧口径到最优，四步拆开</span></h2>')
    h.append(steps_svg([
        {"name": "旧口径|(量纲错配)", "v": old["total_return_pct"]},
        {"name": "修好失效线|量纲对齐", "v": by_job["base"]["total_return_pct"]},
        {"name": "改到期制|关失效线+20日", "v": by_job["no3b_m20"]["total_return_pct"]},
        {"name": "加追高门槛|距上沿≤3%", "v": by_job["ext03_m20"]["total_return_pct"]},
        {"name": "加移动止损|5%启动/3.5%跟随", "v": B["total_return_pct"]},
    ]))
    h.append('<p class="sm" style="margin-top:8px;line-height:1.9">'
             '三刀各有分工：<b>修量纲</b>（−21.90%% → −13.68%%）把「一天一刷」变成'
             '「持 50 天」，换手 60.63 → 2.68；<b>换掉失效线</b>（→ −8.47%%）不再拿'
             '「跌回中枢上沿」当离场信号，改成 20 日到期；<b>卡住买点位置</b>'
             '（→ +6.70%%）——这一步贡献最大，因为它砍掉的正是'
             '「信号触发时价格已离上沿十几个点」的追高单；<b>移动止损</b>（→ +11.45%%）'
             '负责把浮盈兑现，胜率从 51.8%% 抬到 57.3%%。</p>')
    h.append('<p class="sm" style="line-height:1.9"><b>这条阶梯也解释了上一版为什么判错。</b>'
             '上一版说「零成本仍亏 6.28%%，问题在信号本身」——那是在量纲错配的'
             '768 笔刷单里测出来的。同一批信号、同一个引擎，口径修好之后'
             '<b>零成本是 +%.2f%%</b>。真正的问题是<b>离场线的量纲</b>和'
             '<b>买点离中枢的距离</b>，不是三买信号没有 alpha。</p>'
             % (zero_of("ext03_m20_tr") or 0))
    h.append("</div>")

    # ── 五、零成本 vs 含成本
    rows = []
    for jid, label in (("base", "修好量纲"), ("no3b_m20", "关线+20日"),
                       ("ext03_m20", "加追高门槛"), ("ext03_m20_tr", "再加移动止损")):
        z = zero_of(jid)
        if z is None:
            continue
        rows.append({"label": label, "zero": z, "net": by_job[jid]["total_return_pct"]})
    h.append('<div class="panel"><h2>五、成本还剩多大分量 <span class="mu">'
             '同一组规则跑两遍：一遍零成本、一遍 0.15% 单边</span></h2>')
    h.append(cost_bars_svg(rows))
    h.append('<div class="scroll-x"><table><thead><tr><th>变体</th>'
             '<th class="r">零成本</th><th class="r">含成本</th><th class="r">成本吃掉</th>'
             '<th class="r">换手</th><th class="r">往返费率</th><th class="r">模型估算成本</th>'
             '</tr></thead><tbody>')
    for r in rows:
        jid = [k for k, v in (("base", "修好量纲"), ("no3b_m20", "关线+20日"),
                              ("ext03_m20", "加追高门槛"), ("ext03_m20_tr", "再加移动止损"))
               if v == r["label"]][0]
        d = by_job[jid]
        drag = r["net"] - r["zero"]  # 负值 = 成本吃掉的百分点
        h.append('<tr><td>%s</td><td class="r num %s">%s</td><td class="r num %s">%s</td>'
                 '<td class="r num dn">%+.2fpp</td><td class="r num">%.2f 倍</td>'
                 '<td class="r num mu">0.30%%</td><td class="r num mu">%.2f%%</td></tr>'
                 % (r["label"], cls(r["zero"]), pct(r["zero"]), cls(r["net"]),
                    pct(r["net"]), drag, d["turnover_x"], d["cost_total_pct"]))
    h.append("</tbody></table></div>")
    h.append('<p class="mu sm">「成本吃掉」= 零成本收益 − 含成本收益（实测差，含复利效应）；'
             '「模型估算成本」= 换手 × 0.30%% 往返。两者差 0.1~0.3pp，'
             '因为卖出金额不等于买入金额（有盈有亏），模型是近似。'
             '<b>结论：换手压到 7 倍以下后，成本只值 2pp 上下，不再是主导项。</b></p>')
    h.append("</div>")

    # ── 六、逐月
    h.append('<div class="panel"><h2>六、最优组合的逐月 <span class="mu">'
             '距上沿≤3%% + 关失效线 + 20 日强平 + 移动止损 5%%/3.5%%</span></h2>')
    h.append(monthly_svg(best["monthly"]))
    pos = sum(1 for m in best["monthly"] if m["ret_pct"] > 0)
    beat = sum(1 for m in best["monthly"] if m["excess_pct"] > 0)
    h.append('<p class="mu sm">13 个月里 %d 个月正收益、%d 个月跑赢基准。'
             '月胜率不到一半但年度跑赢：靠的是 <b>2026-07 基准 −8.73%% 时它空仓近八成</b>'
             '（当月 +2.62%%、超额 +11.35pp）和 <b>2026-03 基准 −7.91%% 时它 −0.18%%</b>。'
             '反过来 2026-08 基准 +8.83%% 它只有 2.8%% 仓位，跑输 9.4pp——'
             '<b>这不是靠选股赢的，是靠仓位与回撤控制赢的</b>。</p>' % (pos, beat))
    h.append("</div>")

    # ── 七、跨窗口
    q = oos
    h.append('<div class="panel"><h2>七、换个窗口还成立吗 <span class="mu">'
             '2023-10-10 → 2023-12-29（59 个交易日·完全没参与调参）</span></h2>')
    h.append('<div class="scroll-x"><table><thead><tr><th>口径</th>'
             '<th class="r">收益</th><th class="r">回撤</th><th class="r">笔数</th>'
             '<th class="r">胜率</th><th class="r">PF</th><th class="r">换手</th>'
             '<th class="r">均持</th></tr></thead><tbody>')
    h.append('<tr class="tot"><td>同期等权基准</td><td class="r num mu">%s</td>'
             '<td class="r num mu">%s</td><td class="r num mu" colspan="5">'
             '%d 个交易日 · 5816 只文件</td></tr>'
             % (pct(q["bench"]["total_return_pct"]), pct(q["bench"]["max_drawdown_pct"], 2, False),
                q["bench"]["days"]))
    for r in q["variants"]:
        h.append('<tr><td>%s</td><td class="r num %s">%s</td><td class="r num dn">%s</td>'
                 '<td class="r num">%d</td><td class="r num">%s</td>'
                 '<td class="r num">%s</td><td class="r num">%.2f 倍</td>'
                 '<td class="r num">%s 日</td></tr>'
                 % (r["note"], cls(r["total_return_pct"]), pct(r["total_return_pct"]),
                    pct(r["max_drawdown_pct"], 2, False), r["n_trades"],
                    pct(r["win_rate_pct"], 1, False), r["profit_factor"],
                    r["turnover_x"], r["avg_hold_days"]))
    h.append("</tbody></table></div>")
    bo = [r for r in q["variants"] if r["job"] == "oos_best"][0]
    h.append('<p class="sm" style="margin-top:10px;line-height:1.9">'
             '<b>诚实读法：换窗口之后，最优组合 +%.2f%% 对基准 +%.2f%%，'
             '<span class="dn">没跑赢</span>。</b>但它做到了两件更值钱的事——'
             '回撤只有 %.2f%%（基准 %.2f%%）、只用一半仓位；而「修好量纲的基准」'
             '在同一个窗口里只有 %s，说明<b>修复与降换手是稳的，超额是不稳的</b>。'
             '一个季度的样本说明不了太多，只能说明「这套规则在另一个窗口没有崩」。</p>'
             % (bo["total_return_pct"], q["bench"]["total_return_pct"],
                bo["max_drawdown_pct"], q["bench"]["max_drawdown_pct"],
                pct([r for r in q["variants"] if r["job"] == "oos_base"][0]["total_return_pct"])))
    h.append("</div>")

    # ── 八、边界
    h.append('<div class="panel warnbox"><h2>这份实验的边界，别拿 +11.45% 当预期收益</h2>'
             '<ol class="sm">')
    h.append('<li><b>25 组变体、一个年度窗口、一个基准，最优解一定有过拟合成分。</b>'
             '跨窗口那一节已经证伪了「稳定超额」：2023Q4 只做到跟住基准。'
             '正确用法是把它当<b>规则诊断</b>，不是当收益承诺。</li>')
    h.append('<li><b>追高门槛 3%% 是从样本内挑出来的。</b>2%% 是 +7.58%%，3%% 是 +6.70%%，'
             '5%% 是 +2.07%%——单调但幅度不大，说明方向可信（越贴中枢越好），'
             '具体阈值不可信。</li>')
    h.append('<li><b>扫描端还有一处同类问题没修。</b><code>market_scan.py</code> 构造 K 线时'
             '把 <code>open/high/low</code> 用原始价、只把 <code>close</code> 乘了复权因子，'
             '四种价格不在同一量纲。中枢由 high/low 生成（所以是原始价），'
             '评分函数 <code>score_3buy(close, zg)</code> 却拿复权 close 去比原始 zg，'
             '位置分那一项实际恒为满分。本次只在引擎侧对齐了失效线的量纲，'
             '<b>没有重扫信号</b>——这只影响评分排序，不影响本次结论的方向。</li>')
    h.append('<li><b>卖点确认这条出口几乎是空的。</b>一年只有 2 次缠论卖点触发；'
             '所有「降换手」的出口实质上都靠持有到期，缠论自身的卖点在实战里没有兑现能力。</li>')
    h.append('<li><b>成交假设偏乐观。</b>买入按次日开盘、卖出按次日开盘，无滑点；'
             '单票约 %s 元，对中小盘已有冲击。真实结果会更差。</li>'
             % money(best["trades"]["avg_notional"]))
    h.append('<li><b>样本量小。</b>最优组合一年 96 笔、均仓 39.5%%，'
             '统计上仍属噪音区间，PF %.2f 的标准误不低。</li>' % B["profit_factor"])
    h.append('<li><b>本章没有改任何线上卖出规则。</b>引擎新增参数'
             '（<code>max_ext_pct</code>、缠论分支的 <code>min_holding_days</code>/'
             '<code>holding_max_days</code>）默认值全部关闭，旧回测结果不受影响；'
             '是否把它设成默认口径，需要你拍板后再动。</li>')
    h.append("</ol></div>")

    h.append('<p class="mu sm" style="margin-top:14px">生成于 %s · 数据：'
             '<code>reports/bt10m_sweep.json</code>（25 组变体）、'
             '<code>reports/bt10m_sweep_oos.json</code>（跨窗口）、'
             '<code>data/bt2/bt2_ext03_m20_tr.json</code>（最优组合逐日逐笔）、'
             '<code>reports/bt10m_ext03_m20_tr_daily.csv</code>、'
             '<code>_trades.csv</code>。生成：<code>python bt10m_sweep.py</code> → '
             '<code>python bt10m.py ext03_m20_tr</code> → '
             '<code>python bt10m_sweep_page.py</code>。</p>'
             % datetime.now().strftime("%Y-%m-%d"))

    css = """
.kpi{margin:12px 0}
.kc .k{color:var(--mu);font-size:11.5px;margin-bottom:5px}
.kc .v{font-size:20px;font-weight:600;line-height:1.25}
.kc .s{color:var(--mu);font-size:11px;margin-top:4px}
.chart{width:100%;height:330px;background:var(--pn2);border:1px solid var(--bd);
 border-radius:8px;margin-top:6px}
.chart{height:auto}
.gl{stroke:#1c2530;stroke-width:1}
.gl.base{stroke:#5b6572;stroke-dasharray:2 4}
.gl.tgt{stroke:#e0b95c;stroke-dasharray:5 4;opacity:.75}
.base{stroke:#5b6572;stroke-width:1;stroke-dasharray:2 4}
.ax{fill:#7b8698;font-size:10.5px}
.lb{font-size:11px;font-weight:600}
.ob{paint-order:stroke;stroke:var(--pn2);stroke-width:3.2px;stroke-linejoin:round}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px}
th{text-align:left;color:var(--mu);font-weight:500;font-size:11.5px;
 padding:5px 8px;border-bottom:1px solid var(--bd);white-space:nowrap}
td{padding:5px 8px;border-bottom:1px solid #161e28;white-space:nowrap}
tbody tr:hover{background:#141c26}
.r{text-align:right}
tr.hi td{background:#16232c}
tr.grp td{background:#0f1620;color:var(--tx);font-weight:600;font-size:11.5px;
 border-top:1px solid var(--bd);padding:6px 8px}
tr.tot td{border-top:1px solid var(--bd);font-weight:600;background:#141c26}
.wb td{padding:7px 8px}
.sm{font-size:11.5px;line-height:1.75}
.warnbox{border-color:#4a3d1c;background:#191710}
.warnbox ol{margin:6px 0 0;padding-left:20px;color:#c9d1d9}
.warnbox li{margin:6px 0}
code{background:var(--pn2);padding:1px 5px;border-radius:4px;font-size:11px}
h2{font-size:15px;margin:0 0 4px}
"""
    html = U.page("降换手实验 · 1000万·1年", "".join(h), css)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("写出 %s（%.1f KB）" % (OUT, len(html) / 1024))


if __name__ == "__main__":
    main()
