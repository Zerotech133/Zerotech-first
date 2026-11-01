/* 司马徽 · 今日决策台（只读）
   数据：GET /api/acad1/pool；不写库/不自动决策/不接实盘。 */
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));
const num = (v, d) => v == null ? "—" : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: d == null ? 2 : d });
const pct = (v) => v == null ? "—" : (Number(v) * 100).toFixed(1) + "%";
const ACTION = { buy: "ok", watch: "warn", avoid: "err", pending_analysis: "" };
let DATA = null;
let FILTER = "all";
let ACTION_FILTER = "all";
let SEARCH = "";

function statusOf(it) {
  return it.decision_status === "done" ? "已复盘" : "待复盘";
}

function rows() {
  let list = (DATA && DATA.items) || [];
  if (FILTER === "reviewed") list = list.filter((x) => x.decision_status === "done");
  if (FILTER === "pending") list = list.filter((x) => x.decision_status !== "done");
  if (ACTION_FILTER !== "all") list = list.filter((x) => x.action === ACTION_FILTER);
  if (SEARCH) {
    const q = SEARCH.toLowerCase();
    list = list.filter((x) => [x.code, x.name, (x.sources || []).join(" "), x.industry]
      .join(" ").toLowerCase().includes(q));
  }
  return list;
}

function renderKpi() {
  const d = DATA || {};
  const s = d.summary || {};
  const a = s.by_action || {};
  const reviewed = s.reviewed || 0;
  const pending = s.pending || 0;
  $("kpi-pool").textContent = s.pool_total == null ? "—" : s.pool_total;
  $("kpi-reviewed").textContent = reviewed;
  $("kpi-pending").textContent = pending;
  $("kpi-actions").textContent = `${a.watch || 0} / ${a.buy || 0}`;
  $("kpi-stale").textContent = d.stale ? "STALE" : "FRESH";
  $("kpi-date").textContent = d.data_date || "—";

  $("pool-count").textContent = s.pool_total == null ? "—" : s.pool_total;
  $("tab-all").textContent = s.pool_total == null ? "—" : s.pool_total;
  $("tab-reviewed").textContent = reviewed;
  $("tab-pending").textContent = pending;

  $("flow-data").textContent = d.data_date || "—";
  $("flow-candidates").textContent = s.pool_total == null ? "—" : s.pool_total;
  $("flow-pool").textContent = s.pool_total == null ? "—" : s.pool_total;
  $("flow-evidence").textContent = reviewed;
  $("flow-review").textContent = pending;

  $("run-title").textContent = `ACAD1 推荐池 · ${s.pool_total == null ? "—" : s.pool_total} 只`;
  $("run-sub").textContent = `已复盘 ${reviewed} · 待复盘 ${pending} · ${d.stale ? "数据过期" : "数据新鲜"}`;
  $("run-time").textContent = (d.generated_at || "").replace("T", " ").slice(0, 19) || "—";
  $("fresh-tag").textContent = d.stale ? "STALE · " + (d.freshness_days ?? "—") + "d" : "FRESH";
  $("live-tag").innerHTML = `<i class="dot"></i>${d.stale ? "STALE" : "LIVE"}`;
  $("pool-id").textContent = d.pool_id || "—";
  $("owner").textContent = d.owner || "—";
  $("gen-at").textContent = (d.generated_at || "").slice(11, 19) || "—";
  $("freshness").textContent = d.freshness_days == null ? "—" : d.freshness_days;
  $("mode-line").textContent = "只读 · trading_permitted=" + String(d.trading_permitted);

  const risks = {};
  (d.items || []).forEach((it) => (it.risk_flags || []).forEach((r) => { risks[r] = (risks[r] || 0) + 1; }));
  $("risk-count").textContent = Object.keys(risks).length;
  $("risk-list").innerHTML = Object.keys(risks).length
    ? Object.entries(risks).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k, n]) =>
      `<li><span class="risk-dot amber"></span><div><b>${esc(k)}</b><small>${n} 只</small></div><em>risk_flag</em></li>`).join("")
    : '<li class="empty-row">池内无 risk_flags</li>';

  $("error-banner").hidden = true;
}

function renderPool() {
  const list = rows();
  const total = ((DATA && DATA.summary) || {}).pool_total || 0;
  $("pool-rows").innerHTML = list.length ? list.map((r) => `
    <button class="pool-row" type="button" data-code="${esc(r.code)}">
      <span class="code">${esc(r.code)}</span>
      <span class="name">${esc(r.name || "—")}<small>${esc(r.industry || "")}</small></span>
      <span class="src">${esc((r.sources || []).join("+") || "—")}<small>${esc((r.risk_flags || []).join(" · ") || "无风险标记")}</small></span>
      <span class="rec">${esc(r.recommendation || "—")}</span>
      <span class="conf">${num(r.confidence, 2)}</span>
      <span class="price">${num(r.price, 2)}</span>
      <span class="act"><span class="badge ${ACTION[r.action] || ""}">${esc(r.action || "—")}</span></span>
    </button>`).join("") : '<div class="empty-row">当前筛选无匹配</div>';
  $("pool-summary").textContent = `显示 ${list.length} / ${total} 只 · 待复盘 ${((DATA && DATA.summary) || {}).pending ?? "—"}`;
}

function renderDrawer(r) {
  $("drawer-market").textContent = "ACAD1";
  $("drawer-title").textContent = r.code + " · " + (r.name || "—");
  $("drawer-name").textContent = r.industry || "—";
  $("drawer-meta").innerHTML = [
    `<span class="pill">${esc(r.tier || "—")}</span>`,
    `<span class="pill">${esc(r.recommendation || "—")}</span>`,
    `<span class="pill">${esc(r.action || "—")}</span>`,
    `<span class="pill">${esc(r.data_date || "—")}</span>`
  ].join("");
  $("drawer-reasons").innerHTML = `
    <div class="reason-item"><b>裁决</b><span>${esc(r.recommendation || "—")} · 置信 ${num(r.confidence, 2)} · 价格 ${num(r.price, 2)}</span></div>
    <div class="reason-item"><b>来源</b><span>${esc((r.sources || []).join(" + ") || "—")}</span></div>
    <div class="reason-item"><b>风险标记</b><span>${esc((r.risk_flags || []).join(" · ") || "无")}</span></div>`;
  const ana = r.analyses || {};
  const keys = Object.keys(ana);
  $("drawer-evidence").innerHTML = keys.length
    ? keys.map((k) => `<div class="evidence-item"><b>${esc(k)}</b><p>${esc(String(ana[k]).slice(0, 700))}</p></div>`).join("")
    : '<div class="muted">本次未返回多角色分析（可能待复盘）</div>';
  $("drawer-checks").innerHTML = `
    <li><span class="risk-dot ${DATA && DATA.stale ? "amber" : "green"}"></span>数据新鲜度：${DATA ? (DATA.stale ? "STALE" : "FRESH") : "—"}</li>
    <li><span class="risk-dot ${r.decision_status === "done" ? "green" : "amber"}"></span>复核状态：${statusOf(r)}</li>
    <li><span class="risk-dot green"></span>边界：只读研究，不自动下单/不自动通过</li>`;
  const drawer = $("drawer");
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  const scrim = $("scrim");
  scrim.hidden = false;
  requestAnimationFrame(() => scrim.classList.add("is-open"));
  $("drawer-close").focus();
}

function closeDrawer() {
  $("drawer").classList.remove("is-open");
  $("drawer").setAttribute("aria-hidden", "true");
  const scrim = $("scrim");
  scrim.classList.remove("is-open");
  setTimeout(() => { scrim.hidden = true; }, 180);
}

async function load() {
  try {
    const res = await fetch("/api/acad1/pool", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    DATA = await res.json();
    if (!DATA.ok) throw new Error(DATA.error || "bad payload");
    renderKpi();
    renderPool();
  } catch (e) {
    const banner = $("error-banner");
    banner.hidden = false;
    banner.textContent = "⚠ 读取 ACAD1 推荐池失败：" + String(e.message || e);
    $("run-title").textContent = "数据读取失败";
  }
}

function bind() {
  $("btn-refresh").addEventListener("click", load);
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    FILTER = b.dataset.filter;
    document.querySelectorAll(".tab").forEach((x) => {
      const on = x === b;
      x.classList.toggle("is-active", on);
      x.setAttribute("aria-selected", on ? "true" : "false");
    });
    renderPool();
  }));
  $("action-filter").addEventListener("change", (e) => { ACTION_FILTER = e.target.value; renderPool(); });
  $("search").addEventListener("input", (e) => { SEARCH = e.target.value.trim(); renderPool(); });
  $("pool-rows").addEventListener("click", (e) => {
    const row = e.target.closest(".pool-row");
    if (!row) return;
    const item = ((DATA && DATA.items) || []).find((x) => x.code === row.dataset.code);
    if (item) renderDrawer(item);
  });
  $("drawer-close").addEventListener("click", closeDrawer);
  $("scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
}

bind();
load();
setInterval(load, 60000);
