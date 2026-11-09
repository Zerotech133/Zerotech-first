# -*- coding: utf-8 -*-
"""缠论结构分析工具：全市场结构画像 + 单票解剖。

和已有两页的分工（三页不是同一件事的三种排版）
----------------------------------------------
  pool_report_h5.html  手机版日报 —— 今天有哪些票，一屏一档滑着看。
  pool_pc.html         PC 工作台 —— 选中一只，中间画 K 线与中枢，右边看判据。
  本页 chanlun_tool    **分析工具** —— 两个现在没有的视角：
                        ① 全市场结构画像（3961 只判过的票，九态 / 趋势×位置 /
                           中枢构成 / 买卖点年龄 / 背驰标注，一次看全）
                        ② 单票解剖（任意代码或名称 → 结构链逐条实测值 +
                           九态判定因果，不是只给一个结论标签）

为什么值得单独一页
------------------
日报与工作台回答的是「买哪个」，都只给结论和少量证据。要回答「这个结论是
怎么来的、这批票的结构整体在什么位置」，得有一页把**判定链**和**全池分布**
摊开。这两件事都不能靠翻 3961 张卡片完成。

只用 pool_state 的字段，不重算结构
--------------------------------
每一行都标出取自哪个字段，页面上的判断可被 json 复核。九态、中枢、买卖点、
背驰全部来自 pool_state_*.json（由 pool_state.py --scan --save 产出），
本页一个数都不另算——避免「页面口径」与「产线口径」分叉。

结论必须写清楚（不给错觉）
--------------------------
结构不是收益信号：板块层 46,584 观测回放，买点类 20 日超额 −13bp（t=−0.58），
强度分层无单调性。这页是解剖工具，不是选股依据。

用法
----
  python chanlun_tool.py            # → reports/chanlun_tool.html
  python chanlun_tool.py --stats    # 终端打全市场画像
"""

import argparse
import glob
import json
import os
import sys
from collections import Counter, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
sys.path.insert(0, HERE)

OUT = os.path.join(REPORTS, "chanlun_tool.html")

# 九态显示顺序：从最前置的待确认到最下游的卖点
STAGES = ["三买待", "三买", "二买", "一买", "背驰", "离开段", "中枢", "中枢下", "三卖"]

STAGE_DESC = {
    "三买待": "价格逼近中枢上沿，尚未回抽确认",
    "三买": "离开中枢后回抽不回到中枢内",
    "二买": "转折后首次回调不破前低",
    "一买": "下跌末端出现背驰后转折",
    "背驰": "离开段力度弱于进入段（MACD 面积对比）",
    "离开段": "已离开中枢，尚未回抽",
    "中枢": "价格在中枢区间内震荡",
    "中枢下": "价格在中枢下沿之下",
    "三卖": "反抽不回中枢，趋势转弱",
}

STAGE_COLOR = {
    "三买": "#e3554f", "三买待": "#e08a4f", "二买": "#d4694f", "一买": "#c05a7a",
    "背驰": "#c9a227", "离开段": "#6b8cae", "中枢": "#5b7fa6",
    "中枢下": "#4a7a63", "三卖": "#3fa96a",
}

# 内嵌字段顺序（前端按下标取，省一半体积）
COLS = ["code", "name", "stage", "bucket", "close", "pct", "zg", "zd", "pos",
        "dist_zg", "dist_zd", "n_piv", "piv_sdt", "piv_edt", "piv_solid",
        "piv_wide", "piv_over", "trend", "bi_dir", "bi_sdt", "bi_edt", "bi_amp",
        "bi_live", "n_bi", "seg_dir", "n_seg", "sig_type", "sig_dt", "sig_price",
        "sig_zg", "sig_note", "sig_age", "sig_fresh", "sig_gain", "bei", "score",
        "amt20_yi", "mv_yi"]

# 非买点档只嵌这些字段：中枢 / 中枢下 / 三卖 共 3324 只，解剖价值低，
# 全字段会让页面从 400KB 涨到 1.3MB，而它们的主要用途只是「搜得到、知道在哪档」。
SLIM = ["code", "name", "stage", "close", "pct", "zg", "zd", "pos", "dist_zg",
        "dist_zd", "trend", "n_piv", "sig_type", "sig_dt", "sig_age", "sig_note"]
FULL_STAGES = ("三买待", "三买", "二买", "一买", "背驰", "离开段")

# 这些字段取值重复度极高（sig_note 只有 7 种、stage 9 种），存字典下标而不是原文
DICT_FIELDS = ("stage", "pos", "trend", "bucket", "bei", "sig_type", "sig_note")


def newest(pat):
    fs = sorted(glob.glob(os.path.join(REPORTS, pat)))
    return fs[-1] if fs else None


def load():
    p = newest("pool_state_*.json")
    if not p:
        print("[tool] 没有 pool_state_*.json，先跑 pool_state.py --scan --save")
        return None, None, None
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    asof = str(d.get("data_asof") or d.get("asof") or "")
    rows = []
    for stage in STAGES:
        for r in (d.get("buckets", {}).get(stage) or []):
            rows.append(r)
    return d, rows, asof


# ---------------------------------------------------------------- 统计

def stats(rows):
    n = len(rows)
    st = Counter(r.get("stage") for r in rows)
    trend = Counter(r.get("trend") for r in rows)
    pos = Counter(r.get("pos") for r in rows)

    # 趋势 × 位置 交叉
    cross = OrderedDict()
    for t in ("上涨", "下跌", "盘整"):
        cross[t] = Counter()
    for r in rows:
        t = r.get("trend") or "?"
        if t not in cross:
            cross[t] = Counter()
        cross[t][r.get("pos") or "?"] += 1

    # 中枢构成
    piv_n = [r.get("n_piv") or 0 for r in rows]
    solid = sum(1 for r in rows if r.get("piv_solid"))
    wide = sum(1 for r in rows if r.get("piv_wide"))
    over = sum(1 for r in rows if r.get("piv_over"))
    nbi = [r.get("piv_n_bi") or 0 for r in rows]

    # 中枢宽度（(zg-zd)/zd）
    widths = []
    for r in rows:
        zg, zd = r.get("zg"), r.get("zd")
        try:
            zg, zd = float(zg), float(zd)
        except (TypeError, ValueError):
            continue
        if zd > 0 and zg > zd:
            widths.append((zg - zd) / zd)
    wbucket = Counter()
    for w in widths:
        if w < 0.03:
            wbucket["<3%"] += 1
        elif w < 0.06:
            wbucket["3-6%"] += 1
        elif w < 0.10:
            wbucket["6-10%"] += 1
        elif w < 0.20:
            wbucket["10-20%"] += 1
        else:
            wbucket[">20%"] += 1

    # 买卖点年龄
    age_b = Counter()
    for r in rows:
        if not r.get("sig_type"):
            continue
        a = r.get("sig_age")
        if a is None:
            continue
        if a <= 5:
            age_b["≤5 日"] += 1
        elif a <= 20:
            age_b["6-20 日"] += 1
        elif a <= 60:
            age_b["21-60 日"] += 1
        else:
            age_b[">60 日"] += 1

    bei = Counter(r.get("bei") for r in rows if r.get("bei"))
    sig_types = Counter(r.get("sig_type") for r in rows if r.get("sig_type"))

    scores = [r.get("score") for r in rows if isinstance(r.get("score"), (int, float))]
    return dict(n=n, st=st, trend=trend, pos=pos, cross=cross,
                solid=solid, wide=wide, over=over,
                piv_n_avg=(sum(piv_n) / n if n else 0),
                nbi_avg=(sum(nbi) / n if n else 0),
                wbucket=wbucket, w_n=len(widths),
                age=age_b, bei=bei, sig_types=sig_types,
                score_avg=(sum(scores) / len(scores) if scores else 0),
                score_max=(max(scores) if scores else 0))


# ---------------------------------------------------------------- HTML

CSS = """<style>
:root{--bg:#0d1117;--pn:#131a24;--pn2:#0f1620;--bd:#1f2937;--tx:#e6edf3;
 --mu:#7b8698;--up:#e3554f;--dn:#3fa96a;--ac:#5b9bd5}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
 font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 18px 60px}
h1{font-size:20px;margin:0 0 6px;font-weight:650}
.lead{color:var(--mu);font-size:12.5px;line-height:1.8;margin-bottom:16px}
.lead b{color:#c3cddb}
h2{font-size:15px;margin:28px 0 10px;font-weight:620;display:flex;
 align-items:center;gap:8px;flex-wrap:wrap}
h2 .d{font-size:11.5px;color:var(--mu);font-weight:400}
h3{font-size:13px;margin:16px 0 8px;color:#b9c4d2;font-weight:600}
.panel{background:var(--pn);border:1px solid var(--bd);border-radius:10px;padding:13px 14px}
.split{display:grid;grid-template-columns:1fr 1fr;gap:14px;align-items:start}
.split3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;align-items:start}
@media(max-width:820px){.split,.split3{grid-template-columns:1fr}}
table{width:100%%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;color:var(--mu);font-weight:500;padding:6px 8px;
 border-bottom:1px solid var(--bd);font-size:11.5px;white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid #19212b;white-space:nowrap}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr:hover td{background:#141c26}
.up{color:var(--up)}.dn{color:var(--dn)}.mu{color:var(--mu)}
.bar{display:flex;align-items:center;gap:8px;margin:4px 0;font-size:12.5px}
.bar .k{width:132px;flex:0 0 auto;color:#c3cddb;line-height:1.25}
.bar .bx{height:17px;border-radius:3px;min-width:2px;flex:0 1 auto}
.bar .v{color:var(--mu);font-variant-numeric:tabular-nums;font-size:11.5px;
 white-space:nowrap}
.tag{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:9px;
 background:#1a222c;color:#8fa0b5}
.tag.ok{background:#14261b;color:#7fd18b}
.tag.warn{background:#2a2312;color:#e0b95c}
.tag.bad{background:#2b1618;color:#e8807f}
.note{margin-top:12px;padding:11px 13px;border-radius:8px;background:#101821;
 border-left:3px solid #38506b;color:#94a3b8;font-size:12px;line-height:1.85}
.note b{color:#c3cddb}
.foot{margin-top:32px;padding-top:13px;border-top:1px solid var(--bd);
 color:#6b7787;font-size:11.5px;line-height:1.85}
.foot a,a{color:#8fb4e8;text-decoration:none}
/* —— 解剖器 —— */
.search{display:flex;gap:8px;align-items:center;margin-bottom:12px}
.search input{flex:1;background:var(--pn2);border:1px solid #2b3846;color:var(--tx);
 border-radius:8px;padding:9px 12px;font-size:13.5px;outline:none}
.search input:focus{border-color:#3f5a78}
.search .cnt{color:var(--mu);font-size:11.5px;white-space:nowrap}
.cand{position:absolute;z-index:20;background:#16202b;border:1px solid #2b3846;
 border-radius:8px;margin-top:2px;max-height:300px;overflow:auto;width:min(520px,86vw);
 box-shadow:0 8px 24px rgba(0,0,0,.5);display:none}
.cand div{padding:7px 11px;font-size:12.5px;cursor:pointer;display:flex;
 gap:8px;align-items:center}
.cand div:hover,.cand div.on{background:#1f2c39}
.cand .c{font-variant-numeric:tabular-nums;color:#9fb2c8}
.dhead{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:4px 0 12px}
.dhead .nm{font-size:19px;font-weight:650}
.dhead .cd{font-size:12.5px;color:var(--mu);font-variant-numeric:tabular-nums}
.kv{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--bd);
 border:1px solid var(--bd);border-radius:8px;overflow:hidden;margin-bottom:14px}
@media(max-width:700px){.kv{grid-template-columns:1fr 1fr}}
.kv .c{background:var(--pn2);padding:9px 11px}
.kv .c .l{font-size:10.5px;color:var(--mu)}
.kv .c .v{font-size:14px;font-variant-numeric:tabular-nums;margin-top:1px}
.chain td:first-child{color:var(--mu);width:112px}
.chain td:nth-child(2){font-variant-numeric:tabular-nums;color:#dbe4ee}
.chain td:nth-child(3){white-space:normal;color:#9fb2c8;font-size:11.5px}
.empty{color:var(--mu);padding:26px 0;text-align:center;font-size:13px}
</style>"""


def bar(label, n, mx, color, suffix=""):
    w = max(1, int(100.0 * n / mx)) if mx else 1
    return ('<div class="bar"><span class="k">%s</span>'
            '<span class="bx" style="width:%.1f%%;background:%s"></span>'
            '<span class="v">%s%s</span></div>'
            % (label, w * 0.90, color, "{:,}".format(n), suffix))


def render():
    d, rows, asof = load()
    if not rows:
        return None
    meta = d.get("meta") or {}
    s = stats(rows)
    n = s["n"]

    h = ['<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>缠论结构分析工具</title>', CSS, '</head><body><div class="wrap">']

    h.append('<h1>缠论结构分析工具</h1>')
    h.append('<div class="lead">结构基准日 <b>%s-%s-%s</b> · 判过 <b>%s</b> 只 · '
             '扫描 %s 个数据文件 · 用时 %s 秒。<br>'
             '这一页回答两个问题：<b>全市场的结构现在分布在哪</b>，'
             '以及<b>某一只票的九态结论是怎么一步步判出来的</b>。'
             '所有数值都取自 <code>pool_state</code> 的原始字段，不重算、不改口径。</div>'
             % (asof[:4], asof[4:6], asof[6:8], "{:,}".format(n),
                meta.get("files", "?"), meta.get("elapsed", "?")))

    # ---------- ① 九态分布
    h.append('<h2>① 九态分布 <span class="d">同一套结构口径下的全池状态</span></h2>')
    mx = max(s["st"].values()) if s["st"] else 1
    h.append('<div class="split"><div class="panel">')
    for st in STAGES:
        c = s["st"].get(st, 0)
        h.append(bar(st, c, mx, STAGE_COLOR.get(st, "#5b7fa6"),
                     "  ·  %.1f%%" % (100.0 * c / n)))
    h.append('</div><div class="panel">')
    h.append('<h3 style="margin-top:0">怎么读这九档</h3>')
    h.append('<table><tbody>')
    for st in ("三买", "三买待", "二买", "一买", "背驰", "离开段", "中枢", "中枢下", "三卖"):
        h.append('<tr><td style="color:%s;width:64px"><b>%s</b></td>'
                 '<td class="mu" style="white-space:normal">%s</td></tr>'
                 % (STAGE_COLOR.get(st, "#888"), st, STAGE_DESC.get(st, "")))
    h.append('</tbody></table>')
    h.append('<div class="note" style="margin-top:12px">'
             '九档不是九个买点。真正构成本级别买点的只有<b>一类、二类、三类</b>；'
             '「离开段」「中枢」「中枢下」「三卖」是位置描述，「背驰」是力度描述。'
             '把它们并排放在一张榜上，是因为它们共用同一套笔/段/中枢计算，'
             '不是因为它们都是看涨理由。</div>')
    h.append('</div></div>')

    # ---------- ② 趋势 × 位置
    h.append('<h2>② 趋势 × 位置 <span class="d">结构判定的两个自变量，交叉看才知道票在哪</span></h2>')
    h.append('<div class="split">')
    h.append('<div class="panel"><h3 style="margin-top:0">交叉表</h3>')
    pos_keys = [p for p in ("上沿外", "枢内", "下沿外", "无枢")
                if s["pos"].get(p)] or ["上沿外", "枢内", "下沿外"]
    h.append('<table><thead><tr><th>趋势 \\ 位置</th>')
    for p in pos_keys:
        h.append('<th class="n">%s</th>' % p)
    h.append('<th class="n">合计</th></tr></thead><tbody>')
    for t in ("上涨", "盘整", "下跌"):
        cc = s["cross"].get(t) or Counter()
        tot = sum(cc.values())
        h.append('<tr><td><b>%s</b></td>' % t)
        for p in pos_keys:
            v = cc.get(p, 0)
            h.append('<td class="n%s">%s</td>'
                     % (" mu" if not v else "", "{:,}".format(v) if v else "·"))
        h.append('<td class="n mu">%s</td></tr>' % "{:,}".format(tot))
    h.append('<tr><td class="mu">合计</td>')
    for p in pos_keys:
        h.append('<td class="n mu">%s</td>' % "{:,}".format(s["pos"].get(p, 0)))
    h.append('<td class="n mu">%s</td></tr>' % "{:,}".format(n))
    h.append('</tbody></table>')
    h.append('<div class="note" style="margin-top:11px">'
             '「趋势」是段的合成方向，「位置」是相对最近中枢的三分法。'
             '两列相乘得到的九格，就是九态判定的落脚点——'
             '同样是「中枢下」，在一个上涨段里和一个下跌段里，含义完全不同。</div>')
    h.append('</div>')

    h.append('<div class="panel"><h3 style="margin-top:0">中枢构成</h3>')
    h.append('<table><tbody>')
    h.append('<tr><td class="mu">判过中枢的票</td><td class="n">%s</td>'
             '<td class="mu" style="padding-left:16px">平均 %d 个中枢 / 只</td></tr>'
             % ("{:,}".format(n), round(s["piv_n_avg"])))
    h.append('<tr><td class="mu">中枢内平均笔数</td><td class="n">%d</td>'
             '<td class="mu" style="padding-left:16px">笔数越多，中枢越结实</td></tr>'
             % round(s["nbi_avg"]))
    h.append('<tr><td class="mu">标记「扎实」</td><td class="n">%s</td>'
             '<td class="mu" style="padding-left:16px">%.0f%%</td></tr>'
             % ("{:,}".format(s["solid"]), 100.0 * s["solid"] / n))
    h.append('<tr><td class="mu">标记「过宽」</td><td class="n">%s</td>'
             '<td class="mu" style="padding-left:16px">%.0f%%</td></tr>'
             % ("{:,}".format(s["wide"]), 100.0 * s["wide"] / n))
    h.append('<tr><td class="mu">标记「重叠」</td><td class="n">%s</td>'
             '<td class="mu" style="padding-left:16px">%.0f%%</td></tr>'
             % ("{:,}".format(s["over"]), 100.0 * s["over"] / n))
    h.append('</tbody></table>')
    h.append('<h3>中枢宽度 (ZG−ZD)/ZD</h3>')
    wm = max(s["wbucket"].values()) if s["wbucket"] else 1
    for k in ("<3%", "3-6%", "6-10%", "10-20%", ">20%"):
        c = s["wbucket"].get(k, 0)
        h.append(bar(k, c, wm, "#5b7fa6",
                     "  ·  %.0f%%" % (100.0 * c / s["w_n"]) if s["w_n"] else ""))
    h.append('<div class="note" style="margin-top:10px">'
             '中枢太窄（&lt;3%）时，价格随便动一下就「离开中枢」，三买信号会虚高；'
             '太宽（&gt;20%）时，回抽不进中枢几乎没有信息量。'
             '做参数调校时这两个极端都该单独看。</div>')
    h.append('</div></div>')

    # ---------- ③ 买卖点年龄 + 背驰
    h.append('<h2>③ 信号新鲜度与背驰标注 <span class="d">同一个「三买」，昨天和两个月前不是一回事</span></h2>')
    h.append('<div class="split3">')
    h.append('<div class="panel"><h3 style="margin-top:0">买卖点年龄</h3>')
    am = max(s["age"].values()) if s["age"] else 1
    for k in ("≤5 日", "6-20 日", "21-60 日", ">60 日"):
        h.append(bar(k, s["age"].get(k, 0), am, "#c9a227"))
    h.append('<div class="note" style="margin-top:10px">sig_age 是信号日到数据日的交易日数。'
             '超过 60 日的「三买」多数已经走完一段，再当新信号用就是追高。</div></div>')

    h.append('<div class="panel"><h3 style="margin-top:0">背驰标注</h3>')
    bm = max(s["bei"].values()) if s["bei"] else 1
    for k, c in s["bei"].most_common(8):
        h.append(bar(str(k)[:14], c, bm, "#c05a7a"))
    if not s["bei"]:
        h.append('<div class="empty">当前数据没有背驰标注</div>')
    h.append('</div>')

    h.append('<div class="panel"><h3 style="margin-top:0">信号类型分布</h3>')
    sm = max(s["sig_types"].values()) if s["sig_types"] else 1
    for k, c in s["sig_types"].most_common(8):
        h.append(bar(str(k), c, sm, "#d4694f"))
    h.append('<div class="note" style="margin-top:10px">'
             '评分均值 <b>%.1f</b>、最高 <b>%.1f</b>。评分是九维合成，'
             '不是概率——高分票不等于会涨，见页尾的实测结论。</div>'
             % (s["score_avg"], s["score_max"]))
    h.append('</div></div>')

    # ---------- ④ 单票解剖
    h.append('<h2>④ 单票解剖 <span class="d">输入代码或名称，看判定链逐条实测值</span></h2>')
    h.append('<div class="panel">')
    h.append('<div class="search" style="position:relative">'
             '<input id="q" placeholder="代码 / 名称，如 600519 或 贵州茅台" '
             'autocomplete="off"><span class="cnt" id="cnt"></span>'
             '<div class="cand" id="cand"></div></div>')
    h.append('<div id="detail"><div class="empty">载入中…</div></div>')
    h.append('</div>')

    h.append('<div class="note"><b>判定链怎么读</b>：左列是判据名，中列是这只票的'
             '<b>实测值</b>（可直接回 pool_state 复核），右列是这个值意味着什么。'
             '链的顺序就是九态的推导顺序：段方向 → 中枢定位 → 当前笔 → 买卖点确认 → 力度。'
             '任何一个环节缺数据，页面直接写「无」，不补默认值。</div>')

    # ---------- ⑤ 结论
    h.append('<h2>⑤ 这个工具能回答什么、不能回答什么</h2>')
    h.append('<div class="split"><div class="panel"><h3 style="margin-top:0">能</h3>'
             '<div class="mu" style="font-size:12.5px;line-height:1.9">'
             '· 某只票现在是九态里的哪一档，依据是哪几个字段<br>'
             '· 它的中枢在哪、有多宽、由几笔构成<br>'
             '· 买卖点是哪天确认的、距今多久、有没有失效<br>'
             '· 全市场结构整体偏向哪一侧（九态与交叉表）<br>'
             '· 参数调校时，某个阈值会让哪一档爆量或归零</div></div>')
    h.append('<div class="panel"><h3 style="margin-top:0">不能</h3>'
             '<div class="mu" style="font-size:12.5px;line-height:1.9">'
             '· 不能预测涨跌。结构是<b>位置描述</b>，不是方向判断<br>'
             '· 不能替代仓位与止损。守位在日报与 PC 页，这页不给<br>'
             '· 不能跨级别。所有字段都是日线口径，30 分钟级别没算<br>'
             '· 不能拿「背驰」当买入理由。力度弱不等于会反转</div></div></div>')

    h.append('<div class="note"><b>必须写在页面上的实测结论</b><br>'
             '板块层 46,584 个观测的回放结果：买点类信号 20 日超额收益 '
             '<b>−13bp（t=−0.58）</b>，按强度分层后没有单调性。'
             '也就是说，「属于某个买点档」在统计上不构成看涨依据。'
             '这套结构的价值在于<b>把价格位置说清楚、把止损位定死</b>，'
             '而不在于选出会涨的票。把它当信号源使用，回测已经否掉了。</div>')

    h.append('<div class="foot">数据：reports/pool_state_%s.json（data_asof=%s，'
             'run_at=%s）。结构与判定全部由 pool_state.py 计算，本页只做呈现。<br>'
             '看图请用 <a href="pc.html">结构工作台 · PC</a>；'
             '看名单请用 <a href="pool_report_h5.html">结构日报</a>；'
             '<a href="index.html">← 回分析台</a><br>'
             '本页为纸面研究，不构成投资建议。</div>'
             % (asof, asof, str(d.get("run_at") or "")[:16]))

    # ---------- 数据 + 交互
    #
    # 体积控制（1290KB → 期望 ~400KB）两招：
    #   ① 高频重复的字符串字段改存字典下标（stage 9 种、sig_note 7 种）
    #   ② 非买点档（中枢/中枢下/三卖，3324 只）只嵌 SLIM 列，其余位置补 null
    dicts, didx = [], {}
    for f in DICT_FIELDS:
        vals = []
        for r in rows:
            v = r.get(f)
            if v is None or v == "":
                continue
            v = str(v)
            if v not in vals:
                vals.append(v)
        didx[f] = len(dicts)
        dicts.append(vals)

    def enc(r, cols):
        out = []
        for c in cols:
            v = r.get(c)
            if c in DICT_FIELDS:
                if v is None or v == "":
                    out.append(None)
                else:
                    out.append(dicts[didx[c]].index(str(v)))
            else:
                out.append(v)
        return out

    data = []
    for r in rows:
        if r.get("stage") in FULL_STAGES:
            data.append(enc(r, COLS))
        else:
            data.append(enc(r, SLIM))   # 变长行：前端按 r.length 判断用哪套列

    h.append('<script>var COLS=%s;var SLIM=%s;var DATA=%s;var SDESC=%s;'
             'var DIDX=%s;var DICTS=%s;</script>'
             % (json.dumps(COLS, ensure_ascii=False),
                json.dumps(SLIM, ensure_ascii=False),
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                json.dumps(STAGE_DESC, ensure_ascii=False),
                json.dumps(didx, ensure_ascii=False),
                json.dumps(dicts, ensure_ascii=False, separators=(",", ":"))))

    h.append("""<script>
(function(){
var iCode=COLS.indexOf('code'), iName=COLS.indexOf('name'), iStage=COLS.indexOf('stage');
var q=document.getElementById('q'), cand=document.getElementById('cand'),
    det=document.getElementById('detail'), cnt=document.getElementById('cnt');

function g(r,k){
  // 行有两种长度：全字段（38）与精简（16），按 r.length 选列名表
  var cols = r.length===COLS.length ? COLS : SLIM;
  var i=cols.indexOf(k); if(i<0)return null;
  var v=r[i];
  if(DIDX.hasOwnProperty(k)&&v!==null&&v!==undefined){
    return DICTS[DIDX[k]][v];
  }
  return v;}
function fmt(v,nd){if(v===null||v===undefined)return '—';
  if(typeof v==='number'){return nd===undefined?String(v):v.toFixed(nd);}return String(v);}
function pct(v,nd){if(v===null||v===undefined)return '—';
  var f=Number(v); if(isNaN(f))return '—';
  return (f>0?'+':'')+f.toFixed(nd===undefined?2:nd)+'%';}
function ymd(v){if(!v)return '—';var s=String(v);
  return s.length===8?s.slice(0,4)+'-'+s.slice(4,6)+'-'+s.slice(6,8):s;}

// 判定链：顺序即推导顺序，缺数据写「无」
function chain(r){
  var stage=g(r,'stage'), rows=[];
  function add(k,v,note){rows.push([k,v,note]);}

  var sd=g(r,'seg_dir'), ns=g(r,'n_seg');
  add('段方向', (sd===1?'向上':(sd===-1?'向下':'无')) + (ns?'（'+ns+' 段）':''),
      sd===1?'大级别在上行结构里，回抽更容易形成买点'
            :(sd===-1?'大级别在下行结构里，任何买点都要更严的守位'
                     :'段方向未确立，位置判断的可信度下降'));

  var pos=g(r,'pos'), dzg=g(r,'dist_zg'), dzd=g(r,'dist_zd');
  add('中枢定位', pos||'无',
      pos==='枢内'?'价格在中枢区间内，方向未择'
      :(pos==='上沿外'?'已在中枢上沿之上（距 ZG '+pct(dzg===null?null:dzg*100)+'），属离开段范畴'
      :(pos==='下沿外'?'已在中枢下沿之下（距 ZD '+pct(dzd===null?null:dzd*100)+'），属弱势区'
      :'未形成有效中枢')));

  var zg=g(r,'zg'), zd=g(r,'zd'), npv=g(r,'n_piv');
  if(zg!==null&&zd!==null){
    var w=(zg-zd)/zd*100;
    add('最近中枢', 'ZG '+fmt(zg)+' / ZD '+fmt(zd)+'（宽 '+w.toFixed(1)+'%）',
        '共 '+fmt(npv)+' 个中枢；宽度 <3% 时进出频繁、信号易虚，>20% 时回抽确认的信息量低');
    add('中枢区间', ymd(g(r,'piv_sdt'))+' → '+ymd(g(r,'piv_edt')),
        '中枢由 '+fmt(g(r,'piv_n_bi'))+' 笔构成'
        +(g(r,'piv_solid')?'，标记「扎实」':'')
        +(g(r,'piv_wide')?'，标记「过宽」':'')
        +(g(r,'piv_over')?'，标记「重叠」':''));
  } else {
    add('最近中枢','无','没有有效中枢时，三类买点不成立');
  }

  var bd=g(r,'bi_dir');
  add('当前笔', (bd===1?'向上':(bd===-1?'向下':'无'))
      +'  振幅 '+fmt(g(r,'bi_amp'),2)+'%'+(g(r,'bi_live')?'  · 延伸中':''),
      '振幅是这一笔的高低差；「延伸中」表示笔还没走完，此时的中枢上沿只是暂定值');

  var st=g(r,'sig_type');
  if(st){
    add('买卖点', st+'  确认日 '+ymd(g(r,'sig_dt'))+'  价 '+fmt(g(r,'sig_price')),
        '距今 '+fmt(g(r,'sig_age'))+' 个交易日'
        +(g(r,'sig_fresh')?'，标记「新鲜」':'，已不新鲜')
        +(g(r,'sig_gain')!==null?'，信号后至今 '+pct(g(r,'sig_gain')*100):''));
    var gp=g(r,'sig_zg');
    if(gp!==null&&gp!==undefined){
      add('守位', fmt(gp), '三类买点的守位取离开段的中枢上沿；破了就是判断失效');
    }
    if(g(r,'sig_note')){
      add('判定原文', g(r,'sig_note'), '这是产线写下的判定理由，不做改写');
    }
  } else {
    add('买卖点','无','该票当前不在任何买点档');
  }

  var bei=g(r,'bei');
  add('力度（背驰）', bei||'无', bei?'离开段与进入段的 MACD 面积对比结果，只描述力度，不给方向'
                                   :'未触发背驰条件');

  add('九态结论', stage||'—', SDESC[stage]||'');

  var out='<table class="chain"><tbody>';
  for(var i=0;i<rows.length;i++){
    out+='<tr><td>'+rows[i][0]+'</td><td>'+rows[i][1]+'</td><td>'+rows[i][2]+'</td></tr>';
  }
  return out+'</tbody></table>';
}

function show(r){
  var stage=g(r,'stage'), stc={'三买':'#e3554f','三买待':'#e08a4f','二买':'#d4694f',
    '一买':'#c05a7a','背驰':'#c9a227','离开段':'#6b8cae','中枢':'#5b7fa6',
    '中枢下':'#4a7a63','三卖':'#3fa96a'}[stage]||'#8fa0b5';
  var p=g(r,'pct'), pc=p===null?'mu':(p>0?'up':(p<0?'dn':'mu'));
  var h='<div class="dhead"><span class="nm">'+g(r,'name')+'</span>'
    +'<span class="cd">'+g(r,'code')+'</span>'
    +'<span class="tag" style="background:'+stc+'22;color:'+stc+'">'+stage+'</span>'
    +'<span class="mu" style="font-size:12px">'+g(r,'trend')+' · '+g(r,'pos')+' · 评分 '+fmt(g(r,'score'),1)+'</span></div>';
  h+='<div class="kv">';
  var cells=[['收盘',fmt(g(r,'close')),pc],['当日涨跌',pct(p===null?null:p*100),pc],
    ['距 ZG',pct(g(r,'dist_zg')===null?null:g(r,'dist_zg')*100),''],
    ['距 ZD',pct(g(r,'dist_zd')===null?null:g(r,'dist_zd')*100),''],
    ['笔数',fmt(g(r,'n_bi')),''],['段数',fmt(g(r,'n_seg')),''],
    ['20日均额',fmt(g(r,'amt20_yi'),2)+' 亿',''],['市值',fmt(g(r,'mv_yi'),1)+' 亿','']];
  for(var i=0;i<cells.length;i++){
    h+='<div class="c"><div class="l">'+cells[i][0]+'</div><div class="v '+cells[i][2]+'">'+cells[i][1]+'</div></div>';
  }
  h+='</div>'+chain(r);
  h+='<div style="margin-top:12px;font-size:12px"><a href="pc.html#'+g(r,'code')+'">→ 在结构工作台看图（中枢矩形与守位线）</a></div>';
  det.innerHTML=h;
}

function pick(i){show(DATA[i]);}
window.__pick=pick;

function render(list){
  if(!list.length){cand.style.display='none';cnt.textContent='无匹配';return;}
  var h='';
  for(var i=0;i<Math.min(list.length,12);i++){
    var r=DATA[list[i]], stc=g(r,'stage');
    h+='<div data-i="'+list[i]+'"><span class="c">'+g(r,'code')+'</span>'
      +'<span>'+g(r,'name')+'</span><span class="mu" style="font-size:11px">'+stc+'</span></div>';
  }
  cand.innerHTML=h;cand.style.display='block';
  cnt.textContent=list.length+' 只匹配';
}

q.addEventListener('input',function(){
  var v=q.value.trim().toLowerCase();
  if(!v){cand.style.display='none';cnt.textContent='';return;}
  var out=[];
  for(var i=0;i<DATA.length;i++){
    var c=String(g(DATA[i],'code')||'').toLowerCase(),
        nm=String(g(DATA[i],'name')||'').toLowerCase();
    if(c.indexOf(v)===0||nm.indexOf(v)===0||c.indexOf(v)>0||nm.indexOf(v)>=0){
      out.push(i); if(out.length>=200) break;
    }
  }
  render(out);
});
q.addEventListener('keydown',function(e){
  if(e.key==='Enter'){var v=q.value.trim().toLowerCase();
    for(var i=0;i<DATA.length;i++){
      if(String(g(DATA[i],'code')).toLowerCase().indexOf(v)===0){
        cand.style.display='none';cnt.textContent='';show(DATA[i]);return;}}
  }
});
cand.addEventListener('click',function(e){
  var t=e.target; while(t&&t!==cand&&!t.getAttribute('data-i')) t=t.parentNode;
  if(t&&t.getAttribute('data-i')!==null&&t.getAttribute('data-i')!==undefined){
    var i=Number(t.getAttribute('data-i'));
    q.value=g(DATA[i],'code');cand.style.display='none';cnt.textContent='';show(DATA[i]);
  }
});
document.addEventListener('click',function(e){
  if(e.target!==q&&!cand.contains(e.target)) cand.style.display='none';
});

// 默认展示：三买里评分最高的一只；没有三买就展示第一只
var best=-1,bestS=-1e9;
for(var i=0;i<DATA.length;i++){
  if(g(DATA[i],'stage')==='三买'){
    var sc=g(DATA[i],'score'); if(sc!==null&&sc>bestS){bestS=sc;best=i;}
  }
}
if(best<0)best=0;
show(DATA[best]);
})();
</script>""")
    h.append('</body></html>')

    html = "".join(h)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return OUT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.stats:
        d, rows, asof = load()
        if not rows:
            return 1
        s = stats(rows)
        print("数据日 %s  判过 %d 只" % (asof, s["n"]))
        for st in STAGES:
            c = s["st"].get(st, 0)
            print("  %-6s %5d  %5.1f%%" % (st, c, 100.0 * c / s["n"]))
        print("  趋势 %s" % dict(s["trend"]))
        print("  位置 %s" % dict(s["pos"]))
        print("  中枢宽度 %s" % dict(s["wbucket"]))
        print("  信号年龄 %s" % dict(s["age"]))
        print("  背驰 %s" % dict(s["bei"]))
        return 0
    out = render()
    if not out:
        return 1
    print("[tool] %s  %.1f KB" % (out, os.path.getsize(out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
