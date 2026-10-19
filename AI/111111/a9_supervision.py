"""本地 A9 提醒和基于现有军团心跳账本的只读监督。"""
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = ROOT / 'data' / 'runtime' / 'a9'
DEFAULT_HEARTBEATS = ROOT / 'agent_platform' / 'data' / 'llm_tool_audit.jsonl'


class StoreError(Exception):
    pass


def _now():
    return datetime.now().astimezone()


def _iso(value):
    return value.isoformat(timespec='seconds')


def _date(value):
    if not isinstance(value, str):
        raise ValueError('时间必须是带时区的 ISO 时间')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('时间必须是带时区的 ISO 时间') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('时间必须是带时区的 ISO 时间')
    return parsed


def _text(value, name, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} 必须是非空字符串，最多 {limit} 字')
    return value.strip()


class A9Service:
    def __init__(self, runtime_dir=DEFAULT_RUNTIME, heartbeat_path=DEFAULT_HEARTBEATS,
                 timeout_minutes=30, escalation_minutes=30, window_minutes=60):
        self.runtime_dir = Path(runtime_dir)
        self.heartbeat_path = Path(heartbeat_path)
        self.timeout_minutes = timeout_minutes
        self.escalation_minutes = escalation_minutes
        self.window_minutes = window_minutes
        self.lock = threading.RLock()

    @property
    def snapshot(self):
        return self.runtime_dir / 'reminders.json'

    @property
    def audit(self):
        return self.runtime_dir / 'events.jsonl'

    def _load(self):
        try:
            data = json.loads(self.snapshot.read_text(encoding='utf-8'))
        except FileNotFoundError:
            if self.audit.exists():
                raise StoreError('提醒存储缺失但审计账本存在')
            data = {'reminders': []}
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StoreError('提醒存储损坏或不可读') from exc
        if not isinstance(data, dict) or not isinstance(data.get('reminders'), list):
            raise StoreError('提醒存储结构损坏')
        for row in data['reminders']:
            if not isinstance(row, dict) or not all(k in row for k in
                    ('id', 'title', 'due_at', 'status', 'severity', 'cycle', 'escalated')):
                raise StoreError('提醒存储结构损坏')
            try:
                _date(row['due_at'])
                if (not isinstance(row['id'], str) or not isinstance(row['title'], str)
                        or row['status'] not in ('pending', 'due', 'acknowledged')
                        or row['severity'] not in ('low', 'medium', 'high')
                        or type(row['cycle']) is not int or row['cycle'] < 0
                        or type(row['escalated']) is not bool):
                    raise ValueError()
            except (ValueError, TypeError):
                raise StoreError('提醒存储内容损坏')
        if data['reminders'] and not self.audit.exists():
            raise StoreError('提醒审计账本缺失')
        try:
            with self.audit.open(encoding='utf-8') as stream:
                for line in stream:
                    if not line.strip():
                        raise StoreError('提醒审计账本损坏')
                    event = json.loads(line)
                    if not isinstance(event, dict) or not all(k in event for k in ('ts', 'event', 'id')):
                        raise StoreError('提醒审计账本损坏')
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StoreError('提醒审计账本损坏或不可读') from exc
        return data

    def _save(self, data, events):
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        filename = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.runtime_dir,
                                             prefix='.reminders-', delete=False) as stream:
                filename = stream.name
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(filename, self.snapshot)
            if events:
                with self.audit.open('a', encoding='utf-8') as stream:
                    for event, row in events:
                        stream.write(json.dumps({'ts': _iso(_now()), 'event': event,
                                                 'id': row['id'], 'cycle': row['cycle']}, ensure_ascii=False) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
        except OSError as exc:
            raise StoreError('提醒存储写入失败') from exc
        finally:
            if filename and os.path.exists(filename):
                os.unlink(filename)

    def create(self, payload, now=None):
        if not isinstance(payload, dict):
            raise ValueError('请求必须是 JSON 对象')
        allowed = {'title', 'due_at', 'severity', 'repeat_minutes', 'dedupe_key', 'recipient'}
        if set(payload) - allowed:
            raise ValueError('提醒包含未知字段')
        title = _text(payload.get('title'), 'title')
        due_at = _iso(_date(payload.get('due_at')))
        severity = payload.get('severity')
        if severity not in ('low', 'medium', 'high'):
            raise ValueError('severity 必须是 low/medium/high')
        repeat = payload.get('repeat_minutes')
        if repeat is not None and (type(repeat) is not int or not 1 <= repeat <= 525600):
            raise ValueError('repeat_minutes 必须是正整数（不超过一年）')
        key = payload.get('dedupe_key')
        if key is not None:
            key = _text(key, 'dedupe_key')
        recipient = _text(payload.get('recipient', 'local'), 'recipient')
        with self.lock:
            data = self._load()
            if key:
                for row in data['reminders']:
                    if row.get('dedupe_key') == key:
                        return row.copy()
            row = {'id': uuid.uuid4().hex, 'title': title, 'due_at': due_at,
                   'severity': severity, 'repeat_minutes': repeat, 'dedupe_key': key,
                   'recipient': recipient, 'status': 'pending', 'cycle': 0,
                   'escalated': False, 'created_at': _iso(now or _now())}
            data['reminders'].append(row)
            self._save(data, [('created', row)])
            return row.copy()

    def list(self, now=None):
        with self.lock:
            data = self._load()
            current = now or _now()
            events = []
            for row in data['reminders']:
                if row['status'] == 'pending' and _date(row['due_at']) <= current:
                    row['status'] = 'due'
                    events.append(('due', row))
                if (row['status'] == 'due' and row['severity'] == 'high' and not row['escalated']
                        and _date(row['due_at']) + timedelta(minutes=self.escalation_minutes) <= current):
                    row['escalated'] = True
                    events.append(('escalated', row))
            if events:
                self._save(data, events)
            return [row.copy() for row in data['reminders']]

    def ack(self, reminder_id, now=None):
        reminder_id = _text(reminder_id, 'id', 100)
        with self.lock:
            data = self._load()
            row = next((item for item in data['reminders'] if item['id'] == reminder_id), None)
            if row is None:
                raise KeyError('提醒不存在')
            if row['status'] == 'acknowledged':
                return row.copy()
            current = now or _now()
            row['status'] = 'acknowledged'
            events = [('acknowledged', row.copy())]
            if row['repeat_minutes']:
                due = _date(row['due_at'])
                interval = timedelta(minutes=row['repeat_minutes'])
                while due <= current:
                    due += interval
                row['due_at'] = _iso(due)
                row['cycle'] += 1
                row['status'] = 'pending'
                row['escalated'] = False
            self._save(data, events)
            return row.copy()

    def agents(self, now=None):
        current = now or _now()
        try:
            lines = self.heartbeat_path.read_text(encoding='utf-8').splitlines()
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError) as exc:
            raise StoreError('心跳账本不可读') from exc
        agents = {}
        for line in lines:
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get('ts'), str):
                    raise ValueError()
                ts = datetime.fromisoformat(row['ts'].replace('Z', '+00:00'))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=current.tzinfo)
                caller = row.get('caller')
                if caller is not None:
                    caller = _text(caller, 'caller')
                if isinstance(row.get('action'), str) and isinstance(row.get('detail'), str):
                    action, detail = row['action'], row['detail']
                    failed = bool(re.search(r'fail|error|exception|失败|错误|异常', action + ' ' + detail, re.I))
                    blocked = bool(re.search(r'block|阻塞|卡住', action + ' ' + detail, re.I))
                elif (isinstance(row.get('endpoint'), str) and isinstance(row.get('method'), str)
                      and type(row.get('ok')) is bool):
                    action, detail = row['endpoint'], row['method']
                    failed = row['ok'] is False
                    blocked = False
                else:
                    raise ValueError()
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise StoreError('心跳账本损坏') from exc
            if ts > current or caller is None:
                continue
            item = agents.setdefault(caller, {'agent': caller, 'last_activity': None,
                                               'status': '未观测', 'evidence': None, 'error_count': 0})
            if ts >= current - timedelta(minutes=self.window_minutes) and (failed or blocked):
                item['error_count'] += 1
            if item['last_activity'] is None or ts > _date(item['last_activity']):
                item['last_activity'] = _iso(ts)
                item['evidence'] = {'ts': row['ts'], 'action': action, 'detail': detail}
                item['status'] = '阻塞' if blocked else '失败' if failed else '正常'
        for item in agents.values():
            if _date(item['last_activity']) < current - timedelta(minutes=self.timeout_minutes):
                item['status'] = '超时'
        return sorted(agents.values(), key=lambda item: item['agent'])

    def check_agents(self, now=None):
        current = now or _now()
        agents = self.agents(current)
        created = []
        for item in agents:
            if item['status'] not in ('超时', '失败', '阻塞'):
                continue
            key = f"agent:{item['agent']}:{item['status']}:{item['last_activity']}"
            with self.lock:
                existing = next((row for row in self._load()['reminders'] if row.get('dedupe_key') == key), None)
                if existing:
                    continue
                created.append(self.create({'title': f"Agent {item['agent']} {item['status']}",
                                            'due_at': _iso(current), 'severity': 'high',
                                            'dedupe_key': key}, now=current))
        return {'agents': agents, 'created': created}
