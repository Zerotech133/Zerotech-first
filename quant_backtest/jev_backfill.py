# -*- coding: utf-8 -*-
"""JEV 待判断队列的可恢复、可审计处理器。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


DEFAULT_QUEUE = (
    Path(__file__).resolve().parent / "data" / "jev_archive" / "jev_queue.json"
)


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _content_sha(item: Mapping[str, Any]) -> str:
    """对标题、正文和关联标的一起做输入指纹，避免只哈希正文造成误对账。"""
    return _sha(
        {
            "title": item.get("title", ""),
            "content": item.get("content", ""),
            "stocks": item.get("stocks", ""),
        }
    )


def build_jev_queue(
    news_db_path: str | Path, queue_path: str | Path = DEFAULT_QUEUE
) -> dict[str, Any]:
    """从 news.db 建立/增量更新 JEV staging 队列，不写 news.db。"""
    import sqlite3

    db_path = Path(news_db_path)
    if not db_path.exists():
        return {
            "added": 0,
            "pending": 0,
            "queue_path": str(queue_path),
            "reason": "news_db_missing",
        }
    queue_file = Path(queue_path)
    existing: list[dict[str, Any]] = []
    if queue_file.exists():
        payload = json.loads(queue_file.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("JEV 队列必须是数组")
        existing = [dict(item) for item in payload if isinstance(item, Mapping)]
    by_id = {
        str(item.get("id")): item for item in existing if item.get("id") is not None
    }
    db = sqlite3.connect(
        "file:" + str(db_path).replace("\\", "/") + "?mode=ro", uri=True
    )
    rows = db.execute(
        "SELECT id, source, ts, title, content, stocks, created_at "
        "FROM news WHERE jev IS NULL OR jev='' ORDER BY ts DESC"
    ).fetchall()
    db.close()
    added = 0
    for row in rows:
        item_id, source, ts, title, content, stocks, created_at = row
        key = str(item_id)
        if key in by_id:
            continue
        item = {
            "id": key,
            "source": source or "",
            "ts": ts or "",
            "title": title or "",
            "content": content or "",
            "stocks": stocks or "",
            "created_at": created_at or "",
            "status": "pending",
        }
        item["content_sha256"] = _content_sha(item)
        by_id[key] = item
        added += 1
    merged = list(by_id.values())
    merged.sort(key=lambda item: str(item.get("id", "")))
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = queue_file.with_suffix(queue_file.suffix + ".tmp")
    tmp.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(queue_file)
    return {
        "added": added,
        "existing": len(existing),
        "pending": sum(
            str(item.get("status", "pending")) == "pending" for item in merged
        ),
        "queue_path": str(queue_file),
        "production_write": False,
        "global_activation": False,
    }


def process_jev_queue(
    limit: int,
    resume_from: str | None,
    *,
    queue_path: str | Path = DEFAULT_QUEUE,
    receipt_path: str | Path | None = None,
    evaluator: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """处理最多 ``limit`` 条 pending JEV；已有 decision 永不覆盖。"""
    if limit <= 0:
        raise ValueError("limit 必须为正数")
    queue_file = Path(queue_path)
    if not queue_file.exists():
        return {
            "processed": 0,
            "blocked": 0,
            "remaining": 0,
            "production_write": False,
            "reason": "queue_missing",
        }
    payload = json.loads(queue_file.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("JEV 队列必须是数组")
    items = [dict(item) for item in payload if isinstance(item, Mapping)]
    candidates = [
        item
        for item in items
        if str(item.get("status", "pending")) == "pending"
        and (resume_from is None or str(item.get("id", "")) > str(resume_from))
    ]
    candidates.sort(key=lambda item: str(item.get("id", "")))
    selected = candidates[:limit]
    receipt_file = (
        Path(receipt_path) if receipt_path else queue_file.with_suffix(".receipt.jsonl")
    )
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    processed = 0
    blocked = 0
    next_resume = resume_from
    receipt_lines: list[str] = []
    for candidate in selected:
        item_id = str(candidate.get("id", ""))
        if evaluator is None:
            blocked += 1
            receipt_lines.append(
                json.dumps(
                    {
                        "id": item_id,
                        "status": "blocked",
                        "reason": "evaluator_unavailable",
                        "content_sha256": _content_sha(candidate),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            continue
        try:
            decision = evaluator(candidate)
            if (
                not isinstance(decision, Mapping)
                or not decision.get("decision")
                or not decision.get("model")
            ):
                raise ValueError("判断结果缺少 decision/model")
            idx = items.index(candidate)
            now = datetime.now(timezone.utc).isoformat()
            content_sha = _content_sha(candidate)
            audit = {
                "status": "decided",
                "decision": str(decision["decision"]),
                "model": str(decision["model"]),
                "content_sha256": content_sha,
                "decided_at_utc": now,
            }
            # 判断器可选返回依据、置信度和范围；只接受 JSON 基础类型，避免把
            # 不可序列化的模型对象写入可审计回执。
            for key in ("reason", "confidence", "scope"):
                value = decision.get(key)
                if isinstance(value, (str, int, float, bool)):
                    audit[key] = value
            items[idx].update(audit)
            receipt_lines.append(
                json.dumps({"id": item_id, **audit}, ensure_ascii=False, sort_keys=True)
            )
            processed += 1
            next_resume = item_id
        except Exception as exc:
            blocked += 1
            receipt_lines.append(
                json.dumps(
                    {
                        "id": item_id,
                        "status": "blocked",
                        "reason": f"evaluator_error:{type(exc).__name__}",
                        "content_sha256": _content_sha(candidate),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    if processed:
        temporary = queue_file.with_suffix(queue_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(items, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(queue_file)
    if receipt_lines:
        with receipt_file.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(receipt_lines) + "\n")
    return {
        "processed": processed,
        "blocked": blocked,
        "remaining": sum(
            str(item.get("status", "pending")) == "pending" for item in items
        ),
        "next_resume": next_resume,
        "production_write": False,
        "global_activation": False,
        "receipt_path": str(receipt_file),
    }
