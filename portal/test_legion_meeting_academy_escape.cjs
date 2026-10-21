'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const scripts = [...fs.readFileSync(path.join(__dirname, 'legion.html'), 'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]);
assert.equal(scripts.length, 2);

// 按引号边界解析标签和属性；实体在解析完标签后才解码，模拟浏览器的 DOM 行为。
const decode = value => value.replace(/&(?:#(x[\da-f]+|\d+)|amp|lt|gt|quot|apos);/gi, (full, num) => {
  if (num) return String.fromCodePoint(num[0].toLowerCase() === 'x' ? parseInt(num.slice(1), 16) : Number(num));
  return { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&apos;': "'" }[full.toLowerCase()] || full;
});
const node = (tag = 'root', attributes = new Map()) => ({
  tag, attributes, children: [], listeners: {}, style: {},
  get dataset() {
    return Object.fromEntries([...this.attributes].filter(([key]) => key.startsWith('data-'))
      .map(([key, value]) => [key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()), value]));
  },
  get textContent() { return this.children.map(child => typeof child === 'string' ? child : child.textContent).join(''); },
  set textContent(value) { this.children = [String(value)]; this.listeners = {}; },
  get innerHTML() { return this.markup || ''; },
  set innerHTML(value) { this.markup = String(value); this.children = parse(this.markup); this.listeners = {}; },
  querySelectorAll(selector) {
    const found = [];
    const visit = current => {
      for (const child of current.children) {
        if (typeof child === 'string') continue;
        if (selector[0] === '#' ? child.attributes.get('id') === selector.slice(1)
          : selector[0] === '[' && child.attributes.has(selector.slice(1, -1))) found.push(child);
        visit(child);
      }
    };
    visit(this);
    return found;
  },
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
  addEventListener(type, listener) { this.listeners[type] = listener; },
  click() { return this.listeners.click?.(); },
});
function parse(markup) {
  const root = node();
  const stack = [root];
  const voidTags = new Set(['br', 'img', 'input', 'hr', 'meta', 'link']);
  let pos = 0;
  while (pos < markup.length) {
    if (markup[pos] !== '<') {
      const end = markup.indexOf('<', pos);
      stack.at(-1).children.push(decode(markup.slice(pos, end < 0 ? markup.length : end)));
      pos = end < 0 ? markup.length : end;
      continue;
    }
    let end = pos + 1;
    let quote = '';
    while (end < markup.length) {
      const char = markup[end];
      if (quote ? char === quote : char === '"' || char === "'") quote = quote ? '' : char;
      else if (char === '>') break;
      end++;
    }
    assert.ok(end < markup.length, 'HTML 标签必须闭合');
    const tag = markup.slice(pos + 1, end);
    pos = end + 1;
    const closing = tag.match(/^\/([a-z][\w:-]*)\s*$/i);
    if (closing) {
      assert.equal(stack.pop().tag, closing[1].toLowerCase(), 'HTML 标签必须正确嵌套');
      continue;
    }
    const opening = tag.match(/^([a-z][\w:-]*)([\s\S]*)$/i);
    assert.ok(opening, `无法解析 HTML 标签：${tag}`);
    const name = opening[1].toLowerCase();
    const attrs = new Map();
    let rest = opening[2].replace(/\/$/, '');
    const attribute = /^\s+([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/;
    while (rest.trim()) {
      const match = rest.match(attribute);
      assert.ok(match, `无法解析 ${name} 的属性：${rest}`);
      attrs.set(match[1].toLowerCase(), decode(match[2] ?? match[3] ?? match[4] ?? ''));
      rest = rest.slice(match[0].length);
    }
    const child = node(name, attrs);
    stack.at(-1).children.push(child);
    if (!voidTags.has(name) && !/\/\s*$/.test(tag)) stack.push(child);
  }
  assert.equal(stack.length, 1, 'HTML 标签必须正确闭合');
  return root.children;
}
const walk = (root, fn) => {
  for (const child of root.children) {
    if (typeof child === 'string') continue;
    fn(child);
    walk(child, fn);
  }
};
const safeDom = (root, label) => walk(root, child => {
  assert.ok(!['img', 'script', 'svg', 'iframe'].includes(child.tag), `${label} 不得生成注入标签`);
  for (const key of child.attributes.keys()) assert.ok(!/^on/i.test(key), `${label} 不得生成 ${key} 事件属性`);
});

const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, node());
  return elements.get(id);
};
const payload = '<img src=x onerror=globalThis.injected=1>';
const ids = [
  `m' );globalThis.injected=1;//${payload}`,
  `m" onmouseover="globalThis.injected=1${payload}`,
];
const meetings = ids.map(meeting_id => ({ meeting_id, subject: '恶意 ID 会议', status: 'open', confirmation: { status: 'pending' }, disagreement_count: 0 }));
const topics = [{ topic_id: `课题编号'" ${payload}`, title: `课题标题'" ${payload}`,
  status: `课题状态'" ${payload}`, owner: `课题负责人'" ${payload}` }];
const requests = [];
const response = data => ({ ok: true, status: 200, json: async () => data });
const context = vm.createContext({
  window: {}, document: { getElementById: element, querySelectorAll: () => [] },
  sessionStorage: { getItem: () => 'test-token' },
  setInterval: () => {}, prompt: () => { throw new Error('不应提示输入'); },
  fetch: async (url, options = {}) => {
    requests.push({ url, options });
    if (url.endsWith('/tri-party/meetings')) return response({ meetings });
    if (url.includes('/tri-party/meeting?')) {
      const meeting_id = new URL(url, 'http://localhost').searchParams.get('meeting_id');
      return response({ ok: true, meeting: { meeting_id, subject: '会议详情', status: 'open', confirmation: { status: 'pending' },
        views: {}, disagreements: [], material_fingerprint: 'a'.repeat(64) } });
    }
    if (url.endsWith('/academy/topics')) return response({ topics });
    if (url.endsWith('/ops')) return response({});
    throw new Error(`未预期请求：${url}`);
  },
});
vm.runInContext(scripts[0].replace(/apiReady\.then\(\(\)=>\{ tick\(\); setInterval\(tick, 30000\); \}\);/, ''), context);
vm.runInContext(scripts[1], context);
vm.runInContext('globalThis.confirmCalls = []; confirmTriParty = (id, status, fingerprint) => confirmCalls.push([id, status, fingerprint])', context);
const call = expression => vm.runInContext(expression, context);

(async () => {
  await call('apiReady');
  await call('loadTriPartyMeetings()');
  const list = element('tri-party-meetings');
  safeDom(list, '会议列表');
  const rows = list.querySelectorAll('[data-meeting-index]');
  assert.equal(rows.length, ids.length);
  for (const [index, id] of ids.entries()) {
    assert.equal(rows[index].textContent.includes(id), true, '列表应保留 ID 的原始文字');
    assert.equal(typeof rows[index].listeners.click, 'function', '列表使用真实事件监听器');
    await rows[index].click();
    const detailRequest = requests.at(-1);
    assert.equal(detailRequest.url, `/legion-api/api/legion/tri-party/meeting?meeting_id=${encodeURIComponent(id)}`);
    assert.equal(new URL(detailRequest.url, 'http://localhost').searchParams.get('meeting_id'), id);
    const detail = element('modal-body');
    safeDom(detail, '会议详情');
    for (const [buttonId, status] of [['tri-party-confirm', 'confirmed'], ['tri-party-reject', 'rejected']]) {
      const button = detail.querySelector(`#${buttonId}`);
      assert.ok(button, `${buttonId} 按钮存在`);
      assert.equal(typeof button.listeners.click, 'function', `${buttonId} 使用真实事件监听器`);
      button.click();
      assert.deepEqual(Array.from(context.confirmCalls.at(-1)), [id, status, 'a'.repeat(64)], '监听器传原始 ID、固定状态与材料指纹');
    }
  }
  assert.equal(context.confirmCalls.length, ids.length * 2);
  assert.equal(requests.some(request => request.options.method === 'POST'), false, '不会发出 POST');
  await call('loadAcademyTopics()');
  const academy = element('academy-topics');
  safeDom(academy, '课题台账');
  for (const field of ['topic_id', 'title', 'status', 'owner']) {
    assert.ok(academy.textContent.includes(topics[0][field]), `${field} 保留原始文本，而非生成 img/onerror`);
  }
  assert.equal(context.injected, undefined, '恶意代码未执行');
  console.log('PASS 三方会议恶意 ID 编码与按钮监听器、研究院四字段 DOM 转义');
})().catch(error => { console.error(error); process.exitCode = 1; });
