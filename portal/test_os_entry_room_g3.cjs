'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = name => fs.readFileSync(path.join(__dirname, name), 'utf8');
const entry = read('os_entry.html');
const preview = read('os_room_preview.html');
const entryScript = [...entry.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)?.[1];
const previewScript = [...preview.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)?.[1];
assert.ok(entryScript && previewScript, '入口与预览页都应有内联主脚本');
new vm.Script(entryScript);
new vm.Script(previewScript);

// 只验证工作树静态配置，不推断 nginx 已部署或接口已上线。
const nginx = read('nginx.conf');
assert.match(nginx, /location\s*=\s*\/os\s*\{[^}]*return\s+302\s+\/os-board\s*;/, '/os 保持跳往 /os-board');
assert.match(nginx, /location\s*=\s*\/os-board\s*\{[^}]*try_files\s+\/os\.html/, '/os-board 仍指向原页');
assert.match(entry, /data-drill="legion"/, '总览保留数字军团下钻入口');

function element() {
  return {
    innerHTML: '', textContent: '', dataset: {}, style: {}, title: '', children: [],
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, setAttribute() {},
  };
}
const els = new Map();
const get = id => { if (!els.has(id)) els.set(id, element()); return els.get(id); };
const location = { hash: '#/legion', pathname: '/os-entry' };
const entryContext = vm.createContext({
  document: { getElementById: get, querySelectorAll: () => [], querySelector: () => null, addEventListener() {} },
  window: { addEventListener() {} }, location,
  fetch: () => Promise.resolve({ ok: false, status: 503, json: async () => ({ ok: false }) }),
  AbortController, setInterval() {}, setTimeout() {}, clearTimeout() {}, Date,
});
// 测试钩仅加在内存脚本末尾，实际页面不作改动。
const end = entryScript.lastIndexOf('})();');
assert.ok(end >= 0, '入口脚本须有 IIFE 收尾');
vm.runInContext(entryScript.slice(0, end) + ';window.__test = { BOARDS, drillTarget, renderBoard };' + entryScript.slice(end), entryContext);
const { BOARDS, drillTarget, renderBoard } = entryContext.window.__test;
assert.equal(BOARDS.legion.route, '/legion', '原 /legion 指挥台主入口不变');
const legion = BOARDS.legion.drills.find(d => d[1] === '/legion');
assert.ok(legion && /指挥台/.test(legion[0]), '保留原指挥台 drill');
const previews = BOARDS.legion.drills.filter(d => d[1] === '/os_room_preview.html');
assert.equal(previews.length, 1, '数字军团新增且仅新增一个同源预览 drill');
assert.match(previews[0][0] + ' ' + previews[0][2], /只读/, '预览 drill 明确标注只读');
assert.match(previews[0][0] + ' ' + previews[0][2], /待签准/, '预览 drill 明确标注待签准');
assert.doesNotMatch(previews[0].join(' '), /已上线|正式上线|(?<!不代表)(?<!并非)已批准|已实测可达/, '预览数据不捏造上线或批准');
assert.equal(drillTarget(previews[0]), '/os_room_preview.html');
assert.equal(drillTarget(legion), '/legion');
for (const unsafe of ['//evil.example/x', '///evil.example', 'javascript:alert(1)', 'https://evil.example/x', '', null]) {
  assert.equal(drillTarget(['bad', unsafe]), null, `非同源目标不可成为链接：${unsafe}`);
}
renderBoard('legion');
const cards = get('b-drills').innerHTML;
const anchors = [...cards.matchAll(/<a\b[^>]*class="dcard"[^>]*>[^]*?<\/a>/g)].map(m => m[0]);
const previewCard = anchors.find(a => /href="\/os_room_preview\.html"/.test(a));
assert.ok(previewCard, '预览 drill 实际渲染为同源可点链接');
assert.match(previewCard, /只读/);
assert.match(previewCard, /待签准/);
assert.doesNotMatch(previewCard, /已上线|正式上线|已实测可达|(?<!不代表)(?<!并非)已批准/, '预览链接不得宣称已实测可达、已上线或已批准');
assert.ok(anchors.some(a => /href="\/legion"/.test(a) && /指挥台/.test(a)), '原指挥台链接仍可点');
assert.doesNotMatch(cards, /href="(?:\/\/|javascript:|https?:)/i, 'drill 渲染不生成外域或脚本链接');
const originalDrills = BOARDS.legion.drills;
try {
  BOARDS.legion.drills = [
    ['外域', '//evil.example/path', '不可点'],
    ['注入', '/safe" onclick="alert(1)', '<img src=x onerror=alert(1)>'],
  ];
  renderBoard('legion');
  const unsafeCards = get('b-drills').innerHTML;
  assert.doesNotMatch(unsafeCards, /href="\/\/evil|href="\/safe" onclick=|<img\b/i, '外域目标不可点击，属性与描述插值须转义');
  assert.match(unsafeCards, /href="\/safe&quot; onclick=&quot;alert\(1\)"/, '同源链接的属性上下文正确转义');
} finally {
  BOARDS.legion.drills = originalDrills;
}
assert.ok(fs.existsSync(path.join(__dirname, 'os_room_preview.html')), '目标预览页存在于工作树（不等于已部署）');

// 预览页 G3 边界：共享投影契约 + 默认 GET + 403/校验失败隐藏决议正文。
assert.match(preview, /static\/os_meeting_readonly_contract\.js/, '载入共享只读投影契约');
assert.match(preview, /只读/);
assert.match(preview, /待签准/);
assert.match(preview, /内容一致≠已批准/, '内容一致不代表已批准');
assert.doesNotMatch(previewScript, /method\s*:|XMLHttpRequest|sendBeacon|WebSocket|\beval\s*\(|localStorage/, '无写方法、其他网络通道及持久 token');

class Node {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.textContent = ''; this.dataset = {}; this.hidden = false; this.handlers = {}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this.html = ''; }
  addEventListener(type, handler) { this.handlers[type] = handler; }
  click() { return this.handlers.click(); }
  set innerHTML(value) { this.html = value; }
  get innerHTML() { return this.html || ''; }
  get visibleText() { return this.textContent + this.innerHTML + this.children.map(n => n.visibleText).join(' '); }
  descendants(filter) { return [this, ...this.children.flatMap(n => n.descendants(filter))].filter(filter); }
}
const previewNodes = new Map();
const node = id => { if (!previewNodes.has(id)) previewNodes.set(id, new Node()); return previewNodes.get(id); };
const calls = [];
let respond = async () => { throw Error('未设置测试响应'); };
let prompts = 0;
const store = new Map();
const previewContext = vm.createContext({
  document: { getElementById: node, createElement: tag => new Node(tag) },
  window: { prompt: () => { prompts++; return 'manual-token'; } },
  sessionStorage: { getItem: key => store.get(key), setItem: (key, value) => store.set(key, value) },
  fetch: (url, options) => { calls.push({ url, options }); return respond(url); },
  setInterval() {}, Date, Map, Set,
});
vm.runInContext(read('static/os_meeting_readonly_contract.js'), previewContext);
const reply = (data, status = 200) => ({ status, ok: status === 200, json: async () => data });
const room = {
  room_id: 'room-one', subject: '议题', status: 'open', board_version: 1,
  messages: [{ kind: 'decision', text: '保密发言' }],
  board: [{ card_id: 'card-one', kind: 'decision', status: 'resolved', text: '保密决议', content_hash: 'hash-one' }],
  resolutions: [{ resolution_id: 'res-one', room_id: 'room-one', card_id: 'card-one', status: 'proposed', text: '保密决议', content_hash: 'hash-one' }],
  board_history: [],
};
const detail = () => node('detail-body').visibleText;
const select = () => node('rooms').descendants(n => n.tag === 'button')[0].click();
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  respond = async () => reply({}, 403);
  vm.runInContext(previewScript, previewContext);
  await flush();
  assert.equal(prompts, 0, '列表 403 不自动索取 token');
  assert.equal(node('btn-token').hidden, false, '403 提供手动 token 入口');
  assert.match(node('state-text').textContent, /访问凭据/);

  respond = async () => reply({ ok: true, rooms: [{ room_id: 'room-one', subject: '议题' }] });
  node('btn-token').click();
  await flush();
  assert.equal(prompts, 1, 'token 只能由用户主动输入');
  assert.equal(calls.at(-1).options.headers['X-Portal-Token'], 'manual-token');

  respond = url => Promise.resolve(reply(url.includes('/resolution?') ? {
    ok: true, resolution: room.resolutions[0], integrity: {
      resolution_id: 'res-one', integrity: 'valid', approval_status: 'not_verified', actionable: false,
    },
  } : { ok: true, room }));
  await select();
  assert.match(detail(), /内容一致.*待签准/);
  assert.match(detail(), /内容一致≠已批准/);
  assert.doesNotMatch(detail(), /保密发言|保密决议/, '房间概览即使校验一致也隐藏决议正文');
  node('detail-body').descendants(n => n.tag === 'button' && n.textContent === '查看只读详情')[0].click();
  assert.match(detail(), /保密决议/, '仅只读校验详情展示一致的决议正文');
  assert.doesNotMatch(detail(), /保密发言/);

  respond = url => Promise.resolve(reply(url.includes('/resolution?') ? {
    ok: true, resolution: room.resolutions[0], integrity: {
      resolution_id: 'res-one', integrity: 'valid', approval_status: 'not_verified', actionable: true,
    },
  } : { ok: true, room }));
  await select();
  assert.match(detail(), /完整性无效/);
  assert.match(detail(), /待签准/);
  assert.doesNotMatch(detail(), /保密发言|保密决议/, '不可执行的伪有效校验不得泄露正文');

  respond = url => Promise.resolve(url.includes('/resolution?') ? reply({}, 403) : reply({ ok: true, room }));
  await select();
  assert.equal(prompts, 1, '决议 403 不自动弹窗');
  assert.match(detail(), /决议校验失败.*访问凭据/);
  assert.doesNotMatch(detail(), /保密发言|保密决议/, '校验失败继续隐藏正文');
  assert.ok(calls.every(c => !c.options.method && c.options.cache === 'no-store' &&
    c.url.startsWith('/legion-api/api/legion/room')), '只请求同源议事厅 GET，不写入');
  console.log('PASS G3 OS 入口：静态路由、drill 安全与诚实标注、预览只读边界及失败态（仅工作树测试）');
})().catch(error => { console.error(error); process.exitCode = 1; });
