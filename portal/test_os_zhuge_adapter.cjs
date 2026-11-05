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

/* 3. WO-OS-23：blocked 聚合成簇(P2)，逾期工单仍 P1 排最前 */
(function test_cluster_and_overdue_rank() {
  const out = A.buildBrief(GOOD_SIX, CLOCK);
  const ids = out.suggestions.map(s => s.id);
  check('逾期工单排最前', ids[0] === 'task:t2');
  check('blocked 聚合成簇', ids.includes('notify:history_cluster'));
  check('blocked 不再逐条展开', !ids.includes('notify:n1'));
  const cluster = out.suggestions.find(s => s.id === 'notify:history_cluster');
  check('簇优先级 2', cluster && cluster.priority === 2);
  check('簇文案含近7天', cluster && cluster.reason.indexOf('近 7 天') !== -1);
  check('簇文案澄清非当前故障', cluster && cluster.reason.indexOf('不代表此刻发不出去') !== -1);
  check('overdue 优先级 1', out.suggestions.find(s => s.id === 'task:t2').priority === 1);
})();

/* 3b. WO-OS-23：deadline 类仍单列 P1 */
(function test_deadline_stays_p1() {
  const raw = clone(GOOD_SIX);
  raw.notify = { ok: true, errors: [], items: [
    { id: 'd1', title: '数据锚到点未就绪 · us / postclose', state: 'blocked',
      created_at: '2026-10-21T08:00:00+08:00', reason: '锚 stale' },
    { id: 'n1', title: 'hub 投递失败', state: 'blocked',
      created_at: '2026-10-21T07:00:00+08:00', reason: 'ok=0' }] };
  const out = A.buildBrief(raw, CLOCK);
  const d = out.suggestions.find(s => s.id === 'notify:d1');
  check('deadline 单列 P1', d && d.priority === 1);
  check('deadline 文案到点未就绪', d && d.reason.indexOf('到点未就绪') !== -1);
  const c = out.suggestions.find(s => s.id === 'notify:history_cluster');
  check('簇不含 deadline', c && c.reason.indexOf('1 条') !== -1);
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

/* 7b. 2026-11-05 OPS-ZHUGE-EMPTY-WORDING：empty 是合法零警报、retired 是已下线，
 * 都不能套 stale 的“需回原账本核对”；只有真正非 ok/empty 的源才报 P2。 */
(function test_empty_and_retired_not_reported_as_stale() {
  const raw = clone(GOOD_SIX);
  raw.alerts = { ok: true, source_health: { sources: {
    nine_dim: { status: 'empty', as_of: '2026-10-21T06:00:00+08:00' },
    council: { status: 'retired', as_of: null },
    premarket: { status: 'stale', as_of: '2026-10-20T08:00:00+08:00' } } } };
  const out = A.buildBrief(raw, CLOCK);
  check('empty 不产建议', !out.suggestions.some(s => s.id === 'alert:nine_dim'));
  check('retired 不产建议', !out.suggestions.some(s => s.id === 'alert:council'));
  check('真 stale 仍 P2', out.suggestions.some(s => s.id === 'alert:premarket' && s.priority === 2));
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

/* 10f. WO-OS-25 趋势：改善/恶化/持平三档 */
(function test_pushflow_trend_three_cases() {
  const mk = (todayRate, prevRate) => {
    const raw = clone(GOOD_SIX);
    raw.pushflow = {
      ok: true, today: 100, attempted: 100,
      ok_count: Math.round(100 * (1 - todayRate)),
      fail_count: Math.round(100 * todayRate), fail_rate: todayRate,
      policy_suppressed: 0, channels: {},
      recent: [
        { day: '2026-10-20', attempted: 100, fail_count: Math.round(100 * prevRate), fail_rate: prevRate },
        { day: '2026-10-21', attempted: 100, fail_count: Math.round(100 * todayRate), fail_rate: todayRate },
      ],
    };
    return A.buildBrief(raw, CLOCK).suggestions.find(s => s.id === 'pushflow:daily_rate');
  };
  const improving = mk(0.24, 0.53);
  check('趋势改善', improving && improving.reason.indexOf('改善') !== -1);
  check('趋势带基准值', improving && improving.reason.indexOf('前一日 53%') !== -1);
  const worsening = mk(0.55, 0.28);
  check('趋势恶化', worsening && worsening.reason.indexOf('恶化') !== -1);
  const flat = mk(0.30, 0.31);
  check('趋势持平', flat && flat.reason.indexOf('基本持平') !== -1);
  // 缺 recent 不给趋势
  const raw = clone(GOOD_SIX);
  raw.pushflow = { ok: true, today: 100, attempted: 100, ok_count: 70,
    fail_count: 30, fail_rate: 0.30, policy_suppressed: 0, channels: {} };
  const noTrend = A.buildBrief(raw, CLOCK).suggestions.find(s => s.id === 'pushflow:daily_rate');
  check('缺 recent 不给趋势', noTrend && noTrend.reason.indexOf('改善') === -1
    && noTrend.reason.indexOf('恶化') === -1);
})();

/* 10g. WO-OS-25-FIX：①5pp 边界一致判持平 ②null fail_rate 不得凭空造趋势 */
(function test_trend_boundary_and_null_guard() {
  const mk = (todayRate, prevRate) => {
    const raw = clone(GOOD_SIX);
    raw.pushflow = {
      ok: true, today: 100, attempted: 100,
      ok_count: Math.round(100 * (1 - todayRate)),
      fail_count: Math.round(100 * todayRate), fail_rate: todayRate,
      policy_suppressed: 0, channels: {},
      recent: [
        { day: '2026-10-20', attempted: 100, fail_count: Math.round(100 * prevRate), fail_rate: prevRate },
        { day: '2026-10-21', attempted: 100, fail_count: Math.round(100 * todayRate), fail_rate: todayRate },
      ],
    };
    return A.buildBrief(raw, CLOCK).suggestions.find(s => s.id === 'pushflow:daily_rate');
  };
  // 边界：+5pp 与 -5pp 都必须持平
  const up5 = mk(0.25, 0.20);
  const down5 = mk(0.25, 0.30);
  check('+5pp 判持平(含边界)', up5 && up5.reason.indexOf('持平') !== -1);
  check('-5pp 判持平(含边界)', down5 && down5.reason.indexOf('持平') !== -1);
  // 越界：6pp 才判
  const up6 = mk(0.31, 0.25);
  const down6 = mk(0.25, 0.31);
  check('+6pp 判恶化', up6 && up6.reason.indexOf('恶化') !== -1);
  check('-6pp 判改善', down6 && down6.reason.indexOf('改善') !== -1);

  // null / '' fail_rate 不得被 Number() 当 0
  const raw = clone(GOOD_SIX);
  raw.pushflow = {
    ok: true, today: 100, attempted: 100, ok_count: 70, fail_count: 30,
    fail_rate: 0.30, policy_suppressed: 0, channels: {},
    recent: [{ day: '2026-10-20', attempted: 100, fail_count: 30, fail_rate: null },
             { day: '2026-10-21', attempted: 100, fail_count: 30, fail_rate: 0.30 }],
  };
  const s = A.buildBrief(raw, CLOCK).suggestions.find(x => x.id === 'pushflow:daily_rate');
  check('null fail_rate 不凭空造改善', s && s.reason.indexOf('改善') === -1);
  check('null fail_rate 不给趋势', s && s.reason.indexOf('个百分点') === -1);
})();

if (failures.length) { console.error('FAIL WO-OS-07 adapter 契约：' + failures.join('；')); process.exit(1); }
console.log('PASS WO-OS-07 os_zhuge_adapter 契约：同语义9场景+pushflow2场景+UMD双端+无写副作用（' +
  A.buildBrief(GOOD_SIX, CLOCK).suggestions.length + ' 条建议样例）');
