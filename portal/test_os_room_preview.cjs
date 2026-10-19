'use strict';
/* G2 议事厅房间只读预览页契约：vm 可编译、只读 fetch（room/list + room?room_id）、
   403 + sessionStorage token 通道、escHtml/textContent 转义纪律、无写方法、无 localStorage。
   纯读文件断言，不访问线上 8080/8096。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const page = fs.readFileSync(path.join(__dirname, 'os_room_preview.html'), 'utf8');

assert.match(page, /只读联调预览·G2/, '顶部标注只读联调预览·G2');
assert.match(page, /<meta name="color-scheme" content="dark">/, '深色视觉');

const scripts = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert.ok(scripts.length >= 1, '存在内联主脚本');
const main = scripts[scripts.length - 1];

// 1) 脚本可编译
new vm.Script(main, { filename: 'portal/os_room_preview.html' });

// 2) 只读接口调用：room/list 与 room?room_id，经 nginx 同源相对路径
assert.match(main, /['"]\/legion-api\/api\/legion\/room\/list['"]/, '包含 room/list 调用');
assert.match(main, /['"]\/legion-api\/api\/legion\/room\?room_id=['"]/, '包含 room?room_id 调用');
assert.match(main, /encodeURIComponent/, 'room_id 参数须编码');

// 3) 403 与 sessionStorage token 通道
assert.match(main, /403/, '识别 403');
assert.match(main, /X-Portal-Token/, '门户 token 请求头');
assert.match(main, /sessionStorage\.(get|set)Item/, 'token 存 sessionStorage');
assert.match(main, /promptLegionToken/, 'prompt token 通道');
assert.match(main, /需要访问凭据（LAN 需门户 token）/, '403 文案');
assert.match(main, /议事厅数据不可达/, '网络失败文案');
assert.match(main, /暂无议题房间/, '空房间文案');

// 4) 转义纪律：escHtml 定义 + textContent 渲染 + 模板插值全部经 escHtml
assert.match(main, /const escHtml\s*=/, 'escHtml 定义');
assert.match(main, /textContent\s*=/, 'textContent 使用');
const interps = [...main.matchAll(/\$\{([^}]*)\}/g)].map(m => m[1]);
assert.ok(interps.length > 0, '存在模板插值（静态骨架）');
for (const expr of interps) {
  assert.match(expr, /escHtml\(/, '插值必须经 escHtml：' + expr);
}
assert.match(main, /\.innerHTML\s*=\s*`/, 'innerHTML 仅赋静态模板字符串');
assert.doesNotMatch(main, /\.innerHTML\s*=(?!\s*`)/, 'innerHTML 不得赋非模板值');

// escHtml 行为抽查（按行提取单行定义，vm 求值）
const s0 = main.search(/const escHtml\s*=/);
const s1 = main.indexOf('\n', s0);
assert.ok(s0 >= 0 && s1 > s0, 'escHtml 定义可定位（须单行）');
const escHtml = vm.runInNewContext('(' + main.slice(s0, s1).replace(/^const escHtml\s*=\s*/, '').replace(/;\s*$/, '') + ')', {});
assert.equal(
  escHtml('<img src=x onerror=alert(1)>&"\''),
  '&lt;img src=x onerror=alert(1)&gt;&amp;&quot;&#39;'
);

// board_history 仅最近 10 条
assert.match(main, /slice\(-10\)/, 'board_history 最近 10 条');

// 5) 无写方法 / 无其他写通道 / 无动态执行 / 无 localStorage
assert.doesNotMatch(main, /['"](?:POST|PUT|DELETE|PATCH)['"]/i, '无写方法字面量');
assert.doesNotMatch(main, /method\s*:/, 'fetch 不指定自定义 method（默认 GET）');
assert.doesNotMatch(main, /XMLHttpRequest|sendBeacon|WebSocket|EventSource/, '无其他网络通道');
assert.doesNotMatch(main, /\beval\s*\(|new Function\(/, '无动态执行');
assert.doesNotMatch(page, /localStorage/, '全页无 localStorage');

console.log('PASS G2 议事厅房间只读预览页：vm 可编译、只读 room 接口、403+sessionStorage token 通道、转义纪律、无写方法无 localStorage');
