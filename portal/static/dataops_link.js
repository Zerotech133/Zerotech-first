/* 全站指挥连接条（dataops_link.js）
 * 用法：页面 </body> 前加一行——
 *   <script src="/static/dataops_link.js" data-entity="pipe:classify" defer></script>
 * 效果：右上角固定一个健康 pill，实时显示本页对应实体状态；
 *       点击 → 跳转 /dataops.html?entity=<id> 打开拓扑+下钻抽屉。
 * 契约：GET /api/dataops/entity?id=<id>（统一 schema，fail-visible）。
 * 无 data-entity 的页面自动跳过（零侵入）；API 不可达时静默隐藏（不干扰业务页）。
 */
(function () {
  var me = document.currentScript;
  var nid = me && me.getAttribute('data-entity');
  if (!nid) return;
  var API = 'http://127.0.0.1:8091/api/dataops/entity?id=' + encodeURIComponent(nid);
  var el = document.createElement('div');
  el.id = 'dops-link';
  el.title = '点击下钻：' + nid;
  el.style.cssText = 'position:fixed;top:10px;right:12px;z-index:999;display:none;align-items:center;gap:6px;' +
    'padding:4px 12px;border-radius:14px;font:12px/1.4 "Microsoft YaHei",sans-serif;cursor:pointer;' +
    'background:rgba(22,27,34,.92);border:1px solid #2d333b;color:#e6edf3;box-shadow:0 2px 10px rgba(0,0,0,.35)';
  document.body.appendChild(el);
  function paint(h, name) {
    var c = { ok: '#3fb950', running: '#58a6ff', complete: '#3fb950', done: '#bc8cff',
              yielding: '#d29922', backoff: '#d29922', blocked: '#f85149', down: '#f85149',
              unknown: '#8b949e', disabled: '#8b949e' }[h] || '#8b949e';
    el.innerHTML = '<span style="width:8px;height:8px;border-radius:50%;background:' + c + ';display:inline-block"></span>' +
      '<span style="color:#8b949e">数据运营</span><b>' + (name || nid) + '</b>' +
      '<span style="color:' + c + '">' + h + '</span>';
    el.style.display = 'flex';
  }
  function load() {
    var x = new XMLHttpRequest();
    x.open('GET', API, true);
    x.timeout = 5000;
    x.onload = function () {
      try {
        var d = JSON.parse(x.responseText);
        if (d.status) paint(d.status.health, d.name);
      } catch (e) { /* fail-silent：连接条不干扰业务页 */ }
    };
    x.onerror = function () { el.style.display = 'none'; };
    x.ontimeout = function () { el.style.display = 'none'; };
    x.send();
  }
  el.onclick = function () {
    window.open('/dataops.html?entity=' + encodeURIComponent(nid), '_blank');
  };
  load();
  setInterval(load, 60000);
})();
