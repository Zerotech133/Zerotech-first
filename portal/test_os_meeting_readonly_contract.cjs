'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const filename = path.join(__dirname, 'static', 'os_meeting_readonly_contract.js');
const script = fs.readFileSync(filename, 'utf8');
const { build } = require('./static/os_meeting_readonly_contract.js');
const at = '2026-10-18T16:30:00Z'; // 北京时间 10 月 19 日 00:30
const endpointList = ['/api/os', '/api/home', '/api/alerts', '/api/notify/queue', '/api/org/tasks', '/api/capability/board'];
function byId(result, id) { return result.cards.find(card => card.id === id); }
function deepFreeze(x) {
  if (x && typeof x === 'object') {
    Object.values(x).forEach(deepFreeze);
    Object.freeze(x);
  }
  return x;
}
const browser = { window: {} };
vm.runInNewContext(script, browser);
assert.equal(typeof browser.window.OSMeetingReadonlyContract.build, 'function');
assert.doesNotMatch(script, /\bfetch\s*\(|XMLHttpRequest|sendBeacon|\bPOST\b|\/bridge\/act|innerHTML|eval\s*\(/);
assert.throws(() => build({}, 'invalid'), RangeError);
const empty = build({}, at);
assert.deepEqual(Object.keys(empty), ['observedAt', 'cards', 'pointers']);
assert.equal(empty.observedAt, new Date(at).toISOString());
assert.equal(empty.cards.length, 10);
assert.equal(empty.pointers.length, 6);
assert.deepEqual(empty.pointers.map(p => p.source), endpointList);
assert.ok(empty.pointers.every(p => p.readonly === true && typeof p.selector === 'string' && !('method' in p)));
assert.ok(empty.cards.every(c => c.status === 'unknown' && c.asOf === null));
assert.ok(empty.cards.every(c => c.evidence.length && c.next && !('href' in c) && !('action' in c)));
assert.equal(byId(empty, 'source:os').pointerId, 'os');
assert.equal(byId(empty, 'my-approvals').pointerId, null);
const input = deepFreeze({
  os: { ok: true, ts: '2026-10-19 09:00', todo: { health: 99 }, coding: { commits_today: 77 },
    biz: { near: [{ topic: '旧商务记录', date: '2026-10-19' }], notes: [{ title: '在研' }] } },
  home: { ok: true, now: '2026-10-19 09:00', calendar: { ok: true, items: [{ what: '项目节点', day: '10-20', in_days: -1 }] } },
  alerts: { ok: true, ts: '2026-10-19 09:00', source_health: { checked_at: '2026-10-19 09:00', sources: {
    premarket: { status: 'ok', as_of: '2026-10-18T23:00:00+08:00' },
    weak: { status: 'stale', as_of: '2026-10-19T00:20:00+08:00' }
  } } },
  notify: { ok: true, generated_at: '2026-10-19T09:00:00+08:00', errors: ['partial'],
    items: [{ title: '审批通过', state: 'review', reason: '<img src=x onerror=alert(1)>', href: 'javascript:alert(1)' }],
    channels_errors: [], meta: { truncated: false } },
  tasks: { ok: true, count: 20, tasks: [{ title: '仅在原始行的秘密工单标题', status: 'open' }] },
  capability: { ok: true, production_lines: [{ artifact_count: 300, approval_count: 500 }] }
});
const before = JSON.stringify(input);
const result = build(input, at);
assert.equal(JSON.stringify(input), before, '不可修改输入对象');
assert.deepEqual(build(input, at), result, '相同输入和时钟应产生相同输出');
assert.equal(byId(result, 'source:os').status, 'stale');
assert.equal(byId(result, 'source:home').status, 'limited');
assert.equal(byId(result, 'source:alerts').status, 'stale');
assert.equal(byId(result, 'source:alerts').asOf, '2026-10-18T15:00:00.000Z');
assert.equal(byId(result, 'source:notify').status, 'partial');
assert.equal(byId(result, 'source:tasks').status, 'limited');
assert.equal(byId(result, 'source:capability').status, 'limited');
for (const id of ['personal-calendar', 'business-opportunities', 'my-approvals', 'delivery-receipts']) {
  const card = byId(result, id);
  assert.equal(card.status, 'unknown', id + ' 缺源不能推断为零或已完成');
  assert.equal(card.asOf, null);
  assert.equal(card.pointerId, null);
}
assert.ok(result.cards.filter(c => c.asOf !== null).every(c => c.id === 'source:alerts'));
const serialized = JSON.stringify(result);
for (const value of ['旧商务记录', '审批通过', '仅在原始行的秘密工单标题', 'javascript:', '<img', '99', '77', '300', '500']) {
  assert.ok(!serialized.includes(value), '原始行内容与计数不能进入白板契约：' + value);
}
assert.ok(Object.isFrozen(result) && Object.isFrozen(result.cards) && Object.isFrozen(result.cards[0].evidence) && Object.isFrozen(result.pointers[0]));
assert.throws(() => { result.cards[0].status = 'ok'; }, TypeError);
assert.throws(() => { result.cards[0].evidence.push('伪造证据'); }, TypeError);
assert.throws(() => { result.pointers[0].source = '/api/approve'; }, TypeError);
assert.equal(byId(build({ os: { ok: false, biz: input.os.biz }, notify: { ok: true, items: [] },
  home: { calendar: { ok: false, items: [] } }, tasks: { tasks: null }, capability: { production_lines: null } }, at), 'source:os').status, 'unknown');
for (const id of ['home', 'notify', 'tasks', 'capability']) {
  assert.equal(byId(build({ home: { calendar: {} }, notify: { items: [] }, tasks: { tasks: {} },
    capability: { production_lines: {} } }, at), 'source:' + id).status, 'unknown');
}
const partial = build({ notify: { items: [], errors: [], meta: { truncated: true } },
  alerts: { source_health: { sources: { one: { status: 'ok', as_of: null } } } } }, at);
assert.equal(byId(partial, 'source:notify').status, 'partial');
assert.equal(byId(partial, 'source:alerts').status, 'partial');
assert.equal(byId(partial, 'source:alerts').asOf, null);
const crossDay = build({ alerts: { source_health: { sources: {
  one: { status: 'ok', as_of: '2026-10-18T23:30:00+08:00' }
} } }, home: { calendar: { items: [{ day: '10-19', in_days: 1 }] } } }, at);
assert.equal(byId(crossDay, 'source:alerts').asOf, '2026-10-18T15:30:00.000Z');
assert.equal(byId(crossDay, 'source:alerts').status, 'limited');
assert.equal(byId(build({ alerts: { source_health: { sources: {
  one: { status: 'ok', as_of: '2026-10-18T23:30:00+08:00' }
} } } }, '2026-10-19T16:00:00Z'), 'source:alerts').status, 'stale', '北京跨日后源时间超过 24 小时');
assert.equal(byId(build({ alerts: { source_health: { sources: {
  one: { status: 'ok', as_of: '2026-02-30 25:99' }
} } } }, at), 'source:alerts').status, 'partial', '非法业务时间不作 asOf');
const malicious = build({ notify: { items: [{ title: '<script>alert(1)</script>', state: 'review' }], errors: [] },
  tasks: { tasks: [{ title: 'javascript:alert(1)', status: 'open' }] } }, at);
assert.ok(!JSON.stringify(malicious).includes('<script>'));
assert.equal(byId(malicious, 'my-approvals').status, 'unknown');
assert.ok(malicious.pointers.every(p => endpointList.includes(p.source)));
console.log('PASS 会议只读契约：六源范围、四项缺源、过期/部分/跨日、恶意文本与深层不可变');
