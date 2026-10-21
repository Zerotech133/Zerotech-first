'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const page = fs.readFileSync(path.join(__dirname, 'os_room_preview.html'), 'utf8');
const contract = fs.readFileSync(path.join(__dirname, 'static/os_meeting_readonly_contract.js'), 'utf8');
const main = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)?.[1];
assert.ok(main, '预览页应有主脚本');
new vm.Script(main);
const refreshButtons = [...page.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/gi)]
  .map(match => match[0]).filter(markup => /刷新/.test(markup));
const refreshListId = refreshButtons.find(markup => /列表/.test(markup))?.match(/\bid=["']([^"']+)["']/)?.[1];
const refreshRoomId = refreshButtons.find(markup => /房间/.test(markup) && !/列表/.test(markup))?.match(/\bid=["']([^"']+)["']/)?.[1];

class Node {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.textContent = '';
    this.dataset = {};
    this.hidden = false;
    this.handlers = {};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this.innerHTML = ''; }
  addEventListener(type, listener) { this.handlers[type] = listener; }
  click() { assert.equal(typeof this.handlers.click, 'function', '按钮应可点击'); return this.handlers.click(); }
  set innerHTML(value) { this.html = value; }
  get innerHTML() { return this.html || ''; }
  get visibleText() { return [this.textContent, this.innerHTML, ...this.children.map(child => child.visibleText)].join(' '); }
  descendants(predicate) { return [this, ...this.children.flatMap(child => child.descendants(predicate))].filter(predicate); }
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const reply = (data, status = 200) => ({ status, ok: status === 200, json: async () => data });
const summaries = (ids = ['room-one', 'room-two']) => ({
  ok: true, rooms: ids.map(id => ({ room_id: id, subject: id }))
});
const room = (id, text = '只供校验通过展示的旧决议正文') => ({
  room_id: id, subject: id, status: 'open', board_version: 1,
  messages: [{ kind: 'decision', text }],
  board: [{ card_id: 'card-' + id, kind: 'decision', status: 'resolved', text, content_hash: 'hash-' + id }],
  resolutions: [{ resolution_id: 'res-' + id, room_id: id, card_id: 'card-' + id,
    status: 'proposed', text, content_hash: 'hash-' + id }],
  board_history: []
});
const checked = (snapshot, integrity = 'valid') => ({
  ok: true, resolution: { ...snapshot.resolutions[0] },
  integrity: { resolution_id: snapshot.resolutions[0].resolution_id, integrity,
    approval_status: 'not_verified', actionable: false }
});
function harness(responder) {
  const ids = new Map();
  const get = id => { if (!ids.has(id)) ids.set(id, new Node()); return ids.get(id); };
  const requests = [];
  const store = new Map();
  let prompts = 0;
  const context = vm.createContext({
    document: { getElementById: get, createElement: tag => new Node(tag) },
    window: { prompt: () => { prompts++; return 'manual-token'; } },
    sessionStorage: { getItem: key => store.get(key), setItem: (key, value) => store.set(key, value) },
    fetch: (url, options) => { requests.push({ url, options }); return responder(url); },
    setInterval: () => {}, Date, Map, Set
  });
  vm.runInContext(contract, context);
  vm.runInContext(main, context);
  return {
    get, requests, prompts: () => prompts,
    setResponder: next => { responder = next; },
    refreshList: () => {
      assert.ok(refreshListId, '预览页须提供刷新列表按钮');
      return get(refreshListId).click();
    },
    refreshRoom: () => {
      assert.ok(refreshRoomId, '预览页须提供刷新当前房间按钮');
      return get(refreshRoomId).click();
    },
    buttons: () => get('rooms').descendants(node => node.tag === 'button'),
    detail: () => get('detail-body').visibleText,
    detailButton: () => get('detail-body').descendants(node => node.tag === 'button' && /查看.*详情/.test(node.textContent))[0],
    selected: () => get('rooms').descendants(node => node.tag === 'button' && node.className?.split(/\s+/).includes('sel'))
  };
}
function readOnly(h) {
  assert.ok(h.requests.length);
  for (const { url, options } of h.requests) {
    assert.match(url, /^\/legion-api\/api\/legion\/room(?:\/list|\?room_id=|\/resolution\?room_id=)/);
    assert.equal(options.method === undefined || String(options.method).toUpperCase() === 'GET', true, '只允许 GET');
    assert.equal(options.cache, 'no-store');
  }
}
const normal = (url, snapshots = {}) => {
  if (url.endsWith('/room/list')) return reply(summaries());
  const id = new URL(url, 'https://local.invalid').searchParams.get('room_id');
  const snapshot = snapshots[id] || room(id);
  return reply(url.includes('/room/resolution?') ? checked(snapshot) : { ok: true, room: snapshot });
};

test('预览页提供返回 /os-entry 的导航', () => {
  assert.match(page, /<a\b[^>]*\bhref=["']\/os-entry["'][^>]*>/i);
});

test('列表失败后可手动刷新恢复，且所有网络请求仅 GET', async () => {
  const h = harness(() => reply({}, 500));
  await flush();
  assert.equal(h.get('state').dataset.kind, 'err');
  assert.equal(h.buttons().length, 0);
  h.setResponder(url => normal(url));
  await h.refreshList();
  await flush();
  assert.equal(h.buttons().length, 2);
  assert.equal(h.get('state').dataset.kind, 'ok');
  assert.equal(h.prompts(), 0);
  assert.equal(h.requests.filter(request => request.url.endsWith('/room/list')).length, 2);
  readOnly(h);
});

test('刷新时按 room_id 保留当前选择，列表重排不能错选', async () => {
  const h = harness(url => normal(url));
  await flush();
  await h.buttons()[1].click();
  assert.match(h.get('detail-head').visibleText, /room-two/);
  const before = h.requests.length;
  h.setResponder(url => url.endsWith('/room/list') ? reply(summaries(['room-two', 'room-one'])) : normal(url));
  await h.refreshList();
  await flush();
  assert.equal(h.selected().length, 1);
  assert.match(h.selected()[0].visibleText, /room-two/);
  assert.match(h.get('detail-head').visibleText, /room-two/);
  assert.ok(h.requests.slice(before).some(request => request.url.includes('/room?room_id=room-two')));
  assert.ok(!h.requests.slice(before).some(request => request.url.includes('/room?room_id=room-one')));
  readOnly(h);
});

test('刷新后旧正文立即隐藏，重新校验 valid 前详情不可展示', async () => {
  const secret = '仅旧校验允许的正文_OLD_741';
  const snapshot = room('room-one', secret);
  const h = harness(url => normal(url, { 'room-one': snapshot }));
  await flush();
  await h.buttons()[0].click();
  assert.ok(h.detailButton());
  h.detailButton().click();
  assert.match(h.detail(), /仅旧校验允许的正文_OLD_741/);

  const pending = deferred();
  h.setResponder(url => url.includes('/room/resolution?') ? reply(checked(snapshot, 'invalid')) :
    url.includes('/room?room_id=') ? pending.promise : reply(summaries()));
  const refresh = h.refreshRoom();
  assert.doesNotMatch(h.detail(), /仅旧校验允许的正文_OLD_741/, '刷新开始即撤销旧校验正文');
  pending.resolve(reply({ ok: true, room: snapshot }));
  await refresh;
  await flush();
  assert.doesNotMatch(h.detail(), /仅旧校验允许的正文_OLD_741/, '校验无效不能沿用旧 valid');
  if (h.detailButton()) h.detailButton().click();
  assert.doesNotMatch(h.detail(), /仅旧校验允许的正文_OLD_741/);

  h.setResponder(url => normal(url, { 'room-one': snapshot }));
  await h.refreshRoom();
  await flush();
  assert.doesNotMatch(h.detail(), /仅旧校验允许的正文_OLD_741/, '概览不直接显示决议正文');
  assert.ok(h.detailButton(), '重新校验 valid 后可进入详情');
  h.detailButton().click();
  assert.match(h.detail(), /仅旧校验允许的正文_OLD_741/);
  readOnly(h);
});

test('并发刷新乱序响应不回写旧房间或旧决议正文', async () => {
  const oldSecret = '旧房间决议正文_STALE_751';
  const old = room('room-one', oldSecret);
  const next = room('room-two', '新房间正文_CURRENT_752');
  const h = harness(url => normal(url, { 'room-one': old, 'room-two': next }));
  await flush();
  await h.buttons()[0].click();
  h.detailButton().click();
  assert.match(h.detail(), /旧房间决议正文_STALE_751/);

  const staleList = deferred();
  let listCalls = 0;
  h.setResponder(url => {
    if (url.endsWith('/room/list')) return ++listCalls === 1 ? staleList.promise : reply(summaries(['room-two', 'room-one']));
    return normal(url, { 'room-one': old, 'room-two': next });
  });
  const first = h.refreshList();
  assert.doesNotMatch(h.detail(), /旧房间决议正文_STALE_751/);
  const second = h.refreshList();
  await second;
  await flush();
  await h.buttons().find(button => button.visibleText.includes('room-two')).click();
  staleList.resolve(reply(summaries(['room-one', 'room-two'])));
  await first;
  assert.equal(h.selected().length, 1);
  assert.match(h.selected()[0].visibleText, /room-two/);
  assert.match(h.get('detail-head').visibleText, /room-two/);
  assert.doesNotMatch(h.detail(), /旧房间决议正文_STALE_751/);

  await h.buttons().find(button => button.visibleText.includes('room-one')).click();
  const staleResolution = deferred();
  h.setResponder(url => url.includes('/room/resolution?room_id=room-one') ? staleResolution.promise :
    normal(url, { 'room-one': old, 'room-two': next }));
  const oldRefresh = h.refreshRoom();
  await flush();
  await h.refreshList();
  await h.buttons().find(button => button.visibleText.includes('room-two')).click();
  staleResolution.resolve(reply(checked(old)));
  await oldRefresh;
  assert.match(h.get('detail-head').visibleText, /room-two/);
  assert.doesNotMatch(h.detail(), /旧房间决议正文_STALE_751/);
  readOnly(h);
});

test('403 不自动索取 token，手动按钮仍可恢复刷新', async () => {
  const h = harness(() => reply({}, 403));
  await flush();
  assert.equal(h.prompts(), 0);
  assert.equal(h.get('state').dataset.kind, 'auth');
  assert.equal(h.get('btn-token').hidden, false);
  await h.refreshList();
  await flush();
  assert.equal(h.prompts(), 0, '手动刷新遇 403 也不得自动弹 token');
  assert.equal(h.get('btn-token').hidden, false);
  h.setResponder(url => normal(url));
  h.get('btn-token').click();
  await flush();
  assert.equal(h.prompts(), 1);
  assert.equal(h.buttons().length, 2);
  assert.equal(h.requests.at(-1).options.headers['X-Portal-Token'], 'manual-token');
  readOnly(h);
});
