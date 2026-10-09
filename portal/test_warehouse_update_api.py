"""每日接口仅使用临时任务库和注入服务，不整体导入门户。"""
import copy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'quant_backtest'))
from warehouse_runs import RunStore


def frozen():
    return dict(version=1, source='tdx', resource='test-root', market='cn', date='2026-10-09',
                selected_tables=['daily'], retry_of=None, retry_units=[], registry_digest='r',
                targets=[], units=[dict(unit_id='a', table='daily', target='2026-10-09',
                kind='date', scope={}, selection_reason='user')], operations=[], unsupported=[],
                baseline_digest='b', plan_digest='digest')


@pytest.fixture
def env(tmp_path):
    api = importlib.import_module('warehouse_update_api')
    state = SimpleNamespace(now=100., launches=[], stale=False, fail=False, unknown=False)
    def preview(request, *, now):
        plan = frozen()
        plan.update(date=request['date'], market=request['market'], source=request['source'])
        return plan
    def validate(plan, *, now):
        if state.stale:
            raise ValueError('baseline_digest 已变化')
    def launch(plan, run_id, *, entry):
        state.launches.append(run_id)
        if state.fail:
            raise RuntimeError('Authorization: Basic secret-do-not-return')
        return dict(pid=123, created_at=None if state.unknown else 'identity')
    registry = {'physical_tables':[{'asset_id':'daily', 'physical_tables':['daily']}]}
    services = dict(store=RunStore(tmp_path/'runs.sqlite3'),
        planner=api.PlanService(preview, validate), registry=lambda:registry,
        launcher=launch, daily_view=lambda date, runs:dict(date=date, assets=[], runs=runs),
        process_probe=lambda pid, identity:'unknown', clock=lambda:state.now)
    return api, state, services


def call(env, method, path, body=None, query=None, **kw):
    api, _, services = env
    return api.handle_request(method, path, query or {},
        json.dumps(body).encode() if body is not None else b'',
        origin=kw.get('origin', 'http://127.0.0.1:8091'), host='127.0.0.1:8091',
        authenticated=kw.get('authenticated', True), services=services)


def plan_request(env):
    code, value = call(env,'POST','/api/data/update-plan',
        dict(source='tdx',market='cn',date='2026-10-09',selected_tables=['daily'],retry_of=None,retry_units=[]))
    assert code == 200
    return dict(plan_id=value['plan_id'],plan_digest=value['plan']['plan_digest'],request_id='request-one')


def test_preview_and_daily_get_never_launch(env):
    plan_request(env)
    code, response = call(env,'GET','/api/data/daily',query={'date':['2026-10-09']})
    assert code == 200 and response['date'] == '2026-10-09'
    assert env[1].launches == []


def test_duplicate_request_is_exactly_one_launch_and_survives_adapter_reopen(env):
    request = plan_request(env)
    first = call(env,'POST','/api/data/update',request)
    second = call(env,'POST','/api/data/update',request)
    assert first[0] == second[0] == 200
    assert first[1]['run_id'] == second[1]['run_id']
    assert len(env[1].launches) == 1
    assert env[2]['store'].get_run(first[1]['run_id'])['status'] == 'running'


def test_launcher_failure_saved_and_lock_released_without_exposing_auth(env):
    request = plan_request(env)
    env[1].fail = True
    code, result = call(env,'POST','/api/data/update',request)
    assert code == 503 and 'secret-do-not-return' not in json.dumps(result)
    run = env[2]['store'].get_run(result['run_id'])
    assert run['status'] == 'failed'
    assert 'secret-do-not-return' not in json.dumps(run)
    env[1].fail = False
    request['request_id'] = 'request-two'
    assert call(env,'POST','/api/data/update',request)[0] == 200


def test_unknown_process_identity_preserves_queue_and_no_second_launch(env):
    request = plan_request(env)
    env[1].unknown = True
    code, result = call(env,'POST','/api/data/update',request)
    assert code == 202 and result['status'] == 'queued'
    assert call(env,'POST','/api/data/update',request)[0] == 202
    assert len(env[1].launches) == 1
    request['request_id'] = 'request-two'
    assert call(env,'POST','/api/data/update',request)[0] == 409


@pytest.mark.parametrize('age,stale', [(301,False),(0,True)])
def test_expired_or_changed_baseline_does_not_create_run(env, age, stale):
    request = plan_request(env)
    env[1].now += age
    env[1].stale = stale
    assert call(env,'POST','/api/data/update',request)[0] == 409
    assert env[1].launches == []
    assert env[2]['store'].list_runs('2026-10-09') == []


@pytest.mark.parametrize('patch', [dict(date='2026-02-30'),dict(market='mars'),dict(source='anything'),
    dict(selected_tables=['not_registered']),dict(command='run.exe'),dict(retry_of='fake',retry_units=['a'])])
def test_plan_rejects_invalid_inputs_before_planner(env, patch):
    body = dict(source='tdx', market='cn', date='2026-10-09',selected_tables=['daily'])
    body.update(patch)
    assert call(env,'POST','/api/data/update-plan',body)[0] == 400
    assert not env[1].launches


@pytest.mark.parametrize('origin,auth', [('http://evil.test',True),('null',True),
    ('http://127.0.0.1:8092',True),('http://127.0.0.1:8091',False)])
def test_origin_and_auth_rejection(env,origin,auth):
    assert call(env,'POST','/api/data/update-plan',{},origin=origin,authenticated=auth)[0] == 403


def test_body_size_and_non_json_do_not_start(env):
    api, _, services = env
    for body, expected in [(b' '*65537,413),(b'{broken',400),(b'[]',400)]:
        code, _ = api.handle_request('POST','/api/data/update-plan',{},body,
            origin=None,host='127.0.0.1:8091',authenticated=True,services=services)
        assert code == expected


def test_plan_hash_or_extra_update_field_rejected(env):
    request = plan_request(env)
    request['plan_digest'] = 'tamper'
    assert call(env,'POST','/api/data/update',request)[0] == 409
    request['command'] = 'arbitrary'
    assert call(env,'POST','/api/data/update',request)[0] == 400


def test_run_pagination_limits_and_get_do_not_launch(env):
    request = plan_request(env)
    _, started = call(env,'POST','/api/data/update',request)
    run_id = started['run_id']
    code, result = call(env,'GET','/api/data/runs/'+run_id,query={'after_seq':['0'],'limit':['1']})
    assert code == 200 and len(result['events']) == 1 and result['has_more']
    assert len(env[1].launches) == 1
    assert call(env,'GET','/api/data/runs/'+run_id,query={'limit':['501']})[0] == 400
    assert call(env,'GET','/api/data/runs/not-valid')[0] == 400


def test_launch_reservation_is_atomic_and_persists(env):
    _, _, services = env
    store = services['store']
    run = store.create_run(frozen(),entry='manual',request_id='reservation',now=100)
    assert store.reserve_launch(run,reservation_id='one',now=100) is True
    other = RunStore(store.path)
    assert other.reserve_launch(run,reservation_id='two',now=100) is False
