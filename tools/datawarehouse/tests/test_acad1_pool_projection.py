# -*- coding: utf-8 -*-
"""ACAD1 只读 PG→JSON 投影脚本单测 — IFS-POOL。

覆盖验收：
1) build_projection 纯函数：单一 pool_date / 配额统计 / published_at / data_date / stale；
2) 契约校验 validate_projection（schema/source_pool/count/rows 字段/review_status）；
3) 原子写 write_projection（tmp+replace，无 .tmp 残留）；load_projection_file 往返；
4) CLI：--check 合法 rc0 / 非法 rc1；fetch 异常 rc2（fail-visible）；--json 输出契约。

全程离线：行数据合成，不连真实 PG、不写真实投影/运行时数据。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "datawarehouse"))

import acad1_pool_projection as projection  # noqa: E402


def _rows(n=4):
    markets = ["CN", "CN", "HK", "US"][:n]
    rows = []
    for index, market in enumerate(markets):
        rows.append(
            {
                "id": index + 1,
                "pool_date": "2026-11-02",
                "strategy_id": "v1-%s-strat-%d" % (market.lower(), index + 1),
                "code": "00%04d.SZ" % index,
                "name": "样本%d" % index,
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


def _doc(**overrides):
    doc = projection.build_projection(
        _rows(),
        ["2026-11-02", "2026-10-31"],
        review_verdict="观察",
        generated_at="2026-11-03 08:00:00",
    )
    doc.update(overrides)
    return doc


# ── 1. build_projection ─────────────────────────────────────────────
class TestBuildProjection:
    def test_contract_fields(self):
        doc = _doc()
        assert doc["schema"] == "acad1_pool_projection/v1"
        assert doc["source_pool"] == "pg"
        assert doc["source_table"] == "public.v2_acad1_pool"
        assert doc["source_mode"] == "projection"
        assert doc["readonly"] is True
        assert doc["pool_id"] == "ACAD1-POOL-PG-20261102"
        assert doc["pool_date"] == "2026-11-02"
        assert doc["data_date"] == "2026-11-02"
        assert doc["published_at"] == "2026-11-02T21:05:14+00:00"
        assert doc["review_status"] == "pool_reviewed"
        assert doc["count"] == 4
        assert doc["counts_by_market"] == {"CN": 2, "HK": 1, "US": 1}
        assert doc["stale"] is False
        assert "read-only projection" in doc["attribution"]

    def test_no_review_verdict_means_pool_published(self):
        doc = projection.build_projection(_rows(), review_verdict=None)
        assert doc["review_status"] == "pool_published"
        assert doc["review_verdict"] is None

    def test_empty_rows_rejected(self):
        with pytest.raises(ValueError, match="no rows"):
            projection.build_projection([])

    def test_multi_pool_date_rejected(self):
        rows = _rows()
        rows[0]["pool_date"] = "2026-11-01"
        with pytest.raises(ValueError, match="单一 pool_date"):
            projection.build_projection(rows)

    def test_stale_when_data_date_lag(self):
        rows = _rows()
        for row in rows:
            row["data_date"] = "2026-10-30"
        doc = projection.build_projection(rows)
        assert doc["data_date"] == "2026-10-30"
        assert doc["data_date_lag_days"] == 3
        assert doc["stale"] is True

    def test_scores_normalized_to_float(self):
        doc = _doc()
        assert all(isinstance(row["score"], float) for row in doc["rows"])
        assert all(row["pool_date"] == "2026-11-02" for row in doc["rows"])


# ── 2. validate_projection ──────────────────────────────────────────
class TestValidateProjection:
    def test_valid(self):
        assert projection.validate_projection(_doc()) == []

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda doc: doc.update({"schema": "wrong/v0"}),
            lambda doc: doc.update({"source_pool": "shadow"}),
            lambda doc: doc.update({"readonly": False}),
            lambda doc: doc.update({"count": 99}),
            lambda doc: doc.update({"review_status": "bogus"}),
            lambda doc: doc.update({"pool_id": ""}),
        ],
    )
    def test_invalid_mutations(self, mutate):
        doc = _doc()
        mutate(doc)
        errors = projection.validate_projection(doc)
        assert errors, "非法投影必须报错"

    def test_row_missing_field(self):
        doc = _doc()
        doc["rows"][0]["code"] = ""
        errors = projection.validate_projection(doc)
        assert any("rows[0].code" in item for item in errors)


# ── 3. 原子写 / 读取 ────────────────────────────────────────────────
class TestWriteAndLoad:
    def test_atomic_write_and_read_back(self, tmp_path):
        doc = _doc()
        target = tmp_path / "sub" / "acad1_pool_projection.json"
        written = projection.write_projection(doc, str(target))
        assert Path(written).is_file()
        assert not list(tmp_path.rglob("*.tmp")), "不得残留 .tmp 半文件"
        loaded = projection.load_projection_file(written)
        assert loaded["schema"] == projection.PROJECTION_SCHEMA
        assert loaded["count"] == 4

    def test_load_invalid_file_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"schema": "bad"}), encoding="utf-8")
        with pytest.raises(ValueError, match="投影契约非法"):
            projection.load_projection_file(str(path))


# ── 4. CLI ──────────────────────────────────────────────────────────
class TestCli:
    def test_check_valid(self, tmp_path, capsys):
        path = tmp_path / "p.json"
        projection.write_projection(_doc(), str(path))
        assert projection.main(["--check", str(path)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True and payload["count"] == 4

    def test_check_invalid(self, tmp_path, capsys):
        path = tmp_path / "p.json"
        path.write_text(json.dumps({"schema": "bad"}), encoding="utf-8")
        assert projection.main(["--check", str(path)]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False

    def test_json_mode_with_fetch_stub(self, capsys, monkeypatch):
        monkeypatch.setattr(
            projection,
            "fetch_rows",
            lambda pool_date=None, dsn_env="X": {
                "rows": _rows(),
                "dates": ["2026-11-02"],
                "review_verdict": None,
            },
        )
        assert projection.main(["--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == projection.PROJECTION_SCHEMA
        assert payload["count"] == 4

    def test_missing_connector_rc2(self, capsys, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("missing_connector: jev_common 不可用")

        monkeypatch.setattr(projection, "fetch_rows", boom)
        assert projection.main(["--json"]) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False and payload["fail_visible"] is True

    def test_out_writes_projection(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(
            projection,
            "fetch_rows",
            lambda pool_date=None, dsn_env="X": {
                "rows": _rows(),
                "dates": ["2026-11-02"],
                "review_verdict": "观察",
            },
        )
        out = tmp_path / "out" / "acad1_pool_projection.json"
        assert projection.main(["--out", str(out)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True and payload["count"] == 4
        assert out.is_file()
