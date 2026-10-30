# -*- coding: utf-8 -*-
"""JEV 回填动态 batch：按 deadline 与历史单条耗时估算本轮批量。

原则：
- 历史中位耗时来自 `logs/jev_backfill_metrics.json`（最近 20 轮）；
- 没有历史时用 3.0s/条（2026-10-30 审计实测中位 3.0s）；
- 留 15% + 30s 余量，避免固定 600 在慢路径上必然 deadline_hit；
- 仍受 --batch 显式值约束：显式 >0 时不覆盖。
"""
from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

DEFAULT_ITEM_S = 3.0
MIN_BATCH = 8
MAX_BATCH = 600
KEEP_SAMPLES = 20


def metrics_path() -> Path:
    return Path(os.environ.get(
        "JEV_BACKFILL_METRICS",
        str(Path(__file__).resolve().parent / "logs" / "jev_backfill_metrics.json"),
    ))


def _load(path: Path | None = None) -> dict:
    p = path or metrics_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"samples": []}


def median_item_s(path: Path | None = None) -> float:
    samples = _load(path).get("samples") or []
    vals = []
    for s in samples[-KEEP_SAMPLES:]:
        try:
            v = float(s.get("item_s") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if 0.5 <= v <= 120:
            vals.append(v)
    return float(statistics.median(vals)) if vals else DEFAULT_ITEM_S


def estimate_batch(deadline_s: int, remaining: int, explicit: int = 0,
                   path: Path | None = None) -> int:
    """返回本轮 batch；explicit>0 原样返回（保留人工控制）。"""
    if remaining <= 0:
        return 0
    if explicit and explicit > 0:
        return min(int(explicit), int(remaining))
    item_s = median_item_s(path)
    if deadline_s and deadline_s > 0:
        budget = max(1.0, float(deadline_s) - 30.0) * 0.85
        n = int(budget / max(item_s, 0.05))
    else:
        n = MAX_BATCH
    n = max(MIN_BATCH, min(MAX_BATCH, n, int(remaining)))
    return int(n)


def record_run(elapsed_s: float, judged: int, remaining: int, batch: int,
               deadline_hit: int = 0, path: Path | None = None) -> None:
    p = path or metrics_path()
    data = _load(p)
    samples = list(data.get("samples") or [])
    # 样本质量门槛：少于 20 条（锁被抢/提前收工）不写入基准，避免把
    # 测试/异常样本带进下一轮 batch。
    if judged >= 20 and elapsed_s > 0:
        samples.append({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "item_s": round(float(elapsed_s) / judged, 4),
            "judged": int(judged),
            "batch": int(batch),
            "remaining": int(remaining),
            "deadline_hit": int(deadline_hit),
        })
    data["samples"] = samples[-KEEP_SAMPLES:]
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
