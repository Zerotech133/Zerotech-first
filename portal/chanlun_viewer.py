# -*- coding: utf-8 -*-
"""缠论查看器前端片段（2026-11-07 从 api_chanlun_pages.py 拆出）。

由 tools/check_portal_split.py 冻结行数回收产生；字符串内容与拆出前
保持一致，主文件仅通过 import 使用，路由/页面行为不变。
"""

VIEWER = (
    "<div style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px'>"
    "<input id='ck-code' placeholder='600519 / AAPL / 00700 / BTC/USDT' "
    "style='flex:1;min-width:200px;background:#0d1117;border:1px solid "
    "#30363d;border-radius:8px;color:#e6edf3;padding:8px 12px;font-size:14px'>"
    "<select id='ck-period' style='background:#0d1117;border:1px solid "
    "#30363d;border-radius:8px;color:#e6edf3;padding:8px;font-size:14px'>"
    "<option value='1d'>日线</option><option value='60m'>60分钟</option>"
    "<option value='30m'>30分钟</option></select>"
    "<select id='ck-last' title='尾部K线根数' style='background:#0d1117;"
    "border:1px solid #30363d;border-radius:8px;color:#e6edf3;padding:8px;"
    "font-size:14px'><option value='120'>最近120根</option>"
    "<option value='240'>最近240根</option>"
    "<option value='500' selected>最近500根</option>"
    "<option value='0'>全部</option></select>"
    "<button onclick='ckLoad()' style='background:#238636;color:#fff;"
    "border:0;border-radius:8px;padding:8px 18px;font-size:14px;"
    "cursor:pointer'>加载 K 线</button></div>"
    "<div id='ck-meta' class='dim' style='font-size:13px;min-height:18px'>"
    "输入代码加载 · 图层可开关 · 悬停看数值</div>"
    "<div id='ck-toggles' style='display:flex;gap:6px;flex-wrap:wrap;"
    "margin:8px 0'></div>"
    "<div id='ck-chart' style='background:#0d1117;border:1px solid "
    "#30363d;border-radius:10px;padding:6px'></div>"
    "<div id='ck-heat' style='margin-top:12px'></div>"
    "<div id='ck-sigcards' style='margin-top:12px'></div>"
    "<div id='ck-bt' style='margin-top:12px'></div>"
    "<div id='ck-sigs' style='margin-top:12px'></div>")

JS = """<script>
var CK={layers:{bars:1,vol:1,fr:1,bi:1,seg:1,pv:1,sig:1},data:null,bt:null};
function $(id){return document.getElementById(id)}
function ckToggle(k,btn){
 CK.layers[k]=!CK.layers[k];btn.style.opacity=CK.layers[k]?1:.35;
 if(CK.data)ckDraw(CK.data);
}
function ckInitToggles(){
 var defs=[['bars','K线',1],['vol','成交量',1],['fr','分型',1],['bi','笔',1],
           ['seg','线段',1],['pv','中枢',1],['sig','买卖点',1]];
 var el=$('ck-toggles');el.innerHTML='';
 defs.forEach(function(d){
  var b=document.createElement('button');
  b.textContent=d[1];
  b.style.cssText='background:#21262d;color:#e6edf3;border:1px solid #30363d;'
   +'border-radius:14px;padding:2px 12px;font-size:12px;cursor:pointer;opacity:'
   +(d[2]?1:.35);
  b.onclick=function(){ckToggle(d[0],b)};
  el.appendChild(b);
 });
}
function ckLoad(){
 var c=$('ck-code').value.trim(),p=$('ck-period').value,lastN=parseInt($('ck-last').value,10)||0;
 var m=$('ck-meta'),el=$('ck-chart');
 if(!c){m.innerHTML="<span style='color:#d29922'>请先输入代码</span>";el.innerHTML='';return}
 m.textContent='加载中…';el.innerHTML='';
 fetch('/api/chanlun/structure?code='+encodeURIComponent(c)+'&period='+p)
 .then(function(r){return r.json()})
 .then(function(j){
  if(!j.ok){m.innerHTML="<span style='color:#f85149'>"+j.error+"</span>";return}
  if(lastN>0&&j.bars.length>lastN){
   var cut=j.bars[j.bars.length-lastN].dt;
   j.bars=j.bars.slice(-lastN);
   var inR=function(d){return d>=cut};
   j.fractals=(j.fractals||[]).filter(function(f){return inR(f.dt)});
   j.bis=(j.bis||[]).filter(function(b){return inR(b.e_dt)});
   j.segments=(j.segments||[]).filter(function(s){return inR(s.e_dt)});
   j.pivots=(j.pivots||[]).filter(function(v){return inR(v.e_dt)});
  }
  CK.data=j;
  var b=j.bars,frst=b[0],last=b[b.length-1];
  var nt=0,nb=0;(j.fractals||[]).forEach(function(f){f.type=='顶'?nt++:nb++});
  m.innerHTML='来源 '+j.source+' · '+j.market+' '+j.code+' · '+b.length+' 根 · '
   +frst.dt+' → '+last.dt+' · 最新收盘 <b style="color:#e6edf3">'+last.c+'</b>'
   +' · <span style="color:#3fb950">顶 '+nt+'</span>/<span style="color:#f85149">底 '+nb+'</span>'
   +'（合并后 '+j.merged_n+' 根）'
   +' · <span style="color:#e3b341">笔 '+(j.bis||[]).length+'</span>'
   +' · <span style="color:#58a6ff">段 '+(j.segments||[]).length+'</span>'
   +' · <span style="color:#a371f7">枢 '+(j.pivots||[]).length+'</span>'
   +' · <span style="color:#3fb950">信号 '+(j.signals||[]).length+'</span>';
  ckDraw(j);ckHeat();ckSigCards(j);ckSigs(j);
  var pl=$('ck-png');
  if(pl)pl.href='/chanlun-png?c='+encodeURIComponent(j.code)+'&p='+j.period;
  fetch('/api/chanlun/backtest?code='+encodeURIComponent(c)+'&period='+p)
  .then(function(r){return r.json()}).then(function(bt){
   CK.bt=bt;var el2=$('ck-bt');
   if(!bt.ok){el2.innerHTML="<span style='color:#f85149'>验证失败："+bt.error+"</span>";return}
   ckBtTable(bt);
  }).catch(function(){});
 }).catch(function(){m.innerHTML="<span style='color:#f85149'>请求失败，请重试</span>"});
}
function ckDraw(j){
 var b=j.bars,n=b.length;
 var W=1160,H=460,HV=90,PL=8,PR=64,PT=10,PB=24,GAP=6;
 var HP=PT+HV+GAP,HC=H-HP-PB;
 var el=$('ck-chart');
 var hi=Math.max.apply(null,b.map(function(x){return x.h}));
 var lo=Math.min.apply(null,b.map(function(x){return x.l}));
 var rng=(hi-lo)||1, pad=rng*0.04; hi+=pad; lo-=pad; rng=hi-lo;
 var mv=Math.max.apply(null,b.map(function(x){return x.v}))||1;
 function X(i){return PL+(i+0.5)*(W-PL-PR)/n}
 function Y(v){return PT+(hi-v)/rng*HC}
 function YV(v){return HP+HV-(v/mv)*HV}
 var cw=Math.max(1.2,(W-PL-PR)/n*0.62),g='',i;
 for(var k=0;k<=5;k++){
  var v=lo+rng*k/5,yy=Y(v);
  g+='<line x1="'+PL+'" x2="'+(W-PR)+'" y1="'+yy+'" y2="'+yy+'" stroke="#21262d"/>'
   +'<text x="'+(W-PR+6)+'" y="'+(yy+4)+'" fill="#8b949e" font-size="10">'
   +(v>=100?v.toFixed(0):v.toFixed(2))+'</text>';}
 if(CK.layers.vol){
  g+='<line x1="'+PL+'" x2="'+(W-PR)+'" y1="'+(HP+HV)+'" y2="'+(HP+HV)+'" stroke="#21262d"/>';
  for(i=0;i<n;i++){var vv2=b[i],up2=vv2.c>=vv2.o,vc=up2?'#da6b6b':'#4a9e70';
   g+='<rect x="'+(X(i)-cw/2)+'" y="'+YV(vv2.v)+'" width="'+cw+'" height="'
    +Math.max(.6,HV-(vv2.v/mv)*HV)+'" fill="'+vc+'" opacity=".55"/>';}
  g+='<text x="'+PL+'" y="'+(HP+10)+'" fill="#8b949e" font-size="9">量</text>';
 }
 if(CK.layers.bars){
  for(i=0;i<n;i++){var bb=b[i],up=bb.c>=bb.o,col=up?'#f85149':'#3fb950';
   var x1=X(i),yo=Y(bb.o),yc=Y(bb.c);
   g+='<line x1="'+x1+'" x2="'+x1+'" y1="'+Y(bb.h)+'" y2="'+Y(bb.l)+'" stroke="'+col+'"/>'
    +'<rect x="'+(x1-cw/2)+'" y="'+Math.min(yo,yc)+'" width="'+cw+'" height="'
    +Math.max(1,Math.abs(yc-yo))+'" fill="'+col+'"><title>'+bb.dt+' O'+bb.o+' H'+bb.h
    +' L'+bb.l+' C'+bb.c+' V'+bb.v+'</title></rect>';}
 }
 var idx=[];var step=Math.max(1,Math.floor(n/6));
 for(i=0;i<n;i+=step){idx.push(i)}
 if(idx[idx.length-1]!==n-1)idx.push(n-1);
 idx.forEach(function(i2){
  g+='<text x="'+X(i2)+'" y="'+(H-6)+'" fill="#8b949e" font-size="10" text-anchor="middle">'
   +b[i2].dt.slice(5,10)+'</text>';});
 var map={};for(i=0;i<n;i++){map[b[i].dt]=i}
 if(CK.layers.pv){
  (j.pivots||[]).forEach(function(pv){
   var xi=map[pv.s_dt],xo=map[pv.e_dt];
   if(xi===undefined||xo===undefined)return;
   g+='<rect x="'+(X(xi)-cw/2)+'" y="'+Y(pv.zg)+'" width="'+Math.max(1,X(xo)-X(xi))
    +'" height="'+Math.max(1,Y(pv.zd)-Y(pv.zg))
    +'" fill="rgba(163,113,247,0.10)" stroke="#a371f7" stroke-dasharray="4 3">'
    +'<title>中枢 '+pv.s_dt+'~'+pv.e_dt+' ZG '+pv.zg+' ZD '+pv.zd+'（'+pv.n_bi+' 笔）</title></rect>';
   g+='<text x="'+(X(xi)+2)+'" y="'+(Y(pv.zg)-3)+'" fill="#a371f7" font-size="9">ZG '+pv.zg+'</text>'
    +'<text x="'+(X(xi)+2)+'" y="'+(Y(pv.zd)+10)+'" fill="#a371f7" font-size="9">ZD '+pv.zd+'</text>';
  });
 }
 if(CK.layers.seg){
  (j.segments||[]).forEach(function(sg){
   var xi=map[sg.s_dt],xo=map[sg.e_dt];
   if(xi===undefined||xo===undefined)return;
   g+='<line x1="'+X(xi)+'" y1="'+Y(sg.s_price)+'" x2="'+X(xo)+'" y2="'+Y(sg.e_price)
    +'" stroke="#58a6ff" stroke-width="2.5" stroke-linecap="round">'
    +'<title>线段 '+(sg.dir>0?'↑':'↓')+' '+sg.s_dt+' '+sg.s_price+' → '+sg.e_dt+' '+sg.e_price
    +'（'+sg.n_bi+' 笔）</title></line>';
  });
 }
 if(CK.layers.bi){
  (j.bis||[]).forEach(function(bi){
   var xi=map[bi.s_dt],xo=map[bi.e_dt];
   if(xi===undefined||xo===undefined)return;
   g+='<line x1="'+X(xi)+'" y1="'+Y(bi.s_price)+'" x2="'+X(xo)+'" y2="'+Y(bi.e_price)
    +'" stroke="#e3b341" stroke-width="1.2">'
    +'<title>笔 '+(bi.dir>0?'↑':'↓')+' '+bi.s_dt+' '+bi.s_price+' → '+bi.e_dt+' '+bi.e_price+'</title></line>';
  });
 }
 if(CK.layers.fr){
  (j.fractals||[]).forEach(function(f){
   var i2=map[f.dt];if(i2===undefined)return;
   var y2=f.type=='顶'?Y(f.price)-6:Y(f.price)+12;
   g+='<text x="'+X(i2)+'" y="'+y2+'" font-size="10" text-anchor="middle" fill="'
    +(f.type=='顶'?'#3fb950':'#f85149')+'">'+(f.type=='顶'?'▼':'▲')
    +'<title>'+f.type+'分型 '+f.dt+' '+f.price+'</title></text>';
  });
 }
 if(CK.layers.sig){
  (j.signals||[]).forEach(function(sg2){
   var i2=map[sg2.dt];if(i2===undefined)return;
   var buy=sg2.type.indexOf('买')>=0;
   var cy=buy?Y(sg2.price)+14:Y(sg2.price)-8;
   g+='<circle cx="'+X(i2)+'" cy="'+cy+'" r="3.2" fill="'+(buy?'#f85149':'#3fb950')+'">'
    +'<title>'+sg2.type+' '+sg2.dt+' @'+sg2.price+'\\n'+(sg2.note||'')+'</title></circle>';
   g+='<text x="'+X(i2)+'" y="'+(buy?cy+12:cy-6)+'" font-size="11" font-weight="bold"'
    +' text-anchor="middle" fill="'+(buy?'#f85149':'#3fb950')+'">'+sg2.type+'</text>';
  });
 }
 el.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto;display:block">'+g+'</svg>';
}
function ckBtTable(bt){
 var el=$('ck-bt'),st=bt.stats||{},ev=bt.events||[],ty=bt.by_type||{};
 var rows='',k;
 for(k in ty){rows+='<span style="margin-right:12px">'+k+' n='+ty[k].n
  +' · 胜率'+ty[k].win+'% · 均值'+ty[k].avg+'%</span>'}
 var evr='';
 if(ev.length){
  evr='<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:6px">'
   +'<tr style="color:#8b949e"><th style="text-align:left">类型</th><th>信号日</th>'
   +'<th>信号价</th><th>+5日</th><th>+10日</th><th>+20日</th></tr>';
  ev.forEach(function(x){
   var f=function(v){return v==null?'—':(v>=0?'+':'')+(v*100).toFixed(1)+'%'};
   var c=function(v){return v==null?'#8b949e':(v>=0?'#f85149':'#3fb950')};
   evr+='<tr><td><b>'+x.type+'</b></td><td style="text-align:right">'+x.dt
    +'</td><td style="text-align:right">'+x.price+'</td>'
    +'<td style="text-align:right;color:'+c(x.r5)+'">'+f(x.r5)+'</td>'
    +'<td style="text-align:right;color:'+c(x.r10)+'">'+f(x.r10)+'</td>'
    +'<td style="text-align:right;color:'+c(x.r20)+'">'+f(x.r20)+'</td></tr>';
  });
  evr+='</table>';
 }
 el.innerHTML='<b style="color:#e6edf3">📈 信号事件复盘</b>（理论价口径 · 纸面参考）· '
  +bt.n_signals+' 个信号 · 策略 '+(st.trades||0)+' 笔 · 胜率 '
  +(st.win_rate==null?'—':st.win_rate+'%')+' · 累计 '
  +(st.total_return==null?'—':st.total_return+'%')+' · 回撤 '+(st.max_dd||0)
  +'% · 买入持有 '+(st.buy_hold==null?'—':st.buy_hold+'%')
  +(st.open_pos?' · <span style="color:#d29922">期末持仓未平</span>':'')
  +(rows?'<br><span style="font-size:12px">'+rows+'</span>':'')
  +evr;
}
function ckHeat(){
 var el=$('ck-heat');
 fetch('/api/chanlun/pool/heat')
 .then(function(r){return r.ok?r.json():null})
 .then(function(d){
  if(!d||!d.ok){el.innerHTML='';return}
  var types=['1买','2买','3买','1卖','2卖','3卖'],mx=1;
  types.forEach(function(t){if((d.counts[t]||0)>mx)mx=d.counts[t]});
  var cells='';
  types.forEach(function(t){
   var v=d.counts[t]||0,w=v/mx*100,buy=t.indexOf('买')>=0;
   cells+='<div style="display:grid;grid-template-columns:36px 1fr 44px;gap:6px;'
    +'align-items:center;font-size:11px;font-family:monospace;margin:2px 0">'
    +'<span>'+t+'</span><span style="height:12px;background:#161b22;'
    +'border-radius:3px;position:relative;overflow:hidden"><span style="position:'
    +'absolute;left:0;top:0;bottom:0;width:'+w+'%;background:'
    +(buy?'#f85149':'#3fb950')+'"></span></span>'
    +'<span style="text-align:right">'+v+'</span></div>';
  });
  el.innerHTML='<b style="font-size:13px;color:#e6edf3">🔥 市场信号热度</b>'
   +'<span class="dim" style="font-size:11.5px;margin-left:8px">'+d.detect_date
   +' 扫描检出 · 全市场六类买卖点</span>'
   +'<div style="max-width:420px;margin-top:4px">'+cells+'</div>';
 }).catch(function(){el.innerHTML=''});
}
function ckSigCards(j){
 var el=$('ck-sigcards'),sigs=j.signals||[];
 if(!sigs.length){el.innerHTML="<div class='dim' style='font-size:12.5px'>"
  +"本票当前无检出信号（结构已出，可切周期复核）。</div>";return}
 var cards='';
 sigs.forEach(function(s){
  var buy=s.type.indexOf('买')>=0;
  var badges='';
  if(s.mbei===true)badges+='<span style="color:#bc8cff">M✓MACD背驰</span> ';
  if(s.tbe===true)badges+='<span style="color:#f778ba">趋✓双中枢</span> ';
  if(s.vv===true)badges+='<span style="color:#f0883e">量✓缩量</span> ';
  if(s.mt===true)badges+='<span style="color:#2dd4bf">次✓共振</span> ';
  if(s.pt)badges+='<span style="color:#d29922">K线·'+(s.pt_name||'形态')+'</span> ';
  var detail='';
  if(s.mfv)detail+='<div style="font-size:10.5px;color:#8b949e">MACD 快'
   +s.mfv[0].toFixed(2)+' 慢'+s.mfv[1].toFixed(2)+' 柱'
   +s.mfv[2].toFixed(2)+'/'+s.mfv[3].toFixed(2)+'</div>';
  if(s.vv_ev)detail+='<div style="font-size:10.5px;color:#8b949e">量能 前期'
   +Math.round(s.vv_ev[0])+' vs 信号'+Math.round(s.vv_ev[1])+'</div>';
  cards+='<div style="border:1px solid #30363d;border-left:3px solid '
   +(buy?'#f85149':'#3fb950')+';border-radius:8px;padding:10px 14px;'
   +'margin-bottom:8px;background:#0d1117">'
   +'<div style="display:flex;gap:14px;align-items:baseline;flex-wrap:wrap">'
   +'<b style="color:'+(buy?'#f85149':'#3fb950')+';font-size:15px">'+s.type+'</b>'
   +'<span style="font-family:monospace">'+s.dt+'</span>'
   +'<span style="font-family:monospace">@ '+(s.price==null?'—':s.price)+'</span>'
   +'<span class="dim" style="font-size:12px">'+(s.note||'')+'</span></div>'
   +(badges?'<div style="margin-top:4px;font-size:12px">'+badges+'</div>':'')
   +detail+'</div>';
 });
 el.innerHTML='<b style="font-size:13.5px;color:#e6edf3">🎯 检出信号 · 影子证据卡</b>'
  +'<div style="margin-top:6px">'+cards+'</div>';
}
function ckSigs(j){
 var el=$('ck-sigs');
 var sigs=j.signals||[],b=j.bars||[];
 var lastdt=b.length?b[b.length-1].dt:'';
 if(j.period!=='1d'){
  el.innerHTML="<div class='dim' style='font-size:12.5px'>⇪ 推池仅支持日线信号；当前 "
   +j.period+" 仅作结构观察。</div>";return}
 if(j.market!=='CN'&&j.market!=='US'&&j.market!=='HK'){
  el.innerHTML="<div class='dim' style='font-size:12.5px'>⇪ 推池支持 CN/US/HK 日线"
   +"（独立市场池链）；当前 "+j.market+" 仅作结构观察。</div>";return}
 if(!sigs.length){el.innerHTML='';return}
 var rows='',i;
 for(i=0;i<sigs.length;i++){var s=sigs[i];
  var buy=s.type.indexOf('买')>=0,old=(s.dt!==lastdt);
  rows+='<tr><td><b style="color:'+(buy?'#3fb950':'#f85149')+'">'+s.type+'</b></td>'
   +'<td>'+s.dt+'</td><td>'+(s.price==null?'—':s.price)+'</td>'
   +'<td>'+(old?"<span style='color:#e3b341'>⚠ 旧信号</span>"
     :'<span style="color:#3fb950">最新</span>')+'</td>'
   +'<td><button id="ckpb_'+i+'" onclick="ckPush('+i+')" style="background:#238636;'
   +'color:#fff;border:0;border-radius:6px;padding:2px 10px;cursor:pointer;'
   +'font-size:12px">⇪ 推池</button></td></tr>';}
 el.innerHTML='<div style="font-size:13.5px;margin-bottom:4px">'
  +'<b style="color:#e6edf3">信号入池</b>（source=manual · 三层幂等）</div>'
  +'<table><tr><th>类型</th><th>信号日</th><th>信号价</th><th>时效</th>'
  +'<th>入池</th></tr>'+rows+'</table>';
 window._ckSig=sigs;window._ckCode=j.code;window._ckMkt=j.market;
}
function ckPush(i){
 var s=(window._ckSig||[])[i];if(!s)return;
 var btn=$('ckpb_'+i);
 btn.disabled=true;btn.textContent='推池中…';
 fetch('/api/chanlun/pool/push',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({code:window._ckCode,type:s.type,dt:s.dt,market:window._ckMkt})})
 .then(function(r){return r.json()})
 .then(function(j2){
  if(j2.ok){
   var hit=(j2.pushed||[]).some(function(x){return x.type===s.type&&x.dt===s.dt});
   btn.textContent=hit?'已入池 ✓':'已在池';
   btn.style.background='#30363d';
  }else{btn.textContent='失败';btn.style.background='#b35900';
   btn.title=(j2.error||'').slice(0,80);btn.disabled=false;}
 }).catch(function(){btn.textContent='重试';btn.disabled=false;});
}
ckInitToggles();
</script>"""
