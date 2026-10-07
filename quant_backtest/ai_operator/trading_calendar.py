"""从当前本地读取冻结交易日历；完整性哈希不认证供应商或历史可用性。"""

from datetime import date, datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat

from . import contracts


MAX_FILE_BYTES = 32 * 1024 * 1024
_FORMATS = frozenset({"json", "tushare_parquet"})
_EXCHANGES = frozenset({"SSE", "SZSE"})
_SOURCE_FIELDS = {"path", "sha256", "size_bytes", "format"}
_ROW_FIELDS = {"exchange", "cal_date", "is_open"}
_CAPTURE_FIELDS = {"schema_version", "kind", "exchange", "source_id", "revision",
                   "coverage_start", "coverage_end", "received_at", "source_files",
                   "days", "calendar", "supplier_verification", "production_eligible", "capture_id"}
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _fields(value, expected, name):
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{name} 必须是包含固定字段的 JSON 对象")


def _text(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value


def _date(value, *, compact=False):
    if type(value) is not str:
        raise ValueError("日期必须是字符串")
    if compact and re.fullmatch(r"[0-9]{8}", value):
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("日期必须是 YYYY-MM-DD 或约定的 YYYYMMDD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("日历日期无效") from exc


def _coverage(start, end):
    first, last = _date(start), _date(end)
    count = (last - first).days + 1
    if not 1 <= count <= 5000:
        raise ValueError("覆盖区间必须为 1 至 5000 个自然日")
    return first, last, [first + timedelta(days=index) for index in range(count)]


def _open_flag(value):
    if type(value) is not int or value not in (0, 1):
        raise ValueError("is_open 必须是整数 0 或 1，不能是布尔值")
    return value


def _sha(value):
    return hashlib.sha256(contracts.canonical_json(value).encode("utf-8")).hexdigest()


def _digest(value, name):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{name} 必须是小写 SHA256")


def _version(exchange, start, end, days):
    return _sha({"exchange": exchange, "coverage_start": start, "coverage_end": end, "days": days})


def _sessions(days):
    return [{"session": item["session"], "close_at": item["session"] + "T15:00:00+08:00"}
            for item in days if item["is_open"] == 1]


def _reject_links(path):
    for part in [path, *path.parents]:
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError("日历源文件路径不能包含符号链接或重解析点")


def _local_path(value):
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ValueError("源路径必须是非空本地文件路径")
    path = Path(value)
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("源文件必须在本地，不能使用网络共享路径")
    return Path(os.path.abspath(path))


def _read_file(path):
    try:
        _reject_links(path)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("日历源路径必须是普通文件")
        if before.st_size > MAX_FILE_BYTES:
            raise ValueError("日历源文件超过 32 MiB")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            _reject_links(path)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("日历源文件在打开时被替换或不是普通文件")
            if opened.st_size > MAX_FILE_BYTES:
                raise ValueError("日历源文件超过 32 MiB")
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("日历源文件实际读取超过 32 MiB")
        return data
    except (OSError, TypeError, RuntimeError) as exc:
        raise ValueError("无法安全读取本地日历普通文件") from exc


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 含有重复键")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"JSON 不允许非有限常量 {value}")


def _rows(data, format):
    if format == "json":
        try:
            value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs,
                               parse_constant=_invalid_constant)
            contracts.canonical_json(value)
        except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as exc:
            raise ValueError("日历源文件不是严格 UTF-8 JSON") from exc
        _fields(value, {"rows"}, "日历源")
        if type(value["rows"]) is not list:
            raise ValueError("rows 必须是 JSON 数组")
        for row in value["rows"]:
            _fields(row, _ROW_FIELDS, "日历行")
        return value["rows"]
    try:
        import pandas as pd
        frame = pd.read_parquet(io.BytesIO(data))
        if not frame.columns.is_unique or not _ROW_FIELDS.issubset(frame.columns):
            raise ValueError("Parquet 必须包含唯一 exchange/cal_date/is_open 字段")
        return frame[["exchange", "cal_date", "is_open"]].to_dict(orient="records")
    except ImportError as exc:
        raise ValueError("读取 Parquet 需要本地已有 pandas 和 Parquet 引擎") from exc
    except Exception as exc:
        raise ValueError("无法解析日历 Parquet 或其字段不符合契约") from exc


def capture_calendar(paths, *, exchange, source_id, revision, coverage_start, coverage_end, format="json") -> dict:
    """冻结本进程当前读取的完整自然日历，拒绝调用者指定接收时点。"""
    if type(exchange) is not str or exchange not in _EXCHANGES:
        raise ValueError("exchange 必须显式为 SSE 或 SZSE")
    _text(source_id, "source_id")
    _text(revision, "revision")
    if type(format) is not str or format not in _FORMATS:
        raise ValueError("format 仅支持 json 或 tushare_parquet")
    first, last, expected_days = _coverage(coverage_start, coverage_end)
    if type(paths) not in (list, tuple) or not 1 <= len(paths) <= 32:
        raise ValueError("paths 必须包含 1 至 32 个唯一普通文件")
    normalized = []
    try:
        for value in paths:
            path = _local_path(value)
            _reject_links(path)
            normalized.append(path.resolve(strict=True))
    except (OSError, TypeError, RuntimeError) as exc:
        raise ValueError("无法解析本地日历普通文件路径") from exc
    if len({os.path.normcase(str(path)) for path in normalized}) != len(normalized):
        raise ValueError("日历源路径不能重复")
    source_files, selected = [], {}
    for path in sorted(normalized, key=str):
        data = _read_file(path)
        source_files.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                             "size_bytes": len(data), "format": format})
        for row in _rows(data, format):
            if type(row["exchange"]) is not str or row["exchange"] != exchange:
                continue
            day = _date(row["cal_date"], compact=True)
            if not first <= day <= last:
                continue
            is_open = _open_flag(row["is_open"])
            if day in selected and selected[day] != is_open:
                raise ValueError(f"日历存在冲突重复日期: {day.isoformat()}")
            selected[day] = is_open
    received_at = contracts.utc_now()
    if set(selected) != set(expected_days):
        raise ValueError("日历源未完整覆盖区间的每个自然日")
    days = [{"session": day.isoformat(), "is_open": selected[day]} for day in expected_days]
    capture = {"schema_version": 1, "kind": "exchange_calendar_capture", "exchange": exchange,
               "source_id": source_id, "revision": revision, "coverage_start": coverage_start,
               "coverage_end": coverage_end, "received_at": received_at, "source_files": source_files,
               "days": days, "calendar": {"version": _version(exchange, coverage_start, coverage_end, days),
                                           "sessions": _sessions(days)},
               "supplier_verification": "unverified", "production_eligible": False}
    capture["capture_id"] = _sha(capture)
    return capture


def validate_calendar_capture(capture) -> dict:
    """重验离线快照契约与完整性，返回深拷贝，不重新访问源路径。"""
    serialized = contracts.canonical_json(capture)
    _fields(capture, _CAPTURE_FIELDS, "日历快照")
    if type(capture["schema_version"]) is not int or capture["schema_version"] != 1:
        raise ValueError("仅支持 schema_version=1")
    if capture["kind"] != "exchange_calendar_capture":
        raise ValueError("快照类型必须是 exchange_calendar_capture")
    exchange = capture["exchange"]
    if type(exchange) is not str or exchange not in _EXCHANGES:
        raise ValueError("exchange 必须是 SSE 或 SZSE")
    for name in ("source_id", "revision"):
        _text(capture[name], name)
    _, _, expected_days = _coverage(capture["coverage_start"], capture["coverage_end"])
    received = capture["received_at"]
    if type(received) is not str or contracts.normalize_time(received) != received:
        raise ValueError("received_at 必须是含微秒的规范 UTC 时间字符串")
    if datetime.fromisoformat(received) > datetime.fromisoformat(contracts.utc_now()):
        raise ValueError("received_at 不能晚于当前校验时点")
    if capture["supplier_verification"] != "unverified" or capture["production_eligible"] is not False:
        raise ValueError("日历快照未经供应商核验且不得用于生产放行")
    sources = capture["source_files"]
    if type(sources) is not list or not 1 <= len(sources) <= 32:
        raise ValueError("source_files 必须有 1 至 32 项")
    paths, formats = [], set()
    for item in sources:
        _fields(item, _SOURCE_FIELDS, "日历来源文件")
        path = item["path"]
        if type(path) is not str or not Path(path).is_absolute() or str(_local_path(path)) != path:
            raise ValueError("来源文件路径必须是规范本地绝对路径")
        _digest(item["sha256"], "源文件 sha256")
        if type(item["size_bytes"]) is not int or not 1 <= item["size_bytes"] <= MAX_FILE_BYTES:
            raise ValueError("源文件大小必须为 1 至 32 MiB 范围内的整数")
        if type(item["format"]) is not str or item["format"] not in _FORMATS:
            raise ValueError("源文件格式无效")
        paths.append(path)
        formats.add(item["format"])
    if paths != sorted(paths) or len({os.path.normcase(path) for path in paths}) != len(paths) or len(formats) != 1:
        raise ValueError("来源文件必须按路径排序、路径唯一且格式一致")
    days = capture["days"]
    if type(days) is not list or len(days) != len(expected_days):
        raise ValueError("days 必须完整覆盖每个自然日")
    for item, day in zip(days, expected_days):
        _fields(item, {"session", "is_open"}, "日历日期")
        if type(item["session"]) is not str or item["session"] != day.isoformat():
            raise ValueError("days 日期必须完整、唯一且升序")
        _open_flag(item["is_open"])
    calendar = capture["calendar"]
    _fields(calendar, {"version", "sessions"}, "开市日历")
    if type(calendar["sessions"]) is not list:
        raise ValueError("sessions 必须是 JSON 数组")
    for item in calendar["sessions"]:
        _fields(item, {"session", "close_at"}, "开市日期")
    if calendar["sessions"] != _sessions(days):
        raise ValueError("开市日历必须与 days 一一对应，收盘为本地 15:00 +08:00")
    _digest(calendar["version"], "日历版本")
    if calendar["version"] != _version(exchange, capture["coverage_start"], capture["coverage_end"], days):
        raise ValueError("日历版本语义哈希不匹配")
    _digest(capture["capture_id"], "capture_id")
    if capture["capture_id"] != _sha({key: value for key, value in capture.items() if key != "capture_id"}):
        raise ValueError("日历快照完整性哈希不匹配")
    return json.loads(serialized)
