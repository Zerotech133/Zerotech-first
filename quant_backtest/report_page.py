# -*- coding: utf-8 -*-
"""回测报告 HTML 页面生成（自包含单文件、零依赖、暗色主题）。
runner 每次回测/推送后调用 write_report_html() 原子覆盖 reports/report.html，
门户 /bt-report 路由读取该文件，飞书卡片「回测报告」按钮跳转到它。"""

import html
import json
import os
from urllib.parse import quote

import strategies as _strat
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "reports", "report.html")

# 策略参数中文说明（含义与 strategies.py 各策略 docstring/默认值对应）
STRATEGY_DOCS = {
    "ma_cross": (
        "均线交叉：快线上穿慢线做多，下穿空仓",
        {"fast": "快均线天数", "slow": "慢均线天数"},
    ),
    "rsi_reversal": (
        "RSI 反转：RSI 跌破超卖线做多，升破超买线平仓",
        {
            "period": "RSI 计算周期",
            "oversold": "超卖阈值（低于买入）",
            "overbought": "超买阈值（升破平仓）",
        },
    ),
    "boll_breakout": (
        "布林突破：收盘突破上轨做多，跌破中轨空仓",
        {"period": "布林带周期", "k": "带宽倍数（±标准差 × k）"},
    ),
    "trend_follow": (
        "趋势跟随：价格在 N 日均线上方且均线向上 → 做多",
        {"n": "趋势均线天数", "slope_lookback": "均线斜率回看天数"},
    ),
}

CSS = """
body{margin:0;background:#0d1117;color:#e6edf3;font:14px/1.6 "Segoe UI",
system-ui,-apple-system,"Microsoft YaHei",sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:28px 16px 60px}
h1{font-size:20px;margin:0 0 4px} .meta{color:#8b949e;font-size:12px;margin-bottom:22px}
section{background:#161b22;border:1px solid #30363d;border-radius:10px;
padding:18px;margin-bottom:20px}
h2{font-size:16px;margin:0 0 2px} .win{color:#8b949e;font-size:12px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));
gap:10px;margin-bottom:16px}
.kpi{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:10px 12px}
.kpi .l{color:#8b949e;font-size:11px}.kpi .v{font-size:17px;font-weight:600;margin-top:2px}
.up{color:#f85149}.dn{color:#3fb950}.mu{color:#d2a8ff}
svg{width:100%;height:auto;display:block;background:#0d1117;border:1px solid #30363d;
border-radius:8px}
.leg{color:#8b949e;font-size:11px;margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid #21262d}
th{color:#8b949e;font-weight:500}
.cmp{font-size:13px;margin:4px 0 0;padding-left:0;list-style:none}
.cmp li{padding:3px 0;border-bottom:1px dashed #21262d}
.foot{color:#8b949e;font-size:11px;text-align:center;margin-top:8px}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px}
.nav a{display:inline-block;padding:6px 13px;border:1px solid #30363d;
border-radius:16px;color:#58a6ff;text-decoration:none;font-size:12.5px;
background:#161b22}
.nav a:hover{border-color:#58a6ff;background:#1c2a3a}
/* 可折叠区块：详情藏进 <details>，首屏只露标题+摘要 */
details.sec{padding:0;overflow:hidden}
details.sec>summary{cursor:pointer;padding:14px 17px;list-style:none;
display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;user-select:none}
details.sec>summary::-webkit-details-marker{display:none}
details.sec>summary::before{content:"▶";font-size:10px;color:#8b949e;
transition:transform .15s;flex:none;margin-top:2px}
details.sec[open]>summary::before{transform:rotate(90deg)}
details.sec>summary h2{display:inline;font-size:15px}
details.sec>.sec-body{padding:0 17px 15px;border-top:1px solid #21262d}
/* 双列网格：宽屏下两栏并排 */
@media(min-width:960px){.duo{display:grid;grid-template-columns:1fr 1fr;
gap:14px;align-items:start}}
/* 吸顶表头：长表格滚动时表头不丢 */
table.sticky th{position:sticky;top:0;background:#0f141c;z-index:1}
"""

# K39 面包屑：与门户 SSR 同源（portal_api.pill_nav 单一数据源）。
# 旧 NAV_PAGES 七胶囊互跳废除——☰ 域内下拉已覆盖全部互跳目标。
# exclude=当前页 key（report/signals/positions/opt，与 PAGE_SECTION 同名）。
_NAV_FALLBACK = (
    '<div class="nav"><a href="/">🏠 首页</a><a href="/quant">📈 量化</a></div>'
)


def _nav_html(exclude=None):
    try:
        import sys

        _p = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "portal"
        )
        if _p not in sys.path:
            sys.path.insert(0, _p)
        from portal_api import pill_nav

        return pill_nav(exclude or "report")
    except Exception:
        return _NAV_FALLBACK


# ── 统一下钻链接（L1 发现层 → L2 个股全景 / 策略纵切） ──
_LK = "color:#58a6ff;text-decoration:none;border-bottom:1px dotted #30363d"


def _stock_link(name, symbol):
    """标的 → 统一详情卡 /kg/stock/（CN 去后缀取 6 位代码，
    其余原样 URL 编码；symbol 缺失时退回纯文本）。"""
    nm = html.escape(str(name or ""))
    c = str(symbol or "").strip()
    if not c:
        return nm
    if c.endswith(".SH") or c.endswith(".SZ"):
        c = c[:6]
    return '<a href="/kg/stock/%s" style="%s" title="下钻到个股全景">%s</a>' % (
        quote(c, safe=""),
        _LK,
        nm,
    )


def _strat_link(sn):
    """策略 → 独立纵切页 /bt/strategy/<sn>（宽度/自选状态/回测/寻优）。"""
    lb = html.escape(OPT_HEAD.get(sn, sn))
    return '<a href="/bt/strategy/%s" style="%s" title="下钻到策略纵切页">%s</a>' % (
        sn,
        _LK,
        lb,
    )


def _downsample(series, cap=240):
    n = len(series)
    if n <= cap:
        return [(i.strftime("%m-%d"), float(v)) for i, v in series.items()]
    k = n // cap
    pts = [(i.strftime("%m-%d"), float(v)) for i, v in series.items()][::k]
    last = (series.index[-1].strftime("%m-%d"), float(series.iloc[-1]))
    if pts[-1] != last:
        pts.append(last)
    return pts


def _equity_svg(strat, bench):
    """两条净值曲线的 SVG（策略着色、基准灰、1.0 基线虚线）。"""
    allv = [v for _, v in strat] + [v for _, v in bench]
    lo, hi = min(allv), max(allv)
    if hi - lo < 1e-9:
        hi = lo + 1e-9
    W, H, PAD = 720, 220, 26

    def xy(pts, w):
        out = []
        for j, (_, v) in enumerate(pts):
            x = PAD + (W - 2 * PAD) * (j / max(1, len(pts) - 1))
            y = H - PAD - (H - 2 * PAD) * (v - lo) / (hi - lo)
            out.append("%.1f,%.1f" % (x, y))
        return " ".join(out)

    y_base = H - PAD - (H - 2 * PAD) * (1.0 - lo) / (hi - lo)
    color = "#f85149" if strat[-1][1] >= 1 else "#3fb950"
    svg = [
        '<svg viewBox="0 0 %d %d" preserveAspectRatio="none">' % (W, H),
        '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#30363d" '
        'stroke-dasharray="4 4"/>' % (PAD, y_base, W - PAD, y_base),
        '<polyline fill="none" stroke="#8b949e" stroke-width="1.4" '
        'points="%s"/>' % xy(bench, 1),
        '<polyline fill="none" stroke="%s" stroke-width="2" points="%s"/>'
        % (color, xy(strat, 0)),
        '<text x="%d" y="14" fill="#8b949e" font-size="11">%s</text>'
        % (PAD, bench[0][0]),
        '<text x="%d" y="14" fill="#8b949e" font-size="11" '
        'text-anchor="middle">%s</text>' % (W // 2, bench[len(bench) // 2][0]),
        '<text x="%d" y="14" fill="#8b949e" font-size="11" '
        'text-anchor="end">%s</text>' % (W - PAD, bench[-1][0]),
        "</svg>",
    ]
    return "".join(svg)


def _kpi(label, value, cls=""):
    return (
        '<div class="kpi"><div class="l">%s</div><div class="v %s">%s</div></div>'
        % (html.escape(label), cls, value)
    )


def _dd_svg(dd, bench_dd):
    """回撤曲线：策略红色填充区域 + 基准灰线，0 轴在上。"""
    allv = [v for _, v in dd] + [v for _, v in bench_dd] + [0.0]
    lo = min(allv)
    if lo > -1e-9:
        lo = -0.05
    W, H, PAD = 720, 170, 26

    def xy(pts):
        out = []
        for j, (_, v) in enumerate(pts):
            x = PAD + (W - 2 * PAD) * (j / max(1, len(pts) - 1))
            y = PAD + (H - 2 * PAD) * (v / lo)  # v∈[lo,0] → y∈[底,顶]
            out.append("%.1f,%.1f" % (x, y))
        return " ".join(out)

    strat_pts = xy(dd)
    x0, x1 = PAD, W - PAD
    return "".join(
        [
            '<svg viewBox="0 0 %d %d" preserveAspectRatio="none">' % (W, H),
            '<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#30363d"/>'
            % (x0, PAD, x1, PAD),
            '<polyline fill="none" stroke="#8b949e" stroke-width="1.2" '
            'points="%s"/>' % xy(bench_dd),
            '<polygon fill="#3fb950" fill-opacity="0.22" stroke="#3fb950" '
            'stroke-width="1.6" points="%s %d,%.1f"/>' % (strat_pts, x1, float(PAD)),
            '<text x="%d" y="%d-4" fill="#3fb950" font-size="11">最深 %.1f%%</text>'
            % (PAD, H - PAD, lo * 100),
            '<text x="%d" y="14" fill="#8b949e" font-size="11">%s</text>'
            % (PAD, dd[0][0]),
            '<text x="%d" y="14" fill="#8b949e" font-size="11" '
            'text-anchor="end">%s</text>' % (x1, dd[-1][0]),
            "</svg>",
        ]
    )


def _opt_table(rows, base_params):
    """参数寻优表：当前生效参数组合行高亮 ★。"""
    if not rows:
        return ""
    tr = []
    for i, r in enumerate(rows):
        pj = json.dumps(r["params"], ensure_ascii=False)
        cur = (
            isinstance(base_params, dict)
            and all(
                r["params"].get(k) == v
                for k, v in base_params.items()
                if k in r["params"]
            )
            and all(k in base_params for k in r["params"])
        )
        tr.append(
            '<tr%s><td>%s</td><td>%s%s</td><td class="%s">%+.1f%%</td>'
            '<td><b>%.2f</b></td><td class="dn">%.1f%%</td><td>%d</td>'
            "</tr>"
            % (
                ' style="background:#1c2a3a"' if cur else "",
                "★" if cur else str(i + 1),
                html.escape(pj),
                "（当前）" if cur else "",
                "up" if r["ret"] >= 0 else "dn",
                r["ret"] * 100,
                r["sharpe"],
                r["dd"] * 100,
                r["trades"],
            )
        )
    return """
<h2 style="margin-top:16px;font-size:13px">参数寻优 Top %d（按夏普）</h2>
<table><tr><th>#</th><th>参数组合</th><th>收益</th><th>夏普</th><th>回撤</th>
<th>交易</th></tr>%s</table>
<div class="leg">样本内网格寻优 · 高亮行为当前配置 · 结果仅作参数敏感性参考，
谨防过拟合（样本外表现通常更低）</div>""" % (len(rows), "".join(tr))


def _monthly_table(monthly):
    """月度收益表：monthly = [(ym, 策略, 持有)]。"""
    if not monthly:
        return ""
    tr = []
    for ym, s_r, b_r in monthly:
        d = s_r - b_r
        tr.append(
            '<tr><td>%s</td><td class="%s">%+.1f%%</td>'
            '<td class="%s">%+.1f%%</td><td class="%s">%+.1f%%</td></tr>'
            % (
                ym,
                "up" if s_r >= 0 else "dn",
                s_r * 100,
                "up" if b_r >= 0 else "dn",
                b_r * 100,
                "up" if d >= 0 else "dn",
                d * 100,
            )
        )
    return """
<h2 style="margin-top:16px;font-size:13px">月度收益</h2>
<table><tr><th>月份</th><th>策略</th><th>买入持有</th><th>超额</th></tr>%s</table>
<div class="leg">首月为区间起始的不足月 · 红涨绿跌为习惯色（正=红）</div>""" % "".join(
        tr
    )


def _heat_table(heat):
    """日收益热力：仅持仓日着色（红涨绿跌），深灰=空仓日，无色=非交易日。"""
    if not heat:
        return ""
    rows = []
    for ym, cells in heat:
        tds = []
        for day, cell in enumerate(cells, 1):  # 1..31
            if cell is None:
                tds.append('<td style="border:none"></td>')
                continue
            r, held = cell
            if held:
                a = min(1.0, abs(r) / 0.03) * 0.6 + 0.15
                rgb = "248,81,73" if r >= 0 else "63,185,80"
                tds.append(
                    '<td title="%02d %s %+.2f%%" style="border:none;'
                    'background:rgba(%s,%.2f);border-radius:3px"></td>'
                    % (day, ym, r * 100, rgb, a)
                )
            else:
                tds.append(
                    '<td title="%02d %s 空仓" style="border:none;'
                    'background:#21262d;border-radius:3px"></td>' % (day, ym)
                )
        rows.append(
            '<tr><th style="text-align:left">%s</th>%s</tr>' % (ym, "".join(tds))
        )
    return """
<h2 style="margin-top:16px;font-size:13px">日收益热力（持仓区间着色）</h2>
<table style="table-layout:fixed">%s</table>
<div class="leg">红涨绿跌 · 颜色深浅=当日盈亏幅度（悬停看数值）·
深灰=空仓日 · 空白=非交易日</div>""" % "".join(rows)


def _charts_html(s):
    """有 matplotlib PNG 用图片（可下载/打印），否则退回 SVG。"""
    png = s.get("png")
    if png:
        u = "/bt-report/img/" + png
        return (
            '<img src="%s" style="width:100%%;border-radius:8px;'
            'border:1px solid #21262d">'
            % u
            + '<div class="leg"><a href="%s" download="%s">⬇ 下载图表 PNG'
            '</a>　<a href="javascript:window.print()">🖨 打印 / 存 PDF'
            "</a></div>" % (u, png)
        )
    deepest = min(v for _, v in s["dd"]) * 100
    return "".join(
        [
            _equity_svg(s["equity"], s["bench_equity"]),
            '<div class="leg">— 策略净值　— 买入持有（灰）　虚线 = 1.0 基线</div>',
            '<h2 style="margin-top:16px;font-size:13px">回撤曲线</h2>',
            _dd_svg(s["dd"], s["bench_dd"]),
            '<div class="leg">红色区域 = 策略回撤　灰线 = 买入持有回撤　'
            "最深 %.1f%%</div>" % deepest,
        ]
    )


def _section_html(s):
    m, bh = s["m"], s["bh"]
    ex = m["total_return"] - bh["total_return"]
    rows = [
        _kpi(
            "总收益",
            "%+.1f%%" % (m["total_return"] * 100),
            "up" if m["total_return"] >= 0 else "dn",
        ),
        _kpi("年化", "%+.1f%%" % (m["cagr"] * 100)),
        _kpi("夏普", "%.2f" % m["sharpe"]),
        _kpi("索提诺", "%.2f" % m["sortino"]),
        _kpi("卡玛", "%.2f" % m["calmar"]),
        _kpi("最大回撤", "%.1f%%" % (m["max_drawdown"] * 100), "dn"),
        _kpi("胜率", "%.0f%%" % (m["win_rate"] * 100)),
        _kpi("交易次数", str(m["trades"])),
        _kpi(
            "买入持有",
            "%+.1f%%" % (bh["total_return"] * 100),
            "up" if bh["total_return"] >= 0 else "dn",
        ),
        _kpi("超额收益", "%+.1f%%" % (ex * 100), "up" if ex >= 0 else "dn"),
    ]
    tr_rows = "".join(
        "<tr><td>%s</td><td>%.2f</td><td>%s</td><td>%.2f</td>"
        '<td class="%s">%+.1f%%</td><td>%s</td></tr>'
        % (
            t["in"],
            t["in_px"],
            t["out"],
            t["out_px"],
            "up" if t["pnl"] >= 0 else "dn",
            t["pnl"] * 100,
            "未平仓" if t.get("open") else "",
        )
        for t in s["trades"]
    )
    cmp_li = "".join("<li>%s</li>" % html.escape(c) for c in s["compare"])
    return """
<section>
  <h2>%s <span class="mu">· %s</span></h2>
  <div class="win">区间 %s · 参数 %s · 现价 %.2f（%s · 日 %+.1f%%）· %s</div>
  <div class="grid">%s</div>
  %s
  <h2 style="margin-top:16px;font-size:13px">四策略同期对比</h2>
  <ul class="cmp">%s</ul>
  %s
  %s
  %s
  <h2 style="margin-top:16px;font-size:13px">交易明细（近 %d 笔）</h2>
  <table><tr><th>买入日</th><th>买价</th><th>卖出日</th><th>卖价</th>
  <th>盈亏</th><th>状态</th></tr>%s</table>
</section>""" % (
        html.escape(s["name"]),
        html.escape(s["strategy"]),
        html.escape(s["window"]),
        html.escape(json.dumps(s["params"], ensure_ascii=False)),
        s["last_px"],
        s["last_date"],
        s["day_chg"] * 100,
        html.escape(s["status"]),
        "".join(rows),
        _charts_html(s),
        cmp_li,
        _opt_table(s.get("opt") or [], s.get("opt_base", "")),
        _monthly_table(s.get("monthly") or []),
        _heat_table(s.get("heat") or []),
        len(s["trades"]),
        tr_rows,
    )


def _strategy_docs_html():
    """策略参数说明区：默认值 vs 当前生效值（config strategies 节）。"""
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            cur = json.load(f).get("strategies", {})
    except Exception:
        cur = {}
    trs = []
    for name, (logic, pdoc) in STRATEGY_DOCS.items():
        cur_p = cur.get(name) or {}
        dflt = _strat.PARAM_DEFAULTS.get(name, {})
        first = True
        for k, desc in pdoc.items():
            v = cur_p.get(k, "—")
            d = dflt.get(k, "—")
            tag = "" if str(v) == str(d) else " <span class='up'>★调参</span>"
            head = (
                (
                    "<b>"
                    + name
                    + "</b><br><span class='mu' style='font-size:11px'>"
                    + logic
                    + "</span>"
                )
                if first
                else ""
            )
            trs.append(
                "<tr><td>"
                + head
                + "</td><td><code>"
                + k
                + "</code></td><td>"
                + desc
                + "</td><td>"
                + str(d)
                + "</td><td><b>"
                + str(v)
                + "</b>"
                + tag
                + "</td></tr>"
            )
            first = False
    return (
        "<section><h2>策略参数说明 <span class='mu' style='font-size:12px'>"
        "· 默认 vs 当前生效（季度自动调参）</span></h2><table><tr>"
        "<th style='width:27%'>策略 / 逻辑</th><th>参数</th><th>含义</th>"
        "<th>默认</th><th>当前</th></tr>" + "".join(trs) + "</table>"
        "<p class='leg'>信号均为次日开盘生效（T+1，避免未来函数）· 仅做多策略"
        " · ★调参 = 当前值来自全池寻优，与代码默认不同</p></section>"
    )


def _page_html(title, sections):
    body = "".join(_section_html(s) for s in sections)
    return """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s</style></head><body><div class="wrap">
<h1>📈 %s</h1><div class="meta">生成于 %s · 量化回测域 · 仅回测参考，不构成投资建议</div>
%s%s%s<div class="foot">AI 工作站 · 门户 /bt-report</div></div></body></html>""" % (
        html.escape(title),
        CSS,
        html.escape(title),
        sections[0]["gen"] if sections else "",
        _nav_html(exclude="report"),
        body,
        _strategy_docs_html(),
    )


def write_report_html(title, sections):
    """原子覆盖 reports/report.html（门户 /bt-report 展示的最新报告）。"""
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    tmp = REPORT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(_page_html(title, sections))
    os.replace(tmp, REPORT)
    return REPORT


SIGNALS = os.path.join(HERE, "reports", "signals.html")
SIG_STRATS = ["trend_follow", "ma_cross", "boll_breakout", "rsi_reversal"]
SIG_HEAD = {
    "trend_follow": "趋势跟随",
    "ma_cross": "均线交叉",
    "boll_breakout": "布林突破",
    "rsi_reversal": "RSI 反转",
}
# [20261021_wo-qt-072 来源列] 机器聚合选票池（portal /picks.html 数据源）。
# watchlist code 经 resolve_watchlist 归一为 "301047.SZ"/"603580.SH"，
# 与 todays_picks stocks[].code 同格式，可直接精确匹配。
PICKS_JSON = os.path.abspath(
    os.path.join(HERE, "..", "portal", "data", "todays_picks.json")
)


def _load_picks_index():
    """读 todays_picks.json → ({code: (策略名缩写串, tier)}, 可用?）。

    文件缺失/坏 JSON/结构异常时返回 ({}, False)，调用方整列降级，
    信号页照常出——绝不让票池数据问题炸掉产线信号页。
    """
    try:
        with open(PICKS_JSON, encoding="utf-8") as f:
            stocks = (json.load(f) or {}).get("stocks") or []
        idx = {}
        for st in stocks:
            if not isinstance(st, dict) or not st.get("code"):
                continue
            srcs = []
            for s in st.get("sources") or []:
                sn = s.get("s") if isinstance(s, dict) else None
                if sn and sn not in srcs:
                    srcs.append(sn)
            idx[st["code"]] = ("·".join(srcs), st.get("tier") or "")
        return idx, True
    except Exception:
        return {}, False


def _src_cell(symbol, picks, picks_ok):
    """来源列单元格：命中→策略缩写+tier 徽标；未命中→灰「观察名单」；
    票池数据不可用→整列降级占位（不炸）。"""
    if not picks_ok:
        return '<td class="src0">（票池数据不可用）</td>'
    hit = picks.get(symbol or "")
    if hit and (hit[0] or hit[1]):
        tcls = "t1" if hit[1] == "T1" else "t2"
        return '<td class="src1">%s<span class="tier %s">%s</span></td>' % (
            html.escape(hit[0] or ""),
            tcls,
            html.escape(hit[1] or "—"),
        )
    return '<td class="src0">观察名单</td>'


def write_signals_html(rows, gen_ts, hold_only=False):
    """全池策略信号页：●持仓 ○空仓（悬停可见信号起始日），共振 ≥3 高亮。
    可下钻：点标的 → 个股全景 /bt/stock · 点策略列头 → 策略纵切页。
    hold_only=True 时只显示有任一策略持仓的标的。"""
    head_cells = "".join(
        '<th><a href="/bt/strategy/%s" style="%s" title="下钻到策略纵切页">'
        "%s</a></th>" % (s, _LK, SIG_HEAD[s])
        for s in SIG_STRATS
    )
    picks, picks_ok = _load_picks_index()  # [来源列] 双池口径：join 选票池
    n_picks = str(len(picks)) if picks_ok else "—"
    n_hold = sum(1 for r in rows if any(r["sigs"].values()))
    tr = []
    for r in rows:
        if hold_only and not any(r["sigs"].values()):
            continue
        cells = "".join(
            ('<td class="s1" title="自 %s">●</td>' % r["since"][s])
            if r["sigs"][s]
            else ('<td class="s0" title="自 %s">○</td>' % r["since"][s])
            for s in SIG_STRATS
        )
        res_cls = ' class="hi"' if r["res"] >= 3 else ""
        tr.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%.4g</td>"
            "<td>%s</td>%s<td%s>%d/4</td></tr>"
            % (
                _stock_link(r["name"], r.get("symbol", "")),
                _src_cell(r.get("symbol", ""), picks, picks_ok),
                r["market"],
                r["px"],
                r["date"],
                cells,
                res_cls,
                r["res"],
            )
        )
    page = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>全池策略信号</title><style>%s
.s1{color:#3fb950;text-align:center}.s0{color:#58606a;text-align:center}
.hi{color:#f0883a;font-weight:700;text-align:center}
td{padding:5px 10px;border-bottom:1px solid #21262d}
th{padding:6px 10px;color:#8b949e;font-weight:600;text-align:left}
table{border-collapse:collapse;width:100%%;font-size:13px}
.filter{display:flex;gap:8px;margin:14px 0 10px;flex-wrap:wrap}
.filter a{display:inline-block;padding:5px 13px;border:1px solid #30363d;
border-radius:14px;font-size:12px;text-decoration:none;color:#8b949e}
.filter a.on{color:#e6edf3;border-color:#58a6ff;background:#1c2a3a}
.src1{color:#c9d1d9;white-space:nowrap}.src0{color:#58606a;font-size:12px}
.tier{display:inline-block;padding:0 6px;margin-left:5px;border-radius:9px;
font-size:11px;line-height:17px;vertical-align:1px}
.tier.t1{color:#f0883a;border:1px solid #f0883a}
.tier.t2{color:#58a6ff;border:1px solid #58a6ff}
</style></head><body><div class="wrap">
<h1>🚦 全池策略信号（明日执行参照）</h1>
<div class="meta">生成于 %s · %d 只 · 策略参数=当前 config（季度自动调参）</div>
<div class="meta">本页=观察名单(%d)信号检查；机器聚合选票池见
<a href="/picks.html" style="color:#58a6ff">/picks.html</a>（%s 只带来源评分）</div>
%s
<div class="filter"><a class="%s" href="/bt-signals">全部 %d</a>
<a class="%s" href="/bt-signals?hold=1">只看持仓 %d</a></div>
<table class="sticky"><tr><th>标的</th><th>来源</th><th>市场</th><th>现价</th>
<th>数据日</th>
%s<th>共振</th></tr>%s</table>
<div class="leg">● 持仓 ○ 空仓（悬停可见信号起始日）· 共振=4 策略同时持仓数（≥3 橙色）
· 点标的 → 个股全景 · 点策略列头 → 策略纵切页
· 信号基于最新收盘数据，按下一交易日执行（T+1）· 不构成投资建议</div>
</div></body></html>""" % (
        CSS,
        gen_ts,
        len(rows),
        len(rows),
        n_picks,
        _nav_html(exclude="signals"),
        "on" if not hold_only else "",
        len(rows),
        "on" if hold_only else "",
        n_hold,
        head_cells,
        "".join(tr),
    )
    os.makedirs(os.path.dirname(SIGNALS), exist_ok=True)
    tmp = SIGNALS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, SIGNALS)
    return SIGNALS


OPT = os.path.join(HERE, "reports", "opt.html")
OPT_HEAD = {
    "trend_follow": "趋势跟随",
    "ma_cross": "均线交叉",
    "boll_breakout": "布林突破",
    "rsi_reversal": "RSI 反转",
}


def write_opt_html(data, gen_ts):
    """全池寻优榜单页：data = [{"name","rows":[...top5],"cur":params}]。"""
    secs = []
    for d in data:
        tr = []
        for i, r in enumerate(d["rows"]):
            pj = json.dumps(r["params"], ensure_ascii=False)
            cur = r["params"] == d["cur"]
            tr.append(
                '<tr%s><td>%s</td><td>%s%s</td><td class="%s">%+.1f%%'
                "</td><td><b>%.2f</b></td><td>%d/51</td></tr>"
                % (
                    ' style="background:#1c2a3a"' if cur else "",
                    "★" if cur else str(i + 1),
                    html.escape(pj),
                    "（当前）" if cur else "",
                    "up" if r["mean_ret"] >= 0 else "dn",
                    r["mean_ret"] * 100,
                    r["mean_sharpe"],
                    r.get("top3", 0),
                )
            )
        secs.append(
            """
<details class="sec"%s><summary><h2>%s</h2>
<span class="dim">当前 %s</span></summary><div class="sec-body">
<table><tr><th>#</th><th>参数组合</th><th>全池平均收益</th><th>平均夏普</th>
<th>Top3命中</th></tr>%s</table></div></details>"""
            % (
                " open" if i == 0 else "",
                OPT_HEAD.get(d["name"], d["name"]),
                html.escape(json.dumps(d["cur"], ensure_ascii=False)),
                "".join(tr),
            )
        )
    page = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>全池参数寻优榜单</title><style>%s
td{padding:5px 10px;border-bottom:1px solid #21262d}
th{padding:6px 10px;color:#8b949e;font-weight:600;text-align:left}
table{border-collapse:collapse;width:100%%;font-size:13px}
</style></head><body><div class="wrap">
<h1>📊 全池参数寻优榜单</h1>
<div class="meta">生成于 %s · 51 只自选 · 近 %d 天 · 季度自动重调（季首 10:00）·
<a href="javascript:window.print()">🖨 打印 / 存 PDF</a></div>
%s
%s
<div class="leg">按（全池平均夏普, Top3 命中数）排序取前 5 · ★ 当前生效参数 ·
样本内寻优仅供敏感性参考，谨防过拟合</div>
</div></body></html>""" % (CSS, gen_ts, 365, _nav_html(exclude="opt"), "".join(secs))
    os.makedirs(os.path.dirname(OPT), exist_ok=True)
    tmp = OPT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, OPT)
    return OPT


POSITIONS = os.path.join(HERE, "reports", "positions.html")

# ── W1-2：九维警报池命中检测（供盘前作战卡 / 持仓页共用） ──────────
# 放在本模块而非 runner：runner 已 import report_page，反向 import 会成环，
# 且 nine_dim.py 拖 tushare/pandas 全家桶，账本页不值得为读一个 jsonl 加载。
# 判例 K32/K33：九维 score≥75 为拥挤警报（反指实证，前向收益显著为负），
# 持仓/待买命中 = 提醒风险，非买入信号。
NINE_DIM_HIST = os.path.join(HERE, "data", "nine_dim_history.jsonl")
NINE_DIM_RECENT_N = 20  # 只看池内最近 20 个交易日（约一个月）的记录，过期警报不再提醒

POOL_ROW_BG = ' style="background:#3a1d1d"'  # 命中行醒目暗红底（同 opt 当前参数行风格）


def _cfg_flag(key, default=True):
    """W1-2：读 config.json 顶层布尔开关；读不到按 default（fail-open）。"""
    try:
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            return bool(json.load(f).get(key, default))
    except Exception:
        return default


def nine_dim_cn_code(market, symbol):
    """账本 (market, symbol) → 警报池全码；非 CN 返回 None（九维只覆盖 A 股）。
    池内 code 一律带后缀（600519.SH，见 nine_dim.py 入池段）；账本 CN 亦是全码，
    直接对齐；裸 6 位码兜底按首位推断后缀（6→SH，4/8/9→BJ，其余→SZ）。"""
    if (market or "").upper() != "CN" or not symbol:
        return None
    s = str(symbol).strip().upper()
    if "." in s:
        return s
    return s + (".SH" if s[0] == "6" else ".BJ" if s[0] in "489" else ".SZ")


def nine_dim_alert_check(codes, path=None, recent_n=NINE_DIM_RECENT_N):
    """W1-2：代码集合命中九维警报池检测。
    codes: 全码可迭代（600519.SH / 000045.SZ）；path 可注入（单测用）。
    返回 {code: (dt, score, band)}，同码取最近 dt（YYYYMMDD）。
    大文件按行流式读 + code 集合过滤，不整读；
    「最近 N 个交易日」= 池内出现过的 dt 降序前 N（免依赖交易日历）。
    fail-open（K98 同款纪律）：文件缺失/损坏 = 无提醒，不炸卡不炸页。"""
    hits = {}
    try:
        want = {c for c in (codes or ()) if c}
        if not want:
            return hits
        dts, rows = set(), []
        with open(path or NINE_DIM_HIST, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue  # 坏行跳过，不因单行损坏丢弃全池
                if r.get("pool_type") != "alert":
                    continue  # 只认警报池（键=code+dt，见 nine_dim 入池段）
                dts.add(str(r.get("dt") or ""))
                c = str(r.get("code") or "")
                if c in want:
                    rows.append(r)
        recent = set(sorted(dts, reverse=True)[:recent_n])
        for r in rows:
            dt = str(r.get("dt") or "")
            if dt not in recent:
                continue
            old = hits.get(r["code"])
            if not old or dt > old[0]:
                hits[r["code"]] = (dt, r.get("score"), r.get("band"))
        return hits
    except Exception:
        return {}  # 文件缺失等一律 fail-open


def _ledger_alert_hits(entries, alert_hits=None):
    """W1-2：账本条目列表 → 命中映射。alert_hits 非 None 时直接透传（单测注入）；
    None 则按开关 nine_dim_alert_card 现算。"""
    if alert_hits is not None:
        return alert_hits
    if not _cfg_flag("nine_dim_alert_card", True):
        return {}
    codes = {
        c
        for c in (nine_dim_cn_code(e.get("market"), e.get("symbol")) for e in entries)
        if c
    }
    return nine_dim_alert_check(codes)


def _alert_deco(e, hits):
    """W1-2：单条账本条目 → (行属性, 徽标 html)。未命中返回 ("","")。"""
    c = nine_dim_cn_code(e.get("market"), e.get("symbol"))
    h = hits.get(c) if c else None
    if not h:
        return "", ""
    tip = "九维警报池命中 %s-%s-%s · 分数 %s（%s）· 高分=拥挤警报，非买入信号" % (
        h[0][:4],
        h[0][4:6],
        h[0][6:8],
        h[1],
        h[2] or "alert",
    )
    return (
        POOL_ROW_BG,
        "<span title='%s' style='background:#b62324;color:#fff;"
        "border-radius:4px;padding:0 5px;font-size:11px;"
        "margin-left:6px'>⚠️警报池</span>" % tip,
    )


def write_positions_html(led, gen_ts, alert_hits=None):
    """纸面持仓跟踪页（/bt-positions）：待买入 / 持仓中 / 已平仓。
    led = {"pending":[], "open":[], "closed":[]}（runner.update_ledger 维护）。
    全表可下钻：点标的 → 个股全景 /bt/stock · 点策略 → 策略纵切页。"""

    def pct_td(p):
        return (
            "<td class='up'>%+.2f%%</td>" if p >= 0 else "<td class='dn'>%+.2f%%</td>"
        ) % (p * 100)

    def trail_td(e):
        """近 5 日收盘浮盈轨迹（update_ledger marks 留痕）。"""
        ms = (e.get("marks") or [])[-5:]
        if not ms:
            return "<td style='color:#8b949e'>—</td>"
        spans = "".join(
            "<span class='%s' title='%s 收 %s'>%+.1f%%</span> "
            % (
                "up" if m["pnl"] >= 0 else "dn",
                m["d"][5:],
                ("%.4g" % m["px"]),
                m["pnl"] * 100,
            )
            for m in ms
        )
        return "<td style='font-size:11px;white-space:nowrap'>%s</td>" % spans

    pend = led.get("pending") or []
    open_ = led.get("open") or []
    closed = (led.get("closed") or [])[:30]

    # W1-2：九维警报池命中标记（与盘前作战卡同源同开关；closed 不标，已成历史）
    hits = _ledger_alert_hits(pend + open_, alert_hits)

    p_rows = ""
    for e in pend:
        bg, badge = _alert_deco(e, hits)
        p_rows += (
            "<tr%s><td>%s%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%.4g</td></tr>"
            % (
                bg,
                _stock_link(e["name"], e.get("symbol")),
                badge,
                e["market"],
                _strat_link(e["strategy"]),
                e.get("sig_date", ""),
                e.get("last_px") or 0,
            )
        )
    p_rows = p_rows or "<tr><td colspan=5>—</td></tr>"

    o_rows = ""
    for e in sorted(open_, key=lambda x: -x.get("pnl", 0)):
        bg, badge = _alert_deco(e, hits)
        o_rows += (
            "<tr%s><td>%s%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%.4g</td><td>%.4g</td>%s<td>%d</td>%s</tr>"
            % (
                bg,
                _stock_link(e["name"], e.get("symbol")),
                badge,
                e["market"],
                _strat_link(e["strategy"]),
                e.get("entry_date", ""),
                e["entry_px"],
                e.get("last_px") or e["entry_px"],
                pct_td(e.get("pnl", 0)),
                e.get("days", 0),
                trail_td(e),
            )
        )
    o_rows = o_rows or "<tr><td colspan=9>—</td></tr>"

    c_rows = (
        "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s → %s</td>"
            "<td>%.4g → %.4g</td>%s<td>%d天</td></tr>"
            % (
                _stock_link(e["name"], e.get("symbol")),
                e["market"],
                _strat_link(e["strategy"]),
                e.get("entry_date", ""),
                e.get("exit_date", ""),
                e["entry_px"],
                e["exit_px"],
                pct_td(e.get("pnl", 0)),
                e.get("days", 0),
            )
            for e in closed
        )
        or "<tr><td colspan=8>—</td></tr>"
    )

    n_all = len(led.get("closed") or [])
    wins = sum(1 for c in led.get("closed") or [] if c.get("pnl", 0) > 0)
    avg = (
        (sum(c.get("pnl", 0) for c in led.get("closed") or []) / n_all) if n_all else 0
    )
    tot = sum(c.get("pnl", 0) for c in led.get("closed") or [])
    stats = (
        "累计平仓 %d 笔 · 胜率 %.0f%% · 平均 %+.2f%% · 合计 %+.2f%%"
        "（等权单笔，不含手续费）"
        % (n_all, wins * 100.0 / n_all if n_all else 0, avg * 100, tot * 100)
    )
    # W1-2：命中时页脚标注数据源与口径（未命中不加，免噪音）
    alert_foot = (
        (
            " · ⚠️ 警报池标记：data/nine_dim_history.jsonl"
            "（九维 score≥75 拥挤反指，近 %d 个池内交易日）· "
            "高分=拥挤警报，非买入信号" % NINE_DIM_RECENT_N
        )
        if hits
        else ""
    )

    page = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>纸面持仓跟踪</title><style>%s
.up{color:#f85149}.dn{color:#3fb950}
td{padding:5px 10px;border-bottom:1px solid #21262d}
th{padding:6px 10px;color:#8b949e;font-weight:600;text-align:left}
table{border-collapse:collapse;width:100%%;font-size:13px}
</style></head><body><div class="wrap">
<h1>💼 纸面持仓跟踪（量化信号实战）</h1>
<div class="meta">账本更新于 %s · %s</div>
%s
<details class="sec" open><summary><h2>🛒 待执行（纸面模拟·非投资建议）</h2>
<span class="dim">%d 笔</span></summary><div class="sec-body">
<table class="sticky"><tr><th>标的</th><th>市场</th><th>策略</th><th>信号日</th>
<th>信号日收盘</th></tr>%s</table></div></details>
<details class="sec" open><summary><h2>📦 持仓中（信号翻空前持有）</h2>
<span class="dim">%d 笔</span></summary><div class="sec-body">
<table class="sticky"><tr><th>标的</th><th>市场</th><th>策略</th><th>买入日</th>
<th>买价</th><th>最新</th><th>盈亏</th><th>天数</th><th>近5日轨迹</th></tr>%s</table></div></details>
<details class="sec"><summary><h2>✅ 已平仓（近 30 笔）</h2>
<span class="dim">%d 笔 · 点击展开</span></summary><div class="sec-body">
<table class="sticky"><tr><th>标的</th><th>市场</th><th>策略</th><th>买入 → 卖出</th>
<th>买价 → 卖价</th><th>盈亏</th><th>持有</th></tr>%s</table></div></details>
<div class="leg">纸面模拟：信号 T+1 开盘建仓、翻空收盘平仓 · 点标的 → 个股全景（行情/信号/共振/AI展望）· 点策略 → 策略纵切页（宽度/回测/寻优）· 不构成投资建议%s</div>
</div></body></html>""" % (
        CSS,
        gen_ts,
        stats,
        _nav_html(exclude="positions"),
        len(pend),
        p_rows,
        len(open_),
        o_rows,
        len(closed),
        c_rows,
        alert_foot,
    )
    os.makedirs(os.path.dirname(POSITIONS), exist_ok=True)
    tmp = POSITIONS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, POSITIONS)
    return POSITIONS


SNAP_DIR = os.path.join(HERE, "reports", "snap")
SNAP_KEEP = 60  # 快照保留上限，超出按名字排序删最旧


def write_snapshot(title, sections, tag=""):
    """按次存档页：reports/snap/时间戳-tag.html，飞书卡片按钮直达（永不失效）。
    返回文件名（不含路径）；失败返回 None。"""
    import re
    import time

    safe = re.sub(r"[^0-9A-Za-z_.\-]+", "_", tag)[:40] or "bt"
    name = "%s-%s.html" % (time.strftime("%Y%m%d-%H%M%S"), safe)
    try:
        os.makedirs(SNAP_DIR, exist_ok=True)
        with open(os.path.join(SNAP_DIR, name), "w", encoding="utf-8") as f:
            f.write(_page_html(title, sections))
        snaps = sorted(x for x in os.listdir(SNAP_DIR) if x.endswith(".html"))
        for old in snaps[:-SNAP_KEEP]:
            try:
                os.remove(os.path.join(SNAP_DIR, old))
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/report_page.py", exc)
        return name
    except Exception:
        return None
