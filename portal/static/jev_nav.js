/* JEV 统一短链导航（2026-11-07）
 * 老田口径：能力就两条——① 可追溯的证据链 ② 叙事解读；链条不要太长，
 * 每一步都能返回。
 *
 * 用法：页面引入 <script src="/static/jev_nav.js" defer></script>
 * 自动在页头下方插入三步短链，并隐掉旧的多节点动线条（.crumb），
 * 使全族页面只有一条动线，不再各处各写一份。
 */
(function () {
  "use strict";

  var CHAIN = [
    { key: "pano", label: "① 全景", sub: "输代码 / 关键词", href: "/jev_pano.html" },
    { key: "evidence", label: "② 证据链", sub: "可追溯到原文", href: "/jev_evidence.html" },
    { key: "story", label: "③ 叙事", sub: "形态 · 量价 · 消息", href: "/jev_story.html" }
  ];

  var SECOND = [
    { key: "platform", label: "🛰 总览", href: "/jev_platform.html" },
    { key: "api", label: "📡 能力目录", href: "/jev_api.html" },
    { key: "wordcloud", label: "☁ 词云", href: "/jev_wordcloud.html" }
  ];

  var HERE = (location.pathname || "").toLowerCase();

  function activeKey() {
    if (HERE.indexOf("jev_evidence") >= 0) return "evidence";
    if (HERE.indexOf("jev_story") >= 0) return "story";
    if (HERE.indexOf("jev_target") >= 0) return "story";
    if (HERE.indexOf("jev_pano") >= 0) return "pano";
    if (HERE.indexOf("jev_wordcloud") >= 0) return "evidence";
    if (HERE.indexOf("jev_platform") >= 0) return "platform";
    if (HERE.indexOf("jev_api") >= 0) return "api";
    if (HERE.indexOf("jev_integration") >= 0) return "api";
    return "pano";
  }

  // 返回：优先浏览器历史；没有上一页就落到全景舱（不落空白页）
  function goBack(ev) {
    if (ev) ev.preventDefault();
    try {
      if (window.history.length > 1 && document.referrer) {
        window.history.back();
        return;
      }
    } catch (e) { /* ignore */ }
    location.href = "/jev_pano.html";
  }

  function css() {
    var s = document.createElement("style");
    s.textContent = [
      ".jnav{display:flex;flex-wrap:wrap;align-items:center;gap:8px;",
      "padding:8px 12px;margin:0 0 12px;border:1px solid var(--border-weak);",
      "border-radius:10px;background:var(--bg-raised);font-size:12px}",
      ".jnav .back{display:inline-flex;align-items:center;gap:4px;cursor:pointer;",
      "border:1px solid var(--border-weak);border-radius:999px;padding:3px 10px;",
      "background:transparent;color:var(--gray-6);font-size:12px;font-family:inherit}",
      ".jnav .back:hover{border-color:var(--accent);color:var(--link)}",
      ".jnav .step{display:inline-flex;flex-direction:column;gap:0;",
      "border:1px solid var(--border-weak);border-radius:8px;padding:3px 10px;",
      "color:var(--gray-6);text-decoration:none;line-height:1.25}",
      ".jnav .step b{font-weight:600;font-size:12.5px}",
      ".jnav .step i{font-style:normal;font-size:10px;color:var(--gray-5)}",
      ".jnav .step:hover{border-color:var(--accent);color:var(--link)}",
      ".jnav .step.here{border-color:var(--accent);background:var(--bg-page,transparent);",
      "color:var(--text);cursor:default}",
      ".jnav .step.here i{color:var(--text)}",
      ".jnav .arw{color:var(--gray-5);font-size:11px}",
      ".jnav .side{margin-left:auto;display:flex;gap:6px;align-items:center;flex-wrap:wrap}",
      ".jnav .side a{color:var(--gray-5);text-decoration:none;padding:2px 6px;border-radius:6px}",
      ".jnav .side a:hover{color:var(--link);background:var(--border-weak)}",
      ".jnav .side a.here{color:var(--text);background:var(--border-weak)}",
      ".jnav .ai{font-size:10.5px;color:var(--color-up);border:1px solid var(--color-up);",
      "border-radius:999px;padding:1px 8px;white-space:nowrap}",
      "@media (max-width:640px){.jnav .step i{display:none}.jnav .side{width:100%;margin-left:0}}"
    ].join("");
    document.head.appendChild(s);
  }

  function build() {
    var act = activeKey();
    var bar = document.createElement("nav");
    bar.className = "jnav";
    bar.setAttribute("aria-label", "JEV 动线（三步，可返回）");

    var h = '<button class="back" type="button" title="返回上一页（Alt+←）">← 返回</button>';
    CHAIN.forEach(function (c, i) {
      if (i) h += '<span class="arw">→</span>';
      h += '<a class="step' + (c.key === act ? " here" : "") + '" href="' + c.href + '">'
        + "<b>" + c.label + "</b><i>" + c.sub + "</i></a>";
    });
    h += '<span class="ai" title="每页内嵌：数据看完点「生成 AI 解读」出结论">🤖 AI 结论 · 页内</span>';

    h += '<span class="side">';
    SECOND.forEach(function (c) {
      h += '<a class="' + (c.key === act ? "here" : "") + '" href="' + c.href + '">'
        + c.label + "</a>";
    });
    h += "</span>";

    bar.innerHTML = h;

    // 定位插入点：页头之后；无页头则插到 main 首部
    var main = document.querySelector("main.ui-page") || document.querySelector("main");
    if (!main) return false;
    var head = main.querySelector("header.ui-page-head, header");
    if (head && head.parentNode === main) {
      main.insertBefore(bar, head.nextSibling);
    } else {
      main.insertBefore(bar, main.firstChild);
    }

    var bk = bar.querySelector(".back");
    if (bk) bk.addEventListener("click", goBack);
    document.addEventListener("keydown", function (e) {
      if (e.altKey && e.key === "ArrowLeft") goBack(e);
    });
    return true;
  }

  function hideOldCrumbs() {
    // 旧的多节点动线条（总览→判断→词云→复核→对接台→目录）一律下线，避免双动线
    var olds = document.querySelectorAll(".crumb");
    for (var i = 0; i < olds.length; i++) {
      var el = olds[i];
      if (el.classList.contains("jnav")) continue;
      el.parentNode && el.parentNode.removeChild(el);
    }
  }

  function init() {
    try {
      css();
      hideOldCrumbs();
      build();
    } catch (e) {
      // 导航失败绝不影响页面本身
      if (window.console) console.warn("[jev_nav]", e);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
