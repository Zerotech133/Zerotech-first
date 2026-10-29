# -*- coding: utf-8 -*-
"""KG 页面内嵌 JS 常量（拆分收尾，2026-10-30 自 api_kg.py 迁出）。

纯静态字符串数据模块：无 IO、无依赖、供 kg_pages_misc 渲染时拼接。
迁出目的：api_kg.py 收尾为「路由委托 + helper」，去掉 300 行内嵌 JS；
契约守卫：portal/kg/* 不得反向 import api_kg（见 test_api_kg_contract）。
"""

_KG_LABEL_JS = r"""'use strict';
const cv=document.getElementById('cv'),ctx=cv.getContext('2d');
const stEl=document.getElementById('st'),tipEl=document.getElementById('tip');
const UP='#f85149',DN='#3fb950',BI='#e8a33d',PV='#7048e8';
const PAD_L=8,PAD_R=64,PAD_T=14,PAD_B=26;
const S={bars:[],dtI:{},n:0,code:'',period:'30m',last_dt:'',
 algo:{bis:[],pivots:[],bs:[]},user:{bis:[],pivots:[],bs:[]},
 cw:7,off:0,tool:'select',bsType:null,sel:null,undoS:[],
 drag:null,pend:null,mouse:null,ma20:[],ma60:[],W:0,H:660};
function st(t){stEl.textContent=t;}
function tip(t){tipEl.textContent=t;}
function snap(){S.undoS.push(JSON.stringify(S.user));if(S.undoS.length>60)S.undoS.shift();}
function undo(){const u=S.undoS.pop();if(u){S.user=JSON.parse(u);S.sel=null;draw();st('已撤销');}else st('无可撤销');}
function delSel(){if(!S.sel){st('无选中');return;}snap();
 const k=S.sel.kind,i=S.sel.i;
 if(k==='bi'||k==='bend')S.user.bis.splice(i,1);
 else if(k==='pivot'||k==='pvz')S.user.pivots.splice(i,1);
 else if(k==='bs')S.user.bs.splice(i,1);
 S.sel=null;draw();st('已删除');}
function xOf(i){return PAD_L+(i-S.off)*S.cw;}
function iOf(x){return S.off+(x-PAD_L)/S.cw;}
function iClamp(x){return Math.max(0,Math.min(S.n-1,Math.round(iOf(x))));}
function yDom(){let lo=1e18,hi=-1e18;
 const i0=Math.max(0,Math.floor(iOf(PAD_L))),i1=Math.min(S.n-1,Math.ceil(iOf(S.W-PAD_R)));
 for(let i=i0;i<=i1;i++){const b=S.bars[i];if(b.l<lo)lo=b.l;if(b.h>hi)hi=b.h;}
 if(lo>hi){lo=0;hi=1;}
 const p=(hi-lo)*0.06||1;return[lo-p,hi+p];}
function yOf(p,d){return PAD_T+(d[1]-p)/(d[1]-d[0])*(S.H-PAD_T-PAD_B);}
function pOf(y,d){return d[1]-(y-PAD_T)/(S.H-PAD_T-PAD_B)*(d[1]-d[0]);}
function fd(dt){dt=String(dt);return dt.length>10?dt.slice(5,16):dt.slice(5);}
function distSeg(px,py,x0,y0,x1,y1){const dx=x1-x0,dy=y1-y0;const L2=dx*dx+dy*dy||1;
 let t=((px-x0)*dx+(py-y0)*dy)/L2;t=Math.max(0,Math.min(1,t));
 return Math.hypot(px-x0-t*dx,py-y0-t*dy);}
function hit(mx,my){const d=yDom();
 for(let i=S.user.bs.length-1;i>=0;i--){const s=S.user.bs[i];
  const x=xOf(S.dtI[s.dt]||0),y=yOf(s.price,d);
  if(Math.abs(x-mx)<=8&&Math.abs(y-my)<=10)return{kind:'bs',i};}
 for(let i=S.user.bis.length-1;i>=0;i--){const b=S.user.bis[i];
  const x0=xOf(S.dtI[b.s_dt]||0),y0=yOf(b.s_price,d),
        x1=xOf(S.dtI[b.e_dt]||S.n-1),y1=yOf(b.e_price,d);
  if(Math.hypot(x0-mx,y0-my)<=7)return{kind:'bend',i,which:'s'};
  if(Math.hypot(x1-mx,y1-my)<=7)return{kind:'bend',i,which:'e'};}
 for(let i=S.user.pivots.length-1;i>=0;i--){const p=S.user.pivots[i];
  const x0=xOf(S.dtI[p.s_dt]||0),x1=xOf(S.dtI[p.e_dt]||S.n-1),
        yg=yOf(p.zg,d),yd=yOf(p.zd,d);
  if(mx>=x0-4&&mx<=x1+4&&my>=Math.min(yg,yd)-6&&my<=Math.max(yg,yd)+6){
   if(Math.abs(my-yg)<=5)return{kind:'pvz',i,f:'zg'};
   if(Math.abs(my-yd)<=5)return{kind:'pvz',i,f:'zd'};}
  if(mx>=x0&&mx<=x1&&my>yg&&my<yd)return{kind:'pivot',i};}
 for(let i=S.user.bis.length-1;i>=0;i--){const b=S.user.bis[i];
  const x0=xOf(S.dtI[b.s_dt]||0),y0=yOf(b.s_price,d),
        x1=xOf(S.dtI[b.e_dt]||S.n-1),y1=yOf(b.e_price,d);
  if(distSeg(mx,my,x0,y0,x1,y1)<=5)return{kind:'bi',i};}
 return null;}
function draw(){ctx.clearRect(0,0,S.W,S.H);
 if(!S.n){ctx.fillStyle='#8b949e';ctx.font='13px sans-serif';
  ctx.fillText('输入代码后点「加载」',20,40);return;}
 const d=yDom();
 for(let k=0;k<=5;k++){const p=d[0]+(d[1]-d[0])*k/5,y=yOf(p,d);
  ctx.strokeStyle='rgba(139,148,158,0.12)';ctx.beginPath();
  ctx.moveTo(PAD_L,y);ctx.lineTo(S.W-PAD_R,y);ctx.stroke();
  ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';
  ctx.fillText(p.toFixed(2),S.W-PAD_R+4,y+3);}
 const bw=Math.max(1,S.cw*0.62);
 for(let i=0;i<S.n;i++){const x=xOf(i);if(x<PAD_L-2||x>S.W-PAD_R+2)continue;
  const b=S.bars[i],c=b.c>=b.o?UP:DN;
  ctx.strokeStyle=c;ctx.fillStyle=c;ctx.lineWidth=1;
  ctx.beginPath();ctx.moveTo(x,yOf(b.h,d));ctx.lineTo(x,yOf(b.l,d));ctx.stroke();
  const y1=yOf(Math.max(b.o,b.c),d),y2=yOf(Math.min(b.o,b.c),d);
  ctx.fillRect(x-bw/2,y1,bw,Math.max(1,y2-y1));}
 ctx.lineWidth=1;
 if(S.ma20.length===S.n){ctx.strokeStyle='#f2b544';ctx.beginPath();let m0=1;
  for(let i=19;i<S.n;i++){const x=xOf(i),y=yOf(S.ma20[i],d);
   if(m0){ctx.moveTo(x,y);m0=0;}else ctx.lineTo(x,y);}ctx.stroke();}
 if(S.ma60.length===S.n){ctx.strokeStyle='#4dabf7';ctx.beginPath();let m0=1;
  for(let i=59;i<S.n;i++){const x=xOf(i),y=yOf(S.ma60[i],d);
   if(m0){ctx.moveTo(x,y);m0=0;}else ctx.lineTo(x,y);}ctx.stroke();}
 for(let i=0;i<S.user.pivots.length;i++){const p=S.user.pivots[i];
  const x0=xOf(S.dtI[p.s_dt]||0),x1=xOf(S.dtI[p.e_dt]||S.n-1),
        y0=yOf(p.zg,d),y1=yOf(p.zd,d);
  ctx.fillStyle='rgba(112,72,232,0.14)';ctx.fillRect(x0,y0,x1-x0,y1-y0);
  ctx.strokeStyle=PV;ctx.lineWidth=1;ctx.strokeRect(x0,y0,x1-x0,y1-y0);
  ctx.fillStyle=PV;ctx.font='10px sans-serif';
  ctx.fillText(p.zg.toFixed(2),Math.min(x1,S.W-PAD_R-34),y0+4);
  ctx.fillText(p.zd.toFixed(2),Math.min(x1,S.W-PAD_R-34),y1+4);
  if(S.sel&&(S.sel.i===i&&(S.sel.kind==='pivot'||S.sel.kind==='pvz'))){
   ctx.strokeStyle='#fff';ctx.lineWidth=1.5;
   ctx.strokeRect(x0-2,y0-2,x1-x0+4,y1-y0+4);}}
 ctx.strokeStyle=BI;ctx.lineWidth=1.6;
 for(let i=0;i<S.user.bis.length;i++){const b=S.user.bis[i];
  const x0=xOf(S.dtI[b.s_dt]||0),y0=yOf(b.s_price,d),
        x1=xOf(S.dtI[b.e_dt]||S.n-1),y1=yOf(b.e_price,d);
  ctx.beginPath();ctx.moveTo(x0,y0);ctx.lineTo(x1,y1);ctx.stroke();
  ctx.fillStyle=BI;
  ctx.fillRect(x0-2.5,y0-2.5,5,5);ctx.fillRect(x1-2.5,y1-2.5,5,5);
  if(S.sel&&S.sel.kind==='bi'&&S.sel.i===i){
   ctx.strokeStyle='#fff';ctx.lineWidth=2.5;
   ctx.beginPath();ctx.moveTo(x0,y0);ctx.lineTo(x1,y1);ctx.stroke();}}
 ctx.font='11px sans-serif';
 for(let i=0;i<S.user.bs.length;i++){const s=S.user.bs[i];
  const x=xOf(S.dtI[s.dt]||0),y=yOf(s.price,d),buy=s.type.indexOf('买')>=0,c=buy?UP:DN;
  ctx.fillStyle=c;ctx.beginPath();
  if(buy){ctx.moveTo(x,y-5);ctx.lineTo(x-6,y+6);ctx.lineTo(x+6,y+6);}
  else{ctx.moveTo(x,y+5);ctx.lineTo(x-6,y-6);ctx.lineTo(x+6,y-6);}
  ctx.closePath();ctx.fill();
  ctx.fillText(s.type,x+8,buy?y+6:y-4);
  if(S.sel&&S.sel.kind==='bs'&&S.sel.i===i){
   ctx.strokeStyle='#fff';ctx.lineWidth=1.5;
   ctx.beginPath();ctx.arc(x,y,10,0,7);ctx.stroke();}}
 if(S.pend&&S.mouse){const d2=yDom();
  ctx.strokeStyle='#fff';ctx.setLineDash([4,3]);ctx.beginPath();
  ctx.moveTo(xOf(S.pend.i),yOf(S.pend.p,d2));
  ctx.lineTo(S.mouse.x,yOf(pOf(S.mouse.y,d2),d2));ctx.stroke();ctx.setLineDash([]);}
 if(S.drag&&S.drag.kind==='newpv'&&S.mouse){
  ctx.strokeStyle='#fff';ctx.setLineDash([4,3]);ctx.strokeRect(
   Math.min(S.drag.x,S.mouse.x),Math.min(S.drag.y,S.mouse.y),
   Math.abs(S.mouse.x-S.drag.x),Math.abs(S.mouse.y-S.drag.y));ctx.setLineDash([]);}
 ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';
 const stepK=Math.max(1,Math.round(90/S.cw));
 for(let i=0;i<S.n;i+=stepK){const x=xOf(i);
  if(x>PAD_L&&x<S.W-PAD_R)ctx.fillText(fd(S.bars[i].dt),x-24,S.H-8);}
 ctx.font='11px sans-serif';
 ctx.fillText(S.code+' '+S.period+' · '+S.n+'根 · 笔'+S.user.bis.length+
  ' 枢'+S.user.pivots.length+' 点'+S.user.bs.length+
  ' · MA20黄 MA60蓝 红买绿卖',PAD_L+4,PAD_T+4);}
function resize(){const dpr=window.devicePixelRatio||1;
 S.W=document.getElementById('wrap').clientWidth;S.H=660;
 cv.width=S.W*dpr;cv.height=S.H*dpr;cv.style.height=S.H+'px';
 ctx.setTransform(dpr,0,0,dpr,0,0);draw();}
window.onresize=resize;
function hi(){document.querySelectorAll('.tbtn[data-tool],.tbtn[data-bs]')
 .forEach(el=>{const on=(el.dataset.tool&&el.dataset.tool===S.tool&&!S.bsType)||
  (el.dataset.bs&&el.dataset.bs===S.bsType);el.classList.toggle('on',on);});}
document.querySelectorAll('[data-tool]').forEach(el=>el.onclick=()=>{
 S.tool=el.dataset.tool;S.bsType=null;S.pend=null;hi();});
document.querySelectorAll('[data-bs]').forEach(el=>el.onclick=()=>{
 S.tool='bs';S.bsType=el.dataset.bs;S.pend=null;hi();});
cv.addEventListener('wheel',e=>{e.preventDefault();
 const r=cv.getBoundingClientRect(),mx=e.clientX-r.left;
 const iAt=iOf(mx);
 S.cw=Math.max(2,Math.min(18,S.cw*(e.deltaY<0?1.15:0.87)));
 S.off=Math.round(iAt-(mx-PAD_L)/S.cw);
 S.off=Math.max(-20,Math.min(S.n-2,S.off));draw();},{passive:false});
cv.onmousedown=e=>{const r=cv.getBoundingClientRect(),
 mx=e.clientX-r.left,my=e.clientY-r.top;S.mouse={x:mx,y:my};
 if(S.tool==='bi'){
  if(!S.pend){S.pend={i:iClamp(mx),p:pOf(my,yDom())};}
  else{snap();const a=S.pend,i1=iClamp(mx),p1=pOf(my,yDom());
   S.user.bis.push({s_dt:S.bars[a.i].dt,s_price:a.p,
    e_dt:S.bars[i1].dt,e_price:p1,dir:p1>=a.p?1:-1});
   S.user.bis.sort((u,v)=>String(u.s_dt)<String(v.s_dt)?-1:1);
   S.pend=null;draw();st('加笔');}
  return;}
 if(S.tool==='pivot'){S.drag={kind:'newpv',x:mx,y:my};return;}
 if(S.tool==='bs'){snap();const i=iClamp(mx);
  S.user.bs.push({dt:S.bars[i].dt,price:pOf(my,yDom()),type:S.bsType});
  S.user.bs.sort((u,v)=>String(u.dt)<String(v.dt)?-1:1);
  draw();st('加 '+S.bsType);return;}
 const h=hit(mx,my);S.sel=h;
 if(!h){S.drag={kind:'pan',x:mx,off:S.off};draw();return;}
 snap();
 S.drag={kind:h.kind,h:h,x:mx,y:my,
  init:h.kind==='bi'?S.user.bis[h.i]:h.kind==='pivot'?S.user.pivots[h.i]:
   h.kind==='bs'?S.user.bs[h.i]:null};
 draw();};
cv.onmousemove=e=>{const r=cv.getBoundingClientRect(),
 mx=e.clientX-r.left,my=e.clientY-r.top;S.mouse={x:mx,y:my};
 if(!S.drag){if(S.pend)draw();return;}
 const d=yDom();
 if(S.drag.kind==='pan'){
  S.off=Math.max(-20,Math.min(S.n-2,S.drag.off-(mx-S.drag.x)/S.cw));
  draw();return;}
 if(S.drag.kind==='newpv'){draw();return;}
 const h=S.drag.h;
 if(h.kind==='bend'){const b=S.user.bis[h.i],dt=S.bars[iClamp(mx)].dt,pr=pOf(my,d);
  if(h.which==='s'){b.s_dt=dt;b.s_price=pr;}else{b.e_dt=dt;b.e_price=pr;}
  b.dir=b.e_price>=b.s_price?1:-1;}
 else if(h.kind==='bi'){const b=S.user.bis[h.i],o=S.drag.init;
  const di=Math.round((mx-S.drag.x)/S.cw),dp=pOf(my,d)-pOf(S.drag.y,d);
  let i0=S.dtI[o.s_dt]||0,i1=S.dtI[o.e_dt]||S.n-1;
  i0=Math.max(0,Math.min(S.n-1,i0+di));i1=Math.max(0,Math.min(S.n-1,i1+di));
  b.s_dt=S.bars[i0].dt;b.e_dt=S.bars[i1].dt;
  b.s_price=o.s_price+dp;b.e_price=o.e_price+dp;}
 else if(h.kind==='pvz'){S.user.pivots[h.i][h.f]=pOf(my,d);}
 else if(h.kind==='pivot'){const p=S.user.pivots[h.i],o=S.drag.init;
  const di=Math.round((mx-S.drag.x)/S.cw);
  let i0=S.dtI[o.s_dt]||0,i1=S.dtI[o.e_dt]||S.n-1;
  i0=Math.max(0,Math.min(S.n-1,i0+di));i1=Math.max(0,Math.min(S.n-1,i1+di));
  p.s_dt=S.bars[i0].dt;p.e_dt=S.bars[i1].dt;}
 else if(h.kind==='bs'){const s=S.user.bs[h.i];
  s.dt=S.bars[iClamp(mx)].dt;s.price=pOf(my,d);}
 draw();};
cv.onmouseup=()=>{
 if(S.drag&&S.drag.kind==='newpv'){
  const d=yDom(),m=S.mouse||S.drag;
  const i0=iClamp(S.drag.x),i1=iClamp(m.x);
  const yA=pOf(S.drag.y,d),yB=pOf(m.y,d);
  const zg=Math.max(yA,yB),zd=Math.min(yA,yB);
  if(Math.abs(i1-i0)>=2&&zg>zd){snap();
   S.user.pivots.push({s_dt:S.bars[Math.min(i0,i1)].dt,
    e_dt:S.bars[Math.max(i0,i1)].dt,zg:zg,zd:zd});st('加中枢');}
  S.drag=null;draw();return;}
 S.drag=null;};
document.onkeydown=e=>{
 if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT')return;
 if((e.key==='Delete'||e.key==='Backspace')&&S.sel){delSel();e.preventDefault();}
 if(e.key.toLowerCase()==='z'&&(e.ctrlKey||e.metaKey)){undo();e.preventDefault();}};
function ma(cs,n){const o=new Array(cs.length).fill(null);let a=0;
 for(let i=0;i<cs.length;i++){a+=cs[i];if(i>=n)a-=cs[i-n];if(i>=n-1)o[i]=a/n;}
 return o;}
function load(){S.code=document.getElementById('code').value.trim();
 S.period=document.getElementById('period').value;
 const lim=parseInt(document.getElementById('limit').value)||480;
 st('加载中…');
 fetch('/api/chanlun/structure?code='+encodeURIComponent(S.code)+
  '&period='+S.period+'&limit='+lim).then(r=>r.json()).then(r=>{
  if(!r.ok||!r.bars||!r.bars.length){st('加载失败：'+(r.msg||r.error||'无数据'));return;}
  S.bars=r.bars;S.n=r.bars.length;
  S.last_dt=String(r.bars[S.n-1].dt);
  S.dtI={};r.bars.forEach((b,i)=>S.dtI[String(b.dt)]=i);
  const cs=r.bars.map(b=>b.c);S.ma20=ma(cs,20);S.ma60=ma(cs,60);
  S.algo={bis:(r.bis||[]).map(b=>({s_dt:b.s_dt,s_price:b.s_price,
    e_dt:b.e_dt,e_price:b.e_price,dir:b.dir})),
   pivots:(r.pivots||[]).map(p=>({s_dt:p.s_dt,e_dt:p.e_dt,zg:p.zg,zd:p.zd})),
   bs:(r.signals||[]).map(s=>({dt:s.dt,price:s.price,type:s.type}))};
  S.user=JSON.parse(JSON.stringify(S.algo));
  S.off=Math.max(0,S.n-Math.floor((S.W-PAD_L-PAD_R)/S.cw));
  S.undoS=[];S.sel=null;S.pend=null;draw();
  st('已预填算法结构：笔'+S.algo.bis.length+' 枢'+S.algo.pivots.length+
   ' 点'+S.algo.bs.length+'，改错处后保存');
  fetch('/api/chanlun/label/load?code='+encodeURIComponent(S.code)+
   '&period='+S.period).then(r=>r.json()).then(rec=>{
   if(rec&&!rec.none&&rec.user){
    S.user={bis:rec.user.bis||[],pivots:rec.user.pivots||[],bs:rec.user.bs||[]};
    draw();st('已载入 '+rec.ts+' 的标注（笔'+S.user.bis.length+'）续标');
    tip('上次 diff：'+((rec.diff||{}).summary||'—'));}});})
 .catch(err=>st('加载失败：'+err));}
function save(){if(!S.n){st('先加载');return;}
 st('保存中…');
 fetch('/api/chanlun/label/save',{method:'POST',
  headers:{'Content-Type':'application/json'},
  body:JSON.stringify({code:S.code,period:S.period,last_dt:S.last_dt,
   algo:S.algo,user:S.user})}).then(r=>r.json()).then(d=>{
  if(d.error){st('保存失败：'+d.error);return;}
  st('已保存 · '+d.diff.summary);
  tip('落盘 '+d.n_rows+' 行 @ data/labels/chanlun_labels.jsonl');})
 .catch(err=>st('保存失败：'+err));}
resize();hi();"""
# K29 推演卡三键反馈 JS（r-string 原生 JS，与 HTML 分离避免 % 转义）
_KG_CARD_JS = r"""
<script>
(function(){
  var q = new URLSearchParams(location.search);
  var code = (q.get('code') || '').trim();
  document.querySelectorAll('.fb').forEach(function(box){
    var p = box.getAttribute('data-p'), d = box.getAttribute('data-d');
    box.querySelectorAll('.fb-b').forEach(function(b){
      b.addEventListener('click', function(){
        if(!code){ box.querySelector('.fb-msg').textContent = '缺 code'; return; }
        fetch('/api/chanlun/card/feedback', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({code: code, period: p, last_dt: d,
                                verdict: b.getAttribute('data-v')})
        }).then(function(r){ return r.json(); }).then(function(j){
          var msg = box.querySelector('.fb-msg');
          if(j && j.ok){
            msg.textContent = '已记录（改判覆盖）';
            box.querySelectorAll('.fb-b').forEach(function(x){
              x.disabled = (x !== b);
            });
            b.style.fontWeight = '600';
          } else {
            msg.textContent = (j && j.error) ? j.error : '保存失败';
          }
        }).catch(function(){
          box.querySelector('.fb-msg').textContent = '网络错误';
        });
      });
    });
  });
})();
</script>
"""
