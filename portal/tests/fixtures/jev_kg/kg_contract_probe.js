/* JEV 前端 KG 契约探针（由 portal/tests/test_jev_kg_fallback.py 调用）。
 *
 * 不是「字符串在不在」的静态检查：把 jev_intel.html 的 <script> 放进 Node vm
 * 真跑，验 kgUsable 的每一项判据、kgFromServer 与 compileRelations 同形状、
 * 6 位码兜底、徽标与深链 URL。入口的 load() 被摘掉，探针不发任何请求。
 *
 * 用法: node kg_contract_probe.js <jev_intel.html 绝对路径> <graph 响应 fixture>
 * 输出: 单行 JSON {"cases":[{name,ok,detail}],"total_fail":N}
 */
const fs = require('fs');
const vm = require('vm');

const htmlPath = process.argv[2];
const fixturePath = process.argv[3];
const cases = [];
let totalFail = 0;

function ck(name, ok, detail) {
  if (!ok) totalFail++;
  cases.push({ name: name, ok: !!ok, detail: String(detail === undefined ? '' : detail) });
}

function fakeEl() {
  return {
    innerHTML: '', textContent: '', value: '', dataset: {}, children: [], style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    querySelectorAll() { return []; }, querySelector() { return fakeEl(); },
    appendChild() {}, closest() { return null; }, setAttribute() {},
    set onclick(v) {}, get onclick() { return null; }
  };
}

function fatal(msg) {
  ck('探针自身可运行', false, msg);
  process.stdout.write(JSON.stringify({ cases: cases, total_fail: totalFail }) + '\n');
  process.exit(0);
}

let html;
try {
  html = fs.readFileSync(htmlPath, 'utf8');
} catch (e) { fatal('读不到 ' + htmlPath + ': ' + e.message); }
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) fatal('jev_intel.html 里没有内联 <script>');
const script = m[1];

/* 只摘掉入口的自动取数，其余代码原样执行（锚点必须唯一） */
const anchor = "load(_q.get('board') || null);";
const hit = script.split(anchor).length - 1;
if (hit !== 1) fatal('入口 load() 锚点 count=' + hit + '，探针需同步');
const code = script.replace(anchor, '/* pytest probe: no auto load */');

let graphData;
try {
  graphData = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
} catch (e) { fatal('读不到 graph fixture: ' + e.message); }

const els = {};
const search = '?day=20261106&kind=theme&board=' + encodeURIComponent('电力') +
               '&kg=auto&src=snapshot';
const ctx = {
  console, URL, URLSearchParams, Math, Object, Array, String, Number, Set, Map,
  Date, JSON, RegExp, Promise, Error, isNaN, parseInt, parseFloat,
  encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout,
  document: {
    getElementById(id) { return (els[id] ||= fakeEl()); },
    addEventListener() {}, querySelectorAll() { return []; },
    body: fakeEl()
  },
  location: { href: 'http://127.0.0.1:8080/jev_intel.html' + search, search },
  history: { replaceState() {} },
  fetch() { throw new Error('探针不应发起任何请求'); }
};
try {
  vm.createContext(ctx);
  vm.runInContext(code, ctx, { filename: 'jev_intel.js' });
  vm.runInContext('globalThis.__S = S; globalThis.__MODE = KG_MODE;' +
                  ' globalThis.__SRC = KG_SRC;', ctx);
} catch (e) { fatal('脚本执行失败: ' + e.message); }

const good = {
  ok: true, source: 'graph_cache', schema_version: undefined,
  day: '20261106', board_kind: 'theme', board: '电力',
  clean: true, n_rel: 2,
  rels: [{ id: 'r1' }], cover: [{ src: 'network.nodes', miss: 0 }],
  ents: {}, warnings: [], members: {}, idx_counts: {}
};
const clone = (o, patch) => Object.assign({}, o, patch || {});

/* ── A. kgUsable：判例 #30「静默成功比 404 危险」 ── */
ck('A01 graph_cache 通过', ctx.kgUsable(good) === true);
ck('A02 snapshot 通过', ctx.kgUsable(clone(good, { source: 'snapshot' })) === true);
ck('A03 jev.v2 兜底被拒 (HTTP 200)',
   ctx.kgUsable({ ok: true, schema_version: 'jev.v2', day: '20261106',
                  verdict: {}, message: 'ok' }) === false);
ck('A04 只有 ok 无 source 被拒',
   ctx.kgUsable({ ok: true, rels: [{ id: 'r1' }], cover: [], n_rel: 1 }) === false);
ck('A05 非法 source 被拒',
   ctx.kgUsable(clone(good, { source: 'jev_fallback' })) === false);
ck('A06 rels 空被拒',
   ctx.kgUsable(clone(good, { rels: [], n_rel: 0 })) === false);
ck('A07 rels 非数组被拒',
   ctx.kgUsable(clone(good, { rels: { r1: 1 } })) === false);
ck('A08 cover 缺失被拒',
   ctx.kgUsable(clone(good, { cover: undefined })) === false);
ck('A09 n_rel=0 被拒',
   ctx.kgUsable(clone(good, { n_rel: 0 })) === false);
ck('A10 ok 非 true 被拒',
   ctx.kgUsable(clone(good, { ok: 1 })) === false);
ck('A11 500 错误体被拒',
   ctx.kgUsable({ ok: false, error: 'internal', message: 'boom' }) === false);
ck('A12 null 被拒', ctx.kgUsable(null) === false);
ck('A13 schema_version 空串不误拒',
   ctx.kgUsable(clone(good, { schema_version: '' })) === true);

/* ── B. kgFromServer 与 Step1 compileRelations 完全同形状 ── */
const out = {
  ok: true, source: 'snapshot', day: '20261106', board_kind: 'theme',
  board: '电力', clean: true, n_rel: 2, warnings: [], members: { covered: 219 },
  cover: [{ src: 'network.nodes', miss: 0 }, { src: 'flow:board_rank', miss: 0 }],
  rels: [
    { id: 'r1', subj: '600900.SH', obj: 'board:电力', pred: 'IN_BOARD',
      t: '09:31', refs: ['600900.SH', 'board:电力'],
      evidence: { src: 'network.nodes', key: '600900.SH' } },
    { id: 'r2', subj: 'actor:主力资金', obj: '600900.SH', pred: 'ACTED_VIA',
      t: '15:00', refs: ['actor:主力资金', '600900.SH'],
      evidence: { src: 'flow:board_rank', key: 'x' } }
  ],
  ents: {
    '600900.SH': { label: '长江电力', type: 'stock' },
    'board:电力': { label: '电力', type: 'board' },
    'actor:主力资金': { label: '主力资金', type: 'actor' }
  }
};
let api = null, local = null;
try {
  api = ctx.kgFromServer(out);
  local = ctx.compileRelations(graphData);
} catch (e) { ck('B00 两个编译器都能跑', false, e.message); }
if (api && local) {
  ck('B00 两个编译器都能跑', true);
  /* 「同形状」= 渲染层消费的键一个不少；服务端额外只允许带来源元数据 */
  const lk = Object.keys(local).sort();
  const sk = Object.keys(api);
  const missing = lk.filter(k => sk.indexOf(k) < 0);
  const extras = sk.filter(k => lk.indexOf(k) < 0).sort();
  const allowed = ['clean', 'members', 'server', 'src', 'warnings'].sort();
  ck('B01 本地键被服务端对象全覆盖', missing.length === 0,
     'missing=' + missing.join(','));
  ck('B01b 附加键只限来源元数据', extras.join(',') === allowed.join(','),
     'extras=' + extras.join(','));
  const ia = Object.keys(api.idx).sort().join(',');
  const ib = Object.keys(local.idx).sort().join(',');
  ck('B02 五个反查索引同形状', ia === ib, ia + ' | ' + ib);
  ck('B03 server 标记 / source 透传', api.server === true && api.src === 'snapshot');
  ck('B04 refs 全为字符串', api.rels.every(r => r.refs.every(x => typeof x === 'string')));
  ck('B05 ofPred 反查', api.ofPred('IN_BOARD').length === 1 && api.count('ACTED_VIA') === 1);
  ck('B06 label / type', api.label('600900.SH') === '长江电力' && api.type('600900.SH') === 'stock');
  ck('B07 6 位码兜底不断链', api.neighbors('600900').length === 2,
     'neighbors(600900)=' + api.neighbors('600900').length);
  ck('B08 ofCode 反查', api.ofCode('600900.SH').length === 2);
  ck('B09 totalMiss 可用', api.totalMiss() === 0);
  ck('B10 ofTime 索引', api.ofTime('09:31').length === 1);
}

/* ── C. 徽标 hot / ok 与深链 ── */
try {
  const S = ctx.__S;
  S.kgMode = 'server'; S.kgSource = 'snapshot'; S.kg = api;
  const b1 = ctx.kgBadge();
  ck('C01 server 徽标 ok + 落盘快照',
     b1[1] === 'ok' && b1[0].indexOf('落盘快照') >= 0, b1.join(' / '));
  S.kgSource = 'graph_cache';
  ck('C02 server 徽标 graph 缓存',
     ctx.kgBadge()[0].indexOf('graph 缓存') >= 0, ctx.kgBadge()[0]);
  S.kgMode = 'local'; S.kgSource = null;
  const b2 = ctx.kgBadge();
  ck('C03 降级徽标 hot', b2[1] === 'hot' && b2[0].indexOf('本地编译') >= 0, b2.join(' / '));
  const u = ctx.kgApiUrl('电力');
  ck('C04 src=snapshot 深链透传', u.indexOf('src=snapshot') >= 0, u);
  ck('C05 深链带 day/kind/top/board',
     u.indexOf('day=20261106') >= 0 && u.indexOf('kind=theme') >= 0 &&
     u.indexOf('top=12') >= 0 && u.indexOf('board=') >= 0, u);
  ck('C06 KG_SRC / KG_MODE 常量', ctx.__SRC === 'snapshot' && ctx.__MODE === 'auto');
} catch (e) { ck('C00 徽标/深链可调用', false, e.message); }

process.stdout.write(JSON.stringify({ cases: cases, total_fail: totalFail }) + '\n');
