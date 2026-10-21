'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const scripts = [...fs.readFileSync(path.join(__dirname, 'legion.html'), 'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
assert.equal(scripts.length, 2);
const topic = title => ({ topic_id: `ID-${title}`, title, status: 'open', owner: '成员' });

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function harness() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      html: id === 'academy-topics' ? '旧课题' : '', text: '', button: null,
      get innerHTML() { return this.html; },
      set innerHTML(value) {
        this.html = String(value); this.text = '';
        this.button = this.html.includes('<button') ? { addEventListener(type, fn) { this[type] = fn; } } : null;
      },
      get textContent() { return this.text; },
      set textContent(value) { this.text = String(value); this.html = ''; this.button = null; },
      querySelector(selector) { return selector === 'button' ? this.button : null; },
      querySelectorAll() { return []; },
    });
    return elements.get(id);
  };
  const tokens = new Map();
  const requests = [];
  let promptCount = 0;
  let answer = '';
  let startupProbe = true;
  const context = vm.createContext({
    window: {}, document: { getElementById: element },
    sessionStorage: { getItem: key => tokens.get(key) || null, setItem: (key, value) => tokens.set(key, value) },
    setInterval() {}, prompt() { promptCount++; return answer; },
    fetch(url, options = {}) {
      assert.ok(!options.method || options.method === 'GET', '只读课题读取不得发送 POST');
      if (startupProbe) {
        startupProbe = false;
        assert.ok(url.endsWith('/ops'), `启动探针应请求 /ops：${url}`);
        return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
      }
      assert.ok(/\/academy\/topics$|\/reviews\/today$|\/ops$/.test(url), `未预期请求：${url}`);
      const req = { url, options, network: deferred(), body: deferred() };
      requests.push(req);
      return req.network.promise;
    },
  });
  vm.runInContext(scripts[0].replace(/apiReady\.then\(\(\)=>\{ tick\(\); setInterval\(tick, 30000\); \}\);/, ''), context);
  vm.runInContext(scripts[1], context);
  const call = expression => vm.runInContext(expression, context);
  return {
    box: element('academy-topics'), element, requests, tokens, ready: call('apiReady'),
    get promptCount() { return promptCount; },
    load: () => call('loadAcademyTopics()'),
    reviews: () => call('loadReviews()'),
    setAnswer(token) { answer = token; },
    reenter(token) {
      answer = token;
      assert.equal(call('promptLegionToken()'), true, '模拟用户主动填写 token');
      assert.equal(tokens.get('legionToken'), token);
    },
    async finish(req, value, running, status = 200) {
      req.network.resolve({ ok: status === 200, status, json: () => req.body.promise });
      req.body.resolve(value);
      await running;
    },
  };
}

for (const [label, response, message, row] of [
  ['完整空集', { meta: { complete: true }, topics: [] }, /暂无课题/, false],
  ['完整非空', { meta: { complete: true }, topics: [topic('可信课题')] }, /可信课题/, true],
  ['不完整空集', { meta: { complete: false, reason: 'source_missing' }, topics: [] }, /课题台账不完整.*源不可核实/, false],
  ['不完整非空', { meta: { complete: false, reason: 'source_missing' }, topics: [topic('不可核实课题')] }, /课题台账不完整.*源不可核实/, false],
  ['缺 meta 空集', { topics: [] }, /课题台账完整性未核实/, false],
  ['缺 meta 非空', { topics: [topic('旧版课题')] }, /课题台账完整性未核实/, true],
  ['无 complete 非空', { meta: { reason: 'unknown' }, topics: [topic('待核课题')] }, /课题台账完整性未核实/, true],
]) {
  test(`课题台账健康：${label}`, async () => {
    const h = harness();
    await h.ready;
    const running = h.load();
    await h.finish(h.requests[0], response, running);
    const visible = h.box.innerHTML + h.box.textContent;
    assert.match(visible, message);
    assert.equal(h.box.innerHTML.includes('<div class="row">'), row);
    if (response.meta?.complete !== true) assert.doesNotMatch(visible, /暂无课题/);
    if (response.meta?.complete === false) assert.doesNotMatch(visible, /旧课题|不可核实课题/);
    if (response.meta?.complete === true) assert.doesNotMatch(visible, /未核实|不完整/);
  });
}

test('课题台账健康：非法 topics 即使标完整也不能渲染行或空结论', async () => {
  const h = harness(); await h.ready;
  const running = h.load();
  await h.finish(h.requests[0], { meta: { complete: true }, topics: [topic('合法行'), null] }, running);
  assert.match(h.box.textContent, /数据无效/);
  assert.equal(h.box.innerHTML, '');
  assert.doesNotMatch(h.box.textContent, /暂无课题|合法行/);
});

test('课题台账健康：较新不完整覆盖旧行，较早完整响应不得回填', async () => {
  const h = harness(); await h.ready;
  const old = h.load();
  h.requests[0].network.resolve({ ok: true, status: 200, json: () => h.requests[0].body.promise });
  const newer = h.load();
  await h.finish(h.requests[1], { meta: { complete: false, reason: 'source_missing' }, topics: [topic('未核实新行')] }, newer);
  await h.finish(h.requests[0], { meta: { complete: true }, topics: [topic('过期旧行')] }, old);
  assert.match(h.box.textContent, /课题台账不完整.*源不可核实/);
  assert.equal(h.box.innerHTML, '');
  assert.doesNotMatch(h.box.textContent, /过期旧行|未核实新行|暂无课题/);
});

test('课题台账健康：较早不完整响应不得覆盖较新完整空集', async () => {
  const h = harness(); await h.ready;
  const old = h.load();
  const newer = h.load();
  await h.finish(h.requests[1], { meta: { complete: true }, topics: [] }, newer);
  await h.finish(h.requests[0], { meta: { complete: false }, topics: [] }, old);
  assert.match(h.box.innerHTML, /暂无课题/);
  assert.doesNotMatch(h.box.innerHTML + h.box.textContent, /不完整|未核实/);
});

test('课题台账健康：较早请求报错不得覆盖较新未核实状态', async () => {
  const h = harness(); await h.ready;
  const old = h.load();
  const newer = h.load();
  await h.finish(h.requests[1], { topics: [] }, newer);
  h.requests[0].network.reject(new Error('旧请求失败'));
  await old;
  assert.match(h.box.innerHTML, /完整性未核实/);
  assert.doesNotMatch(h.box.innerHTML + h.box.textContent, /不可达|暂无课题/);
});

test('课题台账凭据：当前 403 清除历史课题，仅显示主动输入 token 重试入口', async () => {
  const h = harness(); await h.ready;
  const previous = h.load();
  await h.finish(h.requests[0], { meta: { complete: true }, topics: [topic('历史私密课题')] }, previous);
  assert.match(h.box.innerHTML, /历史私密课题/);

  const current = h.load();
  assert.equal(h.requests[1].options.headers['X-Portal-Token'], undefined);
  await h.finish(h.requests[1], { ok: false, err: 'forbidden' }, current, 403);
  assert.match(h.box.innerHTML, /课题台账需要访问凭据.*输入 token 后重试/);
  assert.doesNotMatch(h.box.innerHTML + h.box.textContent, /历史私密课题|暂无课题|不可达/);
  assert.ok(h.box.button?.click, '凭据提示须绑定主动重试按钮');
  assert.equal(h.promptCount, 0, '403 不自动 prompt');
  assert.equal(h.requests.length, 2, '403 不自动重试');

  h.setAnswer('new-token');
  h.box.button.click();
  assert.equal(h.tokens.get('legionToken'), 'new-token');
  const retry = h.requests.find(r => r.url.endsWith('/academy/topics') && r.options.headers['X-Portal-Token'] === 'new-token');
  assert.ok(retry, '主动点击才携带新 token 重试课题 GET');
  assert.ok(h.requests.some(r => r.url.endsWith('/ops') && r.options.headers['X-Portal-Token'] === 'new-token'), '主动点击才重试总览 GET');
  assert.equal(h.promptCount, 1, '仅点击按钮才请求输入 token');
  assert.ok(h.requests.every(r => !r.options.method || r.options.method === 'GET'), '不得发送 POST');
});

test('课题台账凭据：旧 200 晚于另一路私人 GET 当前 403 不得回填课题', async () => {
  const h = harness(); await h.ready;
  const old = h.load();
  const oldReq = h.requests[0];
  oldReq.network.resolve({ ok: true, status: 200, json: () => oldReq.body.promise });
  const reviews = h.reviews();
  assert.ok(h.requests[1].url.endsWith('/reviews/today'));
  await h.finish(h.requests[1], { ok: false, err: 'forbidden' }, reviews, 403);
  assert.match(h.box.textContent, /需要访问凭据/);
  await h.finish(oldReq, { meta: { complete: true }, topics: [topic('晚到私密课题')] }, old);
  assert.match(h.box.textContent, /需要访问凭据/);
  assert.equal(h.box.innerHTML, '');
  assert.doesNotMatch(h.box.innerHTML + h.box.textContent, /晚到私密课题|暂无课题|不可达/);
  assert.equal(h.promptCount, 0);
  assert.equal(h.requests.length, 2, '403 不自动重试');
});

test('课题台账凭据：旧 403 晚于新 token 成功不得清屏', async () => {
  const h = harness(); await h.ready;
  h.tokens.set('legionToken', 'old-token');
  const old = h.load();
  assert.equal(h.requests[0].options.headers['X-Portal-Token'], 'old-token');
  h.reenter('new-token');
  const newer = h.load();
  assert.equal(h.requests[1].options.headers['X-Portal-Token'], 'new-token');
  await h.finish(h.requests[1], { meta: { complete: true }, topics: [topic('新凭据课题')] }, newer);
  assert.match(h.box.innerHTML, /新凭据课题/);
  await h.finish(h.requests[0], { ok: false, err: 'forbidden' }, old, 403);
  assert.match(h.box.innerHTML, /新凭据课题/);
  assert.doesNotMatch(h.box.innerHTML + h.box.textContent, /需要访问凭据|不可达|旧课题/);
  assert.equal(h.promptCount, 1, '只允许主动重填 token 的 prompt');
  assert.equal(h.requests.length, 2, '旧 403 不应自动重试');
});
