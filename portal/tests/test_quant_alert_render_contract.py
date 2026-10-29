"""Offline assertions for the real quant portal alert loader (Node.js required)."""

from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess

import pytest


PORTAL_HTML = Path(__file__).resolve().parents[1] / "quant.html"
NODE_SCRIPT = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const page = fs.readFileSync(process.argv[1], 'utf8');
const start = page.indexOf('  async function loadAlerts() {');
const end = page.indexOf('  loadAlerts();', start);
assert(start >= 0 && end > start, 'loadAlerts must exist in the real page');
const helperStart = page.indexOf('  function safePortalHref(value) {');
const strategyStart = page.indexOf('  async function loadStrategies() {');
const strategyEnd = page.indexOf('  loadStrategies();', strategyStart);
assert(helperStart >= 0 && strategyStart > helperStart && strategyEnd > strategyStart);
const sharedHelper = page.slice(helperStart, page.indexOf('  const ST_CN', helperStart));
const strategiesSource = page.slice(strategyStart, strategyEnd);
const loaderSource = page.slice(page.lastIndexOf('  let latestAlertRequest', start), end);
assert(loaderSource.startsWith('  let latestAlertRequest = 0;'), 'generation fence must exist');
// 2026-10-26 WO-UPGRADE-20261026：loadAlerts 现引用页面级常量 LV_CN（第 595 行，
// 位于 sharedHelper 截取区间之后）。此前 harness 未注入 → VM 内 ReferenceError 被
// loadAlerts 自己的 catch 吞成「预警数据不可用」，测试恒红。这里从真实页面按源码
// 抽取 LV_CN 字面量注入，不硬编码、不改被测业务逻辑。
const lvCnStart = page.indexOf('  const LV_CN = ');
assert(lvCnStart >= 0, 'LV_CN must exist in the real page');
const lvCnSource = page.slice(lvCnStart, page.indexOf('\n', lvCnStart));
// 固定时钟：fixture 快照日期是合成的，注入固定 Date 使用例不随真实时间漂移
// （页面将来若加客户端新鲜度判断，也仍落在窗口内），保证确定性。
const FIXED_NOW = new Date('2026-10-19T11:30:00+08:00');
class FixedDate extends Date {
  constructor(...args) { super(...(args.length ? args : [FIXED_NOW.getTime()])); }
  static now() { return FIXED_NOW.getTime(); }
}
const script = new vm.Script(lvCnSource + sharedHelper + loaderSource + '\nloadAlerts');

async function render(events) {
  const elements = Object.fromEntries(['al-ts', 'k-alerts', 'al-list'].map(id => [
    id, { textContent: '初始', innerHTML: '初始' }
  ]));
  const fetch = async (url) => {
    assert.equal(url, '/api/alerts');
    const event = events.shift();
    assert(event, 'unexpected request');
    if (event.error) throw new Error('network unavailable');
    return { ok: event.ok !== false, json: async () => event.data };
  };
  const context = {
    AbortController,
    URL,
    Date: FixedDate,
    location: { origin: 'https://portal.example.test' },
    setTimeout: () => 0,
    fetch,
    document: { getElementById: id => elements[id] },
    esc: value => String(value ?? '').replace(/[&<>"]/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'
    })[ch]),
  };
  const requestCount = events.length;
  vm.createContext(context);
  const loadAlerts = script.runInContext(context);
  for (let index = 0; index < requestCount; index += 1) {
    await loadAlerts();
  }
  return elements;
}

async function renderStrategies(items) {
  const elements = Object.fromEntries(['strat-grid', 'k-strat', 'k-strat-sub', 'k-weak'].map(id => [
    id, { textContent: '初始', innerHTML: '初始' }
  ]));
  const context = {
    AbortController,
    URL,
    Date: FixedDate,
    location: { origin: 'https://portal.example.test' },
    setTimeout: () => 0,
    fetch: async url => {
      assert.equal(url, '/api/strategies');
      return { ok: true, json: async () => ({ strategies: items }) };
    },
    document: { getElementById: id => elements[id] },
    esc: value => String(value ?? '').replace(/[&<>"]/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'
    })[ch]),
    ST_CN: { live: '实盘', shadow: '影子', tool: '支撑', paused: '暂停' },
  };
  vm.createContext(context);
  await new vm.Script(sharedHelper + strategiesSource + '\nloadStrategies()').runInContext(context);
  return elements;
}

async function renderOutOfOrder(firstFails) {
  const elements = Object.fromEntries(['al-ts', 'k-alerts', 'al-list'].map(id => [
    id, { textContent: '初始', innerHTML: '初始' }
  ]));
  let resolveFirst;
  const firstResponse = new Promise(resolve => { resolveFirst = resolve; });
  let requests = 0;
  const context = {
    AbortController,
    URL,
    Date: FixedDate,
    location: { origin: 'https://portal.example.test' },
    setTimeout: () => 0,
    fetch: async () => {
      requests += 1;
      if (requests === 1) return firstResponse;
      if (requests === 2) {
        return firstFails ? { ok: true, json: async () => ({
          ts: '新快照', alerts: [{ name: '新预警', value: '2', href: '/watch' }]
        }) } : { ok: false };
      }
      throw new Error('unexpected request');
    },
    document: { getElementById: id => elements[id] },
    esc: value => String(value ?? '').replace(/[&<>"]/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'
    })[ch]),
  };
  vm.createContext(context);
  const loader = new vm.Script(lvCnSource + sharedHelper + loaderSource + '\nloadAlerts');
  const loadAlerts = loader.runInContext(context);
  const older = loadAlerts();
  const newer = loadAlerts();
  await newer;
  const snapshotAfterNewer = Object.fromEntries(
    Object.entries(elements).map(([key, element]) => [
      key, { textContent: element.textContent, innerHTML: element.innerHTML }
    ])
  );
  resolveFirst(firstFails ? { ok: false } : { ok: true, json: async () => ({
    ts: '旧快照', alerts: [{ name: '旧预警', value: '1', href: '/watch' }]
  }) });
  await older;
  assert.deepEqual(elements, snapshotAfterNewer, 'older request must never overwrite newer result');
  if (firstFails) {
    assert.match(elements['al-list'].innerHTML, /新预警/);
    assert.equal(elements['k-alerts'].textContent, '1 条');
  } else {
    assert.match(elements['al-list'].innerHTML, /预警服务离线或响应异常/);
    assert.equal(elements['k-alerts'].textContent, '—');
  }
}

(async () => {
  const row = { name: '真实预警', value: '3', sub: '来源', href: '/watch' };
  // 固定 fixture 快照（合成日期，不读真实数据盘/不随真实时钟变化）
  const FIXTURE_TS = '2026-10-19 10:00';
  const success = { ts: FIXTURE_TS, alerts: [row] };
  const normal = await render([{ data: success }]);
  assert.match(normal['al-ts'].textContent,
               new RegExp('快照 ' + FIXTURE_TS + ' · 共 1 条'));
  assert.equal(normal['k-alerts'].textContent, '1 条');
  assert.match(normal['al-list'].innerHTML, /真实预警/);
  assert.match(normal['al-list'].innerHTML, /href="\/watch"/);

  // Parse the actual attribute boundaries, not only an escaped substring.
  const attributeNames = html => [...html.matchAll(/<a\s+([^>]*)>/g)]
    .flatMap(match => [...match[1].matchAll(/([^\s=]+)="([^"]*)"/g)]
      .map(attribute => attribute[1]));
  const producedRows = [];
  for (const hostileHref of [
    "/watch' onclick='alert(1)", 'javascript:alert(1)', 'data:text/html,attack',
    '//attacker.example', '/watch%27%20onclick%3Dalert(1)', '/watch\\evil',
    '/watch\n', '/watch%0aonclick=attack', '/%2f%2fattacker.example',
    '/watch%2e%2e/evil', 'https://attacker.example/watch',
    'https://portal.example.test.evil/watch'
  ]) {
    const hostile = await render([{ data: { ts: '不可信链接', alerts: [{ ...row, href: hostileHref }] } }]);
    assert.match(hostile['al-list'].innerHTML, /href="#"/);
    assert.deepEqual(attributeNames(hostile['al-list'].innerHTML), ['class', 'href']);
    assert.doesNotMatch(hostile['al-list'].innerHTML, /onclick|javascript:|attacker\.example/);
    producedRows.push({ html: hostile['al-list'].innerHTML, expectedHref: '#' });
  }
  const safeQuery = await render([{ data: { ts: '站内下钻', alerts: [{ ...row, href: '/watch?id=1&pool=main' }] } }]);
  assert.match(safeQuery['al-list'].innerHTML, /href="\/watch\?id=1&amp;pool=main"/);
  const sameOrigin = await render([{ data: { alerts: [{ ...row, href: 'https://portal.example.test/watch?id=2' }] } }]);
  assert.match(sameOrigin['al-list'].innerHTML, /href="\/watch\?id=2"/);
  assert.deepEqual(attributeNames(sameOrigin['al-list'].innerHTML), ['class', 'href']);
  producedRows.push({ html: normal['al-list'].innerHTML, expectedHref: '/watch' });
  producedRows.push({ html: safeQuery['al-list'].innerHTML, expectedHref: '/watch?id=1&pool=main' });
  producedRows.push({ html: sameOrigin['al-list'].innerHTML, expectedHref: '/watch?id=2' });

  for (const badDelta of ["1' onclick='alert(1)", 'NaN', Infinity, -Infinity, 1000001, -1000001]) {
    const result = await render([{ data: { alerts: [{ ...row, delta: badDelta }] } }]);
    assert.equal(result['k-alerts'].textContent, '1 条');
    assert.doesNotMatch(result['al-list'].innerHTML, /class='delta|onclick|Infinity|NaN/);
  }
  for (const delta of [-3, 5]) {
    const result = await render([{ data: { alerts: [{ ...row, delta }] } }]);
    assert.match(result['al-list'].innerHTML, /class='delta (up|dn)'/);
    assert.match(result['al-list'].innerHTML, new RegExp((delta > 0 ? '↗' : '↘') + Math.abs(delta)));
  }

  const empty = await render([{ data: { ts: '空快照', alerts: [] } }]);
  assert.match(empty['al-ts'].textContent, /共 0 条/);
  assert.equal(empty['k-alerts'].textContent, '0 条');
  assert.match(empty['al-list'].innerHTML, /暂无预警数据/);

  for (const bad of [null, {}, { alerts: 'invalid' }, { alerts: [null] }]) {
    const malformed = await render([{ data: success }, { data: bad }]);
    assert.equal(malformed['al-ts'].textContent, '预警数据不可用 · 快照未更新');
    assert.equal(malformed['k-alerts'].textContent, '—');
    assert.match(malformed['al-list'].innerHTML, /预警服务离线或响应异常/);
    assert.doesNotMatch(malformed['al-list'].innerHTML, /真实预警|暂无预警数据/);
  }

  for (const failure of [{ ok: false }, { error: true }]) {
    const unavailable = await render([{ data: success }, failure]);
    assert.equal(unavailable['al-ts'].textContent, '预警数据不可用 · 快照未更新');
    assert.equal(unavailable['k-alerts'].textContent, '—');
    assert.match(unavailable['al-list'].innerHTML, /预警服务离线或响应异常/);
    assert.doesNotMatch(unavailable['al-list'].innerHTML, /真实预警|暂无预警数据/);
  }
  await renderOutOfOrder(false); // Old success after newer failure.
  await renderOutOfOrder(true);  // Old failure after newer success.

  const strategy = { id: 'one', status: 'live', name: '站内策略', href: '/strategies/one' };
  const strategyNormal = await renderStrategies([strategy]);
  assert.equal(strategyNormal['k-strat'].textContent, '1 条');
  assert.match(strategyNormal['strat-grid'].innerHTML, /href="\/strategies\/one"/);
  producedRows.push({ html: strategyNormal['strat-grid'].innerHTML, expectedHref: '/strategies/one', expectedClass: 'scard' });
  for (const hostileHref of [
    "/watch' onclick='alert(1)", '/watch" onclick="alert(1)',
    'javascript:alert(1)', 'data:text/html,attack', '//attacker.example',
    '/watch%27%20onclick%3Dalert(1)', '/watch%22onclick=attack',
    '/watch%0d', 'https://attacker.example/watch'
  ]) {
    const hostile = await renderStrategies([{ ...strategy, href: hostileHref }]);
    assert.match(hostile['strat-grid'].innerHTML, /href="#"/);
    assert.doesNotMatch(hostile['strat-grid'].innerHTML, /onclick|javascript:|attacker\.example/);
    producedRows.push({ html: hostile['strat-grid'].innerHTML, expectedHref: '#', expectedClass: 'scard' });
  }
  const hostileStatus = await renderStrategies([{ ...strategy, status: '<img src=x onerror=alert(1)>' }]);
  assert.doesNotMatch(hostileStatus['strat-grid'].innerHTML, /<img|onerror=/);
  assert.match(hostileStatus['strat-grid'].innerHTML, /sdot paused/);
  const hostileExt = await renderStrategies([{ ...strategy, href: '', ext: "javascript:alert(1)" }]);
  assert.match(hostileExt['strat-grid'].innerHTML, /href="#"/);
  assert.doesNotMatch(hostileExt['strat-grid'].innerHTML, /target="_blank"|javascript:/);
  producedRows.push({ html: hostileExt['strat-grid'].innerHTML, expectedHref: '#', expectedClass: 'scard' });
  const strategySameOrigin = await renderStrategies([{ ...strategy, href: 'https://portal.example.test/watch?id=1&pool=main' }]);
  assert.match(strategySameOrigin['strat-grid'].innerHTML, /href="\/watch\?id=1&amp;pool=main"/);
  producedRows.push({ html: strategySameOrigin['strat-grid'].innerHTML, expectedHref: '/watch?id=1&pool=main', expectedClass: 'scard' });
  const extLink = await renderStrategies([{ ...strategy, href: '', ext: '/strategies' }]);
  assert.match(extLink['strat-grid'].innerHTML, /href="\/strategies" target="_blank" rel="noopener"/);
  producedRows.push({ html: extLink['strat-grid'].innerHTML, expectedHref: '/strategies', expectedClass: 'scard', expectedExternal: true });
  console.log(JSON.stringify(producedRows));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


class _AlertLinkParser(HTMLParser):
    """Collect parsed anchor attributes to detect HTML attribute injection."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[list[tuple[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record browser-like parsed anchor attributes."""
        if tag == "a":
            self.anchors.append(attrs)


def test_quant_alert_loader_offline() -> None:
    """Execute page JS with mocked fetch/DOM; never contact a real service."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node.js is required to execute the portal's real JavaScript")
    result = subprocess.run(
        [node, "-e", NODE_SCRIPT, str(PORTAL_HTML)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for rendered in json.loads(result.stdout):
        parser = _AlertLinkParser()
        parser.feed(rendered["html"])
        expected = [
            ("class", rendered.get("expectedClass", "arow")),
            ("href", rendered["expectedHref"]),
        ]
        if rendered.get("expectedExternal"):
            expected.extend([("target", "_blank"), ("rel", "noopener")])
        # 2026-10-30 P63：OS-UI 统一后 class 可能是 "ui-card scard" 等组合；
        # 断言改为「期望的 class token 是实际 class 的子集」，其余属性精确相等。
        assert len(parser.anchors) == 1, rendered
        got_map = dict(parser.anchors[0])
        for key, val in expected:
            if key == "class":
                got_tokens = set((got_map.get("class") or "").split())
                want_tokens = set((val or "").split())
                assert want_tokens <= got_tokens, (rendered, got_map)
            else:
                assert got_map.get(key) == val, (rendered, got_map)
