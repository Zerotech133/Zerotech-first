/* WO-OS-07 前端诸葛亮建议适配器：与 portal/api_zhuge_brief.py（WO-OS-04 规则版）完全同语义。
 * 纯函数、零副作用：不 fetch/XHR/storage/eval/写库/派单。
 * 输入：六源只读投影原始对象 + 观察时钟（Date）；输出：结构化"今日建议草案"。
 * 每条建议必须带 source 与 as_of（生产者时点，未知为 null）；
 * 缺源/坏源显式进 gaps（"待核"开头），绝不把接口可达当业务健康。
 * UMD：浏览器挂 window.OSZhugeAdapter；Node 可 require。 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) { module.exports = api; }
  else { root.OSZhugeAdapter = api; }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var ENGINE = 'rule-based';
  var BEIJING_OFFSET_MIN = 8 * 60; // UTC+8，无 DST
  var HOUR_MS = 3600 * 1000;

  function pad2(n) { return n < 10 ? '0' + n : String(n); }

  /* 北京业务日 YYYY-MM-DD（按 UTC+8 换算，不依赖宿主时区） */
  function beijingDay(clock) {
    var t = clock.getTime() + BEIJING_OFFSET_MIN * 60000;
    var d = new Date(t);
    return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate());
  }

  /* 解析生产者时点；仅接受可无歧义解析的 ISO 字符串，失败返回 null（对齐 datetime.fromisoformat） */
  function parseAsOf(value) {
    if (typeof value !== 'string' || !value) return null;
    var text = value.trim().replace(' ', 'T');
    // 兼容 Python fromisoformat 常见形态：日期或日期T时间，可带时区偏移
    if (!/^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/.test(text)) return null;
    var normalized = text.replace(/([+-]\d{2}):?(\d{2})$/, '$1:$2');
    var ms = Date.parse(normalized.length === 10 ? normalized + 'T00:00:00Z' : normalized);
    if (!isFinite(ms)) return null;
    return new Date(ms);
  }

  function freshnessBucket(asOf, clock) {
    if (!asOf) return 'unknown';
    var ageHours = (clock.getTime() - asOf.getTime()) / HOUR_MS;
    if (ageHours <= 48) return 'ok';
    if (ageHours <= 168) return 'stale';
    return 'unknown';
  }

  function suggestion(sid, domain, priority, reason, source, asOf) {
    return {
      id: sid,
      domain: domain,
      priority: priority,
      reason: reason,
      source: source,
      as_of: asOf ? asOf.toISOString() : null,
      confidence: ENGINE
    };
  }

  function buildBrief(raw, clock) {
    var tag = Object.prototype.toString.call(clock);
    if (tag !== '[object Date]') throw new TypeError('clock 必须是 Date');
    if (isNaN(clock.getTime())) throw new TypeError('clock 必须是有效 Date');
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new TypeError('raw 必须是对象');

    var suggestions = [];
    var gaps = [];
    var today = beijingDay(clock);

    function usable(key) {
      var entry = raw[key];
      var ok = !!entry && typeof entry === 'object' && !Array.isArray(entry) && entry.ok !== false;
      if (!ok) {
        gaps.push({
          source: key,
          note: '待核：该来源缺失或显式 ok:false，不可推断无事。'
        });
      }
      return ok;
    }

    // notify：blocked 优先级 1；review 优先级 3（非本人审批）
    if (usable('notify')) {
      var notifyItems = raw.notify.items || [];
      for (var i = 0; i < notifyItems.length; i++) {
        var row = notifyItems[i];
        if (!row || typeof row !== 'object') continue;
        var id = row.id !== undefined ? row.id : i;
        if (row.state === 'blocked') {
          suggestions.push(suggestion(
            'notify:' + id, '通知', 1,
            '阻断通知《' + (row.title || '未命名') + '》需优先核对：' + (row.reason || '原因未提供'),
            '/api/notify/queue', parseAsOf(row.created_at)));
        } else if (row.state === 'review') {
          suggestions.push(suggestion(
            'notify:' + id, '通知', 3,
            '通知《' + (row.title || '未命名') + '》待确认（非待本人审批）',
            '/api/notify/queue', parseAsOf(row.created_at)));
        }
      }
    }

    // tasks：overdue 优先级 1；今日到期优先级 2；其余 open/doing 优先级 4
    if (usable('tasks')) {
      var taskRows = raw.tasks.tasks || [];
      for (var j = 0; j < taskRows.length; j++) {
        var t = taskRows[j];
        if (!t || typeof t !== 'object') continue;
        var status = t.status;
        if (status !== 'open' && status !== 'doing') continue;
        var sid = 'task:' + (t.task_id !== undefined ? t.task_id : j);
        var due = typeof t.due === 'string' ? t.due.slice(0, 10) : null;
        if (due && due < today) {
          suggestions.push(suggestion(
            sid, '研发', 1,
            '工单《' + (t.title || '未命名') + '》已逾期至 ' + due + '，需核对是否阻塞',
            '/api/org/tasks', parseAsOf(t.created_at)));
        } else if (due === today) {
          suggestions.push(suggestion(
            sid, '研发', 2,
            '工单《' + (t.title || '未命名') + '》今日到期，建议先看',
            '/api/org/tasks', parseAsOf(t.created_at)));
        } else {
          suggestions.push(suggestion(
            sid, '研发', 4,
            '工单《' + (t.title || '未命名') + '》进行中（' + status + '）',
            '/api/org/tasks', parseAsOf(t.created_at)));
        }
      }
    }

    // alerts：非 ok 源优先级 2；ok 但新鲜度非 ok 优先级 3
    if (usable('alerts')) {
      var health = raw.alerts.source_health;
      var sources = health && typeof health === 'object' ? health.sources : null;
      if (!sources || typeof sources !== 'object' || Array.isArray(sources)) {
        gaps.push({ source: 'alerts', note: '待核：source_health 结构不符，逐源状态未知。' });
      } else {
        for (var name in sources) {
          if (!Object.prototype.hasOwnProperty.call(sources, name)) continue;
          var srow = sources[name];
          if (!srow || typeof srow !== 'object') continue;
          var asOf = parseAsOf(srow.as_of);
          var bucket = freshnessBucket(asOf, clock);
          var ssid = 'alert:' + name;
          if (srow.status !== 'ok') {
            suggestions.push(suggestion(
              ssid, '情报', 2,
              '信号源 ' + name + ' 状态 ' + (srow.status !== undefined ? srow.status : 'unknown') + '，需回原账本核对',
              '/api/alerts', asOf));
          } else if (bucket !== 'ok') {
            var label = bucket === 'stale' ? '偏旧(>48h)' : '未知/过期';
            suggestions.push(suggestion(
              ssid, '情报', 3,
              '信号源 ' + name + ' 数据新鲜度 ' + label,
              '/api/alerts', asOf));
          }
        }
      }
    }

    // os / home / capability：当前无个人事实可提炼，仅登记覆盖，不产建议
    usable('os');
    usable('home');
    usable('capability');

    // 按 priority 升序；同 priority 保持产出顺序（稳定排序）
    suggestions.sort(function (a, b) { return a.priority - b.priority; });
    var seen = {};
    var deduped = [];
    for (var k = 0; k < suggestions.length; k++) {
      if (seen[suggestions[k].id]) continue;
      seen[suggestions[k].id] = true;
      deduped.push(suggestions[k]);
    }

    return {
      engine: ENGINE,
      model_invoked: false,
      dispatched: false,
      generated_at: clock.toISOString(),
      business_day: today,
      suggestions: deduped,
      gaps: gaps,
      note: '规则归集草案，非诸葛亮模型推理；不派单、不代替本人审批；缺源即待核。'
    };
  }

  return { buildBrief: buildBrief, ENGINE: ENGINE };
}));
