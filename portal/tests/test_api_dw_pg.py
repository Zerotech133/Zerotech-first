# -*- coding: utf-8 -*-
"""DW API pg_table 支持测试（WO-DW-API-PG-20261104）。

全部离线：FakeRegistry 注入 `api_dw_registry._REG`，不读真实 inventory、不连 PG。
覆盖：stats v1 兼容 + stats_v2；list kind 过滤；get pg 元数据；sample 只读 PG/
connector_unavailable fail-visible；search 含 pg；query 对 pg 显式 not_supported +
拒绝任意 SQL；只读（无写调用）；route baseline 不变。
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "portal", ROOT / "tools" / "datawarehouse"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import api_dw_registry as DW  # noqa: E402
from dataset_registry import ConnectorUnavailable, QueryNotSupported  # noqa: E402

PG_ROW = {
    "dataset_id": "pg_public_v2_acad1_pool",
    "domain": "平台运行",
    "label": "public.v2_acad1_pool",
    "kind": "pg_table",
    "path": "pg@127.0.0.1:5446/zhice_os_v2",
    "connector": "pg@127.0.0.1:5446/zhice_os_v2",
    "physical": {"schema": "public", "table": "v2_acad1_pool"},
    "ref": "dataset/pg/v2_acad1_pool",
    "ref_aliases": ["v2_acad1_pool", "dataset/pg/v2_acad1_pool"],
    "owner": "DATA1",
    "registered": True,
    "exposed": False,
    "access": "read_only",
    "freshness": None,
    "row_estimate": None,
    "relkind": "",
    "status": "missing_dsn",
    "available": False,
    "unified_entry": True,
}
ART_ROW = {
    "dataset_id": "tmp_contract_demo",
    "domain": "平台运行",
    "label": "contract_demo",
    "kind": "contract_artifact",
    "path": "C:\\Temp\\contract_demo.json",
    "artifact_id": "contract_demo",
    "owner": "A0",
    "registered": True,
    "exposed": True,
    "status": "active",
    "available": True,
    "ref_aliases": ["contract_demo", "dataset/file/contract_demo"],
    "unified_entry": True,
}
FILE_ROWS = [
    {"dataset_id": "ts_daily", "domain": "量化", "label": "ts_daily", "kind": "sqlite_table",
     "path": "research/ts_daily.sqlite", "registered": True, "exposed": True,
     "access": "read_only", "unified_entry": True},
    {"dataset_id": "bars_1d", "domain": "量化", "label": "bars_1d", "kind": "parquet",
     "path": "quant_backtest/data/bars.parquet", "registered": True, "exposed": False,
     "access": "read_only", "unified_entry": True},
]


class FakeRegistry:
    def __init__(self):
        self.datasets = [dict(r) for r in FILE_ROWS]
        self.pg_datasets = [dict(PG_ROW)]
        self.contract_artifacts = [dict(ART_ROW)]
        self.connector_status = "missing_dsn"
        self.connector_error = ""
        self.calls = []
        self.sample_error = None
        self.sample_data = [[1, "600519.SH"]]
        self.stats_v2_error = None

    def stats(self):
        return {
            "inventory": "research/reports/dataset_inventory_demo.json",
            "total": 2,
            "registered": 2,
            "exposed": 1,
            "by_domain": {"量化": 2},
            "by_kind": {"sqlite_table": 1, "parquet": 1},
        }

    def stats_v2(self):
        if self.stats_v2_error:
            raise RuntimeError(self.stats_v2_error)
        return {
            "denominator_version": "v2",
            "generated_at": "2026-11-04T10:00:00",
            "connector_status": self.connector_status,
            "connector_error": self.connector_error,
            "file_inventory": {"total": 2, "registered": 2, "exposed": 1,
                               "by_kind": {"sqlite_table": 1, "parquet": 1}},
            "pg": {"total": 1, "available": 0, "registered": 1, "exposed": 0,
                   "connector_status": self.connector_status,
                   "by_status": {"missing_dsn": 1}},
            "contract_artifacts": {"total": 1, "active": 1, "missing": 0,
                                   "mismatch": 0, "by_status": {"active": 1}},
            "all": {"total": 3, "registered": 3, "exposed": 1},
        }

    def list(self, domain=None, q=None, registered=None, exposed=None,
             limit=50, kinds=None):
        self.calls.append(("list", kinds))
        if kinds is None:
            pool = list(self.datasets)
        elif kinds == "all":
            pool = self.datasets + self.pg_datasets + self.contract_artifacts
        else:
            wanted = {kinds} if isinstance(kinds, str) else set(kinds)
            pool = [d for d in (self.datasets + self.pg_datasets + self.contract_artifacts)
                    if d["kind"] in wanted]
        out = []
        ql = (q or "").lower()
        for d in pool:
            if domain and d["domain"] != domain:
                continue
            if registered is not None and d["registered"] != registered:
                continue
            if exposed is not None and d["exposed"] != exposed:
                continue
            hay = (str(d.get("label")) + " " + str(d.get("path")) + " " + d["dataset_id"]).lower()
            if ql and ql not in hay:
                continue
            out.append(d)
        return out[:limit]

    def get(self, dataset_id):
        for d in self.datasets + self.pg_datasets + self.contract_artifacts:
            if d["dataset_id"] == dataset_id:
                return d
        return None

    def resolve_ref(self, ref):
        if not ref:
            return None
        for d in self.pg_datasets + self.contract_artifacts:
            if ref in (d.get("ref_aliases") or []):
                return d
        return None

    def sample(self, dataset_id, n=3):
        self.calls.append(("sample", dataset_id, n))
        if self.get(dataset_id) is None:
            raise KeyError("未知 dataset_id: %s" % dataset_id)
        if self.sample_error is not None:
            raise self.sample_error
        return {
            "dataset_id": dataset_id,
            "kind": "pg_table",
            "connector": PG_ROW["connector"],
            "physical": dict(PG_ROW["physical"]),
            "columns": ["pool_id", "symbol"],
            "sample": self.sample_data,
            "row_estimate": None,
        }

    def query(self, dataset_id, where=None, order=None, limit=100, offset=0,
              since=None, columns=None, count=False):
        self.calls.append(("query", dataset_id))
        if dataset_id.startswith("pg_"):
            raise QueryNotSupported(
                "pg_table 载体 P1/P2 不支持行级 query（契约产物走 sample；PG 走 P3）",
                {"carrier": "pg_table", "hint": "sample() 可用"})
        return {"ok": True, "dataset_id": dataset_id, "state": "ok", "rows": []}


class FakeHandler:
    def __init__(self):
        self.out = []

    def _json(self, obj, status=200):
        self.out.append((status, obj))


def _get(path, qs=""):
    h = FakeHandler()
    DW.handle_get(h, path, qs)
    return h


def _last(h):
    assert h.out, "handler 未输出响应"
    return h.out[-1]


@pytest.fixture
def reg(monkeypatch):
    fake = FakeRegistry()
    monkeypatch.setattr(DW, "_REG", fake)
    monkeypatch.setattr(DW, "_data_date", lambda: "2026-11-04")
    monkeypatch.setattr(DW, "STALE_DAYS", 99999)
    return fake


# ── 1. /api/dw/stats：v1 兼容 + stats_v2 ────────────────────────────
class TestStats:
    def test_v1_fields_unchanged_and_stats_v2_added(self, reg):
        status, payload = _last(_get("/api/dw/stats"))
        assert status == 200 and payload["ok"] is True
        # v1 字段语义/数值不变
        for key in ("inventory", "total", "registered", "exposed", "by_domain", "by_kind"):
            assert payload[key] == reg.stats()[key]
        # v2 附加
        assert payload["stats_version"] == "v2"
        assert payload["stats_v2"]["pg"]["total"] == 1
        assert payload["stats_v2"]["pg"]["available"] == 0
        assert payload["stats_v2"]["contract_artifacts"]["active"] == 1
        assert payload["connector_status"] == "missing_dsn"
        assert payload["pg"]["connector_status"] == "missing_dsn"
        assert payload["contract_artifacts"]["missing"] == 0
        assert payload["state"] in ("ok", "stale")

    def test_stats_v2_failure_keeps_v1(self, reg):
        reg.stats_v2_error = "boom"
        status, payload = _last(_get("/api/dw/stats"))
        assert status == 200 and payload["ok"] is True
        assert payload["total"] == 2  # v1 不破坏
        assert payload["stats_version"] == "v1"
        assert payload["stats_v2"] is None
        assert "boom" in payload["stats_v2_error"]


# ── 2. /api/dw/list：kind 过滤 ──────────────────────────────────────
class TestListKind:
    def test_default_backward_compatible_files_only(self, reg):
        status, payload = _last(_get("/api/dw/list"))
        assert status == 200
        assert payload["count"] == 2
        assert {d["kind"] for d in payload["datasets"]} == {"sqlite_table", "parquet"}

    def test_kind_pg_table(self, reg):
        status, payload = _last(_get("/api/dw/list", "kind=pg_table"))
        assert status == 200
        assert payload["count"] == 1
        assert payload["kind"] == "pg_table"
        assert payload["datasets"][0]["dataset_id"] == "pg_public_v2_acad1_pool"
        assert payload["datasets"][0]["connector"]

    def test_kind_all_and_file_alias(self, reg):
        _, payload_all = _last(_get("/api/dw/list", "kind=all"))
        assert payload_all["count"] == 4  # 2 文件 + pg + 契约产物
        _, payload_file = _last(_get("/api/dw/list", "kind=file"))
        assert payload_file["count"] == 2
        assert {d["kind"] for d in payload_file["datasets"]} == {"sqlite_table", "parquet"}

    def test_kind_multi(self, reg):
        _, payload = _last(_get("/api/dw/list", "kind=pg_table,contract_artifact"))
        assert payload["count"] == 2
        assert {d["kind"] for d in payload["datasets"]} == {"pg_table", "contract_artifact"}

    def test_kind_empty_honest_reason(self, reg):
        _, payload = _last(_get("/api/dw/list", "kind=bogus_kind"))
        assert payload["count"] == 0 and payload["state"] == "no_data"
        assert "kind='bogus_kind'" in payload["reason"]


# ── 3. /api/dw/get：pg 元数据 ───────────────────────────────────────
class TestGetPg:
    def test_get_pg_metadata(self, reg):
        status, payload = _last(_get("/api/dw/get", "id=pg_public_v2_acad1_pool"))
        assert status == 200 and payload["ok"] is True
        d = payload["dataset"]
        assert d["kind"] == "pg_table"
        assert d["connector"] == PG_ROW["connector"]
        assert d["physical"] == {"schema": "public", "table": "v2_acad1_pool"}
        assert d["status"] == "missing_dsn"
        assert "v2_acad1_pool" in d["ref_aliases"]
        pg = payload["pg"]
        assert pg["connector"] == PG_ROW["connector"]
        assert pg["physical"]["table"] == "v2_acad1_pool"
        assert pg["ref"] == "dataset/pg/v2_acad1_pool"
        assert pg["ref_aliases"] == ["v2_acad1_pool", "dataset/pg/v2_acad1_pool"]
        assert pg["status"] == "missing_dsn"
        assert pg["available"] is False
        assert pg["connector_status"] == "missing_dsn"
        assert pg["read_only"] is True
        assert pg["query_supported"] is False
        assert pg["sample_supported"] is True

    def test_get_pg_by_alias(self, reg):
        status, payload = _last(_get("/api/dw/get", "id=dataset/pg/v2_acad1_pool"))
        assert status == 200
        assert payload["dataset"]["dataset_id"] == "pg_public_v2_acad1_pool"

    def test_get_unknown_404(self, reg):
        status, payload = _last(_get("/api/dw/get", "id=not_exist"))
        assert status == 404 and payload["state"] == "not_found"

    def test_get_file_dataset_has_no_pg_block(self, reg):
        status, payload = _last(_get("/api/dw/get", "id=ts_daily"))
        assert status == 200 and "pg" not in payload


# ── 4. /api/dw/sample：pg 只读 + connector_unavailable ──────────────
class TestSamplePg:
    def test_sample_pg_ok(self, reg):
        status, payload = _last(_get("/api/dw/sample", "id=pg_public_v2_acad1_pool&n=2"))
        assert status == 200 and payload["ok"] is True
        assert payload["kind"] == "pg_table"
        assert payload["columns"] == ["pool_id", "symbol"]
        assert payload["sample"] == [[1, "600519.SH"]]
        assert payload["connector"] == PG_ROW["connector"]
        assert payload["state"] in ("ok", "stale")

    def test_sample_pg_date_decimal_json_safe(self, reg):
        """PG 样本的 date/datetime/Decimal 必须转 JSON 安全值，不能再 503。"""
        reg.sample_data = [[date(2026, 11, 3), datetime(2026, 11, 3, 12, 0, 0),
                            Decimal("1.23")]]
        status, payload = _last(_get("/api/dw/sample", "id=pg_public_v2_acad1_pool&n=2"))
        assert status == 200 and payload["ok"] is True
        assert payload["sample"] == [["2026-11-03", "2026-11-03T12:00:00", "1.23"]]

    def test_sample_missing_dsn_explicit(self, reg):
        reg.sample_error = ConnectorUnavailable("DSN 未配置（缺 ZHICE_V2_DSN）",
                                                status="missing_dsn")
        status, payload = _last(_get("/api/dw/sample", "id=pg_public_v2_acad1_pool"))
        assert status == 503
        assert payload["ok"] is False
        assert payload["error"] == "connector_unavailable"
        assert payload["connector_status"] == "missing_dsn"
        assert payload["state"] == "connector_unavailable"
        assert payload["dataset_id"] == "pg_public_v2_acad1_pool"
        assert "DSN" in payload["reason"]
        assert "code" in payload or status == 503

    def test_sample_connector_unavailable_status(self, reg):
        reg.sample_error = ConnectorUnavailable("连接 5446 失败", status="connector_unavailable")
        status, payload = _last(_get("/api/dw/sample", "id=pg_public_v2_acad1_pool"))
        assert status == 503
        assert payload["error"] == "connector_unavailable"
        assert payload["connector_status"] == "connector_unavailable"

    def test_sample_unknown_404(self, reg):
        status, payload = _last(_get("/api/dw/sample", "id=nope"))
        assert status == 404 and payload["state"] == "not_found"


# ── 5. /api/dw/search：默认含 pg，kind 可收窄 ───────────────────────
class TestSearch:
    def test_search_finds_pg_by_default(self, reg):
        status, payload = _last(_get("/api/dw/search", "q=v2_acad1_pool"))
        assert status == 200
        assert payload["count"] == 1
        assert payload["datasets"][0]["kind"] == "pg_table"
        assert payload["kind"] == "all"

    def test_search_kind_narrow(self, reg):
        _, payload = _last(_get("/api/dw/search", "q=demo&kind=file"))
        assert payload["count"] == 0  # contract_demo 是契约产物，file 别名不含
        _, payload2 = _last(_get("/api/dw/search", "q=demo&kind=contract_artifact"))
        assert payload2["count"] == 1
        assert payload2["datasets"][0]["kind"] == "contract_artifact"

    def test_search_missing_q_400(self, reg):
        status, payload = _last(_get("/api/dw/search", ""))
        assert status == 400 and payload["ok"] is False


# ── 6. /api/dw/query：pg 显式 not_supported + 拒绝任意 SQL ──────────
class TestQueryPg:
    def test_query_pg_explicit_not_supported(self, reg):
        status, payload = _last(_get("/api/dw/query", "id=pg_public_v2_acad1_pool"))
        assert status == 409
        assert payload["ok"] is False
        assert payload["state"] == "not_supported"
        assert payload["detail"]["carrier"] == "pg_table"
        assert payload["carrier"] == "pg_table"
        assert payload["query_supported"] is False
        assert "sample" in payload["hint"]
        assert "rows" not in payload

    @pytest.mark.parametrize("raw_key", ["sql", "raw_sql", "statement", "query"])
    def test_query_rejects_arbitrary_sql(self, reg, raw_key):
        status, payload = _last(_get(
            "/api/dw/query", "id=pg_public_v2_acad1_pool&%s=SELECT+*+FROM+v2_acad1_pool" % raw_key))
        assert status == 400
        assert payload["ok"] is False
        assert "任意 SQL" in payload["reason"]
        assert ("query", "pg_public_v2_acad1_pool") not in reg.calls

    def test_post_query_pg_rejects_sql(self, reg):
        body = json.dumps({"id": "pg_public_v2_acad1_pool", "sql": "SELECT 1"}).encode()
        h = FakeHandler()
        DW.handle_post(h, "/api/dw/query", body)
        status, payload = h.out[-1]
        assert status == 400 and "任意 SQL" in payload["reason"]

    def test_query_sqlite_still_ok(self, reg):
        status, payload = _last(_get("/api/dw/query", "id=ts_daily&limit=2"))
        assert status == 200 and payload["ok"] is True


# ── 7. 只读 / route 不变 ────────────────────────────────────────────
class TestReadonlyAndRoute:
    def test_readonly_calls_only(self, reg):
        _get("/api/dw/stats")
        _get("/api/dw/list", "kind=pg_table")
        _get("/api/dw/get", "id=pg_public_v2_acad1_pool")
        _get("/api/dw/sample", "id=pg_public_v2_acad1_pool")
        _get("/api/dw/search", "q=v2")
        kinds = {c[0] for c in reg.calls}
        assert kinds <= {"list", "sample"}  # 无写调用（fake 也无写方法可调）

    def test_route_baseline_unchanged(self):
        baseline = json.loads(
            (ROOT / "tools" / "portal_route_baseline.json").read_text(encoding="utf-8"))
        sigs = {tuple(x) for x in baseline["signatures"]}
        assert ("GET", "prefix", "/api/dw/") in sigs
        assert ("POST", "prefix", "/api/dw/") in sigs
