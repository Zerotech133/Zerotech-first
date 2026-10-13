/* K-STOCK-345 审计回归：342 验收清单全项复核 + send 全链路真实推理（9b） */
const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1500, height: 920 } });
  const errs = [];
  pg.on("pageerror", e => errs.push(String(e).slice(0, 200)));
  await pg.goto("http://127.0.0.1:8080/ide.html", { waitUntil: "networkidle" });
  let pass = 0, fail = 0;
  const chk = (name, ok, extra) => { console.log((ok ? "✅" : "❌"), name, extra||""); ok ? pass++ : fail++; };

  // ── 验收① 活动栏 5 视图 + 刷新记忆 ──
  for(const v of ["组织","代码","工单","记忆","轨迹"]){
    await pg.click(`.ai[title="${v}"]`);
    await pg.waitForTimeout(120);
    chk(`①视图切换[${v}]`, (await pg.locator(".ai.on").getAttribute("title")) === v);
  }
  await pg.click('.ai[title="代码"]');
  await pg.reload({ waitUntil: "networkidle" });
  await pg.waitForTimeout(400);
  chk("①刷新后视图记忆=代码", (await pg.locator(".ai.on").getAttribute("title")) === "代码");

  // ── 验收② 档案+代码 Tab 并存可关 ──
  await pg.click('.ai[title="组织"]');
  await pg.click('#side .it:has-text("DEV1")');
  await pg.click('.ai[title="代码"]');
  await pg.click('#side .it:has-text("run_shadow.py")');
  await pg.waitForTimeout(300);
  const tabN = await pg.locator(".tab").count();
  const h2 = await pg.locator("#edc h2").first().textContent().catch(()=>"");
  const code = await pg.locator("#edc .prompt-box").textContent().catch(()=>"");
  chk("②双Tab并存", tabN >= 2, `tabs=${tabN} · 当前内容${Math.max(h2.length?code.length:0)}字符`);
  await pg.locator(".tab.on .x").click();
  await pg.waitForTimeout(200);
  chk("②关闭当前Tab回退相邻", (await pg.locator(".tab").count()) === tabN - 1);

  // ── 验收③ 工单 Tab + 日志随 send 追加（send 见④） ──
  await pg.click('.ai[title="工单"]');
  await pg.click("#side .it >> nth=0");
  await pg.waitForTimeout(300);
  chk("③工单Tab出kv详情", (await pg.locator("#edc .kv .k").count()) >= 3);

  // ── 验收④ send 全链路真实推理（Ollama 9b·342 验收遗留项） ──
  await pg.click('.ai[title="组织"]');
  await pg.click('#side .it:has-text("DEV3")');
  await pg.waitForTimeout(300);
  const sendEnabled = await pg.locator("#send").isEnabled();
  chk("④选中Agent后send联锁启用", sendEnabled);
  const logBefore = (await pg.locator("#pbody div").count());
  await pg.fill("#q", "用一句话说明你的岗位职责（不超过30字）");
  await pg.click("#send");
  // 9b 推理最长 90s
  await pg.waitForFunction(() => !document.querySelector("#send").disabled, null, { timeout: 120000 });
  const bubble = await pg.locator(".msg.a .bubble").last().textContent();
  const logAfter = (await pg.locator("#pbody div").count());
  chk("④send全链路真实推理回复", bubble.length > 10 && !bubble.includes("调用失败"), `回复${bubble.length}字`);
  chk("④调试日志随send追加", logAfter > logBefore, `${logBefore}→${logAfter}行`);

  // ── 审计修复点复核：T 已不出现于轨迹侧栏 ──
  await pg.click('.ai[title="轨迹"]');
  await pg.waitForTimeout(200);
  const sideTxt = await pg.locator("#side").textContent();
  chk("审计FIX-B: 畸形轨迹T已过滤", !/\bT\b\s*2\s*动作/.test(sideTxt) && !(await pg.locator('#side .it:has-text("T")').count()),
      `轨迹条目${await pg.locator("#side .it").count()}`);

  console.log("页面JS错误:", errs.length ? errs.join(" | ") : "无");
  console.log(`=== 审计回归 ${pass} PASS / ${fail} FAIL ===`);
  await b.close();
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error("TEST-ERR:", e.message); process.exit(2); });
