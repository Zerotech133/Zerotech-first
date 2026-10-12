const { chromium } = require('playwright');
const fs = require('fs');
const BASE = 'http://127.0.0.1:8080';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push('PAGEERR ' + e.message.slice(0, 80)));
  const results = [];
  // 门面 /
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForTimeout(1800);
  const r1 = await page.evaluate(() => ({
    cards: document.querySelectorAll('a.dm').length,
    pageH: document.documentElement.scrollHeight,
    overflowX: document.documentElement.scrollWidth > window.innerWidth,
    backBtn: Array.from(document.querySelectorAll('a')).some(a => a.textContent.trim() === '← 返回'),
  }));
  results.push(['门面卡片数=9', r1.cards === 9, `got ${r1.cards}`]);
  results.push(['门面无横向溢出', !r1.overflowX, '']);
  results.push(['首页不渲染返回键', !r1.backBtn, '']);
  console.log('门面 pageH=', r1.pageH);
  // /creator 子页返回键存在+在最前
  await page.goto(BASE + '/quant', { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForTimeout(1500);
  const r2 = await page.evaluate(() => {
    const bar = document.querySelector('div[style*="position:sticky"]');
    const kids = bar ? Array.from(bar.children) : [];
    const back = kids.find(a => a.textContent.trim() === '← 返回');
    return { back: !!back, first2: back ? kids.indexOf(back) <= 1 : false,
             overflowX: document.documentElement.scrollWidth > window.innerWidth };
  });
  results.push(['/quant 返回键存在', r2.back, '']);
  results.push(['/quant 返回键在顶栏最前', r2.first2, '']);
  results.push(['/quant 无横向溢出', !r2.overflowX, '']);
  // 导航无死链
  const navHtml = await page.content();
  results.push(['导航无死链(col-audit/screen-sig/bridge-act)',
    !navHtml.includes('/col-audit') && !navHtml.includes('/screen/sig') && !navHtml.includes('/bridge/act'), '']);
  results.push(['青萍之末带🚧标注', navHtml.includes('青萍之末 🚧'), '']);
  await browser.close();
  console.log('===== 终验 =====');
  let fail = 0;
  for (const [n, ok, note] of results) { console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${note ? ' (' + note + ')' : ''}`); if (!ok) fail++; }
  console.log('pageerror:', errors.length); errors.slice(0, 3).forEach(e => console.log(' ', e));
  fs.writeFileSync('work/ui_verify2_211.json', JSON.stringify({ results, errors }, null, 1));
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('FATAL', e.message); process.exit(2); });
