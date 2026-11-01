/* 曹操军团 · 研发指挥台 v2（只读）
   数据：GET /api/dev/legion（ADR-009 契约 v1）
   不写接口、不派单、不合并。 */

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

const DISCIPLINE = {
  backend: "后端", frontend: "前端", ai: "AI/模型", quant: "量化",
  devops: "运维/SRE", docs: "文档", architecture: "架构"
};

// 治理状态为人工维护快照；API v1 未暴露决策/跨席工单接口。
const DECISIONS_AS_OF = "2026-11-01 15:35";
const DECISIONS = [
  { title: "v1.1 批文 0/1", due: "11-02 12:00", owner: "ARCH1", high: true, desc: "12:00 已滑期；补签须注明实际时间，不倒签。", action: "补签/记滑期" },
  { title: "11 席回执 0/11", due: "11-03 09:30", owner: "ARCH1/G1/G2", high: false, desc: "11 席能力迭代回执仍为 0；未回=未完成。", action: "分组催办" },
  { title: "跨席 16 单未回流", due: "今日", owner: "ARCH1", high: true, desc: "研发部直属在制 0；跨席总额为人工快照，待工单 API 接入。", action: "决定派工规则" }
];
const CROSS_SEAT_SNAPSHOT = 16;

let DATA = null;
let SEAT_WIP = {};

function pill(text, cls) { return `<span class="pill ${cls || ""}">${esc(text)}</span>`; }

function renderStatus() {
  const d = DATA || {};
  const m = d.metrics || {};
  const g = d.governance || {};
  const errors = d.errors || [];
  $("run-time").textContent = (d.generated_at || "").replace("T", " ").slice(0, 19) || "—";
  $("run-banner").querySelector("strong").textContent =
    `近 7 日：${m.dev_tasks_recent ?? "—"} 任务 / ${m.dev_merged_recent ?? "—"} 合并 / 审批覆盖 ${g.approval_ok ? "PASS" : "未判定"}`;
  $("run-sub").textContent = `直属在制 ${Object.values(SEAT_WIP).reduce((a, b) => a + b, 0)} · 跨席快照 ${CROSS_SEAT_SNAPSHOT} · ${errors.length ? "有数据源告警" : "无 API errors"}`;
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
  $("logic-zhang").textContent = (Object.values(SEAT_WIP).reduce((a, b) => a + b, 0)) + " / " + CROSS_SEAT_SNAPSHOT;
  $("logic-men").textContent = g.approval_ok ? "PASS" : "待判定";
  $("logic-ling").textContent = DECISIONS.length + " 项";

  $("flow-tasks").textContent = m.dev_tasks_recent ?? "—";
  $("flow-wip").textContent = Object.values(SEAT_WIP).reduce((a, b) => a + b, 0);
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
  $("decision-list").innerHTML = DECISIONS.map((d) => `
    <div class="decision-item ${d.high ? "high" : ""}">
      <div class="decision-top"><b>${esc(d.title)}</b><em>${esc(d.due)}</em></div>
      <p>${esc(d.desc)}</p>
      <div class="decision-foot"><span>${esc(d.owner)}</span><span class="audit-flag">人工快照 · ${esc(DECISIONS_AS_OF.slice(11))}</span></div>
    </div>`).join("");
}

function renderWip() {
  const rows = [];
  Object.keys(SEAT_WIP).forEach((owner) => {
    (SEAT_WIP[owner] || []).forEach((t) => rows.push({ owner, ...t }));
  });
  if (!rows.length) {
    $("work-rows").innerHTML = '<div class="empty-row">研发部直属在制 0（API 实测）。跨席 16 单未在 API v1 暴露，请到 devops 工单总线查看。</div>';
    $("work-foot").textContent = "来源：/api/dev/legion work_in_progress（空）";
    return;
  }
  $("work-rows").innerHTML = rows.map((w) => `
    <div class="work-row">
      <span class="work-status ${w.status === "doing" ? "doing" : "review"}">${w.status === "doing" ? "在制" : "待复核"}</span>
      <span class="work-task"><span class="work-id">${esc(w.id)}</span><small class="work-desc">${esc(w.desc || "")}</small></span>
      <span class="work-owner">${esc(w.owner)}</span>
      <span class="work-age">—</span>
      <span class="work-next">${w.status === "doing" ? "继续开发" : "待复核"}</span>
    </div>`).join("");
  $("work-foot").textContent = "来源：/api/dev/legion work_in_progress";
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
    const res = await fetch("/api/dev/legion", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    DATA = await res.json();
    SEAT_WIP = {};
    (DATA.work_in_progress || []).forEach((x) => {
      if (x && x.owner) SEAT_WIP[x.owner] = x.tasks || [];
    });
    renderStatus();
    renderSeats();
    renderWip();
  } catch (e) {
    $("error-banner").hidden = false;
    $("error-banner").textContent = "⚠ 读取 /api/dev/legion 失败：" + String(e.message || e);
    $("run-banner").querySelector("strong").textContent = "数据读取失败";
  }
}

$("btn-refresh").addEventListener("click", () => load());
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
renderDecisions();
setInterval(load, 60000);
