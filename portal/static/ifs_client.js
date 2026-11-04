/* IFS envelope 前端客户端（IFS v1 F1c / WO-IFS-ENVELOPE-UI-20261104）
 *
 * 职责（只读、零依赖、无网络写操作）：
 *   1) unwrapEnvelope(payload)：优先读 envelope 的 data；旧格式（无 data）回退旧顶层字段；
 *   2) warnings：读取/格式化/渲染（只读提示，不改变业务数据）；
 *   3) request_id / schema_version / source（含 training 页 source 字符串碰撞的 source_detail）；
 *   4) 错误归一：优先 error_detail{code,message,retryable,details}，兼容旧 error 字符串；
 *   5) renderMeta / renderWarnings：把 envelope 元信息以只读文本/列表渲染到指定元素。
 *
 * 兼容期（F1b 保留旧顶层字段）：是否 envelope 通过 data 对象 / request_id / warnings /
 * source.interface_id 判定；旧格式调用方无需改动即可继续工作。
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.IFSClient = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var VERSION = 'ifs_client/v1';
  var REQUEST_ID_RE = /^req-[0-9a-f]{32}$/;
  var LEGACY_FALLBACK_TEXT = '旧格式响应（无 IFS envelope，按旧顶层字段兼容读取）';

  function isObject(v) {
    return v !== null && typeof v === 'object' && !Array.isArray(v);
  }

  function has(obj, key) {
    return !!obj && Object.prototype.hasOwnProperty.call(obj, key);
  }

  function asString(v) {
    if (v === null || v === undefined) {
      return '';
    }
    return typeof v === 'string' ? v : String(v);
  }

  function esc(s) {
    return asString(s).replace(/[&<>"']/g, function (c) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[c];
    });
  }

  /* ── 错误归一（error_detail 优先，旧 error 字符串兼容） ── */
  function errorInfo(payload) {
    var raw = isObject(payload) ? payload : {};
    var detail = isObject(raw.error_detail) ? raw.error_detail : null;
    if (detail) {
      return {
        code: asString(detail.code) || 'unknown_error',
        message: asString(detail.message) || asString(raw.message) ||
          asString(detail.code) || 'unknown_error',
        retryable: detail.retryable === true,
        details: isObject(detail.details) ? detail.details : null,
        legacyCode: isObject(detail.details) ? asString(detail.details.legacy_code) : '',
        httpStatus: (isObject(detail.details) && typeof detail.details.http_status === 'number')
          ? detail.details.http_status : null,
        source: 'error_detail'
      };
    }
    if (raw.ok === false) {
      var legacy = isObject(raw.error) ? raw.error : null;
      var code = legacy ? asString(legacy.code) : asString(raw.error);
      var message = asString(raw.message) ||
        (legacy ? asString(legacy.message) : '') || code || 'unknown_error';
      return {
        code: code || 'unknown_error',
        message: message,
        retryable: !!(legacy && legacy.retryable === true),
        details: legacy ? (isObject(legacy.details) ? legacy.details : null) : null,
        legacyCode: code || '',
        httpStatus: null,
        source: 'legacy_error'
      };
    }
    return null;
  }

  function errorText(payload) {
    var info = errorInfo(payload);
    if (!info) {
      return '';
    }
    var text = info.code;
    if (info.message && info.message !== info.code) {
      text += '：' + info.message;
    }
    if (info.retryable) {
      text += '（可重试）';
    }
    return text;
  }

  /* ── warnings / request_id / source ── */
  function warningsOf(payload) {
    var raw = isObject(payload) ? payload : {};
    return Array.isArray(raw.warnings) ? raw.warnings.slice() : [];
  }

  function requestIdOf(payload) {
    var raw = isObject(payload) ? payload : {};
    return asString(raw.request_id);
  }

  function sourceOf(payload) {
    var raw = isObject(payload) ? payload : {};
    if (isObject(raw.source)) {
      return raw.source;
    }
    if (isObject(raw.source_detail)) {
      return raw.source_detail;
    }
    return null;
  }

  /* ── 核心：解包（data 优先，旧字段 fallback） ── */
  function unwrapEnvelope(payload) {
    var raw = isObject(payload) ? payload : {};
    var src = sourceOf(raw);
    var isEnvelope = isObject(raw.data) || has(raw, 'request_id') ||
      has(raw, 'warnings') || !!(src && src.interface_id);
    var data = isObject(raw.data) ? raw.data : raw;
    var error = errorInfo(raw);
    var ok;
    if (has(raw, 'ok')) {
      ok = raw.ok === true;
    } else if (isObject(raw.data) && has(raw.data, 'ok')) {
      ok = raw.data.ok === true;
    } else {
      ok = error === null;
    }
    return {
      version: VERSION,
      isEnvelope: isEnvelope,
      hasData: isObject(raw.data),
      ok: ok,
      data: data,
      raw: raw,
      warnings: warningsOf(raw),
      requestId: requestIdOf(raw),
      schemaVersion: asString(raw.schema_version),
      source: src,
      sourceMode: src ? asString(src.source_mode) : '',
      interfaceId: src ? asString(src.interface_id) : '',
      stale: !!(src && src.stale === true),
      error: error
    };
  }

  function formatWarning(item) {
    if (!isObject(item)) {
      return asString(item);
    }
    var code = asString(item.code) || 'warning';
    var message = asString(item.message);
    return message ? '[' + code + '] ' + message : '[' + code + ']';
  }

  /* ── DOM 渲染（只读文本/列表，安全转义） ── */
  function _asEnv(payload) {
    if (isObject(payload) && payload.version === VERSION) {
      return payload;
    }
    return unwrapEnvelope(payload);
  }

  function renderMeta(el, payload, opts) {
    if (!el) {
      return '';
    }
    var env = _asEnv(payload);
    var options = isObject(opts) ? opts : {};
    var text;
    if (!env.isEnvelope) {
      text = LEGACY_FALLBACK_TEXT;
    } else {
      var bits = [];
      if (env.interfaceId) {
        bits.push('interface=' + env.interfaceId);
      }
      if (env.schemaVersion) {
        bits.push('schema=' + env.schemaVersion);
      }
      if (env.requestId) {
        bits.push('request_id=' + env.requestId);
      }
      if (env.sourceMode) {
        bits.push('source_mode=' + env.sourceMode);
      }
      bits.push('warnings=' + env.warnings.length);
      if (env.stale) {
        bits.push('stale=true');
      }
      if (env.error) {
        bits.push('error=' + env.error.code);
      }
      text = (options.prefix === undefined ? 'IFS envelope · ' : asString(options.prefix)) +
        bits.join(' · ');
    }
    el.textContent = text;
    if (el.setAttribute) {
      el.setAttribute('data-envelope', env.isEnvelope ? 'envelope' : 'legacy');
    }
    return text;
  }

  function renderWarnings(el, payload, opts) {
    if (!el) {
      return 0;
    }
    var env = _asEnv(payload);
    var list = env.warnings || [];
    var options = isObject(opts) ? opts : {};
    var title = asString(options.title) || 'warnings';
    if (!list.length) {
      el.innerHTML = '';
      if (el.style) {
        el.style.display = 'none';
      }
      if (el.setAttribute) {
        el.setAttribute('data-warnings', '0');
      }
      return 0;
    }
    el.innerHTML = '<b>' + esc(title) + '（' + list.length + '）</b><ul>' +
      list.map(function (item) {
        return '<li>' + esc(formatWarning(item)) + '</li>';
      }).join('') + '</ul>';
    if (el.style) {
      el.style.display = 'block';
    }
    if (el.setAttribute) {
      el.setAttribute('data-warnings', String(list.length));
    }
    return list.length;
  }

  /* ── 只读 fetch 包装（GET/既定请求语义不变；不发起任何写操作） ── */
  function fetchJson(url, options) {
    var opts = isObject(options) ? options : {};
    if (!has(opts, 'cache')) {
      opts.cache = 'no-store';
    }
    return fetch(url, opts).then(function (resp) {
      return resp.json().catch(function () {
        return {};
      }).then(function (body) {
        var payload = isObject(body) ? body : {};
        return {
          ok: resp.ok,
          status: resp.status,
          payload: payload,
          env: unwrapEnvelope(payload)
        };
      });
    });
  }

  return {
    VERSION: VERSION,
    LEGACY_FALLBACK_TEXT: LEGACY_FALLBACK_TEXT,
    REQUEST_ID_RE: REQUEST_ID_RE,
    isObject: isObject,
    esc: esc,
    unwrapEnvelope: unwrapEnvelope,
    warningsOf: warningsOf,
    requestIdOf: requestIdOf,
    sourceOf: sourceOf,
    errorInfo: errorInfo,
    errorText: errorText,
    formatWarning: formatWarning,
    renderMeta: renderMeta,
    renderWarnings: renderWarnings,
    fetchJson: fetchJson
  };
});
