"""显式调用的 SQLite 记忆、导入审计和可恢复事件导出。"""

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .contracts import ENVIRONMENTS, canonical_json, normalize_time, utc_now, validate_event


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, event_json TEXT NOT NULL, kind TEXT NOT NULL, environment TEXT NOT NULL, available_at TEXT, recorded_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS checkpoints (source_key TEXT PRIMARY KEY, checkpoint_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS import_errors (source_key TEXT NOT NULL, error_hash TEXT NOT NULL, error_json TEXT NOT NULL, recorded_at TEXT NOT NULL, PRIMARY KEY (source_key, error_hash))",
    "CREATE TABLE IF NOT EXISTS lessons (lesson_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id), text TEXT NOT NULL, tags_json TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('draft','reviewed','rejected')), available_at TEXT, expires_at TEXT)",
    "CREATE TABLE IF NOT EXISTS outbox (event_id TEXT PRIMARY KEY REFERENCES events(event_id), exported_at TEXT)",
    "CREATE INDEX IF NOT EXISTS event_recall ON events(environment, available_at)",
    "CREATE INDEX IF NOT EXISTS lesson_recall ON lessons(status, available_at, event_id)",
)


def _hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _nonempty(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value


def _object(value, name):
    if type(value) is not dict:
        raise ValueError(f"{name} 必须是 JSON 对象")
    return canonical_json(value)


def _tags(tags):
    if type(tags) is not list or any(type(tag) is not str or not tag.strip() for tag in tags):
        raise ValueError("tags 必须是非空字符串组成的列表")
    return sorted(set(tags))


class MemoryStore:
    def __init__(self, connection, readonly=False):
        self._conn = connection
        self.readonly = readonly
        self._closed = False

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        if not self._closed:
            self._conn.close()
            self._closed = True

    def _ensure_open(self):
        if self._closed:
            raise ValueError("记忆库已关闭")

    def _ensure_writable(self):
        self._ensure_open()
        if self.readonly:
            raise ValueError("只读记忆库不允许写入")

    @contextmanager
    def _transaction(self, write=True):
        if write:
            self._ensure_writable()
        else:
            self._ensure_open()
        self._conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    def _insert_event(self, event):
        validated = validate_event(event)
        event_id = _hash(validated)
        payload_hash = _hash(validated["payload"])
        recorded_at = utc_now()
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, payload_hash, canonical_json(validated), validated["kind"], validated["environment"], validated["available_at"], recorded_at),
        )
        inserted = cursor.rowcount == 1
        if inserted:
            self._conn.execute("INSERT INTO outbox(event_id) VALUES (?)", (event_id,))
        return event_id, inserted

    def append_event(self, event: dict) -> str:
        with self._transaction():
            event_id, _ = self._insert_event(event)
        return event_id

    @staticmethod
    def _event_from_row(row):
        value = json.loads(row["event_json"])
        value.update(event_id=row["event_id"], payload_hash=row["payload_hash"], recorded_at=row["recorded_at"])
        return value

    def get_event(self, event_id: str):
        self._ensure_open()
        row = self._conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        return self._event_from_row(row) if row else None

    def get_checkpoint(self, source_key: str):
        self._ensure_open()
        _nonempty(source_key, "source_key")
        row = self._conn.execute("SELECT checkpoint_json FROM checkpoints WHERE source_key=?", (source_key,)).fetchone()
        return json.loads(row[0]) if row else None

    def commit_import(self, source_key: str, checkpoint: dict, events: list, errors: list, expected_checkpoint=None) -> dict:
        _nonempty(source_key, "source_key")
        checkpoint_json = _object(checkpoint, "checkpoint")
        expected_json = _object(expected_checkpoint, "expected_checkpoint") if expected_checkpoint is not None else None
        if type(events) is not list or type(errors) is not list:
            raise ValueError("events 与 errors 必须是列表")
        result = {"inserted": 0, "duplicates": 0, "errors": 0}
        with self._transaction():
            current = self._conn.execute("SELECT checkpoint_json FROM checkpoints WHERE source_key=?", (source_key,)).fetchone()
            if (current[0] if current else None) != expected_json:
                raise ValueError("导入进度冲突，请重新读取 checkpoint 后重试")
            for event in events:
                _, inserted = self._insert_event(event)
                result["inserted" if inserted else "duplicates"] += 1
            for error in errors:
                error_json = _object(error, "error")
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO import_errors VALUES (?, ?, ?, ?)",
                    (source_key, _hash(error), error_json, utc_now()),
                )
                result["errors"] += cursor.rowcount
            self._conn.execute(
                "INSERT INTO checkpoints VALUES (?, ?) ON CONFLICT(source_key) DO UPDATE SET checkpoint_json=excluded.checkpoint_json",
                (source_key, checkpoint_json),
            )
        return result

    def put_lesson(self, event_id: str, text: str, tags: list, status="draft", available_at=None, expires_at=None) -> str:
        _nonempty(text, "text")
        tags = _tags(tags)
        if status not in ("draft", "reviewed", "rejected"):
            raise ValueError("经验状态必须是 draft、reviewed 或 rejected")
        available = normalize_time(available_at) if available_at is not None else None
        expires = normalize_time(expires_at) if expires_at is not None else None
        with self._transaction():
            event = self.get_event(event_id)
            if event is None:
                raise ValueError("经验的来源事件不存在")
            event_available = event["available_at"]
            lesson_id = _hash({"event_id": event_id, "text": text, "tags": tags})
            existing = self._conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (lesson_id,)).fetchone()
            if available is None:
                if existing is not None:
                    available = existing["available_at"]
                # 默认不能把今天新增或审核的知识回填到历史决策时点。
                if existing is None or (status == "reviewed" and existing["status"] != "reviewed"):
                    available = max(value for value in (available, event_available, utc_now()) if value is not None)
            if expires is None and existing is not None:
                expires = existing["expires_at"]
            if event_available is not None and available < event_available:
                raise ValueError("经验可用时间不能早于来源事件可用时间")
            if available is not None and expires is not None and expires <= available:
                raise ValueError("经验过期时间必须晚于可用时间")
            self._conn.execute(
                "INSERT INTO lessons VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(lesson_id) DO UPDATE SET status=excluded.status, available_at=excluded.available_at, expires_at=excluded.expires_at",
                (lesson_id, event_id, text, canonical_json(tags), status, available, expires),
            )
        return lesson_id

    @staticmethod
    def _available_sql():
        return "l.status='reviewed' AND e.available_at IS NOT NULL AND l.available_at IS NOT NULL AND e.available_at<=? AND l.available_at<=? AND (l.expires_at IS NULL OR l.expires_at>?)"

    def recall(self, query: dict, as_of: str, limit=5) -> list:
        self._ensure_open()
        _object(query, "query")
        if query.get("environment") not in ENVIRONMENTS:
            raise ValueError("recall 必须指定有效 environment")
        if set(query) - {"environment", "code", "strategy_id", "tags"}:
            raise ValueError("recall 包含不支持的过滤字段")
        tags = _tags(query.get("tags", []))
        for key in ("code", "strategy_id"):
            if key in query:
                _nonempty(query[key], key)
        if type(limit) is not int or limit < 0:
            raise ValueError("limit 必须是非负整数")
        instant = normalize_time(as_of)
        if not limit:
            return []
        rows = self._conn.execute(
            "SELECT l.*, e.event_json FROM lessons l JOIN events e ON e.event_id=l.event_id WHERE "  # nosec B608 - 值参数化/内部标识符（已抽查）
            + self._available_sql() + " AND e.environment=? ORDER BY l.available_at DESC, l.lesson_id ASC",
            (instant, instant, instant, query["environment"]),
        )
        result = []
        for row in rows:
            payload = json.loads(row["event_json"])["payload"]
            lesson_tags = json.loads(row["tags_json"])
            strategy_id = payload.get("strategy_id", payload.get("sid"))
            if "code" in query and payload.get("code") != query["code"]:
                continue
            if "strategy_id" in query and strategy_id != query["strategy_id"]:
                continue
            if not set(tags).issubset(lesson_tags):
                continue
            result.append({
                "lesson_id": row["lesson_id"], "event_id": row["event_id"],
                "text": row["text"], "tags": lesson_tags, "status": row["status"],
                "environment": query["environment"], "code": payload.get("code"),
                "strategy_id": strategy_id, "available_at": row["available_at"], "expires_at": row["expires_at"],
            })
            if len(result) == limit:
                break
        return result

    def snapshot(self, as_of=None) -> dict:
        instant = normalize_time(as_of) if as_of is not None else utc_now()
        with self._transaction(write=False):
            def count(table):
                return self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # nosec B608 - 值参数化/内部标识符（已抽查）

            result = {
                "events": count("events"),
                "events_by_kind": dict(self._conn.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind ORDER BY kind")),
                "events_by_environment": dict(self._conn.execute("SELECT environment, COUNT(*) FROM events GROUP BY environment ORDER BY environment")),
                "lessons": count("lessons"),
                "recallable_lessons": self._conn.execute(
                    "SELECT COUNT(*) FROM lessons l JOIN events e ON e.event_id=l.event_id WHERE " + self._available_sql(),  # nosec B608 - 值参数化/内部标识符（已抽查）
                    (instant, instant, instant),
                ).fetchone()[0],
                "import_errors": count("import_errors"),
                "pending_events": self._conn.execute("SELECT COUNT(*) FROM outbox WHERE exported_at IS NULL").fetchone()[0],
            }
        return result

    def export_pending(self, output_dir: str) -> dict:
        self._ensure_writable()
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "events.jsonl"
        temporary = None
        try:
            # 导出器与写入者共享写锁；文件替换成功后才确认本次快照。
            with self._transaction():
                pending = self._conn.execute("SELECT COUNT(*) FROM outbox WHERE exported_at IS NULL").fetchone()[0]
                count = 0
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=directory, prefix=".events-", suffix=".tmp", delete=False) as output:
                    temporary = Path(output.name)
                    for row in self._conn.execute("SELECT * FROM events ORDER BY event_id"):
                        output.write(canonical_json(self._event_from_row(row)) + "\n")
                        count += 1
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination)
                temporary = None
                self._conn.execute("UPDATE outbox SET exported_at=? WHERE exported_at IS NULL", (utc_now(),))
            return {"exported": count, "pending": 0, "path": str(destination.resolve()), "acknowledged": pending}
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _validate_database(connection):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1):
        raise ValueError("不支持此记忆库版本")
    query = "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    actual = [tuple(row) for row in connection.execute(query)]
    if version == 0:
        if actual:
            raise ValueError("未初始化的数据库包含已有对象，拒绝修改外部数据库")
    else:
        # 由 SQLite 自身规范化冻结的 DDL，同时核对约束和隐式索引。
        reference = sqlite3.connect(":memory:")
        try:
            for statement in _SCHEMA:
                reference.execute(statement)
            expected = reference.execute(query).fetchall()
        finally:
            reference.close()
        if actual != expected:
            raise ValueError("数据库结构不是受支持的完整记忆库，拒绝修改")
    return version


def open_store(path, readonly=False) -> MemoryStore:
    if type(readonly) is not bool:
        raise ValueError("readonly 必须是布尔值")
    database = Path(path).resolve()
    connection = None
    try:
        if readonly:
            connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=10)
            connection.execute("PRAGMA query_only=ON")
        else:
            # 可写连接关闭时可能自动检查点；外部库必须在只读预检中拒绝。
            if database.exists():
                preview = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=10)
                try:
                    _validate_database(preview)
                finally:
                    preview.close()
            connection = sqlite3.connect(database, isolation_level=None, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        store = MemoryStore(connection, readonly=readonly)
        version = _validate_database(connection)
        if readonly:
            if version != 1:
                raise ValueError("记忆库尚未初始化")
        else:
            connection.execute("PRAGMA journal_mode=WAL")
            with store._transaction():
                if _validate_database(connection) == 0:
                    for statement in _SCHEMA:
                        connection.execute(statement)
                    connection.execute("PRAGMA user_version=1")
        return store
    except BaseException:
        if connection is not None:
            connection.close()
        raise
