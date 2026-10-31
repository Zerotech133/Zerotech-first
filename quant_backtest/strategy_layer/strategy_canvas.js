/*!
 * Phase 2-B / 4-B · Strategy Canvas —— 轻量 DAG 编排 + Composition IR 导出 + 模板库载入
 * 工单：WO-STRATEGY-CANVAS-20261031（Phase 2-B）、WO-CANVAS-TEMPLATES-20261031（Phase 4-B）
 *
 * 职责边界（硬约束）：
 *  - 前端纯本地：唯一网络出口 = 白名单 GET（pipeline_graph.json / pipeline_templates.json），
 *    收敛在 getJson() 单点；不写服务端、不发 POST/PUT/DELETE、不接执行/不下单；
 *  - 模板库（Phase 4-B）：下拉列出 pipeline_templates.json 的 3 个模板，载入节点/边/header
 *    到画布（只读内存状态）；fetch 失败用内置 3 模板 fallback，不崩；
 *  - 导出的 Composition IR（composition_ir/v1）只是「计划」，权威校验仍走
 *    `python -X utf8 strategy_layer/composition_ir.py --file <ir>.json`；
 *  - 不改 pipeline_map.html / strategy_cards / strategy_registry / composition_ir.py。
 *
 * 结构：
 *  1) 常量与契约（NODE_TYPES / NODE_SPECS / 端口 / schema 字段 / 模板 URL）——与
 *     composition_ir.py、pipeline_templates.py 同口径（客户端镜像）；
 *  2) 纯函数：buildIR / validateIR / canConnect / parseTemplatesPayload / selectTemplates /
 *     templateToCanvas —— 浏览器与 Node 均可直接调用（Node 下 module.exports 导出，
 *     便于 pytest 用 node 做契约/负例/模板测试）；
 *  3) DOM 层：左侧节点面板、中 SVG+HTML 画布（拖拽/连线/删除/移动）、右侧 IR 预览、
 *     工具栏模板下拉+载入。
 */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module && module.exports) {
    module.exports = api; /* Node：供测试 require */
  } else if (root) {
    root.CanvasCore = api; /* 浏览器：全局命名空间 */
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

    /* ══════════════════════════════════════════════════════════════════
     * 1. 常量与契约（镜像 composition_ir/v1）
     * ══════════════════════════════════════════════════════════════════ */
    var SCHEMA = "composition_ir/v1";
    var GRAPH_URL = "pipeline_graph.json";
    var TEMPLATES_URL = "pipeline_templates.json";
    var TEMPLATE_SCHEMA = "pipeline_templates/v1";
    var TEMPLATE_IDS = ["trend_pullback", "trend_reversal", "nine_dim_chan"];
    var ALLOWED_MODES = ["BACKTEST", "REPLAY", "SHADOW", "SIM_MARKET"];
    var NODE_TYPES = [
      "universe",
      "strategy",
      "filter",
      "rank",
      "allocator",
      "risk",
      "execution",
      "agent_team"
    ];
    var PORT_TYPES = ["universe", "signals", "ranking", "allocations", "orders", "advice"];
    var TERMINAL_NODE_TYPES = ["execution", "agent_team"];

    /* 节点类型 → 输入端口名/端口类型、输出端口名/端口类型（= composition_ir.NODE_SPECS） */
    var NODE_SPECS = {
      universe: { inputs: {}, outputs: { universe: "universe" }, terminal: false },
      strategy: { inputs: { universe: "universe" }, outputs: { signals: "signals" }, terminal: false },
      filter: { inputs: { signals: "signals" }, outputs: { signals: "signals" }, terminal: false },
      rank: { inputs: { signals: "signals" }, outputs: { ranking: "ranking" }, terminal: false },
      allocator: {
        inputs: { signals: "signals", ranking: "ranking", advice: "advice" },
        outputs: { allocations: "allocations" },
        terminal: false
      },
      risk: { inputs: { allocations: "allocations" }, outputs: { allocations: "allocations" }, terminal: false },
      execution: { inputs: { allocations: "allocations" }, outputs: { orders: "orders" }, terminal: true },
      agent_team: {
        inputs: {
          universe: "universe",
          signals: "signals",
          ranking: "ranking",
          allocations: "allocations",
          orders: "orders"
        },
        outputs: { advice: "advice" },
        terminal: true
      }
    };

    /* 面板展示元数据（label/short/hint/默认 params；颜色由 HTML CSS 的 type-* 类承载） */
    var NODE_META = {
      universe: {
        label: "Universe 市场宇宙",
        short: "Universe",
        hint: "无输入 → universe 端口",
        defaults: { markets: ["CN"] }
      },
      strategy: {
        label: "Strategy 策略",
        short: "Strategy",
        hint: "输入 universe → signals；从 Graph JSON 读取 live/shadow",
        defaults: {}
      },
      filter: {
        label: "Filter 信号过滤",
        short: "Filter",
        hint: "signals → signals",
        defaults: {}
      },
      rank: { label: "Rank 排序 TopN", short: "Rank", hint: "signals → ranking", defaults: { top_n: 30 } },
      allocator: {
        label: "Allocator 仓位分配",
        short: "Allocator",
        hint: "signals/ranking/advice → allocations",
        defaults: { method: "equal_weight" }
      },
      risk: { label: "Risk 风控", short: "Risk", hint: "allocations → allocations", defaults: {} },
      execution: {
        label: "Execution 模拟执行（dry_run）",
        short: "Execution",
        hint: "allocations → orders；只读计划，不下单",
        defaults: { book: "official", dry_run: true }
      },
      agent_team: {
        label: "AgentTeam 智能体团队",
        short: "AgentTeam",
        hint: "任意层观察 → advice；只读建议",
        defaults: { role: "observer" }
      }
    };

    var TOP_LEVEL_REQUIRED = [
      "pipeline_id",
      "version",
      "owner",
      "tenant",
      "mode",
      "nodes",
      "edges",
      "risk_policy",
      "publish"
    ];
    var TOP_LEVEL_ALLOWED = TOP_LEVEL_REQUIRED.concat(["schema", "description"]);
    var NODE_REQUIRED = ["id", "type"];
    var NODE_ALLOWED = ["id", "type", "label", "strategy_id", "params"];
    var EDGE_REQUIRED = ["from", "to", "port"];
    var EDGE_ALLOWED = ["from", "to", "port", "label"];
    var RISK_POLICY_REQUIRED = ["policy_id", "max_position_pct"];
    var RISK_POLICY_ALLOWED = [
      "policy_id",
      "max_position_pct",
      "max_gross_exposure_pct",
      "max_positions",
      "stop_loss_pct",
      "kill_switch",
      "notes"
    ];
    var PUBLISH_REQUIRED = ["enabled"];
    var PUBLISH_ALLOWED = ["enabled", "channel", "visibility", "requires_approval", "notes"];
    var PUBLISH_VISIBILITIES = ["private", "tenant", "public"];
    var EXECUTION_FORBIDDEN_TRUE_FLAGS = ["live", "live_trading", "allow_live_trading", "place_orders"];

    var ID_RE = /^[A-Za-z][A-Za-z0-9_.\-]{0,63}$/;
    var PIPELINE_ID_RE = /^[A-Za-z][A-Za-z0-9_.\-]{0,127}$/;
    var TENANT_RE = /^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$/;
    var VERSION_RE = /^\d+\.\d+\.\d+$/;

    var DEFAULT_HEADER = {
      pipeline_id: "pc-canvas-001",
      version: "1.0.0",
      owner: "quant-classic",
      tenant: "official",
      mode: "SHADOW",
      description: "Canvas MVP 只读组合（不执行、不下单、不发布）"
    };
    var DEFAULT_RISK_POLICY = {
      policy_id: "rp-canvas-default",
      max_position_pct: 10.0,
      max_positions: 30,
      kill_switch: true,
      notes: "Canvas 只读计划：不接执行、不下单、不写服务端"
    };
    var DEFAULT_PUBLISH = {
      enabled: false,
      visibility: "private",
      requires_approval: true,
      notes: "Canvas 只读：不发布、不发 POST"
    };

    /* pipeline_graph.json 不可达时的内置最小 fallback（基线 2 live / 8 shadow）。
     * card=false = registry-only（如 kg_boost 无 manifest 卡），可展示但 composition_ir
     * 的 strategy 节点要求 strategy_id 在 10 张 Strategy Card 内，客户端校验会据此拒绝。 */
    var FALLBACK_STRATEGIES = [
      { id: "kg_boost", status: "live", label: "kg_boost 图谱社区打分", card: false },
      { id: "chan_monitor", status: "live", label: "chan_monitor 盯盘缠论", card: true },
      { id: "trend_follow", status: "shadow", label: "trend_follow 趋势跟随", card: true },
      { id: "boll_breakout", status: "shadow", label: "boll_breakout 布林突破", card: true },
      { id: "rsi_reversal", status: "shadow", label: "rsi_reversal RSI 反转", card: true },
      { id: "ma_cross", status: "shadow", label: "ma_cross 双均线交叉", card: true },
      { id: "chanlun_li122", status: "shadow", label: "chanlun_li122 缠论多级别", card: true },
      { id: "nine_dim", status: "shadow", label: "nine_dim 九维拥挤预警", card: true },
      { id: "qpzm", status: "shadow", label: "qpzm 相对强弱", card: true },
      { id: "rps_resonance", status: "shadow", label: "rps_resonance RPS 共振", card: true }
    ];

    /* pipeline_templates.json 不可达时的内置 3 模板最小 fallback（合法 composition_ir/v1）。
     * template_id 与 pipeline_templates.json 对齐；策略均用 10 张 Strategy Card 内的
     * strategy_id（composition_ir 校验要求），execution 恒 dry_run，publish 不生效。 */
    var FALLBACK_TEMPLATES = [
      {
        template_id: "trend_pullback",
        title: "趋势回踩",
        description: "内置 fallback：trend_follow 单策略 + filter/allocator/risk/execution（只读计划）",
        parameters: {
          tenant: { type: "str", default: "official" },
          weights: { type: "dict", default: { trend_follow: 1.0 } },
          max_single: { type: "float", default: 8.0 }
        },
        ir: {
          schema: SCHEMA,
          pipeline_id: "pc-template-trend-pullback",
          version: "1.0.0",
          owner: "quant-classic",
          tenant: "official",
          mode: "SHADOW",
          description: "模板：趋势回踩（内置 fallback，只读计划）",
          nodes: [
            { id: "u_cn", type: "universe", label: "CN 股票池", params: { markets: ["CN"] } },
            { id: "s_trend", type: "strategy", label: "趋势跟随", strategy_id: "trend_follow", params: {} },
            { id: "f_pullback", type: "filter", label: "回踩/量能过滤", params: { pullback_pct: 3.0 } },
            { id: "a_weight", type: "allocator", label: "加权分配", params: { scheme: "weighted", max_single_pct: 8.0 } },
            { id: "k_risk", type: "risk", label: "风控闸", params: {} },
            { id: "x_paper", type: "execution", label: "纸面执行（dry-run）", params: { book: "official", dry_run: true } }
          ],
          edges: [
            { from: "u_cn", to: "s_trend", port: "universe" },
            { from: "s_trend", to: "f_pullback", port: "signals" },
            { from: "f_pullback", to: "a_weight", port: "signals" },
            { from: "a_weight", to: "k_risk", port: "allocations" },
            { from: "k_risk", to: "x_paper", port: "allocations" }
          ],
          risk_policy: { policy_id: "rp-trend-pullback", max_position_pct: 8.0, max_positions: 30, kill_switch: true },
          publish: { enabled: false, visibility: "private", requires_approval: true }
        }
      },
      {
        template_id: "trend_reversal",
        title: "趋势+反转",
        description: "内置 fallback：trend_follow + rsi_reversal 双信号源 → allocator → risk → execution（只读计划）",
        parameters: {
          tenant: { type: "str", default: "official" },
          weights: { type: "dict", default: { trend_follow: 0.5, rsi_reversal: 0.5 } },
          max_single: { type: "float", default: 10.0 }
        },
        ir: {
          schema: SCHEMA,
          pipeline_id: "pc-template-trend-reversal",
          version: "1.0.0",
          owner: "quant-classic",
          tenant: "official",
          mode: "SHADOW",
          description: "模板：趋势+反转（内置 fallback，只读计划）",
          nodes: [
            { id: "u_cn", type: "universe", label: "CN 股票池", params: { markets: ["CN"] } },
            { id: "s_trend", type: "strategy", label: "趋势跟随", strategy_id: "trend_follow", params: {} },
            { id: "s_rsi", type: "strategy", label: "RSI 反转", strategy_id: "rsi_reversal", params: {} },
            { id: "a_weight", type: "allocator", label: "加权分配", params: { scheme: "weighted" } },
            { id: "k_risk", type: "risk", label: "风控闸", params: {} },
            { id: "x_paper", type: "execution", label: "纸面执行（dry-run）", params: { book: "official", dry_run: true } }
          ],
          edges: [
            { from: "u_cn", to: "s_trend", port: "universe" },
            { from: "u_cn", to: "s_rsi", port: "universe" },
            { from: "s_trend", to: "a_weight", port: "signals" },
            { from: "s_rsi", to: "a_weight", port: "signals" },
            { from: "a_weight", to: "k_risk", port: "allocations" },
            { from: "k_risk", to: "x_paper", port: "allocations" }
          ],
          risk_policy: { policy_id: "rp-trend-reversal", max_position_pct: 10.0, max_positions: 30, kill_switch: true },
          publish: { enabled: false, visibility: "private", requires_approval: true }
        }
      },
      {
        template_id: "nine_dim_chan",
        title: "九维+缠论",
        description: "内置 fallback：nine_dim + chan_monitor + chanlun_li122 三信号源 → allocator → risk → execution（只读计划）",
        parameters: {
          tenant: { type: "str", default: "official" },
          weights: { type: "dict", default: { nine_dim: 0.5, chan_monitor: 0.3, chanlun_li122: 0.2 } },
          max_single: { type: "float", default: 10.0 }
        },
        ir: {
          schema: SCHEMA,
          pipeline_id: "pc-template-nine-dim-chan",
          version: "1.0.0",
          owner: "quant-strategy-layer",
          tenant: "official",
          mode: "SHADOW",
          description: "模板：九维+缠论（内置 fallback，只读计划）",
          nodes: [
            { id: "u_cn", type: "universe", label: "CN 股票池", params: { markets: ["CN"] } },
            { id: "s_nine", type: "strategy", label: "九维拥挤预警", strategy_id: "nine_dim", params: {} },
            { id: "s_chan", type: "strategy", label: "盯盘缠论", strategy_id: "chan_monitor", params: {} },
            { id: "s_li", type: "strategy", label: "缠论多级别", strategy_id: "chanlun_li122", params: {} },
            { id: "a_multi", type: "allocator", label: "多信号加权分配", params: { scheme: "weighted" } },
            { id: "k_risk", type: "risk", label: "风控闸", params: {} },
            { id: "x_paper", type: "execution", label: "纸面执行（dry-run）", params: { book: "official", dry_run: true } }
          ],
          edges: [
            { from: "u_cn", to: "s_nine", port: "universe" },
            { from: "u_cn", to: "s_chan", port: "universe" },
            { from: "u_cn", to: "s_li", port: "universe" },
            { from: "s_nine", to: "a_multi", port: "signals" },
            { from: "s_chan", to: "a_multi", port: "signals" },
            { from: "s_li", to: "a_multi", port: "signals" },
            { from: "a_multi", to: "k_risk", port: "allocations" },
            { from: "k_risk", to: "x_paper", port: "allocations" }
          ],
          risk_policy: { policy_id: "rp-nine-dim-chan", max_position_pct: 10.0, max_positions: 30, kill_switch: true },
          publish: { enabled: false, visibility: "private", requires_approval: true }
        }
      }
    ];

    var NODE_W = 200;
    var NODE_H = 70;
    var ID_PREFIX = {
      universe: "u",
      strategy: "s",
      filter: "f",
      rank: "r",
      allocator: "a",
      risk: "k",
      execution: "x",
      agent_team: "g"
    };

    /* ══════════════════════════════════════════════════════════════════
     * 2. 小工具
     * ══════════════════════════════════════════════════════════════════ */
    function copy(value) {
      return JSON.parse(JSON.stringify(value));
    }

    function isNonemptyStr(value) {
      return typeof value === "string" && value.trim() !== "";
    }

    function isNumber(value) {
      return typeof value === "number" && isFinite(value);
    }

    function isPositiveInt(value) {
      return typeof value === "number" && isFinite(value) && value > 0 && Math.floor(value) === value;
    }

    function isPlainObject(value) {
      return !!value && typeof value === "object" && !Array.isArray(value);
    }

    function stringList(value) {
      if (!Array.isArray(value) || !value.length) return null;
      for (var i = 0; i < value.length; i += 1) {
        if (!isNonemptyStr(value[i])) return null;
      }
      return value.map(function (item) {
        return String(item);
      });
    }

    function outputPortOf(type) {
      var keys = Object.keys(NODE_SPECS[type].outputs);
      return keys.length ? keys[0] : null;
    }

    function acceptsPort(type, portType) {
      var inputs = NODE_SPECS[type].inputs;
      return Object.keys(inputs).some(function (name) {
        return inputs[name] === portType;
      });
    }

    /* ══════════════════════════════════════════════════════════════════
     * 3. 纯函数：canConnect / buildIR / validateIR
     * ══════════════════════════════════════════════════════════════════ */
    /**
     * 画布连线可行性：返回 {ok, port?, reason?}。
     * 口径同 composition_ir._validate_edges：port=上游输出端口名，下游按端口类型接受；
     * 禁止自环 / 重复边 / 下游不接受该端口类型。
     */
    function canConnect(fromNode, toNode, edges) {
      if (!fromNode || !toNode) return { ok: false, reason: "节点不存在" };
      if (fromNode.id === toNode.id) return { ok: false, reason: "禁止自环：不能连到自己" };
      if (NODE_TYPES.indexOf(fromNode.type) < 0 || NODE_TYPES.indexOf(toNode.type) < 0) {
        return { ok: false, reason: "节点类型非法" };
      }
      var port = outputPortOf(fromNode.type);
      if (!port) return { ok: false, reason: fromNode.type + " 无输出端口" };
      var portType = NODE_SPECS[fromNode.type].outputs[port];
      if (!acceptsPort(toNode.type, portType)) {
        return {
          ok: false,
          reason:
            toNode.type +
            " 不接受 " +
            portType +
            " 端口（接受: " +
            Object.keys(NODE_SPECS[toNode.type].inputs).join(", ") +
            "）"
        };
      }
      var duplicated = (edges || []).some(function (edge) {
        return edge.from === fromNode.id && edge.to === toNode.id && edge.port === port;
      });
      if (duplicated) return { ok: false, reason: "重复边：" + fromNode.id + " → " + toNode.id };
      return { ok: true, port: port };
    }

    /** 由画布图 {nodes:[{id,type,label,strategy_id,params}], edges:[{from,to,port}]} 生成 IR。 */
    /**
     * 画布图 → Composition IR。
     * options（Phase 5-C 可选）：{risk_policy, publish} 覆写默认只读口径；
     * 缺省时 risk_policy=DEFAULT_RISK_POLICY（旧调用/旧测试口径不变）。
     */
    function buildIR(graph, header, options) {
      var head = {};
      var source = header || DEFAULT_HEADER;
      TOP_LEVEL_REQUIRED.forEach(function (key) {
        head[key] = source[key];
      });
      if (typeof source.description === "string" && source.description) {
        head.description = source.description;
      }
      var nodes = ((graph && graph.nodes) || [])
        .map(function (node) {
          var out = { id: node.id, type: node.type, params: copy(node.params || {}) };
          if (node.label) out.label = node.label;
          if (node.type === "strategy") out.strategy_id = node.strategy_id;
          return out;
        })
        .sort(function (a, b) {
          return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
        });
      var edges = ((graph && graph.edges) || [])
        .map(function (edge) {
          var out = { from: edge.from, to: edge.to, port: edge.port };
          if (edge.label) out.label = edge.label;
          return out;
        })
        .sort(function (a, b) {
          var left = a.from + "\u0000" + a.to + "\u0000" + a.port;
          var right = b.from + "\u0000" + b.to + "\u0000" + b.port;
          return left < right ? -1 : left > right ? 1 : 0;
        });
      var riskPolicy =
        options && isPlainObject(options.risk_policy)
          ? copy(options.risk_policy)
          : copy(DEFAULT_RISK_POLICY);
      var publish =
        options && isPlainObject(options.publish)
          ? copy(options.publish)
          : copy(DEFAULT_PUBLISH);
      var ir = {
        schema: SCHEMA,
        pipeline_id: head.pipeline_id,
        version: head.version,
        owner: head.owner,
        tenant: head.tenant,
        mode: head.mode,
        nodes: nodes,
        edges: edges,
        risk_policy: riskPolicy,
        publish: publish
      };
      if (head.description) ir.description = head.description;
      return ir;
    }

    function validateNodeParams(node, params, errors) {
      if (!isPlainObject(params)) {
        errors.push("节点 " + node.id + "(" + node.type + ") 的 params 必须为 dict");
        return;
      }
      if (node.type === "universe") {
        if ("markets" in params && stringList(params.markets) === null) {
          errors.push("节点 " + node.id + " 的 params.markets 必须为非空无重复字符串 list");
        }
      } else if (node.type === "agent_team") {
        if ("team" in params && stringList(params.team) === null) {
          errors.push("节点 " + node.id + " 的 params.team 必须为非空字符串 list");
        }
        if ("role" in params && !isNonemptyStr(params.role)) {
          errors.push("节点 " + node.id + " 的 params.role 必须为非空字符串");
        }
      } else if (node.type === "execution") {
        if ("book" in params && !isNonemptyStr(params.book)) {
          errors.push("节点 " + node.id + " 的 params.book 必须为非空字符串");
        }
        if ("dry_run" in params && params.dry_run !== true) {
          errors.push("节点 " + node.id + " 为只读计划：execution.params.dry_run 不得为 false");
        }
        EXECUTION_FORBIDDEN_TRUE_FLAGS.forEach(function (flag) {
          if (params[flag] === true) {
            errors.push("节点 " + node.id + " 为只读计划：不得声明实盘/下单参数 " + flag + "=true");
          }
        });
      }
    }

    function detectCycle(nodeIds, edges) {
      var adjacency = {};
      var indegree = {};
      nodeIds.forEach(function (id) {
        adjacency[id] = [];
        indegree[id] = 0;
      });
      edges.forEach(function (edge) {
        if (adjacency[edge.from] && adjacency[edge.to]) {
          adjacency[edge.from].push(edge.to);
          indegree[edge.to] += 1;
        }
      });
      var ready = nodeIds.filter(function (id) {
        return indegree[id] === 0;
      }).sort();
      var order = [];
      while (ready.length) {
        var current = ready.shift();
        order.push(current);
        adjacency[current].forEach(function (child) {
          indegree[child] -= 1;
          if (indegree[child] === 0) ready.push(child);
        });
        ready.sort();
      }
      if (order.length !== nodeIds.length) {
        var remaining = nodeIds.filter(function (id) {
          return indegree[id] > 0;
        }).sort();
        return { hasCycle: true, remaining: remaining, order: order };
      }
      return { hasCycle: false, remaining: [], order: order };
    }

    /**
     * 客户端快速校验（composition_ir/v1 契约的镜像子集，权威校验仍以 Python 为准）。
     * options.strategyIds：允许的 strategy_id 清单（来自 pipeline_graph.json 或内置 fallback）。
     * 返回 {ok, errors, warnings, node_count, edge_count, schema, topological_order}。
     */
    function validateIR(ir, options) {
      var opts = options || {};
      var errors = [];
      var warnings = [];
      if (!isPlainObject(ir)) {
        return { ok: false, errors: ["IR 必须为 JSON 对象"], warnings: [], node_count: 0, edge_count: 0, schema: SCHEMA };
      }
      /* ── 顶层 ── */
      Object.keys(ir).forEach(function (key) {
        if (TOP_LEVEL_ALLOWED.indexOf(key) < 0) errors.push("IR 含未知顶层字段: " + key);
      });
      TOP_LEVEL_REQUIRED.forEach(function (key) {
        if (!(key in ir)) errors.push("IR 缺必填字段: " + key);
      });
      var schema = ir.schema === undefined ? SCHEMA : ir.schema;
      if (!isNonemptyStr(schema) || schema !== SCHEMA) {
        errors.push("不支持的 IR schema 版本: " + JSON.stringify(schema) + "（支持: " + SCHEMA + "）");
      }
      if (!isNonemptyStr(ir.pipeline_id) || !PIPELINE_ID_RE.test(String(ir.pipeline_id))) {
        errors.push("pipeline_id 必须为合法标识符（字母开头，允许 . _ -）: " + JSON.stringify(ir.pipeline_id));
      }
      if (typeof ir.version !== "string" || !VERSION_RE.test(ir.version)) {
        errors.push("pipeline version 必须为 x.y.z 形式: " + JSON.stringify(ir.version));
      }
      if (!isNonemptyStr(ir.owner)) errors.push("owner 必须为非空字符串");
      if (!isNonemptyStr(ir.tenant) || !TENANT_RE.test(String(ir.tenant))) {
        errors.push("tenant 必须为合法标识符: " + JSON.stringify(ir.tenant));
      }
      if (ALLOWED_MODES.indexOf(ir.mode) < 0) {
        errors.push("mode 非法: " + JSON.stringify(ir.mode) + "（允许: " + ALLOWED_MODES.join(", ") + "）");
      }
      if ("description" in ir && typeof ir.description !== "string") {
        errors.push("description 必须为字符串");
      }
      if (!isPlainObject(ir.risk_policy)) errors.push("IR.risk_policy 必须为 dict");
      if (!isPlainObject(ir.publish)) errors.push("IR.publish 必须为 dict");

      /* ── 节点 ── */
      var nodes = Array.isArray(ir.nodes) ? ir.nodes : [];
      if (!Array.isArray(ir.nodes) || !nodes.length) errors.push("IR.nodes 必须为非空 list");
      var nodeMap = {};
      var nodeIds = [];
      var typeCounts = {};
      nodes.forEach(function (node, index) {
        var where = "nodes[" + index + "]";
        if (!isPlainObject(node)) {
          errors.push(where + " 必须为 dict");
          return;
        }
        Object.keys(node).forEach(function (key) {
          if (NODE_ALLOWED.indexOf(key) < 0) errors.push(where + " 含未知字段: " + key);
        });
        NODE_REQUIRED.forEach(function (key) {
          if (!(key in node)) errors.push(where + " 缺必填字段: " + key);
        });
        if (!isNonemptyStr(node.id)) {
          errors.push(where + " 的 id 必须为非空字符串");
          return;
        }
        if (nodeMap[node.id]) {
          errors.push("节点 id 重复: " + node.id);
          return;
        }
        if (!ID_RE.test(node.id)) errors.push("节点 id 非法: " + node.id);
        if (NODE_TYPES.indexOf(node.type) < 0) {
          errors.push("节点 " + node.id + " 类型非法: " + JSON.stringify(node.type));
          return;
        }
        if ("label" in node && typeof node.label !== "string") {
          errors.push("节点 " + node.id + " 的 label 必须为字符串");
        }
        var params = "params" in node ? node.params : {};
        if (!isPlainObject(params)) {
          errors.push("节点 " + node.id + " 的 params 必须为 dict");
          params = {};
        }
        nodeMap[node.id] = node;
        nodeIds.push(node.id);
        typeCounts[node.type] = (typeCounts[node.type] || 0) + 1;
        if (node.type === "strategy") {
          if (!isNonemptyStr(node.strategy_id)) {
            errors.push("节点 " + node.id + "（strategy）缺 strategy_id");
          } else if (
            Array.isArray(opts.strategyIds) &&
            opts.strategyIds.length &&
            opts.strategyIds.indexOf(node.strategy_id) < 0
          ) {
            errors.push("节点 " + node.id + " 的 strategy_id 不在 Graph JSON live/shadow 清单: " + node.strategy_id);
          }
          if (Object.keys(params).length) {
            warnings.push(
              "节点 " + node.id + " 带 strategy params（" + Object.keys(params).join(", ") + "）：Canvas 不做卡片级参数编辑，请用权威校验复核"
            );
          }
        } else {
          validateNodeParams(node, params, errors);
        }
      });

      /* ── 边 ── */
      var edges = Array.isArray(ir.edges) ? ir.edges : [];
      if (!Array.isArray(ir.edges) || !edges.length) errors.push("IR.edges 必须为非空 list");
      var seenEdges = {};
      var validEdges = [];
      edges.forEach(function (edge, index) {
        var where = "edges[" + index + "]";
        if (!isPlainObject(edge)) {
          errors.push(where + " 必须为 dict");
          return;
        }
        Object.keys(edge).forEach(function (key) {
          if (EDGE_ALLOWED.indexOf(key) < 0) errors.push(where + " 含未知字段: " + key);
        });
        var missing = EDGE_REQUIRED.filter(function (key) {
          return !(key in edge);
        });
        if (missing.length) {
          errors.push(where + " 缺必填字段: " + missing.join(", "));
          return;
        }
        var bad = EDGE_REQUIRED.filter(function (key) {
          return !isNonemptyStr(edge[key]);
        });
        if (bad.length) {
          errors.push(where + " 字段必须为非空字符串: " + bad.join(", "));
          return;
        }
        if ("label" in edge && typeof edge.label !== "string") {
          errors.push(where + " 的 label 必须为字符串");
        }
        var key = edge.from + "\u0000" + edge.to + "\u0000" + edge.port;
        if (seenEdges[key]) {
          errors.push("重复边: " + edge.from + " --" + edge.port + "--> " + edge.to);
          return;
        }
        seenEdges[key] = true;
        var source = nodeMap[edge.from];
        var target = nodeMap[edge.to];
        if (!source) {
          errors.push(where + " 的 from 节点不存在: " + edge.from);
          return;
        }
        if (!target) {
          errors.push(where + " 的 to 节点不存在: " + edge.to);
          return;
        }
        if (edge.from === edge.to) {
          errors.push("自环边不允许: 节点 " + edge.from + " --" + edge.port + "--> 自身");
          return;
        }
        var sourceOutputs = NODE_SPECS[source.type].outputs;
        if (Object.keys(sourceOutputs).indexOf(edge.port) < 0) {
          errors.push("端口非法: 节点 " + edge.from + "(" + source.type + ") 无输出端口 " + edge.port);
          return;
        }
        var portType = sourceOutputs[edge.port];
        if (!acceptsPort(target.type, portType)) {
          errors.push(
            "端口类型不兼容: " + edge.from + "." + edge.port + "(" + portType + ") → " + edge.to + "(" + target.type + ") 不接受类型 " + portType
          );
          return;
        }
        validEdges.push(edge);
      });

      /* ── 图结构（同 composition_ir._validate_structure） ── */
      if (nodeIds.length) {
        if (!typeCounts.universe) errors.push("图必须至少含 1 个 universe 节点");
        if (!typeCounts.strategy) errors.push("图必须至少含 1 个 strategy 节点");
        var incoming = {};
        var outgoing = {};
        validEdges.forEach(function (edge) {
          (incoming[edge.to] || (incoming[edge.to] = [])).push(edge);
          (outgoing[edge.from] || (outgoing[edge.from] = [])).push(edge);
        });
        nodeIds.forEach(function (id) {
          var node = nodeMap[id];
          var hasIn = !!(incoming[id] && incoming[id].length);
          var hasOut = !!(outgoing[id] && outgoing[id].length);
          if (node.type === "universe") {
            if (hasIn) errors.push("universe 节点 " + id + " 不得有入边");
            if (!hasOut) errors.push("universe 节点 " + id + " 必须有出边");
          } else {
            if (!hasIn) errors.push("节点 " + id + "(" + node.type + ") 缺入边（非 universe 节点必须有上游）");
            if (TERMINAL_NODE_TYPES.indexOf(node.type) < 0 && !hasOut) {
              errors.push("节点 " + id + "(" + node.type + ") 缺出边（该类型不是终止节点）");
            }
          }
        });
      }

      /* ── risk_policy ── */
      if (isPlainObject(ir.risk_policy)) {
        var risk = ir.risk_policy;
        Object.keys(risk).forEach(function (key) {
          if (RISK_POLICY_ALLOWED.indexOf(key) < 0) errors.push("risk_policy 含未知字段: " + key);
        });
        var riskMissing = RISK_POLICY_REQUIRED.filter(function (key) {
          return !(key in risk);
        });
        if (riskMissing.length) errors.push("risk_policy 缺必填字段: " + riskMissing.join(", "));
        if ("policy_id" in risk && !isNonemptyStr(risk.policy_id)) {
          errors.push("risk_policy.policy_id 必须为非空字符串");
        }
        if ("max_position_pct" in risk) {
          if (!isNumber(risk.max_position_pct) || !(risk.max_position_pct > 0 && risk.max_position_pct <= 100)) {
            errors.push("risk_policy.max_position_pct 必须为 (0, 100] 内的数值");
          }
        }
        if ("max_positions" in risk && !isPositiveInt(risk.max_positions)) {
          errors.push("risk_policy.max_positions 必须为正整数");
        }
        if ("max_gross_exposure_pct" in risk && (!isNumber(risk.max_gross_exposure_pct) || risk.max_gross_exposure_pct <= 0)) {
          errors.push("risk_policy.max_gross_exposure_pct 必须为正数");
        }
        if ("stop_loss_pct" in risk && (!isNumber(risk.stop_loss_pct) || !(risk.stop_loss_pct > 0 && risk.stop_loss_pct <= 100))) {
          errors.push("risk_policy.stop_loss_pct 必须为 (0, 100] 内的数值");
        }
        if ("kill_switch" in risk && typeof risk.kill_switch !== "boolean") {
          errors.push("risk_policy.kill_switch 必须为 bool");
        }
        if ("notes" in risk && typeof risk.notes !== "string") {
          errors.push("risk_policy.notes 必须为字符串");
        }
      }

      /* ── publish ── */
      if (isPlainObject(ir.publish)) {
        var publish = ir.publish;
        Object.keys(publish).forEach(function (key) {
          if (PUBLISH_ALLOWED.indexOf(key) < 0) errors.push("publish 含未知字段: " + key);
        });
        var publishMissing = PUBLISH_REQUIRED.filter(function (key) {
          return !(key in publish);
        });
        if (publishMissing.length) errors.push("publish 缺必填字段: " + publishMissing.join(", "));
        if ("enabled" in publish && typeof publish.enabled !== "boolean") {
          errors.push("publish.enabled 必须为 bool");
        }
        if ("visibility" in publish && PUBLISH_VISIBILITIES.indexOf(publish.visibility) < 0) {
          errors.push("publish.visibility 非法: " + JSON.stringify(publish.visibility));
        }
        if (publish.enabled === true && !isNonemptyStr(publish.channel)) {
          errors.push("publish 启用时 channel 必须为非空字符串");
        }
        if ("channel" in publish && typeof publish.channel !== "string") {
          errors.push("publish.channel 必须为字符串");
        }
        if ("requires_approval" in publish && typeof publish.requires_approval !== "boolean") {
          errors.push("publish.requires_approval 必须为 bool");
        }
        if ("notes" in publish && typeof publish.notes !== "string") {
          errors.push("publish.notes 必须为字符串");
        }
      }

      /* ── DAG 无环（对结构合法的边做 Kahn 拓扑排序） ── */
      var order = [];
      if (nodeIds.length) {
        var cycle = detectCycle(nodeIds, validEdges);
        if (cycle.hasCycle) {
          errors.push("DAG 校验失败：图存在环（无法拓扑排序），涉及节点: " + cycle.remaining.join(", "));
        } else {
          order = cycle.order;
        }
      }

      return {
        ok: errors.length === 0,
        errors: errors,
        warnings: warnings,
        node_count: nodes.length,
        edge_count: edges.length,
        schema: SCHEMA,
        topological_order: order
      };
    }

    /* ══════════════════════════════════════════════════════════════════
     * 3b. Phase 4-B：模板库解析 / 画布转换（纯函数，Node 测试可直接 require）
     * ══════════════════════════════════════════════════════════════════ */
    /** 解析 pipeline_templates.json（pipeline_templates/v1）→ 规范化模板列表。 */
    function parseTemplatesPayload(payload) {
      if (!isPlainObject(payload) || !Array.isArray(payload.templates)) return [];
      var parsed = [];
      payload.templates.forEach(function (item) {
        if (!isPlainObject(item) || !isNonemptyStr(item.template_id) || !isPlainObject(item.ir)) return;
        if (!Array.isArray(item.ir.nodes) || !item.ir.nodes.length) return;
        if (!Array.isArray(item.ir.edges) || !item.ir.edges.length) return;
        parsed.push({
          template_id: item.template_id,
          title: isNonemptyStr(item.title) ? item.title : item.template_id,
          description: typeof item.description === "string" ? item.description : "",
          ir: copy(item.ir)
        });
      });
      return parsed;
    }

    /** 解析成功用文件模板；失败（null/非法/空）回落内置 3 模板，不崩。 */
    function selectTemplates(payload) {
      var parsed = parseTemplatesPayload(payload);
      if (parsed.length) {
        return { templates: parsed, source: TEMPLATES_URL, from_fallback: false };
      }
      return {
        templates: copy(FALLBACK_TEMPLATES),
        source: "内置 fallback（" + TEMPLATES_URL + " 不可用或无有效模板）",
        from_fallback: true
      };
    }

    /** 模板节点自动布局：按最长路径分层（x=层号、y=层内序号），仅内存。 */
    function layoutTemplateNodes(nodes, edges) {
      var indegree = {};
      var adjacency = {};
      nodes.forEach(function (node) {
        indegree[node.id] = 0;
        adjacency[node.id] = [];
      });
      edges.forEach(function (edge) {
        if (!(edge.from in adjacency) || !(edge.to in indegree)) return;
        adjacency[edge.from].push(edge.to);
        indegree[edge.to] += 1;
      });
      var depth = {};
      var queue = [];
      nodes.forEach(function (node) {
        if (indegree[node.id] === 0) {
          depth[node.id] = 0;
          queue.push(node.id);
        }
      });
      var guard = 0;
      while (queue.length && guard < nodes.length * 4) {
        guard += 1;
        var current = queue.shift();
        adjacency[current].forEach(function (nextId) {
          depth[nextId] = Math.max(depth[nextId] || 0, (depth[current] || 0) + 1);
          indegree[nextId] -= 1;
          if (indegree[nextId] === 0) queue.push(nextId);
        });
      }
      var perDepth = {};
      nodes.forEach(function (node) {
        var level = depth[node.id] || 0;
        var index = perDepth[level] || 0;
        perDepth[level] = index + 1;
        node.x = 40 + level * (NODE_W + 60);
        node.y = 40 + index * (NODE_H + 40);
      });
    }

    /** 模板 IR → 画布状态（nodes/edges/header）；不改模板本体、不写任何文件。 */
    function templateToCanvas(template) {
      var ir = template && template.ir;
      if (!isPlainObject(ir) || !Array.isArray(ir.nodes) || !ir.nodes.length) {
        throw new Error("模板 IR 非法（缺 nodes）: " + (template ? template.template_id : "?"));
      }
      if (!Array.isArray(ir.edges) || !ir.edges.length) {
        throw new Error("模板 IR 非法（缺 edges）: " + (template ? template.template_id : "?"));
      }
      var header = {
        pipeline_id: isNonemptyStr(ir.pipeline_id) ? ir.pipeline_id : DEFAULT_HEADER.pipeline_id,
        version: isNonemptyStr(ir.version) ? ir.version : DEFAULT_HEADER.version,
        owner: isNonemptyStr(ir.owner) ? ir.owner : DEFAULT_HEADER.owner,
        tenant: isNonemptyStr(ir.tenant) ? ir.tenant : DEFAULT_HEADER.tenant,
        mode: ALLOWED_MODES.indexOf(ir.mode) >= 0 ? ir.mode : DEFAULT_HEADER.mode,
        description: typeof ir.description === "string" ? ir.description : ""
      };
      var nodes = ir.nodes.map(function (node) {
        if (!isPlainObject(node) || !isNonemptyStr(node.id) || NODE_TYPES.indexOf(node.type) < 0) {
          throw new Error("模板节点非法: " + JSON.stringify(node));
        }
        return {
          id: node.id,
          type: node.type,
          label: isNonemptyStr(node.label) ? node.label : node.id,
          strategy_id: node.type === "strategy" ? node.strategy_id : null,
          params: copy(node.params || {}),
          x: 0,
          y: 0
        };
      });
      var edges = ir.edges.map(function (edge, index) {
        if (!isPlainObject(edge) || !isNonemptyStr(edge.from) || !isNonemptyStr(edge.to) || !isNonemptyStr(edge.port)) {
          throw new Error("模板边非法: " + JSON.stringify(edge));
        }
        return { id: "e" + (index + 1), from: edge.from, to: edge.to, port: edge.port };
      });
      layoutTemplateNodes(nodes, edges);
      return { nodes: nodes, edges: edges, header: header, params: parseTemplateParameters(template) };
    }

    /* ══════════════════════════════════════════════════════════════════
     * 3c. Phase 5-C：模板参数（tenant / 每策略 weights / max_single）
     *     —— 纯函数，浏览器与 Node 测试共用；只改内存 IR，不写服务端。
     *     max_single 在 Canvas 内统一为归一化 (0,1]；写入 IR 时 ×100 变成
     *     risk_policy.max_position_pct / allocator.params.max_single_pct 的百分数口径。
     * ══════════════════════════════════════════════════════════════════ */
    /** 模板 IR 里的策略 id（去重，保持出现顺序）——参数区按此生成权重输入。 */
    function templateStrategyIds(template) {
      var ir = template && template.ir;
      var ids = [];
      ((ir && ir.nodes) || []).forEach(function (node) {
        if (node && node.type === "strategy" && isNonemptyStr(node.strategy_id) && ids.indexOf(node.strategy_id) < 0) {
          ids.push(node.strategy_id);
        }
      });
      return ids;
    }

    /** 模板参数默认值 → Canvas 参数（max_single 归一化到 (0,1]）。 */
    function parseTemplateParameters(template) {
      var ir = (template && template.ir) || {};
      var raw = template && template.parameters;
      var tenant =
        raw && raw.tenant && isNonemptyStr(raw.tenant.default)
          ? raw.tenant.default
          : isNonemptyStr(ir.tenant)
            ? ir.tenant
            : DEFAULT_HEADER.tenant;
      var weights = null;
      if (raw && raw.weights && isPlainObject(raw.weights.default)) {
        weights = copy(raw.weights.default);
      }
      if (!isPlainObject(weights) || !Object.keys(weights).length) {
        weights = {};
        ((ir && ir.nodes) || []).forEach(function (node) {
          if (
            node &&
            node.type === "allocator" &&
            isPlainObject(node.params) &&
            isPlainObject(node.params.weights) &&
            Object.keys(node.params.weights).length &&
            !Object.keys(weights).length
          ) {
            weights = copy(node.params.weights);
          }
        });
      }
      if (!isPlainObject(weights) || !Object.keys(weights).length) {
        var strategyIds = templateStrategyIds(template);
        var equal = strategyIds.length ? 1 / strategyIds.length : 0;
        strategyIds.forEach(function (sid) {
          weights[sid] = Math.round(equal * 1000) / 1000;
        });
      }
      var maxPct =
        raw && raw.max_single && isNumber(raw.max_single.default)
          ? raw.max_single.default
          : ir.risk_policy && isNumber(ir.risk_policy.max_position_pct)
            ? ir.risk_policy.max_position_pct
            : 10.0;
      var maxSingle = maxPct / 100;
      return { tenant: String(tenant), weights: weights, max_single: maxSingle, max_single_pct: maxPct };
    }

    /** Canvas 模板参数校验：权重和 ∈ (0,1]、max_single ∈ (0,1]、tenant 合法。 */
    function validateTemplateParams(params) {
      var errors = [];
      var warnings = [];
      if (!isPlainObject(params)) {
        return { ok: false, errors: ["模板参数必须为 dict"], warnings: [], weights_sum: null, max_single: null };
      }
      var tenant = params.tenant;
      if (!isNonemptyStr(tenant)) {
        errors.push("tenant 必须为非空字符串");
      } else if (!TENANT_RE.test(String(tenant))) {
        errors.push("tenant 必须为合法标识符（字母/数字开头，允许 . _ -）: " + JSON.stringify(tenant));
      }
      var weights = params.weights;
      var sum = null;
      if (!isPlainObject(weights) || !Object.keys(weights).length) {
        errors.push("策略权重 weights 必须为非空 dict（至少 1 个策略）");
      } else {
        sum = 0;
        Object.keys(weights).forEach(function (sid) {
          var value = weights[sid];
          if (!isNonemptyStr(sid)) {
            errors.push("策略权重键必须为非空 strategy_id");
            return;
          }
          if (!isNumber(value)) {
            errors.push("策略权重 " + sid + " 必须为数值: " + JSON.stringify(value));
            return;
          }
          if (value < 0) {
            errors.push("策略权重 " + sid + " 不得为负数: " + value);
            return;
          }
          sum += value;
        });
        if (sum !== null && !(sum > 0)) {
          errors.push("策略权重和必须 > 0（当前 Σw = " + sum + "）");
        } else if (sum !== null && sum > 1 + 1e-9) {
          errors.push("策略权重和必须 ≤ 1（当前 Σw = " + Math.round(sum * 1000) / 1000 + "）");
        }
        if (sum !== null && Math.abs(sum - 1) > 1e-9) {
          warnings.push("策略权重和未归一化（Σw = " + Math.round(sum * 1000) / 1000 + "，允许 (0,1]）");
        }
      }
      var maxSingle = params.max_single;
      if (!isNumber(maxSingle)) {
        errors.push("max_single 必须为数值（归一化 (0,1] 口径）: " + JSON.stringify(maxSingle));
      } else if (!(maxSingle > 0) || maxSingle > 1) {
        errors.push("max_single 必须 ∈ (0,1]（当前 " + maxSingle + "；写入 IR 时 ×100 为百分数）");
      }
      return {
        ok: errors.length === 0,
        errors: errors,
        warnings: warnings,
        weights_sum: sum,
        max_single: isNumber(maxSingle) ? maxSingle : null
      };
    }

    /**
     * 应用模板参数到画布图/header → 返回 {nodes, edges, header, options}：
     * - header.tenant ← params.tenant；
     * - 每个 allocator 节点 params.weights ← 权重表，params.max_single_pct ← max_single×100；
     * - risk_policy.max_position_pct ← max_single×100（仅内存，publish 保持不生效）。
     */
    function applyTemplateParams(graph, header, params) {
      var nodes = ((graph && graph.nodes) || []).map(copy);
      var edges = ((graph && graph.edges) || []).map(copy);
      var outHeader = cloneHeader(header || DEFAULT_HEADER);
      var riskPolicy = copy(DEFAULT_RISK_POLICY);
      if (isPlainObject(params)) {
        if (isNonemptyStr(params.tenant)) outHeader.tenant = params.tenant;
        var maxPct = null;
        if (isNumber(params.max_single)) {
          maxPct = Math.round(params.max_single * 100 * 100) / 100;
          riskPolicy.max_position_pct = maxPct;
        }
        nodes.forEach(function (node) {
          if (node.type !== "allocator") return;
          node.params = copy(node.params || {});
          if (!isNonemptyStr(node.params.scheme)) node.params.scheme = "weighted";
          if (isPlainObject(params.weights)) node.params.weights = copy(params.weights);
          if (maxPct !== null) node.params.max_single_pct = maxPct;
        });
      }
      return { nodes: nodes, edges: edges, header: outHeader, options: { risk_policy: riskPolicy, publish: copy(DEFAULT_PUBLISH) } };
    }

    /* ══════════════════════════════════════════════════════════════════
     * 4. DOM 层（浏览器 only；Node require 时不会执行）
     * ══════════════════════════════════════════════════════════════════ */
    var state = null;
    var dom = {};

    function $(id) {
      return document.getElementById(id);
    }

    function escHtml(value) {
      var div = document.createElement("div");
      div.textContent = value === null || value === undefined ? "" : String(value);
      return div.innerHTML;
    }

    function attr(value) {
      return String(value === null || value === undefined ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    function newState() {
      return {
        nodes: [],
        edges: [],
        nextNode: 1,
        nextEdge: 1,
        selection: { kind: null, id: null },
        drag: null,
        link: null,
        header: copy(DEFAULT_HEADER),
        strategies: FALLBACK_STRATEGIES.slice(),
        graphSource: "内置 fallback（pipeline_graph.json 加载中…）",
        templates: copy(FALLBACK_TEMPLATES),
        templateSource: "内置 fallback（pipeline_templates.json 加载中…）",
        template: null,
        templateParams: null,
        lastIR: null,
        booted: false
      };
    }

    function cloneHeader(header) {
      return {
        pipeline_id: header.pipeline_id,
        version: header.version,
        owner: header.owner,
        tenant: header.tenant,
        mode: header.mode,
        description: header.description
      };
    }

    function nodeById(id) {
      for (var i = 0; i < state.nodes.length; i += 1) {
        if (state.nodes[i].id === id) return state.nodes[i];
      }
      return null;
    }

    function edgeById(id) {
      for (var i = 0; i < state.edges.length; i += 1) {
        if (state.edges[i].id === id) return state.edges[i];
      }
      return null;
    }

    function strategyById(id) {
      for (var i = 0; i < state.strategies.length; i += 1) {
        if (state.strategies[i].id === id) return state.strategies[i];
      }
      return null;
    }

    /** 选「示例种子」策略：优先 live 且带卡（composition_ir 允许），避免 registry-only kg_boost。 */
    function pickSeedStrategy(strategies) {
      var items = strategies || [];
      var usable = items.filter(function (item) {
        return item.card !== false;
      });
      var liveUsable = usable.filter(function (item) {
        return item.status === "live";
      });
      if (liveUsable.length) return liveUsable[0];
      if (usable.length) return usable[0];
      return items.length ? items[0] : null;
    }

    function nodeLabel(type, strategyId) {
      if (type === "strategy") {
        var found = strategyById(strategyId);
        return found ? found.label || found.id : strategyId || "Strategy";
      }
      return NODE_META[type].label;
    }

    function defaultParams(type) {
      return copy(NODE_META[type].defaults || {});
    }

    function uniqueNodeId(type) {
      var prefix = ID_PREFIX[type] || "n";
      var candidate;
      do {
        candidate = prefix + state.nextNode;
        state.nextNode += 1;
      } while (nodeById(candidate));
      return candidate;
    }

    function addNode(type, x, y, params, strategyId) {
      if (NODE_TYPES.indexOf(type) < 0) return null;
      if (type === "strategy" && !isNonemptyStr(strategyId)) return null;
      var node = {
        id: uniqueNodeId(type),
        type: type,
        label: nodeLabel(type, strategyId),
        x: Math.max(0, Math.round(x)),
        y: Math.max(0, Math.round(y)),
        strategy_id: type === "strategy" ? strategyId : null,
        params: isPlainObject(params) ? copy(params) : defaultParams(type)
      };
      state.nodes.push(node);
      return node;
    }

    function connect(fromId, toId) {
      var from = nodeById(fromId);
      var to = nodeById(toId);
      if (!from || !to) return { ok: false, reason: "节点不存在" };
      var check = canConnect(from, to, state.edges);
      if (!check.ok) return check;
      var edge = { id: "e" + state.nextEdge, from: fromId, to: toId, port: check.port };
      state.nextEdge += 1;
      state.edges.push(edge);
      return { ok: true, edge: edge };
    }

    function removeNode(id) {
      state.nodes = state.nodes.filter(function (node) {
        return node.id !== id;
      });
      state.edges = state.edges.filter(function (edge) {
        return edge.from !== id && edge.to !== id;
      });
      if (state.selection.kind === "node" && state.selection.id === id) resetSelection();
      renderAll();
    }

    function removeEdge(id) {
      state.edges = state.edges.filter(function (edge) {
        return edge.id !== id;
      });
      if (state.selection.kind === "edge" && state.selection.id === id) resetSelection();
      renderAll();
    }

    function resetSelection() {
      state.selection = { kind: null, id: null };
    }

    function select(kind, id) {
      state.selection = { kind: kind, id: id };
      renderNodes();
      renderEdges();
    }

    function nodeSub(node) {
      if (node.type === "strategy") {
        var found = strategyById(node.strategy_id);
        var strategyStatus = found ? found.status : "?";
        var registryOnly = found && found.card === false ? " · registry-only（无卡片）" : "";
        return "strategy_id: " + node.strategy_id + " · " + strategyStatus + registryOnly;
      }
      var port = outputPortOf(node.type);
      var parts = [];
      if (node.type === "universe" && node.params && node.params.markets) {
        parts.push("markets: " + node.params.markets.join("/"));
      }
      if (node.type === "rank" && node.params && node.params.top_n !== undefined) {
        parts.push("top_n: " + node.params.top_n);
      }
      if (node.type === "execution") {
        parts.push("dry_run: " + (node.params && node.params.dry_run === true ? "true" : "?"));
        if (node.params && node.params.book) parts.push("book: " + node.params.book);
      }
      parts.push("→ " + port);
      return parts.join(" · ");
    }

    function renderNodes() {
      if (!dom.nodeLayer) return;
      var html = state.nodes
        .map(function (node) {
          var selected = state.selection.kind === "node" && state.selection.id === node.id;
          return (
            '<div class="dag-node type-' +
            attr(node.type) +
            (selected ? " selected" : "") +
            '" data-id="' +
            attr(node.id) +
            '" style="left:' +
            Math.round(node.x) +
            "px;top:" +
            Math.round(node.y) +
            'px">' +
            '<span class="port in" title="输入端口"></span>' +
            '<div class="node-head"><span class="node-type">' +
            escHtml(NODE_META[node.type].short) +
            '</span><button type="button" class="node-del" data-del="' +
            attr(node.id) +
            '" title="删除节点">×</button></div>' +
            '<div class="node-title">' +
            escHtml(node.label) +
            "</div>" +
            '<div class="node-sub">' +
            escHtml(nodeSub(node)) +
            "</div>" +
            '<span class="port out" title="输出端口（拖到目标节点连线）"></span>' +
            "</div>"
          );
        })
        .join("");
      dom.nodeLayer.innerHTML = html;
      if (dom.canvasEmpty) {
        dom.canvasEmpty.style.display = state.nodes.length ? "none" : "block";
      }
    }

    function edgeGeometry(from, to) {
      var x1 = from.x + NODE_W;
      var y1 = from.y + NODE_H / 2;
      var x2 = to.x;
      var y2 = to.y + NODE_H / 2;
      var dx = Math.max(40, Math.abs(x2 - x1) / 2);
      return (
        "M" +
        Math.round(x1) +
        "," +
        Math.round(y1) +
        " C" +
        Math.round(x1 + dx) +
        "," +
        Math.round(y1) +
        " " +
        Math.round(x2 - dx) +
        "," +
        Math.round(y2) +
        " " +
        Math.round(x2) +
        "," +
        Math.round(y2)
      );
    }

    function renderEdges() {
      if (!dom.edgeGroup) return;
      var maxX = 880;
      var maxY = 560;
      var html = state.edges
        .map(function (edge) {
          var from = nodeById(edge.from);
          var to = nodeById(edge.to);
          if (!from || !to) return "";
          maxX = Math.max(maxX, from.x + NODE_W + 80, to.x + NODE_W + 80);
          maxY = Math.max(maxY, from.y + NODE_H + 80, to.y + NODE_H + 80);
          var selected = state.selection.kind === "edge" && state.selection.id === edge.id;
          var d = edgeGeometry(from, to);
          var title = escHtml(edge.from + " → " + edge.to + " [" + edge.port + "]");
          return (
            '<path class="edge-hit" data-edge="' +
            attr(edge.id) +
            '" d="' +
            d +
            '"></path>' +
            '<path class="edge-line' +
            (selected ? " selected" : "") +
            '" data-edge="' +
            attr(edge.id) +
            '" d="' +
            d +
            '" marker-end="url(#arrow)"><title>' +
            title +
            "</title></path>"
          );
        })
        .join("");
      dom.edgeGroup.innerHTML = html;
      if (dom.edgeLayer) {
        dom.edgeLayer.setAttribute("width", String(maxX));
        dom.edgeLayer.setAttribute("height", String(maxY));
        dom.edgeLayer.style.width = maxX + "px";
        dom.edgeLayer.style.height = maxY + "px";
      }
      if (dom.nodeLayer) {
        dom.nodeLayer.style.minWidth = maxX + "px";
        dom.nodeLayer.style.minHeight = maxY + "px";
      }
    }

    function updateTempEdge(evt) {
      if (!state.link || !dom.tempEdge) return;
      var from = nodeById(state.link.fromId);
      if (!from) return;
      var point = canvasPoint(evt);
      var x1 = from.x + NODE_W;
      var y1 = from.y + NODE_H / 2;
      var dx = Math.max(30, Math.abs(point.x - x1) / 2);
      var d =
        "M" +
        Math.round(x1) +
        "," +
        Math.round(y1) +
        " C" +
        Math.round(x1 + dx) +
        "," +
        Math.round(y1) +
        " " +
        Math.round(point.x - dx) +
        "," +
        Math.round(point.y) +
        " " +
        Math.round(point.x) +
        "," +
        Math.round(point.y);
      dom.tempEdge.setAttribute("d", d);
      dom.tempEdge.style.display = "block";
    }

    function hideTempEdge() {
      if (!dom.tempEdge) return;
      dom.tempEdge.style.display = "none";
      dom.tempEdge.setAttribute("d", "");
    }

    function canvasPoint(evt) {
      var rect = dom.canvasWrap.getBoundingClientRect();
      return {
        x: evt.clientX - rect.left + dom.canvasWrap.scrollLeft,
        y: evt.clientY - rect.top + dom.canvasWrap.scrollTop
      };
    }

    function showToast(message) {
      if (!dom.toast) return;
      dom.toast.textContent = message;
      dom.toast.classList.add("show");
      if (dom.toastTimer) clearTimeout(dom.toastTimer);
      dom.toastTimer = setTimeout(function () {
        dom.toast.classList.remove("show");
      }, 2600);
    }

    /* ── 右侧 IR 预览 / 校验状态 ── */
    function syncHeaderInputs() {
      if (dom.fPipeline) dom.fPipeline.value = state.header.pipeline_id;
      if (dom.fOwner) dom.fOwner.value = state.header.owner;
      if (dom.fTenant) dom.fTenant.value = state.header.tenant;
      if (dom.fMode) dom.fMode.value = state.header.mode;
      if (dom.fDescription) dom.fDescription.value = state.header.description || "";
    }

    function readHeaderInputs() {
      if (dom.fPipeline) state.header.pipeline_id = dom.fPipeline.value.trim();
      if (dom.fOwner) state.header.owner = dom.fOwner.value.trim();
      if (dom.fTenant) state.header.tenant = dom.fTenant.value.trim();
      if (dom.fMode) state.header.mode = dom.fMode.value;
      if (dom.fDescription) state.header.description = dom.fDescription.value.trim();
    }

    function strategyIdList() {
      /* composition_ir 的 strategy 节点要求 strategy_id 属于 10 张 Strategy Card；
       * registry-only（card=false，如 kg_boost）可展示但 IR 校验会拒绝。 */
      return state.strategies
        .filter(function (item) {
          return item.card !== false;
        })
        .map(function (item) {
          return item.id;
        });
    }

    /** 当前画布 + 模板参数 → 待预览 IR（参数只在内存生效，不自动导出/不写盘）。 */
    function currentIRBundle() {
      var graph = { nodes: state.nodes, edges: state.edges };
      var header = cloneHeader(state.header);
      var options = null;
      if (state.templateParams) {
        var applied = applyTemplateParams(graph, header, state.templateParams);
        graph = { nodes: applied.nodes, edges: applied.edges };
        header = applied.header;
        options = applied.options;
      }
      return { ir: buildIR(graph, header, options), paramResult: state.templateParams ? validateTemplateParams(state.templateParams) : null };
    }

    function mergeParamStatus(result, paramResult) {
      if (!paramResult || paramResult.ok) return result;
      var errors = paramResult.errors.map(function (item) {
        return "模板参数: " + item;
      });
      return {
        ok: false,
        errors: errors.concat(result.errors),
        warnings: (result.warnings || []).concat(paramResult.warnings || []),
        node_count: result.node_count,
        edge_count: result.edge_count,
        schema: result.schema
      };
    }

    function syncPreview() {
      var bundle = currentIRBundle();
      state.lastIR = bundle.ir;
      if (dom.irPreview) dom.irPreview.textContent = JSON.stringify(state.lastIR, null, 2);
      if (dom.statSummary) dom.statSummary.textContent = "nodes " + state.nodes.length + " / edges " + state.edges.length;
      renderTemplateParamErrors(bundle.paramResult);
      var result = validateIR(state.lastIR, { strategyIds: strategyIdList() });
      paintStatus(mergeParamStatus(result, bundle.paramResult), false);
    }

    function paintStatus(result, verbose) {
      if (!dom.irStatus) return;
      dom.irStatus.classList.remove("ok", "err");
      if (result.ok) {
        dom.irStatus.classList.add("ok");
        dom.irStatus.innerHTML =
          '<span class="status-badge ok">本地校验通过</span> nodes ' +
          result.node_count +
          " / edges " +
          result.edge_count +
          (result.warnings.length ? " · 提醒 " + result.warnings.length + " 条" : "");
        if (verbose && result.warnings.length) {
          dom.irStatus.innerHTML += '<ul class="status-list">' + result.warnings.map(function (item) {
            return "<li>" + escHtml(item) + "</li>";
          }).join("") + "</ul>";
        }
        dom.irStatus.innerHTML +=
          '<div class="status-note">权威校验：python -X utf8 strategy_layer/composition_ir.py --file &lt;导出的 ir&gt;.json</div>';
      } else {
        dom.irStatus.classList.add("err");
        var shown = result.errors.slice(0, verbose ? 50 : 3);
        dom.irStatus.innerHTML =
          '<span class="status-badge err">本地校验失败 ' +
          result.errors.length +
          ' 项</span><ul class="status-list">' +
          shown.map(function (item) {
            return "<li>" + escHtml(item) + "</li>";
          }).join("") +
          (result.errors.length > shown.length ? "<li>…共 " + result.errors.length + " 项（点「校验」展开）</li>" : "") +
          "</ul>";
      }
    }

    function runValidate(verbose) {
      readHeaderInputs();
      syncPreview();
      var result = validateIR(state.lastIR, { strategyIds: strategyIdList() });
      var paramResult = state.templateParams ? validateTemplateParams(state.templateParams) : null;
      var merged = mergeParamStatus(result, paramResult);
      paintStatus(merged, verbose !== false);
      return merged;
    }

    /* ── 渲染面板 ── */
    function paletteButton(type, strategyId, label, status, hintOverride, card) {
      var meta = type === "strategy" ? null : NODE_META[type];
      var display = label || (meta ? meta.label : type);
      var hint = hintOverride || (type === "strategy" ? (status || "") + " · 点击/拖拽添加" : meta.hint);
      var dot = type === "strategy" ? '<span class="pdot ' + attr(status || "shadow") + (card === false ? " registry-only" : "") + '"></span>' : '<span class="pico type-' + attr(type) + '"></span>';
      return (
        '<button type="button" class="palette-item" draggable="true" data-type="' +
        attr(type) +
        '"' +
        (strategyId ? ' data-strategy-id="' + attr(strategyId) + '"' : "") +
        ' title="' +
        attr(display + " · " + hint) +
        '">' +
        dot +
        '<span class="plabel">' +
        escHtml(display) +
        '</span><span class="phint">' +
        escHtml(hint) +
        "</span></button>"
      );
    }

    function renderPalette() {
      if (!dom.palette) return;
      dom.palette.innerHTML = NODE_TYPES.filter(function (type) {
        return type !== "strategy";
      })
        .map(function (type) {
          return paletteButton(type, null, NODE_META[type].label, null);
        })
        .join("");
    }

    function renderStrategyPalette() {
      if (!dom.strategyPalette) return;
      if (!state.strategies.length) {
        dom.strategyPalette.innerHTML = '<div class="empty">暂无策略（Graph JSON 与内置 fallback 均不可用）</div>';
        return;
      }
      var live = state.strategies.filter(function (item) {
        return item.status === "live";
      });
      var shadow = state.strategies.filter(function (item) {
        return item.status !== "live";
      });
      var strategyHintOf = function (item) {
        var hint = "点击/拖拽添加";
        if (item.card === false) hint = "registry-only · 无卡片（IR 校验会拒绝）· 仅供参考";
        return hint;
      };
      var html = "";
      if (live.length) {
        html += '<div class="palette-group">启用（live）</div>';
        html += live.map(function (item) {
          return paletteButton("strategy", item.id, item.label, "live", strategyHintOf(item), item.card);
        }).join("");
      }
      if (shadow.length) {
        html += '<div class="palette-group">影子候选（shadow）</div>';
        html += shadow.map(function (item) {
          return paletteButton("strategy", item.id, item.label, "shadow", strategyHintOf(item), item.card);
        }).join("");
      }
      dom.strategyPalette.innerHTML = html;
    }

    function updateGraphSource() {
      if (!dom.graphSource) return;
      dom.graphSource.textContent = "Graph: " + state.graphSource;
      dom.graphSource.title = state.graphSource;
      dom.graphSource.classList.toggle("fallback", state.graphSource.indexOf("fallback") >= 0);
      if (dom.strategyHint) {
        dom.strategyHint.textContent =
          "live " +
          state.strategies.filter(function (item) {
            return item.status === "live";
          }).length +
          " / shadow " +
          state.strategies.filter(function (item) {
            return item.status !== "live";
          }).length +
          " · 来源：" +
          state.graphSource;
      }
    }

    /* ── 示例 / 清空 ── */
    function seedCanvas() {
      resetSelection();
      state.nodes = [];
      state.edges = [];
      state.nextNode = 1;
      state.nextEdge = 1;
      var seed = pickSeedStrategy(state.strategies);
      var seedStrategy = seed ? seed.id : "chan_monitor";
      var u = addNode("universe", 40, 40, { markets: ["CN"] }, null);
      var s = addNode("strategy", 300, 40, null, seedStrategy);
      var r = addNode("rank", 560, 40, { top_n: 30 }, null);
      var a = addNode("allocator", 40, 250, { method: "equal_weight" }, null);
      var k = addNode("risk", 300, 250, null, null);
      var x = addNode("execution", 560, 250, { book: "official", dry_run: true }, null);
      connect(u.id, s.id);
      connect(s.id, r.id);
      connect(r.id, a.id);
      connect(a.id, k.id);
      connect(k.id, x.id);
      state.template = null;
      state.templateParams = null;
      renderTemplateParams();
      renderAll();
    }

    function clearCanvas() {
      resetSelection();
      state.nodes = [];
      state.edges = [];
      state.nextNode = 1;
      state.nextEdge = 1;
      state.template = null;
      state.templateParams = null;
      renderTemplateParams();
      renderAll();
      showToast("画布已清空");
    }

    /* ── 唯一网络出口：GET 白名单（graph + templates 两条只读数据）──
     * Canvas 契约要求「唯一网络 = GET、不得 POST」；本函数收敛为唯一 fetch 调用点，
     * method 固定 GET，URL 仅允许 GRAPH_URL / TEMPLATES_URL（其余直接 reject）。 */
    function getJson(url) {
      if (url !== GRAPH_URL && url !== TEMPLATES_URL) {
        return Promise.reject(new Error("非白名单 GET URL: " + url));
      }
      return fetch(url === TEMPLATES_URL ? TEMPLATES_URL : GRAPH_URL, {
        method: "GET",
        cache: "no-store",
        credentials: "same-origin"
      }).then(function (response) {
        if (!response || !response.ok) {
          throw new Error("HTTP " + (response ? response.status : "?"));
        }
        return response.json();
      });
    }

    /* ── Graph JSON（GET；失败用内置 fallback） ── */
    function fetchGraph() {
      return getJson(GRAPH_URL)
        .then(function (graph) {
          var strategies = ((graph && graph.nodes) || [])
            .filter(function (node) {
              return node && node.type === "strategy" && (node.status === "live" || node.status === "shadow");
            })
            .map(function (node) {
              return {
                id: node.strategy_id,
                status: node.status,
                label: node.label || node.strategy_id,
                card: node.card !== false
              };
            });
          if (!strategies.length) throw new Error("Graph JSON 无 live/shadow 策略节点");
          state.strategies = strategies;
          state.graphSource =
            "pipeline_graph.json" +
            (graph.meta && graph.meta.generated_at ? " · " + graph.meta.generated_at : "") +
            " · live " +
            strategies.filter(function (item) {
              return item.status === "live";
            }).length +
            " / shadow " +
            strategies.filter(function (item) {
              return item.status !== "live";
            }).length;
        })
        .catch(function (error) {
          state.strategies = FALLBACK_STRATEGIES.slice();
          state.graphSource = "内置 fallback（pipeline_graph.json 不可达：" + ((error && error.message) || error) + "）";
        })
        .then(function () {
          renderStrategyPalette();
          updateGraphSource();
          syncPreview();
        });
    }

    /* ── Phase 4-B：模板库（GET；失败用内置 3 模板 fallback） ── */
    function renderTemplateOptions() {
      if (!dom.templateSelect) return;
      var options = ['<option value="">— 选择模板 —</option>'];
      state.templates.forEach(function (template) {
        options.push(
          '<option value="' +
            attr(template.template_id) +
            '">' +
            escHtml(template.title + "（" + template.template_id + "）") +
            "</option>"
        );
      });
      dom.templateSelect.innerHTML = options.join("");
      dom.templateSelect.value = state.templates.length ? state.templates[0].template_id : "";
    }

    function updateTemplateSource() {
      if (!dom.templateSource) return;
      var fromFallback = state.templateSource.indexOf("fallback") >= 0;
      dom.templateSource.textContent = "模板: " + state.templateSource + " · " + state.templates.length + " 个";
      dom.templateSource.title = state.templateSource;
      dom.templateSource.classList.toggle("fallback", fromFallback);
      dom.templateSource.classList.toggle("ok", !fromFallback);
    }

    function fetchTemplates() {
      return getJson(TEMPLATES_URL)
        .then(function (payload) {
          var resolved = selectTemplates(payload);
          state.templates = resolved.templates;
          state.templateSource = resolved.source;
        })
        .catch(function (error) {
          state.templates = copy(FALLBACK_TEMPLATES);
          state.templateSource = "内置 fallback（" + TEMPLATES_URL + " 不可达：" + ((error && error.message) || error) + "）";
        })
        .then(function () {
          renderTemplateOptions();
          updateTemplateSource();
        });
    }

    /** 把模板 IR 载入画布（节点/边/header）；只改内存，不执行、不写服务端。 */
    function loadTemplateById(templateId) {
      var template = null;
      for (var i = 0; i < state.templates.length; i += 1) {
        if (state.templates[i].template_id === templateId) {
          template = state.templates[i];
          break;
        }
      }
      if (!template) {
        showToast("未找到模板：" + templateId);
        return null;
      }
      var converted;
      try {
        converted = templateToCanvas(template);
      } catch (error) {
        showToast("模板载入失败：" + ((error && error.message) || error));
        return null;
      }
      resetSelection();
      state.nodes = converted.nodes;
      state.edges = converted.edges;
      state.nextNode = converted.nodes.length + 1;
      state.nextEdge = converted.edges.length + 1;
      state.header = cloneHeader(converted.header);
      state.template = template;
      state.templateParams = copy(converted.params);
      syncHeaderInputs();
      renderTemplateParams();
      renderAll();
      showToast(
        "已载入模板 " + template.title + "（" + converted.nodes.length + " 节点 / " +
          converted.edges.length + " 边，只读；参数区已在右侧）"
      );
      return converted;
    }

    function onLoadTemplateClick() {
      var selected = dom.templateSelect ? dom.templateSelect.value : "";
      if (!selected) {
        showToast("请先选择模板");
        return null;
      }
      return loadTemplateById(selected);
    }

    /* ── Phase 5-C：模板参数区（tenant / 每策略权重 / max_single）── */
    function renderTemplateParamErrors(paramResult) {
      if (!dom.pErrors) return;
      if (!state.templateParams) {
        dom.pErrors.textContent = "";
        dom.pErrors.classList.remove("err");
      } else if (!paramResult || paramResult.ok) {
        var sum = paramResult && isNumber(paramResult.weights_sum) ? Math.round(paramResult.weights_sum * 1000) / 1000 : "-";
        dom.pErrors.textContent = "参数校验通过 · Σw = " + sum + " · max_single = " + paramResult.max_single;
        dom.pErrors.classList.remove("err");
        dom.pErrors.classList.add("ok");
      } else {
        dom.pErrors.textContent = "参数非法：" + paramResult.errors.join("；");
        dom.pErrors.classList.remove("ok");
        dom.pErrors.classList.add("err");
      }
      if (dom.pWeightsHint) {
        var sumText = paramResult && isNumber(paramResult.weights_sum) ? Math.round(paramResult.weights_sum * 1000) / 1000 : "-";
        dom.pWeightsHint.textContent = "Σw = " + sumText + "（需 ∈ (0,1]）";
      }
    }

    function renderTemplateParams() {
      if (!dom.templateParams) return;
      if (!state.template || !state.templateParams) {
        dom.templateParams.classList.add("hidden");
        if (dom.pWeights) dom.pWeights.innerHTML = "";
        if (dom.pErrors) dom.pErrors.textContent = "";
        return;
      }
      dom.templateParams.classList.remove("hidden");
      if (dom.templateParamsTitle) {
        dom.templateParamsTitle.textContent = state.template.title + "（" + state.template.template_id + "）";
      }
      if (dom.pTenant) dom.pTenant.value = state.templateParams.tenant;
      if (dom.pMaxSingle) dom.pMaxSingle.value = String(state.templateParams.max_single);
      var strategyIds = templateStrategyIds(state.template);
      var weightHtml = strategyIds
        .map(function (sid) {
          var weight = isNumber(state.templateParams.weights[sid]) ? state.templateParams.weights[sid] : 0;
          return (
            '<label class="pweight"><span class="pwsid">' +
            escHtml(sid) +
            '</span><input id="pw-' +
            attr(sid) +
            '" type="number" step="0.05" min="0" max="1" value="' +
            attr(weight) +
            '"></label>'
          );
        })
        .join("");
      if (dom.pWeights) dom.pWeights.innerHTML = weightHtml;
      /* 逐项补值 + 绑定（不依赖 querySelectorAll，便于离线 DOM shim 测试） */
      strategyIds.forEach(function (sid) {
        var input = document.getElementById("pw-" + sid);
        if (!input) return;
        if (isNumber(state.templateParams.weights[sid])) input.value = String(state.templateParams.weights[sid]);
        if (input.addEventListener) input.addEventListener("input", onTemplateParamInput);
      });
      renderTemplateParamErrors(validateTemplateParams(state.templateParams));
    }

    function readTemplateParamsFromInputs() {
      if (!state.templateParams) return;
      if (dom.pTenant) state.templateParams.tenant = dom.pTenant.value.trim();
      if (dom.pMaxSingle) state.templateParams.max_single = parseFloat(dom.pMaxSingle.value);
      if (state.template) {
        templateStrategyIds(state.template).forEach(function (sid) {
          var input = document.getElementById("pw-" + sid);
          if (input) state.templateParams.weights[sid] = parseFloat(input.value);
        });
      }
      if (isNonemptyStr(state.templateParams.tenant)) {
        state.header.tenant = state.templateParams.tenant;
        if (dom.fTenant) dom.fTenant.value = state.templateParams.tenant;
      }
    }

    function onTemplateParamInput() {
      readTemplateParamsFromInputs();
      syncPreview(); /* 实时更新 IR 预览（内存），不自动导出、不写服务端 */
    }

    function resetTemplateParams() {
      if (!state.template) return;
      state.templateParams = parseTemplateParameters(state.template);
      renderTemplateParams();
      syncPreview();
      showToast("已重置模板参数（仅内存，不写服务端）");
    }

    /* ── 画布节点事件 ── */
    function onNodePointerDown(evt) {
      var target = evt.target;
      var delButton = target.closest ? target.closest(".node-del") : null;
      if (delButton) {
        removeNode(delButton.getAttribute("data-del"));
        return;
      }
      var outPort = target.closest ? target.closest(".port.out") : null;
      var nodeEl = target.closest ? target.closest(".dag-node") : null;
      if (!nodeEl) return;
      var node = nodeById(nodeEl.getAttribute("data-id"));
      if (!node) return;
      evt.preventDefault();
      select("node", node.id);
      if (outPort) {
        state.link = { fromId: node.id };
        showToast("拖到目标节点松开完成连线；松开空白处取消");
        return;
      }
      var point = canvasPoint(evt);
      state.drag = { id: node.id, offsetX: point.x - node.x, offsetY: point.y - node.y };
      if (nodeEl.setPointerCapture) {
        try {
          nodeEl.setPointerCapture(evt.pointerId);
        } catch (error) {
          /* 忽略：setPointerCapture 在部分环境不可用 */
        }
      }
    }

    function onPointerMove(evt) {
      if (state.drag) {
        var point = canvasPoint(evt);
        var node = nodeById(state.drag.id);
        if (!node) return;
        node.x = Math.max(0, Math.round(point.x - state.drag.offsetX));
        node.y = Math.max(0, Math.round(point.y - state.drag.offsetY));
        var nodeEl = dom.nodeLayer.querySelector('.dag-node[data-id="' + node.id + '"]');
        if (nodeEl) {
          nodeEl.style.left = node.x + "px";
          nodeEl.style.top = node.y + "px";
        }
        renderEdges();
        return;
      }
      if (state.link) updateTempEdge(evt);
    }

    function onPointerUp(evt) {
      if (state.drag) {
        state.drag = null;
        renderAll();
        return;
      }
      if (state.link) {
        var fromId = state.link.fromId;
        state.link = null;
        hideTempEdge();
        var hit = document.elementFromPoint(evt.clientX, evt.clientY);
        var nodeEl = hit && hit.closest ? hit.closest(".dag-node") : null;
        if (nodeEl) {
          var result = connect(fromId, nodeEl.getAttribute("data-id"));
          if (!result.ok) showToast("无法连接：" + result.reason);
        }
        renderAll();
      }
    }

    function onKeyDown(evt) {
      var tag = (evt.target && evt.target.tagName ? evt.target.tagName : "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if (evt.key === "Delete" || evt.key === "Backspace") {
        if (state.selection.kind === "node") {
          evt.preventDefault();
          removeNode(state.selection.id);
        } else if (state.selection.kind === "edge") {
          evt.preventDefault();
          removeEdge(state.selection.id);
        }
      }
    }

    /* ── 面板拖拽 ── */
    function onPaletteDragStart(evt) {
      var button = evt.target.closest ? evt.target.closest("[data-type]") : null;
      if (!button || !evt.dataTransfer) return;
      var payload = {
        type: button.getAttribute("data-type"),
        strategy_id: button.getAttribute("data-strategy-id") || null
      };
      evt.dataTransfer.setData("text/plain", JSON.stringify(payload));
      evt.dataTransfer.effectAllowed = "copy";
    }

    function addFromPalette(type, strategyId, x, y) {
      var node = addNode(type, x, y, null, strategyId);
      if (!node) {
        showToast("无法添加节点：" + type + (strategyId ? " / " + strategyId : ""));
        return;
      }
      renderAll();
      showToast("已添加 " + node.label + "（" + node.id + "）");
    }

    function paletteClick() {
      return function (evt) {
        var button = evt.target.closest ? evt.target.closest("[data-type]") : null;
        if (!button) return;
        /* 简单错位排布，避免连续点击添加时节点完全重叠 */
        var index = state.nodes.length;
        var x = 80 + (index % 3) * 90;
        var y = 80 + (index % 4) * 80;
        addFromPalette(button.getAttribute("data-type"), button.getAttribute("data-strategy-id") || null, x, y);
      };
    }

    function onCanvasDrop(evt) {
      evt.preventDefault();
      var raw = evt.dataTransfer ? evt.dataTransfer.getData("text/plain") : "";
      if (!raw) return;
      var payload;
      try {
        payload = JSON.parse(raw);
      } catch (error) {
        return;
      }
      var point = canvasPoint(evt);
      addFromPalette(payload.type, payload.strategy_id || null, point.x - 40, point.y - 30);
    }

    /* ── 右侧动作：复制 / 导出（全部本地，不发请求） ── */
    function irText() {
      readHeaderInputs();
      var ir = buildIR({ nodes: state.nodes, edges: state.edges }, cloneHeader(state.header));
      return JSON.stringify(ir, null, 2);
    }

    function copyIR() {
      var text = irText();
      var finish = function (ok) {
        showToast(ok ? "IR JSON 已复制到剪贴板" : "复制失败：请手动从预览区复制");
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          function () {
            finish(true);
          },
          function () {
            finish(legacyCopy(text));
          }
        );
      } else {
        finish(legacyCopy(text));
      }
    }

    function legacyCopy(text) {
      try {
        var area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "readonly");
        area.style.position = "fixed";
        area.style.left = "-9999px";
        document.body.appendChild(area);
        area.select();
        var ok = document.execCommand && document.execCommand("copy");
        document.body.removeChild(area);
        return !!ok;
      } catch (error) {
        return false;
      }
    }

    function downloadIR() {
      var text = irText();
      var blob = new Blob([text], { type: "application/json;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var link = document.createElement("a");
      link.href = url;
      link.download = (state.header.pipeline_id || "composition_ir") + ".json";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(function () {
        URL.revokeObjectURL(url);
      }, 1000);
      showToast("已导出本地 IR JSON（不写服务端）");
    }

    function renderAll() {
      renderNodes();
      renderEdges();
      syncHeaderInputs();
      syncPreview();
    }

    function bindEvents() {
      if (dom.palette) {
        dom.palette.addEventListener("click", paletteClick());
        dom.palette.addEventListener("dragstart", onPaletteDragStart);
      }
      if (dom.strategyPalette) {
        dom.strategyPalette.addEventListener("click", paletteClick());
        dom.strategyPalette.addEventListener("dragstart", onPaletteDragStart);
      }
      if (dom.nodeLayer) dom.nodeLayer.addEventListener("pointerdown", onNodePointerDown);
      if (dom.edgeLayer) {
        dom.edgeLayer.addEventListener("click", function (evt) {
          var hit = evt.target.closest ? evt.target.closest("[data-edge]") : null;
          if (hit) select("edge", hit.getAttribute("data-edge"));
        });
      }
      if (dom.canvasWrap) {
        dom.canvasWrap.addEventListener("dragover", function (evt) {
          evt.preventDefault();
          if (evt.dataTransfer) evt.dataTransfer.dropEffect = "copy";
        });
        dom.canvasWrap.addEventListener("drop", onCanvasDrop);
      }
      document.addEventListener("pointermove", onPointerMove);
      document.addEventListener("pointerup", onPointerUp);
      document.addEventListener("keydown", onKeyDown);
      if (dom.btnSeed) dom.btnSeed.addEventListener("click", seedCanvas);
      if (dom.btnClear) dom.btnClear.addEventListener("click", clearCanvas);
      if (dom.btnSelDelete) {
        dom.btnSelDelete.addEventListener("click", function () {
          if (state.selection.kind === "node") removeNode(state.selection.id);
          else if (state.selection.kind === "edge") removeEdge(state.selection.id);
        });
      }
      if (dom.btnValidate) dom.btnValidate.addEventListener("click", function () {
        runValidate(true);
      });
      if (dom.btnCopy) dom.btnCopy.addEventListener("click", copyIR);
      if (dom.btnDownload) dom.btnDownload.addEventListener("click", downloadIR);
      if (dom.btnLoadTemplate) dom.btnLoadTemplate.addEventListener("click", onLoadTemplateClick);
      if (dom.btnResetParams) dom.btnResetParams.addEventListener("click", resetTemplateParams);
      [dom.pTenant, dom.pMaxSingle].forEach(function (input) {
        if (!input) return;
        input.addEventListener("input", onTemplateParamInput);
        input.addEventListener("change", onTemplateParamInput);
      });
      if (dom.templateSelect) {
        dom.templateSelect.addEventListener("change", function () {
          var selected = dom.templateSelect.value;
          if (!selected) return;
          var found = null;
          state.templates.forEach(function (template) {
            if (template.template_id === selected) found = template;
          });
          if (found && dom.templateSource) {
            dom.templateSource.title = found.title + " · " + found.description;
          }
        });
      }
      [dom.fPipeline, dom.fOwner, dom.fTenant, dom.fMode, dom.fDescription].forEach(function (input) {
        if (!input) return;
        input.addEventListener("input", function () {
          readHeaderInputs();
          /* 模板参数生效时，header.tenant 与参数区 tenant 双向同步 */
          if (state.templateParams) {
            state.templateParams.tenant = state.header.tenant;
            if (dom.pTenant) dom.pTenant.value = state.header.tenant;
          }
          syncPreview();
        });
        input.addEventListener("change", function () {
          readHeaderInputs();
          if (state.templateParams) {
            state.templateParams.tenant = state.header.tenant;
            if (dom.pTenant) dom.pTenant.value = state.header.tenant;
          }
          syncPreview();
        });
      });
    }

    function boot() {
      if (state) return; /* 幂等：重复 boot 直接返回 */
      if (typeof document === "undefined") return;
      var app = document.getElementById("canvas-app");
      if (!app) return;
      state = newState();
      dom = {
        palette: $("palette"),
        strategyPalette: $("strategyPalette"),
        strategyHint: $("strategyHint"),
        graphSource: $("graphSource"),
        nodeLayer: $("nodeLayer"),
        edgeLayer: $("edgeLayer"),
        edgeGroup: $("edgeGroup"),
        tempEdge: $("tempEdge"),
        canvasWrap: $("canvasWrap"),
        canvasEmpty: $("canvasEmpty"),
        irPreview: $("irPreview"),
        irStatus: $("irStatus"),
        statSummary: $("statSummary"),
        toast: $("toast"),
        fPipeline: $("fPipelineId"),
        fOwner: $("fOwner"),
        fTenant: $("fTenant"),
        fMode: $("fMode"),
        fDescription: $("fDescription"),
        btnSeed: $("btnSeed"),
        btnClear: $("btnClear"),
        btnSelDelete: $("btnSelDelete"),
        btnValidate: $("btnValidate"),
        btnCopy: $("btnCopy"),
        btnDownload: $("btnDownload"),
        templateSelect: $("templateSelect"),
        btnLoadTemplate: $("btnLoadTemplate"),
        templateSource: $("templateSource"),
        templateParams: $("templateParams"),
        templateParamsTitle: $("templateParamsTitle"),
        pTenant: $("pTenant"),
        pWeights: $("pWeights"),
        pWeightsHint: $("pWeightsHint"),
        pMaxSingle: $("pMaxSingle"),
        pErrors: $("pErrors"),
        btnResetParams: $("btnResetParams")
      };
      state.booted = true;
      syncHeaderInputs();
      renderPalette();
      renderStrategyPalette();
      renderTemplateOptions();
      updateTemplateSource();
      renderTemplateParams();
      updateGraphSource();
      bindEvents();
      seedCanvas();
      /* 唯一网络出口：两条只读 GET（graph + templates），均失败走内置 fallback */
      fetchGraph();
      fetchTemplates();
    }

    return {
      SCHEMA: SCHEMA,
      GRAPH_URL: GRAPH_URL,
      TEMPLATES_URL: TEMPLATES_URL,
      TEMPLATE_SCHEMA: TEMPLATE_SCHEMA,
      TEMPLATE_IDS: TEMPLATE_IDS,
      ALLOWED_MODES: ALLOWED_MODES,
      NODE_TYPES: NODE_TYPES,
      PORT_TYPES: PORT_TYPES,
      TERMINAL_NODE_TYPES: TERMINAL_NODE_TYPES,
      NODE_SPECS: NODE_SPECS,
      NODE_META: NODE_META,
      TOP_LEVEL_REQUIRED: TOP_LEVEL_REQUIRED,
      NODE_REQUIRED: NODE_REQUIRED,
      EDGE_REQUIRED: EDGE_REQUIRED,
      RISK_POLICY_REQUIRED: RISK_POLICY_REQUIRED,
      PUBLISH_REQUIRED: PUBLISH_REQUIRED,
      DEFAULT_HEADER: DEFAULT_HEADER,
      DEFAULT_RISK_POLICY: DEFAULT_RISK_POLICY,
      DEFAULT_PUBLISH: DEFAULT_PUBLISH,
      FALLBACK_STRATEGIES: FALLBACK_STRATEGIES,
      FALLBACK_TEMPLATES: FALLBACK_TEMPLATES,
      canConnect: canConnect,
      buildIR: buildIR,
      validateIR: validateIR,
      parseTemplatesPayload: parseTemplatesPayload,
      selectTemplates: selectTemplates,
      templateToCanvas: templateToCanvas,
      templateStrategyIds: templateStrategyIds,
      parseTemplateParameters: parseTemplateParameters,
      validateTemplateParams: validateTemplateParams,
      applyTemplateParams: applyTemplateParams,
      pickSeedStrategy: pickSeedStrategy,
      boot: boot
    };
  }
);
