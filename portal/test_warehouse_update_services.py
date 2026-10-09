"""真实临时仓的门户服务适配，不加载门户或采集配置。"""
import importlib
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'quant_backtest'))


def root(tmp_path):
    project = tmp_path/'project'
    data = project/'quant_backtest'/'data'
    table = data/'tdx'/'ts_daily'
    table.mkdir(parents=True)
    (data/'tdx'/'.warehouse-publication.lock').touch()
    pd.DataFrame({'ts_code':['000001.SZ'],'trade_date':['20261008'],'close':[1]}).to_parquet(table/'part_0000.parquet')
    (data/'tdx'/'_manifest.json').write_text(json.dumps({'ts_daily':{'done':True,'rows':1}}))
    (data/'catalog.json').write_text(json.dumps({'tables':{'ts_daily':dict(source='xtcloud',store=str(table),
        manifest=str(data/'tdx'/'_manifest.json'),layout='part',date_col='trade_date',done=True)}}))
    return project,data


def test_get_daily_retains_unknown_sources_and_does_not_create_task_db(tmp_path):
    module = importlib.import_module('warehouse_update_services')
    project,data = root(tmp_path)
    services = module.make_services(project,clock=lambda:1791504000.)
    result = services['daily_view']('2026-10-09',[])
    assert result['date'] == '2026-10-09'
    assert len(result['assets']) == 1 and result['assets'][0]['data_status'] == 'unknown'
    assert result['assets'][0]['update_enabled'] is False
    assert not (data/'warehouse_runs.sqlite3').exists()
    assert not (data/'warehouse_plans').exists()


def test_unknown_remote_preview_does_not_fetch_or_launch(tmp_path):
    module = importlib.import_module('warehouse_update_services')
    project,data = root(tmp_path)
    services = module.make_services(project,clock=lambda:1791504000.)
    result = services['planner'].preview(dict(source='tdx',market='cn',date='2026-10-09',
        selected_tables=['ts_daily'],retry_of=None,retry_units=[]),now=1791504000.)
    assert result['plan']['unsupported']
    assert not (data/'warehouse_runs.sqlite3').exists()
    assert not (data/'warehouse_plans').exists()


def test_launcher_cannot_accept_arbitrary_resource_or_script(tmp_path):
    module = importlib.import_module('warehouse_update_services')
    project,data = root(tmp_path)
    services = module.make_services(project,clock=lambda:1791504000.)
    with pytest.raises(ValueError):
        services['launcher']({'source':'tdx','resource':str(tmp_path/'outside'),'plan_digest':'a'*64},'a'*32,entry='manual')
    assert not (data/'warehouse_plans').exists()


def test_pending_publication_does_not_turn_into_unknown_success(tmp_path):
    module = importlib.import_module('warehouse_update_services')
    from warehouse_commit import PublicationPending
    project,data = root(tmp_path)
    pending = data/'tdx'/'.warehouse-publication'/'pending'
    pending.mkdir(parents=True)
    (pending/'known.json').write_text('{}')
    services = module.make_services(project,clock=lambda:1791504000.)
    with pytest.raises(PublicationPending):
        services['daily_view']('2026-10-09',[])


def test_logs_do_not_leak_secret_when_offset_starts_inside_a_line(tmp_path):
    module = importlib.import_module('warehouse_update_services')
    folder = tmp_path/'logs'
    folder.mkdir()
    run_id = 'a'*32
    raw = b'Authorization: Basic secret-tail\nnormal record\n'
    (folder/(run_id+'.log')).write_bytes(raw)
    logs = module.RunLogs(folder)
    result = logs.read_log(run_id,offset=24,limit=4)
    assert 'secret-tail' not in result['text']
    assert '[认证信息已隐藏]' in result['text']
    assert result['next_offset'] == raw.index(b'\n')+1


def test_old_unassociated_logs_are_never_claimed_as_run_history(tmp_path):
    module = importlib.import_module('warehouse_update_services')
    logs = module.RunLogs(tmp_path)
    result = logs.read_log('a'*32,offset=0,limit=128)
    assert result['available'] is False
    assert result['association'] == 'unavailable'


def test_empty_history_reconciliation_does_not_initialize_task_db(tmp_path):
    module = importlib.import_module('warehouse_update_services')
    project,data = root(tmp_path)
    services = module.make_services(project,clock=lambda:100.)
    assert services['store'].reconcile(lambda *args:'unknown',now=100.) == []
    assert not (data/'warehouse_runs.sqlite3').exists()


def test_default_display_install_never_launches_legacy_collectors(tmp_path,monkeypatch):
    module=importlib.import_module('warehouse_update_services')
    project,data=root(tmp_path)
    import warehouse_update
    monkeypatch.setattr(warehouse_update,'validate_plan',lambda *a,**k:None)
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**k:pytest.fail('展示安装不能启动旧采集器'))
    services=module.make_services(project)
    plan=dict(source='tdx',resource=str(data/'tdx'),plan_digest='a'*64)
    with pytest.raises(ValueError,match='采集入口尚未启用'):
        services['launcher'](plan,'a'*32,entry='manual')
    assert not (data/'warehouse_plans').exists()


def test_unknown_daily_and_registry_do_not_scan_all_warehouse_files(tmp_path,monkeypatch):
    module=importlib.import_module('warehouse_update_services')
    import warehouse_metadata
    project,_=root(tmp_path)
    monkeypatch.setattr(warehouse_metadata,'_latest',lambda *a:pytest.fail('未知资格展示不应遍历footer'))
    services=module.make_services(project)
    assert services['registry']()['physical_tables']
    assert services['daily_view']('2026-10-09',[])['assets'][0]['data_status']=='unknown'
