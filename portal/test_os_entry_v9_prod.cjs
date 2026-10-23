/* WO-OS-01 契约测试：os_entry_v9_prod.html 生产候选
 * 断言：四视图、无全站 nav.js、无 iframe、无写请求、缺源文案、aria-live、标题/footer 标识。
 * 运行：node test_os_entry_v9_prod.cjs  （先红后绿：本文件先于 v9 落地时必须红）*/
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const htmlPath = path.join(__dirname, 'os_entry_v9_prod.html');
const failures = [];
function check(name, cond) { if (!cond) failures.push(name); }

let html = '';
try { html = fs.readFileSync(htmlPath, 'utf8'); }
catch (e) { console.error('FAIL 无法读取 ' + htmlPath + '：' + e.message); process.exit(1); }

// 1. 四视图齐备且 ID 唯一
const ids = [...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]);
check('ID 唯一', ids.length === new Set(ids).size);
for (const v of ['ai', 'work', 'results', 'screen']) check('view-' + v + ' 存在', ids.includes('view-' + v));

// 2. 禁项：iframe、写请求、本地存储。
// 2026-10-23 更新：K-INTEL-20261023-NAV 已定「全站统一顶栏 nav.js」方向并注入 7 页
//（含本页，守卫 55 页全带顶栏）；旧断言「不含 nav.js」随之过时，改为必须带统一顶栏。
check('带统一顶栏 nav.js', html.includes('static/nav.js'));
check('不含 iframe', !html.includes('<iframe'));
check('全文件零 iframe 字样', !html.includes('iframe'));
check('不含 POST/PUT/DELETE', !/(method\s*:\s*['"](?:POST|PUT|DELETE)['"])/.test(html));
check('不含 localStorage/sessionStorage', !/localStorage|sessionStorage/.test(html));

// 2b. WO-OS-07 诸葛亮建议引擎接入：adapter 引用顺序 + 降级文案
check('引用 os_zhuge_adapter.js', /<script\s+src="\/static\/os_zhuge_adapter\.js"\s+defer><\/script>/.test(html));
check('adapter 在 os_integrated_projection.js 之后',
  html.indexOf('/static/os_integrated_projection.js') !== -1 &&
  html.indexOf('/static/os_zhuge_adapter.js') > html.indexOf('/static/os_integrated_projection.js'));
check('建议引擎未加载降级文案', html.includes('建议引擎未加载'));
check('消费 OSZhugeAdapter.buildBrief', html.includes('OSZhugeAdapter.buildBrief'));

// 2c. WO-OS-08 ResultEvidence 证据槽：契约脚本挂载 + 证据槽容器 + 显式空态
check('引用 os_result_contract.js', /<script\s+src="\/static\/os_result_contract\.js"\s+defer><\/script>/.test(html));
check('contract 在 os_zhuge_adapter.js 之后',
  html.indexOf('/static/os_zhuge_adapter.js') !== -1 &&
  html.indexOf('/static/os_result_contract.js') > html.indexOf('/static/os_zhuge_adapter.js'));
check('证据槽容器 evidence-list', /id="evidence-list"/.test(html));
check('证据槽空态文案', html.includes('证据槽暂无数据'));
check('空态声明不伪造完成', html.includes('不伪造完成记录'));
check('契约字段说明行', /事项ID/.test(html) && /业务日/.test(html) && /回执/.test(html));
check('消费 OSResultContract.entry', html.includes('OSResultContract.entry'));

// 2d. WO-OS-10 大屏与首页同源消费建议引擎：#board-brief 容器位于大屏区 + 共用引擎标识
check('大屏建议容器 board-brief', /id="board-brief"/.test(html));
(function () {
  const start = html.indexOf('id="view-screen"');
  const seg = start === -1 ? '' : html.slice(start, html.indexOf('</section>', start));
  check('board-brief 位于 view-screen 大屏区', seg.includes('board-brief'));
})();
check('大屏与首页共用建议引擎标识', html.includes('大屏与首页共用建议引擎'));

// 2e. WO-OS-11 渐进消费 8091 只读路由 /api/zhuge/brief：fetch 字符串 + 本地兜底降级文案
check('fetch /api/zhuge/brief', html.includes("'/api/zhuge/brief'"));
check('路由不可达本地兜底文案', html.includes('路由不可达，本地引擎兜底'));

// 2g. WO-OS-13 G2 补强：唯一 fetch 出口与唯一 buildBrief 调用点（字符串计数===1）
check('fetch( 计数===1', (html.match(/fetch\(/g) || []).length === 1);
check('buildBrief( 计数===1', (html.match(/buildBrief\(/g) || []).length === 1);

// 2h. WO-OS-13 G1 补强：fetchRouteBrief 结构闸门三条件
check('G1 结构闸门 r.engine', /fetchRouteBrief[\s\S]*?r\.engine/.test(html));
check('G1 结构闸门 Array.isArray(r.suggestions)', /fetchRouteBrief[\s\S]*?Array\.isArray\(r\.suggestions\)/.test(html));
check('G1 结构闸门 Array.isArray(r.gaps)', /fetchRouteBrief[\s\S]*?Array\.isArray\(r\.gaps\)/.test(html));

// 2f. WO-OS-12 建议条目点击下钻工作平台：jumpWork 事件委托 + data-work-domain + 能力目录域匹配高亮
check('存在 jumpWork 函数', html.includes('function jumpWork'));
check('建议条目可点击 data-work-domain', html.includes('data-work-domain'));
check('能力目录支持 highlighted 高亮类', /cap-item\.highlighted\s*\{/.test(html));

// 2j. WO-OS-24 执行级渲染断言：briefCard 必须能真跑通（防未声明变量致建议区空白）
// 静态字符串断言抓不到 ReferenceError——验收席实测页面 briefCard 引用了未声明的
// meta，只要建议非空就抛错、整块建议区渲染中断。此处构造最小 DOM stub 真执行一次。
(function test_briefCard_actually_renders() {
  const m = html.match(/function briefCard\(s\)\{[\s\S]*?return n\}/);
  if (!m) { failures.push('未找到 briefCard 函数体'); return; }
  const makeEl = () => ({
    tag: '', cls: '', text: '', children: [], dataset: {}, title: '',
    append(...kids) { this.children.push(...kids); },
    setAttribute() {},
  });
  const elStub = (tag, cls, text) => {
    const n = makeEl();
    n.tag = tag; n.cls = cls || ''; n.text = text === undefined ? '' : String(text);
    return n;
  };
  const linkForStub = () => null;
  let factory;
  try {
    factory = new Function('el', 'linkFor', 'return (' + m[0] + ');');
  } catch (e) {
    failures.push('briefCard 无法构造：' + e.message);
    return;
  }
  const briefCard = factory(elStub, linkForStub);
  try {
    const card = briefCard({
      id: 'notify:history_cluster', domain: '通知', priority: 2,
      reason: '近 7 天投递失败簇 21 条', source: '/api/notify/queue', as_of: null,
    });
    check('briefCard 执行成功返回节点', !!card && typeof card.append === 'function');
    check('briefCard 渲染出内容', !!card && card.children.length >= 2);
  } catch (e) {
    failures.push('briefCard 执行抛错（建议区会整块空白）：' + e.message);
  }
  // 2k. as_of 类型防御：非字符串（Date 对象等）不得渲染出错位文本
  try {    const card = briefCard({
      id: 'task:t1', domain: '研发', priority: 4, reason: '工单进行中',
      source: '/api/org/tasks', as_of: new Date('2026-10-21T16:02:49+08:00'),
      confidence: 'rule-based',
    });
    const small = card.children[card.children.length - 1];
    const txt = String(small && small.text || '');
    check('as_of=Date 不泄漏错位文本', txt.indexOf('ct 2') === -1 && txt.indexOf('Invalid') === -1);
    check('as_of=Date 仍标规则归集', txt.indexOf('规则归集') !== -1);
  } catch (e) {
    failures.push('as_of=Date 场景抛错：' + e.message);
  }
})();

// 2i. WO-OS-15 ResultEvidence DEMO 样例：容器 + data-demo 标记 + 显式演示文案 + 三条样例走契约 entry()check('样例容器 evidence-sample', /id="evidence-sample"/.test(html));
check('样例条目 data-demo=true 标记', html.includes("'data-demo','true'"));
check('样例显式演示文案', html.includes('样例数据，仅演示分级样式'));
check('样例分级预览标题', html.includes('分级样式预览（样例数据）'));
check('三条样例 id 齐备', html.includes('demo-verified') && html.includes('demo-unverified') && html.includes('demo-pending'));
check('样例渲染走契约 entry', /renderEvidenceSample[\s\S]*?OSResultContract\.entry/.test(html));
(function () {
  const a = html.indexOf('function renderEvidenceSample');
  const b = html.indexOf('function renderEvidence(', a);
  const body = a === -1 || b === -1 ? '' : html.slice(a, b);
  check('样例函数体存在', body.length > 0);
  check('样例禁 fetch 真实结果数据', !/\bfetch\(/.test(body));
})();

// 2j. WO-OS-16 移动端触控：590px 断点内 nav button 与 .actions a/button 触控目标 ≥44px
(function () {
  const m = /@media\s*\(max-width:\s*590px\)\s*\{([\s\S]*?)\}\}/.exec(html);
  check('存在 @media(max-width:590px) 断点', !!m);
  const seg = m ? m[1] : '';
  check('590px 断点内 nav button 触控 min-height:44px', /\.nav\s+button\s*\{[^}]*min-height:\s*44px/.test(seg));
  check('590px 断点内 .actions a/button 触控 min-height:44px', /\.actions\s+(?:a|button)\s*\{[^}]*min-height:\s*44px/.test(seg));
  check('590px 断点内触控 padding 增强', /\.nav\s+button\s*\{[^}]*padding/.test(seg) && /\.actions\s+(?:a|button)\s*\{[^}]*padding/.test(seg));
})();

// 3. 只读 GET 端点齐备
for (const ep of ['/api/os', '/api/home', '/api/alerts', '/api/notify/queue', '/api/org/tasks', '/api/capability/board'])
  check('端点 ' + ep, html.includes("'" + ep + "'"));

// 4. 缺源与边界文案（不得伪造完成/审批/提醒）
check('缺源不等于零', html.includes('不代表') || html.includes('不等于'));
check('非模型声明', html.includes('非模型'));
check('未派单声明', html.includes('未派单'));
check('评审标识移除', !html.includes('评审稿'));

// 5. 无障碍：状态区 aria-live
check('freshness aria-live', /id="freshness"[^>]*aria-live="polite"/.test(html));
check('board-stamp aria-live', /id="board-stamp"[^>]*aria-live="polite"/.test(html));

// 6. 内联脚本可解析
const jsMatch = html.match(/<script>\s*([\s\S]*?)<\/script>/);
if (!jsMatch) { failures.push('未找到内联 script'); }
else {
  try { new vm.Script(jsMatch[1]); } catch (e) { failures.push('JS 解析失败：' + e.message); }
}

// 2l. WO-OS-25 每条建议都能直达原账本：pushflow 建议必须映射到通知指挥页
check('linkFor 支持 pushflow 前缀', /id\.startsWith\('pushflow:'\)/.test(html));
check('pushflow 映射通知指挥页', /pushflow:['"]?\s*\?\s*'\/notify-command'/.test(html)
  || /startsWith\('pushflow:'\)\s*\?\s*'\/notify-command'/.test(html));

// 2m. WO-OS-27 真实证据源：静态 JSON 拉取 + 喂给 renderEvidence
check('存在 fetchEvidence 加载器', html.includes('async function fetchEvidence'));
check('静态证据端点', html.includes('/data/result_evidence/today.json'));
check('证据喂入 renderEvidence（带元数据）',
  /renderEvidence\(evidencePayload\?evidencePayload\.items:window\.OS_RESULT_EVIDENCE,evidencePayload\)/.test(html));
check('refresh 取证据', html.includes('const evidencePayload=await fetchEvidence()'));
check('render 签名含证据参数', html.includes('function render(states,projection,raw,routeBrief,evidencePayload)'));
check('证据源标注为静态不依赖8091', html.includes('nginx 直服，不依赖 8091'));
check('新增加载器复用唯一 fetch 出口', html.includes("await get('/data/result_evidence/today.json')"));

// 2n. WO-OS-28 证据诚实呈现：过期态 + 渲染上限 + 截断可见
check('证据业务日过期态常量', /EVIDENCE_MAX_ROWS\s*=/.test(html));
check('证据渲染有上限', /slice\(0,\s*EVIDENCE_MAX_ROWS\)/.test(html));
check('截断提示含总量与显示数', html.includes('共') && html.includes('项，本页显示最近'));
check('非今日数据显式过期', html.includes('非今日数据') || html.includes('数据日期为'));
check('证据源范围声明', html.includes('仅本仓提交与交付物'));

// 2o. WO-OS-29 行为断言固化：真执行 renderEvidence 验证行数与降级（不再只靠字符串 grep）
(function test_renderEvidence_behaviour() {
  const s = html.indexOf('const EVIDENCE_MAX_ROWS');
  const e = html.indexOf('async function get(url)');
  if (s < 0 || e < 0 || e < s) { failures.push('renderEvidence 片段定位失败'); return; }
  const src = html.slice(s, e);
  const makeEl = (t, c, x) => ({
    tag: t, cls: c || '', text: x === undefined ? '' : String(x), children: [],
    append(...k) { this.children.push(...k); },
    setAttribute(k, v) { this[k] = v; },
  });
  const box = { children: [], replaceChildren() { this.children = []; }, append(n) { this.children.push(n); } };
  // 契约 stub：足够判级即可（status 由字段齐全度决定）
  const contractStub = {
    entry(raw) {
      const ok = raw && raw.id && raw.domain && raw.business_day && raw.producer_time && raw.evidence_uri;
      return ok ? { status: raw.receipt_uri ? 'verified' : 'unverified' } : { status: 'pending', display: '缺项' };
    },
  };
  let render;
  try {
    render = new Function('window', '$', 'el', src + '; return renderEvidence;')(
      { OSResultContract: contractStub, OS_RESULT_EVIDENCE: [] }, () => box, makeEl);
  } catch (err) {
    failures.push('renderEvidence 构造失败：' + err.message); return;
  }
  const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' });
  const mkItem = (i) => ({
    id: 'commit:' + i, domain: '研发', business_day: today,
    producer_time: today + 'T10:0' + (i % 10) + ':00+08:00', outcome: 'done',
    evidence_uri: 'commit:' + i, receipt_uri: null,
  });
  const items = Array.from({ length: 20 }, (_, i) => mkItem(i));

  // a) 正常：8 条上限 + 1 行汇总
  box.replaceChildren();
  render(items, { business_day: today, total_available: 183 });
  check('行为·截断到8条+汇总行', box.children.length === 9);
  check('行为·汇总含总量', String(box.children[8].text).indexOf('共 183 项') !== -1);
  check('行为·汇总含显示数', String(box.children[8].text).indexOf('显示最近 8 项') !== -1);

  // b) 过期：只 1 行提示，零证据行
  box.replaceChildren();
  render(items, { business_day: '2026-10-20', total_available: 183 });
  check('行为·过期只1行', box.children.length === 1);
  check('行为·过期文案', String(box.children[0].text).indexOf('非今日数据') !== -1);
  check('行为·过期零证据行', box.children.every(n => String(n.text || '').indexOf('commit:') === -1));

  // c) total_available=NaN 不得渲染脏值
  box.replaceChildren();
  render(items, { business_day: today, total_available: NaN });
  const tail = String(box.children[box.children.length - 1].text);
  check('行为·NaN 不泄漏', tail.indexOf('NaN') === -1);
  check('行为·NaN 回落 items 数', tail.indexOf('共 20 项') !== -1);

  // d) 空数据
  box.replaceChildren();
  render([], null);
  check('行为·空态1行', box.children.length === 1);

  // e) 时区固定北京：源码不得用裸 sv-SE（会随浏览器本地时区漂移）
  check('行为·时区固定北京', html.indexOf("toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'})") !== -1);
})();

// 2p. WO-OS-30 真浏览器巡检（CDP 驱动本机 Edge）发现的三处问题
// 背景：stub 测试全绿，但整页在真实浏览器里有三个假陈述——
//   ① 首屏约 1 秒显示"证据契约未加载，不展示任何完成记录"（defer 未执行时同步调用就下了结论）；
//   ② 证据接口读失败会落成"证据槽暂无数据"，把"读不到"说成"没有"；
//   ③ 汇总文案写死"全部为 unverified"，一旦有回执就变成假话。
check('契约脚本单次引入且带 defer',
  (html.match(/<script src="\/static\/os_result_contract\.js"/g) || []).length === 1
  && /<script src="\/static\/os_result_contract\.js" defer>/.test(html));
check('静态占位不得断言暂无数据',
  !/<div id="evidence-list"><div class="empty">证据槽暂无数据/.test(html));
check('静态占位为正在读取', /<div id="evidence-list"><div class="empty">正在读取证据源/.test(html));
check('初始调用改为待读取态', html.includes('renderEvidencePending();'));

(function test_evidence_honesty() {
  // 取段起点必须在 renderEvidenceSample 之前，否则样例函数不在作用域内
  const s = html.indexOf('function renderEvidenceSample');
  const e = html.indexOf('async function get(url)');
  if (s < 0 || e < 0 || e < s) { failures.push('诚实性测试片段定位失败'); return; }
  const src = html.slice(s, e);
  const makeEl = (t, c, x) => ({
    tag: t, cls: c || '', text: x === undefined ? '' : String(x), children: [],
    append(...k) { this.children.push(...k); },
    setAttribute(k, v) { this[k] = v; },
  });
  const box = { children: [], replaceChildren() { this.children = []; }, append(n) { this.children.push(n); } };
  const contractStub = {
    entry(raw) {
      const ok = raw && raw.id && raw.domain && raw.business_day && raw.producer_time && raw.evidence_uri;
      return ok ? { status: raw.receipt_uri ? 'verified' : 'unverified' } : { status: 'pending', display: '缺项' };
    },
  };
  let render, pending, sample;
  try {
    const f = new Function('window', '$', 'el',
      src + '; return { render: renderEvidence, pending: renderEvidencePending, sample: renderEvidenceSample };')(
      { OSResultContract: contractStub, OS_RESULT_EVIDENCE: [] }, () => box, makeEl);
    render = f.render; pending = f.pending; sample = f.sample;
  } catch (err) {
    failures.push('诚实性测试构造失败：' + err.message); return;
  }
  const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' });
  const allText = () => box.children.map(n => String(n.text || '')).join(' | ');

  // f) 证据接口读失败：必须说"读取失败"，不得落成"暂无数据"
  box.replaceChildren();
  render([], { __failed: true, reason: '模拟 404' });
  check('行为·失败态说明读取失败', allText().indexOf('证据源读取失败') !== -1);
  check('行为·失败态带原因', allText().indexOf('模拟 404') !== -1);
  check('行为·失败态不推断为零', allText().indexOf('不推断为零') !== -1);
  check('行为·失败态不得说暂无数据', allText().indexOf('证据槽暂无数据') === -1);

  // g) 混合分级：汇总必须按真实结果算，不得写死"全部为 unverified"
  const mixed = [
    { id: 'a', domain: '研发', business_day: today, producer_time: today + 'T10:00:00+08:00', outcome: 'done', evidence_uri: 'x', receipt_uri: 'receipt:1' },
    { id: 'b', domain: '研发', business_day: today, producer_time: today + 'T10:01:00+08:00', outcome: 'done', evidence_uri: 'y', receipt_uri: null },
    { id: 'c', domain: '研发', business_day: today, producer_time: today + 'T10:02:00+08:00', outcome: 'done', evidence_uri: '', receipt_uri: null },
  ];
  box.replaceChildren();
  render(mixed, { business_day: today, total_available: 3 });
  const tail = String(box.children[box.children.length - 1].text);
  check('行为·汇总统计已验证', tail.indexOf('已验证 1 项') !== -1);
  check('行为·汇总统计缺回执', tail.indexOf('缺回执 1 项') !== -1);
  check('行为·汇总统计待核', tail.indexOf('待核 1 项') !== -1);
  check('行为·汇总不写死全部unverified', tail.indexOf('全部为 unverified') === -1);

  // h) 待读取态：JS 刚启动时的诚实表达
  box.replaceChildren();
  pending();
  check('行为·待读取态文案', allText().indexOf('正在读取证据源') !== -1);
  check('行为·待读取态不下结论',
    allText().indexOf('暂无数据') === -1 && allText().indexOf('契约未加载') === -1);

  // i) 分级样式预览：契约可用时必须真渲染 3 条样例（截图曾显示"契约未加载"恒空）
  box.replaceChildren();
  sample();
  check('行为·样例预览渲染3条', box.children.length === 3);
  check('行为·样例预览按契约分三级',
    box.children.map(n => n.cls).join(',').indexOf('ev-verified') !== -1
    && box.children.map(n => n.cls).join(',').indexOf('ev-unverified') !== -1
    && box.children.map(n => n.cls).join(',').indexOf('ev-pending') !== -1);
  check('行为·样例预览不显示契约未加载', allText().indexOf('契约未加载') === -1);
})();

// 2q. WO-OS-30 补充：样例渲染必须延后到 DOMContentLoaded（defer 脚本晚于内联脚本执行）
check('样例渲染延后到 DOMContentLoaded',
  /document\.readyState==='loading'[\s\S]{0,120}renderEvidenceSample/.test(html));
check('样例契约缺载文案指向真实文件', html.includes('/static/os_result_contract.js）未加载'));

// 2r. WO-OS-31 D5 地基状态灯：结构断言 + 真执行 renderFoundation 行为断言
check('地基灯面板存在', html.includes('id="board-foundation"'));
check('地基灯数据源为审计摘要',
  html.includes("get('/data/task_exit_audit/task_exit_summary.json')"));
check('地基灯并入refresh链', /renderFoundation\(foundationPayload\)/.test(html));
(function test_renderFoundation_behaviour() {
  const s = html.indexOf('function renderFoundation(');
  const e = html.indexOf('async function fetchFoundation(');
  if (s < 0 || e < 0 || e < s) { failures.push('renderFoundation 片段定位失败'); return; }
  const src = html.slice(s, e);
  const makeEl = (t, c, x) => ({
    tag: t, cls: c || '', text: x === undefined ? '' : String(x), children: [],
    append(...k) { this.children.push(...k); },
    setAttribute(k, v) { this[k] = v; },
  });
  const box = { children: [], replaceChildren() { this.children = []; }, append(n) { this.children.push(n); } };
  let render;
  try {
    render = new Function('window', '$', 'el', src + '; return renderFoundation;')(
      {}, () => box, makeEl);
  } catch (err) {
    failures.push('renderFoundation 构造失败：' + err.message); return; }
  const allText = () => box.children.map(n => n.text + ' ' + n.children.map(c => c.text).join(' ')).join(' ');

  // a) 失败态：读取失败必须显式说，不得推断为正常
  box.replaceChildren();
  render({ __failed: true, reason: 'HTTP 404' });
  check('地基·读取失败显式', allText().indexOf('读取失败') !== -1 && allText().indexOf('不推断为正常') !== -1);

  // b) 真实摘要（当前盘面 9 失败）：红灯 + 失败数 + 首三个任务名 + 超3条截断
  box.replaceChildren();
  render({ generated_at: '2026-10-22T01:39:00+08:00', total_scanned: 42, ok: 29,
           failed: 9, disabled: 2, never_run: 2,
           failures: [
             { name: 'QuantWatchdog', last_result: 2 },
             { name: 'AlertWatch', last_result: 1 },
             { name: 'AgentMondayCheckpoint', last_result: 3221225786 },
             { name: 'HermesBriefPush', last_result: 267014 },
           ] });
  const txt = allText();
  check('地基·失败态红灯', txt.indexOf('● 红') !== -1);
  check('地基·失败数与任务名', txt.indexOf('失败 9/42') !== -1 && txt.indexOf('QuantWatchdog exit=2') !== -1);
  check('地基·超出3条截断提示', txt.indexOf('AgentMondayCheckpoint') !== -1 && txt.indexOf(' 等') !== -1);

  // c) 全绿态：0 失败给绿灯 + 正常退出≠有产出 caveat
  box.replaceChildren();
  render({ generated_at: '2026-10-22T01:39:00+08:00', total_scanned: 42, ok: 42,
           failed: 0, disabled: 0, never_run: 0, failures: [] });
  const green = allText();
  check('地基·全绿态绿灯', green.indexOf('● 绿') !== -1 && green.indexOf('● 红') === -1);
  check('地基·绿灯 caveat 正常退出≠有产出', green.indexOf('正常退出≠有产出') !== -1);

  // d) 覆盖不全：有禁用/未跑给黄灯
  box.replaceChildren();
  render({ generated_at: '2026-10-22T01:39:00+08:00', total_scanned: 42, ok: 38,
           failed: 0, disabled: 2, never_run: 2, failures: [] });
  check('地基·覆盖不全黄灯', allText().indexOf('● 黄') !== -1);
})();

if (failures.length) { console.error('FAIL WO-OS-01 契约：' + failures.join('；')); process.exit(1); }
console.log('PASS WO-OS-01/08/10/11 os_entry_v9_prod 契约：四视图/禁项/端点/文案/无障碍/脚本/ResultEvidence证据槽/大屏board-brief同源建议/8091路由渐进消费本地兜底');
