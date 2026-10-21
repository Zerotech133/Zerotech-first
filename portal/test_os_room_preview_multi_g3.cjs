'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const page = fs.readFileSync(path.join(__dirname, 'os_room_preview.html'), 'utf8');
const contract = fs.readFileSync(path.join(__dirname, 'static/os_meeting_readonly_contract.js'), 'utf8');
const main = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)?.[1];
assert.ok(main);
new vm.Script(main);
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const reply = (data, status = 200) => ({ status, ok: status === 200, json: async () => data });
const list = () => reply({ ok: true, rooms: [{ room_id: 'room-one', subject: '房间一' }] });
function room(count = 5, marker = 'CURRENT') {
  const resolutions = Array.from({ length: count }, (_, i) => ({
    room_id: 'room-one', resolution_id: 'res-' + i, card_id: 'card-' + i,
    status: 'proposed', text: marker + '_正文_' + i, content_hash: 'hash-' + i
  }));
  return {
    room_id: 'room-one', subject: '房间一', messages: [], board_history: [], resolutions,
    board: resolutions.map(r => ({ card_id: r.card_id, kind: 'decision', status: 'resolved', text: r.text, content_hash: r.content_hash }))
  };
}
function checked(snapshot, id, integrity = 'valid') {
  return reply({ ok: true, resolution: { ...snapshot.resolutions.find(r => r.resolution_id === id) },
    integrity: { resolution_id: id, integrity, approval_status: 'not_verified', actionable: false } });
}
class Node {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.textContent = ''; this.dataset = {}; this.hidden = false; this.handlers = {}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this.innerHTML = ''; }
  addEventListener(type, listener) { this.handlers[type] = listener; }
  click() { assert.equal(typeof this.handlers.click, 'function'); return this.handlers.click(); }
  set innerHTML(value) { this.html = value; }
  get innerHTML() { return this.html || ''; }
  get visibleText() { return [this.textContent, this.innerHTML, ...this.children.map(child => child.visibleText)].join(' '); }
  descendants(predicate) { return [this, ...this.children.flatMap(child => child.descendants(predicate))].filter(predicate); }
}
function harness(responder) {
  const ids = new Map(), requests = [], intervals = [];
  const get = id => { if (!ids.has(id)) ids.set(id, new Node()); return ids.get(id); };
  let prompts = 0;
  const context = vm.createContext({
    document: { getElementById: get, createElement: tag => new Node(tag) },
    window: { prompt: () => { prompts++; return 'manual'; } },
    sessionStorage: { getItem: () => '', setItem: () => {} },
    fetch: (url, options) => { requests.push({ url, options }); return responder(url); },
    setInterval: (...args) => intervals.push(args), Date, Map, Set
  });
  vm.runInContext(contract, context);
  vm.runInContext(main, context);
  return {
    get, requests, prompts: () => prompts, intervals,
    setResponder: next => { responder = next; },
    disableContract: () => { context.window.OSMeetingReadonlyContract = null; },
    state: () => get('state-text').textContent,
    kind: () => get('state').dataset.kind,
    detail: () => get('detail-body').visibleText,
    roomButton: () => get('rooms').descendants(n => n.tag === 'button')[0],
    detailButton: id => get('detail-body').descendants(n => n.tag === 'button' && n.textContent === '查看只读详情')[Number(id.split('-')[1])],
    refreshRoom: () => get('btn-refresh-room').click()
  };
}
function assertReadOnly(h) {
  assert.equal(h.prompts(), 0, '不能自动弹 token');
  assert.equal(h.intervals.length, 1, '仅允许既有时钟，不得增加自动轮询');
  for (const { url, options } of h.requests) {
    assert.match(url, /^\/legion-api\/api\/legion\/room(?:\/list|\?room_id=|\/resolution\?room_id=)/);
    assert.equal(options.method, undefined, '不得发起写请求');
    assert.equal(options.cache, 'no-store');
  }
}

test('五项决议并发峰值不超过三，未完成前不报已加载或完成', async () => {
  const snapshot = room();
  const pending = new Map();
  let inFlight = 0, peak = 0;
  const h = harness(url => {
    if (url.endsWith('/room/list')) return list();
    if (url.includes('/room?room_id=')) return reply({ ok: true, room: snapshot });
    const id = new URL(url, 'https://local.invalid').searchParams.get('resolution_id');
    const wait = deferred(); pending.set(id, wait);
    inFlight++; peak = Math.max(peak, inFlight);
    return wait.promise.finally(() => { inFlight--; });
  });
  await flush();
  const selection = h.roomButton().click();
  await flush();
  assert.equal(pending.size, 3);
  assert.equal(peak, 3);
  assert.equal(h.kind(), 'load');
  assert.match(h.state(), /校验中.*0\/5/);
  assert.doesNotMatch(h.state(), /已加载|校验完成/);
  h.detailButton('res-0').click();
  assert.match(h.detail(), /校验中.*正文已隐藏/);
  assert.doesNotMatch(h.detail(), /CURRENT_正文/);
  pending.get('res-1').resolve(checked(snapshot, 'res-1'));
  await flush();
  assert.equal(pending.size, 4);
  assert.equal(h.kind(), 'load');
  assert.match(h.state(), /校验中.*1\/5/);
  assert.equal(peak, 3);
  pending.get('res-0').resolve(checked(snapshot, 'res-0'));
  await flush();
  assert.match(h.detail(), /CURRENT_正文_0/);
  assert.equal(h.kind(), 'load', '个别 valid 不代表全部校验完成');
  for (const id of ['res-2', 'res-3', 'res-4']) pending.get(id).resolve(checked(snapshot, id));
  await selection;
  assert.equal(inFlight, 0);
  assert.equal(h.kind(), 'ok');
  assert.match(h.state(), /校验完成：valid 5 \/ invalid 0 \/ unknown 0 \/ 403 0 \/ net 0/);
  assert.match(h.state(), /内容一致≠已批准.*待签准.*不可执行/);
  assertReadOnly(h);
});

test('HTTP 200 的业务失败、invalid、403 和 net 汇总；失败正文均隐藏', async () => {
  const snapshot = room();
  const pending = new Map();
  const h = harness(url => {
    if (url.endsWith('/room/list')) return list();
    if (url.includes('/room?room_id=')) return reply({ ok: true, room: snapshot });
    const id = new URL(url, 'https://local.invalid').searchParams.get('resolution_id');
    const wait = deferred(); pending.set(id, wait); return wait.promise;
  });
  await flush();
  const selection = h.roomButton().click();
  await flush();
  pending.get('res-0').resolve(checked(snapshot, 'res-0', 'invalid'));
  await flush();
  pending.get('res-1').resolve(reply({ ok: false, err: 'business_failure' }));
  await flush();
  pending.get('res-2').resolve(reply({}, 403));
  await flush();
  assert.equal(h.kind(), 'load', '即使已有失败也须等待所有校验完成');
  assert.match(h.state(), /校验中.*3\/5/);
  pending.get('res-3').reject(new Error('network down'));
  await flush();
  pending.get('res-4').resolve(checked(snapshot, 'res-4'));
  await selection;
  assert.equal(h.kind(), 'auth');
  assert.equal(h.get('btn-token').hidden, false);
  assert.match(h.state(), /valid 1 \/ invalid 1 \/ unknown 0 \/ 403 1 \/ net 1 \/ 其他失败 1/);
  for (let i = 0; i < 4; i++) {
    h.detailButton('res-' + i).click();
    assert.doesNotMatch(h.detail(), new RegExp('CURRENT_正文_' + i));
    assert.match(h.detail(), /正文已隐藏/);
    h.get('detail-body').descendants(n => n.tag === 'button' && n.textContent === '返回原房间')[0].click();
  }
  h.detailButton('res-4').click();
  assert.match(h.detail(), /CURRENT_正文_4/);
  assertReadOnly(h);
});

test('契约不可用为 unknown，HTTP 200 不代表业务校验通过', async () => {
  const snapshot = room(1);
  const h = harness(url => url.endsWith('/room/list') ? list() :
    url.includes('/room?room_id=') ? reply({ ok: true, room: snapshot }) : checked(snapshot, 'res-0'));
  await flush();
  h.disableContract();
  await h.roomButton().click();
  assert.equal(h.kind(), 'err');
  assert.match(h.state(), /valid 0 \/ invalid 0 \/ unknown 1 \/ 403 0 \/ net 0/);
  h.detailButton('res-0').click();
  assert.match(h.detail(), /校验契约不可用.*正文已隐藏/);
  assert.doesNotMatch(h.detail(), /CURRENT_正文_0/);
  assertReadOnly(h);
});

test('旧刷新校验乱序结束不得回写新房间状态或正文', async () => {
  const old = room(4, 'STALE');
  const fresh = room(4, 'FRESH');
  const stale = new Map();
  let revision = 0;
  const h = harness(url => {
    if (url.endsWith('/room/list')) return list();
    if (url.includes('/room?room_id=')) return reply({ ok: true, room: revision ? fresh : old });
    const id = new URL(url, 'https://local.invalid').searchParams.get('resolution_id');
    if (!revision) { const wait = deferred(); stale.set(id, wait); return wait.promise; }
    return checked(fresh, id);
  });
  await flush();
  const first = h.roomButton().click();
  await flush();
  assert.equal(stale.size, 3);
  revision = 1;
  const second = h.refreshRoom();
  assert.doesNotMatch(h.detail(), /STALE_正文/);
  await second;
  assert.equal(h.kind(), 'ok');
  assert.match(h.state(), /valid 4/);
  h.detailButton('res-0').click();
  assert.match(h.detail(), /FRESH_正文_0/);
  for (const [id, wait] of stale) wait.resolve(checked(old, id));
  await first;
  assert.equal(stale.size, 3, '旧批次不应再派发第四个请求');
  assert.equal(h.kind(), 'ok');
  assert.match(h.state(), /valid 4/);
  assert.match(h.detail(), /FRESH_正文_0/);
  assert.doesNotMatch(h.detail(), /STALE_正文/);
  assertReadOnly(h);
});
