/* WO-OS-02 契约测试：ResultEvidence 证据槽
 * 运行：node test_os_result_contract.cjs
 * 覆盖：全字段合法、逐字段缺失→pending、伪造日期/时点拒绝、outcome 词表、回执分级、project 批量、双端加载。*/
'use strict';
const assert = require('assert');
const path = require('path');
const contract = require(path.join(__dirname, 'static', 'os_result_contract.js'));

const CLOCK = new Date('2026-10-21T12:00:00+08:00');
const GOOD = {
  id: 'dev-001', domain: '开发', business_day: '2026-10-21',
  producer_time: '2026-10-21T09:30:00+08:00', outcome: 'done',
  evidence_uri: '/deliverables/x.md', receipt_uri: '/receipts/x.json'
};

// 1. 全字段合法 → valid + verified
let r = contract.entry(GOOD, CLOCK);
assert.strictEqual(r.valid, true, '全字段应 valid');
assert.strictEqual(r.status, 'verified', '有回执应 verified');

// 2. 逐字段缺失 → pending + gap 命中
for (const key of ['id', 'domain', 'business_day', 'producer_time', 'outcome', 'evidence_uri']) {
  const bad = Object.assign({}, GOOD); delete bad[key];
  r = contract.entry(bad, CLOCK);
  assert.strictEqual(r.valid, false, key + ' 缺失应 invalid');
  assert.strictEqual(r.status, 'pending', key + ' 缺失应 pending');
  assert.ok(r.gaps.includes(key), key + ' 应出现在 gaps');
}

// 3. 伪造/不合法日期时间
assert.strictEqual(contract.entry(Object.assign({}, GOOD, { business_day: '2026-02-30' }), CLOCK).status, 'pending', '2月30日应拒绝');
assert.strictEqual(contract.entry(Object.assign({}, GOOD, { business_day: '2026/10/21' }), CLOCK).status, 'pending', '斜杠日期应拒绝');
assert.strictEqual(contract.entry(Object.assign({}, GOOD, { producer_time: '2026-10-21 09:30' }), CLOCK).status, 'pending', '无时区时点应拒绝');
assert.strictEqual(contract.entry(Object.assign({}, GOOD, { producer_time: '2026-10-22T09:30:00+08:00' }), CLOCK).status, 'pending', '未来时点应拒绝');

// 4. outcome 词表外一律 pending
for (const bad of ['finished', 'ok', 'DONE', '', null, 1]) {
  assert.strictEqual(contract.entry(Object.assign({}, GOOD, { outcome: bad }), CLOCK).status, 'pending', 'outcome=' + JSON.stringify(bad) + ' 应 pending');
}

// 5. 回执分级：缺 receipt → unverified
const noReceipt = Object.assign({}, GOOD); delete noReceipt.receipt_uri;
r = contract.entry(noReceipt, CLOCK);
assert.strictEqual(r.valid, true);
assert.strictEqual(r.status, 'unverified', '缺回执应 unverified');

// 6. project 批量与异常时钟
const list = contract.project([GOOD, {}], CLOCK);
assert.strictEqual(list.length, 2);
assert.strictEqual(list[1].status, 'pending');
assert.throws(() => contract.entry(GOOD, 'not-a-date'), RangeError, '坏时钟应抛错');
assert.throws(() => contract.project('x'), TypeError, '非数组应抛错');

// 7. 双端加载：模拟 window 环境
const vm = require('vm');
const sandbox = { module: { exports: {} } };
vm.runInNewContext(require('fs').readFileSync(path.join(__dirname, 'static', 'os_result_contract.js'), 'utf8'), sandbox);
assert.strictEqual(sandbox.module.exports.entry(GOOD, CLOCK).valid, true, 'CommonJS 加载应可用');

console.log('PASS WO-OS-02 ResultEvidence 契约：合法/缺失/伪造/词表/回执/批量/双端');
