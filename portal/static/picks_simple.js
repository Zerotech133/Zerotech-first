/* picks_simple.js — /picks 默认精简 + 高级筛选 + 按页面/按日期的筛选记忆。
   本地 localStorage，不上传；只记 UI 筛选状态，不涉及账户/交易数据。
   记忆绑定扫描日：新交易日自动失效回默认；重置只清当前页。 */
(function () {
  'use strict';
  var MAX = 20;
  var STORE_PREFIX = 'zero.picks.filters.v2:';
  var LEGACY_KEY = 'zero.picks.filters.v1';
  var pageKey = (location.pathname || '/').replace(/\/+$/, '') || '/';
  var STORE_KEY = STORE_PREFIX + pageKey;
  var showAll = false;
  var timer = null;
  var restoring = false;
  var restored = false;

  function $(sel, root) { return (root || document).querySelector(sel); }

  function rows() {
    var list = $('#list');
    return list ? Array.prototype.slice.call(list.querySelectorAll('.lrow')) : [];
  }

  function activeTier() {
    var b = $('#filters .fbtn.on[data-t]');
    return b ? (b.getAttribute('data-t') || '') : '';
  }

  function activeAttr(attr) {
    var b = $('#filters .fbtn.on[' + attr + ']');
    return b ? (b.getAttribute(attr) || '') : '';
  }

  function hasRows() { return rows().length > 0; }

  function dataDate() {
    try {
      if (typeof DATA !== 'undefined' && DATA) {
        var raw = DATA.scan_run_date || DATA.trade_basis || '';
        var m0 = String(raw).match(/\d{4}-\d{2}-\d{2}/);
        if (m0) return m0[0];
      }
    } catch (e) { /* DATA 尚未初始化：回退 DOM */ }
    var txt = ($('#vitals') && $('#vitals').innerText) || '';
    var m = txt.match(/扫描日\s*([0-9]{4}-[0-9]{2}-[0-9]{2})/);
    return m ? m[1] : '';
  }

  function updateMemInfo(msg) {
    var el = $('#p4-mem');
    if (el) el.textContent = '记忆：' + msg;
  }

  function reapply() {
    var list = rows();
    var shown = 0;
    var active = activeTier();
    var explicit = active !== '';
    list.forEach(function (row) {
      var tier = row.querySelector('.tier');
      var tierText = tier ? (tier.textContent || '').trim() : '';
      var isT12 = tierText === 'T1' || tierText === 'T2';
      // 显式筛选优先；默认「全部」才收成 T1/T2 前 20 条。
      var show = showAll || (explicit ? true : isT12);
      if (!showAll && shown >= MAX) show = false;
      if (show) shown += 1;
      row.style.display = show ? '' : 'none';
    });
    var btn = $('#p2-toggle');
    var info = $('#p2-info');
    if (btn) btn.textContent = showAll ? '只看 T1/T2 · 前 20 条' : '显示全部清单';
    if (info) info.textContent = showAll ? '当前显示全部'
      : (explicit ? '筛选 ' + active + ' · 前 20 条' : '默认：仅 T1/T2，前 20 条');
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {
      reapply();
      tryRestore();
    }, 80);
  }

  function ensureUI() {
    var filters = $('#filters');
    if (!filters || $('#p2-bar')) return;
    var bar = document.createElement('div');
    bar.id = 'p2-bar';
    bar.innerHTML = '<span>清单视图：<b>默认精简</b></span>' +
      '<span class="p2-note" id="p2-info">默认：仅 T1/T2，前 20 条</span>' +
      '<span class="p2-note" id="p4-mem">记忆：无</span>' +
      '<button type="button" id="p2-toggle">显示全部清单</button>' +
      '<button type="button" id="p2-reset">重置本页筛选</button>';
    filters.parentNode.insertBefore(bar, filters.nextSibling);
    bar.querySelector('#p2-toggle').addEventListener('click', function () {
      showAll = !showAll;
      reapply();
      saveState();
    });
    bar.querySelector('#p2-reset').addEventListener('click', resetState);
  }

  function initAdvancedFilters() {
    var filters = $('#filters');
    if (!filters || $('#p3-filter-toggle')) return;
    var searchBtn = $('#filters #q');
    searchBtn = searchBtn ? searchBtn.closest('.fbtn') : null;
    Array.prototype.forEach.call(filters.children, function (el) {
      if (el === searchBtn) return;
      el.classList.add('p3-advanced');
    });
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.id = 'p3-filter-toggle';
    btn.className = 'p3-filter-toggle';
    btn.textContent = '高级筛选';
    btn.setAttribute('aria-expanded', 'false');
    filters.insertBefore(btn, filters.firstChild);
    btn.addEventListener('click', function () {
      var open = filters.classList.toggle('p3-open');
      btn.textContent = open ? '收起筛选' : '高级筛选';
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      saveState();
    });
  }

  function currentState() {
    return {
      page: pageKey,
      date: dataDate(),
      t: activeTier(),
      s: activeAttr('data-s'),
      v: activeAttr('data-v'),
      q: ($('#q') && $('#q').value.trim()) || '',
      showAll: !!showAll,
      advanced: !!($('#filters') && $('#filters').classList.contains('p3-open')),
      savedAt: new Date().toISOString()
    };
  }

  function saveState() {
    if (restoring) return;
    try {
      var st = currentState();
      localStorage.setItem(STORE_KEY, JSON.stringify(st));
      updateMemInfo(st.date || '当前页');
    } catch (e) { /* 无 localStorage/隐私模式：静默降级 */ }
  }

  function loadState() {
    try {
      var raw = localStorage.getItem(STORE_KEY);
      // 兼容 P4 单 key 记忆：仅迁移一次，立刻落到本页 key
      if (!raw) {
        raw = localStorage.getItem(LEGACY_KEY);
        if (raw) {
          var old = JSON.parse(raw);
          old.page = pageKey;
          old.date = old.date || dataDate();
          localStorage.setItem(STORE_KEY, JSON.stringify(old));
          localStorage.removeItem(LEGACY_KEY);
          raw = JSON.stringify(old);
        }
      }
      if (!raw) return null;
      var st = JSON.parse(raw);
      if (!st || typeof st !== 'object') return null;
      if (st.page && st.page !== pageKey) return null;
      var today = dataDate();
      if (st.date && today && st.date !== today) {
        localStorage.removeItem(STORE_KEY);
        return { stale: true, oldDate: st.date, today: today };
      }
      return st;
    } catch (e) { return null; }
  }

  function clickFilter(attr, value) {
    var btn;
    if (attr === 'data-t' && !value) {
      btn = $('#filters .fbtn[data-t=""]');
    } else {
      btn = value ? $('#filters .fbtn[' + attr + '="' + value + '"]') : null;
    }
    if (btn) btn.click();
  }

  function restoreState(st) {
    if (restored) return;
    restored = true;
    restoring = true;
    var filters = $('#filters');
    var advBtn = $('#p3-filter-toggle');
    if (st.advanced && filters && !filters.classList.contains('p3-open') && advBtn) advBtn.click();
    if (st.q) {
      var q = $('#q');
      if (q) {
        q.value = st.q;
        q.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }
    if (st.t) clickFilter('data-t', st.t);
    if (st.s) clickFilter('data-s', st.s);
    if (st.v) clickFilter('data-v', st.v);
    showAll = !!st.showAll;
    reapply();
    updateMemInfo(st.date || '当前页');
    setTimeout(function () { restoring = false; }, 700);
  }

  function tryRestore() {
    if (restored || !hasRows()) return;
    var st = loadState();
    if (!st) { restored = true; updateMemInfo('无'); return; }
    if (st.stale) {
      restored = true;
      reapply();
      updateMemInfo('日期变化，已重置');
      return;
    }
    restoreState(st);
  }

  function resetState() {
    restoring = true;
    try {
      localStorage.removeItem(STORE_KEY);
      localStorage.removeItem(LEGACY_KEY);
    } catch (e) { /* noop */ }
    var filters = $('#filters');
    if (filters && filters.classList.contains('p3-open')) {
      var adv = $('#p3-filter-toggle');
      if (adv) adv.click();
    }
    clickFilter('data-t', '');
    clickFilter('data-s', 'tier');
    clickFilter('data-v', 'flat');
    var q = $('#q');
    if (q) {
      q.value = '';
      q.dispatchEvent(new Event('input', { bubbles: true }));
    }
    showAll = false;
    reapply();
    updateMemInfo('无');
    setTimeout(function () { restoring = false; }, 500);
  }

  function setupSave() {
    document.addEventListener('click', function (e) {
      if (restoring) return;
      if (e.target.closest('#filters, #p2-bar')) setTimeout(saveState, 0);
    });
    document.addEventListener('input', function (e) {
      if (restoring) return;
      if (e.target && e.target.id === 'q') setTimeout(saveState, 0);
    });
  }

  function init() {
    initAdvancedFilters();
    setupSave();
    var list = $('#list');
    if (!list) return;
    ensureUI();
    reapply();
    var mo = new MutationObserver(schedule);
    mo.observe(list, { childList: true, subtree: true });
    setTimeout(tryRestore, 500);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
