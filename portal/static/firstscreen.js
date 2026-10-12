/* firstscreen.js — 零界报告页首屏结论条（宪法 v1.1 · UX 三定律「3 秒定则」落地件）
 * 职责：
 *   1) 从页面正文提取「结论候选」（.env 结论块 / section .kpi 指标组），克隆进首屏条
 *   2) 统计买卖倾向徽标（.buy/.up 涨、.avoid/.dn 跌、.watch/.watch 观察）
 *   3) 首屏条插在顶栏之后——滚动即走，不占纵向空间
 * 安全纪律：
 *   - 幂等哨兵 window.__FS__；无结论候选的短页自动跳过
 *   - 纯 DOM 增强（cloneNode），不改原数据流与样式
 *   - 样式用 tokens 令牌带 fallback；配 shortscreen.js 共存（锚点条在右上，本条在顶部）
 */
(function () {
  if (window.__FS__) return; window.__FS__ = 1;
  var TRIES = 0;
  function init() {
    /* 结论候选优先级：.env（premarket 综合倾向）→ section .grid / .kpi
       （signals/report KPI 组）→ .meta（精简壳兜底，如 signals「38 只·生成时间」）。
       正文结构随生成器版本变，无候选时重试（800ms×15≈12s），仍无则不插 */
    var pick = document.querySelector(".env, section .grid, .kpi, .meta");
    if (!pick) {
      if (TRIES++ < 15) setTimeout(init, 800);
      return;
    }
    if (document.getElementById("fs-bar")) return;

    /* 买卖倾向计数徽标 */
    var cnt = function (sel) {
      return document.querySelectorAll(sel).length;
    };
    var badges = [];
    var nBuy = cnt(".buy,.up"), nAvoid = cnt(".avoid,.dn"), nWatch = cnt(".watch,.mu");
    if (nBuy) badges.push('<span style="color:var(--up,#f85149);font-weight:700">买/涨 ' + nBuy + "</span>");
    if (nWatch) badges.push('<span style="color:var(--warn,#d29922);font-weight:700">观察 ' + nWatch + "</span>");
    if (nAvoid) badges.push('<span style="color:var(--down,#3fb950);font-weight:700">避/跌 ' + nAvoid + "</span>");

    var h1 = document.querySelector("h1");
    var bar = document.createElement("div");
    bar.id = "fs-bar";
    bar.style.cssText =
      "margin:0 0 14px;border:1px solid var(--border-weak,#58a6ff44);" +
      "border-left:4px solid var(--link,#58a6ff);border-radius:10px;" +
      "padding:10px 14px;background:var(--bg-surface,#161b22);" +
      "box-shadow:0 2px 10px rgba(0,0,0,.25)";

    var head =
      '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
      '<span style="font:800 11px/1 \'Microsoft YaHei\',sans-serif;letter-spacing:1px;' +
      'color:var(--link,#58a6ff);border:1px solid var(--link,#58a6ff);' +
      'border-radius:6px;padding:3px 8px">📌 结论</span>' +
      '<b style="font-size:14.5px">' + (h1 ? h1.textContent.trim() : document.title) + "</b>" +
      (badges.length
        ? '<span style="margin-left:auto;display:flex;gap:12px;font-size:12px">' +
          badges.join("") + "</span>"
        : "") +
      "</div>";

    var body = document.createElement("div");
    body.style.cssText = "margin-top:8px;font-size:13px";
    var clone = pick.cloneNode(true);
    /* 克隆块限高防长内容撑爆首屏条 */
    clone.style.cssText += ";max-height:180px;overflow:auto;margin:0";
    body.appendChild(clone);

    bar.appendChild(document.createElement("div")).innerHTML = head;
    bar.appendChild(body);

    /* 插入位置：nav.js 顶栏之后（顶栏是 body 首个子元素）；
       若顶栏不存在（未注入 nav），直接 body 最前 */
    var bar0 = document.body.firstElementChild;
    if (bar0 && bar0.style && bar0.style.cssText.indexOf("sticky") >= 0) {
      /* 找到顶栏的下一个兄弟作为锚，若无则 insertBefore 二号元素 */
      var anchor = bar0.nextElementSibling;
      if (anchor) document.body.insertBefore(bar, anchor);
      else document.body.appendChild(bar);
    } else {
      document.body.insertBefore(bar, document.body.firstChild);
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
