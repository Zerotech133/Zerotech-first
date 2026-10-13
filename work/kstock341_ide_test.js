// IDE 改造实测：①PROMPT 全文块折叠 ②左栏代码预览
const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1440, height: 900 } });
  await pg.goto("http://127.0.0.1:8080/ide.html", { waitUntil: "networkidle" });

  // 1) 选 agent，验证 PROMPT 全文块折叠态（默认 150px）
  await pg.click("#ag-DEV1");
  await pg.waitForTimeout(400);
  const h1 = await pg.locator("#pbox").evaluate(el => el.getBoundingClientRect().height);
  const btn1 = (await pg.locator(".clamp-bar .mini").last().textContent()).trim();
  console.log("pbox折叠高:", Math.round(h1) + "px", "| 按钮:", btn1);

  await pg.click(".clamp-bar .mini:last-child");
  await pg.waitForTimeout(300);
  const h2 = await pg.locator("#pbox").evaluate(el => el.getBoundingClientRect().height);
  const btn2 = (await pg.locator(".clamp-bar .mini").last().textContent()).trim();
  console.log("展开后高:", Math.round(h2) + "px", "| 按钮:", btn2);

  await pg.click(".clamp-bar .mini:last-child");
  await pg.waitForTimeout(300);
  const h3 = await pg.locator("#pbox").evaluate(el => el.getBoundingClientRect().height);
  console.log("收起后高:", Math.round(h3) + "px");

  // 2) 左树平台代码区，点 run_shadow.py
  const cfCount = await pg.locator(".cf").count();
  console.log("左树代码文件数:", cfCount);
  await pg.locator(".cf", { hasText: "run_shadow.py" }).click();
  await pg.waitForTimeout(400);
  const headTxt = await pg.locator("#center .a-head h2").textContent();
  const preChars = (await pg.locator("#center pre").textContent()).length;
  const sendDisabled = await pg.locator("#send").isDisabled();
  console.log("代码预览头:", headTxt.trim(), "| pre字符:", preChars, "| send禁用:", sendDisabled);

  // 3) 代码态切回 agent 态正常
  await pg.click("#ag-DEV1");
  await pg.waitForTimeout(300);
  const backHead = await pg.locator("#center .a-head h2").textContent();
  console.log("切回agent态头:", backHead.trim(), "| send恢复:", !(await pg.locator("#send").isDisabled()));

  await pg.screenshot({ path: "work/ide_code_preview.png" });
  await b.close();
  console.log("=== ALL PASS ===");
})().catch(e => { console.error("FAIL:", e.message); process.exit(1); });
