/* 会议白板只读来源契约；仅处理调用方传入的数据，不读取接口或业务账。 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.OSMeetingReadonlyContract = api;
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  var sources = [
    { id: 'os', source: '/api/os', selector: 'biz.near / biz.notes', coverage: '旧商务台账，仅待核实事项和在研记录；非个人商务机会账、日历或交付回执', next: '核对旧账来源与时效；另接个人商务机会账' },
    { id: 'home', source: '/api/home', selector: 'calendar.items', coverage: '固定项目时点；非个人日历', next: '另接经授权的个人日历来源' },
    { id: 'alerts', source: '/api/alerts', selector: 'source_health.sources', coverage: '至多六个量化信号源的逐源健康；非全域或个人健康', next: '核对逐源状态和业务时间' },
    { id: 'notify', source: '/api/notify/queue', selector: 'items / errors / channels_errors / meta.truncated', coverage: '通知指挥队列；review 非本人审批', next: '核对失败或截断来源；另接本人审批队列' },
    { id: 'tasks', source: '/api/org/tasks', selector: 'tasks', coverage: '最近工单；非全量任务或本人审批', next: '另核对工单归属和完整性' },
    { id: 'capability', source: '/api/capability/board', selector: 'production_lines', coverage: '产线登记；产物及审批数量非交付回执', next: '另接可核验的交付回执' }
  ];
  var personal = [
    { id: 'personal-calendar', coverage: '个人日历', next: '接入经授权的个人日历事实来源' },
    { id: 'business-opportunities', coverage: '商务机会账', next: '接入本人商务机会账并核对时效' },
    { id: 'my-approvals', coverage: '本人审批', next: '接入本人审批队列；不能用通知 review 替代' },
    { id: 'delivery-receipts', coverage: '交付回执', next: '接入可核验的交付回执；不能用 Git 活动或产线登记替代' }
  ];
  function obj(x) { return x !== null && typeof x === 'object' && !Array.isArray(x); }
  function day(x) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(x)) return false;
    var date = new Date(x + 'T00:00:00Z');
    return !isNaN(date.getTime()) && date.toISOString().slice(0, 10) === x;
  }
  function businessTime(x) {
    if (typeof x !== 'string') return null;
    var s = x.trim();
    var m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?(?:([+-])(\d{2}):(\d{2})|Z)?$/.exec(s);
    if (!m || !day(m[1]) || +m[2] > 23 || +m[3] > 59 || +(m[4] || 0) > 59 ||
        (m[5] && (+m[6] > 23 || +m[7] > 59))) return null;
    var parsed = new Date(m[5] || s.endsWith('Z') ? s.replace(' ', 'T') : s.replace(' ', 'T') + '+08:00');
    return isNaN(parsed.getTime()) ? null : parsed.toISOString();
  }
  function freeze(value) {
    Object.freeze(value);
    Object.keys(value).forEach(function (key) {
      if (value[key] && typeof value[key] === 'object') freeze(value[key]);
    });
    return value;
  }
  function card(id, coverage, status, asOf, evidence, next, pointerId) {
    return { id: id, coverage: coverage, status: status, asOf: asOf,
      evidence: evidence, next: next, pointerId: pointerId };
  }
  function build(raw, now) {
    var clock = now instanceof Date ? new Date(now.getTime()) : (typeof now === 'string' ? new Date(now) : new Date(NaN));
    if (isNaN(clock.getTime())) throw new RangeError('now 必须是有效时间');
    var input = obj(raw) ? raw : {};
    var cards = sources.map(function (spec) {
      var data = Object.prototype.hasOwnProperty.call(input, spec.id) && obj(input[spec.id]) && input[spec.id].ok !== false ? input[spec.id] : null;
      var status = 'unknown';
      var asOf = null;
      var evidence = ['未取得有效响应；不能推断事项为零'];
      if (data && spec.id === 'os' && obj(data.biz) && Array.isArray(data.biz.near) && Array.isArray(data.biz.notes)) {
        status = 'stale';
        evidence = ['仅有旧台账结构；既有来源标注为 2026-10-13 02:20，当前时效未经核实', '接口响应与 Git 活动均非个人事实'];
      } else if (data && spec.id === 'home' && obj(data.calendar) && data.calendar.ok !== false && Array.isArray(data.calendar.items)) {
        status = 'limited';
        evidence = ['仅确认固定项目时点结构可读；in_days 不作为当前日期证据'];
      } else if (data && spec.id === 'alerts' && obj(data.source_health) && obj(data.source_health.sources)) {
        var rows = data.source_health.sources;
        var names = Object.keys(rows);
        if (names.length && names.length <= 6) {
          var partial = false;
          var stale = false;
          var earliest = null;
          var earliestBiz = null;
          var bizMissing = false;
          names.forEach(function (name) {
            var row = rows[name];
            var time = obj(row) ? businessTime(row.as_of) : null;
            if (time && (!earliest || time < earliest)) earliest = time;
            if (!obj(row) || !time || (row.status !== 'ok' && row.status !== 'stale')) partial = true;
            if (obj(row) && row.status === 'stale' || (time && (Date.parse(time) > clock.getTime() || clock.getTime() - Date.parse(time) > 86400000))) stale = true;
            var biz = obj(row) && day(row.business_date) ? row.business_date : null;
            if (biz && (!earliestBiz || biz < earliestBiz)) earliestBiz = biz;
            if (!biz) bizMissing = true;
          });
          asOf = earliest;
          status = partial ? 'partial' : stale ? 'stale' : 'limited';
          evidence = [partial ? '逐源状态或业务时间不完整' : stale ? '逐源标记过期、时间超出 24 小时或晚于观察时间' : '六源范围内状态与业务时间可核对；不代表全域健康'];
          // 2026-10-26 OS-FIX-SMALL2：as_of 是生产者完成时间，business_date 才是业务日；
          // 旧业务日 + 新完成时间时两者必须同时展示，避免把"刚聚合"误读成"数据新"。
          evidence.push(earliestBiz
            ? '业务日最早 ' + earliestBiz + (bizMissing ? '；部分源缺业务日' : '')
            : '业务日缺失（不得据此判断数据新鲜）');
        } else if (names.length > 6) {
          status = 'partial';
          evidence = ['逐源范围超过六项；不可据此宣称完整覆盖'];
        }
      } else if (data && spec.id === 'notify' && Array.isArray(data.items) && Array.isArray(data.errors)) {
        var incomplete = data.errors.length > 0 || (Array.isArray(data.channels_errors) && data.channels_errors.length > 0) || (obj(data.meta) && data.meta.truncated === true);
        status = incomplete ? 'partial' : 'limited';
        evidence = [incomplete ? '通知队列存在来源失败或截断；仅供逐条核对' : '通知队列结构可读；HTTP 200 与 review 均不证明本人审批'];
      } else if (data && spec.id === 'tasks' && Array.isArray(data.tasks)) {
        status = 'limited';
        evidence = ['仅最近工单结构可读；未核实归属与全量'];
      } else if (data && spec.id === 'capability' && Array.isArray(data.production_lines)) {
        status = 'limited';
        evidence = ['仅产线登记结构可读；数量不证明实际交付'];
      }
      return card('source:' + spec.id, spec.coverage, status, asOf, evidence, spec.next, spec.id);
    });
    personal.forEach(function (spec) {
      cards.push(card(spec.id, spec.coverage, 'unknown', null,
        ['六个只读 GET 不提供此项的权威事实；缺源不等于零'], spec.next, null));
    });
    var pointers = sources.map(function (spec) {
      return { id: spec.id, source: spec.source, selector: spec.selector, scope: spec.coverage, readonly: true };
    });
    return freeze({ observedAt: clock.toISOString(), cards: cards, pointers: pointers });
  }
  return Object.freeze({ build: build });
}));
