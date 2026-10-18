# -*- coding: utf-8 -*-
"""郭嘉·预判对账司出口 mixin（K-ORG-191，2026-10-18）。

GET /api/guojia/data   零参数
    → 郭嘉对账聚合快照机读投影（news_collector/data/guojia/summary.json
      由 guojia_reconcile.py 跑批产出）+ verdicts 账本行数/mtime。
      产物缺失是合法空态（exists=false + hint），坏 JSON 带 error 不吞。

只读纪律（沿 api_daily_data 判例）：mode=ro 语义（只读文件不触库）、
fail-visible、参数白名单空集，带任何参数走 400（api_perf 判例）。
JSON 文件是唯一契约——门户不 import guojia_reconcile（依赖链隔离），
生成器换实现不影响出口。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(os.environ.get("AIOS_ROOT", "C:/aios"))
GUOJIA_DIR = REPO / "news_collector" / "data" / "guojia"
SUMMARY_JSON = GUOJIA_DIR / "summary.json"
VERDICTS_JSONL = GUOJIA_DIR / "verdicts.jsonl"

CST = timezone(timedelta(hours=8))


def _read_json(p: Path):
    if not p.exists():
        return None, None
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as e:  # noqa: BLE001
        return None, "bad json: %r" % e


def _count_lines(p: Path):
    try:
        with open(p, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def guojia_section() -> dict:
    """郭嘉对账快照投影（summary.json + 账本规模）。"""
    data, err = _read_json(SUMMARY_JSON)
    out = {"exists": data is not None, "source": SUMMARY_JSON.name}
    if err:
        out["error"] = err
        return out
    if data is None:
        out["hint"] = "由 guojia_reconcile.py 跑批产出（K-ORG-191）"
        return out
    for k in ("generated_at", "window_days", "overall", "markets",
              "pipeline"):
        if k in data:
            out[k] = data[k]
    if VERDICTS_JSONL.exists():
        out["verdict_lines"] = _count_lines(VERDICTS_JSONL)
        out["verdicts_mtime"] = datetime.fromtimestamp(
            VERDICTS_JSONL.stat().st_mtime, CST).strftime("%Y-%m-%d %H:%M:%S")
        out["age_hours"] = round(
            (time.time() - VERDICTS_JSONL.stat().st_mtime) / 3600.0, 1)
    return out


class Handler:
    def _guojia_data(self, qs):
        # 零参数出口：参数白名单空集，带任何参数走 400（api_perf 判例）
        if self._norm_qs(qs):
            self._json({"error": "本出口不接受任何参数"}, 400)
            return
        self._json({
            "endpoint": "/api/guojia/data",
            "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
            "guojia": guojia_section(),
        })
