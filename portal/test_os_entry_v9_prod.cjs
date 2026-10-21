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

// 2. 禁项：全站胶囊导航、iframe、写请求、本地存储
check('不含 nav.js', !html.includes('static/nav.js'));
check('不含 iframe', !html.includes('<iframe'));
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

if (failures.length) { console.error('FAIL WO-OS-01 契约：' + failures.join('；')); process.exit(1); }
console.log('PASS WO-OS-01/08 os_entry_v9_prod 契约：四视图/禁项/端点/文案/无障碍/脚本/ResultEvidence证据槽');
