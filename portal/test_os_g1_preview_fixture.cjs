'use strict';
/* G1 联调预览页契约：只读来源卡、夹具标注、无网络无持久化。不访问线上。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const page = fs.readFileSync(path.join(__dirname, '..', 'outputs', '个人OS_三席会议与白板_G1联调预览版_20261019.html'), 'utf8');

assert.match(page, /G1 联调预览版/);
assert.match(page, /<script src="\.\.\/portal\/static\/os_meeting_readonly_contract\.js"><\/script>/);
assert.ok(page.indexOf('os_meeting_readonly_contract.js') < page.lastIndexOf('<script>'), '契约模块须在联调脚本前加载');

const scripts = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const g1 = scripts[scripts.length - 1];
assert.match(g1, /window\.OSMeetingReadonlyContract/, '须优先使用共享契约模块');
assert.match(g1, /内置夹具（契约模块未加载）/, '降级路径必须显著标注夹具');
assert.match(g1, /const ALLOWED=new Set\(\['unknown','limited','stale','partial'\]\)/, '状态白名单');
assert.match(g1, /if\(!data\.cards\.every\(c=>ALLOWED\.has\(c\.status\)\)\)return;/, '非法状态直接不渲染');
assert.match(g1, /personal-calendar[\s\S]*business-opportunities[\s\S]*my-approvals[\s\S]*delivery-receipts/, '四项个人事实缺源卡');
assert.match(g1, /缺源不等于零/, '缺源不推断为零');
assert.match(g1, /textContent=/);
assert.doesNotMatch(g1, /innerHTML\s*=|insertAdjacentHTML|\beval\s*\(|fetch\s*\(|XMLHttpRequest|localStorage|sessionStorage|sendBeacon/);
assert.doesNotMatch(g1, /\/bridge\/act|\/api\/flow\/rerun|dry_run=false/);

const { build } = require('./static/os_meeting_readonly_contract.js');
const result = build({}, new Date('2026-10-19T03:00:00Z'));
assert.equal(result.cards.length, 10);
assert.ok(result.cards.every(c => c.status === 'unknown' && c.asOf === null), '空输入时契约全未知');
assert.ok(Object.isFrozen(result) && Object.isFrozen(result.cards));

console.log('PASS G1 联调预览页：契约模块优先、夹具显著标注、状态白名单、无网络无持久化');
