"""只读计划与每日聚合；仅使用调用方提供的脱敏、冻结元数据。"""
import copy
import hashlib
import json
import os
from datetime import date as Date, datetime, time, timedelta
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from warehouse_sync_spec import (
    IDX_CORE, ETF_CORE, TRADE_CAL_PARAMS, TRADE_CAL_CAP, RECENT_TABLES,
    RECENT_MAX_OFF, RECENT_DAY_COUNT, DATE_TABLES, CODE_DAILY, MINUTE_TABLES,
    INDEX_WEIGHT_PARAMS, INDEX_WEIGHT_CAP, US_FINA_SYNC, US_FINA_TABLES,
    shallow_tables, month_pair, recent_periods, minute_request_windows,
)


PLAN_KEYS = {'version', 'source', 'resource', 'market', 'date', 'selected_tables',
             'retry_of', 'retry_units', 'registry_digest', 'targets', 'units',
             'operations', 'unsupported', 'baseline_digest', 'plan_digest'}
TABLE_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,127}$')
TDX_SKIP_DEFAULT = frozenset({'ts_rt_min', 'tdx_sector_min'})
TERMINAL_RUNS = frozenset({'succeeded', 'partial', 'failed', 'interrupted', 'blocked'})
UNIT_DONE = frozenset({'succeeded', 'source_empty', 'failed', 'interrupted', 'blocked',
                       'no_local_file', 'unsupported', 'rejected'})
UNIT_FAILURES = UNIT_DONE - {'succeeded', 'source_empty'}

# 数据地基模块章程 §2：provider→adapter 名唯一登记处（warehouse_sync_runtime
# 的 ADAPTERS 名字侧与此对齐；新 provider 只允许在这里加条目，禁止散落硬编码）。
ADAPTER_NAMES = {'tdx': 'tdx_parquet_v1',
                 'tushare': 'tushare_frame_v1',
                 'xueqiu': 'xueqiu_frame_v1'}

# 章程 §2：provider→可信 channel 集（build_plan 白名单与资格校验共用；
# 2026-10-16 审计修复：xueqiu 此前仅在 ADAPTER_NAMES 登记，plan 白名单与
# channel 映射散落硬编码会全链路拒绝。xueqiu 采集执行器未对接、
# warehouse_registry.json 无 qualified 行前，资格校验仍会拒绝——fail-closed 保留。
PROVIDER_CHANNELS = {'tdx': {'tdx', 'xtcloud'},
                     'tushare': {'tushare'},
                     'xueqiu': {'xueqiu'}}


def metadata_digest(value):
    """规范 JSON 摘要；禁止 NaN、隐式对象序列化及调用方可变引用。"""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def plan_digest(plan):
    return metadata_digest({key: value for key, value in plan.items() if key != 'plan_digest'})


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('date 必须为 YYYY-MM-DD')
    return Date.fromisoformat(value)


def _now(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('now 必须带时区')
    return value


def _registry(registry):
    if not isinstance(registry, dict) or not isinstance(registry.get('physical_tables'), list):
        raise ValueError('registry.physical_tables 必须为登记项列表')
    physical, asset_ids = {}, set()
    for row in registry['physical_tables']:
        if not isinstance(row, dict) or not isinstance(row.get('asset_id'), str):
            raise ValueError('registry 项必须有 asset_id')
        if not row['asset_id'] or row['asset_id'] in asset_ids:
            raise ValueError('registry 物理 asset_id 必须非空且唯一')
        asset_ids.add(row['asset_id'])
        names = row.get('physical_tables')
        if not isinstance(names, list) or not names or any(
                not isinstance(n, str) or not TABLE_NAME.fullmatch(n) for n in names):
            raise ValueError('registry 项必须有合法 physical_tables')
        for name in names:
            if name in physical:
                raise ValueError('registry 物理表重复登记')
            physical[name] = row
    return physical


def resolve_target(table, *, date, now, calendars, policy):
    """严格分离业务收盘与发布时点；缺日不能当休市，缺政策不能猜目标。"""
    requested, now = _day(date), _now(now)
    result = {'table': table, 'date': date, 'target': None,
              'status': 'unknown', 'reason': 'policy_unknown', 'available_at': None}
    required = ('market', 'calendar', 'session_cutoff', 'available_after', 'policy_evidence')
    if (not isinstance(policy, dict) or policy.get('qualification_status') != 'qualified'
            or any(not policy.get(k) for k in required)
            or policy.get('frequency') != 'D'
            or policy.get('event_date_kind') != 'business_session'
            or policy.get('tolerance') != 0):
        return result
    cal = calendars.get(policy['calendar']) if isinstance(calendars, dict) else None
    if not isinstance(cal, dict) or not isinstance(cal.get('days'), dict):
        return dict(result, reason='calendar_unknown')
    try:
        zone = ZoneInfo(cal['timezone'])
        close = time.fromisoformat(policy['session_cutoff'])
        release = time.fromisoformat(policy['available_after'])
        if close.tzinfo is not None or release.tzinfo is not None or release < close:
            return dict(result, reason='publication_rule_unsupported')
    except (KeyError, ValueError, TypeError):
        return dict(result, reason='publication_rule_unsupported')
    local = now.astimezone(zone)
    if requested > local.date():
        return dict(result, reason='future_date_unsupported')
    candidate = requested
    if requested == local.date() and local.time() < close:
        # 即使尚未收盘也先证明浏览日存在，不跨过缺失日历。
        if type(cal['days'].get(date)) is not bool:
            return dict(result, reason='calendar_gap')
        candidate -= timedelta(days=1)
    while True:
        flag = cal['days'].get(candidate.isoformat())
        if type(flag) is not bool:
            return dict(result, reason='calendar_gap')
        if flag:
            break
        candidate -= timedelta(days=1)
    available = datetime.combine(candidate, release, zone)
    return dict(result, target=candidate.isoformat(), available_at=available.isoformat(),
                status='waiting_release' if now < available else 'due', reason=None)


def _resource(source, catalog, registry):
    """只从显式根或同源 store 推导仓根；不以 source 文本冒充资源路径。"""
    roots = set()
    explicit = registry.get('resource', {})
    if explicit:
        if not isinstance(explicit, dict):
            raise ValueError('registry.resource 必须为 source 到绝对仓根的映射')
        if explicit.get(source):
            if not isinstance(explicit[source], str) or not os.path.isabs(explicit[source]):
                raise ValueError('registry.resource 必须为绝对路径')
            roots.add(str(Path(explicit[source]).resolve()))
    channels = PROVIDER_CHANNELS[source]
    for meta in catalog['tables'].values():
        if not isinstance(meta, dict) or meta.get('source') not in channels:
            continue
        store = meta.get('store')
        if not store:
            continue
        if not isinstance(store, str) or not os.path.isabs(store):
            return None, 'resource_store_not_absolute'
        path = Path(store)
        # catalog 的 store 为 parquet 表目录或 SQLite db 文件。
        roots.add(str(path.parent.resolve()))
    if len(roots) != 1:
        return None, 'resource_unknown' if not roots else 'resource_roots_inconsistent'
    return roots.pop(), None


def _operation(table, api, operation, kind, scope, dependencies=None):
    return {'table': table, 'api_name': api, 'operation': operation, 'kind': kind,
            'scope': copy.deepcopy(scope), 'dependencies': dependencies or [], 'executable': False}


def _physical_mapping_reason(source, table, catalog, registered, resource):
    """同名不足以绑定物理资格；来源、渠道与目标仓根都须明确一致。"""
    meta, policy = catalog['tables'].get(table), registered.get(table)
    if not isinstance(meta, dict) or policy is None:
        return 'missing_registration'
    channels = {'tdx': {'tdx', 'xtcloud'}, 'tushare': {'tushare'}}[source]
    if meta.get('source') not in channels:
        return 'source_mapping_mismatch'
    channel = policy.get('channel')
    if channel and channel not in channels:
        return 'source_mapping_mismatch'
    store = meta.get('store')
    if not channel or not resource or not isinstance(store, str) or not os.path.isabs(store):
        return 'physical_mapping_unknown'
    if Path(store).parent.resolve() != Path(resource):
        return 'source_mapping_mismatch'
    return None


def _tushare_operations(market, today, baseline, calendars):
    """声明可见范围；动态来源只标待定，不发请求、不伪造代码或日期。"""
    ops = [_operation('trade_cal', 'trade_cal', 'full_replace', 'table',
                      {'params': TRADE_CAL_PARAMS, 'cap': TRADE_CAL_CAP})]
    for table, api, params, cap in shallow_tables(market):
        ops.append(_operation(table, api, 'full_replace', 'table', {'params': params, 'cap': cap}))
    for table, api, cap in RECENT_TABLES:
        ops.append(_operation(table, api, 'recent_replace', 'table',
                              {'params': {}, 'cap': cap, 'max_off': RECENT_MAX_OFF}))
    cal = calendars.get('SSE', {})
    days = cal.get('days', {}) if isinstance(cal, dict) else {}
    # 这是现有脚本依赖重拉后 SSE 日历的候选快照，而非已证明冻结日期。
    past = sorted(d.replace('-', '') for d, opened in days.items()
                  if type(opened) is bool and opened and _day(d) <= today)
    if market in ('all', 'cn'):
        for table, date_col, cap, max_off in DATE_TABLES:
            ops.append(_operation(table, table, 'date_replace', 'date',
                {'dates': past[-RECENT_DAY_COUNT:] if past else None, 'date_parameter': date_col,
                 'cap': cap, 'max_off': max_off, 'calendar_basis': 'candidate_snapshot',
                 'calendar_refresh_before_execution': True}))
    windows = month_pair(today)
    for table, code_source, cap, half, end_h, markets in MINUTE_TABLES:
        if market not in markets:
            continue
        static = {'IDX_CORE': IDX_CORE, 'ETF_CORE': ETF_CORE}.get(code_source)
        dependencies = []
        if table == 'stk_mins':
            dependencies = [{'api_name': 'index_weight', 'params': INDEX_WEIGHT_PARAMS,
                             'cap': 8000, 'condition': 'local_000906_codes_less_than_100',
                             'output_table': None}]
        if table == 'hk_mins':
            hk_days = sorted(d.replace('-', '') for d, opened in days.items()
                             if type(opened) is bool and opened)
            dependencies = [{'api_name': 'hk_hold', 'params': {'trade_date': d},
                             'cap': 4200, 'output_table': None} for d in hk_days[-20:-1]]
            if not hk_days:
                dependencies = [{'api_name': 'hk_hold', 'params': {'trade_date': None},
                                 'scope': 'refreshed_trade_cal.open_days[-20:-1]',
                                 'cap': 4200, 'output_table': None}]
        ops.append(_operation(table, table, 'unsafe_window_replace', 'window',
            {'codes': static, 'code_source': code_source, 'months': [w[0] for w in windows],
             'windows': [list(w) for w in windows], 'freq': '1min', 'start_time': '09:00:00',
             'end_time': end_h, 'half_month': half, 'cap': cap,
             'delete_before_fetch': True}, dependencies))
        ops[-1]['scope']['request_windows'] = minute_request_windows(windows, half=half, end_h=end_h)
    tables = baseline.get('tables', {})
    for table, master, cap, markets in CODE_DAILY:
        if market not in markets:
            continue
        snapshot = tables.get(table, {})
        ops.append(_operation(table, table, 'per_code_incremental', 'code',
            {'codes': snapshot.get('codes'), 'latest_by_code': snapshot.get('latest_by_code', {}),
             'code_source': master, 'master_refresh_before_execution': True, 'cap': cap,
             'end_date': None, 'missing_local': 'skip_initialization_unsupported'}))
    if US_FINA_SYNC and market in ('all', 'us'):
        for api in US_FINA_TABLES:
            ops.append(_operation(api, api, 'period_replace', 'window',
                                  {'periods': recent_periods(today), 'cap': 10000}))
    if market in ('all', 'cn'):
        ops.append(_operation('index_weight', 'index_weight', 'month_replace', 'window',
            {'params': INDEX_WEIGHT_PARAMS, 'windows': [list(w) for w in windows],
             'cap': INDEX_WEIGHT_CAP}))
    return ops


def _retry(source, retry_of, retry_units):
    if retry_units is not None and (not isinstance(retry_units, list) or any(
            not isinstance(uid, str) or not uid for uid in retry_units)):
        raise ValueError('retry_units 必须为单元标识列表')
    if retry_of is None and not retry_units:
        return None, []
    if not isinstance(retry_of, dict) or not isinstance(retry_units, list) or not retry_units:
        raise ValueError('retry_of 与 retry_units 必须同时给出')
    if retry_of.get('source') != source or retry_of.get('status') not in TERMINAL_RUNS:
        raise ValueError('retry_of 必须为同来源已结束任务')
    if not isinstance(retry_of.get('run_id'), str) or not retry_of['run_id']:
        raise ValueError('retry_of 缺少 run_id')
    old_units = {}
    for item in retry_of.get('units', []):
        if not isinstance(item, dict):
            raise ValueError('retry_of 单元格式不合法')
        unit = item.get('plan', item)
        outcome = item.get('result', item)
        if not isinstance(unit, dict) or not isinstance(unit.get('unit_id'), str):
            raise ValueError('retry_of 单元缺少 unit_id')
        if unit['unit_id'] in old_units:
            raise ValueError('retry_of 单元重复')
        old_units[unit['unit_id']] = outcome or {}
    if len(retry_units) != len(set(retry_units)):
        raise ValueError('retry_units 重复')
    for uid in retry_units:
        if uid not in old_units or old_units[uid].get('execution_status') not in ('failed', 'interrupted'):
            raise ValueError('retry_units 包含外任务或不可重试单元')
    # 两个原执行器均无法接收冻结失败单元；不能扩大为整个任务。
    raise ValueError('exact_retry_unsupported: 原执行器不支持精确单元重试')


def _adapter_ready(source, policy):
    """只使用可信登记，布尔executable不是授予资格的依据。"""
    adapter = policy.get('execution_adapter', {})
    # 章程 §2：adapter 名查 ADAPTER_NAMES 登记表；未登记 provider 一律不合格。
    name = ADAPTER_NAMES.get(source)
    if name is None:
        return False
    return (policy.get('qualification_status') == 'qualified'
            and policy.get('supported_scope') == 'frozen_units_v1'
            and isinstance(adapter, dict) and adapter.get('name') == name and adapter.get('version') == 1
            and all(isinstance(policy.get(k), str) and policy[k] for k in ('provider', 'channel', 'source_id'))
            and all(isinstance(policy.get(k), list) and policy[k]
                    and all(isinstance(c, str) and c for c in policy[k])
                    and len(policy[k]) == len(set(policy[k])) for k in ('primary_key', 'business_columns'))
            and isinstance(adapter.get('operations'), list)
            and all(type(adapter.get(k)) is int and adapter[k] > 0 for k in ('max_page_size', 'max_pages')))


def _selected_dependencies(chosen):
    tables = set(chosen)
    for table, master, _, _ in CODE_DAILY:
        if table in tables:
            tables.update((master, 'trade_cal'))
    return tables


def _frozen_scopes(source, op, target, policy, meta, state, resource):
    """从可信登记与真实文件集合构造最小可执行范围，未知布局拒绝。"""
    if (not _adapter_ready(source, policy) or target.get('status') != 'due'
            or not isinstance(policy.get('verification_rule'), dict)
            or not policy['verification_rule'].get('rule_id') or state.get('read_errors')):
        raise ValueError('execution_qualification_unknown')
    table, adapter, operation = op['table'], policy['execution_adapter'], op['operation']
    if operation not in adapter['operations']:
        raise ValueError('operation_not_qualified')
    api_name = adapter.get('api_name', table)
    if api_name != op['api_name']:
        raise ValueError('api_mapping_unknown')
    root = Path(resource).resolve()
    files = state.get('files')
    if not isinstance(files, dict):
        raise ValueError('physical_files_unknown')
    physical = []
    for relative in files:
        path = (root.parent / relative).resolve()
        if path.parent != root / table or path.suffix != '.parquet' or path.name.startswith('.'):
            raise ValueError('physical_layout_unsupported')
        physical.append(path)
    limit = min(op['scope'].get('cap', adapter['max_page_size']), adapter['max_page_size'])
    if type(limit) is not int or limit <= 0:
        raise ValueError('page_size_unknown')
    def scope(params, path, mode):
        return dict(params=copy.deepcopy(params), relative_target=str(path.relative_to(root)).replace('\\', '/'),
                    page_size=limit, max_pages=adapter['max_pages'], write_mode=mode)
    end = target['target'].replace('-', '')
    if source == 'tdx':
        if meta.get('layout') not in {'part', 'parts'} or len(physical) > 1:
            raise ValueError('tdx_single_part_only')
        latest = state.get('latest_date') if state.get('done') else None
        latest = str(latest).replace('-', '') if latest else None
        expected_op = 'paged_incremental' if latest else 'paged_full'
        if operation != expected_op or physical and not latest:
            raise ValueError('tdx_initialization_or_resume_unknown')
        params = {'start_date': latest, 'end_date': end} if latest else {'end_date': end}
        if latest and (not re.fullmatch(r'\d{8}', latest) or latest > end):
            raise ValueError('local_latest_outside_target')
        op['scope']['params'] = copy.deepcopy(params)
        op['scope']['offset'], op['scope']['end_date'] = 0, end
        path = physical[0] if physical else root / table / 'part_0000.parquet'
        return [scope(params, path, 'merge' if latest else 'replace')]
    if operation == 'full_replace':
        if meta.get('layout') not in {'part', 'parts'} or len(physical) > 1:
            raise ValueError('full_single_part_only')
        params = op['scope']['params']
        op['scope']['frozen_file_set'] = sorted(str(p.relative_to(root)).replace('\\', '/') for p in physical)
        path = physical[0] if physical else root / table / 'part_0000.parquet'
        return [scope(params, path, 'replace')]
    if operation != 'per_code_incremental' or meta.get('layout') != 'by_code':
        raise ValueError('legacy_operation_not_adapted')
    codes, latest = state.get('codes'), state.get('latest_by_code', {})
    code_files = state.get('code_files')
    if not isinstance(codes, list) or not codes or len(codes) != len(set(codes)):
        raise ValueError('code_initialization_unsupported')
    if not isinstance(code_files, dict) or set(code_files) != set(codes):
        raise ValueError('code_file_mapping_unproven')
    scopes = []
    for code in codes:
        if not isinstance(code, str) or not re.fullmatch(r'[A-Z0-9]{1,32}\.[A-Z]{2,8}', code):
            raise ValueError('full_code_identity_unknown')
        references = code_files.get(code)
        if not isinstance(references, list) or len(references) != 1:
            raise ValueError('code_file_mapping_unproven')
        candidates = [(root.parent / references[0]).resolve()]
        start = str(latest.get(code) or '').replace('-', '')
        if candidates[0] not in physical or not re.fullmatch(r'\d{8}', start) or start > end:
            raise ValueError('code_file_or_latest_unknown')
        scopes.append(scope({'ts_code': code, 'start_date': start, 'end_date': end}, candidates[0], 'merge'))
    if len(scopes) != len(physical):
        raise ValueError('code_file_mapping_incomplete')
    op['scope'].update(codes=copy.deepcopy(codes), latest_by_code=copy.deepcopy(latest),
                       master_refresh_before_execution=False, end_date=end,
                       dependency_basis='frozen_local_codes_and_calendar')
    return scopes


def build_plan(source, market, date, *, now, catalog, registry, baseline,
               remote_tables=None, selected_tables=None, retry_of=None, retry_units=None):
    requested, now = _day(date), _now(now)
    if source not in PROVIDER_CHANNELS or market not in ('all', 'cn', 'hk', 'us'):
        raise ValueError('source 或 market 不在白名单中')
    if source == 'tdx' and market not in ('all', 'cn'):
        raise ValueError('TDX 市场范围不支持')
    if not isinstance(catalog, dict) or not isinstance(catalog.get('tables'), dict):
        raise ValueError('catalog.tables 必须为物理表到脱敏元数据的映射')
    if not isinstance(baseline, dict) or not isinstance(baseline.get('tables', {}), dict):
        raise ValueError('baseline 必须为脱敏快照字典')
    registered = _registry(registry)
    if selected_tables is not None and (not isinstance(selected_tables, list) or not selected_tables
            or any(not isinstance(t, str) or not TABLE_NAME.fullmatch(t) for t in selected_tables)
            or len(selected_tables) != len(set(selected_tables))):
        raise ValueError('selected_tables 必须为非空、唯一、合法表名列表')
    chosen = sorted(selected_tables) if selected_tables is not None else None
    retry_id, retry_ids = _retry(source, retry_of, retry_units)
    resource, root_reason = _resource(source, catalog, registry)
    p = {'version': 1, 'source': source, 'resource': resource, 'market': market, 'date': date,
         'selected_tables': chosen or [], 'retry_of': retry_id, 'retry_units': retry_ids,
         'registry_digest': metadata_digest(registry), 'targets': [], 'units': [],
         'operations': [], 'unsupported': [], 'baseline_digest': metadata_digest(baseline)}
    def unsupported(table, reason):
        item = {'table': table, 'reason': reason}
        if item not in p['unsupported']:
            p['unsupported'].append(item)
    if root_reason:
        unsupported(None, root_reason)
    today = now.astimezone(ZoneInfo('Asia/Shanghai')).date()
    if requested != today:
        unsupported(None, 'historical_scope_unsupported' if requested < today else 'future_scope_unsupported')
    calendars = registry.get('calendars', {})
    if calendars is None:
        calendars = {}
    if not isinstance(calendars, dict):
        raise ValueError('registry.calendars 必须为逐市场日历映射')
    if source == 'tushare':
        ops = _tushare_operations(market, today, baseline, calendars)
        whitelist = {o['table'] for o in ops}
        if chosen and any(t not in whitelist for t in chosen):
            raise ValueError('selected_tables 不在该市场执行器白名单中')
        safe_selection = chosen and all(_adapter_ready(source, registered.get(t, {}))
            and next(o for o in ops if o['table'] == t)['operation']
            in registered[t]['execution_adapter']['operations'] for t in chosen)
        if safe_selection:
            # 已接管子集仍显示并执行明确依赖，未知依赖会阻断整份计划。
            needed = _selected_dependencies(chosen)
            ops = [o for o in ops if o['table'] in needed]
        elif chosen:
            unsupported(None, 'selected_scope_not_supported')
    else:
        if remote_tables is not None and (not isinstance(remote_tables, dict) or any(
                not isinstance(n, str) or not TABLE_NAME.fullmatch(n) or not isinstance(m, dict)
                for n, m in remote_tables.items())):
            raise ValueError('remote_tables 必须为可信远端适配器返回的表名映射')
        whitelist = set(catalog['tables'])  # 普通 TDX 分支本身支持所有合法远端表。
        if remote_tables is None:
            unsupported(None, 'remote_metadata_unknown')
            names = chosen or []
        else:
            names = list(chosen) if chosen else [n for n in remote_tables if n not in TDX_SKIP_DEFAULT]
            names.sort(key=lambda n: remote_tables.get(n, {}).get('rows', 0) or 0)
        ops = []
        for name in names:
            if remote_tables is None or name not in remote_tables:
                unsupported(name, 'not_in_remote' if remote_tables is not None else 'remote_metadata_unknown')
                continue
            mapping_reason = _physical_mapping_reason(source, name, catalog, registered, resource)
            if mapping_reason:
                unsupported(name, mapping_reason)
            state = baseline.get('tables', {}).get(name, {}) if not mapping_reason else {}
            meta = catalog['tables'].get(name, {}) if not mapping_reason else {}
            is_sqlite = meta.get('layout') == 'sqlite'
            latest = state.get('latest_date') if state.get('created' if is_sqlite else 'done') else None
            if latest and not is_sqlite:
                latest = latest.replace('-', '').split(' ')[0]
            ops.append(_operation(name, name, 'paged_incremental' if latest else 'paged_full', 'table',
                {'params': {'start_date': latest} if latest else {}, 'offset': 0 if latest else state.get('offset', 0),
                 'end_date': None, 'remote_rows': remote_tables[name].get('rows'),
                 'row_denominator': None, 'storage': meta.get('layout')}))
            if name in TDX_SKIP_DEFAULT:
                unsupported(name, 'minute_scope_unsupported')
    if chosen and any(t not in catalog['tables'] or t not in registered or t not in whitelist for t in chosen):
        raise ValueError('表名必须同时在 catalog、registry、执行器白名单中')
    for op in ops:
        table = op['table']
        op['selection_reason'] = 'user' if chosen is None or table in chosen else 'dependency'
        mapping_reason = _physical_mapping_reason(source, table, catalog, registered, resource)
        if mapping_reason:
            unsupported(table, mapping_reason)
        policy = registered.get(table, {}) if not mapping_reason else {}
        if table not in catalog['tables'] or table not in registered:
            unsupported(table, 'missing_registration')
        target = resolve_target(table, date=date, now=now, calendars=calendars, policy=policy)
        if mapping_reason:
            target['reason'] = mapping_reason
        p['targets'].append(target)
        if target['status'] == 'unknown':
            unsupported(table, 'target_qualification_unknown')
        if target['status'] == 'waiting_release':
            unsupported(table, 'waiting_release')
        if policy.get('qualification_status') != 'qualified' or not policy.get('verification_rule'):
            unsupported(table, 'verification_qualification_unknown')
        safe_units = None
        if not mapping_reason and _adapter_ready(source, policy):
            try:
                if registry.get('resource', {}).get(source) != resource:
                    raise ValueError('execution_resource_unknown')
                safe_units = _frozen_scopes(source, op, target, policy, catalog['tables'][table],
                    baseline.get('tables', {}).get(table, {}), resource)
                op['executable'], op['planned_at'] = True, now.isoformat()
            except ValueError as exc:
                unsupported(table, str(exc))
        if op['operation'] == 'unsafe_window_replace':
            unsupported(table, 'unsafe_window_replace')
            if op['scope']['codes'] is None:
                unsupported(table, 'dynamic_code_list_unsupported')
        if op['operation'] == 'per_code_incremental' and safe_units is None:
            codes, latest = op['scope']['codes'], op['scope']['latest_by_code']
            if not codes or any(not latest.get(code) for code in codes):
                unsupported(table, 'code_initialization_unsupported')
            unsupported(table, 'refreshed_code_list_not_frozen')
        if op['operation'] == 'date_replace':
            unsupported(table, 'refreshed_calendar_not_frozen')
        if safe_units is None:
            unsupported(table, 'legacy_executor_not_plan_bound')
        units = []
        scope = op['scope']
        if safe_units is not None:
            units = safe_units
        elif op['kind'] == 'code' and scope.get('codes'):
            units = [dict(scope, codes=[code], latest_by_code={code: scope['latest_by_code'].get(code)})
                     for code in scope['codes']]
        elif op['kind'] == 'date' and scope.get('dates'):
            units = [dict(scope, dates=[d]) for d in scope['dates']]
        elif op['kind'] == 'window' and scope.get('windows'):
            if scope.get('codes'):
                units = [dict(scope, codes=[code], months=[w[0]], windows=[w],
                              request_windows=minute_request_windows([w],
                                  half=scope['half_month'], end_h=scope['end_time']))
                         for code in scope['codes'] for w in scope['windows']]
            else:
                units = [dict(scope, windows=[w], **({'months': [w[0]],
                    'request_windows': minute_request_windows([w],
                        half=scope['half_month'], end_h=scope['end_time'])}
                    if op['operation'] == 'unsafe_window_replace' else {})) for w in scope['windows']]
        else:
            units = [scope]
        for unit_scope in units:
            unit = {'table': table, 'target': target['target'], 'kind': op['kind'],
                    'scope': copy.deepcopy(unit_scope), 'selection_reason': op['selection_reason']}
            unit['unit_id'] = metadata_digest(unit)[:24]
            p['units'].append(unit)
    p['operations'] = ops
    p['plan_digest'] = plan_digest(p)
    return p


def validate_plan(plan, *, current_baseline, current_registry):
    """校验冻结内容与两种基线，然后拒绝未获执行资格的预览。"""
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS or plan.get('version') != 1:
        raise ValueError('plan 字段或版本无效')
    if plan.get('plan_digest') != plan_digest(plan):
        raise ValueError('plan_digest 不匹配')
    if plan['baseline_digest'] != metadata_digest(current_baseline):
        raise ValueError('baseline_digest 已变化，请重新预览')
    if plan['registry_digest'] != metadata_digest(current_registry):
        raise ValueError('registry_digest 已变化，请重新预览')
    if (plan['unsupported'] or not plan['operations'] or not plan['units']
            or any(not o.get('executable') for o in plan['operations'])):
        raise ValueError('unsupported: 计划未获安全执行资格')
    # 从可信本地快照重建计划，不依赖客户端可修改的批准位或请求范围。
    roots = current_registry.get('resource', {})
    if plan['source'] not in ('tdx', 'tushare') or roots.get(plan['source']) != plan['resource']:
        raise ValueError('unsupported: 执行仓根未获资格')
    registered = _registry(current_registry)
    catalog = {'tables': {}}
    for table, state in current_baseline.get('tables', {}).items():
        if not isinstance(state, dict):
            raise ValueError('unsupported: 物理快照无效')
        catalog['tables'][table] = {k: state.get(k) for k in ('source', 'layout', 'store', 'date_col')}
    times = {op.get('planned_at') for op in plan['operations']}
    if len(times) != 1 or None in times:
        raise ValueError('unsupported: 缺少冻结预览时点')
    planned_at = _now(times.pop())
    if _day(plan['date']) != planned_at.astimezone(ZoneInfo('Asia/Shanghai')).date():
        raise ValueError('unsupported: 预览日期与冻结时点不一致')
    remote = None
    if plan['source'] == 'tdx':
        remote = {op['table']: {'rows': op.get('scope', {}).get('remote_rows')} for op in plan['operations']}
    expected = build_plan(plan['source'], plan['market'], plan['date'], now=planned_at,
        catalog=catalog, registry=current_registry, baseline=current_baseline,
        selected_tables=plan['selected_tables'] or None, remote_tables=remote)
    if expected != plan:
        raise ValueError('unsupported: 计划范围未由可信登记与冻结快照生成')
    from warehouse_sync_runtime import qualify_execution_plan
    qualify_execution_plan(plan, registry=current_registry)
    return True


def _assets(registry):
    rows = {}
    for row in registry['physical_tables'] + registry.get('display_assets', []):
        old = rows.get(row['asset_id'])
        if old is None:
            rows[row['asset_id']] = copy.deepcopy(row)
        else:
            # 不把历史展示快照覆盖当前物理资格；保留历史映射证据。
            old.setdefault('display_evidence', []).append(copy.deepcopy(row))
    return list(rows.values())


def build_daily_view(date, *, runs, assets, verification):
    """执行完成数与业务达标独立；没有当前、逐项资格和来源证据时保持 unknown。"""
    _day(date)
    if not isinstance(assets, dict) or not isinstance(verification, dict):
        raise ValueError('assets 和 verification 必须为字典')
    registry = assets['registry']
    registered = _registry(registry)
    digest = metadata_digest(registry)
    rows = _assets(registry)
    for row in rows:
        row['data_status'] = 'unknown'
        row['update_enabled'] = False
        row['reason'] = 'verification_unknown'
        members = row['physical_tables']
        targets = [assets.get('targets', {}).get(t) for t in members]
        row['targets'] = copy.deepcopy(targets)
        if not targets or any(not t or t.get('status') == 'unknown' or not t.get('target') for t in targets):
            continue
        rule = row.get('verification_rule')
        if (row.get('qualification_status') != 'qualified' or not row.get('policy_evidence')
                or not isinstance(rule, dict) or not isinstance(rule.get('rule_id'), str) or not rule['rule_id']):
            row['reason'] = 'qualification_unknown'
            continue
        try:
            reference_now = _now(assets['now'])
            expected = [resolve_target(member, date=date, now=reference_now,
                calendars=registry.get('calendars', {}), policy=registered.get(member, {})) for member in members]
        except (KeyError, TypeError, ValueError):
            continue
        if any(actual.get('table') != wanted['table'] or actual.get('target') != wanted['target']
                or actual.get('status') != wanted['status'] or wanted['status'] == 'unknown'
                for actual, wanted in zip(targets, expected)):
            continue
        if any(t['status'] == 'waiting_release' for t in targets):
            row['data_status'], row['reason'] = 'waiting_release', 'not_due'
            continue
        item = verification.get(row['asset_id'])
        if not isinstance(item, dict) or (item.get('date') != date
                or item.get('registry_digest') != digest
                or item.get('baseline_digest') != assets.get('baseline_digest')
                or not assets.get('baseline_digest') or not item.get('receipt_ids')
                or item.get('rule_id') != rule['rule_id']):
            continue
        try:
            checked_at, valid_until = _now(item['verified_at']), _now(item['valid_until'])
            reference_now = _now(assets['now'])
        except (KeyError, TypeError, ValueError):
            continue
        if not checked_at <= reference_now <= valid_until:
            continue
        wanted = targets[0]['target'] if len(targets) == 1 else {t['table']: t['target'] for t in targets}
        if item.get('target') != wanted:
            continue
        states = {'passed': 'up_to_date', 'missing': 'not_updated', 'partial': 'partial'}
        if item.get('status') in states:
            row['data_status'] = states[item['status']]
            row['reason'] = None
            row['verification'] = copy.deepcopy(item)
    visible_runs = []
    for run in runs:
        if run.get('date') != date:
            continue
        run = copy.deepcopy(run)
        units = run.get('units', [])
        # RunStore.get_run 的单元使用 {plan, result, recorded_at} 包装。
        # 无 result 的计划不能算已完成；平面形状仅兼容外部只读适配器。
        results = [u.get('result') if 'plan' in u else u for u in units]
        run['progress'] = {'completed_units': sum(r is not None and r.get('execution_status') in UNIT_DONE for r in results),
                           'total_units': len(units),
                           'failed_units': sum(r is not None and r.get('execution_status') in UNIT_FAILURES for r in results),
                           'unit': 'planned_unit'}
        visible_runs.append(run)
    return {'date': date, 'assets': rows, 'runs': visible_runs,
            'registry_digest': digest, 'baseline_digest': assets.get('baseline_digest')}
