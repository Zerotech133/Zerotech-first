/* 曹操军团 · 研发指挥台 v2（决策优先 · 只读）
   数据：GET /api/dev/legion（ADR-009 契约 v1；v2 extras：workorder_bus / decisions / events）
   纪律：只读；不写库、不派单、不合并、不新增写接口。 */

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

const DISCIPLINE = {
  backend: "后端", frontend: "前端", ai: "AI/模型", quant: "量化",
  devops: "运维/SRE", docs: "文档", architecture: "架构"
};

const API_V2 = "/api/dev/legion";
const EVENT_LIMIT = 8;  // 事件流最多显示最近 8 条
const DECISION_SNAPSHOT_AS_OF = "2026-11-01 15:35";
// 仅当 API 未返回 decisions 时才使用的人工快照（必须标注时点，不伪装实时）。
const DECISION_SNAPSHOT = [
  { title: "v1.1 批文 0/1", due: "11-02 12:00", owner: "ARCH1", high: true, desc: "12:00 已滑期；补签须注明实际时间，不倒签。", action: "补签/记滑期" },
  { title: "11 席回执 0/11", due: "11-03 09:30", owner: "ARCH1/G1/G2", high: false, desc: "11 席能力迭代回执仍为 0；未回=未完成。", action: "分组催办" },
  { title: "跨席工单回流", due: "今日", owner: "ARCH1", high: true, desc: "跨席工单在工单总线；由 ARCH1 决定回流规则。", action: "决定派工规则" }
];

let DATA = null;
let SEAT_WIP = {};
let WORK_FILTER = "all";

const STATUS_LABEL = { doing: "在制", review: "待复核", blocked: "阻塞", done: "已完成" };

function pill(text, cls) { return `<span class="pill ${cls || ""}">${esc(text)}</span>`; }

function _workRows() {
  if (DATA && Array.isArray(DATA.workorder_bus) && DATA.workorder_bus.length) {
    return DATA.workorder_bus.map((t) => ({ owner: t.owner, ...t }));
  }
  const rows = [];
  Object.keys(SEAT_WIP).forEach((owner) => {
    (SEAT_WIP[owner] || []).forEach((t) => rows.push({ owner, ...t }));
  });
  return rows;
}

function renderStatus() {
  const d = DATA || {};
  const m = d.metrics || {};
  const g = d.governance || {};
  const errors = d.errors || [];
  const bus = _workRows();
  const wip = Object.values(SEAT_WIP).reduce((a, b) => a + b, 0);
  $("run-time").textContent = (d.generated_at || "").replace("T", " ").slice(0, 19) || "—";
  $("run-banner").querySelector("strong").textContent =
    `近 7 日：${m.dev_tasks_recent ?? "—"} 任务 / ${m.dev_merged_recent ?? "—"} 合并 / 审批覆盖 ${g.approval_ok ? "PASS" : "未判定"}`;
  $("run-sub").textContent = `直属在制 ${wip} · 跨席总线 ${bus.length} 单(API) · 决策 ${((d.decisions || DECISION_SNAPSHOT).length)} 项 · ${errors.length ? "有数据源告警" : "无 API errors"}`;
  const banner = $("error-banner");
  if (errors.length) {
    banner.hidden = false;
    banner.textContent = "⚠ 数据源告警：" + errors.join("；");
  } else {
    banner.hidden = true;
    banner.textContent = "";
  }
  $("kpi-seats").textContent = (d.seats || []).length || "—";
  $("kpi-tasks").textContent = m.dev_tasks_recent ?? "—";
  $("kpi-merged").textContent = m.dev_merged_recent ?? "—";
  $("kpi-capture").textContent = "capture " + (m.dev_capture_share_recent == null ? "—" : (m.dev_capture_share_recent * 100).toFixed(2) + "%");
  $("kpi-approval").textContent = g.merge_approval_coverage == null ? "—" : (g.merge_approval_coverage * 100).toFixed(0) + "%";
  $("kpi-approval-sub").textContent = g.approval_ok ? "PASS" : "未判定";
  const rl = d.redlines || [];
  const pass = rl.filter((x) => x.ok === true).length;
  const unknown = rl.filter((x) => x.ok == null).length;
  $("kpi-redlines").textContent = `${pass}/${rl.length || "—"}`;
  $("kpi-redline-sub").textContent = `${pass} PASS · ${unknown} 未判定`;

  $("logic-bing").textContent = ((d.seats || []).length || "—") + " 席";
  $("logic-zhang").textContent = wip + " / " + bus.length;
  $("logic-men").textContent = g.approval_ok ? "PASS" : "待判定";
  $("logic-ling").textContent = ((d.decisions || DECISION_SNAPSHOT).length) + " 项";

  $("flow-tasks").textContent = m.dev_tasks_recent ?? "—";
  $("flow-wip").textContent = wip;
  $("flow-merged").textContent = m.dev_merged_recent ?? "—";

  $("m-lead").textContent = m.dev_leadtime_p50_min == null ? "—" : m.dev_leadtime_p50_min;
  $("m-capture").textContent = m.dev_capture_share_recent == null ? "—" : (m.dev_capture_share_recent * 100).toFixed(2) + "%";
  $("m-tasks").textContent = m.dev_tasks_recent ?? "—";
  $("m-merged").textContent = m.dev_merged_recent ?? "—";

  $("gate-approval").textContent = g.merge_approval_coverage == null ? "—" : (g.merge_approval_coverage * 100).toFixed(0) + "%";
  $("gate-approval").className = g.approval_ok ? "ok-text" : "warn-text";
  $("gate-owner").textContent = m.owner_legal_rate_recent == null ? "—" : (m.owner_legal_rate_recent * 100).toFixed(2) + "%";
  $("gate-e3").textContent = m.e3_trigger ?? "—";
  $("gate-verify").textContent = m.verification_rate == null ? "—" : (m.verification_rate * 100).toFixed(2) + "%";
  $("gate-tag").innerHTML = `<i class="dot"></i>${g.approval_ok ? "PASS" : "待判定"}`;

  $("redline-list").innerHTML = rl.length ? rl.map((r) => {
    const ok = r.ok === true ? "green" : r.ok === false ? "red" : "amber";
    const txt = r.ok === true ? "PASS" : r.ok === false ? "FAIL" : "未判定";
    return `<div><span class="risk-dot ${ok}"></span>${esc(r.id)} <em>${txt}</em></div>`;
  }).join("") : '<div class="muted">未取到红线</div>';
}

/* 首屏「今日必须拍板」：API decisions 优先；无 API 才回退人工快照并标时点。 */
function renderToday() {
  const items = (DATA && Array.isArray(DATA.decisions) && DATA.decisions.length)
    ? DATA.decisions : DECISION_SNAPSHOT;
  const apiDriven = !!(DATA && Array.isArray(DATA.decisions) && DATA.decisions.length);
  $("today-count").textContent = String(items.length);
  $("today-source").textContent = apiDriven
    ? "API v2 · decisions · 只读"
    : "人工快照 · as_of " + DECISION_SNAPSHOT_AS_OF;
  $("today-list").innerHTML = items.map((d, i) => `
    <article class="today-item ${d.high ? "high" : ""}">
      <div class="today-head">
        <span class="today-rank">${String(i + 1).padStart(2, "0")}</span>
        <b>${esc(d.title)}</b>
        <em class="today-due">${esc(d.due || "—")}</em>
      </div>
      <p class="today-desc">${esc(d.desc || "")}</p>
      <div class="today-foot">
        <span class="today-owner">owner：${esc(d.owner || "—")}</span>
        <a class="today-go" href="/hermes/" target="_blank" rel="noopener" title="只读跳转：处理仍走 devtask/Hermes">${esc(d.action || "去处理")} ↗</a>
      </div>
    </article>`).join("");
}

function renderSeats() {
  const seats = (DATA && DATA.seats) || [];
  $("seat-count").textContent = seats.length || "—";
  if (!seats.length) {
    $("seat-grid").innerHTML = '<div class="empty-row">未取到席位数据（API errors 见顶部）。</div>';
    return;
  }
  $("seat-grid").innerHTML = seats.map((s) => {
    const wip = SEAT_WIP[s.id] || 0;
    const caps = [DISCIPLINE[s.discipline] || s.discipline, s.group, s.grade].filter(Boolean);
    return `<button class="seat-card ${s.id === "ARCH1" ? "is-lead" : ""}" type="button" data-seat="${esc(s.id)}">
      <div class="seat-top"><span><span class="seat-id">${esc(s.id)}</span><span class="seat-name">${esc(s.name)}</span></span><span class="seat-grade">${esc(s.grade || "—")}</span></div>
      <div class="seat-role">${esc(s.role_title || "—")}</div>
      <div class="seat-foot"><span class="seat-status ${wip ? "" : "idle"}">${wip ? "在制 " + wip : "在册/可派工"}</span><span class="seat-last">→ ${esc(s.report_to || "—")}</span></div>
      <div class="seat-tags">${caps.map((c) => `<span class="seat-tag">${esc(c)}</span>`).join("")}</div>
    </button>`;
  }).join("");
}

function renderDecisions() {
  const items = (DATA && Array.isArray(DATA.decisions) && DATA.decisions.length)
    ? DATA.decisions : DECISION_SNAPSHOT;
  const apiDriven = !!(DATA && Array.isArray(DATA.decisions) && DATA.decisions.length);
  $("decision-count").textContent = String(items.length);
  $("decision-help").textContent = apiDriven
    ? "来源：API decisions（v2）；只读，不代签、不写库。"
    : "人工快照 · as_of " + DECISION_SNAPSHOT_AS_OF + "；待 API v2 接入后改为实时。";
  $("decision-list").innerHTML = items.map((d) => `
    <div class="decision-item ${d.high ? "high" : ""}">
      <div class="decision-top"><b>${esc(d.title)}</b><em>${esc(d.due)}</em></div>
      <p>${esc(d.desc)}</p>
      <div class="decision-foot"><span>${esc(d.owner)}</span><span class="audit-flag">${apiDriven ? "API v2" : "人工快照 · " + esc(DECISION_SNAPSHOT_AS_OF.slice(11))}</span></div>
    </div>`).join("");
}

/* 工单区：真实 DATA.workorder_bus（只读）+ 全部/在制/待复核筛选；空态明确。 */
function renderWip() {
  const all = _workRows();
  const doing = all.filter((w) => w.status === "doing");
  const review = all.filter((w) => w.status === "review");
  $("work-count").textContent = String(all.length);
  $("tab-all-count").textContent = String(all.length);
  $("tab-doing-count").textContent = String(doing.length);
  $("tab-review-count").textContent = String(review.length);

  const rows = WORK_FILTER === "doing" ? doing
    : WORK_FILTER === "review" ? review : all;
  if (!rows.length) {
    $("work-rows").innerHTML = `<div class="empty-row">当前筛选（${WORK_FILTER === "all" ? "全部" : STATUS_LABEL[WORK_FILTER] || WORK_FILTER}）无工单；总线共 ${all.length} 单（在制 ${doing.length} / 待复核 ${review.length}）。</div>`;
  } else {
    $("work-rows").innerHTML = rows.map((w) => {
      const label = STATUS_LABEL[w.status] || esc(w.status || "—");
      const cls = w.status === "doing" ? "doing" : w.status === "review" ? "review" : "other";
      return `
      <div class="work-row">
        <span class="work-status ${cls}">${label}</span>
        <span class="work-task"><span class="work-id">${esc(w.id)}</span><small class="work-desc">${esc(w.desc || "")}</small></span>
        <span class="work-owner">${esc(w.owner || "—")}</span>
        <span class="work-age">${w.created ? esc(String(w.created).slice(5, 10)) : "—"}</span>
        <span class="work-next">${w.status === "doing" ? "继续开发" : w.status === "review" ? "待复核" : "待处理"}</span>
      </div>`;
    }).join("");
  }
  $("work-foot").textContent = `来源：/api/dev/legion workorder_bus（${all.length} 单 · 筛选：${WORK_FILTER === "all" ? "全部" : STATUS_LABEL[WORK_FILTER] || WORK_FILTER}）`;
}

/* 事件流：API events（10 条），只显示最近 8 条；字段 ts/kind/agent/task。 */
function renderEvents() {
  const events = (DATA && Array.isArray(DATA.events)) ? DATA.events : [];
  const latest = events.slice(-EVENT_LIMIT).reverse();
  $("event-count").textContent = events.length ? `${latest.length}/${events.length}` : "0";
  $("event-list").innerHTML = latest.length
    ? latest.map((e) => `
      <li class="event-row">
        <span class="event-kind">${esc(e.kind || "—")}</span>
        <span class="event-ts">${esc(e.ts || "—")}</span>
        <span class="event-agent">${esc(e.agent || "—")}</span>
        <span class="event-task" title="${esc(e.task || "")}">${esc(e.task || "—")}</span>
      </li>`).join("")
    : '<li class="event-empty">暂无事件（API events 为空）· 不伪造、不补造。</li>';
}

function setWorkFilter(next) {
  WORK_FILTER = ["all", "doing", "review"].includes(next) ? next : "all";
  document.querySelectorAll("[data-work-filter]").forEach((btn) => {
    const active = btn.dataset.workFilter === WORK_FILTER;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  renderWip();
}

function renderDrawer(s) {
  const wip = SEAT_WIP[s.id] || [];
  $("drawer-market").textContent = s.id;
  $("drawer-title").textContent = s.name || s.id;
  $("drawer-name").textContent = s.role_title || "—";
  $("drawer-meta").innerHTML = [pill("在册", "ok"), pill(s.group || "—"), pill(s.grade || "—"), pill(s.model || "—")].join("");
  $("drawer-caps").innerHTML = [DISCIPLINE[s.discipline] || s.discipline, s.org_role, s.group, s.grade].filter(Boolean)
    .map((c) => `<span class="cap-tag">${esc(c)}</span>`).join("");
  $("drawer-state").innerHTML = `
    <div class="reason-item"><b>直属在制</b><span>${wip.length} 单</span></div>
    <div class="reason-item"><b>汇报线</b><span>${esc(s.report_to || "—")}</span></div>
    <div class="reason-item"><b>模型</b><span>${esc(s.model || "—")}</span></div>`;
  $("drawer-assign").textContent = wip.length
    ? "当前有直属在制，优先完成；跨席工单是否回流由 ARCH1 决定。"
    : "当前无直属在制，可直接派工；建议按 G1/G2 专业匹配。";
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
    const res = await fetch(API_V2, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    DATA = await res.json();
    const v2 = DATA.v2 || {};
    DATA.api_v2 = !!(v2.readonly || Array.isArray(v2.extras)) ||
      Array.isArray(DATA.workorder_bus) || Array.isArray(DATA.decisions) || Array.isArray(DATA.events);
    SEAT_WIP = {};
    (DATA.work_in_progress || []).forEach((x) => {
      if (x && x.owner) SEAT_WIP[x.owner] = x.tasks || [];
    });
    renderStatus();
    renderToday();
    renderSeats();
    renderDecisions();
    setWorkFilter(WORK_FILTER);
    renderEvents();
  } catch (e) {
    $("error-banner").hidden = false;
    $("error-banner").textContent = "⚠ 读取 /api/dev/legion 失败：" + String(e.message || e);
    $("run-banner").querySelector("strong").textContent = "数据读取失败";
  }
}

$("btn-refresh").addEventListener("click", () => load());
$("work-tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-work-filter]");
  if (btn) setWorkFilter(btn.dataset.workFilter);
});
$("seat-grid").addEventListener("click", (e) => {
  const card = e.target.closest(".seat-card");
  if (!card) return;
  const s = ((DATA && DATA.seats) || []).find((x) => x.id === card.dataset.seat);
  if (s) renderDrawer(s);
});
$("drawer-close").addEventListener("click", closeDrawer);
$("scrim").addEventListener("click", closeDrawer);
$("drawer-copy").addEventListener("click", () => {
  const s = ((DATA && DATA.seats) || []).find((x) => x.id === $("drawer-market").textContent);
  if (s) navigator.clipboard?.writeText(`${s.id} ${s.name} ${s.role_title} ${s.group || ""} ${s.grade || ""} ${s.model || ""}`).catch(() => {});
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

load();
setInterval(load, 60000);
