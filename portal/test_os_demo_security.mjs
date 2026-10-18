import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import vm from 'node:vm';

const html = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'os_demo.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, '页面内联脚本应存在');

function render(os, paperStatus = {}) {
  const elements = new Map();
  const chartOptions = new Map();
  const context = {
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, { innerHTML: '', textContent: '' });
        return elements.get(id);
      },
    },
    echarts: {
      init(element) {
        return { setOption(options) { chartOptions.set(element, options); }, resize() {} };
      },
      graphic: { LinearGradient: class {} },
    },
    fetch: async url => ({ json: async () => url === '/api/os' ? os : paperStatus }),
    addEventListener() {},
    setInterval() {},
  };
  vm.runInNewContext(script, context, { filename: 'os_demo.html' });
  return { elements, chartOptions };
}

const settle = () => new Promise(resolve => setImmediate(resolve));

test('商务、学习及账户等动态字段安全渲染，页面加载不中断', async () => {
  const payload = `<img src=x onerror="alert('x')">&`;
  const os = {
    accounts: { markets: { cn: { opened: true, name: payload, total: 100, accounts: [
      { id: payload, total: 100, initial: 100, pos_pct: 0, ret_pct: 0 },
    ] } } },
    agents: { chief: payload, daily_report: payload, a0_review: payload, last_commit: payload },
    coding: { last_commit: payload },
    biz: { near: [{ where: payload, date: payload, topic: payload }], remind_next: payload,
      notes: [{ title: payload, type: payload, updated: payload }],
      learn: { ready: true, academy: payload, stage: payload, wins: [payload] } },
  };
  const { elements, chartOptions } = render(os);
  await settle();
  const escaped = '&lt;img src=x onerror=&quot;alert(&#39;x&#39;)&quot;&gt;&amp;';
  for (const id of ['acct-rows', 'ag-rows', 'biz-rows', 'learn-rows', 'ticker-move']) {
    const content = elements.get(id).innerHTML;
    assert.ok(content.includes(escaped), `${id} 应编码接口内容`);
    assert.ok(!content.includes(payload), `${id} 不应拼入原始接口内容`);
  }
  assert.equal(elements.get('k-commit-s').textContent, `最近 ${payload}`);
  assert.equal(chartOptions.size, 5, '图表应正常完成渲染');
  const dist = chartOptions.get(elements.get('ch-dist'));
  assert.ok(dist.tooltip.formatter({ name: payload, value: 100, percent: 50 }).includes(escaped));
  assert.ok(!dist.tooltip.formatter({ name: payload, value: 100, percent: 50 }).includes(payload));
});

test('空数据正常加载并渲染占位内容', async () => {
  const { elements, chartOptions } = render({});
  await settle();
  assert.equal(elements.get('k-collect').textContent, '0');
  assert.ok(elements.get('acct-rows').innerHTML.includes('暂无账户'));
  assert.ok(elements.get('learn-rows').innerHTML.includes('学习域建设中'));
  assert.equal(chartOptions.size, 5);
});
