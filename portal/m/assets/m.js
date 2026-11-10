/* 零界 · 移动端 H5 共享脚本 v1（2026-10-25）
   · 从 URL 取 t（页面令牌）→ /api/m/data?t=… 拉数据
   · 按 body[data-page] 渲染；任何异常显示可读错误（不白屏）
   · 埋点：m_page_view / m_action / m_error（sendBeacon → /api/m/beacon）
*/
(function () {
  'use strict';
  var qs = new URLSearchParams(location.search);
  var TOKEN = qs.get('t') || '';
  window.M_TOKEN = TOKEN;
  var FROM = qs.get('from') || 'browser';
  var ST = qs.get('st') || '';   // 分享归因 token（旧 picks ?st= 同口径）
  var PAGE = document.body.getAttribute('data-page') || '';
  var PATH_ID = (function () {
    var m = location.pathname.match(/^\/m\/(strategy|feed)\/([^\/]+)\/?$/);
    return m ? decodeURIComponent(m[2]) : '';
  })();
  var app = document.getElementById('app');
  var T0 = Date.now();

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function pct(v) {
    if (v == null || isNaN(v)) return '—';
    var n = Number(v);
    var s = (n > 0 ? '+' : '') + n.toFixed(2) + '%';
    return '<span class="' + (n > 0 ? 'up' : (n < 0 ? 'dn' : 'dim')) + '">' + s + '</span>';
  }
  function money(v) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toLocaleString('zh-CN', {maximumFractionDigits: 2});
  }
  function esc0(s) { return esc(s); }

  function beacon(events) {
    try {
      var payload = JSON.stringify({events: events});
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/m/beacon', new Blob([payload], {type: 'application/json'}));
      } else {
        fetch('/api/m/beacon', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: payload, keepalive: true}).catch(function () {});
      }
    } catch (e) { /* 埋点失败不阻塞页面 */ }
  }

  function fail(msg, hint) {
    app.innerHTML = '<div class="m-error">' + esc(msg) +
      (hint ? '<br><span class="dim">' + esc(hint) + '</span>' : '') + '</div>';
    beacon([{event: 'm_error', scene: PAGE, code: 'render', ts: Math.floor(Date.now() / 1000)}]);
  }

  function load() {
    if (!TOKEN) { fail('缺少页面令牌（t）', '请从飞书卡片按钮进入，或重新获取链接。'); return; }
    app.innerHTML = '<div class="m-skel">加载中…</div>';
    var _u = '/api/m/data?t=' + encodeURIComponent(TOKEN) +
             (PATH_ID ? '&id=' + encodeURIComponent(PATH_ID) : '');
    fetch(_u, {cache: 'no-store'})
      .then(function (r) { return r.json().then(function (j) { return {status: r.status, body: j}; }); })
      .then(function (res) {
        if (res.status !== 200 || !res.body || res.body.ok === false) {
          fail((res.body && res.body.error) || ('接口错误 HTTP ' + res.status),
               '令牌可能已过期，请重新获取卡片链接。');
          return;
        }
        render(res.body);
        beacon([{event: 'm_page_view', scene: PAGE, from: FROM, token_type: 'page',
                 load_ms: Date.now() - T0, st: ST, ts: Math.floor(Date.now() / 1000)}]);
      })
      .catch(function (e) { fail('网络异常：' + (e && e.message ? e.message : e), '请检查网络后重试。'); });
  }

  function header(title, back) {
    return '<div class="m-header">' +
      (back ? '<button class="m-icon" onclick="history.back()">←</button>' : '<span style="width:44px"></span>') +
      '<h1>' + esc(title) + '</h1><span style="width:44px"></span></div>';
  }

  // ── picks ───────────────────────────────────────────────
  function renderPicks(d) {
    var rows = (d.stocks || []).map(function (s) {
      var tier = String(s.tier || '').slice(0, 2).toUpperCase();
      return '<a class="m-row" data-tier="' + esc(tier) + '" href="' + esc(s.link || '#') + '">' +
        '<div class="info"><b>' + esc(s.name) + '</b><span class="code">' + esc(s.code) + '</span>' +
        '<p>' + esc(s.why || (s.industry || '')) + ' · 命中' + esc(s.n_hit || 0) + '</p></div>' +
        '<div class="val"><b class="' + (Number(s.pct) > 0 ? 'up' : (Number(s.pct) < 0 ? 'dn' : 'dim')) + '">' +
        esc(s.pct == null ? '—' : Number(s.pct).toFixed(2) + '%') + '</b>' +
        '<span class="dim">' + esc(s.close == null ? '' : s.close) + '</span></div></a>';
    }).join('');
    app.innerHTML = header('今日选股', true) +
      '<div class="m-crumbs">量化 / <b>今日选股</b></div>' +
      (d.stale ? '<div class="m-stale">行情基准 ' + esc(d.scan_run_date || '') +
        '（最近交易日快照，非实时）</div>' : '') +
      '<div class="m-card concl"><p>' + esc(d.headline || '') + '</p></div>' +
      '<div class="m-tabs"><button class="m-chip on" onclick="mFilter(\'all\',this)">全部 ' + esc(d.total || 0) + '</button>' +
      '<button class="m-chip" onclick="mFilter(\'T1\',this)">T1 ' + esc(d.t1_n == null ? 0 : d.t1_n) + '</button>' +
      '<button class="m-chip" onclick="mFilter(\'T2\',this)">T2 ' + esc(d.t2_n == null ? 0 : d.t2_n) + '</button></div>' +
      (rows || '<div class="m-empty">今日无候选</div>') +
      '<div class="m-note" style="margin-top:10px">点单只进入缠论详情 · 数据为演示口径</div>' +
      '<div class="m-actionbar"><button class="m-btn" onclick="location.reload()">刷新</button>' +
      '<button class="m-btn primary" onclick="history.back()">返回</button></div>';
  }

  // 列表筛选（T1/T2；无匹配时提示）
  window.mFilter = function (tier, btn) {
    var chips = document.querySelectorAll('#app .m-tabs .m-chip');
    for (var i = 0; i < chips.length; i++) chips[i].classList.remove('on');
    if (btn) btn.classList.add('on');
    var rows = document.querySelectorAll('#app .m-row[data-tier]');
    var shown = 0;
    for (var j = 0; j < rows.length; j++) {
      var t = rows[j].getAttribute('data-tier') || '';
      var ok = (tier === 'all' || t === tier);
      rows[j].style.display = ok ? '' : 'none';
      if (ok) shown++;
    }
    var emptyEl = document.getElementById('m-filter-empty');
    if (emptyEl) emptyEl.parentNode.removeChild(emptyEl);
    if (shown === 0) {
      var div = document.createElement('div');
      div.id = 'm-filter-empty';
      div.className = 'm-empty';
      div.textContent = '该档暂无候选';
      var tabs = document.querySelector('#app .m-tabs');
      if (tabs && tabs.parentNode) tabs.parentNode.insertBefore(div, tabs.nextSibling);
    }
  };

  // ── chan 图（日线/30F/60F：蜡烛+成交量+中枢+笔+买卖点） ──
  var CHAN_PERIOD_LABEL = {'1d': '日线', '30m': '30F', '60m': '60F'};

  function chanPeriodTabs(cur) {
    var html = '<div class="m-tabs" id="m-chan-periods">';
    ['1d', '30m', '60m'].forEach(function (p) {
      html += '<button class="m-chip' + (p === cur ? ' on' : '') +
        '" data-p="' + p + '" onclick="mChanPeriod(\'' + p + '\')">' +
        CHAN_PERIOD_LABEL[p] + '</button>';
    });
    return html + '</div>';
  }

  function chanChart(chart, period) {
    if (chart && chart.bars && !chart.periods) {
      chart = {default: '1d', periods: {'1d': chart}};   // 兼容旧单周期数据
    }
    if (!chart || !chart.periods) {
      return '<div class="m-card"><p class="dim">K线图暂不可用（数据源未就绪）</p></div>';
    }
    var data = chart.periods[period];
    if (!data || !data.bars || !data.bars.length) {
      return '<div class="m-card"><p class="dim">' +
        (CHAN_PERIOD_LABEL[period] || period) + ' 数据暂不可用</p></div>';
    }
    var bars = data.bars, W = 360, H = 330, pl = 4, pr = 30;
    var topP = 8, botP = 236, volTop = 250, volBot = 312;
    var n = bars.length, step = (W - pl - pr) / n, idx = {};
    for (var i = 0; i < n; i++) idx[bars[i].d] = i;
    var lo = Infinity, hi = -Infinity, maxVol = 1;
    for (var j = 0; j < n; j++) {
      lo = Math.min(lo, bars[j].l); hi = Math.max(hi, bars[j].h);
      if (bars[j].v > maxVol) maxVol = bars[j].v;
    }
    (data.pivots || []).forEach(function (p) {
      if (p.zg != null) hi = Math.max(hi, p.zg);
      if (p.zd != null) lo = Math.min(lo, p.zd);
    });
    if (!isFinite(lo) || !isFinite(hi) || hi <= lo) {
      return '<div class="m-card"><p class="dim">K线数据异常</p></div>';
    }
    var padY = (hi - lo) * 0.05 || 1; lo -= padY; hi += padY;
    function X(i) { return pl + (i + 0.5) * step; }
    function Y(v) { return topP + (hi - v) / (hi - lo) * (botP - topP); }
    function VY(v) { return volBot - (v / maxVol) * (volBot - volTop); }
    var s = '<svg viewBox="0 0 ' + W + ' ' + H +
      '" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">';
    for (var g = 0; g < 4; g++) {
      var gy = topP + g * (botP - topP) / 3;
      s += '<line x1="' + pl + '" y1="' + gy + '" x2="' + (W - pr) + '" y2="' +
        gy + '" stroke="#21262d" stroke-width="1"/>';
    }
    // 中枢框 + 延伸线（最后一个中枢延伸到当前）
    var lastPivotB = -1;
    (data.pivots || []).forEach(function (p) {
      var a = idx[p.s], b = idx[p.e];
      if (a == null && b == null) return;
      if (a == null) a = 0;
      if (b == null) b = n - 1;
      if (b < a) { var t = a; a = b; b = t; }
      if (b > lastPivotB) lastPivotB = b;
      var top = Y(p.zg), bot = Y(p.zd);
      s += '<rect x="' + X(a) + '" y="' + top + '" width="' +
        Math.max(2, X(b) - X(a)) + '" height="' + Math.max(2, bot - top) +
        '" fill="rgba(88,166,255,0.10)" stroke="#58a6ff" stroke-width="1" stroke-dasharray="3,2"/>';
      if (b - a >= 6) {
        s += '<text x="' + (X(a) + 3) + '" y="' + (top - 2) +
          '" fill="#58a6ff" font-size="7">中枢 ' + esc(p.zd) + '~' + esc(p.zg) + '</text>';
      }
    });
    if (lastPivotB >= 0 && lastPivotB < n - 1) {
      var lp = null;
      (data.pivots || []).forEach(function (p) {
        var b = idx[p.e]; if (b == null) b = n - 1;
        var a = idx[p.s]; if (a == null) a = 0;
        if (b > a) { var tt = a; a = b; b = tt; }
        if (b === lastPivotB && !lp) lp = p;
      });
      if (lp) {
        s += '<line x1="' + X(lastPivotB) + '" y1="' + Y(lp.zg) + '" x2="' + (W - pr) +
          '" y2="' + Y(lp.zg) + '" stroke="#58a6ff" stroke-width="0.9" stroke-dasharray="2,3" opacity="0.55"/>';
        s += '<line x1="' + X(lastPivotB) + '" y1="' + Y(lp.zd) + '" x2="' + (W - pr) +
          '" y2="' + Y(lp.zd) + '" stroke="#58a6ff" stroke-width="0.9" stroke-dasharray="2,3" opacity="0.55"/>';
      }
    }
    var bw = Math.max(1.2, step * 0.62);
    bars.forEach(function (b, i2) {
      var up = b.c >= b.o, col = up ? '#f85149' : '#3fb950', cx = X(i2);
      s += '<line x1="' + cx + '" y1="' + Y(b.h) + '" x2="' + cx + '" y2="' +
        Y(b.l) + '" stroke="' + col + '" stroke-width="1"/>';
      var yo = Y(Math.max(b.o, b.c)), yc = Y(Math.min(b.o, b.c));
      s += '<rect x="' + (cx - bw / 2) + '" y="' + yo + '" width="' + bw +
        '" height="' + Math.max(1, yc - yo) + '" fill="' + col + '"/>';
      if (b.v != null) {
        var vy = VY(b.v);
        s += '<rect x="' + (cx - bw / 2) + '" y="' + vy + '" width="' + bw +
          '" height="' + Math.max(1, volBot - vy) + '" fill="' + col + '" opacity="0.55"/>';
      }
    });
    (data.bis || []).forEach(function (b) {
      var a = idx[b.s], e = idx[b.e];
      if (a == null || e == null) return;
      s += '<line x1="' + X(a) + '" y1="' + Y(b.sp) + '" x2="' + X(e) + '" y2="' +
        Y(b.ep) + '" stroke="#8b949e" stroke-width="1" opacity="0.7"/>';
    });
    // 买卖点（可点击）
    (data.signals || []).forEach(function (sig, si) {
      var i3 = idx[sig.d];
      if (i3 == null || sig.price == null) return;
      var buy = String(sig.type || '').indexOf('买') >= 0;
      var col2 = buy ? '#f85149' : '#3fb950', cx2 = X(i3), cy2 = Y(sig.price), r = 5;
      var pts = buy
        ? (cx2 + ',' + (cy2 - r) + ' ' + (cx2 - r) + ',' + (cy2 + r) + ' ' + (cx2 + r) + ',' + (cy2 + r))
        : (cx2 + ',' + (cy2 + r) + ' ' + (cx2 - r) + ',' + (cy2 - r) + ' ' + (cx2 + r) + ',' + (cy2 - r));
      s += '<g class="m-sig" onclick="mChanSignal(' + si + ')" style="cursor:pointer">' +
        '<polygon points="' + pts + '" fill="' + col2 + '"/>' +
        '<text x="' + cx2 + '" y="' + (buy ? cy2 - r - 3 : cy2 + r + 10) +
        '" fill="' + col2 + '" font-size="8" text-anchor="middle">' + esc(sig.type) + '</text></g>';
    });
    var last = bars[n - 1];
    s += '<line x1="' + pl + '" y1="' + Y(last.c) + '" x2="' + (W - pr) + '" y2="' +
      Y(last.c) + '" stroke="#58a6ff" stroke-width="0.8" stroke-dasharray="2,3"/>';
    s += '<text x="' + (W - pr + 2) + '" y="' + (Y(last.c) + 3) +
      '" fill="#58a6ff" font-size="8" text-anchor="start">' + esc(last.c) + '</text>';
    s += '<text x="' + pl + '" y="' + (volTop - 3) +
      '" fill="#8b949e" font-size="7">成交量</text>';
    [0, Math.floor(n / 2), n - 1].forEach(function (i4) {
      s += '<text x="' + X(i4) + '" y="' + (H - 5) +
        '" fill="#8b949e" font-size="8" text-anchor="middle">' +
        esc((bars[i4].d || '').slice(5)) + '</text>';
    });
    s += '</svg>';
    return '<div class="m-card m-chart">' + s +
      '<div class="m-note" style="padding:6px 0 0">红涨绿跌 · 蓝框=中枢 · 蓝虚线=中枢延伸 · ' +
      '灰线=笔 · ▲买点/▼卖点（可点开看背驰/共振）· 下方=成交量 · ' +
      (CHAN_PERIOD_LABEL[period] || period) + '</div></div>';
  }

  // ── chan ────────────────────────────────────────────────
  function renderChan(d) {
    if (d.ok === false && d.status === 'facts_missing') {
      app.innerHTML = header('缠论详情', true) +
        '<div class="m-empty">该标的当日事实包未生成<br><span class="dim">' +
        esc(d.hint || '') + '</span></div>';
      return;
    }
    var ch = d.chanlun || {};
    var notices = (d.notices || []).map(function (n) {
      return '<div style="border-top:1px dashed var(--border);padding:8px 0">' +
        '<b>' + esc(n.title || n.name || '公告') + '</b>' +
        '<div class="dim" style="font-size:var(--fs-xs)">' + esc(n.date || n.ts || '') + ' ' +
        esc(n.source || '') + '</div></div>';
    }).join('');
    var steps = (d.decision || []).map(function (s) {
      return '<li>' + esc(s.name) + '<span class="when">' + esc(s.detail || '') + '</span></li>';
    }).join('');
    var stale = d.stale
      ? '<div class="m-stale">行情截至 ' + esc(d.ts || '—') +
        '（' + esc(d.quote_src || '缓存') + '）' +
        (d.facts_ts && d.facts_ts !== d.ts
          ? ' · 结构事实包缓存 ' + esc(d.facts_ts) : '') +
        '，仅作演示</div>'
      : '';
    window.mChan = d.chart || null;
    window.mChanCur = (d.chart && d.chart.default) || '1d';
    window.mChanSignals = ((d.chart || {}).periods || {})[window.mChanCur]
      ? (((d.chart.periods || {})[window.mChanCur] || {}).signals || []) : [];
    app.innerHTML = header('缠论详情', true) +
      '<div class="m-crumbs">选股 / <b>' + esc(d.name || d.code) + '</b></div>' + stale +
      '<div class="m-hero"><div class="name"><strong>' + esc(d.name) + '</strong>' +
      '<span class="code">' + esc(d.code) + '</span></div>' +
      '<div class="price"><b class="' + (Number(d.pct) > 0 ? 'up' : (Number(d.pct) < 0 ? 'dn' : '')) + '">' +
      esc(money(d.price)) + '</b>' + pct(d.pct) + '</div>' +
      '<div class="meta">行情 ' + esc(d.ts || '—') +
      '（' + esc(d.quote_src || '缓存') + '）' +
      (d.facts_ts && d.facts_ts !== d.ts
        ? ' · 结构缓存 ' + esc(d.facts_ts) : '') + '</div></div>' +
      '<div class="m-card concl"><span class="m-tag">' + esc(d.tag || '结构') + '</span>' +
      '<p>' + esc(d.conclusion || '暂无结论') + '</p></div>' +
      (d.chart ? chanPeriodTabs(window.mChanCur) : '') +
      '<div id="m-chan-chart">' + chanChart(d.chart, window.mChanCur) + '</div>' +
      '<div class="m-kv">' +
      '<div><label>中枢下沿</label><b>' + esc(ch.dzd == null ? '—' : ch.dzd) + '</b></div>' +
      '<div><label>中枢上沿</label><b>' + esc(ch.dzg == null ? '—' : ch.dzg) + '</b></div>' +
      '<div><label>状态</label><b>' + esc(ch.pivot_state || '—') + '</b></div>' +
      '<div><label>止损</label><b class="dn">' + esc(ch.stop == null ? '—' : ch.stop) + '</b></div>' +
      '<div><label>止盈1</label><b>' + esc(ch.tp1 == null ? '—' : ch.tp1) + '</b></div>' +
      '<div><label>止盈2</label><b>' + esc(ch.tp2 == null ? '—' : ch.tp2) + '</b></div></div>' +
      (d.rule_cache_ts
        ? '<div class="m-note" style="margin:-4px 16px 8px">止损/止盈为事实包规则线（缓存 ' +
          esc(d.rule_cache_ts) + '），行情与中枢已按日线 K 线最新校准</div>' : '') +
      '<div class="m-collapse open"><button onclick="this.parentNode.classList.toggle(\'open\')">' +
      '缠论结构<span class="chev">›</span></button><div class="body">' +
      '<p>' + esc(ch.basis || '—') + '</p><p class="dim">30m 中枢 ' +
      esc(ch.mzd == null ? '—' : ch.mzd) + ' ~ ' + esc(ch.mzg == null ? '—' : ch.mzg) + '</p></div></div>' +
      '<div class="m-collapse"><button onclick="this.parentNode.classList.toggle(\'open\')">' +
      '情报与资金<span class="chev">›</span></button><div class="body">' +
      (notices || '<p class="dim">暂无情报</p>') + '</div></div>' +
      '<div class="m-collapse"><button onclick="this.parentNode.classList.toggle(\'open\')">' +
      '决策链<span class="chev">›</span></button><div class="body"><ol class="m-steps">' +
      (steps || '<li class="dim">—</li>') + '</ol></div></div>' +
      '<div class="m-collapse"><button onclick="this.parentNode.classList.toggle(\'open\')">' +
      '证据与原文<span class="chev">›</span></button><div class="body"><p class="dim">' +
      '数据来源：量化引擎事实包 / 公开数据。非投资建议。</p></div></div>' +
      '<div class="m-actionbar"><button class="m-btn" onclick="history.back()">返回</button>' +
      '<button class="m-btn primary" onclick="location.href=((window.mNav&&mNav.position)||\'/m/position\')">看持仓</button></div>';
  }

  // ── position ────────────────────────────────────────────
  function renderPosition(d) {
    if (d.ok === false) { fail(d.error || '持仓数据不可用'); return; }
    var cards = (d.positions || []).map(function (p) {
      var cost = Number(p.cost || 0), price = Number(p.price || 0), sl = Number(p.sl || 0);
      var hi = Math.max(cost, price, sl) || 1;
      var w = Math.max(2, Math.min(98, price / hi * 100));
      var wpnl = p.pnl_pct == null ? null : p.pnl_pct;
      return '<div class="m-card"><div style="display:flex;justify-content:space-between">' +
        '<b>' + esc(p.name) + '</b>' + pct(wpnl) + '</div>' +
        '<div class="dim" style="font-size:var(--fs-xs)">' + esc(p.code) + ' · ' +
        esc(p.qty) + '股 · 成本 ' + esc(p.cost) + '</div>' +
        '<div class="m-bar"><i style="width:' + w + '%"></i>' +
        (sl ? '<em style="left:' + Math.max(2, Math.min(98, sl / hi * 100)) + '%"></em>' : '') + '</div>' +
        '<div class="m-bar-labels"><span>现价 ' + esc(p.price) + '</span>' +
        (sl ? '<span>止损 ' + esc(p.sl) + '</span>' : '<span>无止损线</span>') + '</div></div>';
    }).join('');
    app.innerHTML = header('持仓 · 止盈止损', true) +
      '<div class="m-hero"><div style="display:flex;justify-content:space-between">' +
      '<div><div class="meta">总资产</div><div style="font-size:22px;font-family:var(--font-mono)">' +
      esc(money(d.total)) + '</div></div>' +
      '<div style="text-align:right"><div class="meta">仓位</div>' +
      '<div style="font-size:22px;font-family:var(--font-mono)">' +
      (d.pos_pct == null ? '—' : (Number(d.pos_pct) * 100).toFixed(1) + '%') + '</div></div></div>' +
      '<div class="meta">截至 ' + esc(d.as_of || '—') + ' · 现金 ' + esc(money(d.cash)) +
      ' · 累计 ' + pct(d.cum_pct) + '</div></div>' +
      '<div class="m-card concl"><p>' + esc(d.headline || '') + '</p></div>' +
      (cards || '<div class="m-empty">当前无持仓</div>') +
      '<div class="m-note">线位修改可发「止盈 605100 8」「撤线 605100 tp」给「零界·指令」</div>' +
      '<div class="m-actionbar"><button class="m-btn" onclick="location.href=((window.mNav&&mNav.picks)||\'/m/picks\')">选股</button>' +
      '<button class="m-btn primary" onclick="location.reload()">刷新</button></div>';
  }

  // ── notify（通知流水 · 人话版） ──────────────────────────
  function renderNotify(d) {
    var rows = (d.recent || []).map(function (r) {
      return '<div class="m-row"><div class="info"><b>' + esc(r.kind_label || r.kind || '') + '</b>' +
        '<p>' + esc(r.ts) + ' · ' + esc(r.channel_label || r.channel || '') +
        ' · ' + esc(r.target) + '</p></div>' +
        '<div class="val"><b class="' + (r.ok ? 'ok' : 'bad') + '">' +
        esc(r.result || (r.ok ? '成功' : '失败')) + '</b></div></div>';
    }).join('');
    app.innerHTML = header('通知流水', true) +
      '<div class="m-crumbs">推送 / <b>最近 24 小时</b></div>' +
      '<div class="m-card concl"><p>' + esc(d.headline || '') + '</p></div>' +
      '<div class="m-kv"><div><label>总发送</label><b>' + esc(d.total) + '</b></div>' +
      '<div><label>成功</label><b class="ok">' + esc(d.ok) + '</b></div>' +
      '<div><label>失败</label><b class="' + (d.fail ? 'bad' : 'dim') + '">' + esc(d.fail) + '</b></div>' +
      '<div><label>静默拦截</label><b class="dim">' + esc(d.denied_silent == null ? 0 : d.denied_silent) + '</b></div></div>' +
      '<div class="m-crumbs">最近记录（倒序）</div>' + (rows || '<div class="m-empty">暂无记录</div>') +
      '<div class="m-note">' + esc(d.note || '') + '</div>' +
      '<div class="m-actionbar"><button class="m-btn" onclick="location.reload()">刷新</button>' +
      '<button class="m-btn" onclick="history.back()">返回</button></div>';
  }

  // ── subscription ────────────────────────────────────────
  function renderSubscription(d) {
    var plan = d.plan;
    var items = (d.items || []).map(function (it) {
      return '<div class="m-row"><div class="info"><b>' + esc(it.plan || it.kind || '订阅') + '</b>' +
        '<p>' + esc(it.start || '') + ' → ' + esc(it.end || '') + ' · ' + esc(it.status || '') + '</p></div>' +
        '<div class="val"><b>' + esc(it.status || '') + '</b></div></div>';
    }).join('');
    var body;
    if (d.state === 'unbound') {
      body = '<div class="m-empty">未绑定飞书身份<br>' +
        '<span class="dim">' + esc(d.note || '') + '</span><br><br>' +
        '<button class="m-btn primary" style="max-width:260px;margin:0 auto;display:inline-block" ' +
        'onclick="location.href=\'/api/m/auth/start?t=\'+encodeURIComponent(window.M_TOKEN||\'\')">' +
        '飞书授权 · 查看我的订阅</button></div>';
    } else if (!plan && !items) {
      body = '<div class="m-empty">暂无有效订阅<br><span class="dim">' +
        esc(d.note || '可私聊「零界·服务」开通') + '</span></div>';
    } else {
      body = '<div class="m-hero"><div class="meta">当前档位</div>' +
        '<div style="font-size:22px">' + esc((plan && plan.name) || '—') + '</div>' +
        '<div class="meta">到期 ' + esc((plan && plan.end) || '—') +
        ' · 状态 ' + esc((plan && plan.status) || '—') + '</div></div>' + items;
    }
    app.innerHTML = header('我的订阅', true) +
      '<div class="m-crumbs">客户 / <b>我的订阅</b></div>' + body +
      '<div class="m-note" style="margin-top:12px">' + esc(d.open_id_masked || '') +
      ' · 更新 ' + esc(d.as_of || '') + '</div>' +
      '<div class="m-actionbar"><button class="m-btn" onclick="history.back()">返回</button></div>';
  }

  // ── ops（P3 管理台只读） ────────────────────────────────
  function renderOps(d) {
    if (d.ok === false) { fail(d.error || '管理台数据不可用'); return; }
    var ports = Object.keys(d.ports || {}).map(function (p) {
      return '<div><label>' + esc(p) + '</label><b class="' +
        (d.ports[p] ? 'ok' : 'bad') + '">' + (d.ports[p] ? 'up' : 'down') + '</b></div>';
    }).join('');
    var bots = (d.bots || []).map(function (b) {
      var age = b.heartbeat_age_sec;
      var ok = age != null && age < 900;
      return '<div class="m-row"><div class="info"><b>' + esc(b.role) + '</b>' +
        '<p>心跳 ' + (age == null ? '无' : age + 's 前') + '</p></div>' +
        '<div class="val"><b class="' + (ok ? 'ok' : 'bad') + '">' +
        (ok ? 'OK' : 'STALE') + '</b></div></div>';
    }).join('');
    app.innerHTML = header('管理台 · 只读', true) +
      '<div class="m-kv">' + ports + '</div>' +
      '<div class="m-crumbs">机器人心跳</div>' + bots +
      '<div class="m-note">埋点事件文件更新于 ' +
      (d.events_age_sec == null ? '—' : d.events_age_sec + 's 前') + '</div>' +
      '<div class="m-actionbar"><button class="m-btn" onclick="location.reload()">刷新</button>' +
      '<button class="m-btn" onclick="location.href=((window.mNav&&mNav.notify)||\'/m/notify\')">通知流水</button></div>';
  }

  // ── entry（统一入口） ────────────────────────────────────
  function renderEntry(d) {
    var links = '';
    var curSec = '';
    (d.links || []).forEach(function (it) {
      var sec = it.section || '其他';
      if (sec !== curSec) {
        links += '<div class="m-crumbs">' + esc(sec) + '</div>';
        curSec = sec;
      }
      links += '<a class="m-row" href="' + esc(it.url || '#') + '">' +
        '<div class="info"><b>' + esc(it.title || it.scene) + '</b>' +
        '<p>' + esc(it.desc || '') + '</p></div><div class="val"><b>›</b></div></a>';
    });
    app.innerHTML = header('零界 · 移动端', false) +
      '<div class="m-hero"><strong style="font-size:22px">一个人的 AI 公司</strong>' +
      '<div class="meta">统一入口：所有飞书页面都在这里 · 令牌 7 天有效</div></div>' +
      (links || '<div class="m-empty">暂无入口</div>') +
      '<div class="m-actionbar"><button class="m-btn primary" onclick="location.reload()">刷新</button></div>';
  }

  // ── daily（数据日报） ────────────────────────────────────
  // ── daily（数据日报 · 人话版） ──────────────────────────
  function renderDaily(d) {
    if (d.ok === false) { fail(d.error || '数据日报不可用'); return; }
    var bias = d.bias || {}, tier = d.tier || {};
    var bp = d.bias_pct || {}, tp = d.tier_pct || {};
    var lv = d.level || 'ok';
    var banner = '<div class="' + (lv === 'bad' ? 'm-error' : (lv === 'warn' ? 'm-stale' : 'm-card')) + '">' +
      (lv === 'ok' ? '✓ ' : (lv === 'warn' ? '⚠ ' : '✕ ')) +
      esc(d.status_line || '') + '</div>';
    var evs = (d.events || []).map(function (e) {
      return '<div class="m-row"><div class="info"><b>' + esc(e.label || e.key) +
        '</b><span class="code">' + esc(e.key) + '</span></div>' +
        '<div class="val"><b>' + esc(e.count) + '</b><span class="dim">' +
        esc(e.pct) + '%</span></div></div>';
    }).join('');
    var alerts = (d.alerts || []).map(function (a) {
      return '<div class="m-collapse"><button onclick="this.parentNode.classList.toggle(\'open\')">' +
        '<span class="warn">' + esc(a.title || '漂移提醒') + '</span>' +
        '<span class="chev">›</span></button>' +
        '<div class="body"><p>' + esc(a.short || '') + '</p>' +
        '<p class="dim" style="white-space:pre-wrap">' + esc(a.msg || '') + '</p></div></div>';
    }).join('');
    var bad = (d.health_bad || []).map(function (b) {
      return '<div class="m-row"><div class="info"><b>' + esc(b.name || '') + '</b>' +
        '<p>' + esc((b.path || '').slice(-40)) + '</p></div>' +
        '<div class="val"><b class="bad">缺失</b></div></div>';
    }).join('');
    var bt = d.bt;
    var btHtml = '';
    if (bt) {
      function pv(v) { return v == null ? '—' : (Number(v) > 0 ? '+' : '') + Number(v).toFixed(2) + '%'; }
      btHtml = '<div class="m-crumbs">次日实证（样本内回看）</div>' +
        '<div class="m-card"><p class="dim">样本 ' + esc(bt.n == null ? '—' : bt.n) +
        ' 条 · ' + esc((bt.range || []).join(' ~ ')) + ' · 截至 ' + esc(bt.generated_at || '—') + '</p>' +
        '<p>偏多组次日均 <b>' + esc(pv(bt.bull_avg1)) + '</b>（相对大盘 ' + esc(pv(bt.bull_vs)) + '）<br>' +
        '偏空组次日均 <b>' + esc(pv(bt.bear_avg1)) + '</b>（相对大盘 ' + esc(pv(bt.bear_vs)) + '）</p>' +
        '<p class="dim" style="font-size:var(--fs-xs)">' + esc(bt.note || '') + '</p></div>';
    }
    app.innerHTML = header('数据日报', true) +
      '<div class="m-crumbs">情报 / <b>数据日报</b></div>' + banner +
      '<div class="m-card concl"><p>' + esc(d.headline || '') + '</p></div>' +
      '<div class="m-kv">' +
      '<div><label>偏多</label><b class="up">' + esc(bp.bull == null ? '—' : bp.bull) + '%</b><span class="dim" style="font-size:var(--fs-xs)"> ' + esc(bias.bull == null ? '' : bias.bull) + ' 条</span></div>' +
      '<div><label>偏空</label><b class="dn">' + esc(bp.bear == null ? '—' : bp.bear) + '%</b><span class="dim" style="font-size:var(--fs-xs)"> ' + esc(bias.bear == null ? '' : bias.bear) + ' 条</span></div>' +
      '<div><label>中性</label><b>' + esc(bp.neutral == null ? '—' : bp.neutral) + '%</b><span class="dim" style="font-size:var(--fs-xs)"> ' + esc(bias.neutral == null ? '' : bias.neutral) + ' 条</span></div>' +
      '<div><label>强信号类型</label><b>' + esc(tp.strong == null ? '—' : tp.strong) + '%</b><span class="dim" style="font-size:var(--fs-xs)"> ' + esc(tier.strong == null ? '' : tier.strong) + ' 条</span></div>' +
      '<div><label>弱信号类型</label><b>' + esc(tp.weak == null ? '—' : tp.weak) + '%</b><span class="dim" style="font-size:var(--fs-xs)"> ' + esc(tier.weak == null ? '' : tier.weak) + ' 条</span></div>' +
      '<div><label>未标注</label><b class="dim">' + esc(tp.unknown == null ? '—' : tp.unknown) + '%</b><span class="dim" style="font-size:var(--fs-xs)"> ' + esc(tier.unknown == null ? '' : tier.unknown) + ' 条</span></div></div>' +
      (evs ? '<div class="m-crumbs">事件类型（JEV 归类）</div>' + evs : '') +
      btHtml +
      (alerts ? '<div class="m-crumbs">漂移提醒（' + esc(d.drift_open == null ? 0 : d.drift_open) + '）· 点开看依据</div>' + alerts
              : '<div class="m-card"><p class="dim">✓ 近 7 天分布无显著漂移</p></div>') +
      '<div class="m-crumbs">管道体检（' + esc(d.health_total == null ? 0 : d.health_total) + ' 项）</div>' +
      (bad || '<div class="m-card"><p class="dim">✓ 全部数据源正常</p></div>') +
      '<div class="m-note">' + esc(d.note || '') + '<br>生成 ' + esc(d.generated_at || '—') + '</div>' +
      '<div class="m-actionbar"><button class="m-btn" onclick="history.back()">返回</button>' +
      '<button class="m-btn primary" onclick="location.reload()">刷新</button></div>';
  }

  // ── ledger（账本） ──────────────────────────────────────
  function renderLedger(d) {
    if (d.ok === false) { fail(d.error || '账本不可用'); return; }
    var s = d.summary || {};
    var rows = (d.open || []).map(function (p) {
      var pnl = p.pnl_pct;
      return '<div class="m-row"><div class="info"><b>' + esc(p.name || p.symbol) + '</b>' +
        '<span class="code">' + esc(p.symbol) + '</span>' +
        '<p>' + esc(p.market || '') + ' · ' + esc(p.strategy || '') + ' · ' +
        esc(p.days == null ? '' : p.days + '天') +
        ' · 现价 ' + esc(p.last_px == null ? '—' : Number(p.last_px).toFixed(2)) + '</p></div>' +
        '<div class="val"><b class="' + (Number(pnl) > 0 ? 'up' : (Number(pnl) < 0 ? 'dn' : 'dim')) + '">' +
        (pnl == null ? '—' : (Number(pnl) > 0 ? '+' : '') + Number(pnl).toFixed(2) + '%') + '</b></div></div>';
    }).join('');
    app.innerHTML = header('账本', true) +
      '<div class="m-crumbs">量化 / <b>账本</b></div>' +
      (d.stale ? '<div class="m-stale">账本快照 ' + esc(d.ledger_ts || '—') +
        '（非实时，仅演示）</div>' : '') +
      '<div class="m-card concl"><p>' + esc(d.headline || '') + '</p></div>' +
      '<div class="m-kv"><div><label>开仓</label><b>' + esc(s.open_n == null ? '—' : s.open_n) + '</b></div>' +
      '<div><label>待定</label><b>' + esc(s.pending_n == null ? '—' : s.pending_n) + '</b></div>' +
      '<div><label>已平</label><b>' + esc(s.closed_n == null ? '—' : s.closed_n) + '</b></div>' +
      '<div><label>胜率</label><b>' + esc(s.win_rate == null ? '—' : s.win_rate + '%') + '</b></div>' +
      '<div><label>平均盈亏</label><b class="' + (Number(s.avg_pnl) > 0 ? 'up' : (Number(s.avg_pnl) < 0 ? 'dn' : '')) + '">' +
      esc(s.avg_pnl == null ? '—' : s.avg_pnl + '%') + '</b></div>' +
      '<div><label>累计</label><b>' + esc(s.closed_total == null ? '—' : s.closed_total) + '%</b></div></div>' +
      '<div class="m-crumbs">当前开仓（' + esc((d.open || []).length) + '）</div>' +
      (rows || '<div class="m-empty">暂无开仓</div>') +
      '<div class="m-note">' + esc(d.note || '') + '</div>' +
      '<div class="m-actionbar"><button class="m-btn" onclick="history.back()">返回</button>' +
      '<button class="m-btn primary" onclick="location.reload()">刷新</button></div>';
  }

  // ── lineage（数据血缘 · 人话版） ──────────────────────────
  function renderLineage(d) {
    if (d.ok === false) { fail(d.error || '血缘数据不可用'); return; }
    var lv = d.level || 'ok';
    var bannerClass = lv === 'bad' ? 'm-error' : (lv === 'warn' ? 'm-stale' : 'm-card');
    var banner = '<div class="' + bannerClass + '">' +
      (lv === 'ok' ? '✓ ' : (lv === 'warn' ? '⚠ ' : '✕ ')) +
      esc(d.status_line || '') + '</div>';
    var rows = (d.rows || []).map(function (r) {
      var fr = r.freshness;
      var tag = (r.status !== 'ok') ? '<b class="bad">断链</b>' :
        (fr === 'watch' ? '<b class="warn">偏旧</b>' :
          '<b class="ok">新鲜</b>');
      return '<div class="m-row"><div class="info"><b>' + esc(r.label || r.table) + '</b>' +
        '<span class="code">' + esc(r.table || '') + '</span>' +
        '<p>' + esc(r.age_text || '') + ' · 产出 ' + esc(r.rows == null ? '—' : r.rows) + ' 行</p></div>' +
        '<div class="val">' + tag + '<span class="dim">' + esc(r.ended_at || '') + '</span></div></div>';
    }).join('');
    var att = (d.attention || []).map(function (a) {
      return '<div class="m-card"><b class="' + (a.level === 'bad' ? 'bad' : 'warn') + '">' +
        esc(a.label || a.table) + '</b>' +
        '<p class="dim">' + esc(a.why || '') + ' · ' + esc(a.table || '') + '</p></div>';
    }).join('');
    app.innerHTML = header('数据血缘', true) +
      '<div class="m-crumbs">情报 / <b>数据血缘</b></div>' + banner +
      '<div class="m-kv"><div><label>追踪表</label><b>' + esc(d.tracked == null ? '—' : d.tracked) + '</b></div>' +
      '<div><label>当前断链</label><b class="' + (d.broken_n ? 'bad' : 'ok') + '">' +
      esc(d.broken_n == null ? '—' : d.broken_n) + '</b></div>' +
      '<div><label>新鲜表</label><b class="ok">' + esc(d.fresh_n == null ? '—' : d.fresh_n) + '</b></div>' +
      '<div><label>偏旧表</label><b class="' + (d.watch_n ? 'warn' : 'dim') + '">' +
      esc(d.watch_n == null ? '—' : d.watch_n) + '</b></div>' +
      '<div><label>过期表</label><b class="' + (d.stale_n ? 'bad' : 'dim') + '">' +
      esc(d.stale_n == null ? '—' : d.stale_n) + '</b></div>' +
      '<div><label>历史失败运行</label><b class="dim">' +
      esc(d.history_fail_runs == null ? '—' : d.history_fail_runs) + '</b></div></div>' +
      (att ? '<div class="m-crumbs">需要关注（' + esc((d.attention || []).length) + '）</div>' + att
           : '<div class="m-card"><p class="dim">✓ 无需关注项</p></div>') +
      '<div class="m-crumbs">各表最近一次产出</div>' + (rows || '<div class="m-empty">暂无数据</div>') +
      '<div class="m-note">' + esc(d.note || '') + '</div>' +
      '<div class="m-actionbar"><button class="m-btn" onclick="history.back()">返回</button>' +
      '<button class="m-btn primary" onclick="location.reload()">刷新</button></div>';
  }

  // ── ticket（工单详情） ──────────────────────────────────
  function renderTicket(d) {
    if (d.ok === false) {
      app.innerHTML = header('工单详情', true) +
        '<div class="m-empty">' + esc(d.error || '工单不可用') +
        '<br><span class="dim">工单号：' + esc(d.id || '—') + '</span></div>' +
        '<div class="m-actionbar"><button class="m-btn" onclick="history.back()">返回</button></div>';
      return;
    }
    var t = d.ticket || {};
    var steps = (d.timeline || []).map(function (s) {
      return '<li>' + esc(s.label || '') + '<span class="when">' +
        esc(s.time || '') + (s.pending ? ' · 处理中' : '') + '</span></li>';
    }).join('');
    var wait = d.waited_min == null ? '—' : Number(d.waited_min).toFixed(1) + ' 分钟';
    var nudgeN = Number(d.nudge_n || 0);
    var replies = (d.replies || []).map(function (r) {
      return '<div class="m-row"><div class="info"><b>' + esc(r.by || '处理人') + '</b>' +
        '<p style="white-space:pre-wrap">' + esc(r.text || '') + '</p></div>' +
        '<div class="val"><span class="dim">' + esc((r.ts || '').replace('T', ' ')) + '</span></div></div>';
    }).join('');
    app.innerHTML = header('工单详情', true) +
      '<div class="m-crumbs">服务 / <b>' + esc(t.dispatch_id || d.id) + '</b></div>' +
      '<div class="m-card concl"><p>' + esc(d.headline || '') + '</p></div>' +
      '<div class="m-hero"><div class="name"><strong>' + esc(t.kind_label || '工单') + '</strong>' +
      '<span class="code">' + esc(d.status_label || t.state || '') + '</span></div>' +
      '<div class="meta">' + esc(t.intent_label || '') + ' · ' + esc(t.code || '') +
      ' · 路由 ' + esc(t.route_desc || '') + '</div></div>' +
      '<div class="m-kv"><div><label>提交</label><b>' + esc((t.submitted_at || '').replace('T', ' ').slice(5, 16)) + '</b></div>' +
      '<div><label>时限</label><b>' + esc(t.deadline_min == null ? '—' : t.deadline_min + ' 分钟') + '</b></div>' +
      '<div><label>身份</label><b>' + esc(t.open_id_masked || '—') + '</b></div></div>' +
      '<div class="m-kv"><div><label>已等待</label><b>' + esc(wait) + '</b></div>' +
      '<div><label>催办</label><b class="' + (nudgeN ? 'warn' : 'dim') + '">' +
      esc(nudgeN) + ' 次' + (d.escalate ? ' · 建议升级' : '') + '</b></div>' +
      '<div><label>回复</label><b>' + esc((d.replies || []).length) + ' 条</b></div></div>' +
      (d.can_nudge === false ? '' : '<div class="m-card"><button class="m-btn primary" style="width:100%" onclick="window.mTicketNudge()">催一下（提醒处理人）</button></div>') +
      '<div class="collapse m-collapse open"><button onclick="this.parentNode.classList.toggle(\'open\')">处理进度<span class="chev">›</span></button>' +
      '<div class="body"><ol class="m-steps">' + (steps || '<li class="dim">—</li>') + '</ol></div></div>' +
      '<div class="m-crumbs">处理人回复（' + esc((d.replies || []).length) + '）</div>' +
      (replies || '<div class="m-empty">还没有回复</div>') +
      (d.can_reply
        ? '<div class="m-card"><label class="dim">回复这条工单（处理人）</label>' +
          '<textarea id="m-ticket-reply" rows="3" style="width:100%;margin:8px 0;background:var(--bg-2);color:var(--fg-1);border:1px solid var(--line);border-radius:8px;padding:8px" placeholder="输入回复内容…"></textarea>' +
          '<button class="m-btn primary" onclick="window.mTicketReply()">发送回复</button></div>'
        : '<div class="m-card"><label class="dim">处理人回复</label>' +
          '<p class="dim" style="margin:6px 0">' + esc(d.reply_hint || '处理人回复需先用飞书身份登录') + '</p>' +
          '<button class="m-btn" style="width:100%" onclick="window.mTicketLogin()">飞书授权 · 处理人身份</button></div>') +
      (d.next_hint ? '<div class="m-note">' + esc(d.next_hint) + '</div>' : '') +
      '<div class="m-actionbar"><button class="m-btn" onclick="history.back()">返回</button>' +
      '<button class="m-btn primary" onclick="location.reload()">刷新</button></div>';
  }

  function ticketAutoNudge() {
    try {
      var q = new URLSearchParams(location.search);
      if (q.get('nudge') === '1' && PAGE === 'ticket') {
        setTimeout(function () { window.mTicketNudge(true); }, 500);
      }
    } catch (e) { /* noop */ }
  }

  // ── analysis（分析总览 · 统一分格） ─────────────────────
  function renderAnalysis(d) {
    if (d.ok === false) { fail(d.error || '分析总览不可用'); return; }
    var blocks = (d.blocks || []).map(function (b) {
      var t = b.type || 'md';
      if (t === 'stats') {
        var items = (b.items || []).map(function (it) {
          var lv = it.level || 'info';
          return '<div class="m-an-stat ' + esc(lv) + '">' +
            '<div class="lbl">' + esc(it.label || '') + '</div>' +
            '<div class="val">' + esc(it.value == null ? '—' : it.value) + '</div>' +
            '<div class="sub">' + esc(it.sub || '') + '</div></div>';
        }).join('');
        return '<div class="m-an-block"><div class="m-an-h">' + esc(b.title || '') + '</div>' +
          '<div class="m-an-stats">' + items + '</div>' +
          (b.note ? '<div class="m-an-note">' + esc(b.note) + '</div>' : '') + '</div>';
      }
      if (t === 'list') {
        var rows = (b.items || []).map(function (it) {
          return '<div class="m-an-row"><div class="info"><b>' + esc(it.left || '') + '</b>' +
            '<p>' + esc(it.sub || '') + '</p></div>' +
            '<div class="val"><b>' + esc(it.right || '') + '</b></div></div>';
        }).join('') || '<div class="m-empty">暂无数据</div>';
        return '<div class="m-an-block"><div class="m-an-h">' + esc(b.title || '') + '</div>' +
          rows + (b.note ? '<div class="m-an-note">' + esc(b.note) + '</div>' : '') + '</div>';
      }
      if (t === 'table') {
        var hd = (b.headers || []).map(function (h) {
          return '<th>' + esc(h) + '</th>';
        }).join('');
        var trs = (b.rows || []).map(function (row) {
          return '<tr>' + row.map(function (c) {
            var cls = /^[▲+\d]/.test(String(c)) ? 'up' : (/^[▼-]/.test(String(c)) ? 'dn' : '');
            return '<td class="' + cls + '">' + esc(c) + '</td>';
          }).join('') + '</tr>';
        }).join('');
        return '<div class="m-an-block"><div class="m-an-h">' + esc(b.title || '') + '</div>' +
          '<div class="m-an-tablewrap"><table class="m-an-table"><thead><tr>' + hd +
          '</tr></thead><tbody>' + trs + '</tbody></table></div>' +
          (b.note ? '<div class="m-an-note">' + esc(b.note) + '</div>' : '') + '</div>';
      }
      if (t === 'note') {
        return '<div class="m-an-block"><div class="m-an-note">' + esc(b.content || '') + '</div></div>';
      }
      var content = String(b.content || '').split('\n').slice(0, 60).map(function (ln) {
        return esc(ln);
      }).join('<br>');
      return '<div class="m-an-block"><div class="m-an-h">' + esc(b.title || '') + '</div>' +
        '<div class="m-an-md">' + content + '</div>' +
        (b.note ? '<div class="m-an-note">' + esc(b.note) + '</div>' : '') + '</div>';
    }).join('');
    app.innerHTML = header(d.title || '分析总览', true) +
      '<div class="m-crumbs">' + esc(d.crumbs || '分析 / 总览') + '</div>' +
      '<div class="m-an-banner"><b>' + esc(d.title || '分析总览') + '</b>' +
      '<p>' + esc(d.subtitle || '') + '</p>' +
      '<span class="dim">生成 ' + esc(d.generated_at || '') + '</span></div>' +
      blocks +
      '<div class="m-actionbar"><button class="m-btn" onclick="history.back()">返回</button>' +
      '<button class="m-btn primary" onclick="location.reload()">刷新</button></div>';
  }

  function render(data) {
    setTimeout(ticketAutoNudge, 0);
    window.mNav = data.nav || null;
    var scene = data.scene || PAGE;
    if (scene === 'entry') return renderEntry(data);
    if (scene === 'picks') return renderPicks(data);
    if (scene === 'chan') return renderChan(data);
    if (scene === 'position') return renderPosition(data);
    if (scene === 'notify') return renderNotify(data);
    if (scene === 'subscription') return renderSubscription(data);
    if (scene === 'ops') return renderOps(data);
    if (scene === 'daily') return renderDaily(data);
    if (scene === 'ledger') return renderLedger(data);
    if (scene === 'lineage') return renderLineage(data);
    if (scene === 'ticket') return renderTicket(data);
    if (scene === 'strategy') return renderStrategy(data);
    if (scene === 'analysis' || scene === 'brief' || scene === 'push' || scene === 'feed') return renderAnalysis(data);
    fail('未知场景：' + scene);
  }

  // 周期切换（日线/30F/60F）
  window.mChanPeriod = function (p) {
    if (!window.mChan || !window.mChan.periods || !window.mChan.periods[p]) return;
    window.mChanCur = p;
    window.mChanSignals = (window.mChan.periods[p].signals || []);
    var tabs = document.getElementById('m-chan-periods');
    if (tabs) {
      var bs = tabs.querySelectorAll('.m-chip');
      for (var i = 0; i < bs.length; i++) {
        if (bs[i].getAttribute('data-p') === p) bs[i].classList.add('on');
        else bs[i].classList.remove('on');
      }
    }
    var box = document.getElementById('m-chan-chart');
    if (box) box.innerHTML = chanChart(window.mChan, p);
    beacon([{event: 'm_action', scene: 'chan', action: 'period',
             result: p, ts: Math.floor(Date.now() / 1000)}]);
  };

  // 买卖点详情（背驰/共振/说明）
  window.mChanSignal = function (i) {
    var sig = (window.mChanSignals || [])[i];
    if (!sig) return;
    function fmt(v) {
      if (v == null) return '—';
      if (Array.isArray(v)) return v.map(fmt).join(' / ');
      if (typeof v === 'object') {
        var ks = Object.keys(v);
        if (!ks.length) return '—';
        return ks.slice(0, 4).map(function (k) { return k + '=' + fmt(v[k]); }).join(' ');
      }
      return String(v);
    }
    var rows = [
      ['类型', sig.type], ['时间', sig.dt], ['价格', sig.price],
      ['说明', sig.note || '—'],
      ['背驰', sig.bei ? ('是 · ' + fmt(sig.fv)) : '否'],
      ['二次背驰', sig.bei2 ? ('是 · ' + fmt(sig.fv2)) : '否'],
      ['量价背离', sig.vv ? ('是 · ' + fmt(sig.vv_ev)) : '否'],
      ['盘口', sig.pt ? (sig.pt_name || '是') : '否'],
      ['次级别共振', sig.mt == null ? '—' : (sig.mt ? ('是 · ' + fmt(sig.mtv)) : '否')],
      ['大盘环境', sig.reg == null ? '—' : (sig.reg + ' · ' + fmt(sig.reg_ev))]
    ];
    var html = '<div class="m-sheet-mask" onclick="mChanSignalClose()"></div>' +
      '<div class="m-sheet open"><h3>' + esc(sig.type || '买卖点') + ' 详情</h3>';
    rows.forEach(function (r) {
      html += '<div class="m-row"><div class="info"><b>' + esc(r[0]) + '</b></div>' +
        '<div class="val dim" style="max-width:62%;word-break:break-all">' + esc(fmt(r[1])) + '</div></div>';
    });
    html += '<div style="padding:12px 0 0"><button class="m-btn primary" style="width:100%" ' +
      'onclick="mChanSignalClose()">关闭</button></div></div>';
    var old = document.getElementById('m-chan-signal');
    if (old) old.remove();
    var div = document.createElement('div');
    div.id = 'm-chan-signal';
    div.innerHTML = html;
    document.body.appendChild(div);
  };
  window.mChanSignalClose = function () {
    var el = document.getElementById('m-chan-signal');
    if (el) el.remove();
  };

  function _mTicketPost(path, body) {
    return fetch(path, {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)}).then(function (r) { return r.json(); });
  }
  window.mTicketNudge = function (silent) {
    var t = window.M_TOKEN || '';
    if (!t) { alert('缺少页面令牌'); return; }
    _mTicketPost('/api/m/ticket/nudge', {t: t}).then(function (j) {
      if (j && j.ok) {
        if (!silent) alert('已催办第 ' + j.n + ' 次');
        location.reload();
      } else { alert((j && j.error) || '催办失败'); }
    }).catch(function (e) { alert('网络异常：' + e.message); });
  };
  window.mTicketLogin = function () {
    var t = encodeURIComponent(window.M_TOKEN || '');
    var ret = encodeURIComponent(location.pathname);
    location.href = '/api/m/auth/start?t=' + t + '&return=' + ret;
  };
  window.mTicketReply = function () {
    var ta = document.getElementById('m-ticket-reply');
    var v = ta ? ta.value.trim() : '';
    if (!v) { alert('请输入回复内容'); return; }
    _mTicketPost('/api/m/ticket/reply', {t: window.M_TOKEN, text: v}).then(function (j) {
      if (j && j.ok) {
        alert(j.sent ? '回复已推送给发起人' : '回复已记录（推送失败：' + (j.why || '') + '）');
        location.reload();
      } else if (j && /登录|授权/.test(j.error || '')) {
        window.mTicketLogin();
      } else { alert((j && j.error) || '回复失败'); }
    }).catch(function (e) { alert('网络异常：' + e.message); });
  };
  window.mHooks = {
    beacon: beacon,
    token: function () { return TOKEN; },
    esc: esc,
    header: header
  };
  document.addEventListener('DOMContentLoaded', load);
})();



/* ── 策略工单（环 3）：手机提交 + 回测/观察名单（自包含，不依赖 IIFE 内部函数） ── */
function stEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function stQ(id) { return document.getElementById(id); }

function stBlockHtml(b) {
  var t = b.type || 'md';
  if (t === 'stats') {
    var items = (b.items || []).map(function (it) {
      return '<div class="m-an-stat ' + stEsc(it.level || 'info') + '">' +
        '<div class="lbl">' + stEsc(it.label) + '</div>' +
        '<div class="val">' + stEsc(it.value == null ? '—' : it.value) + '</div>' +
        '<div class="sub">' + stEsc(it.sub || '') + '</div></div>';
    }).join('');
    return '<div class="m-an-block"><div class="m-an-h">' + stEsc(b.title || '') + '</div>' +
      '<div class="m-an-stats">' + items + '</div>' +
      (b.note ? '<div class="m-an-note">' + stEsc(b.note) + '</div>' : '') + '</div>';
  }
  if (t === 'table') {
    var hd = (b.headers || []).map(function (h) { return '<th>' + stEsc(h) + '</th>'; }).join('');
    var trs = (b.rows || []).map(function (row) {
      return '<tr>' + row.map(function (c) { return '<td>' + stEsc(c) + '</td>'; }).join('') + '</tr>';
    }).join('');
    return '<div class="m-an-block"><div class="m-an-h">' + stEsc(b.title || '') + '</div>' +
      '<div class="m-an-tablewrap"><table class="m-an-table"><thead><tr>' + hd +
      '</tr></thead><tbody>' + trs + '</tbody></table></div>' +
      (b.note ? '<div class="m-an-note">' + stEsc(b.note) + '</div>' : '') + '</div>';
  }
  if (t === 'md') {
    return '<div class="m-an-block"><div class="m-an-h">' + stEsc(b.title || '') + '</div>' +
      '<div class="m-an-note" style="white-space:pre-wrap;color:var(--text)">' +
      stEsc(b.content || '') + '</div>' +
      (b.note ? '<div class="m-an-note">' + stEsc(b.note) + '</div>' : '') + '</div>';
  }
  return '<div class="m-an-block"><div class="m-an-note">' + stEsc(b.content || '') + '</div></div>';
}

function renderStrategy(d) {
  var app = stQ('app');
  if (!app) return;
  if (d && d.ok === false) {
    app.innerHTML = '<div class="m-an-block"><div class="m-an-note">' +
      stEsc(d.error || '策略工单不可用') + '</div></div>';
    return;
  }
  var w = d.windows || {};
  var opts = Object.keys(w).map(function (k) {
    return '<option value="' + stEsc(k) + '">' + stEsc(w[k]) + '</option>';
  }).join('');
  var form = '<div class="m-an-block"><div class="m-an-h">提交选股方法</div>' +
    '<textarea id="st_method" rows="4" placeholder="例：缠论二买，全市场选股，top10，单票8%，持有不超过10天" ' +
    'style="width:100%;box-sizing:border-box;background:#0d1726;color:#e6f1ff;border:1px solid #2c4a68;border-radius:10px;padding:10px;font-size:14px"></textarea>' +
    '<div style="display:flex;gap:8px;margin-top:10px">' +
    '<select id="st_win" style="flex:1;background:#0d1726;color:#e6f1ff;border:1px solid #2c4a68;border-radius:10px;padding:9px">' +
    opts + '</select>' +
    '<button class="m-btn primary" style="flex:0 0 130px" onclick="stSubmit()">提交并回测</button></div>' +
    '<div id="st_msg" class="m-an-note" style="margin-top:8px"></div></div>';
  var blocks = (d.blocks || []).map(stBlockHtml).join('');
  if (d.detail) form = '';
  app.innerHTML =
    '<div class="m-header"><button class="m-icon" onclick="history.back()">←</button>' +
    '<h1>' + stEsc(d.title || '策略工单') + '</h1><span style="width:44px"></span></div>' +
    '<div class="m-crumbs">' + stEsc(d.crumbs || '情报 / 策略工单') + '</div>' +
    '<div class="m-hero"><div class="meta">' + stEsc(d.subtitle || '') +
    '<br>生成 ' + stEsc(d.generated_at || '') + '</div></div>' +
    form + blocks +
    '<div class="m-actionbar"><button class="m-btn" onclick="location.reload()">刷新</button>' +
    '<button class="m-btn" onclick="history.back()">返回</button></div>';
}

function stSubmit() {
  var m = (stQ('st_method') && stQ('st_method').value || '').trim();
  var msg = stQ('st_msg');
  if (!msg) return;
  if (m.length < 10) { msg.textContent = '方法至少 10 字'; return; }
  var tok = window.M_TOKEN || '';
  if (!tok) { msg.textContent = '页面令牌缺失，请从飞书卡片重新打开'; return; }
  msg.textContent = '提交中…';
  fetch('/api/m/strategy/submit', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ t: tok, method: m, window: (stQ('st_win') && stQ('st_win').value) || '1y' })
  }).then(function (r) { return r.json(); }).then(function (d) {
    msg.textContent = d.ok ? ('✅ ' + (d.msg || '已受理') + ' · ' + (d.order_id || '')) : ('❌ ' + (d.error || '提交失败'));
    if (d.ok) setTimeout(function () { location.reload(); }, 1500);
  }).catch(function (e) { msg.textContent = '提交失败：' + e; });
}
