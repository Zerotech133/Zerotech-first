# -*- coding: utf-8 -*-
"""JEV 待判断队列的可恢复、可审计处理器。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


DEFAULT_QUEUE = Path(__file__).resolve().parent / "data" / "jev_archive" / "jev_queue.json"


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def process_jev_queue(limit: int, resume_from: str | None, *, queue_path: str | Path = DEFAULT_QUEUE,
                      receipt_path: str | Path | None = None,
                      evaluator: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """处理最多 ``limit`` 条 pending JEV；已有 decision 永不覆盖。"""
    if limit <= 0:
        raise ValueError("limit 必须为正数")
    queue_file = Path(queue_path)
    if not queue_file.exists():
        return {"processed": 0, "blocked": 0, "remaining": 0, "production_write": False, "reason": "queue_missing"}
    payload = json.loads(queue_file.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("JEV 队列必须是数组")
    items = [dict(item) for item in payload if isinstance(item, Mapping)]
    candidates = [item for item in items if str(item.get("status", "pending")) == "pending"
                  and (resume_from is None or str(item.get("id", "")) > str(resume_from))]
    candidates.sort(key=lambda item: str(item.get("id", "")))
    selected = candidates[:limit]
    receipt_file = Path(receipt_path) if receipt_path else queue_file.with_suffix(".receipt.jsonl")
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    processed = 0
    blocked = 0
    next_resume = resume_from
    receipt_lines: list[str] = []
    for candidate in selected:
        item_id = str(candidate.get("id", ""))
        if evaluator is None:
            blocked += 1
            receipt_lines.append(json.dumps({"id": item_id, "status": "blocked", "reason": "evaluator_unavailable", "content_sha256": _sha(candidate.get("content", ""))}, ensure_ascii=False, sort_keys=True))
            continue
        try:
            decision = evaluator(candidate)
            if not isinstance(decision, Mapping) or not decision.get("decision") or not decision.get("model"):
                raise ValueError("判断结果缺少 decision/model")
            idx = items.index(candidate)
            now = datetime.now(timezone.utc).isoformat()
            content_sha = _sha(candidate.get("content", ""))
            items[idx].update({"status": "decided", "decision": str(decision["decision"]), "model": str(decision["model"]), "content_sha256": content_sha, "decided_at_utc": now})
            receipt_lines.append(json.dumps({"id": item_id, "status": "decided", "decision": str(decision["decision"]), "model": str(decision["model"]), "content_sha256": content_sha, "decided_at_utc": now}, ensure_ascii=False, sort_keys=True))
            processed += 1
            next_resume = item_id
        except Exception as exc:
            blocked += 1
            receipt_lines.append(json.dumps({"id": item_id, "status": "blocked", "reason": f"evaluator_error:{type(exc).__name__}", "content_sha256": _sha(candidate.get("content", ""))}, ensure_ascii=False, sort_keys=True))
    if processed:
        temporary = queue_file.with_suffix(queue_file.suffix + ".tmp")
        temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(queue_file)
    if receipt_lines:
        with receipt_file.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(receipt_lines) + "\n")
    return {"processed": processed, "blocked": blocked, "remaining": sum(str(item.get("status", "pending")) == "pending" for item in items), "next_resume": next_resume, "production_write": False, "global_activation": False, "receipt_path": str(receipt_file)}

