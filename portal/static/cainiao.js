/* ═══════════════════════════════════════════════════════════════
 * cainiao.js — 悬浮 AI「菜鸟蛋」全站共享组件（8080 工作站）
 * 自包含：宿主页面零改动，<script src="/static/cainiao.js" defer> 即用。
 * 自注入 CSS + DOM；对话走同源 /cai/api/chat，失败同源重试一次；原直连端口已退役。
 * 带路导航全部用相对路径（./paper 等），不假设宿主页所在目录。
 * ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';
  if (window.__CAINIAO_ORB__) return;
  window.__CAINIAO_ORB__ = 1;

  /* ── 1. 组件 CSS（变量带硬编码 fallback，兼容变量名不同的宿主页）── */
  var CSS = [
    '#ai-orb{position:fixed;z-index:900;width:64px;height:64px;cursor:grab;user-select:none;-webkit-user-select:none;touch-action:none;right:26px;bottom:26px;}',
    '#ai-orb.drag{cursor:grabbing;}',
    '#ai-orb .orb-halo{position:absolute;inset:-8px;border-radius:50%;background:radial-gradient(circle,color-mix(in srgb,var(--link,#58a6ff) 22%,transparent) 30%,transparent 72%);animation:orbBreathe 2.4s ease-in-out infinite;}',
    '#ai-orb .orb-ring{position:absolute;inset:0;border-radius:50%;border:2px dashed color-mix(in srgb,var(--link,#58a6ff) 55%,transparent);animation:orbSpin 8s linear infinite;}',
    '#ai-orb .orb-core{position:absolute;inset:7px;border-radius:50%;background:#ded0af;overflow:hidden;box-shadow:0 4px 18px color-mix(in srgb,var(--link,#58a6ff) 35%,transparent);animation:orbFloat 3.2s ease-in-out infinite;}',
    '#ai-orb .orb-core img,#cai-win .win-avatar img{width:100%;height:100%;display:block;object-fit:cover;object-position:center 24%;}',
    '#ai-orb:hover .orb-core{box-shadow:0 6px 28px color-mix(in srgb,var(--pur-500,#bc8cff) 60%,transparent);}',
    '#ai-orb .orb-dot{position:absolute;top:2px;right:2px;width:10px;height:10px;border-radius:50%;background:var(--color-ok,#3fb950);box-shadow:0 0 8px var(--color-ok,#3fb950);animation:orbBreathe 1.6s ease-in-out infinite;}',
    '@keyframes orbBreathe{0%,100%{transform:scale(1);opacity:.85;}50%{transform:scale(1.12);opacity:1;}}',
    '@keyframes orbSpin{to{transform:rotate(360deg);}}',
    '@keyframes orbFloat{0%,100%{transform:translateY(0);}50%{transform:translateY(-3px);}}',
    '#cai-win{position:fixed;z-index:950;width:min(430px,94vw);background:var(--bg-raised,#161b22);border:1px solid color-mix(in srgb,var(--link,#58a6ff) 40%,var(--border,#30363d));border-radius:16px;padding:14px;box-shadow:0 14px 48px rgba(0,0,0,.55);display:none;color:var(--text,#e6edf3);}',
    '#cai-win.open{display:block;animation:winPop .22s ease-out;}',
    '@keyframes winPop{from{opacity:0;transform:scale(.72) translateY(18px);}to{opacity:1;transform:none;}}',
    '#cai-win .win-head{display:flex;align-items:center;gap:9px;margin-bottom:6px;}',
    '#cai-win .win-avatar{width:34px;height:34px;border-radius:50%;flex:none;background:#ded0af;overflow:hidden;}',
    '#cai-win .win-head b{font-size:14.5px;display:block;}',
    '#cai-win .win-head span{color:var(--text-dim,#8b949e);font-size:11px;}',
    '#cai-win .win-x{margin-left:auto;background:none;border:1px solid var(--border,#30363d);color:var(--text-dim,#8b949e);width:26px;height:26px;border-radius:50%;font-size:15px;cursor:pointer;line-height:1;}',
    '#cai-win .win-x:hover{color:var(--text,#e6edf3);border-color:var(--link,#58a6ff);}',
    '#cai-msgs{max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:8px;margin:6px 0;}',
    '#cai-win .cmsg{padding:9px 12px;border-radius:10px;max-width:88%;white-space:pre-wrap;line-height:1.55;font-size:13px;}',
    '#cai-win .cmsg.me{align-self:flex-end;background:color-mix(in srgb,var(--blu-600,#1f6feb) 20%,transparent);border:1px solid color-mix(in srgb,var(--blu-600,#1f6feb) 40%,transparent);}',
    '#cai-win .cmsg.cai{align-self:flex-start;background:var(--bg-surface,#21262d);border:1px solid var(--border,#30363d);}',
    '#cai-win .cmsg .via{display:block;margin-top:5px;font-size:10.5px;color:var(--text-dim,#8b949e);}',
    '#cai-win .win-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}',
    '#cai-win .win-chip{cursor:pointer;border:1px solid var(--border,#30363d);background:var(--bg-surface,#21262d);color:var(--text,#e6edf3);border-radius:14px;padding:4px 12px;font-size:12px;transition:border-color .2s ease,color .2s ease;}',
    '#cai-win .win-chip:hover{border-color:var(--link,#58a6ff);}',
    '#cai-win .win-inrow{display:flex;gap:8px;margin-top:8px;}',
    '#cai-win .win-inrow input{flex:1;background:var(--bg-page,#0d1117);border:1px solid var(--border,#30363d);border-radius:8px;color:var(--text,#e6edf3);padding:9px 12px;font-size:13px;outline:none;}',
    '#cai-win .win-inrow input:focus{border-color:var(--link,#58a6ff);}',
    '#cai-win .win-inrow button{background:var(--blu-600,#1f6feb);color:#fff;border:none;border-radius:8px;padding:9px 18px;font-weight:700;cursor:pointer;}',
    '@media (prefers-reduced-motion:reduce){#ai-orb,#ai-orb *,#cai-win,#cai-win *{animation:none !important;transition:none !important;}}'
  ].join('\n');

  /* ── 2. 注入 CSS 与 DOM ── */
  var styleEl = document.createElement('style');
  styleEl.textContent = CSS;
  document.head.appendChild(styleEl);

  var portrait = '/static/cainiao_avatar_zhuge.jpg';
  var HOST = document.createElement('div');
  HOST.id = 'cai-orb-host';
  HOST.innerHTML =
    '<div id="ai-orb" title="菜鸟 · 拖动换位置 / 点击召唤">' +
      '<div class="orb-halo"></div>' +
      '<div class="orb-ring"></div>' +
      '<div class="orb-core"><img src="' + portrait + '" alt="诸葛亮形象"></div>' +
      '<div class="orb-dot"></div>' +
    '</div>' +
    '<div id="cai-win">' +
      '<div class="win-head">' +
        '<div class="win-avatar"><img src="' + portrait + '" alt=""></div>' +
        '<div><b>菜鸟</b><span>平行空间的自己 · 替你管公司 · 说「打开模拟仓」就带路</span></div>' +
        '<button class="win-x" title="收起（Esc）">\u00D7</button>' +
      '</div>' +
      '<div id="cai-msgs">' +
        '<div class="cmsg cai">老板好，我在这儿盯着的。问「各部门情况」「我的钱」「挂单」「裁决窗」都行，说「打开模拟仓」我带路。也可以把我这颗蛋拖到任何顺手的位置。</div>' +
      '</div>' +
      '<div class="win-chips"></div>' +
      '<div class="win-inrow">' +
        '<input id="cai-in" placeholder="给菜鸟下指令…">' +
        '<button id="cai-send">发送</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(HOST);

  /* ── 3. 元素引用与常量 ── */
  var CAI_API_PRIMARY = '/cai/api/chat';                 // 同源优先（nginx 反代）
  var CAI_API_FALLBACK = '/cai/api/chat'; // 同源重试（原直连已退役，避免跨域）
  var ORB_SIZE = 64, EDGE = 6;

  var orb = document.getElementById('ai-orb');
  var cwin = document.getElementById('cai-win');
  var msgs = document.getElementById('cai-msgs');
  var input = document.getElementById('cai-in');
  var chipsBox = cwin.querySelector('.win-chips');

  /* ── 4. 快捷 chips（内部事件，不再依赖全局函数）── */
  var CHIPS = [
    ['\u{1F4CA} 各部门情况', 'ask', '各部门情况'],
    ['\u{1F4B0} 我的钱', 'ask', '我的钱'],
    ['\u{1F4CB} 挂单', 'ask', '挂单'],
    ['\u{1F4C5} 裁决窗', 'ask', '裁决窗'],
    ['\u{1F464} 影子进度', 'ask', '影子进度'],
    ['\u2192 模拟仓', 'nav', './paper'],
    ['\u2192 情报中枢', 'nav', './intel'],
    ['\u2192 量化股票', 'nav', './quant'],
    ['\u2192 数据大屏', 'nav', './screen']
  ];
  CHIPS.forEach(function (ch) {
    var b = document.createElement('button');
    b.className = 'win-chip';
    b.textContent = ch[0];
    b.addEventListener('click', function () {
      if (ch[1] === 'ask') caiAsk(ch[2]); else caiNav(ch[2]);
    });
    chipsBox.appendChild(b);
  });

  /* ── 5. 拖拽（Pointer Events · 6px 阈值区分拖/点 · localStorage 记位 · 视口 clamp）── */
  var cDrag = false, cMoved = false, cSx = 0, cSy = 0, cSl = 0, cSt = 0;
  function cClamp(l, t) {
    return [
      Math.max(EDGE, Math.min(innerWidth - ORB_SIZE - EDGE, l)),
      Math.max(EDGE, Math.min(innerHeight - ORB_SIZE - EDGE, t))
    ];
  }
  function cSet(l, t) {
    orb.style.left = l + 'px'; orb.style.top = t + 'px';
    orb.style.right = 'auto'; orb.style.bottom = 'auto';
  }
  (function cRestore() {  // 恢复上次拖放位置
    try {
      var p = JSON.parse(localStorage.getItem('orb_pos'));
      if (p && isFinite(p.l) && isFinite(p.t)) {
        var c = cClamp(p.l, p.t);
        cSet(c[0], c[1]);
      }
    } catch (e) {}
  })();
  orb.addEventListener('pointerdown', function (e) {
    cDrag = true; cMoved = false;
    orb.setPointerCapture(e.pointerId);
    cSx = e.clientX; cSy = e.clientY;
    var r = orb.getBoundingClientRect(); cSl = r.left; cSt = r.top;
    orb.classList.add('drag');
    e.preventDefault();
  });
  orb.addEventListener('pointermove', function (e) {
    if (!cDrag) return;
    var dx = e.clientX - cSx, dy = e.clientY - cSy;
    if (!cMoved && Math.hypot(dx, dy) < 6) return;  // 位移阈值：拖 vs 点
    cMoved = true;
    var p = cClamp(cSl + dx, cSt + dy);
    cSet(p[0], p[1]);
  });
  orb.addEventListener('pointerup', function () {
    cDrag = false; orb.classList.remove('drag');
    if (cMoved) {
      var r = orb.getBoundingClientRect();
      try { localStorage.setItem('orb_pos', JSON.stringify({ l: r.left, t: r.top })); } catch (e) {}
    } else caiToggle();
  });
  orb.addEventListener('pointercancel', function () {
    cDrag = false; orb.classList.remove('drag');
  });

  /* ── 6. 召唤窗开关（窗对齐蛋心 · 优先弹蛋上方 · Esc 收起）── */
  function caiToggle() { cwin.classList.contains('open') ? caiClose() : caiOpen(); }
  function caiOpen() {
    cwin.classList.add('open');
    var r = orb.getBoundingClientRect(), w = cwin.offsetWidth, h = cwin.offsetHeight;
    var l = r.left + r.width / 2 - w / 2;            // 窗对齐蛋心
    l = Math.max(EDGE, Math.min(innerWidth - w - EDGE, l));
    var t = r.top - h - 12;                          // 优先弹在蛋上方
    if (t < EDGE) t = Math.min(r.bottom + 12, innerHeight - h - EDGE);
    cwin.style.left = l + 'px'; cwin.style.top = t + 'px';
    cwin.style.right = 'auto'; cwin.style.bottom = 'auto';
    setTimeout(function () { input.focus(); }, 150);
  }
  function caiClose() { cwin.classList.remove('open'); }
  cwin.querySelector('.win-x').addEventListener('click', caiClose);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') caiClose(); });

  /* ── 7. 站内带路：相对路径，不假设宿主页所在目录 ── */
  function caiNav(p) { location.href = p; }
  document.getElementById('cai-send').addEventListener('click', caiSend);
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') caiSend(); });
  function caiSend() {
    var v = input.value.trim();
    if (!v) return;
    caiAsk(v); input.value = '';
  }
  async function caiAsk(m) {
    caiOpen();
    caiAdd(m, 'me');
    // 导航意图：说「打开/带路/去 XX」就地跳转（相对路径）
    var navMap = [['模拟仓', './paper'], ['情报', './intel'], ['图谱', './intel'], ['量化', './quant'],
      ['双刀', './quant'], ['大屏', './screen'], ['对话', './nl'], ['运维', './ops'], ['系统', './ops'], ['开发', './dev.html']];
    if (/(打开|带路|去)/.test(m)) {
      for (var i = 0; i < navMap.length; i++) {
        if (m.includes(navMap[i][0])) {
          caiAdd('好的老板，这就带你过去 \u2192 ' + navMap[i][0], 'cai');
          setTimeout(function (p) { return function () { caiNav(p); }; }(navMap[i][1]), 600);
          return;
        }
      }
    }
    var body = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ msg: m })
    };
    try {
      var ctl = new AbortController();
      setTimeout(function () { ctl.abort(); }, 35000);
      body.signal = ctl.signal;
      var j = null;
      try {
        var r1 = await fetch(CAI_API_PRIMARY, body);
        if (r1.ok) j = await r1.json();
      } catch (e) { j = null; }               // 同源失败（网络/非 ok）→ 同源重试一次
      if (!j) {
        var r2 = await fetch(CAI_API_FALLBACK, body);
        j = await r2.json();
      }
      caiAdd(j.text || ('错误：' + (j.error || '未知')), 'cai', j.via);
    } catch (e) {
      caiAdd('菜鸟后端（8094）没应答：' + e.message, 'cai');
    }
  }
  function caiAdd(t, who, via) {
    var d = document.createElement('div');
    d.className = 'cmsg ' + (who === 'me' ? 'me' : 'cai');
    d.textContent = t;
    if (via) {
      var v = document.createElement('span');
      v.className = 'via'; v.textContent = 'via ' + via;
      d.appendChild(v);
    }
    msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight;
  }
})();
