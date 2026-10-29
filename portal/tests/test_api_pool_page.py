# -*- coding: utf-8 -*-
"""ACAD1 推荐池页面测试（2026-10-30 WO-INV-POOL-UI）。"""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PORTAL = REPO / "portal"
sys.path.insert(0, str(PORTAL))


def _load():
    spec = importlib.util.spec_from_file_location("pool_data_t", PORTAL / "pool_data.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_snapshot_merges_pool_and_review(tmp_path):
    m = _load()
    pool_dir = tmp_path / "pool"; pool_dir.mkdir()
    review_dir = tmp_path / "review"; review_dir.mkdir()
    pool = {"pool_id": "P1", "owner": "ACAD1", "data_date": "2026-10-30",
            "freshness_days": 0, "stale": False,
            "items": [{"code": "600519", "name": "茅台", "tier": "T2",
                       "sources": ["ninedim"], "data_date": "2026-10-30",
                       "risk_flags": []}]}
    review = {"summary": {"pool_total": 1, "reviewed": 1, "pending": 0,
                          "by_action": {"buy": 0, "watch": 1, "avoid": 0,
                                        "pending_analysis": 0}},
              "records": [{"code": "600519", "recommendation": "WATCH",
                           "confidence": 0.5, "price": 100.0,
                           "decision_status": "done", "action": "watch",
                           "analyses": {"T1": "多头弱", "T2": "看空"}}],
              "plan": {"selected": [], "watch": [{"code": "600519"}]}}
    (pool_dir / "2026-10-30.json").write_text(json.dumps(pool), encoding="utf-8")
    (review_dir / "2026-10-30.json").write_text(json.dumps(review), encoding="utf-8")
    m.POOL_DIR = str(pool_dir); m.REVIEW_DIR = str(review_dir)
    snap = m.pool_snapshot("2026-10-30")
    assert snap["ok"] and snap["owner"] == "ACAD1"
    assert snap["summary"]["reviewed"] == 1
    assert snap["items"][0]["recommendation"] == "WATCH"
    assert snap["items"][0]["analyses"]["T1"] == "多头弱"
    assert snap["dates"] == ["2026-10-30"]


def test_page_assets_and_routes():
    html = (PORTAL / "acad1_pool.html").read_text(encoding="utf-8")
    for x in ("/static/tokens.css", "/static/nav.js", "/static/shortscreen.js",
              "/api/acad1/pool", "全池复盘"):
        assert x in html
    api = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
    assert 'path == "/api/acad1/pool"' in api
