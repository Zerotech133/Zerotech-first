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


FAKE_MAPS = {
    "code2sec": {"600519": [{"sector": "酿酒行业", "idx": "881121"}]},
    "sec2mem": {"酿酒行业": {"idx": "881121",
                             "members": ["600519", "000858"]}},
    "name2code": {"贵州茅台": "600519"},
    "code2name": {"600519": "贵州茅台"},
    "rps": {"酿酒行业": 88.2},
}


@pytest.fixture
def no_sec(monkeypatch):
    """板块词典注入假映射（不触 tdx parquet）。"""
    monkeypatch.setattr(api_guojia, "_sector_maps", lambda: FAKE_MAPS)


def _row(rid, day="2026-10-13", scope="cn", verdict="hit", t1_pct=2.0,
         title=None, ents=None):
    return {"rowid": rid, "day": day, "ts": day + " 10:00:00",
            "scope": scope, "bias": "bull", "conf": 0.9,
            "event_type": None, "t1_day": day, "t1_pct": t1_pct,
            "verdict": verdict, "title": title,
            "ents": ents if ents is not None else []}


class TestDrill:
    """K-ORG-193 /api/guojia/drill：参数校验 400 + 三模式 + Handler 薄封装。"""

    def _ledger(self, repo, rows):
        api_guojia.GUOJIA_DIR.mkdir(parents=True, exist_ok=True)
        api_guojia.VERDICTS_JSONL.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False)
                      for r in rows) + "\n", encoding="utf-8")

    def test_no_params_400(self, repo):
        payload, code = api_guojia.drill_data({})
        assert code == 400 and "usage" in payload

    def test_unknown_param_400(self, repo):
        payload, code = api_guojia.drill_data({"foo": "1"})
        assert code == 400 and "foo" in payload["error"]

    def test_bad_scope_400(self, repo):
        _, code = api_guojia.drill_data({"scope": "mars"})
        assert code == 400

    def test_bad_limit_400(self, repo):
        _, c1 = api_guojia.drill_data({"limit": "0"})
        _, c2 = api_guojia.drill_data({"limit": "abc"})
        _, c3 = api_guojia.drill_data({"limit": "201"})
        assert c1 == c2 == c3 == 400

    def test_code_and_sector_conflict_400(self, repo):
        _, code = api_guojia.drill_data({"code": "600519",
                                         "sector": "酿酒行业"})
        assert code == 400

    def test_mode_a_sort_and_filter(self, repo):
        self._ledger(repo, [
            _row(1, verdict="hit", t1_pct=1.0),
            _row(2, verdict="hit", t1_pct=-3.0),
            _row(3, verdict="miss", t1_pct=9.0),
            _row(4, verdict="hit", t1_pct=None),
        ])
        payload, code = api_guojia.drill_data({"verdict": "hit"})
        assert code == 200 and payload["mode"] == "list"
        assert payload["total"] == 3 and len(payload["rows"]) == 3
        # |T+1 涨跌| 降序，无 pct 沉底
        assert [r["rowid"] for r in payload["rows"]] == [2, 1, 4]

    def test_mode_b_stock_by_code(self, repo, no_sec):
        self._ledger(repo, [
            _row(1, verdict="hit", t1_pct=2.0, title="贵州茅台利好",
                 ents=["贵州茅台"]),
            _row(2, verdict="miss", t1_pct=-1.0, title="贵州茅台回调",
                 ents=["贵州茅台"]),
            _row(3, verdict="hit", t1_pct=5.0, title="宁德时代利好",
                 ents=["宁德时代"]),
        ])
        payload, code = api_guojia.drill_data({"code": "600519"})
        assert code == 200 and payload["mode"] == "stock"
        st = payload["stock"]
        assert st["mapped"] is True and st["name"] == "贵州茅台"
        assert payload["stats"]["n_dir"] == 2
        assert payload["sectors"][0]["sector"] == "酿酒行业"
        assert payload["sectors"][0]["rps50"] == 88.2
        assert payload["total"] == 2

    def test_mode_b_entity_name_unmapped(self, repo, no_sec):
        self._ledger(repo, [
            _row(1, verdict="hit", t1_pct=1.5, title="特斯拉发布新品",
                 ents=["特斯拉"]),
        ])
        payload, _ = api_guojia.drill_data({"code": "特斯拉"})
        assert payload["stock"]["mapped"] is False
        assert payload["stock"]["name"] == "特斯拉"
        assert payload["total"] == 1 and payload["rows"][0]["rowid"] == 1

    def test_mode_c_sector(self, repo, no_sec):
        self._ledger(repo, [
            _row(1, verdict="hit", t1_pct=2.0, title="酿酒行业景气上行"),
            _row(2, verdict="miss", t1_pct=-0.5, title="白酒板块走弱"),
            _row(3, verdict="hit", t1_pct=3.0, title="白酒动销旺盛",
                 ents=["贵州茅台"]),   # K-ORG-194: ents 成分股实体反向命中
            _row(4, verdict="miss", t1_pct=-0.8, title="电池排产回暖",
                 ents=["宁德时代"]),   # 非成分股 → 不入
        ])
        payload, code = api_guojia.drill_data({"sector": "酿酒行业"})
        assert code == 200 and payload["mode"] == "sector"
        assert payload["sector"]["n_members"] == 2
        assert payload["members"][0] == {"code": "000858", "name": None}
        assert payload["total"] == 2
        # |T+1| 降序：row3(3.0, ents命中) → row1(2.0, 标题命中)
        assert [r["rowid"] for r in payload["rows"]] == [3, 1]

    def test_handler_thin_wrapper(self, repo, no_sec):
        from urllib.parse import parse_qs
        captured = {}

        class Stub:
            _norm_qs = staticmethod(
                lambda qs: parse_qs(qs, keep_blank_values=True))
            _json = staticmethod(lambda payload, code=200: captured.update(
                {"payload": payload, "code": code}))

        api_guojia.Handler._guojia_drill(
            Stub(), "code=600519&scope=cn&verdict=hit")
        assert captured["code"] == 200
        assert captured["payload"]["mode"] == "stock"
        assert captured["payload"]["filters"]["scope"] == "cn"
