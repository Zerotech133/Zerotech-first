# -*- coding: utf-8 -*-
"""PC 端缠论结构工作台：左选票 / 中画结构 / 右看判据。

和手机版的分工（不是把 390px 居中拉宽）：
  手机版 pool_report_h5.py —— 卡片流，一屏一档，滑着看「今天有什么」。
  PC 版就这一份      —— 工作台，三栏常驻，看「这一只的结构到底长什么样」。
  数据严格同源（九态读 pool_state、五层读 pool_judge、现价当场补取），不另算。

为什么中栏要自己画：
  手机版每行一个 320x150 的小图，只能看出「大概在涨还是跌」。PC 有宽度，
  就该把中枢矩形、上下沿、买卖点、守位线、实时价一次画清楚——这才叫
  「缠论自身的页面」，不是把日报放大。

四个时点在页面上分开写（与手机版同口径，别混成一个）：
  结构 asof / 五层 judge 时刻 / 市场快照时刻 / 补价时刻

用法:
  python pool_pc.py               # 生成 reports/pool_pc.html
  python pool_pc.py --no-live     # 不叠实时价（纯结构日收盘）
  python pool_pc.py --bars 160    # 图上画多少根（默认 120）
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
sys.path.insert(0, HERE)

import pool_report_h5 as H          # 复用取价、位置口径、判据生成——不复制一份逻辑

OUT = os.path.join(REPORTS, "pool_pc.html")
ASHARE = H.ASHARE
CARD_STAGES = H.CARD_STAGES
LAYER_META = H.LAYER_META
STAGE_DESC = H.STAGE_DESC

# 每档最多放多少只（PC 左栏能滚动，但数据是嵌进 HTML 的，得有个上限）
PER_STAGE = {"三买待": 40, "三买": 82, "二买": 50, "一买": 34, "背驰": 80, "中枢": 60}


def newest(pat):
    fs = sorted(glob.glob(os.path.join(REPORTS, pat)))
    return fs[-1] if fs else None


def load_bars(code, bars):
    """近 bars 根的 (日期, 收盘, 最高)。只取这三列——页面不吃 OHLC 全量。"""
    p = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_parquet(p, columns=["trade_date", "close", "high"])
    except Exception:
        return None
    if len(df) < 20:
        return None
    df = df.sort_values("trade_date").tail(bars)
    d = [int(x) for x in df["trade_date"].tolist()]
    c = [round(float(x), 2) for x in df["close"].tolist()]
    h = [round(float(x), 2) for x in df["high"].tolist()]
    return d, c, h


def guard_of(r, stage):
    """守位，与 pool_report_h5 / pool_trace 同源。一买/二买/背驰不生造止损位。"""
    if stage == "三买":
        return r.get("sig_zg") or r.get("zg")
    if stage == "三买待":
        return r.get("zg")
    return r.get("guard")


def build(args):
    sp = newest("pool_state_*.json")
    if not sp:
        print("[pc] 没有 pool_state_*.json，先跑 pool_state.py --scan --save")
        return None
    with open(sp, encoding="utf-8") as f:
        st = json.load(f)
    asof = str(st.get("asof") or "")
    buckets = st.get("buckets") or {}
    meta = st.get("meta") or {}

    # 五层：与卡片同一批票、同一时刻
    jd, jp = None, None
    jp = newest("pool_judge_*.json")
    if jp:
        try:
            with open(jp, encoding="utf-8") as f:
                jd = json.load(f)
        except Exception as e:
            print("[pc] judge 读不了：%s" % e)

    picked, seen = [], set()

    def take(r, group, stage):
        code = r.get("code")
        if not code or code in seen:
            return
        seen.add(code)
        picked.append((r, group, stage))

    for s in CARD_STAGES:
        for r in (buckets.get(s) or [])[:PER_STAGE.get(s, 40)]:
            take(r, s, s)
    for k, label, desc, col in LAYER_META:
        for r in ((jd or {}).get(k) or []):
            take(r, label, label)

    # 一次性补实时价：每只票的价格要在同一时刻取，才谈得上「一屏一个时点」
    px_clock = ""
    pmap = {}
    if not args.no_live:
        codes = [c for c, _, _ in [(r.get("code"), 0, 0) for r, _, _ in picked] if c]
        pmap = H._fetch_px(codes) or {}
        if pmap:
            px_clock = datetime.now().strftime("%H:%M")
        print("[pc] 补取实时价 %d/%d 只" % (len(pmap), len(codes)))

    cals, cal_map = [], {}
    rows, groups = {}, []
    for group in list(CARD_STAGES) + [m[1] for m in LAYER_META]:
        groups.append({"k": group, "n": 0, "d": STAGE_DESC.get(group, ""),
                       "c": dict((m[1], m[3]) for m in LAYER_META).get(group, "#5a6b82")})

    skip = 0
    for r, group, stage in picked:
        code = r["code"]
        bars = load_bars(code, args.bars)
        if not bars:
            skip += 1
            continue
        d, c, h = bars
        key = tuple(d)
        if key not in cal_map:
            cal_map[key] = len(cals)
            cals.append(d)
        ci = cal_map[key]

        rr = dict(r)
        rr["stage"] = stage
        px, pct = pmap.get(code, (None, None))
        if px:
            rr["close"] = px
            rr["lpos"] = H._lpos(px, rr.get("zd"), rr.get("zg"))
            # 判据里那句「距 ZG +5.3%」必须按现价重算，否则同一页面会出现
            # 数字条 +5.18%、右栏判据 +5.3% 两个值（一个按现价、一个按结构日收盘）。
            # H5 的 _mkrow 就是这么做的，这里对齐它。
            if rr.get("zg"):
                try:
                    rr["dist_zg"] = float(px) / float(rr["zg"]) - 1
                except (TypeError, ValueError):
                    pass
            # 单位坑：_fetch_px 返回的涨跌幅是**百分数**（2.34 = +2.34%），
            # 而 pool_state 落盘的 pct 是小数（-0.0296）。两处混用会出现
            # 「今天涨 28%」这种一眼假的数字，所以在这里统一折成小数。
            pct = (pct / 100.0) if pct is not None else None
        else:
            pct = r.get("pct")
        g = guard_of(rr, stage)
        broke = None
        try:
            if px and g and float(px) < float(g):
                broke = round(float(px) / float(g) - 1, 4)
        except (TypeError, ValueError):
            pass

        rows[code] = {
            "k": code, "n": r.get("name") or "", "g": group, "st": stage,
            "px": px, "pct": pct, "lpos": rr.get("lpos") or r.get("pos") or "",
            "zg": r.get("zg"), "zd": r.get("zd"), "gd": g, "bk": broke,
            "sg": r.get("sig_type"), "sgp": r.get("sig_price"),
            "sgd": str(r.get("sig_dt") or ""), "age": r.get("sig_age"),
            "fr": 1 if r.get("sig_fresh") else 0, "nt": r.get("sig_note") or "",
            "tr": r.get("trend") or "", "td": r.get("trend_dir") or 0,
            "nb": r.get("n_bi"), "ns": r.get("n_seg"), "np": r.get("n_piv"),
            "bd": r.get("bi_dir"), "bl": 1 if r.get("bi_live") else 0,
            "ps": str(r.get("piv_sdt") or ""), "pe": str(r.get("piv_edt") or ""),
            "mv": r.get("mv_yi"), "sc": r.get("score"), "bei": r.get("bei") or "",
            "ci": ci, "c": c, "h": h,
            "ev": H.build_evidence(rr),
        }
        for gg in groups:
            if gg["k"] == group:
                gg["n"] += 1

    lv = None
    lp = newest("pool_live_*.json")
    if lp:
        try:
            with open(lp, encoding="utf-8") as f:
                lv = json.load(f)
        except Exception:
            pass

    live_at = str((jd or {}).get("live_at") or "")[11:16]
    mkt_at = str((lv or {}).get("live_at") or "")[11:16]
    data = {
        "asof": asof, "cal": cals, "rows": rows,
        "groups": [g for g in groups if g["n"]],
        "pxAt": px_clock, "jAt": live_at, "mAt": mkt_at,
        "mk": {"up": (lv or {}).get("up") or 0, "dn": (lv or {}).get("down") or 0,
               "lu": (lv or {}).get("limit_up") or 0},
        "judged": meta.get("judged") or (jd or {}).get("judged") or 0,
        "cnt": {s: len(buckets.get(s) or []) for s in CARD_STAGES},
        "build": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "src": os.path.basename(sp),
    }
    if skip:
        print("[pc] %d 只没有日线文件，跳过" % skip)
    return data


TPL = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>缠论结构工作台 __ASOF__</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%}
body{display:flex;flex-direction:column;overflow:hidden;background:#eef1f5;color:#1c2330;
     font:13px/1.55 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.num{font-variant-numeric:tabular-nums}
.up{color:#d92b2b}.dn{color:#0a9e5c}.mut{color:#6b7480}

/* 顶栏 */
.top{flex:0 0 52px;display:flex;align-items:center;gap:14px;padding:0 16px;
     background:#fff;border-bottom:1px solid #e3e7ee}
.top h1{font-size:15px;font-weight:700;letter-spacing:.2px}
.tag{font-size:11px;padding:1px 7px;border-radius:9px;background:#eef1f5;color:#5a6b82}
.srch{margin-left:auto;position:relative}
.srch input{width:230px;height:30px;padding:0 10px 0 28px;border:1px solid #dde2ea;
            border-radius:6px;font-size:12.5px;outline:none;background:#fafbfc}
.srch input:focus{border-color:#9db4d8;background:#fff}
.srch i{position:absolute;left:9px;top:7px;color:#9aa3b0;font-style:normal;font-size:12px}
.lnk{font-size:12px;color:#2456c4;text-decoration:none}
.lnk:hover{text-decoration:underline}

/* 三栏 */
.main{flex:1;display:grid;grid-template-columns:296px minmax(0,1fr) 356px;overflow:hidden}
.col{overflow-y:auto;overflow-x:hidden}
.col::-webkit-scrollbar{width:8px}
.col::-webkit-scrollbar-thumb{background:#cfd6e0;border-radius:4px}
.left{background:#fff;border-right:1px solid #e3e7ee}
.mid{background:#f7f9fc;display:flex;flex-direction:column;overflow:hidden}
.right{background:#fff;border-left:1px solid #e3e7ee}

/* 左栏 */
.gp{padding:9px 12px 5px;display:flex;align-items:center;gap:6px}
.gp .dot{width:7px;height:7px;border-radius:50%}
.gp .nm{font-size:12px;font-weight:700}
.gp .ct{font-size:11px;color:#8a93a3;margin-left:auto}
.gp .ds{display:none}
.row{padding:7px 12px 7px 14px;border-left:3px solid transparent;cursor:pointer;
     display:flex;align-items:baseline;gap:8px}
.row:hover{background:#f5f7fb}
.row.on{border-left-color:#d92b2b;background:#fdf2f2}
.row .nm{font-size:13px;font-weight:600;max-width:96px;overflow:hidden;
         white-space:nowrap;text-overflow:ellipsis}
.row .cd{font-size:11px;color:#9aa3b0}
.row .px{margin-left:auto;font-size:12.5px}
.row .bd{font-size:10.5px;color:#d92b2b;background:#fdeaea;border-radius:3px;padding:0 3px}

/* 中栏 */
.chead{padding:12px 18px 0;display:flex;align-items:baseline;gap:10px}
.chead .nm{font-size:18px;font-weight:700}
.chead .cd{font-size:12px;color:#8a93a3}
.chead .st{font-size:11.5px;padding:1px 8px;border-radius:9px;color:#fff}
.chart{flex:1;min-height:0;position:relative;margin:6px 10px 0}
.chart svg{display:block}
.strip{display:grid;grid-template-columns:repeat(6,1fr);gap:1px;background:#e3e7ee;
       margin:8px 10px 0;border:1px solid #e3e7ee;border-radius:6px;overflow:hidden}
.strip div{background:#fff;padding:6px 9px}
.strip .k{font-size:10.5px;color:#8a93a3}
.strip .v{font-size:13px;font-weight:600;margin-top:1px}
.note{padding:8px 14px 12px;font-size:11.5px;color:#8a93a3;line-height:1.6}

/* 右栏 */
.blk{padding:12px 14px;border-bottom:1px solid #eef1f5}
.blk h3{font-size:12px;font-weight:700;color:#5a6b82;margin-bottom:8px;
        display:flex;align-items:center;gap:6px}
.blk h3 span{font-weight:400;color:#9aa3b0;font-size:11px}
.ev{display:grid;grid-template-columns:64px 1fr;gap:2px 8px;padding:5px 0;
    border-top:1px dashed #eef1f5;font-size:12px}
.ev:first-of-type{border-top:0}
.ev .l{color:#8a93a3}
.ev .a{color:#1c2330}
.ev .r{grid-column:2;color:#b0b7c3;font-size:10.5px}
.ev.bad .a{color:#b23b3b}
.ev .mk{font-size:10px;margin-left:5px;padding:0 4px;border-radius:3px}
.ev .mk.y{background:#e8f5ee;color:#0a9e5c}
.ev .mk.n{background:#fdeaea;color:#b23b3b}
.cal{font-size:11.5px;color:#6b7480;line-height:1.9}
.cal b{color:#1c2330;font-weight:600}
.warn{background:#fff8e8;border:1px solid #f0e0bd;border-radius:6px;padding:8px 10px;
      font-size:11.5px;color:#8a6d2f;line-height:1.65}
@media (max-width:1180px){
  .main{grid-template-columns:264px minmax(0,1fr)}
  .right{display:none}
}
</style></head>
<body>
<div class="top">
  <h1>缠论结构工作台</h1>
  <span class="tag">__ASOF__</span>
  <span class="tag" id="t-time"></span>
  <div class="srch"><i>/</i><input id="q" placeholder="代码 / 名称，回车跳到第一只"></div>
  <a class="lnk" id="lnk-h5" href="pool_report_h5.html">手机版</a>
  <a class="lnk" id="lnk-sec" href="sector_platform.html">板块平台</a>
</div>

<div class="main">
  <div class="col left" id="list"></div>

  <div class="col mid">
    <div class="chead">
      <span class="nm" id="c-name">—</span>
      <span class="cd" id="c-code"></span>
      <span class="st" id="c-stage"></span>
      <span class="cd" id="c-trend" style="margin-left:auto"></span>
    </div>
    <div class="chart" id="chart"></div>
    <div class="strip" id="strip"></div>
    <div class="note" id="note"></div>
  </div>

  <div class="col right">
    <div class="blk"><h3>判据 <span>哪条成立、哪条不成立</span></h3><div id="ev"></div></div>
    <div class="blk"><h3>结构数字</h3><div id="nums"></div></div>
    <div class="blk"><h3>这一页有四个时点</h3><div class="cal" id="cal"></div></div>
    <div class="blk"><div class="warn" id="warn"></div></div>
  </div>
</div>

<script>
var PC = __DATA__;
var CUR = null;
var CN = {三买待:'离开上沿，等回踩不破 ZG', 三买:'回踩不破 ZG，向上趋势确认',
          二买:'一买后首回调不破极值', 一买:'向下线段终结·底背驰',
          背驰:'顶背驰，上涨结构走完', 中枢:'枢内震荡，等方向'};

function fmt(v,d){ return v==null? '—' : Number(v).toFixed(d==null?2:d); }
function pctTxt(v){ if(v==null) return '—'; return (v>0?'+':'')+(v*100).toFixed(2)+'%'; }
function cls(v){ return v>0?'up':(v<0?'dn':''); }
/* 守位与 ZG 是同一个价位（三买待）时，别再显示两个一模一样的百分比 */
function sameG(r){ return !!(r.gd && r.zg && Math.abs(Number(r.gd)-Number(r.zg))<0.005); }
/* 线上站点**逐请求**校验 sharecode，站内跳转丢了它就 403（页面显示「链接不完整」）。
   手机版用 sessionStorage 存，这里只做一件事：把当前地址上的 sharecode 透传过去。 */
function withSC(u){
  var m = String(location.search).match(/[?&]sharecode=([^&]+)/);
  return m ? (u + (u.indexOf('?')>=0?'&':'?') + 'sharecode=' + m[1]) : u;
}

/* ---- 左栏 ---- */
function renderList(){
  var h = '', rows = PC.rows;
  PC.groups.forEach(function(g){
    var codes = Object.keys(rows).filter(function(c){ return rows[c].g===g.k; });
    if(!codes.length) return;
    h += '<div class="gp"><span class="dot" style="background:'+g.c+'"></span>'
       + '<span class="nm">'+g.k+'</span><span class="ct">'+codes.length+'</span></div>';
    codes.forEach(function(c){
      var r = rows[c];
      var bd = r.bk!=null ? '<span class="bd">破守位</span>' : '';
      h += '<div class="row" data-c="'+c+'">'
         + '<span class="nm">'+r.n+'</span><span class="cd">'+c.slice(0,6)+'</span>'
         + bd
         + '<span class="px num '+cls(r.pct)+'">'+fmt(r.px)+'</span></div>';
    });
  });
  document.getElementById('list').innerHTML = h;
  Array.prototype.forEach.call(document.querySelectorAll('.row'), function(el){
    el.onclick = function(){ select(el.getAttribute('data-c')); };
  });
}

/* ---- 中栏大图 ---- */
function idxOf(d, cal){
  if(!d) return -1;
  var lo=0, hi=cal.length-1, ans=-1;
  while(lo<=hi){ var m=(lo+hi)>>1; if(cal[m]<=d){ans=m;lo=m+1;} else hi=m-1; }
  return ans;
}

function drawChart(r){
  var host = document.getElementById('chart');
  var W = host.clientWidth, H = host.clientHeight;
  if(W<60||H<60) return;
  var cal = PC.cal[r.ci], c = r.c, n = c.length;
  var padL=10, padR=66, padT=16, padB=24;
  var iw=W-padL-padR, ih=H-padT-padB;
  var vals = c.slice();
  [r.zg, r.zd, r.gd, r.px].forEach(function(v){ if(v!=null) vals.push(Number(v)); });
  var lo=Math.min.apply(null,vals), hi=Math.max.apply(null,vals);
  var pad=(hi-lo)*0.10 || (hi*0.02) || 0.5; lo-=pad; hi+=pad;
  function X(i){ return padL + (i/Math.max(n-1,1))*iw; }
  function Y(v){ return padT + (hi-v)/(hi-lo)*ih; }

  var s = ['<svg width="'+W+'" height="'+H+'" viewBox="0 0 '+W+' '+H+'">'];
  s.push('<rect x="0" y="0" width="'+W+'" height="'+H+'" fill="#fff"/>');

  /* 横向网格 + 右侧价格刻度 */
  for(var g=0; g<=4; g++){
    var v = lo + (hi-lo)*g/4, y = Y(v);
    s.push('<line x1="'+padL+'" y1="'+y.toFixed(1)+'" x2="'+(padL+iw)+'" y2="'+y.toFixed(1)+'" stroke="#eef1f5"/>');
    s.push('<text x="'+(padL+iw+6)+'" y="'+(y+3.5).toFixed(1)+'" font-size="10" fill="#9aa3b0">'+v.toFixed(2)+'</text>');
  }

  /* 中枢矩形：piv_sdt → piv_edt，ZG/ZD 上下沿。日期早于窗口就贴左边并标 ← */
  var i1 = idxOf(r.ps, cal), i2 = idxOf(r.pe, cal);
  var sameGuard = r.gd && r.zg && Math.abs(Number(r.gd) - Number(r.zg)) < 0.005;
  if(r.zg && r.zd && i2>=0){
    var cut = (i1<0 || i1===0 && r.ps < String(cal[0]));
    if(i1<0) i1=0;
    var x1=X(i1), x2=X(i2), y1=Y(r.zg), y2=Y(r.zd);
    s.push('<rect x="'+x1.toFixed(1)+'" y="'+y1.toFixed(1)+'" width="'+Math.max(x2-x1,2).toFixed(1)
         + '" height="'+Math.max(y2-y1,1).toFixed(1)+'" fill="#5a6b82" opacity="0.09"/>');
    [[r.zg,'ZG'],[r.zd,'ZD']].forEach(function(t){
      var yy=Y(t[0]);
      s.push('<line x1="'+x1.toFixed(1)+'" y1="'+yy.toFixed(1)+'" x2="'+(padL+iw)+'" y2="'+yy.toFixed(1)
           + '" stroke="#b9a98a" stroke-width="1" stroke-dasharray="5 4"/>');
      s.push('<text x="'+(x1+3).toFixed(1)+'" y="'+(yy-4).toFixed(1)+'" font-size="10" fill="#8a7a5c">'
           + t[1]+' '+fmt(t[0])+(t[1]==='ZG'&&sameGuard?' = 守位':'')+'</text>');
    });
    if(cut) s.push('<text x="'+padL+'" y="'+(y1-16).toFixed(1)+'" font-size="10" fill="#b0b7c3">← 中枢起点早于本窗口</text>');
  }

  /* 收盘面积 + 折线 */
  var pts = c.map(function(v,i){ return X(i).toFixed(1)+','+Y(v).toFixed(1); }).join(' ');
  s.push('<polygon points="'+X(0).toFixed(1)+','+Y(lo)+' '+pts+' '+X(n-1).toFixed(1)+','+Y(lo)+'" fill="#2456c4" opacity="0.05"/>');
  s.push('<polyline points="'+pts+'" fill="none" stroke="#2456c4" stroke-width="1.6"/>');

  /* 守位线：跌破时画红，否则灰蓝虚线。守位与 ZG 同一个价位时（三买待）
     不再重复画一条，否则两条虚线叠在一起、两个标签也叠在一起。 */
  if(r.gd && !sameGuard){
    var yg=Y(r.gd), bad = r.bk!=null;
    s.push('<line x1="'+padL+'" y1="'+yg.toFixed(1)+'" x2="'+(padL+iw)+'" y2="'+yg.toFixed(1)
         + '" stroke="'+(bad?'#d92b2b':'#4a7fd4')+'" stroke-width="1.2" stroke-dasharray="2 4"/>');
    s.push('<text x="'+(padL+2)+'" y="'+(yg-5).toFixed(1)+'" font-size="10" fill="'
         + (bad?'#d92b2b':'#4a7fd4')+'">守位 '+fmt(r.gd)+(bad?' · 已破 '+pctTxt(r.bk):'')+'</text>');
  }

  /* 买卖点：sig_dt 那天的 sig_price 打个点。价格缺失就用当天收盘。
     标签要避让 ZG/ZD 两条线——它们常与信号价重合，直接画会叠成一团。 */
  var is = idxOf(r.sgd, cal);
  if(is>=0 && r.sg){
    var sp = r.sgp!=null? Number(r.sgp) : c[is];
    var xs=X(is), ys=Y(sp);
    var ly = ys - 8;
    [Y(r.zg), Y(r.zd)].forEach(function(ref){
      if(Math.abs(ly-ref) < 12) ly = ref + 15;
    });
    ly = Math.min(Math.max(ly, padT+9), H-padB-3);
    s.push('<circle cx="'+xs.toFixed(1)+'" cy="'+ys.toFixed(1)+'" r="3.6" fill="#fff" stroke="#e07b39" stroke-width="1.8"/>');
    s.push('<text x="'+(xs+6).toFixed(1)+'" y="'+ly.toFixed(1)+'" font-size="10.5" fill="#c2691f" font-weight="600">'
         + r.sg+' '+fmt(sp)+'</text>');
  }

  /* 实时价：接在最后一根之后，与收盘线断开——结构只到 asof，价是当下 */
  if(r.px){
    var xp=X(n-1)+6, yp=Y(Number(r.px));
    s.push('<line x1="'+X(n-1).toFixed(1)+'" y1="'+Y(c[n-1]).toFixed(1)+'" x2="'+xp.toFixed(1)+'" y2="'+yp.toFixed(1)+'" stroke="#d92b2b" stroke-width="1" stroke-dasharray="2 2"/>');
    s.push('<circle cx="'+xp.toFixed(1)+'" cy="'+yp.toFixed(1)+'" r="3.8" fill="#d92b2b"/>');
    s.push('<text x="'+(xp+6).toFixed(1)+'" y="'+(yp+3.5).toFixed(1)+'" font-size="10.5" fill="#d92b2b" font-weight="700">'+fmt(r.px)+'</text>');
  }

  /* 日期轴：首 / 中 / 末，末根标注是收盘日还是今天 */
  var marks = [0, Math.floor((n-1)/2), n-1];
  marks.forEach(function(i,ix){
    var lb = String(cal[i]||'');
    lb = lb.slice(4,6)+'-'+lb.slice(6,8);
    var x = X(i);
    s.push('<text x="'+x.toFixed(1)+'" y="'+(H-6)+'" font-size="10" fill="#9aa3b0" text-anchor="'
         + (ix===0?'start':ix===2?'end':'middle')+'">'+lb+(ix===2&&r.px?' → 今':'')+'</text>');
  });
  s.push('</svg>');
  host.innerHTML = s.join('');
}

/* ---- 右栏 / 数字条 ---- */
function renderSide(r){
  var ev = r.ev || [];
  var h='';
  ev.forEach(function(e){
    h += '<div class="ev '+(e.p?'':'bad')+'"><div class="l">'+e.l+'</div>'
       + '<div class="a">'+e.a+'<span class="mk '+(e.p?'y':'n')+'">'+(e.p?'成立':'不成立')+'</span></div>'
       + '<div class="r">规矩：'+e.r+'</div></div>';
  });
  document.getElementById('ev').innerHTML = h || '<div class="mut">无</div>';

  var nums = [
    ['中枢', r.zd&&r.zg ? fmt(r.zd)+' ~ '+fmt(r.zg) : '未构成'],
    ['笔 / 段 / 中枢', (r.nb||0)+' / '+(r.ns||0)+' / '+(r.np||0)],
    ['末笔', (r.bd===1?'向上':r.bd===-1?'向下':'—')+(r.bl?'（未走完）':'')],
    ['最近买卖点', r.sg? r.sg+' '+(r.age!=null? r.age+' 日前':'')+(r.fr?'':' · 已过期') : '无'],
    ['背驰', r.bei || '—'],
    ['市值', r.mv? fmt(r.mv,0)+' 亿' : '—'],
    ['评分', r.sc!=null? fmt(r.sc,1) : '—']
  ];
  document.getElementById('nums').innerHTML = nums.map(function(x){
    return '<div class="ev"><div class="l">'+x[0]+'</div><div class="a">'+x[1]+'</div></div>';
  }).join('');
}

function renderStrip(r){
  var cells = [
    ['现价', fmt(r.px), cls(r.pct)],
    ['涨跌', pctTxt(r.pct), cls(r.pct)],
    ['位置', r.lpos || '—', ''],
    ['距 ZG', r.zg&&r.px ? pctTxt(r.px/r.zg-1) : '—', r.zg&&r.px? cls(r.px/r.zg-1):''],
    ['守位', r.gd? fmt(r.gd) : '未定义', ''],
    ['守位差', sameG(r)? '= 距ZG' : (r.gd&&r.px? pctTxt(r.px/r.gd-1):'—'),
      r.bk!=null? 'dn' : '']
  ];
  document.getElementById('strip').innerHTML = cells.map(function(x){
    return '<div><div class="k">'+x[0]+'</div><div class="v num '+x[2]+'">'+x[1]+'</div></div>';
  }).join('');
}

function renderCal(){
  var el = document.getElementById('cal');
  el.innerHTML =
    '结构（笔/中枢/档位）<b>'+PC.asof+' 收盘</b><br>'
  + '五层判据 <b>'+(PC.jAt||'—')+'</b> · 市场快照 <b>'+(PC.mAt||'—')+'</b><br>'
  + '每只票的价格 <b>'+(PC.pxAt||'结构日收盘')+'</b>（本页打开时补取）<br>'
  + '<span class="mut">四者不是同一时刻。图上蓝色收盘线到结构日为止，红点才是当下的价。</span>';
  document.getElementById('warn').innerHTML =
    '本页只做结构陈述，不构成买卖建议。守位口径：三买取信号中枢上沿、三买待取末中枢上沿；'
    + '一买/二买/背驰不设守位——它们的止损不是同一个概念，不在这里编一个。';
}

function select(code){
  var r = PC.rows[code];
  if(!r) return;
  CUR = code;
  document.getElementById('c-name').textContent = r.n;
  document.getElementById('c-code').textContent = r.k + ' · ' + (CN[r.st] || r.st);
  var st = document.getElementById('c-stage');
  st.textContent = r.st;
  st.style.background = (PC.groups.filter(function(g){return g.k===r.g;})[0]||{}).c || '#5a6b82';
  document.getElementById('c-trend').textContent =
    (r.tr? r.tr+'走势' : '') + '  ·  ' + (PC.cnt[r.st]!=null? '本档 '+PC.cnt[r.st]+' 只' : '');
  document.getElementById('note').textContent =
    '中枢 ' + (r.zd&&r.zg? fmt(r.zd)+'~'+fmt(r.zg) : '未构成')
    + (r.nt? ' · '+r.nt : '') + (r.sg? ' · 信号日 '+r.sgd : '')
    + ' · 窗口画 ' + r.c.length + ' 根日线';
  Array.prototype.forEach.call(document.querySelectorAll('.row'), function(el){
    el.classList.toggle('on', el.getAttribute('data-c')===code);
  });
  if(location.hash !== '#'+code) history.replaceState(null, '', '#'+code);
  // 顺序要紧：数字条先渲染（它占高度），再画图。反过来的话图按「没有数字条」
  // 的高度画，数字条一出现就把图挤下去，图的 svg 溢出盖子一样压在数字条上。
  renderStrip(r); renderSide(r); drawChart(r);
}

/* ---- 顶栏时间 / 搜索 / 键盘 ---- */
function initTop(){
  var t = [];
  if(PC.pxAt) t.push('价 '+PC.pxAt);
  if(PC.mAt) t.push('市场 '+PC.mAt);
  document.getElementById('t-time').textContent = '结构 '+PC.asof + (t.length? ' · '+t.join(' · ') : '');
  document.getElementById('lnk-h5').href = withSC('pool_report_h5.html');
  document.getElementById('lnk-sec').href = withSC('sector_platform.html');
  var q = document.getElementById('q');
  q.addEventListener('keydown', function(e){
    if(e.key==='Enter'){
      var v = q.value.trim().toUpperCase();
      var hit = Object.keys(PC.rows).filter(function(c){
        var r=PC.rows[c];
        return c.indexOf(v)===0 || r.n.indexOf(q.value.trim())>=0;
      });
      if(hit.length){ select(hit[0]);
        var el=document.querySelector('.row[data-c="'+hit[0]+'"]');
        if(el) el.scrollIntoView({block:'center'});
      }
    }
  });
  document.addEventListener('keydown', function(e){
    if(e.target.tagName==='INPUT') return;
    if(e.key==='/'){ e.preventDefault(); document.getElementById('q').focus(); return; }
    if(e.key!=='ArrowDown' && e.key!=='ArrowUp') return;
    var codes = Object.keys(PC.rows), i = codes.indexOf(CUR);
    if(i<0) return;
    i += (e.key==='ArrowDown'? 1 : -1);
    if(i<0||i>=codes.length) return;
    e.preventDefault();
    select(codes[i]);
    var el=document.querySelector('.row[data-c="'+codes[i]+'"]');
    if(el) el.scrollIntoView({block:'center'});
  });
}

var rt=null;
window.addEventListener('resize', function(){
  clearTimeout(rt);
  rt = setTimeout(function(){ if(CUR) drawChart(PC.rows[CUR]); }, 120);
});

renderList(); renderCal(); initTop();
/* 地址带 #代码 就能直接分享「我在看这一只」——PC 上常开着几个标签页对比结构 */
var want = String(location.hash||'').replace('#','');
var first = (want && PC.rows[want]) ? want : Object.keys(PC.rows)[0];
if(first) select(first);
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-live", action="store_true", help="不叠实时价")
    ap.add_argument("--bars", type=int, default=120, help="图上画多少根日线")
    a = ap.parse_args()

    data = build(a)
    if not data:
        return 1

    js = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    js = js.replace("</", "<\\/")          # 数据里若出现 </script 不会截断脚本

    # 站内跳转要带 sharecode（线上逐请求校验，丢了就 403）
    sc = os.environ.get("POOL_SHARECODE", "").strip()
    sc = ("sharecode=" + sc) if sc else ""

    html = (TPL.replace("__ASOF__", data["asof"])
               .replace("__SC__", sc)
               .replace("__DATA__", js))
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("[pc] %s  (%.1f KB, %d 只, %d 组分档)"
          % (OUT, len(html.encode("utf-8")) / 1024.0, len(data["rows"]), len(data["groups"])))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
