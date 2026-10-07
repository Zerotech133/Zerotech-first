"""委员会历史 JSONL 的只读、可重试增量导入。"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import canonical_json, normalize_time, validate_event


SOURCES = (
    ("council_proposals.jsonl", "council.proposal", "shadow"),
    ("council_watch.jsonl", "council.watch", "shadow"),
    ("council_reviews.jsonl", "council.review", "shadow"),
    ("council_actions.jsonl", "council.action", "shadow"),
    ("council_sim_proposals.jsonl", "council.sim_proposal", "paper"),
)


def _occurred_at(payload):
    value = next((payload[key] for key in ("ts", "occurred_at", "d", "date")
                  if payload.get(key) not in (None, "")), None)
    if not isinstance(value, str):
        raise ValueError("记录缺少有效的事件日期或 ts")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("事件日期或 ts 非法") from exc
    if parsed.tzinfo is None:
        try:
            zone = ZoneInfo("Asia/Shanghai")
        except ZoneInfoNotFoundError:
            # 无 tzdata 的 Windows 使用现代上海固定时差；历史夏令时不猜测。
            if parsed.year < 1992:
                raise ValueError("缺少 Asia/Shanghai 历史时区数据，无法转换 1992 年前记录")
            zone = timezone(timedelta(hours=8), "Asia/Shanghai")
        parsed = parsed.replace(tzinfo=zone)
    return normalize_time(parsed)


def _event(payload, filename, kind, environment):
    if type(payload) is not dict:
        raise ValueError("JSONL 记录必须是 JSON 对象")
    content = canonical_json(payload)
    source_id = None
    for key in ("event_id", "id", "proposal_id", "review_id", "action_id", "watch_id"):
        value = payload.get(key)
        if type(value) in (str, int) and str(value).strip():
            source_id = str(value)
            break
    return validate_event({
        "schema_version": 1,
        "kind": kind,
        "environment": environment,
        "source": "legacy/" + filename,
        "source_id": source_id or hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "occurred_at": _occurred_at(payload),
        "available_at": payload.get("available_at"),
        "payload": payload,
    })


def _hash_prefix(stream, size):
    stream.seek(0)
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, 1024 * 1024))
        if not chunk:
            raise RuntimeError("来源文件在读取期间缩短，请重试导入")
        digest.update(chunk)
        remaining -= len(chunk)
    return digest


def _read_snapshot(stream, previous, filename, kind, environment, source_key):
    stat = os.fstat(stream.fileno())
    size = stat.st_size
    identity = {"device": stat.st_dev, "inode": stat.st_ino}
    offset = previous["offset"] if previous else 0
    generation = previous["generation"] if previous else 0
    changed = previous is not None and (size < offset or previous.get("file_identity") != identity)
    digest = _hash_prefix(stream, offset if not changed else 0)
    if previous is not None and not changed:
        changed = digest.hexdigest() != previous["prefix_sha256"]
    if changed:
        offset = 0
        generation += 1
        digest = _hash_prefix(stream, 0)
    events, errors = [], []
    while offset < size:
        raw = stream.readline(size - offset)
        if not raw:
            raise RuntimeError("来源文件在读取期间缩短，请重试导入")
        if not raw.endswith(b"\n"):
            break
        line_offset = offset
        offset += len(raw)
        digest.update(raw)
        try:
            text = raw.decode("utf-8-sig" if line_offset == 0 else "utf-8")
            payload = json.loads(text)
            events.append(_event(payload, filename, kind, environment))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            if isinstance(exc, UnicodeDecodeError):
                error_type, message = "invalid_utf8", "记录不是有效 UTF-8 文本"
            elif isinstance(exc, json.JSONDecodeError):
                error_type, message = "invalid_json", "记录不是有效 JSON"
            else:
                error_type, message = "invalid_record", "记录验证失败：" + str(exc)
            errors.append({"source": source_key, "offset": line_offset,
                           "generation": generation, "type": error_type, "message": message})
    prefix_hash = digest.hexdigest()
    # 拒绝读取过程中被改写的前缀；并发追加只影响下一次导入。
    if _hash_prefix(stream, offset).hexdigest() != prefix_hash:
        raise RuntimeError("来源文件在读取期间被改写，请重试导入")
    checkpoint = {"offset": offset, "generation": generation,
                  "prefix_sha256": prefix_hash, "file_identity": identity}
    return checkpoint, events, errors, size - offset


def ingest_legacy(store, reports_dir) -> dict:
    """显式导入固定五类来源；每个文件独立事务，冲突直接抛出供调用方重试。"""
    directory = Path(reports_dir).resolve()
    if not directory.is_dir():
        raise NotADirectoryError("reports_dir 必须是已存在的目录")
    result = {"inserted": 0, "duplicates": 0, "errors": 0,
              "pending_bytes": 0, "files": [], "missing": []}
    for filename, kind, environment in SOURCES:
        path = directory / filename
        source_key = os.path.normcase(str(path.resolve()))
        previous = store.get_checkpoint(source_key)
        try:
            stream = path.open("rb")
        except FileNotFoundError:
            result["missing"].append(source_key)
            continue
        with stream:
            checkpoint, events, errors, pending = _read_snapshot(
                stream, previous, filename, kind, environment, source_key)
        counts = store.commit_import(source_key, checkpoint, events, errors,
                                     expected_checkpoint=previous)
        item = {"source": "legacy/" + filename, "source_key": source_key,
                "offset": checkpoint["offset"], "generation": checkpoint["generation"],
                "pending_bytes": pending, **counts}
        result["files"].append(item)
        for key in ("inserted", "duplicates", "errors"):
            result[key] += counts[key]
        result["pending_bytes"] += pending
    return result
