"""Offline assertions for the real quant portal alert loader (Node.js required)."""

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
const script = new vm.Script(page.slice(start, end) + '\nloadAlerts();');

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
    setTimeout: () => 0,
    fetch,
    document: { getElementById: id => elements[id] },
    esc: value => String(value ?? '').replace(/[&<>"]/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'
    })[ch]),
  };
  const requestCount = events.length;
  for (let index = 0; index < requestCount; index += 1) {
    await script.runInNewContext(context);
  }
  return elements;
}

(async () => {
  const row = { name: '真实预警', value: '3', sub: '来源', href: '/watch' };
  const success = { ts: '2026-10-19 10:00', alerts: [row] };
  const normal = await render([{ data: success }]);
  assert.match(normal['al-ts'].textContent, /快照 2026-10-19 10:00 · 共 1 条/);
  assert.equal(normal['k-alerts'].textContent, '1 条');
  assert.match(normal['al-list'].innerHTML, /真实预警/);

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
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


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
