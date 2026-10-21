'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, 'legion.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
assert.equal(scripts.length, 2);
const elements = new Map();
const element = id => {
  if (!elements.has(id)) {
    const node = { style: {}, button: null,
      get textContent() { return this.text || ''; },
      set textContent(value) { this.text = value; this.html = ''; this.button = null; },
      get innerHTML() { return this.html || ''; },
      set innerHTML(value) {
        this.html = value;
        this.text = '';
        this.button = value.includes('<button') ? { addEventListener(type, fn) { this[type] = fn; } } : null;
      },
      querySelector(selector) { return selector === 'button' ? this.button : null; },
      querySelectorAll() { return []; },
    };
    elements.set(id, node);
  }
  return elements.get(id);
};
const tokens = new Map();
const requests = [];
let promptCount = 0;
let answer = '';
let authorized = false;
let protect = true;
const ops = {
  roster: { n: 1, n_active: 1, members: [{ id: 'A0', name: '成员', state: 'active', dept: 'team' }] },
  tasks: [{ id: 'T1', desc: '旧工单秘密', status: 'review' }],
  feed: [{ action: '旧活动秘密' }],
};
const agent = { name: '成员', agent_id: 'A0', dept: 'team', title: '成员', rank: '一', editable: {}, tasks: [], recent_acts: [] };
const review = { ok: true, tid: 'T1', desc: '工单私密正文', status: 'review', findings: ['私密判读'] };
const today = { ok: true, reviews: { A0: { agent_id: 'A0', q1: '复盘私密正文', q2: '详情' } } };
const response = (data, status = 200) => ({ ok: status === 200, status, json: async () => data });
const context = vm.createContext({
  window: {},
  document: { getElementById: element, querySelectorAll: () => [] },
  sessionStorage: { getItem: key => tokens.get(key) || null, setItem: (key, value) => tokens.set(key, value) },
  prompt: () => { promptCount++; return answer; },
  fetch: async (url, options = {}) => {
    requests.push({ url, options });
    if (protect && options.headers?.['X-Portal-Token'] !== 'valid-token') return response({ ok: false, err: 'forbidden' }, 403);
    if (url.includes('/reviews/today')) return response(today);
    if (url.includes('/review?')) return response(review);
    if (url.includes('/agent?')) return response(agent);
    if (url.includes('/ops')) return response(ops);
    return response({ ok: true, rooms: [], meetings: [], topics: [] });
  },
  setInterval: () => {},
});
vm.runInContext(scripts[0].replace(/apiReady\.then\(\(\)=>\{ tick\(\); setInterval\(tick, 30000\); \}\);/, ''), context);
vm.runInContext(scripts[1], context);
const call = expr => vm.runInContext(expr, context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const notice = id => {
  const box = element(id);
  assert.match(box.innerHTML, /需要访问凭据.*token.*不代表本人签准.*输入 token 后重试/);
  assert.ok(box.button);
  return box;
};

(async () => {
  // 403 是权限问题，不是同源后端断线；自动刷新不会主动弹出凭据输入框。
  await call('apiReady');
  assert.equal(call('API2'), '/legion-api');
  assert.equal(call('API'), '/legion-api/api/legion/ops');
  await call('tick()');
  assert.equal(promptCount, 0);
  assert.equal(element('s-total').textContent, '-');
  assert.doesNotMatch(element('wall').innerHTML + element('feed').innerHTML, /undefined/);
  assert.match(element('feed').innerHTML, /需要访问凭据/);
  assert.equal(requests.some(r => r.url.includes('127.0.0.1')), false);
  assert.equal(requests.every(r => !r.options.method || r.options.method === 'GET'), true);

  // 曾经显示过的数据，在后续 403 时清除；按钮必须由用户亲自点击。
  protect = false;
  await call('tick()');
  await flush();
  assert.match(element('lane-review').innerHTML, /旧工单秘密/);
  assert.match(element('review-drawer').innerHTML, /复盘私密正文/);
  await call('drill("A0")');
  assert.match(element('modal-body').innerHTML, /成员/);
  protect = true;
  await call('tick()');
  for (const id of ['wall', 'lane-review', 'review-drawer', 'modal-body']) {
    assert.doesNotMatch(element(id).innerHTML, /旧工单秘密|复盘私密正文|成员.*保存属性/);
    assert.match(element(id).textContent, /需要访问凭据/);
  }
  assert.equal(Object.keys(context.window._reviews).length, 0);
  assert.equal(promptCount, 0);

  // 每一种下钻在 403 时遮蔽原正文；有 token 也不会自动反复 prompt。
  await call('drill("A0")');
  assert.match(element('modal-body').innerHTML, /成员详情需要访问凭据/);
  await call('reviewTk("T1")');
  assert.match(element('modal-body').innerHTML, /工单复盘需要访问凭据/);
  await call('loadReviews()');
  assert.match(element('review-drawer').innerHTML, /今日复盘需要访问凭据/);
  assert.equal(promptCount, 0);
  assert.equal(requests.some(r => r.url.includes('127.0.0.1')), false);

  // 显式按钮动作输入 token 后才重试；路由不变，四类 GET 均携带凭据。
  answer = 'valid-token';
  const box = element('review-drawer');
  assert.ok(box.button, '页面提供重试入口');
  box.button.click();
  await flush();
  await flush();
  assert.equal(promptCount, 1);
  assert.equal(tokens.get('legionToken'), 'valid-token');
  assert.match(element('review-drawer').innerHTML, /复盘私密正文/);
  assert.match(element('lane-review').innerHTML, /旧工单秘密/);
  await call('drill("A0")');
  assert.match(element('modal-body').innerHTML, /成员/);
  await call('reviewTk("T1")');
  assert.match(element('modal-body').innerHTML, /工单私密正文|私密判读/);
  const four = ['/ops', '/agent?id=', '/review?tid=', '/reviews/today'];
  for (const route of four) {
    assert.ok(requests.some(r => r.url.includes(route) && r.options.headers?.['X-Portal-Token'] === 'valid-token'), route);
  }
  assert.equal(requests.some(r => r.url.includes('127.0.0.1')), false);
  assert.equal(requests.some(r => r.options.method === 'POST'), false);
  tokens.set('legionToken', 'expired-token');
  await call('tick()');
  await call('tick()');
  assert.equal(promptCount, 1, '过期 token 的定时刷新不重复弹窗');
  assert.doesNotMatch(element('lane-review').innerHTML + element('modal-body').innerHTML, /旧工单秘密|工单私密正文/);
  console.log('PASS 私人 GET 页面：403 不直连、手动 token、旧内容遮蔽与路由恢复');
})().catch(err => { console.error(err); process.exitCode = 1; });
