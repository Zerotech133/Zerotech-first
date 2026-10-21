/* WO-OS-07 契约测试：os_zhuge_adapter.js（前端规则版建议引擎，与 api_zhuge_brief.py 同语义）
 * 先红后绿：本文件先于 os_zhuge_adapter.js 落地时必须红。
 * 复刻 test_api_zhuge_brief.py 的 9 个场景 + UMD 双端契约。
 * 运行：node test_os_zhuge_adapter.cjs  */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const failures = [];
function check(name, cond) { if (!cond) failures.push(name); }

const adapterPath = path.join(__dirname, 'static', 'os_zhuge_adapter.js');
let src = '';
try { src = fs.readFileSync(adapterPath, 'utf8'); }
catch (e) { console.error('FAIL 无法读取 ' + adapterPath + '：' + e.message); process.exit(1); }

// 以纯函数方式 require（UMD：Node 可 require，浏览器挂 window.OSZhugeAdapter）
const A = require(adapterPath);

// 固定观察时钟（北京时间 2026-10-21 09:00，与 Python 测试同刻）
const CLOCK = new Date('2026-10-21T09:00:00+08:00');

function clone(x) { return JSON.parse(JSON.stringify(x)); }

const GOOD_SIX = {
  os: { ok: true, biz: { near: [], notes: [] } },
  home: { ok: true, calendar: { items: [] } },
  alerts: { ok: true, source_health: { sources: {
    premarket: { status: 'ok', as_of: '2026-10-21T08:00:00+08:00' } } } },
  notify: { ok: true, errors: [], items: [
    { id: 'n1', title: '阻断通知', state: 'blocked', created_at: '2026-10-21T07:00:00+08:00', reason: '通道异常' }] },
  tasks: { ok: true, tasks: [
    { task_id: 't1', title: '今日到期工单', status: 'open', due: '2026-10-21', created_at: '2026-10-20T10:00:00+08:00' },
    { task_id: 't2', title: '逾期工单', status: 'open', due: '2026-10-19', created_at: '2026-10-18T10:00:00+08:00' }] },
  capability: { ok: true, production_lines: [] },
};

/* 1. 模块可加载且纯函数：不修改输入 */
(function test_module_loadable_and_pure() {
  const snapshot = clone(GOOD_SIX);
  const out = A.buildBrief(GOOD_SIX, CLOCK);
  check('纯函数不改输入', JSON.stringify(GOOD_SIX) === JSON.stringify(snapshot));
  check('有建议产出', Array.isArray(out.suggestions) && out.suggestions.length > 0);
})();

/* 2. 输出契约：engine/model_invoked/dispatched/generated_at/business_day/建议字段 */
(function test_output_contract() {
  const out = A.buildBrief(GOOD_SIX, CLOCK);
  check('engine=rule-based', out.engine === 'rule-based');
  check('model_invoked=false', out.model_invoked === false);
  check('dispatched=false', out.dispatched === false);
  check('generated_at 为 ISO 且与 clock 同刻', out.generated_at === CLOCK.toISOString());
  check('business_day 为北京日', out.business_day === '2026-10-21');
  check('note 存在', typeof out.note === 'string' && out.note.length > 0);
  for (const s of out.suggestions) {
    for (const key of ['id', 'domain', 'priority', 'reason', 'source', 'as_of', 'confidence']) {
      if (!(key in s)) { failures.push('建议缺 ' + key); }
    }
    if (s.confidence !== 'rule-based') failures.push('confidence 必须为 rule-based');
  }
  const ids = out.suggestions.map(s => s.id);
  check('建议 id 去重', ids.length === new Set(ids).size);
})();

/* 3. blocked 通知与逾期工单排最前（priority 1） */
(function test_blocked_and_overdue_rank_first() {
  const out = A.buildBrief(GOOD_SIX, CLOCK);
  const top2 = out.suggestions.slice(0, 2).map(s => s.id);
  check('blocked+overdue 排前', top2.includes('notify:n1') && top2.includes('task:t2'));
  check('blocked 优先级 1', out.suggestions.find(s => s.id === 'notify:n1').priority === 1);
  check('overdue 优先级 1', out.suggestions.find(s => s.id === 'task:t2').priority === 1);
})();

/* 4. 今日到期点名（reason 含"今日"） */
(function test_due_today_called_out() {
  const out = A.buildBrief(GOOD_SIX, CLOCK);
  const s = out.suggestions.find(x => x.id === 'task:t1');
  check('存在 task:t1', !!s);
  check('今日到期点名', s && s.reason.indexOf('今日') !== -1);
  check('今日到期优先级 2', s && s.priority === 2);
})();

/* 5. 七源全缺：逐一点名 gaps，note 以"待核"开头，不产建议 */
(function test_missing_sources_degrade_explicitly() {
  const out = A.buildBrief({}, CLOCK);
  check('七源全缺不产建议', out.suggestions.length === 0);
  const gaps = new Set(out.gaps.map(g => g.source));
  const want = ['os', 'home', 'alerts', 'notify', 'tasks', 'capability', 'pushflow'];
  check('七源逐一点名', want.every(k => gaps.has(k)) && out.gaps.length === 7);
  check('gaps note 以待核开头', out.gaps.every(g => typeof g.note === 'string' && g.note.indexOf('待核') === 0));
})();

/* 6. ok:false 源不产建议且进 gaps */
(function test_ok_false_source_degrades() {
  const raw = Object.assign({}, GOOD_SIX, { notify: { ok: false } });
  const out = A.buildBrief(raw, CLOCK);
  check('ok:false 不产建议', out.suggestions.every(s => s.id !== 'notify:n1'));
  check('ok:false 进 gaps', out.gaps.some(g => g.source === 'notify'));
})();

/* 7. stale as_of：ok 源但数据偏旧降级提示（>48h→3；>168h→unknown） */
(function test_stale_as_of_flags_low_freshness() {
  const rawStale = clone(GOOD_SIX);
  rawStale.alerts = { ok: true, source_health: { sources: {
    premarket: { status: 'ok', as_of: '2026-10-01T08:00:00+08:00' } } } }; // 距 clock 20 天 >168h
  const out = A.buildBrief(rawStale, CLOCK);
  const s = out.suggestions.find(x => x.id === 'alert:premarket');
  check('stale 源产建议', !!s);
  check('stale reason 提新鲜度', s && s.reason.indexOf('新鲜度') !== -1);
  check('unknown 档优先级 3', s && s.priority === 3);
  const rawMid = clone(GOOD_SIX);
  rawMid.alerts = { ok: true, source_health: { sources: {
    premarket: { status: 'ok', as_of: '2026-10-18T09:00:00+08:00' } } } }; // 72h：48h<age<=168h
  const out2 = A.buildBrief(rawMid, CLOCK);
  const s2 = out2.suggestions.find(x => x.id === 'alert:premarket');
  check('偏旧档优先级 3', s2 && s2.priority === 3);
  check('偏旧档 reason 含偏旧', s2 && s2.reason.indexOf('偏旧') !== -1);
})();

/* 8. 坏时钟抛错（非 Date / 无效 Date） */
(function test_bad_clock_raises() {
  let threw = false;
  try { A.buildBrief(GOOD_SIX, 'not-a-date'); } catch (e) { threw = true; }
  check('字符串时钟抛错', threw);
  threw = false;
  try { A.buildBrief(GOOD_SIX, new Date('invalid')); } catch (e) { threw = true; }
  check('Invalid Date 抛错', threw);
  threw = false;
  try { A.buildBrief(null, CLOCK); } catch (e) { threw = true; }
  check('raw 非 dict 抛错', threw);
})();

/* 9. 源码无写副作用：禁 fetch/XHR/storage/eval/new Function/写属性 */
(function test_no_write_side_effects_by_design() {
  for (const banned of ['fetch(', 'XMLHttpRequest', 'localStorage', 'sessionStorage',
    'indexedDB', 'document.cookie', 'navigator.sendBeacon', 'eval(', 'new Function',
    '.post(', '.put(', '.delete(', 'import(', 'postMessage(']) {
    check('源码无 ' + banned, src.indexOf(banned) === -1);
  }
})();

/* 10. UMD 浏览器端：vm 沙箱执行后挂 window.OSZhugeAdapter */
(function test_umd_browser_global() {
  const sandbox = { window: {}, navigator: { userAgent: 'node-vm' } };
  sandbox.self = sandbox.window;
  vm.createContext(sandbox);
  try {
    new vm.Script(src).runInContext(sandbox);
    const G = sandbox.window.OSZhugeAdapter || (sandbox.self && sandbox.self.OSZhugeAdapter);
    check('浏览器端挂 OSZhugeAdapter', !!G);
    check('浏览器端 buildBrief 可用', G && typeof G.buildBrief === 'function');
    if (G) {
      const out = G.buildBrief(clone(GOOD_SIX), CLOCK);
      check('浏览器端输出 engine', out.engine === 'rule-based');
    }
  } catch (e) {
    failures.push('浏览器端执行失败：' + e.message);
  }
})();

/* 10b. WO-OS-20 第七源 pushflow：当日失败率 55% → P1，文案区分"历史失败簇" */
(function test_pushflow_high_rate_priority_1() {
  const raw = clone(GOOD_SIX);
  raw.pushflow = {
    ok: true, today: 276, attempted: 276, ok_count: 124, fail_count: 152,
    fail_rate: 0.55, policy_suppressed: 0,
    channels: { openapi: { ok: 100, fail: 150 }, 'lark-cli': { ok: 24, fail: 2 } },
  };
  const out = A.buildBrief(raw, CLOCK);
  const s = out.suggestions.find(x => x.id === 'pushflow:daily_rate');
  check('pushflow 产建议', !!s);
  check('pushflow P1', s && s.priority === 1);
  check('pushflow domain=通知', s && s.domain === '通知');
  check('pushflow 含百分比', s && s.reason.indexOf('真实投递失败率 55%') !== -1);
  check('pushflow 含失败/尝试数', s && s.reason.indexOf('失败 152/尝试 276') !== -1);
  check('pushflow 区分历史簇', s && s.reason.indexOf('不是历史失败簇') !== -1);
  check('pushflow source', s && s.source === '/api/zhuge/brief.pushflow');
  check('pushflow 说明来源', s && s.reason.indexOf('pushflow.jsonl') !== -1);
  check('pushflow confidence', s && s.confidence === 'rule-based');
  check('pushflow 源可用不进 gaps', !out.gaps.some(g => g.source === 'pushflow'));
})();

/* 10b2. WO-OS-20-FIX：策略抑制不计失败，文案点名抑制条数 */
(function test_pushflow_policy_suppressed_noted() {
  const raw = clone(GOOD_SIX);
  raw.pushflow = {
    ok: true, today: 282, attempted: 179, ok_count: 130, fail_count: 49,
    fail_rate: 0.274, policy_suppressed: 103,
    channels: { openapi: { ok: 64, fail: 46 }, 'lark-cli': { ok: 65, fail: 3 } },
  };
  const out = A.buildBrief(raw, CLOCK);
  const s = out.suggestions.find(x => x.id === 'pushflow:daily_rate');
  check('FIX 27.4% 属 P3', s && s.priority === 3);
  check('FIX 分母用尝试数', s && s.reason.indexOf('失败 49/尝试 179') !== -1);
  check('FIX 点名抑制条数', s && s.reason.indexOf('103 条静默窗口策略抑制') !== -1);
  check('FIX 抑制不计入失败', s && s.reason.indexOf('不计入失败') !== -1);
})();

/* 10c. WO-OS-20 缺 pushflow → gaps 待核、不产建议 */
(function test_pushflow_missing_degrades() {
  const out = A.buildBrief(clone(GOOD_SIX), CLOCK);
  const gap = out.gaps.find(g => g.source === 'pushflow');
  check('缺 pushflow 进 gaps', !!gap);
  check('缺 pushflow note 待核', gap && gap.note.indexOf('待核') === 0);
  check('缺 pushflow 不产建议', !out.suggestions.some(s => s.id === 'pushflow:daily_rate'));
})();

/* 10d. WO-OS-22 自洽校验：旧载荷（无 attempted）不得静默按 today 算 */
(function test_pushflow_legacy_payload_guarded() {
  const raw = clone(GOOD_SIX);
  raw.pushflow = {
    ok: true, today: 276, ok_count: 124, fail_count: 152, fail_rate: 0.55,
    channels: { openapi: { ok: 100, fail: 150 } },
  };
  const out = A.buildBrief(raw, CLOCK);
  const gap = out.gaps.find(g => g.source === 'pushflow');
  check('旧载荷缺 attempted 进 gaps', !!gap);
  check('旧载荷 note 待核', gap && gap.note.indexOf('待核') === 0);
  check('旧载荷不产假失败率建议', !out.suggestions.some(s => s.id === 'pushflow:daily_rate'));
})();

/* 10e. WO-OS-22 自洽校验：行数与尝试数对不上 → 待核 */
(function test_pushflow_inconsistent_guarded() {
  const raw = clone(GOOD_SIX);
  raw.pushflow = {
    ok: true, today: 282, attempted: 179, ok_count: 130, fail_count: 49,
    fail_rate: 0.274, policy_suppressed: 50,   // 282-179=103 ≠ 50
    channels: {},
  };
  const out = A.buildBrief(raw, CLOCK);
  const gap = out.gaps.find(g => g.source === 'pushflow');
  check('不自洽进 gaps', !!gap);
  check('不自洽不产建议', !out.suggestions.some(s => s.id === 'pushflow:daily_rate'));
})();

if (failures.length) { console.error('FAIL WO-OS-07 adapter 契约：' + failures.join('；')); process.exit(1); }
console.log('PASS WO-OS-07 os_zhuge_adapter 契约：同语义9场景+pushflow2场景+UMD双端+无写副作用（' +
  A.buildBrief(GOOD_SIX, CLOCK).suggestions.length + ' 条建议样例）');
