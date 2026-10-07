"""只读本地日线快照；字节完整性不构成供应商或历史可用时间证明。"""

import hashlib
import io
import json
import math
import os
import re
import stat
from datetime import date

from . import contracts


MAX_FILE_BYTES = 32 * 1024 * 1024
PRICE_BASES = frozenset({"raw", "qfq_snapshot", "hfq_snapshot"})
FORMATS = frozenset({"json", "ashare_parquet"})
BAR_FIELDS = frozenset({"session", "open", "high", "low", "close", "volume"})
SNAPSHOT_FIELDS = frozenset({
    "schema_version", "kind", "code", "period", "source_id", "price_basis", "revision", "received_at",
    "source_file", "bars", "historical_availability", "supplier_verification", "production_eligible", "snapshot_id",
})
SOURCE_FIELDS = frozenset({"path", "sha256", "size_bytes", "format"})


def _fields(value, fields, name):
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{name} 的字段必须严格匹配契约")


def _metadata(source_id, code, price_basis, revision, format):
    for name, value in (("source_id", source_id), ("revision", revision)):
        if type(value) is not str or not value.strip():
            raise ValueError(f"{name} 必须是非空字符串")
    if type(code) is not str or re.fullmatch(r"[0-9]{6}\.(SH|SZ)", code) is None:
        raise ValueError("code 必须是六位证券代码加 .SH 或 .SZ")
    if type(price_basis) is not str or price_basis not in PRICE_BASES:
        raise ValueError("不支持的价格口径")
    if type(format) is not str or format not in FORMATS:
        raise ValueError("不支持的行情文件格式")


def _session(value, *, compact=False):
    if type(value) is not str:
        raise ValueError("交易日期必须是字符串")
    if compact and re.fullmatch(r"[0-9]{8}", value):
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("交易日期必须是 YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("交易日期不存在") from exc
    return value


def _number(value, name, *, positive=False):
    if type(value) not in (int, float):
        raise ValueError(f"{name} 必须是数值，不能是布尔值")
    try:
        valid = math.isfinite(value) and (value > 0 if positive else value >= 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} 必须是有限{'正' if positive else '非负'}数")


def _time(value):
    if type(value) is not str:
        raise ValueError("快照时间必须是带时区的字符串")
    return contracts.normalize_time(value)


def _bars(rows, received_at, *, captured):
    if type(rows) is not list or not 1 <= len(rows) <= 5000:
        raise ValueError("日线必须包含 1 至 5000 根")
    result = []
    seen = set()
    previous = None
    for row in rows:
        _fields(row, BAR_FIELDS if captured else BAR_FIELDS | {"close_at", "available_at"}, "日线")
        session = _session(row["session"])
        if session in seen:
            raise ValueError("交易日期重复")
        if not captured and previous is not None and session <= previous:
            raise ValueError("快照日线必须按唯一日期升序")
        previous = session
        seen.add(session)
        for name in ("open", "high", "low", "close"):
            _number(row[name], name, positive=True)
        _number(row["volume"], "volume")
        if not row["low"] <= row["open"] <= row["high"] or not row["low"] <= row["close"] <= row["high"]:
            raise ValueError("OHLC 必须满足 low <= open/close <= high")
        close_at = session + "T15:00:00+08:00"
        close_utc = contracts.normalize_time(close_at)
        if close_utc > received_at:
            raise ValueError("行情收盘时刻晚于当前接收时间")
        if not captured:
            if _time(row["close_at"]) != close_utc:
                raise ValueError("close_at 必须等于该交易日中国交易所 15:00 +08:00")
            if _time(row["available_at"]) != received_at:
                raise ValueError("每根日线的可用时间必须等于本批实际接收时间")
        result.append({name: row[name] for name in BAR_FIELDS} | {"close_at": close_at, "available_at": received_at})
    return sorted(result, key=lambda row: row["session"])


def _local_path(path):
    if type(path) is not str or not path or "\0" in path:
        raise ValueError("源文件路径必须是非空文本路径")
    normalized = path.replace("\\", "/")
    if normalized.startswith("//") or normalized.startswith("/??/"):
        raise ValueError("只允许本地普通路径，禁止 UNC 和 Windows 设备命名空间")


def _read_file(path):
    try:
        supplied = os.fspath(path)
        # 先按文本拒绝远端地址，避免路径检查本身触发网络文件访问。
        _local_path(supplied)
        absolute = os.path.abspath(supplied)
        _local_path(absolute)
        # 检查父路径，避免通过目录符号链接间接读取来源文件。
        current = absolute
        while True:
            details = os.lstat(current)
            if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("源文件路径不能包含符号链接或 Windows 重解析点")
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        absolute = os.path.realpath(absolute)
        before = os.lstat(absolute)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("行情来源必须是普通文件")
        if before.st_size > MAX_FILE_BYTES:
            raise ValueError("源文件超过 32 MiB")
        with open(absolute, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("源文件在打开前已被替换")
            raw = stream.read(MAX_FILE_BYTES + 1)
        if not raw or len(raw) > MAX_FILE_BYTES:
            raise ValueError("源文件为空或超过 32 MiB")
        return absolute, raw
    except (OSError, TypeError) as exc:
        raise ValueError("无法读取普通行情源文件") from exc


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON 包含重复键")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("JSON 不允许 NaN 或无穷数")

    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        _fields(result, {"bars"}, "行情 JSON")
        return result["bars"]
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("行情文件不是严格 UTF-8 JSON") from exc


def _parquet(raw, code):
    try:
        import pandas as pd
    except ImportError as exc:
        raise ValueError("Parquet 读取需要既有 pandas 和 Parquet 引擎") from exc
    try:
        frame = pd.read_parquet(io.BytesIO(raw))
    except Exception as exc:
        raise ValueError("无法解析行情 Parquet") from exc
    if not frame.columns.is_unique:
        raise ValueError("Parquet 字段重复")
    volume = "volume" if "volume" in frame.columns else "vol"
    required = {"ts_code", "trade_date", "open", "high", "low", "close", volume}
    if not required <= set(frame.columns):
        raise ValueError("Parquet 缺少证券、日期或 OHLCV 字段")
    selected = frame.loc[frame["ts_code"] == code, ["trade_date", "open", "high", "low", "close", volume]]
    if not 1 <= len(selected) <= 5000:
        raise ValueError("所选证券的日线必须包含 1 至 5000 根")
    return [{"session": _session(row["trade_date"], compact=True), "open": row["open"], "high": row["high"],
             "low": row["low"], "close": row["close"], "volume": row[volume]} for row in selected.to_dict("records")]


def _hash(value):
    return hashlib.sha256(contracts.canonical_json({k: v for k, v in value.items() if k != "snapshot_id"}).encode("utf-8")).hexdigest()


def capture_snapshot(path, *, source_id, code, price_basis, revision, format="json") -> dict:
    """在本进程实际读完源字节后记录接收时间，调用者不能回填时间。"""
    _metadata(source_id, code, price_basis, revision, format)
    absolute, raw = _read_file(path)
    received_at = contracts.normalize_time(contracts.utc_now())
    rows = _strict_json(raw) if format == "json" else _parquet(raw, code)
    result = {"schema_version": 1, "kind": "daily_local_capture", "code": code, "period": "1d",
              "source_id": source_id, "price_basis": price_basis, "revision": revision, "received_at": received_at,
              "source_file": {"path": absolute, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "format": format},
              "bars": _bars(rows, received_at, captured=True), "historical_availability": "unknown",
              "supplier_verification": "unverified", "production_eligible": False}
    result["snapshot_id"] = _hash(result)
    return result


def validate_snapshot(snapshot) -> dict:
    """重验完整性和日线语义，返回独立规范副本；不认证外部重造的快照。"""
    _fields(snapshot, SNAPSHOT_FIELDS, "快照")
    result = json.loads(contracts.canonical_json(snapshot))
    if type(result["snapshot_id"]) is not str or re.fullmatch(r"[0-9a-f]{64}", result["snapshot_id"]) is None or result["snapshot_id"] != _hash(result):
        raise ValueError("snapshot_id 与完整快照内容不匹配")
    if type(result["schema_version"]) is not int or result["schema_version"] != 1:
        raise ValueError("仅支持 schema_version=1")
    for name, expected in (("kind", "daily_local_capture"), ("period", "1d"), ("historical_availability", "unknown"),
                           ("supplier_verification", "unverified")):
        if result[name] != expected:
            raise ValueError(f"{name} 与快照契约不符")
    if result["production_eligible"] is not False:
        raise ValueError("来源快照不能启用生产资格")
    source = result["source_file"]
    _fields(source, SOURCE_FIELDS, "源文件元数据")
    _metadata(result["source_id"], result["code"], result["price_basis"], result["revision"], source["format"])
    _local_path(source["path"])
    if not os.path.isabs(source["path"]) or source["path"] != os.path.abspath(source["path"]):
        raise ValueError("源文件必须记录规范绝对路径")
    if type(source["sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is None:
        raise ValueError("源文件 SHA256 非法")
    if type(source["size_bytes"]) is not int or not 1 <= source["size_bytes"] <= MAX_FILE_BYTES:
        raise ValueError("源文件大小必须为 1 字节至 32 MiB")
    result["received_at"] = _time(result["received_at"])
    if result["received_at"] > contracts.normalize_time(contracts.utc_now()):
        raise ValueError("快照接收时间不能晚于验证当前时间")
    result["bars"] = _bars(result["bars"], result["received_at"], captured=False)
    result["snapshot_id"] = _hash(result)
    return result
