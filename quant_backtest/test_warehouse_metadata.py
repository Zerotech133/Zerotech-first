"""计划与采集共享只读快照，不以累计行数代替文件基线。"""
import importlib
import json

import pandas as pd
import pytest


def warehouse(tmp_path):
    data = tmp_path/'data'
    table = data/'tdx'/'ts_daily'
    table.mkdir(parents=True)
    (data/'tdx'/'.warehouse-publication.lock').touch()
    pd.DataFrame({'ts_code':['000001.SZ'],'trade_date':['20261008'],'close':[1]}).to_parquet(table/'part_0000.parquet')
    (data/'tdx'/'_manifest.json').write_text(json.dumps({'ts_daily':{'done':True,'rows':1,'part':1}}))
    (data/'catalog.json').write_text(json.dumps({'tables':{'ts_daily':{'source':'xtcloud','store':str(table),
        'manifest':str(data/'tdx'/'_manifest.json'),'layout':'part','date_col':'trade_date'}}}))
    (data/'warehouse_registry.json').write_text(json.dumps({'physical_tables':[{'asset_id':'ts_daily',
        'physical_tables':['ts_daily'],'qualification_status':'unknown'}],'display_assets':[]}))
    return data,table


def test_snapshot_is_read_only_and_file_change_invalidates_baseline(tmp_path):
    module = importlib.import_module('warehouse_metadata')
    data,table = warehouse(tmp_path)
    original = sorted(str(p.relative_to(data)) for p in data.rglob('*'))
    catalog,registry,first = module.snapshot(data,data/'warehouse_registry.json')
    assert catalog['tables']['ts_daily']['source'] == 'xtcloud'
    assert registry['physical_tables'][0]['qualification_status'] == 'unknown'
    assert first['tables']['ts_daily']['done'] is True
    assert first['tables']['ts_daily']['latest_date'] == '20261008'
    assert {key:first['tables']['ts_daily'][key] for key in ('source','layout','store','date_col')} == {
        'source':'xtcloud','layout':'part','store':str(table.resolve()),'date_col':'trade_date'}
    assert sorted(str(p.relative_to(data)) for p in data.rglob('*')) == original
    pd.DataFrame({'ts_code':['000001.SZ'],'trade_date':['20261009'],'close':[2]}).to_parquet(table/'part_0000.parquet')
    _,_,second = module.snapshot(data,data/'warehouse_registry.json')
    assert first != second


def test_missing_registry_keeps_every_catalog_entry_unknown(tmp_path):
    module = importlib.import_module('warehouse_metadata')
    data,_ = warehouse(tmp_path)
    (data/'warehouse_registry.json').unlink()
    _,registry,_ = module.snapshot(data,data/'warehouse_registry.json')
    assert [r['asset_id'] for r in registry['physical_tables']] == ['ts_daily']
    assert registry['physical_tables'][0]['qualification_status'] == 'unknown'
    assert registry['physical_tables'][0]['verification_rule'] is None


def test_external_store_and_unregistered_physical_table_rejected(tmp_path):
    module = importlib.import_module('warehouse_metadata')
    data,_ = warehouse(tmp_path)
    catalog = json.loads((data/'catalog.json').read_text())
    catalog['tables']['ts_daily']['store'] = str(tmp_path/'outside')
    (data/'catalog.json').write_text(json.dumps(catalog))
    with pytest.raises(ValueError):
        module.snapshot(data,data/'warehouse_registry.json')


def test_unknown_layout_or_unreadable_file_does_not_get_latest_date(tmp_path):
    module = importlib.import_module('warehouse_metadata')
    data,table = warehouse(tmp_path)
    (table/'part_0000.parquet').write_bytes(b'broken')
    _,_,baseline = module.snapshot(data,data/'warehouse_registry.json')
    assert baseline['tables']['ts_daily']['latest_date'] is None
    assert baseline['tables']['ts_daily']['read_errors']


def test_pending_publication_rejects_metadata_snapshot(tmp_path):
    module = importlib.import_module('warehouse_metadata')
    from warehouse_commit import PublicationPending
    data,_ = warehouse(tmp_path)
    pending = data/'tdx'/'.warehouse-publication'/'pending'
    pending.mkdir(parents=True)
    (pending/'known.json').write_text('{}')
    with pytest.raises(PublicationPending):
        module.snapshot(data,data/'warehouse_registry.json')


def test_display_snapshot_does_not_read_unknown_table_footers(tmp_path,monkeypatch):
    module=importlib.import_module('warehouse_metadata')
    data,_=warehouse(tmp_path)
    monkeypatch.setattr(module,'_latest',lambda *a:pytest.fail('未知表展示不应扫描footer'))
    _,_,baseline=module.snapshot(data,data/'warehouse_registry.json',scan_mode='qualified')
    item=baseline['tables']['ts_daily']
    assert item['scan_status']=='not_scanned'
    assert item['files']=={} and item['latest_date'] is None


def test_display_snapshot_still_scans_qualified_tables(tmp_path):
    module=importlib.import_module('warehouse_metadata')
    data,_=warehouse(tmp_path)
    registry=json.loads((data/'warehouse_registry.json').read_bytes())
    registry['physical_tables'][0]['qualification_status']='qualified'
    (data/'warehouse_registry.json').write_text(json.dumps(registry))
    _,_,baseline=module.snapshot(data,data/'warehouse_registry.json',scan_mode='qualified')
    assert baseline['tables']['ts_daily']['latest_date']=='20261008'
    assert baseline['tables']['ts_daily']['scan_status']=='scanned'


def test_by_code_uses_actual_stock_code_instead_of_underscore_filename(tmp_path):
    module = importlib.import_module('warehouse_metadata')
    data,table = warehouse(tmp_path)
    (table/'part_0000.parquet').unlink()
    pd.DataFrame({'ts_code':['110001.SH'],'trade_date':['20261008']}).to_parquet(table/'110001_SH.parquet')
    catalog=json.loads((data/'catalog.json').read_text())
    catalog['tables']['ts_daily'].update(layout='by_code',code_col='ts_code')
    (data/'catalog.json').write_text(json.dumps(catalog))
    _,_,baseline=module.snapshot(data,data/'warehouse_registry.json')
    entry=baseline['tables']['ts_daily']
    assert entry['codes'] == ['110001.SH']
    assert entry['code_files'] == {'110001.SH':['tdx/ts_daily/110001_SH.parquet']}


@pytest.mark.parametrize('codes,statistics', [(['110001.SH','110002.SH'],True),
    (['110001.SH',None],True), (['110001.SH'],False)])
def test_by_code_without_proven_unique_code_keeps_mapping_unknown(tmp_path,codes,statistics):
    module=importlib.import_module('warehouse_metadata')
    data,table=warehouse(tmp_path)
    (table/'part_0000.parquet').unlink()
    pd.DataFrame({'ts_code':codes,'trade_date':['20261008']*len(codes)}).to_parquet(
        table/'110001_SH.parquet',write_statistics=statistics,row_group_size=1)
    catalog=json.loads((data/'catalog.json').read_text())
    catalog['tables']['ts_daily'].update(layout='by_code',code_col='ts_code')
    (data/'catalog.json').write_text(json.dumps(catalog))
    _,_,baseline=module.snapshot(data,data/'warehouse_registry.json')
    entry=baseline['tables']['ts_daily']
    assert entry['codes'] == []
    assert entry['code_files'] == {}
    assert entry['read_errors'] == ['tdx/ts_daily/110001_SH.parquet']
    assert entry['latest_date'] is None
