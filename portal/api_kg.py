# -*- coding: utf-8 -*-
"""P4 批3：知识图谱域 Mixin（kg 页族 17 方法 + 卡片/标注 JS 与小函数）。
Handler 壳经基座多继承并入（禁 import *）。拍板：D1(v1画布)/D2(net_html) 均留，纯搬运零删减。
"""
import json
import sys
import os as _os
from api_common import (
    QBT_DIR,
    pill_nav)
import logging
# 拆分第一批：顶层 helper 实体在 kg/kg_service.py；此处保留同名薄壳（_mixin_integrity 方法集校验依赖模块级定义）。
def _kg_label_save(*args, **kwargs):
    """label 保存（实体在 kg/kg_service.py）。"""
    from kg.kg_service import _kg_label_save as _impl
    return _impl(*args, **kwargs)


def _kg_label_diff(*args, **kwargs):
    """label diff（实体在 kg/kg_service.py）。"""
    from kg.kg_service import _kg_label_diff as _impl
    return _impl(*args, **kwargs)


def _kg_card_fb_save(*args, **kwargs):
    """card feedback 保存（实体在 kg/kg_service.py）。"""
    from kg.kg_service import _kg_card_fb_save as _impl
    return _impl(*args, **kwargs)



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


# ══ Handler 方法（经 MRO 并入基座）══

class Handler:
    def _kg_ask_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_ask_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_battle_page(self, *args, **kwargs):
        """委托 kg.kg_pages_events（拆分第三批）。"""
        from kg.kg_pages_events import _kg_battle_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_lexicon_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_lexicon_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_doc_page(self, *args, **kwargs):
        """委托 kg.kg_pages_events（拆分第三批）。"""
        from kg.kg_pages_events import _kg_doc_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_events_page(self, *args, **kwargs):
        """委托 kg.kg_pages_events（拆分第三批）。"""
        from kg.kg_pages_events import _kg_events_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_canvas_v2_page(self):
        """K234 画布 v2：AntV G6 v5 重做（拖拽/力导向/右键编辑全内置）。
        数据层复用 K226/K231 csearch/cexpand/chist/canvas_edit 零改动。
        body 用 replace 占位符（CSS 含 %，% 格式化会炸）。"""
        body = """<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>知识画布 v2 · 图谱</title>{css}<style>
body{box-sizing:border-box;margin:0;padding:var(--sp-2);height:100vh;height:100dvh;display:flex;flex-direction:column;overflow:hidden;background:var(--bg-page);color:var(--text)}
.toolbar{display:flex;flex-wrap:wrap;gap:var(--sp-2);align-items:center;margin:0 0 var(--sp-2)}
.toolbar form{display:flex;gap:6px;flex:1 1 240px;min-width:0}
.toolbar #qi{flex:1;min-width:0;width:0;box-sizing:border-box}
.wrap{display:grid;grid-template-columns:clamp(180px,18vw,250px) minmax(0,1fr) clamp(270px,23vw,340px);flex:1;min-height:0;gap:var(--sp-2)}
.left,.inspector{min-height:0;overflow-y:auto;background:var(--bg-raised);border:1px solid var(--border);border-radius:var(--r-m);padding:var(--sp-3);box-sizing:border-box}
.left h3,.inspector h3{margin:0 0 var(--sp-3);font-size:var(--fs-l)}
.right{min-width:0;min-height:320px;position:relative;border:1px solid var(--border);border-radius:var(--r-m);background:var(--bg-page);overflow:hidden}
#cv{width:100%;height:100%}
#empty{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);text-align:center;color:var(--text-dim);pointer-events:none;width:min(280px,90%);line-height:1.7}
#gxp input,#gxp select{box-sizing:border-box;width:100%;margin:0 0 var(--sp-2);padding:7px 9px;background:var(--gray-0);color:var(--text);border:1px solid var(--border);border-radius:var(--r-s);font:inherit}
#gxp label{display:block;font-size:var(--fs-s);color:var(--text-dim);margin:0 0 4px}
.gxactions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin:4px 0 10px}
.gxactions button{background:var(--gray-3);color:var(--text);border:1px solid var(--border);border-radius:var(--r-s);padding:7px 4px;cursor:pointer}
.gxactions button:hover,.hit:hover{border-color:var(--blu-500)}
#gxr{font-size:var(--fs-s);line-height:1.6;overflow-wrap:anywhere}
.timeline{border-top:1px solid var(--border);margin-top:var(--sp-3);padding-top:var(--sp-3);font-size:var(--fs-s)}
.timeline h3{margin:0 0 var(--sp-2)}
.timeline .hint{color:var(--text-dim);line-height:1.5;margin:4px 0 8px}
.timeline .dates{display:flex;flex-wrap:wrap;gap:6px;align-items:end}
.timeline .dates label{flex:1 1 110px;min-width:0;color:var(--text-dim)}
.timeline .dates input{box-sizing:border-box;width:100%;margin-top:4px;background:var(--gray-0);color:var(--text);border:1px solid var(--border);border-radius:var(--r-s);padding:6px;font:inherit;color-scheme:dark}
.timeline button{font:inherit;cursor:pointer;border:1px solid var(--border);border-radius:var(--r-s);background:var(--gray-3);color:var(--text);padding:6px 8px}
.timeline button:hover,.timeline button[aria-pressed=true]{border-color:var(--blu-500);color:var(--blu-500)}
.timeline button:disabled{opacity:.55;cursor:wait}
.timeline #tlDays{display:flex;gap:5px;overflow-x:auto;padding:8px 0}
.timeline #tlDays button{flex:none;white-space:nowrap}
.timeline #tlEvents{max-height:220px;overflow-y:auto}
.timeline #tlEvents button{display:block;text-align:left;width:100%;margin:0 0 5px;overflow-wrap:anywhere}
.timeline #tlStatus{color:var(--text-dim);margin:7px 0;overflow-wrap:anywhere;line-height:1.5}
#gxr .path-node{margin:3px;padding:4px 7px;background:var(--gray-3);border:1px solid var(--border);border-radius:var(--r-s);color:var(--blu-500);cursor:pointer}
@media(max-width:1050px){body{height:auto;min-height:100dvh;overflow:auto}.wrap{grid-template-columns:minmax(180px,1fr) minmax(0,2fr)}.inspector{grid-column:1/-1;overflow:visible}.left{max-height:480px}.right{height:65vh}}
@media(max-width:700px){.wrap{display:flex;flex-direction:column}.left{max-height:250px}.right{flex:none;height:55vh;min-height:340px}.inspector{flex:none}.legend{flex-wrap:wrap}#ed{box-sizing:border-box;width:min(430px,calc(100vw - 24px))!important;max-height:calc(100dvh - 24px)!important}}
:focus-visible{outline:2px solid var(--blu-500);outline-offset:2px}
.hit{display:block;width:100%;box-sizing:border-box;text-align:left;color:var(--text);font:inherit;padding:7px 10px;border:1px solid var(--gray-4);border-radius:8px;
margin:0 0 6px;cursor:pointer;background:var(--gray-2)}
.hit:hover{border-color:var(--blu-600)}
.hit b{color:var(--gray-7);font-size:13.5px}
.hit .lb{color:var(--gray-5);font-size:11px;margin-left:6px}
.side{border-top:1px solid var(--border);margin-top:var(--sp-3);padding-top:var(--sp-3);overflow-wrap:anywhere}
.side h4{margin:0 0 6px;color:var(--gray-7);font-size:14px}
.side .kv{font-size:12px;color:var(--gray-5);margin:2px 0}
.side .kv b{color:var(--gray-6);font-weight:500}
.side a{color:var(--blu-500);font-size:12.5px;text-decoration:none;margin-right:8px}
.legend{display:flex;gap:12px;font-size:11.5px;color:var(--gray-5);margin:6px 0}
.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;
margin-right:4px}
#err{position:absolute;left:10px;bottom:10px;color:var(--red-500);font-size:11px;
max-width:80%;white-space:pre-wrap;z-index:30}
</style></head><body>{nav}
<div class=toolbar>
<form id=sf>
<input id=qi class=kfind placeholder='搜实体：股票/游资/环节/主题/公司/概念…'
style='flex:1' autocomplete=off>
<button class=kfbtn>🔍</button></form>
<button class=kfbtn style='background:var(--grn-600)' onclick=newEntity()>＋实体</button>
<a class=kfbtn href='#gxp' style='background:var(--pur-500);color:var(--gray-0);text-decoration:none'>图分析 ↓</a>
<select id=layoutSel onchange=setLayoutKind(this.value)
 style='background:var(--gray-3);color:var(--gray-7);border:1px solid var(--gray-4);
 border-radius:8px;padding:4px 8px;font-size:12.5px;cursor:pointer'>
<option value=force>🌐 力导向</option><option value=radial>🏛 组织阵型</option>
<option value=circular>⭕ 环形</option><option value=grid>▦ 网格</option></select>
<button class=kfbtn style='background:var(--blu-600)' onclick=colorCommunity()>🎨 社群着色</button>
<button class=kfbtn style='background:var(--gray-4)' onclick=resetNodeColor()>🎨✕</button>
<button class=kfbtn style='background:var(--gray-4)' onclick=resetAll()>重置</button>
</div>
<div id=linkbar style='display:none;margin:0 0 6px;padding:6px 10px;
background:#2a2111;border:1px solid #4d3a13;border-radius:8px;
color:var(--ylw-500);font-size:12.5px'></div>
<div class=legend><span><i style='background:var(--red-500)'></i>人</span>
<span><i style='background:var(--grn-500)'></i>地</span>
<span><i style='background:var(--ylw-500)'></i>事</span>
<span><i style='background:var(--blu-500)'></i>物</span>
<span><i style='background:#bc8cff'></i>组织</span>
<span style='margin-left:auto'>G6 力导向 · 节点可拖拽 · 点击继续扩散</span>
</div>
<div class=wrap><section class=left><h3>搜索结果</h3><div id=hits aria-live=polite>输入实体名称或代码，搜索后自动上图。</div></section>
<main class=right><div id=cv></div><div id=empty>从顶部搜索实体，自动展开关系图；点击节点可继续扩散，右键可编辑或连线。</div><div id=err role=alert></div></main>
<aside class=inspector id=gxp><h3>图分析</h3>
<label for=gxa>实体 A</label><input id=gxa placeholder='名称或代码' autocomplete=off>
<label for=gxb>实体 B（路径 / 共同邻居）</label><input id=gxb placeholder='名称或代码' autocomplete=off>
<label for=gxh>最大跳数</label><select id=gxh><option value=''>自动</option><option value=2>2 跳</option><option value=3>3 跳</option><option value=6>6 跳</option></select>
<label for=scopeTypes>关系类型（逗号分隔，可留空）</label><input id=scopeTypes placeholder='如 SUPPLIES_TO,IN_GROUP' autocomplete=off spellcheck=false>
<label for=scopeAt>有效时点（仅筛有 validStart 的关系）</label><input id=scopeAt type=date>
<div id=scopeInfo class=kv role=status aria-live=polite>范围：全部关系 · 不限时点</div>
<div class=gxactions><button onclick="gxGo('path')">六度路径</button><button onclick="gxGo('shared')">共同邻居</button><button onclick="gxGo('community')">社群</button><button onclick="gxGo('centrality')">中心性</button></div>
<div id=gxr aria-live=polite>输入实体 A、B 查看六度路径；社群与中心性只需实体 A。</div>
<section class=timeline aria-labelledby=tlTitle><h3 id=tlTitle>事件时间窗口</h3>
<div class=hint>按事件发生日期查找选中股票；独立于上方关系“有效时点”。点击事件才加入画布。</div>
<div id=tlSeed class=hint>请先选中 Security 股票节点。</div>
<form id=tlForm class=dates><label for=tlFrom>起始日期<input id=tlFrom type=date required></label>
<label for=tlTo>结束日期<input id=tlTo type=date required></label>
<button id=tlLoad type=submit>加载事件</button></form>
<div id=tlStatus role=status aria-live=polite>尚未加载事件。</div>
<div id=tlDays aria-label='事件日期' role=group></div><div id=tlEvents aria-label='当日事件'></div>
</section>
<div class=side id=side><h4>节点详情</h4><div class=kv>点击画布节点查看属性。</div></div></aside></div>
<div id=ed style='display:none;position:fixed;left:50%;top:50%;
transform:translate(-50%,-50%);width:min(430px,90vw);max-height:80vh;overflow-y:auto;
background:var(--gray-2);border:1px solid var(--gray-4);border-radius:12px;padding:14px;
z-index:20;box-shadow:0 8px 30px rgba(0,0,0,.5)'>
<div style='display:flex;justify-content:space-between;align-items:center'>
<h4 id=edt style='margin:0 0 8px;color:var(--gray-7);font-size:14.5px'>实体编辑</h4>
<button type=button aria-label='关闭编辑面板' style='cursor:pointer;color:var(--gray-5);background:none;border:0;font-size:18px' onclick=closeEd()>✕</button></div>
<div style='display:flex;gap:6px;margin:0 0 6px'>
<select id=edl class=kfind style='width:150px'></select>
<input id=edk class=kfind placeholder='键（代码/名称）' style='flex:1'></div>
<div id=edrows></div>
<button class=kfbtn style='background:var(--gray-4);padding:4px 10px;
font-size:12px' onclick=addRow()>＋属性</button>
<button class=kfbtn style='background:var(--blu-600);padding:4px 10px;
font-size:12px' onclick=saveProps()>保存入图</button>
<div style='margin:10px 0 4px;color:var(--gray-5);font-size:12px'>
同类属性分布直方图 <select id=edf class=kfind style='width:120px'></select>
<button class=kfbtn style='background:var(--gray-4);padding:2px 8px;font-size:11px'
onclick=loadHist()>查看</button></div>
<div id=edhist style='font-size:11.5px;color:var(--gray-6)'></div>
</div>
<div id=ctx style='display:none;position:fixed;background:var(--gray-2);
border:1px solid var(--gray-4);border-radius:8px;z-index:30;min-width:130px'>
<div class=hit onclick=ctxExpand()>🔍 扩散展开</div>
<div class=hit onclick=ctxEdit()>✏️ 编辑属性</div>
<div class=hit onclick=ctxLink()>🔗 连接到…</div>
<div class=hit onclick="hideCtx();side(ctxId)">ℹ️ 详情</div>
</div>
<script src='/static/g6.min.js'></script>
<script>
// K-SPATIAL-01 2026-10-19 补声明——隐式 global 首次读取会 ReferenceError
var graph=null,graphReady=null;
var CAT={person:'#f85149',place:'#3fb950',event:'#d29922',
thing:'#58a6ff',org:'#bc8cff'};
var LB={person:'人',place:'地',event:'事',thing:'物',org:'组织'};
// K261 L3：社群着色 10 色板 + 当前焦点（radial 组织阵型中心）
var COMM_COLS=['#f85149','#3fb950','#58a6ff','#d29922','#bc8cff',
'#39c5cf','#ff7b72','#7ee787','#ffa657','#d2a8ff'];
var curFocus=null,tlSecurity=null,tlData=null,tlDay=null,tlSelected=null,tlSeq=0,tlPickSeq=0,focusSeq=0;
var tlToday=new Date(),tlStart=new Date(tlToday);
tlStart.setDate(tlStart.getDate()-89);
function localDate(d){return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')}
document.getElementById('tlFrom').value=localDate(tlStart);
document.getElementById('tlTo').value=localDate(tlToday);
var LBL=[['Security','thing','键=代码 002050.SZ'],['Company','org','键=com_id'],
['HotMoney','person','键=游资名'],['Commentator','person','键=派系名'],
['Seat','person','键=席位全称'],['Region','place','键=地区名'],
['Theme','event','键=主题名'],['Segment','thing','键=环节名'],
['Technique','thing','键=手法名'],['Concept','org','键=概念名'],
['Industry','org','键=行业名'],['SwL1Industry','org','键=申万一级名'],
['EXP_Community','org','键=集团名'],['Product','thing','键=产品名'],
['MarketEvent','event','键=evt_id'],['Event','event','键=事件id'],
['TradeDate','event','键=20261005'],['TradeAction','event','键=ta:...:side'],
['Location','place','键=city|省|市|席位']];
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,
function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',
"'":'&#39;'}[c]})}
function err(m){document.getElementById('err').textContent=m}
var scopeSeq=0;
function scopeQuery(){
  var types=document.getElementById('scopeTypes').value.trim().toUpperCase();
  var at=document.getElementById('scopeAt').value;
  var q=new URLSearchParams();
  if(types)q.set('types',types.split(',').map(function(t){return t.trim()}).join(','));
  if(at)q.set('at',at);
  return q;
}
function scopeLabel(d){
  var s=d.scope||{},c=d.coverage||{};
  var text='范围：'+(s.types?s.types.join(', ')+' 关系':'全部关系')+
    ' · '+(s.at?s.at+' 有效（仅含 validStart）':'不限时点');
  if(d.sampled!=null||c.sampled!=null)text+=' · '+((d.sampled||c.sampled)?'已采样':'未采样');
  var nodes=c.nodes!=null?c.nodes:(d.n_nodes!=null?d.n_nodes:d.graph&&d.graph.nodes&&d.graph.nodes.length);
  var edges=c.edges!=null?c.edges:(d.n_edges!=null?d.n_edges:d.graph&&d.graph.edges&&d.graph.edges.length);
  if(nodes!=null||edges!=null)text+=' · 覆盖 '+
    (nodes!=null?nodes+' 节点':'')+(nodes!=null&&edges!=null?' / ':'')+
    (edges!=null?edges+' 边':'');
  return text;
}
function showScope(d){document.getElementById('scopeInfo').textContent=scopeLabel(d)}
var lastScope=scopeQuery().toString();
['scopeTypes','scopeAt'].forEach(function(id){
  var el=document.getElementById(id);
  function scopeChanged(){
    var q=scopeQuery(),next=q.toString();if(next===lastScope)return;
    lastScope=next;resetAll();
    document.getElementById('hits').textContent='筛选已更新，请重新搜索或点击图分析。';
    showScope({scope:{types:q.get('types')?q.get('types').split(','):null,
      at:q.get('at')}});
  }
  el.addEventListener('input',scopeChanged);
  el.addEventListener('change',scopeChanged);
});
async function ensureGraph(){
  if(graphReady)return graphReady;
  if(typeof G6==='undefined'){err('G6 未加载（/static/g6.min.js）');
    throw new Error('G6 missing')}
  graph=new G6.Graph({container:'cv',animation:false,
    data:{nodes:[],edges:[]},
    node:{style:{size:16,
      fill:function(d){return d.data.comm!=null?
        COMM_COLS[d.data.comm%10]:(CAT[d.data.cat]||'#8b949e')},
      stroke:function(d){return d.data.eventFocus?'#d29922':d.data.path?'#e6edf3':'#8b949e'},
      lineWidth:function(d){return d.data.eventFocus?4:d.data.path?3:1},
      labelText:function(d){return d.data.name},
      labelPlacement:'top',labelFontSize:11,labelFill:'#c9d1d9',
      labelBackground:true,labelBackgroundFill:'#161b22',
      labelBackgroundOpacity:.85,labelPadding:[1,4]}},
    edge:{style:{stroke:function(d){return d.data.eventFocus?'#d29922':d.data.path?'#a371f7':'#30363d'},
      lineWidth:function(d){return d.data.eventFocus?3:d.data.path?3:1},endArrow:true,
      labelText:function(d){return d.data.rel},labelFontSize:9,
      labelFill:'#8b949e',labelBackground:true,
      labelBackgroundFill:'#0d1117',labelBackgroundOpacity:.8}},
    layout:{type:'d3-force',
      link:{distance:130},charge:{strength:-380},
      collide:{radius:26}},
    behaviors:['drag-canvas','zoom-canvas','drag-element']});
  graph.on('node:click',function(e){
    var id=e.target&&e.target.id;nodeClick(id)});
  graph.on('edge:click',function(e){
    var id=e.target&&e.target.id;if(id)sideEdge(id)});
  graph.on('node:contextmenu',function(e){
    var id=e.target&&e.target.id;
    var ev=e.originalEvent||e;
    if(ev&&ev.preventDefault)ev.preventDefault();
    showCtx(ev,id)});
  graphReady=graph.render().then(function(){return graph},function(e){
    graph=null;graphReady=null;throw e});
  return graphReady;}
function nodeClick(id){if(!id)return;
  if(linkFrom){if(id==linkFrom)return;finishLink(id);return}
  var n=getNd(id);
  if(n&&n.data.label==='Security')selectTimelineSecurity(id,n.data.name);
  else if(n&&n.data.label!=='MarketEvent'&&n.data.label!=='Event'&&n.data.label!=='TradeDate'&&tlSecurity)clearTimeline(true);
  if(n&&n.data.terminal){side(id);return}
  curFocus=id;focusNode(id);}
function getNd(id){if(!graph)return null;try{return graph.getNodeData(id)}catch(e){return null}}
function edgeId(e){return e.id!=null&&e.id!==''?String(e.id):e.s+'>>'+e.t+':'+e.type}
async function mergeGraph(nodes,edges,path,seq,valid){
  var g=await ensureGraph();if(seq!==scopeSeq||(valid&&!valid()))return;
  var add={nodes:[],edges:[]},updates=[],edgeUpdates=[];
  var existingNodes=g.getNodeData(),existingEdges=g.getEdgeData();
  var knownNodes=new Set(existingNodes.map(function(n){return n.id}));
  var knownEdges=new Set(existingEdges.map(function(e){return e.id}));
  (nodes||[]).forEach(function(n){if(!n||!n.id)return;
    var data={name:n.name||n.id,cat:n.category,props:n.fields||{},label:n.label,
      terminal:!!n.terminal,path:!!path};
    if(!knownNodes.has(n.id)){knownNodes.add(n.id);add.nodes.push({id:n.id,data:data})}
    else if(path){var old=existingNodes.find(function(x){return x.id===n.id});
      updates.push({id:n.id,data:Object.assign({},old.data,{path:true})})}});
  (edges||[]).forEach(function(e){if(!e||!knownNodes.has(e.s)||!knownNodes.has(e.t))return;
    var id=edgeId(e),data={rel:e.type,path:!!path};
    ['source','validStart','validEnd','weight','properties'].forEach(function(k){
      if(Object.prototype.hasOwnProperty.call(e,k))data[k]=e[k]});
    if(!knownEdges.has(id)){knownEdges.add(id);add.edges.push({id:id,source:e.s,target:e.t,data:data})}
    else if(path){var old=existingEdges.find(function(x){return x.id===id});
      if(old)edgeUpdates.push({id:id,data:Object.assign({},old.data,data,{path:true})})}});
  if(seq!==scopeSeq||(valid&&!valid()))return;
  if(add.nodes.length||add.edges.length)await g.addData(add);
  if(seq!==scopeSeq||(valid&&!valid()))return;
  if(updates.length)await g.updateNodeData(updates);
  if(seq!==scopeSeq||(valid&&!valid()))return;
  if(edgeUpdates.length)await g.updateEdgeData(edgeUpdates);
  if(seq!==scopeSeq||(valid&&!valid()))return;
  if(add.nodes.length||add.edges.length||updates.length||edgeUpdates.length)await g.render();
  if(seq===scopeSeq&&(!valid||valid()))document.getElementById('empty').style.display=g.getNodeData().length?'none':'block';
}
async function focusNode(id){
  if(!id)return;
  if(id.indexOf('Security|')===0)selectTimelineSecurity(id);
  var seq=scopeSeq,focus=++focusSeq,q=scopeQuery().toString();
  try{
    var d=await fetch('/kg/cexpand/'+encodeURIComponent(id)+(q?'?'+q:''))
      .then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json()});
    if(seq!==scopeSeq||focus!==focusSeq)return;
    if(!d.ok){err(d.error||'实体加载失败');return}
    var c=d.center||{id:id};
    await mergeGraph([Object.assign({},c,{id:id,terminal:!!d.terminal})].concat(d.nodes||[]),d.edges||[],false,seq,function(){return focus===focusSeq});
    if(seq!==scopeSeq||focus!==focusSeq)return;
    curFocus=id;side(id);showScope(d);err('');
  }catch(e){if(seq===scopeSeq&&focus===focusSeq)err('实体展开失败：'+String(e).slice(0,120))}}
function side(id){var n=getNd(id);if(!n)return;
  var p=n.data.props||{},el=document.getElementById('side');
  var h='<h4>'+esc(n.data.name)+'</h4><div class=kv>类型：'+
    esc(LB[n.data.cat]||n.data.cat||'未知')+' / '+esc(n.data.label||'')+
    (n.data.terminal?'（终端）':'')+'</div>';
  Object.keys(p).forEach(function(k){if(p[k]==null||p[k]==='')return;
    h+='<div class=kv><b>'+esc(k)+'</b>：'+esc(p[k])+'</div>'});
  if(n.data.label=='Security')
    h+="<a target=_blank href='/kg/stock/"+encodeURIComponent(
      p.ts_code||'')+"'>→ K 线研判</a>"+
      "<a target=_blank href='/kg?c="+encodeURIComponent(
      p.ts_code||'')+"'>→ 图谱全景</a>";
  if(n.data.label=='HotMoney')
    h+="<a target=_blank href='/kg?hm="+encodeURIComponent(n.data.name)+
      "'>→ 游资归因</a>";
  if(n.data.label=='Company')
    h+="<a target=_blank href='/kg/company?c="+encodeURIComponent(n.data.name)+
      "'>→ 公司本体</a>";
  el.innerHTML=h;}
function sideEdge(id){var e=null;
  try{e=graph.getEdgeData(id)}catch(x){}
  if(!e)return;
  var d=e.data||{},h='<h4>关系详情</h4>',el=document.getElementById('side');
  function val(v){return esc(typeof v==='object'?JSON.stringify(v):v)}
  function row(k,v){if(v==null||v==='')return;
    h+='<div class=kv><b>'+esc(k)+'</b>：'+val(v)+'</div>'}
  var src=getNd(e.source),dst=getNd(e.target);
  row('方向',(src&&src.data.name||e.source)+' → '+(dst&&dst.data.name||e.target));
  row('关系类型',d.rel);row('source',d.source);
  row('有效开始',d.validStart);row('有效结束',d.validEnd);row('weight',d.weight);
  var p=d.properties;
  if(p&&typeof p==='object'&&!Array.isArray(p))Object.keys(p).forEach(function(k){row(k,p[k])});
  el.innerHTML=h;}
function clearTimeline(dropSeed){++tlSeq;++tlPickSeq;tlData=null;tlDay=null;tlSelected=null;
  if(dropSeed)tlSecurity=null;
  document.getElementById('tlSeed').textContent=tlSecurity?'当前股票：'+tlSecurity.split('|').slice(1).join('|'):'请先选中 Security 股票节点。';
  document.getElementById('tlDays').replaceChildren();
  document.getElementById('tlEvents').replaceChildren();
  document.getElementById('tlStatus').textContent=tlSecurity?'请选择日期范围并加载事件。':'尚未加载事件。';
  document.getElementById('tlLoad').disabled=false;
  clearEventFocus();}
function selectTimelineSecurity(id,name){
  if(!id||id.indexOf('Security|')!==0)return;
  if(tlSecurity!==id){++focusSeq;clearTimeline(true);tlSecurity=id}
  document.getElementById('tlSeed').textContent='当前股票：'+(name||id.slice(9));}
function clearEventFocus(){if(!graph)return;
  var ns=graph.getNodeData().filter(function(n){return n.data.eventFocus});
  var es=graph.getEdgeData().filter(function(e){return e.data.eventFocus});
  if(ns.length)graph.updateNodeData(ns.map(function(n){return {id:n.id,data:Object.assign({},n.data,{eventFocus:false})}}));
  if(es.length)graph.updateEdgeData(es.map(function(e){return {id:e.id,data:Object.assign({},e.data,{eventFocus:false})}}));
  if(ns.length||es.length)graph.draw();}
function renderTimeline(){var d=tlData,days=document.getElementById('tlDays'),list=document.getElementById('tlEvents');
  days.replaceChildren();list.replaceChildren();if(!d)return;
  var events=Array.isArray(d.events)?d.events:[],byDay={};
  events.forEach(function(e){if(e.date)(byDay[e.date]||(byDay[e.date]=[])).push(e)});
  var counts=Array.isArray(d.days)?d.days:[];
  counts.forEach(function(day){var btn=document.createElement('button');btn.type='button';
    btn.textContent=day.date+' · '+day.count;btn.setAttribute('aria-pressed',day.date===tlDay?'true':'false');
    btn.addEventListener('click',function(){tlDay=day.date;renderTimeline()});days.appendChild(btn)});
  if(!tlDay){list.textContent=events.length?'选择日期查看事件。':'此时间窗口暂无事件。';return}
  var selected=byDay[tlDay]||[];
  if(!selected.length){list.textContent='该日没有返回的事件（结果可能已截断）。';return}
  selected.forEach(function(e){var btn=document.createElement('button');btn.type='button';
    btn.textContent=(e.ts?e.ts+' · ':'')+(e.kind?e.kind+' · ':'')+(e.title||e.id);
    btn.setAttribute('aria-pressed',e.id===tlSelected?'true':'false');
    btn.addEventListener('click',function(){selectTimelineEvent(e)});list.appendChild(btn)});}
document.getElementById('tlForm').addEventListener('submit',async function(ev){ev.preventDefault();
  var status=document.getElementById('tlStatus'),from=document.getElementById('tlFrom').value,to=document.getElementById('tlTo').value;
  if(!tlSecurity){status.textContent='请先搜索或点击 Security 股票节点。';return}
  if(from>to){status.textContent='起始日期不可晚于结束日期。';return}
  clearTimeline(false);var seq=tlSeq,scope=scopeSeq,seed=tlSecurity;
  status.textContent='加载事件中…';document.getElementById('tlLoad').disabled=true;
  var q=new URLSearchParams({from:from,to:to,limit:'200'});
  try{var res=await fetch('/kg/ctimeline/'+encodeURIComponent(seed)+'?'+q.toString());
    if(!res.ok)throw new Error('HTTP '+res.status);
    var d=await res.json();if(seq!==tlSeq||scope!==scopeSeq||seed!==tlSecurity)return;
    if(!d.ok){status.textContent='事件加载失败：'+(d.error||'请稍后重试');return}
    tlData=d;var c=d.coverage;
    status.textContent='已返回 '+c.returned+' 条事件'+
      (c.has_more?'；结果已截断，请缩小日期范围后重新加载。':'。')+
      ' 点击日期筛选，点击事件才加入画布。';renderTimeline();
  }catch(e){if(seq===tlSeq&&scope===scopeSeq&&seed===tlSecurity)status.textContent='事件加载失败：'+String(e).slice(0,120)}
  finally{if(seq===tlSeq&&scope===scopeSeq&&seed===tlSecurity)document.getElementById('tlLoad').disabled=false}});
async function selectTimelineEvent(e){if(!tlData||!e.id)return;
  ++focusSeq;var seq=tlSeq,pick=++tlPickSeq,scope=scopeSeq,d=tlData,seed=tlSecurity;
  var nodes=Array.isArray(d.nodes)?d.nodes:[],edges=Array.isArray(d.edges)?d.edges:[];
  var links=edges.filter(function(x){return x.s===e.id||x.t===e.id});
  var ids=new Set([e.id,seed]);links.forEach(function(x){ids.add(x.s);ids.add(x.t)});
  var chosen=nodes.filter(function(n){return ids.has(n.id)});
  if(d.seed&&d.seed.id===seed)chosen.unshift(d.seed);
  if(!chosen.some(function(n){return n.id===e.id})||!chosen.some(function(n){return n.id===seed})){
    document.getElementById('tlStatus').textContent='该事件缺少标准画布节点，无法上图。';return}
  try{await mergeGraph(chosen,links,false,scope,function(){return seq===tlSeq&&pick===tlPickSeq&&seed===tlSecurity});
    if(seq!==tlSeq||pick!==tlPickSeq||scope!==scopeSeq||seed!==tlSecurity)return;
    clearEventFocus();tlSelected=e.id;
    var n=getNd(e.id);if(n)graph.updateNodeData([{id:e.id,data:Object.assign({},n.data,{eventFocus:true})}]);
    var highlights=links.map(edgeId).map(function(id){try{return graph.getEdgeData(id)}catch(x){return null}}).filter(Boolean);
    if(highlights.length)graph.updateEdgeData(highlights.map(function(x){return {id:x.id,data:Object.assign({},x.data,{eventFocus:true})}}));
    graph.draw();renderTimeline();curFocus=e.id;
    var source=typeof e.source==='object'?JSON.stringify(e.source):e.source;
    document.getElementById('side').innerHTML='<h4>'+esc(e.title||e.id)+'</h4>'+
      '<div class=kv>事件时间：'+esc(e.ts||e.date||'未知')+'</div>'+
      '<div class=kv>事件类型：'+esc(e.kind||'未知')+'</div>'+
      '<div class=kv>事件来源：'+esc(source||'未提供')+'</div>'+
      '<div class=kv>关联真实关系：'+esc(highlights.length)+' 条</div>';
    document.getElementById('tlStatus').textContent='已将所选事件及其 '+highlights.length+' 条真实关系加入画布。';
  }catch(x){if(seq===tlSeq&&pick===tlPickSeq&&scope===scopeSeq)document.getElementById('tlStatus').textContent='事件上图失败：'+String(x).slice(0,120)}}
function resetAll(){++scopeSeq;++searchSeq;++focusSeq;clearTimeline(true);
  if(graph){graph.setData({nodes:[],edges:[]});graph.render()}
  curFocus=null;document.getElementById('empty').style.display='block';
  document.getElementById('gxr').textContent='输入实体 A、B 查看六度路径；社群与中心性只需实体 A。';
  document.getElementById('side').innerHTML='<h4>节点详情</h4><div class=kv>点击画布节点查看属性。</div>';err('')}
/* 搜索 */
var searchSeq=0;
document.getElementById('sf').addEventListener('submit',async function(ev){
  ev.preventDefault();var q=document.getElementById('qi').value.trim();
  var el=document.getElementById('hits'),seq=++searchSeq,scope=scopeSeq;
  if(!q){el.textContent='请输入实体名称或代码。';return}
  el.textContent='搜索中…';
  try{
    var r=await fetch('/kg/csearch/'+encodeURIComponent(q));
    if(!r.ok)throw new Error('HTTP '+r.status);
    var d=await r.json();if(seq!==searchSeq||scope!==scopeSeq)return;
    if(!d.ok){el.textContent='搜索失败：'+(d.error||'请稍后重试');return}
    var hits=d.hits||[];
    el.innerHTML=hits.length?hits.map(function(h){
      var nm=(h.fields&&(h.label==='Company'?h.fields.com_name:h.fields.name))||h.name||h.id;
      return '<button type=button class=hit data-id="'+esc(h.id)+'"><b>'+esc(nm)+
        '</b><span class=lb>'+esc(h.label||'')+' · '+
        esc(LB[h.category]||h.category||'')+'</span></button>'}).join('')
      :'未找到匹配实体，请换个名称或代码。';
    el.querySelectorAll('.hit').forEach(function(x){
      x.addEventListener('click',function(){var id=this.getAttribute('data-id');
        if(id.indexOf('Security|')!==0)clearTimeline(true);focusNode(id)})});
    if(hits.length&&hits[0].id){if(hits[0].id.indexOf('Security|')!==0)clearTimeline(true);
      focusNode(hits[0].id)}
  }catch(e){if(seq===searchSeq&&scope===scopeSeq)el.textContent='搜索失败：'+String(e).slice(0,120)}
});
/* 编辑面板（K231 逻辑平移） */
function newEntity(){openEd(null)}
function closeEd(){document.getElementById('ed').style.display='none'}
function openEd(n){var el=document.getElementById('ed');
  var sl=document.getElementById('edl');sl.innerHTML='';
  LBL.forEach(function(x){var o=document.createElement('option');
    o.value=x[0];o.textContent=x[0]+'（'+LB[x[1]]+'）';
    if(n&&n.label==x[0])o.selected=true;sl.appendChild(o)});
  document.getElementById('edk').value=n?n.id.split('|')[1]:'';
  var rows=document.getElementById('edrows');rows.innerHTML='';
  var src=n?(n.data.props||{}):{};
  Object.keys(src).forEach(function(k){addRow(k,src[k])});
  if(!Object.keys(src).length)addRow('','');
  var hf=document.getElementById('edf');hf.innerHTML='';
  ['name','ts_code','chain','industry','desc','type'].forEach(
    function(f){var o=document.createElement('option');
      o.value=f;o.textContent=f;hf.appendChild(o)});
  document.getElementById('edhist').innerHTML='';
  el.style.display='block';hideCtx();}
function curLabel(){return document.getElementById('edl').value}
function curKey(){return document.getElementById('edk').value.trim()}
function addRow(k,v){var rows=document.getElementById('edrows');
  var d=document.createElement('div');d.style.cssText=
    'display:flex;gap:4px;margin:0 0 4px';
  d.innerHTML='<input class=kfind placeholder=字段 style="width:120px" '+
    'value="'+esc(k||'')+'"><input class=kfind placeholder=值 '+
    'style="flex:1" value="'+esc(v||'')+'">'+
    '<span style="cursor:pointer;color:var(--red-500);align-self:center" '+
    'onclick=this.parentNode.remove()>✕</span>';
  rows.appendChild(d);}
function saveProps(){var props={};
  document.getElementById('edrows').querySelectorAll('div').forEach(
    function(d){var ks=d.querySelectorAll('input');
      if(ks[0].value.trim())props[ks[0].value.trim()]=ks[1].value});
  postEdit({op:'save',label:curLabel(),key:curKey(),props:props},
    function(d){if(!d.ok){alert(d.error);return}
      closeEd();focusNode(d.id);});}
function loadHist(){var f=document.getElementById('edf').value;
  fetch('/kg/chist/'+encodeURIComponent(curLabel()+'|'+f))
    .then(function(r){return r.json()}).then(function(d){
    var el=document.getElementById('edhist');
    if(!d.ok){el.textContent=d.error;return}
    var mx=Math.max.apply(null,d.top.map(function(x){return x.n}).concat([1]));
    el.innerHTML='样本 '+d.n_sampled+'：'+
      d.top.map(function(x){
        return '<div style="display:flex;align-items:center;gap:6px'+
          ';margin:2px 0"><span style="width:90px;overflow:hidden;'+
          'text-overflow:ellipsis;white-space:nowrap">'+esc(x.v)+
          '</span><span class=hb style="width:'+
          Math.round(x.n/mx*100)+'px;background:var(--blu-600)"></span>'+x.n+
          '</div>'}).join('')||'（无数据）';});}
function postEdit(obj,cb){
  fetch('/kg/canvas_edit',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(obj)})
    .then(function(r){return r.json()}).then(cb);}
/* 右键菜单 */
function showCtx(ev,id){ctxId=id;if(!id)return;
  var m=document.getElementById('ctx');
  m.style.left=ev.clientX+'px';m.style.top=ev.clientY+'px';
  m.style.display='block';}
function hideCtx(){document.getElementById('ctx').style.display='none'}
document.addEventListener('click',function(){hideCtx()});
function ctxExpand(){var id=ctxId;hideCtx();focusNode(id);}
function ctxEdit(){var id=ctxId;hideCtx();openEd(getNd(id));}
function ctxLink(){var id=ctxId;hideCtx();linkFrom=id;
  var b=document.getElementById('linkbar');b.style.display='block';
  b.textContent='🔗 连线模式：点击目标节点（Esc 取消）';}
document.addEventListener('keydown',function(ev){if(ev.key=='Escape'){
  linkFrom=null;document.getElementById('linkbar').style.display='none';closeEd()}});
function finishLink(dstId){var b=document.getElementById('linkbar');
  var rel=prompt('关系类型（白名单）：SUPPLIES_TO / IN_GROUP / OCCUPIES / '+
    'SERVES_AS / CONDUCTION / SUB_OF / CORRELATES_WITH / PRODUCES',
    'SUPPLIES_TO');
  if(!rel){linkFrom=null;b.style.display='none';return}
  var ev2=prompt('证据/说明（可选，写入 evidence 属性）：','');
  postEdit({op:'link',src:linkFrom,dst:dstId,rel:rel.trim(),
    props:ev2?{evidence:ev2}:{}},function(d){
    if(!d.ok){alert(d.error);b.style.display='none';linkFrom=null;return}
    focusNode(dstId);b.style.display='none';linkFrom=null;});}
// ── K261 L3 布局插件化 + 社群着色 ──
async function setLayoutKind(k){
  var L={force:{type:'d3-force',link:{distance:130},
    charge:{strength:-380},collide:{radius:26}},
   radial:{type:'radial',unitRadius:95,linkDistance:130,
    focusNode:curFocus||undefined,preventOverlap:true,nodeSize:26},
   circular:{type:'circular',radius:300,divisions:4,ordering:'topology'},
   grid:{type:'grid',sortBy:'degree'}}[k];
  if(!L)return;
  if(!graph||!graph.getNodeData().length){
    document.getElementById('layoutSel').value='force';
    err('请先搜索实体，再切换布局。');return}
  try{await graph.setLayout(L);await graph.layout();err('')}
  catch(e){err('布局切换失败：'+e.message)}}
function colorCommunity(){
  if(!graph){err('画布为空，先搜索实体');return}
  var seed=curFocus;
  if(!seed){var ids=graph.getNodeData().map(function(n){return n.id});
    if(!ids.length){err('画布为空，先搜索实体');return}
    seed=ids[0]}
  var seedNode=getNd(seed),seedName=seedNode&&seedNode.data.name||seed;
  var seq=scopeSeq,q=scopeQuery();q.set('days','3');
  fetch('/kg/gx/community:'+encodeURIComponent(seed)+'?'+q.toString())
  .then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}).then(function(d){
   if(seq!==scopeSeq)return;
   if(!d.ok){err(d.error);return}
   var m={};(d.communities||[]).forEach(function(c){
    (c.members||[]).forEach(function(id){m[id]=c.id})});
   var nds=graph.getNodeData().map(function(n){
    var c=Object.prototype.hasOwnProperty.call(m,n.id)?m[n.id]:null;
    return {id:n.id,data:{comm:c===null?undefined:c,
      name:n.data.name,cat:n.data.cat,props:n.data.props,
      label:n.data.label,terminal:n.data.terminal,focus:n.data.focus,path:n.data.path,
      eventFocus:n.data.eventFocus}}});
   graph.updateNodeData(nds);graph.draw();
   document.getElementById('gxr').textContent='已按 '+d.algorithm+' 着色：'+d.n_communities+' 社群 / '
    +d.n_nodes+' 节点（种子 '+seedName+'）；加权模块度 '+(d.modularity==null?'—':d.modularity)+
    '；核心划分、附属回填，画布中命中的节点已着色。';showScope(d);err('')})
  .catch(function(e){if(seq===scopeSeq)err('社群着色失败：'+String(e).slice(0,120))})}
function resetNodeColor(){
  if(!graph)return;
  var nds=graph.getNodeData().map(function(n){
    return {id:n.id,data:{comm:undefined,name:n.data.name,cat:n.data.cat,
      props:n.data.props,label:n.data.label,terminal:n.data.terminal,
      focus:n.data.focus,path:n.data.path,eventFocus:n.data.eventFocus}}});
  graph.updateNodeData(nds);graph.draw()}
// ── K261 L2 图分析面板（六度/共同邻居/社群/中心性）──
async function gxGo(sub){
  var a=document.getElementById('gxa').value.trim();
  var b=document.getElementById('gxb').value.trim(),r=document.getElementById('gxr');
  if(!a||(sub==='path'||sub==='shared')&&!b){r.textContent='请填写实体 A 和实体 B。';return}
  var h=document.getElementById('gxh').value,seq=scopeSeq,q=scopeQuery();
  if(a.indexOf('Security|')===0)selectTimelineSecurity(a);
  else if(a.indexOf('|')!==-1&&tlSecurity)clearTimeline(true);
  if(h)q.set('days',h);
  var u='/kg/gx/'+sub+':'+encodeURIComponent(a)+(b?','+encodeURIComponent(b):'')
    +(q.toString()?'?'+q.toString():'');
  r.textContent='分析中…';
  try{
    var res=await fetch(u);if(!res.ok)throw new Error('HTTP '+res.status);
    var d=await res.json();if(seq!==scopeSeq)return;
    if(!d.ok){r.textContent='分析失败：'+(d.error||'请稍后重试');return}
    showScope(d);
    if(sub==='path'){
      var nodes=d.graph&&Array.isArray(d.graph.nodes)?d.graph.nodes:[];
      var edges=d.graph&&Array.isArray(d.graph.edges)?d.graph.edges:[];
      if(nodes.length)await mergeGraph(nodes,edges,true,seq);
      if(seq!==scopeSeq)return;
      var pathNodes=nodes.length?nodes:(d.nodes||[]).map(function(n){return typeof n==='string'?{name:n}:n});
      r.innerHTML=(d.hops==null?'未找到连通路径':esc(d.hops)+' 跳'+
        (d['六度内']?' · 六度内':''))+
        (pathNodes.length?'<div style="margin-top:8px">'+pathNodes.map(function(n){
          return '<button type=button class=path-node data-id="'+esc(n.id||'')+'">'+
            esc(n.name||n.label||n.id||'未知节点')+'</button>'}).join(' → ')+'</div>':
          '<div class=dim>暂无路径节点。</div>')+
        ((d.rels||[]).length?'<div class=dim>关系：'+(d.rels||[]).map(esc).join(' → ')+'</div>':'');
      r.querySelectorAll('.path-node').forEach(function(btn){btn.addEventListener('click',function(){
        var id=this.getAttribute('data-id');if(id)focusNode(id);
        else{document.getElementById('qi').value=this.textContent;
          document.getElementById('sf').dispatchEvent(new Event('submit'))}})});
    }else if(sub==='shared'){
      var ns=d.neighbors||[];
      r.innerHTML=esc(d.n==null?ns.length:d.n)+' 个共同邻居'+
        (ns.length?ns.map(function(n){return '<div>'+esc(n.name||'未知')+
          ' <span class=dim>'+esc((n.labels||[]).join('/'))+' ×'+esc(n.w||0)+'</span></div>'}).join(''):'<div class=dim>暂无共同邻居。</div>')+
        (Object.keys(d.by_label||{}).length?'<div class=dim>按类型：'+
          Object.keys(d.by_label).map(function(k){return esc(k)+'×'+esc(d.by_label[k])}).join(' · ')+'</div>':'');
    }else if(sub==='community'){
      var cs=d.communities||[];
      r.innerHTML=esc(d.algorithm)+' · '+esc(d.n_nodes||0)+' 节点 / '+esc(d.n_communities||0)+' 社群 · 加权模块度 '+
        esc(d.modularity==null?'—':d.modularity)+'<div class=dim>'+esc(d.weight_basis||'')+'</div>'+
        (cs.length?cs.map(function(c){return '<div class=hit>'+
          (c.has_seed?'★ ':'')+'#'+esc(c.id)+' · '+esc(c.size)+' 节点（核心 '+esc(c.core_size)+
          ' / 附属 '+esc(c.attachment_size)+'） · '+
          Object.keys(c.labels||{}).map(function(k){return esc(k)+' '+esc(c.labels[k])}).join(' / ')+
          '<div class=dim>代表节点（括号为原图邻居数）：'+((c.tops||[]).map(function(t){return esc(t.name)+' ('+esc(t.deg)+')'}).join(' · ')||'暂无代表节点')+'</div></div>'}).join(''):'<div class=dim>'+esc(d.note||'暂无社群数据。')+'</div>');
    }else if(sub==='centrality'){
      function tbl(t,arr){return '<div style="margin-top:8px"><b>'+t+'</b>'+
        ((arr||[]).length?arr.map(function(x){return '<div class=dim>'+esc(x.name)+
          ' · '+esc(x.v)+'</div>'}).join(''):'<div class=dim>暂无数据</div>')+'</div>'}
      r.innerHTML=esc(d.n_nodes||0)+' 节点 / '+esc(d.n_edges||0)+' 边'+
        tbl('度中心性',d.degree)+tbl('中介中心性',d.betweenness)+tbl('PageRank',d.pagerank);
    }
  }catch(e){if(seq===scopeSeq)r.textContent='分析失败：'+String(e).slice(0,120)}
}
</script></body></html>""".replace("{css}", self._KG_CSS).replace(
            "{nav}", pill_nav("kgcanvas"))
        self._html(body)
    def _kg_canvas_page(self):
        """K226 知识画布：拖出实体点 → 加载数据 → 逐跳扩散引导。
        人/地/事/物/组织五类着色；海量节点（事件/交易日）为终端只读。
        数据层 kg_portal csearch/cexpand（/kg/api 分发复用）。"""
        body = """<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>知识画布 · 图谱</title>%s%s<style>
.wrap{display:flex;gap:10px;height:calc(100vh - 118px)}
.left{width:250px;flex:none;overflow-y:auto}
.right{flex:1;position:relative;border:1px solid var(--gray-4);border-radius:10px;
background:var(--gray-0);overflow:hidden}
#cv{width:100%%;height:100%%;cursor:grab;touch-action:none}
.hit{padding:7px 10px;border:1px solid var(--gray-4);border-radius:8px;
margin:0 0 6px;cursor:pointer;background:var(--gray-2)}
.hit:hover{border-color:var(--blu-600)}
.hit b{color:var(--gray-7);font-size:13.5px}
.hit .lb{color:var(--gray-5);font-size:11px;margin-left:6px}
.side{position:absolute;right:10px;top:10px;width:270px;background:var(--gray-2);
border:1px solid var(--gray-4);border-radius:10px;padding:10px;display:none;
max-height:70%%;overflow-y:auto}
.side h4{margin:0 0 6px;color:var(--gray-7);font-size:14px}
.side .kv{font-size:12px;color:var(--gray-5);margin:2px 0}
.side .kv b{color:var(--gray-6);font-weight:500}
.side a{color:var(--blu-500);font-size:12.5px;text-decoration:none}
.legend{display:flex;gap:12px;font-size:11.5px;color:var(--gray-5);margin:6px 0}
.legend i{display:inline-block;width:9px;height:9px;border-radius:50%%;
margin-right:4px}
</style></head><body>%s
<div style='display:flex;gap:10px;align-items:center;margin:0 0 6px'>
<form id=sf style='display:flex;gap:6px;flex:1'>
<input id=qi class=kfind placeholder='搜实体：股票/游资/环节/主题/公司/概念…'
style='flex:1' autocomplete=off>
<button class=kfbtn>🔍</button></form>
<button class=kfbtn style='background:var(--grn-600)' onclick=newEntity()>＋实体</button>
<button class=kfbtn style='background:var(--gray-4)' onclick=resetAll()>重置</button>
</div>
<div id=linkbar style='display:none;margin:0 0 6px;padding:6px 10px;
background:#2a2111;border:1px solid #4d3a13;border-radius:8px;
color:var(--ylw-500);font-size:12.5px'></div>
<div class=legend><span><i style='background:var(--red-500)'></i>人</span>
<span><i style='background:var(--grn-500)'></i>地</span>
<span><i style='background:var(--ylw-500)'></i>事</span>
<span><i style='background:var(--blu-500)'></i>物</span>
<span><i style='background:#bc8cff'></i>组织</span>
<span style='margin-left:auto'>点击节点继续扩散 · 虚圈＝终端（只读详情）</span>
</div>
<div class=wrap><div class=left id=hits></div>
<div class=right><svg id=cv></svg>
<div class=side id=side></div></div></div>
<div id=ed style='display:none;position:absolute;left:50%%;top:50%%;
transform:translate(-50%%,-50%%);width:430px;max-height:80vh;overflow-y:auto;
background:var(--gray-2);border:1px solid var(--gray-4);border-radius:12px;padding:14px;
z-index:20;box-shadow:0 8px 30px rgba(0,0,0,.5)'>
<div style='display:flex;justify-content:space-between;align-items:center'>
<h4 id=edt style='margin:0 0 8px;color:var(--gray-7);font-size:14.5px'>实体编辑</h4>
<span style='cursor:pointer;color:var(--gray-5)' onclick=closeEd()>✕</span></div>
<div style='display:flex;gap:6px;margin:0 0 6px'>
<select id=edl class=kfind style='width:150px'></select>
<input id=edk class=kfind placeholder='键（代码/名称）' style='flex:1'></div>
<div id=edrows></div>
<button class=kfbtn style='background:var(--gray-4);padding:4px 10px;
font-size:12px' onclick=addRow()>＋属性</button>
<button class=kfbtn style='background:var(--blu-600);padding:4px 10px;
font-size:12px' onclick=saveProps()>保存入图</button>
<div style='margin:10px 0 4px;color:var(--gray-5);font-size:12px'>
同类属性分布直方图 <select id=edf class=kfind style='width:120px'></select>
<button class=kfbtn style='background:var(--gray-4);padding:2px 8px;font-size:11px'
onclick=loadHist()>查看</button></div>
<div id=edhist style='font-size:11.5px;color:var(--gray-6)'></div>
</div>
<div id=ctx style='display:none;position:fixed;background:var(--gray-2);
border:1px solid var(--gray-4);border-radius:8px;z-index:30;min-width:130px'>
<div class=hit onclick=ctxExpand()>🔍 扩散展开</div>
<div class=hit onclick=ctxEdit()>✏️ 编辑属性</div>
<div class=hit onclick=ctxLink()>🔗 连接到…</div>
<div class=hit onclick=hideCtx()>ℹ️ 详情</div>
</div>
<script>
var CAT={person:'#f85149',place:'#3fb950',event:'#d29922',
thing:'#58a6ff',org:'#bc8cff'};
var LB={person:'人',place:'地',event:'事',thing:'物',org:'组织'};
var nodes={},edges=[],view={x:0,y:0,k:1},drag=null,dragNode=null,
    ctxId=null,linkFrom=null;
var cv=document.getElementById('cv');
function esc(s){return String(s==null?'':s).replace(/[&<>\"']/g,
function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',
\"'\":'&#39;'}[c]})}
function render(){
  var e=[];
  e.push('<g id=vp transform=\"translate('+view.x+','+view.y+
    ') scale('+view.k+')\">');
  edges.forEach(function(ed){
    var a=nodes[ed.s],b=nodes[ed.t];if(!a||!b)return;
    e.push('<line x1='+a.x+' y1='+a.y+' x2='+b.x+' y2='+b.y+
      ' stroke=#30363d stroke-width=1.2/>');
    e.push('<text x='+(a.x+b.x)/2+' y='+(a.y+b.y)/2+
      ' fill=#8b949e font-size=9 text-anchor=middle opacity=.8>'+
      esc(ed.type)+'</text>');});
  Object.keys(nodes).forEach(function(id){var n=nodes[id];
    var r=n.focus?11:7;
    e.push('<circle cx='+n.x+' cy='+n.y+' r='+r+' fill='+(CAT[n.cat]||'#8b949e')+
      ' stroke='+(n.focus?'#e6edf3':'#0d1117')+' stroke-width='+
      (n.focus?2.5:1.5)+' opacity='+(n.terminal?.55:1)+
      ' style=\"cursor:pointer\" data-id=\"'+id+'\"/>');
    e.push('<text x='+n.x+' y='+(n.y-r-4)+' font-size=10.5 fill=#c9d1d9 '+
      'text-anchor=middle style=\"pointer-events:none\" '+
      'opacity='+(n.terminal?.7:1)+'>'+esc(n.name)+'</text>');});
  e.push('</g>');cv.innerHTML=e.join('');
  cv.querySelectorAll('circle').forEach(function(c){
    c.addEventListener('click',function(ev){ev.stopPropagation();
      nodeClick(c.getAttribute('data-id'))});
    c.addEventListener('contextmenu',function(ev){ev.preventDefault();
      ev.stopPropagation();showCtx(ev,c.getAttribute('data-id'))});
    c.addEventListener('mousedown',function(ev){ev.stopPropagation();
      var id=c.getAttribute('data-id'),w=toWorld(ev);
      dragNode={id:id,ox:w.x-nodes[id].x,oy:w.y-nodes[id].y};});});
}
function toWorld(ev){var r=cv.getBoundingClientRect();
  return{x:(ev.clientX-r.left-view.x)/view.k,
         y:(ev.clientY-r.top-view.y)/view.k};}
cv.addEventListener('wheel',function(ev){ev.preventDefault();
  var w=toWorld(ev),f=ev.deltaY<0?1.15:.87;
  view.k=Math.max(.3,Math.min(3,view.k*f));
  var r=cv.getBoundingClientRect();
  view.x=ev.clientX-r.left-w.x*view.k;
  view.y=ev.clientY-r.top-w.y*view.k;render();});
cv.addEventListener('mousedown',function(ev){if(ev.target.tagName==
  'circle')return;drag={x:ev.clientX,
  y:ev.clientY,vx:view.x,vy:view.y};cv.style.cursor='grabbing';});
window.addEventListener('mousemove',function(ev){
  if(dragNode){var w=toWorld(ev);
    nodes[dragNode.id].x=w.x-dragNode.ox;
    nodes[dragNode.id].y=w.y-dragNode.oy;render();return;}
  if(!drag)return;
  view.x=drag.vx+ev.clientX-drag.x;view.y=drag.vy+ev.clientY-drag.y;
  render();});
window.addEventListener('mouseup',function(){drag=null;dragNode=null;
  cv.style.cursor='grab';});
function placeAround(f,n){
  var R=150,a0=Math.random()*6.28;
  for(var i=0;i<n;i++){var a=a0+i*6.28/Math.max(n,1);
    var id=arguments[2][i];
    nodes[id].x=f.x+R*Math.cos(a)*(1+(i%%3)*.25);
    nodes[id].y=f.y+R*Math.sin(a)*(1+(i%%4)*.2);}}
function focusNode(id){
  fetch('/kg/cexpand/'+encodeURIComponent(id))
    .then(function(r){return r.json()}).then(function(d){
    if(!d.ok){alert(d.error);return}
    var c=d.center;
    var isNew=!nodes[id];
    nodes[id]={id:id,name:c.name,cat:c.category,
      x:isNew?400: nodes[id].x,y:isNew?300:nodes[id].y,
      focus:true,props:c.fields,label:c.label,terminal:!!d.terminal};
    var f=nodes[id],fresh=[];
    d.nodes.forEach(function(n){if(!nodes[n.id]){n.cat=n.category;
      nodes[n.id]=n;fresh.push(n.id)}
      else if(!edges.some(function(e2){return(e2.s==id&&e2.t==n.id)||
        (e2.s==n.id&&e2.t==id)}))return;});
    d.edges.forEach(function(ed){if(!edges.some(function(e2){
      return e2.s==ed.s&&e2.t==ed.t&&e2.type==ed.type}))edges.push(ed)});
    if(fresh.length)placeAround(f,fresh.length,fresh);
    if(isNew){var n2=Object.keys(nodes).length;
      if(n2>1){/* 首次置中视图 */}}
    render();side(id);});
}
function nodeClick(id){var n=nodes[id];
  if(n.expanded){side(id);return}
  n.expanded=true;focusNode(id);}
function side(id){var n=nodes[id],el=document.getElementById('side');
  var h='<h4>'+esc(n.name)+'</h4><div class=kv>类型：'+
    (LB[n.cat]||n.cat)+' / '+esc(n.label||'')+
    (n.terminal?'（终端）':'')+'</div>';
  Object.keys(n.props||{}).forEach(function(k){
    var v=n.props[k];if(v==null||v==='')return;
    h+='<div class=kv><b>'+esc(k)+'</b>：'+esc(v)+'</div>'});
  if(n.label=='Security')
    h+=\"<a target=_blank href='/kg/stock/\"+encodeURIComponent(
      (n.props||{}).ts_code||'')+\"'>→ K 线研判</a> \"+
      \"<a target=_blank href='/kg?c=\"+encodeURIComponent(
      (n.props||{}).ts_code||'')+\"'>→ 图谱全景</a>\";
  if(n.label=='HotMoney')
    h+=\"<a target=_blank href='/kg?hm=\"+encodeURIComponent(n.name)+
      \"'>→ 游资归因</a>\";
  if(n.label=='Company')
    h+=\"<a target=_blank href='/kg/company?c=\"+encodeURIComponent(n.name)+
      \"'>→ 公司本体</a>\";
  el.innerHTML=h;el.style.display='block';}
function resetAll(){nodes={};edges=[];render();
  document.getElementById('side').style.display='none'}
document.getElementById('sf').addEventListener('submit',function(ev){
  ev.preventDefault();var q=document.getElementById('qi').value.trim();
  if(!q)return;
  fetch('/kg/csearch/'+encodeURIComponent(q))
    .then(function(r){return r.json()}).then(function(d){
    var el=document.getElementById('hits');
    el.innerHTML=d.ok&&d.hits.length?d.hits.map(function(h){
      var nm=(h.fields&&h.fields.name)||h.name;
      return '<div class=hit data-id=\"'+esc(h.id)+'\"><b>'+esc(nm)+
        '</b><span class=lb>'+esc(h.label)+' · '+
        (LB[h.category]||'')+'</span></div>'}).join('')
      :'<div class=hit>无命中</div>';
    el.querySelectorAll('.hit').forEach(function(x){
      var id=x.getAttribute('data-id');if(!id)return;
      x.addEventListener('click',function(){
        if(nodes[id]){nodes[id].focus=true;render();side(id);return}
        focusNode(id);});});});});

/* ── K231 编辑器：新建/属性/直方图/连边 ─────────────────── */
var LBL=[['Security','thing','键=代码 002050.SZ'],['Company','org','键=公司名'],
['HotMoney','person','键=游资名'],['Commentator','person','键=派系名'],
['Seat','person','键=席位全称'],['Region','place','键=地区名'],
['Theme','event','键=主题名'],['Segment','thing','键=环节名'],
['Technique','thing','键=手法名'],['Concept','org','键=概念名'],
['Industry','org','键=行业名'],['SwL1Industry','org','键=申万一级名'],
['EXP_Community','org','键=集团名'],['Product','thing','键=产品名'],
['MarketEvent','event','键=evt_id'],['Event','event','键=事件id'],
['TradeDate','event','键=20261005'],['TradeAction','event','键=ta:...:side'],
['Location','place','键=city|省|市|席位']];
function newEntity(){openEd(null)}
function closeEd(){document.getElementById('ed').style.display='none'}
function openEd(n){var el=document.getElementById('ed');
  var sl=document.getElementById('edl');sl.innerHTML='';
  LBL.forEach(function(x){var o=document.createElement('option');
    o.value=x[0];o.textContent=x[0]+'（'+LB[x[1]]+'）';
    if(n&&n.label==x[0])o.selected=true;sl.appendChild(o)});
  document.getElementById('edk').value=n?n.id.split('|')[1]:'';
  document.getElementById('edk').placeholder=
    LBL.filter(function(x){return x[0]==(n?n.label:LBL[0][0])})[0][2];
  var rows=document.getElementById('edrows');rows.innerHTML='';
  var src=n?(n.props||{}):{};
  Object.keys(src).forEach(function(k){addRow(k,src[k])});
  if(!Object.keys(src).length)addRow('','');
  var hf=document.getElementById('edf');hf.innerHTML='';
  ['name','ts_code','chain','industry','desc','type'].forEach(
    function(f){var o=document.createElement('option');
      o.value=f;o.textContent=f;hf.appendChild(o)});
  document.getElementById('edhist').innerHTML='';
  el.style.display='block';hideCtx();}
function curLabel(){return document.getElementById('edl').value}
function curKey(){return document.getElementById('edk').value.trim()}
function addRow(k,v){var rows=document.getElementById('edrows');
  var d=document.createElement('div');d.style.cssText=
    'display:flex;gap:4px;margin:0 0 4px';
  d.innerHTML='<input class=kfind placeholder=字段 style="width:120px" '+
    'value="'+esc(k||'')+'"><input class=kfind placeholder=值 '+
    'style="flex:1" value="'+esc(v||'')+'">'+
    '<span style="cursor:pointer;color:var(--red-500);align-self:center" '+
    'onclick=this.parentNode.remove()>✕</span>';
  rows.appendChild(d);}
function saveProps(){var props={};
  document.getElementById('edrows').querySelectorAll('div').forEach(
    function(d){var ks=d.querySelectorAll('input');
      if(ks[0].value.trim())props[ks[0].value.trim()]=ks[1].value});
  postEdit({op:'save',label:curLabel(),key:curKey(),props:props},
    function(d){if(!d.ok){alert(d.error);return}
      var id=d.id;
      if(!nodes[id])nodes[id]={id:id,name:curKey(),
        cat:(LBL.filter(function(x){return x[0]==curLabel()})[0]||[])[1],
        x:400+Math.random()*120,y:280+Math.random()*120,
        props:props,label:curLabel()};
      else{nodes[id].props=props;
        nodes[id].name=props.name||nodes[id].name}
      nodes[id].focus=true;render();closeEd();});}
function loadHist(){var f=document.getElementById('edf').value;
  fetch('/kg/chist/'+encodeURIComponent(curLabel()+'|'+f))
    .then(function(r){return r.json()}).then(function(d){
    var el=document.getElementById('edhist');
    if(!d.ok){el.textContent=d.error;return}
    var mx=Math.max.apply(null,d.top.map(function(x){return x.n}).concat([1]));
    el.innerHTML='样本 '+d.n_sampled+'：'+
      d.top.map(function(x){
        return '<div style="display:flex;align-items:center;gap:6px'+
          ';margin:2px 0"><span style="width:90px;overflow:hidden;'+
          'text-overflow:ellipsis;white-space:nowrap">'+esc(x.v)+
          '</span><span class=hb style="width:'+
          Math.round(x.n/mx*100)+'px;background:var(--blu-600)"></span>'+x.n+
          '</div>'}).join('')||'（无数据）';});}
function postEdit(obj,cb){
  fetch('/kg/canvas_edit',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(obj)})
    .then(function(r){return r.json()}).then(cb);}
/* 右键菜单 */
function showCtx(ev,id){ctxId=id;var m=document.getElementById('ctx');
  m.style.left=ev.clientX+'px';m.style.top=ev.clientY+'px';
  m.style.display='block';}
function hideCtx(){document.getElementById('ctx').style.display='none'}
document.addEventListener('click',function(){hideCtx()});
function ctxExpand(){var id=ctxId;hideCtx();
  if(nodes[id]&&!nodes[id].expanded){nodes[id].expanded=true;
    focusNode(id);}else side(id);}
function ctxEdit(){var id=ctxId;hideCtx();openEd(nodes[id]);}
function ctxLink(){var id=ctxId;hideCtx();linkFrom=id;
  var b=document.getElementById('linkbar');b.style.display='block';
  b.textContent='🔗 连线模式：点击目标节点（Esc 取消）';}
document.addEventListener('keydown',function(ev){if(ev.key=='Escape'){
  linkFrom=null;document.getElementById('linkbar').style.display='none'}});
function finishLink(dstId){var b=document.getElementById('linkbar');
  var rel=prompt('关系类型（白名单）：SUPPLIES_TO / IN_GROUP / OCCUPIES / '+
    'SERVES_AS / CONDUCTION / SUB_OF / CORRELATES_WITH / PRODUCES',
    'SUPPLIES_TO');
  if(!rel){linkFrom=null;b.style.display='none';return}
  var ev2=prompt('证据/说明（可选，写入 evidence 属性）：','');
  postEdit({op:'link',src:linkFrom,dst:dstId,rel:rel.trim(),
    props:ev2?{evidence:ev2}:{}},function(d){
    if(!d.ok){alert(d.error);b.style.display='none';linkFrom=null;return}
    edges.push({s:linkFrom,t:dstId,type:rel.trim()});render();
    side(dstId);b.style.display='none';linkFrom=null;});}
var _nodeClick=nodeClick;
nodeClick=function(id){if(linkFrom){
  if(id==linkFrom){return}
  finishLink(id);return}
  _nodeClick(id);};

</script></body></html>""" % (self._KG_CSS, pill_nav("kgcanvas"),
                              pill_nav("kgcanvas"))
        self._html(body)
    def _kg_resonance_block(self):
        from kg.kg_html import _kg_resonance_block as _impl
        return _impl()
    def _kg_shadow_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_shadow_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_label_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_label_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_card_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_card_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_flow_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_flow_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_map_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_map_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_company_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_company_page as _impl
        return _impl(self, *args, **kwargs)
    def _kg_net_html(self, mg):
        from kg.kg_html import _kg_net_html as _impl
        return _impl(mg)
    def _kg_overview_html(self, ov):
        from kg.kg_html import _kg_overview_html as _impl
        return _impl(ov)
    def _kg_page(self, *args, **kwargs):
        """委托 kg.kg_pages_misc（拆分第二批）。"""
        from kg.kg_pages_misc import _kg_page as _impl
        return _impl(self, *args, **kwargs)
