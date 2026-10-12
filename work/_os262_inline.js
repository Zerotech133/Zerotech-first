
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  function set(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
  function tick() { set('clock', new Date().toLocaleTimeString('zh-CN', { hour12: false })); }
  tick(); setInterval(tick, 1000);

  // ── 💼 个人模拟资产 · 分市场账户（K-STOCK-262）──
  function renderAccounts(ac) {
    const tiles = document.getElementById('acct-tiles'),
          rows  = document.getElementById('acct-rows');
    if (!tiles || !rows) return;
    if (!ac || !ac.markets) { tiles.innerHTML = '<div class="dim" style="font-size:12px">账户矩阵暂不可用</div>'; return; }
    const fmt = n => (n ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
    let th = '', tr = '';
    Object.values(ac.markets).forEach(m => {
      const ret = m.initial ? ((m.total - m.initial) / m.initial * 100) : 0;
      const retCls = ret > 0.005 ? 'up' : (ret < -0.005 ? 'dn' : '');
      th += '<div class="tile"><div class="v num">' + (m.opened ? '¥' + fmt(m.total) : '<span class="dim">未开立</span>')
          + '</div><div class="k">' + m.name + '</div><div class="sub">'
          + (m.opened ? (m.accounts.length + ' 账户 · 持仓 ' + m.positions + ' · 收益 <b class="' + retCls + '">' + (ret >= 0 ? '+' : '') + ret.toFixed(2) + '%</b>') : '待接入')
          + '</div></div>';
      m.accounts.forEach(x => {
        const xr = x.ret_pct, xc = xr > 0.005 ? 'up' : (xr < -0.005 ? 'dn' : '');
        tr += '<div class="arow"><span class="an">' + esc(x.id) + '</span>'
            + '<span>现金 <b class="num">¥' + fmt(x.cash) + '</b></span>'
            + '<span>市值 <b class="num">¥' + fmt(x.mv) + '</b></span>'
            + '<span>仓位 <b class="num">' + ((x.pos_pct || 0) * 100).toFixed(1) + '%</b></span>'
            + '<span>收益 <b class="num ' + xc + '">' + (xr >= 0 ? '+' : '') + xr.toFixed(2) + '%</b></span>'
            + '<span class="dim">截至 ' + esc(x.as_of || '—') + '</span></div>';
      });
    });
    tiles.innerHTML = th;
    rows.innerHTML = tr || '<div class="dim" style="font-size:12px">暂无账户</div>';
  }

  const REGIME_CN = { trend_up: ['趋势上行', 'var(--color-up)'], range: ['区间震荡', '#8b949e'],
                      rotation: ['轮动', 'var(--pur-500)'], trend_down: ['趋势下行', 'var(--color-dn)'] };

  // ── 核心：/api/os（七域一炮：采集/Agent/量化/入库/产出/研发/待办）──
  async function loadOS() {
    try {
      const ctl = new AbortController(); setTimeout(() => ctl.abort(), 8000);
      const r = await fetch('/api/os', { signal: ctl.signal, cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      set('f-ts', d.ts || '--');
      const c = d.collect || {}, a = d.agents || {}, p = d.paper || {}, q = d.qd || {},
            s = d.strategies || {}, w = d.warehouse || {}, o = d.output || {},
            cd = d.coding || {}, td = d.todo || {};
      renderAccounts(d.accounts);
      // 今日一览六数
      set('v-collect', c.today ?? 0);
      set('v-collect-sub', '已判 ' + (c.judged ?? 0));
      const outCnt = [o.brief, o.ai_review].filter(x => x && x.ok).length
        + [a.daily_report, a.a0_review].filter(Boolean).length
        + ((o.picks && o.picks.total > 0) ? 1 : 0)
        + ((o.strong && o.strong.items > 0) ? 1 : 0);
      set('v-output', outCnt + ' 件');
      set('v-output-sub', '晨报/日报/点评/选股池/强弱');
      set('v-commit', cd.commits_today ?? 0);
      set('v-commit-sub', cd.last_commit ? '最近 ' + cd.last_commit : '今日暂无提交');
      const revCnt = (a.judge_total ?? 0) + (a.plan_total ?? 0) + (o.verdicts ? 1 : 0);
      set('v-review', revCnt);
      set('v-review-sub', '判决/任务单/复盘判定');
      const todoSum = (td.health ?? 0) + (td.biz_remind ?? 0) + (td.notes_doing ?? 0);
      set('v-todo', todoSum);
      set('v-todo-sub', '亮牌 ' + (td.health ?? 0) + ' · 提醒 ' + (td.biz_remind ?? 0) + ' · 在研 ' + (td.notes_doing ?? 0));
      const tTile = document.getElementById('v-todo-tile');
      if (tTile) tTile.className = 'tile' + (todoSum > 0 ? ' warn' : '');
      set('v-trade', (p.orders_yday ?? 0) + (p.orders_today ?? 0));
      set('v-trade-sub', '昨 ' + (p.orders_yday ?? 0) + ' · 今 ' + (p.orders_today ?? 0));
      // ① 情报
      set('c-today', c.today ?? 0); set('c-judged', c.judged ?? 0);
      set('c-total', (c.total ?? 0) + ' 条');
      // ② 今日产出
      const bf = o.brief || {};
      const bTag = document.getElementById('o-brief-tag');
      if (bTag) { bTag.textContent = bf.ok ? '已推送' : '未出'; bTag.className = bf.ok ? 'tag-ok' : 'tag-no'; }
      set('o-brief', bf.ts ? String(bf.ts).slice(5, 16) + ' 产出 · 汇聚 ' + (bf.news_count ?? '—') + ' 条新闻' : '晨报未生成');
      const pm = o.premarket || {};
      set('o-pre', (pm.verdict || '—') + (pm.prob ? '（' + pm.prob + '%）' : ''));
      set('o-daily', a.daily_report ? '最新 ' + a.daily_report.replace('daily_report_', '').replace('.md', '') : '暂无产物');
      set('o-review', a.a0_review ? '最新 ' + a.a0_review.replace('a0_review_', '').replace('.md', '') : '暂无产物');
      const vr = o.ai_review || {};
      const aTag = document.getElementById('o-airev-tag');
      if (aTag) { aTag.textContent = vr.ok ? '已出' : '未出'; aTag.className = vr.ok ? 'tag-ok' : 'tag-no'; }
      set('o-airev', vr.date ? String(vr.date) + ' 复盘已生成' : 'AI 复盘未生成');
      const pk = o.picks || {};
      set('o-picks', (pk.total ?? 0).toLocaleString() + ' 只 · T1共振 ' + (pk.t1 ?? 0) + ' / T2高价值 ' + (pk.t2 ?? 0) + ' / T3单点 ' + (pk.t3 ?? 0));
      set('o-verdict', (o.verdicts ?? 0).toLocaleString() + ' 只已判定 · 卖点留痕');
      const sg = o.strong || {};
      set('o-strong', sg.date ? String(sg.date) + ' · ' + (sg.items ?? 0).toLocaleString() + ' 项' : '未生成');
      set('o-gzh', (o.gzh ?? 0) + ' 篇在库 · 选题→成稿流水线');
      set('o-video', (o.video ?? 0) + ' 条成片在库');
      // ③ 研发
      set('d-commits', cd.commits_today ?? 0);
      set('d-commits-sub', cd.last_commit ? '主仓库 · 最近 ' + String(cd.last_commit).split(' ')[0] : '主仓库 · 今日暂无提交');
      set('d-last', cd.last_commit ? String(cd.last_commit).split(' ')[0] : '—');
      set('d-last-sub', cd.last_commit ? String(cd.last_commit).split(' ').slice(1).join(' ') : '');
      set('d-plan', a.plan_total ?? 0);
      set('d-plan-sub', '今日新增 ' + (a.plan_today ?? 0));
      // ④ 复核
      set('r-judge', '累计 ' + (a.judge_total ?? 0) + ' 件 · 今日 ' + (a.judge_today ?? 0));
      set('r-plan', '累计 ' + (a.plan_total ?? 0) + ' 单 · 今日 ' + (a.plan_today ?? 0));
      set('r-a0', a.a0_review ? '最新 ' + a.a0_review.replace('a0_review_', '').replace('.md', '') : '暂无');
      // ⑤ 量化
      set('q-live', (s.live ?? 0) + ' live');
      set('q-strat-sub', '影子 ' + (s.shadow ?? 0) + ' · 工具 ' + (s.tool ?? 0) + ' · 在册 ' + (s.total ?? 0));
      set('q-scan', (q.scanned ?? 0).toLocaleString() + ' 只');
      set('q-scan-sub', q.ts ? String(q.ts).slice(5, 16) : '快照未生成');
      set('q-star', (q.star_pool_n ?? 0).toLocaleString());
      set('q-orders', p.orders_total ?? 0);
      set('q-orders-sub', '昨日 ' + (p.orders_yday ?? 0) + ' · 今日 ' + (p.orders_today ?? 0));
      set('q-pos', p.positions ?? 0);
      set('w-strat', (s.total ?? 0) + ' 条');
      const rg = q.regime || {};
      const keys = Object.keys(REGIME_CN).filter(k => rg[k] > 0);
      const tot = keys.reduce((x, k) => x + rg[k], 0);
      const bar = document.getElementById('q-regime');
      const lg = document.getElementById('q-regime-legend');
      if (bar && tot) {
        bar.innerHTML = keys.map(k => {
          const pct = rg[k] / tot * 100;
          return '<div style="width:' + pct + '%;background:' + REGIME_CN[k][1] + '">' + (pct > 8 ? Math.round(pct) + '%' : '') + '</div>';
        }).join('');
        lg.innerHTML = keys.map(k => '<span>' + REGIME_CN[k][0] + ' <b>' + rg[k].toLocaleString() + '</b></span>').join('');
        set('q-regime-ts', q.ts ? '· ' + String(q.ts).slice(5, 16) : '');
      }
      // ⑥ 入库
      set('w-reports', w.reports_total ?? 0);
      set('w-reports-sub', '今日更新 ' + (w.reports_today ?? 0) + ' 件');
      set('w-kg', w.kg_stale ?? 0);
      set('w-kg-sub', w.catalog_age_days === 0 ? '目录今日已巡检' : '目录龄 ' + (w.catalog_age_days ?? '?') + ' 天');
      const ktile = document.getElementById('w-kg-tile');
      if (ktile) ktile.className = 'tile' + ((w.kg_stale ?? 0) > 0 ? ' warn' : '');
      // ⑦ 待办
      set('t-health', td.health ?? 0);
      set('t-remind', td.biz_remind ?? 0);
      set('t-notes', td.notes_doing ?? 0);
      const htile = document.getElementById('t-health-tile');
      if (htile) htile.className = 'tile' + ((td.health ?? 0) > 0 ? ' warn' : '');
    } catch {
      set('f-ts', '聚合接口未就绪');
    }
    // brief 补采集分布
    try {
      const ctl = new AbortController(); setTimeout(() => ctl.abort(), 8000);
      const r = await fetch('/api/brief', { signal: ctl.signal, cache: 'no-store' });
      if (r.ok) {
        const n = ((await r.json()).intel) || {};
        set('c-hit', n.hit ?? 0);
        set('c-bull', n.bull ?? 0); set('c-bear', n.bear ?? 0);
        set('c-neutral', '中性 ' + (n.neutral ?? 0));
      }
    } catch {}
  }

  // ── 此刻（/api/home）──
  async function loadHome() {
    try {
      const ctl = new AbortController(); setTimeout(() => ctl.abort(), 8000);
      const r = await fetch('/api/home', { signal: ctl.signal, cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      const nc = d.now_context || {};
      set('now-mode', nc.label || d.now || '此刻');
      set('now-focus', (nc.focus || []).join(' · '));
    } catch {}
  }

  // ── 任务矩阵 + 容器 + 备份 + 告警（/api/overview，首次聚合可能慢）──
  async function loadOverview() {
    try {
      const ctl = new AbortController(); setTimeout(() => ctl.abort(), 45000);
      const r = await fetch('/api/overview', { signal: ctl.signal, cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      const today = new Date().toISOString().slice(0, 10);
      const tasks = d.tasks || [];
      const ranToday = tasks.filter(t => String(t.lastrun || '').slice(0, 10) === today).length;
      const bad = tasks.filter(t => String(t.last) !== '0' && String(t.last) !== '267011').length;
      set('t-tasks', bad);
      set('t-tasks-sub', '今日已跑 ' + ranToday + '/' + tasks.length);
      let run = 0, tot = 0;
      Object.values(d.containers || {}).forEach(v => { run += v.running; tot += v.total; });
      set('d-cont', run + '/' + tot);
      set('w-backup', String((d.backups || {}).latest || '').split('_')[0].slice(5) || '—');
      set('t-alerts', (d.alerts || {}).pending ?? 0);
    } catch { set('t-tasks', '—'); }
  }

  // ── 模拟仓资产 + 对账（/api/paper_status）──
  async function loadPaperStatus() {
    try {
      const ctl = new AbortController(); setTimeout(() => ctl.abort(), 8000);
      const r = await fetch('/api/paper_status', { signal: ctl.signal, cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      if (!d.ok) return;
      const L = d.latest || {};
      set('q-equity', '¥' + (Math.round((L.total || 0) / 1000) / 10).toFixed(1) + ' 万');
      const v = L.cum_pct ?? 0;
      const el = document.getElementById('q-equity-sub');
      if (el) {
        el.textContent = '累计 ' + (v > 0 ? '+' : '') + v.toFixed(2) + '%';
        el.style.color = v > 0 ? 'var(--color-up)' : (v < 0 ? 'var(--color-dn)' : '');
      }
      set('r-verify', (L.verify_ok ? '✅ 对账一致' : '⚠️ 对账异常') + ' · 快照 ' + String(L.date || '').slice(5));
    } catch { set('r-verify', '数据未就绪'); }
  }

  // ── 本地模型数 ──
  async function countModels() {
    try {
      const ctl = new AbortController(); setTimeout(() => ctl.abort(), 3000);
      const r = await fetch('http://localhost:11434/api/tags', { signal: ctl.signal, cache: 'no-store' });
      const j = await r.json();
      set('w-models', j.models.length + ' 个');
    } catch { set('w-models', '离线'); }
  }

  loadOS(); loadHome(); loadOverview(); loadPaperStatus(); countModels();
  setInterval(loadOS, 120000); setInterval(loadHome, 120000);
  setInterval(loadOverview, 120000); setInterval(loadPaperStatus, 120000);
