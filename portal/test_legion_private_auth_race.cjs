'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const scripts = [...fs.readFileSync(path.join(__dirname, 'legion.html'), 'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
assert.equal(scripts.length, 2);
const startup = 'apiReady.then(()=>{ tick(); setInterval(tick, 30000); });';
assert.ok(scripts[0].includes(startup), '测试必须显式控制定时 tick');

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const ops = label => ({
  roster: { n: 1, n_active: 1, members: [{ id: 'A0', name: `${label}成员`, state: 'active', dept: 'team' }] },
  tasks: [{ id: 'T1', desc: `${label}工单`, status: 'review' }],
  feed: [{ action: `${label}活动` }],
});
const reviews = label => ({ ok: true, reviews: { A0: { agent_id: 'A0', q1: `${label}复盘`, q2: '' } } });
const agent = label => ({ ok: true, name: `${label}成员`, agent_id: 'A0', dept: 'team', title: '成员', rank: '一', editable: {}, tasks: [], recent_acts: [] });
const review = label => ({ ok: true, tid: 'T1', desc: `${label}工单复盘`, status: 'review', findings: [`${label}判读`] });

function harness(t) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) {
      elements.set(id, {
        style: {}, html: '', text: '',
        get innerHTML() { return this.html; },
        set innerHTML(value) { this.html = String(value); this.text = ''; },
        get textContent() { return this.text; },
        set textContent(value) { this.text = String(value); this.html = ''; },
        querySelectorAll() { return []; },
        querySelector() { return { addEventListener() {} }; },
        addEventListener() {},
      });
    }
    return elements.get(id);
  };
  const tokens = new Map([['legionToken', 'old-token']]);
  const requests = [];
  let answer = '';
  let promptCount = 0;
  let probe = true;
  const response = (data, status = 200) => ({ ok: status === 200, status, json: async () => data });
  const context = vm.createContext({
    window: {},
    document: { getElementById: element, querySelectorAll: () => [] },
    sessionStorage: { getItem: key => tokens.get(key) || null, setItem: (key, value) => tokens.set(key, value) },
    setInterval() {},
    prompt() { promptCount++; return answer; },
    fetch(url, options = {}) {
      assert.ok(!options.method || options.method === 'GET', '只读竞态测试不得发送 POST');
      if (probe) {
        probe = false;
        assert.ok(url.endsWith('/ops'), '仅启动探针立即返回');
        return Promise.resolve(response({}));
      }
      const request = { url, options, network: deferred() };
      requests.push(request);
      if (!/\/ops$|\/reviews\/today$|\/review\?|\/agent\?/.test(url)) {
        const data = url.includes('/academy/topics') ? { topics: [] } : { meetings: [], rooms: [] };
        request.network.resolve(response(data));
      }
      return request.network.promise;
    },
  });
  vm.runInContext(scripts[0].replace(startup, ''), context);
  vm.runInContext(scripts[1], context);
  const call = expression => vm.runInContext(expression, context);
  const pending = (route, token) => {
    const found = requests.findLast(req => !req.done && req.url.includes(route) && (token === undefined || req.options.headers?.['X-Portal-Token'] === token));
    assert.ok(found, `等待 ${route} (${token || '任意 token'}) GET`);
    return found;
  };
  const finish = async (req, running, data, status = 200) => {
    assert.equal(req.done, undefined, 'GET 只能完成一次');
    req.done = true;
    req.network.resolve(response(data, status));
    await running;
  };
  const fail = async (req, running, error) => {
    req.done = true;
    req.network.reject(error);
    await running;
  };
  const shown = id => element(id).innerHTML + element(id).textContent;
  const reenter = token => {
    answer = token;
    assert.equal(call('promptLegionToken()'), true, '模拟用户主动重填 token');
    assert.equal(tokens.get('legionToken'), token);
  };
  t.after(() => assert.ok(requests.every(req => !req.options.method || req.options.method === 'GET'), '不得请求 POST'));
  return { call, pending, finish, fail, shown, reenter, context,
    get promptCount() { return promptCount; }, ready: call('apiReady'),
  };
}

async function successfulTick(h, label, token) {
  const running = h.call('tick()');
  await h.finish(h.pending('/ops', token), running, ops(label));
  await h.finish(h.pending('/reviews/today', token), Promise.resolve(), reviews(label));
  await new Promise(resolve => setImmediate(resolve));
}

test('旧 token 的 /ops 403 晚于新 token 成功，不清屏也不弹凭据提示', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('tick()');
  const oldReq = h.pending('/ops', 'old-token');
  h.reenter('new-token');
  await successfulTick(h, '新', 'new-token');
  await h.finish(oldReq, old, { ok: false }, 403);
  for (const id of ['wall', 'feed', 'lane-review', 'review-drawer']) {
    assert.match(h.shown(id), /新/, id);
    assert.doesNotMatch(h.shown(id), /需要访问凭据|数据源不可达/, id);
  }
  assert.equal(h.shown('s-total'), '1');
  assert.equal(h.promptCount, 1, '只有主动重填产生 prompt');
});

test('同一 token 字符串重填：旧 /ops 403 晚于新成功仍不清屏', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('tick()');
  const oldReq = h.pending('/ops', 'old-token');
  h.reenter('old-token');
  await successfulTick(h, '重填后', 'old-token');
  await h.finish(oldReq, old, { ok: false }, 403);
  assert.match(h.shown('feed'), /重填后活动/);
  assert.match(h.shown('review-drawer'), /重填后复盘/);
  assert.doesNotMatch(h.shown('wall') + h.shown('feed'), /需要访问凭据|数据源不可达/);
  assert.equal(h.promptCount, 1);
});

test('当前 token 的 /ops 403 必须遮蔽既有私有数据，不自动弹窗或 POST', async t => {
  const h = harness(t);
  await h.ready;
  await successfulTick(h, '先前', 'old-token');
  const drill = h.call('drill("A0")');
  await h.finish(h.pending('/agent?', 'old-token'), drill, agent('先前'));
  assert.match(h.shown('modal-body'), /先前成员/);
  const running = h.call('tick()');
  await h.finish(h.pending('/ops', 'old-token'), running, { ok: false }, 403);
  for (const id of ['wall', 'lane-review', 'review-drawer', 'modal-body', 'feed']) {
    assert.doesNotMatch(h.shown(id), /先前成员|先前工单|先前复盘|先前活动/, id);
    assert.match(h.shown(id), /需要访问凭据/, id);
  }
  assert.equal(Object.keys(h.context.window._reviews).length, 0);
  assert.equal(h.shown('s-total'), '-');
  assert.match(h.shown('feed'), /输入 token 后重试/);
  assert.equal(h.promptCount, 0);
});

test('旧 tick 的普通异常晚于新 feed 成功，不能改成断线', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('tick()');
  const oldReq = h.pending('/ops', 'old-token');
  h.reenter('new-token');
  await successfulTick(h, '在线', 'new-token');
  await h.fail(oldReq, old, new Error('旧网络异常'));
  assert.match(h.shown('feed'), /在线活动/);
  assert.doesNotMatch(h.shown('feed'), /数据源不可达|旧网络异常|需要访问凭据/);
  assert.equal(h.promptCount, 1);
});

for (const { name, start, route, payload, target, expected } of [
  { name: '今日复盘', start: 'loadReviews()', route: '/reviews/today', payload: reviews, target: 'review-drawer', expected: /新复盘/ },
  { name: '工单复盘', start: 'reviewTk("T1")', route: '/review?', payload: review, target: 'modal-body', expected: /新工单复盘|新判读/ },
  { name: '成员详情', start: 'drill("A0")', route: '/agent?', payload: agent, target: 'modal-body', expected: /新成员/ },
]) {
  test(`旧 ${name} 私人 GET 的 403 晚于新成功，不遮蔽新视图`, async t => {
    const h = harness(t);
    await h.ready;
    const old = h.call(start);
    const oldReq = h.pending(route, 'old-token');
    h.reenter('new-token');
    const newer = h.call(start);
    await h.finish(h.pending(route, 'new-token'), newer, payload('新'));
    await h.finish(oldReq, old, { ok: false }, 403);
    assert.match(h.shown(target), expected);
    assert.doesNotMatch(h.shown(target), /需要访问凭据|下钻失败|复盘失败/);
    assert.equal(h.promptCount, 1);
  });
}
