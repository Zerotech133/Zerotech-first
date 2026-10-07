"""对照本地声明证据；匹配通过仍需真实来源认证与人审。"""

from datetime import date
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from . import contracts
from .capture_journal import validate_receipt
from .provenance import PRICE_BASES, _local_path


MAX_EVIDENCE_BYTES = 1024 * 1024
_KINDS = ("supplier_identity", "price_basis", "revision", "official_calendar")
_DOCUMENT_FIELDS = {
    "supplier_identity": {"source_id", "provider"},
    "price_basis": {"code", "price_basis", "adjustment_reference"},
    "revision": {"revision", "source_sha256"},
    "official_calendar": {"exchange", "rows"},
}
_REVIEW_FIELDS = {"schema_version", "capture_event_id", "stream_id", "reviewer", "compiled_at",
                  "evidence", "checks", "status", "eligible", "production_eligible", "review_id"}
_EVIDENCE_FIELDS = {"kind", "path", "url", "sha256", "size_bytes", "raw_utf8"}
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _fields(value, expected, name):
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{name} 必须严格包含固定字段")


def _text(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return value


def _canonical(value):
    try:
        result = contracts.canonical_json(value)
        result.encode("utf-8")
        return result
    except UnicodeError as exc:
        raise ValueError("JSON 字符串必须可编码为严格 UTF-8") from exc


def _digest(value, name):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} 必须是小写 SHA256")


def _review_id(review):
    return hashlib.sha256(_canonical({key: value for key, value in review.items()
                                     if key != "review_id"}).encode("utf-8")).hexdigest()


def _url(value, exchange=None):
    _text(value, "url")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise ValueError("证据 URL 不允许空白、控制字符或反斜杠")
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("证据 URL 必须是无凭据的完整 HTTPS URL")
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("证据 URL 端口非法")
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                                      for label in host.split(".")):
                raise ValueError("证据 URL 主机名非法")
    except (UnicodeError, ValueError) as exc:
        raise ValueError("证据 URL 不是合法 HTTPS URL") from exc
    if exchange is not None:
        domain = "sse.com.cn" if exchange == "SSE" else "szse.cn"
        if host != domain and not host.endswith("." + domain):
            raise ValueError("官方日历 URL 必须属于对应交易所真实域或其子域")
    return value


def _reject_links(path):
    for part in (path, *path.parents):
        details = part.lstat()
        if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & _REPARSE:
            raise ValueError("证据路径不能包含符号链接或 Windows 重解析点")


def _read_evidence(path):
    _local_path(path)
    absolute = Path(os.path.abspath(path))
    _local_path(str(absolute))
    try:
        _reject_links(absolute)
        before = absolute.lstat()
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_EVIDENCE_BYTES:
            raise ValueError("证据必须是大小为 1 字节至 1 MiB 的普通文件")
        descriptor = os.open(absolute, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            _reject_links(absolute)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("证据文件在打开时被替换")
            if not 1 <= opened.st_size <= MAX_EVIDENCE_BYTES:
                raise ValueError("证据文件超过 1 MiB 或为空")
            raw = stream.read(MAX_EVIDENCE_BYTES + 1)
        if not 1 <= len(raw) <= MAX_EVIDENCE_BYTES:
            raise ValueError("证据文件超过 1 MiB 或为空")
        return str(absolute), raw
    except (OSError, TypeError, RuntimeError) as exc:
        raise ValueError("无法安全读取本地普通证据文件") from exc


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("证据 JSON 包含重复键")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("证据 JSON 不允许 NaN 或无穷数")


def _document(raw_utf8, kind):
    try:
        document = json.loads(raw_utf8, object_pairs_hook=_unique_pairs, parse_constant=_invalid_constant)
        _canonical(document)
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as exc:
        raise ValueError("证据不是严格 UTF-8 JSON") from exc
    _fields(document, _DOCUMENT_FIELDS[kind], "证据文档")
    if kind == "supplier_identity":
        for field in ("source_id", "provider"):
            _text(document[field], field)
    elif kind == "price_basis":
        if type(document["code"]) is not str or re.fullmatch(r"[0-9]{6}\.(SH|SZ)", document["code"]) is None:
            raise ValueError("证据 code 必须是六位证券代码加 .SH 或 .SZ")
        basis = document["price_basis"]
        if type(basis) is not str or basis not in PRICE_BASES:
            raise ValueError("证据价格口径非法")
        reference = document["adjustment_reference"]
        if basis == "raw" and reference is not None:
            raise ValueError("raw 价格证据的 adjustment_reference 必须为空")
        if reference is not None:
            _url(reference)
    elif kind == "revision":
        _text(document["revision"], "revision")
        _digest(document["source_sha256"], "source_sha256")
    else:
        if type(document["exchange"]) is not str or document["exchange"] not in ("SSE", "SZSE"):
            raise ValueError("官方日历 exchange 必须是 SSE 或 SZSE")
        if type(document["rows"]) is not list:
            raise ValueError("官方日历 rows 必须是数组")
        seen = {}
        for row in document["rows"]:
            _fields(row, {"cal_date", "is_open"}, "官方日历行")
            day = row["cal_date"]
            if type(day) is not str or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day) is None:
                raise ValueError("官方日历日期必须是 YYYY-MM-DD")
            try:
                date.fromisoformat(day)
            except ValueError as exc:
                raise ValueError("官方日历日期不存在") from exc
            flag = row["is_open"]
            if type(flag) is not int or flag not in (0, 1):
                raise ValueError("官方日历 is_open 必须是整数 0 或 1，不能是布尔值")
            if day in seen and seen[day] != flag:
                raise ValueError("官方日历存在冲突重复日期")
            seen[day] = flag
    return document


def _bound_documents(evidence, exchange):
    if type(evidence) is not list or len(evidence) > len(_KINDS):
        raise ValueError("审核 evidence 必须是至多四类证据的数组")
    documents = {}
    indexes = []
    for item in evidence:
        _fields(item, _EVIDENCE_FIELDS, "审核证据")
        kind = item["kind"]
        if type(kind) is not str or kind not in _KINDS or kind in documents:
            raise ValueError("审核证据类别非法或重复")
        indexes.append(_KINDS.index(kind))
        _local_path(item["path"])
        if not os.path.isabs(item["path"]) or item["path"] != os.path.abspath(item["path"]):
            raise ValueError("审核证据路径必须是规范本地绝对路径")
        _url(item["url"], exchange if kind == "official_calendar" else None)
        _digest(item["sha256"], "证据 sha256")
        if type(item["size_bytes"]) is not int or not 1 <= item["size_bytes"] <= MAX_EVIDENCE_BYTES:
            raise ValueError("证据 size_bytes 必须为 1 至 1 MiB 范围内的整数")
        if type(item["raw_utf8"]) is not str:
            raise ValueError("证据 raw_utf8 必须是字符串")
        try:
            raw = item["raw_utf8"].encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("证据 raw_utf8 不是严格 UTF-8") from exc
        if len(raw) != item["size_bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("证据原始字节、大小与 SHA256 不匹配")
        documents[kind] = _document(item["raw_utf8"], kind)
    if indexes != sorted(indexes):
        raise ValueError("证据必须按冻结类别顺序排列")
    return documents


def _checks(receipt, documents):
    snapshot, calendar = receipt["snapshot"], receipt["calendar_capture"]
    checks = []
    for kind in _KINDS:
        document = documents.get(kind)
        status, reason = "missing", "缺少声明证据文档"
        if document is not None:
            if kind == "supplier_identity":
                matched = document["source_id"] == snapshot["source_id"]
            elif kind == "price_basis":
                matched = document["code"] == snapshot["code"] and document["price_basis"] == snapshot["price_basis"]
            elif kind == "revision":
                matched = document["revision"] == snapshot["revision"] and document["source_sha256"] == snapshot["source_file"]["sha256"]
            else:
                rows = {row["cal_date"]: row["is_open"] for row in document["rows"]}
                mismatched = document["exchange"] != calendar["exchange"] or any(
                    day["session"] in rows and rows[day["session"]] != day["is_open"] for day in calendar["days"])
                missing = any(day["session"] not in rows for day in calendar["days"])
                if mismatched:
                    status, reason = "fail", "官方日历参考与接收日历不一致"
                elif missing:
                    status, reason = "missing", "官方日历参考未覆盖全部自然日"
                else:
                    status, reason = "pass", "官方域声明和覆盖日期一致，尚未认证下载或签名"
                checks.append({"name": kind, "status": status, "reason": reason})
                continue
            status = "pass" if matched else "fail"
            reason = "声明与接收凭据一致，尚未认证真实来源" if matched else "声明与接收凭据不一致"
        checks.append({"name": kind, "status": status, "reason": reason})
    if snapshot["price_basis"] in ("qfq_snapshot", "hfq_snapshot"):
        checks.append({"name": "adjustment_factors", "status": "missing",
                       "reason": "缺少复权算法及因子的独立认证，本批不提供认证"})
    statuses = {check["status"] for check in checks}
    status = "mismatch" if "fail" in statuses else "needs_evidence" if "missing" in statuses else "ready_for_human_review"
    return checks, status


def build_source_review(receipt, reviewer, evidence) -> dict:
    """读取当前本地声明证据并逐项对照，不能授予生产资格。"""
    receipt = validate_receipt(receipt)
    _text(reviewer, "reviewer")
    _canonical(evidence)
    _fields(evidence, set(_KINDS), "evidence")
    frozen = []
    for kind in _KINDS:
        request = evidence[kind]
        if request is None:
            continue
        _fields(request, {"path", "url"}, "证据请求")
        _url(request["url"], receipt["calendar_capture"]["exchange"] if kind == "official_calendar" else None)
        path, raw = _read_evidence(request["path"])
        try:
            raw_utf8 = raw.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError("证据不是严格 UTF-8 JSON") from exc
        frozen.append({"kind": kind, "path": path, "url": request["url"],
                       "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "raw_utf8": raw_utf8})
    documents = _bound_documents(frozen, receipt["calendar_capture"]["exchange"])
    checks, status = _checks(receipt, documents)
    compiled_at = contracts.normalize_time(contracts.utc_now())
    if compiled_at < receipt["recorded_available_at"]:
        raise ValueError("系统编译时间早于接收凭据的实际可用时间")
    review = {"schema_version": 1, "capture_event_id": receipt["capture_event_id"], "stream_id": receipt["stream_id"],
              "reviewer": reviewer, "compiled_at": compiled_at, "evidence": frozen, "checks": checks,
              "status": status, "eligible": False, "production_eligible": False}
    review["review_id"] = _review_id(review)
    return review


def validate_source_review(review, receipt) -> dict:
    """离线重算证据字节与语义；哈希完整性无法排除人为重造声明。"""
    receipt = validate_receipt(receipt)
    serialized = _canonical(review)
    _fields(review, _REVIEW_FIELDS, "source review")
    result = json.loads(serialized)
    if type(result["schema_version"]) is not int or result["schema_version"] != 1:
        raise ValueError("仅支持审核 schema_version=1")
    for name in ("capture_event_id", "stream_id"):
        _digest(result[name], name)
        if result[name] != receipt[name]:
            raise ValueError("来源审核与接收凭据身份不一致")
    _text(result["reviewer"], "reviewer")
    if result["eligible"] is not False or result["production_eligible"] is not False:
        raise ValueError("来源声明审核不能授予放行或生产资格")
    compiled = result["compiled_at"]
    if type(compiled) is not str or contracts.normalize_time(compiled) != compiled:
        raise ValueError("compiled_at 必须是规范带时区 UTC 时间字符串")
    if compiled < receipt["recorded_available_at"] or compiled > contracts.normalize_time(contracts.utc_now()):
        raise ValueError("审核编译时间不能早于接收可用时间或晚于当前时间")
    _digest(result["review_id"], "review_id")
    if result["review_id"] != _review_id(result):
        raise ValueError("review_id 与审核完整内容不匹配")
    documents = _bound_documents(result["evidence"], receipt["calendar_capture"]["exchange"])
    checks, status = _checks(receipt, documents)
    if _canonical(result["checks"]) != _canonical(checks) or result["status"] != status:
        raise ValueError("审核 checks 或 status 与原始证据语义不一致")
    return result
