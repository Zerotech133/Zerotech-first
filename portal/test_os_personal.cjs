/* 个人 OS 独立只读页：口径、故障态与安全回归。 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, 'os_personal.html'), 'utf8');
const js = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(js);
assert.match(html, /height:100dvh/);
assert.match(html, /overflow:hidden/);
assert.doesNotMatch(js, /innerHTML|insertAdjacentHTML|eval\(|fetch\(['"]\/bridge\/act/);
const elements = new Map();
function el() {
  return { children: [], textContent: '', attributes: {}, classList: { add() {}, remove() {} },
    append(...nodes) { this.children.push(...nodes); }, appendChild(node) { this.children.push(node); },
    replaceChildren(...nodes) { this.children = nodes; }, addEventListener() {}, focus() {},
    setAttribute(k, v) { this.attributes[k] = v; } };
}
const document = { activeElement: null, getElementById(id) { if (!elements.has(id)) elements.set(id, el()); return elements.get(id); }, createElement: el };
const calls = [];
let replies = {};
const context = vm.createContext({ document, window: { addEventListener() {} }, console,
  AbortController, setTimeout, clearTimeout, setInterval() {},
  fetch(url) { calls.push(url); const x = replies[url]; return x instanceof Error ? Promise.reject(x) : Promise.resolve({ ok: true, json: () => Promise.resolve(x) }); },
  Intl, Date, Promise, Number, String, Object, Array });
vm.runInContext(js.replace(/refresh\(\);setInterval\(refresh,60000\);/, ''), context);
function run(expr) { return vm.runInContext(expr, context); }
const day = '2026-10-19';
assert.equal(run("isoDay('2026-02-30')"), null, '不存在的日期不能通过');
assert.equal(run("futureWithin('2026-10-18','2026-10-19',7)"), false);
assert.equal(run("futureWithin('2026-10-26','2026-10-19',7)"), true);
const os = { biz: { near: [{ date: '2026-10-18', topic: '已过期' }, { date: '2026-10-20', topic: '<img src=x onerror=alert(1)>' }], notes: [] }, coding: { last_commit: '10-18 20:00' } };
const osCards = run('fromOS')(os, day);
assert.equal(osCards.some(x => x.title.includes('已过期')), false);
assert.equal(osCards.some(x => x.status === 'ledger-unverified'), true);
assert.equal(run('fromOS')({ biz: {}, coding: {} }, day).some(x => x.title.includes('七天内无列出')), false);
const home = { calendar: { items: [{ day: '2026-10-18', in_days: 1, what: '伪未来' }, { day: '2026-10-21', in_days: -1, what: '真实未来' }] } };
assert.equal(run('fromHome')(home, day).length, 1);
assert.equal(run('fromHome')(home, day)[0].title, '真实未来');
assert.throws(() => run('fromHome')({ calendar: {} }, day));
assert.equal(run('fromAlerts')({ source_health: { sources: {} } })[0].status, 'unknown');
assert.equal(run('fromAlerts')({ source_health: { sources: { source: null } } })[0].status, 'unverified');
assert.equal(run('fromAlerts')({ source_health: { sources: { source: { status: 'ok' } } } })[0].status, 'limited-ok');
const alerts = { source_health: { sources: Object.fromEntries(Array.from({ length: 5 }, (_, i) => ['src' + i, { status: 'stale', reason: 'unproven_completion' }])) } };
assert.equal(run('fromAlerts')(alerts).at(-1).status, 'truncated');
run('render')(osCards, ['测试']);
const schedule = document.getElementById('schedule').children;
assert.equal(schedule.length, 1);
assert.match(schedule[0].children[1].children[0].textContent, /<img/);
assert.equal(schedule[0].children[1].children[0].children.length, 0, '恶意标题只能是纯文本');
run('openDetail')(osCards[0]);
assert.equal(document.getElementById('drawer-title').textContent, osCards[0].title);
assert.equal(document.getElementById('drawer-body').children.some(x => x.href), false);
async function checkRefresh() {
  replies = { '/api/os': os, '/api/home': home, '/api/alerts': alerts };
  await run('refresh')();
  assert.equal(calls.length, 3);
  assert.equal(document.getElementById('schedule').children.some(x => x.children[1].children[0].textContent === '个人日历和交通行程待接入'), true);
  assert.equal(document.getElementById('notices').children.some(x => x.children[1].children[0].textContent === '跨渠道通知收件箱待接入'), true);
  replies['/api/home'] = { calendar: {} };
  replies['/api/alerts'] = new Error('offline');
  await run('refresh')();
  assert.match(document.getElementById('source-state').textContent, /项目时点解析失败/);
  assert.match(document.getElementById('source-state').textContent, /逐源健康不可用/);
  assert.equal(document.getElementById('notices').children.some(x => x.children[1].children[0].textContent === '逐源健康读取失败'), true);
  console.log('PASS 个人 OS：日期、缺源、异常截断、XSS 文本、只读下钻、接口故障态（20+ 断言）');
}
checkRefresh().catch(e => { console.error(e); process.exitCode = 1; });
