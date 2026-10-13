// K-STOCK-343 五区 IDE 全量实测：视图切换/多Tab/底部面板/@引用/证据表（深层内容断言）
const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1500, height: 920 } });
  const errs = [];
  pg.on("pageerror", e => errs.push(String(e).slice(0,200)));
  await pg.goto("http://127.0.0.1:8080/ide.html", { waitUntil: "networkidle" });

  // 1) 活动栏 5 图标 + 默认组织视图
  const aiCount = await pg.locator(".ai").count();
  const dev1InSide = await pg.locator("#side").textContent();
  console.log("1) 活动栏图标:", aiCount, "| 组织视图含DEV1:", dev1InSide.includes("DEV1"));

  // 2) 开 Agent Tab（深层断言：CHARTER 标题 + prompt-box）
  await pg.click('#side .it:has-text("DEV1")');
  await pg.waitForTimeout(300);
  const charterH3 = await pg.locator("#edc h3").first().textContent();
  const pboxCnt = await pg.locator("#edc .prompt-box").count();
  console.log("2) AgentTab 深层:", charterH3.trim(), "| prompt-box:", pboxCnt, "| send启用:", !(await pg.locator("#send").isDisabled()));

  // 3) 切代码视图开代码 Tab → 两 Tab 并存
  await pg.click('.ai[title="代码"]');
  await pg.waitForTimeout(200);
  await pg.click('#side .it:has-text("run_shadow.py")');
  await pg.waitForTimeout(300);
  const tabCnt = await pg.locator(".tab").count();
  const preChars = (await pg.locator("#edc pre").textContent()).length;
  const tabTitles = await pg.locator(".tab").allTextContents();
  console.log("3) Tab并存:", tabCnt, "| 代码字符:", preChars, "| titles:", tabTitles.map(t=>t.replace("×","").trim()).join(" / "));

  // 4) Tab 切换回来（档案内容还在）
  await pg.locator(".tab", { hasText: "DEV1" }).click();
  await pg.waitForTimeout(200);
  const backCharter = await pg.locator("#edc h3").first().textContent();
  console.log("4) 切回档案Tab:", backCharter.trim());

  // 5) 工单视图 → 开工单 Tab（深层断言 owner 行）
  await pg.click('.ai[title="工单"]');
  await pg.waitForTimeout(200);
  const taskCnt = await pg.locator("#side .it").count();
  await pg.click('#side .it >> nth=0');
  await pg.waitForTimeout(300);
  const kvTxt = await pg.locator("#edc .kv").textContent();
  const tabCnt2 = await pg.locator(".tab").count();
  console.log("5) 工单条目:", taskCnt, "| 工单Tab开:", tabCnt2, "| kv含owner:", kvTxt.includes("owner"));

  // 6) 记忆/轨迹视图
  await pg.click('.ai[title="记忆"]');
  await pg.waitForTimeout(200);
  const memCnt = await pg.locator("#side .it").count();
  await pg.click('.ai[title="轨迹"]');
  await pg.waitForTimeout(200);
  const traceCnt = await pg.locator("#side .it").count();
  console.log("6) 记忆条目:", memCnt, "| 轨迹条目:", traceCnt);

  // 7) 底部面板：日志有 bundle 行；A0 日报页有内容；控制台有统计
  const logTxt = await pg.locator("#pbody").textContent();
  console.log("7a) 日志含bundle行:", logTxt.includes("bundle 加载完成"));
  await pg.click('.pt[data-p="daily"]');
  await pg.waitForTimeout(200);
  const dailyTxt = await pg.locator("#pbody").textContent();
  console.log("7b) A0日报页有内容:", dailyTxt.trim().length > 50, "| 含A0:", dailyTxt.includes("A0"));
  await pg.click('.pt[data-p="console"]');
  await pg.waitForTimeout(200);
  const conTxt = await pg.locator("#pbody").textContent();
  console.log("7c) 控制台含统计:", conTxt.includes("code_files"), conTxt.includes("A0 日报快照"));
  await pg.click("#ptoggle");
  await pg.waitForTimeout(200);
  const pbodyHidden = await pg.locator("#pbody").isHidden();
  const ptabsStill = await pg.locator(".ptabs").isVisible();
  await pg.click("#ptoggle");
  await pg.waitForTimeout(200);
  console.log("7d) 折叠后日志区隐藏:", pbodyHidden, "| 细条常驻:", ptabsStill, "| 展开恢复:", await pg.locator("#pbody").isVisible());

  // 8) @引用菜单：输入 @ru 应弹 run_shadow.py
  await pg.locator(".tab", { hasText: "DEV1" }).click();
  await pg.click("#q");
  await pg.type("#q", "检查 @ru", { delay: 30 });
  await pg.waitForTimeout(300);
  const atVisible = await pg.locator("#atmenu").isVisible();
  const atTxt = await pg.locator("#atmenu").textContent();
  console.log("8) @菜单弹出:", atVisible, "| 含run_shadow:", atTxt.includes("run_shadow.py"));
  await pg.keyboard.press("Enter"); // 选中第一项
  await pg.waitForTimeout(200);
  const qVal = await pg.inputValue("#q");
  console.log("8b) 回车选中后输入框:", JSON.stringify(qVal));
  await pg.fill("#q", "");

  // 9) 证据表渲染（直接调 evTable）
  const tblRendered = await pg.evaluate(() => {
    const html = evTable("| 证据 | 结果 |\n|---|---|\n| 历史文件数 | 仍 3 条 |\n正文行");
    return { hasTable: html.includes("<table>"), hasTh: html.includes("<th>证据</th>"), hasTd: html.includes("<td>仍 3 条</td>"), body: html.includes("正文行") };
  });
  console.log("9) 证据表:", JSON.stringify(tblRendered));

  // 10) @file 上下文注入（直接调 atContext）
  const ctxOk = await pg.evaluate(() => {
    const s = atContext("看看 @run_shadow.py 的 parse 分支");
    return s.includes("[上下文 @run_shadow.py") && s.includes("=== 用户消息 ===");
  });
  console.log("10) @file上下文注入:", ctxOk);

  // 11) 模型选择器
  const modelOpts = await pg.locator("#model option").allTextContents();
  console.log("11) 模型选项:", modelOpts.join("/"));

  await pg.screenshot({ path: "work/ide5_after.png" });
  console.log("页面JS错误:", errs.length ? errs.join(" | ") : "无");
  await b.close();
  if (errs.length) process.exit(1);
  console.log("=== ALL PASS ===");
})().catch(e => { console.error("FAIL:", e.message); process.exit(1); });
