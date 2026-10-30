/* 页面登录态软保护 / 权限提示（Phase A，只读）
 *
 * 用法：<script defer src="/static/auth_guard.js"></script>
 * - 拉取 /api/bff/identity/me；
 * - 在所有 opt-in 页面顶部显示登录态横幅（已登录身份/角色；未登录可去登录）；
 * - 默认不拦截浏览（软保护）；调用 AuthGuard.require() 可硬跳登录；
 * - 不写浏览器存储、不读 token、不发起写业务请求。
 */
(function () {
  'use strict';
  if (window.__AUTH_GUARD_READY__) return;
  window.__AUTH_GUARD_READY__ = true;

  var state = { status: 'checking', session: null, csrf: '', error: '' };
  var listeners = [];

  function nextPath() {
    if (location.pathname === '/login') return '/os-entry';
    return location.pathname + (location.search || '') + (location.hash || '');
  }

  function loginHref() {
    return '/login?next=' + encodeURIComponent(nextPath());
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function host() {
    var h = document.getElementById('auth-guard-banner');
    if (h) return h;
    h = el('div', 'auth-guard-banner');
    h.id = 'auth-guard-banner';
    h.setAttribute('role', 'status');
    h.setAttribute('aria-live', 'polite');
    h.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px;'
      + 'flex-wrap:wrap;margin:10px auto 0;padding:9px 14px;border:1px solid var(--os-line,#2a3242);'
      + 'border-radius:10px;background:var(--os-card,#151b24);color:var(--os-muted,#8a93a6);'
      + 'font-size:12px;max-width:1200px;';
    var shell = document.querySelector('.shell');
    if (shell && shell.firstChild) shell.insertBefore(h, shell.firstChild);
    else document.body.insertBefore(h, document.body.firstChild);
    return h;
  }

  function text() {
    if (state.status === 'checking') return '正在检查登录态…';
    if (state.status === 'authed' && state.session) {
      var c = (state.session.identity_context || {});
      var roles = (c.roles || []).join('/') || '—';
      return '已登录 · ' + (c.display_name || state.session.sub) + ' · ' + roles + ' · 只读';
    }
    if (state.status === 'error') return '登录服务不可用 · 游客只读 · ' + state.error;
    return '未登录 · 游客只读';
  }

  function render() {
    var h = host();
    h.replaceChildren();
    var left = el('span', null, text());
    h.append(left);
    var right = el('span');
    right.style.cssText = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap;';
    if (state.status !== 'authed') {
      var a = el('a', null, '去登录 →');
      a.href = loginHref();
      a.style.color = 'var(--os-blue,#4f8ef7)';
      right.append(a);
    } else {
      var ro = el('span', null, '只读会话');
      ro.style.color = 'var(--os-amber,#e6b450)';
      right.append(ro);
    }
    h.append(right);
    listeners.forEach(function (fn) { try { fn(state); } catch (e) { /* noop */ } });
  }

  function refresh() {
    state.status = 'checking';
    render();
    return fetch('/api/bff/identity/me', { cache: 'no-store' })
      .then(function (r) {
        if (r.status === 401) return null;
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        if (d && d.session) {
          state = { status: 'authed', session: d.session, csrf: d.csrf_token || '', error: '' };
        } else {
          state = { status: 'guest', session: null, csrf: '', error: '' };
        }
        render();
        maybeRedirect();
        return state;
      })
      .catch(function (e) {
        state = { status: 'error', session: null, csrf: '', error: String(e && e.message || e).slice(0, 80) };
        render();
        maybeRedirect();
        return state;
      });
  }

  function maybeRedirect() {
    if (state.status === 'authed') return;
    if (document.documentElement.getAttribute('data-auth-required') === '1') {
      location.replace(loginHref());
    }
  }

  window.AuthGuard = {
    getState: function () { return state; },
    refresh: refresh,
    on: function (fn) { if (typeof fn === 'function') listeners.push(fn); },
    require: function () {
      if (state.status === 'authed') return true;
      location.href = loginHref();
      return false;
    },
    loginHref: loginHref
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', refresh, { once: true });
  else refresh();
})();
