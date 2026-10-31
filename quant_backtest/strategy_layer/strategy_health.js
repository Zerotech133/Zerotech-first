/*!
 * Phase 5-B Strategy Health 周报只读页面（WO-HEALTH-UI-20261031）
 * 零外部依赖、离线优先、只读。
 *
 * 职责边界（硬约束）：
 *  - 只读展示：仅发 1 个同源 GET 请求 /strategy_health.json
 *    （WeeklyHealthReport 只读产出，Phase5-A 路由由 Lead 后续接入）；
 *  - 不写服务端、不调 POST/PUT/PATCH/DELETE、不上传/不下载、不做本地存储、
 *    不下单、不执行、不改 registry/权重、不新增计划任务、不重启服务；
 *  - 空/缺失/不可用一律显式显示（暂无周报 / 样本不足 / 端点未接入 /
 *    接口不可用 / 非 JSON），绝不补零、不猜归属、不造数；
 *  - 不引用外部 CDN / 绝对外部 URL / 写死端口；页面路由由 Lead 后续接入；
 *  - BiasTicket 全部 status=draft 且 not_auto_apply=true：仅建议上游人工复核。
 *
 * 结构：
 *  1) 常量：只读端点、等级/状态文案、空态文案、只读边界；
 *  2) 纯函数（display / formatPct / formatNum / statusText / healthLevelLabel /
 *     periodText / regimeText / summarize* …，浏览器与 Node 均可直调；
 *     Node 下 module.exports，供 pytest 做纯函数契约测试）；
 *  3) DOM 层：总览/策略卡片/偏差票表格/口径说明；网络出口只有一处（apiGet）。
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api; /* Node：供 pytest 纯函数契约测试 require */
  } else if (root) {
    root.StrategyHealthUI = api; /* 浏览器：全局命名空间 */
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

    /* ── 1. 常量：只读契约 ───────────────────────────────────────────── */
    /* 唯一网络出口：同源 GET（页面路由由 Lead 后续接入，本页不写服务端）。 */
    var HEALTH_URL = "/strategy_health.json";

    /* 健康等级固定顺序与文案（与 strategy_health.HEALTH_LEVELS 同口径）。 */
    var LEVEL_ORDER = ["green", "yellow", "red", "insufficient"];
    var LEVEL_TEXT = {
      green: "健康",
      yellow: "关注",
      red: "预警",
      insufficient: "样本不足"
    };
    var LEVEL_TONE = {
      green: "ok",
      yellow: "warn",
      red: "bad",
      insufficient: "empty"
    };
    var STATUS_TEXT = {
      OK: "正常",
      insufficient_data: "样本不足"
    };

    /* 空态/不可用显式文案（不造数；HTML 首屏亦有静态同款文案）。 */
    var TEXT = {
      loading: "正在读取 /strategy_health.json…",
      endpointMissing:
        "端点未接入：GET /strategy_health.json 暂无响应（路由未接或离线）——暂无周报，不造数。",
      networkFail:
        "接口不可用：同源 GET /strategy_health.json 失败——暂无周报，不造数。",
      nonJson:
        "响应非 JSON：/strategy_health.json 结构不可解析，按不可用处理，不造数。",
      emptyReport:
        "暂无周报：本周窗口内没有策略后验数据（空态显式显示，不造数）。",
      insufficient:
        "样本不足：窗口内样本数未达门槛，本期不下健康结论（insufficient，不造数）。",
      emptyStrategies:
        "暂无周报：没有可展示的策略卡片（空态显式显示，不造数）。",
      emptyTickets:
        "本周无偏差票（BiasTicket status=draft，仅建议上游人工复核）。",
      unknown: "未知",
      noRegime: "regime 无标签（不猜）",
      notAutoApply: "not_auto_apply=true（仅建议，不自动生效）",
      readonlyNote:
        "只读：仅 GET /strategy_health.json（同源）；不写服务端、无 POST、不执行、"
        + "不改 registry/权重，不造数。"
    };

    /* 只读边界徽标（页面/JS 双处显式声明）。 */
    var BOUNDARIES = ["只读", "不下单", "不造数"];

    /* ── 2. 纯函数（Node 契约测试直调） ─────────────────────────────── */
    function $(id) {
      if (typeof document === "undefined") {
        return null;
      }
      return document.getElementById(id);
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

    /* 显式缺失显示：null/undefined/空串 → fallback（默认 —），不补零。 */
    function display(value, fallback) {
      var fill = fallback === undefined ? "—" : fallback;
      if (value === null || value === undefined) {
        return fill;
      }
      var text = String(value).trim();
      return text === "" ? fill : text;
    }

    function numberOrNull(value) {
      if (typeof value !== "number" || !isFinite(value)) {
        return null;
      }
      return value;
    }

    /* 百分数口径：值本身已是百分数（66.7 → "66.7%"）；缺失 → —。 */
    function formatPct(value) {
      if (value === null || value === undefined || value === "") {
        return "—";
      }
      var num = Number(value);
      if (!isFinite(num)) {
        return "—";
      }
      if (num === 0) {
        num = 0; /* 消除 -0 */
      }
      return num.toFixed(1) + "%";
    }

    /* 普通数值：缺省 2 位小数，去尾零；缺失 → —。 */
    function formatNum(value, digits) {
      if (value === null || value === undefined || value === "") {
        return "—";
      }
      var num = Number(value);
      if (!isFinite(num)) {
        return "—";
      }
      var places = digits === undefined ? 2 : digits;
      var fixed = num.toFixed(places);
      if (fixed.indexOf(".") >= 0) {
        fixed = fixed.replace(/0+$/, "").replace(/\.$/, "");
      }
      if (fixed === "-0" || fixed === "-0.0") {
        fixed = "0";
      }
      return fixed;
    }

    function healthLevelLabel(level) {
      return LEVEL_TEXT[level] || TEXT.unknown;
    }

    function healthLevelTone(level) {
      return LEVEL_TONE[level] || "empty";
    }

    function statusText(status) {
      return STATUS_TEXT[status] || TEXT.unknown;
    }

    /* 周期文案：start ~ end（N 交易日 / M 行）；无周期 → 暂无周报。 */
    function periodText(period) {
      if (!period || typeof period !== "object") {
        return "暂无周报";
      }
      var start = display(period.start);
      var end = display(period.end);
      if (start === "—" && end === "—") {
        return "暂无周报";
      }
      var text = start + " ~ " + end;
      var tradingDays = numberOrNull(period.trading_days);
      var rows = numberOrNull(period.rows);
      if (tradingDays !== null) {
        text += "（" + tradingDays + " 交易日";
        if (rows !== null) {
          text += " / " + rows + " 行";
        }
        text += "）";
      }
      return text;
    }

    /* regime 分层文案：无标签按「不猜」显式显示，不臆造分层结论。 */
    function regimeText(entry) {
      if (!entry || typeof entry !== "object") {
        return TEXT.noRegime;
      }
      if (entry.regime_status !== "available") {
        return TEXT.noRegime;
      }
      var breakdown = entry.regime_breakdown;
      if (!breakdown || typeof breakdown !== "object") {
        return TEXT.noRegime;
      }
      var names = Object.keys(breakdown).sort();
      if (!names.length) {
        return TEXT.noRegime;
      }
      return names
        .map(function (name) {
          var bucket = breakdown[name] || {};
          var t1 = bucket.t1 || {};
          return (
            name +
            "=" +
            display(bucket.count, "0") +
            " 行 / T+1 胜率 " +
            formatPct(t1.win_rate)
          );
        })
        .join(" · ");
    }

    /* 总览：周期/asof/状态 + 健康等级计数 + summary 指标。 */
    function summarizeOverview(doc) {
      if (!doc || typeof doc !== "object") {
        return null;
      }
      var levels =
        doc.health_levels && typeof doc.health_levels === "object"
          ? doc.health_levels
          : {};
      var summary = doc.summary && typeof doc.summary === "object" ? doc.summary : {};
      var period = doc.period && typeof doc.period === "object" ? doc.period : {};
      var priority = summary.tickets_by_priority || {};
      return {
        status: doc.status || null,
        statusText: statusText(doc.status),
        asof: display(doc.asof),
        periodText: periodText(period),
        generatedAt: display(doc.generated_at),
        readonly: doc.readonly === true,
        counts: LEVEL_ORDER.map(function (level) {
          return {
            level: level,
            label: healthLevelLabel(level),
            tone: healthLevelTone(level),
            count: numberOrNull(levels[level])
          };
        }),
        metrics: {
          strategies: numberOrNull(summary.strategies),
          strategiesWithSamples: numberOrNull(summary.strategies_with_samples),
          biasTickets: numberOrNull(summary.bias_tickets),
          p1: numberOrNull(priority.P1),
          p2: numberOrNull(priority.P2),
          tradingDays: numberOrNull(period.trading_days),
          rows: numberOrNull(period.rows)
        }
      };
    }

    /* 单策略卡片：样本/t1/t5 胜率与均收益/回撤/regime/等级。 */
    function summarizeStrategy(entry) {
      if (!entry || typeof entry !== "object") {
        return null;
      }
      var windows = entry.windows && typeof entry.windows === "object" ? entry.windows : {};
      var t1 = windows.t1 && typeof windows.t1 === "object" ? windows.t1 : {};
      var t5 = windows.t5 && typeof windows.t5 === "object" ? windows.t5 : {};
      var drawdown = entry.max_drawdown && typeof entry.max_drawdown === "object"
        ? entry.max_drawdown
        : {};
      var card = entry.card && typeof entry.card === "object" ? entry.card : {};
      var weekly = entry.weekly && typeof entry.weekly === "object" ? entry.weekly : {};
      return {
        strategyId: display(entry.strategy_id),
        level: display(entry.health_level),
        levelLabel: healthLevelLabel(entry.health_level),
        tone: healthLevelTone(entry.health_level),
        sampleCount: numberOrNull(entry.sample_count),
        t1Count: numberOrNull(t1.count),
        t1WinRate: formatPct(t1.win_rate),
        t1AvgRet: formatPct(t1.avg_ret),
        t5Count: numberOrNull(t5.count),
        t5WinRate: formatPct(t5.win_rate),
        t5AvgRet: formatPct(t5.avg_ret),
        drawdown: formatPct(drawdown.max_drawdown),
        drawdownStatus: drawdown.status || null,
        divergenceRate: formatPct(entry.divergence_rate),
        regimeText: regimeText(entry),
        regimeStatus: entry.regime_status || null,
        weeklyTradingDays: numberOrNull(weekly.trading_days),
        healthReasons: Array.isArray(entry.health_reasons)
          ? entry.health_reasons.slice()
          : [],
        cardName: card.display_name || null,
        owner: card.owner || null,
        ticketIds: Array.isArray(entry.bias_ticket_ids)
          ? entry.bias_ticket_ids.slice()
          : []
      };
    }

    /* 单张上游 BiasTicket：类型/优先级/证据/建议/status/owner_upstream。 */
    function summarizeTicket(ticket) {
      if (!ticket || typeof ticket !== "object") {
        return null;
      }
      var evidence = Array.isArray(ticket.evidence) ? ticket.evidence : [];
      return {
        ticketId: display(ticket.ticket_id),
        strategyId: display(ticket.strategy_id),
        biasType: display(ticket.bias_type),
        priority: display(ticket.priority),
        status: display(ticket.status),
        statusText:
          ticket.status === "draft" ? "draft（草稿）" : display(ticket.status),
        ownerUpstream: display(ticket.owner_upstream),
        evidenceText: evidence.join("；"),
        suggestedAction: display(ticket.suggested_action),
        notAutoApply: ticket.not_auto_apply === true
      };
    }

    /* 整份周报：空/insufficient 显式标记，不造数。 */
    function summarizeReport(payload) {
      if (!payload || typeof payload !== "object") {
        return null;
      }
      var strategies = Array.isArray(payload.strategies)
        ? payload.strategies.map(summarizeStrategy).filter(Boolean)
        : [];
      var tickets = Array.isArray(payload.bias_tickets)
        ? payload.bias_tickets.map(summarizeTicket).filter(Boolean)
        : [];
      var insufficient = payload.status === "insufficient_data";
      var empty = insufficient || strategies.length === 0;
      var emptyText = "";
      if (insufficient) {
        emptyText = TEXT.insufficient;
      } else if (!strategies.length) {
        emptyText = TEXT.emptyReport;
      }
      return {
        status: payload.status || null,
        statusText: statusText(payload.status),
        overview: summarizeOverview(payload),
        strategies: strategies,
        tickets: tickets,
        empty: empty,
        insufficient: insufficient,
        emptyText: emptyText,
        notes: Array.isArray(payload.notes) ? payload.notes.slice() : []
      };
    }

    /* ── 3. DOM 层 ──────────────────────────────────────────────────── */
    function setText(id, text) {
      var node = $(id);
      if (node) {
        node.textContent = String(text);
      }
    }

    function setBadge(id, text, tone) {
      var node = $(id);
      if (!node) {
        return;
      }
      node.textContent = String(text);
      node.className = "badge" + (tone ? " " + tone : "");
    }

    function setStatus(id, text) {
      var node = $(id);
      if (!node) {
        return;
      }
      node.textContent = String(text);
    }

    function countNode(item) {
      var box = el("div", "count " + item.tone);
      box.setAttribute("data-level", item.level);
      box.appendChild(el("div", "k", item.label + "（" + item.level + "）"));
      box.appendChild(el("div", "v", display(item.count, "—")));
      return box;
    }

    function metricNode(label, value) {
      var box = el("div", "count");
      box.appendChild(el("div", "k", label));
      box.appendChild(el("div", "v", display(value, "—")));
      return box;
    }

    function strategyNode(item) {
      var card = el("article", "strategy " + item.tone);
      card.setAttribute("data-strategy", item.strategyId);
      card.setAttribute("data-health-level", item.level);

      var title = el("h3");
      title.appendChild(el("span", "sname", item.strategyId));
      var badge = el("span", "badge " + item.tone, item.levelLabel);
      badge.setAttribute("data-level", item.level);
      title.appendChild(badge);
      if (item.cardName) {
        title.appendChild(el("span", "tag", item.cardName));
      }
      card.appendChild(title);

      var metrics = el("div", "metrics");
      [
        ["样本", display(item.sampleCount, "—")],
        ["T+1 胜率", item.t1WinRate],
        ["T+1 均收益", item.t1AvgRet],
        ["T+5 胜率", item.t5WinRate],
        ["T+5 均收益", item.t5AvgRet],
        ["最大回撤", item.drawdown],
        ["regime", item.regimeText],
        ["分歧率", item.divergenceRate],
        ["交易日", display(item.weeklyTradingDays, "—")]
      ].forEach(function (pair) {
        var cell = el("div", "metric");
        cell.appendChild(el("div", "k", pair[0]));
        cell.appendChild(el("div", "v", pair[1]));
        metrics.appendChild(cell);
      });
      card.appendChild(metrics);

      if (item.healthReasons.length) {
        var reasons = el("ul", "reasons");
        item.healthReasons.forEach(function (reason) {
          reasons.appendChild(el("li", "", reason));
        });
        card.appendChild(reasons);
      }
      return card;
    }

    function ticketRow(item, index) {
      var row = el("tr");
      row.setAttribute("data-ticket", item.ticketId);
      [
        item.ticketId,
        item.strategyId,
        item.ownerUpstream,
        item.biasType,
        item.priority,
        item.evidenceText,
        item.suggestedAction,
        item.statusText
      ].forEach(function (value, column) {
        var cell = el("td", column === 0 ? "mono" : "", value);
        row.appendChild(cell);
      });
      if (index % 2 === 1) {
        row.className = "alt";
      }
      return row;
    }

    function renderOverview(state) {
      var holder = $("levelCounts");
      if (holder) {
        holder.textContent = "";
        (state.overview ? state.overview.counts : []).forEach(function (item) {
          holder.appendChild(countNode(item));
        });
      }
      var metricsHolder = $("summaryCounts");
      if (metricsHolder) {
        metricsHolder.textContent = "";
        var metrics = state.overview ? state.overview.metrics : {};
        metricsHolder.appendChild(
          metricNode("策略数", display(metrics.strategies, "—"))
        );
        metricsHolder.appendChild(
          metricNode("有样本策略", display(metrics.strategiesWithSamples, "—"))
        );
        metricsHolder.appendChild(
          metricNode("偏差票", display(metrics.biasTickets, "—"))
        );
        metricsHolder.appendChild(metricNode("P1 / P2", display(metrics.p1, "—") + " / " + display(metrics.p2, "—")));
        metricsHolder.appendChild(
          metricNode("交易日", display(metrics.tradingDays, "—"))
        );
        metricsHolder.appendChild(metricNode("本周样本行", display(metrics.rows, "—")));
      }
      if (state.overview) {
        setBadge("periodBadge", "周期 " + state.overview.periodText, "");
        setBadge("asofBadge", "asof " + state.overview.asof, "");
        setBadge(
          "statusBadge",
          "状态 " + state.overview.statusText,
          state.insufficient ? "warn" : "ok"
        );
        setText(
          "overviewStatus",
          "数据源：GET /strategy_health.json｜生成 " +
            state.overview.generatedAt +
            "｜" +
            TEXT.readonlyNote
        );
      }
    }

    function renderStrategies(state) {
      var holder = $("strategyCards");
      if (holder) {
        holder.textContent = "";
        state.strategies.forEach(function (item) {
          holder.appendChild(strategyNode(item));
        });
      }
      var empty = $("strategyEmpty");
      if (empty) {
        empty.textContent = TEXT.emptyStrategies;
        empty.hidden = state.strategies.length > 0;
      }
    }

    function renderTickets(state) {
      var body = $("ticketBody");
      if (body) {
        body.textContent = "";
        state.tickets.forEach(function (item, index) {
          body.appendChild(ticketRow(item, index));
        });
      }
      var empty = $("ticketEmpty");
      if (empty) {
        empty.textContent = TEXT.emptyTickets;
        empty.hidden = state.tickets.length > 0;
      }
    }

    function renderNotes(state) {
      var list = $("notesList");
      if (!list) {
        return;
      }
      list.textContent = "";
      state.notes.forEach(function (note) {
        list.appendChild(el("li", "", note));
      });
      list.appendChild(el("li", "", TEXT.notAutoApply));
    }

    function renderEmptyBanner(message) {
      var banner = $("reportEmpty");
      if (banner) {
        banner.textContent = message;
        banner.hidden = false;
      }
      var insuf = $("insufficientBanner");
      if (insuf) {
        insuf.hidden = true;
      }
    }

    function renderUnavailable(message) {
      setBadge("statusBadge", "状态 不可用", "bad");
      setText("overviewStatus", message);
      renderEmptyBanner(message);
      var holder = $("levelCounts");
      if (holder) {
        holder.textContent = "";
      }
      var metricsHolder = $("summaryCounts");
      if (metricsHolder) {
        metricsHolder.textContent = "";
      }
      var cards = $("strategyCards");
      if (cards) {
        cards.textContent = "";
      }
      var body = $("ticketBody");
      if (body) {
        body.textContent = "";
      }
      var strategyEmpty = $("strategyEmpty");
      if (strategyEmpty) {
        strategyEmpty.textContent = message;
        strategyEmpty.hidden = false;
      }
      var ticketEmpty = $("ticketEmpty");
      if (ticketEmpty) {
        ticketEmpty.textContent = message;
        ticketEmpty.hidden = false;
      }
    }

    function renderReport(state) {
      renderOverview(state);
      renderStrategies(state);
      renderTickets(state);
      renderNotes(state);
      var banner = $("reportEmpty");
      if (banner) {
        if (state.empty) {
          banner.textContent = state.emptyText || TEXT.emptyReport;
          banner.hidden = false;
        } else {
          banner.hidden = true;
        }
      }
      var insuf = $("insufficientBanner");
      if (insuf) {
        if (state.insufficient) {
          insuf.textContent = TEXT.insufficient;
          insuf.hidden = false;
        } else {
          insuf.hidden = true;
        }
      }
    }

    /* 唯一网络出口：同源 GET（全页仅此一处）。
       fetch 返回 Promise：网络失败走 catch → 显式「接口不可用」，不造数。 */
    function apiGet() {
      return fetch(HEALTH_URL, {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store"
      })
        .then(function (response) {
          return response.text().then(function (text) {
            return {
              http: response.status,
              ok: response.ok,
              doc: parseJsonSafe(text)
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

    function loadHealthReport() {
      setStatus("overviewStatus", TEXT.loading);
      return apiGet().then(function (result) {
        if (result.networkError) {
          renderUnavailable(TEXT.networkFail);
          return;
        }
        if (result.http === 404) {
          renderUnavailable(TEXT.endpointMissing);
          return;
        }
        if (!result.ok || result.doc === null) {
          renderUnavailable(
            result.doc === null ? TEXT.nonJson : TEXT.endpointMissing
          );
          return;
        }
        var state = summarizeReport(result.doc);
        if (!state) {
          renderUnavailable(TEXT.nonJson);
          return;
        }
        renderReport(state);
      });
    }

    function boot() {
      if (typeof document === "undefined") {
        return;
      }
      var refresh = $("btnRefresh");
      if (refresh) {
        refresh.addEventListener("click", function () {
          loadHealthReport();
        });
      }
      setBadge("sourceBadge", "GET " + HEALTH_URL, "");
      setBadge("statusBadge", "状态 读取中", "empty");
      setText("overviewStatus", TEXT.loading);
      loadHealthReport();
    }

    function parseJsonSafe(text) {
      if (typeof text !== "string" || text.trim() === "") {
        return null;
      }
      try {
        return JSON.parse(text);
      } catch (error) {
        return null;
      }
    }

    return {
      /* 契约常量 */
      API: { health: HEALTH_URL },
      LEVEL_ORDER: LEVEL_ORDER,
      LEVEL_TEXT: LEVEL_TEXT,
      STATUS_TEXT: STATUS_TEXT,
      TEXT: TEXT,
      BOUNDARIES: BOUNDARIES,
      /* 纯函数（Node 契约测试） */
      apiEndpoint: function () {
        return HEALTH_URL;
      },
      display: display,
      numberOrNull: numberOrNull,
      formatPct: formatPct,
      formatNum: formatNum,
      healthLevelLabel: healthLevelLabel,
      healthLevelTone: healthLevelTone,
      statusText: statusText,
      periodText: periodText,
      regimeText: regimeText,
      parseJsonSafe: parseJsonSafe,
      summarizeOverview: summarizeOverview,
      summarizeStrategy: summarizeStrategy,
      summarizeTicket: summarizeTicket,
      summarizeReport: summarizeReport,
      /* DOM 入口（浏览器自动 boot；Node 仅导出引用） */
      boot: boot,
      loadHealthReport: loadHealthReport
    };
  }
);
