/* K-STOCK-344 三期实测：Ctrl+P 命令面板 / 轨迹时间线 / 记忆加深 —— 深层断言（341 判例） */
const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1500, height: 920 } });
  const errs = [];
  pg.on("pageerror", e => errs.push(String(e).slice(0, 200)));
  await pg.goto("http://127.0.0.1:8080/ide.html", { waitUntil: "networkidle" });

  // ── 1) Ctrl+P 命令面板 ──
  await pg.keyboard.press("Control+p");
  await pg.waitForTimeout(200);
  const opened = await pg.locator("#palmask.open").count();
  console.log("1a) Ctrl+P 打开面板:", opened === 1);
  await pg.fill("#palq", "run_shadow");
  await pg.waitForTimeout(200);
  const items = await pg.locator("#pallist .pi").count();
  const firstTxt = await pg.locator("#pallist .pi").first().textContent();
  console.log("1b) 搜 run_shadow 命中:", items, "条 · 首条:", firstTxt.trim().slice(0, 50));
  await pg.keyboard.press("Enter");
  await pg.waitForTimeout(400);
  const tabOn = await pg.locator(".tab.on").textContent();
  const codeLen = await pg.locator("#edc .prompt-box").textContent();
  console.log("1c) Enter 打开代码 Tab:", tabOn.trim(), "· 内容", codeLen.length, "字符");
  // 面板已关
  console.log("1d) 选后面板自动关:", (await pg.locator("#palmask.open").count()) === 0);
  // 工单搜索
  await pg.keyboard.press("Control+p");
  await pg.fill("#palq", "K-STOCK");
  await pg.waitForTimeout(200);
  const tcnt = await pg.locator("#pallist .pi").count();
  console.log("1e) 搜工单 K-STOCK 命中:", tcnt, "条");
  await pg.keyboard.press("Escape");
  await pg.waitForTimeout(150);
  console.log("1f) Esc 关闭:", (await pg.locator("#palmask.open").count()) === 0);

  // ── 2) 轨迹时间线 ──
  await pg.click('.ai[title="轨迹"]');
  await pg.waitForTimeout(200);
  await pg.click("#side .it >> nth=0");
  await pg.waitForTimeout(400);
  const tlCnt = await pg.locator("#edc .tli").count();
  const tlHead = await pg.locator("#edc h3:has-text('动作时间线')").count();
  const tlDot = await pg.locator("#edc .tl-dot").count();
  console.log("2) 轨迹时间线: h3", tlHead, "· 动作行", tlCnt, "· 时间点", tlDot);

  // ── 3) 记忆加深（recent 8 + desc 200） ──
  await pg.click('.ai[title="记忆"]');
  await pg.waitForTimeout(200);
  const memCnt = await pg.locator("#side .it").count();
  console.log("3a) 记忆视图 Agent 数:", memCnt);
  if (memCnt > 0) {
    await pg.click("#side .it >> nth=0");
    await pg.waitForTimeout(400);
    const memH2 = await pg.locator("#edc h2").first().textContent();
    const rows = await pg.locator("#edc .blk .charter div").count();
    console.log("3b) 记忆面板:", memH2.trim(), "· 明细行", rows);
  }

  // ── 截图（面板打开态 + 时间线态） ──
  await pg.click('.ai[title="轨迹"]');
  await pg.click("#side .it >> nth=0");
  await pg.waitForTimeout(300);
  await pg.screenshot({ path: "work/ide6_timeline.png" });
  await pg.keyboard.press("Control+p");
  await pg.fill("#palq", "DEV");
  await pg.waitForTimeout(250);
  await pg.screenshot({ path: "work/ide6_palette.png" });

  console.log("页面JS错误:", errs.length ? errs.join(" | ") : "无");
  await b.close();
})().catch(e => { console.error("TEST-ERR:", e.message); process.exit(1); });
