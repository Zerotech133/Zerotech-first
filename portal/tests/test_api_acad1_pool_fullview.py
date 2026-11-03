# -*- coding: utf-8 -*-
"""ACAD1 全池 30 只读聚合视图（?view=full）单测 — ACAD1-FULLVIEW（2026-11-04）。

覆盖验收：
1) `view=full`：每市场最新 pool_date 聚合 → 30（CN12/HK10/US6/CRYPTO2），
   source_mode=pg_official_aggregated、official=true、pool_id=ACAD1-POOL-PG-FULL-<maxdate>、
   pool_dates、trading_permitted=false；
2) 默认行为不变：不带 view / view=single 仍为单日视图（当前 12，source_mode=pg_official）；
3) 缺 PG 降级：view=full 不伪造 30，降级单日并显式 degraded_reason；
4) pool_data query 解析：view=full 透传、非法值回落 single；
5) 只读：不写任何文件；页面开关存在且默认单日。

全程离线：PG 用注入 reader，投影/旧池用 tmp_path；不连真实 PG、不改真实数据。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PORTAL = REPO / "portal"
sys.path.insert(0, str(PORTAL))

import api_acad1_pool as api  # noqa: E402
import pool_data  # noqa: E402

MARKET_DATES = {"CN": "2026-11-03", "HK": "2026-10-30",
                "US": "2026-10-29", "CRYPTO": "2026-10-31"}
QUOTAS = {"CN": 12, "HK": 10, "US": 6, "CRYPTO": 2}


# ── 合成数据 ──────────────────────────────────────────────────────────
def _full_rows():
    rows = []
    for market, count in QUOTAS.items():
        for index in range(count):
            rows.append({
                "id": len(rows) + 1,
                "pool_date": MARKET_DATES[market],
                "strategy_id": "v1-%s-strat-%d" % (market.lower(), index % 2 + 1),
                "code": "%s%03d" % (market, index),
                "name": "%s样本%d" % (market, index),
                "market": market,
                "signal": "均值回归",
                "score": 60.0 + index,
                "selection_reason": "基础信号：买点",
                "selected_by": "ACAD1",
                "selected_at": "2026-11-03T12:51:29.630818+00:00",
                "status": "selected",
                "data_date": MARKET_DATES[market],
            })
    return rows


def _single_rows():
    return [row for row in _full_rows() if row["market"] == "CN"]


def _full_payload():
    return {
        "rows": _full_rows(),
        "dates": ["2026-11-03", "2026-10-31", "2026-10-30", "2026-10-29"],
        "pool_date": "2026-11-03",
        "pool_dates": dict(MARKET_DATES),
        "published_at": "2026-11-03T12:51:29.630818+00:00",
        "review_verdict": "观察",
        "aggregated": True,
    }


def _single_payload():
    return {
        "rows": _single_rows(),
        "dates": ["2026-11-03"],
        "pool_date": "2026-11-03",
        "published_at": "2026-11-03T12:51:29.630818+00:00",
        "review_verdict": "观察",
    }


def _legacy():
    return {
        "ok": True,
        "mode": "acad1_pool_review",
        "date": "2026-10-30",
        "pool_id": "ACAD1-REC-20261030",
        "owner": "ACAD1",
        "data_date": "2026-10-30",
        "freshness_days": 0,
        "stale": False,
        "summary": {"pool_total": 1, "reviewed": 0, "pending": 1,
                    "by_action": {"buy": 0, "watch": 0, "avoid": 0,
                                  "pending_analysis": 1}},
        "plan": {"selected": [], "watch": [], "avoid": [], "total_weight": None},
        "items": [{"code": "600519", "name": "贵州茅台", "action": "pending_analysis"}],
        "dates": ["2026-10-30"],
    }


def _projection_doc():
    rows = _single_rows()
    return {
        "schema": api.PROJECTION_SCHEMA,
        "source_pool": "pg",
        "source_mode": "projection",
        "readonly": True,
        "generated_at": "2026-11-04 02:00:00",
        "pool_id": "ACAD1-POOL-PG-20261103",
        "pool_date": "2026-11-03",
        "data_date": "2026-11-03",
        "published_at": "2026-11-03T12:51:29+00:00",
        "review_status": "pool_reviewed",
        "stale": False,
        "count": len(rows),
        "rows": rows,
        "dates": ["2026-11-03"],
    }


def _full_loader(payload=None):
    data = payload or _full_payload()
    return lambda day=None: data


def _sha(paths):
    out = {}
    for path in paths:
        item = Path(path)
        out[str(item)] = (hashlib.sha256(item.read_bytes()).hexdigest()
                          if item.is_file() else "dir:" + str(len(list(item.iterdir()))))
    return out


# ── 1. view=full 聚合（30） ──────────────────────────────────────────
class TestFullView:
    def test_aggregate_30_with_required_fields(self):
        snap = api.load_pool_snapshot(
            view="full",
            pg_full_reader=_full_loader(),
            allow_projection=False,
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["ok"] is True
        assert snap["view"] == "full"
        assert snap["source_mode"] == "pg_official_aggregated"
        assert snap["source_mode"] in api.SOURCE_MODES
        assert snap["source_mode"] in api.OFFICIAL_SOURCE_MODES
        assert snap["official"] is True
        assert snap["trading_permitted"] is False
        assert snap["pool_id"] == "ACAD1-POOL-PG-FULL-20261103"
        assert snap["pool_date"] == "2026-11-03"
        assert snap["pool_dates"] == MARKET_DATES
        assert snap["count"] == 30
        assert len(snap["items"]) == 30
        assert snap["plan"]["selected"] == 30
        assert snap["summary"]["by_market"] == QUOTAS
        assert snap["review_status"] == "pool_reviewed"
        assert snap["read_semantics"] == "market_latest_aggregated"
        assert snap["stale"] is True  # 跨市场数据日不同 → 显式 stale（不伪造新鲜）
        assert snap["freshness_by_market"] == {"CN": 0, "HK": 4, "US": 5, "CRYPTO": 3}

    def test_market_quotas_exact(self):
        snap = api.load_pool_snapshot(
            view="full", pg_full_reader=_full_loader(),
            allow_projection=False, legacy_loader=lambda day: _legacy(),
        )
        counts = {}
        for item in snap["items"]:
            counts[item["market"]] = counts.get(item["market"], 0) + 1
        assert counts == QUOTAS
        per_market_dates = {item["market"]: item["pool_date"] for item in snap["items"]}
        assert per_market_dates == MARKET_DATES

    def test_payload_missing_pool_dates_degrades(self):
        payload = _full_payload()
        payload.pop("pool_dates")
        snap = api.load_pool_snapshot(
            view="full", pg_full_reader=_full_loader(payload),
            allow_projection=False, legacy_loader=lambda day: _legacy(),
        )
        # 缺 pool_dates 不是聚合证据 → 不冒充 aggregated，降级单日
        assert snap["source_mode"] != "pg_official_aggregated"
        assert snap["degraded_reason"].startswith("full_view_requires_pg_aggregation")


# ── 2. 默认单日视图不变（12） ────────────────────────────────────────
class TestDefaultSingleView:
    def test_default_is_single_12(self):
        snap = api.load_pool_snapshot(
            pg_reader=lambda day: _single_payload(),
            allow_projection=False,
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["view"] == "single"
        assert snap["source_mode"] == "pg_official"
        assert snap["official"] is True
        assert snap["count"] == 12
        assert len(snap["items"]) == 12
        assert snap["pool_id"] == "ACAD1-POOL-PG-20261103"

    def test_explicit_single_same_as_default(self):
        default = api.load_pool_snapshot(
            pg_reader=lambda day: _single_payload(),
            allow_projection=False, legacy_loader=lambda day: _legacy())
        explicit = api.load_pool_snapshot(
            view="single", pg_reader=lambda day: _single_payload(),
            allow_projection=False, legacy_loader=lambda day: _legacy())
        assert explicit["view"] == "single"
        assert explicit["source_mode"] == default["source_mode"] == "pg_official"
        assert len(explicit["items"]) == len(default["items"]) == 12

    def test_invalid_view_raises(self):
        with pytest.raises(ValueError, match="未知 view"):
            api.load_pool_snapshot(view="bogus")

    def test_pool_data_default_query_keeps_single(self, monkeypatch):
        captured = {}

        def fake(day=None, legacy_loader=None, allow_pg=True,
                 allow_projection=True, view="single"):
            captured["view"] = view
            return {"ok": True, "view": view}

        monkeypatch.setattr(pool_data._api, "load_pool_snapshot", fake)
        snap = pool_data.pool_snapshot(query=None)
        assert snap["view"] == "single"
        assert captured["view"] == "single"


# ── 3. 缺 PG 降级（不伪造 30） ───────────────────────────────────────
class TestDegradation:
    def test_full_without_pg_degrades_shadow(self):
        snap = api.load_pool_snapshot(
            view="full", allow_pg=False, allow_projection=False,
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["view"] == "full"
        assert snap["source_mode"] == "shadow"
        assert snap["official"] is False
        assert len(snap["items"]) == 1  # 不伪造 30
        assert snap["degraded_reason"].startswith("full_view_requires_pg_aggregation")

    def test_full_without_pg_projection_fallback_not_30(self, tmp_path):
        path = tmp_path / "projection.json"
        path.write_text(json.dumps(_projection_doc(), ensure_ascii=False), encoding="utf-8")
        snap = api.load_pool_snapshot(
            view="full", allow_pg=False, projection_file=str(path),
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["view"] == "full"
        assert snap["source_mode"] == "projection"
        assert snap["count"] == 12
        assert snap["degraded_reason"].startswith("full_view_requires_pg_aggregation")

    def test_full_pg_raises_degrades_to_single(self):
        def boom(day=None):
            raise RuntimeError("pg down")

        snap = api.load_pool_snapshot(
            view="full", pg_full_reader=boom,
            pg_reader=lambda day: _single_payload(),
            allow_projection=False,
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["view"] == "full"
        # 聚合失败 → 降级单日官方视图（12），不伪造 30
        assert snap["source_mode"] == "pg_official"
        assert snap["count"] == 12
        assert "pg_unavailable" in snap["degraded_reason"]
        assert snap["degraded_reason"].startswith("full_view_requires_pg_aggregation")

    def test_full_pg_and_single_unavailable_shadow(self):
        def boom(day=None):
            raise RuntimeError("pg down")

        snap = api.load_pool_snapshot(
            view="full", pg_full_reader=boom, pg_reader=boom,
            allow_projection=False,
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["view"] == "full"
        assert snap["source_mode"] == "shadow"
        assert len(snap["items"]) == 1
        assert "pg_unavailable" in snap["degraded_reason"]

    def test_no_source_at_all_full(self):
        snap = api.load_pool_snapshot(
            view="full", allow_pg=False, allow_projection=False,
            legacy_loader=lambda day: None,
        )
        assert snap["ok"] is False
        assert snap["review_status"] == "unavailable"
        assert snap["items"] == []


# ── 4. pool_data query 解析/透传 ─────────────────────────────────────
class TestQueryParsing:
    @pytest.mark.parametrize(
        "query,expected",
        [
            (None, "single"),
            ("", "single"),
            ("view=single", "single"),
            ("view=full", "full"),
            ("view=FULL", "full"),
            ("x=1&view=full", "full"),
            ("view=bogus", "single"),
            ("view=", "single"),
            ({"view": ["full"]}, "full"),
        ],
    )
    def test_view_from_query(self, query, expected):
        assert pool_data.view_from_query(query) == expected

    def test_pool_snapshot_passes_view_to_api(self, monkeypatch):
        calls = {}

        def fake(day=None, legacy_loader=None, allow_pg=True,
                 allow_projection=True, view="single"):
            calls["view"] = view
            calls["allow_pg"] = allow_pg
            return {"ok": True, "view": view}

        monkeypatch.setattr(pool_data._api, "load_pool_snapshot", fake)
        assert pool_data.pool_snapshot(query="view=full")["view"] == "full"
        assert calls["view"] == "full"
        pool_data.pool_snapshot(query="view=bogus")
        assert calls["view"] == "single"


# ── 5. 只读 / 页面开关 ──────────────────────────────────────────────
class TestReadOnlyAndPage:
    def test_read_only_no_writes(self, tmp_path):
        path = tmp_path / "projection.json"
        path.write_text(json.dumps(_projection_doc(), ensure_ascii=False), encoding="utf-8")
        legacy_file = tmp_path / "legacy.json"
        legacy_file.write_text(json.dumps(_legacy(), ensure_ascii=False), encoding="utf-8")
        before = _sha([path, legacy_file])
        api.load_pool_snapshot(view="full", pg_full_reader=_full_loader(),
                               allow_projection=False, legacy_loader=lambda day: _legacy())
        api.load_pool_snapshot(pg_reader=lambda day: _single_payload(),
                               allow_projection=False, legacy_loader=lambda day: _legacy())
        api.load_pool_snapshot(view="full", allow_pg=False, projection_file=str(path),
                               legacy_loader=lambda day: _legacy())
        assert _sha([path, legacy_file]) == before

    def test_page_has_toggle(self):
        html = (PORTAL / "acad1_pool.html").read_text(encoding="utf-8")
        assert "view=full" in html
        assert "全池30" in html
        assert "单日视图" in html
        assert "pg_official_aggregated" in html
        assert "/api/acad1/pool" in html
        # 默认仍是单日视图（按钮默认 single，不自动请求 full）
        assert "let currentView='single'" in html

    def test_portal_api_route_passes_query(self):
        api_src = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
        assert "pool_snapshot(query=" in api_src


# ── 6. CLI ──────────────────────────────────────────────────────────
class TestCli:
    def test_cli_full_json(self, capsys, monkeypatch):
        monkeypatch.setattr(api, "default_pg_reader_full", lambda day=None: _full_payload())
        rc = api.main(["--json", "--view", "full"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["view"] == "full"
        assert payload["source_mode"] == "pg_official_aggregated"
        assert payload["count"] == 30
        assert payload["pool_id"] == "ACAD1-POOL-PG-FULL-20261103"

    def test_cli_default_single(self, capsys, monkeypatch):
        monkeypatch.setattr(api, "default_pg_reader", lambda day=None: _single_payload())
        rc = api.main(["--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["view"] == "single"
        assert payload["count"] == 12

    def test_cli_human_full(self, capsys, monkeypatch):
        monkeypatch.setattr(api, "default_pg_reader_full", lambda day=None: _full_payload())
        assert api.main(["--view", "full"]) == 0
        out = capsys.readouterr().out
        assert "view=full" in out
        assert "pool_dates" in out
