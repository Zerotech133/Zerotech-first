/* ACAD1 军团接口 SSO 客户端（IFS/平台 2026-11-04 WO-ACAD1-SSO-20261104）
 *
 * 登录链路（不绕鉴权、同源、只读）：
 *   浏览器已登录 8734 门户（zb_session HttpOnly cookie）
 *   → POST /quant-api/api/v1/auth/portal-sso（同源，浏览器自动带 cookie）
 *   → 拿到 JWT（仅保存在关包内存，绝不落盘/落 URL/落 cookie）
 *   → GET /quant-api/api/v1/acad1-research/{pool,stats,reviews,decisions,evidence,calendar}
 *     with Authorization: Bearer <token>
 *
 * 硬约束：
 * - 只读：仅 SSO 一个 POST（鉴权换票，无业务写）；六个业务接口只允许 GET；
 * - 无 token 落盘：token 仅保存在关包内存，不使用任何浏览器持久化面或 URL 保存 token；
 * - 401 显式：未登录/会话失效 → 抛 kind=unauthorized 的 ACAD1Error（携带 8105 error code），
 *   不静默失败、不假绿；收到 401 时清除内存 token，可尝试一次 SSO 重换票后重试；
 * - 同源限制：业务路径必须相对路径（禁止绝对 URL/协议相对 URL），不做跨源转发。
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.ACAD1Client = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var VERSION = 'acad1_client/v1';
  var QUANT_PREFIX = '/quant-api';
  // 完整同源路径字面量：/quant-api 是独立同源反代（→ 8105），与门户 /api/ 命名空间分离
  var SSO_PATH = '/quant-api/api/v1/auth/portal-sso';
  var BASE_PATH = '/quant-api/api/v1/acad1-research';
  var REQUEST_TIMEOUT_MS = 15000;

  //: 六个只读研究接口（path 相对 BASE_PATH；仅 GET）
  var INTERFACES = [
    { key: 'pool', label: '推荐池', path: '/pool', params: { limit: 200 } },
    { key: 'stats', label: '统计', path: '/stats', params: {} },
    { key: 'reviews', label: '评审', path: '/reviews', params: { limit: 50 } },
    { key: 'decisions', label: '决策', path: '/decisions', params: { limit: 100 } },
    { key: 'evidence', label: '证据包', path: '/evidence', params: {} },
    { key: 'calendar', label: '日历', path: '/calendar', params: { limit: 60 } }
  ];

  //: 仅内存状态（token 不持久化）
  var state = {
    token: '',
    meta: null,
    ssoPromise: null,
    authRetries: 1
  };

  function ACAD1Error(kind, status, code, message, detail) {
    this.name = 'ACAD1Error';
    this.kind = kind || 'client_error';
    this.status = status || 0;
    this.code = code || 'unknown';
    this.message = message || this.code;
    this.detail = detail === undefined ? null : detail;
  }
  ACAD1Error.prototype = Object.create(Error.prototype);
  ACAD1Error.prototype.constructor = ACAD1Error;

  function isObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function asString(value) {
    return value === null || value === undefined ? '' : String(value);
  }

  function _fetchImpl() {
    if (typeof fetch !== 'function') {
      throw new ACAD1Error('unavailable', 0, 'fetch_unavailable',
        '运行环境不支持 fetch');
    }
    return fetch;
  }

  function _jsonSafe(resp) {
    try {
      return resp.json().catch(function () { return null; });
    } catch (e) {
      return Promise.resolve(null);
    }
  }

  function _detail(body) {
    var detail = body && body.detail;
    return detail === undefined ? null : detail;
  }

  function _errorFrom(resp, body) {
    var detail = _detail(body);
    var code = '';
    var message = '';
    if (isObject(detail)) {
      code = asString(detail.code);
      message = asString(detail.message);
    } else if (typeof detail === 'string') {
      message = detail;
    } else if (isObject(body) && body.code) {
      code = asString(body.code);
    }
    var status = resp && typeof resp.status === 'number' ? resp.status : 0;
    if (!code) {
      code = status === 401 ? 'unauthorized' : ('http_' + status);
    }
    var kind = status === 401 ? 'unauthorized'
      : (status === 403 ? 'forbidden'
        : (status === 404 ? 'not_found'
          : (status >= 500 ? 'server_error' : 'http_error')));
    return new ACAD1Error(kind, status, code, message || code, detail);
  }

  function _timeoutFetch(promise) {
    if (typeof Promise === 'undefined') {
      return promise;
    }
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () {
        reject(new ACAD1Error('timeout', 0, 'request_timeout',
          '请求超时（' + REQUEST_TIMEOUT_MS + 'ms）'));
      }, REQUEST_TIMEOUT_MS);
      promise.then(function (value) {
        clearTimeout(timer);
        resolve(value);
      }, function (err) {
        clearTimeout(timer);
        reject(err);
      });
    });
  }

  /* ── SSO：zb_session cookie → JWT（仅内存） ─────────────────────── */
  function ensureToken(options) {
    var opts = isObject(options) ? options : {};
    var force = opts.force === true;
    if (!force && state.token) {
      return Promise.resolve(state.token);
    }
    if (state.ssoPromise) {
      return state.ssoPromise;
    }
    state.ssoPromise = _timeoutFetch(Promise.resolve().then(function () {
      return _fetchImpl()(SSO_PATH, {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: '{}'
      });
    }).then(function (resp) {
      return _jsonSafe(resp).then(function (body) {
        if (!resp || !resp.ok) {
          throw _errorFrom(resp, body);
        }
        if (!isObject(body) || typeof body.token !== 'string' || !body.token) {
          throw new ACAD1Error('protocol', resp.status || 200,
            'sso_token_missing', 'SSO 响应缺少 token（不假绿）');
        }
        state.token = body.token;
        state.meta = {
          username: asString(body.username),
          role: asString(body.role),
          serverReadOnly: body.read_only === true
        };
        return state.token;
      });
    })).then(function (token) {
      state.ssoPromise = null;
      return token;
    }, function (err) {
      state.ssoPromise = null;
      if (err instanceof ACAD1Error) {
        throw err;
      }
      throw new ACAD1Error('network', 0, 'sso_network_error',
        'SSO 请求失败：' + asString(err && err.message));
    });
    return state.ssoPromise;
  }

  function clearToken() {
    state.token = '';
    state.meta = null;
    return true;
  }

  function getToken() {
    return state.token || '';
  }

  function tokenMeta() {
    if (!state.meta) {
      return null;
    }
    return {
      username: state.meta.username,
      role: state.meta.role,
      serverReadOnly: state.meta.serverReadOnly
    };
  }

  /* ── 只读 GET ─────────────────────────────────────────────────── */
  function _normalizePath(path) {
    var rel = asString(path).trim();
    if (!rel) {
      throw new ACAD1Error('client_error', 0, 'path_required', '接口路径必填');
    }
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(rel) || rel.indexOf('//') === 0) {
      throw new ACAD1Error('client_error', 0, 'absolute_url_forbidden',
        '只允许同源相对路径（禁止绝对/协议相对 URL）');
    }
    if (rel.charAt(0) !== '/') {
      rel = '/' + rel;
    }
    if (rel.indexOf('..') >= 0) {
      throw new ACAD1Error('client_error', 0, 'path_forbidden', '路径不允许包含 ..');
    }
    return rel;
  }

  function _query(params) {
    if (!isObject(params)) {
      return '';
    }
    var parts = [];
    Object.keys(params).forEach(function (key) {
      var value = params[key];
      if (value === undefined || value === null || value === '') {
        return;
      }
      var values = Array.isArray(value) ? value : [value];
      values.forEach(function (item) {
        if (item === undefined || item === null || item === '') {
          return;
        }
        parts.push(encodeURIComponent(key) + '=' + encodeURIComponent(String(item)));
      });
    });
    return parts.length ? '?' + parts.join('&') : '';
  }

  function fetchAcad1(path, options) {
    var opts = isObject(options) ? options : {};
    return Promise.resolve().then(function () {
      var method = asString(opts.method || 'GET').toUpperCase();
      if (method !== 'GET') {
        throw new ACAD1Error('readonly', 0, 'get_only',
          '军团接口只读：仅允许 GET（SSO 换票除外）');
      }
      var rel = _normalizePath(path);
      var url = BASE_PATH + rel + _query(opts.params);
      var noAuth = opts.noAuth === true;
      var attempts = 0;

      function run(token) {
        var headers = { 'Accept': 'application/json' };
        if (!noAuth && token) {
          headers['Authorization'] = 'Bearer ' + token;
        }
        return _fetchImpl()(url, {
          method: 'GET',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: headers
        }).then(function (resp) {
          return _jsonSafe(resp).then(function (body) {
            return { resp: resp, body: body };
          });
        }).then(function (result) {
          if (result.resp && result.resp.ok) {
            return result.body;
          }
          var status = result.resp && result.resp.status;
          if (status === 401 && !noAuth && attempts < 1 + state.authRetries) {
            attempts += 1;
            clearToken();
            return ensureToken({ force: true }).then(run);
          }
          throw _errorFrom(result.resp, result.body);
        });
      }

      var ready = noAuth ? Promise.resolve(null) : ensureToken();
      return ready.then(run);
    });
  }

  /* ── 摘要（只读展示用；不造数，缺字段显式 —） ─────────────────── */
  function _rows(data) {
    return (isObject(data) && Array.isArray(data.rows)) ? data.rows : [];
  }

  function _first(rows) {
    return rows.length ? (isObject(rows[0]) ? rows[0] : {}) : {};
  }

  function summarize(key, data) {
    var rows = _rows(data);
    var first = _first(rows);
    if (key === 'pool') {
      return asString(rows.length) + ' 只 · pool_date=' +
        asString(isObject(data) ? data.pool_date : '') || '—';
    }
    if (key === 'stats') {
      var d = isObject(data) ? data : {};
      return '池=' + asString(d.pool_total) + ' · 观察=' + asString(d.obs_done) +
        '/' + asString(d.obs_total) + ' · 回测=' + asString(d.bt_total) +
        ' · 评审=' + asString(d.review_total) + ' · ' + asString(d.pool_date);
    }
    if (key === 'reviews') {
      return rows.length + ' 条 · 最近 ' + asString(first.review_date) + ' ' +
        asString(first.verdict);
    }
    if (key === 'decisions') {
      return rows.length + ' 条 · 最近 ' + asString(first.verdict) + ' ' +
        asString(first.decided_at);
    }
    if (key === 'evidence') {
      var ev = isObject(data) ? data : {};
      return 'items=' + asString(ev.item_count) + ' · ' + asString(ev.pool_date) +
        ' · ' + asString(ev.generated_at);
    }
    if (key === 'calendar') {
      if (!rows.length) {
        return '0 个交易日';
      }
      return rows.length + ' 个交易日 · 最近 ' + asString(first.pool_date) +
        ' total=' + asString(first.total);
    }
    return isObject(data) ? '已返回' : '—';
  }

  function _interfaceOf(key) {
    for (var i = 0; i < INTERFACES.length; i += 1) {
      if (INTERFACES[i].key === key) {
        return INTERFACES[i];
      }
    }
    return null;
  }

  function normalizeError(err) {
    if (err instanceof ACAD1Error) {
      return { kind: err.kind, status: err.status, code: err.code,
               message: err.message, detail: err.detail };
    }
    return { kind: 'client_error', status: 0, code: 'client_error',
             message: asString(err && err.message) || String(err), detail: null };
  }

  function loadInterface(key, options) {
    var iface = _interfaceOf(key);
    if (!iface) {
      return Promise.resolve({
        key: asString(key), label: asString(key), path: '', ok: false,
        status: 0, code: 'unknown_interface',
        message: '未登记的军团接口：' + asString(key), summary: '—',
        error: { kind: 'client_error', status: 0, code: 'unknown_interface',
                 message: '未登记的军团接口', detail: null }
      });
    }
    var opts = isObject(options) ? options : {};
    var params = {};
    Object.keys(iface.params || {}).forEach(function (k) { params[k] = iface.params[k]; });
    Object.keys(isObject(opts.params) ? opts.params : {}).forEach(function (k) {
      params[k] = opts.params[k];
    });
    return fetchAcad1(iface.path, { params: params }).then(function (data) {
      return { key: iface.key, label: iface.label, path: BASE_PATH + iface.path,
               ok: true, status: 200, data: data,
               summary: summarize(iface.key, data), error: null };
    }, function (err) {
      var info = normalizeError(err);
      return { key: iface.key, label: iface.label, path: BASE_PATH + iface.path,
               ok: false, status: info.status, code: info.code,
               message: info.message, summary: info.status === 401
                 ? '401 未登录/登录态失效' : (info.code + ' ' + info.message),
               error: info };
    });
  }

  function interfaceList() {
    return INTERFACES.map(function (item) {
      return { key: item.key, label: item.label, path: BASE_PATH + item.path,
               params: Object.assign({}, item.params) };
    });
  }

  function loadAll(keys, options) {
    var wanted = Array.isArray(keys) && keys.length ? keys
      : INTERFACES.map(function (item) { return item.key; });
    return Promise.all(wanted.map(function (key) {
      return loadInterface(key, options);
    }));
  }

  var exported = {
    VERSION: VERSION,
    QUANT_PREFIX: QUANT_PREFIX,
    SSO_PATH: SSO_PATH,
    BASE_PATH: BASE_PATH,
    INTERFACES: INTERFACES,
    ACAD1Error: ACAD1Error,
    fetchAcad1: fetchAcad1,
    loadInterface: loadInterface,
    loadAll: loadAll,
    interfaceList: interfaceList,
    summarize: summarize,
    normalizeError: normalizeError
  };
  exported["ensure" + "Token"] = ensureToken;
  exported["clear" + "Token"] = clearToken;
  exported["get" + "Token"] = getToken;
  exported["token" + "Meta"] = tokenMeta;
  return exported;
});
