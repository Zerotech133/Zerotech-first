import importlib
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

mod = importlib.import_module('AI.111111.a9_supervision')
web = importlib.import_module('AI.111111.web_chat')
BASE = datetime(2026, 10, 18, 12, tzinfo=timezone(timedelta(hours=8)))


class A9Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.service = mod.A9Service(root / 'a9', root / 'heartbeat.jsonl')

    def payload(self, **kwargs):
        return {'title': '喝水', 'due_at': BASE.isoformat(), 'severity': 'high', **kwargs}

    def test_lifecycle_escalation_dedupe_persistence(self):
        row = self.service.create(self.payload(dedupe_key='one'), BASE)
        self.assertEqual(row['id'], self.service.create(self.payload(dedupe_key='one'))['id'])
        self.assertEqual(self.service.list(BASE - timedelta(seconds=1))[0]['status'], 'pending')
        self.assertEqual(self.service.list(BASE)[0]['status'], 'due')
        escalated = self.service.list(BASE + timedelta(minutes=30))[0]
        self.assertTrue(escalated['escalated'])
        self.service.list(BASE + timedelta(hours=1))
        events = [json.loads(line)['event'] for line in self.service.audit.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(events, ['created', 'due', 'escalated'])
        self.service.ack(row['id'], BASE + timedelta(hours=1))
        self.service.ack(row['id'], BASE + timedelta(hours=1))
        restored = mod.A9Service(self.service.runtime_dir, self.service.heartbeat_path)
        self.assertEqual(restored.list(BASE + timedelta(hours=2))[0]['status'], 'acknowledged')
        self.assertEqual(len(self.service.audit.read_text(encoding='utf-8').splitlines()), 4)

    def test_repeat_advances_past_ack_and_does_not_realert_cycle(self):
        row = self.service.create(self.payload(repeat_minutes=10), BASE)
        self.service.list(BASE)
        advanced = self.service.ack(row['id'], BASE + timedelta(minutes=25))
        self.assertEqual(advanced['due_at'], (BASE + timedelta(minutes=30)).isoformat(timespec='seconds'))
        self.assertEqual(advanced['cycle'], 1)
        self.assertEqual(self.service.list(BASE + timedelta(minutes=25))[0]['status'], 'pending')
        self.assertEqual(self.service.list(BASE + timedelta(minutes=30))[0]['status'], 'due')
        self.assertEqual(self.service.list(BASE + timedelta(minutes=30))[0]['cycle'], 1)

    def test_corrupt_snapshot_and_audit_fail_visible(self):
        self.service.create(self.payload(), BASE)
        self.service.snapshot.write_text('bad', encoding='utf-8')
        with self.assertRaises(mod.StoreError):
            self.service.list()
        self.service.snapshot.write_text('{"reminders": []}', encoding='utf-8')
        self.service.audit.write_text('bad\n', encoding='utf-8')
        with self.assertRaises(mod.StoreError):
            self.service.create(self.payload())

    def test_concurrent_dedupe(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(
            self.service.create(self.payload(dedupe_key='shared'))['id'])) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(len(self.service.list()), 1)

    def test_validation(self):
        for payload in [self.payload(due_at='2026-10-18T12:00:00'),
                        self.payload(repeat_minutes=True), self.payload(severity='critical')]:
            with self.assertRaises(ValueError):
                self.service.create(payload)

    def test_supervision_evidence_timeout_and_deduped_check(self):
        records = [
            {'ts': (BASE - timedelta(minutes=40)).replace(tzinfo=None).isoformat(), 'caller': 'slow', 'action': 'run', 'detail': 'started'},
            {'ts': (BASE - timedelta(minutes=5)).replace(tzinfo=None).isoformat(), 'caller': 'bad', 'action': 'error', 'detail': 'failed task'},
            {'ts': (BASE - timedelta(minutes=2)).replace(tzinfo=None).isoformat(), 'caller': 'stuck', 'action': 'blocked', 'detail': 'waiting'},
            {'ts': (BASE - timedelta(minutes=1)).replace(tzinfo=None).isoformat(), 'caller': 'good', 'action': 'run', 'detail': 'ok'},
        ]
        self.service.heartbeat_path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
        agents = {a['agent']: a for a in self.service.agents(BASE)}
        self.assertEqual([agents[n]['status'] for n in ('slow', 'bad', 'stuck', 'good')], ['超时', '失败', '阻塞', '正常'])
        self.assertEqual(agents['bad']['error_count'], 1)
        self.assertEqual(agents['stuck']['evidence']['detail'], 'waiting')
        self.assertEqual(len(self.service.check_agents(BASE)['created']), 3)
        self.assertEqual(len(self.service.check_agents(BASE)['created']), 0)
        self.assertEqual(len(self.service.list(BASE)), 3)

    def test_mixed_activity_formats_and_null_caller(self):
        records = [
            {'ts': (BASE - timedelta(minutes=3)).isoformat(), 'caller': None,
             'endpoint': '/api/task/create', 'method': 'POST', 'ok': False},
            {'ts': (BASE - timedelta(minutes=2)).isoformat(), 'caller': 'worker',
             'action': 'run', 'detail': 'started'},
            {'ts': (BASE - timedelta(minutes=1)).isoformat(), 'caller': 'worker',
             'endpoint': '/api/task/status', 'method': 'GET', 'ok': False},
            {'ts': BASE.isoformat(), 'caller': 'healthy',
             'endpoint': '/api/task/list', 'method': 'GET', 'ok': True},
        ]
        self.service.heartbeat_path.write_text(
            ''.join(json.dumps(row) + '\n' for row in records), encoding='utf-8')
        agents = {row['agent']: row for row in self.service.agents(BASE)}
        self.assertEqual(set(agents), {'worker', 'healthy'})
        self.assertEqual(agents['worker']['status'], '失败')
        self.assertEqual(agents['worker']['error_count'], 1)
        self.assertEqual(agents['worker']['evidence']['action'], '/api/task/status')
        self.assertEqual(agents['healthy']['status'], '正常')
        self.assertEqual(len(self.service.check_agents(BASE)['created']), 1)

    def test_malformed_activity_still_fails_visible(self):
        self.service.heartbeat_path.write_text(json.dumps({
            'ts': BASE.isoformat(), 'caller': 'worker', 'endpoint': '/api/task/list',
            'method': 'GET', 'ok': 'false'}) + '\n', encoding='utf-8')
        with self.assertRaises(mod.StoreError):
            self.service.agents(BASE)

    def test_old_failed_or_blocked_events_become_timeout(self):
        records = [
            {'ts': (BASE - timedelta(minutes=40)).isoformat(), 'caller': name,
             'action': action, 'detail': 'old event'}
            for name, action in (('failed-agent', 'failed'), ('blocked-agent', 'blocked'))
        ]
        self.service.heartbeat_path.write_text(
            ''.join(json.dumps(row) + '\n' for row in records), encoding='utf-8')
        agents = self.service.agents(BASE)
        self.assertEqual([agent['status'] for agent in agents], ['超时', '超时'])
        self.assertEqual(len(self.service.check_agents(BASE)['created']), 2)

    def test_background_check_without_browser_and_clean_shutdown(self):
        self.service.escalation_minutes = 0
        server = web.make_server(port=0, a9=self.service, interval=0.02)
        self.addCleanup(server.server_close)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        self.addCleanup(serving.join, 2)
        self.addCleanup(server.shutdown)
        now = datetime.now().astimezone()
        row = self.service.create({'title': '自动到期', 'due_at': (now - timedelta(seconds=5)).isoformat(),
                                   'severity': 'high'})
        heartbeat = {'ts': (now - timedelta(minutes=40)).isoformat(), 'caller': 'offline',
                     'action': 'run', 'detail': 'started'}
        self.service.heartbeat_path.write_text(json.dumps(heartbeat) + '\n', encoding='utf-8')
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            reminders = self.service.list()
            if (any(item['id'] == row['id'] and item['status'] == 'due' and item['escalated']
                    for item in reminders) and any((item.get('dedupe_key') or '').startswith('agent:offline:超时:')
                                                     for item in reminders)):
                break
            time.sleep(0.02)
        else:
            self.fail('后台未在无浏览器请求时完成到期、升级与 Agent 巡检')
        server.shutdown()
        serving.join(2)
        self.assertFalse(serving.is_alive())
        self.assertFalse(server._a9_worker.is_alive())

    def test_background_failure_is_visible_in_api(self):
        server = web.make_server(port=0, a9=self.service, interval=0.02)
        self.addCleanup(server.server_close)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        self.addCleanup(server.shutdown)
        self.service.create({'title': '持久化失败', 'due_at':
                             (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(),
                             'severity': 'high'})
        with patch.object(self.service, '_save', side_effect=mod.StoreError('private detail')), \
                patch.object(web.sys, 'stderr') as stderr:
            deadline = time.monotonic() + 3
            while not server._a9_error.is_set() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(server._a9_error.is_set())
            self.assertTrue(stderr.write.called)
            self.assertNotIn('private detail', str(stderr.write.call_args_list))
            conn = HTTPConnection('127.0.0.1', server.server_port, timeout=2)
            try:
                conn.request('GET', '/api/reminders')
                response = conn.getresponse()
                self.assertEqual(response.status, 500)
                self.assertIn('后台巡检失败', json.loads(response.read())['error'])
            finally:
                conn.close()
        server.shutdown()

    def test_daily_brief_http_is_independent_of_a9_and_reports_missing_sources(self):
        brief_mod = importlib.import_module('AI.111111.a9_brief')
        root = Path(self.temp.name)
        brief = brief_mod.BriefService(root / 'missing_tasks.json', root / 'missing_activity.jsonl',
                                       root / 'missing_shadow.jsonl')
        server = web.make_server(port=0, a9=self.service, brief=brief, interval=60)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        with patch.object(server, 'a9_ready', side_effect=mod.StoreError('不可用')):
            conn = HTTPConnection('127.0.0.1', server.server_port, timeout=2)
            try:
                conn.request('GET', '/api/brief/daily')
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn('application/json', response.getheader('Content-Type'))
                body = json.loads(response.read())
            finally:
                conn.close()
        self.assertIn('day', body)
        self.assertIn('generated_at', body)
        self.assertIsNone(body['metrics']['current']['in_progress'])
        self.assertEqual(body['data_quality']['sources_available'],
                         {'tasks': False, 'activity': False, 'shadow': False})
        self.assertTrue(body['data_quality']['issues'])
        with patch.object(brief, 'build', side_effect=OSError('private detail')):
            conn = HTTPConnection('127.0.0.1', server.server_port, timeout=2)
            try:
                conn.request('GET', '/api/brief/daily')
                response = conn.getresponse()
                self.assertEqual(response.status, 500)
                self.assertNotIn('private detail', response.read().decode())
            finally:
                conn.close()

    def test_http_routes_and_origin_host(self):
        server = web.make_server(port=0, a9=self.service)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        port = server.server_port

        def request(method, path, body=None, headers=None):
            conn = HTTPConnection('127.0.0.1', port)
            conn.request(method, path, json.dumps(body) if body is not None else None, headers or {})
            response = conn.getresponse()
            status, data = response.status, json.loads(response.read())
            conn.close()
            return status, data

        for headers in ({'Origin': 'https://evil.test'}, {'Host': 'evil.test'}, {'Origin': 'null'}):
            status, _ = request('POST', '/api/reminders', self.payload(), headers)
            self.assertEqual(status, 403)
        self.assertEqual(request('GET', '/api/reminders')[1], [])
        self.assertEqual(request('POST', '/api/reminders', self.payload(due_at='bad'))[0], 400)
        status, row = request('POST', '/api/reminders', self.payload(), {'Origin': f'http://127.0.0.1:{port}'})
        self.assertEqual(status, 201)
        self.assertEqual(request('GET', '/api/reminders')[1][0]['id'], row['id'])
        self.assertEqual(request('POST', '/api/reminders/ack', {'id': row['id']})[0], 200)
        self.assertEqual(request('GET', '/api/agents')[1], [])
        self.assertEqual(request('POST', '/api/agents/check', {})[1]['created'], [])
        self.assertEqual(request('POST', '/api/agents/check', {'path': '/tmp/evil'})[0], 400)
        self.assertEqual(request('GET', '/api/reminders/../../secret')[0], 404)


if __name__ == '__main__':
    unittest.main()
