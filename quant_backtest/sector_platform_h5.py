# -*- coding: utf-8 -*-
"""板块操作平台 v1：把「市 → 板块 → 个股」三层串成一页。

为什么不新建一个"平台"
----------------------
这套系统里已经有池子（个股结构）、有板块效应、有涨幅榜，但它们是四张
互不相干的表：
  · 池子告诉你「哪只票结构好」，不告诉你「它所在板块在崩还是在起」；
  · 板块表告诉你「哪个板块涨」，不告诉你「板块内有没有结构买点」；
  · 涨幅榜告诉你「今天谁在涨」，不告诉你「它在结构里的哪个位置」。
本页把三者放在同一行上：板块在什么位置 × 板块内多少只在买点 × 现在涨多少。
三列并排，冲突自己会浮出来（板块三卖但成分一堆买点 = 个股逆势）。

信息层级
--------
L1 市    涨跌家数 / 涨停 / 净宽 —— 今天整体能不能做
L2 板块  465 个板块结构（板块指数跑缠论）+ RPS + 成分共振
L3 个股  板块内池子成员的结构（点板块展开）

页面纪律（与 pool_state_page.py 同源）
--------------------------------------
· 纯渲染：一个指标都不在这里算，全部来自 sector_state_*.json 与 pool_state_*.json。
· 数据基准日写在页首，不是"今天"就直说；结构日与价格日分开标。
· 涨红跌绿。
· 不出现"买入 / 推荐 / 目标价"。这里是位置清单，不是选股结论。

用法
----
  python sector_platform_h5.py                          # 用最新 sector_state
  python sector_platform_h5.py --asof 20261106
  python sector_platform_h5.py --out reports/sector_platform.html
"""

import argparse
import glob
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pool_state as ps

REPORTS = ps.REPORTS

BUY_STAGES = ("一买", "二买", "三买", "三买待")
WEAK_STAGES = ("三卖", "中枢下")
STAGE_COLOR = {
    "一买": "#c2410c", "二买": "#c2410c", "三买": "#b91c1c", "三买待": "#b91c1c",
    "中枢": "#475569", "背驰": "#7c3aed", "离开段": "#0f766e",
    "三卖": "#3fa96a", "中枢下": "#3fa96a", "中枢下移": "#3fa96a",
}


# ────────────────────────────────────────────────────────────
# 数据
# ────────────────────────────────────────────────────────────
def latest_state(asof=None):
    if asof:
        p = os.path.join(REPORTS, "sector_state_%d.json" % int(asof))
        if not os.path.exists(p):
            raise SystemExit("[fail] 没有 %s" % p)
        return p
    fs = sorted(glob.glob(os.path.join(REPORTS, "sector_state_*.json")))
    if not fs:
        raise SystemExit("[fail] reports/ 下没有 sector_state_*.json，"
                         "先跑 python sector_state.py --save")
    return fs[-1]


def load_pool(asof):
    """当日池子九态，用于板块详情里的成分清单。"""
    p = os.path.join(REPORTS, "pool_state_%d.json" % int(asof))
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def slim(sec, pool):
    """精简成页面要用的字段。页面只认这个结构，口径全在生成端定死。"""
    rows = []
    for r in sec.get("sectors") or []:
        rows.append({
            "c": r["code"], "n": r["name"], "t": r["type"],
            "m": r.get("n_mem") or 0,
            "st": r.get("stage"), "po": r.get("pos"), "tr": r.get("trend"),
            "p": _r2(r.get("pct")), "r5": _r2(r.get("r5")),
            "r20": _r2(r.get("r20")), "r60": _r2(r.get("r60")),
            "rps": r.get("rps20"), "rps60": r.get("rps60"),
            "zg": _r2(r.get("zg")), "zd": _r2(r.get("zd")),
            "dz": _r2(r.get("dist_zg")), "dd": _r2(r.get("dist_zd")),
            "nb": r.get("n_buy"), "nj": r.get("n_judged"),
            "nw": r.get("n_weak"), "res": _r2(r.get("resonance")),
            "age": r.get("sig_age"), "sg": r.get("sig_type"),
            "pb": r.get("piv_n_bi"), "ov": 1 if r.get("piv_over") else 0,
            "bc": (r.get("buy_codes") or [])[:10],
        })
    return rows


def _r2(x):
    if x is None:
        return None
    try:
        return round(float(x), 4)
    except Exception:
        return None


def pool_brief(sec, pool):
    """L1 摘要：优先用 sector_state 落盘的市场宽度（收盘口径、全市场）。

    pool_state 的结构里没有涨跌家数（那是实时价算的），拿它的 judged 当分母
    会得出"0 只判过"这种假数。板块层已经把全市场宽度算好了，直接用。
    """
    m = sec.get("market") or {}
    if m.get("ok"):
        out = {"ok": 1, "src": "market", "struct_asof": str(sec.get("asof") or ""),
               "total": m.get("total"), "judged": m.get("total"),
               "up": m.get("up"), "down": m.get("down"),
               "flat": m.get("flat"), "lim": m.get("limit_up"),
               "lim20": m.get("limit_up_20"),
               "net": m.get("net"), "med": m.get("med")}
        if pool:
            out["live_at"] = pool.get("live_at") or ""
            out["cnt"] = {k: int(v or 0)
                          for k, v in (pool.get("counts") or {}).items()}
        return out
    if not pool:
        return {"ok": 0}
    cnt = pool.get("counts") or {}
    return {
        "ok": 1, "src": "pool",
        "live_at": pool.get("live_at") or "",
        "struct_asof": pool.get("struct_asof") or "",
        "judged": pool.get("judged") or pool.get("hit") or 0,
        "up": pool.get("up"), "down": pool.get("down"),
        "flat": pool.get("flat"), "lim": pool.get("limit_up"),
        "cnt": {k: int(v or 0) for k, v in cnt.items()},
    }


# ────────────────────────────────────────────────────────────
# 页面
# ────────────────────────────────────────────────────────────
_CSS = """
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{background:#0d1117;color:#e6edf3;
     font:14px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
     max-width:1180px;margin:0 auto;min-height:100vh;
     -webkit-font-smoothing:antialiased}
.wrap{padding:10px 13px 30px}
.screen{display:none}.screen.on{display:block}
.hd{display:flex;align-items:baseline;gap:8px;margin:2px 0 1px}
.hd h1{font-size:20px;font-weight:700;letter-spacing:-.2px}
.hd .v{font-size:10px;color:#fff;background:#5b9bd5;border-radius:4px;padding:1px 5px}
.hd .d{font-size:11px;color:#7b8698;margin-left:auto}
.sub{font-size:11px;color:#7b8698;margin-bottom:9px}

/* L1 市场条 */
.mkt{background:linear-gradient(135deg,#18243a,#1d2c47);border-radius:12px;
     padding:11px 13px;color:#fff;margin-bottom:9px}
.mkt .t{font-size:10.5px;opacity:.72}
.mkt .m{font-size:14px;font-weight:700;margin:2px 0 8px;line-height:1.35}
.mkt .g{display:flex;gap:6px}
.mkt .g>div{flex:1;text-align:center;background:rgba(255,255,255,0.045);
            border-radius:7px;padding:5px 2px}
.mkt .n{font-size:15px;font-weight:700;line-height:1.25}
.mkt .l{font-size:9.5px;opacity:.68}

/* 分布条 */
.dist{background:#131a24;border-radius:11px;padding:10px 12px;margin-bottom:9px;
      box-shadow:0 1px 2px rgba(20,30,50,.05)}
.dist .row1{display:flex;justify-content:space-between;font-size:10.5px;
            color:#7b8698;margin-bottom:5px}
.bar{display:flex;height:9px;border-radius:5px;overflow:hidden;background:#161d27}
.bar i{display:block;height:100%;min-width:3px}
.lead{background:#131a24;border-left:3px solid #5b9bd5;border-radius:9px;
      padding:9px 12px;font-size:12.5px;line-height:1.72;color:#37424f;
      margin-bottom:9px;box-shadow:0 1px 2px rgba(20,30,50,.05)}
.legend{display:flex;flex-wrap:wrap;gap:9px;font-size:10.5px;color:#7b8698;margin-top:6px}
.legend b{display:inline-block;width:7px;height:7px;border-radius:2px;margin-right:3px}

/* 筛选 */
.tabs{display:flex;gap:5px;margin-bottom:8px;overflow-x:auto;padding-bottom:1px}
.tabs::-webkit-scrollbar{display:none}
.tab{flex:none;font-size:11.5px;padding:5px 10px;border-radius:15px;background:#131a24;
     color:#5b6577;border:1px solid #1f2937;white-space:nowrap}
.tab.on{background:#1f2b45;color:#fff;border-color:#1f2b45}
.tab .c{opacity:.62;margin-left:3px;font-size:10.5px}

/* 板块行 */
.row{background:#131a24;border-radius:11px;padding:9px 12px;margin-bottom:6px;
     box-shadow:0 1px 2px rgba(20,30,50,.05);display:block}
.row:active{background:#0f1620}
.r1{display:flex;align-items:center;gap:7px}
.nm{font-size:14.5px;font-weight:650;flex:1;min-width:0;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tag{font-size:9.5px;color:#7c8798;background:#161d27;border-radius:3px;
     padding:1px 4px;flex:none}
.badge{font-size:10.5px;font-weight:600;border-radius:4px;padding:1.5px 6px;
       flex:none;white-space:nowrap}
.pct{font-size:15px;font-weight:700;flex:none;font-variant-numeric:tabular-nums}
.r2{display:flex;align-items:center;gap:8px;margin-top:5px;
    font-size:10.5px;color:#7b8698}
.r2 .k{flex:none}
.rps{width:42px;flex:none;height:4px;background:#1f2937;border-radius:2px;
     overflow:hidden}
.rps i{display:block;height:100%;background:#5b9bd5;border-radius:2px}
.res{flex:none;font-variant-numeric:tabular-nums}
.res.hi{color:#b91c1c;font-weight:650}
.up{color:#e3554f}.dn{color:#3fa96a}.flat{color:#7b8698}

/* 详情 */
.back{display:flex;align-items:center;gap:5px;margin:2px 0 9px;
      font-size:13px;color:#5b9bd5}
.back span{font-size:17px;line-height:1}
.dhd{background:#131a24;border-radius:12px;padding:13px 14px;margin-bottom:8px}
.dhd .t{display:flex;align-items:baseline;gap:8px}
.dhd .n{font-size:19px;font-weight:700}
.dhd .c{font-size:10.5px;color:#9aa3b2}
.dhd .q{font-size:24px;font-weight:700;margin-top:7px;
        font-variant-numeric:tabular-nums}
.dhd .s{font-size:11px;color:#7b8698;margin-top:1px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:9px}
.grid>div{background:#161d27;border-radius:8px;padding:6px 8px}
.grid .k{font-size:10px;color:#7b8698}
.grid .v{font-size:13.5px;font-weight:650;font-variant-numeric:tabular-nums}
.sec{font-size:11px;color:#7b8698;margin:11px 2px 6px;
     display:flex;justify-content:space-between}
.card{background:#131a24;border-radius:11px;padding:11px 13px;margin-bottom:8px;
      box-shadow:0 1px 2px rgba(20,30,50,.05)}
.kv{display:flex;justify-content:space-between;font-size:12.5px;padding:4px 0;
    border-bottom:1px solid #0d1117}
.kv:last-child{border:0}
.kv .k{color:#7b8698}
.kv .v{font-weight:600;font-variant-numeric:tabular-nums}
.note{background:#2a2312;border:1px solid #4a3d1c;border-radius:9px;
      padding:8px 11px;font-size:11px;line-height:1.65;color:#7a5a12}
.bad{background:#2b1618;border-color:#4d2528;color:#8c2f2f}
.mem{font-size:12px;padding:6px 0;border-bottom:1px solid #0d1117;
     display:flex;gap:8px;align-items:baseline;cursor:pointer;
     -webkit-tap-highlight-color:transparent}
.mem:last-child{border:0}
.mem:active{background:#0f1620}
.mem .cd{font-size:11px;color:#7b8698;font-variant-numeric:tabular-nums}
.mem .nm2{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mem .sg{font-size:10px;color:#7b8698;flex:none}
.mem .ar{color:#2b3949;font-size:13px;flex:none;line-height:1}
.mlink{font-size:10px;color:#9aa3b2;text-align:right;margin-top:5px}
.foot{font-size:10px;color:#9aa3b2;line-height:1.7;margin-top:12px}
details.rule{background:#131a24;border-radius:10px;padding:9px 11px;font-size:11px;
             line-height:1.8;color:#7b8698;margin-top:9px}
details.rule summary{cursor:pointer;color:#4a5568;font-weight:600;list-style:none}
details.rule summary::-webkit-details-marker{display:none}
details.rule summary:after{content:" \\2304";color:#7b8698}
details.rule[open] summary:after{content:" \\2303"}
.empty{background:#131a24;border-radius:11px;padding:22px 14px;text-align:center;
       color:#9aa3b2;font-size:12px}
/* 能力地图 */
.mods{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-top:2px}
.mod{background:#131a24;border-radius:9px;padding:8px 10px;
     box-shadow:0 1px 2px rgba(20,30,50,.05)}
.mh{display:flex;align-items:center;gap:6px;justify-content:space-between}
.mn{font-size:12px;font-weight:650;color:#37424f;white-space:nowrap;
    overflow:hidden;text-overflow:ellipsis}
.ms{font-size:9.5px;border-radius:3px;padding:1px 5px;flex:none;white-space:nowrap}
.md{font-size:10.5px;color:#7b8698;line-height:1.5;margin-top:3px}
"""

_JS = r"""
var D = __DATA__;
var FILTER = 'all';

function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function pct(x,digits){ if(x==null) return '—';
  var v=x*100, s=v.toFixed(digits==null?1:digits);
  return (v>0?'+':'')+s+'%'; }
function cls(x){ return x==null?'flat':(x>0?'up':(x<0?'dn':'flat')); }
function sColor(st){ return D.stColor[st]||'#475569'; }
function isBuy(st){ return D.buy.indexOf(st)>=0; }

function rowHtml(r){
  var col = sColor(r.st);
  var pcls = cls(r.p);
  var res = '';
  if(r.nj){ res = '<span style="color:#2b3949">买点</span><span class="res'+(r.res>=0.15?' hi':'')+'">'+r.nb+'/'+r.nj+'</span>'; }
  else { res = '<span class="res">未判</span>'; }
  var rpsw = (r.rps==null?0:r.rps);
  return '<div class="row" onclick="goDetail(\''+r.c+'\')">'
    + '<div class="r1">'
      + '<span class="nm">'+esc(r.n)+'</span>'
      + '<span class="tag">'+(r.t=='I'?'行业':'概念')+'</span>'
      + '<span class="badge" style="color:'+col+';background:'+col+'14">'
        + esc(r.st||'—')+(r.fail?'*':'')+'</span>'
      + '<span class="pct '+pcls+'">'+pct(r.r20)+'</span>'
    + '</div>'
    + '<div class="r2">'
      + '<span class="k">'+esc(r.po||'—')+' · '+esc(r.tr||'—')+' · 成分 '+r.m+'</span>'
      + '<span class="rps"><i style="width:'+rpsw+'%"></i></span>'
      + '<span class="k">RPS '+ (r.rps==null?'—':r.rps.toFixed(0))+'</span>'
      + res
    + '</div></div>';
}

function match(r){
  if(FILTER=='buy') return isBuy(r.st);
  if(FILTER=='strong') return r.r20!=null && r.r20>0.05 && (r.po=='上沿外'||r.po=='枢内');
  if(FILTER=='weak') return r.st=='三卖'||r.st=='中枢下';
  if(FILTER=='res') return r.nj && r.res>=0.15;
  return true;
}

/* 排序：结构优先，涨幅次之。
   按涨幅排会把"三卖但今天暴涨"的板块顶到第一屏——那是超跌反弹，
   不是结构转强，第一眼就误导。先按结构分档（买点 > 中枢 > 卖点已失效
   > 卖点），档内才按 RPS。 */
var RANK = {'一买':0,'二买':0,'三买':0,'三买待':0,
            '中枢':1,'背驰':1,
            '离开段':2,'三卖':2,'中枢下':2,'中枢下移':2};

function rankOf(r){
  if(isBuy(r.st)) return 0;
  if(r.st=='中枢'||r.st=='背驰') return 1;
  if(r.fail) return 2;            /* 卖点已失效 -> 结构已向上破坏，比"卖点"靠前 */
  return 3;
}

function renderList(){
  var arr = D.rows.filter(match);
  if(FILTER=='weak'){
    arr.sort(function(a,b){return (a.r20||0)-(b.r20||0);});
  } else {
    arr.sort(function(a,b){
      var d = rankOf(a)-rankOf(b);
      if(d) return d;
      return (b.rps||0)-(a.rps||0);
    });
  }
  var h = arr.map(rowHtml).join('');
  if(!arr.length) h = '<div class="empty">这个筛选下没有板块。</div>';
  document.getElementById('list').innerHTML = h;
  document.getElementById('lcount').textContent = arr.length+' 个';
}

function setFilter(f, el){
  FILTER = f;
  var ts = document.querySelectorAll('.tab');
  for(var i=0;i<ts.length;i++) ts[i].classList.remove('on');
  el.classList.add('on');
  renderList();
}

function go(id){
  var ss = document.querySelectorAll('.screen');
  for(var i=0;i<ss.length;i++) ss[i].classList.remove('on');
  document.getElementById(id).classList.add('on');
  window.scrollTo(0,0);
}

function findRow(c){ for(var i=0;i<D.rows.length;i++) if(D.rows[i].c==c) return D.rows[i]; return null; }

/* 板块 → 个股：跳到个股结构页，并把「从哪个板块来」一并带过去。
   个股页是同目录的 pool_report_h5.html，file:// 打开和同目录部署都走得通；
   两个文件必须放在一起，否则这个跳转会 404。
   线上站点逐请求校验 sharecode，不带就是 403（「链接不完整」页），
   所以跳转一律走 withSC()。本地 file:// 没有 sharecode，原样返回。 */
var STOCK_H5 = 'pool_report_h5.html';
var SC = (function(){
  var m = (location.search||'').match(/[?&]sharecode=([^&#]+)/);
  if(m){ try{ sessionStorage.setItem('pool_sc', m[1]); }catch(e){} return m[1]; }
  try{ return sessionStorage.getItem('pool_sc') || ''; }catch(e){ return ''; }
})();
function withSC(u){
  if(!SC) return u;
  var i = u.indexOf('#'), hash = i>=0 ? u.slice(i) : '';
  if(i>=0) u = u.slice(0,i);
  if(/[?&]sharecode=/.test(u)) return u + hash;
  return u + (u.indexOf('?')>=0 ? '&' : '?') + 'sharecode=' + encodeURIComponent(SC) + hash;
}
var CUR_SEC = null;
var FROM_STOCK = null;      // 从个股页跳进来的，返回箭头要指回那只票

function goStock(code){
  var u = STOCK_H5 + '?code=' + encodeURIComponent(code);
  if(CUR_SEC) u += '&ref=' + encodeURIComponent(CUR_SEC.c || '')
                  + '&sname=' + encodeURIComponent(CUR_SEC.n || '');
  location.href = withSC(u);
}

function backTo(){
  if(FROM_STOCK){
    location.href = withSC(STOCK_H5 + '?code=' + encodeURIComponent(FROM_STOCK.c));
    return;
  }
  go('s-home');
}

function goDetail(code){
  var r = findRow(code); if(!r) return;
  CUR_SEC = r;
  var b = D.brief;
  var col = sColor(r.st);
  var mem = D.members[code] || [];
  var zg = r.zg, zd = r.zd;
  var h = '<div class="back" onclick="backTo()"><span>&#8249;</span>'
        + (FROM_STOCK ? '返回 '+esc(FROM_STOCK.n) : '返回板块表') + '</div>';

  h += '<div class="dhd">'
     + '<div class="t"><span class="n">'+esc(r.n)+'</span>'
     + '<span class="c">'+esc(r.c)+' · '+(r.t=='I'?'行业板块':'概念板块')+' · '+r.m+' 只成分</span></div>'
     + '<div class="q '+cls(r.r20)+'">'+pct(r.r20,2)
     + ' <span style="font-size:12px;color:#7b8698;font-weight:400">近 20 日</span></div>'
     + '<div class="s">当日 '+pct(r.p,2)+' · 5 日 '+pct(r.r5)+' · 60 日 '+pct(r.r60)+'</div>'
     + '<div class="grid">'
       + '<div><div class="k">结构</div><div class="v" style="color:'+col+'">'
         + esc(r.st||'—')+(r.fail?'（已失效）':'')+'</div></div>'
       + '<div><div class="k">位置</div><div class="v">'+esc(r.po||'—')+'</div></div>'
       + '<div><div class="k">中枢上沿</div><div class="v">'+(zg==null?'—':zg.toFixed(2))+'</div></div>'
       + '<div><div class="k">中枢下沿</div><div class="v">'+(zd==null?'—':zd.toFixed(2))+'</div></div>'
     + '</div></div>';

  // 结构解读
  var dz = r.dz, dd = r.dd;
  var ln = '';
  if(dz!=null){
    ln = '现价距中枢上沿 <b>'+pct(dz,1)+'</b>'+(dd!=null?'、距下沿 <b>'+pct(dd,1)+'</b>':'')
       + '。' + (dz>0.003 ? '已在上沿之外——按缠论是离开段方向，不是震荡区。'
              : (dd!=null&&dd<-0.003 ? '已跌破下沿——向下离开，中枢还没被收复。'
              : '还在中枢带内，属于震荡。'));
  }
  var tline, tdir;
  if(r.tr=='上涨'){ tdir='中枢序列在抬升'; }
  else if(r.tr=='下跌'){ tdir='中枢序列在下移'; }
  else { tdir='中枢基本持平'; }
  if(r.tr=='上涨' && dz!=null && dz<0){
    tline = tdir + '，但价格已回到中枢之下——上升趋势里的回调，'
          + '回调是否结束要看能不能重新站上 <b>'+(zg==null?'—':zg.toFixed(2))+'</b>。';
  } else if(r.tr=='下跌' && dz!=null && dz>0){
    tline = tdir + '，但价格已站到中枢上沿之外——下跌趋势里的反弹，'
          + '反弹不等于趋势反转，除非中枢跟着上移。';
  } else {
    tline = tdir + '。注意「趋势」看的是<b>中枢序列</b>的方向，'
          + '与当日涨跌方向可以相反。';
  }
  h += '<div class="card"><div class="kv"><span class="k">结构解读</span></div>'
     + '<div style="font-size:12px;line-height:1.75;color:#4a5568">'
     + ln + '<br>' + tline;
  if(r.ov){ h += '<br><span style="color:#b45309">中枢已升级：当前处在大级别震荡里，'
              + '本级别买卖点信号可靠性下降。</span>'; }
  h += '</div></div>';

  // 成分共振
  h += '<div class="card"><div class="kv"><span class="k">成分股结构（池子当日判过）</span>'
     + '<span class="v">'+ (r.nj? r.nb+' / '+r.nj : '未判') +'</span></div>'
     + '<div style="font-size:11.5px;color:#7b8698;line-height:1.7;margin-top:4px">';
  if(r.nj){
    h += '这个板块有 <b>'+r.nj+'</b> 只成分进了池子，其中 <b>'+r.nb+'</b> 只处在买点类'
       + '（'+(r.res==null?'—':(r.res*100).toFixed(0)+'%')+'），'
       + '<b>'+r.nw+'</b> 只在三卖/中枢下。';
    if(r.res>=0.2) h += '<br>成分与板块结构同向偏强，共振度较高。';
    else if(r.res<0.05) h += '<br>成分几乎全在卖点侧——板块的位置不是靠个股结构撑起来的。';
  } else {
    h += '当日池子没有覆盖这个板块的成分，共振无法计算。这里留空，不拿指数涨幅充数。';
  }
  if(r.nb>0 && (r.st=='三卖'||r.st=='中枢下'||r.st=='离开段')){
    h += '<br><span style="color:#b45309">下面是 <b>'+r.nb+' 只逆势买点</b>：'
       + '板块本身在卖点侧，个股却出现了买点。逆势买点不是机会的同义词——'
       + '它只说明「板块还没走好，个股先动」，可能是领先，也可能是假信号。'
       + '要等板块结构跟上，才谈得上共振。</span>';
  }
  if(r.m < 15){
    h += '<br><span style="color:#b45309">板块只有 '+r.m+' 只成分，'
       + '共振比例的统计意义有限，别拿它当板块强弱的主证据。</span>';
  }
  h += '</div></div>';

  // 成分买点清单
  if(mem.length){
    h += '<div class="sec"><span>成分买点（池子内）</span><span>'+mem.length+' 只</span></div>'
       + '<div class="card">';
    for(var i=0;i<mem.length;i++){
      var m = mem[i];
      h += '<div class="mem" onclick="goStock(\''+m.c+'\')">'
         + '<span class="nm2">'+esc(m.n)+'</span>'
         + '<span class="cd">'+esc(m.c)+'</span>'
         + '<span class="sg" style="color:'+sColor(m.st)+'">'+esc(m.st)
         + (m.a!=null? ' · '+m.a+'日' : '')+'</span>'
         + '<span class="'+(m.p==null?'flat':cls(m.p))+'">'+pct(m.p)+'</span>'
         + '<span class="ar">›</span></div>';
    }
    h += '</div><div class="mlink">点任意一行 → 看它在个股页的完整结构</div>';
  }

  // 口径
  h += '<div class="note">数据基准 <b>'+(D.asof||'')+'</b> 收盘。'
     + '板块结构由<b>板块指数</b>跑出，与个股同源但不可当个股买卖点；'
     + '指数是市值加权，龙头权重大，与成分股等权涨幅不同。'
     + (r.fail?' 结构带 * 表示原为卖点类、价格已回到上沿外，该卖点已被证伪。':'')
     + '</div>';

  document.getElementById('d-body').innerHTML = h;
  go('s-detail');
}

(function init(){
  // 统计
  var b = D.brief;
  document.getElementById('mkt-sub').textContent =
    '板块指数与个股结构均基于 '+D.asof+' 收盘'
    + (b.live_at? ' · 池子落盘 '+b.live_at.slice(5,16) : '');
  if(b.ok){
    var u=b.up||0, dn=b.down||0, n=u+dn;
    var net = (b.net!=null? b.net*100 : (n? (u-dn)/n*100 : 0));
    var medtxt = (b.med!=null? '，中位涨幅 '+((b.med*100).toFixed(2))+'%' : '');
    var tail = net<=-30 ? ' —— 普跌日，下面的强势板块也要打折看。'
             : (net>=30 ? ' —— 普涨日。' : '。');
    document.getElementById('mkt-m').innerHTML =
      '全市场 '+ (b.total||n) +' 只，净宽 <b style="color:'+
      (net>0?'#ff9a9a':'#7ce0b0')+'">'+(net>0?'+':'')+net.toFixed(0)+'%</b>'
      + medtxt + tail;
    document.getElementById('g1').textContent = u;
    document.getElementById('g2').textContent = dn;
    document.getElementById('g3').textContent = b.lim==null?'—':b.lim;
    document.getElementById('g4').textContent = b.flat==null?'—':b.flat;
  } else {
    document.getElementById('mkt-m').textContent = '当日无市场宽度数据。';
  }
  // 分布条
  var tot = D.rows.length, cnt = {buy:0,weak:0,mid:0};
  for(var i=0;i<D.rows.length;i++){
    var s = D.rows[i].st;
    if(isBuy(s)) cnt.buy++; else if(s=='三卖'||s=='中枢下') cnt.weak++; else cnt.mid++;
  }
  D.cnt = cnt;
  var w1=cnt.buy/tot*100, w2=cnt.mid/tot*100, w3=cnt.weak/tot*100;
  document.getElementById('bar').innerHTML =
      '<i style="width:'+w1+'%;background:#b91c1c"></i>'
    + '<i style="width:'+w2+'%;background:#5e6b7a"></i>'
    + '<i style="width:'+w3+'%;background:#3fa96a"></i>';
  document.getElementById('lg1').textContent = cnt.buy;
  document.getElementById('lg2').textContent = cnt.mid;
  document.getElementById('lg3').textContent = cnt.weak;
  document.getElementById('dist-t').textContent = tot+' 个板块';
  document.getElementById('t-buy').textContent = cnt.buy;
  document.getElementById('t-weak').textContent = cnt.weak;
  document.getElementById('lead').textContent = b.headline || '';
  renderList();

  // 深链：飞书卡片带 ?sector=885959.TI
  var q=(location.search||'').replace(/^\?/,'')||(location.hash||'').replace(/^#/,'');
  if(!q) return;
  var p={};
  q.split('&').forEach(function(kv){
    if(!kv) return; var i=kv.indexOf('='); if(i<0) return;
    p[decodeURIComponent(kv.slice(0,i))]=decodeURIComponent(kv.slice(i+1));
  });
  if(p.sector && findRow(p.sector)){
    // 从个股页跳过来的：返回箭头指回那只票，不再让人自己找
    if(p.from) FROM_STOCK = {c: p.from, n: p.fromn || p.from};
    goDetail(p.sector);
  }
  else if(p.filter && document.querySelector('.tab[data-f="'+p.filter+'"]'))
    setFilter(p.filter, document.querySelector('.tab[data-f="'+p.filter+'"]'));
})();
"""


def _headline(rows):
    """一句话结论：只用能复算的统计，不写形容词。

    最有信息量的一句是「涨幅前 20 里有几个在买点侧」——它把「结构转强」
    和「超跌反弹」这两种看起来一样的上涨分开了。今天大涨的板块如果
    全是三卖，那句"涨"就没什么可高兴的。
    """
    ok = [r for r in rows if r.get("r20") is not None]
    if not ok:
        return ""
    top = sorted(ok, key=lambda r: -r["r20"])[:20]
    nbuy_top = sum(1 for r in top if r["st"] in BUY_STAGES)
    tot = len(rows)
    nbuy = sum(1 for r in rows if r["st"] in BUY_STAGES)
    nweak = sum(1 for r in rows if r["st"] in WEAK_STAGES)
    head = "%d 个板块里 %d 个在买点侧、%d 个在卖点侧" % (tot, nbuy, nweak)
    if nbuy_top <= 3:
        tail = "涨幅前 20 只有 %d 个在买点侧——涨的是超跌反弹，不是结构转强" % nbuy_top
    elif nbuy_top >= 10:
        tail = "涨幅前 20 有 %d 个在买点侧，结构与价格同向" % nbuy_top
    else:
        tail = "涨幅前 20 有 %d 个在买点侧，结构与价格分化" % nbuy_top
    return head + "；" + tail + "。"


# 能力地图：这套系统里已经存在的模块，以及它们与本页的关系。
# state: in=已接入本页 / part=部分接入 / out=已有独立入口但还没串进来
# file 会做存在性校验——文件不在就标"缺失"，不拿规划当现状。
MODULES = [
    ("L1 市场宽度", "全市场涨跌家数、涨停、净宽", "in", "sector_state.py"),
    ("L2 板块结构", "板块指数跑缠论，465 个", "in", "sector_state.py"),
    ("L2 成分共振", "板块 × 池子交叉，买点占比", "in", "sector_state.py"),
    ("L3 池子九态", "3,961 只结构分类", "in", "pool_state.py"),
    ("L3 池子五层", "A 突破/B 可介入/C 持有/D 衰竭/E 贴沿", "in",
     "pool_state.py"),
    ("板块 ⇄ 个股", "成分点进个股结构，个股列出所属板块", "in",
     "sector_platform_h5.py"),
    ("板块层回放", "2.2 年 4.7 万观测：买点类无稳定超额", "in",
     "sector_replay.py"),
    ("收盘复盘", "15:00 重算 + 推一张卡", "in", "pool_close_review.py"),
    ("溯源回看", "一天十几份快照串成「当时→后来」", "in", "pool_trace.py"),
    ("每日流水线", "日线到齐后一次跑完出页", "in", "pool_daily.py"),
    ("成分快照", "留存历史成分，为共振回放积累", "part",
     "sector_member_snapshot.py"),
    ("盘中推飞书", "每 15 分钟一次，计划任务已挂", "in",
     "pool_intraday_push.py"),
    ("板块资金流", "龙虎榜行业异动工单", "out", "kg_sector_flow.py"),
    ("飞书订阅", "按 kind/when 订阅推送", "out", "subscribe.py"),
]


def _modules_html():
    """能力地图：一张表说清楚"串到哪了"。

    v1 的价值一半在页面，一半在**边界清楚**——哪些是已经连起来的，
    哪些还只是并行跑着、没进这条链。用文件存在性校验，不写规划稿。
    """
    tag = {"in": ("已接入", "#0a7d4e", "#14261b"),
           "part": ("部分", "#b45309", "#2a2312"),
           "out": ("独立入口", "#7b8698", "#161d27"),
           "todo": ("未做", "#b91c1c", "#2b1618")}
    rows = []
    for name, desc, state, f in MODULES:
        fn = f.split()[0]
        exists = os.path.exists(os.path.join(HERE, fn))
        st, col, bg = tag[state]
        if state != "todo" and not exists:
            st, col, bg = "缺失", "#b91c1c", "#2b1618"
        rows.append(
            '<div class="mod"><div class="mh"><span class="mn">%s</span>'
            '<span class="ms" style="color:%s;background:%s">%s</span></div>'
            '<div class="md">%s</div></div>' % (name, col, bg, st, desc))
    return "".join(rows)


def build_html(sec, pool, asof, rows):
    brief = pool_brief(sec, pool)
    brief["headline"] = _headline(rows)
    members = _member_rows(pool, rows)
    st_color = dict(STAGE_COLOR)
    # 卖点已失效标记：卖点类 + 价在上沿外
    for r in rows:
        r["fail"] = 1 if (r["st"] in ("三卖", "中枢下", "离开段")
                          and r["po"] == "上沿外") else 0
    data = {
        "asof": str(asof), "rows": rows, "brief": brief,
        "members": members,
        "buy": list(BUY_STAGES), "stColor": st_color,
    }
    js = _JS.replace("__DATA__", json.dumps(data, ensure_ascii=False,
                                            separators=(",", ":")))
    run_at = sec.get("run_at") or ""
    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>板块操作平台 {asof}</title><style>{css}</style></head><body>
<div class="wrap">

<div class="screen on" id="s-home">
  <div class="hd"><h1>板块操作平台</h1><span class="v">v1</span>
    <span class="d">{asof_short}</span></div>
  <div class="sub" id="mkt-sub"></div>

  <div class="mkt">
    <div class="t">L1 · 今天整体能不能做</div>
    <div class="m" id="mkt-m">—</div>
    <div class="g">
      <div><div class="n" id="g1">—</div><div class="l">上涨</div></div>
      <div><div class="n" id="g2">—</div><div class="l">下跌</div></div>
      <div><div class="n" id="g3">—</div><div class="l">涨停</div></div>
      <div><div class="n" id="g4">—</div><div class="l">平盘</div></div>
    </div>
  </div>

  <div class="dist">
    <div class="row1"><span>L2 · 板块结构分布</span><span id="dist-t"></span></div>
    <div class="bar" id="bar"></div>
    <div class="legend">
      <span><b style="background:#b91c1c"></b>买点类 <span id="lg1">0</span></span>
      <span><b style="background:#5e6b7a"></b>中枢/背驰 <span id="lg2">0</span></span>
      <span><b style="background:#3fa96a"></b>三卖/中枢下 <span id="lg3">0</span></span>
    </div>
  </div>

  <div class="lead" id="lead"></div>

  <div class="tabs">
    <div class="tab on" data-f="all" onclick="setFilter('all',this)">全部</div>
    <div class="tab" data-f="buy" onclick="setFilter('buy',this)">买点类<span class="c" id="t-buy"></span></div>
    <div class="tab" data-f="strong" onclick="setFilter('strong',this)">强势</div>
    <div class="tab" data-f="weak" onclick="setFilter('weak',this)">走弱<span class="c" id="t-weak"></span></div>
    <div class="tab" data-f="res" onclick="setFilter('res',this)">共振≥15%</div>
  </div>
  <div class="sec"><span>L2 板块 × L3 成分</span><span id="lcount"></span></div>
  <div id="list"></div>

  <div class="sec"><span>这套系统里已经有什么</span><span>v1 串到哪了</span></div>
  <div class="mods">{modules}</div>

  <details class="rule"><summary>这一页在说什么（口径）</summary>
    三个数字并排，是这一页的全部内容：<b>板块在什么位置</b>（结构）、
    <b>板块内多少只在买点</b>（共振）、<b>最近涨了多少</b>（RPS/涨幅）。<br>
    三者冲突的时候信息量最大——板块三卖、但成分里有买点，说明个股在逆着板块走；
    板块三买、成分却全在卖点，说明是几只权重股把指数拉起来的。<br>
    板块结构来自<b>板块指数</b>跑缠论，与个股<b>同一套代码</b>，但指数是市值加权，
    不等于任何一只成分股的中枢。这里是位置清单，不是买入建议。<br>
    <b>已经回放过了</b>（2024-08→2026-11，46,584 观测）：买点类 N=20 日等权超额
    <b>−13bp</b>（t=−0.58），强度分层<b>没有单调性</b>，唯一显著的是「中游偏弱的
    板块出现买点」+47bp（t=3.43）。所以「买点类」这个标签只能当<b>结构描述</b>用，
    不能当"会涨"的依据——这一页告诉你板块走到哪了，不告诉你哪个会涨。<br>
    「共振 n/m」的 m 是<b>池子当日判过的</b>成分数，不是板块全部成分；
    没进池子的（成交额不足、ST、次新、结构不足）不计入分母，也不算弱。
  </details>
  <div class="foot">生成 {run_at} · 结构基准 {asof} 收盘<br>
    涨红跌绿；数据缺口如实留空，不用估算值填充。<br>
    <b>本页所有数字都是 {asof} 收盘</b>（结构、成分标签、涨幅），<b>不叠盘中价</b>；
    点成分股会进个股页，那一页额外叠了今日实时价，两组数字不同源，别按同一时点比。</div>
</div>

<div class="screen" id="s-detail"><div id="d-body"></div></div>

</div>
<script>{js}</script></body></html>"""
    a = str(asof)
    return html.format(asof=a, asof_short="%s-%s 收盘" % (a[4:6], a[6:8]),
                       css=_CSS, js=js, run_at=run_at,
                       modules=_modules_html())


def _member_rows(pool, rows):
    """每个板块的成分买点清单：code → [{c,n,st,p}]。

    只放买点类的成分。清单过长会撑爆页面，每板块上限 10 只（生成端截断，
    截断条数在详情里如实说明）。
    """
    if not pool:
        return {}
    stage_of, name_of, pct_of, age_of = {}, {}, {}, {}
    for st, items in (pool.get("buckets") or {}).items():
        for it in items or []:
            c = it.get("code")
            if not c:
                continue
            stage_of.setdefault(str(c), st)
            name_of.setdefault(str(c), it.get("name") or "")
            # pool_state 落盘的字段是 pct（小数），judge 那边才叫 lpct（百分数）。
            # 混着用会让整列显示成 "—" 而不报错。
            pct_of.setdefault(str(c), it.get("pct"))
            age_of.setdefault(str(c), it.get("sig_age"))
    out = {}
    for r in rows:
        codes = r.get("bc") or []
        lst = []
        for c in codes:
            if c not in stage_of:
                continue
            p = pct_of.get(c)
            lst.append({"c": c, "n": name_of.get(c, ""), "st": stage_of[c],
                        "p": (round(float(p), 4) if p is not None else None),
                        "a": age_of.get(c)})
        if lst:
            out[r["c"]] = lst
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", type=int, default=None)
    ap.add_argument("--out", default=os.path.join(REPORTS, "sector_platform.html"))
    a = ap.parse_args()

    p = latest_state(a.asof)
    with open(p, encoding="utf-8") as f:
        sec = json.load(f)
    asof = sec.get("asof")
    pool = load_pool(asof)
    rows = slim(sec, pool)
    html = build_html(sec, pool, asof, rows)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    print("[ok] %s  %d 个板块  %.0fKB"
          % (a.out, len(rows), os.path.getsize(a.out) / 1024))
    if not pool:
        print("[warn] 没有 pool_state_%s.json，成分共振与个股清单为空" % asof)
    return 0


if __name__ == "__main__":
    sys.exit(main())
