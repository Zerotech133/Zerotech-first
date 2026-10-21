'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const page = fs.readFileSync(path.join(__dirname, 'os_room_preview.html'), 'utf8');
const main = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)?.[1];
assert.ok(main);
new vm.Script(main);
assert.match(page, /id="list-health"[^>]*role="status"/);

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
  replaceChildren(...nodes) { this.children = nodes; this.html = ''; }
  addEventListener(type, handler) { this.handlers[type] = handler; }
  click() { assert.equal(typeof this.handlers.click, 'function'); return this.handlers.click(); }
  set innerHTML(value) { this.html = value; }
  get innerHTML() { return this.html || ''; }
  get visibleText() { return [this.textContent, this.innerHTML, ...this.children.map(node => node.visibleText)].join(' '); }
  descendants(predicate) { return [this, ...this.children.flatMap(node => node.descendants(predicate))].filter(predicate); }
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const reply = data => ({ status: 200, ok: true, json: async () => data });
const summary = { room_id: 'room-one', subject: '议题一' };
const list = (meta, rooms = [summary]) => ({ ok: true, rooms, ...(meta === undefined ? {} : { meta }) });
const good = { skipped_count: 0, complete: true, scanned_count: 1 };
function harness(initial) {
  const nodes = new Map(), requests = [];
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); };
  let responder = initial, prompts = 0;
  const context = vm.createContext({
    document: { getElementById: get, createElement: tag => new Node(tag) },
    window: { prompt: () => { prompts++; return 'manual-token'; } },
    sessionStorage: { getItem: () => '', setItem: () => {} },
    fetch: (url, options) => { requests.push({ url, options }); return responder(url); },
    setInterval: () => {}, Date, Map, Set
  });
  vm.runInContext(main, context);
  return {
    get, requests, prompts: () => prompts,
    setResponder: next => { responder = next; },
    refresh: () => get('btn-refresh-list').click(),
    state: () => get('state-text').textContent,
    health: () => get('list-health').textContent,
    buttons: () => get('rooms').descendants(node => node.tag === 'button'),
    visible: () => [get('state-text'), get('list-health'), get('rooms'), get('detail-body')].map(node => node.visibleText).join(' ')
  };
}
function readOnly(h) {
  assert.equal(h.prompts(), 0, '不得自动索取 token');
  assert.ok(h.requests.length);
  for (const { url, options } of h.requests) {
    assert.match(url, /^\/legion-api\/api\/legion\/room(?:\/list|\?room_id=|\/resolution\?room_id=)/);
    assert.equal(options.method, undefined);
    assert.equal(options.cache, 'no-store');
  }
}

test('完整列表与完整空列表仅在合法 meta 下显示完整', async () => {
  const h = harness(() => reply(list(good)));
  await flush();
  assert.equal(h.get('state').dataset.kind, 'ok');
  assert.equal(h.get('list-health').dataset.kind, 'complete');
  assert.match(h.health(), /房间列表完整/);
  assert.equal(h.buttons().length, 1);
  h.setResponder(() => reply(list({ skipped_count: 0, complete: true, scanned_count: 0 }, [])));
  await h.refresh();
  assert.equal(h.get('state').dataset.kind, 'idle');
  assert.match(h.visible(), /暂无议题房间/);
  assert.equal(h.buttons().length, 0);
  readOnly(h);
});

test('complete:false 或 skipped_count>0 均明确告警；空列表不能伪装完整零', async () => {
  for (const meta of [
    { skipped_count: 0, complete: false, scanned_count: 0 },
    { skipped_count: 1, complete: false, scanned_count: 1 },
    { skipped_count: 1, complete: true, scanned_count: 1 }
  ]) {
    const h = harness(() => reply(list(meta, [])));
    await flush();
    assert.equal(h.get('state').dataset.kind, 'err');
    assert.equal(h.get('list-health').dataset.kind, 'partial');
    assert.match(h.visible(), /房间列表不完整\/部分档案不可读/);
    assert.doesNotMatch(h.visible(), /暂无议题房间|房间列表完整/);
    assert.equal(h.buttons().length, 0);
    readOnly(h);
  }
  const h = harness(() => reply(list({ skipped_count: 2, complete: false, scanned_count: 3 })));
  await flush();
  assert.equal(h.buttons().length, 1, '可读房间仍可查看');
  assert.match(h.health(), /跳过 2 间/);
  assert.equal(h.get('state').dataset.kind, 'err');
  readOnly(h);
});

test('旧后端无 meta 兼容但不得把未知当完整，含零房间', async () => {
  const h = harness(() => reply(list(undefined, [])));
  await flush();
  assert.equal(h.get('list-health').dataset.kind, 'unknown');
  assert.match(h.visible(), /完整性未核实/);
  assert.doesNotMatch(h.visible(), /暂无议题房间|房间列表完整/);
  h.setResponder(() => reply(list(undefined)));
  await h.refresh();
  assert.equal(h.buttons().length, 1);
  assert.match(h.health(), /完整性未核实/);
  assert.doesNotMatch(h.health(), /房间列表完整/);
  readOnly(h);
});

test('畸形 meta 拒绝假绿且不会显示私密路径或正文；仅手动刷新可恢复', async () => {
  const privatePath = 'PRIVATE_PATH_SHOULD_NOT_RENDER';
  const privateBody = 'PRIVATE_BODY_SHOULD_NOT_RENDER';
  const malformed = [
    null, [], {}, { complete: 'true', skipped_count: 0, scanned_count: 1 },
    { complete: true, skipped_count: '0', scanned_count: 1 },
    { complete: true, skipped_count: -1, scanned_count: 1 },
    { complete: true, skipped_count: 0.5, scanned_count: 1 },
    { complete: true, skipped_count: 0, scanned_count: NaN },
    { complete: true, skipped_count: 0, scanned_count: 0 },
    { complete: true, skipped_count: 0, scanned_count: 2 },
    { complete: false, skipped_count: 1, scanned_count: 3 },
    { complete: false, skipped_count: 2, scanned_count: 1 }
  ];
  for (const meta of malformed) {
    const h = harness(() => reply({ ...list(meta), private_path: privatePath, body: privateBody }));
    await flush();
    assert.equal(h.get('state').dataset.kind, 'err');
    assert.equal(h.buttons().length, 0);
    assert.match(h.visible(), /格式异常|完整性未核实/);
    assert.doesNotMatch(h.visible(), /房间列表完整|PRIVATE_PATH_SHOULD_NOT_RENDER|PRIVATE_BODY_SHOULD_NOT_RENDER/);
    assert.equal(h.requests.length, 1);
    h.setResponder(() => reply(list(good)));
    await h.refresh();
    assert.equal(h.get('state').dataset.kind, 'ok');
    assert.equal(h.get('list-health').dataset.kind, 'complete');
    assert.equal(h.buttons().length, 1);
    readOnly(h);
  }
});

test('部分列表的告警在进入详情及刷新失败后不误报完整，手动刷新可恢复', async () => {
  const partial = { skipped_count: 1, complete: false, scanned_count: 2 };
  const h = harness(url => reply(url.endsWith('/room/list') ? list(partial) : {
    ok: true, room: { room_id: 'room-one', subject: '议题一', messages: [], board: [], board_history: [], resolutions: [] }
  }));
  await flush();
  await h.buttons()[0].click();
  assert.equal(h.get('state').dataset.kind, 'err');
  assert.match(h.health(), /房间列表不完整\/部分档案不可读/);
  h.setResponder(() => reply({ ok: false, err: 'unavailable' }));
  await h.refresh();
  assert.equal(h.get('state').dataset.kind, 'err');
  assert.match(h.health(), /完整性未核实/);
  h.setResponder(url => reply(url.endsWith('/room/list') ? list(good) : {
    ok: true, room: { room_id: 'room-one', subject: '议题一', messages: [], board: [], board_history: [], resolutions: [] }
  }));
  await h.refresh();
  await flush();
  assert.equal(h.get('state').dataset.kind, 'ok');
  assert.equal(h.buttons().length, 1);
  assert.match(h.health(), /房间列表完整/);
  readOnly(h);
});
