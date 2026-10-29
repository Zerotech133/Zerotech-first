# -*- coding: utf-8 -*-
"""kg 页族 HTML 片段（resonance/net/overview）（P4 api_kg 拆分第一批：自 api_kg.py 纯搬运，零行为变化）。"""
from api_common import (
    QBT_DIR)



def _kg_resonance_block():
    """K207·E2 三线共振看板卡：直读 kg_resonance_last.json（K206 影子
    落盘，只记不推）。last 文件为单主题快照，卡面明示主题与生成时间。"""
    import json as _js
    import os as _os
    from html import escape as _e
    path = _os.path.join(QBT_DIR, "data", "kg_resonance_last.json")
    try:
        with open(path, encoding="utf-8") as f:
            r = _js.load(f)
    except Exception:
        return ("<div class='sec'><h2>🎯 三线共振 · 情报本体</h2>"
                "<div class='dim'>暂无快照——运行 <code>python "
                "kg_resonance.py query 人形机器人</code> 生成"
                "（K206 影子纪律：只记不推）。</div></div>")
    full = r.get("full") or []
    partial = r.get("partial") or []

    def _segs(it):
        return "；".join("@%s" % (s[0] or "") for s in (it.get("segs")
                                                        or []))

    rows_f = []
    for it in full:
        sig = ""
        if it.get("sigs"):
            tp, d, dd, px = it["sigs"][0]
            sig = "%s %s（detect %s）@ %s" % (tp, d, dd, px)
        vt = " ".join("%s%s" % (k, v)
                      for k, v in (it.get("verdicts") or {}).items())
        rows_f.append(
            "<tr><td><a href='/kg?c=%s'>%s</a> <span class='dim'>%s"
            "</span></td><td>%s</td><td class='dim'>%s</td><td>%s</td>"
            "<td style='color:var(--ylw-500)'>%s</td></tr>"
            % (_e(it["code"]), _e(it["name"]), _e(it["code"]),
               _e(_segs(it)) or "—", _e(sig) or "—", _e(vt) or "—",
               "★" * min(5, int(it.get("stars") or 0))))
    rows_p = []
    for it in partial:
        lines = it.get("lines") or []
        has = "+".join(lines)
        miss = next((ln for ln in ("结构", "传导", "共识")
                     if ln not in lines), "")
        sig = ""
        if it.get("sigs"):
            tp, d, dd = it["sigs"][0][:3]
            sig = "最近 %s %s（detect %s）" % (tp, d, dd)
        rows_p.append(
            "<tr><td><a href='/kg?c=%s'>%s</a> <span class='dim'>%s"
            "</span></td><td>%s</td><td><span class='bdg n'>缺%s</span>"
            "</td><td class='dim'>%s</td><td class='dim'>%s</td></tr>"
            % (_e(it["code"]), _e(it["name"]), _e(it["code"]), _e(has),
               _e(miss), _e(_segs(it)) or "—", _e(sig) or "—"))
    tf = ("<table class='sticky'><tr><th>标的</th><th>传导环节</th>"
          "<th>最近结构信号</th><th>派系共识</th><th>共识度</th></tr>%s"
          "</table>" % ("".join(rows_f) or
                        "<tr><td colspan=5 class='dim'>当前快照无三线共振"
                        "</td></tr>"))
    tp_tbl = ("<table class='sticky'><tr><th>标的</th><th>已有线</th>"
              "<th>缺线</th><th>传导环节</th><th>结构信号</th></tr>%s"
              "</table>" % ("".join(rows_p) or
                            "<tr><td colspan=5 class='dim'>无</td></tr>"))
    meta = ("主题快照 <b style='color:var(--gray-7)'>%s</b> · 缠论窗口 %s 起共 "
            "%d 自然日 · 生成 %s · 单线 %d 不输出 · last 文件单主题语义，"
            "刷新：<code>python kg_resonance.py query %s</code>"
            "（主题含空格需引号）"
            % (_e(r.get("theme") or ""), _e(r.get("d0") or ""),
               int(r.get("window_days") or 0),
               _e(r.get("generated_at") or ""),
               int(r.get("n_single") or 0), _e(r.get("theme") or "")))
    return ("<div class='sec'><h2>🎯 三线共振 · 情报本体（结构×传导×共识）"
            "</h2><div class='dim' style='margin:2px 0 8px'>%s</div>"
            "<div style='font-size:13.5px'><b>★ 三线共振 %d</b>（情报级："
            "结构买点+产业位置+派系背书齐备）</div>%s"
            "<div style='font-size:13.5px;margin-top:8px'><b>☆ 两线候选 "
            "%d</b>（缺线标注）</div>%s</div>"
            % (meta, len(full), tf, len(partial), tp_tbl))


def _kg_net_html(mg):
    """KG-V1/V2/V3 集团网络画布：视觉映射（节点∝共现度 / 边宽∝共现
    次数 / watchlist 红描边 / 图例按集团过滤 / 悬停邻域聚焦）+ 枢纽
    弱边折叠聚合（deg>500 的 w<8 共现默认折叠，单击弹清单释放）+
    V2 三件：净流向对冲着色（绿=同向同盟 / 橙=对手对冲）、场景
    绑定布局（力导向/环形按集团/网格按度数）、右键三态聚焦
    （一跳→二跳→清除）。V3：操作回放——边附按日序列 s，
    滑杆按交易日推进、累计口径重渲（节点随首条边出现、边宽/边色
    随累计强度生长、聚焦邻接按当时可见边）。
    数据 kg_portal.map_graph（json 快照 + Cypher 在线边）。"""
    import json as _json
    payload = _json.dumps(
        {"nodes": mg["nodes"], "edges": mg["edges"],
         "watchlist": mg["watchlist"]},
        ensure_ascii=False, separators=(",", ":"))
    head = (
        "<div class='sec'><h2>🧭 集团网络画布</h2>"
        "<div class='dim' style='margin:2px 0 6px'>窗口 %d 天（跟随"
        "集团快照口径，不受窗口切换影响）· 席位同日同标的共现 ≥%d 次"
        "连边 · 节点大小∝共现度 · 边宽∝共现次数 · <span style='color:"
        "var(--red-500)'>红描边</span>=watchlist 名单外候选 · <span style='"
        "color:var(--grn-500)'>绿边</span>=同向同盟 / <span style='color:"
        "var(--ylw-500)'>橙边</span>=对手盘（净流向对冲）· 点图例按集团过滤"
        " · 悬停邻域聚焦 · 枢纽席位（共现度&gt;500）弱共现（&lt;8 次）"
        "默认折叠，单击枢纽弹清单释放 · 右键节点三态聚焦（一跳→二跳"
        "→清除）· ⏱ 回放按交易日推进（累计口径）· <span style='"
        "color:var(--gray-5)'>虚线=跨集团桥接</span> · 画布下方图谱分析"
        "（PageRank/介数/集团统计/Louvain 图社区，随回放联动）· "
        "<span style='color:var(--pur-500)'>双击节点=席位画像（影响力演化/"
        "同盟/对手）</span> · <span style='color:var(--red-500)'>⚡ 突变=最近 "
        "20 日新结盟/突增边检测</span> · <span style='color:var(--grn-500)'>⚖ 对比=双时点结构 diff</span></div>"
        "<div style='margin:4px 0 6px'>"
        "<button id='gnb-force' onclick='window._gl(\"force\")' "
        "style='background:var(--gray-3);color:var(--blu-500);border:1px solid "
        "var(--gray-4);border-radius:6px;font-size:12px;padding:3px 12px;"
        "cursor:pointer'>力导向</button> "
        "<button id='gnb-circle' onclick='window._gl(\"circle\")' "
        "style='background:var(--gray-3);color:var(--blu-500);border:1px solid "
        "var(--gray-4);border-radius:6px;font-size:12px;padding:3px 12px;"
        "cursor:pointer'>环形·按集团</button> "
        "<button id='gnb-grid' onclick='window._gl(\"grid\")' "
        "style='background:var(--gray-3);color:var(--blu-500);border:1px solid "
        "var(--gray-4);border-radius:6px;font-size:12px;padding:3px 12px;"
        "cursor:pointer'>网格·按度数</button> "
        "<button id='gnb-clr' onclick='window._gc()' "
        "style='background:var(--gray-3);color:var(--pur-500);border:1px solid "
        "var(--gray-4);border-radius:6px;font-size:12px;padding:3px 12px;"
        "cursor:pointer'>🎨 着色·注册集团</button> "
        "<button id='gnb-sg' onclick='window._gs()' "
        "style='background:var(--gray-3);color:var(--red-500);border:1px solid "
        "var(--gray-4);border-radius:6px;font-size:12px;padding:3px 12px;"
        "cursor:pointer' title='开关突变边高亮与信号表'>⚡ 突变</button> "
        "<button id='gnb-cmp' onclick='window._gx()' "
        "style='background:var(--gray-3);color:var(--grn-500);border:1px solid "
        "var(--gray-4);border-radius:6px;font-size:12px;padding:3px 12px;"
        "cursor:pointer' title='双时点结构对比（新增/增长边+影响力升降）'>⚖ 对比</button> "
        "<button onclick='window._ge(\"csv\")' style='background:"
        "var(--gray-3);color:var(--blu-500);border:1px solid var(--gray-4);border-radius:"
        "6px;font-size:12px;padding:3px 10px;cursor:pointer' "
        "title='当前时点全图快照 CSV（节点/边/分析/信号）'>⬇ CSV</button> "
        "<button onclick='window._ge(\"json\")' style='background:"
        "var(--gray-3);color:var(--blu-500);border:1px solid var(--gray-4);border-radius:"
        "6px;font-size:12px;padding:3px 10px;cursor:pointer' "
        "title='全图快照 JSON'>⬇ JSON</button> "
        "<button onclick='window._ge(\"png\")' style='background:"
        "var(--gray-3);color:var(--blu-500);border:1px solid var(--gray-4);border-radius:"
        "6px;font-size:12px;padding:3px 10px;cursor:pointer' "
        "title='画布 PNG（2 倍分辨率）'>⬇ PNG</button>"
        "<span id='gnfoc' style='display:none;margin-left:12px;"
        "font-size:12px;color:var(--ylw-500)'></span>"
        "<span id='gncomm' style='display:none;margin-left:12px;"
        "font-size:12px;color:var(--blu-500)'>🔒 已过滤集团：<b id='gncommt'"
        "></b> <a href='javascript:window._kgSetComm(\"\")' style='"
        "color:var(--gray-5);cursor:pointer;text-decoration:underline'>✕ 清除"
        "</a></span></div>"
        "<div style='margin:4px 0 6px;display:flex;align-items:"
        "center;gap:8px'>"
        "<span style='font-size:12px;color:var(--gray-5);white-space:"
        "nowrap'>⏱ 回放</span>"
        "<button id='gnplay' onclick='window._gp()' "
        "style='background:var(--gray-3);color:var(--blu-500);border:1px solid "
        "var(--gray-4);border-radius:6px;font-size:12px;padding:3px 12px;"
        "cursor:pointer;white-space:nowrap'>▶ 播放</button>"
        "<input type='range' id='gnt' min='0' max='365' value='365' "
        "oninput='window._gt(this.value)' style='flex:1;accent-color:"
        "var(--blu-600);cursor:pointer'>"
        "<span id='gntl' style='font-size:12px;color:var(--gray-5);"
        "white-space:nowrap'>全期</span></div>"
        "<div id='gnet' style='height:620px;border:1px solid var(--gray-4);"
        "border-radius:8px;background:var(--gray-0)'></div>"
        "<div id='gnana' style='margin-top:8px'></div>"
        "<div id='gnmodal' style='display:none;position:fixed;inset:0;"
        "background:rgba(0,0,0,.65);z-index:99;align-items:center;"
        "justify-content:center' onclick=\"if(event.target===this)"
        "this.style.display='none'\"><div id='gnbox' style='background:"
        "var(--gray-2);border:1px solid var(--gray-4);border-radius:10px;max-width:"
        "680px;width:92%%;max-height:72vh;overflow:auto;padding:14px "
        "16px'></div></div></div>\n"
        "<script src='/static/echarts.min.js'></script>\n<script>\n")
    tail = r'''(function(){
var HUB=500, AGG_W=8;
// KG-V4: URL ?comm= 集团过滤（总览层 master-detail 无刷新联动入口）
var COMM0='';
try{COMM0=(new URLSearchParams(location.search).get('comm'))||'';}catch(_e){}
function inComm(s){
  if(!COMM0)return true;
  return (((bySeat[s]||{}).comm)||'').indexOf(COMM0)===0;}
var wl={}; D.watchlist.forEach(function(s){wl[s]=1;});
var bySeat={}; D.nodes.forEach(function(n){bySeat[n.seat]=n;});
var cats=[], catIdx={};
D.nodes.forEach(function(n){var c=n.comm||'未分组';
  if(!(c in catIdx)){catIdx[c]=cats.length;cats.push({name:c});}});
var maxDeg=1, maxW=1;
D.nodes.forEach(function(n){if(n.deg>maxDeg)maxDeg=n.deg;});
D.edges.forEach(function(e){if(e.w>maxW)maxW=e.w;});
function isHub(s){return bySeat[s]&&bySeat[s].deg>HUB;}
var folded={};
D.edges.forEach(function(e){
  var ah=isHub(e.a), bh=isHub(e.b);
  if((ah||bh)&&e.w<AGG_W){
var hub=(ah&&bh)?(bySeat[e.a].deg>=bySeat[e.b].deg?e.a:e.b):(ah?e.a:e.b);
e._f=1;(folded[hub]=folded[hub]||[]).push({peer:hub===e.a?e.b:e.a,w:e.w});
  }});
function shortN(n){var t=n.reg||n.seat;return t.length>16?t.slice(0,15)+'…':t;}
function eColor(t){return (t.o*2>t.w)?'#d29922':'#3fb950';}
var FOC=null;
var dates=[],dIdx={},TI=0,FULL=0;
D.edges.forEach(function(e){(e.s||[]).forEach(function(x){
  if(!(x[0] in dIdx)){dIdx[x[0]]=1;dates.push(x[0]);}});});
dates.sort();dates.forEach(function(d,i){dIdx[d]=i;});
FULL=dates.length;TI=FULL;
function eAt(e){
  if(TI>=FULL)return {on:true,w:e.w,o:(e.opp||0)};
  var w=0,o=0,on=false;
  (e.s||[]).forEach(function(x){if(dIdx[x[0]]<=TI){on=true;w+=x[1];o+=x[2];}});
  return {on:on,w:w,o:o};}
function tickEdges(){D.edges.forEach(function(e){e._t=eAt(e);});}
function bfs(seat,hop){
  var adj={};
  D.edges.forEach(function(e){if(e._f||!e._t.on)return;
(adj[e.a]=adj[e.a]||[]).push(e.b);(adj[e.b]=adj[e.b]||[]).push(e.a);});
  var seen={},cur={};seen[seat]=1;cur[seat]=1;
  for(var h=0;h<hop;h++){var nxt={};
Object.keys(cur).forEach(function(s){(adj[s]||[]).forEach(function(t){
  if(!seen[t]){seen[t]=1;nxt[t]=1;}});});cur=nxt;}
  return seen;}
var CLR='comm';
function catOf(seat,a){
  if(CLR==='comm2'){
var ci=a.c2map[seat];
if(ci!==undefined)return ci;}
  return catIdx[(bySeat[seat]||{}).comm||'未分组'];}
function catsOf(a){
  return (CLR==='comm2')
?a.c2.map(function(c,i){return {name:'社区'+(i+1)};})
:cats;}
function build(AA){
  tickEdges();
  var fs=FOC?bfs(FOC.seat,FOC.hop):null;
  var vis={};
  D.edges.forEach(function(e){if(!e._f&&e._t.on&&inComm(e.a)&&inComm(e.b)){vis[e.a]=1;vis[e.b]=1;}});
  if(TI>=FULL)D.nodes.forEach(function(n){if(n.deg>HUB&&inComm(n.seat))vis[n.seat]=1;});
  if(fs)vis[FOC.seat]=1;
  var nodes=D.nodes.filter(function(n){
return inComm(n.seat)&&vis[n.seat]&&(!fs||fs[n.seat]);}).map(function(n){
var f=folded[n.seat]||[];
return {id:n.seat, name:shortN(n), category:catOf(n.seat,AA),
  symbolSize:Math.round(14+34*Math.sqrt(n.deg/maxDeg)), value:n.deg,
  foldN:f.length,
  label:{show:n.deg>HUB, fontSize:10, color:'#c9d1d9'},
  itemStyle:wl[n.seat]?{borderColor:'#f85149', borderWidth:2.5,
    shadowBlur:8, shadowColor:'rgba(248,81,73,.75)'}:{}};});
  var links=D.edges.filter(function(e){
return !e._f&&e._t.on&&inComm(e.a)&&inComm(e.b)&&(!fs||(fs[e.a]&&fs[e.b]));}).map(function(e){
var ca=(bySeat[e.a]||{}).comm||'', cb=(bySeat[e.b]||{}).comm||'';
var ls={width:0.5+3.2*Math.sqrt(e._t.w/maxW),
  opacity:0.22+0.6*e._t.w/maxW, color:eColor(e._t),
  type:(ca&&cb&&ca!==cb)?'dashed':'solid', curveness:0.12};
var sgv=SURGE?surge().map[e.a+'|'+e.b]:null;
if(sgv){ls.color='#f85149';ls.width*=1.7;ls.opacity=0.95;}
else if(CMP&&_cmpAdded[e.a+'|'+e.b]){
  ls.color='#3fb950';ls.width*=1.6;ls.opacity=0.95;}
return {source:e.a, target:e.b, w:e._t.w, o:e._t.o, lineStyle:ls};});
  return {nodes:nodes, links:links};
}
var LAY='force';
var chart=echarts.init(document.getElementById('gnet'));
function render(){
  tickEdges();
  var AA=analyze();
  if(CMP){_cmpT1=TI;
if(_cmpT0>=_cmpT1)_cmpT0=Math.max(0,_cmpT1-1);
cmpCalc(_cmpT0,_cmpT1);}
  var b=build(AA);
  var catsNow=catsOf(AA);
  window._nc=b.nodes.length;window._lc=b.links.length;
  if(LAY==='circle'){
var groups={},total=b.nodes.length,i=0;
b.nodes.forEach(function(n){
  (groups[n.category]=groups[n.category]||[]).push(n);});
var R=Math.max(220,total*2.5);
Object.keys(groups).forEach(function(c){
  var g=groups[c];g.sort(function(p,q){return q.value-p.value;});
  var a0=2*Math.PI*i/total,a1=2*Math.PI*(i+g.length)/total;
  g.forEach(function(n,k){var ang=a0+(a1-a0)*((k+0.5)/g.length);
    n.x=R*Math.cos(ang);n.y=R*Math.sin(ang);});
  i+=g.length;});
  }else if(LAY==='grid'){
var arr=b.nodes.slice().sort(function(p,q){return q.value-p.value;});
var cols=Math.max(6,Math.ceil(Math.sqrt(arr.length*1.7)));
var rows=Math.ceil(arr.length/cols);
arr.forEach(function(n,k){n.x=(k%cols)*76-((cols-1)*76)/2;
  n.y=Math.floor(k/cols)*64-((rows-1)*64)/2;});
  }
  chart.setOption({
backgroundColor:'#0d1117',
tooltip:{formatter:function(p){
  if(p.dataType==='edge')
    return p.data.source+'<br>× '+p.data.target+
      '<br>共现 <b>'+p.data.w+'</b> 次 · 同向 '+(p.data.w-p.data.o)+
      ' / 对手 '+p.data.o+((TI<FULL)
      ?'<br><span style="color:var(--gray-5)">截至 '+dates[TI]+'（累计）</span>'
      :'');
  var n=bySeat[p.data.id];
  var ci=(AA.c2map||{})[p.data.id];
  return '<b>'+p.data.id+'</b><br>集团：'+(n.comm||'未分组')+
    ((ci===undefined)?'':('<br>图社区：社区'+(ci+1)))+
    '<br>主体：'+(n.reg||'—')+'<br>上榜 '+n.n+' 次 · 共现度 '+n.deg+
    (p.data.foldN?'<br><span style="color:var(--ylw-500)">已折叠 '+
     p.data.foldN+' 条弱共现（单击释放）</span>':'');}},
legend:{data:catsNow.map(function(c){return c.name;}),
  textStyle:{color:'#8b949e', fontSize:11}, top:4},
series:[{type:'graph', layout:(LAY==='force'?'force':'none'), roam:true,
  categories:catsNow, data:b.nodes, links:b.links,
  label:{position:'right'},
  force:{repulsion:340, edgeLength:[40,130], gravity:.08},
  emphasis:{focus:'adjacency', lineStyle:{width:5}},
  lineStyle:{color:'#8b949e'}}]}, true);
  anaPanel();
}
// KG-V4: 供总览层 master-detail 无刷新联动（过滤集团 + 平滑滚动到画布）
window._kgSetComm=function(c){
  if(window._tabTo)window._tabTo('kgt-net');
  COMM0=c||'';FOC=null;render();
  var b=document.getElementById('gncomm');
  if(b){b.style.display=COMM0?'':'none';
var t=document.getElementById('gncommt');
if(t)t.textContent=COMM0;}
  var box=document.getElementById('gnet');
  if(box)box.scrollIntoView({behavior:'smooth',block:'start'});};
if(COMM0){if(window._tabTo)window._tabTo('kgt-net');
  var _gb=document.getElementById('gncomm');
  if(_gb){_gb.style.display='';
var _gt=document.getElementById('gncommt');
if(_gt)_gt.textContent=COMM0;}}
render();
// V7: 边突变信号——最近 W=20 交易日内：新结盟（首次共现且窗口≥5 次）
// / 突增（窗口日均 ≥ 历史日均 3 倍，历史≥10 日且≥2 次）。按 TI 缓存。
var SURGE=false,_sur=null,_surTI=-2;
function surge(){
  if(_sur&&_surTI===TI)return _sur;
  var ti=Math.min(TI,dates.length-1);
  var W=20,lo=Math.max(0,ti-W+1),list=[],map={};
  D.edges.forEach(function(e){
var winW=0,oldW=0,firstIdx=null;
e.s.forEach(function(seg){
  var di=dIdx[seg[0]];
  if(di===undefined||di>ti)return;
  if(firstIdx===null)firstIdx=di;
  if(di>=lo)winW+=seg[1];else oldW+=seg[1];});
if(firstIdx===null||winW<5)return;
var oldDays=Math.max(0,lo-firstIdx);
var type=null,ratio=0;
if(firstIdx>=lo){type='新结盟';ratio=winW;}
else if(oldDays>=10&&oldW>=2){
  ratio=(winW/W)/(oldW/oldDays);
  if(ratio>=3)type='突增';}
if(!type)return;
var rec={a:e.a,b:e.b,type:type,winW:winW,
  ratio:ratio,first:e.s[0][0]};
list.push(rec);map[e.a+'|'+e.b]=rec;});
  list.sort(function(p,q){return q.winW-p.winW;});
  _sur={list:list,map:map};_surTI=TI;return _sur;}
// V8: 双时点对比——轻量 prRankAt（边集+PR 排名+总量，局部 PageRank
// 30 迭代，不含 Brandes/Louvain，重算约 10ms 可随拖杆跟随）
var CMP=false,_cmp=null,_cmpAdded={},_cmpT0=0,_cmpT1=0;
function prRankAt(t){
  var sTI=TI;TI=t;tickEdges();
  var nd={},adj={},cross=0,E=0,edges={};
  D.edges.forEach(function(e){if(!e._t.on)return;
E++;edges[e.a+'|'+e.b]=e._t.w;
nd[e.a]=1;nd[e.b]=1;
(adj[e.a]=adj[e.a]||[]).push(e.b);
(adj[e.b]=adj[e.b]||[]).push(e.a);
var ca=(bySeat[e.a]||{}).comm||'',cb=(bySeat[e.b]||{}).comm||'';
if(ca&&cb&&ca!==cb)cross++;});
  var ks=Object.keys(nd),N=ks.length||1,d=.85,pr={};
  ks.forEach(function(k){pr[k]=1/N;});
  for(var i=0;i<30;i++){var np={};
ks.forEach(function(k){np[k]=(1-d)/N;});
ks.forEach(function(k){var nb=adj[k]||[];
  if(!nb.length)return;
  var sh=d*pr[k]/nb.length;
  nb.forEach(function(x){np[x]+=sh;});});
pr=np;}
  var rk=ks.slice().sort(function(p,q){return pr[q]-pr[p];});
  var rank={};rk.forEach(function(s,i){rank[s]=i+1;});
  TI=sTI;tickEdges();
  return {N:N,E:E,cross:cross,edges:edges,rank:rank};}
function cmpCalc(t0,t1){
  if(_cmp&&_cmp.t0===t0&&_cmp.t1===t1)return _cmp;
  var R0=prRankAt(t0),R1=prRankAt(t1);
  var added=[],gains=[],nRem=0;
  Object.keys(R1.edges).forEach(function(k){
if(!(k in R0.edges))added.push([k,R1.edges[k]]);
else{var df=R1.edges[k]-R0.edges[k];
  if(df>0)gains.push([k,df,R0.edges[k]]);}});
  Object.keys(R0.edges).forEach(function(k){
if(!(k in R1.edges))nRem++;});
  added.sort(function(p,q){return q[1]-p[1];});
  gains.sort(function(p,q){return q[1]-p[1];});
  var movers=[];
  Object.keys(R1.rank).forEach(function(s){
if(R0.rank[s])movers.push({s:s,d:R0.rank[s]-R1.rank[s],
  r0:R0.rank[s],r1:R1.rank[s]});});
  movers.sort(function(p,q){return q.d-p.d;});
  var up=movers.slice(0,5);
  var neg=movers.filter(function(m){return m.d<0;});
  var down=neg.slice(-5).reverse();
  _cmpAdded={};
  added.forEach(function(x){_cmpAdded[x[0]]=1;});
  _cmp={t0:t0,t1:t1,R0:R0,R1:R1,
added:added.slice(0,8),gains:gains.slice(0,8),
up:up,down:down,nAdd:added.length,nGain:gains.length,nRem:nRem};
  return _cmp;}
var _ana=null, _anaTI=-2;
function analyze(){
  if(_anaTI===TI)return _ana;
  var adj={},nodes={};
  D.edges.forEach(function(e){
if(!e._t.on)return;
nodes[e.a]=1;nodes[e.b]=1;
(adj[e.a]=adj[e.a]||[]).push(e.b);
(adj[e.b]=adj[e.b]||[]).push(e.a);});
  var keys=Object.keys(nodes),N=keys.length||1,i;
  var pr={},d=.85;
  keys.forEach(function(k){pr[k]=1/N;});
  for(i=0;i<30;i++){
var np={};keys.forEach(function(k){np[k]=(1-d)/N;});
keys.forEach(function(k){var nb=adj[k]||[];
  if(!nb.length)return;
  var sh=d*pr[k]/nb.length;
  nb.forEach(function(t){np[t]+=sh;});});
pr=np;}
  var btw={};keys.forEach(function(k){btw[k]=0;});
  keys.forEach(function(s){
var S=[],P={},sig={},dist={},Q=[s],qi=0;
sig[s]=1;dist[s]=0;P[s]=[];
while(qi<Q.length){var v=Q[qi++];S.push(v);
  var nb=adj[v]||[],j,w;
  for(j=0;j<nb.length;j++){w=nb[j];
    if(dist[w]===undefined){dist[w]=dist[v]+1;Q.push(w);}
    if(dist[w]===dist[v]+1){sig[w]=(sig[w]||0)+sig[s];
      (P[w]=P[w]||[]).push(v);}}}
var delta={};
while(S.length){var w2=S.pop(),pw=P[w2]||[],m,v2;
  for(m=0;m<pw.length;m++){v2=pw[m];
    delta[v2]=(delta[v2]||0)+
      (sig[v2]/sig[w2])*(1+(delta[w2]||0));}
  if(w2!==s)btw[w2]+=delta[w2]||0;}});
  var prList=keys.map(function(k){return [k,pr[k]];})
.sort(function(a,b){return b[1]-a[1];});
  var btwList=keys.map(function(k){return [k,btw[k]];})
.sort(function(a,b){return b[1]-a[1];});
  var cross=0,comm={};
  D.edges.forEach(function(e){
if(!e._t.on)return;
var ca=(bySeat[e.a]||{}).comm||'',cb=(bySeat[e.b]||{}).comm||'';
if(!ca||!cb)return;
if(ca===cb){var c=comm[ca]=comm[ca]||{m:{},inner:0,cross:0};
  c.inner++;c.m[e.a]=1;c.m[e.b]=1;}
else{cross++;
  var x=comm[ca]=comm[ca]||{m:{},inner:0,cross:0};
  var y=comm[cb]=comm[cb]||{m:{},inner:0,cross:0};
  x.cross++;y.cross++;x.m[e.a]=1;y.m[e.b]=1;}});
  keys.forEach(function(k){var c=(bySeat[k]||{}).comm||'';
if(c){var cc=comm[c]=comm[c]||{m:{},inner:0,cross:0};cc.m[k]=1;}});
  var edgeCnt=0;
  D.edges.forEach(function(e){if(e._t.on)edgeCnt++;});
  // V5: Louvain 加权社区发现（单层局部移动至收敛，48 节点规模够用）
  var adjW={},degW={},MW=0;
  D.edges.forEach(function(e){
if(!e._t.on)return;
var wa=(adjW[e.a]=adjW[e.a]||{});wa[e.b]=(wa[e.b]||0)+e._t.w;
var wb=(adjW[e.b]=adjW[e.b]||{});wb[e.a]=(wb[e.a]||0)+e._t.w;});
  keys.forEach(function(k){degW[k]=0;
var nb=adjW[k]||{};Object.keys(nb).forEach(function(t){degW[k]+=nb[t];});});
  keys.forEach(function(k){MW+=degW[k];});MW/=2;if(MW<=0)MW=1;
  var lab={},tot={},nxt=0;
  keys.forEach(function(k){lab[k]=nxt++;tot[lab[k]]=degW[k];});
  var moved=true,round=0;
  while(moved&&round<20){moved=false;round++;
keys.forEach(function(k){
  var cur=lab[k],nb=adjW[k]||[],nbk=Object.keys(nb);
  tot[cur]-=degW[k];
  var cin={};
  nbk.forEach(function(t){var c2=lab[t];
    cin[c2]=(cin[c2]||0)+nb[t];});
  var best=null,bg=1e-12;
  Object.keys(cin).forEach(function(c2i){
    if(+c2i===cur)return;
    var g=cin[c2i]/MW-tot[c2i]*degW[k]/(2*MW*MW);
    if(g>bg){bg=g;best=+c2i;}});
  if(best!==null){lab[k]=best;tot[best]+=degW[k];moved=true;}
  else tot[cur]+=degW[k];});}
  function qOf(lf){var q=0;
D.edges.forEach(function(e){if(!e._t.on)return;
  if(lf[e.a]===lf[e.b])q+=e._t.w-degW[e.a]*degW[e.b]/(2*MW);});
return q/MW;}
  var Qc=qOf(lab);
  var labR={};keys.forEach(function(k){
labR[k]=(bySeat[k]||{}).comm||('__solo_'+k);});
  var Qreg=qOf(labR);
  var cArr=[],cIdx={};
  keys.forEach(function(k){var c=lab[k];
if(!(c in cIdx)){cIdx[c]=cArr.length;
  cArr.push({m:0,inner:0,cross:0,regs:{},core:[],seats:[]});}
var o=cArr[cIdx[c]];o.m++;o.seats.push(k);
var rg=(bySeat[k]||{}).comm||'未分组';
o.regs[rg]=(o.regs[rg]||0)+1;});
  D.edges.forEach(function(e){if(!e._t.on)return;
var o=cArr[cIdx[lab[e.a]]];
if(lab[e.a]===lab[e.b])o.inner++;else o.cross++;});
  prList.forEach(function(x){var o=cArr[cIdx[lab[x[0]]]];
if(o.core.length<2)o.core.push(x[0]);});
  cArr.sort(function(p,q){return q.m-p.m;});
  var c2map={};
  cArr.forEach(function(o,i){o.seats.forEach(function(s){c2map[s]=i;});});
  cArr.forEach(function(o){
var mn='',mc=0;
Object.keys(o.regs).forEach(function(r){
  if(o.regs[r]>mc){mc=o.regs[r];mn=r;}});
o.main=mn.replace(' 集团','');o.purity=Math.round(mc/o.m*100)+'%';
o.core=o.core.map(function(s){return shortN(bySeat[s]||{});}).join('、');});
  _ana={N:N,E:edgeCnt,cross:cross,pr:prList,btw:btwList,
comm:Object.keys(comm).map(function(c){
  var o=comm[c],m=Object.keys(o.m).length;
  return {name:c,m:m,inner:o.inner,cross:o.cross,
    dens:(m>1)?o.inner/(m*(m-1)/2):0};})
  .sort(function(a,b){return b.inner-a.inner;}),
c2:cArr,c2map:c2map,Q:Qc,Qreg:Qreg};
  _anaTI=TI;return _ana;}
function anaPanel(){
  var el=document.getElementById('gnana');if(!el)return;
  var a=analyze();
  var cap=(TI>=FULL)?'全期':('截至 '+dates[TI]+'（累计）');
  function rows(list,fmt){
return list.slice(0,10).map(function(x,i){
  var n=bySeat[x[0]]||{};
  return "<tr><td style='padding:1px 6px;color:#6e7681'>"+(i+1)+
    "</td><td style='padding:1px 6px'><a href='javascript:window._afoc(\""+
    x[0]+"\")' style='color:var(--blu-500)' title='"+x[0]+"'>"+shortN(n)+
    "</a></td><td style='padding:1px 6px;text-align:right;color:var(--gray-6)'>"+
    fmt(x[1])+"</td><td style='padding:1px 6px;color:var(--gray-5);font-size:11px'>"+
    ((n.comm||'—').replace(' 集团',''))+"</td></tr>";}).join('');}
  var th="<tr style='color:var(--gray-5);font-size:11px'><td style='padding:2px 6px'>#</td>"+
"<td style='padding:2px 6px'>席位</td><td style='padding:2px 6px'>分值</td>"+
"<td style='padding:2px 6px'>集团</td></tr>";
  var cs=a.comm.map(function(c){
return "<tr><td style='padding:1px 6px'>"+c.name.replace(' 集团','')+
  "</td><td style='padding:1px 6px;text-align:right'>"+c.m+
  "</td><td style='padding:1px 6px;text-align:right'>"+c.inner+
  "</td><td style='padding:1px 6px;text-align:right'>"+c.cross+
  "</td><td style='padding:1px 6px;text-align:right'>"+
  (c.dens*100).toFixed(0)+"%</td></tr>";}).join('');
  el.innerHTML=
"<div style='border:1px solid var(--gray-4);border-radius:8px;background:var(--gray-0);padding:10px 12px'>"+
"<div style='font-weight:600;margin-bottom:2px'>📊 图谱分析"+
"<span style='font-weight:400;color:var(--gray-5);font-size:12px'> · "+cap+
" · 节点 "+a.N+" · 边 "+a.E+" · 跨集团边 "+a.cross+"</span></div>"+
"<div class='dim' style='font-size:11px;margin-bottom:6px'>口径：当时时间"+
"内全部共现边（含折叠弱边，视觉折叠不影响分析）· 点击席位=一跳聚焦定位</div>"+
(function(){
  if(!CMP)return '';
  var c=cmpCalc(_cmpT0,_cmpT1);
  function opts(sel){
    var sv=Math.min(sel,dates.length-1);
    return dates.map(function(d,i){
      return "<option value='"+i+"'"+
        ((i===sv)?' selected':'')+">"+d+"</option>";}).join('');}
  function pairRows(list,color){
    return list.map(function(x){
      var pp=x[0].split('|');
      return "<tr><td style='padding:1px 6px'><a href='javascript:window._afoc(\""+
        pp[0]+"\")' style='color:var(--blu-500)' title='"+pp[0]+" ↔ "+pp[1]+"'>"+
        shortN(bySeat[pp[0]]||{})+" ↔ "+shortN(bySeat[pp[1]]||{})+
        "</a></td><td style='padding:1px 6px;text-align:right;color:"+
        color+"'>"+x[1]+((x[2]!==undefined)
        ?("<span style='color:var(--gray-5);font-size:11px'> ("+x[2]+"→"+
          (x[2]+x[1])+")</span>"):'')+
        "</td></tr>";}).join('')||
      "<tr><td colspan='2' class='dim' style='padding:4px 6px'>无</td></tr>";}
  function mvRows(ms){
    return ms.map(function(m){
      return "<tr><td style='padding:1px 6px'><a href='javascript:window._afoc(\""+
        m.s+"\")' style='color:var(--blu-500)'>"+shortN(bySeat[m.s]||{})+
        "</a></td><td style='padding:1px 6px;text-align:right;color:var(--gray-5)'>"+
        m.r0+" → "+m.r1+
        "</td><td style='padding:1px 6px;text-align:right;color:"+
        (m.d>0?'#3fb950':'#f85149')+"'>"+(m.d>0?'+':'')+m.d+
        "</td></tr>";}).join('')||
      "<tr><td colspan='3' class='dim' style='padding:4px 6px'>无</td></tr>";}
  return "<div style='color:var(--grn-500);font-size:12px;margin:0 0 2px'>⚖ 双时点对比"+
    "（基准 <select id='gc-t0' onchange='window._cs(0,this.value)' "+
    "style='background:var(--gray-0);color:var(--grn-500);border:1px solid var(--gray-4);"+
    "border-radius:4px;font-size:11px'>"+opts(c.t0)+"</select> → 对比 "+
    "<select id='gc-t1' onchange='window._cs(1,this.value)' "+
    "style='background:var(--gray-0);color:var(--grn-500);border:1px solid var(--gray-4);"+
    "border-radius:4px;font-size:11px'>"+opts(c.t1)+"</select> · "+
    "对比日跟随回放滑杆 · 画布绿色=T1 新增边 · 点击定位）</div>"+
    "<div class='dim' style='font-size:11px;margin:0 0 4px'>基准 "+
    dates[c.t0]+"：节点 "+c.R0.N+" · 边 "+c.R0.E+" · 跨集团 "+c.R0.cross+
    " → 对比 "+dates[Math.min(c.t1,dates.length-1)]+"：节点 "+c.R1.N+
    " · 边 "+c.R1.E+" · 跨集团 "+
    c.R1.cross+"（新增边 "+c.nAdd+" · 增长边 "+c.nGain+"）</div>"+
    "<div style='display:flex;gap:16px;flex-wrap:wrap'>"+
    "<div style='flex:1;min-width:240px'><div style='color:var(--grn-500);font-size:12px;margin:2px 0'>新增边 Top8（T1 有·T0 无，按 T1 共现）</div>"+
    "<table style='width:100%;font-size:12px;border-collapse:collapse'>"+
    pairRows(c.added,'#3fb950')+"</table></div>"+
    "<div style='flex:1;min-width:240px'><div style='color:var(--ylw-500);font-size:12px;margin:2px 0'>增长最快 Top8（区间共现增量，累计口径边不消失）</div>"+
    "<table style='width:100%;font-size:12px;border-collapse:collapse'>"+
    pairRows(c.gains,'#d29922')+"</table></div></div>"+
    "<div style='display:flex;gap:16px;flex-wrap:wrap;margin-top:4px'>"+
    "<div style='flex:1;min-width:240px'><div style='color:var(--grn-500);font-size:12px;margin:2px 0'>影响力上升 Top5（PR 排名变化）</div>"+
    "<table style='width:100%;font-size:12px;border-collapse:collapse'>"+
    mvRows(c.up)+"</table></div>"+
    "<div style='flex:1;min-width:240px'><div style='color:var(--red-500);font-size:12px;margin:2px 0'>影响力下降 Top5</div>"+
    "<table style='width:100%;font-size:12px;border-collapse:collapse'>"+
    mvRows(c.down)+"</table></div></div>";})()+
(function(){
  if(!SURGE)return '';
  var sg=surge();
  var body=sg.list.length?
    "<table style='width:100%;font-size:12px;border-collapse:collapse'>"+
    "<tr style='color:var(--gray-5);font-size:11px'><td style='padding:2px 6px'>席位对</td>"+
    "<td style='padding:2px 6px'>类型</td>"+
    "<td style='padding:2px 6px;text-align:right'>窗口共现</td>"+
    "<td style='padding:2px 6px;text-align:right'>首现/倍数</td></tr>"+
    sg.list.slice(0,12).map(function(r){
      return "<tr><td style='padding:1px 6px'><a href='javascript:window._afoc(\""+
        r.a+"\")' style='color:var(--blu-500)' title='"+r.a+" ↔ "+r.b+"'>"+
        shortN(bySeat[r.a]||{})+" ↔ "+shortN(bySeat[r.b]||{})+
        "</a></td><td style='padding:1px 6px;color:"+
        (r.type==='新结盟'?'#f85149':'#d29922')+"'>"+r.type+
        "</td><td style='padding:1px 6px;text-align:right'>"+r.winW+
        "</td><td style='padding:1px 6px;text-align:right;color:var(--gray-5)'>"+
        (r.type==='新结盟'?r.first:(r.ratio.toFixed(1)+'x'))+
        "</td></tr>";}).join('')+"</table>"
    :"<div class='dim' style='font-size:12px;padding:2px 6px'>窗口内无突变信号</div>";
  return "<div style='color:var(--red-500);font-size:12px;margin:0 0 2px'>⚡ 突变信号"+
    "（最近 20 交易日 · "+sg.list.length+" 条 · 新结盟=窗口内首现≥5 次 · "+
    "突增=窗口日均≥历史 3 倍 · 点击定位）</div>"+body;})()+
"<div style='display:flex;gap:16px;flex-wrap:wrap'>"+
"<div style='flex:1;min-width:260px'><div style='color:var(--ylw-500);font-size:12px;margin:2px 0'>影响力 Top10（PageRank d=0.85）</div>"+
"<table style='width:100%;font-size:12px;border-collapse:collapse'>"+th+
rows(a.pr,function(v){return v.toFixed(4);})+"</table></div>"+
"<div style='flex:1;min-width:260px'><div style='color:var(--blu-500);font-size:12px;margin:2px 0'>桥接 Top10（介数中心性）</div>"+
"<table style='width:100%;font-size:12px;border-collapse:collapse'>"+th+
rows(a.btw,function(v){return v.toFixed(1);})+"</table></div></div>"+
"<div style='color:var(--grn-500);font-size:12px;margin:8px 0 2px'>集团统计"+
"（内聚密度=集团内边/成员完全图边数）</div>"+
"<table style='width:100%;font-size:12px;border-collapse:collapse'>"+
"<tr style='color:var(--gray-5);font-size:11px'><td style='padding:2px 6px'>集团</td>"+
"<td style='padding:2px 6px;text-align:right'>当时成员</td>"+
"<td style='padding:2px 6px;text-align:right'>集团内边</td>"+
"<td style='padding:2px 6px;text-align:right'>跨集团边</td>"+
"<td style='padding:2px 6px;text-align:right'>内聚密度</td></tr>"+
cs+"</table>"+
"<div style='color:var(--pur-500);font-size:12px;margin:8px 0 2px'>图社区发现"+
"（Louvain 加权模块度 Q="+a.Q.toFixed(3)+" · "+a.c2.length+" 社区 · "+
"对照：注册集团划分 Q="+a.Qreg.toFixed(3)+"）</div>"+
"<table style='width:100%;font-size:12px;border-collapse:collapse'>"+
"<tr style='color:var(--gray-5);font-size:11px'><td style='padding:2px 6px'>社区</td>"+
"<td style='padding:2px 6px;text-align:right'>成员</td>"+
"<td style='padding:2px 6px;text-align:right'>社区内边</td>"+
"<td style='padding:2px 6px;text-align:right'>跨社区边</td>"+
"<td style='padding:2px 6px'>主注册集团（纯度）</td>"+
"<td style='padding:2px 6px'>核心席位（PageRank Top2）</td></tr>"+
a.c2.map(function(c,i){
  return "<tr><td style='padding:1px 6px;color:var(--pur-500)'>社区"+(i+1)+
    "</td><td style='padding:1px 6px;text-align:right'>"+c.m+
    "</td><td style='padding:1px 6px;text-align:right'>"+c.inner+
    "</td><td style='padding:1px 6px;text-align:right'>"+c.cross+
    "</td><td style='padding:1px 6px'>"+c.main+
    " <span style='color:var(--gray-5);font-size:11px'>"+c.purity+"</span></td>"+
    "<td style='padding:1px 6px;font-size:11px;color:var(--gray-6)'>"+c.core+
    "</td></tr>";}).join('')+"</table>"+
"<div class='dim' style='font-size:11px;margin-top:4px'>图社区=按共现"+
"结构自动聚类（与注册集团独立）· 工具条『着色』切换画布按社区着色 · "+
"Q 差=注册划分偏离真实结构的程度</div></div>";}
window._afoc=function(s){FOC={seat:s,hop:1};render();focBar();
  var md=document.getElementById('gnmodal');
  if(md)md.style.display='none';};
function syncBtns(){['force','circle','grid'].forEach(function(k){
  var b=document.getElementById('gnb-'+k);if(!b)return;
  var on=(k===LAY);b.style.background=on?'#1f6feb':'#21262d';
  b.style.color=on?'#ffffff':'#58a6ff';});}
function focBar(){
  var el=document.getElementById('gnfoc');if(!el)return;
  if(!FOC){el.style.display='none';el.innerHTML='';return;}
  el.style.display='inline';
  el.innerHTML='🔎 '+FOC.seat+'（'+FOC.hop+' 跳）· 再右键切换 / 第三次清除 '+
"<a href='javascript:window._gfoc()' style='color:var(--blu-500)'>手动清除</a> · "+
"<a href='javascript:window._prof(\""+FOC.seat+"\")' style='color:var(--pur-500)'>画像</a>";}
syncBtns();focBar();
var gnt=document.getElementById('gnt');
if(gnt){gnt.max=FULL||1;gnt.value=TI;}
var PT=null;
function tLabel(){
  var el=document.getElementById('gntl');if(!el)return;
  el.innerHTML=((TI>=FULL)?('全期 · '+FULL+' 交易日')
:(dates[TI]+' 累计'))+' · 边 '+(window._lc||0)+'/'+D.edges.length;}
tLabel();
window._gp=function(){
  var b=document.getElementById('gnplay');
  if(PT){clearInterval(PT);PT=null;if(b)b.innerHTML='▶ 播放';return;}
  if(TI>=FULL)TI=-1;
  if(b)b.innerHTML='⏸ 暂停';
  PT=setInterval(function(){
TI++;var s=document.getElementById('gnt');if(s)s.value=TI;
if(TI>=FULL)TI=FULL;
render();tLabel();
if(TI>=FULL)window._gp();},80);};
window._gt=function(v){
  TI=+v;var pb=document.getElementById('gnplay');
  if(PT){clearInterval(PT);PT=null;if(pb)pb.innerHTML='▶ 播放';}
  render();tLabel();};
window._gl=function(l){LAY=l;render();syncBtns();};
window._gc=function(){
  CLR=(CLR==='comm')?'comm2':'comm';
  var b=document.getElementById('gnb-clr');
  if(b)b.innerHTML=(CLR==='comm')?'🎨 着色·注册集团':'🎨 着色·图社区';
  render();};
window._gs=function(){
  SURGE=!SURGE;
  var b=document.getElementById('gnb-sg');
  if(b){b.style.borderColor=SURGE?'#f85149':'#30363d';
b.style.background=SURGE?'#3d1d20':'#21262d';}
  render();};
window._gx=function(){
  CMP=!CMP;
  var b=document.getElementById('gnb-cmp');
  if(b){b.style.borderColor=CMP?'#3fb950':'#30363d';
b.style.background=CMP?'#12261a':'#21262d';}
  if(CMP){_cmpT1=TI;
_cmpT0=Math.max(0,TI-60);
if(_cmpT0>=_cmpT1)_cmpT0=Math.max(0,_cmpT1-1);}
  render();};
window._cs=function(w,v){
  var i=parseInt(v,10)||0;
  if(w===0){_cmpT0=i;
if(_cmpT0>=_cmpT1)_cmpT0=Math.max(0,_cmpT1-1);
render();}
  else{var s=document.querySelector("input[type='range']");
if(s){s.value=i;
  s.dispatchEvent(new Event('input',{bubbles:true}));}}};
window._gfoc=function(){FOC=null;render();focBar();};
chart.getZr().on('contextmenu', function(e){e.event.preventDefault();});
chart.on('contextmenu', function(p){
  if(!p||p.dataType!=='node')FOC=null;
  else if(FOC&&FOC.seat===p.data.id)
FOC=(FOC.hop===1)?{seat:p.data.id,hop:2}:null;
  else FOC={seat:p.data.id,hop:1};
  render();focBar();
});
chart.on('dblclick', function(p){
  if(p&&p.dataType==='node')window._prof(p.data.id);
});
window._gnl=null;
chart.on('click', function(p){
  if(p.dataType!=='node'||!p.data.foldN)return;
  var f=folded[p.data.id].slice().sort(function(a,b){return b.w-a.w;});
  window._gnl={hub:p.data.id, list:f};
  var rows=f.slice(0,40).map(function(x,i){
var n=bySeat[x.peer];
return "<tr><td style='padding:2px 6px' title='"+x.peer+"'>"+
  ((n&&(n.reg||x.peer))||x.peer).slice(0,26)+"</td><td style='padding:"+
  "2px 6px'>"+x.w+"</td><td style='padding:2px 6px'><button onclick='"+
  "window._rl("+i+")' style='cursor:pointer;background:var(--gray-3);color:"+
  "var(--blu-500);border:1px solid var(--gray-4);border-radius:5px;font-size:11px;"+
  "padding:1px 8px'>上图</button></td></tr>";}).join('');
  document.getElementById('gnbox').innerHTML=
"<div style='font-weight:600;margin-bottom:6px'>枢纽席位折叠清单"+
"（弱共现，按强度排）</div><div class='dim' style='margin-bottom:6px'>"+
p.data.id+" · 共 "+f.length+" 条 · 点击「上图」释放该对端</div>"+
"<table style='width:100%;font-size:12px;border-collapse:collapse'>"+
rows+"</table>"+(f.length>40?"<div class='dim' style='margin-top:6px'>"+
"仅列前 40 条</div>":"");
  document.getElementById('gnmodal').style.display='flex';
});
window._rl=function(i){
  var rec=window._gnl.list[i], hub=window._gnl.hub;
  D.edges.forEach(function(e){
if((e.a===hub&&e.b===rec.peer)||(e.b===hub&&e.a===rec.peer))e._f=0;});
  folded[hub]=folded[hub].filter(function(x){return x.peer!==rec.peer;});
  render();
  if(!folded[hub].length)
document.getElementById('gnmodal').style.display='none';
};
// V6: 席位画像——时变序列（PR 排名+连接数，抽样 30 时点，一次性缓存）
var _pSer={};
function prSeries(seat){
  if(_pSer[seat])return _pSer[seat];
  var pts=[],step=Math.max(1,Math.floor(FULL/30)),saveTI=TI;
  for(var t=0;t<FULL;t+=step){
TI=t;tickEdges();
var nd={},adj={};
D.edges.forEach(function(e){if(!e._t.on)return;
  nd[e.a]=1;nd[e.b]=1;
  (adj[e.a]=adj[e.a]||[]).push(e.b);
  (adj[e.b]=adj[e.b]||[]).push(e.a);});
var ks=Object.keys(nd),N=ks.length||1,d=.85,pr={};
ks.forEach(function(k){pr[k]=1/N;});
for(var i=0;i<30;i++){var np={};
  ks.forEach(function(k){np[k]=(1-d)/N;});
  ks.forEach(function(k){var nb=adj[k]||[];
    if(!nb.length)return;
    var sh=d*pr[k]/nb.length;
    nb.forEach(function(x){np[x]+=sh;});});
  pr=np;}
if(!nd[seat]){pts.push([dates[t],null,0]);continue;}
var rank=1;
ks.forEach(function(k){if(pr[k]>pr[seat])rank++;});
pts.push([dates[t],rank,(adj[seat]||[]).length]);}
  TI=saveTI;tickEdges();
  _pSer[seat]=pts;return pts;
}
// V6: 席位画像弹窗
window._pCh=null;
window._prof=function(seat){
  var n=bySeat[seat];if(!n)return;
  var a=analyze();
  var prR=null,btR=null;
  a.pr.forEach(function(x,i){if(x[0]===seat)prR=i+1;});
  a.btw.forEach(function(x,i){if(x[0]===seat)btR=i+1;});
  var ci=a.c2map[seat];
  var al=[],op=[];
  D.edges.forEach(function(e){
var t=e._t;if(!t.on)return;
var peer=null;
if(e.a===seat)peer=e.b;else if(e.b===seat)peer=e.a;
if(!peer)return;
al.push([peer,t.w,t.o]);
if(t.o>0)op.push([peer,t.o,t.w]);});
  al.sort(function(p,q){return q[1]-p[1];});
  op.sort(function(p,q){return q[1]-p[1];});
  al=al.slice(0,5);op=op.slice(0,5);
  function prRows(list){
return list.map(function(x){
  var pn=bySeat[x[0]]||{};
  return "<tr><td style='padding:1px 6px'><a href='javascript:window._afoc(\""+
    x[0]+"\")' style='color:var(--blu-500)' title='"+x[0]+"'>"+shortN(pn)+
    "</a> <a href='javascript:window._prof(\""+x[0]+
    "\")' style='color:var(--pur-500);font-size:10px'>📈</a></td><td style='padding:1px 6px;text-align:right'>"+
    x[1]+"</td><td style='padding:1px 6px;text-align:right;color:var(--gray-5)'>"+
    x[2]+"</td><td style='padding:1px 6px;color:var(--gray-5);font-size:11px'>"+
    ((pn.comm||'—').replace(' 集团',''))+"</td></tr>";}).join('')||
  "<tr><td colspan='4' class='dim' style='padding:4px 6px'>当时无</td></tr>";}
  var cap=(TI>=FULL)?'全期':('截至 '+dates[TI]+'（累计）');
  var pts=prSeries(seat);
  window._pData={seat:seat,cap:cap,
meta:{comm:n.comm,reg:n.reg,n:n.n,deg:n.deg,wr:n.wr,wrn:n.wrn,prR:prR,btR:btR,ci:ci,N:a.N,E:a.E},
pts:pts,allies:al,opps:op};
  var box=document.getElementById('gnbox');
  box.innerHTML=
"<div style='font-weight:600;margin-bottom:4px'>🧭 席位画像"+
"<span style='font-weight:400;color:var(--gray-5);font-size:12px'> · "+cap+
"</span><span style='float:right;white-space:nowrap'>"+
"<button onclick='window._profCsv()' style='background:var(--gray-3);color:var(--grn-500);"+
"border:1px solid var(--gray-4);border-radius:5px;font-size:11px;padding:1px 8px;"+
"cursor:pointer' title='导出画像数据 CSV'>CSV</button> "+
"<button onclick='window._profJson()' style='background:var(--gray-3);color:var(--blu-500);"+
"border:1px solid var(--gray-4);border-radius:5px;font-size:11px;padding:1px 8px;"+
"cursor:pointer' title='导出画像快照 JSON'>JSON</button> "+
"<button onclick='window._profPng()' style='background:var(--gray-3);color:var(--ylw-500);"+
"border:1px solid var(--gray-4);border-radius:5px;font-size:11px;padding:1px 8px;"+
"cursor:pointer' title='导出影响力演化图 PNG'>PNG</button>"+
"<span onclick=\"document.getElementById('gnmodal').style.display='none'\""+
" style='cursor:pointer;color:var(--gray-5);margin-left:8px'>✕</span></span></div>"+
"<div style='font-size:13px;color:var(--gray-6);margin-bottom:8px'>"+seat+"</div>"+
"<div style='display:flex;flex-wrap:wrap;gap:4px 18px;font-size:12px;margin-bottom:8px'>"+
"<span class='dim'>集团</span><span>"+(n.comm||'未分组')+
"</span><span class='dim'>图社区</span><span style='color:var(--pur-500)'>"+
((ci===undefined)?'—':('社区'+(ci+1)))+
"</span><span class='dim'>主体</span><span>"+(n.reg||'—')+
"</span><span class='dim'>上榜</span><span>"+n.n+" 次"+
"</span><span class='dim'>共现度</span><span>"+n.deg+
"</span><span class='dim'>次日胜率</span><span style='color:"+((n.wr===undefined)?'#8b949e':((n.wr>=0.5)?'#f85149':'#3fb950'))+"'>"+((n.wr===undefined)?'—':((Math.round(n.wr*1000)/10)+'%'+(n.wrn?(' · '+n.wrn+'笔'):'')))+
"</span><span class='dim'>影响力</span><span style='color:var(--ylw-500)'>No."+
((prR===null)?'—':(prR+"/"+a.N))+
"</span><span class='dim'>桥接</span><span style='color:var(--blu-500)'>No."+
((btR===null)?'—':(btR+"/"+a.N))+"</span></div>"+
"<div class='dim' style='font-size:11px;margin-bottom:2px'>影响力演化"+
"（橙=PageRank 排名，倒轴·上=强 · 蓝=当时连接席位数 · 竖线=当前回放位置）</div>"+
"<div id='gprof' style='height:190px;border:1px solid var(--gray-4);border-radius:6px;"+
"margin-bottom:8px'></div>"+
"<div style='display:flex;gap:16px;flex-wrap:wrap'>"+
"<div style='flex:1;min-width:240px'><div style='color:var(--grn-500);font-size:12px;margin:2px 0'>同盟 Top5（累计共现）</div>"+
"<table style='width:100%;font-size:12px;border-collapse:collapse'>"+
"<tr style='color:var(--gray-5);font-size:11px'><td style='padding:2px 6px'>席位</td>"+
"<td style='padding:2px 6px;text-align:right'>共现</td>"+
"<td style='padding:2px 6px;text-align:right'>对手</td>"+
"<td style='padding:2px 6px'>集团</td></tr>"+prRows(al)+"</table></div>"+
"<div style='flex:1;min-width:240px'><div style='color:var(--ylw-500);font-size:12px;margin:2px 0'>对手 Top5（对手次数）</div>"+
"<table style='width:100%;font-size:12px;border-collapse:collapse'>"+
"<tr style='color:var(--gray-5);font-size:11px'><td style='padding:2px 6px'>席位</td>"+
"<td style='padding:2px 6px;text-align:right'>对手</td>"+
"<td style='padding:2px 6px;text-align:right'>共现</td>"+
"<td style='padding:2px 6px'>集团</td></tr>"+prRows(op)+"</table></div></div>"+
"<div class='dim' style='font-size:11px;margin-top:6px'>口径：当时累计 · "+
"名=点击聚焦定位 · 📈=递归画像 · 双击画布节点=打开画像 · "+
"标题行 CSV/JSON=数据导出 · PNG=演化图</div>";
  document.getElementById('gnmodal').style.display='flex';
  if(window._pCh){window._pCh.dispose();window._pCh=null;}
  var pc=echarts.init(document.getElementById('gprof'));
  window._pCh=pc;
  pc.setOption({
backgroundColor:'#0d1117',
tooltip:{trigger:'axis',textStyle:{fontSize:11}},
grid:{left:40,right:40,top:24,bottom:24},
xAxis:{type:'category',data:pts.map(function(p){return p[0];}),
  axisLabel:{color:'#8b949e',fontSize:9,interval:Math.floor(pts.length/5)}},
yAxis:[
  {type:'value',inverse:true,min:1,name:'PR排名',
    nameTextStyle:{color:'#d29922',fontSize:9},
    axisLabel:{color:'#8b949e',fontSize:9},
    splitLine:{lineStyle:{color:'#21262d'}}},
  {type:'value',name:'连接数',
    nameTextStyle:{color:'#58a6ff',fontSize:9},
    axisLabel:{color:'#8b949e',fontSize:9},
    splitLine:{show:false}}],
series:[
  {type:'line',name:'PR排名',connectNulls:true,
    data:pts.map(function(p){return p[1];}),
    symbol:'none',lineStyle:{color:'#d29922',width:1.5},
    markLine:{symbol:'none',silent:true,label:{show:false},
      lineStyle:{color:'#f85149',type:'dashed',width:1},
      data:[{xAxis:dates[Math.min(TI,FULL-1)]}]}},
  {type:'line',name:'连接数',yAxisIndex:1,
    data:pts.map(function(p){return p[2];}),
    symbol:'none',lineStyle:{color:'#58a6ff',width:1.5}}]});
};
// V6e: 画像导出（CSV/JSON/PNG，纯前端 Blob 下载）
window._dl=function(name,mime,content){
  var blob=new Blob([content],{type:mime});
  var url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=name;document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);URL.revokeObjectURL(url);},100);};
function _fname(ext){
  var d=window._pData||{};
  var nm=((bySeat[d.seat]&&shortN(bySeat[d.seat]))||'席位')
.replace(/[\\/:*?"<>|…]/g,'');
  var dt=(TI>=FULL)?'全期':dates[TI];
  return '席位画像_'+nm+'_'+dt+'.'+ext;}
window._profCsv=function(){
  var d=window._pData;if(!d)return;
  function esc(s){s=String(s);
return (s.indexOf(',')>=0||s.indexOf('"')>=0)
  ?'"'+s.replace(/"/g,'""')+'"':s;}
  function cg(s){return ((bySeat[s]||{}).comm||'—').replace(' 集团','');}
  var L=[];
  L.push('席位画像,'+esc(d.seat));
  L.push('口径,'+esc(d.cap));
  L.push('集团,'+esc(d.meta.comm||'未分组'));
  L.push('图社区,'+((d.meta.ci===undefined)?'—':('社区'+(d.meta.ci+1))));
  L.push('注册主体,'+esc(d.meta.reg||'—'));
  L.push('上榜次数,'+d.meta.n);
  L.push('共现度,'+d.meta.deg);
  L.push('影响力排名,'+((d.meta.prR===null)?'—':(d.meta.prR+'/'+d.meta.N)));
  L.push('桥接排名,'+((d.meta.btR===null)?'—':(d.meta.btR+'/'+d.meta.N)));
  L.push('');
  L.push('影响力演化（日期,PageRank排名,连接席位数）');
  d.pts.forEach(function(p){
L.push(p[0]+','+((p[1]===null)?'':p[1])+','+p[2]);});
  L.push('');
  L.push('同盟Top（席位,累计共现,对手次数,集团）');
  d.allies.forEach(function(x){
L.push(esc(x[0])+','+x[1]+','+x[2]+','+esc(cg(x[0])));});
  L.push('');
  L.push('对手Top（席位,对手次数,累计共现,集团）');
  d.opps.forEach(function(x){
L.push(esc(x[0])+','+x[1]+','+x[2]+','+esc(cg(x[0])));});
  window._dl(_fname('csv'),'text/csv;charset=utf-8','\ufeff'+L.join('\r\n'));};
window._profJson=function(){
  var d=window._pData;if(!d)return;
  var out={席位:d.seat,口径:d.cap,
集团:d.meta.comm||'未分组',
图社区:(d.meta.ci===undefined?null:d.meta.ci+1),
注册主体:d.meta.reg||null,上榜次数:d.meta.n,共现度:d.meta.deg,
影响力排名:d.meta.prR,桥接排名:d.meta.btR,
当时节点数:d.meta.N,当时边数:d.meta.E,
影响力演化:d.pts.map(function(p){
  return {日期:p[0],PR排名:p[1],连接数:p[2]};}),
同盟Top:d.allies.map(function(x){
  return {席位:x[0],共现:x[1],对手:x[2],
    集团:((bySeat[x[0]]||{}).comm||null)};}),
对手Top:d.opps.map(function(x){
  return {席位:x[0],对手:x[1],共现:x[2],
    集团:((bySeat[x[0]]||{}).comm||null)};})};
  window._dl(_fname('json'),'application/json',
JSON.stringify(out,null,2));};
window._profPng=function(){
  if(!window._pCh)return;
  var url=window._pCh.getDataURL({pixelRatio:2,backgroundColor:'#0d1117'});
  var a=document.createElement('a');
  a.href=url;a.download=_fname('png');
  document.body.appendChild(a);a.click();
  setTimeout(function(){document.body.removeChild(a);},100);};
// V9: 图谱整卡导出（CSV/JSON/PNG，当前时点全图快照）
window._ge=function(kind){
  var a=analyze();
  var full=(TI>=FULL),tag=full?'全期':dates[TI];
  function fname(ext){return '图谱快照_'+tag+'.'+ext;}
  function esc(s){s=String(s);
return (s.indexOf(',')>=0||s.indexOf('"')>=0)
  ?'"'+s.replace(/"/g,'""')+'"':s;}
  var brk={};a.btw.forEach(function(x,i){brk[x[0]]=i+1;});
  var deg={};
  D.edges.forEach(function(e){if(!e._t.on)return;
deg[e.a]=(deg[e.a]||0)+1;deg[e.b]=(deg[e.b]||0)+1;});
  var sg=surge();
  var q=(a.Q!==undefined)?Math.round(a.Q*1000)/1000:null;
  var qr=(a.Qreg!==undefined)?Math.round(a.Qreg*1000)/1000:null;
  if(kind==='csv'){
var L=[];
L.push('# 龙虎榜席位图谱快照');
L.push('# 时点,'+tag+',节点,'+a.N+',边,'+a.E+',跨集团边,'+
  a.cross+((q!==null)?(',模块度Q,'+q+',注册集团Q,'+qr):''));
L.push('');
L.push('[节点]（影响力降序）席位,集团,图社区,共现度,PageRank,桥接排名');
a.pr.forEach(function(x){var s=x[0];
  L.push([esc(s),
    esc(((bySeat[s]||{}).comm||'未分组').replace(' 集团','')),
    '社区'+((a.c2map[s]!==undefined)?(a.c2map[s]+1):'-'),
    deg[s]||0,x[1].toFixed(4),brk[s]||''].join(','));});
L.push('');
L.push('[边] 席位A,席位B,共现,对手,跨集团,突变');
D.edges.forEach(function(e){if(!e._t.on)return;
  var ca=(bySeat[e.a]||{}).comm||'',cb=(bySeat[e.b]||{}).comm||'';
  L.push([esc(e.a),esc(e.b),e._t.w,e._t.o,
    (ca&&cb&&ca!==cb)?'是':'否',
    (sg.map[e.a+'|'+e.b]||{}).type||''].join(','));});
L.push('');
L.push('[影响力Top10] 排名,席位,PageRank');
a.pr.slice(0,10).forEach(function(x,i){
  L.push((i+1)+','+esc(x[0])+','+x[1].toFixed(4));});
L.push('');
L.push('[桥接Top10] 排名,席位,介数');
a.btw.slice(0,10).forEach(function(x,i){
  L.push((i+1)+','+esc(x[0])+','+(Math.round(x[1]*10)/10));});
L.push('');
L.push('[集团统计] 集团,成员,内边,跨边,内聚密度');
a.comm.forEach(function(o){
  L.push([esc(o.name.replace(' 集团','')),o.m,o.inner,o.cross,
    (Math.round(o.dens*1000)/10)+'%'].join(','));});
L.push('');
L.push('[图社区统计] 社区,成员,内边,跨边,主注册集团,纯度,核心席位');
a.c2.forEach(function(o,i){
  L.push(['社区'+(i+1),o.m,o.inner,o.cross,esc(o.main||''),
    o.purity||'',esc(o.core||'')].join(','));});
L.push('');
L.push('[突变信号]（最近20交易日）席位A,席位B,类型,窗口共现,首现,倍数');
sg.list.forEach(function(r){
  L.push([esc(r.a),esc(r.b),r.type,r.winW,r.first,
    (r.type==='突增')?(r.ratio.toFixed(1)+'x'):''].join(','));});
window._dl(fname('csv'),'text/csv;charset=utf-8',
  '\ufeff'+L.join('\r\n'));
return;}
  if(kind==='json'){
var out={时点:tag,节点数:a.N,边数:a.E,跨集团边:a.cross};
if(q!==null){out.模块度Q=q;out.注册集团Q=qr;}
out.节点=a.pr.map(function(x){var s=x[0];
  return {席位:s,集团:((bySeat[s]||{}).comm||null),
    图社区:(a.c2map[s]!==undefined)?(a.c2map[s]+1):null,
    共现度:deg[s]||0,PageRank:Math.round(x[1]*10000)/10000,
    桥接排名:brk[s]||null};});
out.边=D.edges.filter(function(e){return e._t.on;})
  .map(function(e){
    var ca=(bySeat[e.a]||{}).comm||'',
        cb=(bySeat[e.b]||{}).comm||'';
    return {A:e.a,B:e.b,共现:e._t.w,对手:e._t.o,
      跨集团:(ca&&cb&&ca!==cb),
      突变:(sg.map[e.a+'|'+e.b]||{}).type||null};});
out.集团统计=a.comm.map(function(o){
  return {集团:o.name,成员:o.m,内边:o.inner,跨边:o.cross,
    内聚密度:Math.round(o.dens*1000)/10};});
out.图社区统计=a.c2.map(function(o,i){
  return {社区:i+1,成员:o.m,内边:o.inner,跨边:o.cross,
    主注册集团:o.main||null,纯度:o.purity||null,核心:o.core||null};});
out.突变信号=sg.list.map(function(r){
  return {A:r.a,B:r.b,类型:r.type,窗口共现:r.winW,首现:r.first,
    倍数:(r.type==='突增')?Math.round(r.ratio*10)/10:null};});
window._dl(fname('json'),'application/json',
  JSON.stringify(out,null,1));
return;}
  if(kind==='png'){
var url=chart.getDataURL(
  {pixelRatio:2,backgroundColor:'#0d1117'});
var el=document.createElement('a');
el.href=url;el.download=fname('png');
document.body.appendChild(el);el.click();
setTimeout(function(){document.body.removeChild(el);},100);}};
window.addEventListener('resize', function(){chart.resize();});
})();
</script>'''
    return (head % (mg.get("days") or 0, mg.get("min_w") or 0)
            + "var D=" + payload + ";\n" + tail)


def _kg_overview_html(ov):
    """KG-V4 集团总览层（G6 v5，本地托管 /static/g6.min.js）：
    总览-下钻第一层——首屏只看集团超节点（~4-30 个），节点∝席位
    规模，边∝跨集团共现（橙=对手盘为主）。master-detail：单击集团
    →右侧详情面板（anchor/top 成员/watchlist 数）；双击→图内展开
    top 成员环形；搜索席位名→定位集团；面板"席位网络"按钮→
    window._kgSetComm 联动下方 ECharts 席位画布按集团过滤（无刷新）。
    选中状态进 URL hash（#g=groupId），刷新/分享可复原。"""
    import json as _json
    import html as _h
    e = _h.escape
    payload = _json.dumps(ov, ensure_ascii=False,
                          separators=(",", ":"))
    max([l["w"] for l in ov.get("links") or []] or [1])
    head = (
        "<div class='sec' id='kgovsec'><h2>🧭 集团总览（先看结构，再"
        "下钻）</h2>"
        "<div class='dim' style='margin:2px 0 6px'>窗口 %d 天 · %d 集团"
        " · 跨集团桥接 %d 条 · 节点大小∝席位规模 · 边宽∝跨集团共现"
        " · <span style='color:var(--ylw-500)'>橙边</span>=对手盘为主（净流"
        "向对冲）· <span style='color:var(--red-500)'>红描边</span>=集团内含"
        " watchlist 名单外候选 · <b>单击</b>集团=右侧详情 · <b>双击</b>"
        "=展开 top 成员 · 搜索席位可定位所属集团 · 快照 %s</div>"
        "<div style='display:flex;gap:10px;margin:4px 0 6px;"
        "align-items:center;flex-wrap:wrap'>"
        "<input id='kgoq' placeholder='搜索席位/注册主体/集团…' "
        "oninput='window._kgQS()' style='flex:1;min-width:180px;"
        "background:var(--gray-0);color:var(--gray-7);border:1px solid var(--gray-4);"
        "border-radius:6px;font-size:12px;padding:5px 10px' "
        "autocomplete='off'>"
        "<button onclick='window._kgCSV()' style='background:var(--gray-3);"
        "color:var(--blu-500);border:1px solid var(--gray-4);border-radius:6px;"
        "font-size:12px;padding:4px 12px;cursor:pointer'>⬇ CSV</button>"
        "<span id='kgors' style='font-size:12px;color:var(--gray-5)'></span>"
        "</div>"
        "<div id='kgores' style='display:none;margin:0 0 6px'></div>"
        "<div style='display:flex;gap:10px;align-items:stretch'>"
        "<div id='kgov' style='flex:1.7;min-width:0;height:560px;"
        "border:1px solid var(--gray-4);border-radius:8px;background:var(--gray-0)'"
        "></div>"
        "<div id='kgod' style='flex:1;min-width:260px;max-width:360px;"
        "border:1px solid var(--gray-4);border-radius:8px;background:var(--gray-0);"
        "padding:10px 12px;overflow:auto'></div></div>"
        "<div id='kgotip' style='display:none;position:fixed;z-index:"
        "98;background:var(--gray-2);border:1px solid var(--gray-4);border-radius:"
        "8px;padding:8px 10px;font-size:12px;color:var(--gray-7);pointer-"
        "events:none;max-width:340px;box-shadow:0 4px 14px rgba(0,0,"
        "0,.5)'></div></div>\n"
        "<script src='/static/g6.min.js'></script>\n<script>\n")
    tail = r'''(function(){
var D=__P__;
var byG={}, PAL=['#58a6ff','#d29922','#a371f7','#f778ba','#79c0ff','#ffa657'];
var maxSeats=1, maxW=1;
D.groups.forEach(function(g,i){g._i=i;byG[g.id]=g;g._c=PAL[i%PAL.length];
  if(g.n_seats>maxSeats)maxSeats=g.n_seats;});
D.links.forEach(function(l){if(l.w>maxW)maxW=l.w;});
var EXP=null, SEL=null, graph=null;
function memIdx(mid){ // "m|集团id|席位" → {g,m}
  var p=mid.split('|'); var g=byG[p[1]]; if(!g)return null;
  for(var i=0;i<g.members_top.length;i++)
if('m|'+g.id+'|'+g.members_top[i].seat===mid)return {g:g,m:g.members_top[i]};
  return null;}
function nodesBuild(){
  var ns=[];
  D.groups.forEach(function(g){
ns.push({id:g.id, style:{
  size:Math.round(36+58*Math.sqrt(g.n_seats/maxSeats)),
  fill:g._c, fillOpacity:0.9,
  stroke: SEL===g.id?'#e6edf3':(g.wl_n>0?'#f85149':'#30363d'),
  lineWidth: SEL===g.id?3.5:(g.wl_n>0?2.5:1.5),
  labelText:g.short+'\n'+g.n_seats+'席', labelFill:'#e6edf3',
  labelFontSize:12, labelPlacement:'bottom', labelLineHeight:16}});});
  if(EXP){var g=byG[EXP];
var md=1;(g.members_top||[]).forEach(function(m){if(m.deg>md)md=m.deg;});
(g.members_top||[]).forEach(function(m){
  ns.push({id:'m|'+g.id+'|'+m.seat, style:{
    size:Math.round(8+16*Math.sqrt((m.deg||1)/md)),
    fill:g._c, fillOpacity:0.45, stroke:'#8b949e', lineWidth:1,
    labelText:(m.reg||m.seat).slice(0,12), labelFill:'#8b949e',
    labelFontSize:9, labelPlacement:'bottom'}});});}
  return ns;}
function edgesBuild(){
  var es=[];
  D.links.forEach(function(l,i){
es.push({id:'L'+i, source:l.a, target:l.b, style:{
  stroke:(l.opp*2>l.w)?'#d29922':'#58a6ff',
  strokeOpacity:0.35+0.45*l.w/maxW,
  lineWidth:0.8+4.5*Math.sqrt(l.w/maxW)}});});
  if(EXP){var g=byG[EXP];
(g.members_top||[]).forEach(function(m){
  es.push({id:'E'+m.seat, source:g.id,
           target:'m|'+g.id+'|'+m.seat, style:{
             stroke:'#30363d', lineWidth:1, strokeOpacity:0.8}});});}
  return es;}
function redraw(){
  if(!graph)return;
  graph.setData({nodes:nodesBuild(), edges:edgesBuild()});
  graph.render();}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,
  function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function selGroup(id, scroll){
  SEL=id; var g=byG[id]; var d=document.getElementById('kgod');
  if(!g){d.innerHTML='<div class="dim" style="margin:8px">集团不存在'
+'</div>';return;}
  try{var _h=(location.hash||'').replace(/^#/,'')
.replace(/g=[^&]*/g,'').replace(/&+/g,'&')
.replace(/^&|&$/g,'');
location.hash=(_h?_h+'&':'')+'g='
+encodeURIComponent(id);}catch(e){}
  var rows=(g.members_top||[]).map(function(m,i){
return '<tr><td title="'+esc(m.seat)+'">'+esc((m.reg||m.seat).slice(0,16))
  +'</td><td>'+m.n+'</td><td class="bdg n">'+m.deg+'</td></tr>';}).join('');
  d.innerHTML='<div style="font-weight:600;font-size:14px;margin:2px 0 4px">'
+'<span style="display:inline-block;width:10px;height:10px;border-'
+'radius:50%;background:'+g._c+';margin-right:6px"></span>'+esc(g.short)
+(g.has_reg?'':' <span class="bdg">名单外</span>')+'</div>'
+'<div class="dim" style="font-size:12px;line-height:1.7">'
+'锚点 '+(g.anchor?esc(g.anchor):'—')+'<br>'
+'席位 <b>'+g.n_seats+'</b> · 池内对 <b>'+g.n_pairs+'</b> · 共现 '
+g.w_total+'<br>watchlist 候选 <b style="color:var(--red-500)">'+g.wl_n
+'</b> · 成员共现度合计 '+g.deg_sum+'</div>'
+'<div style="margin:8px 0 4px;font-size:12px;color:var(--gray-5)">Top 席位'
+'（按共现度，双击左侧节点可展开全量前 8）</div>'
+'<table class="sticky"><tr><th>席位/主体</th><th>上榜</th><th>共现'
+'</th></tr>'+(rows||'<tr><td colspan=3 class="dim">无</td></tr>')
+'</table>'
+'<button onclick="window._kgNet(\''+esc(g.short).replace(/'/g,"\\'")
+'\')" style="margin:10px 0 4px;background:var(--blu-600);color:#fff;border:'
+'none;border-radius:6px;font-size:12px;padding:6px 14px;cursor:'
+'pointer">↓ 在席位网络画布中查看本集团</button>';
  redraw();
  if(scroll){var el=document.getElementById('kgovsec');
if(el)el.scrollIntoView({behavior:'smooth',block:'start'});}}
function detailDefault(){
  var d=document.getElementById('kgod');
  var t=D.groups.map(function(g){
return '<tr><td><span style="display:inline-block;width:8px;height:8px;'
+'border-radius:50%;background:'+g._c+';margin-right:5px"></span>'
+'<a href="javascript:void(0)" onclick="window._kgSel(\''+g.id+'\')">'
+esc(g.short)+'</a></td><td>'+g.n_seats+'</td><td>'+g.w_total
+'</td><td>'+(g.wl_n?'<span style="color:var(--red-500)">'+g.wl_n
+'</span>':'0')+'</td></tr>';}).join('');
  var ls=D.links.map(function(l){return '<tr><td>'+esc((byG[l.a]||{})
.short)+' × '+esc((byG[l.b]||{}).short)+'</td><td>'+l.w+'</td><td>'
+(l.opp*2>l.w?'<span style="color:var(--ylw-500)">对手 '+l.opp+'</span>':l.opp)
+'</td></tr>';}).join('');
  d.innerHTML='<div style="font-weight:600;font-size:13px;margin:2px 0 6px">'
+'总览 · 按集团</div><table class="sticky"><tr><th>集团</th><th>席位'
+'</th><th>共现</th><th>WL</th></tr>'+t+'</table>'
+'<div style="font-weight:600;font-size:13px;margin:10px 0 4px">跨集团'
+'桥接（按共现降序）</div><table class="sticky"><tr><th>集团对</th>'
+'<th>共现</th><th>对手</th></tr>'+ls+'</table>'
+'<div class="dim" style="font-size:12px;margin-top:8px">单击左图节点'
+'或上表集团名查看详情；<b>双击</b>图节点展开成员；下面"席位网络画布'
+'"是席位级全量分析（回放/突变/对比）。</div>';}
window._kgSel=function(id){selGroup(id,false);};
window._kgNet=function(short){
  if(window._kgSetComm){window._kgSetComm(short);}
  else{location.href='/kg/map?comm='+encodeURIComponent(short);}};
window._kgQS=function(){
  var q=(document.getElementById('kgoq').value||'').trim();
  var box=document.getElementById('kgores'), st=document.getElementById('kgors');
  if(!q){box.style.display='none';st.textContent='';return;}
  var hits=[];
  D.groups.forEach(function(g){
if((g.short+g.name+(g.anchor||'')).indexOf(q)>=0)hits.push({g:g,why:'集团'});
(g.members_top||[]).forEach(function(m){
  if(((m.seat||'')+(m.reg||'')).indexOf(q)>=0)
    hits.push({g:g,why:(m.reg||m.seat).slice(0,18)});});});
  st.textContent=hits.length?hits.length+' 个匹配':'无匹配';
  box.style.display='';
  box.innerHTML=hits.slice(0,8).map(function(h){
return '<div style="display:inline-block;margin:2px 6px 2px 0"><button '
+'onclick="window._kgSel(\''+h.g.id+'\')" style="background:var(--gray-3);'
+'color:var(--blu-500);border:1px solid var(--gray-4);border-radius:6px;font-size:'
+'12px;padding:3px 10px;cursor:pointer">'+esc(h.g.short)
+' <span style="color:var(--gray-5)">('+esc(h.why)+')</span></button></div>';})
.join('')||'<span class="dim">无匹配（仅索引 top 成员，全量席位用'
+'席位网络画布的集团过滤）</span>';};
window._kgCSV=function(){
  var L=['\ufeffkind,name,seats,pairs,cooccur,wl,deg_sum'];
  D.groups.forEach(function(g){L.push('group,'+JSON.stringify(g.short)+','
+g.n_seats+','+g.n_pairs+','+g.w_total+','+g.wl_n+','+g.deg_sum);});
  L.push('');
  L.push('kind,group_a,group_b,cooccur,opponent');
  D.links.forEach(function(l){L.push('link,'+JSON.stringify((byG[l.a]||{})
.short)+','+JSON.stringify((byG[l.b]||{}).short)+','+l.w+','+l.opp);});
  var b=new Blob([L.join('\n')],{type:'text/csv'});
  var a=document.createElement('a');a.href=URL.createObjectURL(b);
  a.download='kg_overview.csv';a.click();};
var tip=document.getElementById('kgotip');
function tipShow(html,x,y){tip.innerHTML=html;tip.style.display='block';
  tip.style.left=Math.min(x+14,innerWidth-360)+'px';
  tip.style.top=Math.max(8,y-14)+'px';}
try{
  graph=new G6.Graph({container:'kgov', autoFit:'view', animation:false,
layout:{type:'force', link:{distance:160}, nodeStrength:-600},
behaviors:['drag-canvas','zoom-canvas','drag-element']});
  graph.setData({nodes:nodesBuild(), edges:edgesBuild()});
  graph.on('node:click', function(e){var id=e.itemId||e.target.id;
if(!id)return;
LASTC=Date.now();
if(String(id).indexOf('m|')===0){var mi=memIdx(id);
  if(mi)tipShow('<b>'+esc(mi.m.reg||mi.m.seat)+'</b><br>上榜 '
    +mi.m.n+' · 共现度 '+mi.m.deg+'<br><span style="color:var(--gray-5)">'
    +esc(mi.m.seat)+'</span>', e.client?e.client.x:e.clientX,
    e.client?e.client.y:e.clientY);
  return;}
selGroup(id,false);});
  graph.on('node:dblclick', function(e){var id=e.itemId||e.target.id;
if(!id||String(id).indexOf('m|')===0)return;
LASTD=Date.now();
EXP=(EXP===id)?null:id;redraw();});
  graph.on('node:mouseenter', function(e){var id=e.itemId||e.target.id;
if(!id||String(id).indexOf('m|')===0)return;var g=byG[id];if(!g)return;
var brid=D.links.filter(function(l){return l.a===id||l.b===id;});
tipShow('<b style="font-size:13px">'+esc(g.name)+'</b><br>席位 '
  +g.n_seats+' · 池内对 '+g.n_pairs+' · 共现 '+g.w_total
  +'<br>watchlist 候选 '+g.wl_n+' · 锚点 '+(g.anchor?esc(g.anchor):'—')
  +(brid.length?'<br><span style="color:var(--gray-5)">桥接：'
    +brid.map(function(l){return esc((byG[l.a]===g?byG[l.b]:byG[l.a])
      .short)+'×'+l.w;}).join(' · ')+'</span>':''),
  0, 0);});
  graph.on('node:mousemove', function(e){
var x=e.client?e.client.x:e.clientX, y=e.client?e.client.y:e.clientY;
tip.style.left=Math.min(x+14,innerWidth-360)+'px';
tip.style.top=Math.max(8,y-40)+'px';});
  graph.on('node:mouseleave', function(){tip.style.display='none';});
  graph.render();
  // ── hover/click 兜底（同公司本体页方案）：G6 事件桥只认事件层 canvas
  // 上的 PointerEvent，CDP/自动化只发原生 MouseEvent 走不通 → 容器级
  // 原生监听 + getElementPosition 几何命中补位；与 G6 真实交互幂等共存
  // （LASTC/LASTD 60ms 去重 + 拖拽位移抑制）；member hover 与 G6 原语义
  // 一致不提示 ──
  try{
var mbox=document.getElementById('kgov');
var HOVN=null,HOVH='',LASTC=0,LASTD=0,PD=null;
function hitNode(e){
  var p=null;
  try{var a=graph.getCanvasByClient([e.clientX,e.clientY]);
    p={x:(a&&a.x!=null)?a.x:a[0],y:(a&&a.y!=null)?a.y:a[1]};}
  catch(_){p=null;}
  if(!p)return null;
  var hit=null,best=1e9;
  D.groups.forEach(function(g){
    try{var q=graph.getElementPosition(g.id);if(!q)return;
      var m=Math.round(36+58*Math.sqrt(g.n_seats/maxSeats))/2+6,
        dx=p.x-q[0],dy=p.y-q[1],d2=dx*dx+dy*dy;
      if(d2<=m*m&&d2<best){best=d2;hit={id:g.id,g:g};}}catch(_){}});
  if(EXP){var gg=byG[EXP],md=1;
    (gg.members_top||[]).forEach(function(m){if(m.deg>md)md=m.deg;});
    (gg.members_top||[]).forEach(function(m){
      try{var q=graph.getElementPosition('m|'+gg.id+'|'+m.seat);
        if(!q)return;
        var mm=Math.round(8+16*Math.sqrt((m.deg||1)/md))/2+6,
          dx=p.x-q[0],dy=p.y-q[1],d2=dx*dx+dy*dy;
        if(d2<=mm*mm&&d2<best){best=d2;
          hit={id:'m|'+gg.id+'|'+m.seat,g:gg,m:m};}}catch(_){}});}
  return hit;}
mbox.addEventListener('pointermove',function(e){
  var hit=hitNode(e);
  if(!hit||hit.m){   // member 与 G6 原语义一致不做 hover 提示
    if(HOVN){HOVN=null;tip.style.display='none';}
    return;}
  var g=hit.g;
  if(HOVN!==hit.id){
    var brid=D.links.filter(function(l){
      return l.a===g.id||l.b===g.id;});
    HOVN=hit.id;
    HOVH='<b style="font-size:13px">'+esc(g.name)+'</b><br>席位 '
      +g.n_seats+' · 池内对 '+g.n_pairs+' · 共现 '+g.w_total
      +'<br>watchlist 候选 '+g.wl_n+' · 锚点 '
      +(g.anchor?esc(g.anchor):'—')
      +(brid.length?'<br><span style="color:var(--gray-5)">桥接：'
        +brid.map(function(l){return esc((byG[l.a]===g?byG[l.b]
          :byG[l.a]).short)+'×'+l.w;}).join(' · ')+'</span>':'');}
  tipShow(HOVH,e.clientX,e.clientY);},false);
mbox.addEventListener('pointerleave',function(){
  if(HOVN){HOVN=null;tip.style.display='none';}},false);
// click/dblclick 兜底：无 PointerEvent 环境复刻 node:click/dblclick；
// dblclick 用独立 LASTD——CDP 双击序列中 click(1) 兜底自打 LASTC 会
// 吞掉紧随的 dblclick；PD 位移>8 抑制拖画布/拖节点松手误触
mbox.addEventListener('pointerdown',function(e){
  PD={x:e.clientX,y:e.clientY};},false);
mbox.addEventListener('click',function(e){
  var ok=(Date.now()-LASTC>=60);
  if(ok&&PD&&(Math.abs(e.clientX-PD.x)+Math.abs(e.clientY-PD.y))>8)
    ok=false;
  PD=null;
  if(!ok)return;
  var hit=hitNode(e);if(!hit)return;
  LASTC=Date.now();
  if(hit.m){tipShow('<b>'+esc(hit.m.reg||hit.m.seat)+'</b><br>上榜 '
    +hit.m.n+' · 共现度 '+hit.m.deg
    +'<br><span style="color:var(--gray-5)">'+esc(hit.m.seat)
    +'</span>',e.clientX,e.clientY);return;}
  selGroup(hit.id,false);},false);
mbox.addEventListener('dblclick',function(e){
  if(Date.now()-LASTD<60)return;
  var hit=hitNode(e);
  if(!hit||hit.m)return;
  LASTD=Date.now();
  EXP=(EXP===hit.id)?null:hit.id;redraw();},false);
  }catch(e2){}
}catch(err){
  document.getElementById('kgov').innerHTML='<div style="color:var(--red-500);'
  +'padding:20px;font-size:13px">G6 画布初始化失败：'+esc(err.message)
  +'</div>';}
detailDefault();
var m0=(location.hash||'').match(/g=([^&]+)/);
if(m0){var gid=decodeURIComponent(m0[1]);if(byG[gid])selGroup(gid,false);}
})();'''
    tail = tail.replace("__P__", payload)
    sub = ("<div class='dim' style='margin:2px 0 6px'>窗口 %d 天 · %d 集团"
           " · 跨集团桥接 %d 条 · 节点大小∝席位规模 · 边宽∝跨集团共现"
           % (ov.get("days") or 0, len(ov.get("groups") or []),
              len(ov.get("links") or [])))
    head = head.replace(
        "<div class='dim' style='margin:2px 0 6px'>窗口 %d 天 · %d 集团"
        " · 跨集团桥接 %d 条 · 节点大小∝席位规模 · 边宽∝跨集团共现", sub)
    head = head.replace(
        " · 快照 %s</div>", " · 快照 %s</div>", 1)
    head = head % e(ov.get("generated_at") or "")
    return head + tail + "\n</script>\n"
