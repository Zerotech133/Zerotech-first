'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const scripts = [...fs.readFileSync(path.join(__dirname, 'legion.html'), 'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
assert.equal(scripts.length, 2);

function harness() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      style: {}, html: '', text: '', listeners: {},
      get innerHTML() { return this.html; },
      set innerHTML(value) { this.html = String(value); this.text = ''; this.listeners = {}; },
      get textContent() { return this.text; },
      set textContent(value) { this.text = String(value); this.html = ''; this.listeners = {}; },
      querySelectorAll(selector) {
        const key = selector.match(/^\[data-([\w-]+)\]$/)?.[1];
        if (!key) return [];
        return [...this.html.matchAll(new RegExp(`data-${key}="(\\d+)"`, 'g'))].map(match => {
          const child = { dataset: { [key.replace(/-([a-z])/g, (_, c) => c.toUpperCase())]: match[1] }, listeners: {},
            addEventListener(type, fn) { this.listeners[type] = fn; } };
          return child;
        });
      },
      querySelector(selector) {
        const id = selector.match(/^#([\w-]+)$/)?.[1];
        if (!id || !new RegExp(`<button\\b[^>]*\\bid="${id}"`).test(this.html)) return null;
        return { addEventListener(type, fn) { this[type] = fn; } };
      },
    });
    return elements.get(id);
  };
  const requests = [];
  let reply;
  const context = vm.createContext({
    window: {}, document: { getElementById: element, querySelectorAll: () => [] },
    sessionStorage: { getItem: () => 'test-token' },
    setInterval() {}, prompt() { throw new Error('形状测试不应弹出凭据或签准输入'); },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      assert.notEqual(options.method, 'POST', '只读形状测试不得发送 POST');
      if (url.endsWith('/ops')) return { ok: true, status: 200, json: async () => ({}) };
      assert.ok(url.includes('/tri-party/meetings') || url.includes('/tri-party/meeting?') || url.includes('/academy/topics'), `未预期请求：${url}`);
      return { ok: true, status: 200, json: async () => reply };
    },
  });
  vm.runInContext(scripts[0].replace(/apiReady\.then\(\(\)=>\{ tick\(\); setInterval\(tick, 30000\); \}\);/, ''), context);
  vm.runInContext(scripts[1], context);
  return {
    element, requests,
    respond(body) { reply = body; },
    call(expression) { return vm.runInContext(expression, context); },
    async ready() { await vm.runInContext('apiReady', context); },
    noPost() { assert.ok(requests.every(({ options }) => options.method !== 'POST')); },
  };
}

const shapeNotice = /不可达|失败|错误|异常|无效|缺失|不完整|不可用|格式/;
const meeting = { meeting_id: 'M1', subject: '会议私密正文', status: 'open', confirmation: { status: 'pending' }, disagreement_count: 0 };
const detail = { ...meeting, views: {}, disagreements: [], material_fingerprint: 'a'.repeat(64) };
const topic = { topic_id: 'T1', title: '课题私密正文', status: 'open', owner: '成员' };

for (const [label, response] of [
  ['缺 meetings', {}], ['null meetings', { meetings: null }],
  ['非数组 meetings', { meetings: {} }], ['坏元素 null', { meetings: [meeting, null] }],
  ['坏元素缺 ID', { meetings: [meeting, {}] }], ['坏元素错型 ID', { meetings: [meeting, { ...meeting, meeting_id: 7 }] }],
]) {
  test(`三方会议列表：${label} 不能伪装空集或渲染行`, async () => {
    const h = harness(); await h.ready(); h.respond(response);
    await h.call('loadTriPartyMeetings()');
    const box = h.element('tri-party-meetings');
    assert.match(box.textContent + box.innerHTML, shapeNotice);
    assert.doesNotMatch(box.textContent + box.innerHTML, /暂无三方会议|data-meeting-index|会议私密正文/);
    h.noPost();
  });
}

test('三方会议列表：合法空集与合法一行分别呈现', async () => {
  const h = harness(); await h.ready(); h.respond({ meetings: [] });
  await h.call('loadTriPartyMeetings()');
  assert.match(h.element('tri-party-meetings').innerHTML, /暂无三方会议/);
  h.respond({ meetings: [meeting] });
  await h.call('loadTriPartyMeetings()');
  assert.match(h.element('tri-party-meetings').innerHTML, /会议私密正文/);
  assert.equal(h.element('tri-party-meetings').querySelectorAll('[data-meeting-index]').length, 1);
  h.noPost();
});

for (const [label, changed] of [
  ['缺 views', { views: undefined }], ['null confirmation', { confirmation: null }],
  ['错误 meeting_id', { meeting_id: 'M2' }],   ['错型 disagreements', { disagreements: {} }],
  ['错型 evidence', { views: { A0: { evidence: '不可信' } } }],
  ['错型 risks', { views: { AI1: { risks: {} } } }],
]) {
  test(`会议详情：${label} 不展示正文或确认按钮`, async () => {
    const h = harness(); await h.ready(); h.respond({ ok: true, meeting: { ...detail, ...changed } });
    await h.call('openTriPartyMeeting("M1")');
    const box = h.element('modal-body');
    assert.match(box.textContent + box.innerHTML, shapeNotice);
    assert.doesNotMatch(box.textContent + box.innerHTML, /会议私密正文|用户确认|退回补证/);
    assert.equal(box.querySelector('#tri-party-confirm'), null);
    assert.equal(box.querySelector('#tri-party-reject'), null);
    assert.doesNotMatch(box.innerHTML, /<button\b/i);
    assert.ok(h.requests.some(({ url }) => url.includes('meeting?meeting_id=M1')));
    h.noPost();
  });
}

test('会议详情：缺材料指纹不得展示内容或确认入口', async () => {
  const h = harness(); await h.ready(); h.respond({ ok: true, meeting: { ...detail, material_fingerprint: undefined } });
  await h.call('openTriPartyMeeting("M1")');
  const box = h.element('modal-body');
  assert.match(box.textContent, /材料版本不可核验/);
  assert.doesNotMatch(box.textContent + box.innerHTML, /会议私密正文|<button/);
  h.noPost();
});

test('会议详情：合法简约结构仍有两个确认操作入口，但不自动 POST', async () => {
  const h = harness(); await h.ready(); h.respond({ ok: true, meeting: detail });
  await h.call('openTriPartyMeeting("M1")');
  const box = h.element('modal-body');
  assert.match(box.innerHTML, /会议私密正文/);
  assert.ok(box.querySelector('#tri-party-confirm'));
  assert.ok(box.querySelector('#tri-party-reject'));
  h.noPost();
});

const evil = '<img src=x onerror=globalThis.injected=1>';
test('会议详情：恶意 err 仅作为文本呈现，不生成标签或按钮', async () => {
  const h = harness(); await h.ready(); h.respond({ ok: false, err: evil });
  await h.call('openTriPartyMeeting("M1")');
  const box = h.element('modal-body');
  assert.ok(box.textContent.includes(evil), '保留错误原文作为文本');
  assert.equal(box.innerHTML, '', '错误不得写入 innerHTML');
  assert.equal(box.querySelector('#tri-party-confirm'), null);
  h.noPost();
});

for (const [label, response] of [
  ['缺 topics', {}], ['null topics', { topics: null }],
  ['非数组 topics', { topics: {} }], ['坏元素 null', { topics: [topic, null] }], ['坏元素缺 ID', { topics: [topic, {}] }],
  ['坏元素错型 ID', { topics: [topic, { ...topic, topic_id: 7 }] }],
]) {
  test(`公开课题列表：${label} 不能伪装空集或渲染行`, async () => {
    const h = harness(); await h.ready(); h.respond(response);
    await h.call('loadAcademyTopics()');
    const box = h.element('academy-topics');
    assert.match(box.textContent + box.innerHTML, shapeNotice);
    assert.doesNotMatch(box.textContent + box.innerHTML, /暂无课题|<div class="row"|课题私密正文/);
    h.noPost();
  });
}

test('公开课题列表：合法空集与合法一行分别呈现', async () => {
  const h = harness(); await h.ready(); h.respond({ meta: { complete: true, reason: 'empty' }, topics: [] });
  await h.call('loadAcademyTopics()');
  assert.match(h.element('academy-topics').innerHTML, /暂无课题/);
  h.respond({ meta: { complete: true, reason: 'nonempty' }, topics: [topic] });
  await h.call('loadAcademyTopics()');
  assert.match(h.element('academy-topics').innerHTML, /课题私密正文/);
  h.noPost();
});
