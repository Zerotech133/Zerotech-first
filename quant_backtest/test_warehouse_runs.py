"""任务持久化、真实计量和跨进程锁的回归，不启动采集器。"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from warehouse_runs import RunStore, RunBusy


@pytest.fixture
def plan():
    return {'version': 1, 'source': 'tdx', 'resource': 'tdx', 'market': 'cn',
            'date': '2026-10-09', 'selected_tables': ['daily'], 'retry_of': None,
            'retry_units': [], 'registry_digest': 'registry', 'targets': [],
            'units': [{'unit_id': 'a', 'table': 'daily', 'target': '2026-10-09',
                       'kind': 'date', 'scope': {}, 'selection_reason': 'user'},
                      {'unit_id': 'b', 'table': 'daily', 'target': '2026-10-08',
                       'kind': 'date', 'scope': {}, 'selection_reason': 'user'}],
            'operations': [], 'unsupported': [], 'baseline_digest': 'baseline',
            'plan_digest': 'digest'}


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path / 'runs.sqlite3')


def unit_result(**changes):
    result = dict(execution_status='succeeded', provider='provider', channel='channel',
                  source_id='source', receipt_ids=[], commit_id=None,
                  processed_rows=5, new_rows=2, revised_rows=1, duplicate_rows=2,
                  rejected_rows=0, bytes_written=120, net_storage_bytes=-10,
                  error_kind=None, verification=None)
    result.update(changes)
    return result


def test_same_resource_blocks_second_request(store, plan):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    assert store.create_run(plan, entry='manual', request_id='a', now=101) == run
    with pytest.raises(RunBusy):
        store.create_run(plan, entry='scheduled', request_id='b', now=101)
    other = copy.deepcopy(plan)
    other['resource'] = 'tushare'
    assert store.create_run(other, entry='cli', request_id='c', now=101) != run


@pytest.mark.parametrize('identity_status', ['alive', 'unknown'])
def test_stale_owner_not_proven_dead_retains_lock(store, plan, identity_status):
    run = store.create_run(plan, entry='cli', request_id='a', now=100)
    store.claim(run, pid=123, created_at='identity-a', now=100)
    store.reconcile(lambda pid, identity: identity_status, now=200)
    with pytest.raises(RunBusy):
        store.create_run(plan, entry='manual', request_id='b', now=201)
    assert store.get_run(run)['status'] == 'running'
    assert store.get_run(run)['process_status'] == identity_status


def test_reused_pid_interrupts_old_identity(store, plan):
    run = store.create_run(plan, entry='cli', request_id='a', now=100)
    store.claim(run, pid=123, created_at='old', now=100)
    seen = []
    def probe(pid, identity):
        seen.append((pid, identity))
        return 'dead' if identity == 'old' else 'alive'
    store.reconcile(probe, now=200)
    assert seen == [(123, 'old')]
    assert store.get_run(run)['status'] == 'interrupted'
    store.create_run(plan, entry='manual', request_id='b', now=201)


def test_queued_identity_unknown_keeps_lock(store, plan):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    store.reconcile(lambda *args: pytest.fail('无身份不能探测'), now=999)
    assert store.get_run(run)['status'] == 'queued'
    with pytest.raises(RunBusy):
        store.create_run(plan, entry='manual', request_id='b', now=999)


def test_launcher_failure_releases_and_terminal_is_immutable(store, plan):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    with pytest.raises(ValueError):
        store.finish(run, 'done', exit_code=0, now=101)
    store.finish(run, 'failed', exit_code=1, now=101)
    with pytest.raises(ValueError):
        store.finish(run, 'succeeded', exit_code=0, now=102)
    with pytest.raises(ValueError):
        store.claim(run, pid=1, created_at='p', now=102)
    store.create_run(plan, entry='manual', request_id='b', now=103)


def test_identity_mismatch_cannot_claim_or_heartbeat(store, plan):
    run = store.create_run(plan, entry='cli', request_id='a', now=100)
    store.claim(run, pid=123, created_at='p', now=100)
    with pytest.raises(ValueError):
        store.claim(run, pid=124, created_at='q', now=101)
    with pytest.raises(ValueError):
        store.heartbeat(run, pid=123, created_at='q', now=101)
    store.heartbeat(run, pid=123, created_at='p', now=102)
    assert store.get_run(run)['heartbeat_at'] == 102


def test_reopen_progress_idempotency_and_unknown_counts(store, plan):
    run = store.create_run(plan, entry='cli', request_id='a', now=100)
    store.claim(run, pid=123, created_at='p', now=100)
    result = unit_result()
    store.record_unit(run, 'a', result, now=101)
    store.record_unit(run, 'a', result, now=102)
    reopened = RunStore(store.path)
    current = reopened.get_run(run)
    assert current['progress'] == {'completed_units': 1, 'total_units': 2,
                                   'failed_units': 0, 'unit': '计划单元'}
    assert current['metrics']['new_rows'] == 2
    assert current['metrics']['net_storage_bytes'] == -10
    with pytest.raises(ValueError):
        store.record_unit(run, 'a', unit_result(new_rows=9), now=103)
    store.record_unit(run, 'b', unit_result(execution_status='failed', new_rows=None,
                                          error_kind='connection_error'), now=104)
    assert store.get_run(run)['metrics']['new_rows'] is None
    assert store.get_run(run)['progress']['failed_units'] == 1
    with pytest.raises(ValueError):
        store.finish(run, 'succeeded', exit_code=0, now=105)
    store.finish(run, 'partial', exit_code=1, now=105)


@pytest.mark.parametrize('changes', [{'new_rows': -1}, {'new_rows': True},
    {'processed_rows': 1.5}, {'bytes_written': '5'}, {'receipt_ids': 'r'},
    {'execution_status': 'running'}, {'unknown': 1}])
def test_invalid_result_rejected_atomically(store, plan, changes):
    run = store.create_run(plan, entry='cli', request_id='a', now=100)
    with pytest.raises(ValueError):
        store.record_unit(run, 'a', unit_result(**changes), now=101)
    assert store.get_run(run)['progress']['completed_units'] == 0


def test_failed_create_leaves_no_half_lock(store, plan):
    broken = copy.deepcopy(plan)
    broken['units'].append(copy.deepcopy(broken['units'][0]))
    with pytest.raises(ValueError):
        store.create_run(broken, entry='manual', request_id='bad', now=100)
    assert store.list_runs('2026-10-09') == []
    store.create_run(plan, entry='manual', request_id='good', now=101)


def test_idempotency_key_cannot_change_plan(store, plan):
    store.create_run(plan, entry='manual', request_id='a', now=100)
    changed = copy.deepcopy(plan)
    changed['date'] = '2026-10-08'
    with pytest.raises(ValueError):
        store.create_run(changed, entry='manual', request_id='a', now=101)


def test_events_are_monotonic_paginated_and_redacted(store, plan):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    for index in range(505):
        store.event(run, 'progress', {'stage': 'fetch', 'page': index,
                                     'token': 'DO_NOT_KEEP',
                                     'message': 'https://user:secret@host/x?token=DO_NOT_KEEP'}, now=101)
    first = store.get_run(run, after_seq=0, limit=500)
    assert len(first['events']) == 500
    assert 'DO_NOT_KEEP' not in json.dumps(first)
    assert 'user:secret' not in json.dumps(first)
    seqs = [e['seq'] for e in first['events']]
    assert seqs == sorted(set(seqs))
    second = store.get_run(run, after_seq=seqs[-1], limit=500)
    assert len(second['events']) >= 5
    assert all(e['seq'] > seqs[-1] for e in second['events'])
    with pytest.raises(ValueError):
        store.get_run(run, limit=501)


def test_two_independent_processes_cannot_acquire_same_resource(tmp_path, plan):
    path = tmp_path / 'runs.sqlite3'
    RunStore(path)
    script = """
import json, sys
from warehouse_runs import RunStore, RunBusy
store = RunStore(sys.argv[1])
try:
    store.create_run(json.loads(sys.argv[2]), entry='manual', request_id=sys.argv[3], now=100)
    print('acquired')
except RunBusy:
    print('busy')
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parent))
    procs = [subprocess.Popen([sys.executable, '-X', 'utf8', '-B', '-c', script,
              str(path), json.dumps(plan), str(i)], env=env, stdout=subprocess.PIPE,
              stderr=subprocess.PIPE, text=True) for i in range(2)]
    results = [p.communicate(timeout=15) for p in procs]
    assert [p.returncode for p in procs] == [0, 0], results
    assert sorted(out.strip() for out, err in results) == ['acquired', 'busy']


def receipt(run, **changes):
    data = dict(receipt_id='r1', run_id=run, unit_id='a', table='daily',
                provider='provider', channel='channel', source_id='source', api_name='daily',
                request_scope={'trade_date': '20261009'},
                requested_at='2026-10-09T15:00:00+08:00',
                received_at='2026-10-09T15:00:01+08:00', source_time=None,
                result_rows=5, result_kind='authorized_data', error_kind=None)
    data.update(changes)
    return data


@pytest.mark.parametrize('changes', [{'unit_id': 'foreign'}, {'table': 'foreign'},
    {'requested_at': '2026-10-09'}, {'received_at': '2026-10-09T14:59:00+08:00'},
    {'result_rows': -1}, {'result_kind': 'done'}, {'source_time': {'value': 'unknown'}}])
def test_invalid_source_receipt_cannot_become_evidence(store, plan, changes):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    with pytest.raises(ValueError):
        store.event(run, 'source_receipt', receipt(run, **changes), now=101)
    assert store.get_run(run)['receipts'] == []


def test_receipt_scope_redacted_and_association_preserved(store, plan):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    data = receipt(run, request_scope={'trade_date': '20261009', 'token': 'secret'})
    store.event(run, 'source_receipt', data, now=101)
    store.record_unit(run, 'a', unit_result(receipt_ids=['r1']), now=102)
    view = store.get_run(run)
    assert view['receipts'][0]['payload']['request_scope'] == {'trade_date': '20261009'}
    assert view['units'][0]['result']['receipt_ids'] == ['r1']
    with pytest.raises(ValueError):
        store.record_unit(run, 'b', unit_result(receipt_ids=['unknown']), now=103)


def test_receipt_idempotency_prevents_conflicting_source_facts(store, plan):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    first = store.event(run, 'source_receipt', receipt(run), now=101)
    assert store.event(run, 'source_receipt', receipt(run), now=102) == first
    with pytest.raises(ValueError):
        store.event(run, 'source_receipt', receipt(run, result_rows=1), now=103)


def test_concurrent_heartbeat_invalidates_stale_death_probe(store, plan):
    run = store.create_run(plan, entry='cli', request_id='a', now=100)
    store.claim(run, pid=123, created_at='p', now=100)
    def probe(pid, identity):
        store.heartbeat(run, pid=pid, created_at=identity, now=200)
        return 'dead'
    store.reconcile(probe, now=201)
    assert store.get_run(run)['status'] == 'running'
    with pytest.raises(RunBusy):
        store.create_run(plan, entry='manual', request_id='b', now=202)


def intent(**changes):
    data = {'commit_id': 'c1', 'unit_id': 'a', 'table': 'daily', 'receipt_ids': [],
            'target': 'daily.parquet', 'staged_path': 'daily.tmp.parquet',
            'old_sha256': None, 'new_sha256': 'a' * 64, 'counts': {'new_rows': 2}}
    data.update(changes)
    return data


def complete(**changes):
    data = dict(commit_id='c1', unit_id='a', table='daily', receipt_ids=[],
                counts={'new_rows': 2}, verification={'status': 'unknown'}, recovered=False)
    data.update(changes)
    return data


@pytest.mark.parametrize('payload', [{}, intent(unit_id='foreign'),
    intent(receipt_ids=['unknown']), intent(new_sha256='invalid')])
def test_invalid_commit_intent_rejected(store, plan, payload):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    with pytest.raises(ValueError):
        store.event(run, 'commit_intent', payload, now=101)


def test_complete_and_result_must_reference_valid_commit(store, plan):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    with pytest.raises(ValueError):
        store.event(run, 'commit_complete', complete(), now=101)
    with pytest.raises(ValueError):
        store.record_unit(run, 'a', unit_result(commit_id='unknown'), now=101)
    store.event(run, 'commit_intent', intent(), now=102)
    seq = store.event(run, 'commit_complete', complete(), now=103)
    assert store.event(run, 'commit_complete', complete(), now=104) == seq
    with pytest.raises(ValueError):
        store.record_unit(run, 'a', unit_result(commit_id='c1', new_rows=9), now=105)
    store.record_unit(run, 'a', unit_result(commit_id='c1'), now=105)


def test_recovered_commit_facts_allowed_without_reopening_terminal_run(store, plan):
    run = store.create_run(plan, entry='cli', request_id='a', now=100)
    store.claim(run, pid=123, created_at='p', now=100)
    store.event(run, 'commit_intent', intent(), now=101)
    store.reconcile(lambda *args: 'dead', now=200)
    store.event(run, 'commit_complete', complete(recovered=True), now=201)
    store.record_unit(run, 'a', unit_result(commit_id='c1', verification={'status': 'unknown'}), now=202)
    assert store.get_run(run)['status'] == 'interrupted'
    assert store.get_run(run)['metrics']['new_rows'] == 2
    with pytest.raises(ValueError):
        store.event(run, 'commit_intent', intent(commit_id='c2'), now=203)


def test_queued_task_cannot_succeed_without_owner_and_verifying_is_explicit(store, plan):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    store.record_unit(run, 'a', unit_result(), now=101)
    store.record_unit(run, 'b', unit_result(), now=101)
    with pytest.raises(ValueError):
        store.finish(run, 'succeeded', exit_code=0, now=102)
    with pytest.raises(ValueError):
        store.finish(run, 'partial', exit_code=1, now=102)
    store.claim(run, pid=1, created_at='p', now=103)
    store.event(run, 'progress', {'stage': 'verifying'}, now=104)
    assert store.get_run(run)['status'] == 'verifying'
    store.claim(run, pid=1, created_at='p', now=105)
    assert store.get_run(run)['status'] == 'verifying'


@pytest.mark.parametrize('source_time', [
    dict(value=123, timezone=[], kind='business_time', evidence={}),
    dict(value='20261008', timezone='BAD/TZ', kind='business_time', evidence='source'),
])
def test_source_time_invalid_types_or_timezone_rejected(store, plan, source_time):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    with pytest.raises(ValueError):
        store.event(run, 'source_receipt', receipt(run, source_time=source_time), now=101)


def test_error_message_credentials_are_redacted(store, plan):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    store.event(run, 'error', {'message': 'Authorization: Basic SYNTHETIC_BASIC Cookie: sid=SYNTHETIC_COOKIE {"token":"SYNTHETIC_JSON"}'}, now=101)
    result = json.dumps(store.get_run(run))
    assert not any(value in result for value in ('SYNTHETIC_BASIC', 'SYNTHETIC_COOKIE', 'SYNTHETIC_JSON'))


@pytest.mark.parametrize('message', ['{"Cookie":"sid=SYNTHETIC_VALUE"}',
                                   'Authorization: Digest SYNTHETIC_VALUE'])
def test_unrecognized_auth_message_is_discarded_not_partially_redacted(store, plan, message):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    store.event(run, 'error', {'message': message}, now=101)
    assert 'SYNTHETIC_VALUE' not in json.dumps(store.get_run(run))


@pytest.mark.parametrize('changes', [
    {'error_kind': {}},
    {'source_time': dict(value='not-time', timezone='UTC', kind='business_time', evidence='source')},
    {'source_time': dict(value='2026-10-08T12:00:00+08:00', timezone='UTC', kind='business_time', evidence='source')},
])
def test_malformed_time_or_error_type_cannot_enter_source_evidence(store, plan, changes):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    with pytest.raises(ValueError):
        store.event(run, 'source_receipt', receipt(run, **changes), now=101)


def test_complete_cannot_change_intent_counts(store, plan):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    store.event(run, 'commit_intent', intent(), now=101)
    with pytest.raises(ValueError):
        store.event(run, 'commit_complete', complete(counts={'new_rows': 9}), now=102)


def test_multifile_intent_requires_manifest_and_unique_owned_file_units(store, plan):
    run = store.create_run(plan, entry='manual', request_id='a', now=100)
    file = dict(unit_id='a', target='daily-a.parquet', staged_path='daily-a.tmp.parquet',
                old_sha256=None, new_sha256='a' * 64)
    manifest = dict(target='_manifest.json', old_sha256=None, new_sha256='b' * 64)
    for files, meta in (([file], None), ([file, file], manifest),
                        ([dict(file, unit_id='foreign')], manifest)):
        with pytest.raises(ValueError):
            store.event(run, 'commit_intent', dict(commit_id='c1', table='daily',
                         receipt_ids=[], files=files, manifest=meta), now=101)


def test_multifile_results_attribute_counts_to_each_unit_once(store, plan):
    run = store.create_run(plan, entry='cli', request_id='a', now=100)
    store.claim(run, pid=1, created_at='p', now=100)
    files = [dict(unit_id=uid, target=uid + '.parquet', staged_path=uid + '.tmp',
                  old_sha256=None, new_sha256='a' * 64, counts={'new_rows': count})
             for uid, count in [('a', 2), ('b', 3)]]
    meta = dict(target='_manifest.json', old_sha256=None, new_sha256='b' * 64)
    data = dict(commit_id='table-c', table='daily', receipt_ids=[], files=files,
                manifest=meta, counts={'new_rows': 5})
    store.event(run, 'commit_intent', data, now=101)
    store.event(run, 'commit_complete', dict(data, recovered=False, verification={'status': 'unknown'}), now=102)
    for uid, count in [('a', 2), ('b', 3)]:
        store.record_unit(run, uid, unit_result(commit_id='table-c', new_rows=count,
                  processed_rows=None, revised_rows=None, duplicate_rows=None,
                  rejected_rows=None, bytes_written=None, net_storage_bytes=None), now=103)
    assert store.get_run(run)['metrics']['new_rows'] == 5


def test_recovery_of_completed_commit_with_missing_result_is_idempotent(store, plan):
    run = store.create_run(plan, entry='cli', request_id='a', now=100)
    store.claim(run, pid=1, created_at='p', now=100)
    store.event(run, 'commit_intent', intent(), now=101)
    store.event(run, 'commit_complete', complete(), now=102)
    store.reconcile(lambda *args: 'dead', now=200)
    recovery = complete(recovered=True)
    seq = store.event(run, 'commit_recovery', recovery, now=201)
    assert store.event(run, 'commit_recovery', recovery, now=202) == seq
    store.record_unit(run, 'a', unit_result(commit_id='c1', verification={'status': 'unknown'}), now=203)
    store.record_unit(run, 'a', unit_result(commit_id='c1', verification={'status': 'unknown'}), now=204)
    result = store.get_run(run)
    assert result['status'] == 'interrupted'
    assert result['metrics']['new_rows'] == 2
    assert [e for e in result['events'] if e['kind'] == 'commit_complete'][0]['payload']['recovered'] is False


def test_recovery_cannot_create_completion_or_change_known_fact(store, plan):
    run = store.create_run(plan, entry='cli', request_id='a', now=100)
    store.event(run, 'commit_intent', intent(), now=101)
    with pytest.raises(ValueError):
        store.event(run, 'commit_recovery', complete(recovered=True), now=102)
    store.event(run, 'commit_complete', complete(), now=103)
    with pytest.raises(ValueError):
        store.event(run, 'commit_recovery', complete(recovered=True, counts={'new_rows': 9}), now=104)


def test_new_resource_owner_blocks_terminal_result_recovery(store, plan):
    run = store.create_run(plan, entry='cli', request_id='a', now=100)
    store.claim(run, pid=1, created_at='p', now=100)
    store.event(run, 'commit_intent', intent(), now=101)
    store.reconcile(lambda *args: 'dead', now=200)
    store.event(run, 'commit_complete', complete(recovered=True), now=201)
    store.create_run(plan, entry='manual', request_id='b', now=202)
    with pytest.raises(RunBusy):
        store.record_unit(run, 'a', unit_result(commit_id='c1', verification={'status': 'unknown'}), now=203)


def test_source_receipt_retains_nonsecret_tushare_filters(store, plan):
    run = store.create_run(plan, entry='manual', request_id='filters', now=100)
    scope = {'list_status': 'L', 'src': 'SW2021', 'lv': 'L1', 'token': 'do-not-save'}
    store.event(run, 'source_receipt', receipt(run, request_scope=scope), now=101)
    actual = store.get_run(run)['receipts'][0]['payload']['request_scope']
    assert actual == {'list_status':'L','src':'SW2021','lv':'L1'}


def test_two_processes_reserve_same_launch_only_once(tmp_path, plan):
    store = RunStore(tmp_path/'runs.sqlite3')
    run = store.create_run(plan, entry='manual', request_id='launch', now=100)
    script = """
import sys
from warehouse_runs import RunStore
print(RunStore(sys.argv[1]).reserve_launch(sys.argv[2], reservation_id=sys.argv[3], now=101))
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parent))
    procs = [subprocess.Popen([sys.executable,'-X','utf8','-B','-c',script,store.path,run,str(i)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env) for i in range(2)]
    outputs = [p.communicate(timeout=15) for p in procs]
    assert all(p.returncode==0 for p in procs), outputs
    assert sorted(out.strip() for out,err in outputs) == ['False','True']
