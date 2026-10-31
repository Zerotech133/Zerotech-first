# -*- coding: utf-8 -*-
"""Phase 5-B Strategy Health 周报只读页面测试（WO-HEALTH-UI-20261031）。

覆盖验收口径：
1) HTML/JS 静态存在、HTML 相对引用 strategy_health.js、前端准入三件套相对路径
   （tokens.css / nav.js / shortscreen.js），无外部 CDN/绝对 URL/写死端口；
2) 只读契约：仅同源 GET /strategy_health.json（全页 fetch 仅 1 处且 method=GET）；
   无 POST/PUT/PATCH/DELETE/XMLHttpRequest/sendBeacon/WebSocket/本地存储/
   表单提交/下载；不启动服务、不引用 dashboard_api/portal_api、不写服务端；
3) 空/insufficient 显式文案：HTML 静态 + JS TEXT 双处出现
   “暂无周报 / 样本不足 / 端点未接入 / 接口不可用 / 非 JSON / 不造数”；
   h2=4 < 6（无需 shortscreen.js，但相对引用接入点保留）；
4) DOM 契约：HTML 必需元素 id、JS 引用 id 均存在、无内联事件处理器；
5) Node 纯函数契约：apiEndpoint / display / formatPct / formatNum / statusText /
   healthLevelLabel / healthLevelTone / periodText / regimeText / summarize*
   逐项断言；node --check 通过；node 载入不落盘；
6) 只读边界：dashboard_api.py / strategy_health_report.py SHA256 在执行后不变。
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAYER_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(LAYER_DIR))

HTML_PATH = os.path.join(LAYER_DIR, "strategy_health.html")
JS_PATH = os.path.join(LAYER_DIR, "strategy_health.js")
DASHBOARD_API_PATH = os.path.join(LAYER_DIR, "dashboard_api.py")
HEALTH_MODULE_PATH = os.path.join(LAYER_DIR, "strategy_health_report.py")

HEALTH_URL = "/strategy_health.json"
NODE_BIN = shutil.which("node")


# ── 工具 ─────────────────────────────────────────────────────────────
def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _require_node():
    if not NODE_BIN:
        pytest.skip("node 不可用（跳过 node --check / JS 契约测试）")
    return NODE_BIN


def _node_eval(script, cwd=None, timeout=90):
    result = subprocess.run(
        [_require_node(), "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
    )
    assert result.returncode == 0, (
        "node 执行失败 rc=%s\nstdout:\n%s\nstderr:\n%s"
        % (result.returncode, result.stdout[-1500:], result.stderr[-3000:])
    )
    return result.stdout


# ── 契约样例：与 strategy_health_report.py WeeklyHealthReport 同构 ───
_CONTRACT_TEMPLATE = r"""
const api = require(__JS_PATH__);
const report = {
  schema: "strategy_health_weekly/v1",
  status: "OK",
  readonly: true,
  side_effects: [],
  generated_at: "2026-10-31 15:25:26",
  asof: "20260124",
  period: {
    days_requested: 7, start: "20260118", end: "20260124",
    trading_days: 5, rows: 64, rows_out_of_window: 0,
    rows_missing_trade_date: 0
  },
  summary: {
    strategies: 2, strategies_with_samples: 2, strategies_without_samples: 0,
    bias_tickets: 1, tickets_by_priority: { P1: 1, P2: 0 },
    tickets_by_type: { insufficient: 1 }, upstream_owners: { "quant-classic": 1 },
    rows: 64, trading_days: 5
  },
  health_levels: { green: 1, yellow: 0, red: 0, insufficient: 1 },
  strategies: [
    {
      strategy_id: "kg_boost", health_level: "green",
      health_reasons: ["样本 60 >= 20、T+1 胜率 100.0% >= 50.0%",
                       "回撤/分歧率未触黄线"],
      sample_count: 60,
      windows: {
        t1: { count: 60, win_rate: 100.0, avg_ret: 1.5 },
        t5: { count: 60, win_rate: 100.0, avg_ret: 2.5 }
      },
      max_drawdown: { status: "not_provided", max_drawdown: null,
                      points: null, source: null, note: "未注入权益曲线" },
      turnover: { available: false, mean: null },
      cost: { available: false, total: null },
      divergence_count: 0, divergence_rate: 0.0,
      regime_breakdown: {
        bull: { count: 30, t1: { count: 30, win_rate: 100.0, avg_ret: 1.5 },
                t5: { count: 30, win_rate: 100.0, avg_ret: 2.5 },
                divergence_rate: 0.0 },
        bear: { count: 30, t1: { count: 30, win_rate: 66.7, avg_ret: 0.5 },
                t5: { count: 30, win_rate: 66.7, avg_ret: 0.5 },
                divergence_rate: 0.0 }
      },
      regime_status: "available",
      card: { present: true, display_name: "KG 助推", owner: "quant-kg" },
      registry: { known: true, enabled: true, weight: 0.5 },
      discovered_from: ["rows", "cards"],
      weekly: { rows: 60, trading_days: 5, first_date: "20260120",
                last_date: "20260124", daily: [] },
      bias_ticket_ids: []
    },
    {
      strategy_id: "trend_follow", health_level: "insufficient",
      health_reasons: ["样本数 4 < 门槛 20：不下健康结论（insufficient）"],
      sample_count: 4,
      windows: {
        t1: { count: 4, win_rate: 50.0, avg_ret: 0.5 },
        t5: { count: 4, win_rate: 50.0, avg_ret: 1.0 }
      },
      max_drawdown: { status: "not_provided", max_drawdown: null },
      turnover: { available: false, mean: null },
      cost: { available: false, total: null },
      divergence_count: 0, divergence_rate: 0.0,
      regime_breakdown: {}, regime_status: "insufficient",
      card: { present: true, display_name: "趋势跟随", owner: "quant-classic" },
      registry: { known: false, enabled: false, weight: null },
      discovered_from: ["rows", "cards"],
      weekly: { rows: 4, trading_days: 2, first_date: "20260120",
                last_date: "20260124", daily: [] },
      bias_ticket_ids: ["BTW-20260124-trend_follow-insufficient"]
    }
  ],
  bias_tickets: [
    {
      schema: "bias_ticket/v1",
      ticket_id: "BTW-20260124-trend_follow-insufficient",
      strategy_id: "trend_follow", bias_type: "insufficient",
      period: { start: "20260118", end: "20260124" },
      asof: "20260124",
      owner_upstream: "quant-classic", owner_source: "strategy_cards",
      evidence: ["样本数 4 < 门槛 20，胜率/收益无统计效力"],
      evidence_refs: [{ type: "metric", strategy_id: "trend_follow" }],
      suggested_action: "补足回放样本后重跑健康检查；本模块只读",
      priority: "P1", status: "draft", not_auto_apply: true
    }
  ],
  notes: ["本模块只读：不自动改权重/registry、不下单、不写生产数据"]
};
const emptyReport = {
  schema: "strategy_health_weekly/v1", status: "insufficient_data",
  readonly: true, asof: null, generated_at: null,
  period: { days_requested: 7, start: null, end: null, trading_days: 0,
            rows: 0, rows_out_of_window: 0, rows_missing_trade_date: 0 },
  summary: { strategies: 1, strategies_with_samples: 0,
             bias_tickets: 0, tickets_by_priority: { P1: 0, P2: 0 } },
  health_levels: { green: 0, yellow: 0, red: 0, insufficient: 1 },
  strategies: [], bias_tickets: [], notes: []
};
const out = {
  api_names: Object.keys(api).sort(),
  endpoint: api.apiEndpoint(),
  level_order: api.LEVEL_ORDER,
  level_text: api.LEVEL_TEXT,
  status_map: api.STATUS_TEXT,
  boundaries: api.BOUNDARIES,
  text: {
    emptyReport: api.TEXT.emptyReport,
    insufficient: api.TEXT.insufficient,
    endpointMissing: api.TEXT.endpointMissing,
    networkFail: api.TEXT.networkFail,
    nonJson: api.TEXT.nonJson,
    emptyStrategies: api.TEXT.emptyStrategies,
    emptyTickets: api.TEXT.emptyTickets,
    noRegime: api.TEXT.noRegime,
    notAutoApply: api.TEXT.notAutoApply
  },
  display: [api.display(null), api.display(undefined), api.display("x"),
            api.display(0), api.display("")],
  pct: [api.formatPct(null), api.formatPct(66.7), api.formatPct(-25),
        api.formatPct(0)],
  num: [api.formatNum(null), api.formatNum(1.0), api.formatNum(-2.345, 2),
        api.formatNum(3.5, 1)],
  labels: ["green", "yellow", "red", "insufficient", "unknown"]
    .map(api.healthLevelLabel),
  tones: ["green", "yellow", "red", "insufficient", "unknown"]
    .map(api.healthLevelTone),
  status: [api.statusText("OK"), api.statusText("insufficient_data"),
           api.statusText("")],
  period: api.periodText(report.period),
  period_empty: api.periodText(null),
  overview: api.summarizeOverview(report),
  overview_bad: api.summarizeOverview(null),
  green: api.summarizeStrategy(report.strategies[0]),
  insuf: api.summarizeStrategy(report.strategies[1]),
  strategy_bad: api.summarizeStrategy(null),
  regime_green: api.regimeText(report.strategies[0]),
  regime_insuf: api.regimeText(report.strategies[1]),
  ticket: api.summarizeTicket(report.bias_tickets[0]),
  ticket_bad: api.summarizeTicket(null),
  report: api.summarizeReport(report),
  report_insufficient: api.summarizeReport(emptyReport),
  report_null: api.summarizeReport(null),
  parse: [api.parseJsonSafe("not-json"), api.parseJsonSafe('{"ok":true}'),
          api.parseJsonSafe("")]
};
console.log(JSON.stringify(out));
"""


@functools.cache
def _contract():
    script = _CONTRACT_TEMPLATE.replace("__JS_PATH__", json.dumps(JS_PATH))
    return json.loads(_node_eval(script))


# ── fixtures ─────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def html_text():
    return _read(HTML_PATH)


@pytest.fixture(scope="module")
def js_text():
    return _read(JS_PATH)


@pytest.fixture(scope="module")
def contract():
    return _contract()


# ── 1. 文件存在 / 引用关系 / 前端准入三件套 ─────────────────────────
class TestFilesAndReferences:
    def test_html_and_js_exist(self):
        assert os.path.isfile(HTML_PATH) and os.path.isfile(JS_PATH)
        assert os.path.getsize(HTML_PATH) > 6000
        assert os.path.getsize(JS_PATH) > 12000

    def test_html_references_js_relative(self, html_text):
        assert 'src="strategy_health.js"' in html_text
        assert not re.search(r'src\s*=\s*["\']https?://', html_text)
        assert not re.search(r'src\s*=\s*["\']//', html_text)

    def test_tokens_nav_shortscreen_relative_paths(self, html_text):
        assert "../../portal/static/tokens.css" in html_text
        assert "../../portal/static/nav.js" in html_text
        assert "../../portal/static/shortscreen.js" in html_text
        assert not re.search(
            r'["\']/static/(tokens\.css|nav\.js|shortscreen\.js)', html_text
        )

    def test_no_hardcoded_ports_or_cdn(self, html_text, js_text):
        for text in (html_text, js_text):
            for token in (
                "8734",
                "8080",
                "8791",
                "8795",
                "127.0.0.1",
                "cdn.",
            ):
                assert token not in text, token
            assert not re.search(
                r'(?:src|href)\s*=\s*["\'](?:file|https?):/{2}', text
            )
            assert not re.search(
                r'(?:fetch|XMLHttpRequest)\s*\(\s*["\'](?:file|https?)://', text
            )
            assert not re.search(r'["\']C:\\\\', text)

    def test_title_and_lang(self, html_text):
        assert 'lang="zh-CN"' in html_text
        assert "Strategy Health" in html_text
        assert "只读" in html_text


# ── 2. 只读契约：唯一同源 GET /strategy_health.json ──────────────────
class TestReadonlyContract:
    def test_health_json_referenced_in_html_and_js(self, html_text, js_text):
        assert HEALTH_URL in html_text
        assert HEALTH_URL in js_text
        assert '"strategy_health.json"' not in js_text  # 只许绝对同源路径

    def test_single_fetch_and_get_only(self, js_text):
        assert js_text.count("fetch(") == 1
        index = js_text.index("fetch(")
        snippet = js_text[index : index + 300]
        assert "method" in snippet and '"GET"' in snippet
        assert re.search(r'\bmethod\s*:\s*"GET"', js_text)
        assert not re.search(
            r'method\s*:\s*["\'](POST|PUT|PATCH|DELETE)["\']', js_text
        )
        assert not re.search(r'["\'](POST|PUT|PATCH|DELETE)["\']', js_text)

    def test_no_write_http_verbs_or_sockets(self, js_text, html_text):
        for text in (js_text, html_text):
            for forbidden in (
                "XMLHttpRequest",
                "sendBeacon",
                "WebSocket",
                "EventSource",
                "localStorage",
                "sessionStorage",
                "document.cookie",
                "navigator.clipboard",
                "$.ajax",
                "/api/",
                "action=",
                "new Function",
                "eval(",
                "document.write",
            ):
                assert forbidden not in text, forbidden

    def test_no_form_submit_upload_or_download(self, html_text, js_text):
        assert "<form" not in html_text
        assert 'type="submit"' not in html_text
        assert 'type="file"' not in html_text
        assert "onsubmit" not in html_text
        assert "download" not in js_text
        assert "Blob(" not in js_text
        assert "createObjectURL" not in js_text

    def test_no_service_start_or_dashboard_import(self, js_text, html_text):
        for text in (js_text, html_text):
            for forbidden in (
                "serve_forever",
                "HTTPServer",
                "http.server",
                "createServer",
                ".listen(",
                "bind(",
                "register_routes",
                "dashboard_api.",
                "from dashboard_api",
                "portal_api",
            ):
                assert forbidden not in text, forbidden

    def test_refresh_button_is_type_button(self, html_text):
        assert html_text.count('type="button"') >= 1
        assert 'type="submit"' not in html_text


# ── 3. 空态 / insufficient 显式文案 ───────────────────────────────────
class TestExplicitEmptyCopies:
    def test_contract_text_constants(self, contract):
        text = contract["text"]
        assert "暂无周报" in text["emptyReport"]
        assert "暂无周报" in text["emptyStrategies"]
        assert "样本不足" in text["insufficient"]
        assert "端点未接入" in text["endpointMissing"]
        assert "接口不可用" in text["networkFail"]
        assert "非 JSON" in text["nonJson"]
        assert "无偏差票" in text["emptyTickets"]
        assert "不猜" in text["noRegime"]
        assert "not_auto_apply=true" in text["notAutoApply"]
        for value in text.values():
            assert value  # 每条文案非空

    def test_html_static_empty_states(self, html_text):
        assert "暂无周报" in html_text
        assert "样本不足" in html_text
        assert "空态显式显示" in html_text

    def test_html_declares_readonly_boundaries(self, html_text):
        for marker in (
            "只读",
            "不写服务端",
            "不调 POST",
            "不执行",
            "不下单",
            "不造数",
            "status=draft",
            "not_auto_apply=true",
            "GET",
        ):
            assert marker in html_text, marker

    def test_js_declares_readonly_boundaries(self, js_text):
        for marker in ("只读", "不写服务端", "不下单", "不造数", "不猜"):
            assert marker in js_text, marker

    def test_h2_count_below_shortscreen_threshold(self, html_text):
        # AGENTS §2：h2 >= 6 必须过 shortscreen.js；本页 h2=4，暂不需要
        assert html_text.count("<h2") == 4
        assert "../../portal/static/shortscreen.js" in html_text

    def test_footer_readonly_notice(self, html_text):
        assert "只读边界" in html_text
        assert "不写服务端" in html_text
        assert "BiasTicket" in html_text


# ── 4. DOM 结构 / id 契约 ────────────────────────────────────────────
class TestDomStructure:
    def test_required_element_ids(self, html_text):
        for element_id in (
            "periodBadge",
            "asofBadge",
            "statusBadge",
            "sourceBadge",
            "btnRefresh",
            "overviewStatus",
            "reportEmpty",
            "insufficientBanner",
            "levelCounts",
            "summaryCounts",
            "strategyCards",
            "strategyEmpty",
            "ticketBody",
            "ticketEmpty",
            "notesList",
        ):
            assert ('id="%s"' % element_id) in html_text, element_id

    def test_js_dom_ids_exist_in_html(self, html_text, js_text):
        js_ids = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', js_text))
        js_ids |= set(
            re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', js_text)
        )
        html_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html_text))
        missing = sorted(js_ids - html_ids)
        assert not missing, "JS 引用了 HTML 中不存在的元素 id: %s" % missing
        assert js_ids  # JS 确实按 id 取元素

    def test_sections_present(self, html_text):
        assert "健康总览" in html_text
        assert "策略卡片" in html_text
        assert "上游偏差票" in html_text
        assert "口径与数据缺口" in html_text
        for column in (
            "票号",
            "策略",
            "上游 owner",
            "类型",
            "优先级",
            "证据",
            "建议动作",
            "状态",
        ):
            assert "<th" in html_text
            assert column in html_text, column

    def test_no_inline_event_handlers(self, html_text):
        assert "onclick=" not in html_text
        assert "onload=" not in html_text


# ── 5. Node 纯函数契约 / node --check / 不落盘 ────────────────────────
class TestNodeContract:
    def test_exported_api_surface(self, contract):
        assert contract["api_names"] == [
            "API",
            "BOUNDARIES",
            "LEVEL_ORDER",
            "LEVEL_TEXT",
            "STATUS_TEXT",
            "TEXT",
            "apiEndpoint",
            "boot",
            "display",
            "formatNum",
            "formatPct",
            "healthLevelLabel",
            "healthLevelTone",
            "loadHealthReport",
            "numberOrNull",
            "parseJsonSafe",
            "periodText",
            "regimeText",
            "statusText",
            "summarizeOverview",
            "summarizeReport",
            "summarizeStrategy",
            "summarizeTicket",
        ]
        assert contract["endpoint"] == HEALTH_URL
        assert contract["level_order"] == [
            "green",
            "yellow",
            "red",
            "insufficient",
        ]
        assert contract["boundaries"] == ["只读", "不下单", "不造数"]
        assert contract["status_map"] == {
            "OK": "正常",
            "insufficient_data": "样本不足",
        }

    def test_display_and_formatters(self, contract):
        assert contract["display"] == ["—", "—", "x", "0", "—"]
        assert contract["pct"] == ["—", "66.7%", "-25.0%", "0.0%"]
        assert contract["num"] == ["—", "1", "-2.35", "3.5"]
        assert contract["parse"] == [None, {"ok": True}, None]

    def test_health_level_labels_and_tones(self, contract):
        assert contract["labels"] == ["健康", "关注", "预警", "样本不足", "未知"]
        assert contract["tones"] == ["ok", "warn", "bad", "empty", "empty"]
        assert contract["status"] == ["正常", "样本不足", "未知"]

    def test_period_text(self, contract):
        assert contract["period"] == "20260118 ~ 20260124（5 交易日 / 64 行）"
        assert contract["period_empty"] == "暂无周报"

    def test_summarize_overview(self, contract):
        overview = contract["overview"]
        assert overview["statusText"] == "正常"
        assert overview["asof"] == "20260124"
        assert overview["periodText"] == "20260118 ~ 20260124（5 交易日 / 64 行）"
        assert overview["readonly"] is True
        counts = {item["level"]: item for item in overview["counts"]}
        assert counts["green"]["count"] == 1
        assert counts["green"]["label"] == "健康"
        assert counts["green"]["tone"] == "ok"
        assert counts["yellow"]["count"] == 0
        assert counts["red"]["count"] == 0
        assert counts["insufficient"]["count"] == 1
        assert overview["metrics"]["strategies"] == 2
        assert overview["metrics"]["strategiesWithSamples"] == 2
        assert overview["metrics"]["biasTickets"] == 1
        assert overview["metrics"]["p1"] == 1
        assert overview["metrics"]["p2"] == 0
        assert overview["metrics"]["tradingDays"] == 5
        assert overview["metrics"]["rows"] == 64
        assert contract["overview_bad"] is None

    def test_summarize_strategy_green(self, contract):
        green = contract["green"]
        assert green["strategyId"] == "kg_boost"
        assert green["levelLabel"] == "健康"
        assert green["tone"] == "ok"
        assert green["sampleCount"] == 60
        assert green["t1WinRate"] == "100.0%"
        assert green["t1AvgRet"] == "1.5%"
        assert green["t5WinRate"] == "100.0%"
        assert green["t5AvgRet"] == "2.5%"
        assert green["drawdown"] == "—"
        assert green["drawdownStatus"] == "not_provided"
        assert green["divergenceRate"] == "0.0%"
        assert green["weeklyTradingDays"] == 5
        assert green["owner"] == "quant-kg"
        assert green["cardName"] == "KG 助推"
        assert len(green["healthReasons"]) == 2
        assert green["ticketIds"] == []

    def test_summarize_strategy_insufficient(self, contract):
        insuf = contract["insuf"]
        assert insuf["strategyId"] == "trend_follow"
        assert insuf["levelLabel"] == "样本不足"
        assert insuf["tone"] == "empty"
        assert insuf["sampleCount"] == 4
        assert "样本数 4" in insuf["healthReasons"][0]
        assert insuf["ticketIds"] == ["BTW-20260124-trend_follow-insufficient"]
        assert contract["strategy_bad"] is None

    def test_regime_text(self, contract):
        assert "bull" in contract["regime_green"]
        assert "bear" in contract["regime_green"]
        assert "T+1 胜率" in contract["regime_green"]
        assert contract["regime_insuf"] == "regime 无标签（不猜）"

    def test_summarize_ticket(self, contract):
        ticket = contract["ticket"]
        assert ticket["ticketId"] == "BTW-20260124-trend_follow-insufficient"
        assert ticket["strategyId"] == "trend_follow"
        assert ticket["biasType"] == "insufficient"
        assert ticket["priority"] == "P1"
        assert ticket["status"] == "draft"
        assert ticket["statusText"] == "draft（草稿）"
        assert ticket["ownerUpstream"] == "quant-classic"
        assert "样本数 4" in ticket["evidenceText"]
        assert "补足" in ticket["suggestedAction"]
        assert ticket["notAutoApply"] is True
        assert contract["ticket_bad"] is None

    def test_summarize_report_and_empty_states(self, contract):
        report = contract["report"]
        assert report["status"] == "OK"
        assert report["statusText"] == "正常"
        assert report["empty"] is False
        assert report["insufficient"] is False
        assert len(report["strategies"]) == 2
        assert len(report["tickets"]) == 1
        assert report["notes"]

        insuf = contract["report_insufficient"]
        assert insuf["empty"] is True
        assert insuf["insufficient"] is True
        assert "样本不足" in insuf["emptyText"]
        assert insuf["strategies"] == []
        assert insuf["tickets"] == []
        assert contract["report_null"] is None

    def test_node_check_js(self):
        _require_node()
        result = subprocess.run(
            [NODE_BIN, "--check", JS_PATH],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr.strip() == ""

    def test_node_require_does_not_write_files(self, tmp_path):
        _require_node()
        script = (
            "const api = require(%s);"
            "console.log(typeof api.boot + ':' + typeof api.summarizeReport);"
            % json.dumps(JS_PATH)
        )
        output = _node_eval(script, cwd=str(tmp_path))
        assert output.strip() == "function:function"
        assert os.listdir(str(tmp_path)) == [], "Node 载入只读页面 JS 不得落盘"


# ── 6. 只读边界：不改 dashboard_api / 周报模块 / 不启动服务 ──────────
class TestReadonlyBoundary:
    def test_boundary_files_unchanged_by_contract_run(self):
        paths = [
            path
            for path in (DASHBOARD_API_PATH, HEALTH_MODULE_PATH)
            if os.path.exists(path)
        ]
        before = {path: _sha256(path) for path in paths}
        _contract()  # node require + 全部纯函数
        _read(HTML_PATH)
        _read(JS_PATH)
        after = {path: _sha256(path) for path in paths}
        assert before == after, "只读页面/测试不得写入服务端文件或周报模块"

    def test_frontend_does_not_touch_server_modules(self, html_text, js_text):
        for text in (html_text, js_text):
            assert "dashboard_api" not in text
            assert "strategy_health_report.py" not in text
            assert "/api/" not in text

    def test_page_is_offline_first(self, js_text):
        # 离线（file:// 或端点未接）时走显式不可用文案，不依赖外部资源
        assert "接口不可用" in js_text
        assert "端点未接入" in js_text
        assert "Promise" in js_text
        assert "catch(" in js_text
        assert "no-store" in js_text
