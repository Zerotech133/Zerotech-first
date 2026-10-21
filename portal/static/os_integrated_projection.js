/* 只读投影；updatedAt 是观察时间，asOf 只来自业务行的真实时间。 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.OSIntegratedProjection = api;
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  var endpoints = { os: '/api/os', home: '/api/home', alerts: '/api/alerts', notify: '/api/notify/queue', tasks: '/api/org/tasks', capability: '/api/capability/board' };
  var entries = [
    ['dev', '开发', '/dev'], ['legion', '组织', '/legion'],
    ['learn', '学习', '/learn'], ['creator', '创作', '/creator'],
    ['quant', '量化', '/quant'], ['intel', '情报', '/intel'],
    ['os-entry', '个人 OS', '/os-entry'], ['os-board', 'OS 大屏', '/os-board']
  ];
  function obj(x) { return x !== null && typeof x === 'object' && !Array.isArray(x); }
  function text(x, fallback) { return typeof x === 'string' && x.trim() ? x.trim() : (fallback || ''); }
  function day(x) {
    if (typeof x !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(x)) return null;
    var d = new Date(x + 'T00:00:00Z');
    return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === x ? x : null;
  }
  function businessTime(x) {
    if (typeof x !== 'string') return null;
    var s = x.trim();
    var match = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?(?:([+-])(\d{2}):(\d{2})|Z)?$/.exec(s);
    if (!match || !day(match[1]) || +match[2] > 23 || +match[3] > 59 || +(match[4] || 0) > 59 ||
        (match[5] && (+match[6] > 23 || +match[7] > 59))) return null;
    if (match[5] || s.endsWith('Z')) {
      var parsed = new Date(s.replace(' ', 'T'));
      return isNaN(parsed.getTime()) ? null : parsed.toISOString();
    }
    return s.replace(' ', 'T') + '+08:00';
  }
  function beijingDay(date) {
    return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
  }
  function targetDay(x, year) {
    if (day(x)) return x;
    if (typeof x === 'string' && /^\d{2}-\d{2}$/.test(x)) return day(year + '-' + x);
    return null;
  }
  function within(x, today, days) {
    var d = day(x);
    if (!d) return false;
    var diff = (Date.parse(d + 'T00:00:00Z') - Date.parse(today + 'T00:00:00Z')) / 86400000;
    return diff >= 0 && diff <= days;
  }
  function item(id, title, summary, status, source, asOf, detail, href, priority) {
    return { id: id, title: title, summary: summary, status: status, source: source,
      asOf: asOf || null, detail: detail, href: href || null, priority: priority };
  }
  function state(key, status, detail) {
    return item('source:' + key, key, detail, status, endpoints[key], null, detail, null, status === 'ok' ? 3 : 1);
  }
  function build(raw, now) {
    raw = obj(raw) ? raw : {};
    var clock = now === undefined ? new Date() : new Date(now);
    if (isNaN(clock.getTime())) throw new RangeError('now 必须是有效时间');
    var today = beijingDay(clock);
    var result = {
      updatedAt: clock.toISOString(),
      top: { status: 'unknown', summary: '仅观察到部分只读来源；未接个人日历和待我审批队列',
        personalCalendar: null, approvals: null, health: null, asOf: null },
      focus: [], time: [], delivery: [], domains: [], sourceStates: []
    };
    var source = {};
    Object.keys(endpoints).forEach(function (key) {
      source[key] = obj(raw[key]) && raw[key].ok !== false ? raw[key] : null;
      result.sourceStates.push(state(key, source[key] ? 'ok' : 'unknown', source[key] ? '已收到响应；仅代表接口可读，不代表内部数据完整' : '未取得有效响应；不可推断事项为零'));
    });
    function mark(key, status, detail) {
      var s = result.sourceStates[Object.keys(endpoints).indexOf(key)];
      s.status = status; s.summary = detail; s.detail = detail; s.priority = status === 'ok' ? 3 : 1;
    }
    var os = source.os;
    if (os) {
      var biz = os.biz;
      if (!obj(biz) || !Array.isArray(biz.near) || !Array.isArray(biz.notes)) mark('os', 'unknown', 'biz 旧台账结构不符；不能当作今日个人事项');
      else {
        mark('os', 'stale', 'biz 来自旧台账 biz.json / notes.json（2026-10-13 02:20），未核实时效；其余聚合计数不作个人事实');
        biz.near.forEach(function (row, i) {
          if (!obj(row) || !within(row.date, today, 7)) return;
          result.time.push(item('biz:near:' + i, text(row.topic, text(row.title, '旧台账事项')), row.date + ' · 旧台账待核实', 'unverified', '/api/os biz.near', businessTime(row.updated_at), '仅为旧商务台账条目；非已确认的个人日历或出行。', null, 2));
        });
        biz.notes.forEach(function (row, i) {
          if (!obj(row)) return;
          result.delivery.push(item('biz:note:' + i, text(row.title, text(row.name, '在研文稿')), '旧台账在研记录 · 交付未核实', 'unverified', '/api/os biz.notes', businessTime(row.updated_at), '来自 notes.json，不能据此认定已交付。', null, 3));
        });
        if (businessTime(biz.remind_next) && new Date(biz.remind_next.replace(' ', 'T') + '+08:00') >= clock) {
          result.time.push(item('biz:reminder', '旧商务提醒待核实', biz.remind_next, 'unverified', '/api/os biz.remind_next', null, '这是计划提醒时间，不是事实更新时间。', null, 2));
        }
      }
    }
    var home = source.home;
    if (home) {
      if (!obj(home.calendar) || home.calendar.ok === false || !Array.isArray(home.calendar.items)) mark('home', 'unknown', '项目时点结构不符；个人日历未接入');
      else {
        mark('home', 'limited', 'calendar 为固定项目时点，不是个人日历');
        home.calendar.items.forEach(function (row, i) {
          if (!obj(row)) return;
          var target = targetDay(row.day, today.slice(0, 4));
          if (!within(target, today, 7)) return;
          result.time.push(item('home:calendar:' + i, text(row.what, '项目节点'), target + ' · 固定项目时点', 'project-timeline', '/api/home calendar', null, '非个人日历；in_days 与当前北京日期不一致时以日期为准。', null, 3));
        });
      }
    }
    var alerts = source.alerts;
    if (alerts) {
      if (!obj(alerts.source_health) || !obj(alerts.source_health.sources)) mark('alerts', 'unknown', 'source_health 结构不符；逐源状态未知');
      else {
        mark('alerts', 'limited', '只覆盖六个量化信号源，不代表全域健康或个人健康');
        Object.keys(alerts.source_health.sources).slice(0, 6).forEach(function (name) {
          var row = alerts.source_health.sources[name];
          var status = obj(row) && row.status === 'ok' ? 'limited-ok' : obj(row) && text(row.status) ? row.status : 'unknown';
          /* WO-OS-03: as_of 新鲜度分层——48h 内=ok；48h-7d=stale；>7d 或无 as_of=unknown */
          var asOf = obj(row) ? businessTime(row.as_of) : null;
          var freshness = 'unknown';
          if (asOf) {
            var ageH = (clock.getTime() - new Date(asOf).getTime()) / 3600000;
            if (ageH <= 48) freshness = 'ok';
            else if (ageH <= 168) freshness = 'stale';
            else freshness = 'unknown';
          }
          var level = status === 'limited-ok' ? (freshness === 'ok' ? 'limited-ok' : 'stale') : status;
          result.focus.push(item('alert:source:' + name, '信号源 ' + name + (freshness !== 'ok' ? ' · 数据新鲜度 ' + (freshness === 'stale' ? '偏旧(>48h)' : '未知/过期') : ''), '六源范围内的健康信号', level, '/api/alerts.source_health', asOf, obj(row) ? text(row.reason, '仅为信号源状态') : '未取得该源状态', null, level === 'limited-ok' ? 3 : 2));
        });
      }
    }
    var notify = source.notify;
    if (notify) {
      if (!Array.isArray(notify.items) || !Array.isArray(notify.errors)) mark('notify', 'unknown', '通知队列结构不符，不能推断待确认数量');
      else {
        var partial = notify.errors.length > 0 || (Array.isArray(notify.channels_errors) && notify.channels_errors.length > 0) || (obj(notify.meta) && notify.meta.truncated === true);
        mark('notify', partial ? 'partial' : 'limited', partial ? '通知队列部分来源失败或截断；列表仅供逐条核对' : '通知指挥队列可读；不是待我审批队列');
        notify.items.forEach(function (row, i) {
          if (!obj(row) || (row.state !== 'review' && row.state !== 'blocked')) return;
          var when = businessTime(row.created_at);
          var scheduled = businessTime(row.send_at);
          if (scheduled && new Date(scheduled) < clock && row.state === 'review') return;
          /* WO-OS-03: review/blocked 细分——review=待确认(非本人审批)，blocked=异常阻断需优先核对 */
          var isBlocked = row.state === 'blocked';
          result.focus.push(item('notify:' + text(row.id, String(i)), text(row.title, '通知决策'), isBlocked ? '通知异常/阻断 · 需优先核对原因' : (row.retry_count > 0 ? '通知待确认 · 曾重试 ' + row.retry_count + ' 次' : '通知待确认 · 非待我审批'), row.state, '/api/notify/queue', when, text(row.reason, isBlocked ? '阻断项不可忽略；重试次数与阻断原因须回原账本核对' : '通知决策账本记录；不可推断审批队列数量'), null, isBlocked ? 1 : 2));
        });
      }
    }
    var tasks = source.tasks;
    if (tasks) {
      if (!Array.isArray(tasks.tasks)) mark('tasks', 'unknown', '最近工单列表结构不符；不能推断全量');
      else {
        mark('tasks', 'limited', '仅最近工单列表，不是全量任务或待我审批');
        tasks.tasks.forEach(function (row, i) {
          if (!obj(row) || (row.status !== 'open' && row.status !== 'doing')) return;
          var due = day(row.due) || (typeof row.due === 'string' ? day(row.due.slice(0, 10)) : null);
          /* WO-OS-03: 截止日分层——今日到期=2，逾期回补=1（原逻辑直接丢弃逾期项，改为显式标注） */
          if (due && due < today) {
            result.focus.push(item('task:' + text(row.task_id, String(i)), text(row.title, '逾期工单'), '逾期至 ' + due + ' · 需核对是否阻塞', 'overdue', '/api/org/tasks', businessTime(row.created_at), '已过截止的最近工单被原逻辑隐藏；此处显式列出但不推断责任归属。', null, 1));
            return;
          }
          result.focus.push(item('task:' + text(row.task_id, String(i)), text(row.title, '最近工单'), due ? (due === today ? '今日到期 · 优先核对' : '截止 ' + due + ' · 最近工单') : '最近工单 · 截止未知', row.status, '/api/org/tasks', businessTime(row.created_at), '只读最近列表；未证实属于本人或代表全部待办。', null, due === today ? 2 : 3));
        });
      }
    }
    var capability = source.capability;
    if (capability) {
      if (!Array.isArray(capability.production_lines)) mark('capability', 'unknown', '产线登记结构不符；不能推断交付');
      else mark('capability', 'limited', '产物/审批仅是登记数量，不代表交付完成');
    }
    result.domains = entries.map(function (e) {
      return item('entry:' + e[0], e[1], '静态领域入口 · 非实况', 'entry', '静态导航', null, '此入口不提供该领域当前健康、任务或交付结论。', e[2], 3);
    });
    result.focus.sort(function (a, b) { return a.priority - b.priority; });
    result.time.sort(function (a, b) { return a.summary.localeCompare(b.summary); });
    if (result.sourceStates.some(function (s) { return s.status === 'partial'; })) result.top.status = 'partial';
    else if (result.sourceStates.some(function (s) { return s.status === 'unknown'; })) result.top.status = 'unknown';
    else result.top.status = 'limited';
    return result;
  }
  return { build: build };
}));
