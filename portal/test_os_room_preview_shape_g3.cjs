'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const page = fs.readFileSync(path.join(__dirname, 'os_room_preview.html'), 'utf8');
const contract = fs.readFileSync(path.join(__dirname, 'static/os_meeting_readonly_contract.js'), 'utf8');
const main = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)?.[1];
assert.ok(main, '页面须包含内联主脚本');
new vm.Script(main);
const secret = '只供有效决议展示的敏感正文';
const summary = { room_id: 'room-one', subject: '议题一' };
const goodRoom = () => ({
  room_id: 'room-one', subject: '议题一', status: 'open', board_version: 1,
  messages: [{ kind: 'decision', author_name: '甲', text: '不可直接展示的决议发言' }],
  board: [{ card_id: 'card-one', kind: 'decision', status: 'resolved', text: secret, content_hash: 'hash-one' }],
  resolutions: [{ resolution_id: 'res-one', room_id: 'room-one', card_id: 'card-one', status: 'proposed', text: secret, content_hash: 'hash-one' }],
  board_history: [{ version: 1, action: 'resolve', card_id: 'card-one' }]
});
const validResolution = room => ({
  ok: true, resolution: { ...room.resolutions[0] },
  integrity: { resolution_id: 'res-one', integrity: 'valid', approval_status: 'not_verified', actionable: false }
});
const reply = (data, status = 200) => ({ status, ok: status === 200, json: async () => data });
const flush = () => new Promise(resolve => setImmediate(resolve));

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
  click() { assert.equal(typeof this.handlers.click, 'function', '按钮须可点击'); return this.handlers.click(); }
  set innerHTML(value) { this.html = value; }
  get innerHTML() { return this.html || ''; }
  get visibleText() { return [this.textContent, this.innerHTML, ...this.children.map(child => child.visibleText)].join(' '); }
  descendants(predicate) { return [this, ...this.children.flatMap(child => child.descendants(predicate))].filter(predicate); }
}

function harness(initialResponder) {
  const ids = new Map();
  const get = id => { if (!ids.has(id)) ids.set(id, new Node()); return ids.get(id); };
  const requests = [];
  let responder = initialResponder;
  let prompts = 0;
  const store = new Map();
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
    get, requests,
    setResponder: next => { responder = next; },
    prompts: () => prompts,
    buttons: () => get('rooms').descendants(node => node.tag === 'button'),
    detail: () => get('detail-body').visibleText,
    state: () => get('state-text').textContent
  };
}

function assertReadOnly(h) {
  assert.ok(h.requests.length, '至少应发出一次只读请求');
  for (const { url, options } of h.requests) {
    assert.match(url, /^\/legion-api\/api\/legion\/room(?:\/list|\?room_id=|\/resolution\?room_id=)/, '仅请求房间只读接口');
    assert.equal(options.method, undefined, '不得指定写请求方法');
    assert.equal(options.cache, 'no-store');
  }
}
function assertClosed(h) {
  assert.equal(h.get('state').dataset.kind, 'err', '异常数据必须进入错误态');
  assert.match(h.state(), /格式|结构|异常|无效|缺失|重复|错误/, '须给出明确的数据错误提示（不绑定具体文案）');
  assert.match(h.detail(), /格式|结构|异常|无效|缺失|重复|错误/, '详情区须显示明确错误而非空白');
  assert.doesNotMatch(h.detail(), new RegExp(secret + '|不可直接展示的决议发言'), '错误时不得展示决议正文');
  assert.equal(h.get('detail-body').descendants(node => node.tag === 'button' && node.textContent === '查看只读详情').length, 0, '错误数据不能提供决议详情入口');
  assertReadOnly(h);
}

const cases = [
  ...[
    ['rooms 含 null', [null]],
    ['rooms 缺 room_id', [{ subject: '缺 ID' }]],
    ['rooms 空 room_id', [{ room_id: '', subject: '空 ID' }]],
    ['rooms 重复 room_id', [summary, { ...summary, subject: '重复 ID' }]]
  ].map(([name, rooms]) => [name, async () => {
    const h = harness(() => Promise.resolve(reply({ ok: true, rooms })));
    await flush();
    assertClosed(h);
    assert.equal(h.buttons().length, 0, '无效房间列表不能出现可选按钮');
    assert.equal(h.requests.length, 1, '列表错误不能继续请求详情');
  }]),
  ...[
    ['messages 含 null', room => { room.messages.push(null); }],
    ['board 含 null', room => { room.board.push(null); }],
    ['resolutions 含 null', room => { room.resolutions.push(null); }],
    ['board_history 含 null', room => { room.board_history.push(null); }],
    ['board 缺 card_id', room => { delete room.board[0].card_id; }],
    ['resolutions 缺 resolution_id', room => { delete room.resolutions[0].resolution_id; }],
    ['board_history 缺 card_id', room => { delete room.board_history[0].card_id; }],
    ['详情缺 room_id', room => { delete room.room_id; }],
    ['重复 resolution_id', room => { room.resolutions.push({ ...room.resolutions[0] }); }]
  ].map(([name, mutate]) => [name, async () => {
    const room = goodRoom();
    mutate(room);
    const h = harness(url => Promise.resolve(reply(url.endsWith('/room/list') ? { ok: true, rooms: [summary] } : url.includes('/room/resolution?') ? validResolution(goodRoom()) : { ok: true, room })));
    await flush();
    assert.equal(h.buttons().length, 1, '正常列表须能选择房间');
    await h.buttons()[0].click();
    assertClosed(h);
    assert.equal(h.requests.length, 2, '无效详情不能继续请求决议校验');
  }]),
  ['正常决议与 403 仍只读且不自动索 token', async () => {
    const room = goodRoom();
    const h = harness(() => Promise.resolve(reply({}, 403)));
    await flush();
    assert.equal(h.get('state').dataset.kind, 'auth');
    assert.equal(h.get('btn-token').hidden, false);
    assert.equal(h.prompts(), 0, '列表 403 不得自动索取 token');
    h.setResponder(url => Promise.resolve(url.endsWith('/room/list') ? reply({ ok: true, rooms: [summary] }) : url.includes('/room/resolution?') ? reply({}, 403) : reply({ ok: true, room })));
    h.get('btn-token').click();
    await flush();
    assert.equal(h.prompts(), 1, '只能由用户主动输入 token');
    assert.equal(h.buttons().length, 1);
    await h.buttons()[0].click();
    assert.equal(h.prompts(), 1, '决议 403 不得自动索取 token');
    assert.doesNotMatch(h.detail(), new RegExp(secret), '决议 403 时正文须隐藏');
    assert.equal(h.get('state').dataset.kind, 'auth');
    h.setResponder(url => Promise.resolve(reply(url.includes('/room/resolution?') ? validResolution(room) : { ok: true, room })));
    await h.buttons()[0].click();
    assert.equal(h.get('state').dataset.kind, 'ok');
    assert.doesNotMatch(h.detail(), new RegExp(secret), '房间概览即使校验通过也不能直接展示正文');
    const view = h.get('detail-body').descendants(node => node.tag === 'button' && node.textContent === '查看只读详情')[0];
    assert.ok(view, '有效决议须提供只读详情入口');
    view.click();
    assert.match(h.detail(), new RegExp(secret), '通过校验的决议详情须展示正文');
    assert.match(h.detail(), /内容一致/);
    assertReadOnly(h);
    assert.ok(h.requests.some(({ url }) => url.endsWith('/room/resolution?room_id=room-one&resolution_id=res-one')));
    assert.ok(h.requests.slice(1).every(({ options }) => options.headers['X-Portal-Token'] === 'manual-token'));
  }]
];

(async () => {
  const unhandled = [];
  const onUnhandled = error => unhandled.push(error);
  process.on('unhandledRejection', onUnhandled);
  const failures = [];
  try {
    for (const [name, run] of cases) {
      const before = unhandled.length;
      try {
        await run();
        await flush();
        assert.equal(unhandled.length, before, '不得出现未处理的 Promise 拒绝：' + String(unhandled.at(-1)));
      } catch (error) {
        failures.push(name + '：' + (error.stack || error));
      }
    }
  } finally {
    process.removeListener('unhandledRejection', onUnhandled);
  }
  if (failures.length) {
    console.error('FAIL G3 预览数据形状回归（' + failures.length + '/' + cases.length + '）\n' + failures.join('\n\n'));
    process.exitCode = 1;
  } else {
    console.log('PASS G3 预览数据形状回归：异常列表/详情拒绝展示、校验有效详情、GET-only、403 不自动索 token');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
