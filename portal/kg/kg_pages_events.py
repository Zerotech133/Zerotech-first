# -*- coding: utf-8 -*-
"""kg 页族 events/doc/battle（api_kg 拆分第三批，纯搬运 self→h）。"""

import json
import sys
import os as _os
from api_common import (
    QBT_DIR,
    pill_nav)
import logging

def _kg_battle_page(h):
    """K262 作战地图：个股事件·信号·资金·股价时序画布 + 批量体检。

    URL：/kg/battle?code=000338.SZ 单股时间轴；
         /kg/battle?codes=a,b,c,… 数据集导入批量体检（红黄绿）。
    数据：/kg/timeline/{code}（五图层压平+三研究规则）、
         /kg/batch/{codes}。
    """
    body = """<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>作战地图 · 图谱</title>{css}{nav}<style>
.bt{max-width:var(--page-w);margin:0 auto;padding:0 10px}
.btbar{display:flex;gap:8px;margin:12px 0;flex-wrap:wrap;align-items:center}
.btbar input,.btbar select{background:var(--gray-0);border:1px solid var(--gray-4);
color:var(--gray-7);border-radius:8px;padding:7px 10px;font-size:13px}
#qcode{flex:1;min-width:220px}
.btbar button{background:#238636;border:none;color:#fff;border-radius:8px;
padding:8px 14px;font-size:13px;cursor:pointer}
.btbar button.alt{background:var(--gray-4)}
.leg{font-size:11.5px;color:var(--gray-5);margin:6px 0}
.chip{display:inline-block;border-radius:10px;padding:2px 9px;font-size:12px;
margin-right:6px;border:1px solid var(--gray-4)}
.chip.b{background:#1f3a1f;color:#7ee787}.chip.s{background:#3a1f1f;color:#ffa198}
.chip.n{background:var(--gray-2);color:var(--gray-5)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
gap:10px;margin-top:12px}
.card{background:var(--gray-2);border-radius:10px;padding:12px;cursor:pointer}
.card.hi{border:2px solid var(--red-500)}.card.mid{border:2px solid var(--ylw-500)}
.card.ok{border:2px solid var(--grn-600)}
.card .cd{color:var(--gray-7);font-weight:600}.card .pr{font-size:12px;margin-top:6px}
.pr .h{color:#ffa198}.pr .m{color:#e3b341}
#tl{margin-top:14px;background:var(--gray-0);border:1px solid var(--gray-4);
border-radius:10px;padding:10px;overflow-x:auto;cursor:grab}
#tlc{display:block}
.tlrow{font-size:11px;color:var(--gray-5);user-select:none}
.dot{position:absolute;border-radius:50%;cursor:pointer}
.det{background:var(--gray-2);border:1px solid var(--gray-4);border-radius:10px;
padding:12px;margin-top:10px;font-size:13px;color:var(--gray-7);display:none}
.rules{margin:8px 0;font-size:12.5px;color:var(--gray-7)}
.rl{margin-right:14px;display:inline-block}
</style></head><body>{nav}
<div class=bt><h1>🗺 作战地图</h1>
<div class='dim' style='margin:0 0 6px'>个股作战时间轴：公告·缠论买卖点·
游资上榜·资金出逃·涨跌停 沿时间同屏；批量体检：数据集导入看「这批有什么问题」
（红=高危 黄=观察 绿=健康）</div>
<div class=btbar>
<input id=qcode placeholder='单股代码 如 000338.SZ；或一批（逗号分隔）触发批量体检'>
<select id=qdays><option value=60>60日</option><option value=120 selected>
120日</option><option value=250>250日</option></select>
<button onclick=go(1)>单股时间轴</button>
<button class=alt onclick=go(2)>批量体检</button>
<button class=alt onclick="location.href='/kg/ask'">💬 图谱问答入口</button>
</div>
<div class=leg>图层：<span style="color:#7ee787">● 缠论买卖点</span>
<span style="color:#ffa198">● 游资/龙虎榜</span>
<span style="color:#79c0ff">● 资金净流</span>
<span style="color:#e3b341">● 公告事件</span>
<span style="color:var(--gray-6)">● 涨跌停/形态</span>
<span style="color:var(--red-500)">● 舆论恐慌</span>
<span style="color:var(--grn-600)">● 舆论乐观</span>　顶部K线带 · 拖动平移 ·
点圆点看详情 · 词云点词反查原帖</div>
<div class=rules id=rules></div>
<div id=records></div>
<div id=cards></div>
<div class=leg id=lsw style='margin-top:6px'></div>
<canvas id=tlc width=1100 height=270></canvas>
<div class=det id=det></div>
<div id=guba></div>
<div id=gbposts></div>
</div>
<script>
var LAYERS=['缠论','游资','资金','公告','形态','舆情'];
var COLR={缠论:'#7ee787',游资:'#ffa198',资金:'#79c0ff',公告:'#e3b341',
形态:'#c9d1d9',舆情:'#f85149'};
var DATA=null,DATES=[],X0=0,XSCALE=8,selLayer={};
LAYERS.forEach(function(l){selLayer[l]=true});
var GUBA=null,GDOTS=[],KLINE=null,SHOW_K=true;
function go(m){var v=document.getElementById('qcode').value.trim().replace(/，/g,',');
if(!v){alert('输入代码');return}
var d=document.getElementById('qdays').value;
if(m==2||v.indexOf(',')>=0){location.href='/kg/battle?codes='+encodeURIComponent(v)+'&days='+d}
else{location.href='/kg/battle?code='+encodeURIComponent(v)+'&days='+d}}
function qs(k){return new URLSearchParams(location.search).get(k)}
function boot(){var c=qs('code'),cs=qs('codes'),d=qs('days')||120;
document.getElementById('qcode').value=c||cs||'';
if(c){fetch('/kg/timeline/'+encodeURIComponent(c)+'?days='+d)
 .then(function(r){return r.json()}).then(draw)}
else if(cs){fetch('/kg/batch/'+encodeURIComponent(cs)+'?days='+d)
 .then(function(r){return r.json()}).then(drawBatch)}
loadRecords()}
function loadRecords(){var el=document.getElementById('records');
fetch('/kg/records/x').then(function(r){return r.json()}).then(function(d){
if(!d.ok){el.innerHTML="<div class=dim style='color:#ffa198'>战绩层加载失败："+
 d.error+"</div>";return}
var h="<h2 style='margin-top:20px'>📊 四层战绩 <span class=dim style='font-size:12px'>as of "+
 d.as_of+"</span></h2>";
d.layers.forEach(function(L){
h+="<div style='margin-top:12px;background:var(--gray-2);border:1px solid var(--gray-4);"+
"border-radius:10px;padding:12px'><div style='font-weight:600'>"+L.layer+
(L.note?" <span class=dim style='font-weight:400;font-size:11.5px'>"+L.note+"</span>":"")+"</div>";
if(L.error){h+="<div style='color:#ffa198;font-size:12.5px;margin-top:6px'>⚠ "+
 L.error+"</div></div>";return}
h+="<table style='font-size:12.5px;border-collapse:collapse;margin-top:6px'>"+
"<tr class=dim><th align=left style='padding:2px 12px 2px 0'>组</th>"+
"<th align=right style='padding:2px 12px'>n</th>"+
"<th align=right style='padding:2px 12px'>胜率%</th>"+
"<th align=right style='padding:2px 12px'>均收%</th></tr>";
L.rows.forEach(function(r){var av=r.avg_pct,ac=av==null?'#8b949e':
 (av>=0?'#7ee787':'#ffa198');
h+="<tr><td style='padding:2px 12px 2px 0'>"+r.group+"</td>"+
"<td align=right>"+r.n+"</td><td align=right>"+(r.win==null?'—':r.win)+
"</td><td align=right style='color:"+ac+"'>"+(av==null?'—':av)+"</td></tr>";
if(r.extra)h+="<tr class=dim><td colspan=4 style='font-size:11px;padding:0 0 5px'>"+
r.extra+"</td></tr>"});
h+="</table></div>"});
el.innerHTML=h}).catch(function(){})}
function draw(d){if(!d.ok){alert(d.error);return}DATA=d;DATES=[];
var seen={};d.layers.forEach(function(l){if(!seen[l.date]){seen[l.date]=1;DATES.push(l.date)}});
DATES.sort();
var rh='';d.rules.forEach(function(r){var cls=r.hits?'b':'n';
rh+="<span class=rl><span class='chip "+cls+"'>"+r.rule+"</span>"+
(r.total?r.hits+"/"+r.total+"（"+Math.round(100*(r.hit_rate||0))+"%）":'无样本')+
"</span>"});
document.getElementById('rules').innerHTML=rh;renderSwitches();drawTL();
loadGuba(d.code);loadKline(d.code);
}
function loadGuba(code){var el=document.getElementById('guba');
el.innerHTML="<div class='dim' style='margin-top:14px'>⏳ 股吧舆论分析中…</div>";
fetch('/kg/guba/'+encodeURIComponent(code)+'?days=60')
 .then(function(r){return r.json()}).then(function(g){
 if(!g.ok){el.innerHTML="<div class='dim' style='margin-top:14px'>股吧舆论："+
  g.error+"</div>";return}
 GUBA=g;drawTL();
 var mx=g.words.length?g.words[0].n:1;
 var wcl=g.words.map(function(w){var sz=12+Math.round(20*Math.sqrt(w.n/mx));
  var hue=w.w.length>3?'#79c0ff':'#e6edf3';
  return "<span data-w='"+w.w+"' onclick=gbWord(this) "+
  "style='font-size:"+sz+"px;color:"+hue+";margin:2px 6px;cursor:pointer;"+
  "display:inline-block'>"+w.w+"<span class=dim style='font-size:10px'> "+w.n+
  "</span></span>"}).join('');
 var mood='';g.days.slice(-14).forEach(function(d){
  var tot=d.panic+d.hope,bg=tot?Math.round(100*Math.max(d.panic,d.hope)/tot):0;
  mood+="<span style='display:inline-block;width:26px;text-align:center;"+
  "margin-right:3px' title='"+d.date+" 帖"+d.n+" 恐慌"+d.panic+"/乐观"+d.hope+
  "'><span style='display:block;height:34px;vertical-align:bottom;'>"+
  "<span style='display:block;width:10px;height:"+Math.round(30*(d.panic/Math.max(tot,1)))+"px;background:var(--red-500);margin:0 auto;'></span>"+
  "<span style='display:block;width:10px;height:"+Math.round(30*(d.hope/Math.max(tot,1)))+"px;background:var(--grn-600);margin:0 auto;'></span></span>"+
  "<span class=dim style='font-size:9px'>"+d.date.slice(4)+"</span></span>"});
 var evs='';(g.events||[]).forEach(function(e){evs+="<tr><td>"+e.date+
  "</td><td>"+e.tag+"</td><td style='color:"+(e.dir=='bull'?'#7ee787':'#ffa198')+
  "'>"+(e.dir=='bull'?'利多':'利空')+"</td><td>"+(e.matched?
  "<span style='color:#7ee787'>✓ 命中 "+e.hit_words.join('/')+"</span>":
  "<span class=dim>—</span>")+"</td></tr>"});
 el.innerHTML="<h2 style='margin-top:18px'>🗣 股吧舆论（近60日 "+g.n_posts+
 " 帖）</h2><div class=dim>词云重心 · 每日恐慌(红)/乐观(绿) · 事件契合"+
 (g.event_match_rate!=null?("率 "+Math.round(100*g.event_match_rate)+"%"):"")+
 "</div><div style='background:var(--gray-0);border:1px solid var(--gray-4);border-radius:10px;padding:14px;margin-top:8px;line-height:2'>"+wcl+"</div>"+
 "<div style='margin-top:10px'>"+mood+"</div>"+
 (evs?"<table style='margin-top:10px;font-size:12.5px;border-collapse:collapse'>"+
 "<tr class=dim><td>日期</td><td>公告tag</td><td>方向</td><td>词云契合</td></tr>"+evs+"</table>":"");
});}
function loadKline(code){KLINE=null;
fetch('/api/kline?c='+encodeURIComponent(code))
 .then(function(r){return r.json()}).then(function(k){
 if(!k.ok||!k.bars){KLINE=[];drawTL();return}
 KLINE=k.bars;drawTL()}).catch(function(){KLINE=[];drawTL()})}
function gbWord(sp){var w=sp.getAttribute('data-w'),el=document.getElementById('gbposts');
var code=GUBA?GUBA.code:qs('code');
el.innerHTML="<div class='dim' style='margin-top:10px'>⏳ 反查「"+w+"」原帖…</div>";
fetch('/kg/guba/'+encodeURIComponent(code)+'?days=60&per='+encodeURIComponent(w))
 .then(function(r){return r.json()}).then(function(d){
 if(!d.ok){el.innerHTML="<div class='dim' style='margin-top:10px'>反查失败："+
  d.error+"</div>";return}
 var h="<div style='margin-top:14px;background:var(--gray-2);border:1px solid var(--gray-4);"+
 "border-radius:10px;padding:12px'><b>🔍 含「"+d.word+"」原帖 "+d.n+" 条</b>"+
 " <button class='chip n' style='cursor:pointer;float:right' "+
 "onclick=\"document.getElementById('gbposts').innerHTML=''\">✕ 关闭</button>";
 if(!d.n){h+="<div class=dim style='margin-top:8px'>近 60 日无命中</div>"}
 else{h+="<table style='margin-top:8px;font-size:12.5px;width:100%;"+
 "border-collapse:collapse'><tr class=dim><td>日期</td><td>标题</td>"+
 "<td>点击</td><td>评论</td><td>多空</td></tr>";
 d.posts.forEach(function(p){h+="<tr><td class=dim>"+p.ts+"</td><td>"+
 p.title+"</td><td class=dim>"+(p.click||0)+"</td><td class=dim>"+
 (p.comment||0)+"</td><td style='color:"+(p.bb==1?'#7ee787':
 p.bb==-1?'#ffa198':'#8b949e')+"'>"+(p.bb==1?'多':p.bb==-1?'空':'—')+
 "</td></tr>"});
 h+="</table>"}
 h+="</div>";el.innerHTML=h})}
function togLayer(l){if(l=='K线'){SHOW_K=!SHOW_K}else{selLayer[l]=!selLayer[l]}
renderSwitches();drawTL()}
function renderSwitches(){var h="<span class=dim>图层开关：</span>";
LAYERS.forEach(function(l){
h+="<button class='chip' style='cursor:pointer;background:"+(selLayer[l]?
'':'#161b22')+";color:"+(selLayer[l]?COLR[l]:'#484f58')+";border-color:"+
(selLayer[l]?COLR[l]:'#30363d')+"' onclick=togLayer('"+l+"')>"+
(selLayer[l]?'● ':'○ ')+l+"</button>"});
h+="<button class='chip' style='cursor:pointer;color:"+(SHOW_K?'#d29922':'#484f58')+
";border-color:"+(SHOW_K?'#d29922':'#30363d')+"' onclick=togLayer('K线')>"+
(SHOW_K?'● ':'○ ')+"K线</button>";document.getElementById('lsw').innerHTML=h}
function drawTL(){var cv=document.getElementById('tlc'),ctx=cv.getContext('2d');
ctx.clearRect(0,0,cv.width,cv.height);if(!DATA)return;
var W=cv.width,H=cv.height,i;
var KY=94,rowH=(H-KY-30)/LAYERS.length,vw=W-90;
var XG=(KLINE&&KLINE.length)?KLINE.map(function(b){
 return b.d.replace(/-/g,'')}):DATES;
var n=XG.length;if(!n)return;
ctx.font='11px sans-serif';
if(SHOW_K&&KLINE&&KLINE.length){
 var mn=1e18,mx=-1e18;KLINE.forEach(function(b){if(b.l<mn)mn=b.l;if(b.h>mx)mx=b.h});
 if(mn>=mx){mn-=1;mx+=1}
 var y0=16,y1=82,cw=vw/n,bw=Math.max(1.5,cw*0.66);
 ctx.fillStyle='#0d1117';ctx.fillRect(56,y0-2,W-70,y1-y0+4);
 KLINE.forEach(function(b,ki){var d8=b.d.replace(/-/g,''),di=XG.indexOf(d8);
 if(di<0)return;var x=56+(di+0.5)*vw/n;
 var up=b.c>=b.o,col=up?'#f85149':'#3fb950';
 var yh=y1-4-(b.h-mn)/(mx-mn)*(y1-y0-8),yl=y1-4-(b.l-mn)/(mx-mn)*(y1-y0-8);
 var yo=y1-4-(b.o-mn)/(mx-mn)*(y1-y0-8),yc=y1-4-(b.c-mn)/(mx-mn)*(y1-y0-8);
 ctx.strokeStyle=col;ctx.globalAlpha=0.85;ctx.beginPath();
 ctx.moveTo(x,yh);ctx.lineTo(x,yl);ctx.stroke();ctx.globalAlpha=1;
 ctx.fillStyle=col;var top=Math.min(yo,yc),hh=Math.max(1,Math.abs(yc-yo));
 ctx.fillRect(x-bw/2,top,bw,hh)});
 ctx.fillStyle='#8b949e';ctx.fillText('K线',14,(y0+y1)/2+4);
 ctx.textAlign='right';ctx.fillText(mx.toFixed(2),W-24,y0+8);
 ctx.fillText(mn.toFixed(2),W-24,y1);ctx.textAlign='left';
 ctx.strokeStyle='#30363d';ctx.beginPath();ctx.moveTo(56,y1+4);
 ctx.lineTo(W-14,y1+4);ctx.stroke()}
for(i=0;i<LAYERS.length;i++){var y=KY+rowH*i+rowH/2;ctx.fillStyle='#161b22';
ctx.fillRect(56,y-10,W-70,20);ctx.fillStyle=selLayer[LAYERS[i]]?'#8b949e':'#484f58';
ctx.fillText(LAYERS[i],14,y+4)}
var step=Math.max(1,Math.ceil(n/14));
for(i=0;i<n;i+=step){var x=56+(i+0.5)*vw/n;
ctx.fillStyle='#484f58';ctx.fillText(XG[i].slice(2),x-16,H-8)}
DATA.layers.forEach(function(l){
if(!selLayer[l.layer])return;var di=XG.indexOf(l.date);if(di<0)return;
var li=LAYERS.indexOf(l.layer);if(li<0)return;
var x=56+(di+0.5)*vw/n;var y=KY+rowH*li+rowH/2;
var r=l.layer=='资金'?3.2:(l.dir=='bull'?5:4);
ctx.beginPath();ctx.arc(x,y,r,0,7);
ctx.fillStyle=COLR[l.layer]||'#c9d1d9';ctx.globalAlpha=l.dir=='bear'?0.85:1;
ctx.fill();ctx.globalAlpha=1;l._x=x;l._y=y});
GDOTS=[];
if(GUBA&&selLayer['舆情']){var li2=LAYERS.indexOf('舆情');
GUBA.days.forEach(function(d){if(d.mood=='flat')return;
var di=XG.indexOf(d.date);if(di<0)return;
var x=56+(di+0.5)*vw/n,y=KY+rowH*li2+rowH/2;
ctx.beginPath();ctx.arc(x,y,d.mood=='panic'?5:3.5,0,7);
ctx.fillStyle=d.mood=='panic'?'#f85149':'#2ea043';
if(d.mood=='hope')ctx.globalAlpha=0.75;
ctx.fill();ctx.globalAlpha=1;GDOTS.push({x:x,y:y,rec:d})})}
}
var cvv=document.getElementById('tlc'),drag=false,lx=0,sl=0;
cvv.addEventListener('mousedown',function(e){drag=true;lx=e.clientX;sl=cvv.scrollLeft});
window.addEventListener('mouseup',function(){drag=false});
window.addEventListener('mousemove',function(e){if(drag)cvv.scrollLeft=sl-(e.clientX-lx)});
cvv.addEventListener('click',function(e){if(!DATA)return;
var b=cvv.getBoundingClientRect(),mx=e.clientX-b.left+cvv.parentElement.scrollLeft,
my=e.clientY-b.top;var best=null,bd=1e9;
DATA.layers.forEach(function(l){if(l._x==null)return;
var dd=(l._x-mx)*(l._x-mx)+(l._y-my)*(l._y-my);if(dd<bd){bd=dd;best=l}});
var gbest=null,gbd=1e9;
GDOTS.forEach(function(g){var dd=(g.x-mx)*(g.x-mx)+(g.y-my)*(g.y-my);
if(dd<gbd){gbd=dd;gbest=g}});
var det=document.getElementById('det');
if(gbest&&gbd<144&&gbd<bd){var d=gbest.rec;det.style.display='block';
det.innerHTML="<b style='color:var(--red-500)'>舆情 · "+
(d.mood=='panic'?'恐慌日':'乐观日')+"</b>　<span class=dim>"+d.date+
"</span><div style='margin-top:6px'>帖 "+d.n+"　恐慌词 "+d.panic+
"　乐观词 "+d.hope+"　多空票 "+d.bb_bull+"/"+d.bb_bear+
"</div>";return}
if(best&&bd<144){det.style.display='block';
det.innerHTML="<b style='color:"+COLR[best.layer]+"'>"+best.layer+" · "
+best.kind+"</b>　<span class=dim>"+best.date+"　方向 "+(best.dir=='bull'?'利多':best.dir=='bear'?'利空':'中性')
+"</span><div style='margin-top:6px'>"+(best.label||'')+
(best.value!=null?"　值 "+best.value:'')+"</div>"}});
function drawBatch(d){if(!d.ok){alert(d.error);return}
var el=document.getElementById('cards');var h="<div class=cards>";
document.getElementById('rules').innerHTML="批量体检 "+d.n+" 只：🔴 高危 "
+d.summary.high+" · 🟡 观察 "+d.summary.mid+" · 🟢 健康 "
+(d.n-d.summary.high-d.summary.mid);
d.items.forEach(function(it){var cls=it.problems&&it.problems.length?
(it.problems[0].severity=='high'?'hi':'mid'):'ok';
h+="<div class='card "+cls+"' onclick=\\"location.href='/kg/battle?code="
+encodeURIComponent(it.code)+"'\\"><div class=cd>"+it.code+
(it.ok?"　<span class=dim>"+it.n_layers+" 事件</span>":"　❌")+"</div>";
(it.problems||[]).forEach(function(p){h+="<div class='pr'><span class="
+(p.severity=='high'?'h':'m')+">"+p.kind+"</span> · "+p.text+"</div>"});
if(!it.problems||!it.problems.length)h+="<div class='pr dim'>无风险信号</div>";
h+="</div>"});
h+="</div>";el.innerHTML=h}
boot();
</script></body></html>""".replace("{css}", h._KG_CSS).replace(
        "{nav}", pill_nav("kgcanvas"))
    h._html(body)


def _kg_doc_page(h, qs=None):
    """K266 M4 文档智能中心：词云三跳（词→文档→事件→图谱实体）+ 上传入口。

    URL：/kg/doc
    数据：data/k266_docs/doc_texts.jsonl + structure_shadow.jsonl（M2/M3 产出）。
    上传：POST /kg/doc/upload?name=xxx（M2 端点），落 inbox/pending 由
          K266-DocServices 计划任务（5 分钟）自动摄取识别。
    """
    import collections as _collections
    _docs_dir = _os.path.join(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))), "quant_backtest", "data", "k266_docs")
    _shadow_fp = _os.path.join(_docs_dir, "structure_shadow.jsonl")
    # 性能：不再全量加载 doc_texts.jsonl（含全文，4MB+ 且随新闻批次无限增长）。
    # chars 用 shadow 自带值——judge() 恒写 chars，实测 1148 条 0 缺失。
    shadow = {}
    try:
        with open(_shadow_fp, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    shadow[r.get("doc_id")] = r
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
    # 时间窗（K266-W5，?days=7/30）：只统计近 N 日影子行（ts=judge 时刻，
    # 新闻/文档当日采集当日判，近似入库时间成立）；默认全部。窗口同时作用于
    # 词云与文档列表，保持"点词过滤文档"两侧口径一致。ts 恒为
    # YYYY-MM-DD HH:MM:SS，cutoff 直接字典序比较。
    _days = None
    if qs:
        from urllib.parse import parse_qs as _pqd
        try:
            _d = int((_pqd(qs).get("days") or [""])[0])
        except (ValueError, TypeError):
            _d = None
        if _d in (7, 30):
            _days = _d
    _cutoff = ""
    if _days:
        import datetime as _dt
        _cutoff = (_dt.datetime.now() - _dt.timedelta(
            days=_days)).strftime("%Y-%m-%d %H:%M:%S")
    cards, freq = [], _collections.Counter()
    # K266-B 热词归一：LLM tags 同义变体并到规范词（词云/词片/文档过滤三处同源）。
    # 词表与 M3 生成端共用一份（k266_m3_structure.TAG_ALIAS），改一处两端生效；
    # M3 已源头归一，此处兜底历史影子行。保守口径：只并真同义，不并方向复合词。
    if QBT_DIR not in sys.path:
        sys.path.insert(0, QBT_DIR)
    from k266_m3_structure import TAG_ALIAS as _alias
    for did, s in shadow.items():
        if not s.get("raw_ok"):
            continue
        if _cutoff and str(s.get("ts", "")) < _cutoff:
            continue  # 时间窗外：词云与文档列表同口径剔除
        if str(s.get("name", "")) in ("dup_test.png", "make_test.pdf"):
            continue  # OPS-1008: 排除人工测试件（仅展示层，影子数据保留）
        _raw = [x.replace(" ", "").replace("\u3000", "")
                for x in s.get("tags", []) if x.strip()]
        tags, _seen = [], set()
        for _t in (_alias.get(x, x) for x in _raw):
            if _t not in _seen:   # 归一后同文档变体去重，防重复计数
                _seen.add(_t)
                tags.append(_t)
        freq.update(tags)
        cards.append({
            "id": did, "name": s.get("name", ""), "src": s.get("src", ""),
            "type": s.get("doc_type", "其他"),
            "dir": s.get("direction", "中性"),
            "title": s.get("title_guess", "")[:40],
            "why": s.get("direction_why", "")[:40],
            "companies": s.get("companies", [])[:6],
            "tags": tags, "events": s.get("events", [])[:5],
            "ts": s.get("ts", ""),
            "chars": s.get("chars") or 0,
        })
    # K266-B 适配：新闻单条（src=news）按时间置顶，文档按字数
    _news_cards = sorted([c for c in cards if c.get("src") == "news"],
                         key=lambda c: c.get("ts", ""), reverse=True)
    _doc_cards = sorted([c for c in cards if c.get("src") != "news"],
                        key=lambda c: -c["chars"])
    cards = _news_cards + _doc_cards
    # K266-W6 词云渲染统一：PNG（wordcloud 库首次渲染慢/不可点/需缓存）
    # 退役，换股吧侧同款 CSS 字号加权词云——零渲染开销（缓存逻辑一并
    # 删除）、点词直接调 flt() 过滤文档、与 /kg/battle 股吧词云同构。
    # 字号公式与股吧侧一致（sqrt 压缩长尾）。
    top_words = [w for w, _ in freq.most_common(40)]
    # 时间窗切换 chips：href 整页跳转（服务端按窗重渲染，词云/列表/缓存
    # 全套跟随），当前窗高亮；仿右侧 srcflt 前端过滤是另一路，此处必须
    # 服务端切（freq 在 Python 侧算）
    _wins = ((None, "/kg/doc", "全部"),
             (7, "/kg/doc?days=7", "近7日"),
             (30, "/kg/doc?days=30", "近30日"))
    win_html = "".join(
        "<a class='chip n srcf%s' href='%s'>%s</a>"
        % (" on" if _days == d else "", _h, _lb)
        for d, _h, _lb in _wins)
    import html as _html
    chips_html = "".join(
        "<span class='chip wc' data-w='%s' onclick=\"flt('%s')\">%s"
        "<i>%d</i><a href='/kg?q=%s' target='_blank' "
        "onclick=\"event.stopPropagation()\" "
        "style='color:#d2a8ff;margin-left:4px;text-decoration:none'"
        " title='图谱画布搜索'>⤴</a></span>"
        % (_html.escape(w), _html.escape(w), _html.escape(w), freq[w],
           _html.escape(w))
        for w in top_words)
    if freq:
        _mx = freq[top_words[0]]
        wc_html = "".join(
            "<span class='wword' data-w='%s' onclick=\"flt('%s')\" "
            "title='%d 次 · 点击过滤文档' style='font-size:%dpx;color:%s;"
            "margin:2px 6px;cursor:pointer;display:inline-block'>%s"
            "<i style='font-style:normal;color:var(--gray-5);font-size:10px'> %d"
            "</i></span>"
            % (_html.escape(w), _html.escape(w), freq[w],
               12 + round(20 * (freq[w] / _mx) ** 0.5),
               "#79c0ff" if len(w) > 3 else "#e6edf3",
               _html.escape(w), freq[w])
            for w in top_words)
        wc_html = ("<div style='background:var(--gray-0);border:1px solid "
                   "var(--gray-4);border-radius:10px;padding:14px;"
                   "line-height:2'>" + wc_html + "</div>")
    else:
        wc_html = "<div class='empty'>词云待生成（尚无影子数据）</div>"
    body = """<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>文档智能中心 · K266</title>{css}{nav}<style>
.dc{max-width:var(--page-w);margin:0 auto;padding:0 10px}
.dgrid{display:grid;grid-template-columns:400px 1fr;gap:14px;margin-top:12px}
@media(max-width:900px){.dgrid{grid-template-columns:1fr}}
.panel{background:var(--gray-2);border:1px solid var(--gray-4);border-radius:10px;padding:12px}
.wchips{margin-top:10px;line-height:2.1}
.chip.wc{cursor:pointer;background:var(--gray-2);color:var(--gray-7)}
.chip.wc i{font-style:normal;color:var(--gray-5);font-size:10.5px;margin-left:3px}
.chip.wc.on{background:#1f3a1f;color:#7ee787}
.wword.on{background:#1f3a1f;outline:1px solid #7ee787;border-radius:4px}
.chip.srcf{cursor:pointer}
.chip.srcf.on{background:#1f3a1f;color:#7ee787}
a.chip.srcf{text-decoration:none}
.card{background:var(--gray-2);border:1px solid var(--gray-4);border-radius:10px;
padding:10px 12px;margin-bottom:9px}
.card .nm{color:var(--blu-500);font-size:13px;word-break:break-all}
.card .tg{color:var(--gray-7);font-size:14px;font-weight:600;margin:4px 0}
.meta{font-size:11.5px;color:var(--gray-5)}
.chip.b{background:#1f3a1f;color:#7ee787}.chip.s{background:#3a1f1f;color:#ffa198}
.chip.n{background:var(--gray-2);color:var(--gray-5);border:1px solid var(--gray-4)}
.ev{font-size:12px;color:var(--gray-6);margin-top:5px;padding-left:8px;
border-left:2px solid var(--gray-4)}
.ev a{color:#d2a8ff;text-decoration:none}
.upbar{display:flex;gap:10px;align-items:center;margin:12px 0}
.upbar input[type=file]{display:none}
.upbar button{background:#238636;border:none;color:#fff;border-radius:8px;
padding:8px 16px;font-size:13px;cursor:pointer}
.upbar button.alt{background:var(--gray-4)}
#upmsg{font-size:12px;color:var(--gray-5)}
.empty{color:var(--gray-5);font-size:13px;padding:30px;text-align:center}
h3{font-size:14px;color:var(--gray-7);margin:2px 0 8px}
</style></head><body><div class='dc'>
<div class='upbar'>
 <button onclick="document.getElementById('upf').click()">📤 上传公告/研报</button>
 <input type='file' id='upf' accept='.png,.jpg,.jpeg,.pdf,.bmp,.webp,.txt,.md' multiple>
 <button class='alt' onclick="location.reload()">🔄 刷新</button>
 <span id='upmsg'>支持 PNG/JPG/PDF/TXT/MD 多选；识别约 1-5 分钟（K266-DocServices 自动摄取）</span>
</div>
<div class='dgrid'>
 <div class='panel'><h3>热词词云（点击词过滤文档）<span style='float:right'>{win}</span></h3>{wc}
  <div class='wchips'>{chips}</div></div>
 <div class='panel'><h3>文档影子 <span class='meta' id='cnt'></span>
  <span style='float:right'>
   <span class='chip n srcf on' data-f='all' onclick="srcflt('all')">全部</span>
   <span class='chip n srcf' data-f='news' onclick="srcflt('news')">新闻</span>
   <span class='chip n srcf' data-f='doc' onclick="srcflt('doc')">文档</span>
  </span></h3>
  <div id='cards'></div></div>
</div></div>
<script>
const DOCS = {docs_json};
const box = document.getElementById('cards');
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function render(list){
  document.getElementById('cnt').textContent = list.length + ' / ' + DOCS.length;
  const part = list.slice(0, shown);
  if (!part.length) {box.innerHTML = '<div class="empty">无匹配文档</div>'; return}
  let html = part.map(d => `
<div class='card'>
  <div class='nm'>${esc(d.name)} <span class='meta'>${esc(d.src)} · ${d.chars}字${d.ts?' · '+esc(d.ts):''}</span></div>
  <div class='tg'>${esc(d.title)}</div>
  <span class='chip ${d.dir==='利好'?'b':d.dir==='利空'?'s':'n'}'>${esc(d.dir)}</span>
  <span class='chip n'>${esc(d.type)}</span>
  ${d.tags.map(t=>`<span class='chip n'>${esc(t)}</span>`).join(' ')}
  ${d.events.map(e=>`<div class='ev'>${esc(e.subject||'')} <b>${esc(e.rel||'')}</b> ${esc(e.object||'')} ${e.amount? '['+esc(e.amount)+']':''} <a href="/kg?q=${encodeURIComponent(e.subject||e.object||'')}" target="_blank">⤴图谱</a></div>`).join('')}
</div>`).join('');
  if (list.length > shown) html += `<div style='text-align:center;margin:6px 0'><button class='chip n' style='cursor:pointer' onclick='more()'>显示更多（${shown}/${list.length}）</button></div>`;
  box.innerHTML = html;
}
let cur = null, srcmode = 'all', shown = 200;
function baseList(){
  return DOCS.filter(d => srcmode==='all' ? true : (srcmode==='news' ? d.src==='news' : d.src!=='news'));
}
function srcflt(m){
  srcmode = m; shown = 200;
  document.querySelectorAll('.chip.srcf').forEach(c=>c.classList.toggle('on', c.dataset.f===m));
  flt(cur);
}
function more(){ shown += 200; flt(cur); }
function flt(w){
  if (cur === w) w = null;
  cur = w;
  document.querySelectorAll('.chip.wc').forEach(c=>c.classList.toggle('on', c.dataset.w===w));
  document.querySelectorAll('.wword').forEach(c=>c.classList.toggle('on', c.dataset.w===w));
  render(w ? baseList().filter(d=>d.tags.includes(w)) : baseList());
}
document.getElementById('upf').onchange = async (e) => {
  const msg = document.getElementById('upmsg');
  for (const file of e.target.files) {
msg.textContent = '上传中: ' + file.name;
try {
  const r = await fetch('/kg/doc/upload?name=' + encodeURIComponent(file.name),
    {method:'POST', body: file});
  const j = await r.json();
  msg.textContent = (j.ok ? '✅ ' : '❌ ') + (j.saved || j.error || '');
} catch(err) { msg.textContent = '❌ ' + err }
  }
  e.target.value = '';
}
render(DOCS);
</script></body></html>""".replace("{css}", h._KG_CSS).replace(
        "{nav}", pill_nav("kgcanvas")).replace(
        "{wc}", wc_html).replace("{chips}", chips_html).replace(
        "{docs_json}", json.dumps(cards, ensure_ascii=False)).replace(
        "{win}", win_html)
    h._html(body)


def _kg_events_page(h, qs=None):
    """K266 事件图谱查询面板：态势感知 + 市场Tab + G6 力导向图 + 下钻原文。

    URL：/kg/events?market=主板&q=&dir=&rel=&d0=&d1=
    数据层 quant_backtest/kg_events_portal.py（纯查询不写库），JSON 端点
    /kg/events/situ|graph|list|search|doc/{doc_id} 与本页同源。
    下钻链路：点公司节点 → 事件列表（fetch list?c=code，附图谱全景/
    K线研判跳转）→ 点事件边/原文链接 → 原文弹层（条级 doc_id 去 iNN
    后缀关联 doc_texts.jsonl 全文 + structure_shadow 结构化摘要）。
    """
    import html as _h
    import json as _j
    from datetime import datetime as _dt, timedelta as _td
    from urllib.parse import parse_qs as _pq, quote as _qt
    if QBT_DIR not in sys.path:
        sys.path.insert(0, QBT_DIR)
    import kg_events_portal as KE
    e = _h.escape
    qp = _pq(qs or "")
    market = (qp.get("market") or ["全部"])[0].strip() or "全部"
    q = (qp.get("q") or [""])[0].strip()
    direction = (qp.get("dir") or [""])[0].strip()
    rel = (qp.get("rel") or [""])[0].strip()
    d0 = (qp.get("d0") or [""])[0].strip()
    d1 = (qp.get("d1") or [""])[0].strip()
    su = KE.api("situ")
    g = KE.api("graph", None, {"market": market, "q": q, "dir": direction,
                               "rel": rel, "d0": d0, "d1": d1})

    def _ago(ts):
        try:
            mins = int((_dt.now() - _dt.strptime(
                ts, "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
            if mins < 60:
                return "%d 分钟前" % mins
            if mins < 2880:
                return "%.1f 小时前" % (mins / 60)
            return "%.1f 天前" % (mins / 1440)
        except Exception:
            return ""

    def _cell(label, value, sub="", warn=False):
        return ("<div class='sc%s'><div class='sl'>%s</div>"
                "<div class='sv'>%s</div><div class='ss'>%s</div></div>"
                % (" w" if warn else "", label, value, sub))

    # ── ① 态势感知条（5 格，字段级降级）──
    if su.get("ok"):
        c1 = _cell("NEO4J 连接",
                   "<span class='dot' style='background:var(--grn-500)'></span>已连接",
                   "bolt://localhost:7687")
    else:
        c1 = _cell("NEO4J 连接",
                   "<span class='dot' style='background:var(--red-500)'></span>未连接",
                   e((su.get("error") or "")[:60]), warn=True)
    inv_mk = " / ".join("%s %d" % (k, v)
                        for k, v in (su.get("inv_markets") or {}).items())
    c2 = _cell("入图事件", "%d 条 · %d 标的" % (
        su.get("doc_edges") or 0, su.get("sec_involved") or 0), inv_mk,
        warn=not su.get("doc_edges"))
    latest = su.get("latest_event_ts") or ""
    stale = ""
    if latest:
        ag = _ago(latest)
        stale = ag
        if ag.endswith("天前") and float(ag[:-2]) > 2:
            stale += " · ⚠ 超过48小时无新入图"
    c3 = _cell("最新入图", latest or "—", stale)
    st_m = (su.get("struct") or {}).get("mtime") or "—"
    c4 = _cell("影子管线", st_m, "结构 %d · 事件影子 %d · 原文 %d" % (
        (su.get("struct") or {}).get("count") or 0,
        (su.get("event_shadow") or {}).get("count") or 0,
        (su.get("texts") or {}).get("count") or 0))
    cad = su.get("cadence_min") or 5
    nx = _dt.now().replace(second=0, microsecond=0)
    nx += _td(minutes=(cad - nx.minute % cad) or cad)
    c5 = _cell("更新节奏", "摄取 %d 分钟/拍" % cad,
               "下次 ≈ %s · 入图 = 人审后 commit" % nx.strftime("%H:%M"))
    situ_html = ("<div class='situgrid'>%s%s%s%s%s</div>"
                 % (c1, c2, c3, c4, c5))

    # ── ② 市场 Tab（A股四板 + 美股/港股/加密预留灰置）──
    MKTS = ("全部", "主板", "创业板", "科创板", "北交所")

    def _href(m):
        parts = []
        if m != "全部":
            parts.append("market=" + _qt(m))
        for k, v in (("q", q), ("dir", direction), ("rel", rel),
                     ("d0", d0), ("d1", d1)):
            if v:
                parts.append(k + "=" + _qt(v))
        return "/kg/events" + ("?" + "&".join(parts) if parts else "")

    tabs = ("<div class='tabs'>"
            + "".join("<a class='tab%s' href='%s'>%s</a>"
                      % (" on" if market == m else "", _href(m), e(m))
                      for m in MKTS)
            + "".join("<span class='tab off' title='暂未接入 · 预留扩展位'>%s</span>"
                      % e(m) for m in ("美股", "港股", "加密"))
            + "</div>")

    # ── ③ 查询表单 ──
    rel_opts = ("<option value=''>关系</option>"
                + "".join("<option%s>%s</option>"
                          % (" selected" if rel == k else "", e(k))
                          for k in (su.get("rels") or {})))
    dir_opts = "".join("<option value=''>方向</option>%s" % "".join(
        "<option%s>%s</option>" % (" selected" if direction == k else "",
                                   e(k))
        for k in ("利好", "中性", "利空")))
    form = ("<form class='fbar' action='/kg/events' method='get' "
            "autocomplete='off'>"
            "<input type='hidden' name='market' value='%s'>"
            "<input id='qi' name='q' class='kfind' "
            "placeholder='公司 / 代码 / 别名（联想输入）…' value='%s'>"
            "<div id='hits' class='hits'></div>"
            "<select name='dir' class='fsel'>%s</select>"
            "<select name='rel' class='fsel'>%s</select>"
            "<input type='date' name='d0' class='fdt' value='%s'>"
            "<span class='dim'>至</span>"
            "<input type='date' name='d1' class='fdt' value='%s'>"
            "<button class='kfbtn'>🔍 查询</button>"
            "<a class='tab' href='/kg/events'>重置</a></form>"
            % (e(market), e(q), dir_opts, rel_opts, e(d0), e(d1)))

    # ── ④ 图例 ──
    LGC = (("主板", "#3fb950"), ("创业板", "#58a6ff"),
           ("科创板", "#bc8cff"), ("北交所", "#d29922"))
    legend = ("<div class='legend'>"
              + "".join("<span class='lg'><span class='lnd' "
                        "style='background:%s'></span>%s</span>" % (c, k)
                        for k, c in LGC)
              + "".join("<span class='lg'><span class='lsw' "
                        "style='background:%s'></span>%s</span>" % (c, k)
                        for k, c in (("利好", "#3fb950"),
                                     ("利空", "#f85149"),
                                     ("中性", "#8b949e")))
              + "<span>节点大小=事件连接度 · 线宽=报道数 n · "
                "点节点下钻公司 · 点边看原文</span></div>")

    gstat = ("%d 事件 · %d 标的" % (len(g.get("edges") or []),
                                   len(g.get("nodes") or []))
             if g.get("ok") else e((g.get("error") or "查询失败")[:60]))

    body = """<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>
<title>事件图谱 · K266</title>__CSS____NAV__<style>
.situgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:0 0 14px}
.sc{background:var(--gray-2);border:1px solid var(--gray-4);border-radius:10px;padding:10px 14px}
.sc.w{border-color:#4d3a13}
.sl{color:var(--gray-5);font-size:11px;letter-spacing:1px;margin-bottom:4px}
.sv{color:var(--gray-7);font-size:15px;font-weight:600}
.ss{color:var(--gray-5);font-size:11px;margin-top:3px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:1px}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px;align-items:center}
.tab{padding:5px 14px;border:1px solid var(--gray-4);border-radius:14px;color:var(--blu-500);text-decoration:none;font-size:12.5px;background:var(--gray-2)}
.tab.on{color:var(--gray-7);border-color:var(--blu-500);font-weight:600}
.tab.off{opacity:.35;cursor:not-allowed;color:var(--gray-5)}
.fbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 14px;position:relative}
.fsel,.fdt{padding:7px 10px;background:var(--gray-2);border:1px solid var(--gray-4);border-radius:8px;color:var(--gray-7);font-size:12.5px}
.hits{display:none;position:absolute;top:40px;left:0;background:var(--gray-2);border:1px solid var(--gray-4);border-radius:8px;z-index:50;min-width:320px;max-height:280px;overflow:auto}
.hit{padding:7px 12px;cursor:pointer;font-size:12.5px;border-bottom:1px solid var(--gray-3)}
.hit:hover{background:rgba(31,111,235,.2)}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px;color:var(--gray-5);font-size:11.5px;align-items:center}
.lg{display:inline-flex;align-items:center;gap:5px}
.lsw{width:14px;height:3px;border-radius:2px;display:inline-block}
.lnd{width:9px;height:9px;border-radius:50%;display:inline-block}
.evrow{padding:9px 12px;border:1px solid var(--gray-3);border-radius:9px;margin-bottom:7px;background:var(--gray-2)}
.co{color:var(--blu-500);font-weight:600;cursor:pointer}
.co:hover{text-decoration:underline}
.arrow{color:var(--gray-5);font-family:monospace}
.bdgx{display:inline-block;padding:1px 9px;border-radius:10px;font-size:11px;font-weight:600;margin-left:6px;background:var(--gray-2)}
.emeta{color:var(--gray-5);font-size:11.5px;margin-top:4px}
.emeta a{color:var(--ylw-500);text-decoration:none}
#evg{height:460px;border:1px solid var(--gray-4);border-radius:10px;background:var(--gray-0)}
#modal{position:fixed;inset:0;background:rgba(1,4,9,.78);z-index:100;display:none;align-items:center;justify-content:center;padding:30px}
.mbox{background:var(--gray-0);border:1px solid var(--gray-4);border-radius:12px;max-width:860px;width:100%;max-height:86vh;display:flex;flex-direction:column}
.mhead{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:12px 18px;border-bottom:1px solid var(--gray-3);font-weight:600;color:var(--gray-7)}
.mhead button{background:none;border:1px solid var(--gray-4);border-radius:7px;color:var(--gray-5);cursor:pointer;padding:3px 10px}
#mbody{padding:14px 18px;overflow:auto}
.mtags{margin-bottom:8px}
.mbdg{display:inline-block;padding:1px 9px;border-radius:10px;font-size:11px;font-weight:600;margin-right:5px;background:var(--gray-2)}
.mbdg.n{color:var(--gray-5);border:1px solid var(--gray-4)}
.mtag{display:inline-block;background:var(--gray-2);border:1px solid var(--gray-4);color:var(--gray-5);border-radius:10px;padding:1px 9px;font-size:11px;margin:2px 3px 2px 0}
.mwhy{color:var(--gray-6);font-size:12.5px;margin:4px 0}
.mev{color:var(--gray-6);font-size:12.5px;margin:3px 0;border-left:2px solid var(--gray-4);padding-left:10px}
.mmeta{color:var(--gray-5);font-size:11px;margin:8px 0}
.mtext{white-space:pre-wrap;word-break:break-all;color:var(--gray-6);font-size:12.5px;line-height:1.7;background:var(--gray-2);border:1px solid var(--gray-3);border-radius:8px;padding:12px;font-family:inherit;margin:0}
</style></head><body>
__SITU__
__TABS__
__FORM__
<div class='sec'><h2>事件图谱 <span class='dim'>__GSTAT__</span><span class='dim' style='float:right;font-weight:400'>拖拽平移 · 滚轮缩放 · 点节点下钻公司 · 点边看原文</span></h2>
<div id='evg'></div>__LEGEND__</div>
<div class='sec'><h2 id='lh'>事件列表</h2><div id='evlist'><div class='dim'>渲染中…</div></div></div>
<div id='modal' onclick='if(event.target===this)closeM()'><div class='mbox'>
<div class='mhead'><span id='mtitle'></span><button onclick='closeM()'>✕ 关闭</button></div>
<div id='mbody'></div></div></div>
<script src='/static/g6.min.js'></script>
<script>
const INIT=__INIT__;
const EBY={};INIT.edges.forEach(function(x){EBY[x.id]=x});
const NBY={};INIT.nodes.forEach(function(x){NBY[x.id]=x});
const MKTC={'主板':'#3fb950','创业板':'#58a6ff','科创板':'#bc8cff','北交所':'#d29922'};
const DIRC={'利好':'#3fb950','利空':'#f85149','中性':'#8b949e'};
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
var graph=null;
async function ensureG(){
 if(graph)return graph;
 graph=new G6.Graph({container:'evg',animation:false,
  data:{nodes:INIT.nodes,edges:INIT.edges},
  node:{style:{size:function(d){return 18+4*Math.min(4,d.data.deg||1)},
   fill:function(d){return MKTC[d.data.market]||'#8b949e'},
   stroke:'#e6edf3',lineWidth:1,
   labelText:function(d){return d.data.name},labelPlacement:'top',
   labelFontSize:11,labelFill:'#c9d1d9',labelBackground:true,
   labelBackgroundFill:'#161b22',labelBackgroundOpacity:.85,labelPadding:[1,4]}},
  edge:{style:{stroke:function(d){return DIRC[d.data.dir]||'#8b949e'},
   endArrow:true,
   lineWidth:function(d){return 1.5+1.2*Math.log2((d.data.n||1)+1)},
   labelText:function(d){return d.data.rel},labelFontSize:9,
   labelFill:'#8b949e',labelBackground:true,
   labelBackgroundFill:'#0d1117',labelBackgroundOpacity:.8}},
  layout:{type:'d3-force',link:{distance:150},charge:{strength:-420},
   collide:{radius:32}},
  behaviors:['drag-canvas','zoom-canvas','drag-element']});
 graph.on('node:click',function(ev){var id=ev.target&&ev.target.id;
  if(id)focusCo(id)});
 graph.on('edge:click',function(ev){var id=ev.target&&ev.target.id;
  var ed=EBY[id];
  if(ed&&ed.data&&ed.data.src_doc)openDoc(ed.data.src_doc)});
 await graph.render();return graph}
function dirBdg(d){var c=DIRC[d]||'#8b949e';
 return '<span class="bdgx" style="color:'+c+';border:1px solid '+c+'">'+esc(d)+'</span>'}
function nmOf(code){var n=NBY[code];return (n&&n.data&&n.data.name)?n.data.name:code}
function rowH(r){var d=r.data;
 return '<div class="evrow">'
  +'<span class="co" onclick="focusCo(\\''+esc(r.source)+'\\')">'+esc(nmOf(r.source))+'</span>'
  +' <span class="arrow">-['+esc(d.rel)+']-&gt;</span> '
  +'<span class="co" onclick="focusCo(\\''+esc(r.target)+'\\')">'+esc(nmOf(r.target))+'</span>'
  +dirBdg(d.dir)
  +'<div class="emeta">'+(d.date?esc(d.date)+' · ':'')+(d.amount?esc(d.amount)+' · ':'')
  +d.n+' 篇报道 · 判读 '+esc(d.ts_last)
  +' · <a href="javascript:void(0)" onclick="openDoc(\\''+esc(d.src_doc)+'\\')">📄 原文</a></div></div>'}
function renderRows(rows,title){
 document.getElementById('lh').innerHTML=title;
 var el=document.getElementById('evlist');
 el.innerHTML=rows.length?rows.map(rowH).join('')
  :'<div class="dim">暂无入图事件（影子事件需人审后 --commit 入图）</div>'}
function focusCo(code){
 var qs2=location.search?location.search.slice(1)+'&':'';
 fetch('/kg/events/list?'+qs2+'c='+encodeURIComponent(code))
  .then(function(r){return r.json()}).then(function(d){
  if(!d.ok){alert(d.error||'查询失败');return}
  d.rows.forEach(function(r){
   if(!NBY[r.ac])NBY[r.ac]={id:r.ac,data:{name:r.an,market:r.am,deg:0}};
   if(!NBY[r.bc])NBY[r.bc]={id:r.bc,data:{name:r.bn,market:r.bm,deg:0}}});
  var rows=d.rows.map(function(r){return {source:r.ac,target:r.bc,
   data:{rel:r.rel,dir:r.dir,n:r.n||1,date:r.date||'',
amount:r.amount||'',src_doc:r.src_doc||'',
ts_last:String(r.ts_last||'')}}});
  renderRows(rows,'事件 · <b>'+esc(nmOf(code))+'</b> <span class="dim">('+d.total+')</span>'
   +' · <a target="_blank" href="/kg?c='+encodeURIComponent(code)+'">图谱全景</a>'
   +' · <a target="_blank" href="/kg/stock/'+encodeURIComponent(code)+'">K线研判</a>'
   +' · <a href="javascript:location.reload()">← 返回全部</a>')})}
function openDoc(id){
 var m=document.getElementById('modal');m.style.display='flex';
 document.getElementById('mtitle').textContent='加载中…';
 document.getElementById('mbody').innerHTML='';
 fetch('/kg/events/doc/'+encodeURIComponent(id))
  .then(function(r){return r.json()}).then(function(d){
  var st=d.struct||{};
  document.getElementById('mtitle').textContent=
   (st.title_guess||d.name||d.doc_id||'').slice(0,70);
  var h='';
  if(st.direction||st.doc_type){h+='<div class="mtags">';
   if(st.direction){var c=DIRC[st.direction]||'#8b949e';
h+='<span class="mbdg" style="color:'+c+';border:1px solid '+c+'">'+esc(st.direction)+'</span>'}
   if(st.doc_type)h+='<span class="mbdg n">'+esc(st.doc_type)+'</span>';h+='</div>'}
  if(st.direction_why)h+='<div class="mwhy">判读：'+esc(st.direction_why)+'</div>';
  if((st.key_numbers||[]).length)h+='<div class="mwhy">关键数字：'
   +st.key_numbers.map(esc).join(' · ')+'</div>';
  (st.events||[]).forEach(function(x){h+='<div class="mev">'+esc(x.subject||'')
   +' <b>'+esc(x.rel||'')+'</b> '+esc(x.object||'')
   +(x.amount?' ['+esc(x.amount)+']':'')+(x.date?' ('+esc(x.date)+')':'')+'</div>'});
  if((st.tags||[]).length)h+='<div class="mtags">'+st.tags.map(function(t){
   return '<span class="mtag">'+esc(t)+'</span>'}).join('')+'</div>';
  h+='<div class="mmeta">源 '+(d.src||'-')+' · '+(d.kind||'-')+' · '+(d.ts||'-')
   +' · '+(d.text_chars||0)+' 字 · doc '+esc(d.doc_id)
   +(d.found_text?'':'（原文未命中）')+'</div>';
  h+='<pre class="mtext">'+esc(d.text||'（未找到原文）')+'</pre>';
  document.getElementById('mbody').innerHTML=h})
  .catch(function(err){document.getElementById('mtitle').textContent='加载失败';
   document.getElementById('mbody').textContent=String(err)})}
function closeM(){document.getElementById('modal').style.display='none'}
document.addEventListener('keydown',function(ev){if(ev.key==='Escape')closeM()});
/* 公司联想（250ms 防抖；点候选直接下钻该公司）*/
var tmr=null;
document.getElementById('qi').addEventListener('input',function(){
 clearTimeout(tmr);var v=this.value.trim();
 var hb=document.getElementById('hits');
 if(!v){hb.style.display='none';return}
 tmr=setTimeout(function(){
  fetch('/kg/events/search?q='+encodeURIComponent(v))
   .then(function(r){return r.json()}).then(function(d){
   hb.innerHTML=(d.hits||[]).map(function(h){
return '<div class="hit" data-c="'+esc(h.code)+'"><b>'+esc(h.name)
 +'</b> <span class="dim">'+esc(h.code)+' · '+esc(h.market)
 +(h.n_events?' · '+h.n_events+' 事件':'')+'</span></div>'}).join('')
||'<div class="hit">无匹配</div>';
   hb.style.display='block';
   hb.querySelectorAll('.hit').forEach(function(x){
x.addEventListener('click',function(){
 var c=this.getAttribute('data-c');hb.style.display='none';
 if(c)focusCo(c)})})})},250)});
renderRows(INIT.edges,'事件列表 <span class="dim">('+INIT.edges.length+')</span>');
ensureG().catch(function(err){
 document.getElementById('evg').innerHTML=
  '<div style="padding:20px;color:var(--red-500)">G6 渲染失败：'+esc(String(err)).slice(0,150)+'</div>'});
</script></body></html>""".replace("__CSS__", h._KG_CSS).replace(
        "__NAV__", pill_nav("kgevents")).replace(
        "__SITU__", situ_html).replace("__TABS__", tabs).replace(
        "__FORM__", form).replace("__GSTAT__", gstat).replace(
        "__LEGEND__", legend).replace(
        "__INIT__", _j.dumps(g, ensure_ascii=False))
    h._html(body)
