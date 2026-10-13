// K-ORG DEV3 视觉线可拉动改造 · 浏览器实测
// 验证：初始渲染 / 拖 rz2 改调试台宽 / localStorage 记忆 / 刷新后保持
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto('http://127.0.0.1:8080/ide.html', { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);

  const cols = async () => await page.$eval('.main', el => getComputedStyle(el).gridTemplateColumns);
  console.log('initial cols:', await cols());
  await page.screenshot({ path: 'work/ide_drag_before.png' });

  // 拖 rz2（center|debug 分隔条）左移 120px
  const rz = await page.$('.rz2');
  const box = await rz.boundingBox();
  console.log('rz2 box:', JSON.stringify(box));
  await page.mouse.move(box.x + 3, box.y + 300);
  await page.mouse.down();
  await page.mouse.move(box.x + 3 - 120, box.y + 300, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(300);

  const colsAfter = await cols();
  console.log('after drag cols:', colsAfter);
  const dbg = await page.$eval('.main', el => el.style.getPropertyValue('--w-dbg'));
  const stored = await page.evaluate(() => localStorage.getItem('--w-dbg'));
  console.log('--w-dbg set:', dbg, '· localStorage:', stored);
  await page.screenshot({ path: 'work/ide_drag_after.png' });

  // 刷新验证记忆
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(600);
  console.log('after reload cols:', await cols());

  // 拖 rz1（左树宽）
  const rz1 = await page.$('.rz1');
  const b1 = await rz1.boundingBox();
  await page.mouse.move(b1.x + 3, b1.y + 200);
  await page.mouse.down();
  await page.mouse.move(b1.x + 3 + 60, b1.y + 200, { steps: 5 });
  await page.mouse.up();
  await page.waitForTimeout(200);
  console.log('after rz1 drag cols:', await cols());

  await browser.close();
  console.log('ALL DRAG CHECKS DONE');
})().catch(e => { console.error('FAIL:', e.message); process.exit(1); });
