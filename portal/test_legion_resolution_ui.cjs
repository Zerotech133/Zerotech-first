'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, 'legion.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert.equal(scripts.length, 2);
const elements = new Map();
const node = (dataset = {}) => ({
  dataset, style: {}, textContent: '', innerHTML: '',
  addEventListener(type, listener) { this[type] = listener; },
  focus() { this.focused = true; },
});
const element = id => {
  if (!elements.has(id)) elements.set(id, node());
  return elements.get(id);
};
const nodesIn = (markup, key) => [...markup.matchAll(new RegExp(`data-${key}="(\\d+)"`, 'g'))]
  .map(m => node({ [key.replace(/-([a-z])/g, (_, c) => c.toUpperCase())]: m[1] }));
let buttons = [];
let cards = [];
let rooms = [];
let reply;
let fetcher = async () => reply;
let promptCount = 0;
const tokenStore = new Map();
const requests = [];
const context = vm.createContext({
  document: {
    getElementById: element,
    querySelectorAll: selector => selector.includes('resolution-index') ? buttons : cards,
  },
  sessionStorage: { getItem: key => tokenStore.get(key), setItem: (key, value) => tokenStore.set(key, value) },
  prompt: () => { promptCount++; return ''; },
  fetch: async (url, options) => {
    requests.push({ url, options });
    return fetcher(url, options);
  },
});
vm.runInContext(scripts[0].replace(/apiReady\.then\(\(\)=>\{ tick\(\); setInterval\(tick, 30000\); \}\);/, ''), context);
vm.runInContext(scripts[1], context);
const call = (expression) => vm.runInContext(expression, context);
const resolution = { resolution_id: 'res-one', room_id: 'room-one', card_id: 'card-one', text: '<b>正文</b>' };
const response = (integrity) => ({ ok: true, resolution, integrity: {
  resolution_id: 'res-one', integrity, approval_status: 'not_verified', actionable: false,
} });
const respond = (body, status = 200) => { reply = { ok: status === 200, status, json: async () => body }; };
const body = element('modal-body');
const detailHtml = () => body.innerHTML + element('resolution-detail-status').innerHTML;
element('room-list').querySelectorAll = () => rooms;
const room = (roomId = 'room-one', overrides = {}) => ({
  room_id: roomId, subject: '议题', board_version: 1,
  board: [{ card_id: 'card-one', kind: 'decision', text: '敏感正文', status: 'resolved' }],
  resolutions: [{ resolution_id: 'res-one', card_id: 'card-one' }], ...overrides,
});
const render = x => {
  buttons = nodesIn('<button data-resolution-index="0">', 'resolution-index');
  cards = nodesIn('<select data-card-index="0">', 'card-index');
  call(`renderRoom(${JSON.stringify(x)})`);
  assert.match(body.innerHTML, /data-resolution-index="0"/);
  assert.match(body.innerHTML, /data-card-index="0"/);
  assert.match(body.innerHTML, /id="resolution-state-0" role="status" aria-live="polite" aria-atomic="true"/);
};
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  respond({ ok: false, err: 'read unauthorized' }, 403);
  await call('loadRooms()');
  assert.equal(promptCount, 0, '定时列表请求 403 不得弹 token');
  assert.match(element('room-list').textContent, /需要访问凭据/);
  await call('loadTriPartyMeetings()');
  assert.equal(promptCount, 0, '定时会议列表请求 403 不得弹 token');
  assert.match(element('tri-party-meetings').textContent, /需要访问凭据/);
  respond(response('valid'));
  const valid = await call('fetchRoomResolution("room-one", "res-one")');
  assert.equal(valid.integrity.integrity, 'valid');
  const lookup = requests.find(request => /\/room\/resolution\?/.test(request.url));
  assert.match(lookup.url, /room_id=room-one&resolution_id=res-one$/);
  assert.equal(lookup.options.cache, 'no-store');
  await call('openRoomResolution("room-one", "res-one")');
  assert.match(body.innerHTML, /role="status" aria-live="polite" aria-atomic="true"/);
  assert.match(detailHtml(), /内容一致≠已批准/);
  assert.match(detailHtml(), /待签准/);
  assert.match(detailHtml(), /&lt;b&gt;正文&lt;\/b&gt;/);
  assert.doesNotMatch(body.innerHTML, /<button/);

  respond({ ...response('invalid'), resolution: null });
  await call('openRoomResolution("room-one", "res-one")');
  assert.match(detailHtml(), /正文不可信，已隐藏/);
  assert.doesNotMatch(body.innerHTML, /正文<\/b>/);

  for (const bad of [
    { ok: true, resolution, integrity: 'valid', approval_status: 'not_verified', actionable: false },
    { ...response('valid'), integrity: { ...response('valid').integrity, actionable: true } },
    { ...response('valid'), resolution: { ...resolution, room_id: 'other-room' } },
    { ok: false, err: '不可达' },
  ]) {
    respond(bad);
    await call('openRoomResolution("room-one", "res-one")');
    assert.match(detailHtml(), /决议校验失败/);
    assert.doesNotMatch(body.innerHTML, /正文<\/b>/);
  }
  respond({ ok: false, err: 'read unauthorized' }, 403);
  await call('openRoomResolution("room-one", "res-one")');
  assert.match(detailHtml(), /决议校验失败.*需要访问凭据/);
  assert.doesNotMatch(body.innerHTML, /内容一致 · 待签准|&lt;b&gt;正文/);
  assert.match(body.innerHTML, /role="status" aria-live="polite"/);
  assert.equal(promptCount, 1);

  respond({ ...response('invalid'), resolution: null });
  render(room());
  await flush();
  assert.doesNotMatch(body.innerHTML, /敏感正文/);
  assert.match(detailHtml(), /内容一致≠已批准/);
  assert.match(body.innerHTML, /白板 resolved\/done、发言 decision 均非批准/);
  assert.match(body.innerHTML, /resolved（非批准）|done（非批准）/);
  assert.equal(element('resolution-state-0').textContent, '完整性无效 · 待签准');
  buttons[0].click();
  await flush();
  assert.match(detailHtml(), /正文不可信，已隐藏/);
  assert.match(body.innerHTML, /返回原房间/);
  assert.equal(element('resolution-back').focused, true, '返回按钮可由键盘聚焦');
  element('resolution-back').click();
  assert.equal(buttons[0].focused, true, '返回后焦点回到只读校验入口');
  assert.match(body.innerHTML, /🏛 议题/);
  assert.doesNotMatch(body.innerHTML, /敏感正文/);
  await flush();
  assert.equal(element('resolution-state-0').textContent, '完整性无效 · 待签准');

  const sensitive = room('room-one', { messages: [
    { kind: 'decision', text: '敏感正文', refs: ['敏感引用'], author: 'USER' },
  ] });
  respond({ ok: false, err: 'read unauthorized' }, 403);
  render(sensitive);
  await flush();
  assert.doesNotMatch(body.innerHTML, /敏感正文|敏感引用|内容一致 · 待签准/);
  assert.match(body.innerHTML, /决议相关发言正文已隐藏/);
  assert.equal(element('resolution-state-0').textContent, '校验不可用 · 正文已隐藏');
  assert.equal(element('resolution-state-0').style.color, 'var(--dim)', '403 不得呈可靠校验标记');
  assert.equal(promptCount, 1, '自动校验 403 不得弹 token');
  fetcher = async () => { throw new Error('network down'); };
  render(sensitive);
  await flush();
  assert.equal(element('resolution-state-0').textContent, '校验不可用 · 正文已隐藏');
  assert.equal(promptCount, 1);
  fetcher = async () => reply;

  // 决议请求悬而未决时返回，旧详情不得盖掉房间；切换房间时旧状态也不得写入。
  const pendingDetail = deferred();
  fetcher = () => pendingDetail.promise;
  const detail = call('openRoomResolution("room-one", "res-one")');
  assert.match(detailHtml(), /返回原房间.*校验中/);
  element('resolution-back').click();
  assert.match(body.innerHTML, /🏛 议题/);
  pendingDetail.resolve({ ok: true, status: 200, json: async () => response('valid') });
  await detail;
  assert.match(body.innerHTML, /🏛 议题/);
  assert.doesNotMatch(body.innerHTML, /&lt;b&gt;正文/);

  const pendingState = deferred();
  fetcher = () => pendingState.promise;
  render(room());
  assert.match(body.innerHTML, /完整性校验中/);
  fetcher = async () => ({ ok: true, status: 200, json: async () => ({ ...response('invalid'), resolution: null }) });
  render(room('room-two', { subject: '第二间', resolutions: [{ resolution_id: 'res-one', card_id: 'card-one' }] }));
  await flush();
  assert.match(body.innerHTML, /第二间/);
  assert.equal(element('resolution-state-0').textContent, '完整性无效 · 待签准');
  pendingState.resolve({ ok: true, status: 200, json: async () => response('valid') });
  await flush();
  assert.equal(element('resolution-state-0').textContent, '完整性无效 · 待签准');

  const pendingBeforeDetail = deferred();
  fetcher = () => pendingBeforeDetail.promise;
  render(room());
  fetcher = async () => ({ ok: true, status: 200, json: async () => response('valid') });
  await call('openRoomResolution("room-one", "res-one")');
  assert.match(detailHtml(), /&lt;b&gt;正文&lt;\/b&gt;/);
  pendingBeforeDetail.resolve({ ok: true, status: 200, json: async () => ({ ...response('invalid'), resolution: null }) });
  await flush();
  assert.match(detailHtml(), /&lt;b&gt;正文&lt;\/b&gt;/);
  assert.doesNotMatch(body.innerHTML, /完整性无效/);
  element('resolution-back').click();

  const pendingAfterClose = deferred();
  fetcher = () => pendingAfterClose.promise;
  const closingDetail = call('openRoomResolution("room-one", "res-one")');
  call('closeModal()');
  pendingAfterClose.resolve({ ok: true, status: 200, json: async () => response('valid') });
  await closingDetail;
  assert.equal(element('modal').style.display, 'none');
  assert.doesNotMatch(body.innerHTML, /&lt;b&gt;正文/);

  const pendingAfterCloseState = deferred();
  fetcher = () => pendingAfterCloseState.promise;
  render(room());
  call('closeModal()');
  pendingAfterCloseState.resolve({ ok: true, status: 200, json: async () => response('valid') });
  await flush();
  assert.notEqual(element('resolution-state-0').textContent, '内容一致 · 待签准', '关闭后旧校验不得更新状态');

  const pendingRoom = deferred();
  fetcher = () => pendingRoom.promise;
  const oldRoom = call('openRoom("room-one")');
  fetcher = async () => ({ ok: true, status: 200, json: async () => ({ ok: true, room: room('room-two', { subject: '新房间', resolutions: [] }) }) });
  await call('openRoom("room-two")');
  pendingRoom.resolve({ ok: true, status: 200, json: async () => ({ ok: true, room: room('room-one', { subject: '旧房间' }) }) });
  await oldRoom;
  assert.match(body.innerHTML, /新房间/);
  assert.doesNotMatch(body.innerHTML, /旧房间/);

  // ID / 状态来自 room JSON，不能穿透到 HTML 事件属性或新属性。
  const attack = `x' onmouseover='globalThis.injected=1`;
  const malicious = room(attack, {
    status: `open\" onfocus=\"globalThis.injected=1`,
    board_version: `<img src=x onerror=alert(1)>`,
    board: [{ card_id: attack, kind: 'decision', text: '隐藏正文', status: attack }],
    resolutions: [{ resolution_id: attack, card_id: attack }],
  });
  fetcher = async () => ({ ok: true, status: 200, json: async () => ({ ...response('invalid'), resolution: null, integrity: { ...response('invalid').integrity, resolution_id: attack } }) });
  render(malicious);
  assert.doesNotMatch(body.innerHTML, /<[^>]+\s(?:onmouseover|onfocus|onclick|onchange)=|<img/);
  assert.match(body.innerHTML, /&lt;img/);
  assert.doesNotMatch(body.innerHTML, /隐藏正文/);
  cards[0].value = 'done';
  fetcher = async (url, options) => {
    if (options.method === 'POST') return { json: async () => ({ ok: false, err: '只读测试' }) };
    return { ok: true, status: 200, json: async () => ({ ...response('invalid'), resolution: null }) };
  };
  context.alert = () => {};
  cards[0].change();
  await flush();
  const boardRequest = requests.findLast(r => r.options && r.options.method === 'POST');
  assert.equal(JSON.parse(boardRequest.options.body).card_id, attack);
  assert.equal(JSON.parse(boardRequest.options.body).room_id, attack);
  rooms = [node({ roomIndex: '0' })];
  fetcher = async () => ({ ok: true, status: 200, json: async () => ({ ok: true, rooms: [{ room_id: attack, subject: '不可信房间', status: attack, message_count: 1, board_version: 2 }] }) });
  await call('loadRooms()');
  assert.doesNotMatch(element('room-list').innerHTML, /<[^>]+\s(?:onclick|onmouseover)=/);
  assert.match(element('room-list').innerHTML, /&#39;/);
  rooms[0].click();
  assert.match(requests.at(-1).url, new RegExp(`room_id=${encodeURIComponent(attack)}`));
  call('closeModal()');
  console.log('PASS 决议前端：返回导航、异步竞态、不可信 ID、只读校验与正文隐藏');
})().catch(err => { console.error(err); process.exitCode = 1; });
