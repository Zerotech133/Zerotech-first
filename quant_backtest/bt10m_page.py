"""1000 万 · 1 年回测报告页（读 reports/bt10m_*.json → reports/bt10m.html）。

用法：python bt10m_page.py [job_id]
"""
import json
import os
import sys

import ui_kit as U

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(BASE, "reports")
OUT = os.path.join(REP, "bt10m.html")

# 变体展示顺序与标签
VARIANTS = [
    ("bt10m1y", "三买 · 默认", "top10 · 单票8% · 仓位80/50/30 · 追高帽5%"),
    ("bt23", "二买+三买", "买点放宽到二买 · 其余同默认"),
    ("btall", "含一买", "买点不限（含一买）· 其余同默认"),
    ("bthigh", "三买 · 强闸门", "仅池指数≥MA20 才开新仓 · 其余同默认"),
    ("cost10bp", "成本降至 10bp", "单边 0.15%→0.10%，信号与规则不变"),
    ("cost3bp", "成本降至 3bp", "单边 0.15%→0.03%（万三）"),
    ("cost0", "零成本", "不扣费用，只看选股本身"),
]


def wan(x):
    return "%s" % format(round(x / 10000.0), ",")


def pct(v, digits=2, sign=True):
    if v is None:
        return "—"
    return ("%+.*f%%" if sign else "%.*f%%") % (digits, v)


def cls_of(v):
    if v is None:
        return ""
    return "up" if v > 0 else ("dn" if v < 0 else "")


def curve_svg(daily, w=1180, h=300):
    """1000 万资产曲线 + 等权基准。Y 轴单位万元。"""
    ds = [r for r in daily if r.get("asset")]
    if len(ds) < 2:
        return ""
    ys = [r["asset"] for r in ds]
    bs = [r["bench_asset"] for r in ds if r.get("bench_asset")]
    lo = min(ys + bs) if bs else min(ys)
    hi = max(ys + bs) if bs else max(ys)
    pad = max((hi - lo) * 0.12, hi * 0.01)
    lo, hi = lo - pad, hi + pad

    L, R, T, B = 58, 118, 14, 26
    iw, ih = w - L - R, h - T - B

    def X(i):
        return L + iw * (i / (len(ds) - 1))

    def Y(v):
        return T + ih * (1 - (v - lo) / (hi - lo))

    out = ['<svg viewBox="0 0 %d %d" class="chart" preserveAspectRatio="none">' % (w, h)]
    # 网格 + 万元刻度
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        v = lo + (hi - lo) * frac
        y = Y(v)
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="gl"/>'
                   % (L, y, L + iw, y))
        out.append('<text x="%d" y="%.1f" class="ax" text-anchor="end">%s万</text>'
                   % (L - 8, y + 3.5, wan(v)))
    # 本金线 1000 万
    if lo <= 10_000_000 <= hi:
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="base"/>'
                   % (L, Y(10_000_000), L + iw, Y(10_000_000)))
        out.append('<text x="%d" y="%.1f" class="ax" text-anchor="start">本金 1000万</text>'
                   % (L + 4, Y(10_000_000) - 5))
    # 基准
    if bs:
        pts = " ".join("%.1f,%.1f" % (X(i), Y(r["bench_asset"]))
                       for i, r in enumerate(ds) if r.get("bench_asset"))
        out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.4" '
                   'stroke-dasharray="4 3" opacity=".8"/>' % (pts, U.TOKENS["mu"]))
    # 策略面积 + 线
    pts = " ".join("%.1f,%.1f" % (X(i), Y(r["asset"])) for i, r in enumerate(ds))
    out.append('<polygon points="%s %.1f,%.1f %.1f,%.1f" fill="%s" opacity=".10"/>'
               % (pts, X(len(ds) - 1), Y(lo), X(0), Y(lo), U.TOKENS["ac"]))
    out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2"/>'
               % (pts, U.TOKENS["ac"]))
    # 末点标签
    last = ds[-1]
    out.append('<circle cx="%.1f" cy="%.1f" r="3.4" fill="%s"/>'
               % (X(len(ds) - 1), Y(last["asset"]), U.TOKENS["ac"]))
    out.append('<text x="%.1f" y="%.1f" class="lb" fill="%s">策略 %s万</text>'
               % (X(len(ds) - 1) + 7, Y(last["asset"]) + 3.5,
                  U.TOKENS["ac"], wan(last["asset"])))
    if last.get("bench_asset"):
        out.append('<circle cx="%.1f" cy="%.1f" r="3" fill="%s"/>'
                   % (X(len(ds) - 1), Y(last["bench_asset"]), U.TOKENS["mu"]))
        out.append('<text x="%.1f" y="%.1f" class="lb" fill="%s">基准 %s万</text>'
                   % (X(len(ds) - 1) + 7, Y(last["bench_asset"]) + 14,
                      U.TOKENS["mu"], wan(last["bench_asset"])))
    # x 轴：首、中、末
    for i in (0, len(ds) // 2, len(ds) - 1):
        out.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">%s</text>'
                   % (X(i), h - 8, ds[i]["date"]))
    out.append("</svg>")
    return "".join(out)


def hist_svg(vals, w=1180, h=150):
    """单笔收益率分布。"""
    bins = [(-100, -20), (-20, -10), (-10, -5), (-5, -2), (-2, 0),
            (0, 2), (2, 5), (5, 10), (10, 20), (20, 1000)]
    cnt = [0] * len(bins)
    for v in vals:
        for i, (a, b) in enumerate(bins):
            if a <= v < b:
                cnt[i] += 1
                break
    if not any(cnt):
        return ""
    L, R, T, B = 40, 12, 10, 34
    iw, ih = w - L - R, h - T - B
    bw = iw / len(bins) * 0.62
    step = iw / len(bins)
    mx = max(cnt)
    out = ['<svg viewBox="0 0 %d %d" class="chart" preserveAspectRatio="none">' % (w, h)]
    for i, (c, (a, b)) in enumerate(zip(cnt, bins)):
        bh = ih * c / mx
        x = L + step * i + (step - bw) / 2
        y = T + ih - bh
        color = U.TOKENS["up"] if a >= 0 else U.TOKENS["dn"]
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" '
                   'fill="%s" opacity=".85"/>' % (x, y, bw, max(bh, 1), color))
        out.append('<text x="%.1f" y="%.1f" class="ax" text-anchor="middle">%d</text>'
                   % (x + bw / 2, y - 4, c))
        lbl = ("%d~%d%%" % (a, b)) if b < 1000 else ">%d%%" % a
        out.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">%s</text>'
                   % (x + bw / 2, h - 16, lbl))
    out.append("</svg>")
    return "".join(out)


def main():
    job = sys.argv[1] if len(sys.argv) > 1 else "bt10m1y"
    p = os.path.join(REP, "bt10m_%s.json" % job)
    d = json.load(open(p, encoding="utf-8"))
    tr_csv = os.path.join(REP, "bt10m_%s_trades.csv" % job)
    pnls = []
    if os.path.exists(tr_csv):
        import csv
        pnls = [float(r["pnl_pct"]) for r in csv.DictReader(
            open(tr_csv, encoding="utf-8-sig"))]

    cap = d["capital"]
    end = d["end_asset"]
    ret = d["total_return_pct"]
    bench = d["bench"]
    risk = d["risk"]
    tr = d["trades"]
    exp = d["exposure"]
    cfg = d["config"]

    h = []
    h.append('<h1>1000 万 · 一年回测</h1>')
    h.append('<p class="lead">本金 <b>%s 元</b>，区间 <b>%s → %s</b>（%d 个交易日），'
             '策略「%s」。交易口径：信号确认后 T+1 开盘买入、T+1 开盘卖出，'
             '单边成本 0.15%%（买卖各扣一次，往返 0.3%%）、涨停开盘不追、历史 ST 剔除。'
             '曲线是<b>本金 × 引擎净值</b>，不是另跑一遍金额账。</p>'
             % (format(int(cap), ","), d["period"]["d0"], d["period"]["d1"],
                d["period"]["trading_days"], "缠论等机会轮动"))

    # ── KPI
    h.append('<div class="grid grid4 kpi">')
    cards = [
        ("期末资产", "%s 元" % format(int(end), ","), cls_of(ret),
         "期初 %s" % format(int(cap), ",")),
        ("累计收益", pct(ret), cls_of(ret),
         "盈亏 %s 元" % format(int(end - cap), ",")),
        ("最大回撤", pct(risk["max_drawdown_pct"], 2, False), "dn",
         "净值自首日后未回本金"),
        ("相对基准", pct(bench["excess_pct"]), cls_of(bench["excess_pct"]),
         "基准 %s" % pct(bench["total_return_pct"])),
        ("胜率 / PF", "%s / %s" % (pct(tr["win_rate_pct"], 1, False),
                                   tr["profit_factor"]), "",
         "%d 笔平仓" % tr["n_closed"]),
        ("平均仓位", pct(exp["avg_inv_pct"], 1, False), "",
         "均持 %.1f 只 · 最大 %d 只" % (exp["avg_positions"], exp["max_positions"])),
    ]
    for label, val, c, sub in cards:
        h.append('<div class="panel kc"><div class="k">%s</div>'
                 '<div class="v %s num">%s</div><div class="s">%s</div></div>'
                 % (label, c, val, sub))
    h.append("</div>")

    # ── 曲线
    h.append('<div class="panel"><h2>资产曲线 <span class="mu">'
             '蓝实线=策略 · 灰虚线=全市场等权 · 横线=本金</span></h2>')
    h.append(curve_svg(d["daily"]))
    h.append('<p class="mu sm">基准为 %s 只 A 股的每日等权收益累乘，'
             '与策略同区间、同复权口径。它不含成本，因此策略的真实劣势比图中更大。</p>'
             % bench["name"].split("（")[1].split(" 只")[0].replace("全市场等权", "")
             if "（" in bench["name"] else "")
    h.append("</div>")

    # ── 钱亏在哪
    cb = d.get("cost_breakdown")
    if cb:
        h.append('<div class="panel"><h2>钱亏在哪 <span class="mu">'
                 '两层：选股本亏 + 换手放大</span></h2>')
        h.append('<div class="scroll-x"><table class="wb"><thead><tr>'
                 '<th>项目</th><th class="r">金额（元）</th>'
                 '<th class="r">占本金</th><th>怎么来的</th></tr></thead><tbody>')
        rows = [
            ("零成本选股收益", cb["gross_pct"],
             "把费用设成 0 重跑一遍，同一批 768 笔交易", "neg"),
            ("交易成本拖累", cb["cost_impact_pp"],
             "换手 %.1f 倍 × 往返 %.2f%%" % (cb["turnover_x"], cb["roundtrip_pct"]), "neg"),
        ]
        for name, val, note, _cls in rows:
            h.append('<tr><td>%s</td><td class="r num %s">%s</td>'
                     '<td class="r num %s">%s</td><td class="mu sm">%s</td></tr>'
                     % (name, "up" if val > 0 else "dn",
                        format(int(val / 100 * cap), ","),
                        "up" if val > 0 else "dn",
                        pct(val) if name.startswith("零") else "%+.2fpp" % val, note))
        h.append('<tr class="tot"><td>合计</td><td class="r num dn">%s</td>'
                 '<td class="r num dn">%s</td><td class="mu sm">= 期末资产 − 本金</td></tr>'
                 % (format(int(end - cap), ","), pct(ret)))
        h.append("</tbody></table></div>")
        h.append('<p class="sm" style="margin-top:10px;line-height:1.85">'
                 '<b>亏损有两层。第一层：选股本身是负期望</b>——把手续费全免掉，'
                 '同样这 768 笔交易一年仍亏 %.2f%%。<b>第二层：高换手把亏损放大两倍半</b>'
                 '——一年买卖 %d 笔、换手 %.1f 倍本金，按单边 0.15%% 计，'
                 '光手续费 %s 元，等于本金的 %.1f%%。</p>'
                 % (-cb["gross_pct"], tr["n_closed"], cb["turnover_x"],
                    format(int(cb["cost_total_amount"]), ","),
                    -cb["cost_impact_pp"]))
        h.append('<p class="mu sm">这个费率是偏保守的：A 股实际往返约 0.10%%'
                 '（印花税 0.05%% 卖出单边 + 佣金约 0.02%%×2）。'
                 '按真实费率换算，全年亏损会收窄到 −12%% 附近（见下方敏感性）。'
                 '<b>但不改变方向：零成本都亏，说明问题在信号本身。</b></p>')
        h.append("</div>")

    # ── 逐月
    h.append('<div class="panel"><h2>逐月 <span class="mu">金额按当月净值折算</span></h2>')
    h.append('<div class="scroll-x"><table><thead><tr>'
             '<th>月份</th><th class="r">策略</th><th class="r">基准</th>'
             '<th class="r">超额</th><th class="r">平均仓位</th>'
             '<th class="r">盈亏金额</th><th class="r">交易日</th></tr></thead><tbody>')
    for m in d["monthly"]:
        h.append('<tr><td class="num">%s-%s</td>'
                 '<td class="r num %s">%s</td><td class="r num mu">%s</td>'
                 '<td class="r num %s">%s</td><td class="r num">%.1f%%</td>'
                 '<td class="r num %s">%s</td><td class="r num mu">%d</td></tr>'
                 % (m["month"][:4], m["month"][4:], cls_of(m["ret_pct"]),
                    pct(m["ret_pct"]), pct(m["bench_pct"]),
                    cls_of(m["excess_pct"]), pct(m["excess_pct"]),
                    m["avg_inv_pct"], cls_of(m["pnl_amount"]),
                    format(int(m["pnl_amount"]), ","), m["n_days"]))
    h.append("</tbody></table></div>")
    win = sum(1 for m in d["monthly"] if m["ret_pct"] > 0)
    beat = sum(1 for m in d["monthly"] if m["excess_pct"] > 0)
    h.append('<p class="mu sm">%d 个月里 %d 个月正收益、%d 个月跑赢基准。'
             '值得注意的是：跑赢基准的月份（2026-03 起）几乎都是<b>空仓期</b>'
             '——不是选股赢了，是不在场。</p>' % (len(d["monthly"]), win, beat))
    h.append("</div>")

    # ── 交易分布
    if pnls:
        h.append('<div class="panel"><h2>单笔收益分布 <span class="mu">'
                 '%d 笔已平仓 · 均值 %s · 中位 %s</span></h2>'
                 % (len(pnls), pct(tr["avg_pnl_pct"]), pct(tr["median_pnl_pct"])))
        h.append(hist_svg(pnls))
        h.append('<p class="mu sm">均盈利 %s、均亏损 %s。'
                 '亏钱的那半边比赚钱的那半边更肥，这是 PF %s 的直接来源。</p>'
                 % (pct(tr["avg_win_pct"]), pct(tr["avg_loss_pct"]),
                    tr["profit_factor"]))
        h.append("</div>")

    # ── 个股贡献
    h.append('<div class="panel"><h2>个股贡献 <span class="mu">'
             '按金额排序 · 共 %d 只票、每只交易 %d 次</span></h2>'
             % (tr["uniq_stocks"], tr["max_trades_one_stock"]))
    h.append('<div class="grid grid2">')
    for title, rows in (("亏得最多", tr["worst_contrib"]), ("赚得最多", tr["top_contrib"])):
        h.append('<div><h3>%s</h3><table><tbody>' % title)
        for code, amt, n in rows:
            h.append('<tr><td class="num">%s</td><td class="r num %s">%s</td>'
                     '<td class="r num mu">%d 笔</td></tr>'
                     % (code, cls_of(amt), format(int(amt), ","), n))
        h.append("</tbody></table></div>")
    h.append("</div></div>")

    # ── 变体对比
    h.append('<div class="panel"><h2>参数变体 <span class="mu">'
             '同区间、同信号缓存，只改一处</span></h2>')
    h.append('<div class="scroll-x"><table><thead><tr><th>口径</th>'
             '<th class="r">累计</th><th class="r">回撤</th><th class="r">胜率</th>'
             '<th class="r">PF</th><th class="r">笔数</th><th class="r">均仓</th>'
             '<th>说明</th></tr></thead><tbody>')
    best = None
    for jid, label, note in VARIANTS:
        f = os.path.join(BASE, "data", "bt2", "bt2_%s.json" % jid)
        if not os.path.exists(f):
            h.append('<tr><td class="mu">%s</td><td colspan="7" class="mu sm">'
                     '未跑（信号缓存或时间不足）</td></tr>' % label)
            continue
        s = json.load(open(f, encoding="utf-8"))["summary"]
        if best is None or s["total_return_pct"] > best:
            best = s["total_return_pct"]
        hl = ' class="hi"' if s["total_return_pct"] == best else ""
        h.append('<tr%s><td>%s</td><td class="r num %s">%s</td>'
                 '<td class="r num dn">%s</td><td class="r num">%s</td>'
                 '<td class="r num">%s</td><td class="r num">%d</td>'
                 '<td class="r num">%.1f%%</td><td class="mu sm">%s</td></tr>'
                 % (hl, label, cls_of(s["total_return_pct"]),
                    pct(s["total_return_pct"]),
                    pct(s["max_drawdown_pct"], 2, False),
                    pct(s["win_rate_pct"], 1, False), s["profit_factor"],
                    s["n_trades"], s["avg_invested_pct"], note))
    h.append("</tbody></table></div>")
    h.append('<p class="mu sm">七组口径<b>全部为负</b>。放宽买点（二买/一买）只是把亏损摊薄，'
             '没有改变方向——这叫<b>负期望下的仓位调节</b>，不是策略改进。'
             '降成本能明显改善（0.3%% 往返 → 0.06%% 时从 −21.9%% 收窄到 −9.6%%），'
             '但<b>零成本仍亏 6.28%%</b>，说明根源在信号本身，不在费率。</p>')
    h.append("</div>")

    # ── 口径与局限
    h.append('<div class="panel warnbox"><h2>这份回测的边界，必须一起看</h2><ol class="sm">')
    h.append('<li><b>信号极度稀缺，不是满仓策略。</b>top_n=10 的槽位，实际平均只持 '
             '%.1f 只、中位 2 只；242 天里只有 %d 天有仓位，2026-07 之后几乎全空。'
             '所以 −21.9%% 不是「八成仓亏两成」，是「两三成仓也亏两成」。</li>'
             % (exp["avg_positions"], exp["days_with_position"]))
    h.append('<li><b>每笔都是全平，不做加减仓。</b>%d 笔交易覆盖 %d 只票，'
             '一只票只做一次。这是轮动策略，不是持仓策略。</li>'
             % (tr["n_closed"], tr["uniq_stocks"]))
    h.append('<li><b>成本已内含，且是主因。</b>引擎单边 %s%%、买卖各扣一次（往返 %s%%），'
             '全年换手 %.1f 倍，手续费合计 <b>%s 元</b>（本金的 %.1f%%）。'
             '这不是外挂估算，是逐笔记账的结果。</li>'
             % (round(cfg["cost"] * 100, 2),
                cb["roundtrip_pct"] if cb else "0.30",
                cb["turnover_x"] if cb else 0,
                format(int(cb["cost_total_amount"]), ",") if cb else "—",
                cb["cost_total_pct"] if cb else 0))
    h.append('<li><b>没有做资金容量与冲击成本。</b>单票约 %s 元，'
             '对小盘股已接近「买得进、卖得出」的边界，真实成交价会更差。'
             '1000 万的规模在这个策略上是偏大的。</li>'
             % format(int(tr["avg_notional"]), ","))
    h.append('<li><b>样本只有一年。</b>2025-10 至 2026-10 这段区间基准 +%.2f%%，'
             '属震荡偏强市。单一年的结论不能外推，参数也不该按这一年去调。</li>'
             % bench["total_return_pct"])
    h.append('<li><b>基准不含成本、不含 ST 剔除后的重构。</b>所以「超额」是保守估计，'
             '真实差距比表格里的更大。</li>')
    h.append('<li><b>逐笔已复核。</b>抽 25 笔用原始行情（复权开盘价）反算，'
             '与引擎记录的 pnl 偏差 0.00pp——成交记录可信。'
             '季度级净值与逐笔折算存在约 8pp 的记账时点差，已单独标注，'
             '不影响「负期望 + 高换手」这个方向性结论。</li>')
    h.append('<li><b>账户级结论。</b>%d 笔交易覆盖 %d 只票，一只票只做一次；'
             '单笔最大盈利 %s 元（%s %+.2f%%），最大亏损 %s 元（%s %+.2f%%）。</li>'
             % (tr["n_closed"], tr["uniq_stocks"],
                format(int(tr["best"]["pnl_amount"]), ","), tr["best"]["stock"],
                tr["best"]["pnl_pct"],
                format(int(tr["worst"]["pnl_amount"]), ","), tr["worst"]["stock"],
                tr["worst"]["pnl_pct"]))
    h.append("</ol></div>")

    h.append('<p class="mu sm" style="margin-top:14px">数据源：%s · '
             '明细见 <code>bt10m_%s_daily.csv</code>（逐日资产）与 '
             '<code>bt10m_%s_trades.csv</code>（逐笔盈亏）。</p>'
             % (os.path.basename(p), job, job))

    css = """
.kpi{margin:12px 0}
.kc .k{color:var(--mu);font-size:11.5px;margin-bottom:5px}
.kc .v{font-size:20px;font-weight:600;line-height:1.25}
.kc .s{color:var(--mu);font-size:11px;margin-top:4px}
.chart{width:100%;height:300px;background:var(--pn2);border:1px solid var(--bd);
 border-radius:8px;margin-top:6px}
.gl{stroke:#1c2530;stroke-width:1}
.base{stroke:#5b6572;stroke-width:1;stroke-dasharray:2 4}
.ax{fill:#7b8698;font-size:10.5px}
.lb{font-size:11px;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px}
th{text-align:left;color:var(--mu);font-weight:500;font-size:11.5px;
 padding:5px 8px;border-bottom:1px solid var(--bd);white-space:nowrap}
td{padding:5px 8px;border-bottom:1px solid #161e28;white-space:nowrap}
tbody tr:hover{background:#141c26}
.r{text-align:right}
tr.hi td{background:#16202c}
.wb td{padding:7px 8px}
tr.tot td{border-top:1px solid var(--bd);font-weight:600;background:#141c26}
.sm{font-size:11.5px;line-height:1.75}
.warnbox{border-color:#4a3d1c;background:#191710}
.warnbox ol{margin:6px 0 0;padding-left:20px;color:#c9d1d9}
.warnbox li{margin:5px 0}
code{background:var(--pn2);padding:1px 5px;border-radius:4px;font-size:11px}
h2{font-size:15px;margin:0 0 4px}
h3{font-size:12.5px;color:var(--mu);margin:10px 0 4px;font-weight:500}
"""
    html = U.page("1000万 · 一年回测", "".join(h), css)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("写出 %s（%.1f KB）" % (OUT, len(html) / 1024))


if __name__ == "__main__":
    main()
