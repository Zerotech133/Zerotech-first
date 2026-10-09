"""以临时仓和 SQLite 验证盘点信息，不运行生产采集。"""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def health():
    return load('data_health')


@pytest.fixture
def catalog():
    return load('build_catalog')


def parquet(root, name, filename='part_0000.parquet', **columns):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(columns or {'ts_code': ['000001.SZ'], 'trade_date': ['20261008']}).to_parquet(folder / filename)
    return folder


def test_unknown_count_is_not_rendered_as_empty_table(health):
    assert health._fmt_rows(None) == '未计量'
    assert health._fmt_rows(0) == '0'


@pytest.mark.parametrize('source', ['tdx', 'tushare'])
def test_scan_preserves_unknown_and_zero_rows(health, tmp_path, monkeypatch, source):
    root = tmp_path / source
    for name in ['unregistered', 'empty', 'codes_only']:
        parquet(root, name)
    (root / '_manifest.json').write_text(json.dumps({'empty': {'done': True, 'rows': 0},
                                                   'codes_only': {'done': False, 'codes': 1}}))
    monkeypatch.setattr(health, 'DATA', str(tmp_path))
    scanned = getattr(health, '_scan_' + source)()
    rows = {r['asset']: r for r in scanned['tables']}
    assert rows['unregistered']['rows'] is None
    assert rows['empty']['rows'] == 0
    assert rows['codes_only']['rows'] is None
    assert rows['unregistered']['manifest_status'] == 'missing'
    assert rows['codes_only']['manifest_status'] == 'open'
    assert rows['codes_only']['latest_kind'] == 'file_mtime'


def test_missing_manifest_remains_readable_and_unverified(catalog, tmp_path):
    parquet(tmp_path, 'daily')
    entry = catalog._scan_dir('tushare', str(tmp_path), 'manifest.json', 'daily', {})
    assert entry['done'] is True
    assert entry['readiness_basis'] == 'file_presence'
    assert entry['verification_status'] == 'unknown'


def test_part_schema_merges_all_footers_and_records_scope(catalog, tmp_path):
    parquet(tmp_path, 'ts_daily_basic')
    parquet(tmp_path, 'ts_daily_basic', 'part_0001.parquet', ts_code=['000002.SZ'],
            trade_date=['20261008'], pe_ttm=[10], _fetched_at=['2026-10-09'])
    entry = catalog._scan_dir('xtcloud', str(tmp_path), 'manifest.json', 'ts_daily_basic', {})
    assert {'pe_ttm', '_fetched_at'}.issubset(entry['schema'])
    assert entry['schema_scope'] == 'all_footers'
    assert entry['schema_files_inspected'] == 2


def test_corrupt_first_footer_does_not_hide_later_readable_schema(catalog, tmp_path):
    folder = parquet(tmp_path, 'ts_daily_basic', 'part_0001.parquet',
                     ts_code=['000002.SZ'], trade_date=['20261008'], pe_ttm=[10])
    (folder / 'part_0000.parquet').write_bytes(b'corrupt footer')
    entry = catalog._scan_dir('xtcloud', str(tmp_path), '', 'ts_daily_basic', {})
    assert 'pe_ttm' in entry['schema']
    assert entry['schema_errors'] == ['part_0000.parquet']
    assert entry['schema_scope'] == 'sampled'
    assert entry['schema_files_inspected'] == 1


def test_code_layout_schema_is_explicitly_sampled(catalog, tmp_path):
    parquet(tmp_path, 'us_daily', 'AAPL.parquet')
    parquet(tmp_path, 'us_daily', 'MSFT.parquet', ts_code=['MSFT'], trade_date=['20261008'], extra=[1])
    entry = catalog._scan_dir('tushare', str(tmp_path), '', 'us_daily', {})
    assert entry['schema_scope'] == 'sampled'
    assert entry['schema_files_inspected'] == 1


def test_single_code_footer_is_not_a_full_schema_claim(catalog, tmp_path):
    parquet(tmp_path, 'us_daily', 'AAPL.parquet')
    entry = catalog._scan_dir('tushare', str(tmp_path), '', 'us_daily', {})
    assert entry['schema_scope'] == 'sampled'


def test_explicit_empty_manifest_is_registered_without_readiness_guess(catalog, tmp_path):
    parquet(tmp_path, 'daily')
    entry = catalog._scan_dir('tushare', str(tmp_path), 'manifest.json', 'daily', {'daily': {}})
    assert entry['done'] is True  # 兼容旧读层：空条目仍采用目录就绪。
    assert entry['readiness_basis'] == 'file_presence'
    assert entry['manifest_status'] == 'open'


def test_rt_min_registers_real_time_and_static_dates_are_not_applicable(catalog, tmp_path):
    parquet(tmp_path, 'ts_rt_min', ts_code=['000001.SZ'], time=['2026-10-08 15:00:00'])
    entry = catalog._scan_dir('xtcloud', str(tmp_path), 'manifest.json', 'ts_rt_min', {'ts_rt_min': {'done': False}})
    assert entry['date_col'] == 'time'
    assert entry['freshness'] == '20261008'
    assert entry['done'] is False
    assert entry['manifest_status'] == 'open'
    parquet(tmp_path, 'tdx_sector_def', code=['880001'])
    static = catalog._scan_dir('xtcloud', str(tmp_path), '', 'tdx_sector_def', {})
    assert static['date_col'] is None
    assert static['date_status'] == 'not_applicable'


def test_registered_tables_have_business_descriptions(catalog):
    names = ['ts_balancesheet', 'ts_cashflow', 'ts_fina_indicator', 'ts_income', 'ts_rt_min',
             'cyq_chips', 'cyq_perf', 'hk_balancesheet', 'hk_cashflow', 'hk_fina_indicator', 'hk_income',
             'index_weight', 'irm_qa_sh_probe', 'irm_qa_sz_probe', 'limit_list_d', 'stk_holdernumber',
             'stk_managers', 'sw_l1_member', 'us_balancesheet', 'us_cashflow', 'us_fina_indicator', 'us_income']
    assert all(catalog.TABLE_DEFS.get(name, {}).get('desc') for name in names)


def test_sqlite_schema_and_shared_storage_are_measured_once(catalog, health, tmp_path, monkeypatch):
    path = tmp_path / 'tdx_info.db'
    with sqlite3.connect(path) as db:
        for table, date_col in [('stock_notice', 'notice_date'), ('stock_news', 'pub_date'), ('stock_report', 'pub_date')]:
            db.execute(f'CREATE TABLE {table} (ts_code TEXT, {date_col} TEXT, title TEXT)')
            db.execute(f'INSERT INTO {table} VALUES (?, ?, ?)', ('000001.SZ', '2026-10-08', '样本'))
    monkeypatch.setattr(catalog, 'INFO_DB', str(path))
    entries = catalog._scan_sqlite({})
    assert all('title' in e['schema'] for e in entries.values())
    assert all(e['schema_scope'] == 'sqlite_pragma' for e in entries.values())
    assert all(e['rows'] == 1 for e in entries.values())
    monkeypatch.setattr(health, 'DATA', str(tmp_path.parent))
    infos = health._scan_tdx_info_db(str(path))
    assert sum(i.get('size_mb') or 0 for i in infos) == pytest.approx(path.stat().st_size / 1024**2, abs=0.001)


@pytest.fixture
def warehouse(health, monkeypatch):
    monkeypatch.setattr(health, '_scan_kline', lambda market: {'exists': False, 'count': 0})
    monkeypatch.setattr(health, '_scan_news', lambda: {'exists': False})
    monkeypatch.setattr(health, '_scan_tdx', lambda: {'tables': [], 'info': [], 'sync_done': 0, 'sync_total': 0, 'db_size_mb': 0})
    monkeypatch.setattr(health, '_scan_tushare', lambda: {'tables': [], 'sync_done': 0, 'sync_total': 0})
    monkeypatch.setattr(health, '_scan_artifacts', lambda: [])
    monkeypatch.setattr(health, '_scan_integrity', lambda: {'metrics': [{'name': 'real_gap', 'status': 'FAIL', 'value': 3, 'details': {}}], 'fail_count': 1, 'warn_count': 0})
    monkeypatch.setattr(health, 'DATA', '/隔离目录不存在')
    return health


def test_freshness_counts_are_independent_and_warn_is_visible(warehouse, monkeypatch):
    rows = [{'table': s, 'live': '20261008', 'snap': '20261007', 'lag_days': 1, 'ready': True, 'status': s}
            for s in ['WARN', 'STALE', 'FAIL', 'SNAP', 'NA']]
    monkeypatch.setattr(warehouse, '_scan_freshness', lambda: {'tables': rows})
    result = warehouse.scan_warehouse()
    assert result['freshness']['counts'] == {'OK': 0, 'WARN': 1, 'STALE': 1, 'FAIL': 1, 'SNAP': 1, 'NA': 1, 'UNKNOWN': 0}
    assert result['kpi']['integrity_bad'] == 1
    assert result['kpi']['integrity_warn'] == 0
    assert result['kpi']['fresh_warn'] == 1
    assert '全部 OK' not in next(g['note'] for g in result['groups'] if g['kind'] == 'freshness')


def test_news_capacity_and_wal_are_separate(health, tmp_path, monkeypatch):
    path = tmp_path / 'news.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE news (ts TEXT, created_at TEXT, source TEXT, jev TEXT)')
        db.execute('INSERT INTO news VALUES (?, ?, ?, ?)', ('2026-10-08 15:00', '2026-10-09 08:00', 'fixture', None))
    Path(str(path) + '-wal').write_bytes(b'x' * 100)
    monkeypatch.setattr(health, 'NEWS_DB', str(path))
    result = health._scan_news()
    assert result['size_mb'] == pytest.approx(path.stat().st_size / 1024**2, abs=0.001)
    assert result['wal_size_bytes'] == 100
    assert result['measured_at']
    assert result['source_times']['fixture']['latest_event'] == '2026-10-08 15:00'
    assert result['source_times']['fixture']['latest_ingested'] == '2026-10-09 08:00'


def test_manifest_status_does_not_invent_running_state(warehouse, monkeypatch):
    rows = [{'asset': s, 'rows': None, 'parts': 1, 'done': s == 'closed', 'manifest_status': s,
             'latest': '2026-10-09', 'size_mb': 1, 'age_hours': 0} for s in ['missing', 'open', 'closed']]
    monkeypatch.setattr(warehouse, '_scan_tushare', lambda: {'tables': rows, 'sync_done': 1, 'sync_total': 2})
    monkeypatch.setattr(warehouse, '_scan_freshness', lambda: {'tables': []})
    result = warehouse.scan_warehouse()
    assets = next(g['rows'] for g in result['groups'] if g['kind'] == 'tushare')
    assert [r['extra'] for r in assets] == ['未登记（运行状态待核实）', 'manifest未收口（运行状态待核实）', 'manifest已收口（运行状态待核实）']
    assert all(r['rows'].startswith('未计量') for r in assets)


def test_non_manifest_global_index_is_not_claimed_as_unregistered(health, tmp_path, monkeypatch):
    parquet(tmp_path / 'tushare', 'index_global')
    monkeypatch.setattr(health, 'DATA', str(tmp_path))
    table = health._scan_tushare()['tables'][0]
    assert table['manifest_status'] == 'not_applicable'
    assert '不适用' in health._manifest_label(table)


def test_monitoring_keeps_partial_scores_and_failed_graph_qa_visible(health, tmp_path, monkeypatch):
    monkeypatch.setattr(health, 'DATA', str(tmp_path))
    monkeypatch.setattr(health, 'HERE', str(tmp_path.parent))
    rows = [{'full': False, 'cov': 0.5, 'detail': [{'id': 'eps', 'name': '盈利预期', 'cov': 0, 'items': [{'state': None}]}]}] * 218
    (tmp_path / 'nine_dim_scores.json').write_text(json.dumps({'generated_at': '2026-10-09 06:57', 'trade_date': '20261008', 'min_cov': 0.75, 'rows': rows}))
    (tmp_path / 'graph_check').mkdir()
    (tmp_path / 'graph_check/latest.json').write_text(json.dumps({'ts': '2026-10-08', 'ok': False, 'issues': ['source_fresh']}))
    reports = tmp_path.parent / 'reports'
    reports.mkdir(exist_ok=True)
    (reports / 'graph_schema_20261009.json').write_text(json.dumps({'ts': '2026-10-09', 'nodes': 10}))
    result = health._scan_business_monitoring()
    assert result['nine_dim']['n_candidates'] == 218
    assert result['nine_dim']['n_full'] == 0
    assert result['nine_dim']['status'] == 'FAIL'
    assert result['nine_dim']['factors']['eps']['unjudgeable'] == 218
    assert result['graph_qa']['status'] == 'FAIL'
    assert result['graph_qa']['live_verified'] is False
    assert result['graph_counts']['status'] == 'INFO'


def test_source_audit_requires_matching_scoring_version(health, tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(health, 'DATA', str(tmp_path))
    score_path = tmp_path / 'nine_dim_scores.json'
    score_path.write_text(json.dumps({'rows': [{'full': False, 'cov': 0.25}], 'min_cov': 0.75}))
    audit = {'audited_at': '2026-10-09', 'versions': {'nine_dim_scores.json': {'sha256': '旧版本'}},
             'monitoring': {'failure_class_distribution': {'connection_error': 1},
                            'cache_score_relationship': '历史关系'}}
    path = tmp_path / 'nine_dim_source_audit.json'
    path.write_text(json.dumps(audit))
    result = health._scan_business_monitoring()['nine_dim']
    assert result['source_audit_status'] == 'version_mismatch'
    assert 'connection_error' not in result['failure_class_distribution']
    audit['versions']['nine_dim_scores.json']['sha256'] = hashlib.sha256(score_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(audit))
    result = health._scan_business_monitoring()['nine_dim']
    assert result['source_audit_status'] == 'matched'
    assert result['failure_class_distribution'] == {'connection_error': 1}
    assert result['source_audit_at'] == '2026-10-09'


def test_missing_probe_path_is_not_claimed_as_lost_business_data(health, tmp_path, monkeypatch):
    root = tmp_path / 'tushare'
    root.mkdir()
    (root / '_manifest.json').write_text(json.dumps({'anns_d_probe': {'done': True, 'rows': 12000},
        'hk_mins': {'done': True, 'codes': 0}, 'stk_auction_probe': {'done': True, 'rows': 0}}))
    monkeypatch.setattr(health, 'DATA', str(tmp_path))
    notes = {r['asset']: r for r in health._scan_tushare()['manifest_only']}
    assert notes['anns_d_probe']['coverage_status'] == 'location_unverified'
    assert notes['hk_mins']['coverage_status'] == 'no_coverage'
    assert notes['stk_auction_probe']['coverage_status'] == 'empty_probe'
