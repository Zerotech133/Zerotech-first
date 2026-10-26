
const D = __DATA__;
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const sheet = $('#sheet'), backdrop = $('#backdrop'), sheetIn = $('#sheetIn');
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function openSheet(id){
  const d = D.sheet[id]; if(!d) return;
  let h = '<div class="kicker">'+esc(d.kicker)+'</div><h3>'+esc(d.title)+'</h3><div class="rows">';
  d.rows.forEach(r=>{ h += '<div class="r"><div class="k">'+esc(r[0])+'</div><div class="v">'+esc(r[1])+'</div></div>'; });
  h += '</div>';
  if(d.read) h += '<div class="read">'+esc(d.read)+'</div>';
  if(d.note) h += '<div class="note">'+esc(d.note)+'</div>';
  sheetIn.innerHTML = h;
  sheet.classList.add('on'); backdrop.classList.add('on');
  document.body.style.overflow='hidden';
  sheet.scrollTop = 0;
  try{ history.replaceState(null, '', '#s='+id); }catch(e){}
}
function closeSheet(){
  sheet.classList.remove('on'); backdrop.classList.remove('on');
  document.body.style.overflow='';
  try{ if(location.hash) history.replaceState(null, '', location.pathname+location.search); }catch(e){}
}
/* 深链：#s=id 直接打开对应弹层（便于分享某条结论） */
(function(){
  const m = location.hash.match(/^#s=(.+)$/);
  if(m) setTimeout(function(){ openSheet(m[1]); }, 150);
})();
backdrop.addEventListener('click', closeSheet);
$('#sheetClose').addEventListener('click', closeSheet);
document.addEventListener('keydown', e=>{ if(e.key==='Escape'){ closeSheet(); closeLB(); } });

/* 灯箱 */
const lb = $('#lb'), lbImg = $('#lbImg');
function openLB(src){ lbImg.src = src; lb.classList.add('on'); document.body.style.overflow='hidden'; }
function closeLB(){ lb.classList.remove('on'); document.body.style.overflow=''; }
lb.addEventListener('click', closeLB);

/* 点击图（非热点）=放大 */
$$('.zoomable').forEach(el=>{
  el.addEventListener('click', ev=>{
    if(ev.target.closest('.hot')) return;
    const im = el.querySelector('img'); if(im) openLB(im.src);
  });
});

/* 热点 */
$$('.hot').forEach(b=>{
  b.addEventListener('click', ev=>{ ev.stopPropagation(); openSheet(b.dataset.sheet); });
});

/* 判断卡/情景卡 */
$$('[data-sheet]').forEach(el=>{
  if(el.classList.contains('hot')) return;
  el.addEventListener('click', ()=>openSheet(el.dataset.sheet));
});

/* tabs */
$$('.tabs button').forEach(b=>{
  b.addEventListener('click', ()=>{
    const id=b.dataset.tab;
    $$('.tabs button').forEach(x=>x.classList.toggle('on', x===b));
    $$('.tabp').forEach(p=>p.classList.toggle('on', p.id==='tab-'+id));
  });
});

/* 目录高亮 + 进度 + 回顶 */
const secs = $$('section[id]');
const navBtns = $$('.nav button');
function onScroll(){
  const st = window.scrollY || document.documentElement.scrollTop;
  const h = document.documentElement.scrollHeight - window.innerHeight;
  $('#progBar').style.width = (h>0 ? Math.min(100, st/h*100) : 0) + '%';
  $('#fab').classList.toggle('on', st > 600);
  let cur = secs[0] ? secs[0].id : '';
  secs.forEach(s=>{ if(s.getBoundingClientRect().top <= 140) cur = s.id; });
  navBtns.forEach(b=>b.classList.toggle('on', b.dataset.target===cur));
}
window.addEventListener('scroll', onScroll, {passive:true});
navBtns.forEach(b=>b.addEventListener('click', ()=>{
  const t=$('#'+b.dataset.target); if(t) window.scrollTo({top:t.offsetTop-96, behavior:'smooth'});
}));
$('#fab').addEventListener('click', ()=>window.scrollTo({top:0,behavior:'smooth'}));

/* toast */
function toast(msg){ const t=$('#toast'); t.textContent=msg; t.classList.add('on'); setTimeout(()=>t.classList.remove('on'),1800); }
const copyBtn = $('#copyBtn');
if(copyBtn){ copyBtn.addEventListener('click', async ()=>{
  const txt = (window.__COPY_TEXT__ || '研究分析，不构成投资建议');
  try{ await navigator.clipboard.writeText(txt); toast('已复制三句话要点'); }
  catch(e){ toast('复制失败，请长按选择文本'); }
});
}
onScroll();
