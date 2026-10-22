// test_council_page_v5.cjs — 议会厅页面 v5 契约检查
const fs = require('fs');
const html = fs.readFileSync('os_council.html', 'utf8');
const m = html.match(/<script>\n([\s\S]*?)<\/script>/);
if (!m) throw new Error('no script');
try { new Function(m[1]); } catch (e) { throw new Error('JS语法: ' + e.message); }
console.log('PASS 整页JS可解析');
// v5 要点：一句话定稿（archive_preview）+ 存储自主表述 + 多语言占位
for (const kw of ['archive_preview', '一句话定稿', '自有存储', 'AI 语义拆解']) {
  if (!html.includes(kw)) throw new Error('缺 ' + kw);
}
for (const gone of ['每行一条:[谁]']) {
  if (html.includes(gone)) throw new Error('残留旧两步表单 ' + gone);
}
console.log('PASS v5 要素（一句话定稿/预览确认/存储自主）');
