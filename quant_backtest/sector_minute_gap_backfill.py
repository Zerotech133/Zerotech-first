# -*- coding: utf-8 -*-
"""TDX 板块分钟缺口的只读探针与 staging 适配层。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlencode

import pandas as pd
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen


REMOTE = "http://rookie.xtcloud-tech.com:48088/api/data/table/tdx_sector_min"
REQUIRED = [
    "code",
    "trade_date",
    "trade_time",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
]
VALUE_COLUMNS = ["open", "high", "low", "close", "vol", "amount"]
TIME_RE = re.compile(r"^\d{2}:\d{2}(?::\d{2})?$")


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _date(value: Any) -> bool:
    text = str(value)
    try:
        return (
            len(text) == 8
            and text.isdigit()
            and datetime.strptime(text, "%Y%m%d").strftime("%Y%m%d") == text
        )
    except ValueError:
        return False


def validate_sector_minute_frame(frame: pd.DataFrame) -> dict[str, Any]:
    missing_columns = [column for column in REQUIRED if column not in frame.columns]
    required_nulls = {
        column: int(frame[column].isna().sum()) if column in frame else int(len(frame))
        for column in VALUE_COLUMNS
    }
    invalid_keys: list[dict[str, Any]] = []
    if not missing_columns:
        for index, row in frame.iterrows():
            if not _date(row["trade_date"]):
                invalid_keys.append(
                    {"index": int(index), "reason": "invalid_trade_date"}
                )
            valid_time = False
            if isinstance(row["trade_time"], str) and TIME_RE.fullmatch(
                row["trade_time"]
            ):
                try:
                    datetime.strptime(
                        row["trade_time"],
                        "%H:%M:%S" if len(row["trade_time"]) == 8 else "%H:%M",
                    )
                    valid_time = True
                except ValueError:
                    valid_time = False
            if not valid_time:
                invalid_keys.append(
                    {"index": int(index), "reason": "invalid_trade_time"}
                )
    duplicate_keys = (
        int(frame.duplicated(["code", "trade_date", "trade_time"], keep=False).sum())
        if not missing_columns
        else 0
    )
    return {
        "ok": not missing_columns
        and not any(required_nulls.values())
        and not invalid_keys
        and duplicate_keys == 0,
        "row_count": int(len(frame)),
        "missing_columns": missing_columns,
        "required_nulls": required_nulls,
        "duplicate_keys": duplicate_keys,
        "invalid_keys": invalid_keys,
    }


def _params(code: str, trade_date: str, offset: int = 0) -> dict[str, Any]:
    return {
        "code": str(code),
        "start_date": str(trade_date),
        "end_date": str(trade_date),
        "limit": 5000,
        "offset": offset,
    }


def _request(params: Mapping[str, Any]) -> Mapping[str, Any]:
    url = REMOTE + "?" + urlencode(params)
    with safe_urlopen(url, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _result(
    status: str,
    code: str,
    trade_date: str,
    *,
    response: Any = None,
    reason: str | None = None,
    frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "table": "tdx_sector_min",
        "code": str(code),
        "trade_date": str(trade_date),
        "status": status,
        "production_write": False,
        "request_parameters": _params(code, trade_date),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if response is not None:
        result["raw_response"] = response
        result["source_response_json_sha256"] = _sha(response)
    if reason:
        result["reason"] = reason
    if frame is not None:
        result["frame"] = frame
        result["row_count"] = int(len(frame))
    else:
        result["row_count"] = 0
    return result


def probe_sector_minute(
    code: str,
    trade_date: str,
    start_time: str | None = None,
    end_time: str | None = None,
    fetch: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """按代码/日期探针；过滤失效时阻断，不将别的代码的分钟行归入缺口。"""
    params = _params(code, trade_date)
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time
    try:
        response = fetch(params) if fetch is not None else _request(params)
        if not isinstance(response, Mapping):
            return _result(
                "invalid_source",
                code,
                trade_date,
                response=response,
                reason="response_not_object",
            )
        rows, columns, total = (
            response.get("rows"),
            response.get("columns"),
            response.get("total"),
        )
        if (
            not isinstance(rows, list)
            or not isinstance(columns, list)
            or len(columns) != len(set(columns))
        ):
            return _result(
                "invalid_source",
                code,
                trade_date,
                response=response,
                reason="response_contract_invalid",
            )
        if not isinstance(total, int) or isinstance(total, bool) or total < len(rows):
            return _result(
                "invalid_source",
                code,
                trade_date,
                response=response,
                reason="total_mismatch",
            )
        # 自定义 fetch 用于单页测试；真实请求才继续分页，避免重复调用一个固定测试响应。
        if total > len(rows) and fetch is not None:
            return _result(
                "blocked",
                code,
                trade_date,
                response=response,
                reason="pagination_required",
            )
        if fetch is None and total > len(rows):
            all_rows = list(rows)
            offset = len(rows)
            while offset < total:
                page = _request({**params, "offset": offset})
                page_rows = page.get("rows", [])
                if (
                    not page_rows
                    or page.get("total") != total
                    or page.get("columns") != columns
                ):
                    return _result(
                        "blocked",
                        code,
                        trade_date,
                        response=response,
                        reason="pagination_contract_invalid",
                    )
                all_rows.extend(page_rows)
                offset += len(page_rows)
                if offset > 1_000_000:
                    return _result(
                        "blocked",
                        code,
                        trade_date,
                        response=response,
                        reason="pagination_bound_exceeded",
                    )
            response = {**response, "rows": all_rows, "total": len(all_rows)}
            rows = all_rows
        if total == 0:
            return _result(
                "source_empty",
                code,
                trade_date,
                response=response,
                reason="source_empty",
            )
        if any(len(row) != len(columns) for row in rows):
            return _result(
                "invalid_source",
                code,
                trade_date,
                response=response,
                reason="row_width_invalid",
            )
        frame = pd.DataFrame(rows, columns=columns)
        if "code" not in frame or "trade_date" not in frame:
            return _result(
                "invalid_source",
                code,
                trade_date,
                response=response,
                reason="required_columns_missing",
            )
        if set(frame["code"].astype(str)) != {str(code)} or set(
            frame["trade_date"].astype(str)
        ) != {str(trade_date)}:
            return _result(
                "blocked",
                code,
                trade_date,
                response=response,
                reason="remote_filter_ignored",
            )
        checked = validate_sector_minute_frame(frame)
        if not checked["ok"]:
            return _result(
                "invalid_source",
                code,
                trade_date,
                response=response,
                reason="frame_contract_invalid",
                frame=frame,
            )
        result = _result(
            "candidate_ready", code, trade_date, response=response, frame=frame
        )
        result.update({k: v for k, v in checked.items() if k != "ok"})
        return result
    except Exception as exc:
        return _result(
            "blocked",
            code,
            trade_date,
            reason=f"probe_error:{type(exc).__name__}",
            response=None,
        )


def stage_sector_minute(
    keys: Sequence[Mapping[str, Any]],
    stage_root: str | Path,
    probe: Callable[..., Mapping[str, Any]] = probe_sector_minute,
) -> dict[str, Any]:
    root = Path(stage_root)
    raw_dir = root / "raw"
    data_dir = root / "tdx_sector_min"
    raw_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    receipt_rows: list[dict[str, Any]] = []
    frames: list[pd.DataFrame] = []
    counts = {"added": 0, "blocked": 0, "rejected": 0, "source_empty": 0}
    for key in sorted(
        keys, key=lambda x: (str(x.get("trade_date", "")), str(x.get("code", "")))
    ):
        code, date = str(key.get("code", "")), str(key.get("trade_date", ""))
        try:
            try:
                result = probe(code, date, key.get("start_time"), key.get("end_time"))
            except TypeError:
                # 测试/外部适配器也可接收完整 key 对象。
                result = probe(key)
            if isinstance(result, Mapping) and "rows" in result and "columns" in result:
                result = probe_sector_minute(
                    code,
                    date,
                    key.get("start_time"),
                    key.get("end_time"),
                    fetch=lambda _p: result,
                )
            result = dict(result)
        except Exception as exc:
            result = _result(
                "invalid_source", code, date, reason=f"probe_error:{type(exc).__name__}"
            )
        raw = result.get("raw_response")
        if raw is not None:
            (raw_dir / f"{code}_{date}.json").write_text(
                json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        status = str(result.get("status"))
        row_count = int(result.get("row_count", 0))
        if status == "candidate_ready" and isinstance(
            result.get("frame"), pd.DataFrame
        ):
            frames.append(result["frame"])
            counts["added"] += row_count
        elif status == "source_empty":
            counts["source_empty"] += 1
            counts["blocked"] += 1
        elif status == "blocked":
            counts["blocked"] += 1
        else:
            counts["rejected"] += 1
        receipt_rows.append(
            {
                "code": code,
                "trade_date": date,
                "status": status,
                "reason": result.get("reason"),
                "row_count": row_count,
                "source_response_json_sha256": result.get(
                    "source_response_json_sha256"
                ),
            }
        )
    if frames:
        pd.concat(frames, ignore_index=True).to_parquet(
            data_dir / "part_0000.parquet", index=False
        )
    receipt = {
        "table": "tdx_sector_min",
        "production_write": False,
        "global_activation": False,
        "counts": counts,
        "rows": receipt_rows,
    }
    (root / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "table": "tdx_sector_min",
        "production_write": False,
        "global_activation": False,
        "counts": counts,
        "receipt_path": str(root / "receipt.json"),
        "rows": receipt_rows,
    }
