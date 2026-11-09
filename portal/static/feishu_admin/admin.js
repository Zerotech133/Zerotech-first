/* 军机驿 · 飞书管理台 PC 原型 v0.1
   Pure frontend mock: no network, no production writes. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const view = $("#view");
  const navbar = $("#nav");
  const drawer = $("#drawer");
  const scrim = $("#scrim");
  const modalWrap = $("#modal");
  const toastEl = $("#toast");

  const state = {
    route: "overview",
    pushFilter: "all",
    pushQuery: "",
    approvalFilter: "pending",
    auditFilter: "all",
    globalQuery: "",
    drawer: null,
  };

  const fmt = (n) => new Intl.NumberFormat("zh-CN").format(n);

  const pushes = [
    { id: "PSH-1107-001", title: "盘前晨报 · 11月7日", target: "核心订阅组", template: "盘前晨报", status: "sent", time: "07:35", sender: "OPS1", delivered: "128 / 128", latency: "820ms", summary: "今日盘前：政策、产业与全球市场三条主线。" },
    { id: "PSH-1107-002", title: "JEV 情报速递 · 早盘", target: "核心订阅组", template: "JEV 早盘", status: "sent", time: "08:12", sender: "OPS2", delivered: "126 / 128", latency: "760ms", summary: "隔夜海外科技与半导体事件密集，盘前需关注。" },
    { id: "PSH-1107-003", title: "客户域订阅到期提醒", target: "客户域 / 白名单", template: "飞书文本", status: "scheduled", time: "14:00", sender: "OPS2", delivered: "—", latency: "—", summary: "12 个客户订阅将在 3 日内到期。" },
    { id: "PSH-1107-004", title: "KG 情报卡 · 盘后", target: "自选观察组", template: "飞书卡片 · OPS1", status: "draft", time: "—", sender: "OPS1", delivered: "—", latency: "—", summary: "盘口异动、传导链与数据健康摘要。" },
    { id: "PSH-1107-005", title: "涨停复盘推送", target: "策略观察组", template: "飞书卡片 · OPS1", status: "failed", time: "11:02", sender: "OPS1", delivered: "0 / 12", latency: "—", summary: "机器人 230013 不在目标群；已进入死信。" },
    { id: "PSH-1107-006", title: "周度策略观察 · 二买", target: "策略观察组", template: "飞书卡片", status: "sent", time: "昨天 20:30", sender: "OPS1", delivered: "42 / 42", latency: "910ms", summary: "本周二买观察池与触发条件更新。" },
  ];

  const approvals = [
    { id: "APR-1107-003", title: "客户域订阅扩容", applicant: "OPS1", status: "pending", risk: "中风险", time: "10:12", scope: "目标变更 · 二级审批", impact: "客户域 / 白名单", summary: "新增 40 个白名单收件人，预计每日 +40 条。", evidence: "authorization.json#L128-L167" },
    { id: "APR-1107-004", title: "推送目标：核心订阅组", applicant: "OPS1", status: "pending", risk: "低风险", time: "10:26", scope: "目标变更 · 一级审批", impact: "核心订阅组 / 128 人", summary: "替换 3 个已离队收件人。", evidence: "authorization.json#L61-L74" },
    { id: "APR-1107-001", title: "通道机器人配置变更", applicant: "OPS2", status: "approved", risk: "低风险", time: "昨天 16:40", scope: "通道变更 · 二级审批", impact: "零界·问答", summary: "增加 mention 白名单 2 项。", evidence: "feishu_registry.json#bot.qa" },
    { id: "APR-1107-002", title: "死信重试：PSH-1107-005", applicant: "OPS1", status: "rejected", risk: "中风险", time: "11:08", scope: "重试 · 决策账", impact: "12 个收件人", summary: "机器人不在群，先修群成员再重试。", evidence: "dead_letters.jsonl#DL-1107-014" },
  ];

  const targets = [
    { id: "TGT-001", name: "核心订阅组", owner: "OPS1", status: "active", members: 128, quota: 200, expires: "2026-12-31", source: "authorization.json" },
    { id: "TGT-002", name: "客户域 / 白名单", owner: "OPS2", status: "active", members: 40, quota: 80, expires: "2026-11-30", source: "authorization.json" },
    { id: "TGT-003", name: "策略观察组", owner: "OPS1", status: "active", members: 42, quota: 60, expires: "2026-12-15", source: "authorization.json" },
    { id: "TGT-004", name: "自选观察组", owner: "OPS3", status: "review", members: 24, quota: 50, expires: "2026-11-20", source: "authorization.json" },
  ];

  const bots = [
    { id: "BOT-NOTIFY", name: "零界·通知", role: "notify", status: "ok", heartbeat: "12s", owner: "OPS1", note: "唯一广播身份" },
    { id: "BOT-COMMAND", name: "零界·指令", role: "command", status: "ok", heartbeat: "8s", owner: "OPS1", note: "指令 / 卡片动作" },
    { id: "BOT-QA", name: "零界·问答", role: "qa", status: "ok", heartbeat: "21s", owner: "OPS2", note: "问答入口" },
    { id: "BOT-EXEC", name: "零界·执行", role: "exec", status: "ok", heartbeat: "9s", owner: "OPS2", note: "P2P 任务" },
    { id: "BOT-SERVICE", name: "零界·服务", role: "service", status: "reserved", heartbeat: "—", owner: "OPS3", note: "客户身份保留" },
  ];

  const routes = [
    { id: "R-NOTIFY", name: "通知广播", direction: "outbound", target: "8097 / OPS1", latency: "820ms", status: "active" },
    { id: "R-COMMAND", name: "指令 / 卡片动作", direction: "inbound", target: "W43 / 8097", latency: "—", status: "active" },
    { id: "R-QA", name: "问答", direction: "inbound", target: "W43 / 8091", latency: "—", status: "active" },
    { id: "R-EXEC", name: "任务执行", direction: "p2p", target: "8081 / DSH", latency: "—", status: "active" },
    { id: "R-FEED", name: "情报 / Feed", direction: "inbound", target: "W43 / 8091", latency: "—", status: "active" },
    { id: "R-CONFIRM", name: "确认 / 决策", direction: "inbound", target: "decision ledger", latency: "—", status: "active" },
    { id: "R-CUSTOMER", name: "客户服务", direction: "reserved", target: "svc.customer", latency: "—", status: "reserved" },
    { id: "R-FALLBACK", name: "兜底澄清", direction: "inbound", target: "W43 / 8091", latency: "—", status: "active" },
  ];

  const deadletters = [
    { id: "DL-1107-014", task: "PSH-1107-005", reason: "机器人不在群", error: "230013 bot not in chat", attempts: 1, age: "18 分钟", status: "pending" },
    { id: "DL-1107-012", task: "PSH-1106-019", reason: "权限不足", error: "permission denied: drive.file", attempts: 2, age: "1 小时", status: "pending" },
    { id: "DL-1107-009", task: "PSH-1106-008", reason: "网络超时", error: "request timeout after 3000ms", attempts: 3, age: "昨天", status: "resolved" },
    { id: "DL-1107-006", task: "PSH-1105-021", reason: "收件人不存在", error: "open_id not found", attempts: 1, age: "2 天", status: "ignored" },
  ];

  const jobs = [
    { id: "JOB-MORNING", name: "盘前晨报", schedule: "每日 07:30", last: "今天 07:30", next: "明天 07:30", status: "ok", owner: "OPS1", manifest: "job_manifest.json#morning_brief" },
    { id: "JOB-JEV", name: "JEV 早盘", schedule: "交易日 08:00", last: "今天 08:12", next: "明天 08:00", status: "ok", owner: "OPS2", manifest: "job_manifest.json#jev_morning" },
    { id: "JOB-KG", name: "KG 情报卡", schedule: "交易日 15:30", last: "昨天 15:31", next: "今天 15:30", status: "ok", owner: "OPS1", manifest: "job_manifest.json#kg_card" },
    { id: "JOB-SUB", name: "订阅到期提醒", schedule: "每日 14:00", last: "昨天 14:00", next: "今天 14:00", status: "warn", owner: "OPS2", manifest: "job_manifest.json#sub_expiry" },
    { id: "JOB-DL", name: "死信补达", schedule: "每 30 分钟", last: "今天 12:30", next: "今天 13:00", status: "ok", owner: "OPS1", manifest: "job_manifest.json#dl_redelivery" },
  ];

  const audits = [
    { id: "AUD-20261107-001", action: "push.sent", operator: "OPS1", target: "PSH-1107-001", time: "07:35:12", detail: "盘前晨报 · 11月7日", level: "receiver_verified" },
    { id: "AUD-20261107-002", action: "approval.created", operator: "OPS1", target: "APR-1107-003", time: "10:12:08", detail: "客户域订阅扩容", level: "api_accepted" },
    { id: "AUD-20261107-003", action: "approval.approved", operator: "OPS1", target: "APR-1107-001", time: "昨天 16:42", detail: "通道机器人配置变更", level: "receiver_verified" },
    { id: "AUD-20261107-004", action: "deadletter.created", operator: "system", target: "DL-1107-014", time: "11:02:33", detail: "机器人不在群", level: "dead_letter" },
    { id: "AUD-20261107-005", action: "target.changed", operator: "OPS2", target: "TGT-002", time: "昨天 15:10", detail: "客户域 / 白名单 +2", level: "api_accepted" },
    { id: "AUD-20261107-006", action: "channel.heartbeat", operator: "system", target: "BOT-QA", time: "12:52:01", detail: "queued 21s", level: "receiver_verified" },
  ];

  const navGroups = [
    {
      label: "工作台",
      items: [
        { key: "overview", label: "总览", icon: "◫" },
        { key: "push", label: "推送任务", icon: "✈", badge: () => pushes.filter(p => p.status === "failed").length, badgeType: "red" },
        { key: "approvals", label: "审批中心", icon: "✓", badge: () => approvals.filter(a => a.status === "pending").length, badgeType: "red" },
      ],
    },
    {
      label: "运营",
      items: [
        { key: "targets", label: "目标订阅", icon: "◎" },
        { key: "deadletters", label: "死信重试", icon: "↻", badge: () => deadletters.filter(d => d.status === "pending").length, badgeType: "gray" },
        { key: "channel", label: "通道与机器人", icon: "⌁" },
        { key: "jobs", label: "任务调度", icon: "▦" },
      ],
    },
    {
      label: "治理",
      items: [
        { key: "audit", label: "审计证据", icon: "◈" },
        { key: "settings", label: "设置", icon: "⚙" },
      ],
    },
  ];

  const titles = {
    overview: "总览", push: "推送任务", approvals: "审批中心", targets: "目标订阅",
    deadletters: "死信重试", channel: "通道与机器人", jobs: "任务调度", audit: "审计证据", settings: "设置",
  };
  const subtitles = {
    overview: "飞书域运行状态、待办与最近活动",
    push: "创建、预览、发送与重试都在这里",
    approvals: "所有写操作先审批，再进入决策账",
    targets: "收件人目标组与配额；只读投影 + 受控变更",
    deadletters: "失败投递的证据与重试入口",
    channel: "五身份、九路由、心跳与边界",
    jobs: "调度、最近运行与失败原因",
    audit: "逐事件、逐证据，不可修改",
    settings: "账号、安全、环境与原型边界",
  };

  const statusMap = {
    sent: { cls: "ok", text: "已发送" },
    scheduled: { cls: "info", text: "定时" },
    draft: { cls: "neutral", text: "草稿" },
    failed: { cls: "fail", text: "失败" },
    pending: { cls: "wait", text: "待审批" },
    approved: { cls: "ok", text: "已通过" },
    rejected: { cls: "fail", text: "已拒绝" },
    active: { cls: "ok", text: "active" },
    review: { cls: "wait", text: "review" },
    ok: { cls: "ok", text: "正常" },
    warn: { cls: "wait", text: "观察" },
    fail: { cls: "fail", text: "失败" },
    reserved: { cls: "neutral", text: "保留" },
    resolved: { cls: "ok", text: "已恢复" },
    ignored: { cls: "neutral", text: "已忽略" },
  };
  const pill = (s, text) => { const m = statusMap[s] || { cls: "neutral", text: s }; return `<span class="pill ${m.cls}">${text || m.text}</span>`; };

  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  function fmtMembers(n) { return fmt(n); }

  function pageHead(title, sub, actions = "") {
    return `<div class="page-head">
      <div><h1 class="page-title">${title}</h1><p class="page-sub">${sub}</p></div>
      <div class="head-actions">${actions}</div>
    </div>`;
  }

  function kpi(label, value, foot, tone = "") {
    return `<div class="kpi ${tone}">
      <div class="k-label"><i></i>${label}</div>
      <div class="k-value">${value}</div>
      <div class="k-foot">${foot}</div>
    </div>`;
  }

  function overviewPage() {
    const pending = approvals.filter(a => a.status === "pending");
    const recent = pushes.slice(0, 4);
    const failedDl = deadletters.filter(d => d.status === "pending").length;
    return `${pageHead("总览", "飞书域运行状态、待办与最近活动", `<button class="btn" data-action="refresh">↻ 刷新</button><button class="btn primary" data-action="new-push">＋ 新建推送</button>`)}

      <section class="hero-strip">
        <div class="hero-copy">
          <div class="hero-eyebrow">FEISHU OPS · PC CONSOLE</div>
          <div class="hero-title">今天，通道正常。<span class="hero-live">SHELL</span></div>
          <div class="hero-sub">2026-11-07 · 周六 · 数据截至 12:30 · F17 壳层 · mock 数据</div>
        </div>
        <div class="hero-stats">
          <div><b>${fmt(254)}</b><span>今日推送</span></div>
          <div><b>99.6%</b><span>送达成功率</span></div>
          <div><b>820<span style="font-size:11px;opacity:.8">ms</span></b><span>p95 延迟</span></div>
        </div>
      </section>

      <div class="grid kpis">
        ${kpi("今日推送", "254", `<span class="up">↑ 12%</span><span>较昨日</span>`, "good")}
        ${kpi("待审批", `<span>${pending.length}</span>`, `<span class="down">2 项待处理</span>`, "warn")}
        ${kpi("死信待重试", `${failedDl}`, `<span>0.18%</span><span>· 阈值 0.5%</span>`, "")}
        ${kpi("在线机器人", "5 / 5", `<span>心跳正常</span><span>· 最长 21s</span>`, "good")}
      </div>

      <div class="overview-grid">
        <div class="grid">
          <section class="card flush">
            <div class="card-head pad" style="padding-bottom:12px"><div><div class="section-title">最近推送</div><div class="section-sub">最近 6 条投递任务，点击查看详情</div></div><a class="link-btn" href="#/push">全部 ›</a></div>
            <div class="card-body tight">
              <table class="table">
                <thead><tr><th>任务</th><th>目标组</th><th>状态</th><th>时间</th><th class="right">送达</th></tr></thead>
                <tbody>
                  ${recent.map(p => `<tr data-action="push-detail" data-id="${p.id}">
                    <td><span class="title-cell">${p.title}<span class="sub">${p.id} · ${p.template}</span></span></td>
                    <td>${p.target}</td>
                    <td>${pill(p.status)}</td>
                    <td class="mono">${p.time}</td>
                    <td class="right mono">${p.delivered}</td>
                  </tr>`).join("")}
                </tbody>
              </table>
            </div>
          </section>

          <section class="card">
            <div class="row-between" style="margin-bottom:8px"><div><div class="section-title">24 小时投递</div><div class="section-sub">按小时聚合，红色为失败/重试</div></div><span class="tag">06:00 起</span></div>
            <div class="bars" aria-label="24小时投递柱状图">
              ${[8, 14, 18, 12, 20, 24, 18, 30, 26, 22, 38, 44, 28, 26, 32, 40, 34, 62, 30, 24, 18, 14, 10, 6].map((v, i) => `<span class="${i === 17 ? "hot" : i > 19 ? "warn" : ""}" style="height:${v}px" title="${i}:00 · ${v} 条"></span>`).join("")}
            </div>
            <div class="bar-axis"><span>06:00</span><span>12:00</span><span>18:00</span><span>00:00</span><span>06:00</span></div>
          </section>
        </div>

        <div class="grid">
          <section class="card">
            <div class="row-between" style="margin-bottom:6px"><div><div class="section-title">待处理</div><div class="section-sub">${pending.length} 个审批 · ${failedDl} 条死信</div></div><a class="link-btn" href="#/approvals">进入审批 ›</a></div>
            <div class="quick-list">
              ${pending.map(a => `<button class="quick-item" style="width:100%;text-align:left" data-action="approval-detail" data-id="${a.id}">
                <span class="q-ico">✓</span>
                <span class="q-main"><b>${a.title}</b><small>${a.risk} · ${a.applicant} · ${a.time}</small></span>
                ${pill("pending")}
              </button>`).join("")}
              <button class="quick-item" style="width:100%;text-align:left" data-action="deadletter-list">
                <span class="q-ico" style="background:var(--red-soft);color:var(--red)">↻</span>
                <span class="q-main"><b>${failedDl} 条死信待重试</b><small>最近：机器人不在群 / 权限不足</small></span>
                <span class="pill fail">处理</span>
              </button>
            </div>
          </section>

          <section class="card">
            <div class="row-between" style="margin-bottom:4px"><div><div class="section-title">通道健康</div><div class="section-sub">唯一入口、唯一出向、单向链路</div></div><span class="pill ok">5/5</span></div>
            <div class="health-row"><span class="health-name"><b>8097</b><span class="mono">push_admin_server</span></span><span class="health-val">UP · 820ms</span></div>
            <div class="health-row"><span class="health-name"><b>8091</b><span class="mono">m_api / H5 BFF</span></span><span class="health-val">UP · 16 路由</span></div>
            <div class="health-row"><span class="health-name"><b>8081</b><span class="mono">m_public_gate</span></span><span class="health-val" style="color:var(--red)">DOWN · 待隧道</span></div>
            <div class="health-row"><span class="health-name"><b>8787</b><span class="mono">Hermes 控制台</span></span><span class="health-val">UP · 只读</span></div>
            <div class="health-row"><span class="health-name"><b>9104</b><span class="mono">legacy callback</span></span><span class="health-val">DOWN · 已停</span></div>
          </section>
        </div>
      </div>`;
  }

  function pushPage() {
    const counts = {
      all: pushes.length,
      sent: pushes.filter(p => p.status === "sent").length,
      scheduled: pushes.filter(p => p.status === "scheduled").length,
      draft: pushes.filter(p => p.status === "draft").length,
      failed: pushes.filter(p => p.status === "failed").length,
    };
    return `${pageHead("推送任务", "创建、预览、发送与重试都在这里", `<button class="btn" data-action="refresh">↻ 刷新</button><button class="btn primary" data-action="new-push">＋ 新建推送</button>`)}
      <div class="grid kpis" style="grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:14px">
        ${kpi("今日推送", "254", `<span class="up">↑ 12%</span><span>较昨日</span>`, "good")}
        ${kpi("失败待重试", `${counts.failed}`, `<span>已入死信</span>`, "red")}
        ${kpi("定时待发", `${counts.scheduled}`, `<span>最近 14:00</span>`, "warn")}
        ${kpi("草稿", `${counts.draft}`, `<span>尚未提交审批</span>`, "")}
      </div>
      <div class="card flush">
        <div style="padding:14px 16px 0">
          <div class="filter-bar">
            <div class="seg" id="pushSeg">
              ${[["all","全部"],["sent","已发送"],["scheduled","定时"],["draft","草稿"],["failed","失败"]].map(([k, t]) => `<button class="${state.pushFilter === k ? "active" : ""}" data-action="push-filter" data-filter="${k}">${t} <span style="opacity:.55">${counts[k]}</span></button>`).join("")}
            </div>
            <label class="filter-search"><span>⌕</span><input id="pushQuery" placeholder="搜索标题 / 目标 / 任务号" value="${state.pushQuery}" autocomplete="off"></label>
            <span class="small" style="margin-left:auto" id="pushCount">${pushes.length} 条</span>
          </div>
        </div>
        <div class="card-body tight"><table class="table">
          <thead><tr><th style="width:34%">任务</th><th>目标组</th><th>模板 / 发送者</th><th>状态</th><th>时间</th><th class="right">送达</th><th></th></tr></thead>
          <tbody id="pushRows"></tbody>
        </table></div>
      </div>
      <div style="display:flex;gap:8px;margin-top:10px;align-items:center"><span class="small">所有写操作必须 preview → record → approve → execute，真实发送仍由 8097 按决策账执行。</span></div>`;
  }

  function renderPushRows() {
    const q = state.pushQuery.trim().toLowerCase();
    const rows = pushes.filter(p => (state.pushFilter === "all" || p.status === state.pushFilter) && (!q || [p.title, p.target, p.id, p.template].join(" ").toLowerCase().includes(q)));
    const tbody = $("#pushRows");
    if (!tbody) return;
    tbody.innerHTML = rows.length ? rows.map(p => `<tr data-action="push-detail" data-id="${p.id}">
      <td><span class="title-cell">${p.title}<span class="sub">${p.id} · ${p.template}</span></span></td>
      <td>${p.target}</td>
      <td><span class="mono">${p.template}</span><span class="sub">${p.sender}</span></td>
      <td>${pill(p.status)}</td>
      <td class="mono">${p.time}</td>
      <td class="right mono">${p.delivered}</td>
      <td class="right"><button class="btn sm" data-action="push-detail" data-id="${p.id}">详情</button></td>
    </tr>`).join("") : `<tr><td colspan="7"><div class="empty">没有匹配的推送任务</div></td></tr>`;
    const counter = $("#pushCount");
    if (counter) counter.textContent = `${rows.length} 条`;
  }

  function approvalsPage() {
    const counts = { pending: approvals.filter(a => a.status === "pending").length, approved: approvals.filter(a => a.status === "approved").length, rejected: approvals.filter(a => a.status === "rejected").length };
    const rows = approvals.filter(a => state.approvalFilter === "all" || a.status === state.approvalFilter);
    return `${pageHead("审批中心", "所有写操作先审批，再进入决策账", `<button class="btn" data-action="refresh">↻ 刷新</button>`)}
      <div class="grid kpis" style="grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:14px">
        ${kpi("待审批", `${counts.pending}`, `<span>平均等待 1h 24m</span>`, "warn")}
        ${kpi("今日已通过", `${counts.approved}`, `<span class="up">0 条被拒后重提</span>`, "good")}
        ${kpi("已拒绝", `${counts.rejected}`, `<span>均记录证据</span>`, "")}
      </div>
      <div class="card flush">
        <div style="padding:14px 16px 0"><div class="filter-bar">
          <div class="seg">${[["pending","待审批"],["approved","已通过"],["rejected","已拒绝"],["all","全部"]].map(([k, t]) => `<button class="${state.approvalFilter === k ? "active" : ""}" data-action="approval-filter" data-filter="${k}">${t} <span style="opacity:.55">${k === "all" ? approvals.length : counts[k]}</span></button>`).join("")}</div>
          <span class="small" style="margin-left:auto">审批通过只生成“已批准”记录，执行仍由 8097 / 决策账按闸门完成</span>
        </div></div>
        <div class="card-body tight"><table class="table">
          <thead><tr><th>审批事项</th><th>申请人</th><th>风险</th><th>范围</th><th>状态</th><th>时间</th><th></th></tr></thead>
          <tbody id="approvalRows"></tbody>
        </table></div>
      </div>`;
  }

  function renderApprovalRows() {
    const rows = approvals.filter(a => state.approvalFilter === "all" || a.status === state.approvalFilter);
    const tbody = $("#approvalRows");
    if (!tbody) return;
    tbody.innerHTML = rows.map(a => `<tr data-action="approval-detail" data-id="${a.id}">
      <td><span class="title-cell">${a.title}<span class="sub">${a.id} · ${a.scope}</span></span></td>
      <td>${a.applicant}</td>
      <td>${a.risk === "中风险" ? `<span class="pill wait">${a.risk}</span>` : `<span class="pill info">${a.risk}</span>`}</td>
      <td>${a.impact}</td>
      <td>${pill(a.status)}</td>
      <td class="mono">${a.time}</td>
      <td class="right"><button class="btn sm" data-action="approval-detail" data-id="${a.id}">查看</button></td>
    </tr>`).join("");
  }

  function targetsPage() {
    const members = targets.reduce((s, t) => s + t.members, 0);
    const quota = targets.reduce((s, t) => s + t.quota, 0);
    return `${pageHead("目标订阅", "收件人目标组与配额；只读投影 + 受控变更", `<button class="btn" data-action="refresh">↻ 刷新</button>`)}
      <div class="grid kpis" style="grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:14px">
        ${kpi("目标组", `${targets.length}`, `<span>全部来自 authorization.json</span>`, "")}
        ${kpi("覆盖成员", `${fmtMembers(members)}`, `<span>配额 ${fmtMembers(quota)}</span>`, "good")}
        ${kpi("配额使用率", `${Math.round(members / quota * 100)}%`, `<span>${fmtMembers(quota - members)} 个余量</span>`, "warn")}
      </div>
      <div class="card flush"><div class="card-body tight"><table class="table">
        <thead><tr><th>目标组</th><th>负责人</th><th>状态</th><th>成员 / 配额</th><th>到期</th><th>授权真源</th><th></th></tr></thead>
        <tbody>${targets.map(t => `<tr data-action="target-detail" data-id="${t.id}">
          <td><span class="title-cell">${t.name}<span class="sub">${t.id}</span></span></td>
          <td>${t.owner}</td>
          <td>${pill(t.status)}</td>
          <td style="min-width:150px"><span class="mono">${fmtMembers(t.members)} / ${fmtMembers(t.quota)}</span><div class="progress" style="margin-top:5px"><i style="width:${Math.round(t.members / t.quota * 100)}%"></i></div></td>
          <td class="mono">${t.expires}</td>
          <td class="mono">${t.source}</td>
          <td class="right"><button class="btn sm" data-action="target-detail" data-id="${t.id}">详情</button></td>
        </tr>`).join("")}</tbody>
      </table></div></div>`;
  }

  function deadlettersPage() {
    return `${pageHead("死信重试", "失败投递的证据与重试入口", `<button class="btn" data-action="refresh">↻ 刷新</button>`)}
      <div class="grid kpis" style="grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:14px">
        ${kpi("待处理死信", `${deadletters.filter(d => d.status === "pending").length}`, `<span>最近 18 分钟</span>`, "red")}
        ${kpi("近 24h 重试", "6", `<span class="up">4 已恢复</span><span>· 2 待处理</span>`, "good")}
        ${kpi("死信率", "0.18%", `<span>阈值 0.5%</span><span>· 正常</span>`, "good")}
      </div>
      <div class="card flush"><div class="card-body tight"><table class="table">
        <thead><tr><th>死信</th><th>原任务</th><th>原因</th><th>最近错误</th><th>尝试</th><th>状态</th><th>年龄</th><th></th></tr></thead>
        <tbody>${deadletters.map(d => `<tr data-action="deadletter-detail" data-id="${d.id}">
          <td><span class="title-cell">${d.id}<span class="sub">${d.task}</span></span></td>
          <td class="mono">${d.task}</td><td>${d.reason}</td><td class="mono">${d.error}</td><td>${d.attempts}</td>
          <td>${pill(d.status === "pending" ? "wait" : d.status)}</td><td class="mono">${d.age}</td>
          <td class="right">${d.status === "pending" ? `<button class="btn sm" data-action="retry" data-id="${d.id}">重试</button>` : `<button class="btn sm" data-action="deadletter-detail" data-id="${d.id}">详情</button>`}</td>
        </tr>`).join("")}</tbody>
      </table></div></div>`;
  }

  function channelPage() {
    return `${pageHead("通道与机器人", "五身份、九路由、心跳与边界", `<button class="btn" data-action="refresh">↻ 刷新</button>`)}
      <div class="grid three-col" style="grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:14px">
        ${[["8097 推送闸门","UP","唯一出向 · 处理 254 条"],["8091 门户 / H5 BFF","UP","16 路由 · 只读投影"],["8081 公网安全门","DOWN","默认关闭 · 待命名隧道"],["8787 Hermes 控制台","UP","只读接入 · 未纳管"]].map(([n, s, d], i) => `<div class="card" style="padding:13px"><div class="row-between"><span class="small">${n}</span><span class="pill ${s === "UP" ? "ok" : "fail"}">${s}</span></div><div style="font-size:16px;font-weight:650;margin-top:7px;color:${s === "UP" ? "var(--text)" : "var(--red)"}">${s === "UP" ? "运行中" : "未启用"}</div><div class="small" style="margin-top:4px">${d}</div></div>`).join("")}
      </div>
      <div class="card" style="margin-bottom:14px">
        <div class="card-head"><div><div class="section-title">机器人身份</div><div class="section-sub">五个身份，独立职责；客户身份保留未启用</div></div><span class="pill ok">5 个身份</span></div>
        <div class="bot-grid">${bots.map(b => `<div class="bot-card">
          <div class="bot-head"><span class="bot-mark">${b.name.slice(-1)}</span>${pill(b.status)}</div>
          <h3>${b.name}</h3><p>${b.role} · 心跳 ${b.heartbeat}</p>
          <div class="bot-meta"><span>${b.owner}</span><span>${b.note}</span></div>
        </div>`).join("")}</div>
      </div>
      <div class="card flush"><div class="card-head pad"><div><div class="section-title">路由</div><div class="section-sub">出向 8097、入向 W43、P2P 执行；角色与 intent 一级路由</div></div><span class="pill ok">9 注册</span></div>
        <div class="card-body tight"><table class="table">
          <thead><tr><th>路由</th><th>方向</th><th>目标 / 边界</th><th>延迟</th><th>状态</th><th></th></tr></thead>
          <tbody>${routes.map(r => `<tr data-action="route-detail" data-id="${r.id}">
            <td><span class="title-cell">${r.name}<span class="sub">${r.id}</span></span></td><td><span class="tag ${r.direction === "outbound" ? "blue" : ""}">${r.direction}</span></td>
            <td class="mono">${r.target}</td><td class="mono">${r.latency}</td><td>${pill(r.status)}</td>
            <td class="right"><button class="btn sm" data-action="route-detail" data-id="${r.id}">详情</button></td>
          </tr>`).join("")}</tbody>
        </table></div>
      </div>`;
  }

  function jobsPage() {
    return `${pageHead("任务调度", "调度、最近运行与失败原因", `<button class="btn" data-action="refresh">↻ 刷新</button>`)}
      <div class="card flush"><div class="card-body tight"><table class="table">
        <thead><tr><th>任务</th><th>排期</th><th>最近运行</th><th>下次运行</th><th>状态</th><th>负责人</th><th>Manifest</th><th></th></tr></thead>
        <tbody>${jobs.map(j => `<tr data-action="job-detail" data-id="${j.id}">
          <td><span class="title-cell">${j.name}<span class="sub">${j.id}</span></span></td><td>${j.schedule}</td><td class="mono">${j.last}</td><td class="mono">${j.next}</td>
          <td>${pill(j.status)}</td><td>${j.owner}</td><td class="mono">${j.manifest}</td>
          <td class="right"><button class="btn sm" data-action="job-detail" data-id="${j.id}">详情</button></td>
        </tr>`).join("")}</tbody>
      </table></div></div>`;
  }

  function auditPage() {
    const groups = { all: audits.length, push: audits.filter(a => a.action.startsWith("push.")).length, approval: audits.filter(a => a.action.startsWith("approval.")).length, deadletter: audits.filter(a => a.action.startsWith("deadletter.")).length, channel: audits.filter(a => a.action.startsWith("channel.")).length };
    return `${pageHead("审计证据", "逐事件、逐证据，不可修改", `<button class="btn" data-action="refresh">↻ 刷新</button>`)}
      <div class="grid kpis" style="grid-template-columns:repeat(4,minmax(0,1fr));margin-bottom:14px">
        ${kpi("审计事件", `${audits.length}`, `<span>全部只读</span>`, "")}
        ${kpi("receiver_verified", `${audits.filter(a => a.level === "receiver_verified").length}`, `<span>接收侧确认</span>`, "good")}
        ${kpi("api_accepted", `${audits.filter(a => a.level === "api_accepted").length}`, `<span>API 受理</span>`, "")}
        ${kpi("dead_letter", `${audits.filter(a => a.level === "dead_letter").length}`, `<span>需重试</span>`, "red")}
      </div>
      <div class="card flush"><div style="padding:14px 16px 0"><div class="filter-bar"><div class="seg">${[["all","全部"],["push","推送"],["approval","审批"],["deadletter","死信"],["channel","通道"]].map(([k, t]) => `<button class="${state.auditFilter === k ? "active" : ""}" data-action="audit-filter" data-filter="${k}">${t} <span style="opacity:.55">${groups[k]}</span></button>`).join("")}</div><span class="small" style="margin-left:auto">证据等级：api_accepted / receiver_verified / dead_letter；审计只读，真实导出需 L2 迁移后开放。</span></div></div>
      <div class="card-body tight"><table class="table">
        <thead><tr><th>事件</th><th>操作者</th><th>目标</th><th>证据等级</th><th>时间</th><th></th></tr></thead>
        <tbody id="auditRows"></tbody>
      </table></div></div>`;
  }

  function renderAuditRows() {
    const rows = audits.filter(a => state.auditFilter === "all" || a.action.startsWith(state.auditFilter + "."));
    const tbody = $("#auditRows");
    if (!tbody) return;
    tbody.innerHTML = rows.length ? rows.map(a => `<tr data-action="audit-detail" data-id="${a.id}">
      <td><span class="title-cell">${a.action}<span class="sub">${a.id} · ${a.detail}</span></span></td><td>${a.operator}</td><td class="mono">${a.target}</td>
      <td><span class="tag ${a.level === "dead_letter" ? "" : "blue"}">${a.level}</span></td><td class="mono">${a.time}</td>
      <td class="right"><button class="btn sm" data-action="audit-detail" data-id="${a.id}">查看</button></td>
    </tr>`).join("") : `<tr><td colspan="6"><div class="empty">没有匹配的审计事件</div></td></tr>`;
  }

  function settingsPage() {
    return `${pageHead("设置", "账号、安全、环境与原型边界", "")}
      <div class="grid" style="grid-template-columns:1.2fr .8fr;gap:14px">
        <div class="grid">
          <section class="card"><div class="section-title">账号与身份</div><div class="section-sub">只读会话 · 不保存密码</div>
            <div class="divider"></div>
            <div class="health-row"><span class="health-name"><b>当前账号</b><span class="mono">田 · OPS 管理员</span></span><span class="pill ok">已登录</span></div>
            <div class="health-row"><span class="health-name"><b>身份来源</b><span class="mono">portal token</span></span><span class="health-val">1 小时有效 · 不落盘</span></div>
            <div class="health-row"><span class="health-name"><b>权限范围</b><span class="mono">飞书域 · 只读</span></span><span class="health-val">写操作需审批</span></div>
          </section>
          <section class="card"><div class="section-title">安全与审计</div><div class="sub section-sub">所有写操作 preview → record → approve → execute</div>
            <div class="divider"></div>
            <div class="health-row"><span class="health-name"><b>审批边界</b><span class="mono">目标 / 通道 / 重试</span></span><span class="pill wait">强制</span></div>
            <div class="health-row"><span class="health-name"><b>证据等级</b><span class="mono">四级</span></span><span class="health-val">api_accepted / receiver_verified / dead_letter</span></div>
            <div class="health-row"><span class="health-name"><b>真实导出</b><span class="mono">待 L2</span></span><span class="pill neutral">只读</span></div>
          </section>
        </div>
        <section class="card"><div class="section-title">运行环境</div><div class="section-sub">端口 / 通道 / 边界</div>
          <div class="divider"></div>
          <div class="health-row"><span class="health-name"><b>推送闸门</b><span class="mono">8097</span></span><span class="pill ok">UP</span></div>
          <div class="health-row"><span class="health-name"><b>门户 / H5 BFF</b><span class="mono">8091</span></span><span class="pill ok">UP</span></div>
          <div class="health-row"><span class="health-name"><b>公网安全门</b><span class="mono">8081</span></span><span class="pill fail">DOWN</span></div>
          <div class="health-row"><span class="health-name"><b>Hermes 控制台</b><span class="mono">8787</span></span><span class="pill ok">UP</span></div>
          <div class="divider"></div>
          <div class="evidence">原型边界：设置页只展示结构；真实 token、密钥、权限不在此页面展示或修改。生产落码需走 W2 F16，不重启 8091、不写生产账本。</div>
        </section>
      </div>`;
  }

  const pages = { overview: overviewPage, push: pushPage, approvals: approvalsPage, targets: targetsPage, deadletters: deadlettersPage, channel: channelPage, jobs: jobsPage, audit: auditPage, settings: settingsPage };

  function renderNav() {
    navbar.innerHTML = navGroups.map(g => `<div class="nav-group"><div class="nav-label">${g.label}</div>${g.items.map(it => {
      const badge = it.badge ? it.badge() : 0;
      return `<a class="nav-item ${state.route === it.key ? "active" : ""}" href="#/${it.key}"><span class="nav-ico">${it.icon}</span><span>${it.label}</span>${badge ? `<span class="nav-badge ${it.badgeType === "gray" ? "gray" : ""}">${badge}</span>` : ""}</a>`;
    }).join("")}</div>`).join("");
  }

  function render() {
    const key = titles[state.route] ? state.route : "overview";
    state.route = key;
    renderNav();
    $("#crumb").textContent = titles[key];
    view.innerHTML = pages[key]();
    if (key === "push") renderPushRows();
    if (key === "approvals") renderApprovalRows();
    if (key === "audit") renderAuditRows();
    document.title = `${titles[key]} · 军机驿 · 飞书 PC 控制台`;
    window.scrollTo({ top: 0 });
  }

  function routeFromHash() {
    const parts = (location.hash || "#/overview").replace(/^#\/?/, "").split("/");
    const name = parts[0] || "overview";
    state.route = titles[name] ? name : "overview";
    closeDrawer();
    render();
  }

  function showToast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => { toastEl.hidden = true; }, 2200);
  }

  function openDrawer(html) {
    drawer.innerHTML = html;
    drawer.hidden = false;
    scrim.hidden = false;
    requestAnimationFrame(() => drawer.classList.add("open"));
  }

  function closeDrawer() {
    drawer.classList.remove("open");
    drawer.hidden = true;
    scrim.hidden = true;
  }

  function openPush(id) {
    const p = pushes.find(x => x.id === id);
    if (!p) return;
    openDrawer(`<div class="drawer-head"><div class="drawer-top"><span class="drawer-kicker">PUSH TASK · ${p.id}</span><button class="drawer-close" data-action="close-drawer">×</button></div>
      <div class="drawer-title">${p.title}</div><div class="drawer-sub">${p.id} · ${p.template} · 创建人 ${p.sender}</div></div>
      <div class="drawer-body">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">${pill(p.status)}<span class="tag">${p.target}</span><span class="tag">${p.template}</span></div>
        <dl class="kv"><dt>目标组</dt><dd>${p.target}</dd><dt>发送者</dt><dd>${p.sender}</dd><dt>送达</dt><dd class="mono">${p.delivered}</dd><dt>延迟</dt><dd class="mono">${p.latency}</dd><dt>计划时间</dt><dd class="mono">${p.time}</dd><dt>摘要</dt><dd>${p.summary}</dd></dl>
        <div class="section-label">证据 / 决策</div>
        <div class="evidence"><b>决策账：</b>push_flow.jsonl#${p.id}<br><b>发送证据：</b>${p.status === "failed" ? "dead_letters.jsonl#DL-1107-014" : "api_accepted → " + (p.delivered !== "—" ? "receiver_verified" : "pending")}<br><b>边界：</b>真实发送只经 8097；页面按钮不直接调用发送接口。</div>
        <div class="section-label">时间线</div>
        <ul class="timeline"><li class="done">创建推送<time>${p.time}</time></li><li class="done">渲染卡片 / 预览<time>—</time></li><li class="done">审批记录<time>—</time></li>${p.status === "failed" ? '<li class="done">发送失败 → 死信<time>11:02</time></li>' : '<li class="done">发送 / 回执<time>' + p.time + '</time></li>'}</ul>
      </div>
      <div class="drawer-foot">${p.status === "failed" ? '<button class="btn danger" data-action="retry" data-id="DL-1107-014">重试失败任务</button>' : '<button class="btn">查看决策账</button>'} <button class="btn" data-action="close-drawer">关闭</button></div>`);
  }

  function openApproval(id) {
    const a = approvals.find(x => x.id === id);
    if (!a) return;
    const isPending = a.status === "pending";
    openDrawer(`<div class="drawer-head"><div class="drawer-top"><span class="drawer-kicker">APPROVAL · ${a.id}</span><button class="drawer-close" data-action="close-drawer">×</button></div>
      <div class="drawer-title">${a.title}</div><div class="drawer-sub">${a.id} · ${a.applicant} · ${a.time}</div></div>
      <div class="drawer-body">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">${pill(a.status)}<span class="tag ${a.risk === "中风险" ? "" : "blue"}">${a.risk}</span><span class="tag">${a.scope}</span></div>
        <dl class="kv"><dt>申请人</dt><dd>${a.applicant}</dd><dt>影响范围</dt><dd>${a.impact}</dd><dt>说明</dt><dd>${a.summary}</dd><dt>证据</dt><dd class="mono">${a.evidence}</dd></dl>
        <div class="section-label">审批时间线</div>
        <ul class="timeline"><li class="done">提交申请<time>${a.time} · 附加目标清单与授权行</time></li><li class="done">系统校验通过<time>${a.time} · 白名单格式 / 域隔离 / 配额均通过</time></li><li class="${isPending ? "" : "done"}">${isPending ? "等待审批人" : a.status === "approved" ? "审批通过" : "审批拒绝"}<time>${isPending ? "当前节点：OPS 负责人" : "已记录"}</time></li></ul>
        <div class="evidence">通过后只生成“已批准”记录；执行仍由 8097 / 决策账按闸门完成。原型按钮仅模拟状态，不产生真实审批。</div>
      </div>
      <div class="drawer-foot">${isPending ? `<button class="btn danger" data-action="approve" data-id="${a.id}" data-decision="rejected">拒绝</button><button class="btn primary" data-action="approve" data-id="${a.id}" data-decision="approved">通过</button>` : `<button class="btn" data-action="close-drawer">关闭</button>`}</div>`);
  }

  function openTarget(id) {
    const t = targets.find(x => x.id === id);
    if (!t) return;
    const pct = Math.round(t.members / t.quota * 100);
    openDrawer(`<div class="drawer-head"><div class="drawer-top"><span class="drawer-kicker">TARGET · ${t.id}</span><button class="drawer-close" data-action="close-drawer">×</button></div><div class="drawer-title">${t.name}</div><div class="drawer-sub">${t.id} · ${t.owner} · ${t.status}</div></div>
      <div class="drawer-body"><div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">${pill(t.status)}<span class="tag blue">${t.source}</span></div>
        <dl class="kv"><dt>成员</dt><dd class="mono">${fmtMembers(t.members)} 人</dd><dt>配额</dt><dd class="mono">${fmtMembers(t.quota)} 人</dd><dt>使用率</dt><dd><div class="progress" style="width:200px;display:inline-block;vertical-align:middle"><i style="width:${pct}%"></i></div> <span class="small">${pct}%</span></dd><dt>到期</dt><dd class="mono">${t.expires}</dd><dt>负责人</dt><dd>${t.owner}</dd></dl>
        <div class="section-label">变更边界</div><div class="evidence">目标变更必须走二级审批；不允许在移动端 / PC 页面直接改 token、群成员或配额。授权真源：<b>authorization.json</b>。</div>
      </div><div class="drawer-foot"><button class="btn primary" data-action="toast" data-text="变更申请已创建（原型）">申请变更</button><button class="btn" data-action="close-drawer">关闭</button></div>`);
  }

  function openDeadletter(id) {
    const d = deadletters.find(x => x.id === id);
    if (!d) return;
    openDrawer(`<div class="drawer-head"><div class="drawer-top"><span class="drawer-kicker">DEAD LETTER · ${d.id}</span><button class="drawer-close" data-action="close-drawer">×</button></div><div class="drawer-title">${d.reason}</div><div class="drawer-sub">${d.id} · ${d.task} · ${d.age}</div></div>
      <div class="drawer-body"><div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">${pill(d.status === "pending" ? "wait" : d.status)}<span class="tag">${d.attempts} 次尝试</span></div>
        <dl class="kv"><dt>原任务</dt><dd class="mono">${d.task}</dd><dt>失败原因</dt><dd>${d.reason}</dd><dt>最近错误</dt><dd class="mono">${d.error}</dd><dt>死信文件</dt><dd class="mono">dead_letters.jsonl#${d.id}</dd></dl>
        <div class="section-label">证据链</div><ul class="timeline"><li class="done">发送失败<time>—</time></li><li class="done">写入死信<time>—</time></li><li class="${d.status === "pending" ? "" : "done"}">${d.status === "pending" ? "等待重试审批" : "已处理"}<time>—</time></li></ul>
        <div class="evidence">重试视为一次新推送，必须进入审批与决策账；原型只做状态模拟。</div>
      </div><div class="drawer-foot">${d.status === "pending" ? `<button class="btn primary" data-action="retry" data-id="${d.id}">提报重试</button>` : ""}<button class="btn" data-action="close-drawer">关闭</button></div>`);
  }

  function openChannel(kind, id) {
    if (kind === "bot") {
      const b = bots.find(x => x.id === id); if (!b) return;
      openDrawer(`<div class="drawer-head"><div class="drawer-top"><span class="drawer-kicker">BOT · ${b.id}</span><button class="drawer-close" data-action="close-drawer">×</button></div><div class="drawer-title">${b.name}</div><div class="drawer-sub">${b.role} · 心跳 ${b.heartbeat}</div></div><div class="drawer-body"><div style="display:flex;gap:8px;margin-bottom:12px">${pill(b.status)}<span class="tag">${b.owner}</span></div><dl class="kv"><dt>身份 ID</dt><dd class="mono">${b.id}</dd><dt>角色</dt><dd>${b.role}</dd><dt>心跳</dt><dd class="mono">${b.heartbeat}</dd><dt>备注</dt><dd>${b.note}</dd></dl><div class="section-label">边界</div><div class="evidence">机器人身份唯一，不混用；配置变更需审批，不在页面直接改 token。</div></div><div class="drawer-foot"><button class="btn primary" data-action="toast" data-text="配置变更申请已创建（原型）">申请配置变更</button><button class="btn" data-action="close-drawer">关闭</button></div>`);
    } else {
      const r = routes.find(x => x.id === id); if (!r) return;
      openDrawer(`<div class="drawer-head"><div class="drawer-top"><span class="drawer-kicker">ROUTE · ${r.id}</span><button class="drawer-close" data-action="close-drawer">×</button></div><div class="drawer-title">${r.name}</div><div class="drawer-sub">${r.direction} · ${r.target}</div></div><div class="drawer-body"><div style="display:flex;gap:8px;margin-bottom:12px">${pill(r.status)}<span class="tag blue">${r.direction}</span></div><dl class="kv"><dt>路由 ID</dt><dd class="mono">${r.id}</dd><dt>目标</dt><dd class="mono">${r.target}</dd><dt>延迟</dt><dd class="mono">${r.latency}</dd></dl><div class="section-label">边界</div><div class="evidence">入向只走 W43 / 8091；出向唯一 8097；P2P 执行不走群广播。</div></div><div class="drawer-foot"><button class="btn" data-action="close-drawer">关闭</button></div>`);
    }
  }

  function openJob(id) {
    const j = jobs.find(x => x.id === id); if (!j) return;
    openDrawer(`<div class="drawer-head"><div class="drawer-top"><span class="drawer-kicker">JOB · ${j.id}</span><button class="drawer-close" data-action="close-drawer">×</button></div><div class="drawer-title">${j.name}</div><div class="drawer-sub">${j.schedule} · ${j.owner}</div></div><div class="drawer-body"><div style="display:flex;gap:8px;margin-bottom:12px">${pill(j.status)}<span class="tag blue">${j.id}</span></div><dl class="kv"><dt>排期</dt><dd>${j.schedule}</dd><dt>最近运行</dt><dd class="mono">${j.last}</dd><dt>下次运行</dt><dd class="mono">${j.next}</dd><dt>Manifest</dt><dd class="mono">${j.manifest}</dd></dl><div class="section-label">最近运行</div><ul class="timeline"><li class="done">今天 07:30 · 成功<time>128 成功 / 1 失败 · 耗时 42s</time></li><li class="done">昨天 07:30 · 成功<time>126 成功 / 0 失败 · 耗时 39s</time></li></ul><div class="evidence">任务必须有 owner、last_run、alert；暂停 / 恢复同样走审批，不直接改调度器。</div></div><div class="drawer-foot"><button class="btn primary" data-action="toast" data-text="暂停申请已创建（原型）">申请暂停</button><button class="btn" data-action="close-drawer">关闭</button></div>`);
  }

  function openAudit(id) {
    const a = audits.find(x => x.id === id); if (!a) return;
    openDrawer(`<div class="drawer-head"><div class="drawer-top"><span class="drawer-kicker">AUDIT · ${a.id}</span><button class="drawer-close" data-action="close-drawer">×</button></div><div class="drawer-title">${a.action}</div><div class="drawer-sub">${a.id} · ${a.time} · ${a.operator}</div></div><div class="drawer-body"><div style="display:flex;gap:8px;margin-bottom:12px">${pill("pending", "只读")}<span class="tag blue">${a.level}</span></div><dl class="kv"><dt>操作者</dt><dd>${a.operator}</dd><dt>目标</dt><dd class="mono">${a.target}</dd><dt>说明</dt><dd>${a.detail}</dd><dt>证据文件</dt><dd class="mono">audit_events.jsonl#${a.id}</dd><dt>证据等级</dt><dd class="mono">${a.level}</dd></dl><div class="evidence">审计只读，不可修改；真实导出需 L2 迁移后开放。</div></div><div class="drawer-foot"><button class="btn" data-action="close-drawer">关闭</button></div>`);
  }

  function openNewPush() {
    modalWrap.hidden = false;
    modalWrap.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-label="新建推送">
      <div class="modal-head"><div><div class="modal-title">新建推送</div><div class="modal-sub">预览 → 记录 → 审批 → 执行；原型不产生真实发送</div></div><button class="drawer-close" data-action="close-modal">×</button></div>
      <div class="modal-body">
        <div class="form-row"><div class="field"><label for="npTarget">目标组</label><select id="npTarget"><option>核心订阅组 · 128 人</option><option>客户域 / 白名单 · 40 人</option><option>策略观察组 · 42 人</option></select></div>
        <div class="field"><label for="npTemplate">模板</label><select id="npTemplate"><option>盘前晨报</option><option>JEV 早盘</option><option>飞书卡片</option><option>飞书文本</option></select></div></div>
        <div class="field"><label for="npTitle">标题</label><input id="npTitle" value="盘前晨报 · 11月7日" maxlength="80"></div>
        <div class="field"><label for="npSummary">摘要</label><textarea id="npSummary">今日盘前：政策、产业与全球市场三条主线。</textarea></div>
        <div class="preview-box"><div class="p-kicker">卡片预览 · FEISHU CARD</div><div class="p-title" id="npPreviewTitle">盘前晨报 · 11月7日</div><div class="p-sub" id="npPreviewSub">今日盘前：政策、产业与全球市场三条主线。</div><div class="p-foot" id="npPreviewFoot">核心订阅组 · 128 人 · 提交后待审批</div></div>
      </div>
      <div class="modal-foot"><button class="btn" data-action="close-modal">取消</button><button class="btn primary" data-action="submit-new-push">提交审批</button></div>
    </div>`;
    const title = $("#npTitle", modalWrap), summary = $("#npSummary", modalWrap), target = $("#npTarget", modalWrap);
    const sync = () => {
      $("#npPreviewTitle", modalWrap).textContent = title.value || "未命名推送";
      $("#npPreviewSub", modalWrap).textContent = summary.value || "（无摘要）";
      $("#npPreviewFoot", modalWrap).textContent = `${target.value} · 提交后待审批`;
    };
    [title, summary, target].forEach(x => x.addEventListener("input", sync));
    target.addEventListener("change", sync);
    sync();
  }

  function closeModal() { modalWrap.hidden = true; modalWrap.innerHTML = ""; }

  function approve(id, decision) {
    const a = approvals.find(x => x.id === id);
    if (!a) return;
    a.status = decision;
    closeDrawer();
    render();
    showToast(decision === "approved" ? "已通过（原型记录）· 执行仍由 8097 按决策账完成" : "已拒绝（原型记录）");
  }

  function retryDeadletter(id) {
    const d = deadletters.find(x => x.id === id);
    if (!d) return;
    d.status = "pending"; d.attempts += 1; d.age = "刚刚";
    closeDrawer(); render(); showToast("已提报重试（原型）· 需要审批后才会执行");
  }

  function handleAction(action, targetEl) {
    const id = targetEl.dataset.id;
    switch (action) {
      case "refresh": render(); showToast("已刷新（原型数据）"); break;
      case "new-push": openNewPush(); break;
      case "close-modal": closeModal(); break;
      case "submit-new-push": {
        const title = $("#npTitle", modalWrap)?.value.trim() || "未命名推送";
        const target = $("#npTarget", modalWrap)?.value || "核心订阅组";
        const summary = $("#npSummary", modalWrap)?.value.trim() || "";
        const id = `APR-1107-${String(approvals.length + 1).padStart(3, "0")}`;
        approvals.unshift({ id, title: `新建推送：${title}`, applicant: "OPS1", status: "pending", risk: "低风险", time: "刚刚", scope: "推送 · 一级审批", impact: target, summary, evidence: "预览记录（原型）" });
        closeModal(); location.hash = "#/approvals"; render(); showToast("已生成待审批记录（原型）· 未发送");
        break;
      }
      case "push-detail": openPush(id); break;
      case "approval-detail": openApproval(id); break;
      case "target-detail": openTarget(id); break;
      case "deadletter-detail": openDeadletter(id); break;
      case "route-detail": openChannel("route", id); break;
      case "job-detail": openJob(id); break;
      case "audit-detail": openAudit(id); break;
      case "close-drawer": closeDrawer(); break;
      case "push-filter": state.pushFilter = targetEl.dataset.filter; render(); break;
      case "approval-filter": state.approvalFilter = targetEl.dataset.filter; render(); break;
      case "audit-filter": state.auditFilter = targetEl.dataset.filter; render(); break;
      case "approve": approve(id, targetEl.dataset.decision); break;
      case "retry": retryDeadletter(id); break;
      case "toast": showToast(targetEl.dataset.text || "已记录（原型）"); break;
      case "deadletter-list": location.hash = "#/deadletters"; break;
      default: break;
    }
  }

  document.addEventListener("click", (e) => {
    const targetEl = e.target.closest("[data-action]");
    if (!targetEl) {
      if (e.target.closest(".scrim")) { closeDrawer(); closeModal(); }
      return;
    }
    e.preventDefault();
    handleAction(targetEl.dataset.action, targetEl);
  });

  document.addEventListener("input", (e) => {
    if (e.target.id === "pushQuery") { state.pushQuery = e.target.value; renderPushRows(); }
    if (e.target.id === "globalSearch") { state.globalQuery = e.target.value.trim(); }
  });

  window.addEventListener("hashchange", routeFromHash);

  // boot
  if (!location.hash) location.hash = "#/overview";
  routeFromHash();
})();
