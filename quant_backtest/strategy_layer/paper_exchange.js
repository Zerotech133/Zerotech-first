/*!
 * Phase 4-C Paper Exchange 只读页面卡片 —— 健康卡 / 账户目录 / 账户快照 / 路由详情
 * 工单：WO-PAPER-EXCHANGE-UI-20261031（零外部依赖、离线优先、只读）
 *
 * 职责边界（硬约束）：
 *  - 只读展示：仅 4 个只读 GET 端点 /paper_exchange/{health,route,accounts,snapshot}
 *    （由 agent_platform/paper/exchange_gateway.py 提供，与页面同一 HTTP 层）；
 *  - 不写服务端、不调 POST/PUT/PATCH/DELETE、不上传/不下载、不做本地存储、
 *    不下单、不执行、不启动撮合、不迁移两账、不合并两账统计；
 *  - 空/缺失/不可用一律显式显示（unavailable / null / 端点未接入 / 网络失败），
 *    绝不补零、不猜归属、不造数；
 *  - 不引用外部 CDN/绝对 URL/写死端口；页面路由由 dashboard_api 后续接入（本单不改它）。
 *
 * 结构：
 *  1) 常量与只读契约（API 端点 / 状态文案 / 只读边界）；
 *  2) 纯函数（toQuery / parseJsonSafe / statusText / summarize* / describeHttpError …，
 *     浏览器与 Node 均可直调；Node 下 module.exports，供 pytest 做纯函数契约测试）；
 *  3) DOM 层：健康卡、账户目录、快照表单与表格、路由详情；fetch 只有一处（apiGet）。
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api; /* Node：供 pytest 纯函数契约测试 require */
  } else if (root) {
    root.PaperExchangeUI = api; /* 浏览器：全局命名空间 */
  }
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        api.boot();
      });
    } else {
      api.boot();
    }
  }
})(
  typeof globalThis !== "undefined"
    ? globalThis
    : typeof window !== "undefined"
      ? window
      : this,
  function () {
    "use strict";

    /* ── 1. 常量：只读契约（与 exchange_gateway.PATH_* 同口径）────────── */
    var API = {
      health: "/paper_exchange/health",
      route: "/paper_exchange/route",
      accounts: "/paper_exchange/accounts",
      snapshot: "/paper_exchange/snapshot"
    };

    /* 两本账固定顺序：official 在前、legacy 在后（两账不合并统计）。 */
    var BOOK_ORDER = ["official", "legacy"];

    /* 只读边界徽标（页面/JS 双处显式声明）。 */
    var BOUNDARIES = ["只读", "不下单", "不迁移两账"];

    /* 写入面边界键：来自 gateway 信封 boundaries，全部应为 false。 */
    var WRITE_BOUNDARY_KEYS = [
      "ledger_write",
      "disk_write",
      "place_order",
      "start_matching",
      "migrate_ledger",
      "new_port",
      "start_service"
    ];

    var STATUS_TEXT = {
      ok: "正常",
      degraded: "降级（部分数据缺失）",
      empty: "空账（合法空态）",
      unavailable: "不可用"
    };

    var TEXT = {
      readonly: "只读",
      noOrder: "不下单",
      noMigrate: "不迁移两账",
      emptyAccounts: "暂无账户：accounts 返回空列表（不造数）。",
      emptySnapshot: "尚无快照数据（空态显式显示，不造数）。",
      emptyPositions: "持仓为空（合法空态，不造数）。",
      endpointMissing: "端点未接入：HTTP 404（页面路由/只读端点尚未挂载）",
      networkFail: "接口不可用：网络失败或页面以 file:// 打开（离线预览仅展示空态，不造数）",
      nonJson: "响应非 JSON：显式按不可用处理（不造数）",
      tenantUnverified: "该账户 tenant 归属未落库：请确认/填写 tenant（未验证，不猜归属）后读取快照。",
      tenantInputMissing: "请先填写 tenant（未验证）或从账户目录选择账户。",
      noSnapshotYet: "尚未读取快照：从账户目录选择，或填 tenant/account 后点“读取快照”。",
      noRouteYet: "尚未读取路由：点击 official 或 legacy 账本卡片。",
      loading: "读取中…",
      attrMissing: "归属信息缺失（null，不猜）"
    };

    /* ── 2. 纯函数（Node 直测；不触碰 DOM / 网络）────────────────────── */
    function apiEndpoint(name) {
      return Object.prototype.hasOwnProperty.call(API, name) ? API[name] : "";
    }

    function apiEndpoints() {
      return [API.health, API.route, API.accounts, API.snapshot];
    }

    function boundaryChips() {
      return BOUNDARIES.slice();
    }

    function writeBoundaryKeys() {
      return WRITE_BOUNDARY_KEYS.slice();
    }

    /* null/undefined/空串 → fallback（缺失显式化，不造数）。 */
    function display(value, fallback) {
      if (value === null || value === undefined || value === "") {
        return fallback === undefined ? "—" : fallback;
      }
      if (Array.isArray(value)) {
        return value.length ? value.join("、") : (fallback === undefined ? "—" : fallback);
      }
      return String(value);
    }

    /* 稳定 query 串（键排序 + encodeURIComponent；空值跳过）。 */
    function toQuery(params) {
      if (!params || typeof params !== "object") {
        return "";
      }
      var keys = Object.keys(params).sort();
      var parts = [];
      for (var i = 0; i < keys.length; i += 1) {
        var key = keys[i];
        var value = params[key];
        if (value === null || value === undefined || value === "") {
          continue;
        }
        parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(String(value)));
      }
      return parts.length ? "?" + parts.join("&") : "";
    }

    function parseJsonSafe(text) {
      if (typeof text !== "string" || !text.trim()) {
        return null;
      }
      try {
        var parsed = JSON.parse(text);
        return parsed && typeof parsed === "object" ? parsed : null;
      } catch (error) {
        return null;
      }
    }

    function statusText(status) {
      if (status === null || status === undefined || status === "") {
        return "未知状态";
      }
      return STATUS_TEXT[status] || String(status);
    }

    function escapeHtml(value) {
      return String(display(value, ""))
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    /* 快照查询参数：只带 book/tenant/account/combo（绝不带写操作字段）。 */
    function buildSnapshotParams(input) {
      var params = {};
      var source = input && typeof input === "object" ? input : {};
      if (source.book) {
        params.book = String(source.book).trim();
      }
      if (source.tenant) {
        params.tenant = String(source.tenant).trim();
      }
      if (source.account) {
        params.account = String(source.account).trim();
      }
      if (source.combo) {
        params.combo = String(source.combo).trim();
      }
      return params;
    }

    function buildSnapshotQuery(input) {
      return toQuery(buildSnapshotParams(input));
    }

    /* HTTP/网络错误 → 显式可读文案（404=端点未接入；0=网络/离线）。 */
    function describeHttpError(httpStatus, doc) {
      if (httpStatus === 0) {
        return TEXT.networkFail;
      }
      if (httpStatus === 404) {
        return TEXT.endpointMissing;
      }
      var error = doc && typeof doc === "object" && doc.error && typeof doc.error === "object"
        ? doc.error
        : {};
      var code = error.code ? String(error.code) : "";
      var message = error.message ? String(error.message) : "";
      if (httpStatus === 503) {
        return "接口暂不可用：HTTP 503" + (message ? "（" + message + "）" : "");
      }
      if (httpStatus >= 400) {
        return "请求失败：HTTP " + httpStatus +
          (code ? " · " + code : "") +
          (message ? " · " + message : "");
      }
      return "";
    }

    /* 归属说明：两账存储层均无 tenant 维度 → 归属 null（不猜）。 */
    function attributionNote(attribution) {
      if (!attribution || typeof attribution !== "object") {
        return TEXT.attrMissing;
      }
      var parts = [];
      if (attribution.scope) {
        parts.push("归属口径 " + attribution.scope);
      }
      if (attribution.owner_tenant_id === null || attribution.owner_tenant_id === undefined) {
        parts.push("tenant 归属 null（不猜）");
      } else {
        parts.push("tenant " + attribution.owner_tenant_id);
      }
      if (attribution.storage_tenant_dimension === false) {
        parts.push("存储层无 tenant 维度");
      }
      if (attribution.registry_verified_tenant_id) {
        parts.push("registry 已验证 " + attribution.registry_verified_tenant_id);
      }
      if (attribution.note) {
        parts.push(String(attribution.note));
      }
      return parts.length ? parts.join(" · ") : TEXT.attrMissing;
    }

    /* 信封 → health 摘要（兼容 gateway 信封与裸 data 两种入参）。 */
    function summarizeHealth(doc) {
      var data = doc && typeof doc === "object" && doc.data && typeof doc.data === "object"
        ? doc.data
        : doc;
      if (!data || typeof data !== "object" || !data.books) {
        return null;
      }
      var books = [];
      for (var i = 0; i < BOOK_ORDER.length; i += 1) {
        var name = BOOK_ORDER[i];
        var book = data.books[name];
        if (!book || typeof book !== "object") {
          continue;
        }
        books.push({
          book: name,
          backend: display(book.backend, ""),
          status: display(book.status, "unavailable"),
          statusLabel: statusText(book.status),
          exists: book.exists === true,
          reasons: Array.isArray(book.reasons) ? book.reasons.slice() : []
        });
      }
      return {
        ok: data.ok === true,
        status: display(data.status, "unavailable"),
        statusLabel: statusText(data.status),
        books: books,
        readonly: data.readonly === true,
        boundaries: data.boundaries && typeof data.boundaries === "object"
          ? data.boundaries
          : {}
      };
    }

    /* 信封 → accounts 摘要（两本账分列，保留 unavailable/null 语义）。 */
    function summarizeAccounts(doc) {
      var data = doc && typeof doc === "object" && doc.data && typeof doc.data === "object"
        ? doc.data
        : doc;
      if (!data || typeof data !== "object" || !data.books) {
        return null;
      }
      var books = [];
      var total = 0;
      for (var i = 0; i < BOOK_ORDER.length; i += 1) {
        var name = BOOK_ORDER[i];
        var book = data.books[name];
        if (!book || typeof book !== "object") {
          continue;
        }
        var entries = Array.isArray(book.entries) ? book.entries : [];
        var rows = [];
        for (var j = 0; j < entries.length; j += 1) {
          var entry = entries[j] || {};
          rows.push({
            book: name,
            source: display(entry.source, "storage"),
            account_id: display(entry.account_id, ""),
            tenant_id: entry.tenant_id === null || entry.tenant_id === undefined
              ? null
              : String(entry.tenant_id),
            namespace: entry.namespace === null || entry.namespace === undefined
              ? null
              : String(entry.namespace),
            addressable: entry.addressable === true,
            reason: display(entry.reason, ""),
            meta: entry.meta && typeof entry.meta === "object" ? entry.meta : {}
          });
          total += 1;
        }
        books.push({
          book: name,
          available: book.available === true,
          attribution: display(book.attribution, ""),
          storageNote: display(book.storage_note, ""),
          storageAccounts: Array.isArray(book.storage_accounts) ? book.storage_accounts.length : null,
          unavailable: book.unavailable && typeof book.unavailable === "object"
            ? book.unavailable
            : null,
          entries: rows
        });
      }
      return {
        count: typeof data.count === "number" ? data.count : total,
        books: books,
        readonly: data.readonly === true
      };
    }

    /* 快照 section → 统一持仓视图（official 记权重、legacy 记股数，缺失即 null）。 */
    function positionsOf(data) {
      if (!data || typeof data !== "object") {
        return [];
      }
      if (data.official && data.official.state &&
          Array.isArray(data.official.state.positions)) {
        return data.official.state.positions.map(function (item) {
          return {
            symbol: display(item.symbol, ""),
            weight: item.weight === undefined ? null : item.weight,
            qty: null,
            entry_price: item.entry_price === undefined ? null : item.entry_price,
            last_price: item.last_price === undefined ? null : item.last_price,
            industry: display(item.industry, "")
          };
        });
      }
      if (data.legacy && Array.isArray(data.legacy.positions)) {
        return data.legacy.positions.map(function (item) {
          return {
            symbol: display(item.stock, ""),
            weight: null,
            qty: item.qty === undefined ? null : item.qty,
            entry_price: item.avg_cost === undefined ? null : item.avg_cost,
            last_price: null,
            industry: display(item.strategy, "")
          };
        });
      }
      return [];
    }

    /* 信封 → snapshot 摘要（status/namespace/持仓/不可用原因，缺失显式 null）。 */
    function summarizeSnapshot(doc) {
      var data = doc && typeof doc === "object" && doc.data && typeof doc.data === "object"
        ? doc.data
        : doc;
      if (!data || typeof data !== "object" || !data.book) {
        return null;
      }
      var section = data.book === "official" ? data.official : data.legacy;
      var rows = null;
      var lastDate = null;
      if (data.book === "official" && section) {
        rows = typeof section.rows === "number" ? section.rows : null;
        lastDate = section.last_date === undefined ? null : section.last_date;
      } else if (data.book === "legacy" && section) {
        rows = section.ledger && typeof section.ledger.rows === "number"
          ? section.ledger.rows
          : null;
        lastDate = section.equity ? display(section.equity.date, null) : null;
      }
      return {
        status: display(data.status, "unavailable"),
        statusLabel: statusText(data.status),
        book: data.book,
        namespace: data.namespace === undefined ? null : data.namespace,
        accountId: data.account ? display(data.account.account_id, null) : null,
        tenantId: data.tenant_id === undefined ? null : data.tenant_id,
        backend: display(data.backend, ""),
        rows: rows,
        lastDate: lastDate,
        positions: positionsOf(data),
        unavailable: data.unavailable && typeof data.unavailable === "object"
          ? data.unavailable
          : null,
        warnings: Array.isArray(data.warnings) ? data.warnings.slice() : [],
        attributionNote: attributionNote(data.attribution)
      };
    }

    /* ── 3. DOM 层（浏览器；Node 下不执行）──────────────────────────── */
    function $(id) {
      return document.getElementById(id);
    }

    function setStatus(id, text, tone) {
      var node = $(id);
      if (!node) {
        return;
      }
      node.textContent = display(text, "");
      node.className = "status" + (tone ? " " + tone : "");
    }

    function showEmpty(id, text, tone) {
      var node = $(id);
      if (!node) {
        return;
      }
      node.textContent = display(text, "");
      node.className = "empty" + (tone ? " " + tone : "");
      node.classList.remove("hidden");
    }

    function hideEmpty(id) {
      var node = $(id);
      if (node) {
        node.classList.add("hidden");
      }
    }

    function setSourceBadge(text, tone) {
      var node = $("sourceBadge");
      if (!node) {
        return;
      }
      node.textContent = "数据源：" + display(text, "未知");
      node.className = "badge" + (tone ? " " + tone : "");
    }

    function toneOfStatus(status) {
      if (status === "ok") {
        return "ok";
      }
      if (status === "degraded" || status === "empty") {
        return "warn";
      }
      return "bad";
    }

    /* 唯一网络出口：只读 GET（fetch 全页仅此一处）。 */
    function apiGet(name, params) {
      var endpoint = apiEndpoint(name);
      if (!endpoint) {
        return Promise.resolve({ http: 0, doc: null, ok: false, networkError: "未知只读端点: " + name });
      }
      var url = endpoint + toQuery(params || {});
      return fetch(url, {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store"
      })
        .then(function (response) {
          return response.text().then(function (text) {
            return {
              http: response.status,
              ok: response.ok,
              doc: parseJsonSafe(text),
              networkError: null
            };
          });
        })
        .catch(function (error) {
          return {
            http: 0,
            ok: false,
            doc: null,
            networkError: String((error && error.message) || error)
          };
        });
    }

    function describeResultError(result) {
      if (!result) {
        return "无响应（显式不可用，不造数）";
      }
      if (result.networkError) {
        return TEXT.networkFail;
      }
      if (result.doc === null) {
        return describeHttpError(result.http, null) || TEXT.nonJson;
      }
      var failure = describeHttpError(result.http, result.doc);
      if (failure) {
        return failure;
      }
      if (result.doc.ok === false && result.doc.error && result.doc.error.code) {
        return "业务失败：" + result.doc.error.code +
          (result.doc.error.message ? " · " + result.doc.error.message : "");
      }
      return "响应结构不符合只读契约（显式按不可用处理，不造数）";
    }

    function el(tag, className, text) {
      var node = document.createElement(tag);
      if (className) {
        node.className = className;
      }
      if (text !== undefined && text !== null) {
        node.textContent = String(text);
      }
      return node;
    }

    function appendKv(container, label, value) {
      var dt = el("dt", "", label);
      var dd = el("dd", "", display(value));
      container.appendChild(dt);
      container.appendChild(dd);
    }

    function renderBoundaries(boundaries) {
      var holder = $("boundaryChips");
      if (!holder) {
        return;
      }
      holder.textContent = "";
      var chips = boundaryChips();
      for (var i = 0; i < chips.length; i += 1) {
        holder.appendChild(el("span", "chip ok", chips[i]));
      }
      var keys = writeBoundaryKeys();
      var source = boundaries && typeof boundaries === "object" ? boundaries : {};
      for (var j = 0; j < keys.length; j += 1) {
        var key = keys[j];
        var off = source[key] === false;
        holder.appendChild(el("span", off ? "chip off" : "chip warn",
          key + (off ? "=关" : "=" + display(source[key]))));
      }
    }

    function renderHealth(result) {
      var booksHolder = $("healthBooks");
      if (booksHolder) {
        booksHolder.textContent = "";
      }
      if (!result || result.networkError || result.doc === null ||
          (result.doc && result.doc.ok === false && !result.doc.data)) {
        setSourceBadge("health 不可用", "bad");
        setStatus("healthStatus", describeResultError(result), "bad");
        showEmpty("healthEmpty", "两本账健康数据不可用（显式 unavailable，不造数）：" +
          describeResultError(result), "warn");
        return;
      }
      var data = result.doc.data && typeof result.doc.data === "object"
        ? result.doc.data
        : result.doc;
      var summary = summarizeHealth(data);
      if (!summary) {
        setSourceBadge("health 结构异常", "bad");
        setStatus("healthStatus", describeResultError(result), "bad");
        showEmpty("healthEmpty", "health 响应结构不符合契约（不造数）", "warn");
        return;
      }
      var hasUnavailable = false;
      for (var i = 0; i < summary.books.length; i += 1) {
        var book = summary.books[i];
        if (book.status === "unavailable") {
          hasUnavailable = true;
        }
        var card = el("div", "book-card");
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("data-book", book.book);
        card.title = "点击查看 " + book.book + " 只读路由（GET route）";
        var head = el("div", "book-head");
        head.appendChild(el("span", "book-name", book.book));
        head.appendChild(el("span", "chip " + toneOfStatus(book.status),
          "状态：" + book.statusLabel));
        card.appendChild(head);
        var kv = el("dl", "kv");
        appendKv(kv, "backend", book.backend);
        appendKv(kv, "exists", book.exists ? "是" : "否（缺失，不造数）");
        card.appendChild(kv);
        if (book.reasons.length) {
          var reasons = el("ul", "reasons");
          for (var r = 0; r < book.reasons.length; r += 1) {
            reasons.appendChild(el("li", "", book.reasons[r]));
          }
          card.appendChild(reasons);
        }
        if (booksHolder) {
          booksHolder.appendChild(card);
        }
      }
      renderBoundaries(data.boundaries);
      setSourceBadge("health " + summary.statusLabel, toneOfStatus(summary.status));
      setStatus("healthStatus",
        "health：" + summary.statusLabel +
        " · 只读边界已声明（写入面全关） · books=" +
        (summary.books.map(function (item) { return item.book + ":" + item.statusLabel; }).join(" / ")),
        toneOfStatus(summary.status));
      if (hasUnavailable || summary.status !== "ok") {
        showEmpty("healthEmpty",
          "存在不可用/降级账本：缺数据显式标注（unavailable/null），不造数。",
          summary.status === "unavailable" ? "warn" : "");
      } else {
        hideEmpty("healthEmpty");
      }
    }

    function accountRow(entry) {
      var row = el("button", "account-row");
      row.type = "button";
      row.setAttribute("data-book", entry.book);
      row.setAttribute("data-account", entry.account_id);
      row.appendChild(el("span", "aid", display(entry.account_id, "（空）")));
      var metaBits = [];
      if (entry.meta && entry.meta.cash !== undefined && entry.meta.cash !== null) {
        metaBits.push("cash " + entry.meta.cash);
      }
      if (entry.source) {
        metaBits.push("source " + entry.source);
      }
      row.appendChild(el("span", "ameta", metaBits.join(" · ")));
      row.appendChild(el("span", "anote",
        entry.namespace ? "namespace " + entry.namespace : "tenant 未落库（null，不猜）"));
      row.appendChild(el("span", "abook", entry.book));
      row.addEventListener("click", function () {
        fillSnapshotForm(entry);
      });
      return row;
    }

    function renderAccounts(result) {
      var list = $("accountsList");
      if (list) {
        list.textContent = "";
      }
      if (!result || result.networkError || result.doc === null ||
          (result.doc && result.doc.ok === false && !result.doc.data)) {
        setStatus("accountsNote", describeResultError(result), "bad");
        showEmpty("accountsEmpty", "账户目录不可用（显式 unavailable，不造数）：" +
          describeResultError(result), "warn");
        return;
      }
      var data = result.doc.data && typeof result.doc.data === "object"
        ? result.doc.data
        : result.doc;
      var summary = summarizeAccounts(data);
      if (!summary) {
        setStatus("accountsNote", "accounts 响应结构不符合契约（不造数）", "bad");
        showEmpty("accountsEmpty", TEXT.emptyAccounts, "warn");
        return;
      }
      var rendered = 0;
      var notes = [];
      for (var i = 0; i < summary.books.length; i += 1) {
        var book = summary.books[i];
        if (book.unavailable) {
          notes.push(book.book + "：" + display(book.unavailable.code, "unavailable") +
            " · " + display(book.unavailable.reason, ""));
        }
        if (book.storageAccounts === null) {
          notes.push(book.book + "：存储侧账户数 null（" + display(book.storageNote, "") + "）");
        }
        for (var j = 0; j < book.entries.length; j += 1) {
          if (list) {
            list.appendChild(accountRow(book.entries[j]));
          }
          rendered += 1;
        }
      }
      setStatus("accountsNote",
        "accounts：count=" + summary.count + " · 已渲染 " + rendered +
        " 条（存储发现 + 可选 registry；tenant 归属缺失显式 null，不猜）",
        rendered ? "" : "warn");
      if (notes.length) {
        setStatus("accountsNote",
          "accounts：count=" + summary.count + " · " + notes.join("；"), "warn");
      }
      if (rendered === 0) {
        showEmpty("accountsEmpty", TEXT.emptyAccounts, "");
      } else {
        hideEmpty("accountsEmpty");
      }
    }

    function fillSnapshotForm(entry) {
      var selBook = $("selBook");
      var inpTenant = $("inpTenant");
      var inpAccount = $("inpAccount");
      if (selBook && entry.book) {
        selBook.value = entry.book;
      }
      if (inpAccount) {
        inpAccount.value = entry.account_id || "";
      }
      if (inpTenant && entry.tenant_id) {
        inpTenant.value = entry.tenant_id;
      }
      if (entry.namespace) {
        setStatus("snapshotStatus", "已按 namespace 预填：" + entry.namespace + "，读取快照中…", "");
        loadSnapshot();
      } else {
        setStatus("snapshotStatus", TEXT.tenantUnverified, "warn");
        if (inpTenant) {
          inpTenant.focus();
        }
      }
    }

    function renderSnapshotKv(data) {
      var holder = el("dl", "kv");
      appendKv(holder, "账本 book", display(data.book));
      appendKv(holder, "后端 backend", display(data.backend));
      appendKv(holder, "namespace", display(data.namespace));
      appendKv(holder, "tenant_id", display(data.tenant_id));
      appendKv(holder, "account_id", data.account ? display(data.account.account_id) : null);
      appendKv(holder, "status", statusText(data.status) + "（" + display(data.status) + "）");
      appendKv(holder, "registry_verified", data.registry_verified === true ? "是" : "否（未声明 registry，归属不猜）");
      appendKv(holder, "归属说明", attributionNote(data.attribution));
      appendKv(holder, "只读", data.readonly === true ? "是" : "否");
      appendKv(holder, "writable / trading_permitted",
        display(data.writable) + " / " + display(data.trading_permitted));
      if (data.unavailable) {
        appendKv(holder, "不可用 unavailable",
          display(data.unavailable.code) + " · " + display(data.unavailable.reason) +
          (Array.isArray(data.unavailable.missing) && data.unavailable.missing.length
            ? " · 缺：" + data.unavailable.missing.join("、")
            : ""));
      }
      return holder;
    }

    function renderPositions(data) {
      var wrap = el("div");
      var title = el("div", "section-title",
        "持仓（positions）" + (positionsOf(data).length ? "" : " — " + TEXT.emptyPositions));
      wrap.appendChild(title);
      var positions = positionsOf(data);
      if (!positions.length) {
        wrap.appendChild(el("div", "empty", TEXT.emptyPositions));
        return wrap;
      }
      var tableWrap = el("div", "table-wrap");
      var table = el("table");
      var head = el("thead");
      var headRow = el("tr");
      var official = data.book === "official";
      var columns = official
        ? ["symbol", "weight", "entry_price", "last_price", "industry"]
        : ["symbol", "qty", "entry_price", "last_price", "industry"];
      for (var i = 0; i < columns.length; i += 1) {
        headRow.appendChild(el("th", "", columns[i]));
      }
      head.appendChild(headRow);
      table.appendChild(head);
      var body = el("tbody");
      for (var j = 0; j < positions.length; j += 1) {
        var row = el("tr");
        var item = positions[j];
        var values = official
          ? [item.symbol, item.weight, item.entry_price, item.last_price, item.industry]
          : [item.symbol, item.qty, item.entry_price, item.last_price, item.industry];
        for (var k = 0; k < values.length; k += 1) {
          row.appendChild(el("td", k === 0 ? "mono" : "", display(values[k], "null")));
        }
        body.appendChild(row);
      }
      table.appendChild(body);
      tableWrap.appendChild(table);
      wrap.appendChild(tableWrap);
      return wrap;
    }

    function renderBookSection(data) {
      var wrap = el("div");
      if (data.book === "official" && data.official) {
        var section = data.official;
        var kv = el("dl", "kv");
        appendKv(kv, "ledger rows", display(section.rows));
        appendKv(kv, "bad_lines", display(section.bad_lines));
        appendKv(kv, "dates", section.dates && typeof section.dates === "object"
          ? Object.keys(section.dates).sort().map(function (day) {
            return day + ":" + section.dates[day];
          }).join("、")
          : null);
        appendKv(kv, "last_date", display(section.last_date));
        appendKv(kv, "verdicts", section.verdicts && typeof section.verdicts === "object"
          ? Object.keys(section.verdicts).sort().map(function (key) {
            return key + ":" + section.verdicts[key];
          }).join("、")
          : null);
        appendKv(kv, "actions", section.actions && typeof section.actions === "object"
          ? Object.keys(section.actions).sort().map(function (key) {
            return key + ":" + section.actions[key];
          }).join("、")
          : null);
        appendKv(kv, "hash_chain_ok", section.hash_chain_ok === true ? "是" : "否（显式降级）");
        appendKv(kv, "nav", display(section.state ? section.state.nav : null, "null"));
        appendKv(kv, "cash_weight", display(section.state ? section.state.cash_weight : null, "null"));
        appendKv(kv, "state.updated_at", display(section.state ? section.state.updated_at : null, "null"));
        wrap.appendChild(kv);
      } else if (data.book === "legacy" && data.legacy) {
        var legacy = data.legacy;
        var legacyKv = el("dl", "kv");
        var account = legacy.account || {};
        var equity = legacy.equity || null;
        appendKv(legacyKv, "account.cash", display(account.cash, "null"));
        appendKv(legacyKv, "account.initial_cash", display(account.initial_cash, "null"));
        appendKv(legacyKv, "equity.date", equity ? display(equity.date) : "null（无权益快照）");
        appendKv(legacyKv, "equity.total", equity ? display(equity.total) : "null");
        appendKv(legacyKv, "positions_count", display(legacy.positions_count));
        appendKv(legacyKv, "orders.total", legacy.orders ? display(legacy.orders.total) : "null");
        appendKv(legacyKv, "orders.by_status", legacy.orders && legacy.orders.by_status
          ? Object.keys(legacy.orders.by_status).sort().map(function (key) {
            return key + ":" + legacy.orders.by_status[key];
          }).join("、")
          : null);
        appendKv(legacyKv, "ledger.rows", legacy.ledger ? display(legacy.ledger.rows) : "null");
        appendKv(legacyKv, "ledger.by_type", legacy.ledger && legacy.ledger.by_type
          ? Object.keys(legacy.ledger.by_type).sort().map(function (key) {
            return key + ":" + legacy.ledger.by_type[key];
          }).join("、")
          : null);
        wrap.appendChild(legacyKv);
      }
      wrap.appendChild(renderPositions(data));
      return wrap;
    }

    function renderSnapshot(result) {
      var body = $("snapshotBody");
      var empty = $("snapshotEmpty");
      if (body) {
        body.textContent = "";
      }
      if (!result || result.networkError || result.doc === null ||
          (result.doc && result.doc.ok === false && !result.doc.data)) {
        setStatus("snapshotStatus", "快照不可用：" + describeResultError(result), "bad");
        showEmpty("snapshotEmpty", "快照不可用（显式 unavailable，不造数）：" +
          describeResultError(result), "warn");
        return;
      }
      var data = result.doc.data && typeof result.doc.data === "object"
        ? result.doc.data
        : result.doc;
      var summary = summarizeSnapshot(data);
      if (!summary) {
        setStatus("snapshotStatus", "snapshot 响应结构不符合契约（不造数）", "bad");
        showEmpty("snapshotEmpty", TEXT.emptySnapshot, "warn");
        return;
      }
      setStatus("snapshotStatus",
        "snapshot：" + summary.statusLabel +
        " · book=" + summary.book +
        " · namespace=" + display(summary.namespace, "null（租户归属未落库）") +
        " · 持仓 " + summary.positions.length + " 只",
        toneOfStatus(summary.status));
      if (body) {
        body.appendChild(renderSnapshotKv(data));
        if (summary.unavailable) {
          var un = el("div", "empty warn",
            "缺失/不可用显式标注（不造数）：" +
            display(summary.unavailable.code) + " · " +
            display(summary.unavailable.reason));
          body.appendChild(un);
        }
        if (summary.warnings.length) {
          var warnBox = el("ul", "reasons");
          for (var w = 0; w < summary.warnings.length; w += 1) {
            warnBox.appendChild(el("li", "", summary.warnings[w]));
          }
          body.appendChild(warnBox);
        }
        body.appendChild(renderBookSection(data));
      }
      hideEmpty("snapshotEmpty");
      if (empty) {
        empty.classList.add("hidden");
      }
    }

    function renderRoute(result) {
      var body = $("routeBody");
      if (body) {
        body.textContent = "";
      }
      if (!result || result.networkError || result.doc === null ||
          (result.doc && result.doc.ok === false && !result.doc.data)) {
        setStatus("routeStatus", "路由不可用：" + describeResultError(result), "bad");
        return;
      }
      var data = result.doc.data && typeof result.doc.data === "object"
        ? result.doc.data
        : result.doc;
      if (!data || !data.book) {
        setStatus("routeStatus", "route 响应结构不符合契约（不造数）", "bad");
        return;
      }
      setStatus("routeStatus",
        "route：" + display(data.book) + " · backend=" + display(data.backend) +
        " · namespace_prefix=" + display(data.route ? data.route.namespace_prefix : null),
        "ok");
      if (!body) {
        return;
      }
      var kv = el("dl", "kv");
      appendKv(kv, "book", data.book);
      appendKv(kv, "backend", data.backend);
      appendKv(kv, "namespace", display(data.namespace, "null（仅 book 级路由描述）"));
      appendKv(kv, "resolved", data.resolved === true ? "是" : "否");
      appendKv(kv, "namespace_prefix", data.route ? data.route.namespace_prefix : null);
      var storage = data.route && data.route.storage ? data.route.storage : {};
      appendKv(kv, "storage.kind", display(storage.kind));
      appendKv(kv, "storage.mode", display(storage.mode, "ro（只读）"));
      appendKv(kv, "storage.exists", storage.ledger ? (storage.ledger.exists ? "是" : "否（缺失，不造数）")
        : (storage.db ? (storage.db.exists ? "是" : "否（缺失，不造数）") : null));
      appendKv(kv, "readonly / writable",
        (data.readonly === true ? "true" : "false") + " / " + display(data.writable));
      appendKv(kv, "trading_permitted", display(data.trading_permitted));
      var caps = data.route && data.route.capabilities ? data.route.capabilities : {};
      appendKv(kv, "capabilities 写入面",
        "write_ledger=" + display(caps.write_ledger, "false") +
        " · place_order=" + display(caps.place_order, "false") +
        " · start_matching=" + display(caps.start_matching, "false") +
        " · migrate_ledger=" + display(caps.migrate_ledger, "false"));
      body.appendChild(kv);
      renderBoundaries(data.boundaries || (data.route ? data.route.capabilities : {}));
    }

    var lastRouteBook = "official";

    function loadHealth() {
      setStatus("healthStatus", TEXT.loading, "");
      setSourceBadge("health 读取中…", "");
      return apiGet("health", {}).then(renderHealth);
    }

    function loadAccounts() {
      var filter = $("bookFilter");
      var book = filter ? filter.value : "";
      var params = {};
      if (book) {
        params.book = book;
      }
      setStatus("accountsNote", TEXT.loading, "");
      return apiGet("accounts", params).then(renderAccounts);
    }

    function loadSnapshot() {
      var selBook = $("selBook");
      var inpTenant = $("inpTenant");
      var inpAccount = $("inpAccount");
      var params = buildSnapshotParams({
        book: selBook ? selBook.value : "",
        tenant: inpTenant ? inpTenant.value : "",
        account: inpAccount ? inpAccount.value : ""
      });
      if (!params.tenant && !params.account) {
        setStatus("snapshotStatus", TEXT.tenantInputMissing, "warn");
        return null;
      }
      setStatus("snapshotStatus", TEXT.loading, "");
      return apiGet("snapshot", params).then(renderSnapshot);
    }

    function loadRoute(book) {
      var target = book || lastRouteBook;
      lastRouteBook = target;
      setStatus("routeStatus", TEXT.loading, "");
      return apiGet("route", { book: target }).then(renderRoute);
    }

    function bindBookCards() {
      var holder = $("healthBooks");
      if (!holder) {
        return;
      }
      holder.addEventListener("click", function (event) {
        var node = event.target;
        while (node && node !== holder) {
          if (node.getAttribute && node.getAttribute("data-book")) {
            loadRoute(node.getAttribute("data-book"));
            return;
          }
          node = node.parentNode;
        }
      });
      holder.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        var node = event.target;
        if (node && node.getAttribute && node.getAttribute("data-book")) {
          event.preventDefault();
          loadRoute(node.getAttribute("data-book"));
        }
      });
    }

    function boot() {
      if (typeof document === "undefined") {
        return;
      }
      var refresh = $("btnRefresh");
      if (refresh) {
        refresh.addEventListener("click", function () {
          loadHealth();
          loadAccounts();
          loadRoute(lastRouteBook);
        });
      }
      var loadAccountsBtn = $("btnLoadAccounts");
      if (loadAccountsBtn) {
        loadAccountsBtn.addEventListener("click", loadAccounts);
      }
      var loadSnapshotBtn = $("btnLoadSnapshot");
      if (loadSnapshotBtn) {
        loadSnapshotBtn.addEventListener("click", loadSnapshot);
      }
      var bookFilter = $("bookFilter");
      if (bookFilter) {
        bookFilter.addEventListener("change", loadAccounts);
      }
      bindBookCards();
      setStatus("healthStatus", TEXT.loading, "");
      setStatus("accountsNote", TEXT.loading, "");
      setStatus("snapshotStatus", TEXT.noSnapshotYet, "");
      setStatus("routeStatus", TEXT.noRouteYet, "");
      loadHealth();
      loadAccounts();
    }

    return {
      /* 契约常量 */
      API: API,
      BOOK_ORDER: BOOK_ORDER,
      STATUS_TEXT: STATUS_TEXT,
      TEXT: TEXT,
      /* 纯函数（Node 契约测试） */
      apiEndpoint: apiEndpoint,
      apiEndpoints: apiEndpoints,
      boundaryChips: boundaryChips,
      writeBoundaryKeys: writeBoundaryKeys,
      display: display,
      toQuery: toQuery,
      parseJsonSafe: parseJsonSafe,
      statusText: statusText,
      escapeHtml: escapeHtml,
      buildSnapshotParams: buildSnapshotParams,
      buildSnapshotQuery: buildSnapshotQuery,
      describeHttpError: describeHttpError,
      attributionNote: attributionNote,
      summarizeHealth: summarizeHealth,
      summarizeAccounts: summarizeAccounts,
      summarizeSnapshot: summarizeSnapshot,
      positionsOf: positionsOf,
      /* DOM 入口（浏览器自动 boot；Node 仅导出引用） */
      boot: boot,
      loadHealth: loadHealth,
      loadAccounts: loadAccounts,
      loadSnapshot: loadSnapshot,
      loadRoute: loadRoute
    };
  }
);
