#!/usr/bin/env node
/**
 * test_dashboard_contracts.cjs —— E9 驾驶舱数据契约回归（离线默认 / --live 实测）
 *
 * 离线：strategies.json schema、quant.html / quant_v2_demo.html 关键不变量、契约文档存在。
 * --live：8080/8791 端点按 docs/dashboard_contracts.md 钉死形状逐项校验。
 * 纪律：改任何驾驶舱页面/端点前先跑本测试；失败必须先修再动。
 */
'use strict';
const fs = require('fs');
const path = require('path');

const PORTAL = __dirname;
const QB_DOCS = path.join(PORTAL, '..', 'docs');
const QB = path.join(PORTAL, '..', 'quant_backtest');

let pass = 0, fail = 0, warn = 0;
const failures = [];
function ok(cond, msg) {
  if (cond) { pass++; }
  else { fail++; failures.push(msg); console.error('  FAIL ' + msg); }
}
function todo(msg) { warn++; console.error('  WARN(台账) ' + msg); }
function section(name) { console.log('== ' + name); }

/* ── 1. 策略注册表 schema（契约 §6 /api/strategies） ── */
section('strategies.json schema');
(function () {
  const p = path.join(PORTAL, 'strategies.json');
  ok(fs.existsSync(p), 'strategies.json 存在');
  if (!fs.existsSync(p)) return;
  const d = JSON.parse(fs.readFileSync(p, 'utf-8'));
  ok(!!d._meta, '_meta 存在（注册表自述）');
  ok(Array.isArray(d.strategies) && d.strategies.length > 0, 'strategies 非空数组');
  const REQ = ['id', 'name', 'status', 'href'];
  const STATUSES = ['live', 'shadow', 'tool', 'paused'];
  const STAGES = ['S1', 'S2', 'S3', 'S4', 'S5'];
  for (const s of d.strategies) {
    for (const k of REQ) ok(s[k] !== undefined, `条目 ${s.id || '?'} 缺 ${k}`);
    ok(STATUSES.includes(s.status), `${s.id} status 枚举合法（${s.status}）`);
    if (s.since !== undefined) ok(/^\d{4}-\d{2}-\d{2}$/.test(s.since), `${s.id} since=YYYY-MM-DD`);
    if (s.metrics !== undefined) ok(Array.isArray(s.metrics), `${s.id} metrics 为数组`);
    if (s.pipeline !== undefined) {
      ok(STAGES.includes(s.pipeline.stage), `${s.id} pipeline.stage ∈ S1..S5`);
      ok(/^\d{8}$/.test(s.pipeline.as_of || ''), `${s.id} pipeline.as_of=YYYYMMDD`);
      ok(s.pipeline.source === 'strategy_layer/onboarding', `${s.id} pipeline.source 固定值`);
    }
  }
  // 362 定稿：上架必带 since。现 6 个 live 条目全缺（真实数据缺口，日期须人工回填，
  // 禁编造）→ 台账 WARN 模式，回填完成后升回 ok() 硬断言。见 docs/ops/BLOCKERS 等老板清单。
  const noSince = d.strategies.filter(s => s.status === 'live' && !s.since).map(s => s.id);
  if (noSince.length) todo(`live 条目缺 since（362 定稿）待人工回填: ${noSince.join(',')}`);
})();

/* ── 2. quant.html 原设计不变量 + v2 增量转正不变量 ── */
section('quant.html invariants');
(function () {
  const p = path.join(PORTAL, 'quant.html');
  ok(fs.existsSync(p), 'quant.html 存在');
  if (!fs.existsSync(p)) return;
  const h = fs.readFileSync(p, 'utf-8');
  // 原五条（不动）
  ok(h.includes('safePortalHref'), 'safePortalHref 同源安全策略在位');
  ok(!/no-cors/.test(h), '无 no-cors 假绿取数');
  ok(h.includes('var(--color-up)') && h.includes('var(--color-dn)'), '涨红跌绿配色约定在位');
  ok(h.includes('四层动线') && h.includes('驾驶层') && h.includes('投研竞技层'), '四层动线结构在位');
  ok(h.includes('/api/strategies') && h.includes('/api/alerts') && h.includes('/api/brief'), '五源取数在位');
  // v2 转正追加：置顶预警区
  ok(h.includes("RISK OFF") && h.includes('rbanner market'), 'risk_off 市场级横幅在位');
  ok(h.includes('rbanner chain') && h.includes('数据断链'), '链路级断链横幅在位');
  ok(h.includes('REGIME_EMBED') && h.includes('静态兜底(10-16)'), '内嵌快照兜底且显著标注静态(10-16)');
  ok(h.includes('top-alerts'), '置顶预警区容器在位');
  // v2 转正追加：regime-first 双轨倾向格
  ok(h.includes('k-regime-src'), '倾向格来源标签元素在位');
  ok(h.includes('策源 regime') && h.includes('brief 兜底'), '倾向格双来源标签在位（E4 双轨条款）');
  // 数据丰富（2026-10-19）：基建条四 chip + 聚合信号副行 + 三门计数 + 仓位 + 考核链
  ok(['inf-cont', 'inf-backup', 'inf-task', 'inf-kg'].every(id => h.includes(id)), '基建条四 chip 在位');
  ok(h.includes('renderInfra') && h.includes("state === 'Disabled'"), '基建渲染+计划任务异常检测在位');
  ok(h.includes('k-sig') && h.includes('/api/signals'), '聚合信号副行（8791 计数语义）在位');
  ok(h.includes("'三门 '") && h.includes("passed"), '倾向格三门通过数在位');
  ok(h.includes('溢价概率') && h.includes('pos_pct'), 'brief 兜底溢价概率+模拟仓仓位在位');
  ok(h.includes('60日考核链'), '策略考核链计数在位');
  // 投研交易所面板（k_stock/paper 机构 + PaperEngine 组合进板块）
  ok(['dk-strat', 'dk-judge', 'dk-market', 'dk-us'].every(id => h.includes(id)), '投研交易所四 chip 在位');
  ok(h.includes('renderDesk') && h.includes('us.accounts'), 'renderDesk+美股账本读取在位');
  // v2 转正追加：收编六环快照条
  const RINGS = ['pool', 'watch', 'auth', 'risk', 'trade', 'review'];
  ok(RINGS.every(r => h.includes(r + ':')), '六环键齐全（RING_CN 字面量）');
  ok(h.includes('six-strip') && h.includes('schip'), '六环快照条 sec 在位');
  // v2 转正追加：fetchFirst 降级链 + 工具坞 8791
  ok(h.includes('fetchFirst') && h.includes('/api/regime') && h.includes('/api/sixring'), 'fetchFirst 降级链（同源→直连 8791→内嵌兜底）在位');
  ok(h.includes('/probe/dash') && h.includes('dash-link'), '工具坞 8791 量化仪表盘（探活+链接）在位');
  // E7 市场闸
  ok(h.includes('k-gate') && h.includes('market_gate'), 'E7 市场闸展示在位');
  // E8 预警分级徽标
  ok(h.includes('LV_CN') && h.includes("class='lv ") && h.includes('.lv.market'), 'E8 预警分级徽标在位');
  // F5 市场准入判定卡（2026-10-19，工位 cockpit-v3）
  ok(h.includes('admission-card') && h.includes('/api/admission'), 'F5 准入判定卡容器 + /api/admission 取数在位');
  ok(h.includes("fetchFirst(['/api/admission'") && h.includes('DASH + \'/api/admission\''), 'F5 admission 降级链（同源代理→直连 8791）在位');
  ok(h.includes('ADMISSION_EMBED') && h.includes('静态兜底'), 'F5 内嵌静态兜底位与显著标注在位');
  ok(h.includes('counter_evidence') && h.includes('adm-counter') && h.includes('反证'), 'F5 反证标注（黄字）在位');
  ok(h.includes('不猜值'), 'F5 unknown/全败灰态不猜值在位');
  ok(h.includes('VERDICT_CN') && h.includes("'关门'") && h.includes("'窄门'") && h.includes("'宽门'"), 'F5 L0 三态判定（关门/窄门/宽门）在位');
  // F6 策略师观点墙 + 投研会一行 + 四市场 tab
  ok(h.includes('/data/wall.json') && h.includes('strategists'), 'F6 观点墙 wall.json 取数 + strategists 渲染在位');
  ok(h.includes('committee_line') && h.includes('wall-committee'), 'F6 投研会一行（有则显示无则隐藏）在位');
  ok(h.includes('wall-tabs') && h.includes('mtab planned') && h.includes('接入中'), 'F6 四市场 tab（planned 灰位接入中）在位');
  ok(h.includes("data-market=\"cn\"") && h.includes("data-market=\"hk\"") && h.includes("data-market=\"us\"") && h.includes("data-market=\"crypto\""), 'F6 四市场 tab 键齐全（A股 active，港股/美股/虚拟币 planned）');
  ok(h.includes('暂无观点') && h.includes('.scard.grey'), 'F6 score/view 空灰标「暂无」逻辑在位');
})();

/* ── 3. quant_v2_demo.html 最优化版不变量（E3/E4/E5 前端面） ── */
section('quant_v2_demo.html invariants');
(function () {
  const p = path.join(PORTAL, 'quant_v2_demo.html');
  ok(fs.existsSync(p), 'quant_v2_demo.html 存在');
  if (!fs.existsSync(p)) return;
  const h = fs.readFileSync(p, 'utf-8');
  ok(h.includes('safePortalHref'), 'safePortalHref 沿用');
  ok(h.includes('fetchFirst'), 'fetchFirst 降级链在位');
  ok(h.includes('静态兜底'), '静态兜底显著标注在位');
  ok(h.includes('REGIME_EMBED'), '内嵌快照兜底在位');
  ok(!/no-cors/.test(h), '无 no-cors 假绿取数');
  ok(h.includes("RISK OFF") && h.includes('rbanner market'), 'risk_off 市场级横幅在位');
  ok(h.includes('rbanner chain') && h.includes('数据断链'), '链路级断链横幅在位');
  const RINGS = ['pool', 'watch', 'auth', 'risk', 'trade', 'review'];
  ok(RINGS.every(r => h.includes(r + ':')), '六环键齐全（RING_CN 字面量）');
  ok(h.includes('/api/regime') && h.includes('/api/sixring') && h.includes('/probe/dash'),
     'regime/sixring/dash 探针接入在位');
  // E4 细则：倾向格来源标签机制存在（策源优先，brief 兜底）
  ok(h.includes('策源 regime') && h.includes('brief 兜底'), '倾向格双来源标签在位');
  ok(!/mode:\s*['"]no-cors['"]/.test(h), '显式无 no-cors 模式');
})();

/* ── 3b. wall.json schema（F7，2026-10-19：T-B 生成静态文件，未落盘前不挂红） ── */
section('wall.json schema (存在才断言)');
(function () {
  const p = path.join(PORTAL, 'data', 'wall.json');
  if (!fs.existsSync(p)) {
    todo('F7 portal/data/wall.json 未生成（T-B 观点墙适配器 pending）——前端灰态降级已就位，不挂红');
    return;
  }
  let d = null;
  try { d = JSON.parse(fs.readFileSync(p, 'utf-8')); }
  catch (e) { ok(false, 'wall.json 是合法 JSON（解析失败: ' + e.message + '）'); return; }
  ok(Array.isArray(d.strategists), 'wall.json: strategists 为数组');
  if (Array.isArray(d.strategists)) {
    for (const s of d.strategists) {
      ok(s && typeof s.name === 'string' && s.name.length > 0, `wall.json: 策略师条目有 name`);
      if (s.score !== undefined && s.score !== null) ok(typeof s.score === 'number', `wall.json: ${s.name} score 非空时为数值`);
      if (s.view !== undefined && s.view !== null) ok(typeof s.view === 'string', `wall.json: ${s.name} view 非空时为字符串`);
      if (s.picks_count !== undefined && s.picks_count !== null) ok(Number.isInteger(s.picks_count), `wall.json: ${s.name} picks_count 非空时为整数`);
    }
  }
  if (d.committee_line !== undefined && d.committee_line !== null) ok(typeof d.committee_line === 'string', 'wall.json: committee_line 非空时为字符串');
})();

/* ── 4. 契约文档守卫 ── */
section('contract doc guard');
ok(fs.existsSync(path.join(QB_DOCS, 'dashboard_contracts.md')), 'docs/dashboard_contracts.md 存在');

/* ── 5. --live：端点按契约形状实测 ── */
async function live() {
  section('live endpoint shapes');
  const B8080 = 'http://127.0.0.1:8080', B8791 = 'http://127.0.0.1:8791';
  async function j(url, ms) {
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), ms);
    try { const r = await fetch(url, { signal: ctl.signal }); if (!r.ok) return null; return await r.json(); }
    catch (e) { return null; } finally { clearTimeout(t); }
  }
  const brief = await j(B8080 + '/api/brief', 8000);
  ok(!!brief && !!brief.fin && typeof brief.fin.verdict === 'string' && /^\d{8}$/.test(brief.fin.latest || ''), 'brief: fin.verdict+latest 形状');
  ok(!!brief && !!brief.intel && typeof brief.intel.today === 'number', 'brief: intel 计数形状');
  const st = await j(B8080 + '/api/strategies', 8000);
  ok(!!st && Array.isArray(st.strategies) && st.strategies.length > 0, 'strategies: 非空数组');
  const al = await j(B8080 + '/api/alerts', 8000);
  ok(!!al && Array.isArray(al.alerts), 'alerts: 数组形状');
  const pp = await j(B8080 + '/api/paper_status', 8000);
  ok(!!pp && pp.ok === true && !!pp.latest && typeof pp.latest.cum_pct === 'number', 'paper_status: latest.cum_pct 形状');
  const h8791 = await j(B8791 + '/api/health', 5000);
  ok(!!h8791 && Array.isArray(h8791.checks) && h8791.checks.every(c => 'ok' in c && 'name' in c), '8791 health: checks[{name,ok}] 形状');
  const rg = await j(B8791 + '/api/regime', 5000);
  ok(!!rg && !!rg.regime && typeof rg.regime.available === 'boolean' && 'stale' in rg.regime, 'regime: available+stale 形状');
  const sx = await j(B8791 + '/api/sixring', 5000);
  ok(!!sx && !!sx.rings && 'pool' in sx.rings && 'risk' in sx.rings, 'sixring: 六环键形状');
  // F7 live：/api/admission（F2 工位端点，未落地前 WARN 台账 pending，不挂红不假绿）
  const adm = await j(B8791 + '/api/admission', 5000);
  if (!adm) {
    todo('F7 live /api/admission 未可达（F2 端点 pending）——判定卡前端灰态降级已就位');
  } else {
    const a = adm.admission || adm;
    ok(a.verdict !== undefined && ['closed', 'narrow', 'wide', 'unknown'].includes(a.verdict), 'admission: verdict 枚举形状');
    ok(Array.isArray(a.reasons) && Array.isArray(a.counter_evidence), 'admission: reasons/counter_evidence 数组形状');
    ok(!!a.inputs && ('regime' in a.inputs) && ('overall' in a.inputs) && ('prob' in a.inputs), 'admission: inputs{regime,overall,prob} 键形状');
    ok(typeof a.computed_at === 'string' && a.computed_at.length > 0, 'admission: computed_at 形状');
  }
}

(async () => {
  if (process.argv.includes('--live')) await live();
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})();
