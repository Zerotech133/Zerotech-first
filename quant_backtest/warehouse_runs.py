"""每日更新任务持久化；资源锁只在合格终态或证实死亡时释放。"""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TERMINAL = {'succeeded', 'partial', 'failed', 'interrupted', 'blocked'}
UNIT_STATUSES = {'succeeded', 'failed', 'interrupted', 'blocked', 'source_empty',
                 'no_local_file', 'unsupported', 'rejected'}
UNIT_FAILURES = UNIT_STATUSES - {'succeeded', 'source_empty'}
COUNT_FIELDS = ('processed_rows', 'new_rows', 'revised_rows', 'duplicate_rows',
                'rejected_rows', 'bytes_written', 'net_storage_bytes')
RESULT_FIELDS = {'execution_status', 'provider', 'channel', 'source_id',
                 'receipt_ids', 'commit_id', 'error_kind', 'verification', *COUNT_FIELDS}
PLAN_FIELDS = {'version', 'source', 'resource', 'market', 'date', 'selected_tables',
               'retry_of', 'retry_units', 'registry_digest', 'targets', 'units',
               'operations', 'unsupported', 'baseline_digest', 'plan_digest'}
UNIT_FIELDS = {'unit_id', 'table', 'target', 'kind', 'scope', 'selection_reason'}
EVENT_FIELDS = {
    'progress': {'stage', 'page', 'processed_rows', 'total_rows', 'completed_units',
                 'total_units', 'message', 'table', 'unit_id', 'error_kind'},
    'source_receipt': {'receipt_id', 'run_id', 'unit_id', 'table', 'provider',
        'channel', 'source_id', 'api_name', 'request_scope', 'requested_at',
        'received_at', 'source_time', 'result_rows', 'result_kind', 'error_kind'},
    'commit_intent': {'commit_id', 'unit_id', 'table', 'receipt_ids', 'target',
        'staged_path', 'old_sha256', 'new_sha256', 'counts', 'files', 'manifest',
        'resource', 'business_columns', 'primary_key'},
    'commit_complete': {'commit_id', 'unit_id', 'table', 'receipt_ids', 'counts',
                        'files', 'manifest', 'verification', 'recovered'},
    'commit_recovery': {'commit_id', 'unit_id', 'table', 'receipt_ids', 'counts',
                        'files', 'manifest', 'verification', 'recovered'},
    'verification': {'unit_id', 'table', 'status', 'verified_at', 'warehouse_digest',
                     'eligibility_digest', 'metrics', 'reason'},
    'error': {'stage', 'table', 'unit_id', 'error_kind', 'message'},
}
SCOPE_FIELDS = {'code', 'codes', 'ts_code', 'trade_date', 'date', 'start_date',
                'end_date', 'start_time', 'end_time', 'offset', 'limit', 'page',
    'exchange', 'market', 'frequency', 'freq', 'fields', 'period', 'list_status', 'src', 'lv'}
SECRET_KEY = re.compile(r'password|passwd|secret|token|authorization|cookie|api_key', re.I)


class RunBusy(RuntimeError):
    """同一实际资源已有未终结任务。"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _redact(value):
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items() if not SECRET_KEY.search(str(k))}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        if re.search(r'(?i)(?:authorization|(?:set-)?cookie|token|password|passwd|secret|api_key)[\"\x27 ]*\s*[:=]|\b(?:bearer|basic|digest)\s+', value):
            return '[含认证信息的文本已脱敏]'
        # 不保存原始URL、认证头或带键的秘密；仅存白名单来源接口。
        value = re.sub(r'https?://[^\s]+', '[URL已脱敏]', value)
        value = re.sub(r'(?i)(bearer\s+)[^\s]+', r'\1[已脱敏]', value)
        value = re.sub(r'(?i)(basic\s+)[^\s]+', r'\1[已脱敏]', value)
        value = re.sub(r'(?i)(?:set-cookie|cookie)\s*:[^\r\n{}]+', '[Cookie已脱敏] ', value)
        value = re.sub(r'(?i)([\"\x27]?(?:token|password|secret|api_key)[\"\x27]?\s*[:=]\s*)[\"\x27][^\"\x27]*[\"\x27]', r'\1[已脱敏]', value)
        value = re.sub(r'(?i)(token|password|secret|api_key)\s*[:=]\s*[^\s,;]+', r'\1=[已脱敏]', value)
    return value


def _now(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('now必须为显式时间戳')
    _json(value)
    return value


def _text(value, field):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f'{field}必须为非空短文本')
    return value


def _timestamp(value, field):
    _text(value, field)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f'{field}必须为ISO时点') from exc
    if parsed.tzinfo is None:
        raise ValueError(f'{field}必须包含时区')
    return parsed


def _receipts(db, run_id):
    rows = db.execute("SELECT seq,payload FROM events WHERE run_id=? AND kind='source_receipt'", (run_id,)).fetchall()
    return {json.loads(row['payload'])['receipt_id']: (row['seq'], json.loads(row['payload'])) for row in rows}


def _commits(db, run_id, kind):
    rows = db.execute('SELECT seq,payload FROM events WHERE run_id=? AND kind=?', (run_id, kind)).fetchall()
    return {json.loads(row['payload'])['commit_id']: (row['seq'], json.loads(row['payload'])) for row in rows}


def _counts(counts):
    if counts is None:
        return
    if not isinstance(counts, dict) or set(counts) - set(COUNT_FIELDS):
        raise ValueError('提交计量字段不合法')
    for field, value in counts.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or (field != 'net_storage_bytes' and value < 0)):
            raise ValueError('提交计量类型不合法')


def _fingerprint(value, *, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('文件SHA256不合法')


def _plan(plan):
    if not isinstance(plan, dict) or set(plan) != PLAN_FIELDS or plan['version'] != 1:
        raise ValueError('计划字段或版本不合法')
    for field in ('source', 'resource', 'market', 'date', 'registry_digest', 'baseline_digest', 'plan_digest'):
        _text(plan[field], field)
    for field in ('selected_tables', 'retry_units', 'targets', 'units', 'operations', 'unsupported'):
        if not isinstance(plan[field], list):
            raise ValueError(f'{field}必须为列表')
    ids = set()
    for unit in plan['units']:
        if not isinstance(unit, dict) or set(unit) != UNIT_FIELDS:
            raise ValueError('单元字段不合法')
        uid = _text(unit['unit_id'], 'unit_id')
        _text(unit['table'], 'table')
        if uid in ids or unit['kind'] not in {'table', 'code', 'date', 'window'}:
            raise ValueError('单元重复或类型不合法')
        if unit['selection_reason'] not in {'user', 'dependency', 'retry'} or not isinstance(unit['scope'], dict):
            raise ValueError('单元范围不合法')
        ids.add(uid)
    # 任务计划不得携带凭据；范围与哈希的完整验证由计划模块完成。
    if _redact(plan) != plan:
        raise ValueError('计划含敏感字段或URL')
    return _json(plan)


@contextmanager
def immediate(connection):
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


class RunStore:
    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            with immediate(db):
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0, 1):
                    raise ValueError('任务库版本不支持')
                db.execute('CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, entry TEXT NOT NULL, resource TEXT NOT NULL, date TEXT NOT NULL, plan TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, started_at REAL, heartbeat_at REAL, finished_at REAL, pid INTEGER, process_created_at TEXT, process_status TEXT, exit_code INTEGER)')
                db.execute('CREATE TABLE IF NOT EXISTS units (run_id TEXT NOT NULL REFERENCES runs(run_id), unit_id TEXT NOT NULL, plan TEXT NOT NULL, result TEXT, recorded_at REAL, PRIMARY KEY(run_id,unit_id))')
                db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), at REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS resource_locks (resource TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL REFERENCES runs(run_id), pid INTEGER, created_at TEXT, heartbeat_at REAL NOT NULL)')
                db.execute('CREATE INDEX IF NOT EXISTS event_run_seq ON events(run_id,seq)')
                db.execute('PRAGMA user_version=1')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=5000')
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
        finally:
            db.close()

    def _run(self, db, run_id):
        row = db.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row

    def _active(self, db, run_id):
        row = self._run(db, run_id)
        lock = db.execute('SELECT run_id FROM resource_locks WHERE resource=?', (row['resource'],)).fetchone()
        if row['status'] in TERMINAL or not lock or lock['run_id'] != run_id:
            raise ValueError('任务已终结或不持有资源锁')
        return row

    def _event(self, db, run_id, kind, payload, now):
        return db.execute('INSERT INTO events(run_id,at,kind,payload) VALUES(?,?,?,?)',
                          (run_id, now, kind, _json(payload))).lastrowid

    def create_run(self, plan, *, entry, request_id, now):
        encoded = _plan(plan)
        _now(now)
        _text(request_id, 'request_id')
        if entry not in {'manual', 'scheduled', 'cli'}:
            raise ValueError('入口不合法')
        with self._connect() as db, immediate(db):
            existing = db.execute('SELECT run_id,plan,entry FROM runs WHERE request_id=?', (request_id,)).fetchone()
            if existing:
                if existing['plan'] != encoded or existing['entry'] != entry:
                    raise ValueError('幂等标识对应的计划不可改变')
                return existing['run_id']
            if db.execute('SELECT 1 FROM resource_locks WHERE resource=?', (plan['resource'],)).fetchone():
                raise RunBusy('资源已有任务')
            run_id = uuid.uuid4().hex
            db.execute('INSERT INTO runs(run_id,request_id,entry,resource,date,plan,status,created_at,heartbeat_at,process_status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (run_id, request_id, entry, plan['resource'], plan['date'], encoded, 'queued', now, now, 'unknown'))
            db.executemany('INSERT INTO units(run_id,unit_id,plan) VALUES(?,?,?)',
                           [(run_id, u['unit_id'], _json(u)) for u in plan['units']])
            db.execute('INSERT INTO resource_locks(resource,run_id,heartbeat_at) VALUES(?,?,?)', (plan['resource'], run_id, now))
            self._event(db, run_id, 'created', {'entry': entry}, now)
            return run_id

    def reserve_launch(self, run_id, *, reservation_id, now):
        """启动前原子登记一次性意图；崩溃后不猜是否已启动、不自动再拉起。"""
        _text(reservation_id, 'reservation_id')
        _now(now)
        with self._connect() as db, immediate(db):
            row = self._run(db, run_id)
            if row['status'] != 'queued' or row['pid'] is not None:
                return False
            self._active(db, run_id)
            if db.execute("SELECT 1 FROM events WHERE run_id=? AND kind='launch_intent'", (run_id,)).fetchone():
                return False
            self._event(db, run_id, 'launch_intent', {'reservation_id': reservation_id}, now)
            return True

    def claim(self, run_id, *, pid, created_at, now):
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError('PID不合法')
        _text(created_at, 'created_at')
        _now(now)
        with self._connect() as db, immediate(db):
            row = self._active(db, run_id)
            if row['pid'] is not None and (row['pid'], row['process_created_at']) != (pid, created_at):
                raise ValueError('进程身份不匹配')
            if row['pid'] is not None:
                return
            db.execute('UPDATE runs SET pid=?,process_created_at=?,status=?,process_status=?,started_at=COALESCE(started_at,?),heartbeat_at=? WHERE run_id=?',
                       (pid, created_at, 'running', 'alive', now, now, run_id))
            db.execute('UPDATE resource_locks SET pid=?,created_at=?,heartbeat_at=? WHERE run_id=?', (pid, created_at, now, run_id))
            self._event(db, run_id, 'claimed', {'pid': pid, 'created_at': created_at}, now)

    def heartbeat(self, run_id, *, pid, created_at, now):
        _now(now)
        with self._connect() as db, immediate(db):
            row = self._active(db, run_id)
            if row['pid'] is None or (row['pid'], row['process_created_at']) != (pid, created_at):
                raise ValueError('进程身份不匹配')
            db.execute('UPDATE runs SET heartbeat_at=?,process_status=? WHERE run_id=?', (now, 'alive', run_id))
            db.execute('UPDATE resource_locks SET heartbeat_at=? WHERE run_id=?', (now, run_id))

    def record_unit(self, run_id, unit_id, result, *, now):
        _now(now)
        if not isinstance(result, dict) or set(result) != RESULT_FIELDS or result['execution_status'] not in UNIT_STATUSES:
            raise ValueError('单元结果字段或状态不合法')
        for field in COUNT_FIELDS:
            value = result[field]
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or (field != 'net_storage_bytes' and value < 0)):
                raise ValueError(f'{field}计量不合法')
        for field in ('provider', 'channel', 'source_id', 'commit_id', 'error_kind'):
            if result[field] is not None:
                _text(result[field], field)
        if not isinstance(result['receipt_ids'], list) or any(not isinstance(v, str) or not v for v in result['receipt_ids']):
            raise ValueError('来源关联不合法')
        if result['verification'] is not None and not isinstance(result['verification'], dict):
            raise ValueError('核验摘要不合法')
        encoded = _json(_redact(result))
        with self._connect() as db, immediate(db):
            owner = self._run(db, run_id)
            commits = _commits(db, run_id, 'commit_complete')
            commit = commits.get(result['commit_id']) if result['commit_id'] else None
            if result['commit_id']:
                receipts = _receipts(db, run_id)
                expected_receipts = [rid for rid in commit[1]['receipt_ids']
                                     if receipts[rid][1]['unit_id'] == unit_id] if commit else []
                if not commit or expected_receipts != result['receipt_ids']:
                    raise ValueError('单元提交关联不合法')
                if commit[1].get('unit_id') is None:
                    files = [f for f in commit[1].get('files', []) if f.get('unit_id') == unit_id]
                    if not files:
                        raise ValueError('单元不属于表级提交清单')
                    unit_counts = {field: sum(f['counts'][field] for f in files)
                                   if all(f.get('counts', {}).get(field) is not None for f in files)
                                   else None for field in COUNT_FIELDS}
                elif commit[1]['unit_id'] == unit_id:
                    unit_counts = commit[1].get('counts') or {}
                else:
                    raise ValueError('单元不属于提交')
                if any(result[field] != value for field,value in unit_counts.items()):
                    raise ValueError('单元与提交计量不符')
            if owner['status'] in TERMINAL:
                lock = db.execute('SELECT run_id FROM resource_locks WHERE resource=?', (owner['resource'],)).fetchone()
                if lock and lock['run_id'] != run_id:
                    raise RunBusy('新任务占用资源，恢复单元登记暂缓')
                recovered = _commits(db, run_id, 'commit_recovery').get(result['commit_id']) if result['commit_id'] else None
                if not commit or not (commit[1].get('recovered') or recovered) or result['verification'] != {'status': 'unknown'}:
                    raise ValueError('终态只能补登已证明的恢复事实，保留待核验')
            else:
                self._active(db, run_id)
            row = db.execute('SELECT result FROM units WHERE run_id=? AND unit_id=?', (run_id, unit_id)).fetchone()
            if row is None:
                raise ValueError('单元不属于冻结计划')
            receipts = _receipts(db, run_id)
            if any(rid not in receipts or receipts[rid][1]['unit_id'] != unit_id for rid in result['receipt_ids']):
                raise ValueError('来源回执不属于本单元')
            if row['result'] is not None:
                if row['result'] != encoded:
                    raise ValueError('单元结果不可重复修改')
                return
            db.execute('UPDATE units SET result=?,recorded_at=? WHERE run_id=? AND unit_id=?', (encoded, now, run_id, unit_id))
            self._event(db, run_id, 'unit_result', {'unit_id': unit_id, 'result': json.loads(encoded)}, now)

    def event(self, run_id, kind, payload, *, now):
        _now(now)
        if kind not in EVENT_FIELDS or not isinstance(payload, dict):
            raise ValueError('事件类型不合法')
        safe = _redact({k: v for k, v in payload.items() if k in EVENT_FIELDS[kind]})
        if kind == 'source_receipt':
            if set(safe) != EVENT_FIELDS[kind] or safe['run_id'] != run_id:
                raise ValueError('来源回执字段或run关联不合法')
            scope = safe['request_scope']
            if not isinstance(scope, dict):
                raise ValueError('来源范围不合法')
            safe['request_scope'] = {k: v for k,v in scope.items() if k in SCOPE_FIELDS}
            for field in ('receipt_id', 'unit_id', 'table', 'provider', 'channel', 'source_id', 'api_name'):
                _text(safe[field], field)
            requested = _timestamp(safe['requested_at'], 'requested_at')
            received = _timestamp(safe['received_at'], 'received_at')
            if received < requested:
                raise ValueError('响应时点早于请求时点')
            count = safe['result_rows']
            if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
                raise ValueError('来源行数不合法')
            if safe['result_kind'] not in {'authorized_data', 'authorized_empty', 'permission_denied', 'rate_limited', 'connection_error', 'parse_error', 'not_applicable', 'unknown'}:
                raise ValueError('来源结果类型不合法')
            if safe['error_kind'] is not None:
                _text(safe['error_kind'], 'error_kind')
            source_time = safe['source_time']
            if source_time is not None:
                if not isinstance(source_time, dict) or set(source_time) != {'value', 'timezone', 'kind', 'evidence'}:
                    raise ValueError('源时点结构不合法')
                if source_time['kind'] not in {'business_time', 'response_time'}:
                    raise ValueError('源时点类型不合法')
                for field in ('value', 'timezone', 'evidence'):
                    _text(source_time[field], 'source_time.' + field)
                try:
                    zone = ZoneInfo(source_time['timezone'])
                except ZoneInfoNotFoundError as exc:
                    raise ValueError('源时区不合法') from exc
                value = _timestamp(source_time['value'], 'source_time.value')
                if value.utcoffset() != value.astimezone(zone).utcoffset():
                    raise ValueError('源时点偏移与时区不一致')
        with self._connect() as db, immediate(db):
            owner = self._run(db, run_id)
            if owner['status'] in TERMINAL:
                if kind not in {'commit_complete', 'commit_recovery'} or safe.get('recovered') is not True:
                    raise ValueError('终态不接受新执行事件')
            else:
                self._active(db, run_id)
            if kind == 'source_receipt':
                unit = db.execute('SELECT plan FROM units WHERE run_id=? AND unit_id=?', (run_id, safe['unit_id'])).fetchone()
                if unit is None or json.loads(unit['plan'])['table'] != safe['table']:
                    raise ValueError('来源回执不属于冻结单元/表')
                existing = _receipts(db, run_id).get(safe['receipt_id'])
                if existing:
                    if existing[1] != safe:
                        raise ValueError('来源回执ID冲突')
                    return existing[0]
            if kind in {'commit_intent', 'commit_complete', 'commit_recovery'}:
                for field in ('commit_id', 'table'):
                    _text(safe.get(field), field)
                if not isinstance(safe.get('receipt_ids'), list):
                    raise ValueError('提交必须列明来源关联')
                receipts = _receipts(db, run_id)
                if any(rid not in receipts or receipts[rid][1]['table'] != safe['table'] for rid in safe['receipt_ids']):
                    raise ValueError('提交来源不存在或不是本表')
                _counts(safe.get('counts'))
                unit_id = safe.get('unit_id')
                units = [json.loads(row['plan']) for row in db.execute('SELECT plan FROM units WHERE run_id=?', (run_id,))]
                matching = [u for u in units if u['table'] == safe['table'] and (unit_id is None or u['unit_id'] == unit_id)]
                if not matching:
                    raise ValueError('提交不属于冻结单元/表')
                if unit_id and any(receipts[rid][1]['unit_id'] != unit_id for rid in safe['receipt_ids']):
                    raise ValueError('提交来源不属于本单元')
                if kind == 'commit_intent':
                    files = safe.get('files')
                    multiple = files is not None
                    if files is None:
                        files = [safe]
                        if unit_id is None:
                            raise ValueError('单文件提交必须关联单元')
                    if not isinstance(files, list) or not files:
                        raise ValueError('提交清单不能为空')
                    targets = set()
                    for file in files:
                        if not isinstance(file, dict):
                            raise ValueError('提交文件不合法')
                        for field in ('target', 'staged_path'):
                            _text(file.get(field), field)
                        if file['target'] in targets:
                            raise ValueError('提交目标重复')
                        targets.add(file['target'])
                        if multiple and file.get('unit_id') not in {u['unit_id'] for u in matching}:
                            raise ValueError('提交分片必须归属冻结单元')
                        if 'old_sha256' not in file:
                            raise ValueError('必须声明旧文件版本')
                        _fingerprint(file['old_sha256'], nullable=True)
                        _fingerprint(file.get('new_sha256'))
                        _counts(file.get('counts'))
                    if multiple:
                        manifest = safe.get('manifest')
                        if not isinstance(manifest, dict):
                            raise ValueError('多文件提交必须包含manifest最后发布')
                        _text(manifest.get('target'), 'manifest.target')
                        if manifest['target'] in targets or 'old_sha256' not in manifest:
                            raise ValueError('manifest目标或旧指纹不合法')
                        _fingerprint(manifest['old_sha256'], nullable=True)
                        _fingerprint(manifest.get('new_sha256'))
                else:
                    intent = _commits(db, run_id, 'commit_intent').get(safe['commit_id'])
                    if not intent or any(safe.get(key) != intent[1].get(key) for key in ('unit_id', 'table', 'receipt_ids')):
                        raise ValueError('提交完成必须关联原意图')
                    for field in ('counts', 'files', 'manifest'):
                        if intent[1].get(field) != safe.get(field):
                            raise ValueError('完成事实必须匹配原提交清单及计量')
                    if kind == 'commit_recovery':
                        prior = _commits(db, run_id, 'commit_complete').get(safe['commit_id'])
                        if not prior or safe.get('recovered') is not True or safe.get('verification') != {'status': 'unknown'}:
                            raise ValueError('恢复只能关联既有完成事实并保留待核验')
                        if any(safe.get(field) != prior[1].get(field) for field in ('unit_id', 'table', 'receipt_ids', 'counts', 'files', 'manifest')):
                            raise ValueError('恢复不能改写既有完成事实')
                    if owner['status'] in TERMINAL:
                        lock = db.execute('SELECT run_id FROM resource_locks WHERE resource=?', (owner['resource'],)).fetchone()
                        if lock and lock['run_id'] != run_id:
                            raise RunBusy('新任务占用资源，恢复登记暂缓')
                existing = _commits(db, run_id, kind).get(safe['commit_id'])
                if existing:
                    if existing[1] != safe:
                        raise ValueError('提交事实不可重复修改')
                    return existing[0]
            if kind == 'progress' and safe.get('stage') == 'verifying':
                if owner['status'] != 'running' or db.execute('SELECT 1 FROM units WHERE run_id=? AND result IS NULL LIMIT 1', (run_id,)).fetchone():
                    raise ValueError('未执行完的任务不能进入核验')
                db.execute("UPDATE runs SET status='verifying' WHERE run_id=?", (run_id,))
            return self._event(db, run_id, kind, safe, now)

    def finish(self, run_id, status, *, exit_code, now):
        _now(now)
        if status not in TERMINAL or (exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int))):
            raise ValueError('终态或退出码不合法')
        with self._connect() as db, immediate(db):
            self._active(db, run_id)
            if status in {'succeeded', 'partial'} and self._run(db, run_id)['pid'] is None:
                raise ValueError('无进程身份的任务不能登记成功/部分执行')
            if status == 'succeeded':
                if self._run(db, run_id)['pid'] is None:
                    raise ValueError('无进程身份的排队任务不能成功')
                rows = db.execute('SELECT result FROM units WHERE run_id=?', (run_id,)).fetchall()
                if not rows or any(not r['result'] or json.loads(r['result'])['execution_status'] in UNIT_FAILURES for r in rows) or exit_code != 0:
                    raise ValueError('未完成或失败单元不能计任务成功')
            db.execute('UPDATE runs SET status=?,finished_at=?,exit_code=? WHERE run_id=?', (status, now, exit_code, run_id))
            db.execute('DELETE FROM resource_locks WHERE run_id=?', (run_id,))
            self._event(db, run_id, 'finished', {'status': status, 'exit_code': exit_code}, now)

    def get_run(self, run_id, *, after_seq=0, limit=100):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError('事件limit必须为1至500')
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0:
            raise ValueError('after_seq不合法')
        with self._connect() as db:
            # 保证任务、单元与事件来自同一SQLite读快照。
            db.execute('BEGIN')
            run = dict(self._run(db, run_id))
            run['plan'] = json.loads(run['plan'])
            units = db.execute('SELECT * FROM units WHERE run_id=? ORDER BY rowid', (run_id,)).fetchall()
            run['units'] = [dict(plan=json.loads(u['plan']), result=json.loads(u['result']) if u['result'] else None, recorded_at=u['recorded_at']) for u in units]
            results = [u['result'] for u in run['units'] if u['result'] is not None]
            run['progress'] = {'completed_units': len(results), 'total_units': len(units),
                               'failed_units': sum(r['execution_status'] in UNIT_FAILURES for r in results), 'unit': '计划单元'}
            run['metrics'] = {field: sum(r[field] for r in results) if results and all(r[field] is not None for r in results) else None for field in COUNT_FIELDS}
            events = db.execute('SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?', (run_id, after_seq, limit)).fetchall()
            run['events'] = [dict(seq=e['seq'], at=e['at'], kind=e['kind'], payload=json.loads(e['payload'])) for e in events]
            run['next_seq'] = events[-1]['seq'] if events else after_seq
            run['has_more'] = bool(db.execute('SELECT 1 FROM events WHERE run_id=? AND seq>? LIMIT 1', (run_id, run['next_seq'])).fetchone())
            run['receipts'] = [e for e in run['events'] if e['kind'] == 'source_receipt']
            return run

    def list_runs(self, date, *, limit=100):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError('limit不合法')
        with self._connect() as db:
            rows = db.execute('SELECT run_id FROM runs WHERE date=? ORDER BY created_at DESC,run_id LIMIT ?', (date, limit)).fetchall()
        return [self.get_run(row['run_id']) for row in rows]

    def reconcile(self, probe_process, *, now):
        _now(now)
        with self._connect() as db:
            stale = db.execute('SELECT * FROM runs WHERE status IN (?,?,?) AND heartbeat_at<?', ('queued', 'running', 'verifying', now - 90)).fetchall()
        changed = []
        for old in stale:
            if old['pid'] is None:
                continue
            try:
                state = probe_process(old['pid'], old['process_created_at'])
            except Exception:
                state = 'unknown'
            if state not in {'alive', 'dead', 'unknown'}:
                state = 'unknown'
            with self._connect() as db, immediate(db):
                current = self._run(db, old['run_id'])
                # 探测期间若心跳/身份已更新，不应用旧死亡证据。
                if current['status'] in TERMINAL or any(current[k] != old[k] for k in ('pid', 'process_created_at', 'heartbeat_at')):
                    continue
                db.execute('UPDATE runs SET process_status=? WHERE run_id=?', (state, old['run_id']))
                if state == 'dead':
                    db.execute('UPDATE runs SET status=?,finished_at=? WHERE run_id=?', ('interrupted', now, old['run_id']))
                    db.execute('DELETE FROM resource_locks WHERE run_id=?', (old['run_id'],))
                self._event(db, old['run_id'], 'process_checked', {'process_status': state}, now)
                changed.append(old['run_id'])
        return changed
