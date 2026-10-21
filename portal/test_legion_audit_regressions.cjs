'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const scripts = [...fs.readFileSync(path.join(__dirname, 'legion.html'), 'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
assert.equal(scripts.length, 2);
const startup = 'apiReady.then(()=>{ tick(); setInterval(tick, 30000); });';
assert.ok(scripts[0].includes(startup), '测试必须显式控制 tick');

function deferred() {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return { promise, resolve };
}
const ops = label => ({
  roster: { n: 1, n_active: 1, members: [{ id: 'A0', name: `${label}成员`, state: 'active', dept: 'team' }] },
  tasks: [{ id: 'T1', desc: `${label}工单`, status: 'review' }],
  feed: [{ action: `${label}活动` }],
});
const reviews = label => ({ ok: true, reviews: { A0: { agent_id: 'A0', q1: `${label}复盘`, q2: '' } } });
const agent = label => ({ ok: true, name: `${label}成员详情`, agent_id: 'A0', dept: 'team', title: '成员', rank: '一', editable: {}, tasks: [], recent_acts: [] });
const room = label => ({ room_id: label, subject: `${label}议题`, message_count: 0, board_version: 0, status: 'open' });

function harness(t) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      style: {}, html: '', text: '',
      get innerHTML() { return this.html; },
      set innerHTML(value) { this.html = String(value); this.text = ''; },
      get textContent() { return this.text; },
      set textContent(value) { this.text = String(value); this.html = ''; },
      querySelectorAll() { return []; },
      querySelector() { return { addEventListener() {} }; },
      addEventListener() {},
    });
    return elements.get(id);
  };
  const tokens = new Map([['legionToken', 'old-token']]);
  const requests = [];
  let answer = '';
  let promptCount = 0;
  let probe = true;
  const context = vm.createContext({
    window: {},
    document: { getElementById: element, querySelectorAll: () => [] },
    sessionStorage: { getItem: key => tokens.get(key) || null, setItem: (key, value) => tokens.set(key, value) },
    setInterval() {},
    prompt() { promptCount++; return answer; },
    fetch(url, options = {}) {
      assert.ok(!options.method || options.method === 'GET', '不得发送 POST');
      if (probe) {
        probe = false;
        assert.ok(url.endsWith('/ops'), '只有启动探针自动完成');
        return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
      }
      const req = { url, options, network: deferred(), body: deferred() };
      requests.push(req);
      return req.network.promise;
    },
  });
  vm.runInContext(scripts[0].replace(startup, ''), context);
  vm.runInContext(scripts[1], context);
  const call = expression => vm.runInContext(expression, context);
  const pending = (route, token) => {
    const req = requests.findLast(r => !r.done && r.url.includes(route) && (token === undefined || r.options.headers?.['X-Portal-Token'] === token));
    assert.ok(req, `等待 ${route} (${token || '任意 token'}) GET`);
    return req;
  };
  const respondFetch = (req, status = 200) => req.network.resolve({ ok: status >= 200 && status < 300, status, json: () => req.body.promise });
  const finish = async (req, running, data, status = 200) => {
    assert.equal(req.done, undefined, 'GET 只能完成一次');
    req.done = true;
    respondFetch(req, status);
    req.body.resolve(data);
    await running;
  };
  const shown = id => element(id).innerHTML + element(id).textContent;
  const reenter = token => {
    answer = token;
    assert.equal(call('promptLegionToken()'), true, '主动重填 token');
    assert.equal(tokens.get('legionToken'), token);
  };
  t.after(() => assert.ok(requests.every(r => !r.options.method || r.options.method === 'GET'), '不得发送 POST'));
  return { call, pending, respondFetch, finish, shown, reenter, context,
    get promptCount() { return promptCount; }, ready: call('apiReady') };
}

async function seedPrivateContent(h) {
  const running = h.call('tick()');
  await h.finish(h.pending('/ops'), running, ops('先前'));
  await h.finish(h.pending('/reviews/today'), Promise.resolve(), reviews('先前'));
  await h.finish(h.pending('/tri-party/meetings'), Promise.resolve(), { ok: true, meetings: [{ meeting_id: 'M1', subject: '先前会议', status: 'open', disagreement_count: 0 }] });
  await h.finish(h.pending('/academy/topics'), Promise.resolve(), { ok: true, topics: [] });
  await h.finish(h.pending('/room/list'), Promise.resolve(), { ok: true, rooms: [room('先前房间')], meta: { complete: true } });
  await new Promise(resolve => setImmediate(resolve));
  const drill = h.call('drill("A0")');
  await h.finish(h.pending('/agent?'), drill, agent('先前'));
  for (const [id, marker] of [['wall', '先前成员'], ['feed', '先前活动'], ['lane-review', '先前工单'],
    ['review-drawer', '先前复盘'], ['room-list', '先前房间'], ['tri-party-meetings', '先前会议'], ['modal-body', '先前成员详情']]) {
    assert.match(h.shown(id), new RegExp(marker), `前置真实 GET 已渲染 ${id}`);
  }
}

for (const source of ['成员详情', '今日复盘']) {
  test(`同 token 的 /ops 悬挂，较新${source}成功后 /ops 403 必须清全部私人内容`, async t => {
    const h = harness(t);
    await h.ready;
    await seedPrivateContent(h);
    const running = h.call('tick()');
    const older = h.pending('/ops', 'old-token');
    const newer = source === '成员详情' ? h.call('drill("A0")') : h.call('loadReviews()');
    const route = source === '成员详情' ? '/agent?' : '/reviews/today';
    await h.finish(h.pending(route, 'old-token'), newer, source === '成员详情' ? agent('较新') : reviews('较新'));
    assert.match(h.shown(source === '成员详情' ? 'modal-body' : 'review-drawer'), /较新/);
    await h.finish(older, running, { ok: false }, 403);
    for (const id of ['wall', 'feed', 'lane-doing', 'lane-review', 'review-drawer', 'room-list', 'tri-party-meetings', 'modal-body']) {
      assert.match(h.shown(id), /需要访问凭据/, `${id} 必须清屏`);
      assert.doesNotMatch(h.shown(id), /先前|较新/, `${id} 不得残留私人内容`);
    }
    assert.equal(h.shown('s-total'), '-');
    assert.equal(Object.keys(h.context.window._reviews).length, 0);
    assert.equal(h.promptCount, 0);
  });
}

test('旧 token 的 /ops 403 晚于新 token 的总览成功，不得清屏', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('tick()');
  const oldReq = h.pending('/ops', 'old-token');
  h.reenter('new-token');
  const newer = h.call('tick()');
  await h.finish(h.pending('/ops', 'new-token'), newer, ops('新'));
  await h.finish(h.pending('/reviews/today', 'new-token'), Promise.resolve(), reviews('新'));
  await h.finish(h.pending('/tri-party/meetings', 'new-token'), Promise.resolve(), { ok: true, meetings: [] });
  await h.finish(h.pending('/academy/topics'), Promise.resolve(), { ok: true, topics: [] });
  await h.finish(h.pending('/room/list', 'new-token'), Promise.resolve(), { ok: true, rooms: [], meta: { complete: true } });
  await h.finish(oldReq, old, { ok: false }, 403);
  for (const id of ['wall', 'feed', 'lane-review', 'review-drawer']) {
    assert.match(h.shown(id), /新/, id);
    assert.doesNotMatch(h.shown(id), /需要访问凭据/, id);
  }
  assert.equal(h.shown('s-total'), '1');
  assert.equal(h.promptCount, 1);
});

test('room/list 较新成功后旧成功不得覆盖（旧 fetch 已返回但 JSON 悬挂）', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('loadRooms()');
  const oldReq = h.pending('/room/list');
  h.respondFetch(oldReq);
  const newer = h.call('loadRooms()');
  await h.finish(h.pending('/room/list'), newer, { ok: true, rooms: [room('新房间')], meta: { complete: true } });
  await h.finish(oldReq, old, { ok: true, rooms: [room('旧房间')], meta: { complete: true } });
  assert.match(h.shown('room-list'), /新房间/);
  assert.doesNotMatch(h.shown('room-list'), /旧房间/);
});

test('/ops 403 清屏后旧 room/list 200 不得回填（JSON 悬挂）', async t => {
  const h = harness(t);
  await h.ready;
  await seedPrivateContent(h);
  const old = h.call('loadRooms()');
  const oldReq = h.pending('/room/list');
  h.respondFetch(oldReq);
  const running = h.call('tick()');
  await h.finish(h.pending('/ops'), running, { ok: false }, 403);
  assert.match(h.shown('room-list'), /需要访问凭据/);
  await h.finish(oldReq, old, { ok: true, rooms: [room('旧房间私密')], meta: { complete: true } });
  assert.match(h.shown('room-list'), /需要访问凭据/);
  assert.doesNotMatch(h.shown('room-list'), /旧房间私密|先前房间/);
  assert.equal(h.promptCount, 0);
});

for (const { name, payload, status, empty } of [
  { name: '不完整空列表', payload: { ok: true, rooms: [], meta: { complete: false } } },
  { name: '缺 meta 的空列表', payload: { ok: true, rooms: [] } },
  { name: '完整空列表', payload: { ok: true, rooms: [], meta: { complete: true } }, empty: true },
  { name: '业务失败', payload: { ok: false, rooms: [], meta: { complete: true }, err: '不可用' } },
  { name: 'HTTP 500', payload: { ok: true, rooms: [], meta: { complete: true } }, status: 500 },
]) {
  test(`room/list ${name}：${empty ? '仅此可显示' : '不得显示'}“暂无议题房间”`, async t => {
    const h = harness(t);
    await h.ready;
    const running = h.call('loadRooms()');
    await h.finish(h.pending('/room/list'), running, payload, status);
    if (empty) assert.match(h.shown('room-list'), /暂无议题房间/);
    else assert.doesNotMatch(h.shown('room-list'), /暂无议题房间/);
  });
}

test('三方会议 HTTP 500 即使携带空 meetings 也不得显示“暂无三方会议”', async t => {
  const h = harness(t);
  await h.ready;
  const running = h.call('loadTriPartyMeetings()');
  await h.finish(h.pending('/tri-party/meetings'), running, { meetings: [] }, 500);
  assert.doesNotMatch(h.shown('tri-party-meetings'), /暂无三方会议/);
});
