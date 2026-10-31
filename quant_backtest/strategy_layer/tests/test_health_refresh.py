# -*- coding: utf-8 -*-
"""Phase 6-A Health 周报页自动刷新 / 新鲜度测试（WO-HEALTH-REFRESH-20261031）。

覆盖验收口径：
1) 自动刷新：JS 含 60s 定时器（REFRESH_INTERVAL_MS = 60 * 1000 / setInterval /
   clearInterval），可开关（HTML 按钮 + JS 切换），不打扰（silent 刷新失败保留
   上次数据、不闪 loading）；
2) 新鲜度：顶部 generated_at + 新鲜度徽标；generated_at 距今 >24h → stale
   （「过期」，不判失败）；缺失/非法 → 未知（不猜）；未来时间戳不判过期；
3) visibilitychange：页面隐藏暂停（不刷新），回到页面立即刷新并恢复定时器；
4) 只读边界不回退：仍仅 1 处同源 GET /strategy_health.json（method=GET），
   无 POST/写、无本地存储；空/insufficient 显式文案保留；
5) node --check 通过；node 载入不落盘；既有 UI 契约（Object.keys 23 项）不变
   （新契约非枚举挂载在 api.freshness 上）。
"""

from __future__ import annotations

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

HEALTH_URL = "/strategy_health.json"
NODE_BIN = shutil.which("node")

# 既有 UI 契约（test_strategy_health_ui.py）冻结的导出键：新增契约不得破坏。
FROZEN_API_KEYS = [
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


# ── 工具 ─────────────────────────────────────────────────────────────
def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _require_node():
    if not NODE_BIN:
        pytest.skip("node 不可用（跳过 node --check / 定时器契约测试）")
    return NODE_BIN


def _node_eval(script, cwd=None, timeout=120):
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


def _node_script(script_path, *args):
    result = subprocess.run(
        [_require_node(), str(script_path), *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, (
        "node 执行失败 rc=%s\nstdout:\n%s\nstderr:\n%s"
        % (result.returncode, result.stdout[-1500:], result.stderr[-3000:])
    )
    return result.stdout


# ── 1. 源码契约：60s 定时器 / visibilitychange / stale 文案 ───────────
class TestSourceContract:
    @pytest.fixture(scope="class")
    def html_text(self):
        return _read(HTML_PATH)

    @pytest.fixture(scope="class")
    def js_text(self):
        return _read(JS_PATH)

    def test_refresh_interval_and_stale_constants(self, js_text):
        assert "REFRESH_INTERVAL_MS" in js_text
        assert "60 * 1000" in js_text
        assert "STALE_AFTER_MS" in js_text
        assert "24 * 60 * 60 * 1000" in js_text

    def test_timer_wiring(self, js_text):
        assert "setInterval(" in js_text
        assert "clearInterval(" in js_text
        assert "startAutoRefresh" in js_text
        assert "stopAutoRefresh" in js_text
        assert "setAutoRefresh(" in js_text

    def test_visibilitychange_wiring(self, js_text):
        assert "visibilitychange" in js_text
        assert "visibilityState" in js_text
        assert "document.hidden" in js_text
        assert "onVisibilityChange" in js_text
        # 隐藏 → 停止；可见 → 刷新 + 恢复定时器（源码级契约）
        hidden_branch = js_text[js_text.index("function onVisibilityChange") :]
        hidden_branch = hidden_branch[: hidden_branch.index("function boot")]
        assert "stopAutoRefresh()" in hidden_branch
        assert "loadHealthReport({ silent: true })" in hidden_branch
        assert "startAutoRefresh()" in hidden_branch

    def test_stale_and_unknown_copy(self, js_text):
        assert ">24h，不判失败" in js_text
        assert "新鲜 " in js_text
        assert "过期 " in js_text
        assert "未知（缺 generated_at，不猜）" in js_text
        assert "generated_at" in js_text

    def test_html_auto_refresh_and_freshness_controls(self, html_text):
        for element_id in (
            "generatedBadge",
            "freshnessBadge",
            "btnAutoRefresh",
            "autoRefreshHint",
        ):
            assert ('id="%s"' % element_id) in html_text, element_id
        for marker in ("自动刷新", "60s", "新鲜度", "不判失败", "generated_at"):
            assert marker in html_text, marker
        # 静态首屏也有「新鲜度 —」，空态不空白
        assert "新鲜度 —" in html_text
        assert "generated_at —" in html_text

    def test_html_still_has_four_sections(self, html_text):
        assert html_text.count("<h2") == 4  # 沿用 Phase5-B 结构，shortscreen 仍不需要

    def test_empty_and_insufficient_still_explicit(self, html_text, js_text):
        assert "暂无周报" in html_text
        assert "样本不足" in html_text
        assert "暂无周报" in js_text
        assert "样本不足" in js_text


# ── 2. 只读边界不回退：唯一 GET / 无 POST / 不落本地存储 ──────────────
class TestReadonlyStillHolds:
    @pytest.fixture(scope="class")
    def html_text(self):
        return _read(HTML_PATH)

    @pytest.fixture(scope="class")
    def js_text(self):
        return _read(JS_PATH)

    def test_single_fetch_get_only(self, js_text):
        assert js_text.count("fetch(") == 1
        assert HEALTH_URL in js_text
        assert 'method: "GET"' in js_text
        assert not re.search(
            r'method\s*:\s*["\'](POST|PUT|PATCH|DELETE)["\']', js_text
        )
        assert not re.search(r'["\'](POST|PUT|PATCH|DELETE)["\']', js_text)

    def test_no_local_storage_or_write_calls(self, js_text, html_text):
        for text in (js_text, html_text):
            for forbidden in (
                "localStorage",
                "sessionStorage",
                "document.cookie",
                "XMLHttpRequest",
                "sendBeacon",
                "WebSocket",
                "download",
                "Blob(",
                "/api/",
            ):
                assert forbidden not in text, forbidden
        assert "<form" not in html_text
        assert 'type="submit"' not in html_text

    def test_no_service_start_or_dashboard_import(self, js_text, html_text):
        for text in (js_text, html_text):
            for forbidden in (
                "serve_forever",
                "HTTPServer",
                "createServer",
                ".listen(",
                "dashboard_api",
                "portal_api",
            ):
                assert forbidden not in text, forbidden


# ── 3. Node 新鲜度纯函数契约 ─────────────────────────────────────────
_FRESHNESS_CONTRACT = r"""
const api = require(__JS_PATH__);
const f = api.freshness;
const base = new Date(2026, 9, 31, 20, 0, 0).getTime();
const hour = 60 * 60 * 1000;
const day = 24 * hour;
function fresh(offset) { return f.computeFreshness("2026-10-31 20:00:00", base + offset); }
const out = {
  intervalMs: f.intervalMs,
  staleAfterMs: f.staleAfterMs,
  parse_local: f.parseGeneratedAt("2026-10-31 20:00:00"),
  parse_iso: f.parseGeneratedAt("2026-10-31T20:00:00"),
  parse_invalid: f.parseGeneratedAt("not-a-date"),
  parse_empty: f.parseGeneratedAt(""),
  parse_nonstring: f.parseGeneratedAt(null),
  fresh_30s: fresh(30 * 1000),
  fresh_5m: fresh(5 * 60 * 1000),
  fresh_3h: fresh(3 * hour),
  fresh_boundary: fresh(day),
  stale_over: fresh(day + 1),
  stale_25h: fresh(25 * hour),
  future: fresh(-5 * 60 * 1000),
  unknown_null: f.computeFreshness(null, base),
  unknown_invalid: f.computeFreshness("bad", base),
  age_now: f.formatAge(0),
  age_1h: f.formatAge(hour),
  age_bad: f.formatAge(-1),
  has_set_auto: typeof f.setAutoRefresh,
  has_visibility: typeof f.onVisibilityChange,
  api_keys: Object.keys(api).sort(),
  freshness_enumerable: Object.keys(api).indexOf("freshness") !== -1,
  boot_type: typeof api.boot,
  summarize_type: typeof api.summarizeReport
};
console.log(JSON.stringify(out));
"""


class TestNodeFreshnessContract:
    @pytest.fixture(scope="class")
    def contract(self):
        script = _FRESHNESS_CONTRACT.replace("__JS_PATH__", json.dumps(JS_PATH))
        return json.loads(_node_eval(script))

    def test_policy_constants(self, contract):
        assert contract["intervalMs"] == 60000
        assert contract["staleAfterMs"] == 86400000

    def test_generated_at_parsing(self, contract):
        base = contract["parse_local"]
        assert isinstance(base, (int, float))
        assert contract["parse_iso"] == base
        assert contract["parse_invalid"] is None
        assert contract["parse_empty"] is None
        assert contract["parse_nonstring"] is None

    def test_fresh_within_24h(self, contract):
        fresh = contract["fresh_30s"]
        assert fresh["status"] == "fresh"
        assert fresh["tone"] == "ok"
        assert fresh["ageMs"] == 30000
        assert fresh["ageText"] == "刚刚"
        assert "新鲜" in fresh["label"]
        assert contract["fresh_5m"]["ageText"] == "5 分钟前"
        assert contract["fresh_3h"]["ageText"] == "3 小时前"
        # 恰好 24h：不判过期（严格 > 24h 才 stale）
        assert contract["fresh_boundary"]["status"] == "fresh"

    def test_stale_after_24h_but_not_failure(self, contract):
        stale = contract["stale_over"]
        assert stale["status"] == "stale"
        assert stale["tone"] == "warn"
        assert "过期" in stale["label"]
        assert "不判失败" in stale["label"]
        assert stale["ageMs"] == 86400001
        assert contract["stale_25h"]["ageText"] == "25 小时前"

    def test_future_timestamp_not_stale(self, contract):
        future = contract["future"]
        assert future["status"] == "fresh"
        assert future["ageMs"] == 0
        assert future["ageText"] == "刚刚"

    def test_unknown_when_missing_or_invalid(self, contract):
        unknown = contract["unknown_null"]
        assert unknown["status"] == "unknown"
        assert unknown["tone"] == "empty"
        assert "未知" in unknown["label"]
        assert "不猜" in unknown["label"]
        assert contract["unknown_invalid"]["status"] == "unknown"

    def test_format_age(self, contract):
        assert contract["age_now"] == "刚刚"
        assert contract["age_1h"] == "1 小时前"
        assert contract["age_bad"] == "—"
        assert contract["has_set_auto"] == "function"
        assert contract["has_visibility"] == "function"

    def test_existing_export_contract_unchanged(self, contract):
        assert contract["api_keys"] == FROZEN_API_KEYS
        assert contract["freshness_enumerable"] is False
        assert contract["boot_type"] == "function"
        assert contract["summarize_type"] == "function"


# ── 4. Node 运行时契约：定时器/开关/visibilitychange 真实行为 ─────────
_BOOT_PROBE_TEMPLATE = r"""
const jsPath = process.argv[2];
const payload = JSON.stringify({
  schema: "strategy_health_weekly/v1", status: "OK", readonly: true,
  generated_at: "2026-10-31 20:00:00", asof: "20260124",
  period: { start: "20260118", end: "20260124", trading_days: 5, rows: 10 },
  summary: { strategies: 1, strategies_with_samples: 1, bias_tickets: 0 },
  health_levels: { green: 1, yellow: 0, red: 0, insufficient: 0 },
  strategies: [{ strategy_id: "kg_boost", health_level: "green",
    health_reasons: [], sample_count: 10,
    windows: { t1: { count: 10, win_rate: 100.0, avg_ret: 1.0 },
               t5: { count: 10, win_rate: 100.0, avg_ret: 2.0 } },
    max_drawdown: { status: "not_provided", max_drawdown: null },
    turnover: { available: false }, cost: { available: false },
    divergence_count: 0, divergence_rate: 0.0, regime_breakdown: {},
    regime_status: "insufficient",
    card: { present: true, display_name: "KG", owner: "quant-kg" },
    weekly: { trading_days: 5 }, bias_ticket_ids: [] }],
  bias_tickets: [], notes: []
});
const intervals = [];
const cleared = [];
const listeners = {};
const button = {
  textContent: "", attrs: {},
  setAttribute: function (key, value) { this.attrs[key] = value; },
  addEventListener: function (event, handler) { this.onclick = handler; }
};
let fetches = 0;
global.fetch = function () {
  fetches++;
  return Promise.resolve({
    status: 200, ok: true,
    text: function () { return Promise.resolve(payload); }
  });
};
global.setInterval = function (handler, milliseconds) {
  var handle = { id: intervals.length + 1, fn: handler };
  intervals.push({ ms: milliseconds, handle: handle });
  return handle;
};
global.clearInterval = function (handle) { cleared.push(handle && handle.id); };
global.document = {
  readyState: "complete", hidden: false, visibilityState: "visible",
  addEventListener: function (event, handler) { listeners[event] = handler; },
  getElementById: function (id) { return id === "btnAutoRefresh" ? button : null; }
};
const api = require(jsPath);
function tick() {
  return new Promise(function (resolve) { setTimeout(resolve, 30); });
}
(async function () {
  await tick();
  const out = {
    intervalMs: intervals.map(function (item) { return item.ms; }),
    initialFetches: fetches,
    visibilityListener: typeof listeners.visibilitychange,
    buttonText: button.textContent,
    enabledAttr: button.attrs["aria-pressed"],
    buttonEvents: typeof button.onclick,
    apiKeys: Object.keys(api).length,
    freshnessHiddenFromKeys: Object.keys(api).indexOf("freshness") === -1,
    freshnessType: typeof api.freshness
  };
  button.onclick(); /* 关闭自动刷新 */
  out.afterDisableText = button.textContent;
  out.afterDisableAttr = button.attrs["aria-pressed"];
  out.clearedAfterDisable = cleared.length;
  button.onclick(); /* 重新打开（触发一次 silent 刷新） */
  await tick();
  out.afterEnableText = button.textContent;
  out.fetchesAfterEnable = fetches;
  document.hidden = true;
  document.visibilityState = "hidden";
  listeners.visibilitychange();
  out.clearedAfterHidden = cleared.length;
  const beforeHiddenTick = fetches;
  intervals[intervals.length - 1].handle.fn(); /* 隐藏时定时器回调不应刷新 */
  await tick();
  out.fetchesWhileHiddenTick = fetches - beforeHiddenTick;
  document.hidden = false;
  document.visibilityState = "visible";
  const beforeVisible = fetches;
  listeners.visibilitychange();
  await tick();
  out.fetchesOnVisible = fetches - beforeVisible;
  out.intervalsTotal = intervals.length;
  out.lastIntervalMs = intervals[intervals.length - 1].ms;
  console.log(JSON.stringify(out));
})().catch(function (error) {
  console.error(error && error.stack || error);
  process.exit(1);
});
"""


class TestNodeBootWiring:
    @pytest.fixture(scope="class")
    def probe(self, tmp_path_factory):
        script_path = tmp_path_factory.mktemp("probe") / "boot_probe.js"
        script_path.write_text(_BOOT_PROBE_TEMPLATE, encoding="utf-8")
        output = _node_script(script_path, JS_PATH)
        return json.loads(output)

    def test_60s_timer_started_on_boot(self, probe):
        assert probe["intervalMs"] == [60000]
        assert probe["initialFetches"] == 1  # boot 时首次 GET（唯一网络出口）
        assert probe["lastIntervalMs"] == 60000

    def test_visibilitychange_listener_registered(self, probe):
        assert probe["visibilityListener"] == "function"

    def test_toggle_control(self, probe):
        assert probe["buttonEvents"] == "function"
        assert probe["buttonText"] == "自动刷新：开（60s）"
        assert probe["enabledAttr"] == "true"
        assert probe["afterDisableText"] == "自动刷新：关"
        assert probe["afterDisableAttr"] == "false"
        assert probe["clearedAfterDisable"] >= 1  # 关闭 → clearInterval
        assert probe["afterEnableText"] == "自动刷新：开（60s）"
        assert probe["fetchesAfterEnable"] == 2  # 重开 → 立即 silent 刷新

    def test_hidden_pauses_and_visible_refreshes(self, probe):
        assert probe["clearedAfterHidden"] >= 2  # 隐藏 → 停止定时器
        assert probe["fetchesWhileHiddenTick"] == 0  # 隐藏时定时器回调不刷新
        assert probe["fetchesOnVisible"] == 1  # 回到页面立即刷新
        assert probe["intervalsTotal"] == 3  # 回页后定时器恢复
        assert probe["intervalMs"][0] == 60000

    def test_contract_unchanged_under_runtime(self, probe):
        assert probe["apiKeys"] == 23
        assert probe["freshnessHiddenFromKeys"] is True
        assert probe["freshnessType"] == "object"


# ── 5. node --check / 载入不落盘 / 只读边界 ──────────────────────────
class TestNodeAndReadonly:
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
            "console.log(typeof api.boot + ':' + typeof api.freshness);"
            % json.dumps(JS_PATH)
        )
        output = _node_eval(script, cwd=str(tmp_path))
        assert output.strip() == "function:object"
        assert os.listdir(str(tmp_path)) == []

    def test_dashboard_api_not_touched(self):
        if not os.path.exists(DASHBOARD_API_PATH):
            pytest.skip("dashboard_api.py 不存在")
        before = os.path.getsize(DASHBOARD_API_PATH), os.path.getmtime(
            DASHBOARD_API_PATH
        )
        _read(HTML_PATH)
        _read(JS_PATH)
        after = os.path.getsize(DASHBOARD_API_PATH), os.path.getmtime(
            DASHBOARD_API_PATH
        )
        assert before == after
