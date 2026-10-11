# -*- coding: utf-8 -*-
"""九维 as-of 来源快照只读适配层。

快照只保存 staging 来源回执，不能开启生产写入，也不能把合法空响应
解释为数值 0。适配层同时支持两种快照：带 ``rows`` 的回放载荷，以及
只含 ``row_count``/哈希的资格摘要；后者只能用于覆盖审计。
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional


class SnapshotFormatError(ValueError):
    """来源快照结构或安全门不满足要求。"""


class SourceSnapshot:
    """读取并规范化九维来源快照，整个对象保持只读语义。"""

    APIS = ("forecast", "report_rc", "block_trade")
    OPTIONAL_APIS = ("fina_indicator",)
    SUPPORTED_APIS = APIS + OPTIONAL_APIS
    _DATE_RE = re.compile(r"^\d{8}$")

    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SnapshotFormatError("无法读取来源快照") from exc
        if not isinstance(raw, dict) or raw.get("snapshot_type") not in \
                ("nine_dim_source_asof", "nine_dim_source_payload"):
            raise SnapshotFormatError("快照类型不符")
        if raw.get("global_activation") is not False or \
                raw.get("production_write") is not False:
            raise SnapshotFormatError("来源快照不得开启生产资格")
        asof = str(raw.get("asof_trade_date") or "")
        if not self._DATE_RE.fullmatch(asof):
            raise SnapshotFormatError("as-of 日期无效")
        receipts = (raw.get("source_receipts") or {}).get("per_code")
        if not isinstance(receipts, dict):
            raise SnapshotFormatError("缺少逐码来源回执")
        self.asof_trade_date = asof
        self.snapshot_type = raw["snapshot_type"]
        self.path = self.path.resolve()
        self._records: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for code, api_map in receipts.items():
            if not isinstance(code, str) or not code:
                raise SnapshotFormatError("股票代码无效")
            if not isinstance(api_map, dict):
                raise SnapshotFormatError("逐码回执结构无效")
            normalized: Dict[str, Dict[str, Any]] = {}
            for api in self.SUPPORTED_APIS:
                rec = api_map.get(api)
                if rec is None:
                    normalized[api] = self._missing(api, code)
                    continue
                if not isinstance(rec, dict):
                    raise SnapshotFormatError("接口回执结构无效")
                normalized[api] = self._normalize(api, code, rec)
            self._records[code] = normalized

    @staticmethod
    def _missing(api: str, code: str) -> Dict[str, Any]:
        return {
            "api_name": api,
            "ts_code": code,
            "source_status": "missing_receipt",
            "coverage_status": "unknown",
            "source_code": None,
            "err": 1,
            "fields": [],
            "rows": [],
            "row_count": 0,
            "payload_available": False,
        }

    @classmethod
    def _normalize(cls, api: str, code: str,
                   rec: Mapping[str, Any]) -> Dict[str, Any]:
        source_code = rec.get("source_code")
        fields = rec.get("fields") or []
        rows_present = "rows" in rec
        rows = rec.get("rows") or []
        if not isinstance(fields, list) or not isinstance(rows, list):
            raise SnapshotFormatError("字段或载荷结构无效")
        if source_code == 0:
            status = "source_response_verified"
            coverage = rec.get("coverage_status") or (
                "sample_has_data" if rows else "valid_empty_response")
        else:
            status = "source_error" if source_code is not None else "unknown"
            coverage = "unknown"
        return {
            "api_name": api,
            "ts_code": code,
            "source_status": status,
            "coverage_status": coverage,
            "source_code": source_code,
            "err": 0 if source_code == 0 else 1,
            "source_message": str(rec.get("source_message") or "")[:200],
            "request_params": copy.deepcopy(rec.get("request_params") or {}),
            "body_sha256": rec.get("body_sha256"),
            "fields": copy.deepcopy(fields),
            "rows": copy.deepcopy(rows),
            "row_count": int(rec.get("row_count") or len(rows)),
            "payload_available": rows_present,
        }

    def get(self, api: str, code: str,
            default: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """返回单个接口回执副本；缺失时保持 unknown。"""
        if api not in self.SUPPORTED_APIS:
            raise KeyError(api)
        rec = self._records.get(code, {}).get(api)
        if rec is None:
            if default is not None:
                return copy.deepcopy(dict(default))
            return self._missing(api, code)
        return copy.deepcopy(rec)

    def iter_records(self, api: str) -> Iterator[Dict[str, Any]]:
        """按代码顺序遍历接口回执副本。"""
        if api not in self.SUPPORTED_APIS:
            raise KeyError(api)
        for code in sorted(self._records):
            yield self.get(api, code)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(sorted(self._records))


class ApiRecordStore:
    """把 ``SourceSnapshot`` 适配成回放缓存使用的 ``.get(code)``。"""

    def __init__(self, snapshot: SourceSnapshot, api: str):
        if api not in snapshot.SUPPORTED_APIS:
            raise KeyError(api)
        self.snapshot = snapshot
        self.api = api

    def get(self, code: str, default: Optional[Mapping[str, Any]] = None):
        return self.snapshot.get(self.api, code, default)

    def iter_records(self) -> Iterator[Dict[str, Any]]:
        return self.snapshot.iter_records(self.api)

