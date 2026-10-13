/* K-STOCK-346 一期骨架验收：跨工作区隔离 + 调试台多会话 + 刷新记忆
   判例执行：深层断言（断到内容存在性，不只布局）+ 零页面 JS 错误 */
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

  // 干净起点
  await pg.goto(BASE, { waitUntil: "domcontentloaded" });
  await pg.evaluate(() => localStorage.clear());
  await pg.reload({ waitUntil: "networkidle" });
  await pg.waitForSelector(".wsbtn", { timeout: 8000 });

  // 1) 工作区切换器渲染：全部 + 3 项目部门
  const wsN = await pg.locator("#wsbar .wsbtn").count();
  ok("1a) wsbar 渲染（全部+项目部门）", wsN >= 4, wsN + " 按钮");
  const onTxt = await pg.locator("#wsbar .wsbtn.on").textContent();
  ok("1b) 默认工作区=全部", onTxt.includes("全部"), onTxt.trim());

  // 2) 全部视图：org 侧栏成员数 = 全部部门成员
  const allN = await pg.locator('#side .it').count();
  ok("2) 全部视图侧栏有条目", allN > 10, allN + " 条");

  // 3) 切到第一个项目工作区
  await pg.locator("#wsbar .wsbtn").nth(1).click();
  await pg.waitForTimeout(300);
  const onTxt2 = await pg.locator("#wsbar .wsbtn.on").textContent();
  ok("3a) 工作区已切换", !onTxt2.includes("全部"), onTxt2.trim());
  const wsMembers = await pg.evaluate(() => {
    const d = B.departments.find(x => x.name === WS);
    return d ? d.agents.length : -1;
  });
  const sideAgentN = await pg.locator('#side .it').count();
  ok("3b) 侧栏按工作区过滤成员", sideAgentN === wsMembers, sideAgentN + "/" + wsMembers);
  const logTxt = await pg.locator("#pbody").textContent();
  ok("3c) 日志记录切换+按ws分流", logTxt.includes("工作区 →"), "");

  // 4) Tab 集隔离：ws-A 开 2 个 Tab → 切 ws-B 应为空 → 切回恢复
  const firstAgent = await pg.locator("#side .it .rid").first().textContent();
  await pg.locator("#side .it").first().click();          // 开成员 Tab
  await pg.waitForTimeout(200);
  await pg.click('.ai[title="工单"]');                     // 工单视图再开一个工单 Tab
  await pg.waitForTimeout(150);
  const taskIt = pg.locator("#side .it").first();
  if(await taskIt.count()) await taskIt.click();
  await pg.waitForTimeout(200);
  const tabsA = await pg.locator("#tabs .tab").count();
  ok("4a) ws-A 开出 Tab", tabsA >= 1, tabsA + " 个");

  await pg.locator("#wsbar .wsbtn").nth(2).click();        // 切 ws-B
  await pg.waitForTimeout(300);
  const tabsB = await pg.locator("#tabs .tab").count();
  ok("4b) 切工作区 Tab 集隔离", tabsB === 0, "ws-B " + tabsB + " 个（ws-A 有 " + tabsA + "）");

  await pg.locator("#wsbar .wsbtn").nth(1).click();        // 切回 ws-A
  await pg.waitForTimeout(300);
  const tabsBack = await pg.locator("#tabs .tab").count();
  ok("4c) 切回 Tab 现场恢复", tabsBack === tabsA, tabsBack + " 个");

  // 5) 调试台多会话：A1 说 hello → 切 A2（chat 应空）→ 切回 A1（历史在）——在 ws-A 下进行
  await pg.evaluate(() => { Object.keys(SESSIONS).forEach(k => { delete SESSIONS[k];
    try{ localStorage.removeItem(wsKey("ide-chat@"+k)); }catch(e){} }); });
  const agentIds = await pg.evaluate(() => {
    const d = B.departments.find(x => x.name === WS);
    return d ? d.agents.slice(0, 2) : [];
  });
  if(agentIds.length >= 2){
    await dbgSel(pg, agentIds[0]);
    await pg.evaluate(a => { DBGCUR = B.agents[a]; addMsg("u", a + " 的测试指令", "hello-from-" + a); }, agentIds[0]);
    await dbgSel(pg, agentIds[1]);
    const chatB = await pg.locator("#chat").textContent();
    ok("5a) 切另一 Agent 会话隔离（不含前人消息）", !chatB.includes("hello-from-" + agentIds[0]), chatB.slice(0, 40));
    await dbgSel(pg, agentIds[0]);
    const chatBack = await pg.locator("#chat").textContent();
    ok("5b) 切回会话历史保留", chatBack.includes("hello-from-" + agentIds[0]), "");
    // 会话条 chips
    const chips = await pg.locator(".schip").count();
    ok("5c) 会话条渲染", chips >= 2, chips + " chips");
    // 刷新后会话还在（localStorage 持久化）
    await pg.reload({ waitUntil: "networkidle" });
    await pg.waitForTimeout(500);
    const chatReload = await pg.locator("#chat").textContent();
    const wsKept = await pg.locator("#wsbar .wsbtn.on").textContent();
    ok("5d) 刷新后工作区记忆", !wsKept.includes("全部"), wsKept.trim());
    ok("5e) 刷新后会话历史恢复", chatReload.includes("hello-from-" + agentIds[0]), "");
  } else {
    ok("5) 工作区成员不足 2 跳过多会话断言", false, JSON.stringify(agentIds));
  }

  // 6) 切回全部：Tab 集独立 + 侧栏恢复全量
  await pg.locator("#wsbar .wsbtn").nth(0).click();
  await pg.waitForTimeout(300);
  const sideAll = await pg.locator("#side .it").count();
  ok("6) 切回全部视图恢复", sideAll >= allN, sideAll + " 条（初始 " + allN + "）");

  // 7) 零页面 JS 错误
  ok("7) 零页面 JS 错误", errs.length === 0, errs.join(" | ").slice(0, 160));

  await pg.screenshot({ path: "work/ide7_ws.png" });
  await b.close();
  console.log("\n=== " + PASS + " PASS / " + FAIL + " FAIL ===");
  process.exit(FAIL ? 1 : 0);

  async function dbgSel(pg, id){ await pg.evaluate(a => dbgSelect(a), id); await pg.waitForTimeout(120); }
})().catch(e => { console.error("SCRIPT ERR:", e.message); process.exit(2); });
