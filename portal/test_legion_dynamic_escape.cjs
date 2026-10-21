'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, 'legion.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
assert.equal(scripts.length, 2);
const treeId = `TREE');globalThis.injected=1;//" onmouseover="globalThis.injected=1`;
const memberId = `MEMBER');globalThis.injected=1;//" onmouseover="globalThis.injected=1`;
const taskId = `TASK');globalThis.injected=1;//" onmouseover="globalThis.injected=1`;
const image = '<img src=x onerror=globalThis.injected=1>';
const breakout = `正常经验" onfocus="globalThis.injected=1`;
const ops = {
  roster: {
    n: 2, n_active: 1,
    members: [{ id: memberId, name: `成员姓名${image}`, state: `active" onmouseover="globalThis.injected=1`, dept: `业务部门${image}` }],
    tree: { id: treeId, name: `总指挥${image}`, children: [{ dept: `业务部门${image}`, name: `部门名称${image}`, lead: `负责人${image}`, children: [
      { id: memberId, name: `成员姓名${image}`, state: `active" onmouseover="globalThis.injected=1` },
    ] }] },
  },
  tasks: [{ id: taskId, desc: `工单描述${image}`, status: 'review' }],
  feed: [{ ts: '2026-10-19T10:20:30', caller: `活动调用者${image}`, action: `活动动作${image}`, detail: `动态详情${image}` }],
  academy: { operating_plan: { stages: [`业务阶段${image}`] }, lead: `研究负责人${image}`, kpi: [`指标${image}`], directions: [{ name: `方向${image}` }] },
};
const agent = id => ({
  name: `成员详情${image}`, agent_id: id, dept: `部门详情${image}`, title: `职位${image}`, rank: `级别${image}`,
  onboarded: `入职日期${image}`, acts_14d: 2, state: 'active',
  editable: { experience: breakout, skills: `技能" autofocus onfocus="globalThis.injected=1`, self_statement: `自述" onmouseover="globalThis.injected=1` },
  recent_acts: [{ ts: '2026-10-19T11:20:30', action: `最近动作${image}`, detail: `最近详情${image}` }],
  tasks: [{ tid: taskId, status: `review${image}`, desc: `名下任务${image}` }],
});
const review = { ok: true, tid: taskId, desc: `复盘描述${image}`, status: `review${image}`,
  owner: `工单负责人${image}`, created: `创建时间${image}`, updated: `更新时间${image}`, branch: `分支${image}`,
  findings: [`执行判读${image}`] };
const today = { ok: true, reviews: { [memberId]: { agent_id: memberId, has_task: true,
  q1: `第一问${image}`, q2: `第二问${image}`, q3: `第三问${image}` } } };
const elements = new Map();
const decode = value => value.replace(/&(?:#(x[\da-f]+|\d+)|amp|lt|gt|quot|apos);/gi, (full, num) => {
  if (num) return String.fromCodePoint(num[0].toLowerCase() === 'x' ? parseInt(num.slice(1), 16) : Number(num));
  return { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'" }[full.toLowerCase()] || full;
});
let context;
const makeNode = (dataset = {}) => ({
  dataset, style: {}, value: '', html: '', text: '', listeners: {}, children: [],
  get innerHTML() { return this.html; },
  set innerHTML(value) { this.html = String(value); this.text = ''; this.listeners = {}; this.children = []; },
  get textContent() { return this.text; },
  set textContent(value) { this.text = String(value); this.html = ''; this.listeners = {}; this.children = []; },
  addEventListener(type, fn) { this.listeners[type] = fn; },
  click() { return this.listeners.click?.(); },
  querySelector(selector) { return selector === 'button' && this.html.includes('<button') ? this.button || (this.button = makeNode()) : null; },
  querySelectorAll(selector) {
    const data = selector.match(/\[data-([\w-]+)\]/);
    if (!data) return [];
    const key = data[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    if (!this.children.length) this.children = [...this.html.matchAll(new RegExp(`data-${data[1]}=["'](\\d+)["']`, 'g'))]
      .map(match => makeNode({ [key]: match[1] }));
    return this.children;
  },
});
const element = id => {
  if (!elements.has(id)) elements.set(id, makeNode());
  return elements.get(id);
};
const requests = [];
const tokens = new Map([['legionToken', 'valid-token']]);
let forbidden = false;
let promptCount = 0;
const response = (data, status = 200) => ({ ok: status === 200, status, json: async () => data });
context = vm.createContext({
  window: {}, document: { getElementById: element, querySelectorAll: () => [] },
  sessionStorage: { getItem: key => tokens.get(key) || null, setItem: (key, value) => tokens.set(key, value) },
  prompt: () => { promptCount++; return ''; },
  setInterval: () => {},
  fetch: async (url, options = {}) => {
    requests.push({ url, options });
    if (forbidden || options.headers?.['X-Portal-Token'] !== 'valid-token') return response({ ok: false, err: 'forbidden' }, 403);
    if (url.includes('/reviews/today')) return response(today);
    if (url.includes('/review?')) return response(review);
    if (url.includes('/agent?')) return response(agent(new URL(url, 'http://localhost').searchParams.get('id')));
    if (url.includes('/ops')) return response(ops);
    return response({ ok: true, rooms: [], meetings: [], topics: [] });
  },
});
vm.runInContext(scripts[0].replace(/apiReady\.then\(\(\)=>\{ tick\(\); setInterval\(tick, 30000\); \}\);/, ''), context);
vm.runInContext(scripts[1], context);
const call = expression => vm.runInContext(expression, context);
const flush = () => new Promise(resolve => setImmediate(resolve));
const clean = (id, visible) => {
  const markup = element(id).innerHTML;
  assert.match(markup, new RegExp(visible), `${id} 保留业务文字`);
  assert.doesNotMatch(markup, /<\s*(?:img|script|svg|iframe)\b/i, `${id} 不得生成可执行标签`);
  for (const tag of markup.matchAll(/<[^>]*>/g)) {
    const attributesOnly = tag[0].replace(/(["'])(?:[\s\S]*?)\1/g, '""');
    assert.doesNotMatch(attributesOnly, /\s(?:onerror|onmouseover|onfocus|autofocus)\s*=|javascript\s*:/i, `${id} 不得生成注入属性`);
    // 可信的内联点击可保留，但响应内容不能变成事件处理器代码。
    for (const match of tag[0].matchAll(/\bonclick\s*=\s*(["'])([\s\S]*?)\1/gi)) {
      assert.doesNotMatch(decode(match[2]), /globalThis\.injected|<img/i, `${id} 的点击处理器不得执行响应内容`);
    }
  }
};
const clickEntry = (id, index, key) => {
  const box = element(id);
  const rows = box.querySelectorAll(`[data-${key}]`);
  if (rows.length) {
    assert.ok(rows[index], `${id} 的第 ${index + 1} 个可点击入口存在`);
    assert.equal(typeof rows[index].listeners.click, 'function', `${id} 的点击事件已绑定`);
    return rows[index].click();
  }
  const attrs = [...box.innerHTML.matchAll(/\bonclick\s*=\s*(["'])([\s\S]*?)\1/gi)];
  assert.ok(attrs[index], `${id} 的第 ${index + 1} 个可点击入口存在`);
  return call(decode(attrs[index][2]));
};
const assertGet = (route, param, value) => {
  assert.ok(requests.some(r => {
    const url = new URL(r.url, 'http://localhost');
    return url.pathname.endsWith(route) && url.searchParams.get(param) === value &&
      r.options.headers?.['X-Portal-Token'] === 'valid-token' && (!r.options.method || r.options.method === 'GET');
  }), `${route} 点击时带 token 且参数保持原始 ID`);
};

(async () => {
  await call('apiReady');
  await call('tick()');
  await flush();
  for (const [id, text] of [['wall', '成员姓名'], ['feed', '动态详情'], ['lane-review', '工单描述'], ['review-drawer', '第一问']]) clean(id, text);
  assert.match(element('wall').innerHTML, /总指挥|成员姓名/);
  assert.match(element('review-drawer').innerHTML, /第二问/);
  assert.equal(element('s-total').textContent, '2');
  assert.ok(requests.some(r => r.url.endsWith('/ops') && r.options.headers?.['X-Portal-Token'] === 'valid-token'));
  // 两个不同来源的 ID 都必须原样传给下钻函数，而非从 HTML 中拼出第二条语句。
  await clickEntry('wall', 0, 'drill-index');
  assertGet('/agent', 'id', treeId);
  await clickEntry('wall', 1, 'drill-index');
  assertGet('/agent', 'id', memberId);
  await flush();
  clean('modal-body', '成员详情');
  for (const text of ['最近动作', '名下任务', '第一问', '第二问', '第三问', '正常经验', '技能', '自述']) {
    assert.match(element('modal-body').innerHTML, new RegExp(text), `详情展示 ${text}`);
  }
  const modal = element('modal-body').innerHTML;
  for (const id of ['ed-exp', 'ed-skills', 'ed-stmt']) {
    const input = modal.match(new RegExp(`<input\\b[^>]*\\bid="${id}"[^>]*>`));
    assert.ok(input, `${id} 输入框存在`);
    assert.doesNotMatch(input[0].replace(/(["'])(?:[\s\S]*?)\1/g, '""'), /\s(?:onfocus|onmouseover|autofocus)\s*=/i, `${id} 属性不逃逸`);
  }
  // 保存和派出仅检查入口传参，不发送 POST；攻击 ID 不能截断为新参数或新语句。
  vm.runInContext('globalThis.clickedAgents = []; saveAttrs = id => clickedAgents.push(id); dispatchTo = id => clickedAgents.push(id)', context);
  for (const id of ['save-attrs', 'dispatch-agent']) {
    const button = element(id);
    if (button.listeners.click) button.click();
    else {
      const action = id === 'save-attrs' ? 'saveAttrs' : 'dispatchTo';
      const handler = modal.match(new RegExp(`${action}\\([^"<>]*\\)`));
      assert.ok(handler, `${id} 的点击入口存在`);
      call(handler[0]);
    }
  }
  assert.deepEqual(Array.from(context.clickedAgents), [memberId, memberId]);
  await clickEntry('modal-body', 0, 'task-index');
  assertGet('/review', 'tid', taskId);
  clean('modal-body', '执行判读');
  assert.match(element('modal-body').innerHTML, /复盘描述/);
  await clickEntry('lane-review', 0, 'task-index');
  assertGet('/review', 'tid', taskId);
  assert.equal(context.injected, undefined, '点击不能执行 GET 注入脚本');
  assert.equal(promptCount, 0);

  // 无组织树时仍要渲染部门及状态；状态只允许成为预期的样式类。
  const originalTree = ops.roster.tree;
  delete ops.roster.tree;
  await call('tick()');
  await flush();
  clean('wall', '业务部门');
  assert.match(element('wall').innerHTML, /成员姓名/);
  assert.doesNotMatch(element('wall').innerHTML, /class="dot [^"]*(?:onmouseover|globalThis)/);
  await clickEntry('wall', 0, 'drill-index');
  assertGet('/agent', 'id', memberId);
  ops.roster.tree = originalTree;

  await call(`drill(${JSON.stringify(memberId)})`);
  assert.match(element('modal-body').innerHTML, /第三问/);
  forbidden = true;
  await call('tick()');
  await flush();
  for (const id of ['wall', 'feed', 'lane-review', 'review-drawer', 'modal-body']) {
    assert.doesNotMatch(element(id).innerHTML + element(id).textContent, /成员姓名|动态详情|工单描述|第一问|第三问|最近动作/,
      `${id} 的历史私人内容必须遮蔽`);
  }
  assert.match(element('feed').innerHTML + element('feed').textContent, /需要访问凭据/);
  assert.equal(Object.keys(context.window._reviews).length, 0);
  assert.equal(promptCount, 0, '403 不自动弹窗');
  assert.ok(requests.filter(r => /\/api\/legion\/(?:ops|agent|review|reviews\/today)/.test(r.url))
    .every(r => r.options.headers?.['X-Portal-Token'] === 'valid-token'), '私人 GET 全部带 token');
  console.log('PASS 军团动态私人 GET：转义、点击参数、业务展示、token 和 403 遮蔽');
})().catch(error => { console.error(error); process.exitCode = 1; });
