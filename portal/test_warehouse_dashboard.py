"""验证真实渲染的数据口径及不可信内容转义，不访问生产数据。"""

import importlib.util
import json
from pathlib import Path
import re

import pytest


@pytest.fixture
def dashboard():
    path = Path(__file__).with_name("warehouse_dashboard.py")
    assert path.exists(), "缺少数据仓四视图渲染功能"
    spec = importlib.util.spec_from_file_location("warehouse_dashboard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report():
    return {
        "generated_at": "2026-10-09 02:50:33",
        "kpi": {"n_tables": 92, "total_mb": 12154},
        "gaps": [],
        "groups": [
            {
                "name": "TDX 远端仓",
                "kind": "tdx",
                "rows": [
                    {
                        "asset": "ts_daily",
                        "rows": "415 万",
                        "latest": "20261008",
                        "age_hours": None,
                        "size_mb": 100,
                        "extra": "同步完成",
                    }
                ],
            }
        ],
        "integrity": {
            "generated_at": "2026-10-09 02:45:00",
            "expected_latest": "20261008",
            "metrics": [
                {
                    "name": "tdx_sector_daily_daily_coverage",
                    "status": "FAIL",
                    "value": 51,
                    "details": {
                        "20261008": {
                            "rows": 1118,
                            "unique_codes": 1118,
                            "reference_unique_codes": 1119,
                            "missing_count": 1,
                        },
                        "_summary": {"value_unit": "相对近期最大断面的缺键总数"},
                    },
                },
                {
                    "name": "sector_definition_key_reconciliation",
                    "status": "FAIL",
                    "value": 51,
                    "details": {
                        "20261008": {
                            "defined": 1119,
                            "actual": 1118,
                            "missing": ["880843"],
                            "extra": [],
                        }
                    },
                },
                {
                    "name": "sector_minute_required_nulls",
                    "status": "FAIL",
                    "value": 14693,
                    "details": {"vol": 7347, "amount": 7346},
                },
                {
                    "name": "query_daily_history",
                    "status": "OK",
                    "value": 12,
                    "details": {"rows": 12},
                },
                {
                    "name": "ts_daily_daily_coverage",
                    "status": "OK",
                    "value": 0,
                    "details": {
                        "20261008": {
                            "rows": 5500,
                            "unique_codes": 5500,
                            "reference_unique_codes": 0,
                        }
                    },
                },
            ],
        },
    }


def test_health_counts_checks_without_adding_overlapping_key_values(dashboard):
    model = dashboard.build_model(report(), {"refreshing": False, "error": None})
    assert model["counts"] == {"FAIL": 3, "WARN": 0, "OK": 2, "INFO": 0, "UNKNOWN": 0}
    by_name = {m["name"]: m for m in model["metrics"]}
    assert by_name["sector_minute_required_nulls"]["unit"] == "空字段"
    assert by_name["query_daily_history"]["unit"] == "查询返回行"
    assert by_name["tdx_sector_daily_daily_coverage"]["value"] == 51


def test_coverage_uses_missing_keys_and_preserves_reference_policy(dashboard):
    model = dashboard.build_model(report(), {"refreshing": False, "error": None})
    sector = next(
        x for x in model["coverage"] if x["name"] == "tdx_sector_daily_daily_coverage"
    )
    day = sector["days"]["20261008"]
    assert day["missing_count"] == 1
    assert day["missing"] == ["880843"]
    assert day["status"] == "gap"
    assert sector["policy"] == "相对近期最大断面的缺键总数"


def test_zero_reference_is_unknown_instead_of_healthy(dashboard):
    model = dashboard.build_model(report(), {"refreshing": False, "error": None})
    stock = next(x for x in model["coverage"] if x["name"] == "ts_daily_daily_coverage")
    assert stock["days"]["20261008"]["status"] == "unknown"
    assert model["assets"][0]["freshness"] == "unknown"


def test_source_empty_day_is_labeled_pending_source_verification(dashboard):
    data = report()
    data["integrity"]["metrics"][0]["details"]["20261008"].update(
        rows=0,
        unique_codes=0,
        reference_unique_codes=1119,
        missing_count=1119,
        source_state="source_empty_pending",
    )
    model = dashboard.build_model(data, {"refreshing": False, "error": None})
    day = model["coverage"][0]["days"]["20261008"]
    assert day["source_empty_pending"] is True
    html = dashboard.render_dashboard(
        data,
        {"refreshing": False, "error": None},
        freshness_html="",
        graph_html="",
        nav="",
        base_css="",
        preview_js="",
    )
    assert "待来源核验" in html


def test_reference_count_difference_is_distinct_from_qualified_missing_keys(dashboard):
    data = report()
    data["integrity"]["metrics"].append(
        {
            "name": "ts_margin_detail_daily_coverage",
            "status": "OK",
            "value": 0,
            "details": {
                "20260930": {
                    "rows": 2,
                    "unique_codes": 2,
                    "reference_unique_codes": 3,
                    "missing_count": 0,
                    "missing": [],
                    "prelisting_excluded_count": 1,
                }
            },
        }
    )
    data["integrity"]["metrics"][-2]["details"]["20261008"].update(
        unique_codes=5500, reference_unique_codes=5561, missing_count=61
    )
    model = dashboard.build_model(data, {"refreshing": False, "error": None})
    by_name = {m["name"]: m for m in model["coverage"]}
    assert (
        by_name["ts_daily_daily_coverage"]["days"]["20261008"]["gap_type"]
        == "reference"
    )
    assert (
        by_name["ts_margin_detail_daily_coverage"]["days"]["20260930"]["qualified"]
        is True
    )


def test_definition_missing_keys_override_equal_count_coverage(dashboard):
    data = report()
    data["integrity"]["metrics"][0]["details"]["20261008"].update(
        unique_codes=2, reference_unique_codes=2, missing_count=0
    )
    data["integrity"]["metrics"][1]["details"]["20261008"].update(
        defined=3, actual=2, missing=["880843"]
    )
    model = dashboard.build_model(data, {"refreshing": False, "error": None})
    day = model["coverage"][0]["days"]["20261008"]
    assert day["status"] == "gap"
    assert day["missing_count"] == 1
    assert day["reference"] == 3


def test_minute_fallback_without_definition_does_not_claim_per_key_proof(dashboard):
    data = report()
    data["integrity"]["metrics"].append(
        {
            "name": "sector_minute_daily_coverage",
            "status": "FAIL",
            "value": 1,
            "details": {
                "20261008": {
                    "unique_codes": 2,
                    "reference_unique_codes": 3,
                    "missing_count": 1,
                    "missing": [],
                },
                "_summary": {"reference_source": "recent_max_unique_count"},
            },
        }
    )
    model = dashboard.build_model(data, {"refreshing": False, "error": None})
    assert model["coverage"][-1]["days"]["20261008"]["gap_type"] == "reference"


def test_missing_integrity_is_explicit_instead_of_zero_fail_healthy(dashboard):
    data = report()
    data.pop("integrity")
    model = dashboard.build_model(data, {"refreshing": False, "error": None})
    assert model["health_status"] == "unknown"
    assert model["metrics"] == []


def test_monitoring_rows_are_not_counted_again_as_warehouse_assets(dashboard):
    data = report()
    data["groups"].append(
        {
            "name": "新鲜度监控",
            "kind": "freshness",
            "rows": [
                {
                    "asset": "daily",
                    "rows": "lag 0天",
                    "latest": "20261008",
                    "age_hours": 0,
                }
            ],
        }
    )
    model = dashboard.build_model(data, {"refreshing": False, "error": None})
    assert len(model["assets"]) == 1


def test_refresh_failure_keeps_previous_scan_and_visible_warning(dashboard):
    model = dashboard.build_model(
        report(), {"refreshing": False, "error": "扫描读取失败"}
    )
    assert model["stale"] is True
    assert "扫描读取失败" in model["notice"]
    assert model["generated_at"] == "2026-10-09 02:50:33"


def test_render_does_not_allow_details_to_break_out_of_json_script(dashboard):
    data = report()
    attack = '</script><img src=x onerror="alert(1)">'
    data["integrity"]["metrics"][0]["details"]["error"] = attack
    data["groups"][0]["rows"][0]["asset"] = attack
    output = dashboard.render_dashboard(
        data,
        {"refreshing": False, "error": None},
        freshness_html="",
        graph_html="",
        nav="",
        base_css="",
        preview_js="",
    )
    assert attack not in output
    payload = re.search(
        r'<script type="application/json" id="warehouse-data">(.*?)</script>',
        output,
        re.S,
    )
    assert payload
    decoded = json.loads(payload.group(1))
    assert decoded["metrics"][0]["details"]["error"] == attack


def test_null_field_details_and_all_keys_survive_export_payload(dashboard):
    data = report()
    data["integrity"]["metrics"][1]["details"]["20261008"]["missing"] = [
        str(n) for n in range(4000)
    ]
    output = dashboard.render_dashboard(
        data,
        {"refreshing": False, "error": None},
        freshness_html="",
        graph_html="",
        nav="",
        base_css="",
        preview_js="",
    )
    payload = json.loads(
        re.search(r'id="warehouse-data">(.*?)</script>', output, re.S).group(1)
    )
    assert len(payload["metrics"][1]["details"]["20261008"]["missing"]) == 4000
    assert payload["metrics"][2]["details"] == {"vol": 7347, "amount": 7346}


def test_render_separates_freshness_from_integrity_and_labels_time(dashboard):
    data = report()
    data["freshness"] = {
        "counts": {"WARN": 1, "STALE": 1, "FAIL": 1, "SNAP": 1, "NA": 1}
    }
    data["groups"][0]["rows"][0].update(
        rows="未计量 (×1片)",
        latest_kind="file_mtime",
        business_date="20261008",
        measured_at="2026-10-09 11:30",
        extra="未登记（运行状态待核实）",
    )
    output = dashboard.render_dashboard(
        data, {}, freshness_html="", graph_html="", nav="", base_css="", preview_js=""
    )
    assert "新鲜度：WARN 1" in output
    assert "STALE 1" in output
    assert "SNAP 1" in output
    assert "文件修改时间" in output
    assert "业务日期：20261008" in output
    assert "未计量" in output
    assert dashboard.build_model(data, {})["counts"]["FAIL"] == 3


def test_business_monitoring_is_visible_and_escaped(dashboard):
    data = report()
    data["monitoring"] = {
        "nine_dim": {
            "status": "FAIL",
            "n_candidates": 218,
            "n_full": 0,
            "generated_at": "2026-10-09",
            "factors": {"eps": {"name": "盈利预期", "unjudgeable": 218}},
        },
        "graph_qa": {
            "status": "FAIL",
            "generated_at": "2026-10-08",
            "live_verified": False,
            "details": "<img onerror=evil>",
        },
        "graph_counts": {"status": "INFO", "generated_at": "2026-10-09"},
    }
    output = dashboard.render_dashboard(
        data, {}, freshness_html="", graph_html="", nav="", base_css="", preview_js=""
    )
    assert "完整评分 0 / 218" in output
    assert "盈利预期" in output
    assert "图谱 QA" in output
    assert "实时连接未核验" in output
    assert "<img onerror=evil>" not in output


def test_manifest_only_receipts_are_visible_without_inflating_physical_assets(
    dashboard,
):
    data = report()
    data["groups"].append(
        {
            "name": "manifest独立回执",
            "kind": "manifest_receipt",
            "rows": [
                {"asset": "anns_d_probe", "rows": "1.2 万", "extra": "去向待核验"}
            ],
        }
    )
    model = dashboard.build_model(data, {})
    assert len(model["assets"]) == 1
    assert len(model["manifest_receipts"]) == 1
    output = dashboard.render_dashboard(
        data, {}, freshness_html="", graph_html="", nav="", base_css="", preview_js=""
    )
    assert "anns_d_probe" in output
    assert "去向待核验" in output


def test_fifth_daily_panel_keeps_existing_views_and_accessible_controls(dashboard):
    output = dashboard.render_dashboard(
        report(),
        {},
        freshness_html="",
        graph_html="",
        nav="",
        base_css="",
        preview_js="",
    )
    assert re.findall(r'role="tab"', output) == ['role="tab"'] * 5
    for name in ("overview", "coverage", "gaps", "assets", "daily"):
        assert f'id="wh-{name}"' in output
    for label in (
        "浏览日期",
        "更新来源",
        "更新市场",
        "预览当前来源范围",
        "预览当天未完成项",
        "每日状态与来源 JSON",
    ):
        assert label in output
    assert '<dialog id="wh-daily-plan-dialog"' in output
    assert 'aria-labelledby="wh-daily-plan-title"' in output
    assert "wh-daily-progress" in output


def test_daily_snapshot_is_non_live_and_preserves_full_json_safely(dashboard):
    data = report()
    data["daily_snapshot"] = {
        "date": "2026-10-09",
        "assets": [
            {
                "asset_id": "</script><img src=x>",
                "data_status": "unknown",
                "update_enabled": False,
            }
        ],
        "runs": [],
    }
    output = dashboard.render_dashboard(
        data,
        {"preview": True},
        freshness_html="",
        graph_html="",
        nav="",
        base_css="",
        preview_js="",
    )
    assert "非实时快照" in output and "启动已禁用" in output
    assert "</script><img src=x>" not in output
    payload = json.loads(
        re.search(r'id="warehouse-data">(.*?)</script>', output, re.S).group(1)
    )
    assert payload["daily_read_only"] is True
    assert payload["daily_snapshot"]["assets"][0]["asset_id"] == "</script><img src=x>"


# ── DWH-105（2026-11-02）：data_date 显式 + 空结果三态 ──────────────────


class _Recorder:
    """模拟 portal_api Handler._json：记录 payload/status。"""

    def __init__(self):
        self.payload = None
        self.code = 200

    def _json(self, obj, code=200):
        self.payload = obj
        self.code = code


class _FakeRegistry:
    """api_dw_registry 的最小 DatasetRegistry 替身（不读生产数据）。"""

    def __init__(self, rows=None, dataset=None, sample=None):
        self.rows = rows or []
        self.dataset = dataset
        self.sample_data = sample
        self.inventory_path = "unused-inventory.json"

    def stats(self):
        return {"total": 314, "registered": 265, "exposed": 72,
                "by_domain": {"接口数据": 241}, "by_kind": {"dir": 258}}

    def list(self, **kwargs):
        return list(self.rows)

    def get(self, did):
        return self.dataset

    def sample(self, did, n=3):
        return dict(self.sample_data or {})


@pytest.fixture
def dw_api():
    path = Path(__file__).with_name("api_dw_registry.py")
    spec = importlib.util.spec_from_file_location("api_dw_registry_dwh105", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_dw(api, monkeypatch, fake, path, qs="", data_date="2026-11-02"):
    monkeypatch.setattr(api, "_REG", fake)
    monkeypatch.setattr(api, "_data_date", lambda: data_date)
    rec = _Recorder()
    api.handle_get(rec, path, qs)
    return rec


def test_dw_api_stats_carries_data_date_and_ok_state(dw_api, monkeypatch):
    rec = _call_dw(dw_api, monkeypatch, _FakeRegistry(), "/api/dw/stats")
    assert rec.code == 200
    assert rec.payload["ok"] is True
    assert rec.payload["data_date"] == "2026-11-02"
    assert rec.payload["state"] == "ok"
    assert rec.payload["total"] == 314
    assert rec.payload["registered"] == 265


def test_dw_api_empty_list_is_no_data_with_reason(dw_api, monkeypatch):
    rec = _call_dw(dw_api, monkeypatch, _FakeRegistry(rows=[]), "/api/dw/list",
                   "domain=无此域&limit=10")
    assert rec.code == 200
    assert rec.payload["count"] == 0
    assert rec.payload["state"] == "no_data"
    assert rec.payload["reason"].startswith("no_data:")
    assert "无此域" in rec.payload["reason"]
    assert rec.payload["data_date"] == "2026-11-02"


def test_dw_api_missing_dataset_is_not_found_with_reason(dw_api, monkeypatch):
    rec = _call_dw(dw_api, monkeypatch, _FakeRegistry(dataset=None),
                   "/api/dw/get?id=no_such")
    assert rec.code == 404
    assert rec.payload["ok"] is False
    assert rec.payload["state"] == "not_found"
    assert rec.payload["reason"].startswith("not_found:")
    assert rec.payload["data_date"] == "2026-11-02"


def test_dw_api_sample_missing_path_and_empty_rows_are_no_data(dw_api, monkeypatch):
    missing = _FakeRegistry(sample={"dataset_id": "x", "exists": False,
                                    "path": "C:/tmp/x"})
    rec = _call_dw(dw_api, monkeypatch, missing, "/api/dw/sample", "id=x&n=3")
    assert rec.code == 200
    assert rec.payload["state"] == "no_data"
    assert "路径不存在" in rec.payload["reason"]
    assert rec.payload["data_date"] == "2026-11-02"

    empty = _FakeRegistry(sample={"dataset_id": "y", "kind": "sqlite_table",
                                  "columns": ["a"], "sample": []})
    rec = _call_dw(dw_api, monkeypatch, empty, "/api/dw/sample", "id=y&n=3")
    assert rec.payload["state"] == "no_data"
    assert "样本为空" in rec.payload["reason"]


def test_dw_api_stale_data_date_marks_state_stale(dw_api, monkeypatch):
    fake = _FakeRegistry(rows=[{"dataset_id": "d1", "registered": True, "exposed": True}])
    rec = _call_dw(dw_api, monkeypatch, fake, "/api/dw/stats", "",
                   data_date="2026-10-01")
    assert rec.payload["state"] == "stale"
    assert "超过" in rec.payload["reason"]
    rec = _call_dw(dw_api, monkeypatch, fake, "/api/dw/list", "limit=10",
                   data_date="2026-10-01")
    assert rec.payload["count"] == 1
    assert rec.payload["state"] == "stale"


def test_dw_api_error_body_always_has_reason_and_data_date(dw_api, monkeypatch):
    rec = _call_dw(dw_api, monkeypatch, _FakeRegistry(), "/api/dw/search", "")
    assert rec.code == 400
    assert rec.payload["ok"] is False
    assert rec.payload["reason"].startswith("missing_param:")
    assert rec.payload["data_date"] == "2026-11-02"

    rec = _call_dw(dw_api, monkeypatch, _FakeRegistry(), "/api/dw/nope", "")
    assert rec.code == 404
    assert rec.payload["state"] == "not_found"
    assert rec.payload["reason"].startswith("not_found:")
    assert rec.payload["data_date"] == "2026-11-02"


def test_page_model_exposes_data_date_and_stale_state(dashboard):
    state = {"refreshing": False, "error": "扫描读取失败"}
    model = dashboard.build_model(report(), state)
    assert model["data_date"] == "2026-10-09"
    assert model["state"] == "stale"
    assert model["state_label"] == "数据可能过期"
    assert model["stale"] is True
    html = dashboard.render_dashboard(
        report(), state, freshness_html="", graph_html="", nav="",
        base_css="", preview_js="",
    )
    assert "数据时点：2026-10-09" in html
    assert "状态：stale" in html


def test_page_empty_scan_is_no_data_state(dashboard):
    model = dashboard.build_model({}, {})
    assert model["state"] == "no_data"
    assert model["data_date"] is None
    html = dashboard.render_dashboard(
        {}, {}, freshness_html="", graph_html="", nav="", base_css="", preview_js="",
    )
    assert "no_data：" in html
    assert "状态：no_data" in html

