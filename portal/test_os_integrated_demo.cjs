/* 独立评审页接线与安全契约：不访问线上，不修改服务。 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, 'os_integrated_demo.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(script, '内联入口必须存在');
new vm.Script(script[1]);
assert.match(html, /<script src="\/static\/os_integrated_projection\.js"><\/script>/);
assert.ok(html.indexOf('os_integrated_projection.js') < html.indexOf('<script>'), '数据投影须先加载');
assert.match(html, /height:100dvh/);
assert.match(html, /overflow:hidden/);
assert.match(script[1], /OSIntegratedProjection\.build\(d,new Date\(\)\)/);
assert.match(script[1], /p\.confirm=\[unknown\('本人审批队列尚未接入'/);
assert.match(script[1], /p\.top\.alerts=p\.focus\.filter/);
assert.match(script[1], /title\.textContent=x\.title/);
assert.match(script[1], /Object\.prototype\.hasOwnProperty\.call\(LINKS,x\.href\)/);
assert.doesNotMatch(script[1], /\.innerHTML\s*=|insertAdjacentHTML|\beval\s*\(/);
for (const [, endpoint, method] of script[1].matchAll(/fetch\(([^,]+),\{method:'([^']+)'/g)) {
  assert.equal(method, 'GET');
  assert.equal(endpoint, 'url');
}
const api = script[1].match(/const ENDPOINTS=([^;]+);/);
assert.ok(api);
const endpoints = vm.runInNewContext('(' + api[1] + ')');
assert.deepEqual(Object.values(endpoints).sort(), [
  '/api/os','/api/home','/api/alerts','/api/notify/queue','/api/org/tasks','/api/capability/board'
].sort());
assert.doesNotMatch(script[1], /\/bridge\/act|\/api\/flow\/rerun|\/api\/service|\/api\/agent\/learn/);
console.log('PASS 深色个人 OS 集成页：只读六接口、脚本加载、未知态、DOM 安全、固定单屏契约');
