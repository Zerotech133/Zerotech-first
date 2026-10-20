"""WO-QT-20261020-020：回执防重放跨进程持久记账（离线契约）。

仅本地文件、单机范围：以追加式 JSONL 账本 + 独占 OS 文件锁实现
"同一 message_id 全机只允许成功记账一次"。损坏即拒绝（fail-closed），
恢复须经显式 repair 并先落备份。不发送、不代签、不接生产简报链；
接入真实 R1 接收前须另行授权，且真实多进程并发验证属 R1 范围。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import time

try:  # Windows
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None
try:  # POSIX
    import fcntl
except ImportError:
    fcntl = None


_MSGID = re.compile(r"[!-~]{1,128}\Z")  # 可打印 ASCII，不含空白与控制字符
_TS = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_RECORD_FIELDS = frozenset(("message_id", "claimed_at"))

_UNAVAILABLE = "UNAVAILABLE"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("账本记录含重复 JSON 字段")
        obj[key] = value
    return obj


def _reject_constant(_value):
    raise ValueError("账本记录含非法 JSON 数值")


def _valid_record(record) -> bool:
    return (type(record) is dict
            and set(record) == _RECORD_FIELDS
            and type(record["message_id"]) is str
            and type(record["claimed_at"]) is str
            and _MSGID.fullmatch(record["message_id"]) is not None
            and _TS.fullmatch(record["claimed_at"]) is not None)


def _parse(data: bytes) -> list[dict]:
    """严格解析账本字节；任何坏行、空白行或非法编码都抛 ValueError。"""
    if not data:
        return []
    text = data.decode("utf-8")  # UnicodeDecodeError 是 ValueError 子类
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    records = []
    for line in lines:
        if not line.strip():
            raise ValueError("账本含空白行")
        record = json.loads(line, object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
        if not _valid_record(record):
            raise ValueError("账本记录字段无效")
        records.append(record)
    return records


_LOCK_ATTEMPTS = 50        # 有界重试：瞬态锁冲突按可重试处理
_LOCK_RETRY_SECONDS = 0.05  # 最长约 2.5 秒，超限才判 UNAVAILABLE


def _lock(fh) -> None:
    """非阻塞抢锁一次；同进程线程竞争会立即失败，由 _acquire_lock 重试。"""
    if msvcrt is not None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    elif fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        raise RuntimeError("当前平台无可用文件锁实现")


def _acquire_lock(fh) -> None:
    last_error: OSError | None = None
    for _ in range(_LOCK_ATTEMPTS):
        try:
            _lock(fh)
            return
        except OSError as exc:  # 瞬态锁冲突（EACCES/EDEADLK 等）等待后重试
            last_error = exc
            time.sleep(_LOCK_RETRY_SECONDS)
    raise last_error  # type: ignore[misc]


def _unlock(fh) -> None:
    if msvcrt is not None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    elif fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class ReplayLedger:
    """持久 message_id 记账器；实例无内存缓存，每次 claim 都过文件锁。"""

    def __init__(self, path: str | os.PathLike):
        self._path = Path(path)
        if str(self._path) in ("", "."):
            raise ValueError("账本路径无效")

    @property
    def path(self) -> Path:
        return self._path

    def claim(self, message_id: str) -> dict:
        """原子记账：首个 CLAIMED 生效，重复为 DUPLICATE，异常一律 UNAVAILABLE。

        fail-closed：账本损坏或锁失败时不追加任何内容、不返回 CLAIMED。
        """
        if type(message_id) is not str or _MSGID.fullmatch(message_id) is None:
            return {"status": _UNAVAILABLE,
                    "reason": "message_id 须为 1-128 个可打印 ASCII"}
        last_error: Exception | None = None
        try:
            # open 也在重试环内：同进程并发下 open 本身可能抛共享冲突
            for _ in range(_LOCK_ATTEMPTS):
                try:
                    return self._claim_once(message_id)
                except OSError as exc:  # open 共享冲突/锁竞争均视为瞬态
                    last_error = exc
                    time.sleep(_LOCK_RETRY_SECONDS)
            return {"status": _UNAVAILABLE,
                    "reason": f"账本锁重试超限: {last_error}"}
        except (ValueError, RuntimeError) as exc:  # 账本损坏立即 fail-closed
            return {"status": _UNAVAILABLE,
                    "reason": f"{type(exc).__name__}: {exc}"}

    def _claim_once(self, message_id: str) -> dict:
        with open(self._path, "a+b") as fh:
            _acquire_lock(fh)
            try:
                fh.seek(0)
                records = _parse(fh.read())
                for record in records:
                    if record["message_id"] == message_id:
                        return {"status": "DUPLICATE",
                                "message_id": message_id}
                claimed_at = _utcnow()
                payload = json.dumps(
                    {"message_id": message_id, "claimed_at": claimed_at},
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8") + b"\n"
                fh.seek(0, os.SEEK_END)
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
                return {"status": "CLAIMED", "message_id": message_id,
                        "claimed_at": claimed_at}
            finally:
                _unlock(fh)


def inspect_ledger(path: str | os.PathLike) -> dict:
    """只读检查账本健康度；不做任何修改，读失败也返回结构化结果而非抛错。"""
    target = Path(path)
    data = b""
    try:
        if not target.exists():
            return {"status": "MISSING", "records": 0,
                    "first_corrupt_line": None}
        data = target.read_bytes()
        _parse(data)
        records = 0 if not data else len(
            data.decode("utf-8").rstrip("\n").split("\n"))
        return {"status": "OK", "records": records,
                "first_corrupt_line": None}
    except (OSError, ValueError) as exc:
        corrupt_line = None
        try:
            lines = data.decode("utf-8").split("\n")
            if lines and lines[-1] == "":
                lines.pop()
            for index, line in enumerate(lines, start=1):
                try:
                    if line.strip() and _valid_record(json.loads(
                            line, object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)):
                        continue
                except ValueError:
                    pass
                corrupt_line = index
                break
        except (OSError, ValueError, UnicodeError):
            corrupt_line = None
        return {"status": "CORRUPT", "records": None,
                "first_corrupt_line": corrupt_line,
                "reason": f"{type(exc).__name__}: {exc}"}


def repair_ledger(path: str | os.PathLike, *, confirm: bool = False) -> dict:
    """显式恢复：持锁读全量 → 先落唯一名备份 → 按字节偏移截掉坏行及之后内容。

    未给 confirm=True 一律拒绝；无坏行时不动文件。
    """
    target = Path(path)
    if not confirm:
        return {"status": "REFUSED", "reason": "恢复须显式 confirm=True"}
    try:
        with open(target, "r+b") as fh:
            _acquire_lock(fh)  # 与并发 claim 互斥，防截断丢记录
            try:
                data = fh.read()
                good_end = 0
                corrupt = False
                offset = 0
                for raw_line in data.split(b"\n"):
                    line_len = len(raw_line) + 1
                    if raw_line == b"" and offset + 1 >= len(data):
                        break  # 末尾换行
                    try:
                        record = json.loads(
                            raw_line.decode("utf-8"),
                            object_pairs_hook=_unique_object,
                            parse_constant=_reject_constant)
                        if _valid_record(record):
                            good_end = offset + line_len
                            offset += line_len
                            continue
                    except (ValueError, UnicodeError):
                        pass
                    corrupt = True
                    break
                if not corrupt:
                    return {"status": "NOTHING_TO_REPAIR"}
                stamp = datetime.now(timezone.utc).strftime(
                    "%Y%m%dT%H%M%S%fZ")  # 含微秒，同秒多次恢复不互相覆盖
                backup = target.with_name(target.name + f".bak-{stamp}")
                with open(backup, "wb") as bf:
                    bf.write(data)
                    bf.flush()
                    os.fsync(bf.fileno())
                fh.truncate(good_end)
                fh.flush()
                os.fsync(fh.fileno())
                return {"status": "REPAIRED", "backup": str(backup),
                        "kept_bytes": good_end}
            finally:
                _unlock(fh)
    except (OSError, ValueError) as exc:
        return {"status": _UNAVAILABLE,
                "reason": f"{type(exc).__name__}: {exc}"}
