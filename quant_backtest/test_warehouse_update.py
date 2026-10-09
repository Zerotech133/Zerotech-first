"""只读计划回归；合成日历与元数据，不导入采集器或配置。"""
import ast
import copy
from datetime import datetime
import importlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DAY = '2026-10-09'
NOW = datetime.fromisoformat('2026-10-09T18:00:00+08:00')
CAL = {'SSE': {'timezone': 'Asia/Shanghai', 'days': {
    '2026-10-07': True, '2026-10-08': True, '2026-10-09': True,
    '2026-10-10': False, '2026-10-11': False}}}
POLICY = {'qualification_status': 'qualified', 'market': 'cn', 'calendar': 'SSE',
          'frequency': 'D', 'event_date_kind': 'business_session',
          'session_cutoff': '15:00:00', 'available_after': '17:00:00',
          'tolerance': 0, 'policy_evidence': {'reference': '合成发布证据'},
          'verification_rule': {'rule_id': 'keys-v1'}}


def api():
    try:
        return importlib.import_module('warehouse_update')
    except ModuleNotFoundError:
        pytest.fail('缺少只读 warehouse_update 模块')


def meta(names):
    return {'tables': {name: {'source': 'tushare'} for name in names}}, {
        'physical_tables': [dict(POLICY, asset_id=n, physical_tables=[n],
                                 provider='tushare', channel='tushare') for n in names],
        'display_assets': [], 'calendars': CAL}


def plan(source='tushare', market='all', selected=None, **extra):
    names = ['trade_cal', 'stock_basic', 'stock_company', 'cb_basic', 'hk_basic',
             'npr', 'shibor_lpr', 'sf_month', 'us_basic', 'sw_classify_l1',
             'news', 'major_news', 'research_report', 'anns_d', 'top_list',
             'idx_mins', 'etf_mins', 'sw_mins', 'stk_mins', 'hk_mins',
             'cb_daily', 'bak_basic', 'us_daily', 'index_weight']
    catalog, registry = meta(names)
    args = dict(now=NOW, catalog=catalog, registry=registry,
                baseline={'tables': {'cb_daily': {'codes': ['110001.SH'],
                    'latest_by_code': {'110001.SH': '20261008'}}}},
                selected_tables=selected)
    args.update(extra)
    return api().build_plan(source, market, DAY, **args)


@pytest.mark.parametrize('time,day,want,status', [
    ('14:59:59', DAY, '2026-10-08', 'due'),
    ('15:00:00', DAY, DAY, 'waiting_release'),
    ('17:00:00', DAY, DAY, 'due'),
    ('18:00:00', '2026-10-11', DAY, 'due')])
def test_target_separates_session_from_release(time, day, want, status):
    now = datetime.fromisoformat(day + 'T' + time + '+08:00')
    result = api().resolve_target('prices', date=day, now=now, calendars=CAL, policy=POLICY)
    assert (result['target'], result['status']) == (want, status)


@pytest.mark.parametrize('change', [
    {'policy_evidence': None}, {'market': 'hk', 'calendar': 'HKEX'},
    {'available_after': None}, {'tolerance': None},
    {'event_date_kind': 'listing_event'}, {'qualification_status': 'unknown'}])
def test_unknown_publication_or_event_policy_never_guesses_target(change):
    result = api().resolve_target('margin', date=DAY, now=NOW, calendars=CAL,
                                  policy=dict(POLICY, **change))
    assert result['status'] == 'unknown' and result['target'] is None


def test_calendar_missing_day_cannot_be_assumed_closed():
    cal = copy.deepcopy(CAL)
    del cal['SSE']['days']['2026-10-09']
    assert api().resolve_target('prices', date=DAY, now=NOW, calendars=cal,
                               policy=POLICY)['status'] == 'unknown'


def test_all_preview_has_actual_full_recent_date_code_and_month_ranges():
    p = plan()
    ops = {o['table']: o for o in p['operations']}
    assert set(ops) == {'trade_cal', 'stock_basic', 'stock_company', 'cb_basic',
        'hk_basic', 'npr', 'shibor_lpr', 'sf_month', 'us_basic', 'sw_classify_l1',
        'news', 'major_news', 'research_report', 'anns_d', 'top_list', 'idx_mins',
        'etf_mins', 'sw_mins', 'stk_mins', 'hk_mins', 'cb_daily', 'bak_basic',
        'us_daily', 'index_weight'}
    assert ops['trade_cal']['scope']['params'] == {
        'exchange': 'SSE', 'start_date': '19910101', 'end_date': '20301231'}
    assert ops['stock_basic']['operation'] == 'full_replace'
    assert ops['news']['scope']['max_off'] == 6000
    assert ops['anns_d']['scope']['dates'] == ['20261007', '20261008', '20261009']
    assert ops['cb_daily']['scope']['codes'] == ['110001.SH']
    assert ops['cb_daily']['scope']['latest_by_code'] == {'110001.SH': '20261008'}
    assert ops['idx_mins']['scope']['months'] == ['202609', '202610']
    assert ops['index_weight']['scope']['windows'] == [
        ['202609', '20260901', '20260930'], ['202610', '20261001', '20261031']]
    assert not any(o['executable'] for o in p['operations'])


def test_market_hk_still_previews_mandatory_cn_shallow_and_news():
    tables = {o['table'] for o in plan(market='hk')['operations']}
    assert {'trade_cal', 'stock_basic', 'cb_basic', 'news', 'hk_mins'} <= tables
    assert 'cb_daily' not in tables and 'idx_mins' not in tables


def test_table_selection_never_hides_actual_dependencies():
    p = plan(market='cn', selected=['cb_daily'])
    assert p['selected_tables'] == ['cb_daily']
    assert any(u['table'] == 'cb_basic' and u['selection_reason'] == 'dependency'
               for u in p['units'])
    assert any(x['reason'] == 'selected_scope_not_supported' for x in p['unsupported'])


def test_initialization_dynamic_codes_and_unsafe_minutes_are_explicit():
    p = plan()
    reasons = {(x['table'], x['reason']) for x in p['unsupported']}
    assert ('bak_basic', 'code_initialization_unsupported') in reasons
    assert ('stk_mins', 'dynamic_code_list_unsupported') in reasons
    assert ('hk_mins', 'dynamic_code_list_unsupported') in reasons
    assert ('idx_mins', 'unsafe_window_replace') in reasons
    hk = next(o for o in p['operations'] if o['table'] == 'hk_mins')
    assert any(o['api_name'] == 'hk_hold' for o in hk['dependencies'])


def test_manual_scheduled_same_inputs_have_same_hash_and_inputs_untouched():
    catalog, registry = meta(['daily'])
    original = copy.deepcopy(registry)
    kwargs = dict(now=NOW, catalog=catalog, registry=registry, baseline={},
                  remote_tables={'daily': {'rows': 1}})
    manual = api().build_plan('tdx', 'cn', DAY, **kwargs)
    scheduled = api().build_plan('tdx', 'cn', DAY, **kwargs)
    assert manual['plan_digest'] == scheduled['plan_digest']
    assert registry == original


def test_tdx_default_never_expands_all_to_minutes_or_guesses_remote():
    catalog, registry = meta(['daily', 'ts_rt_min', 'tdx_sector_min'])
    p = api().build_plan('tdx', 'all', DAY, now=NOW, catalog=catalog,
        registry=registry, baseline={}, remote_tables={n: {'rows': 1} for n in catalog['tables']})
    assert [o['table'] for o in p['operations']] == ['daily']
    missing = api().build_plan('tdx', 'all', DAY, now=NOW, catalog=catalog,
        registry=registry, baseline={}, selected_tables=['daily'], remote_tables={})
    assert any(x['reason'] == 'not_in_remote' for x in missing['unsupported'])
    unknown = api().build_plan('tdx', 'all', DAY, now=NOW, catalog=catalog,
        registry=registry, baseline={})
    assert unknown['operations'] == [] and unknown['unsupported']


def test_historical_request_preserves_preview_but_refuses_current_incremental():
    catalog, registry = meta(['daily'])
    p = api().build_plan('tdx', 'cn', '2026-10-08', now=NOW, catalog=catalog,
        registry=registry, baseline={}, remote_tables={'daily': {'rows': 1}})
    assert any(x['reason'] == 'historical_scope_unsupported' for x in p['unsupported'])


def test_selection_requires_catalog_registry_and_executor_membership():
    with pytest.raises(ValueError):
        plan(selected=['not_a_registered_table'])
    catalog, registry = meta(['not_incremental'])
    with pytest.raises(ValueError):
        plan(selected=['not_incremental'], catalog=catalog, registry=registry)


@pytest.mark.parametrize('unit_status,source,status,uid', [
    ('succeeded', 'tushare', 'failed', 'u'), ('failed', 'tdx', 'failed', 'u'),
    ('failed', 'tushare', 'running', 'u'), ('failed', 'tushare', 'failed', 'other'),
    ('failed', 'tushare', 'failed', 'u')])
def test_retry_rejects_foreign_success_live_and_inexact_units(unit_status, source, status, uid):
    old = {'run_id': 'r', 'source': source, 'status': status,
           'units': [{'unit_id': 'u', 'table': 'cb_daily', 'execution_status': unit_status}]}
    with pytest.raises(ValueError):
        plan(retry_of=old, retry_units=[uid])


def test_validation_checks_content_baseline_registry_and_execution_qualification():
    p = plan()
    catalog, registry = meta(['daily'])
    with pytest.raises(ValueError, match='baseline'):
        api().validate_plan(p, current_baseline={'changed': True}, current_registry=registry)
    q = copy.deepcopy(p)
    q['units'][0]['table'] = 'tampered'
    with pytest.raises(ValueError, match='digest'):
        api().validate_plan(q, current_baseline={}, current_registry=registry)
    # 正确摘要也不能令受限执行器开始任务。
    baseline = {'tables': {'cb_daily': {'codes': ['110001.SH'],
                                     'latest_by_code': {'110001.SH': '20261008'}}}}
    all_names = [o['table'] for o in p['operations']]
    _, all_registry = meta(all_names)
    # 顺序也是冻结输入的一部分，直接使用完整原输入。
    with pytest.raises(ValueError):
        api().validate_plan(p, current_baseline=baseline, current_registry=all_registry)


def test_registry_digest_invalidates_even_when_baseline_identical():
    catalog, registry = meta(['daily'])
    p = api().build_plan('tdx', 'all', DAY, now=NOW, catalog=catalog,
        registry=registry, baseline={}, remote_tables={'daily': {'rows': 1}})
    registry['physical_tables'][0]['policy_evidence'] = {'reference': '新证据'}
    with pytest.raises(ValueError, match='registry'):
        api().validate_plan(p, current_baseline={}, current_registry=registry)


def test_daily_unknown_assets_not_omitted_and_execution_complete_not_data_green():
    registry_path = ROOT / 'registry-evidence.json'
    if not registry_path.exists():
        registry_path = Path(__file__).parent / 'data/warehouse_registry.json'
    registry = json.loads(registry_path.read_text(encoding='utf-8-sig'))
    run = {'date': DAY, 'status': 'succeeded', 'units': [
        {'unit_id': 'a', 'execution_status': 'failed'},
        {'unit_id': 'b', 'execution_status': 'succeeded'}]}
    view = api().build_daily_view(DAY, runs=[run],
        assets={'registry': registry, 'baseline_digest': 'b', 'targets': {}}, verification={})
    assert len(view['assets']) >= 92
    assert all(a['data_status'] == 'unknown' for a in view['assets'])
    assert view['runs'][0]['progress'] == {'completed_units': 2, 'total_units': 2,
                                         'failed_units': 1, 'unit': 'planned_unit'}


def test_zero_new_rows_only_green_with_current_target_qualified_verification():
    _, registry = meta(['daily'])
    assets = {'registry': registry, 'baseline_digest': 'b', 'now': NOW.isoformat(), 'targets': {
        'daily': {'table': 'daily', 'target': DAY, 'status': 'due'}}}
    evidence = {'daily': {'date': DAY, 'target': DAY, 'status': 'passed',
        'verified_at': NOW.isoformat(), 'baseline_digest': 'b',
        'registry_digest': api().metadata_digest(registry),
        'rule_id': 'keys-v1', 'receipt_ids': ['r'], 'new_rows': 0,
        'valid_until': '2026-10-09T19:00:00+08:00'}}
    view = api().build_daily_view(DAY, runs=[], assets=assets, verification=evidence)
    assert view['assets'][0]['data_status'] == 'up_to_date'
    evidence['daily']['baseline_digest'] = 'old'
    assert api().build_daily_view(DAY, runs=[], assets=assets,
                                 verification=evidence)['assets'][0]['data_status'] == 'unknown'


def test_daily_consumes_real_runstore_unit_envelopes(tmp_path):
    from warehouse_runs import RunStore
    catalog, registry = meta(['daily'])
    registry['resource'] = {'tdx': str(tmp_path.resolve())}
    p = api().build_plan('tdx', 'cn', DAY, now=NOW, catalog=catalog,
        registry=registry, baseline={}, remote_tables={'daily': {'rows': 1}})
    store = RunStore(tmp_path / 'runs.sqlite3')
    rid = store.create_run(p, entry='manual', request_id='r', now=100)
    store.claim(rid, pid=1, created_at='identity', now=100)
    result = dict(execution_status='failed', provider=None, channel=None, source_id=None,
                  receipt_ids=[], commit_id=None, error_kind='network', verification=None)
    result.update({key: None for key in ['processed_rows', 'new_rows', 'revised_rows',
        'duplicate_rows', 'rejected_rows', 'bytes_written', 'net_storage_bytes']})
    store.record_unit(rid, p['units'][0]['unit_id'], result, now=101)
    view = api().build_daily_view(DAY, runs=[store.get_run(rid)],
        assets={'registry': registry, 'baseline_digest': 'b', 'targets': {}}, verification={})
    assert view['runs'][0]['progress']['completed_units'] == 1
    assert view['runs'][0]['progress']['failed_units'] == 1
    assert view['assets'][0]['data_status'] == 'unknown'


@pytest.mark.parametrize('change', [
    {'verified_at': '2026-10-09T19:00:00+08:00'},
    {'valid_until': '2026-10-09T17:59:59+08:00'}, {'valid_until': None},
    {'receipt_ids': []}, {'rule_id': 'old'}, {'date': '2026-10-08'}])
def test_daily_rejects_expired_future_or_untraceable_verification(change):
    _, registry = meta(['daily'])
    evidence = dict(date=DAY, target=DAY, status='passed', verified_at=NOW.isoformat(),
        baseline_digest='b', registry_digest=api().metadata_digest(registry),
        receipt_ids=['r'], rule_id='keys-v1', valid_until='2026-10-09T19:00:00+08:00')
    evidence.update(change)
    view = api().build_daily_view(DAY, runs=[], assets={
        'registry': registry, 'baseline_digest': 'b', 'now': NOW,
        'targets': {'daily': {'table': 'daily', 'date': DAY, 'target': DAY, 'status': 'due'}}},
        verification={'daily': evidence})
    assert view['assets'][0]['data_status'] == 'unknown'


def test_resource_uses_absolute_resolved_common_warehouse_root(tmp_path):
    catalog, registry = meta(['daily', 'notice'])
    root = tmp_path / 'tdx'
    catalog['tables'] = {
        'daily': {'source': 'xtcloud', 'store': str(root / 'daily'), 'layout': 'parts'},
        'notice': {'source': 'xtcloud', 'store': str(root / 'tdx_info.db'), 'layout': 'sqlite'}}
    p = api().build_plan('tdx', 'cn', DAY, now=NOW, catalog=catalog,
        registry=registry, baseline={}, remote_tables={'daily': {'rows': 1}})
    assert p['resource'] == str(root.resolve())
    registry['resource'] = {'tdx': str(tmp_path / 'wrong')}
    bad = api().build_plan('tdx', 'cn', DAY, now=NOW, catalog=catalog,
        registry=registry, baseline={}, remote_tables={'daily': {'rows': 1}})
    assert bad['resource'] is None
    assert any(x['reason'] == 'resource_roots_inconsistent' for x in bad['unsupported'])


def test_tdx_previews_actual_small_table_first_order():
    catalog, registry = meta(['large', 'small'])
    p = api().build_plan('tdx', 'cn', DAY, now=NOW, catalog=catalog,
        registry=registry, baseline={}, remote_tables={'large': {'rows': 10}, 'small': {'rows': 1}})
    assert [o['table'] for o in p['operations']] == ['small', 'large']


def test_tdx_parquet_and_sqlite_keep_their_actual_incremental_parameters():
    catalog, registry = meta(['daily', 'notice'])
    for name, item in catalog['tables'].items():
        item.update(source='xtcloud', store=str(ROOT / 'fixture-tdx' / name))
    for item in registry['physical_tables']:
        item['channel'] = 'xtcloud'
    catalog['tables']['daily']['layout'] = 'parts'
    catalog['tables']['notice']['layout'] = 'sqlite'
    p = api().build_plan('tdx', 'cn', DAY, now=NOW, catalog=catalog,
        registry=registry, baseline={'tables': {
            'daily': {'done': True, 'latest_date': '2026-10-08 00:00:00', 'offset': 99},
            'notice': {'done': False, 'created': True, 'latest_date': '2026-10-08', 'offset': 88}}},
        remote_tables={'daily': {'rows': 10}, 'notice': {'rows': 20}})
    scopes = {o['table']: o['scope'] for o in p['operations']}
    assert scopes['daily']['params'] == {'start_date': '20261008'}
    assert scopes['notice']['params'] == {'start_date': '2026-10-08'}
    assert scopes['daily']['offset'] == scopes['notice']['offset'] == 0


@pytest.mark.parametrize('entry', ['daily', 'plan'])
def test_duplicate_physical_asset_id_is_rejected_by_all_registry_consumers(entry):
    catalog, registry = meta(['daily', 'daily_basic'])
    registry['physical_tables'][1]['asset_id'] = 'daily'
    with pytest.raises(ValueError, match='asset_id'):
        if entry == 'daily':
            api().build_daily_view(DAY, runs=[], assets={'registry': registry, 'targets': {}}, verification={})
        else:
            api().build_plan('tdx', 'cn', DAY, now=NOW, catalog=catalog,
                registry=registry, baseline={}, remote_tables={'daily': {'rows': 1}})


@pytest.mark.parametrize('selected', [None, ['daily']])
@pytest.mark.parametrize('mismatch', ['catalog_source', 'registry_channel', 'missing_store', 'root_conflict'])
def test_remote_same_name_cannot_borrow_incompatible_local_qualification(tmp_path, selected, mismatch):
    catalog, registry = meta(['daily', 'tdx_other'])
    root = tmp_path / 'tdx'
    for name, item in catalog['tables'].items():
        item.update(source='xtcloud', store=str(root / name))
    for item in registry['physical_tables']:
        item['channel'] = 'xtcloud'
    if mismatch == 'catalog_source':
        catalog['tables']['daily'].update(source='tushare', store=str(tmp_path / 'tushare' / 'daily'))
    elif mismatch == 'registry_channel':
        registry['physical_tables'][0]['channel'] = 'tushare'
    elif mismatch == 'missing_store':
        del catalog['tables']['daily']['store']
    else:
        catalog['tables']['daily']['store'] = str(tmp_path / 'other-root' / 'daily')
    p = api().build_plan('tdx', 'cn', DAY, now=NOW, catalog=catalog, registry=registry,
        baseline={'tables': {'daily': {'done': True, 'latest_date': '20261008'}}},
        remote_tables={'daily': {'rows': 1}}, selected_tables=selected)
    assert any(u['table'] == 'daily' for u in p['units'])
    assert any(item['table'] == 'daily' and item['reason'] in {
        'source_mapping_mismatch', 'physical_mapping_unknown'} for item in p['unsupported'])
    target = next(t for t in p['targets'] if t['table'] == 'daily')
    assert target['status'] == 'unknown' and target['target'] is None
    assert p['operations'][0]['scope']['params'] == {}
    assert all(not o['executable'] for o in p['operations'])


def test_same_source_channel_and_store_can_supply_target_without_enabling_executor(tmp_path):
    catalog, registry = meta(['daily'])
    catalog['tables']['daily'].update(source='xtcloud', store=str(tmp_path / 'tdx' / 'daily'))
    registry['physical_tables'][0]['channel'] = 'xtcloud'
    p = api().build_plan('tdx', 'cn', DAY, now=NOW, catalog=catalog, registry=registry,
        baseline={}, remote_tables={'daily': {'rows': 1}})
    assert p['targets'][0]['target'] == DAY and p['targets'][0]['status'] == 'due'
    assert not p['operations'][0]['executable']


@pytest.mark.parametrize('status,failures', [
    ('source_empty', 0), ('no_local_file', 1), ('unsupported', 1), ('rejected', 1)])
def test_real_unit_outcomes_count_as_attempts_without_data_success(status, failures):
    _, registry = meta(['daily'])
    run = {'date': DAY, 'units': [{'plan': {'unit_id': 'a'},
                                 'result': {'execution_status': status}}]}
    view = api().build_daily_view(DAY, runs=[run],
        assets={'registry': registry, 'targets': {}}, verification={})
    assert view['runs'][0]['progress']['completed_units'] == 1
    assert view['runs'][0]['progress']['failed_units'] == failures
    assert view['assets'][0]['data_status'] == 'unknown'


def test_retry_understands_runstore_envelope_then_explicitly_refuses_inexact_executor():
    old = {'run_id': 'r', 'source': 'tushare', 'status': 'failed', 'units': [
        {'plan': {'unit_id': 'u', 'table': 'cb_daily'}, 'result': {'execution_status': 'failed'}}]}
    with pytest.raises(ValueError, match='exact_retry_unsupported'):
        plan(retry_of=old, retry_units=['u'])


def test_target_before_close_rejects_unproven_calendar_flag():
    cal = copy.deepcopy(CAL)
    cal['SSE']['days'][DAY] = 'unknown'
    target = api().resolve_target('prices', date=DAY,
        now='2026-10-09T14:00:00+08:00', calendars=cal, policy=POLICY)
    assert target['status'] == 'unknown'


def test_minute_half_month_requests_and_january_rollover_are_visible():
    p = plan(now='2026-01-09T18:00:00+08:00')
    sw = next(o for o in p['operations'] if o['table'] == 'sw_mins')
    assert sw['scope']['windows'] == [
        ['202512', '20251201', '20251231'], ['202601', '20260101', '20260131']]
    assert sw['scope']['request_windows'] == [
        ['20251201 09:00:00', '20251215 15:00:00'],
        ['20251216 09:00:00', '20251231 15:00:00'],
        ['20260101 09:00:00', '20260115 15:00:00'],
        ['20260116 09:00:00', '20260131 15:00:00']]


def test_minute_unit_only_contains_its_frozen_month_requests():
    p = plan()
    unit = next(u for u in p['units'] if u['table'] == 'idx_mins' and u['scope']['months'] == ['202609'])
    assert unit['scope']['request_windows'] == [['20260901 09:00:00', '20260930 15:00:00']]


@pytest.mark.parametrize('bad', ['', {}, (), ['x']])
def test_retry_units_wrong_shape_is_rejected_even_without_original_run(bad):
    with pytest.raises(ValueError):
        plan(retry_units=bad)


def test_real_registry_unqualified_target_stays_unknown_even_with_claimed_pass():
    _, registry = meta(['daily'])
    registry['physical_tables'][0]['qualification_status'] = 'unknown'
    fake = {'daily': {'date': DAY, 'target': DAY, 'status': 'passed',
        'baseline_digest': 'b', 'registry_digest': api().metadata_digest(registry),
        'verified_at': NOW.isoformat(), 'valid_until': '2026-10-09T19:00:00+08:00',
        'receipt_ids': ['r'], 'rule_id': 'keys-v1'}}
    view = api().build_daily_view(DAY, runs=[], assets={'registry': registry,
        'baseline_digest': 'b', 'now': NOW, 'targets': {
            'daily': {'table': 'daily', 'date': DAY, 'target': DAY, 'status': 'due'}}}, verification=fake)
    assert view['assets'][0]['data_status'] == 'unknown'


@pytest.mark.parametrize('policy_change,target_change', [
    ({'event_date_kind': 'listing_event'}, {}),
    ({'verification_rule': {'other': 'unknown'}}, {}),
    ({'available_after': None}, {}), ({}, {'status': 'due', 'target': '2026-10-08'})])
def test_daily_rechecks_target_policy_and_named_verification_rule(policy_change, target_change):
    _, registry = meta(['daily'])
    registry['physical_tables'][0].update(policy_change)
    target = dict(table='daily', date=DAY, target=DAY, status='due')
    target.update(target_change)
    evidence = {'date': DAY, 'target': target['target'], 'status': 'passed',
        'baseline_digest': 'b', 'registry_digest': api().metadata_digest(registry),
        'verified_at': NOW.isoformat(), 'valid_until': '2026-10-09T19:00:00+08:00',
        'receipt_ids': ['r'], 'rule_id': registry['physical_tables'][0]['verification_rule'].get('rule_id')}
    view = api().build_daily_view(DAY, runs=[], assets={'registry': registry,
        'baseline_digest': 'b', 'now': NOW, 'targets': {'daily': target}}, verification={'daily': evidence})
    assert view['assets'][0]['data_status'] == 'unknown'


@pytest.mark.parametrize('kwargs', [
    {'selected': []}, {'selected': ['cb_daily', 'cb_daily']},
    {'selected': ['cb_daily;anything']}, {'now': '2026-10-09T18:00:00'},
    {'source': 'other'}, {'market': 'other'}])
def test_invalid_scope_and_naive_time_rejected(kwargs):
    with pytest.raises(ValueError):
        plan(**kwargs)


