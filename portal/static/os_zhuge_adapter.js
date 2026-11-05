/* WO-OS-07 前端诸葛亮建议适配器：与 portal/api_zhuge_brief.py（WO-OS-04 规则版）完全同语义。
 * 纯函数、零副作用：不 fetch/XHR/storage/eval/写库/派单。
 * WO-OS-20 扩第七源 pushflow：当日通知投递成败（raw.pushflow 由页面注入，
 * 缺则 gaps 待核）；失败率分级只看当日账本，不拿历史失败簇冒充当前状态。
 * 输入：七源只读投影原始对象 + 观察时钟（Date）；输出：结构化"今日建议草案"。
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

  /* 失败率转百分比文本（与 Python _pct_text 同语义）：0.55→'55'，0.075→'7.5' */
  function pctText(rate) {
    var text = (rate * 100).toFixed(1);
    return text.slice(-2) === '.0' ? text.slice(0, -2) : text;
  }

  /* 计数文本：非整数一律 '?'（缺字段不猜 0，与 Python _count_text 同语义） */
  function countText(value) {
    if (typeof value !== 'number' || !isFinite(value) || Math.floor(value) !== value) return '?';
    return String(value);
  }

  /* WO-OS-22 自洽校验（与 Python _pushflow_rate_or_none 同语义）：
     fail_rate 数值有效 + attempted 显式且>0 + today-attempted===suppressed，
     任一不成立返回 null → 调用方记待核，绝不静默退回含抑制的旧分母。 */
  function pushflowRateOrNull(stats) {
    if (!stats || typeof stats !== 'object') return null;
    var rawRate = stats.fail_rate;
    var rate = null;
    if (typeof rawRate === 'number' && isFinite(rawRate)) rate = rawRate;
    else if (typeof rawRate === 'string' && rawRate.trim() !== '' && isFinite(Number(rawRate))) rate = Number(rawRate);
    if (rate === null || rate < 0 || rate > 1) return null;
    var attempted = stats.attempted;
    if (typeof attempted !== 'number' || !isFinite(attempted) ||
        Math.floor(attempted) !== attempted || attempted <= 0) return null;
    var total = stats.today;
    if (typeof total !== 'number' || !isFinite(total) || Math.floor(total) !== total) return null;
    var suppressed = (stats.policy_suppressed === undefined || stats.policy_suppressed === null)
      ? 0 : stats.policy_suppressed;
    if (typeof suppressed !== 'number' || !isFinite(suppressed) ||
        Math.floor(suppressed) !== suppressed || suppressed < 0) return null;
    if (total - attempted !== suppressed) return null;
    return rate;
  }

  /* WO-OS-25 近 4 日趋势（与 Python _trend_text 同语义）：
     ±5 个百分点以内（含边界）视为持平；越过才判改善/恶化。
     WO-OS-25-FIX：先量化到 0.01 个百分点再比较——直接比浮点会让同为 5pp 的
     变化随机落两侧；且只接受真正的 number（Number(null)=0 会凭空造出趋势）。 */
  function trendText(recent) {
    if (!Array.isArray(recent) || recent.length < 2) return '';
    var todayRow = recent[recent.length - 1];
    var prevRow = recent[recent.length - 2];
    if (!todayRow || !prevRow || typeof todayRow !== 'object' || typeof prevRow !== 'object') return '';
    var cur = todayRow.fail_rate;
    var before = prevRow.fail_rate;
    if (typeof cur !== 'number' || typeof before !== 'number') return '';
    if (!isFinite(cur) || !isFinite(before)) return '';
    if (cur < 0 || cur > 1 || before < 0 || before > 1) return '';
    var pp = Math.round((cur - before) * 100 * 10000) / 10000;
    var verdict;
    if (pp < -5) verdict = '较前一日改善';
    else if (pp > 5) verdict = '较前一日恶化';
    else verdict = '与前一日基本持平';
    var sign = pp >= 0 ? '+' : '';
    return '；' + verdict + '（前一日 ' + pctText(before) + '%，变化 ' +
      sign + pp.toFixed(1) + ' 个百分点）';
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

    // notify：blocked 按失败类型聚合（WO-OS-23 降噪）；review 优先级 3
    // 历史失败簇不等于当前故障：聚合为 1 条 P2，当日真实失败率由 pushflow 源承担；
    // deadline / 未就绪类是真 P1，单列不聚合。
    if (usable('notify')) {
      var notifyItems = raw.notify.items || [];
      var historyCluster = [];
      for (var i = 0; i < notifyItems.length; i++) {
        var row = notifyItems[i];
        if (!row || typeof row !== 'object') continue;
        var id = row.id !== undefined ? row.id : i;
        var title = String(row.title || '未命名');
        if (row.state === 'blocked') {
          if (title.toLowerCase().indexOf('deadline') !== -1 || title.indexOf('未就绪') !== -1) {
            suggestions.push(suggestion(
              'notify:' + id, '通知', 1,
              '到点未就绪《' + title + '》需优先核对：' + (row.reason || '原因未提供'),
              '/api/notify/queue', parseAsOf(row.created_at)));
          } else {
            historyCluster.push(row);
          }
        } else if (row.state === 'review') {
          suggestions.push(suggestion(
            'notify:' + id, '通知', 3,
            '通知《' + title + '》待确认（非待本人审批）',
            '/api/notify/queue', parseAsOf(row.created_at)));
        }
      }
      if (historyCluster.length) {
        var kinds = {};
        var latest = null;
        for (var k = 0; k < historyCluster.length; k++) {
          var t = String(historyCluster[k].title || '').replace(' 投递失败', '').trim() || '未命名';
          kinds[t] = true;
          var ts = parseAsOf(historyCluster[k].created_at);
          if (ts && (latest === null || new Date(ts) > new Date(latest))) latest = ts;
        }
        var kindList = Object.keys(kinds).sort();
        var sample = kindList.slice(0, 3).join('、');
        var more = kindList.length > 3 ? '等' : '';
        suggestions.push(suggestion(
          'notify:history_cluster', '通知', 2,
          '近 7 天投递失败簇 ' + countText(historyCluster.length) + ' 条，涉及 ' +
            countText(kindList.length) + ' 类（' + sample + more + '）：这是 T+7 滚窗的历史簇，' +
            '不代表此刻发不出去；当日真实失败率见另一条建议',
          '/api/notify/queue', latest));
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
          // 2026-11-05 OPS-ZHUGE-EMPTY-WORDING：retired/disabled 是已下线的
          // source，empty 是“合法的零警报”，都不能套 stale 的“需回原账本核对”。
          if (srow.status === 'retired' || srow.status === 'disabled') continue;
          if (srow.status !== 'ok' && srow.status !== 'empty') {
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

    // pushflow（WO-OS-20 第七源）：当日通知投递失败率分级；
    // 口径红线——只看当日账本实际成败，不拿历史失败簇冒充当前状态。
    // WO-OS-22：先做账本自洽校验（attempted 必填且 total=attempted+suppressed），
    // 旧载荷缺 attempted 时一律待核，防静默退回含策略抑制的假失败率。
    if (usable('pushflow')) {
      var stats = raw.pushflow;
      var rate = pushflowRateOrNull(stats);
      if (rate === null) {
        gaps.push({ source: 'pushflow', note: '待核：fail_rate 非数值或账本行数不自洽，当日失败率未知。' });
      } else {
        var priority = 0;
        if (rate >= 0.5) priority = 1;
        else if (rate >= 0.3) priority = 2;
        else if (rate >= 0.1) priority = 3;
        if (priority) {
          var attempted = stats.attempted;
          var suppressed = stats.policy_suppressed || 0;
          var tail = suppressed
            ? '；另有 ' + countText(suppressed) + ' 条静默窗口策略抑制，不计入失败'
            : '';
          var trend = trendText(stats.recent);
          suggestions.push(suggestion(
            'pushflow:daily_rate', '通知', priority,
            '今日真实投递失败率 ' + pctText(rate) + '%（失败 ' + countText(stats.fail_count) +
              '/尝试 ' + countText(attempted) + '）：这是当日真实投递尝试的失败率，' +
              '不是历史失败簇' + trend + tail + '；来源 pushflow.jsonl',
            '/api/zhuge/brief.pushflow', null));
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
