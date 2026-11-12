# -*- coding: utf-8 -*-
"""collect_section 单测：采集 SLA 出口投影（全 mock，不连服务、不读生产文件）。

覆盖：
  - 产物缺失 → exists=false + hint（fail-visible 合法空态，不是 500）
  - 坏 JSON   → exists=true + error（不吞错）
  - 正常投影  → 逐行字段白名单 + missing 只留 3 只样本（全量走 JSON 契约）
  - Handler 装配 → /api/daily/data 返回值含 collect 键

运行（仓库根）：
  "C:\\Program Files\\Python311\\python.exe" -m pytest portal/tests/test_api_daily_collect.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]
REPO = PORTAL.parent
for _p in (str(PORTAL), str(REPO / "jev-eval")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import api_daily_data as add  # noqa: E402


def _write(tmp_path, obj):
    p = tmp_path / "collect_sla.json"
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def test_missing_artifact_is_legal_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(add, "COLLECT_JSON", tmp_path / "nope.json")
    sec = add.collect_section()
    assert sec["exists"] is False
    assert "collect_sla.py" in (sec.get("hint") or "")
    assert "error" in sec and sec["error"] is None


def test_bad_json_reports_error(tmp_path, monkeypatch):
    p = tmp_path / "collect_sla.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(add, "COLLECT_JSON", p)
    sec = add.collect_section()
    assert sec["exists"] is True
    assert sec["error"]


def test_projection_whitelist_and_missing_truncated(tmp_path, monkeypatch):
    doc = {
        "generated_at": "G",
        "summary": {"verdict": "AMBER", "n_red": 0, "gap_total": 4, "gap_open": 1},
        "thresholds": {"amber_stale_hours": 26.0},
        "thresholds_source": "DEFAULT",
        "weekend_slack_hours": 0,
        "note": "N",
        "rows": [{
            "source": "xq", "label": "雪球timeline", "state": "GREEN",
            "last_ok_ts": "T", "stale_hours": 1.5, "covered": 9, "expected": 10,
            "gap": 1, "gap_open": 1, "gap_known": 0, "gap_pending_fix": 0,
            "consecutive_bad": 0, "empty_files": 0,
            "missing": ["1", "2", "3", "4", "5"], "missing_n": 5,
            "internal_only_field": "不该出现在出口",
            "note": "R",
        }],
    }
    monkeypatch.setattr(add, "COLLECT_JSON", _write(tmp_path, doc))
    sec = add.collect_section()
    assert sec["exists"] is True
    assert sec["summary"]["verdict"] == "AMBER"
    assert sec["thresholds_source"] == "DEFAULT"
    row = sec["rows"][0]
    assert row["missing_n"] == 5
    assert row["missing_sample"] == ["1", "2", "3"]      # 只留 3 只样本
    assert "missing" not in row                           # 全量不外泄到 UI
    assert "internal_only_field" not in row               # 白名单投影
    assert row["gap_open"] == 1 and row["label"] == "雪球timeline"


class _FakeHandler(add.Handler):
    """截获 _json 输出，其余节打桩（不碰 DB / 文件）。"""

    def __init__(self):
        self.out = None

    def _norm_qs(self, qs):
        return {}

    def _json(self, obj, code=200):
        self.out = (obj, code)


def test_handler_payload_contains_collect(tmp_path, monkeypatch):
    for name in ("dist_section", "bt_section", "impact_section",
                 "drift_section", "health_section", "sent_section",
                 "market_section"):
        monkeypatch.setattr(add, name, lambda: {"exists": False})
    monkeypatch.setattr(add, "COLLECT_JSON", _write(tmp_path, {"rows": [], "summary": {}}))
    h = _FakeHandler()
    h._daily_data("")
    body, code = h.out
    assert code == 200
    assert set(body) >= {"dist", "bt", "impact", "drift", "health",
                         "sent", "market", "collect"}
