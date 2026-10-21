'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const scripts = [...fs.readFileSync(path.join(__dirname, 'legion.html'), 'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
assert.equal(scripts.length, 2);
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const meeting = subject => ({ meeting_id: subject, subject, status: 'open', confirmation: { status: 'pending' }, disagreement_count: 0 });
const detail = (meeting_id, subject = meeting_id) => ({ ok: true, meeting: { ...meeting(subject), meeting_id, views: {}, disagreements: [], material_fingerprint: 'a'.repeat(64) } });
const topic = title => ({ topic_id: title, title, status: 'open', owner: '负责人' });

function harness(t) {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) {
      const el = {
        style: {}, html: '', text: '',
        get innerHTML() { return this.html; },
        set innerHTML(value) { this.html = String(value); this.text = ''; },
        get textContent() { return this.text; },
        set textContent(value) { this.text = String(value); this.html = ''; },
        querySelectorAll() { return []; },
        querySelector(selector) {
          if (selector === 'button' && this.html.includes('<button')) return { addEventListener() {} };
          if (!this.html.includes(`id="${selector.slice(1)}"`)) return null;
          return { addEventListener() {} };
        },
      };
      elements.set(id, el);
    }
    return elements.get(id);
  };
  const requests = [];
  let opsStatus = 200;
  let promptCount = 0;
  const context = vm.createContext({
    window: {},
    document: { getElementById: element, querySelectorAll: () => [] },
    sessionStorage: { getItem: () => 'expired-token', setItem() {} },
    setInterval() {},
    prompt() { promptCount++; return ''; },
    fetch(url, options = {}) {
      if (options.method === 'POST') throw new Error('只读竞态测试不得发送 POST');
      if (url.endsWith('/ops')) return Promise.resolve({ ok: opsStatus === 200, status: opsStatus, json: async () => ({}) });
      const request = { url, options, network: deferred(), body: deferred() };
      requests.push(request);
      return request.network.promise;
    },
  });
  vm.runInContext(scripts[0].replace(/apiReady\.then\(\(\)=>\{ tick\(\); setInterval\(tick, 30000\); \}\);/, ''), context);
  vm.runInContext(scripts[1], context);
  const call = expression => vm.runInContext(expression, context);
  const request = (index, suffix) => {
    assert.ok(requests[index], `第 ${index + 1} 个 GET 已发出`);
    assert.ok(requests[index].url.includes(suffix), requests[index].url);
    return requests[index];
  };
  const respondFetch = (req, status = 200) => req.network.resolve({
    ok: status === 200, status, json: () => req.body.promise,
  });
  const finish = async (req, data, running, status = 200) => {
    respondFetch(req, status);
    req.body.resolve(data);
    await running;
  };
  const visible = id => element(id).innerHTML + element(id).textContent;
  t.after(() => assert.ok(requests.every(req => !req.options.method || req.options.method === 'GET'), '不得发送 POST'));
  return { element, requests, call, request, respondFetch, finish, visible,
    get promptCount() { return promptCount; },
    async clearBy403() { opsStatus = 403; await call('tick()'); },
    ready: call('apiReady'),
  };
}

test('会议列表：较早成功（fetch 已完成、json 未完成）不能覆盖较新结果', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('loadTriPartyMeetings()');
  const first = h.request(0, '/tri-party/meetings');
  h.respondFetch(first);
  const newer = h.call('loadTriPartyMeetings()');
  await h.finish(h.request(1, '/tri-party/meetings'), { meetings: [meeting('新会议')] }, newer);
  await h.finish(first, { meetings: [meeting('旧会议')] }, old);
  assert.match(h.visible('tri-party-meetings'), /新会议/);
  assert.doesNotMatch(h.visible('tri-party-meetings'), /旧会议/);
});

test('会议列表：较新 403 清屏后，悬挂的旧成功不得回填', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('loadTriPartyMeetings()');
  const first = h.request(0, '/tri-party/meetings');
  h.respondFetch(first);
  const newer = h.call('loadTriPartyMeetings()');
  h.respondFetch(h.request(1, '/tri-party/meetings'), 403);
  await newer;
  assert.match(h.visible('tri-party-meetings'), /需要访问凭据/);
  await h.finish(first, { meetings: [meeting('旧会议私密内容')] }, old);
  assert.match(h.visible('tri-party-meetings'), /需要访问凭据/);
  assert.doesNotMatch(h.visible('tri-party-meetings'), /旧会议私密内容/);
  assert.equal(h.promptCount, 0);
});

test('会议列表：总览 403 清屏后，悬挂的旧成功不得回填', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('loadTriPartyMeetings()');
  const first = h.request(0, '/tri-party/meetings');
  h.respondFetch(first);
  await h.clearBy403();
  assert.match(h.visible('tri-party-meetings'), /需要访问凭据/);
  await h.finish(first, { meetings: [meeting('旧会议私密内容')] }, old);
  assert.match(h.visible('tri-party-meetings'), /需要访问凭据/);
  assert.doesNotMatch(h.visible('tri-party-meetings'), /旧会议私密内容/);
  assert.equal(h.promptCount, 0);
});

test('课题台账：较早成功不能覆盖较新结果', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('loadAcademyTopics()');
  const first = h.request(0, '/academy/topics');
  h.respondFetch(first);
  const newer = h.call('loadAcademyTopics()');
  await h.finish(h.request(1, '/academy/topics'), { topics: [topic('新课题')] }, newer);
  await h.finish(first, { topics: [topic('旧课题')] }, old);
  assert.match(h.visible('academy-topics'), /新课题/);
  assert.doesNotMatch(h.visible('academy-topics'), /旧课题/);
});

test('会议详情：新详情成功后，旧详情成功不能覆盖', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('openTriPartyMeeting("old")');
  const first = h.request(0, 'meeting?meeting_id=old');
  h.respondFetch(first);
  const newer = h.call('openTriPartyMeeting("new")');
  await h.finish(h.request(1, 'meeting?meeting_id=new'), detail('new', '新详情'), newer);
  await h.finish(first, detail('old', '旧详情'), old);
  assert.match(h.visible('modal-body'), /新详情/);
  assert.doesNotMatch(h.visible('modal-body'), /旧详情/);
  assert.equal(h.element('modal').style.display, 'flex');
});

for (const outcome of ['成功', '报错']) {
  test(`会议详情：closeModal 后旧请求${outcome}不得复活`, async t => {
    const h = harness(t);
    await h.ready;
    const running = h.call('openTriPartyMeeting("old")');
    const req = h.request(0, 'meeting?meeting_id=old');
    h.call('closeModal()');
    if (outcome === '成功') await h.finish(req, detail('old', '关闭后旧详情'), running);
    else { req.network.reject(new Error('关闭后旧请求报错')); await running; }
    assert.equal(h.element('modal').style.display, 'none');
    assert.doesNotMatch(h.visible('modal-body'), /关闭后旧详情|关闭后旧请求报错|会议加载失败/);
  });
}

test('会议详情：较新 403 清屏后，旧成功不得恢复私密详情', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('openTriPartyMeeting("old")');
  const first = h.request(0, 'meeting?meeting_id=old');
  h.respondFetch(first);
  const newer = h.call('openTriPartyMeeting("new")');
  const second = h.request(1, 'meeting?meeting_id=new');
  h.respondFetch(second, 403);
  second.body.resolve({ ok: false, err: 'forbidden' });
  await newer;
  assert.match(h.visible('modal-body'), /需要访问凭据/);
  await h.finish(first, detail('old', '清屏前旧详情'), old);
  assert.match(h.visible('modal-body'), /需要访问凭据/);
  assert.doesNotMatch(h.visible('modal-body'), /清屏前旧详情/);
});

test('会议详情：总览 403 清屏后，旧成功不得恢复私密详情', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('openTriPartyMeeting("old")');
  const req = h.request(0, 'meeting?meeting_id=old');
  h.respondFetch(req);
  await h.clearBy403();
  assert.match(h.visible('modal-body'), /需要访问凭据/);
  await h.finish(req, detail('old', '清屏前旧详情'), old);
  assert.match(h.visible('modal-body'), /需要访问凭据/);
  assert.doesNotMatch(h.visible('modal-body'), /清屏前旧详情/);
  assert.equal(h.promptCount, 0);
});

test('会议详情：较早请求的过期 403 不得弹 token 或覆盖新详情', async t => {
  const h = harness(t);
  await h.ready;
  const old = h.call('openTriPartyMeeting("old")');
  const first = h.request(0, 'meeting?meeting_id=old');
  const newer = h.call('openTriPartyMeeting("new")');
  await h.finish(h.request(1, 'meeting?meeting_id=new'), detail('new', '新详情'), newer);
  await h.finish(first, { ok: false, err: 'forbidden' }, old, 403);
  assert.equal(h.promptCount, 0, '过期 403 不应要求输入 token');
  assert.equal(h.requests.length, 2, '过期 403 不应触发重试');
  assert.match(h.visible('modal-body'), /新详情/);
  assert.doesNotMatch(h.visible('modal-body'), /需要访问凭据|forbidden/);
});
