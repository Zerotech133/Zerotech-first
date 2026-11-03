# -*- coding: utf-8 -*-
"""ACAD1 官方池 API（/api/acad1/pool 数据口径）单测 — IFS-POOL。

覆盖验收：
1) source_mode 三态：pg_official / projection / shadow；
2) 池数：pg 注入 30 行 → 30；projection 2 行 → 2；shadow 旧池 1 行 → 1；
3) 无 PG 时降级：pg 不可用/无行/异常 → projection → shadow（不假绿，degraded_reason 透传）；
4) 兼容旧字段 + 新增 source_mode/pool_id/pool_date/data_date/published_at/review_status；
5) 只读：不写任何文件（tmp 目录 sha 不变）。

全程离线：PG 用注入 pg_reader，投影/旧池用 tmp_path；不连真实 PG、不改真实 pool 数据。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "portal"))
sys.path.insert(0, str(ROOT / "tools" / "datawarehouse"))

import api_acad1_pool as api  # noqa: E402
import acad1_pool_projection as projection  # noqa: E402

REQUIRED_NEW_FIELDS = (
    "source_mode",
    "pool_id",
    "pool_date",
    "data_date",
    "published_at",
    "review_status",
)
COMPAT_FIELDS = (
    "ok",
    "mode",
    "trading_permitted",
    "generated_at",
    "date",
    "pool_id",
    "owner",
    "data_date",
    "freshness_days",
    "stale",
    "summary",
    "plan",
    "items",
    "dates",
)


def _pg_rows(n=30):
    rows = []
    markets = ["CN"] * 12 + ["HK"] * 10 + ["US"] * 6 + ["CRYPTO"] * 2
    if n != 30:
        markets = ["CN"] * n
    for index, market in enumerate(markets[:n]):
        rows.append(
            {
                "id": index + 1,
                "pool_date": "2026-11-02",
                "strategy_id": "v1-cn-strat-2",
                "code": "00%04d.SZ" % index,
                "name": "股票%d" % index,
                "market": market,
                "signal": "均值回归",
                "score": 60.0 + index,
                "selection_reason": "基础信号：买点",
                "selected_by": "ACAD1",
                "selected_at": "2026-11-02T21:05:14+00:00",
                "status": "selected",
                "data_date": "2026-11-02",
            }
        )
    return rows


def _pg_payload(rows=None, verdict="观察"):
    rows = rows or _pg_rows()
    return {
        "rows": rows,
        "dates": ["2026-11-02", "2026-10-31"],
        "pool_date": rows[0]["pool_date"],
        "published_at": "2026-11-02T21:05:14+00:00",
        "review_verdict": verdict,
    }


def _legacy():
    return {
        "ok": True,
        "mode": "acad1_pool_review",
        "trading_permitted": False,
        "generated_at": "2026-11-03 08:00:00",
        "date": "2026-10-30",
        "pool_id": "ACAD1-REC-20261030",
        "owner": "ACAD1",
        "data_date": "2026-10-30",
        "freshness_days": 0,
        "stale": False,
        "summary": {"pool_total": 1, "reviewed": 0, "pending": 1,
                    "by_action": {"buy": 0, "watch": 0, "avoid": 0,
                                  "pending_analysis": 1}},
        "plan": {"selected": 0, "watch": 0, "avoid": 0, "total_weight": None},
        "items": [{"code": "600519", "name": "贵州茅台", "action": "pending_analysis"}],
        "dates": ["2026-10-30"],
    }


def _projection_doc(rows=None):
    rows = rows or _pg_rows(2)
    return projection.build_projection(
        rows,
        ["2026-11-02", "2026-10-31"],
        review_verdict="观察",
        generated_at="2026-11-03 08:00:00",
    )


def _write_projection(tmp_path, doc):
    path = tmp_path / "acad1_pool_projection.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def _sha(paths):
    out = {}
    for path in paths:
        p = Path(path)
        out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else p.stat().st_size
    return out


# ── 1. 三态 source_mode ──────────────────────────────────────────────
class TestSourceModes:
    def test_pg_official(self):
        snap = api.load_pool_snapshot(
            pg_reader=lambda day: _pg_payload(),
            allow_projection=False,
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["source_mode"] == "pg_official"
        assert snap["official"] is True
        assert snap["source_pool"] == "pg"
        assert len(snap["items"]) == 30
        assert snap["pool_id"] == "ACAD1-POOL-PG-20261102"
        assert snap["pool_date"] == "2026-11-02"
        assert snap["data_date"] == "2026-11-02"
        assert snap["published_at"] == "2026-11-02T21:05:14+00:00"
        assert snap["review_status"] == "pool_reviewed"
        assert snap["degraded_reason"] is None
        assert snap["plan"]["selected"] == 30

    def test_projection_fallback(self, tmp_path):
        path = _write_projection(tmp_path, _projection_doc())
        snap = api.load_pool_snapshot(
            allow_pg=False,
            projection_file=str(path),
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["source_mode"] == "projection"
        assert snap["official"] is True
        assert snap["source_pool"] == "projection"
        assert len(snap["items"]) == 2
        assert snap["review_status"] == "pool_reviewed"
        assert snap["published_at"]

    def test_shadow_fallback_is_not_official(self):
        snap = api.load_pool_snapshot(
            allow_pg=False,
            allow_projection=False,
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["source_mode"] == "shadow"
        assert snap["official"] is False
        assert snap["source_pool"] == "shadow"
        assert snap["review_status"] == "shadow_only"
        assert snap["degraded_reason"]
        assert len(snap["items"]) == 1

    def test_all_modes_have_required_and_compat_fields(self, tmp_path):
        path = _write_projection(tmp_path, _projection_doc())
        snaps = [
            api.load_pool_snapshot(pg_reader=lambda day: _pg_payload(),
                                   allow_projection=False,
                                   legacy_loader=lambda day: _legacy()),
            api.load_pool_snapshot(allow_pg=False, projection_file=str(path),
                                   legacy_loader=lambda day: _legacy()),
            api.load_pool_snapshot(allow_pg=False, allow_projection=False,
                                   legacy_loader=lambda day: _legacy()),
        ]
        for snap in snaps:
            for key in REQUIRED_NEW_FIELDS + COMPAT_FIELDS:
                assert key in snap, "缺字段 %s（source_mode=%s）" % (key, snap["source_mode"])
            assert snap["source_mode"] in api.SOURCE_MODES
            assert snap["review_status"] in api.REVIEW_STATUSES
            assert snap["trading_permitted"] is False


# ── 2. 无 PG 降级链 ─────────────────────────────────────────────────
class TestDegradation:
    def test_pg_no_rows_falls_to_projection(self, tmp_path):
        path = _write_projection(tmp_path, _projection_doc())
        snap = api.load_pool_snapshot(
            pg_reader=lambda day: {},
            projection_file=str(path),
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["source_mode"] == "projection"

    def test_pg_exception_falls_to_shadow(self, tmp_path):
        def boom(day):
            raise RuntimeError("pg down")

        snap = api.load_pool_snapshot(
            pg_reader=boom,
            projection_file=str(tmp_path / "missing.json"),
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["source_mode"] == "shadow"
        assert "pg_unavailable" in (snap["degraded_reason"] or "")
        assert "projection_missing" in (snap["degraded_reason"] or "")

    def test_pg_disabled_env_skips_reader(self, tmp_path, monkeypatch):
        called = {"n": 0}

        def reader(day):
            called["n"] += 1
            return _pg_payload()

        monkeypatch.setenv(api.PG_DISABLE_ENV, "1")
        snap = api.load_pool_snapshot(
            pg_reader=reader,
            projection_file=str(tmp_path / "missing.json"),
            legacy_loader=lambda day: _legacy(),
        )
        assert called["n"] == 0
        assert snap["source_mode"] == "shadow"
        assert "pg_disabled_by_env" in (snap["degraded_reason"] or "")

    def test_invalid_projection_ignored(self, tmp_path):
        bad = _projection_doc()
        bad["schema"] = "wrong/v0"
        path = _write_projection(tmp_path, bad)
        snap = api.load_pool_snapshot(
            allow_pg=False,
            projection_file=str(path),
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["source_mode"] == "shadow"
        assert "projection_invalid" in (snap["degraded_reason"] or "")

    def test_day_filter_mismatch_degrades(self, tmp_path):
        path = _write_projection(tmp_path, _projection_doc())
        snap = api.load_pool_snapshot(
            day="2026-10-30",
            allow_pg=False,
            projection_file=str(path),
            legacy_loader=lambda day: _legacy(),
        )
        # 投影是 11-02，day=10-30 不匹配 → shadow（旧池 10-30 有数据）
        assert snap["source_mode"] == "shadow"
        assert snap["date"] == "2026-10-30"

    def test_no_source_at_all(self):
        snap = api.load_pool_snapshot(
            allow_pg=False,
            allow_projection=False,
            legacy_loader=lambda day: None,
        )
        assert snap["ok"] is False
        assert snap["review_status"] == "unavailable"
        assert snap["items"] == []


# ── 3. 池数与兼容字段 ───────────────────────────────────────────────
class TestPoolCounts:
    def test_pg_items_mapping(self):
        snap = api.load_pool_snapshot(
            pg_reader=lambda day: _pg_payload(_pg_rows()),
            allow_projection=False,
            legacy_loader=lambda day: _legacy(),
        )
        first = snap["items"][0]
        for key in ("code", "name", "market", "strategy_id", "signal", "score",
                    "selection_reason", "action", "pool_date", "status"):
            assert key in first
        assert snap["summary"]["pool_total"] == 30
        assert snap["summary"]["by_market"] == {"CN": 12, "HK": 10, "US": 6, "CRYPTO": 2}

    def test_legacy_shadow_fields_kept(self):
        snap = api.load_pool_snapshot(
            allow_pg=False, allow_projection=False,
            legacy_loader=lambda day: _legacy(),
        )
        assert snap["mode"] == "acad1_pool_review"
        assert snap["owner"] == "ACAD1"
        assert snap["summary"]["pool_total"] == 1
        assert snap["items"][0]["name"] == "贵州茅台"
        assert snap["dates"] == ["2026-10-30"]

    def test_read_only_no_writes(self, tmp_path):
        legacy = _legacy()
        projection_path = _write_projection(tmp_path, _projection_doc())
        legacy_file = tmp_path / "legacy.json"
        legacy_file.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        before = _sha([projection_path, legacy_file])

        api.load_pool_snapshot(allow_pg=False, allow_projection=False,
                               legacy_loader=lambda day: legacy)
        api.load_pool_snapshot(allow_pg=False, projection_file=str(projection_path),
                               legacy_loader=lambda day: legacy)
        api.load_pool_snapshot(pg_reader=lambda day: _pg_payload(),
                               allow_projection=False,
                               legacy_loader=lambda day: legacy)
        after = _sha([projection_path, legacy_file])
        assert before == after


# ── 4. 投影契约与 CLI 自检 ──────────────────────────────────────────
class TestProjectionContract:
    def test_projection_validate_consistent_with_api(self):
        doc = _projection_doc()
        assert projection.validate_projection(doc) == []
        assert api.validate_projection(doc) == []

    def test_projection_load_file(self, tmp_path):
        path = _write_projection(tmp_path, _projection_doc())
        doc, errors = api.load_projection(str(path))
        assert errors == [] and doc["schema"] == api.PROJECTION_SCHEMA

    def test_api_cli_json(self, capsys, tmp_path, monkeypatch):
        path = _write_projection(tmp_path, _projection_doc())
        monkeypatch.setenv(api.PROJECTION_ENV, str(path))
        rc = api.main(["--json", "--no-pg"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["source_mode"] == "projection"
        assert payload["official"] is True
