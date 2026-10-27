/* picks_simple.js — P2 /picks 默认精简：T1/T2 + 前 20 条，显示全部可切换。 */
(function () {
  'use strict';
  var MAX = 20;
  var showAll = false;
  var timer = null;

  function rows() {
    var list = document.querySelector('#list');
    return list ? Array.prototype.slice.call(list.querySelectorAll('.lrow')) : [];
  }

  function activeTier() {
    var b = document.querySelector('#filters .fbtn.on[data-t]');
    return b ? (b.getAttribute('data-t') || '') : '';
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
      // 用户显式点了某个筛选（T3/CROWD/T1...）时，以页面筛选结果为准；
      // 只有在默认「全部」下才收成 T1/T2 前 20 条。
      var show = showAll || (explicit ? true : isT12);
      if (!showAll && shown >= MAX) show = false;
      if (show) shown += 1;
      row.style.display = show ? '' : 'none';
    });
    var btn = document.querySelector('#p2-toggle');
    var info = document.querySelector('#p2-info');
    if (btn) btn.textContent = showAll ? '只看 T1/T2 · 前 20 条' : '显示全部清单';
    if (info) info.textContent = showAll ? '当前显示全部'
      : (explicit ? '筛选 ' + active + ' · 前 20 条' : '默认：仅 T1/T2，前 20 条');
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(reapply, 80);
  }

  function ensureUI() {
    var filters = document.querySelector('#filters');
    if (!filters || document.querySelector('#p2-bar')) return;
    var bar = document.createElement('div');
    bar.id = 'p2-bar';
    bar.innerHTML = '<span>清单视图：<b>默认精简</b></span>' +
      '<span class="p2-note" id="p2-info">默认：仅 T1/T2，前 20 条</span>' +
      '<button type="button" id="p2-toggle">显示全部清单</button>';
    filters.parentNode.insertBefore(bar, filters.nextSibling);
    bar.querySelector('#p2-toggle').addEventListener('click', function () {
      showAll = !showAll;
      reapply();
    });
  }

  function init() {
    var list = document.querySelector('#list');
    if (!list) return;
    ensureUI();
    reapply();
    var mo = new MutationObserver(schedule);
    mo.observe(list, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})();
