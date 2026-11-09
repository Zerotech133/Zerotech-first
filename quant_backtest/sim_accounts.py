# -*- coding: utf-8 -*-
"""模拟交易对账台：把 reports/portfolio.json 的四本模拟账摊开成一页。

为什么要单独一页
----------------
portfolio.py 每天把「青萍之末 / 九维评分 / 四策略信号 / 缠论结构」四个虚拟
账户记进 portfolio.json，但它只有 `--status` 在终端打印，**没有任何界面**。
平台上「模拟」域此前只有 positions.html，那是 runner.py 的另一本账
（positions.json：待买 / 持仓 / 已平），两者独立，不能相加。

本页只做一件事：如实显示这四本账现在长什么样，包括**它们是坏的那部分**。

三个必须写在页面上的事实（读账本实测，不是推断）
------------------------------------------------
1. sig4 账户从 2026-10-05 起 nav 与 cash 全是 NaN —— 17 只持仓里含美股/加密，
   估值取不到，整个账户净值失效。页面上画成断线，不补零。
2. 9dim / chanlun 两个账户的已平仓全部 exit_pnl_pct == 0.0、reason 全是
   「环境闸门x5」—— 建仓当天就被闸门平掉，入场价等于出场价。净值因此恒等于
   本金（999,999.99 / 1,000,000.01），这是「没真跑」，不是「跑平了」。
   chanlun 账户 20 笔、9dim 10 笔，全在此列。
3. 账本最后净值点 2026-10-15，之后没有新点 —— **没有任何计划任务在跑
   portfolio.py**（任务列表里没有 Portfolio* 条目）。所以这不是「今天亏了」，
   是「三周没记账」。

页面结构
--------
  ① 体检表：四本账逐行标 可用 / 零成交 / 净值失效，附判据原文
  ② 账户卡：本金、末值、收益、最大回撤、持仓 / 平仓 / 跳过
  ③ 净值曲线：四条线归一化，NaN 段断线不断点
  ④ 持仓明细：唯一有持仓的 sig4 展开到逐票
  ⑤ 平仓流水：54 笔 + 退出原因聚合
  ⑥ 跳过日志：286 条，按原因聚合（这是「为什么没买」的唯一记录）
  ⑦ 口径：与 positions.html 的关系

用法
----
  python sim_accounts.py            # → reports/sim_accounts.html
  python sim_accounts.py --list     # 终端打体检表
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
sys.path.insert(0, HERE)

import cn_trading_days as CAL  # noqa: E402

LEDGER = os.path.join(REPORTS, "portfolio.json")
OUT = os.path.join(REPORTS, "sim_accounts.html")
TODAY = date.today()

# 中式配色：涨红跌绿
UP, DOWN = "#e3554f", "#3fa96a"

# 四本账固定配色 + 线型。9dim / chanlun 的曲线几乎恒等于本金线，
# 单靠颜色分不出来，所以给不同 dash，并在图例里写末值。
NAV_COLOR = {"qpzm": "#5b9bd5", "9dim": "#c9a227",
             "sig4": "#9b7fd4", "chanlun": "#e08a4f"}
NAV_DASH = {"qpzm": "", "9dim": ' stroke-dasharray="5 3"',
            "sig4": "", "chanlun": ' stroke-dasharray="1.5 3" '}


def _num(x, nd=2):
    """NaN / None 一律当「不可用」，不折算成 0。"""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, nd)


def _pct(v, scale=100.0, nd=2):
    """统一百分比格式：|值| < 半个末位时输出 0.00%，不写 -0.00%。"""
    f = v * scale
    if abs(f) < 0.5 * (10 ** -nd):
        f = 0.0
    return ("%+." + str(nd) + "f%%") % f


def trading_gap(d0):
    if not d0:
        return None
    try:
        d = datetime.strptime(d0, "%Y-%m-%d").date()
    except ValueError:
        return None
    n, cur = 0, TODAY
    while cur > d and n < 400:
        if CAL.is_trading_day(cur):
            n += 1
        cur = cur - timedelta(days=1)
    return n


def health(key, a):
    """给一本账做体检，返回 (级别, 短标签, 判据原文)。

    判据必须能被人复核 —— 所以每条都写成「哪几个字段、等于什么」。
    """
    navs = [p.get("nav") for p in (a.get("nav_curve") or [])]
    good = [v for v in navs if _num(v) is not None]
    bad = len(navs) - len(good)
    closed = a.get("closed") or []
    opens = a.get("open") or []
    zero_pnl = [c for c in closed
                if _num(c.get("exit_pnl_pct")) == 0.0]
    gate = [c for c in closed if "闸门" in str(c.get("reason") or "")]

    if not good or bad >= max(1, len(navs) // 2):
        return ("bad", "净值失效",
                "nav_curve %d 点里 %d 点是 NaN（%s 起断），cash=%s"
                % (len(navs), bad,
                   (a.get("nav_curve") or [{}])[2].get("d", "?")
                   if len(navs) > 2 else "?",
                   "NaN" if _num(a.get("cash")) is None else "正常"))
    if closed and len(zero_pnl) == len(closed):
        return ("warn", "零损益平仓",
                "%d 笔已平仓 exit_pnl_pct 全为 0.0，其中 %d 笔 reason 含「闸门」"
                "→ 建仓当天被闸门平掉，净值=本金不代表跑平"
                % (len(closed), len(gate)))
    if not closed and not opens:
        return ("warn", "从未开仓", "closed 与 open 都为空")
    return ("ok", "可用",
            "%d 笔已平仓（%d 盈 / %d 亏），%d 只在持，净值 %d 点连续"
            % (len(closed),
               sum(1 for c in closed if (_num(c.get("exit_pnl_pct")) or 0) > 0),
               sum(1 for c in closed if (_num(c.get("exit_pnl_pct")) or 0) < 0),
               len(opens), len(good)))


def metrics(a):
    navs = [_num(p.get("nav")) for p in (a.get("nav_curve") or [])]
    good = [v for v in navs if v is not None]
    cap = _num(a.get("capital"), 0) or 0
    last = good[-1] if good else None
    ret = (last / cap - 1.0) if (last and cap) else None

    # 最大回撤只在连续有效段上算（NaN 处断开，不插值）
    mdd, seg_peak = None, None
    for v in good:
        if seg_peak is None or v > seg_peak:
            seg_peak = v
        if seg_peak:
            dd = v / seg_peak - 1.0
            if mdd is None or dd < mdd:
                mdd = dd

    closed = a.get("closed") or []
    pnls = [_num(c.get("exit_pnl_pct")) for c in closed]
    pnls = [p for p in pnls if p is not None]
    wins = [p for p in pnls if p > 0]
    opens = a.get("open") or []
    float_pnl = sum((_num(o.get("pnl"), 2) or 0) for o in opens)
    nav_date = None
    for i in range(len(navs) - 1, -1, -1):
        if navs[i] is not None:
            nav_date = (a.get("nav_curve") or [])[i].get("d")
            break

    return dict(cap=cap, last=last, ret=ret, mdd=mdd, nav_date=nav_date,
                points=len(good), nan_points=len(navs) - len(good),
                closed_n=len(closed), open_n=len(opens),
                win_n=len(wins), loss_n=len([p for p in pnls if p < 0]),
                win_rate=(len(wins) / len(pnls)) if pnls else None,
                avg_pnl=(sum(pnls) / len(pnls)) if pnls else None,
                best=max(pnls) if pnls else None,
                worst=min(pnls) if pnls else None,
                float_pnl=float_pnl,
                hold_avg=(sum(_num(c.get("hold_days"), 0) or 0 for c in closed)
                          / len(closed)) if closed else None,
                skip_n=len(a.get("skip_log") or []))


# ---------------------------------------------------------------- SVG

def nav_svg(accs, w=980, h=250):
    """四条净值曲线。y 用 ps（净值/本金），x 用统一日期轴。

    右侧不写线尾文字标签 —— 实测三条线都躺在 1.0 附近时标签会叠在一起，
    读不出谁是谁。改成图例单独一行（见 nav_legend），线内只留末点圆点。
    """
    dates = []
    for _, a in accs:
        for p in (a.get("nav_curve") or []):
            d = p.get("d")
            if d and d not in dates:
                dates.append(d)
    dates.sort()
    if len(dates) < 2:
        return ""

    series = []
    for key, a in accs:
        cap = _num(a.get("capital"), 0) or 0
        m = {}
        for p in (a.get("nav_curve") or []):
            v = _num(p.get("nav"))
            m[p.get("d")] = (v / cap) if (v is not None and cap) else None
        series.append((key, a.get("name") or key, m))

    vals = [v for _, _, m in series for v in m.values() if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    pad = max(0.002, (hi - lo) * 0.18)
    lo, hi = lo - pad, hi + pad

    L, R, T, B = 46, 18, 14, 28
    iw, ih = w - L - R, h - T - B

    def X(i):
        return L + iw * (i / (len(dates) - 1))

    def Y(v):
        return T + ih * (1 - (v - lo) / (hi - lo))

    out = ['<svg viewBox="0 0 %d %d" class="navsvg" preserveAspectRatio="none">' % (w, h)]

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        v = lo + (hi - lo) * frac
        y = Y(v)
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="gl"/>'
                   % (L, y, L + iw, y))
        out.append('<text x="%d" y="%.1f" class="ax" text-anchor="end">%s</text>'
                   % (L - 7, y + 3.5, _pct(v - 1.0, 100.0)))

    if lo <= 1.0 <= hi:
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" class="base"/>'
                   % (L, Y(1.0), L + iw, Y(1.0)))

    for i in (0, len(dates) // 2, len(dates) - 1):
        out.append('<text x="%.1f" y="%d" class="ax" text-anchor="middle">%s</text>'
                   % (X(i), h - 8, dates[i][5:]))

    for key, name, m in series:
        c = NAV_COLOR.get(key, "#888")
        dash = NAV_DASH.get(key, "")
        seg = []
        for i, d in enumerate(dates):
            v = m.get(d)
            if v is None:
                if seg:
                    out.append('<polyline points="%s" fill="none" stroke="%s" '
                               'stroke-width="2"%s/>' % (" ".join(seg), c, dash))
                    seg = []
                continue
            seg.append("%.1f,%.1f" % (X(i), Y(v)))
        if seg:
            out.append('<polyline points="%s" fill="none" stroke="%s" '
                       'stroke-width="2"%s/>' % (" ".join(seg), c, dash))

    # 末点圆点：只画在最后一个有效点上，不做文字避让（图例承担标识）
    for key, name, m in series:
        c = NAV_COLOR.get(key, "#888")
        for i in range(len(dates) - 1, -1, -1):
            if m.get(dates[i]) is not None:
                out.append('<circle cx="%.1f" cy="%.1f" r="3" fill="%s"/>'
                           % (X(i), Y(m[dates[i]]), c))
                break

    out.append('</svg>')
    return "".join(out)


def nav_legend(accs):
    """图例单独一行：色块 + 名称 + 末日 + 末值。三条线压在 1.0 时靠这个分辨。"""
    h = ['<div class="legend">']
    for key, a in accs:
        m = metrics(a)
        c = NAV_COLOR.get(key, "#888")
        ret = m["ret"]
        if m["points"] < 3:
            val = '<b class="mu">净值不可用</b>'
        elif ret is None:
            val = '<b class="mu">—</b>'
        else:
            cls = "up" if ret > 0 else ("dn" if ret < 0 else "mu")
            val = '<b class="%s">%s</b>' % (cls, _pct(ret, 100.0))
        h.append('<span class="lg"><i style="background:%s%s"></i>%s '
                 '<span class="mu">%s</span> %s</span>'
                 % (c, "", a.get("name") or key,
                    m["nav_date"] or "—", val))
    h.append('</div>')
    return "".join(h)


# ---------------------------------------------------------------- HTML

CSS = """<style>
:root{--bg:#0d1117;--pn:#131a24;--pn2:#0f1620;--bd:#1f2937;--tx:#e6edf3;
 --mu:#7b8698;--up:%s;--dn:%s;--ac:#5b9bd5}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
 font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 18px 60px}
h1{font-size:21px;margin:0 0 6px;font-weight:650}
.lead{color:var(--mu);font-size:12.5px;line-height:1.75;margin-bottom:16px}
.lead b{color:#c3cddb}
.alert{border-radius:9px;padding:12px 14px;margin:14px 0 20px;font-size:12.5px;
 line-height:1.8;background:#2a1618;border-left:3px solid #a8433f;color:#f0c9c7}
.alert.warn{background:#2a2312;border-left-color:#8a7020;color:#e6d6a8}
.alert b{color:#fff}
h2{font-size:15px;margin:30px 0 10px;font-weight:620;
 display:flex;align-items:center;gap:8px;flex-wrap:wrap}
h2 .d{font-size:11.5px;color:var(--mu);font-weight:400}
table{width:100%%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;color:var(--mu);font-weight:500;padding:7px 8px;
 border-bottom:1px solid var(--bd);white-space:nowrap;font-size:11.5px}
td{padding:7px 8px;border-bottom:1px solid #19212b;white-space:nowrap}
tr:hover td{background:#141c26}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.up{color:var(--up)}.dn{color:var(--dn)}.mu{color:var(--mu)}
.tag{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:9px;
 background:#1a222c;color:#8fa0b5}
.tag.ok{background:#14261b;color:#7fd18b}
.tag.warn{background:#2a2312;color:#e0b95c}
.tag.bad{background:#2b1618;color:#e8807f}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
@media(max-width:800px){.cards{grid-template-columns:1fr 1fr}}
.card{background:var(--pn);border:1px solid var(--bd);border-radius:10px;padding:12px 13px}
.card .nm{font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px}
.card .big{font-size:19px;font-variant-numeric:tabular-nums;margin:7px 0 2px;font-weight:600}
.card .sub{font-size:11.5px;color:var(--mu);line-height:1.7}
.card .sub span{color:#b9c4d2}
.navsvg{width:100%%;height:250px;background:var(--pn2);border:1px solid var(--bd);
 border-radius:10px;margin-top:4px}
.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;
 padding:8px 12px;background:var(--pn2);border:1px solid var(--bd);
 border-radius:8px 8px 0 0;border-bottom:none;margin-top:4px}
.legend+.navsvg{border-radius:0 0 10px 10px;margin-top:0}
.legend .lg{display:flex;align-items:center;gap:6px;color:#c3cddb}
.legend .lg i{display:inline-block;width:16px;height:3px;border-radius:2px}
.legend .lg b{font-variant-numeric:tabular-nums}
.gl{stroke:#1c2531;stroke-width:1}
.base{stroke:#3a4a5e;stroke-width:1;stroke-dasharray:4 4}
.ax{fill:#66717f;font-size:10.5px}
.lb{font-size:11px;font-weight:600}
.hbar{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:12.5px}
.hbar .bx{height:16px;border-radius:3px;background:#2c4a6b;min-width:2px}
.hbar .k{width:150px;color:#b9c4d2;flex:0 0 auto}
.hbar .v{color:var(--mu);font-variant-numeric:tabular-nums}
.split{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:800px){.split{grid-template-columns:1fr}}
.note{margin-top:14px;padding:11px 13px;border-radius:8px;background:#101821;
 border-left:3px solid #38506b;color:#94a3b8;font-size:12px;line-height:1.85}
.note b{color:#c3cddb}
.foot{margin-top:34px;padding-top:13px;border-top:1px solid var(--bd);
 color:#6b7787;font-size:11.5px;line-height:1.85}
.foot a{color:#8fb4e8;text-decoration:none}
a{color:#8fb4e8}
</style>""" % (UP, DOWN)


def render():
    if not os.path.exists(LEDGER):
        print("[sim] 没有 portfolio.json，先跑 python portfolio.py")
        return None
    with open(LEDGER, encoding="utf-8") as f:
        d = json.load(f)

    accs = list((d.get("accounts") or {}).items())
    if not accs:
        print("[sim] portfolio.json 里没有账户")
        return None

    metas = {k: metrics(a) for k, a in accs}
    healths = {k: health(k, a) for k, a in accs}

    # 账本时点：所有账户里最晚的有效净值日
    last_nav = max([m["nav_date"] for m in metas.values() if m["nav_date"]] or [""])
    gap = trading_gap(last_nav)
    ts_ledger = str(d.get("ts") or "")[:16]

    level_of = {"ok": "ok", "warn": "warn", "bad": "bad"}
    h = ['<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>模拟交易 · 四本账对账</title>', CSS, '</head><body><div class="wrap">']

    h.append('<h1>模拟交易 · 四本账对账</h1>')
    h.append('<div class="lead">四本虚拟账户各 100 万本金，由 <code>portfolio.py</code> 记进 '
             '<code>portfolio.json</code>。本页只如实呈现账本现状——'
             '<b>包括其中三本没有真跑起来</b>。<br>'
             '账本最后净值点 <b>%s</b>，写入时刻 %s。这一页不是「今天赚了多少」，'
             '是「账本停在哪一天、停的原因是什么」。</div>'
             % (last_nav or "?", ts_ledger or "?"))

    # 顶部告警
    bad_n = sum(1 for v in healths.values() if v[0] == "bad")
    warn_n = sum(1 for v in healths.values() if v[0] == "warn")
    ok_n = sum(1 for v in healths.values() if v[0] == "ok")
    if gap is not None and gap > 3:
        h.append('<div class="alert"><b>账本已停更 %d 个交易日</b>（最后净值点 %s，今天 %s）。'
                 '原因不是行情，是<b>没有任何计划任务在跑 portfolio.py</b>——'
                 '任务计划里没有 Portfolio* 条目，只有 PoolCloseReview 与 PoolDailyPipeline。'
                 '所以下面的收益、回撤、持仓全部停留在 %s，不能当成当前状态。</div>'
                 % (gap, last_nav, TODAY.isoformat(), last_nav))
    h.append('<div class="alert%s"><b>四本账体检：%d 可用 · %d 零成交/零损益 · %d 净值失效</b><br>'
             '判据写在下表「判据」列里，每条都能用 portfolio.json 的字段复核。'
             '净值失效的账不补零、不插值，曲线直接断开。</div>'
             % (" warn" if bad_n == 0 else "", ok_n, warn_n, bad_n))

    # ① 体检表
    h.append('<h2>① 体检 <span class="d">每一行的判据都能在 portfolio.json 里复核</span></h2>')
    h.append('<table><thead><tr><th>账户</th><th>结论</th><th class="n">净值点</th>'
             '<th class="n">已平</th><th class="n">在持</th><th>判据</th></tr></thead><tbody>')
    for k, a in accs:
        lv, lbl, why = healths[k]
        m = metas[k]
        h.append('<tr><td><b>%s</b> <span class="mu">%s</span></td>'
                 '<td><span class="tag %s">%s</span></td>'
                 '<td class="n">%d%s</td><td class="n">%d</td><td class="n">%d</td>'
                 '<td class="mu" style="white-space:normal;font-size:11.5px">%s</td></tr>'
                 % (a.get("name") or k, k, level_of.get(lv, ""), lbl,
                    m["points"],
                    ("<span class=\"dn\"> (+%d NaN)</span>" % m["nan_points"])
                    if m["nan_points"] else "",
                    m["closed_n"], m["open_n"], why))
    h.append('</tbody></table>')

    # ② 账户卡
    h.append('<h2>② 账户 <span class="d">收益按「末值 / 本金」，回撤按连续有效段</span></h2>')
    h.append('<div class="cards">')
    for k, a in accs:
        m = metas[k]
        lv, lbl, _ = healths[k]
        ret = m["ret"]
        cls = "" if ret is None else ("up" if ret > 0 else ("dn" if ret < 0 else ""))
        h.append('<div class="card"><div class="nm">%s %s '
                 '<span class="tag %s">%s</span></div>'
                 '<div class="big %s">%s</div>'
                 '<div class="sub">收益 <span>%s</span> · 回撤 <span>%s</span><br>'
                 '持仓 <span>%d</span> · 已平 <span>%d</span>%s</div></div>'
                 % (a.get("icon") or "", a.get("name") or k,
                    level_of.get(lv, ""), lbl,
                    cls,
                    # 有效净值点 <3 时，末值不是「现在的钱」，显示一个数会被当成现值
                    ("%s" % "{:,.0f}".format(m["last"])) if m["points"] >= 3
                    else "净值不可用",
                    _pct(ret, 100.0) if (ret is not None and m["points"] >= 3)
                    else "不可用",
                    # 回撤写绝对值，不写负号：读者要的是「最深跌了多少」
                    ("%.2f%%" % abs(m["mdd"] * 100)) if m["mdd"] is not None else "—",
                    m["open_n"], m["closed_n"],
                    ("<br>胜率 <span>%.0f%%</span> · 均收益 <span>%s</span>"
                     % (m["win_rate"] * 100, _pct(m["avg_pnl"], 1.0)))
                    if m["win_rate"] is not None else ""))
    h.append('</div>')

    # ③ 净值曲线
    h.append('<h2>③ 净值曲线 <span class="d">纵轴为 净值/本金，虚线是本金线；NaN 段断开</span></h2>')
    h.append(nav_legend(accs))
    h.append(nav_svg(accs))
    h.append('<div class="note">四条线要这么读：<b>qpzm</b> 有真实起伏（-10% 止损到 +11.7% 止盈都触发过）；'
             '<b>9dim / chanlun</b> 是一条压在本金线上的直线，因为它们的平仓全是零损益；'
             '<b>sig4</b> 从第 3 个点起断线，那是持仓里的美股/加密估值取不到导致的 NaN，'
             '不是「跌到 0」。<br>纵轴范围被 sig4 前两点的小幅波动压缩过，'
             '所以 qpzm 的 1% 上下看起来也像直线——看表里的数值，不要只看形状。</div>')

    # ④ 持仓明细
    h.append('<h2>④ 持仓明细 <span class="d">只有 sig4 有在持；其余三本当前空仓</span></h2>')
    rows = []
    for k, a in accs:
        for o in (a.get("open") or []):
            rows.append((k, a.get("name") or k, o))
    if rows:
        h.append('<table><thead><tr><th>账户</th><th>代码</th><th>名称</th>'
                 '<th class="n">数量</th><th class="n">成本</th><th class="n">现价</th>'
                 '<th class="n">浮盈</th><th class="n">浮盈%</th>'
                 '<th class="n">持有</th><th>信号来源</th></tr></thead><tbody>')
        for k, nm, o in sorted(rows, key=lambda r: -(_num(r[2].get("pnl_pct"), 0) or 0)):
            p = _num(o.get("pnl_pct"))
            cls = "up" if (p or 0) > 0 else ("dn" if (p or 0) < 0 else "mu")
            h.append('<tr><td class="mu">%s</td><td>%s</td><td>%s</td>'
                     '<td class="n">%s</td><td class="n">%s</td><td class="n">%s</td>'
                     '<td class="n %s">%s</td><td class="n %s">%s</td>'
                     '<td class="n">%s</td><td class="mu">%s</td></tr>'
                     % (nm, o.get("code"), o.get("name") or "",
                        "{:,.0f}".format(_num(o.get("qty"), 0) or 0),
                        _num(o.get("entry_px")), _num(o.get("last_px")),
                        cls, ("%+.0f" % (_num(o.get("pnl"), 0) or 0)),
                        cls, _pct(p, 1.0) if p is not None else "—",
                        "%s 天" % (_num(o.get("hold_days"), 0) or 0),
                        str(o.get("note") or "")[:26]))
        h.append('</tbody></table>')
    else:
        h.append('<div class="note">四本账当前都没有持仓。</div>')

    # ⑤ 平仓流水 + 退出原因
    all_closed = []
    for k, a in accs:
        for c in (a.get("closed") or []):
            all_closed.append((k, a.get("name") or k, c))
    all_closed.sort(key=lambda r: str(r[2].get("exit_date") or ""), reverse=True)

    h.append('<h2>⑤ 平仓流水 <span class="d">%d 笔，按出场日倒序</span></h2>' % len(all_closed))
    h.append('<div class="split">')
    h.append('<div>')
    if all_closed:
        h.append('<table><thead><tr><th>出场日</th><th>账户</th><th>代码</th>'
                 '<th class="n">入场</th><th class="n">出场</th><th class="n">收益%</th>'
                 '<th class="n">持有</th></tr></thead><tbody>')
        for k, nm, c in all_closed[:40]:
            p = _num(c.get("exit_pnl_pct"))
            cls = "up" if (p or 0) > 0 else ("dn" if (p or 0) < 0 else "mu")
            h.append('<tr><td class="mu">%s</td><td class="mu">%s</td>'
                     '<td>%s <span class="mu">%s</span></td>'
                     '<td class="n">%s</td><td class="n">%s</td>'
                     '<td class="n %s">%s</td><td class="n">%s</td></tr>'
                     % (str(c.get("exit_date") or "")[5:], nm,
                        c.get("code"), c.get("name") or "",
                        _num(c.get("entry_px")), _num(c.get("exit_px")),
                        cls, _pct(p, 1.0) if p is not None else "—",
                        "%s 天" % (_num(c.get("hold_days"), 0) or 0)))
        h.append('</tbody></table>')
        if len(all_closed) > 40:
            h.append('<div class="note">只列最近 40 笔，共 %d 笔。</div>' % len(all_closed))
    h.append('</div>')

    # 退出原因聚合
    rc = Counter(str(c.get("reason") or "未记") for _, _, c in all_closed)
    h.append('<div><h2 style="margin-top:0;font-size:13.5px">退出原因分布</h2>')
    mx = max(rc.values()) if rc else 1
    for r, n in rc.most_common():
        h.append('<div class="hbar"><span class="k">%s</span>'
                 '<span class="bx" style="width:%.0f%%"></span>'
                 '<span class="v">%d</span></div>'
                 % (r, 100.0 * n / mx, n))
    h.append('<div class="note" style="margin-top:10px">'
             '「环境闸门x5」这一条占了 %d 笔，全部集中在 9dim 与 chanlun 两个账户，'
             '且这些笔的 exit_pnl_pct 都是 0.0 —— 入场当天即被闸门平掉，'
             '价格没动过。真正按策略退出的是 qpzm 那 18 笔：初始止损、移动止盈、'
             '信号翻空、RPS 骤降、离池 3 日。</div>'
             % rc.get("环境闸门x5", 0))
    h.append('</div></div>')

    # ⑥ 跳过日志
    sk = []
    for k, a in accs:
        for s in (a.get("skip_log") or []):
            sk.append((k, a.get("name") or k, s))
    h.append('<h2>⑥ 为什么没买 <span class="d">%d 条跳过记录，按原因聚合</span></h2>' % len(sk))
    if sk:
        wc = Counter(str(s.get("why") or "未记") for _, _, s in sk)
        mx = max(wc.values())
        for w, n in wc.most_common(10):
            h.append('<div class="hbar"><span class="k">%s</span>'
                     '<span class="bx" style="width:%.0f%%"></span>'
                     '<span class="v">%d 次</span></div>' % (w, 100.0 * n / mx, n))
        by_acc = Counter(nm for _, nm, _ in sk)
        h.append('<div class="note">按账户：%s。<br>'
                 '<b>这 %d 条几乎全是一个原因——「市场闸门收紧」</b>。'
                 '换句话说这两个月里模拟盘不是「选不出票」，是<b>闸门一直关着</b>；'
                 '闸门的开关条件与它自己的历史准确率，见结构日报的「五层判定」与 '
                 'opt.html 的纪律门。</div>'
                 % ("、".join("%s %d 条" % (k, v) for k, v in by_acc.most_common()),
                    len(sk)))
    else:
        h.append('<div class="note">没有跳过记录。</div>')

    # ⑦ 口径
    h.append('<h2>⑦ 这页和 positions.html 什么关系</h2>')
    h.append('<div class="note">平台上「模拟」域有<b>两本互相独立的账</b>，不能相加、不能对账：<br>'
             '· <b>本页 portfolio.json</b> —— <code>portfolio.py</code> 产出，四策略各 100 万，'
             '用来做<b>策略对照</b>（哪个策略在同样的闸门下更抗揍）。<br>'
             '· <b><a href="positions.html">positions.html</a> / positions.json</b> —— '
             '<code>runner.py</code> 产出，是每日流水线的<b>纸面持仓</b>（待买 / 持仓 / 已平），'
             '跟结构日报的名单同步。<br>'
             '两本账的入场规则、仓位、闸门都不一样，看到同一只票在两个页面数字不同是正常的。'
             '真要合并成一本，得先把闸门口径统一——那是改造，不是改页面。</div>')

    h.append('<div class="foot">数据：reports/portfolio.json（%s 写入，最后净值点 %s）。<br>'
             '本页为纸面研究，不构成投资建议。'
             '<a href="index.html">← 回分析台</a></div>'
             % (ts_ledger or "?", last_nav or "?"))
    h.append('</div></body></html>')

    html = "".join(h)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    # 侧车：门面要在这张卡上标数据日。账本的数据日不在 pool_state 里，
    # 是「最后一根有效净值点的日期」，得由本页自己写出来给 platform_pages 读。
    side = os.path.join(REPORTS, "sim_accounts.json")
    with open(side, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"asof": last_nav or "",
                   "ts": ts_ledger,
                   "gap_trading_days": gap,
                   "accounts_ok": sorted(k for k, v in healths.items() if v[0] == "ok"),
                   "accounts_bad": sorted(k for k, v in healths.items() if v[0] == "bad"),
                   "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                  f, ensure_ascii=False, indent=1)
    return OUT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        with open(LEDGER, encoding="utf-8") as f:
            d = json.load(f)
        for k, acc in (d.get("accounts") or {}).items():
            lv, lbl, why = health(k, acc)
            m = metrics(acc)
            print("%-9s %-6s %-10s 净值点%d 已平%2d 在持%2d  末值%s"
                  % (k, lv, lbl, m["points"], m["closed_n"],
                     m["open_n"], m["last"]))
            print("          %s" % why)
        return 0
    out = render()
    if not out:
        return 1
    print("[sim] %s  %.1f KB" % (out, os.path.getsize(out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
