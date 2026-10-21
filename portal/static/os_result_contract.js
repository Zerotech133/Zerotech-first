/* WO-OS-02 今日结果证据槽契约 ResultEvidence v1
 * 只读纯函数，浏览器与 Node 双端。任何关键字段缺失/不合法 → status:'pending'，绝不推断“完成”。
 * 字段：id(必填) domain(必填) business_day(YYYY-MM-DD 北京) producer_time(生产者时点)
 *      outcome(完成/交付/达成…有限词表) evidence_uri(证据定位) receipt_uri(复核回执)
 * 规则：
 *  1. id/domain/business_day/producer_time/outcome/evidence_uri 六者缺一 → pending，gap 注明缺项。
 *  2. business_day 必须是真实日历日；producer_time 必须是带时区的可解析时间，且不得晚于观察时钟。
 *  3. outcome 只认词表 ['done','delivered','verified']；其他一律 pending。
 *  4. receipt_uri 缺失 → 可显示但标 unverified（不称“已验收”）。
 *  5. 本契约不产生任何“完成”计数；展示方不得用 pending 项聚合出“今日完成 N 项”。*/
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.OSResultContract = api;
}(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  var OUTCOMES = ['done', 'delivered', 'verified'];
  function isDay(x) {
    if (typeof x !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(x)) return false;
    var d = new Date(x + 'T00:00:00Z');
    return !isNaN(d.getTime()) && d.toISOString().slice(0, 10) === x;
  }
  function isTime(x) {
    if (typeof x !== 'string') return false;
    var m = /^(\d{4}-\d{2}-\d{2})[T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:\d{2}|Z)$/.exec(x);
    if (!m || !isDay(m[1])) return false;
    var t = new Date(x.replace(' ', 'T').length <= 16 ? x.replace(' ', 'T') + ':00' + '' : x.replace(' ', 'T'));
    return !isNaN(t.getTime());
  }
  function nonEmpty(x) { return typeof x === 'string' && x.trim().length > 0; }
  function entry(raw, clock) {
    raw = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
    var now = clock === undefined ? new Date() : new Date(clock);
    if (isNaN(now.getTime())) throw new RangeError('clock 必须是有效时间');
    var gaps = [];
    if (!nonEmpty(raw.id)) gaps.push('id');
    if (!nonEmpty(raw.domain)) gaps.push('domain');
    if (!isDay(raw.business_day)) gaps.push('business_day');
    if (!isTime(raw.producer_time)) gaps.push('producer_time');
    if (OUTCOMES.indexOf(raw.outcome) === -1) gaps.push('outcome');
    if (!nonEmpty(raw.evidence_uri)) gaps.push('evidence_uri');
    if (gaps.length) {
      return { valid: false, status: 'pending', gaps: gaps,
        display: '待核（缺：' + gaps.join('、') + '）',
        note: '字段不全或格式不合法，不得计入任何完成统计。' };
    }
    if (new Date(raw.producer_time.replace(' ', 'T')) > now) {
      return { valid: false, status: 'pending', gaps: ['producer_time_future'],
        display: '待核（生产者时点晚于观察时钟，疑似预填）', note: '时点不可信，按待核处理。' };
    }
    return {
      valid: true, status: nonEmpty(raw.receipt_uri) ? 'verified' : 'unverified',
      id: raw.id, domain: raw.domain, business_day: raw.business_day,
      producer_time: raw.producer_time, outcome: raw.outcome,
      evidence_uri: raw.evidence_uri, receipt_uri: raw.receipt_uri || null,
      display: nonEmpty(raw.receipt_uri) ? '有回执，可显示为已验证' : '证据在但缺复核回执，不称“已验收”',
      note: 'valid=true 仅代表证据槽完整，不代替本人审批。'
    };
  }
  function project(list, clock) {
    if (!Array.isArray(list)) throw new TypeError('list 必须是数组');
    return list.map(function (x) { return entry(x, clock); });
  }
  return { OUTCOMES: OUTCOMES, entry: entry, project: project, _internals: { isDay: isDay, isTime: isTime } };
}));
