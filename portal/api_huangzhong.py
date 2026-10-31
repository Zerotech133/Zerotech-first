# -*- coding: utf-8 -*-
"""黄忠验收事件只读投影（WO-HZ-HERMES-PHASE2-20261031）。

数据面：`work/huangzhong/events.jsonl`（由 tools/huangzhong_daily.py 每次验收追加，
append-only）。本模块只读——无写入路径、无外部推送；消费方=8091
`GET /api/huangzhong/events` 与 Hermes 控制面（Tile/卡）。

红线（沿 api_execution R1 判例）：纯 stdlib、fail-visible（文件缺失/坏行
不编造事件，返回 ok=false 或 bad_lines 计数）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import parse_qs

REPO = Path(__file__).resolve().parent.parent
EVENTS_PATH = REPO / "work" / "huangzhong" / "events.jsonl"
DEFAULT_LIMIT = 20
MAX_LIMIT = 200


def _read_events(path: Path, limit: int = DEFAULT_LIMIT,
                 verdict: str | None = None) -> dict:
    rows: list[dict] = []
    bad = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if verdict and str(rec.get("verdict") or "").upper() != verdict.upper():
                    continue
                rows.append(rec)
    except FileNotFoundError:
        pass
    except OSError as e:
        return {"ok": False, "read_only": True, "events": [],
                "error": ("events.jsonl 读取失败: %s" % e)[:200]}
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    return {"ok": True, "read_only": True,
            "count": len(rows), "bad_lines": bad,
            "events": rows[-limit:],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def load_events(limit: int = DEFAULT_LIMIT, verdict: str | None = None) -> dict:
    return _read_events(EVENTS_PATH, limit=limit, verdict=verdict)


class Handler:
    """portal_api.Handler 多继承 mixin：GET /api/huangzhong/events 分发。

    只读端点（无写路径）；异常走 fail-visible JSON 而非 500（沿 api_execution 判例）。
    """

    def _huangzhong_events(self, qs):
        try:
            params = parse_qs(qs or "")
            limit = int((params.get("limit") or [DEFAULT_LIMIT])[0])
            verdict = (params.get("verdict") or [None])[0]
            self._json(load_events(limit=limit, verdict=verdict))
        except Exception as e:  # noqa: BLE001  fail-visible：恒 200 携错误体
            self._json({"ok": False, "read_only": True, "events": [],
                        "error": ("黄忠事件端点异常: %s" % e)[:300]})
