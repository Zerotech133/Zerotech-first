/* K-STOCK-348 二期验收：工单看板 + 身份视角 + 分屏编辑器（判例：深层断言+零JS错误） */
const { chromium } = require("playwright");
const BASE = "http://127.0.0.1:8080/ide.html";
let PASS = 0, FAIL = 0;
function ok(name, cond, extra){
  if(cond){ PASS++; console.log("  PASS", name, extra!==undefined?("· "+extra):""); }
  else { FAIL++; console.log("  FAIL", name, extra!==undefined?("· "+extra):""); }
}
(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1560, height: 940 } });
  const errs = [];
  pg.on("pageerror", e => errs.push(String(e).slice(0,200)));

  await pg.goto(BASE, { waitUntil: "domcontentloaded" });
  await pg.evaluate(() => localStorage.clear());
  await pg.reload({ waitUntil: "networkidle" });
  await pg.waitForSelector(".wsbtn", { timeout: 8000 });

  // 1) 看板：🗂 视图 → 单例 Tab → 列 ≥4（3 项目部门+职能未分配）·卡片可点开
  await pg.click('.ai[title="看板"]');
  await pg.waitForTimeout(400);
  ok("1a) 看板 Tab 打开", await pg.locator('#tabs .tab:has-text("工单看板")').count() === 1, "");
  const colN = await pg.locator("#edc .bcol").count();
  ok("1b) 看板列（3部门+职能）", colN >= 4, colN + " 列");
  const cardN = await pg.locator("#edc .card").count();
  ok("1c) 卡片渲染", cardN > 0, cardN + " 卡");
  if(cardN){
    await pg.locator("#edc .card").first().click();
    await pg.waitForTimeout(250);
    ok("1d) 点卡片开工单 Tab", await pg.locator('#tabs .tab:has-text("📋")').count() >= 1, "");
  }

  // 2) 身份视角：切 DEV3 → 工单视图「我的工单」分区 + 侧栏★标
  const meAgent = await pg.evaluate(() => {
    for(const id in B.agents){
      const t = (B.workorders||[]).filter(w=>w.owner===id);
      if(t.length) return id;
    }
    return "";
  });
  await pg.evaluate(id => setMe(id), meAgent);
  await pg.click('.ai[title="工单"]');
  await pg.waitForTimeout(300);
  const sideTxt = await pg.locator("#side").textContent();
  ok("2a) 工单视图「我的工单」分区", sideTxt.includes("我的工单") && sideTxt.includes(meAgent), meAgent);
  await pg.click('.ai[title="组织"]');
  await pg.waitForTimeout(300);
  ok("2b) 组织侧栏 ★我 标记", (await pg.locator("#side").textContent()).includes("★我"), "");
  const meSel = await pg.evaluate(() => document.getElementById("me").value);
  ok("2c) 身份选择器回显", meSel === meAgent, meSel);

  // 3) 分屏：开档案 Tab（左） → 开分屏 → 右栏选代码文件 → 左档案右代码同屏
  await pg.evaluate(() => setMe(""));
  await pg.click('.ai[title="组织"]');
  await pg.waitForTimeout(200);
  await pg.locator("#side .it").first().click();     // 左：档案
  await pg.waitForTimeout(400);
  await pg.click("#splitbtn");                        // 开分屏
  await pg.waitForTimeout(200);
  ok("3a) 分屏右栏显示", await pg.locator("#edc2").isVisible(), "");
  const codePath = await pg.evaluate(() => B.code_files[0].path);
  await pg.evaluate(p => splitPick("code:" + p), codePath);
  await pg.waitForTimeout(300);
  const rightTxt = await pg.locator("#edc2").textContent();
  ok("3b) 右栏代码渲染", rightTxt.includes(codePath.split("/").pop()), codePath.split("/").pop());
  ok("3c) 左档案仍在（同屏）", await pg.locator("#edc .a-head h2").first().isVisible(), "");
  // 右栏选 Agent 不抢调试目标
  const dbgBefore = await pg.evaluate(() => DBGCUR && DBGCUR.id);
  const another = await pg.evaluate(() => { for(const id in B.agents){ if(!DBGCUR || id!==DBGCUR.id) return id; } return ""; });
  await pg.evaluate(id => splitPick("agent:" + id), another);
  await pg.waitForTimeout(300);
  const dbgAfter = await pg.evaluate(() => DBGCUR && DBGCUR.id);
  ok("3d) 分屏右栏档案不抢调试目标", dbgBefore === dbgAfter, dbgBefore + "→" + dbgAfter);

  // 4) 刷新：分屏/身份(空)/看板恢复
  await pg.reload({ waitUntil: "networkidle" });
  await pg.waitForTimeout(600);
  ok("4a) 刷新后分屏恢复", await pg.locator("#edc2").isVisible(), "");
  ok("4b) 刷新后右栏内容恢复", (await pg.locator("#edc2").textContent()).includes(another), "");
  ok("4c) 刷新后 Tab 现场（含看板）", (await pg.locator("#tabs .tab").count()) >= 2, "");

  // 5) 零 JS 错误
  ok("5) 零页面 JS 错误", errs.length === 0, errs.join(" | ").slice(0,160));

  await pg.screenshot({ path: "work/ide8_split_board.png" });
  await b.close();
  console.log("\n=== " + PASS + " PASS / " + FAIL + " FAIL ===");
  process.exit(FAIL ? 1 : 0);
})().catch(e => { console.error("SCRIPT ERR:", e.message); process.exit(2); });
