# -*- coding: utf-8 -*-
"""P4 批3：知识图谱域 Mixin（kg 页族 17 方法 + 卡片/标注 JS 与小函数）。
Handler 壳经基座多继承并入（禁 import *）。拍板：D1(v1画布)/D2(net_html) 均留，纯搬运零删减。
"""
import json
import os
import sys
import time
import os as _os
from api_common import (
    QBT_DIR,
    pill_nav,
    _BTSEL_TAG,
    _PILL, _PILL_ON,
    CANDLE_CN,
    _LABELS_FP, _CARD_FB_FP)
import logging



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
def _kg_label_save(rec):
    """v2.5 标注落盘（data/labels/chanlun_labels.jsonl，纯文件不写图）。

    幂等：code+period+last_dt 相同覆盖旧行；返回 diff 摘要供前端即时显示。
    """
    code = str(rec.get("code") or "").strip()
    period = str(rec.get("period") or "").strip()
    last_dt = str(rec.get("last_dt") or "").strip()
    user = rec.get("user") or {}
    algo = rec.get("algo") or {}
    if not (code and period and last_dt):
        return {"error": "code/period/last_dt 必填"}
    if not (isinstance(user.get("bis"), list)
            and isinstance(user.get("pivots"), list)
            and isinstance(user.get("bs"), list)):
        return {"error": "user 结构需含 bis/pivots/bs 数组"}
    os.makedirs(os.path.dirname(_LABELS_FP), exist_ok=True)
    key = (code, period, last_dt)
    rows = []
    try:
        with open(_LABELS_FP, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get("code"), r.get("period"),
                        r.get("last_dt")) != key:
                    rows.append(r)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
    diff = _kg_label_diff(algo, user)
    rows.append({"code": code, "period": period, "last_dt": last_dt,
                 "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "algo": algo, "user": user, "diff": diff})
    with open(_LABELS_FP, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"ok": True, "n_rows": len(rows), "diff": diff}
def _kg_label_diff(algo, user):
    """v2.5 标注 diff（算法 vs 人工）——系统性偏差定位。

    口径：笔按 e_dt 配对（新增/删除/端点位移%>0.5 计移动），
    买卖点按 dt+type 配对，中枢按 s_dt+e_dt 配对（zg/zd 变化%）。
    """
    a_bis = algo.get("bis") or []
    u_bis = user.get("bis") or []
    am = {str(b.get("e_dt")): b for b in a_bis}
    um = {str(b.get("e_dt")): b for b in u_bis}
    bi_add = sorted(set(um) - set(am))
    bi_del = sorted(set(am) - set(um))
    moved, shifts = 0, []
    for k in set(am) & set(um):
        a, u = am[k], um[k]
        mx = max(
            abs(u.get("s_price", 0) - a.get("s_price", 0))
            / max(abs(a.get("s_price", 1)), 1e-9),
            abs(u.get("e_price", 0) - a.get("e_price", 0))
            / max(abs(a.get("e_price", 1)), 1e-9)) * 100
        if mx > 0.5:
            moved += 1
            shifts.append(round(mx, 2))
    a_bs = {"%s|%s" % (s.get("dt"), s.get("type")) for s in algo.get("bs") or []}
    u_bs = {"%s|%s" % (s.get("dt"), s.get("type")) for s in user.get("bs") or []}
    bs_add = sorted(u_bs - a_bs)
    bs_del = sorted(a_bs - u_bs)
    a_pv = {"%s|%s" % (p.get("s_dt"), p.get("e_dt")): p
            for p in algo.get("pivots") or []}
    u_pv = {"%s|%s" % (p.get("s_dt"), p.get("e_dt")): p
            for p in user.get("pivots") or []}
    pv_shift = []
    for k in set(a_pv) & set(u_pv):
        for f_ in ("zg", "zd"):
            if a_pv[k].get(f_) and u_pv[k].get(f_):
                pv_shift.append(round(
                    abs(u_pv[k][f_] - a_pv[k][f_])
                    / max(abs(a_pv[k][f_]), 1e-9) * 100, 2))
    return {"bi_algo_n": len(a_bis), "bi_user_n": len(u_bis),
            "bi_added": bi_add, "bi_removed": bi_del, "bi_moved": moved,
            "bi_max_shift_pct": max(shifts) if shifts else 0,
            "bs_added": bs_add, "bs_removed": bs_del,
            "pivot_algo_n": len(a_pv), "pivot_user_n": len(u_pv),
            "pivot_max_shift_pct": max(pv_shift) if pv_shift else 0,
            "summary": ("笔 %d→%d（人 +%d/-%d/移 %d）· 中枢 %d→%d · "
                        "买卖点 %d→%d（人 +%d/-%d）· 端点最大位移 %.2f%%"
                        % (len(a_bis), len(u_bis), len(bi_add), len(bi_del),
                           moved, len(a_pv), len(u_pv), len(a_bs), len(u_bs),
                           len(bs_add), len(bs_del),
                           max(shifts) if shifts else 0))}
def _kg_card_fb_save(rec):
    """K29 推演卡三键校对反馈落盘（对/偏/错 → card_feedback.jsonl）。

    幂等：code+period+last_dt 相同覆盖旧行（改判重标）；verdict ∈
    right/partial/wrong；note 可选。纯文件，供推演卡质量迭代统计。
    """
    import time as _t
    code = str(rec.get("code") or "").strip()
    period = str(rec.get("period") or "").strip()
    last_dt = str(rec.get("last_dt") or "").strip()
    verdict = str(rec.get("verdict") or "").strip()
    if not (code and period and last_dt
            and verdict in ("right", "partial", "wrong")):
        return {"error": "code/period/last_dt/verdict(right|partial|wrong) 必填"}
    row = {"code": code, "period": period, "last_dt": last_dt,
           "verdict": verdict, "note": str(rec.get("note") or "")[:200],
           "ts": _t.strftime("%Y-%m-%dT%H:%M:%S")}
    os.makedirs(os.path.dirname(_CARD_FB_FP), exist_ok=True)
    key = (code, period, last_dt)
    rows = []
    try:
        with open(_CARD_FB_FP, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get("code"), r.get("period"),
                        r.get("last_dt")) != key:
                    rows.append(r)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
    rows.append(row)
    tmp = _CARD_FB_FP + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, _CARD_FB_FP)
    return {"ok": True, "n_rows": len(rows), "saved": row}
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
    def _kg_ask_page(self):
        """K240 四期：图原生问答页（问句→实体链接→子图事实→生成+溯源）。"""
        body = """<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>图谱问答 · 图谱</title>{css}{nav}<style>
.qa{max-width:760px;margin:0 auto}
.qbox{display:flex;gap:8px;margin:12px 0}
.ans{background:var(--gray-2);border:1px solid var(--gray-4);border-radius:10px;
padding:14px;margin:10px 0;color:var(--gray-7);font-size:14px;line-height:1.7}
.src{margin-top:10px}
.src a{color:var(--blu-500);font-size:12.5px;text-decoration:none;margin-right:10px}
.dim2{color:var(--gray-5);font-size:11.5px;margin-top:8px}
</style></head><body>{nav}
<div class=qa><h1>💬 图谱问答</h1>
<div class='dim' style='margin:0 0 8px'>图原生问答：实体链接 → 子图检索 → 事实生成（可溯源，Ollama 本地）</div>
<form id=sf class=qbox>
<input id=qi class=kfind style='flex:1' placeholder='如：三花智控的传导链和派系共识？'
autocomplete=off><button class=kfbtn>提问</button></form>
<div id=out></div></div>
<script>
document.getElementById('sf').addEventListener('submit',function(ev){
  ev.preventDefault();var q=document.getElementById('qi').value.trim();
  if(!q)return;
  var out=document.getElementById('out');
  out.innerHTML='<div class=ans>⏳ 图谱检索与生成中（约 10-30 秒）…</div>';
  fetch('/kg/cask/'+encodeURIComponent(q))
    .then(function(r){return r.json()}).then(function(d){
    if(!d.ok){out.innerHTML='<div class=ans>❌ '+d.error+'</div>';return}
    var h='<div class=ans>'+d.answer+
      '<div class=src>溯源：'+(d.sources||[]).map(function(s){
        var lk='<a href=/kg?q='+encodeURIComponent(s.name)+
          '>'+s.name+'</a>';
        // K262 对话出「点」：股票实体直达作战地图
        if((s.id||'').indexOf('Security|')===0){
          lk+=' <a href=/kg/battle?code='+encodeURIComponent(s.id.slice(9))+
            ' style="color:#7ee787">🗺 作战图</a>'}
        return lk}).join('')+
      '</div><div class=dim2>模型 '+d.model+
      ' · 答案基于图内事实，详细关系见 <a href=/kg/canvasv2>知识画布</a></div></div>';
    out.innerHTML=h;});});
</script></body></html>""".replace("{css}", self._KG_CSS).replace(
            "{nav}", pill_nav("kgcanvas"))
        self._html(body)
    def _kg_battle_page(self):
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
</script></body></html>""".replace("{css}", self._KG_CSS).replace(
            "{nav}", pill_nav("kgcanvas"))
        self._html(body)
    def _kg_lexicon_page(self):
        """K263-W7 股吧词表维护：情绪词/同义归并在线编辑。

        保存到 quant_backtest/data/lexicon.json，guba_sentiment 按 mtime
        懒加载热生效（portal 长跑进程免重启）。同义归并候选来源：alias_audit
        周报（LLM 核准后人工终审，在此补录）。STOP/TAG_WORDS 两组本页只读
        （编辑场景少，仍走 guba_sentiment.py）。
        """
        import html as _html
        if QBT_DIR not in sys.path:
            sys.path.insert(0, QBT_DIR)
        import guba_sentiment as _gs
        _gs._apply_lexicon(force=True)   # 取当前生效值（含 json 覆盖）
        _panic = "\n".join(_gs.PANIC_WORDS)
        _hope = "\n".join(_gs.HOPE_WORDS)
        _alias = "\n".join("%s=%s" % kv for kv in _gs.WORD_ALIAS.items())
        _stop = "\n".join(sorted(_gs.STOP))
        body = """<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>股吧词表维护 · K263</title>{css}{nav}<style>
.lx{max-width:var(--page-w);margin:0 auto;padding:0 10px}
.lgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px}
@media(max-width:900px){.lgrid{grid-template-columns:1fr}}
.panel{background:var(--gray-2);border:1px solid var(--gray-4);border-radius:10px;padding:12px}
h3{font-size:14px;color:var(--gray-7);margin:2px 0 8px}
textarea{width:100%;min-height:180px;background:var(--gray-0);color:var(--gray-7);
border:1px solid var(--gray-4);border-radius:8px;padding:10px;font-size:13px;
line-height:1.8;font-family:inherit;box-sizing:border-box}
.sv{display:flex;gap:10px;align-items:center;margin:14px 0}
.sv button{background:#238636;border:none;color:#fff;border-radius:8px;
padding:8px 20px;font-size:13px;cursor:pointer}
#msg{font-size:12.5px;color:var(--gray-5)}
.dim{color:var(--gray-5);font-size:12.5px}
</style></head><body><div class='lx'>
<h1 style='font-size:19px'>📚 股吧词表维护 · K263</h1>
<div class='dim'>情绪词 / 同义归并在线编辑 → data/lexicon.json（guba_sentiment
按 mtime 懒加载，保存即热生效）。同义归并候选来自 alias_audit 周报人工终审。</div>
<div class='lgrid'>
 <div class='panel'><h3>恐慌词（{n_panic} 个，一行一词）</h3>
  <textarea id='panic'>{panic}</textarea></div>
 <div class='panel'><h3>乐观词（{n_hope} 个，一行一词）</h3>
  <textarea id='hope'>{hope}</textarea></div>
</div>
<div class='panel' style='margin-top:14px'><h3>同义归并 WORD_ALIAS
（{n_alias} 组，每行 变体=规范词；只并真同义，不并组合词/上下位词）</h3>
 <textarea id='alias' style='min-height:120px'>{alias}</textarea></div>
<div class='panel' style='margin-top:14px'><h3>停用词 STOP（{n_stop} 个，
一行一词；通用虚词+机构财务话语：关于/万元/同比/点评…。整帖机构噪声
由 NOISE_PAT 剔除，此处管散落词）</h3>
 <textarea id='stop' style='min-height:120px'>{stop}</textarea></div>
<div class='sv'><button onclick='save()'>💾 保存</button><span id='msg'>
本页保存后：股吧词云/情绪打分即时生效；文档词云 TAG_ALIAS 不在此页
（k266_m3_structure.py）。</span></div>
<div class='dim'>只读参考：事件契合 TAG_WORDS {n_tag} 组
（编辑 guba_sentiment.py）</div>
</div>
<script>
function lines(id){return document.getElementById(id).value.split('\\n')
 .map(s=>s.trim()).filter(Boolean)}
function save(){
 var panic=lines('panic'), hope=lines('hope'), stop=lines('stop'), alias={};
 lines('alias').forEach(l=>{var i=l.indexOf('=');
  if(i>0){var k=l.slice(0,i).trim(),v=l.slice(i+1).trim();
   if(k&&v&&k!==v)alias[k]=v}});
 if(!panic.length||!hope.length){alert('情绪词表不可为空');return}
 if(!stop.length){alert('停用词表不可为空');return}
 var msg=document.getElementById('msg');
 msg.textContent='保存中…';
 fetch('/kg/lexicon/save',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({panic:panic,hope:hope,stop:stop,word_alias:alias})})
 .then(r=>r.json()).then(d=>{msg.textContent=d.ok?
  '✅ 已保存：恐慌 '+d.panic+' · 乐观 '+d.hope+' · 归并 '+d.alias+' 组 · 停用 '+d.stop+'（热生效）':
  '❌ '+(d.error||'保存失败')})
 .catch(e=>{msg.textContent='❌ '+e});
}
</script></body></html>""".replace("{css}", self._KG_CSS).replace(
            "{nav}", pill_nav("kgcanvas")).replace(
            "{panic}", _html.escape(_panic)).replace(
            "{hope}", _html.escape(_hope)).replace(
            "{alias}", _html.escape(_alias)).replace(
            "{stop}", _html.escape(_stop)).replace(
            "{n_panic}", str(len(_gs.PANIC_WORDS))).replace(
            "{n_hope}", str(len(_gs.HOPE_WORDS))).replace(
            "{n_alias}", str(len(_gs.WORD_ALIAS))).replace(
            "{n_stop}", str(len(_gs.STOP))).replace(
            "{n_tag}", str(len(_gs.TAG_WORDS)))
        self._html(body)
    def _kg_doc_page(self, qs=None):
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
</script></body></html>""".replace("{css}", self._KG_CSS).replace(
            "{nav}", pill_nav("kgcanvas")).replace(
            "{wc}", wc_html).replace("{chips}", chips_html).replace(
            "{docs_json}", json.dumps(cards, ensure_ascii=False)).replace(
            "{win}", win_html)
        self._html(body)
    def _kg_events_page(self, qs=None):
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
</script></body></html>""".replace("__CSS__", self._KG_CSS).replace(
            "__NAV__", pill_nav("kgevents")).replace(
            "__SITU__", situ_html).replace("__TABS__", tabs).replace(
            "__FORM__", form).replace("__GSTAT__", gstat).replace(
            "__LEGEND__", legend).replace(
            "__INIT__", _j.dumps(g, ensure_ascii=False))
        self._html(body)
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
    def _kg_shadow_page(self, qs):
        """M3-W4 影子面板页：真假信号按日对照——信号当时可见（conf/q 三因子，
        pre 窗口无未来信息）vs 影子后验（hm_tag/post_hm10），口径全透明。"""
        import html as _h
        from urllib.parse import parse_qs as _pq
        import sys as _sy
        if QBT_DIR not in _sy.path:
            _sy.path.insert(0, QBT_DIR)
        import kg_portal as KP
        e = _h.escape
        qp = _pq(qs)
        days = max(7, min(365, int((qp.get("d") or ["60"])[0] or 60)))
        sh = KP.api("shadow", None, days)

        def pills():
            out = []
            for d in (30, 60, 90, 180):
                if d == days:
                    out.append("<span style='%s'>%d天</span>" % (_PILL_ON, d))
                else:
                    out.append("<a href='/kg/shadow?d=%d' style='%s'>"
                               "%d天</a>" % (d, _PILL, d))
            return "".join(out)

        if not sh.get("ok"):
            content = ("<div class='sec'><div class='dn'>%s</div></div>"
                       % e(sh.get("error", "")))
        else:
            t = sh["total"]
            cards = ("<div style='display:grid;grid-template-columns:repeat("
                     "auto-fit,minmax(150px,1fr));gap:10px'>"
                     "<div class='sstat'><span class='st'>结构信号</span>"
                     "<span class='sv'>%d</span><span class='su'>%s ~ %s</span></div>"
                     "<div class='sstat'><span class='st'>先行(资金>1亿)</span>"
                     "<span class='sv'>%d</span><span class='su'>q_hm=1 档</span></div>"
                     "<div class='sstat'><span class='st'>跟随确认</span>"
                     "<span class='sv' style='color:var(--red-500)'>%d</span>"
                     "<span class='su'>历史 ret5 +11.6%%</span></div>"
                     "<div class='sstat'><span class='st'>诱多警示</span>"
                     "<span class='sv' style='color:var(--grn-500)'>%d</span>"
                     "<span class='su'>历史 ret5 -4.2%%</span></div>"
                     "</div>" % (t["n"], t["d_min"], t["d_max"],
                                 t["n_lead"] or 0, t["fc"] or 0, t["lw"] or 0))
            method = ("<div class='sec'><h2>口径透明（影子模式）</h2>"
                      "<div style='font-size:13.5px;line-height:1.8'>"
                      "<b>信号当时可见</b>：conf = 0.40·结构 + 0.35·佐证 + "
                      "0.25·资金，资金分 q_hm 只用信号前 10 日龙虎榜（pre 窗口，"
                      "不含未来信息，阶跃口径 &gt;1亿=1）；<b>影子后验</b>：信号后 "
                      "10 日游资净买 post_hm10 每日链回填，标注 hm_tag——"
                      "<span class='bdg g'>跟随确认</span>post10∈(0,1亿] 且 "
                      "pre≤0（纯跟随，非驱动）／<span class='bdg r'>诱多警示"
                      "</span>pre&gt;0 且 post&gt;0（先行+继续买）。"
                      "校准样本 n=9414（集中 2026-04~07 行情段）：游资是跟随确认"
                      "者而非驱动者，确认组正超额全部来自 pre≤0 子组。"
                      "<b style='color:var(--ylw-500)'>体制切换警讯</b>：近月确认样本 "
                      "ret5 全负——行情段外推局限，标注不等于收益承诺。</div></div>")
            bd = "".join(
                "<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td><td>%.3f</td><td>%.2f</td></tr>"
                % (r["date"], r["n"],
                   r["n_tag"] and
                   "<b style='color:var(--gray-7)'>%d</b>" % r["n_tag"] or "0",
                   r["fc"] and "<span class='bdg g'>%d</span>" % r["fc"] or "—",
                   r["lw"] and "<span class='bdg r'>%d</span>" % r["lw"] or "—",
                   r["n_lead"] and "<span class='bdg y'>%d</span>" % r["n_lead"]
                   or "—", r["conf"] or 0, r["q_hm"] or 0)
                for r in sh["byday"]) or \
                "<tr><td colspan=8 class='dim'>窗口内无信号</td></tr>"
            byday = ("<div class='sec'><h2>按日对照（%d 个交易日，新→旧）</h2>"
                     "<table class='sticky'><tr><th>日期</th><th>信号</th>"
                     "<th>标注</th><th>确认</th><th>诱多</th><th>先行</th>"
                     "<th>avg conf</th><th>avg q_hm</th></tr>%s</table></div>"
                     % (len(sh["byday"]), bd))
            dd = "".join(
                "<tr><td>%s</td><td><a href='/kg?c=%s'>%s %s</a></td>"
                "<td><span class='bdg %s'>%s</span></td>"
                "<td>%.2f</td><td>%.1f</td>"
                "<td>%s</td><td class='%s'>%+.1f</td></tr>"
                % (r["date"], e(r["tc"]), e(r["tc"]), e(r["nm"] or ""),
                   "g" if "买" in (r["type"] or "") else
                   "r" if "卖" in (r["type"] or "") else "n",
                   e(r["type"] or ""), r["conf"] or 0, r["qh"] or 0,
                   ("<span class='bdg g'>确认</span>"
                    if r["tag"] == "follow_confirm"
                    else "<span class='bdg r'>诱多</span>"),
                   "up" if (r["post"] or 0) > 0 else "dn",
                   (r["post"] or 0) / 1e6)
                for r in sh["detail"]) or \
                "<tr><td colspan=7 class='dim'>窗口内无确认/诱多信号</td></tr>"
            detail = ("<div class='sec'><h2>真假信号明细（后验标注 · 新→旧，"
                      "上限 100 条）</h2><table class='sticky'>"
                      "<tr><th>日期</th><th>标的</th><th>结构</th>"
                      "<th colspan=2 style='text-align:center'>当时可见</th>"
                      "<th colspan=2 style='text-align:center'>影子后验</th></tr>"
                      "<tr><th></th><th></th><th></th><th>conf</th><th>q_hm</th>"
                      "<th>标注</th><th>post10 净买(百万)</th></tr>%s</table>"
                      "</div>" % dd)
            content = ("<h1>👁 影子面板 · 真假信号对照</h1>"
                       "<div class='dim' style='margin:2px 0 10px'>"
                       "M2-W4c 校准口径 · 窗口 %d 天 · %s</div>%s%s%s%s%s"
                       % (days, pills(), cards, method, byday, detail,
                          self._kg_resonance_block()))

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>影子面板 · 图谱</title>"
                + self._COCKPIT_CSS + self._KG_CSS + "</head><body>"
                + pill_nav("kgshadow") + content + "</body></html>")
        self._html(body)
    def _kg_label_page(self):
        """v2.5 画图标注工具（K21-A ②）：半自动标注产训练数据。

        系统预填算法结构（笔/中枢/买卖点，来自 /api/chanlun/structure
        = chanlun_signal.analyze 同源），人只改错处：拖笔端点、改枢边界、
        增删买卖点；保存时后端算 diff（=算法系统性偏差定位）落
        data/labels/chanlun_labels.jsonl（金标签，远期 LightGBM/E3）。
        """
        htm = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
               "<meta name='viewport' content='width=device-width,"
               "initial-scale=1><title>画图标注 · 缠论金标签</title>"
               + self._COCKPIT_CSS
               + "<style>.tbtn{display:inline-block;padding:5px 11px;"
                 "border:1px solid var(--gray-4);border-radius:14px;"
                 "background:var(--gray-2);color:var(--gray-5);cursor:pointer;"
                 "font-size:12.5px;margin:0 3px 4px 0;user-select:none}"
                 ".tbtn.on{color:var(--gray-7);border-color:var(--blu-500);"
                 "font-weight:600;background:#1f3a5f}"
                 ".tbtn.buy{color:var(--red-500)}.tbtn.sell{color:var(--grn-500)}"
                 "input,select{background:var(--gray-0);color:var(--gray-7);"
                 "border:1px solid var(--gray-4);border-radius:6px;"
                 "padding:5px 8px;font-size:13px}"
                 "#wrap{margin:10px 0}#cv{width:100%;height:660px;"
                 "background:var(--gray-0);border:1px solid var(--gray-4);"
                 "border-radius:8px;cursor:crosshair;display:block}"
                 "#st{margin-left:10px;font-size:13px;color:var(--gray-5)}"
                 "</style></head><body>" + pill_nav("kglabel")
               + "<h1>🖊 画图标注 · 缠论结构金标签</h1>"
               "<div class='dim'>系统预填算法结构（笔橙 / 中枢紫 / 买卖点"
               "箭头），你只改错处：拖端点修笔、拖边改中枢、点图放买卖点、"
               "Delete 删选中、Ctrl+Z 撤销 · 保存即算 diff（算法 vs 人工）"
               "落 data/labels/chanlun_labels.jsonl · 滚轮缩放 / 空白拖动"
               "平移</div>"
               "<div style='margin:10px 0'>"
               "<input id='code' value='301216.SZ' size='12' "
               "placeholder='代码如 301216.SZ'> "
               "<select id='period'><option>1d</option>"
               "<option selected>30m</option><option>5m</option></select> "
               "<input id='limit' value='480' size='6' title='装载根数'> "
               "<span class='tbtn on' id='bLoad' "
               "onclick='load()'>🔄 加载</span> │ "
               "<span class='tbtn on' data-tool='select'>☝ 拖动/改</span>"
               "<span class='tbtn' data-tool='bi'>✏ 画笔</span>"
               "<span class='tbtn' data-tool='pivot'>▭ 中枢</span>"
               "<span class='tbtn buy' data-bs='1买'>1买</span>"
               "<span class='tbtn buy' data-bs='2买'>2买</span>"
               "<span class='tbtn buy' data-bs='3买'>3买</span>"
               "<span class='tbtn sell' data-bs='1卖'>1卖</span>"
               "<span class='tbtn sell' data-bs='2卖'>2卖</span>"
               "<span class='tbtn sell' data-bs='3卖'>3卖</span> │ "
               "<span class='tbtn' onclick='undo()'>↩ 撤销</span>"
               "<span class='tbtn' onclick='delSel()'>🗑 删除选中</span>"
               "<span class='tbtn on' onclick='save()'>💾 保存</span>"
               "<span id='st'></span></div>"
               "<div id='wrap'><canvas id='cv'></canvas></div>"
               "<div class='dim' id='tip'></div>")
        htm += "<script>\n" + _KG_LABEL_JS + "</script></body></html>"
        self._html(htm)
    def _kg_card_page(self):
        """K29 推演卡网页版 + 三键校对反馈。

        数据与 CLI 同源（chanlun_card.card：三级联立 + 三灯 + 分类应对
        + 二买分档 + 区间套），零新计算；每级卡尾三键「对/偏/错」→
        POST /api/chanlun/card/feedback → card_feedback.jsonl（幂等键
        code+period+last_dt，改判覆盖）。只读渲染，反馈不回写任何计算。
        """
        from urllib.parse import parse_qs as _pqc
        import html as _h
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        qp = _pqc(qs)
        code = (qp.get("code") or [""])[0].strip()
        e = _h.escape

        def _flag(v, lab):
            if v is None:
                return ""
            return (" <b style='color:%s'>%s</b>"
                    % ("#3fb950" if v else "#8b949e", lab if v else lab + "✗"))

        def _trend_html(t):
            if not t:
                return "趋势 —"
            nm = {"up": "↑ 上升", "down": "↓ 下降"}.get(t.get("state"),
                                                        "→ 震荡")
            sa = ""
            if t.get("state") == "up" and t.get("short_adjust"):
                sa = " · 长升短调✓"
            hh, hl = t.get("hh"), t.get("hl")
            rng = ("（HH %s / HL %s）"
                   % (e("%.2f" % hh) if hh is not None else "—",
                      e("%.2f" % hl) if hl is not None else "—"))
            return "趋势 %s%s%s" % (nm, sa, rng)

        def _lvl_block(p, v, cd_code):
            lab = {"1d": "日线", "30m": "30 分钟", "5m": "5 分钟"}[p]
            if not v:
                return ("<div class='sec'><h3>%s</h3>"
                        "<div class='mute'>数据不可用 / 笔不足</div></div>"
                        % lab)
            last_dt = str((v.get("range") or ["", ""])[1])
            rows = []
            sl = v.get("sig_labels") or []
            window = list(zip((v.get("signals_recent") or [])[-5:],
                              sl[-5:]))
            for s, lab_txt in window:
                flags = (_flag(s.get("bei"), "背") + _flag(s.get("bei2"), "背2")
                         + _flag(s.get("mbei"), "m背") + _flag(s.get("tbe"), "趋")
                         + _flag(s.get("vv"), "量"))
                rows.append("<tr><td>%s</td><td>%s</td><td>%s</td>"
                            "<td>%s</td><td>%s</td></tr>"
                            % (e(str(s.get("dt"))), e(str(s.get("type"))),
                               e("%.2f" % s["price"]) if s.get("price") else "—",
                               e(str(lab_txt or "")), flags))
            pv = v.get("pivot")
            pv_html = ("中枢 %.2f–%.2f（%s，%s→%s）"
                       % (pv["zg"], pv["zd"], e(str(pv.get("state") or "—")),
                          e(str(pv.get("s_dt"))), e(str(pv.get("e_dt"))))
                       if pv else "无中枢")
            lb = v.get("last_bi") or {}
            fr = v.get("last_fractal")
            an = v.get("anchors") or {}
            sig_tbl = ("<table class='t'><tr><th>时间</th><th>信号</th>"
                       "<th>价</th><th>细分</th><th>影子</th></tr>%s</table>"
                       % "".join(rows)) if rows else \
                     "<div class='mute'>窗口内无信号</div>"
            fb = ("<div class='fb' data-p='%s' data-d='%s'>"
                  "这级推演："
                  "<button class='fb-b' data-v='right'>对</button>"
                  "<button class='fb-b' data-v='partial'>偏</button>"
                  "<button class='fb-b' data-v='wrong'>错</button>"
                  "<span class='fb-msg'></span></div>"
                  % (p, e(last_dt)))
            return (
                "<div class='sec'><h3>%s <span class='mute'>%s – %s"
                " · %d 根</span></h3>"
                "<div>%s · 现价 <b>%.3f</b> · MA20 %s · MA60 %s</div>"
                "<div>末笔%s %s→%s（%s → %s）%s</div>"
                "<div>%s · 锚8高 %s · 锚8低 %s</div>%s%s</div>"
                % (lab, e(str((v.get("range") or ["", ""])[0])),
                   e(str((v.get("range") or ["", ""])[1])),
                   v.get("bars_n") or 0,
                   _trend_html(v.get("trend")),
                   v.get("last_px") or 0,
                   e("%.3f" % v["ma20"]) if v.get("ma20") else "—",
                   e("%.3f" % v["ma60"]) if v.get("ma60") else "—",
                   "↑" if (lb.get("dir") == "上") else "↓",
                   e(str(lb.get("s_dt"))), e(str(lb.get("e_dt"))),
                   e("%.2f" % lb["s_price"]) if lb.get("s_price") else "—",
                   e("%.2f" % lb["e_price"]) if lb.get("e_price") else "—",
                   (" · 末分型 %s@%s（%s）"
                    % (e(str(fr["type"])), e("%.2f" % fr["price"]),
                       e(str(fr["dt"]))) if fr else ""),
                   pv_html,
                   e("%.2f" % an["high_8"]) if an.get("high_8") else "—",
                   e("%.2f" % an["low_8"]) if an.get("low_8") else "—",
                   sig_tbl, fb))

        htm = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
               "<meta name='viewport' content='width=device-width,"
               "initial-scale=1'><title>推演卡 · 缠论</title>"
               + self._COCKPIT_CSS
               + "<style>.sec{background:var(--gray-2);border:1px solid var(--gray-4);"
                 "border-radius:8px;padding:12px 14px;margin:10px 0}"
                 ".sec h3{margin:0 0 8px;font-size:15px}"
                 ".sec div{font-size:13px;margin:3px 0;color:var(--gray-6)}"
                 "table.t{border-collapse:collapse;margin:8px 0;width:100%}"
                 "table.t th,table.t td{border:1px solid var(--gray-3);"
                 "padding:4px 8px;font-size:12.5px;text-align:left}"
                 ".fb{margin-top:8px;padding-top:8px;"
                 "border-top:1px dashed var(--gray-4)}"
                 ".fb-b{margin:0 4px;padding:4px 14px;border-radius:12px;"
                 "border:1px solid var(--gray-4);background:var(--gray-0);"
                 "color:var(--gray-5);cursor:pointer;font-size:12.5px}"
                 ".fb-b[data-v=right]{color:var(--grn-500)}"
                 ".fb-b[data-v=partial]{color:var(--ylw-500)}"
                 ".fb-b[data-v=wrong]{color:var(--red-500)}"
                 ".fb-b:disabled{opacity:.45;cursor:default}"
                 ".fb-msg{margin-left:8px;font-size:12px;color:var(--gray-5)}"
                 ".kp li{margin:4px 0;font-size:13px}</style></head><body>"
               + pill_nav("kgcard")
               + "<h2 style='margin:14px 4px'>🃏 三级联立推演卡"
                 " · 三键校对</h2>"
               + "<form action='/kg/card' method='get' "
                 "style='margin:8px 4px'>"
                 "<input name='code' value='%s' placeholder="
                 "'600337.SH' style='width:130px;padding:6px 8px'> "
                 "<button>查看</button> "
                 "<span class='mute'>每级卡尾【对/偏/错】校对落 "
                 "card_feedback.jsonl（改判覆盖）</span></form>"
               % e(code))
        if not code:
            htm += ("<div class='sec'>输入代码开始（如 600337.SH / "
                    "301216.SZ）</div></body></html>")
            self._html(htm)
            return
        try:
            import sys as _syc
            if QBT_DIR not in _syc.path:
                _syc.path.insert(0, QBT_DIR)
            from chanlun_card import card as _card
            cd = _card(code)
        except Exception as ex:
            htm += ("<div class='sec'>推演卡计算失败：%s</div>"
                    "</body></html>" % e(str(ex)[:120]))
            self._html(htm)
            return
        kp = cd.get("key_point") or {}
        htm += ("<div class='sec'><h3>三灯（#8 关键点就绪度）</h3>"
                "<ul class='kp'>"
                "<li>空间：%s</li><li>结构：%s</li><li>动能：%s</li>"
                "<li><b>%s</b></li></ul></div>"
                % (e(str((kp.get("pivot_bottom") or {}).get("detail") or "—")),
                   e(str((kp.get("sub_done") or {}).get("detail") or "—")),
                   e(str((kp.get("ma_ok") or {}).get("detail") or "—")),
                   e(str(kp.get("verdict") or "—"))))
        cls = cd.get("classify") or []
        if cls:
            htm += ("<div class='sec'><h3>分类应对（锚位 if/else）</h3>"
                    "<ul class='kp'>%s</ul></div>"
                    % "".join("<li>%s</li>" % e(x) for x in cls))
        tb = cd.get("two_buy") or []
        if tb:
            htm += ("<div class='sec'><h3>二买分档</h3><ul class='kp'>%s"
                    "</ul></div>" % "".join(
                        "<li>%s</li>" % e(str(t)) for t in tb))
        qj = cd.get("qjt") or []
        if qj:
            htm += ("<div class='sec'><h3>区间套（v2-d）</h3><ul class='kp'>"
                    "%s</ul></div>" % "".join(
                        "<li>%s</li>" % e(q.get("note") or "") for q in qj))
        for p in ("1d", "30m", "5m"):
            htm += _lvl_block(p, (cd.get("levels") or {}).get(p), code)
        htm += _KG_CARD_JS + "</body></html>"
        self._html(htm)
    def _kg_flow_page(self):
        """SF 板块异动工单看板（六步数据流第⑥步）。

        数据源（kg_sector_flow 每日链产出，页面只读不重算）：
        quant_backtest/reports/sector_flow_latest.json（当日工单快照，
        含 cross_check 交叉验证）+ sector_flow_hist.jsonl（触发历史）。
        规则：同行业同日 ≥5 股上龙虎榜 + 游资净买同向 ≥4（250 日定标）。"""
        import html as _h
        import json as _jf
        e = _h.escape
        doc = {}
        try:
            with open(os.path.join(QBT_DIR, "reports",
                                   "sector_flow_latest.json"),
                      encoding="utf-8") as f:
                doc = _jf.load(f)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
        hist = []
        try:
            with open(os.path.join(QBT_DIR, "reports",
                                   "sector_flow_hist.jsonl"),
                      encoding="utf-8") as f:
                for line in f:
                    try:
                        hist.append(_jf.loads(line))
                    except Exception:
                        continue
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
        hist = hist[-30:]                      # 近 30 条触发记录

        if not doc:
            self._html("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                       "<title>板块异动工单</title>" + self._COCKPIT_CSS
                       + "</head><body>" + pill_nav("kgflow")
                       + "<h1>🧨 板块异动工单</h1><div class='sec'>"
                       "尚无工单快照——kg_sector_flow 夜链首跑后展示"
                       "</div></body></html>")
            return

        tickets = doc.get("tickets") or []
        rule = doc.get("rule") or {}

        def _bd(bd):
            """技术共振 dict → 着色 spans（主口径红/绿、dt 复盘灰）。"""
            if not bd:
                return "<span class='dim'>—</span>"
            g = lambda k: bd.get(k) or 0        # noqa: E731
            return ("结构买 <b style='color:var(--red-500)'>%d</b> / "
                    "卖 <b style='color:var(--grn-500)'>%d</b>"
                    "<span class='dim'>（信号日口径 买%d/卖%d）</span>"
                    " · 量能 %d · 手法 %d"
                    % (g("结构买"), g("结构卖"), g("结构买dt"),
                       g("结构卖dt"), g("量能"), g("手法")))

        # 顶部统计四格
        n_stk = sum(t.get("n_listed") or 0 for t in tickets)
        h30 = sum(h.get("n") or 0 for h in hist)
        stats = ("<div style='display:grid;grid-template-columns:repeat("
                 "auto-fit,minmax(150px,1fr));gap:10px'>"
                 "<div class='sstat'><span class='st'>当日工单</span>"
                 "<span class='sv'>%d</span><span class='su'>%s</span></div>"
                 "<div class='sstat'><span class='st'>上榜股合计</span>"
                 "<span class='sv'>%d</span><span class='su'>触发行业内"
                 "</span></div>"
                 "<div class='sstat'><span class='st'>近30次触发</span>"
                 "<span class='sv'>%d</span><span class='su'>hist 追加行"
                 "</span></div>"
                 "<div class='sstat'><span class='st'>规则阈值</span>"
                 "<span class='sv' style='font-size:18px'>%s/%s</span>"
                 "<span class='su'>上榜数/游资同向</span></div></div>"
                 % (doc.get("n_tickets") or 0, doc.get("date") or "—",
                    n_stk, h30, rule.get("n_min"), rule.get("m_min")))

        # 逐单卡片
        cards = ""
        for t in tickets:
            cx = t.get("cross") or {}
            bd = _bd(cx.get("tech_breakdown"))
            seat_rows = "".join(
                "<div style='font-size:13px'>· %s ×%d"
                "<span class='dim'>（%s）</span></div>"
                % (e(sx.get("seat") or ""), sx.get("n") or 0,
                   e("、".join(sx.get("stocks") or [])))
                for sx in (cx.get("seat_x") or []))
            if not seat_rows:
                seat_rows = "<div class='dim' style='font-size:13px'>" \
                            "无跨票席位（无协同操盘证据）</div>"
            rows = "".join(
                "<tr><td><a href='/kg?c=%s'>%s</a></td><td>%s</td>"
                "<td style='text-align:right;color:%s'>%+.0f万</td>"
                "<td style='text-align:center'>%s</td><td class='dim'>%s"
                "</td></tr>"
                % (e(s.get("tc") or ""), e(s.get("tc") or ""),
                   e(s.get("nm") or ""),
                   "#f85149" if (s.get("yz_w") or 0) > 0
                   else "#3fb950" if (s.get("yz_w") or 0) < 0
                   else "#8b949e",
                   s.get("yz_w") or 0,
                   "✓" if s.get("pen") else "—",
                   e(s.get("reason") or ""))
                for s in t.get("stocks") or [])
            cards += ("<div class='sec'><h2>[%s] %d 股 · 游资%s同向 %d · "
                      "差额 %+.2f亿</h2>"
                      "<div style='font-size:13px;margin-bottom:8px'>"
                      "穿透 %d/%d · 连续 %d · 跨票席位 %d · 共振 %s</div>"
                      "%s<table><tr><th>代码</th><th>名称</th>"
                      "<th style='text-align:right'>游资净买</th>"
                      "<th>次日穿透</th><th>上榜原因</th></tr>%s</table>"
                      "</div>"
                      % (e(t.get("industry") or ""),
                         t.get("n_listed") or 0,
                         e(t.get("yz_dir") or ""),
                         t.get("n_yz_same") or 0,
                         t.get("gap_sum") or 0,
                         t.get("n_pen") or 0, t.get("n_listed") or 0,
                         cx.get("cont_n") or 0,
                         len(cx.get("seat_x") or []), bd,
                         seat_rows, rows or ""))
        if not cards:
            cards = ("<div class='sec'><h2>当日无触发</h2>"
                     "<div class='dim'>%s 无达阈值行业（≥%s 股上榜 + "
                     "游资同向 ≥%s）——安静日</div></div>"
                     % (doc.get("date") or "—", rule.get("n_min"),
                        rule.get("m_min")))

        hrows = "".join(
            "<tr><td>%s</td><td style='text-align:right'>%d</td>"
            "<td class='dim'>%s</td></tr>"
            % (h.get("date") or "", h.get("n") or 0,
               e("、".join(h.get("inds") or [])))
            for h in reversed(hist))
        htab = ("<div class='sec'><h2>📜 触发历史<span class='dim' "
                "style='font-size:12px'>（近 %d 次运行，hist.jsonl）"
                "</span></h2><table><tr><th>日期</th><th>单数</th>"
                "<th>行业</th></tr>%s</table></div>"
                % (len(hist), hrows or "<tr><td colspan='3' class='dim'>"
                   "尚无历史</td></tr>"))

        self._html(
            "<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
            "<meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>板块异动工单 · 六步数据流</title>"
            + self._COCKPIT_CSS + "</head><body>" + pill_nav("kgflow")
            + "<h1>🧨 板块异动工单</h1><div class='dim'>同行业同日 "
            "≥%s 股上龙虎榜 + 游资净买同向 ≥%s → 工单（250 日回放定标 "
            "日均 0.35 单）· 生成于 %s · <a href='/kg'>图谱探索</a> / "
            "<a href='/kg/map'>游资地图</a></div>%s%s%s</body></html>"
            % (rule.get("n_min"), rule.get("m_min"),
               e(doc.get("generated_at") or "—"), stats, cards, htab))
    def _kg_map_page(self, qs):
        """M3-W3 游资地图页：19 主体全景 + 共现联动 + 动向流 / 单主体画像。
        数据层 kg_portal.hotmoney_map / hm_profile（TRADED_ON 纯聚合）。"""
        import html as _h
        from urllib.parse import parse_qs as _pq, quote as _qt
        import sys as _sy
        if QBT_DIR not in _sy.path:
            _sy.path.insert(0, QBT_DIR)
        import kg_portal as KP
        e = _h.escape

        def hmlink(nm):
            return ("<a href='/kg/map?hm=%s'>%s</a>" % (_qt(nm), e(nm)))

        def slink(tc, nm):
            return "<a href='/kg?c=%s'>%s %s</a>" % (e(tc), e(tc), e(nm or ""))

        qp = _pq(qs)
        hm = (qp.get("hm") or [""])[0].strip()
        days = max(7, min(365, int((qp.get("d") or ["90"])[0] or 90)))
        streamed = False  # U4-A：全景分支流式（骨架先行，慢数据后发）

        def pills(d_list, base):
            out = []
            sep = "&" if "?" in base else "?"
            for d in d_list:
                if d == days:
                    out.append("<span style='%s'>%d天</span>" % (_PILL_ON, d))
                else:
                    out.append("<a href='%s%sd=%d' style='%s'>%d天</a>"
                               % (base, sep, d, _PILL, d))
            return "".join(out)

        back = ("<div style='margin:0 0 10px'><a href='/kg/map' "
                "style='%s'>← 地图全景</a> <a href='/kg' style='%s'>"
                "图谱探索</a></div>" % (_PILL, _PILL))

        if hm:  # ── 单主体画像 ──
            pr = KP.api("profile", hm, days)
            title = "%s 席位画像" % hm
            if not pr.get("ok"):
                content = ("<div class='sec'><div class='dn'>%s</div></div>"
                           % e(pr.get("error", "")))
            else:
                rl = pr["run_len"]
                _wr = pr.get("win_rate")
                _wr_h = (" · 次日胜率 <b class='%s'>%.0f%%</b>（%d 笔）"
                         % ("up" if _wr["rate"] >= 0.5 else "dn",
                            _wr["rate"] * 100, _wr["n"])) if _wr else ""
                head = (
                    "<h1>💰 %s</h1>"
                    "<div class='dim' style='margin:2px 0 8px'>窗口 %d 天 · "
                    "%d 席位 · %d 笔上榜 · 净买合计 <b class='%s'>%+.0f 百万"
                    "</b> · top5 集中度 %.0f%%%s</div>"
                    "<div style='display:flex;gap:8px;flex-wrap:wrap'>%s</div>"
                    % (e(pr["name"]), pr["window_days"], pr["seats_n"],
                       pr["trades_n"],
                       "up" if (pr["net_buy_total"] or 0) > 0 else "dn",
                       (pr["net_buy_total"] or 0) / 1e6,
                       pr["concentration"] * 100, _wr_h,
                       pills((90, 180, 365), "/kg/map?hm="
                             + _qt(pr["name"]))))
                style = ("<div class='sec'><h2>持有风格</h2>"
                         "<table><tr><th>连续上榜段均值</th><td>%.1f 天</td>"
                         "<th>中位</th><td>%d 天</td><th>最长</th><td>%d 天"
                         "</td><th>段数</th><td>%d</td></tr></table>"
                         "<div class='dim' style='margin-top:4px'>口径：同一"
                         "标的相邻上榜日 ≤3 日历日算同一波（节假日断段，"
                         "倾向低估）</div></div>"
                         % (rl["avg"], rl["median"], rl["max"], rl["n_runs"]))

                def pref_bars(rows, key_n="n", with_net=False):
                    mx = max([r[key_n] for r in rows] or [1])
                    out = []
                    for r in rows:
                        net = ""
                        if with_net:
                            v = r.get("net") or 0
                            net = (" <span class='%s'>%+.0fM</span>"
                                   % ("up" if v > 0 else "dn", v / 1e6))
                        out.append(
                            "<div style='display:flex;align-items:center;"
                            "gap:8px;margin:4px 0'><span style='width:96px;"
                            "flex:none;font-size:13px' title='%s'>%s</span>"
                            "<span class='hb' style='width:%dpx'></span>"
                            "<span class='dim'>%d</span>%s</div>"
                            % (e(r["k"]), e(r["k"]),
                               max(4, int(r[key_n] * 150 / mx)),
                               r[key_n], net))
                    return "".join(out) or "<div class='dim'>无数据</div>"

                ind_html = ("<div class='sec'><h2>行业偏好（上榜次数）</h2>%s"
                            "</div>" % pref_bars(pr["industry_pref"],
                                                 with_net=True))
                area_html = ("<div class='sec'><h2>区域偏好</h2>%s</div>"
                             % pref_bars(pr["area_pref"]))
                mxm = max([m["n"] for m in pr["monthly"]] or [1])
                mon = "".join(
                    "<div style='display:inline-block;text-align:center;"
                    "margin:2px 5px'><span class='hb' style='width:14px;"
                    "height:%dpx'></span><div class='dim' style='font-size:"
                    "11px'>%s<br>%d</div></div>"
                    % (max(3, m["n"] * 40 / mxm), m["ym"], m["n"])
                    for m in pr["monthly"]) or \
                    "<div class='dim'>窗口内无上榜</div>"
                mon_html = ("<div class='sec'><h2>月度频次</h2>%s</div>" % mon)
                tops = "".join(
                    "<tr><td>%s</td><td>%d</td><td class='%s'>%+.0f</td>"
                    "<td>%s</td></tr>"
                    % (slink(r["tc"], r["nm"]), r["n"],
                       "up" if (r["net"] or 0) > 0 else "dn",
                       (r["net"] or 0) / 1e6, r["last"])
                    for r in pr["top_securities"]) or \
                    "<tr><td colspan=4 class='dim'>窗口内无交易</td></tr>"
                top_html = ("<div class='sec'><h2>Top 标的</h2><table class="
                            "'sticky'><tr><th>标的</th><th>次数</th>"
                            "<th>净买(百万)</th><th>最近</th></tr>%s</table>"
                            "</div>" % tops)
                # P2-① 榜后差额参与：主体席位同票同日上榜事件的资金穿透
                tg = pr.get("topgap") or {}
                _NAT = {"youzi": ("y", "游资"), "quant": ("b", "量化"),
                        "org": ("n", "机构"), "retail": ("r", "散户"),
                        "other": ("", "普通")}
                _tg_rows = []
                for r in (tg.get("rows") or []):
                    nc = _NAT.get(r.get("nat") or "other", ("", "普通"))
                    _pen = r.get("pen")
                    _gap = r.get("gap")
                    _gap_s = ("—" if _gap is None else
                              ("%.2f亿" % (_gap / 1e8) if abs(_gap) >= 1e8
                               else "%.0f万" % (_gap / 1e4)))
                    _ret = r.get("ret1")
                    _tg_rows.append(
                        "<tr><td>%s</td><td>%s</td><td class='%s'>%s</td>"
                        "<td>%s</td><td><span class='bdg %s'>%s</span></td>"
                        "<td class='%s'>%s</td><td>%s</td></tr>"
                        % (r.get("d"), slink(r["tc"], r.get("nm")),
                           "up" if (_gap or 0) > 0 else "dn", _gap_s,
                           ("%.1f%%" % (100 * r["gr"]))
                           if r.get("gr") is not None else "—",
                           nc[0], nc[1],
                           ("up" if (_ret or 0) > 0 else "dn"),
                           ("—" if _ret is None else "%+.2f%%" % (_ret * 100)),
                           ("<span class='bdg g'>穿透</span>" if _pen == 1
                            else ("<span class='bdg r'>未穿</span>"
                                  if _pen == 0 else "—"))))
                _pr_rate = tg.get("pen_rate")
                _pr_h = (" · 穿透率 <b class='%s'>%.0f%%</b>"
                         % ("up" if _pr_rate >= 0.5 else "dn",
                            _pr_rate * 100)) if _pr_rate is not None else ""
                tg_html = ("<div class='sec'><h2>榜后差额参与</h2>"
                           "<div class='dim' style='margin:2px 0 6px'>其席位"
                           "上榜日的前五买卖差 · 近 %d 笔%s</div>"
                           "<table class='sticky'><tr><th>日期</th><th>标的"
                           "</th><th>差额</th><th>占成交额</th><th>主导性质"
                           "</th><th>次日</th><th>穿透</th></tr>%s</table>"
                           "</div>"
                           % (tg.get("n", 0), _pr_h,
                              "".join(_tg_rows) or
                              "<tr><td colspan=7 class='dim'>窗口内无上榜"
                              "事件</td></tr>"))
                content = (back + head + style
                           + "<div class='duo'>%s<div>%s</div></div>"
                           % (ind_html, mon_html + area_html + top_html)
                           + tg_html)
        else:  # ── 地图全景 ──
            # U4-A 流式骨架：head+导航+Tab 框架先 flush（浏览器即刻有反馈，
            # 冷缓存 Cypher 10~30s 不再白屏），数据算完再续发三个 tabc
            self._html_open(
                "<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>游资地图</title>"
                + self._COCKPIT_CSS + self._KG_CSS + "</head><body>"
                + pill_nav("kgmap")
                + "<h1>💰 游资地图</h1><div class='dim' style='margin:2px 0 "
                  "8px'>TRADED_ON 纯聚合 · 窗口 %d 天 · 窗口切换 %s</div>"
                  % (days, pills((30, 90, 180), "/kg/map"))
                + "<div class='tabwrap'><div class='tabs' style='pointer-"
                  "events:none;opacity:.65'>"
                  "<button class='on' onclick=\"_tab(this,'kgt-ov')\">"
                  "🧭 集团总览</button>"
                  "<button onclick=\"_tab(this,'kgt-net')\">🕸 席位网络"
                  "</button>"
                  "<button onclick=\"_tab(this,'kgt-geo')\">🗺 地图"
                  "</button>"
                  "<button onclick=\"_tab(this,'kgt-det')\">📋 主体明细"
                  "</button></div>"
                + "<div id='kgmap-loading' class='sec' style='margin-top:"
                  "16px;padding:52px 17px;text-align:center'>"
                  "<div style='font-size:30px;line-height:1'>⏳</div>"
                  "<div class='dim' style='margin-top:12px'>正在聚合席位"
                  "数据 · Neo4j 共现大查询<br>冷缓存首次约 10~30 秒，完成后"
                  "自动展开</div></div>")
            streamed = True
            mp = KP.api("map", None, days)
            title = "游资地图"
            if not mp.get("ok"):
                content = (
                    "<script>var _t=document.querySelector('.tabs');"
                    "if(_t){_t.style.pointerEvents='';_t.style.opacity='';}"
                    "var _l=document.getElementById('kgmap-loading');"
                    "if(_l&&_l.parentNode)_l.parentNode.removeChild(_l);"
                    "</script><div class='sec'><div class='dn'>%s</div>"
                    "</div></div>" % e(mp.get("error", "")))
            else:
                rows = "".join(
                    "<tr><td>%s</td><td>%d</td><td>%d</td><td>%d</td>"
                    "<td class='%s'>%+.0f</td><td>%s</td></tr>"
                    % (hmlink(r["name"]), r["seats"], r["n"], r["nsec"],
                       "up" if (r["net"] or 0) > 0 else "dn",
                       (r["net"] or 0) / 1e6, r["last"] or "—")
                    for r in mp["roster"]) or ""
                roster = ("<div class='sec'><h2>主体全景（%d 家，按净买排）"
                          "</h2><table class='sticky'><tr><th>主体</th>"
                          "<th>席位</th><th>笔数</th><th>标的数</th>"
                          "<th>净买(百万)</th><th>最近</th></tr>%s</table>"
                          "<div class='dim' style='margin-top:4px'>点击主体"
                          "名进入席位风格画像；拉萨天团为散户通道汇总，"
                          "共现与榜位解读需区别对待</div></div>"
                          % (len(mp["roster"]), rows))
                pairs = "".join(
                    "<div style='margin:6px 0'>%s <span class='dim'>×</span> %s"
                    " <span class='bdg n'>%d 次</span> <span class='dim'>%s"
                    "</span></div>"
                    % (hmlink(p["a"]), hmlink(p["b"]), p["n"],
                       " ".join("<a href='/kg?c=%s'>%s</a>" % (e(t), e(t))
                                for t in p["tcs"][:2]))
                    for p in mp["pairs"]) or \
                    "<div class='dim'>窗口内无共现</div>"
                pair_html = ("<div class='sec'><h2>共现联动（同日同标的）</h2>"
                             "%s</div>" % pairs)
                # ── P3-c 交替接力（先后上榜≠同日共现，互补口径）──
                alt_html = ""
                ap = KP.api("altpairs", None, days)
                if ap.get("ok") and ap["pairs"]:
                    def _pcell(p):
                        if p["a"] == p["b"]:
                            return ("%s <span class='bdg'>自对</span>"
                                    % hmlink(p["a"]))
                        return ("%s <span class='dim'>→</span> %s"
                                % (hmlink(p["a"]), hmlink(p["b"])))
                    arows = "".join(
                        "<tr><td>%s</td><td><b>%d</b></td><td>%d</td>"
                        "<td>%d → %d</td><td>%s</td></tr>"
                        % (_pcell(p), p["alt"], p["nsec"], p["ab"],
                           p["ba"],
                           " ".join("<a href='/kg?c=%s'>%s</a>" % (e(t), e(t))
                                    for t in p["tcs"][:2]))
                        for p in ap["pairs"]) or (
                        "<tr><td colspan=5 class='dim'>窗口内无交替"
                        "模式</td></tr>")
                    alt_html = (
                        "<div class='sec'><h2>交替接力（隔日先后上榜）</h2>"
                        "<table><tr><th>席位对</th><th>交替</th>"
                        "<th>标的数</th><th>a先→b先</th><th>标的</th>"
                        "</tr>%s</table><div class='dim' style='"
                        "margin-top:4px'>窗口 %d 天内同标的先后上榜"
                        "（间隔≤%d 自然日、非同日）；a先→b先=两方向次数，"
                        "不对称=固定方向接力（后者接前者的货）；自对=同席"
                        "位反复上榜同一标的</div></div>"
                        % (arows, ap.get("window_days") or days,
                           ap.get("gap") or 7))
                else:
                    alt_html = ("<div class='sec'><h2>交替接力（隔日先后"
                                "上榜）</h2><div class='dim'>%s</div></div>"
                                % e(ap.get("error") or "窗口内无交替模式"))
                flow = "".join(
                    "<tr><td>%s</td><td>%s</td><td>%s</td>"
                    "<td class='%s'>%+.1f</td></tr>"
                    % (r["date"], hmlink(r["hm"]),
                       slink(r["tc"], r["nm"]),
                       "up" if (r["net"] or 0) > 0 else "dn",
                       (r["net"] or 0) / 1e6)
                    for r in mp["flow"]) or \
                    "<tr><td colspan=4 class='dim'>窗口内无动向</td></tr>"
                flow_html = ("<div class='sec'><h2>近期动向流</h2><table "
                             "class='sticky'><tr><th>日期</th><th>主体</th>"
                             "<th>标的</th><th>净买(百万)</th></tr>%s</table>"
                             "</div>" % flow)
                # ── G4 游资集团（Louvain 社区发现） ──
                comm_html = ""
                cm = KP.api("communities")
                if cm.get("ok"):
                    cards = []
                    for c in cm["top"]:
                        mrows = "".join(
                            "<tr><td title='%s'>%s</td><td>%s</td>"
                            "<td>%d</td><td class='bdg n'>%d</td></tr>"
                            % (e(m["seat"]), e(m["seat"][:22]),
                               hmlink(m["reg"]) if m["reg"]
                               else "<span class='dim'>—</span>",
                               m["n"], m["deg"])
                            for m in c["members"]) or \
                            "<tr><td colspan=4 class='dim'>无成员</td></tr>"
                        cards.append(
                            "<div class='sec'><h2>🧩 %s "
                            "<span class='bdg n'>%d 席位</span> "
                            "<span class='bdg'>共现 %d</span></h2><table "
                            "class='sticky'><tr><th>席位</th><th>注册主体"
                            "</th><th>上榜次数</th><th>共现度</th></tr>%s"
                            "</table></div>"
                            % (e(c["name"]), c["n_seats"], c["w_total"],
                               mrows))
                    grp_head = ("<div class='sec'><h2>游资集团</h2><div "
                                "class='dim' style='margin:2px 0 4px'>"
                                "Louvain 社区发现 · 同日同标的共现 ≥%d 次"
                                "连边 · 席位池=词表 %d+影子扩容 %d"
                                "（top_inst 高频未注册，散户/机构/沪深"
                                "股通通道已排除 %d 对）· %d/%d 席位入图 "
                                "%d 边 → %d 集团 · 生成 %s</div></div>"
                                % (cm.get("min_w", 2),
                                   cm.get("n_seats_total", 0)
                                   - cm.get("n_unreg_pool", 0),
                                   cm.get("n_unreg_pool", 0),
                                   cm.get("n_retail_pairs_excl", 0),
                                   cm.get("n_seats_graph", 0),
                                   cm.get("n_seats_total", 0),
                                   cm.get("n_edges", 0),
                                   cm.get("n_communities", 0),
                                   e(cm.get("generated_at", ""))))
                    grp = "".join(cards) or \
                        "<div class='sec'><div class='dim'>窗口内无 ≥3 席位" \
                        "的社区</div></div>"
                    wl = cm.get("watchlist") or []
                    if wl:
                        wl_rows = "".join(
                            "<tr><td>%s</td><td>%s</td><td>%d</td>"
                            "<td class='bdg n'>%d</td></tr>"
                            % (e(w["seat"]), e(w["comm"]), w["n"], w["deg"])
                            for w in wl)
                        wl_html = ("<div class='sec'><h2>名单外席位候选"
                                   "（与注册游资同社区·高频）</h2><table "
                                   "class='sticky'><tr><th>席位</th><th>"
                                   "所属集团</th><th>上榜次数</th><th>共现度"
                                   "</th></tr>%s</table></div>" % wl_rows)
                    else:
                        wl_html = ("<div class='sec'><h2>名单外席位候选"
                                   "</h2><div class='dim'>当前为空——窗口"
                                   "内未发现与注册游资同社区的高频未注册"
                                   "席位</div></div>")
                    comm_html = grp_head + grp + wl_html
                # ── KG-V4 集团总览层（G6 超节点）+ KG-V1 席位网络画布 ──
                # mapgraph 先跑（Cypher 结果 10min TTL 缓存），overview 复用
                net_html = ""
                mg = KP.api("mapgraph")
                if mg.get("ok"):
                    net_html = self._kg_net_html(mg)
                ov_html = ""
                ov = KP.api("overview")
                if ov.get("ok"):
                    ov_html = self._kg_overview_html(ov)
                # ── P3-d 交易日事件面（TradeDate 一等节点 × 事件透视）──
                td = (qp.get("td") or [""])[0].strip()
                tc = KP.api("tradecal", td or None, 5)
                if tc.get("ok"):
                    _DW = "一二三四五六日"
                    trows = "".join(
                        "<tr><td><a href='/kg/map?td=%s'>%s</a></td>"
                        "<td class='dim'>周%s</td><td><b>%d</b></td>"
                        "<td>%s</td></tr>"
                        % (e(tdr["date"]), e(tdr["date"]),
                           _DW[(tdr["dow"] or 1) - 1], tdr["n"],
                           " ".join("<span class='bdg n'>%s %d</span>"
                                    % (e(k), v)
                                    for k, v in sorted(
                                        tdr["by_kind"].items(),
                                        key=lambda x: -x[1])))
                        for tdr in tc["days"]) or \
                        "<tr><td colspan=4 class='dim'>图内无事件</td></tr>"
                    _np = (("<a href='/kg/map?td=%s'>← 前一日</a>"
                            % e(tc["prev"])) if tc["prev"]
                           else "<span class='dim'>← 前一日</span>")
                    _nn = (("<a href='/kg/map?td=%s'>后一日 →</a>"
                            % e(tc["next"])) if tc["next"]
                           else "<span class='dim'>后一日 →</span>")
                    tc_html = (
                        "<div class='sec'><h2>📅 交易日事件面（截至 %s）"
                        "</h2><table><tr><th>交易日</th><th>周</th>"
                        "<th>事件</th><th>按类</th></tr>%s</table>"
                        "<div class='dim' style='margin-top:4px'>%s · %s · "
                        "<a href='/kg/map'>回最新</a>（交易日历取自图内"
                        "TradeDate 链，事件按 ON_DATE 归日）</div></div>"
                        % (e(tc["date"]), trows, _np, _nn))
                else:
                    tc_html = ("<div class='sec'><h2>📅 交易日事件面</h2>"
                               "<div class='dim'>%s</div></div>"
                               % e(tc.get("error", "")))
                # ── P2-③ 空间层地图（ECharts geo：席位城市净买气泡 +
                # 标的注册地省份 choropleth；GeoJSON 本地托管零新库）──
                geo_html = ""
                gj = KP.api("geo", None, days)
                if gj.get("ok"):
                    import json as _jsg
                    byday = gj.get("byday") or []
                    gtmax = max(len(byday) - 1, 0)
                    grw = "".join(
                        "<tr><td>%s</td><td>%d</td><td>%d</td>"
                        "<td class='%s'>%+.2f亿</td>"
                        "<td class='%s'>%s <span class='dim' "
                        "style='font-size:11px'>%s</span></td></tr>"
                        % (e(r["city"]), r["n_seat"], r["n"],
                           "up" if r["net"] > 0 else "dn", r["net"] / 1e8,
                           ("up" if (r["wr"] or 0) >= 55
                            else ("dn" if r["wr"] is not None
                                  and r["wr"] <= 45 else "")),
                           r["wr_disp"], r["wr_n_disp"])
                        for r in gj["cities"])
                    geo_html = (
                        "<div class='sec'><h2>🗺 游资地理分布</h2>"
                        "<div class='dim' style='margin:2px 0 2px'>"
                        "底图色深=标的注册地数量 · 气泡=窗口 %d 天席位城市"
                        "净买（红买绿卖，大小∝|净买|，可滚轮缩放） · 城市"
                        "由营业部名提取，未解析席位 %d 个</div>"
                        "<div style='margin:2px 0 6px'>"
                        "<span id='gcm-net' onclick='_gcm(0)' "
                        "style='display:inline-block;padding:2px 12px;"
                        "border:1px solid var(--gray-4);border-radius:11px;"
                        "cursor:pointer;font-size:12px;background:var(--blu-600);"
                        "color:var(--gray-7)'>净买卖色</span> "
                        "<span id='gcm-wr' onclick='_gcm(1)' "
                        "style='display:inline-block;padding:2px 12px;"
                        "border:1px solid var(--gray-4);border-radius:11px;"
                        "cursor:pointer;font-size:12px;color:var(--gray-7)'>"
                        "胜率着色</span> "
                        "<span id='gcnote' class='dim' "
                        "style='font-size:12px'>胜率 40 绿 → 55 黄 → "
                        "70 红 · 气泡大小仍=|净买| · 灰=该城无胜率席位 · "
                        "排行列=全历史口径，着色随滑杆按窗口活跃席位"
                        "重算</span></div>"
                        "<div style='margin:2px 0 6px'>"
                        "<span id='gtp' onclick='_gp()' "
                        "style='display:inline-block;padding:2px 10px;"
                        "border:1px solid var(--gray-4);border-radius:11px;"
                        "cursor:pointer;font-size:12px;color:var(--gray-7)'>"
                        "▶ 播放</span> "
                        "<input type='range' id='gts' min='0' max='%d' "
                        "value='%d' oninput='_gt()' style='vertical-align:"
                        "middle;width:340px'> "
                        "<span id='gtl' style='font-size:12px;"
                        "color:var(--gray-7)'></span> "
                        "<span class='dim' style='font-size:12px'>· 滑杆="
                        "按交易日累计净买重渲（KG-V3 口径），着色随当前"
                        "模式</span></div>"
                        "<div id='kggeo' style='height:560px'></div>"
                        "<h2 style='margin-top:12px'>城市排行（按|净买|）"
                        "</h2><table><tr><th>城市</th><th>席位数</th>"
                        "<th>上榜笔数</th><th>净买</th><th>历史胜率"
                        "</th></tr>%s</table></div>"
                        "<script src='/static/echarts.min.js'></script>"
                        "<script>(function(){var CT=%s,PV=%s,BD=%s,"
                        "_gmode=0;"
                        "fetch('/static/china_geo.json')"
                        ".then(function(r){return r.json()})"
                        ".then(function(geo){"
                        "if(!window.echarts){var e0=document.getElementById"
                        "('kggeo');e0.innerHTML='<div class=dim>ECharts "
                        "未加载</div>';return}"
                        "echarts.registerMap('china',geo);"
                        "var el=document.getElementById('kggeo');"
                        "var ec=echarts.init(el);"
                        "var mx=1;CT.forEach(function(c)"
                        "{mx=Math.max(mx,Math.abs(c.net))});"
                        "var _gmx=mx;"
                        "ec.setOption({animation:false,"
                        "tooltip:{trigger:'item',formatter:function(p){"
                        "if(p.seriesType==='map')return p.name+'<br>注册"
                        "标的 '+(p.value==null?'—':p.value)+' 家';"
                        "var d=p.data;return d.name+'<br>席位 '+d.ns+"
                        "' 家 · 上榜 '+d.n+' 笔<br>净买 '+(d.net/1e8)"
                        ".toFixed(2)+' 亿<br>历史胜率 '+d.wrDisp+"
                        "(d.wrNDisp&&d.wrNDisp!='—'?'（'+d.wrNDisp+'）'"
                        ":'')+'<br>窗口胜率 '+(d.wr2!=null?d.wr2+'%%'+"
                        "(d.wrn2?'（'+d.wrn2+' 笔）':''):'—')},"
                        "legend:{data:['注册地分布','席位净买'],"
                        "textStyle:{color:'#8b949e'},top:2},"
                        "visualMap:{min:0,max:PV.length?PV[0].n:1,"
                        "left:8,bottom:40,seriesIndex:0,calculable:true,"
                        "text:['多','少'],textStyle:{color:'#8b949e',"
                        "fontSize:10},inRange:{color:['#14202e',"
                        "'#274b6d','#58a6ff']}},"
                        "geo:{map:'china',roam:true,zoom:1.2,"
                        "itemStyle:{areaColor:'#0d1520',"
                        "borderColor:'#30363d'},emphasis:{label:{color:"
                        "'#e6edf3'},itemStyle:{areaColor:'#20344d'}}},"
                        "series:[{name:'注册地分布',type:'map',geoIndex:0,"
                        "data:PV.map(function(p){return{name:p.name,"
                        "value:p.n}})},"
                        "{name:'席位净买',type:'effectScatter',"
                        "coordinateSystem:'geo',zlevel:2,"
                        "rippleEffect:{scale:2.6,brushType:'stroke'},"
                        "symbolSize:function(v){return 8+26*Math.min(1,"
                        "Math.abs(v[2])/_gmx)},"
                        "label:{show:true,position:'right',"
                        "color:'#e6edf3',fontSize:10,formatter:'{b}'},"
                        "data:CT.map(function(c){return{name:c.city,"
                        "value:c.coord.concat([c.net]),ns:c.n_seat,"
                        "n:c.n,net:c.net,wr:c.wr,wrDisp:c.wr_disp,"
                        "wrNDisp:c.wr_n_disp,itemStyle:{color:"
                        "c.net>=0?'#f85149':'#3fb950'}}})}]}} ,true);"
                        "window._gec=ec;"
                        "window.wrColor=function(w){if(w==null)"
                        "return '#8b949e';var t=(w-40)/30;if(t<0)t=0;"
                        "if(t>1)t=1;var a=[63,185,80],b=[210,153,34],"
                        "c2=[248,81,73];if(t<0.5){var k=t*2;"
                        "return 'rgb('+Math.round(a[0]+(b[0]-a[0])*k)+"
                        "','+Math.round(a[1]+(b[1]-a[1])*k)+','+"
                        "Math.round(a[2]+(b[2]-a[2])*k)+')'}"
                        "var k2=(t-0.5)*2;return 'rgb('+"
                        "Math.round(b[0]+(c2[0]-b[0])*k2)+','+"
                        "Math.round(b[1]+(c2[1]-b[1])*k2)+','+"
                        "Math.round(b[2]+(c2[2]-b[2])*k2)+')'};"
                        "window._gdata=function(){var s=document."
                        "getElementById('gts'),l=document."
                        "getElementById('gtl');if(!s||!window._gec)"
                        "return;var t=BD.length?Math.min(+s.value,"
                        "BD.length-1):-1,acc={},i,k,a;for(i=0;i<=t;"
                        "i++){var cd=BD[i].cities;for(k in cd){"
                        "a=acc[k];if(a){a.net+=cd[k].net;a.n+=cd[k].n;"
                        "if(cd[k].wr!=null){a.wr=cd[k].wr;"
                        "a.wrn=cd[k].wrn}}else acc[k]={net:cd[k].net,"
                        "n:cd[k].n,wr:cd[k].wr,wrn:cd[k].wrn}}}"
                        "var m2=1;for(k in acc){a=Math.abs(acc[k].net);"
                        "if(a>m2)m2=a}_gmx=m2;if(l)l.textContent="
                        "BD.length?('截至 '+BD[t].d+' 累计'):'';"
                        "window._gec.setOption({series:[{},{data:"
                        "CT.map(function(c){a=acc[c.city];var net=a?"
                        "a.net:0;return{name:c.city,value:c.coord."
                        "concat([net]),ns:c.n_seat,n:a?a.n:0,net:net,"
                        "wr:c.wr,wrDisp:c.wr_disp,wrNDisp:c.wr_n_disp,"
                        "wr2:(a&&a.wr!=null)?a.wr:null,"
                        "wrn2:(a&&a.wrn)?a.wrn:0,"
                        "itemStyle:{color:_gmode?wrColor("
                        "(a&&a.wr!=null)?a.wr:null):"
                        "(net>=0?'#f85149':'#3fb950')}}})}]})};"
                        "window._gcm=function(m){var bN=document."
                        "getElementById('gcm-net'),bW=document."
                        "getElementById('gcm-wr');if(m){bW.style."
                        "background='#1f6feb';bN.style.background=''}"
                        "else{bN.style.background='#1f6feb';bW.style."
                        "background=''}window._gdata()};"
                        "window._gt=function(){window._gdata()};"
                        "var _gpt=null;window._gp=function(){var b="
                        "document.getElementById('gtp');if(_gpt){"
                        "clearInterval(_gpt);_gpt=null;b.textContent="
                        "'▶ 播放';return}var s=document."
                        "getElementById('gts');if(!s||!BD.length)"
                        "return;b.textContent='⏸ 暂停';_gpt=setInterval("
                        "function(){if(+s.value>=+s.max){clearInterval("
                        "_gpt);_gpt=null;b.textContent='▶ 播放';return}"
                        "s.value=(+s.value)+1;window._gdata()},350)};"
                        "window._gdata();"
                        "window.addEventListener('resize',function()"
                        "{ec.resize()})})"
                        ".catch(function(){var el=document.getElementById"
                        "('kggeo');if(el)el.innerHTML='<div class=dim>"
                        "地图边界数据加载失败</div>'});})()</script>"
                        % (days, gj.get("unresolved") or 0,
                           gtmax, gtmax, grw,
                           _jsg.dumps(gj["cities"], ensure_ascii=False),
                           _jsg.dumps(gj["provs"], ensure_ascii=False),
                           _jsg.dumps(byday, ensure_ascii=False)))
                else:
                    geo_html = ("<div class='sec'><h2>🗺 游资地理分布</h2>"
                                "<div class='dim'>%s</div></div>"
                                % e(gj.get("error", "")))
                content = (
                    "<script>var _t=document.querySelector('.tabs');"
                    "if(_t){_t.style.pointerEvents='';_t.style.opacity='';}"
                    "var _l=document.getElementById('kgmap-loading');"
                    "if(_l&&_l.parentNode)_l.parentNode.removeChild(_l);"
                    "</script>"
                    "<div class='tabc on' id='kgt-ov'>%s%s%s</div>"
                    "<div class='tabc' id='kgt-net'>%s%s%s</div>"
                    "<div class='tabc' id='kgt-geo'>%s</div>"
                    "<div class='tabc' id='kgt-det'>"
                    "<div class='duo'>%s<div>%s</div></div></div></div>"
                    % (ov_html, comm_html, tc_html,
                       net_html, pair_html, alt_html, geo_html,
                       roster, flow_html))

        if streamed:
            # U4-A 流式收尾：骨架已在响应头阶段发出，这里只续发正文
            self._html_more(content + _BTSEL_TAG + "</body></html>")
        else:
            body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                    "<meta name='viewport' content='width=device-width,"
                    "initial-scale=1'><title>" + e(title) + "</title>"
                    + self._COCKPIT_CSS + self._KG_CSS + "</head><body>"
                    + pill_nav("kgmap") + content + "</body></html>")
            self._html(body)
    def _kg_company_page(self, qs):
        """KG-O 公司本体全景页：本体论视角看一家公司的全貌——Company
        为中心，六扇区（股东/高管/业务产品/行业归属含同业/游资/盘面
        事件）中心辐射点线边（G6 v5 本地托管 /static/g6.min.js）+
        四直方图（ECharts：实体类型分布/按日事件计数/游资净买/结构
        conf 分布）+ 关系统计表（主端/关系/对端/强度/次数，点行高亮
        该关系类型的边——理念移植自 知识图谱/_study/studio 千江图谱
        工作室 StatisticsPanel）。数据层 kg_portal.company_360。"""
        import html as _h
        import json as _json
        from urllib.parse import parse_qs as _pq, quote as _qt
        import sys as _sy
        if QBT_DIR not in _sy.path:
            _sy.path.insert(0, QBT_DIR)
        import kg_portal as KP
        e = _h.escape
        qp = _pq(qs)
        code = (qp.get("c") or [""])[0].strip()
        days = max(30, min(365, int((qp.get("d") or ["90"])[0] or 90)))
        per = (qp.get("p") or [""])[0].strip()

        def _fmt(v, suf=""):
            if v is None:
                return "—"
            try:
                fv = float(v)
                return ("%s%s" % (_fmtn(fv), suf)) if fv == fv else "—"
            except (TypeError, ValueError):
                return e(str(v))

        def _fmtn(f):
            return ("%d" % f) if f == int(f) else ("%.2f" % f)

        # 无代码 / 未找到：表单页
        if not code:
            body = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset="
                    "utf-8'><title>公司本体全景 · 图谱探索</title>"
                    + self._COCKPIT_CSS + self._KG_CSS + "</head><body>"
                    + pill_nav("kg")
                    + "<div class='sec'><h2>🏢 公司本体全景</h2>"
                      "<div class='dim' style='margin:4px 0 10px'>以公司"
                      "为本体中心（本体论/三元组/点线边），股东 · 高管 · "
                      "业务产品 · 行业同业 · 游资 · 盘面事件六扇区辐射 + "
                      "实体直方图落地。</div>"
                      "<form action='/kg/company' method='get' style="
                      "'display:flex;gap:8px'><input class='kfind' name='c' "
                      "placeholder='ts_code，如 600519.SH 或 002743.SZ'>"
                      "<button class='kfbtn'>🏢 打开全景</button></form>"
                    + "</body></html>")
            self._html(body)
            return
        d = KP.api("company360", code, days, per=per or None)
        if not d.get("ok"):
            body = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset="
                    "utf-8'><title>公司本体全景 · 图谱探索</title>"
                    + self._COCKPIT_CSS + self._KG_CSS + "</head><body>"
                    + pill_nav("kg")
                    + "<div class='sec'><h2>🏢 公司本体全景</h2>"
                      "<div class='dn'>" + e(d.get("error", "查询失败"))
                      + "</div>"
                      "<form action='/kg/company' method='get' style="
                      "'display:flex;gap:8px;margin-top:10px'>"
                      "<input class='kfind' name='c' placeholder="
                      "'ts_code，如 600519.SH'>"
                      "<button class='kfbtn'>🏢 打开全景</button></form>"
                      "</div></body></html>")
            self._html(body)
            return

        b = d["basic"]
        payload = _json.dumps(d, ensure_ascii=False, separators=(",", ":"))
        title = "%s 公司本体全景" % (b.get("name") or code)
        pills = "".join(
            "<span style='%s'>%d天</span>" % (_PILL_ON, dd) if dd == days
            else "<a href='/kg/company?c=%s&d=%d' style='%s'>%d天</a>"
            % (e(code), dd, _PILL, dd) for dd in (90, 180, 365))
        hp = d.get("hist", {}).get("hold_periods") or []
        if len(hp) > 1:
            per0 = d.get("per") or ""
            pl = lambda pp: (pp[:4], pp[4:6]) if len(pp) == 8 else (
                pp[:4], pp[5:7])
            pills += ("<span class='dim' style='margin-left:6px'>股东报告期"
                      "：</span>" + "".join(
                "<span style='%s'>%s-%s</span>" % (_PILL_ON, *pl(pp))
                if pp == per0 else
                "<a href='/kg/company?c=%s&d=%d&p=%s' style='%s'>%s-%s</a>"
                % ((e(code), days, e(pp), _PILL) + pl(pp)) for pp in hp))
        chips = "".join(
            "<span class='bdg%s'>%s</span>" % (cls, txt) for cls, txt in (
                ("", "行业 %s" % e(b.get("industry") or "—")),
                ("", "申万 %s" % e(b.get("sw_l1") or "—")),
                ("", "区域 %s" % e(b.get("area") or "—")),
                ("", "上市 %s" % e(b.get("list_date") or "—")),
                (" y", "董事长 %s" % e(b.get("chairman") or "—")),
                ("", "总经理 %s" % e(b.get("manager") or "—")),
                ("", "员工 %s" % _fmt(b.get("employees"))),
                ("", "注册资本 %s 万" % _fmt(b.get("reg_capital")))))
        len(d.get("hist", {}).get("day_events") or [])
        head = (
            "<div class='sec'><h2>🏢 公司本体全景 · %s <span class='dim' "
            "style='font-weight:400;font-size:13px'>%s</span></h2>"
            "<div style='margin:4px 0 6px'>%s</div>"
            "<div class='dim' style='margin:2px 0 6px'>⇄ "
            "<a href='/kg?c=%s'>图谱视角 →</a> · "
            "<a href='/kg/stock/%s'>量化全景（K线/时间轴/AI）→</a> · "
            "窗口 %d 天 · 三元组 %d 组（点%d 边%d）</div>"
            "<div style='display:flex;gap:14px;align-items:center;"
            "flex-wrap:wrap'><div>%s</div>"
            "<form action='/kg/company' method='get' style='display:flex;"
            "gap:6px;margin:0'><input class='kfind' name='c' value='%s' "
            "style='min-width:140px;width:180px' placeholder='ts_code'>"
            "<button class='kfbtn'>切换</button></form></div></div>\n"
            "<div class='sec'><div id='c360lg' style='margin:2px 0 6px;"
            "display:flex;gap:8px;flex-wrap:wrap;align-items:center'>"
            "</div><div style='display:flex;gap:10px;align-items:stretch'>"
            "<div id='c360g' style='flex:1.7;min-width:0;height:620px;"
            "border:1px solid var(--gray-4);border-radius:8px;background:"
            "var(--gray-0)'></div>"
            "<div id='c360d' style='flex:1;min-width:260px;max-width:340px;"
            "border:1px solid var(--gray-4);border-radius:8px;background:var(--gray-0);"
            "padding:10px 12px;overflow:auto'></div></div>"
            "<div id='c360tip' style='display:none;position:fixed;z-index:"
            "98;background:var(--gray-2);border:1px solid var(--gray-4);border-radius:"
            "8px;padding:8px 10px;font-size:12px;color:var(--gray-7);pointer-"
            "events:none;max-width:340px;box-shadow:0 4px 14px rgba(0,0,"
            "0,.5)'></div></div>\n"
            "<div class='sec'><h2>📊 实体直方图（数据落地）</h2>"
            "<div class='dim' style='margin:2px 0 6px'>左上：本体六扇区"
            "实体类型分布 · 右上：按日盘面事件计数 · 左下：游资净买分布"
            "（红=净买 / 绿=净卖）· 右下：结构信号置信度分布<br>"
            "点击柱子=反筛图谱（命中实体保留、其余压暗，再点同柱取消；"
            "可与关系筛选叠加）</div>"
            "<div style='display:grid;grid-template-columns:1fr 1fr;gap:"
            "10px'><div id='h_type' style='height:230px;border:1px solid "
            "var(--gray-4);border-radius:8px;background:var(--gray-0)'></div>"
            "<div id='h_day' style='height:230px;border:1px solid var(--gray-4);"
            "border-radius:8px;background:var(--gray-0)'></div>"
            "<div id='h_hm' style='height:230px;border:1px solid var(--gray-4);"
            "border-radius:8px;background:var(--gray-0)'></div>"
            "<div id='h_conf' style='height:230px;border:1px solid var(--gray-4);"
            "border-radius:8px;background:var(--gray-0)'></div></div></div>\n"
            "<div class='sec'><h2>🔗 关系统计（主端 · 关系 · 对端 · 强度"
            " · 次数）</h2><div class='dim' style='margin:2px 0 6px'>对标 "
            "graph-studio StatisticsPanel——点行=高亮该关系类型的边并压暗"
            "无关节点（再点还原）；扇区图例点按=隐藏/显示整扇区</div>"
            "<table class='sticky' id='c360rt'><tr><th>主端</th><th>关系"
            "</th><th>对端</th><th>强度</th><th>次数</th><th>备注</th>"
            "</tr></table></div>\n"
            "<div class='sec'><h2>📝 公司底档</h2><div class='dim' style="
            "'line-height:1.8'><b style='color:var(--gray-7)'>%s</b><br>主营："
            "%s<br>%s</div></div>\n"
            "<script src='/static/g6.min.js'></script>\n"
            "<script src='/static/echarts.min.js'></script>\n<script>\n")
        head = head % (
            e(b.get("name") or code), e(code), chips, e(code),
            _qt(b.get("name") or code), days, len(d["rel_stats"]),
            len(d["nodes"]), len(d["edges"]), pills, e(code),
            e(b.get("company") or "—"),
            e(b.get("main_business") or "—"),
            e(b.get("introduction") or "—"))
        tail = r'''(function(){
var D=__P__;
var SEC={security:{n:'标的',c:'#58a6ff'},holder:{n:'股东',c:'#d29922'},
  person:{n:'高管',c:'#a371f7'},product:{n:'业务/产品',c:'#3fb950'},
  taxonomy:{n:'行业/同业/概念',c:'#79c0ff'},hotmoney:{n:'游资',c:'#f778ba'},
  event:{n:'盘面事件',c:'#ffa657'}};
var ORDER=['security','holder','person','product','taxonomy','hotmoney',
  'event'];
var CUT={holder:10,person:6,product:8,taxonomy:9,hotmoney:9,event:13,
  security:22};
var CO=null,hidden={},RELSEL=null,RELROW=-1,SELID=null,HSEL=null,
  HKEY=null,HLAB='',graph=null;
D.nodes.forEach(function(n){if(n.sector==='company')CO=n;});
var mw={};
D.nodes.forEach(function(n){if(n.sector==='company')return;
  mw[n.sector]=Math.max(mw[n.sector]||1,n.w||1);});
var mew=1;
D.edges.forEach(function(x){if((x.w||1)>mew)mew=x.w||1;});
var secOf={};
D.nodes.forEach(function(n){secOf[n.id]=n.sector;});
function cut(s,n){s=String(s==null?'':s);return s.length>n?s.slice(0,n)+'…':s;}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,
  function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function sizeOf(n){if(n.sector==='company')return 56;
  if(n.sector==='security')return 34;
  return Math.round(13+22*Math.sqrt((n.w||1)/(mw[n.sector]||1)));}
function edgeColor(x){var st=secOf[x.s]||'',tt=secOf[x.t]||'';
  if(x.rel==='OF_COMPANY')return '#e6edf3';
  if(x.rel==='DRIVE')return '#8b949e';
  return (SEC[tt]&&SEC[tt].c)||'#58a6ff';}
// ── 中心辐射·扇区分组定位（确定性极坐标，无布局引擎）──
function buildPos(){
  var box=document.getElementById('c360g');
  var W=box.clientWidth||900,H=box.clientHeight||620;
  var cx=W/2,cy=H/2,R=Math.min(W,H)/2-28;
  var rings=[[0.36,5],[0.56,7],[0.78,10]];
  var byS={},pos={};
  D.nodes.forEach(function(n){if(n.sector==='company')return;
    (byS[n.sector]=byS[n.sector]||[]).push(n);});
  var live=ORDER.filter(function(s){return byS[s]&&byS[s].length;});
  var NA=live.length||1,a0=-Math.PI/2,span=2*Math.PI/NA,gap=span*0.14;
  live.forEach(function(s,ai){
    var arr=byS[s],ac=a0+span*ai+span/2,idx=0;
    rings.forEach(function(rg){
      var take=arr.slice(idx,idx+rg[1]);idx+=rg[1];
      var m=take.length;if(!m)return;
      var half=Math.max(span-gap,0.25)/2;
      take.forEach(function(n,i){
        var t=(m===1)?0.5:i/(m-1);
        var ang=ac-half+t*half*2;
        pos[n.id]=[cx+Math.cos(ang)*R*rg[0],cy+Math.sin(ang)*R*rg[0]];
      });});});
  pos[CO.id]=[cx,cy];
  return pos;}
function relOK(x){return !hidden[secOf[x.s]]&&!hidden[secOf[x.t]];}
function nodesBuild(pos){
  var ns=[];
  D.nodes.forEach(function(n){
    if(hidden[n.sector]&&n.sector!=='company')return;
    var p=pos[n.id];if(!p)return;
    var dimmed=(RELSEL&&!hitRel(n.id))||(HSEL&&!hitH(n.id))||(SELID
      &&SELID!==n.id&&!adjOf(SELID,n.id));
    var isCo=n.sector==='company';
    ns.push({id:n.id,type:(n.kind==='src'?'rect':'circle'),style:{
      x:p[0],y:p[1],size:sizeOf(n),
      fill:(SEC[n.sector]&&SEC[n.sector].c)||'#21262d',
      fillOpacity:isCo?0.95:(dimmed?0.12:(n.past?0.38:
        (n.kind==='src'?0.55:0.85))),
      stroke:SELID===n.id?'#e6edf3':(isCo?'#58a6ff':'#30363d'),
      lineWidth:SELID===n.id?3:(isCo?2.5:1.5),
      lineDash:n.past?[3,3]:null,
      labelText:cut(n.label,CUT[n.sector]||10),
      labelFill:isCo?'#e6edf3':'#8b949e',
      labelFontSize:isCo?13:10,labelPlacement:'bottom',
      labelWordSpacing:1}});});
  return ns;}
function edgesBuild(){
  var es=[];
  D.edges.forEach(function(x,i){
    if(!relOK(x))return;
    var on=(!RELSEL||x.rel===RELSEL)&&(!HSEL||(HSEL[x.s]||HSEL[x.t]));
    es.push({id:'L'+i,source:x.s,target:x.t,style:{
      stroke:on?edgeColor(x):'#30363d',
      strokeOpacity:on?(x.rel==='DRIVE'?0.55:0.5):0.05,
      lineWidth:(on?1:0.5)+(on?4*Math.sqrt((x.w||1)/mew):0),
      lineDash:(x.rel==='DRIVE')?[4,3]:null}});});
  return es;}
function redraw(){if(!graph)return;
  graph.setData({nodes:nodesBuild(buildPos()),edges:edgesBuild()});
  graph.render();}
// ── 邻接与关系命中（选中/筛选联动）──
var adj={},byRel={};
D.edges.forEach(function(x){
  (adj[x.s]=adj[x.s]||{})[x.t]=1;(adj[x.t]=adj[x.t]||{})[x.s]=1;
  (byRel[x.rel]=byRel[x.rel]||{})[x.s]=1;(byRel[x.rel]=byRel[x.rel]||{})[x.t]=1;});
function adjOf(a,b){return !a||!b||(adj[a]&&adj[a][b]);}
function hitRel(id){return !RELSEL||(byRel[RELSEL]&&byRel[RELSEL][id]);}
function hitH(id){return !HSEL||id===CO.id||!!HSEL[id];}
// ── 图例（扇区开关）──
function legend(){
  var cnt={};
  D.nodes.forEach(function(n){cnt[n.sector]=(cnt[n.sector]||0)+1;});
  var h=ORDER.filter(function(s){return cnt[s];}).map(function(s){
    var off=hidden[s];
    return '<button onclick="window._c3sec(\''+s+'\')" style="cursor:'
      +'pointer;background:var(--gray-3);border:1px solid var(--gray-4);border-radius:'
      +'16px;font-size:12px;padding:3px 10px;color:'+(off?'#484f58'
      :(SEC[s].c))+';text-decoration:'+(off?'line-through':'none')+'">'
      +'<span style="display:inline-block;width:9px;height:9px;border-'
      +'radius:50%;background:'+(SEC[s].c)+';opacity:'+(off?0.25:1)
      +';margin-right:5px;vertical-align:-1px"></span>'+SEC[s].n+' '
      +cnt[s]+'</button>';}).join('');
  var h2='<span class="dim" style="font-size:12px">中心=公司本体 · 节点'
    +'大小∝扇区内权重 · 边色=对端扇区 · 虚线=事件来源（DRIVE）'
    +' · 高管虚线圆=历任</span>';
  if(RELSEL)h2+=' <button onclick="window._c3rel(\'\')" style="cursor:'
    +'pointer;background:var(--gray-3);border:1px solid var(--red-500);border-radius:'
    +'16px;font-size:12px;padding:3px 10px;color:var(--red-500)">✕ 清除关系筛选: '
    +esc(RELSEL)+'</button>';
  if(HSEL)h2+=' <button onclick="window._c3hs()" style="cursor:pointer;'
    +'background:var(--gray-3);border:1px solid var(--ylw-500);border-radius:16px;'
    +'font-size:12px;padding:3px 10px;color:var(--ylw-500)">✕ 清除直方图筛选: '
    +esc(HLAB)+'</button>';
  document.getElementById('c360lg').innerHTML=h+' '+h2;}
// ── 详情面板 ──
function hrefsOf(n){
  var out=[];
  if(n.sector==='security')
    out.push(['图谱视角','/kg?c='+encodeURIComponent(D.code)],
             ['量化全景','/kg/stock/'+encodeURIComponent(
               D.basic.name||D.code)]);
  if(n.sector==='hotmoney')
    out.push(['游资画像','/kg/map?hm='+encodeURIComponent(n.label)]);
  if(n.props&&n.props['同业标的']){
    var nm=String(n.props['同业标的']).split(' ').slice(1).join(' ');
    if(nm)out.push(['量化全景','/kg/stock/'+encodeURIComponent(nm)]);}
  if(n.sector==='taxonomy'&&/^(sh|sz|bj)/.test(String(n.id)))
    out.push(['图谱视角','/kg?c='+encodeURIComponent(n.id)]);
  return out;}
function detailOf(n){
  var rows='';
  if(n.props)Object.keys(n.props).forEach(function(k){
    var v=n.props[k];if(v==null||v==='')return;
    rows+='<tr><td class="dim" style="white-space:nowrap">'+esc(k)
      +'</td><td>'+esc(String(v).slice(0,80))+'</td></tr>';});
  var lk=hrefsOf(n).map(function(x){
    return '<a href="'+x[1]+'" style="display:inline-block;margin:4px 8px '
      +'0 0;font-size:12px;color:var(--blu-500)">'+x[0]+' →</a>';}).join('');
  return '<div style="font-weight:600;font-size:14px;margin:2px 0 6px">'
    +'<span style="display:inline-block;width:11px;height:11px;border-'
    +'radius:50%;background:'+((SEC[n.sector]||{}).c||'var(--gray-5)')
    +';margin-right:6px"></span>'+esc(n.label)+'</div>'
    +'<div class="dim" style="font-size:12px;margin-bottom:6px">扇区：'
    +((SEC[n.sector]||{}).n||n.sector)+(n.kind==='src'?'（事件来源）':'')
    +'</div><table class="sticky">'+(rows
      ||'<tr><td class="dim">无附加属性</td></tr>')+'</table>'+lk;}
function detailDefault(){
  var cnt={};
  D.nodes.forEach(function(n){if(n.sector!=='company')
    cnt[n.sector]=(cnt[n.sector]||0)+1;});
  var t=ORDER.filter(function(s){return cnt[s];}).map(function(s){
    return '<tr><td><span style="display:inline-block;width:8px;height:8px;'
      +'border-radius:50%;background:'+SEC[s].c+';margin-right:5px">'
      +'</span>'+SEC[s].n+'</td><td>'+cnt[s]+'</td></tr>';}).join('');
  document.getElementById('c360d').innerHTML=
    '<div style="font-weight:600;font-size:13px;margin:2px 0 6px">'
    +esc(D.basic.company||D.basic.name||D.code)+' · 全景摘要</div>'
    +'<div class="dim" style="font-size:12px;line-height:1.7">节点 '
    +D.nodes.length+' · 边 '+D.edges.length+' · 窗口 '
    +D.window_days+' 天<br>单击节点=详情+跳转 · 点图例=隐藏扇区<br>'
    +'点关系统计行=高亮该关系 · 点直方图柱=反筛</div>'
    +'<table class="sticky" style="margin-top:6px"><tr><th>扇区</th>'
    +'<th>实体</th></tr>'+t+'</table>'
    +'<div class="dim" style="font-size:12px;margin-top:8px;line-height:1.7">'
    +esc(String(D.basic.introduction||D.basic.main_business||'').slice(0,180))
    +(String(D.basic.introduction||D.basic.main_business||'').length>180
      ?'…':'')+'</div>';}
// ── 关系统计表 ──
function relTable(){
  var tb=document.getElementById('c360rt');
  tb.querySelectorAll('tr').forEach(function(tr,i){if(i)tr.remove();});
  D.rel_stats.forEach(function(r,i){
    var tr=document.createElement('tr');
    tr.style.cursor='pointer';
    tr.innerHTML='<td>'+esc(r.src)+'</td><td><span style="font-family:'
      +'monospace;color:var(--blu-500)">'+esc(r.rel)+'</span></td><td>'+esc(r.dst)
      +'</td><td class="bdg n">'+esc(r.strength)+'</td><td>'+r.count
      +'</td><td class="dim">'+esc(r.note||'')+'</td>';
    tr.onclick=function(){window._c3rel(r.rel,i);};
    if(i===RELROW)tr.style.background='#161b22';
    tb.appendChild(tr);});}
// ── 交互入口 ──
window._c3sec=function(s){hidden[s]=!hidden[s];legend();redraw();};
window._c3rel=function(rel,i){
  if(!rel||RELSEL===rel){RELSEL=null;RELROW=-1;}
  else{RELSEL=rel;RELROW=(i==null?-1:i);}
  legend();relTable();redraw();};
window._c3hs=function(){HSEL=null;HKEY=null;HLAB='';hsPaint();legend();
  redraw();};
// ── G6 画布 ──
var tip=document.getElementById('c360tip');
function tipShow(html,x,y){tip.innerHTML=html;tip.style.display='block';
  tip.style.left=Math.min(x+14,innerWidth-360)+'px';
  tip.style.top=Math.max(8,y-14)+'px';}
try{
  graph=new G6.Graph({container:'c360g',autoFit:'view',animation:false,
    behaviors:['drag-canvas','zoom-canvas','drag-element']});
  graph.setData({nodes:nodesBuild(buildPos()),edges:edgesBuild()});
  graph.on('node:click',function(ev){var id=ev.itemId||ev.target.id;
    if(!id)return;
    var n=null;D.nodes.forEach(function(x){if(x.id===id)n=x;});
    if(!n)return;
    LASTC=Date.now();
    SELID=(SELID===id)?null:id;
    if(SELID)document.getElementById('c360d').innerHTML=detailOf(n);
    else detailDefault();
    redraw();});
  graph.on('node:mouseenter',function(ev){var id=ev.itemId||ev.target.id;
    var n=null;D.nodes.forEach(function(x){if(x.id===id)n=x;});
    if(!n)return;
    var h='<b style="font-size:13px">'+esc(n.label)+'</b><br>'
      +'<span style="color:var(--gray-5)">'+((SEC[n.sector]||{}).n||n.sector)
      +(n.kind==='src'?'（事件来源）':'')+'</span>';
    if(n.props)Object.keys(n.props).slice(0,5).forEach(function(k){
      var v=n.props[k];if(v==null||v==='')return;
      h+='<br>'+esc(k)+'：'+esc(String(v).slice(0,44));});
    var xy=ev.client||{x:ev.clientX,y:ev.clientY};
    tipShow(h,xy.x,xy.y);});
  graph.on('node:mousemove',function(ev){
    var x=ev.client?ev.client.x:ev.clientX,y=ev.client?ev.client.y:ev.clientY;
    tip.style.left=Math.min(x+14,innerWidth-360)+'px';
    tip.style.top=Math.max(8,y-40)+'px';});
  graph.on('node:mouseleave',function(){tip.style.display='none';});
  graph.render();
  // ── hover/click 兜底：G6 事件桥只认事件层 canvas 上的 PointerEvent
  // （pointermove 无包装直 tap 且 copyMouseData 期望 federated 属性、
  // pointerdown/up 绑 domElement），CDP/自动化与部分环境只发原生
  // MouseEvent 走不通 → 容器级原生监听 + getCanvasByClient 几何命中
  // 补位；与 G6 真实交互幂等共存（LASTC 60ms 去重 + 拖拽位移抑制）──
  try{
    var gbox=document.getElementById('c360g');
    var HOVN=null,HOVH='',LASTC=0,PD=null;
    function hitNode(e){
      var p=null;
      try{var a=graph.getCanvasByClient([e.clientX,e.clientY]);
        p={x:(a&&a.x!=null)?a.x:a[0],y:(a&&a.y!=null)?a.y:a[1]};}
      catch(_){p=null;}
      if(!p)return null;
      var pos=buildPos(),hit=null;
      D.nodes.forEach(function(x){
        if(hidden[x.sector]&&x.sector!=='company')return;
        var q=pos[x.id];if(!q)return;
        var m=sizeOf(x)/2+8,dx=p.x-q[0],dy=p.y-q[1];
        if(dx*dx+dy*dy<=m*m)hit=x;});
      return hit;}
    gbox.addEventListener('pointermove',function(e){
      var hit=hitNode(e);
      if(!hit){
        if(HOVN){HOVN=null;tip.style.display='none';}
        return;}
      if(HOVN!==hit.id){
        var h='<b style="font-size:13px">'+esc(hit.label)+'</b><br>'
          +'<span style="color:var(--gray-5)">'
          +((SEC[hit.sector]||{}).n||hit.sector)
          +(hit.kind==='src'?'（事件来源）':'')+'</span>';
        if(hit.props)Object.keys(hit.props).slice(0,5).forEach(function(k){
          var v=hit.props[k];if(v==null||v==='')return;
          h+='<br>'+esc(k)+'：'+esc(String(v).slice(0,44));});
        HOVN=hit.id;HOVH=h;}
      tipShow(HOVH,e.clientX,e.clientY);},false);
    gbox.addEventListener('pointerleave',function(){
      if(HOVN){HOVN=null;tip.style.display='none';}},false);
    // click 兜底：无 PointerEvent 环境的原生 click 复刻 node:click；
    // PD 位移>8 抑制拖画布/拖节点松手误触
    gbox.addEventListener('pointerdown',function(e){
      PD={x:e.clientX,y:e.clientY};},false);
    gbox.addEventListener('click',function(e){
      var ok=(Date.now()-LASTC>=60);
      if(ok&&PD&&(Math.abs(e.clientX-PD.x)+Math.abs(e.clientY-PD.y))>8)
        ok=false;
      PD=null;
      if(!ok)return;
      var n=hitNode(e);if(!n)return;
      LASTC=Date.now();
      SELID=(SELID===n.id)?null:n.id;
      if(SELID)document.getElementById('c360d').innerHTML=detailOf(n);
      else detailDefault();
      redraw();},false);
  }catch(e2){}
}catch(err){
  document.getElementById('c360g').innerHTML='<div style="color:var(--red-500);'
    +'padding:20px;font-size:13px">G6 画布初始化失败：'+esc(err.message)
    +'</div>';}
legend();relTable();detailDefault();
// ── ECharts 四直方图（点击柱=反筛图谱：命中实体保留、其余压暗，
// 再点同柱取消；选中柱加白描边；与关系筛选/节点选中叠加）──
var AX={axisLine:{lineStyle:{color:'#30363d'}},axisLabel:{color:'#8b949e',
  fontSize:11},splitLine:{lineStyle:{color:'#21262d'}}};
var CH={},HB={};window._c3CH=CH;
function mk(id,builder){try{var c=echarts.init(document.getElementById(id));
  CH[id]=c;c.setOption(builder(-1));}catch(e){
  document.getElementById(id).innerHTML='<div style="color:var(--gray-5);'
    +'padding:20px;font-size:12px">图表初始化失败</div>';}}
function hsel(key,set,lab){
  if(HKEY===key){HKEY=null;HSEL=null;HLAB='';}
  else{HKEY=key;HSEL=set;HLAB=lab;}
  hsPaint();legend();redraw();}
function hsPaint(){['h_type','h_day','h_hm','h_conf'].forEach(function(id){
  if(!CH[id]||!HB[id])return;var sel=-1;
  if(HKEY&&HKEY.slice(0,id.length)===id&&HKEY.charAt(id.length)===':')
    sel=parseInt(HKEY.slice(id.length+1),10);
  try{CH[id].setOption(HB[id](sel));}catch(_){}});}
var TCD=(D.hist.type_counts||[]).slice().reverse();
HB.h_type=function(sel){return {backgroundColor:'transparent',
  grid:{left:8,right:16,top:28,bottom:8,containLabel:true},
  tooltip:{trigger:'axis'},
  title:{text:'实体类型分布（六扇区）',textStyle:{color:'#8b949e',
  fontSize:12},left:8,top:4},
  xAxis:Object.assign({type:'value',minInterval:1},AX),
  yAxis:Object.assign({type:'category',data:TCD.map(function(x){
    return (SEC[x[0]]||{}).n||x[0];})},AX),
  series:[{type:'bar',barMaxWidth:22,
    data:TCD.map(function(x,i){return {value:x[1],itemStyle:{
      color:(SEC[x[0]]||{}).c||'#58a6ff',borderColor:'#e6edf3',
      borderWidth:i===sel?2:0}};})}]};};
mk('h_type',HB.h_type);
CH.h_type.on('click',function(p){var i=p.dataIndex,x=TCD[i];if(!x)return;
  var set={};D.nodes.forEach(function(n){if(n.sector===x[0])set[n.id]=1;});
  hsel('h_type:'+i,set,((SEC[x[0]]||{}).n||x[0])+' '+x[1]);});
var DAYE=D.hist.day_events||[];
HB.h_day=function(sel){return {backgroundColor:'transparent',
  grid:{left:8,right:16,top:28,bottom:8,containLabel:true},
  tooltip:{trigger:'axis'},
  title:{text:'按日盘面事件计数（'+D.window_days+'天 · '
    +DAYE.length+' 个事件日）',textStyle:{color:'#8b949e',fontSize:12},
    left:8,top:4},
  xAxis:Object.assign({type:'category',data:DAYE.map(function(x){
    return x[0].slice(5);})},AX),
  yAxis:Object.assign({type:'value',minInterval:1},AX),
  series:[{type:'bar',barMaxWidth:26,
    data:DAYE.map(function(x,i){return {value:x[1],itemStyle:{color:
      '#ffa657',borderColor:'#e6edf3',borderWidth:i===sel?2:0}};})}]};};
mk('h_day',HB.h_day);
CH.h_day.on('click',function(p){var i=p.dataIndex,x=DAYE[i];if(!x)return;
  var set={};D.nodes.forEach(function(n){
    if(n.props&&n.props['日期']===x[0])set[n.id]=1;});
  hsel('h_day:'+i,set,'事件日 '+x[0].slice(5)+' · '+x[1]+'条');});
var HMN=D.hist.hm_net||[];
HB.h_hm=function(sel){return {backgroundColor:'transparent',
  grid:{left:8,right:16,top:28,bottom:8,containLabel:true},
  tooltip:{trigger:'axis',
  valueFormatter:function(v){return v+' 百万';}},
  title:{text:'游资净买分布（百万 · 红=净买/绿=净卖）',textStyle:{color:
    '#8b949e',fontSize:12},left:8,top:4},
  xAxis:Object.assign({type:'category',data:HMN.map(function(x){
    return x.k;})},AX),
  yAxis:Object.assign({type:'value'},AX),
  series:[{type:'bar',barMaxWidth:26,
    data:HMN.map(function(x,i){return {value:x.net,itemStyle:{color:
      x.net>=0?'#f85149':'#3fb950',borderColor:'#e6edf3',
      borderWidth:i===sel?2:0}};})}]};};
mk('h_hm',HB.h_hm);
CH.h_hm.on('click',function(p){var i=p.dataIndex,x=HMN[i];if(!x)return;
  var set={};D.nodes.forEach(function(n){
    if(n.sector==='hotmoney'&&n.label===x.k)set[n.id]=1;});
  hsel('h_hm:'+i,set,'游资 '+x.k);});
var CFB=D.hist.conf_bins||[];
var CB_LO=[0,0.2,0.4,0.6,0.8],CB_HI=[0.2,0.4,0.6,0.8,1.01];
HB.h_conf=function(sel){return {backgroundColor:'transparent',
  grid:{left:8,right:16,top:28,bottom:8,containLabel:true},
  tooltip:{trigger:'axis'},
  title:{text:'结构信号置信度分布（n='+D.hist.conf_n+'）',
  textStyle:{color:'#8b949e',fontSize:12},left:8,top:4},
  xAxis:Object.assign({type:'category',data:CFB.map(function(x){
    return x.label;})},AX),
  yAxis:Object.assign({type:'value',minInterval:1},AX),
  series:[{type:'bar',barMaxWidth:42,
    data:CFB.map(function(x,i){return {value:x.n,itemStyle:{color:[
      '#1f6feb','#388bfd','#58a6ff','#79c0ff','#a5d6ff'][i]||'#58a6ff',
      borderColor:'#e6edf3',borderWidth:i===sel?2:0}};})}]};};
mk('h_conf',HB.h_conf);
CH.h_conf.on('click',function(p){var i=p.dataIndex,x=CFB[i];if(!x)return;
  var lo=(CB_LO[i]==null?0:CB_LO[i]),hi=(CB_HI[i]==null?1.01:CB_HI[i]);
  var set={};D.nodes.forEach(function(n){
    var c=n.props&&n.props['置信度'];
    if(typeof c==='number'&&c>=lo&&c<hi)set[n.id]=1;});
  hsel('h_conf:'+i,set,'conf '+x.label);});
})();'''
        tail = tail.replace("__P__", payload)
        body = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset="
                "utf-8'><meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>" + e(title) + " · 图谱探索"
                "</title>" + self._COCKPIT_CSS + self._KG_CSS + "</head>"
                "<body>" + pill_nav("kg") + head + tail + "\n</script>\n"
                "</body></html>")
        self._html(body)
    def _kg_net_html(self, mg):
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
    def _kg_overview_html(self, ov):
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
    def _kg_page(self, qs):
        """M3-W2 情报探索页：图谱概览 / 个股全景 / 游资归因（服务端渲染，
        数据层 quant_backtest/kg_portal.py 纯查询）。"""
        import html as _h
        from urllib.parse import parse_qs as _pq, quote as _qt
        import sys as _sy
        if QBT_DIR not in _sy.path:
            _sy.path.insert(0, QBT_DIR)
        import kg_portal as KP
        e = _h.escape
        qp = _pq(qs)
        code = (qp.get("c") or [""])[0].strip()
        hm = (qp.get("hm") or [""])[0].strip()
        q = (qp.get("q") or [""])[0].strip()
        days = max(7, min(365, int((qp.get("d") or ["120"])[0] or 120)))

        def tcls(t):
            return ("g" if "买" in t else "r" if "卖" in t else
                    "y" if "背驰" in t else "n")

        def tagb(tag):
            if tag == "follow_confirm":
                return "<span class='bdg g'>跟随确认</span>"
            if tag == "lure_warn":
                return "<span class='bdg r'>诱多警示</span>"
            return ""

        def stock_link(tc, nm):
            return "<a href='/kg?c=%s'>%s %s</a>" % (e(tc), e(tc), e(nm or ""))

        # ── 搜索框（所有视图常驻）──
        search = ("<form action='/kg' method='get' style='margin:0 0 16px;"
                  "display:flex;gap:8px'><input class='kfind' name='q' "
                  "placeholder='代码 / 名称，如 002743 或 富煌' value='%s'>"
                  "<button class='kfbtn'>🔍 探索</button></form>" % e(q))

        content = ""
        title = "图谱探索"

        # ── ① 搜索结果 ──
        if q:
            lk = KP.api("lookup", q)
            if not lk.get("ok"):
                content = "<div class='sec'><div class='dn'>%s</div></div>" \
                    % e(lk.get("error", "查询失败"))
            else:
                rows = "".join(
                    "<div style='margin:6px 0'>%s <span class='dim'>%s</span></div>"
                    % (stock_link(m["tc"], m["nm"]), e(m["ind"] or ""))
                    for m in lk["matches"]) or \
                    "<div class='dim'>无匹配标的</div>"
                content = ("<div class='sec'><h2>搜索 “%s” · %d 个匹配</h2>%s</div>"
                           % (e(q), len(lk["matches"]), rows))
                if len(lk["matches"]) == 1:  # 唯一命中直接进个股视图
                    code = lk["matches"][0]["tc"]

        # ── ② 个股全景 ──
        if code:
            st = KP.api("stock", code, days)
            tr = KP.api("trace", code, min(days, 90))
            title = "%s 图谱" % (st.get("basic", {}).get("name") or code)
            if not st.get("ok"):
                content += ("<div class='sec'><div class='dn'>%s</div></div>"
                            % e(st.get("error", "")))
            else:
                b = st["basic"]
                # 窗口切换
                pills = "".join(
                    "<span style='%s'>%d天</span>" % (_PILL_ON, d) if d == days
                    else "<a href='/kg?c=%s&d=%d' style='%s'>%d天</a>"
                    % (e(code), d, _PILL, d) for d in (30, 60, 120, 250))
                # S3 双向跳转：图谱视角 ⇄ 量化全景（K线/策略/时间轴/AI）
                content += (
                    "<div class='dim' style='margin:4px 0 8px'>⇄ "
                    "<a href='/kg/stock/%s'>量化全景（K线 / 策略信号 / "
                    "全事件时间轴 / AI 分析）→</a> · <a href='/kg/company"
                    "?c=%s'>🏢 公司本体全景（六扇区点线边 + 直方图）→"
                    "</a></div>"
                    % (_qt(b.get("name") or code), e(code)))
                # L2 Object View 扇区体检条：声明式装配（kg_objectview），
                # 复用已取的 st 零额外查询；新增扇区=配置加一行此条自动带出
                try:
                    import kg_objectview as _OV
                    ov = _OV.assemble(st)
                    _GCLS = {"ok": "g", "stale": "r", "empty": "",
                             "static": "y"}
                    _FQLB = {"D": "日频", "E": "触发式", "Q": "季频",
                             "M": "月频", "static": "静态"}
                    chips = "".join(
                        "<span class='bdg %s' style='margin:2px 6px 2px 0'"
                        " title=\"%s · 信源 %s · %s 档 · %s\">%s · %d · %s"
                        "%s</span>"
                        % (_GCLS.get(s["grade"], ""),
                           e(s["note"] or s["title"]),
                           e(s["source"] or "—"),
                           s["trust"] or "—",
                           _FQLB.get(s["freq"], s["freq"]),
                           e(s["title"]), s["n"],
                           s["fresh"] or "—",
                           ("（滞后%d%s）"
                            % (s["lag"], "交易日" if s["lag_unit"] == "td"
                               else "天")
                            if s["lag"] is not None else ""))
                        for s in ov["sectors"])
                    content += (
                        "<div class='sec'><h2>Object View · 扇区体检</h2>"
                        "<div style='line-height:2.1'>%s</div>"
                        "<div class='dim'>徽章=扇区状态（绿=新鲜 红=连续型"
                        "断流 灰=无数据 黄=静态）· 数字=观测时点/滞后天 · "
                        "悬停看信源信任档。触发式扇区（龙虎榜/涨跌停类）"
                        "无数据≠断流。</div></div>" % chips)
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
                # ACH 三假设条
                ach = tr.get("ach", {}) if tr.get("ok") else {}
                bars = "".join(
                    "<div class='dim' style='display:flex;align-items:center;"
                    "gap:8px;margin:4px 0'><span style='width:86px;"
                    "flex:none'>%s</span><span class='hb' style='width:%dpx'>"
                    "</span><b style='color:var(--gray-7)'>%.2f</b></div>"
                    % (e(h), int(v * 180), v)
                    for h, v in (ach.get("scores") or {}).items())
                h3 = ach.get("h3") or {}
                ach_html = ("<div class='sec'><h2>ACH 竞争性假设（窗口 %d 天）</h2>"
                            "<div style='margin:6px 0 10px;font-size:14px'>"
                            "<span class='bdg y'>收敛</span>%s</div>%s"
                            "<div class='dim'>H1 实质证据 %d 组 · H2 游资净买 "
                            "%.0f 百万（跟随确认 %d / 诱多 %d）· H3 量能 %d / "
                            "信号 %d（无佐证 %d）</div></div>"
                            % (tr.get("window_days", 0), e(ach.get("verdict", "—")),
                               bars, len(ach.get("h1_evidence") or []),
                               (ach.get("h2_net_total") or 0) / 1e6,
                               (ach.get("hm_tags") or {}).get("follow_confirm", 0),
                               (ach.get("hm_tags") or {}).get("lure_warn", 0),
                               h3.get("vol_events", 0), h3.get("signals", 0),
                               h3.get("no_evidence", 0)))
                # 结构信号时间轴
                tli = "".join(
                    "<div class='ti'><span class='bdg %s'>%s</span>"
                    "<b>%s</b> <span class='dim'>conf %.2f · 结构%.1f 佐证%.1f "
                    "资金%.1f%s</span> %s</div>"
                    % (tcls(s["type"] or ""), e(s["type"] or ""), s["date"],
                       s["conf"] or 0, s["q_struct"] or 0, s["q_evid"] or 0,
                       s["q_hm"] or 0,
                       " · 实证佐证×%d" % s["cc"] if s.get("cc") else "",
                       tagb(s.get("hm_tag")))
                    for s in st["structures"]) or \
                    "<div class='dim'>窗口内无结构信号</div>"
                tl_html = ("<div class='sec'><h2>结构事件时间轴（缠论，%d 天）"
                           "</h2><div class='tl'>%s</div></div>"
                           % (days, tli))
                # 游资痕迹 + 量能（右列）
                hmr = "".join(
                    "<tr><td><a href='/kg/map?hm=%s'>%s</a></td><td>%d</td>"
                    "<td class='%s'>%+.0f</td><td>%s</td></tr>"
                    % (_qt(r["hm"]), e(r["hm"]), r["n"],
                       "up" if (r["net"] or 0) > 0 else "dn",
                       (r["net"] or 0) / 1e6, r["last"])
                    for r in st["hotmoney"]) or \
                    ("<tr><td colspan=4 class='dim'>窗口内无游资上榜痕迹"
                     "（龙虎榜仅上榜日披露）</td></tr>")
                volr = "".join(
                    "<tr><td><span class='bdg %s'>%s</span></td><td>%d 次</td></tr>"
                    % (tcls(v["type"] or ""), e(v["type"] or ""), v["n"])
                    for v in st["vol_events"]) or \
                    "<tr><td colspan=2 class='dim'>无量能事件</td></tr>"
                cc = st.get("candle_cov") or {}
                if st.get("candles"):
                    def _regtag(r):
                        # P3-b 监管映射：拉抬/打压类手法带警示徽章
                        # （title=细则条款描述，空 reg=常规无关联不显示）
                        if not r.get("reg"):
                            return ""
                        return (" <span class='bdg' style='border:1px solid "
                                "var(--red-500);color:var(--red-500)' title='%s'>⚖ %s</span>"
                                % (e(r.get("tech") or ""), e(r["reg"])))
                    cd = "".join(
                        "<tr><td>%s</td><td><span class='bdg %s'>%s</span>%s</td>"
                        "<td>%s</td><td class='%s'>%+.2f%%</td><td>%s</td></tr>"
                        % (r["date"],
                           "g" if "seal" in (r["type"] or "") and r["type"] != \
                           "broken" else ("r" if r["type"] in ("broken",
                           "late_dump") else "y"),
                           CANDLE_CN.get(r["type"], r["type"] or ""),
                           _regtag(r),
                           (r["mtime"] or "")[:2] + ":" + (r["mtime"] or "")[2:]
                           if r["mtime"] else "—",
                           "up" if (r["pct"] or 0) > 0 else "dn",
                           (r["pct"] or 0) * 100,
                           ("触板%d分" % r["n_touch"]) if r.get("n_touch")
                           else ("尾盘量%d%%" % round((r.get("tail_vol") or 0)
                           * 100)))
                        for r in st["candles"])
                elif cc.get("covered"):
                    cd = ("<tr><td colspan=5 class='dim'>"
                          "分钟库覆盖内 · 窗口内无盘口事件（无异动）</td></tr>")
                else:
                    cd = ("<tr><td colspan=5 class='dim'>"
                          "⚠ 分钟库未覆盖此标的——盘口域仅覆盖分钟库 "
                          "%d 只标的（非全市场），此处为无数据而非无异动"
                          "</td></tr>" % cc.get("n_cov", 0))
                cd_html = ("<div class='sec'><h2>盘口事件（分时粒度，"
                           "封板/炸板/尾盘）</h2><table class='sticky'>"
                           "<tr><th>日期</th><th>类型</th><th>首触时刻</th>"
                           "<th>收盘涨幅</th><th>特征</th></tr>%s</table>"
                           "</div>" % cd)
                # B 类：涨跌停名单（连板/封单/炸板）+ 十大流通股东
                lmr = "".join(
                    "<tr><td>%s</td><td><span class='bdg %s'>%s</span></td>"
                    "<td>%d 板</td><td>%s</td><td class='%s'>%+.2f%%</td>"
                    "<td>%s</td></tr>"
                    % (r["date"],
                       "g" if (r.get("ltp") or "U") == "U" else
                       ("y" if (r.get("ltp") or "") == "Z" else "b"),
                       "涨停" if (r.get("ltp") or "U") == "U" else
                       ("炸板" if (r.get("ltp") or "") == "Z" else "跌停"),
                       r.get("lt") or 1,
                       ("%.0f万" % ((r.get("lamt") or 0) / 1e4))
                       if r.get("lamt") else "—",
                       "up" if (r.get("pct") or 0) > 0 else "dn",
                       r.get("pct") or 0,
                       ("炸板%d次" % r["ot"]) if r.get("ot") else "封住")
                    for r in (st.get("limits") or [])) or \
                    ("<tr><td colspan=6 class='dim'>窗口内无涨跌停上榜记录"
                     "</td></tr>")
                lm_html = ("<div class='sec'><h2>涨跌停名单（连板强度/封单）"
                           "</h2><table><tr><th>日期</th><th>方向</th>"
                           "<th>连板</th><th>封单额</th><th>收盘涨幅</th>"
                           "<th>炸板</th></tr>%s</table></div>" % lmr)
                # P2-① 榜后差额（前五买卖差×席位性质×次日穿透，kg_dt_gap）
                _NAT = {"youzi": ("y", "游资"), "quant": ("b", "量化"),
                        "org": ("n", "机构"), "retail": ("r", "散户"),
                        "other": ("", "普通")}

                def _amt(v):
                    return ("—" if v is None else
                            ("%.2f亿" % (v / 1e8) if abs(v) >= 1e8
                             else "%.0f万" % (v / 1e4)))

                def _pc1(v):   # 占比 0.053 → 5.3%
                    return "—" if v is None else "%.1f%%" % (v * 100)

                def _pc2(v):   # 收益 0.012 → +1.20%
                    return "—" if v is None else "%+.2f%%" % (v * 100)

                _tg_rows = []
                for r in (st.get("topgaps") or []):
                    nc = _NAT.get(r.get("nat") or "other", ("", "普通"))
                    _pen = r.get("pen")
                    _tg_rows.append(
                        "<tr><td>%s</td><td class='%s'>%s</td><td>%s</td>"
                        "<td><span class='bdg %s'>%s</span></td><td>%s</td>"
                        "<td class='%s'>%s</td><td>%s</td>"
                        "<td class='dim'>%s</td></tr>"
                        % (r.get("date"),
                           "up" if (r.get("gap") or 0) > 0 else "dn",
                           _amt(r.get("gap")), _pc1(r.get("gr")),
                           nc[0], nc[1], _pc1(r.get("qr")),
                           ("up" if (r.get("ret1") or 0) > 0 else "dn"),
                           _pc2(r.get("ret1")),
                           ("<span class='bdg g'>穿透</span>" if _pen == 1
                            else ("<span class='bdg r'>未穿</span>"
                                  if _pen == 0 else "—")),
                           e((r.get("reason") or "")[:22])))
                tg_html = ("<div class='sec'><h2>榜后差额（前五买卖差 × "
                           "席位性质 × 次日穿透）</h2><table class='sticky'>"
                           "<tr><th>日期</th><th>差额</th><th>占成交额</th>"
                           "<th>主导性质</th><th>量化占比</th><th>次日收益"
                           "</th><th>穿透</th><th>上榜原因</th></tr>%s"
                           "</table><div class='dim' style='margin-top:4px'>"
                           "差额=买方前五-卖方前五（元）；穿透=差额方向与次日"
                           "收盘同向（近端事件次日未到显示 —）</div></div>"
                           % ("".join(_tg_rows) or
                              "<tr><td colspan=8 class='dim'>窗口内无龙虎榜"
                              "上榜记录</td></tr>"))
                # P2-② K线相似（60日对数路径 DTW top-5 + 席位双碰线索）
                _sm_rows = "".join(
                    "<tr><td>%s</td><td>%.3f</td>"
                    "<td class='%s'>%.0f%%</td><td class='dim'>%s~%s</td>"
                    "</tr>"
                    % (stock_link(r.get("tc") or "", r.get("nm") or ""),
                       r.get("dist") or 0,
                       "up" if (r.get("corr") or 0) > 0 else "dn",
                       100 * (r.get("corr") or 0),
                       r.get("ws"), r.get("we"))
                    for r in (st.get("similar") or [])) or \
                    ("<tr><td colspan=4 class='dim'>暂无相似形态数据"
                     "（次新股/窗口不足 60 交易日）</td></tr>")
                _ss_rows = "".join(
                    "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (e((r.get("seat") or "")[:30]),
                       stock_link(r.get("tc") or "", r.get("nm") or ""),
                       r.get("d1"), r.get("d2"))
                    for r in (st.get("simseats") or []))
                _ss_html = (""
                            if not _ss_rows else
                            ("<h2 style='margin-top:12px'>手法复现线索"
                             "（同席位近期双碰）</h2><table><tr><th>席位"
                             "</th><th>相似票</th><th>碰本票</th>"
                             "<th>碰相似票</th></tr>%s</table>" % _ss_rows))
                sim_html = ("<div class='sec'><h2>K线相似（60日形态 DTW "
                            "top-5）</h2><table><tr><th>标的</th>"
                            "<th>DTW 距离</th><th>同步度</th><th>窗口"
                            "</th></tr>%s</table><div class='dim' "
                            "style='margin-top:4px'>特征=60日收盘对数路径"
                            "（起点归零）；欧氏预筛+带宽DTW；同步度=窗口内"
                            "相关系数</div>%s</div>"
                            % (_sm_rows, _ss_html))
                hdr = "".join(
                    "<tr><td>%s</td><td>%s</td><td>%.2f%%</td><td>%s</td></tr>"
                    % (r["period"], e(r["name"] or ""), r["ratio"] or 0,
                       ("%.0f万股" % ((r["amt"] or 0) / 1e4))
                       if r.get("amt") else "—")
                    for r in (st.get("holders") or [])) or \
                    ("<tr><td colspan=4 class='dim'>暂无流通股东结构数据"
                     "（图内 Security 未覆盖或未披露）</td></tr>")
                hd_html = ("<details class='sec'><summary><h2>十大流通股东"
                           "</h2><span class='dim'>机构持仓 · 与龙虎榜对偶"
                           "（谁在持有 vs 谁在交易）</span></summary>"
                           "<table><tr><th>报告期</th><th>机构</th>"
                           "<th>占流通比</th><th>持股</th></tr>%s</table>"
                           "</details>" % hdr)
                # B 类：筹码分布摘要（cyq_perf 特征文件，一行式）
                cy = st.get("cyq") or {}
                if cy.get("date"):
                    wr = cy.get("winner_rate")
                    ch = cy.get("wr_chg20")
                    chs = ("（20日 %+.1fpp）" % ch) if ch is not None else ""
                    cy_html = ("<div class='sec'><h2>筹码快照</h2>"
                               "<div style='line-height:1.9'>"
                               "<b>%s</b> · 获利盘 <b>%.1f%%</b>"
                               "<span class='%s'>%s</span> · "
                               "平均成本 %.2f · 成本带宽 %.1f%%"
                               "（85-15 分位/中位，越窄越集中）</div></div>"
                               % (cy["date"], wr,
                                  "up" if (ch or 0) > 0 else
                                  ("dn" if (ch or 0) < 0 else ""),
                                  chs, cy.get("weight_avg") or 0,
                                  100 * (cy.get("cost_span") or 0)))
                else:
                    cy_html = ""
                right = ("<div class='sec'><h2>游资痕迹（窗口内龙虎榜净买）</h2>"
                         "<table><tr><th>主体</th><th>次数</th><th>净买(百万)"
                         "</th><th>最近</th></tr>%s</table></div>"
                         "<div class='sec'><h2>量能事件</h2><table>%s</table></div>"
                         % (hmr, volr)) + cd_html + lm_html + tg_html \
                         + sim_html + cy_html + hd_html
                # DRIVE 证据组 + 正向传导链（折叠区）
                dgr = "".join(
                    "<div style='margin:5px 0'><span class='bdg %s'>%s</span>"
                    "<span class='dim'>×%d %s</span></div>"
                    % ("n" if d["baseline"] else "y", e(d["grp"]), d["n"],
                       "（高密度基线组，非实质证据）" if d["baseline"]
                       else " ".join(e(t) for t in d["texts"][:2]))
                    for d in st["drive_groups"]) or \
                    "<div class='dim'>窗口内无外部事件传导</div>"
                fwt = "".join(
                    "<div class='ti'><span class='bdg %s'>%s</span><b>%s</b>%s</div>"
                    % (tcls(ev["type"] or ""), e(ev["type"] or ""), ev["date"],
                       "".join("<div class='src'>← [%s lag=%dd p=%.2f] %s</div>"
                               % (e(s["grp"]) + ("·基线" if s["baseline"] else ""),
                                  s["lag"] or 0, s["p"] or 0, e(s["text"] or ""))
                               for s in ev["sources"]))
                    for ev in (tr.get("fwd_tree") or [])) or \
                    "<div class='dim'>窗口内无盘面事件</div>"
                chain = ("<details class='sec'><summary><h2>事件传导链</h2>"
                         "<span class='dim'>盘面事件 ← DRIVE 来源树</span>"
                         "</summary><div class='sec-body'>%s"
                         "<h2 style='margin-top:14px'>传导链（时间倒序）</h2>"
                         "<div class='tl'>%s</div></div></details>"
                         % (dgr, fwt))
                head = ("<h1>%s <span class='dim'>%s · %s · %s</span></h1>"
                        "<div class='dim' style='margin:2px 0 8px'>%s</div>"
                        "<div style='display:flex;gap:8px;flex-wrap:wrap'>%s</div>"
                        % (e(b.get("name") or code), e(code),
                           e(b.get("industry") or "-"), e(b.get("area") or "-"),
                           e(b.get("company") or ""), pills))
                duo = ("<div class='duo'>%s<div>%s%s</div></div>"
                       % (tl_html, right, chain))
                content += head + ach_html + duo

        # ── ③ 游资归因视图 ──
        elif hm:
            hd = KP.api("hotmoney", hm, 180)
            title = "%s 游资归因" % hm
            if not hd.get("ok"):
                content = ("<div class='sec'><div class='dn'>%s</div></div>"
                           % e(hd.get("error", "")))
            else:
                top = "".join(
                    "<tr><td>%s</td><td>%d</td><td class='%s'>%+.0f</td>"
                    "<td>%s</td></tr>"
                    % (stock_link(r["tc"], r["nm"]), r["n"],
                       "up" if (r["net"] or 0) > 0 else "dn",
                       (r["net"] or 0) / 1e6, r["last"])
                    for r in hd["top_securities"]) or \
                    "<tr><td colspan=4 class='dim'>窗口内无交易</td></tr>"
                mv = "".join(
                    "<tr><td>%s</td><td>%s</td><td class='%s'>%+.1f</td></tr>"
                    % (r["date"], stock_link(r["tc"], r["nm"]),
                       "up" if (r["net"] or 0) > 0 else "dn",
                       (r["net"] or 0) / 1e6)
                    for r in hd["recent_moves"][:15]) or ""
                seats = "".join("<span class='tp' style='color:var(--gray-5)'>%s</span>"
                                % e(s) for s in hd["seats"][:8])
                content = (
                    "<h1>💰 %s</h1><div class='dim' style='margin:2px 0 8px'>"
                    "近 180 天 · %d 席位 · %d 笔上榜 · 净买合计 "
                    "<b class='%s'>%+.0f 百万</b>（简版视图，完整画像见 W3）</div>"
                    "<div style='margin:0 0 4px'>%s</div>"
                    "<div class='duo'><div class='sec'><h2>Top 标的（净买）</h2>"
                    "<table class='sticky'><tr><th>标的</th><th>次数</th>"
                    "<th>净买(百万)</th><th>最近</th></tr>%s</table></div>"
                    "<div class='sec'><h2>近期动向</h2><table class='sticky'>"
                    "<tr><th>日期</th><th>标的</th><th>净买(百万)</th></tr>%s"
                    "</table></div></div>"
                    % (e(hm), hd["seats_n"], hd.get("trades_n", 0),
                       "up" if (hd.get("net_buy_total") or 0) > 0 else "dn",
                       (hd.get("net_buy_total") or 0) / 1e6, seats, top, mv))

        # ── ④ 默认首页：图谱概览 + 近期确认信号 ──
        if not content:
            ss = KP.api("stats")
            sg = KP.api("signals", 14)
            if not ss.get("ok"):
                content = ("<div class='sec'><div class='dn'>%s</div></div>"
                           % e(ss.get("error", "Neo4j 不可达")))
            else:
                n, edg, fr, tg = (ss["nodes"], ss["edges"],
                                  ss["freshness"], ss["hm_tags"])
                cards = "".join(
                    "<div class='sstat'><span class='st'>%s</span>"
                    "<span class='sv'>%s</span><span class='su'>%s</span></div>"
                    % (lb, e(str(v)), su)
                    for lb, v, su in [
                        ("标的", n.get("Security", 0), "Security"),
                        ("盘面事件", n.get("MarketEvent", 0),
                         "缠论 %d + 量能 %d"
                         % (edg.get("HAS_STRUCTURE", 0),
                            edg.get("HAS_VOL_EVENT", 0))),
                        ("外部事件", n.get("Event", 0),
                         "DRIVE 传导 %d" % edg.get("DRIVE", 0)),
                        ("游资主体", n.get("HotMoney", 0),
                         "席位 %d · 龙虎榜 %d 笔"
                         % (n.get("Seat", 0), edg.get("TRADED_ON", 0))),
                        ("跟随确认", tg.get("follow_confirm", 0),
                         "诱多警示 %d" % tg.get("lure_warn", 0)),
                        ("保鲜时点", fr.get("me", "-"),
                         "事件流 %s / 龙虎榜 %s"
                         % (fr.get("ee", "-"), fr.get("te", "-")))])
                sgr = "".join(
                    "<tr><td>%s</td><td>%s</td><td><span class='bdg %s'>%s"
                    "</span></td><td>%s</td><td>%.2f</td></tr>"
                    % (s["date"], stock_link(s["tc"], s["nm"]),
                       "g" if s["tag"] == "follow_confirm" else "r",
                       e(s["type"] or ""), tagb(s["tag"]), s["conf"] or 0)
                    for s in (sg.get("signals") or [])) or \
                    "<tr><td colspan=5 class='dim'>近 14 天无确认/诱多信号" \
                    "（M2-W4c 口径：post10∈(0,1亿] 且 pre≤0）</td></tr>"
                # G7 来源评级（Admiralty A-F + 断流红点，折叠面板）
                src = KP.api("sources")
                src_html = ""
                if src.get("ok"):
                    GC = {"A": "g", "B": "b", "C": "y", "D": "r",
                          "E": "r", "F": "n"}
                    GN = {"A": "完全可靠", "B": "通常可靠", "C": "相当可靠",
                          "D": "不常可靠", "E": "不可靠", "F": "无法判断"}
                    srows = "".join(
                        "<tr><td><span class='bdg %s' title='%s'>%s</span></td>"
                        "<td>%s</td><td class='dim'>%s</td><td>%s</td></tr>"
                        % (GC.get(s["grade"], "n"),
                           GN.get(s["grade"], ""), s["grade"],
                           e(s["family"]),
                           e((s["basis"] or "")[:40]),
                           ("<span class='bdg e'>断流 %d 表</span>"
                            % len(s["stale"])) if s["stale"] else "✓")
                        for s in src["sources"])
                    cat = (" · <span class='bdg e'>catalog 滞后 %sd</span>"
                           % src["catalog_age_days"]) \
                        if src.get("catalog_stale") else ""
                    src_html = (
                        "<details class='sec'><summary>📋 来源可靠性评级"
                        "（Admiralty · %s%s）</summary><table class='sticky'>"
                        "<tr><th>档</th><th>源族</th><th>依据</th>"
                        "<th>断流</th></tr>%s</table></details>"
                        % (e(src.get("ts") or "-"), cat, srows))
                content = (
                    "<h1>🕸 知识图谱探索</h1><div class='dim' style='margin:2px 0 "
                    "10px'>五元本体 + 时序边 + 情报元属性 · 纯查询不写库</div>"
                    "<div style='display:grid;grid-template-columns:repeat("
                    "auto-fit,minmax(170px,1fr));gap:10px'>%s</div>"
                    "<div class='sec'><h2>近 14 天游资确认/诱多信号</h2>"
                    "<div class='dim' style='margin-bottom:4px'>校准口径：跟随"
                    "确认 post10∈(0,1亿]且pre≤0（历史 ret5 +11.6%%）/ 诱多 "
                    "pre&gt;0 且 post&gt;0（-4.2%%）；近月样本全负，体制切换"
                    "警讯见计划文档</div><table class='sticky'><tr><th>日期</th>"
                    "<th>标的</th><th>结构</th><th>标注</th><th>conf</th></tr>"
                    "%s</table></div>%s" % (cards, sgr, src_html))

        # 标题用拼接而非 % 格式化：content 内含字面量 %（如 +11.6%）
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>" + e(title) + " · 图谱探索</title>"
                + self._COCKPIT_CSS + self._KG_CSS + "</head><body>"
                + pill_nav("kg") + search + content
                + "</body></html>")
        self._html(body)
