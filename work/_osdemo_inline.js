
  const fmt = n => (n ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
  const $ = id => document.getElementById(id);
  setInterval(() => {
    const d = new Date();
    $('clock').textContent = d.toLocaleTimeString('zh-CN', { hour12: false });
    $('date').textContent = d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' });
  }, 1000);

  const AXC = 'rgba(125,146,184,.75)';
  const mkAxis = { axisLine: { lineStyle: { color: AXC } }, axisLabel: { color: AXC, fontSize: 11 },
                   splitLine: { lineStyle: { color: 'rgba(72,120,200,.12)' } } };
  const charts = {};
  function ch(id) { charts[id] = charts[id] || echarts.init($(id)); return charts[id]; }
  addEventListener('resize', () => Object.values(charts).forEach(c => c.resize()));

  // ── 核心：/api/os + /api/paper_status 双源 ──
  async function load() {
    let d = {}, ps = {};
    try { d = await (await fetch('/api/os', { cache: 'no-store' })).json(); } catch {}
    try { ps = await (await fetch('/api/paper_status', { cache: 'no-store' })).json(); } catch {}

    const c = d.collect || {}, p = d.paper || {}, q = d.qd || {},
          cd = d.coding || {}, td = d.todo || {}, ac = d.accounts || {}, o = d.output || {};
    // KPI
    $('k-collect').textContent = fmt(c.today); $('k-collect-s').textContent = '已判 ' + fmt(c.judged) + ' · 总库 ' + fmt(c.total);
    $('k-commit').textContent = fmt(cd.commits_today); $('k-commit-s').textContent = cd.last_commit ? ('最近 ' + cd.last_commit) : '今日';
    $('k-orders').textContent = fmt(p.orders_total); $('k-orders-s').textContent = '今日 ' + fmt(p.orders_today) + ' · 昨日 ' + fmt(p.orders_yday);
    const markets = ac.markets || {};
    const totalAll = Object.values(markets).reduce((s, m) => s + (m.total || 0), 0);
    $('k-assets').innerHTML = '¥<small style="font-size:20px">' + fmt(totalAll) + '</small>';
    $('k-scan').textContent = fmt(q.scanned); $('k-scan-s').textContent = '星池 ' + fmt(q.star_pool_n) + ' · 跳过 ' + fmt(q.skipped);
    const todoN = (td.health || 0) + (td.biz_remind || 0) + (td.notes_doing || 0);
    $('k-todo').textContent = fmt(todoN); $('k-todo-s').textContent = '健康 ' + fmt(td.health) + ' · 提醒 ' + fmt(td.biz_remind) + ' · 在研 ' + fmt(td.notes_doing);

    // ① 市场分布环形
    const mcolor = { cn: '#35d0ff', us: '#4d8bff', hk: '#b58bff', crypto: '#ffc53d', commodities: '#ff8f5d' };
    const dist = Object.entries(markets).filter(([, m]) => m.opened)
      .map(([k, m]) => ({ name: m.name, value: Math.round(m.total), itemStyle: { color: mcolor[k] } }));
    ch('ch-dist').setOption({
      tooltip: { trigger: 'item', formatter: '{b}<br/>¥{c}（{d}%）' },
      legend: { bottom: 0, textStyle: { color: AXC, fontSize: 11 }, itemWidth: 10, itemHeight: 10 },
      series: [{ type: 'pie', radius: ['52%', '74%'], center: ['50%', '44%'],
        label: { color: '#dbe7ff', fontSize: 12, formatter: '{b}\n{d}%' },
        labelLine: { lineStyle: { color: AXC } }, itemStyle: { borderColor: '#060a14', borderWidth: 2 },
        data: dist.length ? dist : [{ name: '暂无数据', value: 1, itemStyle: { color: '#1a2745' } }] }]
    });

    // ② 净值曲线（/api/paper_status.curve）
    const curve = ps.curve || [];
    $('nav-r').textContent = (curve.length ? ('截至 ' + curve[curve.length - 1].date + ' · ') : '') + '基准 ¥1,000,000';
    ch('ch-nav').setOption({
      grid: { left: 70, right: 20, top: 18, bottom: 26 },
      xAxis: { type: 'category', data: curve.map(x => x.date), ...mkAxis, boundaryGap: false },
      yAxis: { type: 'value', scale: true, ...mkAxis,
        axisLabel: { ...mkAxis.axisLabel, formatter: v => (v / 10000).toFixed(0) + '万' } },
      series: [{ type: 'line', data: curve.map(x => x.total), smooth: true, symbol: 'circle', symbolSize: 7,
        lineStyle: { width: 2.5, color: '#35d0ff', shadowColor: 'rgba(53,208,255,.5)', shadowBlur: 12 },
        itemStyle: { color: '#35d0ff', borderColor: '#fff', borderWidth: 1.5 },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(53,208,255,.32)' }, { offset: 1, color: 'rgba(53,208,255,0)' }]) },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: 'rgba(255,197,61,.5)', type: 'dashed' },
          label: { color: '#ffc53d', fontSize: 10, formatter: '基准' }, data: [{ yAxis: 1000000 }] } }]
    });

    // ③ 账户对比柱（初始 vs 当前）
    const acts = Object.values(markets).flatMap(m => m.accounts || []);
    ch('ch-bar').setOption({
      grid: { left: 90, right: 20, top: 18, bottom: 24 },
      tooltip: { trigger: 'axis', valueFormatter: v => '¥' + fmt(v) },
      xAxis: { type: 'value', ...mkAxis, axisLabel: { ...mkAxis.axisLabel, formatter: v => (v / 10000) + '万' } },
      yAxis: { type: 'category', data: acts.map(x => x.id).reverse(), ...mkAxis },
      series: [
        { name: '初始', type: 'bar', stack: 'x', barWidth: 13, itemStyle: { color: 'rgba(77,139,255,.35)' },
          data: acts.map(x => x.initial).reverse() },
        { name: '当前', type: 'bar', stack: 'x', barWidth: 13,
          itemStyle: { color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [{ offset: 0, color: '#35d0ff' }, { offset: 1, color: '#4d8bff' }]) },
          data: acts.map(x => x.total).reverse() }]
    });

    // ④ Regime 四分层
    const rg = q.regime || {};
    const RC = [['trend_up', '趋势上行', '#ff5d5d'], ['range', '区间震荡', '#8b949e'],
                ['rotation', '轮动', '#b58bff'], ['trend_down', '趋势下行', '#3ddc97']];
    const rgv = RC.map(([k]) => rg[k] || 0), rgt = rgv.reduce((a, b) => a + b, 0) || 1;
    $('regime-r').textContent = rg.ts ? ('截至 ' + String(rg.ts).slice(5, 16)) : '';
    ch('ch-regime').setOption({
      grid: { left: 10, right: 10, top: 30, bottom: 4 },
      xAxis: { type: 'value', show: false, max: rgt },
      yAxis: { type: 'category', data: [''], show: false },
      series: RC.map(([k, n, col], i) => ({ name: n, type: 'bar', stack: 'r', barWidth: 26,
        itemStyle: { color: col, shadowColor: col, shadowBlur: 8 },
        label: { show: true, position: 'inside', color: '#060a14', fontWeight: 700, fontSize: 11,
          formatter: p => rgv[p.dataIndex] >= rgt * .08 ? (n + ' ' + rgv[p.dataIndex]) : '' },
        data: [rgv[i]] }))
    });

    // ⑤ 今日产出构成（横向柱，output 实测字段）
    const out = [
      ['卖点判定', o.verdicts || 0],
      ['选股池', (o.picks && o.picks.total) || 0],
      ['板块强弱', (o.strong && o.strong.items) || 0],
      ['晨报汇聚', (o.brief && o.brief.news_count) || 0],
      ['公众号草稿', o.gzh || 0],
      ['成片', o.video || 0],
      ['AI 提交', cd.commits_today || 0]];
    ch('ch-out').setOption({
      grid: { left: 76, right: 40, top: 10, bottom: 22 },
      xAxis: { type: 'value', ...mkAxis },
      yAxis: { type: 'category', data: out.map(x => x[0]).reverse(), ...mkAxis },
      series: [{ type: 'bar', barWidth: 12, data: out.map(x => x[1]).reverse(),
        itemStyle: { color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [{ offset: 0, color: '#ffc53d' }, { offset: 1, color: 'rgba(255,197,61,.15)' }]),
          borderRadius: [0, 6, 6, 0] },
        label: { show: true, position: 'right', color: '#dbe7ff', fontSize: 11, fontFamily: 'Consolas' } }]
    });

    // ⑥ 账户明细行
    $('acct-rows').innerHTML = acts.map(x => {
      const r = x.ret_pct || 0, cls = r > 0.005 ? 'up' : (r < -0.005 ? 'dn' : '');
      return '<div class="arow"><span class="an">' + x.id + '</span>'
        + '<span>总 <b>¥' + fmt(x.total) + '</b></span>'
        + '<span>仓位 <b>' + ((x.pos_pct || 0) * 100).toFixed(1) + '%</b></span>'
        + '<span>收益 <b class="' + cls + '">' + (r >= 0 ? '+' : '') + r.toFixed(2) + '%</b></span></div>';
    }).join('') || '<div class="dim" style="font-size:12px">暂无账户</div>';

    // ⑦ Agent 动向
    const a = d.agents || {};
    const ag = [
      ['🧠', '参谋长 A0', a.chief ? (a.chief + ' · 统领全站') : '组织架构顶点', '—', ''],
      ['📄', '影子日报', a.daily_report ? '最新 ' + a.daily_report : '今日未出', '—', ''],
      ['⚖️', 'A0 点评', a.a0_review ? '最新 ' + a.a0_review : '今日未出', '—', ''],
      ['📋', '岗位任务单', '累计 ' + fmt(a.plan_total) + ' 单', '今日 ' + fmt(a.plan_today), ''],
      ['🔍', '判决复核', '累计 ' + fmt(a.judge_total) + ' 件', '今日 ' + fmt(a.judge_today), ''],
      ['💻', 'AI 研发', '今日提交 ' + fmt(cd.commits_today) + ' 笔', cd.last_commit || '', '']];
    $('ag-rows').innerHTML = ag.map(([i, n, ds, r]) =>
      '<div class="agrow"><span class="ico">' + i + '</span><span><div class="n">' + n + '</div><div class="d">' + ds + '</div></span>'
      + '<span class="r dim">' + r + '</span></div>').join('');

    // ⑨ 商务 · 出行（biz.json：未来 7 天出行/会议 + 下次提醒 + 纪要在库）
    const bz = d.biz || {};
    const KIND = { trips: ['🚗', '出行'], meetings: ['🤝', '会议'] };
    let bizHtml = (bz.near || []).map(x => {
      const [ico, kind] = x.where ? KIND.trips : KIND.meetings;
      return '<div class="agrow"><span class="ico">' + ico + '</span><span>'
        + '<div class="n">' + kind + ' · ' + esc(x.where || x.who || '') + '</div>'
        + '<div class="d">' + esc(x.date || '') + ' · ' + esc(x.topic || x.ideas || '') + '</div></span></div>';
    }).join('');
    if (bz.remind_next) bizHtml += '<div class="agrow"><span class="ico">⏰</span><span><div class="n">AI 定时提醒</div><div class="d">下次 ' + esc(bz.remind_next) + '</div></span></div>';
    bizHtml += '<div class="agrow"><span class="ico">📝</span><span><div class="n">会议纪要在库</div><div class="d">' + fmt(bz.minutes_total) + ' 份 · 节点自动提醒</div></span></div>';
    $('biz-rows').innerHTML = bizHtml || '<div class="dim" style="font-size:12px">近 7 天无出行/会议安排</div>';
    $('biz-r').textContent = '出行 ' + fmt(bz.trips_total) + ' · 会议 ' + fmt(bz.meetings_total);

    // ⑩ 学习 · 内容创作（在研文稿实锤 + 学院域如实占位）
    const notes = bz.notes || [];
    let ln = notes.map(x =>
      '<div class="agrow"><span class="ico">📄</span><span>'
      + '<div class="n">' + esc(x.title || '') + '</div>'
      + '<div class="d">' + esc(x.type || '') + ' · 在研 · 更新 ' + esc(x.updated || '') + '</div></span></div>').join('');
    ln += '<div class="agrow"><span class="ico">✍️</span><span><div class="n">公众号草稿 ' + fmt(o.gzh || 0) + ' 篇</div><div class="d">选题→成稿流水线在库</div></span></div>';
    ln += '<div class="agrow"><span class="ico">🎬</span><span><div class="n">短视频成片 ' + fmt(o.video || 0) + ' 条</div><div class="d">创作流水线</div></span></div>';
    if (!(bz.learn || {}).ready)
      ln += '<div class="agrow"><span class="ico">🎓</span><span><div class="n">学习域建设中</div><div class="d">零界学院课程/笔记数据源待接入</div></span></div>';
    $('learn-rows').innerHTML = ln;
    $('learn-r').textContent = '在研 ' + notes.length + ' / 共 ' + fmt(bz.notes_total) + ' 篇';

    // ⑧ 底部跑马灯
    const items = [
      '📥 今日采集 <b>' + fmt(c.today) + '</b> 条，已研判 <b>' + fmt(c.judged) + '</b>',
      '📤 今日产出：晨报 · 盘前研判 · 影子日报 · A0 点评 · 选股池 <b>' + fmt((o.picks || {}).total) + '</b>',
      '💻 AI 敲码提交 <b>' + fmt(cd.commits_today) + '</b> 笔',
      '📈 双刀全池扫描 <b>' + fmt(q.scanned) + '</b> 只 · 星池 <b>' + fmt(q.star_pool_n) + '</b>',
      '💰 模拟资产总值 <b>¥' + fmt(totalAll) + '</b>（五市场）',
      '🚗 商务：近 7 天出行/会议 <b>' + fmt((bz.near || []).length) + '</b> 项' + (bz.remind_next ? ' · 下次提醒 <b>' + bz.remind_next + '</b>' : ''),
      '📚 在研文稿 <b>' + fmt(bz.notes_total) + '</b> 篇 · 草稿 <b>' + fmt(o.gzh || 0) + '</b>',
      '✅ 待办亮牌 <b>' + fmt(todoN) + '</b> 项'];
    $('ticker-move').innerHTML = items.map(s => '<span>' + s + '</span>').join('') + items.map(s => '<span>' + s + '</span>').join('');
  }
  load(); setInterval(load, 120000);
