/* page_shell.js — W1 PageShell v1
   统一壳：全局面包屑 + 可选页头挂载点 + 旧页渐进标记。
   约束：只做展示/导航，不执行写操作；依赖 nav.js 暴露的 window.__WB_NAV。
   新页（modern）保留自己的上下文条，标记 data-wb-shell=v1，避免重复面包屑；
   普通旧页自动注入「今日 / 域 / 页」面包屑 + 「旧版」标记，供 W4 逐步收编。 */
(function () {
  'use strict';
  if (window.WBShell) return;

  var MODERN = {
    '/': 1, '/home_v3.html': 1, '/paper.html': 1, '/paper': 1,
    '/picks.html': 1, '/picks': 1,
    '/dev_legion_v2.html': 1, '/academy/shuijing': 1, '/strategies': 1,
    '/os-entry': 1, '/zero_proto.html': 1
  };
  var SHELL_ATTR = 'data-wb-shell';

  function normPath(p) {
    return (p || '/').replace(/\/+$/, '') || '/';
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function navMatch() {
    var p = normPath(location.pathname);
    var out = { section: '', sectionHref: '/', item: '', href: p };
    var nav = window.__WB_NAV;
    if (!nav || !nav.sections || !nav.groups) return out;
    nav.sections.forEach(function (s) {
      (nav.groups[s[0]] || []).forEach(function (g) {
        (g[1] || []).forEach(function (it) {
          var href = String(it[1] || '').replace('http://127.0.0.1', '');
          if (normPath(href) === p) {
            out.section = s[1] || '';
            out.sectionHref = s[2] || '/';
            out.item = it[0] || '';
            out.href = it[1];
          }
        });
      });
    });
    return out;
  }

  function mount(opts) {
    var host = document.getElementById('wb-shell-root');
    if (!host) return false;
    opts = opts || {};
    var head = document.createElement('header');
    head.className = 'wb-shell-head';
    var h = document.createElement('h1');
    h.textContent = opts.title || document.title || '零界';
    head.appendChild(h);
    if (opts.subtitle) {
      var p = document.createElement('p');
      p.textContent = opts.subtitle;
      head.appendChild(p);
    }
    if (opts.actions && opts.actions.length) {
      var act = document.createElement('div');
      act.className = 'wb-shell-actions';
      opts.actions.forEach(function (a) {
        var link = document.createElement('a');
        link.href = a.href || '/';
        link.textContent = a.label || a.href;
        act.appendChild(link);
      });
      head.appendChild(act);
    }
    host.insertBefore(head, host.firstChild);
    return head;
  }

  function isAdopted() {
    var m = document.querySelector('meta[name=wb-shell]');
    return !!m && m.getAttribute('content') === 'adopted';
  }

  function adoptedHead(info) {
    if (document.querySelector('.wb-shell-head')) return;
    var head = document.createElement('header');
    head.className = 'wb-shell-head';
    var h = document.createElement('h1');
    h.textContent = (info && info.item) || (document.title || '零界').split('·')[0].trim();
    head.appendChild(h);
    var sub = document.createElement('p');
    sub.textContent = '已接入 PageShell v1 · 原页面功能保持，逐步迁移为统一壳';
    head.appendChild(sub);
    var act = document.createElement('div');
    act.className = 'wb-shell-actions';
    var a1 = document.createElement('a');
    a1.href = '/'; a1.textContent = '🏠 返回今日工作台';
    var a2 = document.createElement('a');
    a2.href = 'javascript:void(0)'; a2.textContent = '⌘K 命令';
    a2.addEventListener('click', function () {
      var trig = document.querySelector('.wb-palette-trigger');
      if (trig) trig.click();
    });
    act.appendChild(a1); act.appendChild(a2);
    head.appendChild(act);
    var crumbEl = document.querySelector('.wb-crumb');
    if (crumbEl && crumbEl.parentNode) crumbEl.parentNode.insertBefore(head, crumbEl.nextSibling);
    else document.body.insertBefore(head, document.body.firstChild);
  }

  function crumb() {
    var p = normPath(location.pathname);
    var modern = !!MODERN[p];
    var adopted = isAdopted();
    if (modern) document.body.setAttribute(SHELL_ATTR, 'v1');
    if (document.querySelector('.wb-crumb')) return null;
    var info = navMatch();
    if (!info.item && isAdopted()) {
      var parts = (document.title || '').split('·').map(function (x) { return x.trim(); }).filter(Boolean);
      var brands = { '零界': 1, '老田的个人 OS': 1 };
      var tt = '';
      for (var pi = 0; pi < parts.length; pi++) {
        if (!brands[parts[pi]]) { tt = parts[pi].split('｜')[0].trim(); break; }
      }
      if (!tt) tt = parts[0] || '';
      if (tt) info.item = tt;
    }
    var el = document.createElement('nav');
    el.className = 'wb-crumb';
    el.setAttribute('aria-label', '面包屑');
    var html = '<a href="/">🏠 今日</a>';
    if (info.section) {
      html += '<span class="wb-crumb-sep">›</span>' +
        '<a href="' + esc(info.sectionHref) + '">' + esc(info.section) + '</a>';
    }
    if (info.item) {
      html += '<span class="wb-crumb-sep">›</span>' +
        '<span class="wb-crumb-cur">' + esc(info.item) + '</span>';
    } else {
      html += '<span class="wb-crumb-sep">›</span>' +
        '<span class="wb-crumb-cur">旧版页面</span>';
    }
    if (!modern) html += '<span class="wb-crumb-chip ' + (adopted ? 'wb-adopted-chip' : 'wb-legacy-chip') + '">' + (adopted ? '已收编' : '旧版') + '</span>';
    el.innerHTML = html;
    var bar = document.querySelector('.wb-nav');
    if (bar && bar.parentNode) bar.parentNode.insertBefore(el, bar.nextSibling);
    else document.body.insertBefore(el, document.body.firstChild);
    if (!modern) {
      document.body.classList.add(adopted ? 'wb-adopted' : 'wb-legacy');
      document.body.setAttribute(SHELL_ATTR, adopted ? 'adopted' : 'legacy');
      if (adopted) adoptedHead(info);
    }
    return el;
  }

  function init() {
    // 新页也带 shell 标记，供后续 CSS/迁移统计识别
    if (MODERN[normPath(location.pathname)]) document.body.setAttribute(SHELL_ATTR, 'v1');
    crumb();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }

  window.WBShell = {
    version: '1.0',
    mount: mount,
    breadcrumb: crumb,
    isModern: function (p) { return !!MODERN[normPath(p || location.pathname)]; }
  };
})();
