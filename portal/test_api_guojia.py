# -*- coding: utf-8 -*-
"""郭嘉出口单测（K-ORG-191，仿 test_api_brief.py 判例，tmp 注入不读真实产物）。

运行：python -X utf8 -m pytest portal/test_api_guojia.py -q
"""

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import api_guojia  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """AIOS_ROOT 注入 tmp 仓（news_collector/data/guojia/ 空态起步）。"""
    monkeypatch.setenv("AIOS_ROOT", str(tmp_path))
    api_guojia.REPO = tmp_path
    api_guojia.GUOJIA_DIR = tmp_path / "news_collector" / "data" / "guojia"
    api_guojia.SUMMARY_JSON = api_guojia.GUOJIA_DIR / "summary.json"
    api_guojia.VERDICTS_JSONL = api_guojia.GUOJIA_DIR / "verdicts.jsonl"
    return tmp_path


def _summary():
    return {"generated_at": "2026-10-18T18:00:00+08:00", "window_days": 180,
            "overall": {"n_reconciled": 10, "n_hit": 6, "n_miss": 4,
                        "hit_rate": 0.6, "first_day": "2026-09-01",
                        "last_day": "2026-10-16"},
            "markets": {"cn": {"name": "A股", "hit": 6, "miss": 4,
                               "hit_rate": 0.6, "days": [], "rolling": []}},
            "pipeline": {"ran_at": "2026-10-18T18:00:00+08:00",
                         "counts": {"pending": 2}, "sources": {}}}


class TestGuojiaSection:
    def test_missing_fail_visible(self, repo):
        r = api_guojia.guojia_section()
        assert r["exists"] is False and "hint" in r and "error" not in r

    def test_bad_json_error_visible(self, repo):
        api_guojia.GUOJIA_DIR.mkdir(parents=True)
        api_guojia.SUMMARY_JSON.write_text("{broken", encoding="utf-8")
        r = api_guojia.guojia_section()
        assert r["exists"] is False and "bad json" in r["error"]

    def test_projection_and_ledger_stats(self, repo):
        api_guojia.GUOJIA_DIR.mkdir(parents=True)
        api_guojia.SUMMARY_JSON.write_text(
            json.dumps(_summary(), ensure_ascii=False), encoding="utf-8")
        api_guojia.VERDICTS_JSONL.write_text(
            "\n".join(["{}"] * 12) + "\n", encoding="utf-8")
        r = api_guojia.guojia_section()
        assert r["exists"] is True
        assert r["overall"]["hit_rate"] == 0.6
        assert r["verdict_lines"] == 12
        assert "verdicts_mtime" in r and "age_hours" in r
