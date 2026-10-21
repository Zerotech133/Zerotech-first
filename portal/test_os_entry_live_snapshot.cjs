/* WO-OS-14 v9 真实快照自测：Node http 模块直连 127.0.0.1:8080（不走系统代理、不用 fetch+代理环境）。
 * 步骤：①GET /os-entry 断言 200 且含 os_zhuge_adapter.js（硬断言）；
 *      ②依次 GET 六源收集真实 JSON；③require 本地 adapter 跑 buildBrief(raw, new Date())；
 *      ④断言结构化输出契约：engine='rule-based'/suggestions 数组/gaps 数组/business_day=北京今天/
 *        每条 suggestion 有 id/domain/priority/reason/source。
 * 降级语义：任一源 5xx/超时/连接失败 → 该源记入 degraded，不判 FAIL（网络波动不是页面缺陷），
 * 但断言该源出现在 gaps（usable 登记或缺源建议）；os-entry 200 与 adapter 可 require 是硬断言。
 * 运行：node test_os_entry_live_snapshot.cjs */
'use strict';
const http = require('http');
const path = require('path');
const assert = require('assert');

const HOST = '127.0.0.1';
const PORT = 8080;
const TIMEOUT_MS = 10000;
const SOURCES = [
  ['os', '/api/os'],
  ['home', '/api/home'],
  ['alerts', '/api/alerts'],
  ['notify', '/api/notify/queue'],
  ['tasks', '/api/org/tasks'],
  ['capability', '/api/capability/board']
];

/* Node http 模块对 IP 直连不读代理环境变量，天然绕过系统代理；显式清空代理环境双保险 */
delete process.env.http_proxy; delete process.env.HTTP_PROXY;
delete process.env.https_proxy; delete process.env.HTTPS_PROXY;
delete process.env.all_proxy; delete process.env.ALL_PROXY;

function get(pathname) {
  return new Promise((resolve) => {
    const req = http.get({ host: HOST, port: PORT, path: pathname, timeout: TIMEOUT_MS }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { body += c; });
      res.on('end', () => resolve({ ok: true, status: res.statusCode, body }));
    });
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, status: 0, error: 'timeout' }); });
    req.on('error', (e) => resolve({ ok: false, status: 0, error: e.message }));
  });
}

async function main() {
  const report = { osEntry: null, sources: [], degraded: [] };

  // ① os-entry 硬断言：200 + 页面挂 os_zhuge_adapter.js
  const entry = await get('/os-entry');
  report.osEntry = entry.status;
  assert.strictEqual(entry.status, 200, '硬断言失败：/os-entry 状态码=' + entry.status);
  assert.ok(entry.body.includes('os_zhuge_adapter.js'), '硬断言失败：/os-entry 页面未引用 os_zhuge_adapter.js');
  console.log('ok /os-entry 200 且含 os_zhuge_adapter.js');

  // ② 六源真实 JSON（5xx/超时不 FAIL，记 degraded）
  const raw = {};
  for (const [key, ep] of SOURCES) {
    const r = await get(ep);
    report.sources.push({ key, ep, status: r.status, ok: r.ok });
    if (!r.ok || r.status >= 500 || r.status === 0) {
      report.degraded.push(key);
      raw[key] = { ok: false };
      console.log('degraded ' + key + ' ' + ep + ' → ' + (r.ok ? 'HTTP ' + r.status : r.error) + '（记入 gaps，不判 FAIL）');
      continue;
    }
    try { raw[key] = JSON.parse(r.body); }
    catch (e) {
      report.degraded.push(key);
      raw[key] = { ok: false };
      console.log('degraded ' + key + ' ' + ep + ' → JSON 解析失败：' + e.message + '（记入 gaps，不判 FAIL）');
    }
  }

  // ③ 本地 adapter + 真实六源
  const adapter = require(path.join(__dirname, 'static', 'os_zhuge_adapter.js')); // 硬断言：可 require
  console.log('ok static/os_zhuge_adapter.js 可 require');
  const brief = adapter.buildBrief(raw, new Date());

  // ④ 结构化输出契约
  assert.strictEqual(brief.engine, 'rule-based', 'engine 应为 rule-based');
  assert.ok(Array.isArray(brief.suggestions), 'suggestions 必须是数组');
  assert.ok(Array.isArray(brief.gaps), 'gaps 必须是数组');

  // 北京业务日 = 今天（UTC+8 换算，不依赖宿主时区）
  const t = Date.now() + 8 * 3600000;
  const bjDay = new Date(t).toISOString().slice(0, 10);
  assert.strictEqual(brief.business_day, bjDay, 'business_day 应为北京今天 ' + bjDay + '，实际 ' + brief.business_day);

  for (const s of brief.suggestions) {
    for (const f of ['id', 'domain', 'priority', 'reason', 'source'])
      assert.ok(s[f] !== undefined && s[f] !== null && s[f] !== '', 'suggestion ' + (s.id || '?') + ' 缺字段 ' + f);
  }

  // 降级源必须显式进 gaps（adapter usable() 对缺失/ok:false 登记"待核"）
  for (const key of report.degraded)
    assert.ok(brief.gaps.some((g) => g.source === key), '降级源 ' + key + ' 未出现在 gaps');

  // 摘要
  console.log('PASS WO-OS-14 真实快照：business_day=' + brief.business_day +
    ' 建议=' + brief.suggestions.length + ' 条 gaps=' + brief.gaps.length + ' 条' +
    ' 降级源=' + (report.degraded.length ? report.degraded.join(',') : '无'));
  for (const s of report.sources)
    console.log('  source-status ' + s.key + ' ' + s.ep + ' → ' + s.status);
  if (brief.suggestions.length)
    console.log('  top建议 ' + brief.suggestions.slice(0, 3).map((s) => '[' + s.priority + ']' + s.id + ' ' + s.reason.slice(0, 40)).join(' | '));
  if (brief.gaps.length)
    console.log('  gaps ' + brief.gaps.map((g) => g.source).join(','));
}

main().catch((e) => { console.error('FAIL WO-OS-14：' + e.message); process.exit(1); });
