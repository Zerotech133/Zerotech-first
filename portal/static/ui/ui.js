/* 零界 UI Kit v0.1：无依赖交互原语（toast / dialog / tabs） */
(function () {
  'use strict';
  if (window.__UI_KIT_READY__) return;
  window.__UI_KIT_READY__ = true;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function host() {
    var h = document.querySelector('.ui-toast-host');
    if (!h) {
      h = el('div', 'ui-toast-host');
      h.setAttribute('role', 'status');
      h.setAttribute('aria-live', 'polite');
      document.body.appendChild(h);
    }
    return h;
  }

  function toast(message, ms) {
    var h = host();
    var n = el('div', 'ui-toast', message);
    h.appendChild(n);
    window.setTimeout(function () { n.remove(); }, ms || 3200);
    return n;
  }

  function dialog(opts) {
    opts = opts || {};
    var d = document.querySelector('.ui-drawer[data-ui-auto]');
    if (!d) {
      d = el('dialog', 'ui-drawer');
      d.setAttribute('data-ui-auto', '1');
      var head = el('div', 'ui-drawer__head');
      var title = el('h2', null, '详情');
      var close = el('button', 'ui-btn ui-btn--sm', '×');
      close.type = 'button';
      close.setAttribute('aria-label', '关闭');
      head.append(title, close);
      var body = el('div', 'ui-drawer__body');
      var foot = el('div', 'ui-drawer__foot');
      var ok = el('button', 'ui-btn ui-btn--primary', '知道了');
      ok.type = 'button';
      foot.appendChild(ok);
      d.append(head, body, foot);
      document.body.appendChild(d);
      function hide() {
        try { d.close(); } catch (e) { d.removeAttribute('open'); }
      }
      close.addEventListener('click', hide);
      ok.addEventListener('click', hide);
      d.addEventListener('cancel', function (e) { e.preventDefault(); hide(); });
      d._uiBody = body;
      d._uiTitle = title;
    }
    d._uiTitle.textContent = opts.title || '详情';
    d._uiBody.textContent = opts.body || '';
    try { d.showModal(); } catch (e) { d.setAttribute('open', ''); }
    return d;
  }

  function initTabs() {
    document.querySelectorAll('[data-ui-tabs]').forEach(function (root) {
      var tabs = Array.prototype.slice.call(root.querySelectorAll('[role="tab"][data-tab]'));
      var panels = Array.prototype.slice.call(root.querySelectorAll('[data-tabpanel]'));
      function select(key) {
        tabs.forEach(function (t) {
          var on = t.getAttribute('data-tab') === key;
          t.setAttribute('aria-selected', String(on));
          t.tabIndex = on ? 0 : -1;
        });
        panels.forEach(function (p) {
          p.hidden = p.getAttribute('data-tabpanel') !== key;
        });
      }
      tabs.forEach(function (t) {
        t.addEventListener('click', function () { select(t.getAttribute('data-tab')); });
        t.addEventListener('keydown', function (e) {
          var i = tabs.indexOf(t);
          if (e.key === 'ArrowRight') { e.preventDefault(); tabs[(i + 1) % tabs.length].focus(); tabs[(i + 1) % tabs.length].click(); }
          if (e.key === 'ArrowLeft') { e.preventDefault(); tabs[(i - 1 + tabs.length) % tabs.length].focus(); tabs[(i - 1 + tabs.length) % tabs.length].click(); }
        });
      });
      if (tabs.length) select(tabs[0].getAttribute('data-tab'));
    });
  }

  function initTriggers() {
    document.querySelectorAll('[data-ui-toast]').forEach(function (btn) {
      btn.addEventListener('click', function () { toast(btn.getAttribute('data-ui-toast') || '操作完成'); });
    });
    document.querySelectorAll('[data-ui-dialog]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        dialog({
          title: btn.getAttribute('data-ui-dialog-title') || '详情',
          body: btn.getAttribute('data-ui-dialog-body') || ''
        });
      });
    });
  }

  function init() {
    initTabs();
    initTriggers();
  }

  window.UI = {toast: toast, dialog: dialog, init: init};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
})();
