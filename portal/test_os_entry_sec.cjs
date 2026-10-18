/* os_entry.html 安全加固 + 时点时钟回归测试（node test_os_entry_sec.cjs）
 *
 * 背景（2026-10-19 审计）：
 * - P0 esc() 原为恒等函数 String(s) → 全部插值点实际裸奔，函数名撒谎；
 * - P1 BOARDS[h] 原型污染（h='constructor' 等命中 Object.prototype，renderBoard 半渲染卡死）；
 * - P1 HEALTH_CACHE 永久复用且失败(null)也缓存 → 8791 宕了又起，灯不刷新；
 * - P1 nav.js 双引入无双栏护栏（f7170225 实录缺陷）；
 * - P2 探针无超时、class/style 拼接无白名单、//evil.com 协议相对 URL 逃逸；
 * - 回归：applyTimelineClock keep 引用 bug（48d54199，单测曾抓出「倒计时正常的隐蔽失效」）。
 *
 * 方法：提取页面内联 <script> → 在 IIFE 收尾前注入 window.__sec 测试钩（仅内存，不改盘上文件）
 *       → vm 新上下文 + 最小 DOM stub 执行 → 行为断言。不 import 生产代码，零副作用。
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

/* ---------- 提取内联脚本（nav.js 是 <script src=...>，正则只匹配无属性裸标签） ---------- */
const HTML = fs.readFileSync(path.join(__dirname, 'os_entry.html'), 'utf8');
const m = HTML.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error('FAIL: os_entry.html 未找到内联 <script>'); process.exit(1); }
let code = m[1];

/* ---------- 注入测试钩：IIFE 最后一个 })(); 之前 ---------- */
const HOOK =
  ";window.__sec={esc:esc,boardOf:boardOf,badgeCls:badgeCls,tlCls:tlCls," +
  "safeColor:safeColor,drillTarget:drillTarget,fetchHealth:fetchHealth,route:route," +
  "applyTimelineClock:applyTimelineClock,BOARDS:BOARDS,ALERTS:ALERTS," +
  "health:function(){return {cache:HEALTH_CACHE,ts:HEALTH_TS,ttl:HEALTH_TTL}}};";
const idx = code.lastIndexOf('})();');
if (idx < 0) { console.error('FAIL: 未找到 IIFE 收尾 })();'); process.exit(1); }
code = code.slice(0, idx) + HOOK + code.slice(idx);

/* ---------- 最小 DOM stub ---------- */
function mkEl(tag) {
  const el = {
    tagName: tag || 'div', innerHTML: '', textContent: '', title: '', id: '',
    style: {}, dataset: {}, children: [], attributes: {}, _b: null,
    classList: {
      _s: new Set(),
      add: function () { for (var i = 0; i < arguments.length; i++) this._s.add(arguments[i]); },
      remove: function () { for (var i = 0; i < arguments.length; i++) this._s.delete(arguments[i]); },
      toggle: function (c, f) {
        if (f === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); }
        else if (f) { this._s.add(c); } else { this._s.delete(c); }
      },
      contains: function (c) { return this._s.has(c); }
    },
    setAttribute: function (k, v) { this.attributes[k] = String(v); },
    getAttribute: function (k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; },
    addEventListener: function () {}, removeEventListener: function () {},
    insertBefore: function (n) { this.children.unshift(n); return n; },
    querySelector: function () { return this._b || null; },
    get firstChild() { return this.children[0] || null; }
  };
  return el;
}
function mkTL(time, label) {
  const el = mkEl('div');
  el.attributes['data-time'] = time;
  el._b = { textContent: label }; /* querySelector('b') */
  return el;
}

const EL_REG = new Map();
function getEl(id) {
  if (!EL_REG.has(id)) { const e = mkEl(); e.id = id; EL_REG.set(id, e); }
  return EL_REG.get(id);
}
let TL_NODES = [
  mkTL('09:15', '开盘准备'), mkTL('13:30', 'Ollama 参数窗口'),
  mkTL('16:40', '护航只读'), mkTL('17:35', 'pre_observation 跑数')
];
const docStub = {
  getElementById: getEl,
  querySelectorAll: function (sel) { return sel.indexOf('tl-item') >= 0 ? TL_NODES : []; },
  querySelector: function () { return null; }, /* .panel.active 初始无 → showTab('overview') */
  addEventListener: function () {}, createElement: function (t) { return mkEl(t); }
};

/* ---------- fetch stub（可切换 ok/fail，记录调用） ---------- */
const fetchCalls = [];
let FETCH_MODE = 'ok';
const fetchStub = function (p, opts) {
  fetchCalls.push([p, opts || {}]);
  if (FETCH_MODE === 'fail') return Promise.reject(new Error('stub net down'));
  return Promise.resolve({
    ok: true,
    json: function () { return Promise.resolve({ ok: true, services: [] }); }
  });
};

/* ---------- 可控时钟（本地时区；月份 0 基） ---------- */
const RealDate = Date;
let NOW_MS = new RealDate(2026, 9, 19, 16, 45, 0).getTime();
function FakeDate() {
  if (arguments.length) return new RealDate.apply(null, arguments);
  return new RealDate(NOW_MS);
}
FakeDate.now = function () { return NOW_MS; };
FakeDate.prototype = RealDate.prototype;
function setClock(h, mnt) { NOW_MS = new RealDate(2026, 9, 19, h, mnt, 0).getTime(); }

/* ---------- vm 执行 ---------- */
const sandbox = {
  document: docStub,
  window: { addEventListener: function () {} },
  location: { hash: '', pathname: '/os-entry' },
  fetch: fetchStub,
  AbortController: AbortController,
  setTimeout: function () { return 0; }, clearTimeout: function () {},
  setInterval: function () { return 0; }, clearInterval: function () {},
  console: console, Date: FakeDate
};
vm.createContext(sandbox);
try {
  vm.runInContext(code, sandbox, { filename: 'os_entry.inline.js' });
} catch (e) {
  console.error('FAIL: 脚本在 stub 下执行即抛错（stub 缺口或产品缺陷）：' + e.stack);
  process.exit(1);
}
const sec = sandbox.window.__sec;
if (!sec) { console.error('FAIL: 测试钩未注入'); process.exit(1); }

/* ---------- 断言器 ---------- */
let PASS = 0, FAIL = 0;
function ok(cond, msg) {
  if (cond) { PASS++; console.log('  PASS  ' + msg); }
  else { FAIL++; console.log('  FAIL  ' + msg); }
}
function eq(a, b, msg) {
  ok(a === b, msg + '（期望 ' + JSON.stringify(b) + '，实得 ' + JSON.stringify(a) + '）');
}

/* ================= A. esc 真转义（P0） ================= */
console.log('\n[A] esc HTML 转义');
eq(sec.esc('<script>alert(1)</script>'), '&lt;script&gt;alert(1)&lt;/script&gt;', '< > 转义');
eq(sec.esc('"onerror="'), '&quot;onerror=&quot;', '" 转义');
eq(sec.esc("a'b"), 'a&#39;b', "' 转义");
eq(sec.esc('a&b'), 'a&amp;b', '& 转义');
eq(sec.esc(null), '', 'null → 空串');
eq(sec.esc(undefined), '', 'undefined → 空串');
eq(sec.esc(42), '42', '数字 → 字符串');
ok(sec.esc('<img src=x onerror=alert(1)>').indexOf('<img') < 0, '无裸 < 残留');

/* ================= B. boardOf 原型污染（P1） ================= */
console.log('\n[B] boardOf 原型污染防护');
var realKey = Object.keys(sec.BOARDS)[0];
ok(!!sec.boardOf(realKey), '自有键 ' + realKey + ' 正常返回板块');
eq(sec.boardOf('constructor'), null, "h='constructor' → null");
eq(sec.boardOf('toString'), null, "h='toString' → null");
eq(sec.boardOf('valueOf'), null, "h='valueOf' → null");
eq(sec.boardOf('hasOwnProperty'), null, "h='hasOwnProperty' → null");
eq(sec.boardOf('__proto__'), null, "h='__proto__' → null");
eq(sec.boardOf(''), null, '空串 → null');
eq(sec.boardOf(null), null, 'null → null');
eq(sec.boardOf('no-such-board'), null, '不存在键 → null');

/* ================= C/D. class 白名单（P2） ================= */
console.log('\n[C/D] class 白名单');
eq(sec.badgeCls('b-live'), 'b-live', 'badgeCls 合法值透传');
eq(sec.badgeCls('b-evil'), 'b-run', 'badgeCls 非法值回落 b-run');
eq(sec.badgeCls(undefined), 'b-run', 'badgeCls undefined 回落 b-run');
eq(sec.tlCls(''), 'tl-item', 'tlCls 空串 → tl-item');
eq(sec.tlCls('w'), 'tl-item w', 'tlCls w → tl-item w');
eq(sec.tlCls('r'), 'tl-item r', 'tlCls r → tl-item r');
eq(sec.tlCls('evil'), 'tl-item', 'tlCls 非法值回落 tl-item');

/* ================= E. safeColor（P2） ================= */
console.log('\n[E] safeColor rgba 校验');
eq(sec.safeColor('rgba(88,166,255,.12)'), 'rgba(88,166,255,.12)', '合法 rgba 透传');
eq(sec.safeColor('rgb(1,2,3)'), 'rgb(1,2,3)', '合法 rgb 透传');
eq(sec.safeColor('red;background:url(//evil)'), '#8b949e', 'CSS 逃逸串 → 兜底灰');
eq(sec.safeColor(''), '#8b949e', '空 → 兜底灰');
eq(sec.safeColor(null), '#8b949e', 'null → 兜底灰');
eq(sec.safeColor('expression(alert(1))'), '#8b949e', 'expression → 兜底灰');

/* ================= F. drillTarget 协议相对 URL（P2） ================= */
console.log('\n[F] drillTarget 下钻目标判定');
eq(sec.drillTarget(['x', '/quant']), '/quant', '/ 开头真实路由 → 放行');
eq(sec.drillTarget(['x', '/legion']), '/legion', '/legion → 放行');
eq(sec.drillTarget(['x', '//evil.com']), null, '//evil.com 协议相对 URL → 拦截');
eq(sec.drillTarget(['x', '///evil.com']), null, '///evil.com → 拦截');
eq(sec.drillTarget(['x', 'http://127.0.0.1:8791']), null, 'http 远程 → 展示卡');
eq(sec.drillTarget(['x', 'javascript:alert(1)']), null, 'javascript: → 展示卡');
eq(sec.drillTarget(['x', null]), null, 'null 目标 → 展示卡');
eq(sec.drillTarget(['x', '']), null, '空串目标 → 展示卡');
eq(sec.drillTarget(null), null, 'null 卡 → 展示卡');

/* ================= G. HEALTH_CACHE TTL + 失败不粘（P1） ================= */
console.log('\n[G] HEALTH_CACHE TTL 与失败不粘');
var h0 = sec.health();
eq(h0.ttl, 15000, 'HEALTH_TTL = 15000ms');
var p1 = sec.fetchHealth(), p2 = sec.fetchHealth();
ok(p1 === p2, 'TTL 内复用同一 Promise');
var fetchSignalCalls = fetchCalls.filter(function (c) { return c[1] && c[1].signal; });
ok(fetchSignalCalls.length > 0, '探针 fetch 带 AbortController signal（超时自保已接线）');
NOW_MS += 20000; /* 越过 TTL */
FETCH_MODE = 'fail';
var p3 = sec.fetchHealth();
p3.then(function () {
  var hf = sec.health();
  ok(hf.cache === null && hf.ts === 0, '探测失败后 cache/ts 清零（失败不粘）');
  FETCH_MODE = 'ok'; NOW_MS += 20000;
  var p4 = sec.fetchHealth();
  ok(p4 && p4 !== p3, '恢复后重新发起探测（新 Promise）');
  return p4;
}).then(function () { part2(); }).catch(function (e) {
  console.error('FAIL: G 段异步链异常：' + e.stack); process.exit(1);
});

/* ================= H~J 同步段（在 G 的微队列之后跑，避免时钟互相污染） ================= */
function part2() {

/* ---------- H. route 集成：原型污染端到端 + 渲染冒烟 ---------- */
console.log('\n[H] route 集成');
var threw = null;
try { sandbox.location.hash = '#/constructor'; sec.route(); } catch (e) { threw = e; }
ok(!threw, "hash='#/constructor' 不抛错（原型污染端到端）");
threw = null;
try { sandbox.location.hash = '#/' + realKey; sec.route(); } catch (e) { threw = e; }
ok(!threw, "hash='#/" + realKey + "' 渲染不抛错");
ok(getEl('b-head').innerHTML.length > 100, 'renderBoard 产出 b-head 内容（' + getEl('b-head').innerHTML.length + ' 字符）');
ok(getEl('b-head').innerHTML.indexOf('<h2>') > 0, 'b-head 含标题结构');
threw = null;
try { sandbox.location.hash = '#/no-such-board'; sec.route(); } catch (e) { threw = e; }
ok(!threw, '不存在板块 hash 不抛错（回落 overview）');
/* 真实契约：无效 hash 走 else 分支——panel-board 撤下 active、tabbar 恢复、总览面板顶上；
   hash 串本身不复位（复位只在 renderBoard 内部守卫 / Esc 键），分享带错 hash 的链接不炸 */
ok(!getEl('panel-board').classList.contains('active'), '无效 hash → panel-board 撤下 active');
ok(getEl('panel-overview').classList.contains('active'), '无效 hash → 总览面板激活');
ok(!getEl('tabbar').classList.contains('hidden'), '无效 hash → tabbar 恢复可见');
var agrid = getEl('alertgrid').innerHTML;
ok(agrid.indexOf('class="lv p0"') > 0 || agrid.indexOf("class='lv p0'") > 0, '告警卡含 lv p0 白名单类');
ok(agrid.indexOf('lv evil') < 0 && agrid.indexOf('lv constructor') < 0, '告警卡无越权 lv 类');

/* ---------- I. 时点时钟状态机（回归 48d54199 keep 引用 bug） ---------- */
console.log('\n[I] applyTimelineClock 状态机');
function tlByTime(t) { return TL_NODES.filter(function (e) { return e.attributes['data-time'] === t; })[0]; }
function nowCount() { return TL_NODES.filter(function (e) { return e.classList.contains('now'); }).length; }
function pastCount() { return TL_NODES.filter(function (e) { return e.classList.contains('past'); }).length; }
var tp = getEl('next-tp');

setClock(16, 45); sec.applyTimelineClock();
ok(tlByTime('16:40').classList.contains('now'), '16:45 → 16:40 时点高亮 now（keep 引用回归：旧 bug 此处全灭）');
ok(tlByTime('17:35').classList.contains('now') === false, '16:45 → 17:35 未高亮');
ok(tlByTime('09:15').classList.contains('past') && tlByTime('13:30').classList.contains('past'), '09:15/13:30 已过 → past');
eq(nowCount(), 1, '同时只有一个 now');
eq(tp.textContent, '下一个时点 50m 后：pre_observation 跑数', '16:45 倒计时指向 17:35');

setClock(17, 36); sec.applyTimelineClock();
ok(tlByTime('17:35').classList.contains('now'), '17:36 → 17:35 高亮 now');
eq(tp.textContent, '正在进行：pre_observation 跑数', '17:36 无未来时点 → 正在进行');

setClock(23, 59); sec.applyTimelineClock();
eq(tp.textContent, '今日时点已全部完成', '23:59 全部完成');
eq(nowCount(), 0, '23:59 无 now');
eq(pastCount(), 4, '23:59 全部 past');

setClock(17, 25); sec.applyTimelineClock();
ok(tlByTime('17:35').classList.contains('now'), '17:25 → 17:35 落入 now 窗（前 10min）');
eq(tp.textContent, '即将到时 10m 后：pre_observation 跑数', 'diff<=15 → 即将到时口径');

setClock(8, 0); sec.applyTimelineClock();
eq(tp.textContent, '下一个时点 1h15m 后：开盘准备', '08:00 → 09:15，1h15m 格式');

/* 唯一性竞选：两个 now 窗重叠（16:40 窗 [16:30,17:00] × 16:50 窗 [16:50,17:10]，16:55 双落窗） */
TL_NODES = [mkTL('16:40', '护航只读'), mkTL('16:50', '双窗竞选'), mkTL('17:35', 'pre_observation 跑数')];
setClock(16, 55); sec.applyTimelineClock();
eq(nowCount(), 1, '双落窗 → 唯一 now');
ok(tlByTime('16:50').classList.contains('now'), '保留最晚已到时点 16:50（正在进行优先）');
ok(!tlByTime('16:40').classList.contains('now'), '16:40 被降级');

/* 恢复默认节点，避免影响后续 */
TL_NODES = [mkTL('09:15', '开盘准备'), mkTL('13:30', 'Ollama 参数窗口'), mkTL('16:40', '护航只读'), mkTL('17:35', 'pre_observation 跑数')];

/* ---------- J. nav.js 幂等护栏（生成物契约） ---------- */
console.log('\n[J] nav.js 幂等护栏');
var navjs = fs.readFileSync(path.join(__dirname, 'static', 'nav.js'), 'utf8');
ok(navjs.indexOf("data-wb-nav") > 0, 'nav.js 含 data-wb-nav 幂等护栏（双引入不叠栏）');
ok(navjs.indexOf("getAttribute('data-wb-nav')==='1'") > 0, '护栏判据落在 IIFE 首行（return 早退）');

/* ---------- 汇总 ---------- */
console.log('\n========== 汇总：PASS ' + PASS + ' / FAIL ' + FAIL + ' ==========');
process.exit(FAIL ? 1 : 0);
}
