# -*- coding: utf-8 -*-
"""同日池复用测试（WO-POOL-STABLE-20261030）。"""
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from agent_platform import recommendation_pool, run_shadow  # noqa: E402


def _pool(day, n=31):
    return {"pool_id": "ACAD1-REC-%s" % day.replace("-", ""),
            "items": [{"code": str(600000 + i), "name": "x"} for i in range(n)]}


def test_existing_pool_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(recommendation_pool, "OUT_DIR", str(tmp_path))
    (tmp_path / "2026-10-30.json").write_text(
        json.dumps(_pool("2026-10-30", 31), ensure_ascii=False), encoding="utf-8")
    p = run_shadow._existing_pool("2026-10-30")
    assert p is not None and len(p["items"]) == 31


def test_existing_pool_missing_or_wrong_day(tmp_path, monkeypatch):
    monkeypatch.setattr(recommendation_pool, "OUT_DIR", str(tmp_path))
    assert run_shadow._existing_pool("2026-10-30") is None
    (tmp_path / "2026-10-30.json").write_text(
        json.dumps(_pool("2026-10-29", 31), ensure_ascii=False), encoding="utf-8")
    assert run_shadow._existing_pool("2026-10-30") is None
