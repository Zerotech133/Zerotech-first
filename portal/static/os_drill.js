/* OS 全站下钻/返回交互层 v1（2026-10-30）
 * - 给含链接的 .card 自动加「下钻」按钮；
 * - 点击打开只读详情抽屉，抽屉内「返回」关闭并归还焦点；
 * - 支持 [data-os-drill] 显式元素、[data-os-back] 返回上一页；
 * - 不改各页业务逻辑，不发起写请求。
 */
(function () {
  'use strict';
  if (window.__OS_DRILL_READY__) return;
  window.__OS_DRILL_READY__ = true;

  var DIALOG_ID = 'os-drill-dialog';
  var lastFocus = null;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function dialog() {
    var d = document.getElementById(DIALOG_ID);
    if (d) return d;
    d = el('dialog', 'os-drill-dialog');
    d.id = DIALOG_ID;
    d.setAttribute('aria-labelledby', 'os-drill-title');
    var inner = el('div', 'os-drill-inner');
    var head = el('div', 'os-drill-head');
    var title = el('h2', null, '详情');
    title.id = 'os-drill-title';
    var close = el('button', 'os-drill-close', '×');
    close.type = 'button';
    close.setAttribute('aria-label', '关闭详情');
    head.append(title, close);
    var body = el('div', 'os-drill-body');
    var text = el('p');
    text.id = 'os-drill-text';
    var meta = el('div', 'os-drill-meta');
    meta.id = 'os-drill-meta';
    body.append(text, meta);
    var foot = el('div', 'os-drill-foot');
    var link = el('a', null, '打开原页 ↗');
    link.id = 'os-drill-link';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    var back = el('button', null, '← 返回');
    back.type = 'button';
    back.id = 'os-drill-back';
    foot.append(link, back);
    inner.append(head, body, foot);
    d.append(inner);
    document.body.appendChild(d);

    function hide() {
      try { d.close(); } catch (e) { d.removeAttribute('open'); }
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }
    close.addEventListener('click', hide);
    back.addEventListener('click', hide);
    d.addEventListener('cancel', function (e) { e.preventDefault(); hide(); });
    d.addEventListener('click', function (e) {
      if (e.target === d) hide();
    });
    return d;
  }

  function openDrill(info) {
    var d = dialog();
    lastFocus = document.activeElement;
    d.querySelector('#os-drill-title').textContent = info.title || '详情';
    d.querySelector('#os-drill-text').textContent = info.text || '无更多文字说明。';
    var meta = d.querySelector('#os-drill-meta');
    meta.replaceChildren();
    (info.meta || []).forEach(function (pair) {
      var k = el('b', null, pair[0]);
      var v = el('span', null, pair[1]);
      meta.append(k, v);
    });
    var link = d.querySelector('#os-drill-link');
    if (info.href) {
      link.href = info.href;
      link.hidden = false;
    } else {
      link.hidden = true;
      link.removeAttribute('href');
    }
    try { d.showModal(); } catch (e) { d.setAttribute('open', ''); }
    var back = d.querySelector('#os-drill-back');
    if (back) back.focus();
  }

  function cardInfo(card) {
    var titleEl = card.querySelector('h1, h2, h3, h4, strong');
    var link = card.querySelector('a[href]');
    var textEls = card.querySelectorAll('p, small, .desc, .subtle');
    var parts = [];
    textEls.forEach(function (n) {
      var t = (n.textContent || '').trim();
      if (t && parts.indexOf(t) < 0 && t !== (titleEl && titleEl.textContent || '').trim()) {
        parts.push(t);
      }
    });
    return {
      title: titleEl ? (titleEl.textContent || '').trim() : '卡片详情',
      text: parts.join('\n').slice(0, 1200),
      href: link ? link.getAttribute('href') : '',
      meta: [
        ['页面', document.title || location.pathname],
        ['来源', location.pathname],
        ['交互', '只读下钻 · 不发起写请求']
      ]
    };
  }

  function decorate(root) {
    var cards = (root || document).querySelectorAll('.card');
    cards.forEach(function (card) {
      if (!card.querySelector('a[href]')) return;
      if (card.querySelector(':scope > .os-drill-btn')) return;
      var btn = el('button', 'os-drill-btn', '下钻详情');
      btn.type = 'button';
      btn.setAttribute('aria-label', '下钻查看详情');
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        openDrill(cardInfo(card));
      });
      card.appendChild(btn);
    });
  }

  document.addEventListener('click', function (e) {
    var back = e.target.closest && e.target.closest('[data-os-back]');
    if (back) {
      e.preventDefault();
      if (history.length > 1) history.back();
      else location.href = '/os-entry';
      return;
    }
    var explicit = e.target.closest && e.target.closest('[data-os-drill]');
    if (explicit) {
      e.preventDefault();
      openDrill({
        title: explicit.getAttribute('data-title') || (explicit.textContent || '').trim() || '详情',
        text: explicit.getAttribute('data-text') || '',
        href: explicit.getAttribute('data-href') || '',
        meta: [['页面', document.title || location.pathname], ['交互', '只读下钻']]
      });
    }
  });

  function start() {
    decorate(document);
    if (window.MutationObserver) {
      var timer = null;
      var mo = new MutationObserver(function () {
        if (timer) return;
        timer = setTimeout(function () {
          timer = null;
          decorate(document);
        }, 120);
      });
      mo.observe(document.body, { childList: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
