/* 独立纯数据测试：不请求服务、不读旧台账。 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'static', 'os_integrated_projection.js'), 'utf8');
const projection = require('./static/os_integrated_projection.js');
const context = vm.createContext({ window: {}, Date, Intl });
vm.runInContext(source, context);
assert.equal(typeof context.window.OSIntegratedProjection.build, 'function');
assert.equal(typeof projection.build, 'function');
const at = '2026-10-18T16:30:00Z'; // 北京 10 月 19 日 00:30
const empty = projection.build({}, at);
assert.equal(empty.updatedAt, new Date(at).toISOString());
assert.deepEqual(Object.keys(empty), ['updatedAt', 'top', 'focus', 'time', 'delivery', 'domains', 'sourceStates']);
assert.equal(empty.top.status, 'unknown');
assert.equal(empty.top.personalCalendar, null);
assert.equal(empty.top.approvals, null);
assert.equal(empty.top.health, null);
assert.equal(empty.sourceStates.length, 6);
assert.ok(empty.sourceStates.every(x => x.status === 'unknown'));
assert.ok(empty.domains.every(x => x.status === 'entry' && x.asOf === null));
assert.equal(empty.time.length, 0);
assert.equal(empty.focus.length, 0);
const data = {
  os: { ok: true, ts: '2026-10-19 08:00', todo: { health: 99, notes_doing: 8 },
    coding: { commits_today: 10 }, biz: {
      near: [{ date: '2026-10-18', topic: '昨天' }, { date: '2026-10-19', topic: '今天' },
        { date: '2026-10-26', topic: '七日内' }, { date: '2026-10-27', topic: '七日外' },
        { date: '2026-02-30', topic: '坏日期' }],
      notes: [{ title: '在研文稿', status: '在研' }], remind_next: '2026-10-18 23:00' } },
  home: { ok: true, now: '2026-10-19 09:00', calendar: { ok: true, items: [
    { day: '10-18', in_days: 1, what: '伪未来' },
    { day: '10-20', in_days: -1, what: '项目节点' }] } },
  alerts: { ts: '2026-10-19 09:00', source_health: { checked_at: '2026-10-19T09:00:00+08:00',
    sources: { premarket: { status: 'ok', as_of: '2026-10-19T08:00:00+08:00' },
      weak: { status: 'stale', as_of: null } } } },
  notify: { ok: true, generated_at: '2026-10-19T09:00:00+08:00', errors: ['决策 DB 不可读'],
    items: [{ id: 'a', title: '待确认通知', state: 'review', created_at: '2026-10-18 20:00', send_at: '2026-10-20 09:00' },
      { id: 'b', title: '过期通知', state: 'review', send_at: '2026-10-18 20:00' },
      { id: 'c', title: '阻断通知', state: 'blocked', created_at: '2026-10-18 21:00', reason: '通道异常' },
      { id: 'd', title: '重试通知', state: 'review', retry_count: 2, send_at: '2026-10-20 09:00' }] },
  tasks: { ok: true, generated_at: '2026-10-19T09:00:00', count: 20, status_counts: { open: 20 },
    tasks: [{ task_id: 't1', title: '工单', status: 'open', created_at: '2026-10-18T10:00:00', due: '2026-10-20' },
      { task_id: 't2', title: '过期工单', status: 'open', due: '2026-10-18' }] },
  capability: { read_only: true, generated_at: '2026-10-19T09:00:00Z', production_lines: [{ label: '开发', artifact_count: 300, registered: true }] }
};
const result = projection.build(data, at);
assert.equal(result.top.status, 'partial');
assert.equal(result.top.approvals, null);
assert.equal(result.top.health, null);
assert.equal(result.sourceStates.find(x => x.id === 'source:notify').status, 'partial');
assert.match(result.sourceStates.find(x => x.id === 'source:tasks').detail, /最近/);
assert.match(result.sourceStates.find(x => x.id === 'source:capability').detail, /登记/);
assert.equal(result.time.some(x => /昨天|伪未来|七日外|坏日期/.test(x.title)), false);
assert.equal(result.time.some(x => x.title === '今天'), true);
assert.equal(result.time.some(x => x.title === '七日内'), true);
assert.equal(result.time.some(x => x.title === '项目节点' && x.status === 'project-timeline'), true);
assert.equal(result.focus.some(x => x.title === '过期通知'), false);
/* WO-OS-03: 逾期工单不再被隐藏，显式标 overdue（标题沿用源 title '过期工单'） */
assert.equal(result.focus.some(x => x.title === '过期工单' && x.status === 'overdue'), true);
/* WO-OS-03: blocked 细分——阻断通知优先级 1 且摘要点名阻断 */
const blockedRow = result.focus.find(x => x.title === '阻断通知');
assert.equal(blockedRow.status, 'blocked');
assert.equal(blockedRow.priority, 1);
assert.match(blockedRow.summary, /阻断/);
/* WO-OS-03: review+重试次数展示 */
assert.match(result.focus.find(x => x.title === '重试通知').summary, /重试 2 次/);
/* WO-OS-03: alerts as_of 新鲜度——premarket as_of 距观察钟 ~16.5h → limited-ok；weak 自身 stale 且无 as_of → stale（源报状态优先，新鲜度未知入标题） */
assert.equal(result.focus.find(x => x.title.startsWith('信号源 premarket')).status, 'limited-ok');
assert.equal(result.focus.find(x => x.title.startsWith('信号源 weak')).status, 'stale');
assert.match(result.focus.find(x => x.title.startsWith('信号源 weak')).title, /新鲜度/);
assert.equal(result.focus.find(x => x.title === '待确认通知').status, 'review');
assert.equal(result.focus.find(x => x.title === '工单').asOf, '2026-10-18T10:00:00+08:00');
assert.equal(result.focus.find(x => x.title === '信号源 premarket').asOf, '2026-10-19T00:00:00.000Z');
assert.equal(result.delivery.find(x => x.title === '在研文稿').asOf, null);
assert.equal(result.time.find(x => x.title === '项目节点').asOf, null);
assert.ok(!JSON.stringify(result).includes('99'));
assert.ok(!JSON.stringify(result).includes('300'));
for (const list of [result.focus, result.time, result.delivery, result.domains, result.sourceStates]) {
  for (const row of list) {
    assert.deepEqual(Object.keys(row), ['id', 'title', 'summary', 'status', 'source', 'asOf', 'detail', 'href', 'priority']);
    assert.ok(row.href === null || /^\/(?:dev|legion|learn|creator|quant|intel|os-entry|os-board)$/.test(row.href));
  }
}
const malformed = projection.build({ os: { ok: true, biz: {} }, home: { calendar: {} },
  notify: { ok: true, errors: [], items: null }, tasks: { ok: true, tasks: null } }, at);
assert.equal(malformed.sourceStates.find(x => x.id === 'source:os').status, 'unknown');
assert.equal(malformed.sourceStates.find(x => x.id === 'source:home').status, 'unknown');
assert.equal(malformed.sourceStates.find(x => x.id === 'source:notify').status, 'unknown');
assert.equal(malformed.focus.length, 0);
const nextDay = projection.build({ os: data.os, home: data.home }, '2026-10-19T16:00:00Z');
assert.equal(nextDay.time.some(x => x.title === '今天'), false, '北京跨日后昨日事项应过期');
assert.equal(nextDay.time.some(x => x.title === '项目节点'), true);
const unsafe = projection.build({ notify: { ok: true, errors: [], items: [
  { id: 'x', title: '<img onerror=alert(1)>', state: 'review', href: 'javascript:alert(1)',
    created_at: '2026-02-30 25:99', send_at: '2026-10-20 09:00' }] },
  tasks: { ok: false, tasks: data.tasks.tasks } }, at);
assert.equal(unsafe.focus[0].title, '<img onerror=alert(1)>', '原文保留为数据，交由 visual 以 textContent 渲染');
assert.equal(unsafe.focus[0].href, null);
assert.equal(unsafe.focus[0].asOf, null);
assert.equal(unsafe.sourceStates.find(x => x.id === 'source:tasks').status, 'unknown');
assert.equal(unsafe.top.approvals, null, '通知 review 不冒充待我审批');
console.log('PASS 全域个人 OS 投影：跨日、缺源、部分错误、待确认口径、时间证据和安全入口');
