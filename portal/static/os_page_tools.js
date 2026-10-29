/* OS 通用页面工具 v1（2026-10-30）
 * 搜索 + 分区折叠 + 结果计数；只作用于当前页 DOM，不发请求、不写状态。
 * 下钻由全站 os_drill.js 负责；本文件只补“搜索/折叠”。
 */
(function () {
  'use strict';
  if (window.__OS_PAGE_TOOLS_READY__) return;
  window.__OS_PAGE_TOOLS_READY__ = true;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function root() {
    return document.querySelector('main, .main, .shell, .content, #view') || document.body;
  }

  function headingOf(block) {
    var direct = block.querySelector(':scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > .sec-line, :scope > .sec-head, :scope > .section-head, :scope > .panel-head, :scope > header, :scope > .top');
    if (direct) return direct;
    return null;
  }

  // /os-board 用 .sec-line + .tiles/.agents 兄弟结构；先包成 section，
  // 内容/ID/事件都不动，只为通用折叠与过滤提供边界。
  function normalizeBoard() {
    var main = document.querySelector('main');
    if (!main) return;
    main.querySelectorAll('.sec-line').forEach(function (head) {
      if (head.closest('.os-board-sec')) return;
      var next = head.nextElementSibling;
      if (!next) return;
      var cls = ' ' + String(next.className || '') + ' ';
      if (!/\s(tiles|agents|grid|split|two|three|board-grid)\s/.test(cls)) return;
      var sec = document.createElement('section');
      sec.className = 'os-board-sec';
      head.parentNode.insertBefore(sec, head);
      sec.appendChild(head);
      sec.appendChild(next);
    });
  }

  function candidates() {
    var r = root();
    var all = Array.prototype.slice.call(r.querySelectorAll('section, .sec, .panel, article.panel, .board-section, .table-wrap'));
    return all.filter(function (b) {
      if (b.closest('.os-page-tools')) return false;
      if (b.hasAttribute('data-os-no-fold')) return false;
      var text = (b.textContent || '').trim();
      if (text.length < 80) return false;
      var h = headingOf(b);
      if (!h) return false;
      // 只收“有头 + 至少两个内容子节点”的块，避免折叠单行小卡
      var kids = Array.prototype.slice.call(b.children).filter(function (n) { return n !== h; });
      return kids.length >= 1;
    });
  }

  function mark() {
    candidates().forEach(function (block) {
      if (block.dataset.osToolBlock === '1') return;
      block.dataset.osToolBlock = '1';
      block.classList.add('os-tool-block');
      var h = headingOf(block);
      h.dataset.osHeading = '1';
      h.classList.add('os-tool-head');
      var btn = el('button', 'os-tool-toggle', '折叠');
      btn.type = 'button';
      btn.setAttribute('aria-label', '折叠/展开本分区');
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        var closed = block.classList.toggle('os-tool-collapsed');
        btn.textContent = closed ? '展开' : '折叠';
        btn.setAttribute('aria-expanded', String(!closed));
      });
      h.appendChild(btn);
    });
  }

  function toolbar() {
    var old = document.querySelector('.os-page-tools');
    if (old) return old;
    var bar = el('div', 'os-page-tools');
    bar.setAttribute('role', 'search');
    var label = el('label', 'os-tools-search');
    var icon = el('span', 'os-tools-icon', '⌕');
    icon.setAttribute('aria-hidden', 'true');
    var input = el('input');
    input.type = 'search';
    input.placeholder = '搜索当前页：指标 / 分区 / 名称';
    input.setAttribute('aria-label', '搜索当前页');
    label.append(icon, input);
    var count = el('span', 'os-tools-count', '输入以过滤');
    var fold = el('button', 'os-tools-btn', '折叠全部分区');
    var unfold = el('button', 'os-tools-btn', '展开全部');
    fold.type = 'button';
    unfold.type = 'button';
    fold.addEventListener('click', function () {
      document.querySelectorAll('.os-tool-block').forEach(function (b) {
        b.classList.add('os-tool-collapsed');
        var t = b.querySelector(':scope > .os-tool-head .os-tool-toggle');
        if (t) { t.textContent = '展开'; t.setAttribute('aria-expanded', 'false'); }
      });
    });
    unfold.addEventListener('click', function () {
      document.querySelectorAll('.os-tool-block').forEach(function (b) {
        b.classList.remove('os-tool-collapsed');
        var t = b.querySelector(':scope > .os-tool-head .os-tool-toggle');
        if (t) { t.textContent = '折叠'; t.setAttribute('aria-expanded', 'true'); }
      });
    });
    input.addEventListener('input', function () {
      var q = (input.value || '').trim().toLowerCase();
      var blocks = Array.prototype.slice.call(document.querySelectorAll('.os-tool-block'));
      var cardSel = '.acard, .card, .tile, .item, .scard, .quick, .panel, .evidence-row, .cap-item';
      var cards = Array.prototype.slice.call(document.querySelectorAll(cardSel));
      var items = blocks.concat(cards);
      var seen = new Set();
      var hit = 0;
      items.forEach(function (node) {
        if (seen.has(node)) return;
        seen.add(node);
        var ok = !q || (node.textContent || '').toLowerCase().indexOf(q) >= 0;
        if (node.classList.contains('os-tool-block')) {
          node.hidden = !ok;
        } else {
          node.hidden = !ok;
        }
        if (ok && q) hit++;
      });
      count.textContent = q ? ('命中 ' + hit + ' 个区块') : '输入以过滤';
    });
    bar.append(label, count, fold, unfold);
    var r = root();
    r.insertBefore(bar, r.firstChild);
    return bar;
  }

  function start() {
    normalizeBoard();
    toolbar();
    mark();
    // 动态页（hash 路由/异步渲染）重新标记，避免折叠按钮丢失
    if (window.MutationObserver) {
      var timer = null;
      new MutationObserver(function () {
        if (timer) return;
        timer = setTimeout(function () { timer = null; mark(); }, 350);
      }).observe(root(), {childList: true, subtree: true});
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, {once: true});
  } else {
    start();
  }
})();
