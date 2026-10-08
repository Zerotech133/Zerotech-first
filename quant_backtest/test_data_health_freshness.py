import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


SOURCE = Path(os.environ.get(
    "HEALTH_MODULE", "E:/老田的AI助手/quant_backtest/data_health.py"))


@pytest.fixture
def health():
    spec = importlib.util.spec_from_file_location("health_under_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def calendar(monkeypatch, rows):
    frame = pd.DataFrame(rows, columns=["exchange", "cal_date", "is_open"])
    calls = []

    def read(table, **kwargs):
        calls.append((table, kwargs))
        return frame

    monkeypatch.setitem(sys.modules, "datamart", SimpleNamespace(read=read))
    return calls


@pytest.mark.parametrize("rows,live,today,expected", [
    ([('SSE', '20260929', 1), ('SZSE', '20260929', 1),
      ('SSE', '20260930', 1), ('SZSE', '20260930', 1)],
     '20260929', '20260930', 1),
    ([('SSE', '20260929', 1), ('SSE', '20260930', 1),
      ('SSE', '20260930', 1)], '20260929', '20260930', 1),
    ([('SSE', '20261001', 0), ('SSE', '20261008', 1)],
     '20261001', '20261008', 1),
    ([('SSE', '20261001', 0), ('SSE', '20261002', 0)],
     '20261001', '20261002', 0),
    ([('SSE', '20260929', 1), ('SSE', '20260930', 0),
      ('SZSE', '20260930', 1)], '20260929', '20260930', 0),
    ([('SSE', '20260930', 1)], '20260929', '20260930', 1),
    ([('SSE', '20260928', 1), ('SSE', '20260929', 1),
      ('SSE', '20260930', 1), ('SSE', '20261008', 1)],
     '20260929', '20260930', 1),
    ([('SSE', '20260929', 1), ('SSE', '20260930', 1.0),
      ('SSE', '20261008', True)], '20260929', '20261008', 2),
])
def test_lag_counts_unique_sse_open_dates_after_live(
        health, monkeypatch, rows, live, today, expected):
    calendar(monkeypatch, rows)
    assert health._lag_open_days(live, today) == expected


@pytest.mark.parametrize("live", ['20261008', '20261009'])
def test_current_or_future_live_needs_no_calendar(health, monkeypatch, live):
    calls = calendar(monkeypatch, [('SSE', '20261008', 1)])
    assert health._lag_open_days(live, '20261008') == 0
    assert calls == []


@pytest.mark.parametrize("frame", [
    None,
    pd.DataFrame(),
    pd.DataFrame({'is_open': [1]}),
    pd.DataFrame({'cal_date': ['20260930'], 'is_open': [1]}),
    pd.DataFrame({'exchange': ['SSE'], 'is_open': [1]}),
    pd.DataFrame({'exchange': ['SSE'], 'cal_date': ['20260930']}),
    pd.DataFrame({'exchange': ['SZSE'], 'cal_date': ['20260930'],
                  'is_open': [1]}),
])
def test_unavailable_calendar_is_unknown(health, monkeypatch, frame):
    monkeypatch.setitem(sys.modules, 'datamart',
                        SimpleNamespace(read=lambda *a, **k: frame))
    assert health._lag_open_days('20260929', '20260930') is None


def test_calendar_read_exception_is_unknown(health, monkeypatch):
    def read(*args, **kwargs):
        raise OSError('隔离读取失败')

    monkeypatch.setitem(sys.modules, 'datamart', SimpleNamespace(read=read))
    assert health._lag_open_days('20260929', '20261008') is None


def test_freshness_keeps_natural_day_fallback(health, monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 10, 8)

    monkeypatch.setattr(health, 'datetime', FixedDatetime)
    monkeypatch.setattr(health, '_FRESH_TABLES', ['daily'])
    monkeypatch.setitem(sys.modules, 'datamart', SimpleNamespace(
        read=lambda *a, **k: None,
        latest=lambda alias: '20260929',
        ready=lambda alias: True,
        _catalog=lambda: {'tables': {'daily': {'freshness': '20260929'}}},
    ))
    row = health._scan_freshness()['tables'][0]
    assert row['lag_days'] == 9
    assert row['status'] == 'STALE'


def test_read_range_stays_within_requested_window(health, monkeypatch):
    calls = calendar(monkeypatch, [('SSE', '20260929', 1),
                                  ('SSE', '20260930', 1)])
    assert health._lag_open_days('20260929', '20260930') == 1
    assert calls == [('trade_cal', {'start': '20260929', 'end': '20260930'})]


@pytest.fixture
def warehouse(health, monkeypatch):
    # 仅替换磁盘扫描边界，保留真实 scan_warehouse 聚合与展示逻辑。
    monkeypatch.setattr(health, '_scan_kline', lambda market: {
        'exists': False, 'count': 0})
    monkeypatch.setattr(health, '_scan_news', lambda: {'exists': False})
    monkeypatch.setattr(health, '_scan_tdx', lambda: {
        'tables': [], 'info': [], 'sync_done': 0, 'sync_total': 0,
        'db_size_mb': 0})
    monkeypatch.setattr(health, '_scan_tushare', lambda: {
        'tables': [], 'sync_done': 0, 'sync_total': 0})
    monkeypatch.setattr(health, '_scan_artifacts', lambda: [])
    monkeypatch.setattr(health, '_scan_integrity', lambda: {
        'metrics': [], 'fail_count': 0, 'warn_count': 0})
    return health


@pytest.mark.parametrize('fresh', [
    None,
    {},
    {'tables': []},
    {'tables': None},
    {'error': '隔离读取失败', 'tables': []},
    {'error': '隔离读取失败'},
])
def test_warehouse_unscanned_freshness_is_explicit_failure(
        warehouse, monkeypatch, fresh):
    monkeypatch.setattr(warehouse, '_scan_freshness', lambda: fresh)
    result = warehouse.scan_warehouse()
    group = next(g for g in result['groups'] if g['kind'] == 'freshness')
    assert '全部 OK' not in group['note']
    assert '失败' in group['note'] or '未扫描' in group['note']
    assert result['kpi']['fresh_bad'] >= 1
    assert len(group['rows']) == 1
    assert group['rows'][0]['asset'] == 'freshness_scan'
    assert 'FAIL' in group['rows'][0]['extra']
    assert any('freshness_scan' in gap for gap in result['gaps'])
    if fresh and fresh.get('error'):
        assert fresh['error'] in group['note']
    # 缓存返回值属于扫描器，聚合时不能修改它。
    if isinstance(fresh, dict):
        assert 'freshness_scan' not in str(fresh)


def test_warehouse_healthy_freshness_keeps_existing_display(warehouse, monkeypatch):
    fresh = {'tables': [{'table': 'daily', 'live': '20261008',
                         'snap': '20261008', 'lag_days': 0,
                         'ready': True, 'status': 'OK'}]}
    monkeypatch.setattr(warehouse, '_scan_freshness', lambda: fresh)
    result = warehouse.scan_warehouse()
    group = next(g for g in result['groups'] if g['kind'] == 'freshness')
    assert group['note'] == '核心表全部 OK'
    assert result['kpi']['fresh_bad'] == 0
    assert group['rows'][0]['asset'] == 'daily'
    assert group['rows'][0]['rows'] == 'lag 0天'
    assert group['rows'][0]['latest'] == '20261008'
    assert not any('freshness_scan' in gap for gap in result['gaps'])


def test_warehouse_partial_freshness_error_keeps_rows_and_failure(
        warehouse, monkeypatch):
    row = {'table': 'daily', 'live': '20261008', 'snap': '20261008',
           'lag_days': 0, 'ready': True, 'status': 'OK'}
    fresh = {'error': '部分扫描失败', 'tables': [row]}
    monkeypatch.setattr(warehouse, '_scan_freshness', lambda: fresh)
    result = warehouse.scan_warehouse()
    group = next(g for g in result['groups'] if g['kind'] == 'freshness')
    assert group['rows'][0]['asset'] == 'daily'
    assert group['rows'][1]['asset'] == 'freshness_scan'
    assert result['kpi']['fresh_bad'] == 1
    assert fresh['error'] in group['note']
    assert fresh['tables'] == [row]
