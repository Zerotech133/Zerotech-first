# -*- coding: utf-8 -*-
"""每日结构化汇报 H5（移动端 · 分屏 · 可下钻）—— 缠论口径。

给飞书 / 微信转发用，单文件，不是长图：
  · 首屏只放全市场结构 + 五档数量 + 今日要点，一屏装得下（390×844 实测）；
  · 名单页按档位切换，一只一行；
  · 点任意一只进详情页：近 60 日走势上叠中枢上下沿，再逐条列判据。

为什么详情页不画均线：缠论看的是价格相对中枢的位置，不是均线关系。
所以图上只画两样东西——收盘价与中枢 [ZD, ZG]。价格在两条灰线之间就是在
枢内震荡，站上去就是离开上沿，掉下来就是离开下沿。

不引任何外部资源（无 CDN、无字体、无图表库），断网也能开；数据全部内联。

用法:
  python pool_report_h5.py                  # 读最近 pool_state_*.json
  python pool_report_h5.py --in X.json --out Y.html
"""

import argparse
import glob
import html
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
ASHARE = os.path.join(HERE, "data", "ashare")

# 九态
STAGE_LABEL = {
    "三买待": "三买待", "三买": "三买", "二买": "二买", "一买": "一买",
    "背驰": "背驰", "中枢": "中枢", "三卖": "三卖",
    "离开段": "离开段", "中枢下": "中枢下",
}
STAGE_DESC = {
    "三买待": "离开上沿，等回踩不破 ZG",
    "三买": "回踩不破 ZG，向上趋势确认",
    "二买": "一买后首回调不破极值",
    "一买": "向下线段终结·底背驰",
    "背驰": "顶背驰，上涨结构走完",
    "中枢": "枢内震荡，等方向",
    "三卖": "跌破下沿反抽不过",
    "离开段": "已远离上沿，追高区",
    "中枢下": "向下离开中枢",
}
STAGE_COLOR = {
    "三买待": "#b7791f", "三买": "#d92b2b", "二买": "#e07b39", "一买": "#c04a8a",
    "背驰": "#0a9e5c", "中枢": "#5a6b82", "三卖": "#0a9e5c",
    "离开段": "#8a7a5c", "中枢下": "#0a9e5c",
}
# 首屏五张卡
CARD_STAGES = ["三买待", "三买", "二买", "一买", "背驰"]
BAR_N = 60


def load_kline(code, n=BAR_N):
    """近 n 日收盘与最高价；缺文件返回 None。"""
    fn = code.replace(".", "_") + ".parquet"
    p = os.path.join(ASHARE, fn)
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_parquet(p, columns=["trade_date", "close", "high"])
    except Exception:
        return None
    if len(df) < n:
        return None
    df = df.sort_values("trade_date").tail(n)
    c = df["close"].to_numpy(float)
    h = df["high"].to_numpy(float)

    def _r(arr):
        return [None if not np.isfinite(x) else round(float(x), 2) for x in arr]

    return {"c": _r(c), "h": _r(h),
            "d0": int(df["trade_date"].iloc[0]),
            "d1": int(df["trade_date"].iloc[-1])}


def _p(v, d=1, sign=False):
    if v is None:
        return "—"
    x = v * 100
    return f"{x:+.{d}f}%" if sign else f"{x:.{d}f}%"


def _ev(label, actual, rule, ok):
    return {"l": label, "a": actual, "r": rule, "p": bool(ok)}


def build_evidence(r):
    """按状态生成判据清单：每条给「实测 / 规矩 / 是否成立」。"""
    st = r.get("stage")
    close = r.get("close") or 0
    zd = r.get("zd")
    zg = r.get("zg")
    pn = r.get("piv_n_bi") or 0
    ev = []

    if zd and zg:
        width = (zg - zd) / zd
        ev.append(_ev("中枢区间", f"{zd:.2f} ~ {zg:.2f}（宽 {width*100:.1f}%）",
                      "三笔重叠定 ZD/ZG", True))
        ev.append(_ev("现价位置",
                      f"{close:.2f}，距 ZG {_p(r.get('dist_zg'), 1, True)}",
                      "在上沿外 / 枢内 / 下沿外",
                      r.get("pos") == "上沿外"))
        ev.append(_ev("中枢延伸", f"{pn} 笔",
                      "≤9 笔本级别有效，超则升级",
                      pn <= 9))
    else:
        ev.append(_ev("中枢", "未构成", "至少三笔重叠", False))

    ev.append(_ev("走势类型", r.get("trend") or "—",
                  "末两中枢同向不重叠=趋势", (r.get("trend_dir") or 0) != 0))
    bi = r.get("bi_dir")
    ev.append(_ev("末笔方向",
                  ("向上" if bi == 1 else "向下" if bi == -1 else "—")
                  + ("（未走完）" if r.get("bi_live") else ""),
                  "三买待要求末笔向上", bi == 1))

    if r.get("sig_type"):
        age = r.get("sig_age")
        fresh = "新鲜" if r.get("sig_fresh") else "已过期"
        ev.append(_ev("最近买卖点",
                      f"{r['sig_type']} @{r.get('sig_price') or 0:.2f}"
                      f"（{age} 日前·{fresh}）",
                      "60 个交易日内有效", bool(r.get("sig_fresh"))))
        g = r.get("sig_gain")
        if g is not None:
            ok = -0.05 <= g <= 0.15
            ev.append(_ev("买点后表现", _p(g, 1, True),
                          "跌超 5% 认输 / 涨超 15% 算已兑现", ok))
    else:
        ev.append(_ev("最近买卖点", "无", "—", False))

    bei = r.get("bei") or ""
    if bei:
        ev.append(_ev("背驰标记", bei, "力度或面积衰减", True))
    if r.get("piv_over"):
        ev.append(_ev("中枢级别", "已升级", "延伸超 9 笔或区间宽超 30%", True))
    return ev


def _svg(r, k):
    """走势图：收盘线 + 中枢上下沿两条灰线 + 现价点。"""
    if not k or not k.get("c"):
        return '<div class="nodata">无走势数据</div>'
    c = [x for x in k["c"] if x is not None]
    if not c:
        return '<div class="nodata">无走势数据</div>'
    zd, zg = r.get("zd"), r.get("zg")
    vals = list(c) + [v for v in (zd, zg) if v]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or 0.1
    lo, hi = lo - pad, hi + pad
    W, H, PL, PR, PT, PB = 320, 150, 6, 44, 8, 16
    iw, ih = W - PL - PR, H - PT - PB

    def X(i):
        return PL + i / max(len(c) - 1, 1) * iw

    def Y(v):
        return PT + (hi - v) / (hi - lo) * ih

    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(c))
    out = [f'<svg viewBox="0 0 {W} {H}" class="kline">']
    # 中枢区间带 + 上下沿
    if zd and zg:
        y_zg, y_zd = Y(zg), Y(zd)
        out.append(f'<rect x="{PL}" y="{y_zg:.1f}" width="{iw}" height="{max(y_zd-y_zg,1):.1f}" '
                   f'fill="#5a6b82" opacity="0.10"/>')
        for y, lab, col in ((y_zg, f"ZG {zg:.2f}", "#8a7a5c"), (y_zd, f"ZD {zd:.2f}", "#8a7a5c")):
            out.append(f'<line x1="{PL}" y1="{y:.1f}" x2="{PL+iw}" y2="{y:.1f}" '
                       f'stroke="{col}" stroke-width="1" stroke-dasharray="4 3"/>')
            out.append(f'<text x="{PL+iw+4}" y="{y+3:.1f}" font-size="9" fill="{col}">{lab}</text>')
    out.append(f'<polyline points="{pts}" fill="none" stroke="#2456c4" stroke-width="1.6"/>')
    # 现价
    out.append(f'<circle cx="{X(len(c)-1):.1f}" cy="{Y(c[-1]):.1f}" r="3.2" fill="#d92b2b"/>')
    out.append(f'<text x="{PL+iw+4}" y="{Y(c[-1])+3:.1f}" font-size="9" fill="#d92b2b" '
               f'font-weight="bold">{c[-1]:.2f}</text>')
    out.append(f'<text x="{PL}" y="{H-4}" font-size="8" fill="#9aa3b0">{k["d0"]}</text>')
    out.append(f'<text x="{PL+iw-34}" y="{H-4}" font-size="8" fill="#9aa3b0">{k["d1"]}</text>')
    out.append("</svg>")
    return "".join(out)


def _trend_text(data, counts, tre, posd):
    """今日要点：从结构数字直接生成，不编故事。"""
    judged = data.get("meta", {}).get("judged") or 0
    up, dn = tre.get("上涨", 0), tre.get("下跌", 0)
    below = posd.get("下沿外", 0)
    n3p = counts.get("三买待", 0)
    n3 = counts.get("三买", 0)
    nbei = counts.get("背驰", 0)

    if not judged:
        return "今日无有效样本。"
    below_share = below / judged * 100
    mood = "弱势" if below_share > 50 else "中性" if below_share > 35 else "偏强"
    s = ("<b>回放已做完</b>（2024-05→2026-11，48.3 万观测）：九态无一跑出稳定正超额。"
         "三买 −146bp（t=−3.03）、三买待 −284bp（t=−3.67）——"
         "<b>等回踩确认再买是负分</b>；一买 +103bp 但 t=1.47，只靠 2024 一年。")
    s += (f" 今日 {judged} 只，下跌趋势 {dn}、枢下 {below}（{below_share:.0f}%），"
          f"结构{mood}。")
    if n3p:
        s += f" 三买待 {n3p} 只，位置在「等回踩」，但上面那行说这位置不赚钱。"
    else:
        s += " 今日无三买待：没票处在「离开上沿、等回踩」。"
    return s


_BUILD = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>缠论结构日报 {asof}</title><style>
*{{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}}
body{{background:#f2f4f7;color:#1c2330;
     font:14px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
     max-width:430px;margin:0 auto;min-height:100vh}}
.screen{{display:none;min-height:100vh;padding:10px 14px 4px}}
.screen.on{{display:block}}
.hd{{display:flex;align-items:baseline;gap:8px;margin-bottom:2px}}
.hd h1{{font-size:17px;font-weight:700}}
.hd .d{{font-size:11px;color:#8a93a3}}
.sub{{font-size:11px;color:#8a93a3;margin-bottom:10px}}
.hero{{background:linear-gradient(135deg,#1f2b45,#2f4570);border-radius:12px;
      padding:12px 14px;color:#fff;margin-bottom:10px}}
.hero .t{{font-size:11px;opacity:.75}}
.hero .m{{font-size:15px;font-weight:700;margin:3px 0 8px}}
.hb{{display:flex;gap:6px;margin-top:6px}}
.hb div{{flex:1;text-align:center;background:rgba(255,255,255,.10);border-radius:7px;padding:5px 2px}}
.hb .n{{font-size:15px;font-weight:700}}
.hb .l{{font-size:10px;opacity:.7}}
.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-bottom:10px}}
.card{{background:#fff;border-radius:9px;padding:8px 3px;text-align:center;
      box-shadow:0 1px 2px rgba(20,30,50,.05)}}
.card .k{{font-size:10px;color:#8a93a3;white-space:nowrap}}
.card .v{{font-size:19px;font-weight:700;line-height:1.25}}
.brief{{background:#fff8e6;border:1px solid #f2dfb4;border-radius:9px;
       padding:8px 11px;font-size:11px;line-height:1.56;color:#7a5a12;margin-bottom:9px}}
.sec{{font-size:11px;color:#8a93a3;margin:2px 2px 6px;display:flex;justify-content:space-between}}
.row{{background:#fff;border-radius:9px;padding:7px 11px;margin-bottom:6px;
     display:flex;align-items:center;gap:9px;box-shadow:0 1px 2px rgba(20,30,50,.05)}}
.row .l{{flex:1;min-width:0}}
.row .nm{{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.row .mt{{font-size:10.5px;color:#8a93a3;margin-top:2px}}
.row .r{{text-align:right;flex:none}}
.row .px{{font-size:14px;font-weight:700}}
.row .dg{{font-size:10.5px;color:#8a93a3}}
.up{{color:#d92b2b}}.down{{color:#0a9e5c}}
.arw{{color:#c3cad6;font-size:16px;flex:none}}
.foot{{font-size:10px;color:#8a93a3;line-height:1.6;margin-top:5px}}
details.rule{{background:#fff;border-radius:9px;padding:9px 11px;font-size:11px;
             line-height:1.8;color:#6b7686;margin-top:10px}}
details.rule summary{{cursor:pointer;color:#4a5568;font-weight:600;list-style:none}}
details.rule summary::-webkit-details-marker{{display:none}}
details.rule summary:after{{content:" ⌄";color:#a6afbd}}
details.rule[open] summary:after{{content:" ⌃"}}
.back{{display:flex;align-items:center;gap:6px;margin-bottom:10px;font-size:13px;color:#2456c4}}
.back span{{font-size:17px}}
.dt-hd{{background:#fff;border-radius:11px;padding:13px 14px;margin-bottom:9px}}
.dt-hd .nm{{font-size:19px;font-weight:700}}
.dt-hd .cd{{font-size:11px;color:#8a93a3;margin-top:2px}}
.dt-hd .px{{font-size:27px;font-weight:700;margin-top:5px;line-height:1.15}}
.dt-hd .ch{{font-size:12px;font-weight:600;margin-left:7px}}
.tag{{display:inline-block;padding:1px 8px;border-radius:9px;font-size:10.5px;
     margin-top:6px;margin-right:5px}}
.kline{{width:100%;height:auto;display:block}}
.kbox{{background:#fff;border-radius:11px;padding:9px 6px 4px;margin-bottom:9px}}
.nodata{{padding:24px;text-align:center;color:#a6afbd;font-size:12px}}
.evs{{background:#fff;border-radius:11px;overflow:hidden;margin-bottom:9px}}
.ev{{padding:9px 12px;border-bottom:1px solid #f2f4f7}}
.ev:last-child{{border-bottom:none}}
.ev .top{{display:flex;align-items:center;gap:7px}}
.ev .lb{{font-size:12px;font-weight:600;flex:1}}
.badge{{font-size:10px;padding:1px 7px;border-radius:8px;flex:none}}
.badge.y{{background:#e8f2ff;color:#2456c4}}
.badge.n{{background:#f2f4f7;color:#a6afbd}}
.ev .ac{{font-size:11px;color:#4a5568;margin-top:3px;word-break:break-all}}
.ev .ru{{font-size:10px;color:#a6afbd;margin-top:1px}}
</style></head><body>

<div class="screen on" id="s-home">
  <div class="hd"><h1>缠论结构日报</h1><span class="d">{asof}</span></div>
  <div class="sub">操作级别 日线 · 窗口 {lookback} 根 · 可判 {judged} 只</div>

  <div class="hero">
    <div class="t">全市场结构</div>
    <div class="m">{mood}</div>
    <div class="hb">
      <div><div class="n">{n_up}</div><div class="l">上涨走势</div></div>
      <div><div class="n">{n_dn}</div><div class="l">下跌走势</div></div>
      <div><div class="n">{n_below}</div><div class="l">枢下</div></div>
    </div>
  </div>

  <div class="cards">{cards}</div>

  <div class="brief">{brief}</div>

  <div class="sec"><span>点档位看名单</span><span>再点个股看判据</span></div>
  <div id="rows-home"></div>

  <details class="rule"><summary>口径与剔除规则</summary>
    <b>三买待</b>＝收盘在 ZG 上方 8% 内＋末笔向上＋无新鲜卖点＋不在下跌趋势。<br>
    <b>三买</b>＝回踩低点&gt;ZG 已确认，未涨超 15%。<b>二买</b>＝一买后回调不破极值。<br>
    <b>一买</b>＝向下线段终结·动量衰减（底背驰）。<br>
    <b>背驰</b>＝新鲜的一卖/二卖。三卖不算衰竭，那是下跌延续。<br>
    中枢延伸超 9 笔或区间宽超 30% 即「已升级」，ZG/ZD 不再作操作边界。<br>
    已剔除 ST/退市、股价&lt;{minp}元、20 日均额&lt;{minamt}亿、次新、数据不新鲜、指数、北交所。<br>
    <b>回放结论（2026-11-09）</b>：124 个截面、48.3 万观测，T+1 开盘入场、同日全样本为基准。
    按市值层内比较，九态 60 日超额全部落在 ±40bp 内、无一个 t 值显著为正 → 结构本身不提供选股超额。<br>
    三买 −146bp（t=−3.03）、三买待 −284bp（t=−3.67，逐日等权）；
    一买 +103bp（t=1.47，2024 年 +493bp、2025 年 −164bp，单年现象）；
    中枢档 75% 是「级别升级·判定作废」，真震荡那部分 +3bp，等于零。<br>
    涨红跌绿。生成于 {run_at}。
  </details>
  <div class="foot">{engine_note}</div>
</div>

<div class="screen" id="s-list">
  <div class="back" onclick="go('s-home')"><span>‹</span>返回</div>
  <div class="hd"><h1 id="l-title">名单</h1><span class="d" id="l-count"></span></div>
  <div class="sub" id="l-desc"></div>
  <div id="rows-list"></div>
</div>

<div class="screen" id="s-detail">
  <div class="back" onclick="go('s-list')"><span>‹</span>返回名单</div>
  <div id="d-body"></div>
</div>

<script>
var D = {data_js};
var ORDER = {order_js};

function go(id){{
  document.querySelectorAll('.screen').forEach(function(s){{s.classList.remove('on')}});
  document.getElementById(id).classList.add('on');
  window.scrollTo(0,0);
}}

function findRow(code){{
  for(var st in D){{
    if(!Array.isArray(D[st])) continue;
    for(var i=0;i<D[st].length;i++) if(D[st][i].code===code) return D[st][i];
  }}
  return null;
}}

function rowHtml(r){{
  var c = r.pct>0?'up':(r.pct<0?'down':'');
  var dg = r.dist_zg===null?'—':(r.dist_zg*100).toFixed(1)+'%';
  var dgc = r.dist_zg>0?'up':(r.dist_zg<0?'down':'');
  return '<div class="row" onclick="goDetail(\\''+r.code+'\\')">'
    + '<div class="l"><div class="nm">'+r.name+'</div>'
    + '<div class="mt">'+r.code+' · '+(r.mv_yi||0).toFixed(0)+'亿 · '
    + (r.trend||'')+' · 枢'+ (r.piv_n_bi||0) +'笔</div></div>'
    + '<div class="r"><div class="px '+c+'">'+r.close.toFixed(2)
    + '<span style="font-size:11px"> '+(r.pct>0?'+':'')+(r.pct*100).toFixed(2)+'%</span></div>'
    + '<div class="dg">距ZG <span class="'+dgc+'">'+dg+'</span></div></div>'
    + '<div class="arw">›</div></div>';
}}

function renderHome(){{
  var h='';
  ORDER.forEach(function(st){{
    var arr = D[st]||[];
    if(!arr.length) return;
    h += '<div class="row" onclick="goList(\\''+st+'\\')" style="padding:11px 13px">'
      + '<div class="l"><div class="nm" style="font-size:13px">'+st+'</div>'
      + '<div class="mt">'+ (D.desc[st]||'') +'</div></div>'
      + '<div class="r"><div class="px" style="color:'+(D.color[st]||'#1c2330')+'">'
      + (D.counts[st]||arr.length) +' 只</div></div>'
      + '<div class="arw">›</div></div>';
  }});
  document.getElementById('rows-home').innerHTML = h;
}}

function goList(st){{
  document.getElementById('l-title').textContent = st;
  document.getElementById('l-desc').textContent = D.desc[st]||'';
  var arr = D[st]||[];
  document.getElementById('l-count').textContent = '共 '+(D.counts[st]||arr.length)+' 只';
  document.getElementById('rows-list').innerHTML = arr.length
    ? arr.map(rowHtml).join('')
    : '<div class="nodata">该档今日为空</div>';
  go('s-list');
}}

function goDetail(code){{
  var r = findRow(code);
  if(!r) return;
  var c = r.pct>0?'up':(r.pct<0?'down':'');
  var h = '<div class="dt-hd"><div class="nm">'+r.name+'</div>'
    + '<div class="cd">'+r.code+' · '+(r.mv_yi||0).toFixed(0)+'亿 · '+(r.trend||'')+'</div>'
    + '<div class="px '+c+'">'+r.close.toFixed(2)
    + '<span class="ch">'+(r.pct>0?'+':'')+(r.pct*100).toFixed(2)+'%</span></div>'
    + '<div><span class="tag" style="background:'+(D.color[r.stage]||'#eef1f5')+'18;color:'
    + (D.color[r.stage]||'#5a6b82')+'">'+r.stage+'</span>'
    + '<span class="tag" style="background:#f2f4f7;color:#6b7686">'
    + (r.pos||'')+'</span>'
    + (r.piv_over?'<span class="tag" style="background:#fdeaea;color:#b23b3b">枢已升级</span>':'')
    + '</div></div>';
  h += '<div class="kbox">'+ (r.svg||'<div class="nodata">无走势数据</div>') +'</div>';
  h += '<div class="evs">';
  (r.ev||[]).forEach(function(e){{
    h += '<div class="ev"><div class="top"><div class="lb">'+e.l+'</div>'
      + '<div class="badge '+(e.p?'y':'n')+'">'+(e.p?'成立':'未成立')+'</div></div>'
      + '<div class="ac">'+e.a+'</div><div class="ru">规矩：'+e.r+'</div></div>';
  }});
  h += '</div>';
  h += '<div class="foot">图上灰带为中枢 [ZD, ZG]，虚线为上下沿；蓝线为收盘价，红点为现价。'
    + '价格在带内＝枢内震荡，站上上沿＝离开上沿，跌破下沿＝向下离开。</div>';
  document.getElementById('d-body').innerHTML = h;
  go('s-detail');
}}

renderHome();
</script></body></html>"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=None)
    ap.add_argument("--out", dest="out", default=os.path.join(REPORTS, "pool_report_h5.html"))
    args = ap.parse_args()

    src = args.src
    if not src:
        fs = sorted(glob.glob(os.path.join(REPORTS, "pool_state_*.json")))
        if not fs:
            print("[h5] 无 pool_state_*.json，先跑 pool_state.py --scan --save")
            return 1
        src = fs[-1]

    with open(src, encoding="utf-8") as f:
        j = json.load(f)
    meta = j.get("meta") or {}
    asof = j.get("data_asof") or "—"
    counts = meta.get("stage_counts") or {}
    tre = meta.get("trend_dist") or {}
    posd = meta.get("pos_dist") or {}
    judged = meta.get("judged") or 0

    # 分档数据 + 详情（含 SVG 与证据）
    data = {}
    order = [st for st in CARD_STAGES if (counts.get(st) or len(j["buckets"].get(st, [])))]
    for st in order + ["中枢"]:
        rows = []
        for r in j["buckets"].get(st, []):
            rr = dict(r)
            rr["stage"] = r.get("stage") or st
            # 买点档与背驰档都画图（要看中枢位置与顶背驰）；中枢档省掉以控体积
            k = load_kline(r["code"]) if st != "中枢" else None
            rr["svg"] = _svg(r, k)
            rr["ev"] = build_evidence(r)
            rows.append(rr)
        data[st] = rows

    # 卡片
    cards = "".join(
        f'<div class="card"><div class="k">{STAGE_LABEL[st]}</div>'
        f'<div class="v" style="color:{STAGE_COLOR[st]}">'
        f'{counts.get(st, len(j["buckets"].get(st, [])))}</div></div>'
        for st in CARD_STAGES)

    below = posd.get("下沿外", 0)
    share = below / judged * 100 if judged else 0
    mood = "弱势" if share > 50 else "中性" if share > 35 else "偏强"

    data_js = json.dumps({
        **{st: data.get(st, []) for st in order + ["中枢"]},
        "counts": counts,
        "desc": STAGE_DESC,
        "color": STAGE_COLOR,
    }, ensure_ascii=False, separators=(",", ":"))

    html_txt = _BUILD.format(
        asof=asof, lookback=meta.get("lookback", 500), judged=judged,
        mood=mood, n_up=tre.get("上涨", 0), n_dn=tre.get("下跌", 0), n_below=below,
        cards=cards, brief=_trend_text(j, counts, tre, posd),
        minp=2.0, minamt=meta.get("min_amt", 0.5),
        run_at=html.escape(j.get("run_at") or ""),
        engine_note="引擎 chanlun_signal，非形态打分。"
                    "本页零重算，判定口径以 pool_state.py 为准。",
        data_js=data_js, order_js=json.dumps(order, ensure_ascii=False),
    )

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_txt)
    print(f"[h5] {args.out}  ({len(html_txt)} bytes, 源 {os.path.basename(src)}, "
          f"{sum(len(v) for v in data.values())} 只明细)")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
