"""只读核查离线发布包；本地完整性证据不构成生产放行。"""

import csv
import hashlib
import io
import json
import lzma
import math
import os
from pathlib import Path
import re
import stat
import unicodedata
import zipfile
import zlib


MAX_FILES = 2000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 10000
CHUNK_BYTES = 64 * 1024
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_COURSE_COLUMNS = {"course_id", "course_code", "lesson_id", "课号", "课程编号"}
_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_RESERVED.update(f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10))


class _InspectionError(ValueError):
    def __init__(self, code, message, path=None):
        super().__init__(message)
        self.code = code
        self.path = path


class _ArchiveView:
    """连 ZIP 中央目录解析的实际读取也限制在容器上限内。"""

    def __init__(self, stream):
        self.stream = stream

    def tell(self):
        return self.stream.tell()

    def seekable(self):
        return True

    def seek(self, offset, whence=os.SEEK_SET):
        position = self.stream.seek(offset, whence)
        if position > MAX_ARCHIVE_BYTES:
            raise _InspectionError("limit_exceeded", "实际 ZIP 容器超过 128 MiB")
        return position

    def read(self, size=-1):
        remaining = MAX_ARCHIVE_BYTES - self.tell()
        if remaining < 0:
            raise _InspectionError("limit_exceeded", "ZIP 读取位置超出容器限额")
        chunk = self.stream.read(min(size, remaining + 1) if size >= 0 else remaining + 1)
        if len(chunk) > remaining:
            raise _InspectionError("limit_exceeded", "实际 ZIP 读取超过容器限额")
        return chunk


def _issue(code, message, path=None):
    value = {"code": code, "message": message}
    if path is not None:
        value["path"] = str(path)
    return value


def _safe_name(name, is_directory=False):
    if not isinstance(name, str) or not name or "\\" in name:
        raise _InspectionError("unsafe_path", "路径为空或包含反斜线", name)
    trimmed = name[:-1] if is_directory and name.endswith("/") else name
    parts = trimmed.split("/")
    for part in parts:
        if (not part or part in (".", "..") or part.endswith((" ", "."))
                or any(ord(char) < 32 or char in ':<>"|?*' for char in part)
                or part.split(".")[0].upper() in _RESERVED):
            raise _InspectionError("unsafe_path", "拒绝绝对路径、穿越、盘符或不安全路径分量", name)
    return trimmed


def _path_key(name):
    return unicodedata.normalize("NFC", name).casefold()


def _reject_link(info, path):
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
        raise _InspectionError("unsafe_type", "拒绝符号链接或重解析点", path)


def _check_ancestors(path):
    for part in [path, *path.parents]:
        _reject_link(part.lstat(), part)


def _register(entries, seen, name, is_directory, size, value):
    name = _safe_name(name, is_directory)
    key = _path_key(name)
    if key in seen:
        raise _InspectionError("duplicate_path", "发现重复或大小写等价路径", name)
    for other, other_is_directory in seen.items():
        if ((key.startswith(other + "/") and not other_is_directory)
                or (other.startswith(key + "/") and not is_directory)):
            raise _InspectionError("duplicate_path", "文件与目录路径冲突", name)
    seen[key] = is_directory
    if len(seen) > MAX_ENTRIES:
        raise _InspectionError("limit_exceeded", "条目数量超过安全上限", name)
    if not is_directory:
        if size < 0 or size > MAX_FILE_BYTES:
            raise _InspectionError("limit_exceeded", "单文件超过 8 MiB", name)
        entries.append((name, size, value))
        if len(entries) > MAX_FILES or sum(item[1] for item in entries) > MAX_TOTAL_BYTES:
            raise _InspectionError("limit_exceeded", "文件超过 2000 个或总量超过 64 MiB", name)


def _zip_entries(archive):
    entries, seen = [], {}
    for info in archive.infolist():
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        is_directory = info.is_dir()
        if (info.orig_filename != info.filename
                or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                or info.external_attr & _REPARSE
                or (kind == stat.S_IFDIR and not is_directory)
                or (kind == stat.S_IFREG and is_directory)):
            raise _InspectionError("unsafe_type", "ZIP 含链接、特殊类型或歧义名称", info.orig_filename)
        if info.flag_bits & 1:
            raise _InspectionError("unsafe_type", "拒绝加密 ZIP 条目", info.filename)
        if is_directory and info.file_size:
            raise _InspectionError("unsafe_type", "目录条目包含文件内容", info.filename)
        _register(entries, seen, info.filename, is_directory, info.file_size, info)
    return entries


def _directory_entries(root):
    entries, seen, pending = [], {}, [root]
    while pending:
        directory = pending.pop()
        _check_ancestors(directory)
        with os.scandir(directory) as scan:
            for item in scan:
                path = Path(item.path)
                info = path.lstat()
                _reject_link(info, path)
                is_directory = stat.S_ISDIR(info.st_mode)
                if not is_directory and not stat.S_ISREG(info.st_mode):
                    raise _InspectionError("unsafe_type", "目录含特殊文件", path)
                name = path.relative_to(root).as_posix()
                _register(entries, seen, name, is_directory, info.st_size,
                          (path, _stamp(info)))
                if is_directory:
                    pending.append(path)
    return entries


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_regular(path):
    _check_ancestors(path)
    before = path.stat(follow_symlinks=False)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        _reject_link(opened, path)
        if not stat.S_ISREG(opened.st_mode) or _stamp(before) != _stamp(opened):
            raise _InspectionError("source_changed", "文件类型或身份在读取前发生变化", path)
        _check_ancestors(path)
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    return stream, opened


def _read_file(stream, name, size, remaining):
    digest, count, chunks = hashlib.sha256(), 0, []
    keep = name in ("manifest.json", "rules/rules.json") or (
        name.startswith("data/") and name.lower().endswith(".csv"))
    while True:
        allowance = min(MAX_FILE_BYTES - count, remaining - count)
        chunk = stream.read(min(CHUNK_BYTES, allowance + 1))
        if not chunk:
            break
        count += len(chunk)
        if count > MAX_FILE_BYTES or count > remaining:
            raise _InspectionError("limit_exceeded", "实际读取超过文件或累计限额", name)
        digest.update(chunk)
        if keep:
            chunks.append(chunk)
    if count != size:
        raise _InspectionError("source_changed", "实际文件大小与读取前快照不一致", name)
    return {"path": name, "size": count, "sha256": digest.hexdigest()}, (
        b"".join(chunks) if keep else None)


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 包含重复键")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("JSON 不允许 NaN 或 Infinity")


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON 数值超出有限数值范围")
    return parsed


def _parse_json(data):
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=_reject_duplicates,
                      parse_constant=_reject_constant, parse_float=_finite_float)


def _manifest(data, files, result):
    if data is None:
        result["warnings"].append(_issue("manifest_missing", "未提供 manifest.json，完整性未验证"))
        return
    try:
        value = _parse_json(data)
    except (ValueError, UnicodeError, RecursionError):
        result["errors"].append(_issue("manifest_invalid", "清单必须是合法且无重复键的 UTF-8 JSON"))
        return
    if not isinstance(value, dict) or not isinstance(value.get("files"), dict):
        result["errors"].append(_issue("manifest_invalid", "清单 files 必须为路径到 SHA256 的字典"))
        return
    version = value.get("bundle_version")
    if not isinstance(version, str) or not version.strip():
        result["errors"].append(_issue("manifest_invalid", "bundle_version 必须为非空字符串"))
    schema = value.get("schema_version")
    if not ((type(schema) is int and schema == 1) or schema == "1"):
        result["warnings"].append(_issue("schema_unknown", "未知本地清单版本，Schema 状态为 unverified"))
    result["bundle_version"] = version
    result["manifest_schema_version"] = schema
    declared = value["files"]
    known = {item["path"]: item["sha256"] for item in files if item["path"] != "manifest.json"}
    aliases = set()
    for name, digest in declared.items():
        try:
            _safe_name(name)
        except _InspectionError as error:
            result["errors"].append(_issue(error.code, str(error), name))
            continue
        key = _path_key(name)
        if key in aliases:
            result["errors"].append(_issue("manifest_mismatch", "清单含大小写等价路径", name))
        aliases.add(key)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            result["errors"].append(_issue("manifest_invalid", "SHA256 必须为 64 位十六进制字符串", name))
        elif name not in known or known[name] != digest.lower():
            result["errors"].append(_issue("manifest_mismatch", "清单文件不存在或 SHA256 不一致", name))
    for name in known.keys() - declared.keys():
        result["errors"].append(_issue("manifest_mismatch", "文件未列入清单", name))


def _rules(data, result):
    if data is None:
        return
    try:
        value = _parse_json(data)
    except (ValueError, UnicodeError, RecursionError):
        result["errors"].append(_issue("rules_invalid", "规则不是合法 UTF-8 JSON", "rules/rules.json"))
        return
    rules = value.get("rules") if isinstance(value, dict) else value
    if not isinstance(rules, list):
        result["warnings"].append(_issue("rules_unknown", "未知规则结构，规则 Schema 为 unverified"))
        return
    identities = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            result["warnings"].append(_issue("rules_unknown", f"第 {index + 1} 条规则结构未知"))
            continue
        identifiers = [rule[key] for key in ("id", "rule_id") if key in rule]
        if (not identifiers or any(type(item) not in (str, int) or not str(item).strip()
                                   for item in identifiers)):
            result["warnings"].append(_issue("rules_unknown", f"第 {index + 1} 条规则缺少可识别 ID"))
            continue
        normalized = {str(item) for item in identifiers}
        if len(normalized) != 1:
            result["errors"].append(_issue("rules_invalid", f"第 {index + 1} 条规则的 ID 别名冲突"))
        identity = str(identifiers[0])
        if identity in identities:
            result["errors"].append(_issue("rules_invalid", "规则 ID 重复", identity))
        identities.add(identity)
    result["stats"]["rule_count"] = len(rules)


def _courses(contents, result):
    found = set()
    for name, data in sorted(contents.items()):
        if not (name.startswith("data/") and name.lower().endswith(".csv")):
            continue
        try:
            reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
            columns = reader.fieldnames or []
            candidates = [col for col in columns if col.strip().lower() in _COURSE_COLUMNS]
            if len(candidates) != 1 or len(set(columns)) != len(columns):
                result["warnings"].append(_issue("csv_unknown_columns", "无法唯一识别课号列，CSV 核查未验证", name))
                continue
            codes = set()
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise csv.Error("CSV 数据行列数与表头不一致")
                code = row.get(candidates[0])
                if code is not None:
                    code = code.strip()
                    if code in ("V011", "V012"):
                        codes.add(code)
            found.update(codes)
            if codes != {"V011", "V012"}:
                result["warnings"].append(_issue("csv_missing_codes", "可识别课号列未同时发现 V011 和 V012", name))
        except (UnicodeError, csv.Error):
            result["errors"].append(_issue("csv_invalid", "CSV 不是可解析的 UTF-8 表格", name))
    result["stats"]["course_codes"] = sorted(found)


def inspect_bundle(path):
    """返回只读本地证据；ok 表示无硬错误，unverified 仍不得放行生产。"""
    result = {"ok": False, "status": "invalid", "errors": [], "warnings": [],
              "files": [], "stats": {"file_count": 0, "total_bytes": 0,
                                       "rule_count": None, "course_codes": []},
              "source_hash": None, "source_hash_kind": "sha256-content-inventory-v1",
              "production_eligible": False, "schema_verified": False}
    contents = {}
    try:
        root = Path(os.path.abspath(os.fspath(path)))
        _check_ancestors(root)
        info = root.lstat()
        if stat.S_ISDIR(info.st_mode):
            entries = _directory_entries(root)
            for name, size, value in sorted(entries):
                file_path, initial_stamp = value
                stream, before = _open_regular(file_path)
                with stream:
                    if initial_stamp != _stamp(before):
                        raise _InspectionError("source_changed", "文件在目录扫描后发生变化", name)
                    item, content = _read_file(stream, name, size,
                        MAX_TOTAL_BYTES - result["stats"]["total_bytes"])
                    if _stamp(before) != _stamp(os.fstat(stream.fileno())):
                        raise _InspectionError("source_changed", "文件在读取中发生变化", name)
                    _check_ancestors(file_path)
                    if _stamp(before) != _stamp(file_path.stat(follow_symlinks=False)):
                        raise _InspectionError("source_changed", "文件在读取后发生替换", name)
                result["files"].append(item)
                result["stats"]["total_bytes"] += item["size"]
                if content is not None:
                    contents[name] = content
            refreshed = _directory_entries(root)
            if [(n, s, v[1]) for n, s, v in sorted(entries)] != [
                    (n, s, v[1]) for n, s, v in sorted(refreshed)]:
                raise _InspectionError("source_changed", "目录清单在核查中发生变化", root)
        elif stat.S_ISREG(info.st_mode):
            if info.st_size > MAX_ARCHIVE_BYTES:
                raise _InspectionError("limit_exceeded", "ZIP 容器超过 128 MiB 元数据读取安全上限", root)
            stream, before = _open_regular(root)
            with stream, zipfile.ZipFile(_ArchiveView(stream)) as archive:
                entries = _zip_entries(archive)
                for name, size, value in sorted(entries):
                    with archive.open(value, "r") as member:
                        item, content = _read_file(member, name, size,
                            MAX_TOTAL_BYTES - result["stats"]["total_bytes"])
                    result["files"].append(item)
                    result["stats"]["total_bytes"] += item["size"]
                    if content is not None:
                        contents[name] = content
                if _stamp(before) != _stamp(os.fstat(stream.fileno())):
                    raise _InspectionError("source_changed", "ZIP 在读取中发生变化", root)
                _check_ancestors(root)
                if _stamp(before) != _stamp(root.stat(follow_symlinks=False)):
                    raise _InspectionError("source_changed", "ZIP 在读取后发生替换", root)
        else:
            raise _InspectionError("unsafe_type", "来源必须是普通 ZIP 文件或目录", root)
        result["stats"]["file_count"] = len(result["files"])
        inventory = json.dumps(result["files"], sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")
        result["source_hash"] = hashlib.sha256(inventory).hexdigest()
        _manifest(contents.get("manifest.json"), result["files"], result)
        _rules(contents.get("rules/rules.json"), result)
        _courses(contents, result)
    except _InspectionError as error:
        result["errors"].append(_issue(error.code, str(error), error.path))
    except (OSError, ValueError, TypeError, RuntimeError, NotImplementedError,
            zipfile.BadZipFile, EOFError, RecursionError, zlib.error, lzma.LZMAError):
        result["errors"].append(_issue("source_error", "无法读取来源：路径不存在、ZIP 损坏或格式不支持"))
    result["stats"]["file_count"] = len(result["files"])
    result["ok"] = not result["errors"]
    result["status"] = "invalid" if result["errors"] else (
        "unverified" if result["warnings"] else "verified")
    return result
