# -*- coding: utf-8 -*-
"""来源回执、staging、发布文件和回读结果的血缘清单。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_lineage_manifest(
    unit_id: str,
    *,
    source_receipts: Iterable[Mapping[str, Any]],
    commit_complete: Mapping[str, Any],
    staging_path: str | Path,
    target: str | Path,
    primary_key: list[str],
    readback: Mapping[str, Any],
    published_at: str | None = None,
) -> dict[str, Any]:
    """构建并校验单元血缘；任一门禁失败都拒绝生成可发布清单。"""
    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError("unit_id 不合法")
    receipts = list(source_receipts)
    receipt_ids = [str(item.get("receipt_id", "")) for item in receipts]
    if not receipts or any(not value for value in receipt_ids):
        raise ValueError("缺少来源回执")
    if not isinstance(commit_complete, Mapping) or commit_complete.get("unit_id") != unit_id:
        raise ValueError("提交完成事实与单元不一致")
    linked = commit_complete.get("receipt_ids")
    if not isinstance(linked, list) or set(linked) != set(receipt_ids):
        raise ValueError("来源回执关联不一致")
    staged = Path(staging_path)
    published = Path(target)
    if not staged.exists() or not published.exists():
        raise ValueError("staging 或发布文件缺失")
    files = commit_complete.get("files") or []
    if not isinstance(files, list) or not files:
        raise ValueError("提交文件清单缺失")
    target_file = next((item for item in files if item.get("target") == str(published)), None)
    if not target_file or not isinstance(target_file.get("new_sha256"), str):
        raise ValueError("提交文件指纹缺失")
    actual_sha = _sha256(published)
    if actual_sha != target_file["new_sha256"]:
        raise ValueError("指纹不一致")
    if not isinstance(readback, Mapping) or readback.get("sha256") != actual_sha:
        raise ValueError("指纹或回读不一致")
    if not readback.get("query_digest"):
        raise ValueError("回读 digest 缺失")
    counts = target_file.get("counts") or {}
    rows = readback.get("rows")
    if counts.get("processed_rows") is not None and rows != counts["processed_rows"]:
        raise ValueError("回读行数不一致")
    stamp = published_at or datetime.now(timezone.utc).isoformat()
    result = {
        "version": 1,
        "unit_id": unit_id,
        "source_receipt_ids": receipt_ids,
        "source": [{"receipt_id": item.get("receipt_id"), "table": item.get("table"),
                     "request_scope": item.get("request_scope")} for item in receipts],
        "staging_path": str(staged),
        "target": str(published),
        "sha256": actual_sha,
        "primary_key": list(primary_key),
        "row_counts": {"readback": readback.get("rows"), "unique_keys": readback.get("unique_keys")},
        "published_at": stamp,
        "readback_digest": readback["query_digest"],
        "production_write": False,
        "global_activation": False,
    }
    return result


def write_lineage_manifest(manifest: Mapping[str, Any], path: str | Path) -> None:
    """以原子替换方式写入脱敏血缘清单。"""
    if manifest.get("production_write") is not False or manifest.get("global_activation") is not False:
        raise ValueError("血缘清单不能开启生产发布")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
