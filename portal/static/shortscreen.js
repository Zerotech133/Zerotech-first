/* shortscreen.js — 灵零全站长页短屏化（K-STOCK-055 L3）
 * 职责：
 *   1) 锚点条：页面 h2/h3 分区 ≥4 时，右上角悬浮锚点胶囊条，点击平滑跳转
 *   2) 折叠：h2 分区 ≥6 时自动把「h2+后续兄弟」包进 <details open>（默认展开，
 *      不改变首屏视觉，用户可逐区收起）+ 条上「收起/展开全部」
 * 安全纪律：
 *   - 幂等哨兵 window.__SS__，重复注入直接跳过
 *   - flex/grid/table 内的标题不折叠（布局守卫，只上锚点）
 *   - 纯 DOM 增强，不改任何数据流；样式用 tokens 令牌带 fallback
 */
(function () {
  if (window.__SS__) return; window.__SS__ = 1;
  function init() {
    var heads = [].slice.call(document.querySelectorAll("h2, h3")).filter(function (h) {
      var t = (h.textContent || "").trim();
      return t && t.length <= 40 && h.offsetParent !== null;
    });
    if (heads.length < 4) return;              // 短页/仪表盘不服务
    var FOLD = heads.filter(function (h) { return h.tagName === "H2"; }).length >= 6;

    /* ---- 锚点条 ---- */
    var bar = document.createElement("div");
    bar.id = "ss-bar";
    heads.forEach(function (h, i) {
      if (!h.id) h.id = "ss-sec-" + i;
      var a = document.createElement("a");
      a.className = "ss-chip";
      a.href = "#" + h.id;
      a.textContent = h.textContent.trim().slice(0, 10);
      a.title = h.textContent.trim();
      bar.appendChild(a);
    });
    var btn = null;
    if (FOLD) {
      btn = document.createElement("button");
      btn.className = "ss-chip ss-toggle";
      btn.type = "button";
      btn.textContent = "收起全部";
      bar.appendChild(btn);
    }
    document.body.appendChild(bar);

    /* ---- 折叠（布局守卫：flex/grid/table 父级跳过） ---- */
    var folded = [];
    if (FOLD) {
      heads.forEach(function (h) {
        if (h.tagName !== "H2") return;
        var p = h.parentElement;
        if (!p) return;
        var disp = "";
        try { disp = getComputedStyle(p).display; } catch (e) { return; }
        if (/flex|grid|table/.test(disp)) return;
        if (h.nextElementSibling === null && p.children.length === 1) return;
        var det = document.createElement("details");
        det.className = "ss-sec"; det.open = true;
        var sum = document.createElement("summary");
        p.insertBefore(det, h);
        sum.appendChild(h);                  // 真 h2 进 summary，语义保留
        det.appendChild(sum);
        var n = h.nextElementSibling;
        while (n) { var nx = n.nextElementSibling; det.appendChild(n); n = nx; }
        folded.push(det);
      });
      if (folded.length) {
        var allOpen = true;
        btn.addEventListener("click", function () {
          allOpen = !allOpen;
          folded.forEach(function (d) { d.open = allOpen; });
          btn.textContent = allOpen ? "收起全部" : "展开全部";
        });
      } else { btn.remove(); }
    }

    /* ---- 样式（令牌 + fallback） ---- */
    var css = document.createElement("style");
    css.textContent =
      "#ss-bar{position:fixed;top:10px;right:12px;z-index:9800;max-width:46vw;" +
      "display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end;" +
      "background:var(--bg-raised,rgba(22,27,34,.92));border:1px solid var(--border,#30363d);" +
      "border-radius:12px;padding:6px 8px;box-shadow:0 4px 14px rgba(0,0,0,.35);" +
      "font:12px/1.2 'Microsoft YaHei',sans-serif}" +
      ".ss-chip{color:var(--text-dim,#8b949e);text-decoration:none;padding:3px 8px;" +
      "border-radius:8px;border:1px solid transparent;cursor:pointer;background:none;" +
      "font:inherit}" +
      ".ss-chip:hover{color:var(--link,#58a6ff);border-color:var(--border-weak,#21262d);" +
      "background:var(--bg-surface,#161b22)}" +
      ".ss-toggle{color:var(--link,#58a6ff)}" +
      ".ss-sec>summary{cursor:pointer;list-style:none}" +
      ".ss-sec>summary::-webkit-details-marker{display:none}" +
      ".ss-sec>summary:hover h2{color:var(--link,#58a6ff)}" +
      "@media print{#ss-bar{display:none}}";
    document.head.appendChild(css);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
