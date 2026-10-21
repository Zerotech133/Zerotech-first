'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const page = fs.readFileSync(path.join(__dirname, 'os_room_preview.html'), 'utf8');
const contract = fs.readFileSync(path.join(__dirname, 'static/os_meeting_readonly_contract.js'), 'utf8');
const main = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
new vm.Script(main);
assert.match(page, /static\/os_meeting_readonly_contract\.js/);
assert.match(page, /color-scheme" content="dark"/);
assert.doesNotMatch(main, /method\s*:|XMLHttpRequest|sendBeacon|localStorage|\beval\s*\(/);
class Node {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.textContent = ''; this.dataset = {}; this.hidden = false; this.handlers = {}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this.innerHTML = ''; }
  addEventListener(type, listener) { this.handlers[type] = listener; }
  click() { return this.handlers.click(); }
  set innerHTML(value) { this.html = value; }
  get innerHTML() { return this.html || ''; }
  get visibleText() { return this.textContent + this.innerHTML + this.children.map(c => c.visibleText).join(' '); }
  descendants(predicate) { return [this, ...this.children.flatMap(c => c.descendants(predicate))].filter(predicate); }
}
const ids = new Map();
const get = id => { if (!ids.has(id)) ids.set(id, new Node()); return ids.get(id); };
const requests = [];
let responder = async () => { throw Error('unexpected fetch'); };
let prompts = 0;
const store = new Map();
const context = vm.createContext({
  document: { getElementById: get, createElement: tag => new Node(tag) },
  window: { prompt: () => { prompts++; return 'manual-token'; } },
  sessionStorage: { getItem: key => store.get(key), setItem: (key, value) => store.set(key, value) },
  fetch: (url, options) => { requests.push({ url, options }); return responder(url); },
  setInterval: () => {}, Date, Map, Set,
});
vm.runInContext(contract, context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const reply = (data, status = 200) => ({ status, ok: status === 200, json: async () => data });
const room = (id = 'room-one') => ({
  room_id: id, subject: '<img src=x onerror=alert(1)>', status: 'open', board_version: 1,
  messages: [{ kind: 'decision', author_name: '甲', text: '机密发言' }, { kind: 'note', text: '<b>普通发言</b>' }],
  board: [{ card_id: 'card-one', kind: 'decision', status: 'resolved', text: '机密卡片', content_hash: 'hash-one' }],
  resolutions: [{ resolution_id: 'res-one', room_id: id, card_id: 'card-one', status: 'proposed', text: '机密卡片', content_hash: 'hash-one' }],
  board_history: []
});
const validResponse = (x = room()) => ({ ok: true, resolution: { ...x.resolutions[0] }, integrity: {
  resolution_id: 'res-one', integrity: 'valid', approval_status: 'not_verified', actionable: false
} });
const list = { ok: true, rooms: [{ room_id: 'room-one', subject: '房间一' }, { room_id: 'room-two', subject: '房间二' }] };
const buttons = () => get('rooms').descendants(n => n.tag === 'button');
const detailButton = () => get('detail-body').descendants(n => n.tag === 'button' && n.textContent === '查看只读详情')[0];
const detail = () => get('detail-body').visibleText;
(async () => {
  responder = async () => reply({ ok: false }, 403);
  vm.runInContext(main, context);
  await flush();
  assert.equal(prompts, 0, '自动列表 403 不得弹窗');
  assert.equal(get('btn-token').hidden, false);
  responder = async () => reply(list);
  get('btn-token').click();
  await flush();
  assert.equal(prompts, 1, '仅主动点击 token 按钮时提示');
  assert.equal(buttons().length, 2);
  assert.match(get('state-text').textContent, /响应时间/);
  assert.equal(requests.at(-1).options.headers['X-Portal-Token'], 'manual-token');

  const wait = deferred();
  responder = url => url.includes('/room/resolution?') ? wait.promise : Promise.resolve(reply({ ok: true, room: room() }));
  const pendingValid = buttons()[0].click();
  await flush();
  assert.match(detail(), /决议相关发言正文已隐藏/);
  assert.doesNotMatch(detail(), /机密发言|机密卡片/);
  assert.match(detail(), /白板 resolved\/done、发言 decision 均非批准/);
  assert.match(detail(), /<b>普通发言<\/b>/);
  assert.doesNotMatch(get('detail-head').innerHTML, /<img/);
  detailButton().click();
  assert.match(detail(), /校验不可用 · 正文已隐藏/);
  wait.resolve(reply(validResponse()));
  await pendingValid;
  assert.match(detail(), /机密卡片/);
  assert.match(detail(), /内容一致≠已批准/);
  assert.doesNotMatch(detail(), /机密发言/);
  assert.doesNotMatch(detail(), /<img|onerror=/);
  assert.match(requests.at(-1).url, /room_id=room-one&resolution_id=res-one$/);
  assert.equal(requests.at(-1).options.cache, 'no-store');
  get('detail-body').descendants(n => n.tag === 'button' && n.textContent === '返回原房间')[0].click();
  assert.doesNotMatch(detail(), /机密卡片/);

  responder = url => Promise.resolve(reply(url.includes('/room/resolution?') ? { ...validResponse(), integrity: { ...validResponse().integrity, actionable: true } } : { ok: true, room: room() }));
  await buttons()[0].click();
  assert.match(detail(), /完整性无效/);
  detailButton().click();
  assert.doesNotMatch(detail(), /机密卡片/);

  responder = url => Promise.resolve(url.includes('/room/resolution?') ? reply({}, 403) : reply({ ok: true, room: room() }));
  await buttons()[0].click();
  assert.equal(prompts, 1, '自动决议 403 不得弹窗');
  assert.match(detail(), /决议校验失败.*需要访问凭据/);
  assert.doesNotMatch(detail(), /机密卡片/);

  const oldRoom = deferred();
  responder = url => url.includes('room-one') ? oldRoom.promise : Promise.resolve(reply({ ok: true, room: room('room-two') }));
  const first = buttons()[0].click();
  await buttons()[1].click();
  assert.match(get('detail-head').innerHTML, /room-two/);
  oldRoom.resolve(reply({ ok: true, room: room() }));
  await first;
  assert.match(get('detail-head').innerHTML, /room-two/);
  assert.doesNotMatch(detail(), /机密卡片/);

  const oldResolution = deferred();
  responder = url => url.includes('room-one') ? (url.includes('resolution?') ? oldResolution.promise : Promise.resolve(reply({ ok: true, room: room() })) ) : Promise.resolve(reply({ ok: true, room: room('room-two') }));
  const firstResolution = buttons()[0].click();
  await flush();
  await buttons()[1].click();
  oldResolution.resolve(reply(validResponse()));
  await firstResolution;
  assert.match(get('detail-head').innerHTML, /room-two/);
  assert.doesNotMatch(detail(), /机密卡片/);
  assert.ok(requests.every(r => !r.options.method && r.url.startsWith('/legion-api/api/legion/room')));
  console.log('PASS G3 房间只读预览：契约遮蔽、详情、失败态、手动 token、切换竞态、HTML 转义和 GET 边界');
})().catch(error => { console.error(error); process.exitCode = 1; });
