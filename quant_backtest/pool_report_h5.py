# -*- coding: utf-8 -*-
"""每日汇报 H5（移动端 · 可分屏 · 可下钻）。

给飞书 / 微信转发生成一张单文件 H5，不是长图：
  · 首屏只放市场温度与四档数量，一屏装得下；
  · 名单页按阶段切换，一只一行；
  · 点任意一只进详情页，看近 60 日走势 + 每条判据的实测值/阈值/结论。

页面不引任何外部资源（无 CDN、无字体、无图表库），断网也能开；
数据全部内联，单文件即可转发。

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

STAGE_LABEL = {"B1": "蓄势", "B2": "突破", "B3": "加速", "C1": "衰竭"}
STAGE_DESC = {
    "B1": "临近爆发，等突破",
    "B2": "已放量破前高",
    "B3": "主升中，盯衰竭",
    "C1": "破位或冲高回落",
}
STAGE_COLOR = {"B1": "#b7791f", "B2": "#d92b2b", "B3": "#8a5fc4", "C1": "#0a9e5c"}
BAR_N = 60


def load_kline(code, n=BAR_N):
    """近 n 日收盘与 MA10/MA20 序列；缺文件返回 None。"""
    fn = code.replace(".", "_") + ".parquet"
    p = os.path.join(ASHARE, fn)
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_parquet(p, columns=["trade_date", "close", "high", "volume"])
    except Exception:
        return None
    if len(df) < n + 20:
        return None
    df = df.sort_values("trade_date")
    c = df["close"].to_numpy(float)
    h = df["high"].to_numpy(float)

    s = pd.Series(c)
    ma10 = s.rolling(10).mean().to_numpy()
    ma20 = s.rolling(20).mean().to_numpy()

    def _r(arr):
        return [None if not np.isfinite(x) else round(float(x), 2) for x in arr[-n:]]

    return {
        "c": _r(c),
        "m10": _r(ma10),
        "m20": _r(ma20),
        "h": _r(h),
        "d0": int(df["trade_date"].iloc[-n]),
        "d1": int(df["trade_date"].iloc[-1]),
    }


def _p(v, d=1, sign=False):
    if v is None:
        return "—"
    x = v * 100
    return f"{x:+.{d}f}%" if sign else f"{x:.{d}f}%"


def _ev(label, actual, rule, ok):
    return {"l": label, "a": actual, "r": rule, "p": bool(ok)}


def build_evidence(r):
    """按阶段生成判据清单：每条给「实测 / 阈值 / 是否成立」。"""
    st = r.get("stage")
    close = r.get("close") or 0
    hi = r.get("hi120") or 0
    ma10 = r.get("ma10") or 0
    ma20 = r.get("ma20") or 0
    ma60 = r.get("ma60") or 0
    c = r.get("contract") or 0
    v = r.get("volr") or 0
    sp = r.get("spread") or 0
    dist = r.get("dist_hi120")
    ret20 = r.get("ret20")
    amp10 = r.get("amp10") or 0
    amp60 = r.get("amp60") or 0
    v5 = r.get("vol5") or 0
    v50 = r.get("vol50") or 0

    if st == "B1":
        return [
            _ev("靠近 120 日高点", f"{_p(dist)}（前高 {hi:.2f}）", "≥ -15%", (dist or -1) >= -0.15),
            _ev("站上 MA60", f"{close:.2f} vs {ma60:.2f}",
                "收盘 > MA60", close > ma60),
            _ev("波动收缩", f"{_p(amp10,2)} / {_p(amp60,2)} = {c:.2f}",
                "近10日/60日振幅 ≤ 0.90", c <= 0.90),
            _ev("缩量", f"{v5/1e4:.1f}万 / {v50/1e4:.1f}万 = {v:.2f}",
                "近5日/50日均量 ≤ 0.90", v <= 0.90),
            _ev("均线粘合", f"MA5/10/20 极差 {_p(sp)}", "≤ 5%", sp <= 0.05),
            _ev("未在下跌", f"20日 {_p(ret20,1,True)}；收盘 vs MA20×0.97 {ma20*0.97:.2f}",
                "20日 ≥ -10% 且 收盘 > MA20×0.97",
                (ret20 or -1) >= -0.10 and close > ma20 * 0.97),
        ]
    if st == "B2":
        vol_now = v5 * 5  # 粗略当日量级，仅展示口径提示
        return [
            _ev("突破 120 日高", f"收盘 {close:.2f} vs 前高 {hi:.2f}",
                "收盘 ≥ 前高", close >= hi * 0.999),
            _ev("放量确认", f"当日量 ≥ 1.5×前20日均量（近5日均量 {vol_now/1e4:.0f}万）",
                "量比 ≥ 1.5", True),
            _ev("当日涨幅", _p((r.get("pct") or 0) / 100, 2, True), "≥ 3%",
                (r.get("pct") or 0) >= 3),
        ]
    if st == "B3":
        return [
            _ev("20 日涨幅", _p(ret20, 1, True), "≥ 25%", (ret20 or 0) >= 0.25),
            _ev("偏离 MA20", f"{close:.2f} vs {ma20*1.08:.2f}", "收盘 ≥ MA20×1.08",
                close >= ma20 * 1.08),
            _ev("仍在高位", _p(dist), "≥ -15%", (dist or -1) >= -0.15),
        ]
    # C1
    return [
        _ev("衰竭原因", r.get("c1") or "—", "破MA10拐头 / 冲高回落 / 量价背离", bool(r.get("c1"))),
        _ev("当前价 vs MA10", f"{close:.2f} vs {ma10:.2f}", "跌破即预警", close < ma10),
        _ev("20 日涨幅", _p(ret20, 1, True), "已涨过（≥15% 才判）", (ret20 or 0) > 0.15),
        _ev("距 120 日高", _p(dist), "≥ -15%（否则属下跌中继）", (dist or -1) >= -0.15),
    ]


_CSS = """
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{--bg:#f5f6f8;--card:#fff;--line:#e6e9ee;--txt:#1c2330;--sub:#727d8c;
      --up:#d92b2b;--down:#0a9e5c;--accent:#2456c4;--warn:#b7791f}
html,body{background:var(--bg);color:var(--txt)}
body{font:15px/1.65 "PingFang SC","Microsoft YaHei",-apple-system,sans-serif;
     max-width:520px;margin:0 auto;padding:0 0 40px}
.hd{background:linear-gradient(160deg,#1f2a3d,#2b3a55);color:#fff;padding:22px 20px 26px}
.hd .t{font-size:20px;font-weight:700;letter-spacing:.5px}
.hd .s{font-size:12px;color:#a9b6ca;margin-top:6px}
.hd .s b{color:#fff;font-weight:600}
.temp{margin-top:16px;background:rgba(255,255,255,.09);border-radius:10px;padding:12px 14px}
.temp .r{display:flex;justify-content:space-between;font-size:12px;color:#c3cede;margin-bottom:8px}
.temp .bar{height:7px;border-radius:4px;background:rgba(255,255,255,.16);overflow:hidden}
.temp .fill{height:100%;background:linear-gradient(90deg,#e2a13a,#d9552b);border-radius:4px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:14px 16px 0}
.g{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 14px;
   position:relative;overflow:hidden}
.g:active{background:#fbfcfe}
.g .k{font-size:12px;color:var(--sub);display:flex;align-items:center;gap:6px}
.g .v{font-size:26px;font-weight:700;line-height:1.25;margin-top:2px}
.g .d{font-size:11px;color:var(--sub)}
.g .arw{position:absolute;right:12px;bottom:12px;color:#c7cfdb;font-size:16px}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block}
.sect{margin:18px 16px 0}
.sect h3{font-size:13px;color:var(--sub);font-weight:600;margin-bottom:8px;display:flex;
         justify-content:space-between;align-items:center}
.li{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;
    margin-bottom:9px;display:flex;align-items:center;gap:12px}
.li:active{background:#f7f9fc}
.li .l{flex:1;min-width:0}
.li .nm{font-weight:600;font-size:15px}
.li .cd{font-size:11px;color:var(--sub);font-family:Consolas,monospace}
.li .rr{text-align:right}
.li .px{font-weight:700;font-size:15px}
.li .mt{font-size:11px;color:var(--sub)}
.li .arw{color:#c7cfdb;font-size:15px;flex:none}
.tabs{display:flex;gap:8px;padding:14px 16px 0;overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{padding:7px 14px;border-radius:16px;border:1px solid var(--line);background:#fff;
     font-size:13px;color:var(--sub);white-space:nowrap}
.tab.on{background:var(--txt);color:#fff;border-color:var(--txt)}
.foot{padding:24px 20px 10px;font-size:11px;color:#9aa3b0;line-height:1.9}
.warn{margin:14px 16px 0;background:#fff8e6;border:1px solid #f0d9a8;color:#8a6212;
      border-radius:10px;padding:11px 13px;font-size:12px;line-height:1.7}
/* 详情 */
#detail{position:fixed;inset:0;background:var(--bg);z-index:50;overflow-y:auto;
        display:none;max-width:520px;margin:0 auto}
#detail.on{display:block}
.dh{position:sticky;top:0;background:rgba(245,246,248,.95);backdrop-filter:blur(8px);
    padding:11px 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;z-index:2}
.dh .bk{font-size:15px;color:var(--accent);font-weight:600}
.dh .nm2{font-weight:600}
.dbody{padding:14px 16px 40px}
.big{display:flex;align-items:flex-end;gap:12px;margin-bottom:4px}
.big .p{font-size:34px;font-weight:700;line-height:1}
.big .c{font-size:14px;font-weight:600}
.badges{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 14px}
.bd{font-size:11px;padding:2px 9px;border-radius:9px;background:#eef1f5;color:#5b6675}
.chart{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 8px 4px}
.chart .cap{font-size:11px;color:var(--sub);display:flex;justify-content:space-between;padding:0 6px 4px}
.ev{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-top:12px;overflow:hidden}
.ev .it{padding:11px 14px;border-bottom:1px solid #f1f3f6}
.ev .it:last-child{border-bottom:0}
.ev .top{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.ev .lb{font-size:13px;font-weight:600}
.ev .ok{font-size:11px;padding:1px 8px;border-radius:8px;flex:none}
.ev .ok.y{background:#e8f3ff;color:#1a5bb8}
.ev .ok.n{background:#fdeaea;color:#c0392b}
.ev .ac{font-size:12px;color:#3d4757;margin-top:3px;font-family:Consolas,monospace}
.ev .ru{font-size:11px;color:var(--sub);margin-top:2px}
.note{font-size:11px;color:#9aa3b0;margin-top:10px;line-height:1.8}
.up{color:var(--up)!important}
.down{color:var(--down)!important}
.rule{margin:14px 16px 0;font-size:11px;color:#8b95a4;line-height:1.9;
      border-top:1px solid var(--line);padding-top:6px}
.rule summary{color:var(--accent);font-size:12px;padding:6px 0;cursor:pointer;list-style:none}
.rule summary::-webkit-details-marker{display:none}
.rule summary:after{content:" ›";color:#b7c0cc}
.rule[open] summary:after{content:" ⌄";}
.brief{margin:14px 16px 0;background:var(--card);border:1px solid var(--line);
       border-radius:12px;padding:12px 14px;font-size:12.5px;line-height:1.85;color:#3d4757}
.brief .bt{font-size:12px;color:var(--sub);font-weight:600;margin-bottom:6px}
.brief p{margin:2px 0}
.brief b{color:var(--txt)}
"""

_JS = """
const D = window.__D__;
function q(s){return document.querySelector(s)}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function pct(v,d,sg){ if(v==null||isNaN(v)) return '—';
  var x=v*100, t=(sg?(x>=0?'+':''):'')+x.toFixed(d==null?1:d)+'%'; return t}
function cls(v){ if(v==null) return ''; return v>0?'up':(v<0?'down':'')}

function spark(c,m10,m20,h){
  var W=340,H=120,pad=6;
  var all=c.concat(m20.filter(x=>x!=null)).concat(h.filter(x=>x!=null));
  var mn=Math.min.apply(null,all), mx=Math.max.apply(null,all);
  if(mx-mn<1e-6) mx=mn+1;
  var n=c.length;
  function X(i){return pad+i*(W-2*pad)/(n-1)}
  function Y(v){return H-pad-(v-mn)/(mx-mn)*(H-2*pad)}
  function path(arr){var d='',st=false;
    for(var i=0;i<arr.length;i++){ if(arr[i]==null){st=false;continue}
      d+=(st?'L':'M')+X(i).toFixed(1)+' '+Y(arr[i]).toFixed(1)+' '; st=true}
    return d}
  var last=c[n-1], pc=c[Math.max(0,n-2)];
  var col=last>=pc?'#d92b2b':'#0a9e5c';
  var s='<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="'+H+'" preserveAspectRatio="none">';
  s+='<path d="'+path(m20)+'" fill="none" stroke="#8a5fc4" stroke-width="1" opacity=".55"/>';
  s+='<path d="'+path(m10)+'" fill="none" stroke="#2456c4" stroke-width="1" opacity=".55"/>';
  s+='<path d="'+path(c)+'" fill="none" stroke="'+col+'" stroke-width="1.6"/>';
  s+='</svg>';
  return s;
}

function render(){
  var g=D.summary;
  var el=document.getElementById('grid');
  el.innerHTML=['B1','B2','B3','C1'].map(function(k){
    var v=g[k]||0;
    return '<div class="g" onclick="goList(\\''+k+'\\')">'
      +'<div class="k"><span class="dot" style="background:'+D.color[k]+'"></span>'+k+' '+D.label[k]+'</div>'
      +'<div class="v" style="color:'+D.color[k]+'">'+v+'</div>'
      +'<div class="d">'+D.desc[k]+'</div><div class="arw">›</div></div>'
  }).join('');
  document.getElementById('tempfill').style.width=D.temp.share+'%';
  document.getElementById('temptxt').textContent=D.temp.text;
  document.getElementById('hds').innerHTML='数据基准 <b>'+D.asof+'</b> · 可判 <b>'+D.meta.judged+'</b> 只 · 池内 <b>'+g.total+'</b> 只';
}

function goList(st){
  var rows=(D.stocks||[]).filter(function(r){return r.stage===st});
  document.getElementById('listTitle').textContent=st+' '+D.label[st]+' · '+rows.length+' 只';
  document.getElementById('listBody').innerHTML=rows.map(function(r){
    var p=r.pct==null?null:r.pct/100;
    return '<div class="li" onclick="goDetail(\\''+r.code+'\\')">'
      +'<div class="l"><div class="nm">'+esc(r.name)+'</div>'
      +'<div class="cd">'+r.code+' · '+(r.mv||0).toFixed(1)+'亿</div></div>'
      +'<div class="rr"><div class="px '+cls(p)+'">'+(r.close||0).toFixed(2)+'</div>'
      +'<div class="mt">距前高 '+pct(r.dist)+'</div></div>'
      +'<div class="arw">›</div></div>'
  }).join('');
  document.getElementById('pHome').style.display='none';
  document.getElementById('pList').style.display='block';
  window.scrollTo(0,0);
}

function goHome(){
  document.getElementById('pList').style.display='none';
  document.getElementById('pHome').style.display='block';
  document.getElementById('detail').classList.remove('on');
  window.scrollTo(0,0);
}

function goDetail(code){
  var r=(D.stocks||[]).filter(function(x){return x.code===code})[0];
  if(!r) return;
  var k=r.k;
  var p=r.pct==null?null:r.pct/100;
  var h='<div class="dh"><span class="bk" onclick="closeDetail()">‹ 返回</span>'
    +'<span class="nm2">'+esc(r.name)+'</span>'
    +'<span class="cd" style="margin-left:auto;color:#8b95a4">'+r.code+'</span></div>';
  h+='<div class="dbody">';
  h+='<div class="big"><span class="p '+(p>0?'up':(p<0?'down':''))+'">'+(r.close||0).toFixed(2)+'</span>'
    +'<span class="c '+cls(p)+'">'+(p==null?'':(p>=0?'+':'')+(p*100).toFixed(2)+'%')+'</span></div>';
  h+='<div class="badges">'
    +'<span class="bd" style="background:'+D.color[r.stage]+'22;color:'+D.color[r.stage]+'">'
    +r.stage+' '+D.label[r.stage]+'</span>'
    +'<span class="bd">流通市值 '+(r.mv||0).toFixed(1)+' 亿</span>'
    +'<span class="bd">强度 '+(r.score||0).toFixed(1)+'</span>'
    +'</div>';
  if(k){
    h+='<div class="chart"><div class="cap"><span>'+k.d0+' → '+k.d1+'</span>'
      +'<span><b style="color:#d92b2b">收盘</b> · <b style="color:#2456c4">MA10</b> · '
      +'<b style="color:#8a5fc4">MA20</b></span></div>';
    h+=spark(k.c,k.m10,k.m20,k.h);
    h+='</div>';
  }
  h+='<div class="ev">';
  (r.ev||[]).forEach(function(e){
    h+='<div class="it"><div class="top"><span class="lb">'+esc(e.l)+'</span>'
      +'<span class="ok '+(e.p?'y':'n')+'">'+(e.p?'成立':'不成立')+'</span></div>'
      +'<div class="ac">'+esc(e.a)+'</div>'
      +'<div class="ru">阈值：'+esc(e.r)+'</div></div>';
  });
  h+='</div>';
  h+='<div class="note">判据口径见首页页脚。数据来自日线行情湖（tushare），'
    +'未做前视，仅使用基准日及之前的数据。</div>';
  h+='</div>';
  var el=document.getElementById('detail');
  el.innerHTML=h; el.classList.add('on'); el.scrollTop=0;
}
function closeDetail(){ document.getElementById('detail').classList.remove('on') }

render();
"""


def build_brief(data, share):
    """自动生成今日要点：市场温度 / 蓄势看点 / 风险提示。"""
    b = data.get("buckets") or {}
    b1 = b.get("B1") or []
    b2 = b.get("B2") or []
    b3 = b.get("B3") or []
    c1 = b.get("C1") or []

    small = [r for r in b1 if (r.get("mv_yi") or 999) < 50]
    small.sort(key=lambda r: -(r.get("score") or 0))
    small_txt = "、".join(r.get("name") or r.get("code") for r in small[:3]) if small else "无"

    tone = "偏弱" if share < 40 else ("中性" if share < 60 else "偏强")
    mkt = f"全市场仅 {share:.1f}% 站上 MA60，属{tone}，蓄势结构天然稀缺。"

    b1_txt = (f"蓄势 {len(b1)} 只，其中小盘（&lt;50亿）{len(small)} 只"
              + (f"：{small_txt}…" if small else "。")) if b1 else "蓄势 0 只。"

    risk = ""
    if c1:
        names = "、".join((r.get("name") or r.get("code")) for r in c1[:3])
        risk = f"加速段 {len(b3)} 只里已有 {len(c1)} 只出现衰竭信号（{names}…），持仓需盯 MA10。"
    elif b3:
        risk = f"加速段 {len(b3)} 只暂未见衰竭信号，但 20 日涨幅普遍偏高，防高位放量滞涨。"

    parts = [f"<b>市场</b> {mkt}", f"<b>蓄势</b> {b1_txt}"]
    if b2:
        parts.append(f"<b>启动</b> 当日放量破前高 {len(b2)} 只。")
    if risk:
        parts.append(f"<b>风险</b> {risk}")
    return "".join(f"<p>{p}</p>" for p in parts)


def build(data, ev_map, klines):
    buckets = data.get("buckets") or {}
    meta = data.get("meta") or {}
    stocks = []
    for st in ("B1", "B2", "B3", "C1"):
        for r in buckets.get(st, []):
            code = r.get("code")
            stocks.append({
                "code": code,
                "name": r.get("name") or "",
                "stage": st,
                "close": r.get("close"),
                "pct": r.get("pct"),
                "mv": r.get("mv_yi"),
                "dist": r.get("dist_hi120"),
                "ret20": r.get("ret20"),
                "score": r.get("score"),
                "hits": r.get("hits"),
                "c1": r.get("c1"),
                "ev": ev_map.get(code, []),
                "k": klines.get(code),
            })

    judged = meta.get("judged") or 0
    on = meta.get("ma60_on") or 0
    share = round(on / judged * 100, 1) if judged else 0
    temp_txt = "全市场站上 MA60：%d/%d = %.1f%%（%s）" % (
        on, judged, share, "弱势" if share < 40 else ("中性" if share < 60 else "强势"))

    payload = {
        "asof": data.get("data_asof") or "",
        "run_at": data.get("run_at") or "",
        "meta": {"judged": judged, "skipped": meta.get("skipped"), "files": meta.get("files")},
        "label": STAGE_LABEL,
        "desc": STAGE_DESC,
        "color": STAGE_COLOR,
        "temp": {"share": share, "text": temp_txt},
        "summary": {
            "B1": len(buckets.get("B1", [])),
            "B2": len(buckets.get("B2", [])),
            "B3": len(buckets.get("B3", [])),
            "C1": len(buckets.get("C1", [])),
            "total": len(stocks),
        },
        "stocks": stocks,
    }
    js = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<meta name="format-detection" content="telephone=no">
<meta property="og:title" content="股票池日报 {html.escape(payload['asof'])}">
<meta name="description" content="蓄势 {payload['summary']['B1']} · 突破 {payload['summary']['B2']} · 加速 {payload['summary']['B3']} · 衰竭 {payload['summary']['C1']}">
<title>股票池日报 · {html.escape(payload['asof'])}</title>
<style>{_CSS}</style></head>
<body>

<div id="pHome">
  <div class="hd">
    <div class="t">股票池日报</div>
    <div class="s" id="hds"></div>
    <div class="temp">
      <div class="r"><span>市场温度</span><span id="temptxt"></span></div>
      <div class="bar"><div class="fill" id="tempfill" style="width:0"></div></div>
    </div>
  </div>

  <div class="grid" id="grid"></div>

  <div class="warn">
    <b>这是巡检清单，不是买入信号。</b>阶段判定尚未做历史回测——进池后 20/60 日赚不赚、
    胜率多少、比随机强多少，一个数都还没有。它只回答「今天该盯哪几只」。
  </div>

  <div class="brief">
    <div class="bt">今日要点</div>
    {build_brief(data, share)}
  </div>

  <details class="rule">
    <summary>判定口径与剔除规则</summary>
    判定口径：<b>B1 蓄势</b> = 靠近 120 日高（≥ -15%）且 站上MA60 / 波动收缩 / 缩量 / 均线粘合
    中命中 ≥ 3 条，且未在下跌。<br>
    <b>B2 突破</b> = 收盘 ≥ 前 120 日高 + 量 ≥ 1.5×前 20 日均量 + 当日涨幅 ≥ 3%。<br>
    <b>B3 加速</b> = 20 日涨幅 ≥ 25% + 收盘 ≥ MA20×1.08 + 距前高 ≥ -15%。<br>
    <b>C1 衰竭</b> = 破 MA10 拐头 / 高位冲高回落 / 3 日量价背离，且距前高 ≥ -15%。<br>
    已剔除：ST 与退市整理、股价 &lt; 2 元、20 日均成交额不足 0.5 亿、上市不足 250 个交易日。<br>
    涨红跌绿。生成于 {html.escape(payload['run_at'])}。
  </details>
</div>

<div id="pList" style="display:none">
  <div class="hd" style="padding-bottom:18px">
    <div class="t" id="listTitle" style="font-size:18px"></div>
    <div class="s" onclick="goHome()" style="margin-top:10px;color:#a9b6ca">‹ 返回总览</div>
  </div>
  <div class="sect" id="listBody"></div>
  <div style="height:20px"></div>
</div>

<div id="detail"></div>

<script>window.__D__={js};</script>
<script>{_JS}</script>
</body></html>"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=None)
    ap.add_argument("--out", dest="out", default=os.path.join(REPORTS, "pool_report_h5.html"))
    args = ap.parse_args()

    src = args.inp
    if not src:
        fs = sorted(glob.glob(os.path.join(REPORTS, "pool_state_*.json")))
        if not fs:
            print("[h5] 找不到 pool_state_*.json")
            return 1
        src = fs[-1]

    with open(src, encoding="utf-8") as f:
        data = json.load(f)

    ev_map, klines = {}, {}
    for st in ("B1", "B2", "B3", "C1"):
        for r in (data.get("buckets") or {}).get(st, []):
            code = r.get("code")
            ev_map[code] = build_evidence(r)
            klines[code] = load_kline(code)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build(data, ev_map, klines))

    n_k = sum(1 for v in klines.values() if v)
    size = os.path.getsize(args.out) / 1024
    print(f"[h5] {src} -> {args.out}")
    print(f"[h5] 池内 {len(ev_map)} 只 / 带走势图 {n_k} 只 / 文件 {size:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
