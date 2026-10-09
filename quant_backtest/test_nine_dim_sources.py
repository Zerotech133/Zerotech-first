"""九维来源资格回归：AST 隔离加载，禁止整体导入或真实网络调用。"""
import ast
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


def engine():
    p = Path(__file__).with_name('nine_dim.py')
    tree = ast.parse(p.read_text(encoding='utf8'))
    names = {'MIN_COV', 'INDEX_CODE', 'FINA_TTL_DAYS', 'FACTORS', 'PENALTIES'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) or
             (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets))]
    ns = {'np': np, 'pd': pd, 'json': json, 'os': os, 'time': time,
          'datetime': datetime, 'timedelta': timedelta, 'TDX_LOCAL': True,
          'tdxr': None}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(p), 'exec'), ns)
    return ns


def frame(**extra):
    row = {'ts_code': '600001.SH', 'trade_date': '20261008', 'pe': 99.,
           'pe_ttm': 12., 'circ_mv': 20000., 'total_mv': 30000.,
           'source_provider': 'tushare.daily_basic',
           'actual_received_at': '2026-10-08T16:56:54+00:00',
           'source_raw_sha256': 'a' * 64}
    row.update(extra)
    return pd.DataFrame([row])


def local(ns, df):
    ns['tdxr'] = SimpleNamespace(fresh=lambda *a: True, read_day=lambda *a, **k: df)
    ns['_by_trade_date'] = lambda *a: ({}, False)
    ns['_tdx_day'] = lambda *a: {}
    ns['_ts'] = lambda *a, **k: (_ for _ in ()).throw(ConnectionError('fixture unavailable'))


def basic_result(ns):
    flow = ns['_fetch_flow']('20261008')
    return flow['basic'], flow['basic_ok']


def test_qualified_local_basic_uses_ttm_and_circulating_value():
    ns = engine()
    local(ns, frame())
    data, ok = basic_result(ns)
    assert ok
    assert data['600001.SH'] == {'pe_ttm': 12., 'total_mv': 30000., 'float_mv': 20000.}


@pytest.mark.parametrize('extra', [
    {'trade_date': '20260930'}, {'source_provider': 'tdx'},
    {'actual_received_at': '2099-10-08T16:56:54Z'},
    {'actual_received_at': None}, {'source_raw_sha256': None},
])
def test_unqualified_basic_never_becomes_score_input(extra):
    ns = engine()
    local(ns, frame(**extra))
    assert basic_result(ns) == ({}, False)


def test_dynamic_pe_does_not_replace_missing_pe_ttm():
    ns = engine()
    local(ns, frame(pe_ttm=np.nan))
    data, ok = basic_result(ns)
    assert ok and data['600001.SH']['pe_ttm'] is None


def test_api_basic_maps_real_circ_mv_field():
    ns = engine()
    local(ns, pd.DataFrame())
    ns['TDX_LOCAL'] = False
    def response(api, day, fields):
        if api != 'daily_basic':
            return {}, False
        assert fields == ['pe_ttm', 'total_mv', 'circ_mv']
        return {'600001.SH': {'pe_ttm': 12., 'total_mv': 30000., 'circ_mv': 20000.}}, True
    ns['_by_trade_date'] = response
    data, ok = basic_result(ns)
    assert ok and data['600001.SH']['float_mv'] == 20000.


def test_partial_local_basic_keeps_api_fallback_for_missing_codes():
    ns = engine()
    local(ns, pd.concat([frame(), frame(ts_code='600002.SH', source_provider='tdx')]))
    ns['_by_trade_date'] = lambda *a: ({'600002.SH': {
        'pe_ttm': 8., 'total_mv': 40000., 'circ_mv': 25000.}}, True)
    data, ok = ns['_daily_basic']('20261008')
    assert ok and set(data) == {'600001.SH', '600002.SH'}
    assert data['600002.SH']['pe_ttm'] == 8.
    assert data['600002.SH']['float_mv'] == 25000.


def test_api_fills_local_null_fields_without_overwriting_qualified_values():
    ns = engine()
    local(ns, frame(pe_ttm=np.nan, total_mv=0.))
    ns['_by_trade_date'] = lambda *a: ({'600001.SH': {
        'pe_ttm': 8., 'total_mv': 999., 'circ_mv': 999.}}, True)
    data, ok = ns['_daily_basic']('20261008')
    assert ok and data['600001.SH'] == {
        'pe_ttm': 8., 'total_mv': 0., 'float_mv': 20000.}


def test_api_never_replaces_complete_qualified_local_values():
    ns = engine()
    local(ns, frame())
    ns['_by_trade_date'] = lambda *a: ({'600001.SH': {
        'pe_ttm': 999., 'total_mv': 999., 'circ_mv': 999.}}, True)
    data, ok = ns['_daily_basic']('20261008')
    assert ok and data['600001.SH'] == {
        'pe_ttm': 12., 'total_mv': 30000., 'float_mv': 20000.}


@pytest.mark.parametrize('remote_ok', [True, False])
def test_remote_empty_or_failure_preserves_qualified_local_data(remote_ok):
    ns = engine()
    local(ns, frame())
    ns['_by_trade_date'] = lambda *a: ({}, remote_ok)
    data, ok = ns['_daily_basic']('20261008')
    assert ok and data['600001.SH']['pe_ttm'] == 12.


def test_api_dynamic_pe_cannot_fill_missing_ttm_value():
    ns = engine()
    local(ns, frame(pe_ttm=np.nan))
    ns['_by_trade_date'] = lambda *a: ({'600001.SH': {
        'pe': 99., 'total_mv': 30000., 'circ_mv': 20000.}}, True)
    data, ok = ns['_daily_basic']('20261008')
    assert ok and data['600001.SH']['pe_ttm'] is None


def test_current_news_recheck_never_certifies_historical_source_response():
    report = Path(__file__).parents[2] / '九维资格.json'
    if not report.exists():
        report = Path(__file__).parent / 'data' / 'nine_dim_source_audit.json'
    result = json.loads(report.read_text(encoding='utf8'))
    historic = [item for row in result['candidates'] for dim in row['dimensions']
                for item in dim['missing_items'] if item['item'] in {'m1', 'm2', 'm_p'}]
    assert len(historic) == 423
    assert all(item['failure_class'] == 'unknown' for item in historic)
    assert all('local_recheck' in row['news_readonly_recheck'] for row in result['candidates'])


def test_report_rc_failure_cannot_be_replaced_by_stock_report():
    ns = engine()
    ns['time'] = SimpleNamespace(sleep=lambda _: None)
    def denied(api, **kw):
        assert api == 'report_rc'
        raise PermissionError('fixture denied')
    ns['_ts'] = denied
    assert ns['_report_rc'](['600001.SH'])['600001.SH']['n_rep'] is None


def test_stale_index_cache_is_not_accepted_for_october_target(tmp_path):
    ns = engine()
    ns['IDX_CACHE'] = str(tmp_path/'idx.json')
    Path(ns['IDX_CACHE']).write_text(json.dumps({'trade_date': '20260930', 'closes': [1.]*300}))
    ns['_ts'] = lambda *a, **k: (_ for _ in ()).throw(ConnectionError('fixture unavailable'))
    with pytest.raises(ConnectionError):
        ns['_idx_bars']()


def test_same_period_comparison_and_absent_ocf_remain_unjudgeable():
    ns = engine()
    # 合法上期必须由来源提供；缺上期及缺 ocf_to_or 均不可凭空补值。
    s = {'fin': {'cur': {'roe': 10.}, 'prev': {}}}
    assert ns['_j_q3'](s) is None
    assert ns['_j_q4'](s) is None


def test_partial_fixture_never_promotes_218_candidates_to_full():
    ns = engine()
    s = {'fin': {}, 'ctx': {'ind_top3': [], 'ind_rps': {}}, 'env': {},
         'news': {}, 'industry': '', 'policy_hit': False,
         'boll_break': False, 'macd_gold_zero': False, 'fake_break': False}
    rows = [ns['score_one'](s, {}) for _ in range(218)]
    assert ns['MIN_COV'] == .75
    assert sum(r['full'] for r in rows) == 0
    assert all(r['score'] is None for r in rows)


def test_qualified_fixture_can_pass_original_coverage_gate():
    ns = engine()
    s = {'fin': {'cur': {'or_yoy': 30., 'grossprofit_margin': 50., 'roe': 12.,
                       'ocf_to_or': 1., 'netprofit_yoy': 20.}, 'prev': {'roe': 10.}},
         'ctx': {'gm_p70': {'x': 40.}, 'ind_top3': ['x'], 'ind_rps': {'x': 70.},
                 'ev_mean': {'x': 10.}}, 'env': {'idx_above_ma20': True, 'vol_pct': 20.},
         'news': {'n7': 4, 'n_prev7': 2, 'n': 6, 'bull': 4, 'bear': 0},
         'industry': 'x', 'policy_hit': False, 'fc_flag': True,
         'inst_net': 6e7, 'block_disc_lo': True, 'block_disc_hi': False,
         'rc': {'n_rep': 4, 'down': 0}, 'margin_detail': {'cur': 110., 'prev': 100.},
         'main_net': 10., 'float_mv': 20000., 'pe_ttm': 12., 'ev_ebitda': 8.,
         'boll_break': False, 'macd_gold_zero': False, 'fake_break': False}
    assert ns['score_one'](s, {})['full'] is True
