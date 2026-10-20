"""WO-QT-20261020-044：可选共振字段覆盖统计测试（纯内存、离线）。"""
from copy import deepcopy
import json

import pytest

from strategy_layer.adapters.chan_monitor import _RESONANCE_FIELDS as _ADAPTER_FIELDS
from strategy_layer.r0_optional_coverage import _RESONANCE_FIELDS, coverage_report


def test_resonance_fields_mirror_chan_monitor():
    """P1 同步守卫：chan_monitor 字段表变更时本模块必须显式跟随，
    否则覆盖统计静默漏统。"""
    assert _RESONANCE_FIELDS == _ADAPTER_FIELDS == ("mbei", "bei", "bei2", "vv")


def _row(code="002633.SZ", **over):
    row = {"market": "CN", "code": code, "type": "2买", "dt": "2026-10-18",
           "detect_date": "2026-10-19", "price": 10.0,
           "mbei": None, "bei": None, "bei2": None, "vv": True}
    row.update(over)
    return row


def test_four_states_counted_per_field():
    rows = [_row("002633.SZ", mbei=True),
            _row("002634.SZ", bei=False),
            _row("002635.SZ"),
            dict(_row("002636.SZ"))]
    del rows[3]["vv"]
    report = coverage_report(rows)
    assert report["status"] == "OK"
    assert report["scope"] == "STATISTICS_ONLY_NOT_EVIDENCE"
    assert report["anomalies"] == []
    assert report["zero_sample"] is False
    assert report["total_rows"] == 4
    f = report["fields"]["mbei"]
    assert f["present_true"] == 1 and f["present_null"] == 3
    assert f["coverage"] == 0.25
    b = report["fields"]["bei"]
    assert b["present_false"] == 1 and b["coverage"] == 0.25
    v = report["fields"]["vv"]
    assert v["present_missing"] == 1 and v["present_true"] == 3
    assert v["coverage"] == 0.75


def test_empty_rows_zero_sample_never_fabricates_coverage():
    report = coverage_report([])
    assert report["status"] == "OK"
    assert report["zero_sample"] is True
    assert report["total_rows"] == 0
    for field in ("mbei", "bei", "bei2", "vv"):
        assert report["fields"][field]["coverage"] is None
        assert report["fields"][field]["total"] == 0


@pytest.mark.parametrize("bad", [None, "rows", 42, {"0": {}}, (("mbei", True),)])
def test_non_list_input_rejected(bad):
    report = coverage_report(bad)
    assert report["status"] == "REJECTED"
    assert report["scope"] == "STATISTICS_ONLY_NOT_EVIDENCE"
    assert report["zero_sample"] is None and report["total_rows"] is None, \
        "拒绝结果不得被误读为空批"
    assert report["fields"] is None and report["anomalies"] is None


def test_non_dict_element_rejected_without_silent_skip():
    report = coverage_report([_row(), ["mbei"], _row()])
    assert report["status"] == "REJECTED"
    assert "第 1 行不是 dict" in report["reasons"][0]


def test_anomalous_values_counted_and_traced_not_silently_zeroed():
    rows = [_row("002633.SZ", mbei=1),
            _row("002634.SZ", vv="yes"),
            _row("002635.SZ", bei=0)]
    report = coverage_report(rows)
    assert report["status"] == "OK_WITH_ANOMALIES"
    assert len(report["anomalies"]) == 3
    by_field = {a["field"]: a for a in report["anomalies"]}
    assert by_field["mbei"]["value_type"] == "int"
    assert by_field["mbei"]["symbol"] == "002633.SZ"
    assert by_field["mbei"]["row_index"] == 0
    assert by_field["vv"]["row_index"] == 1, "异常留痕的行定位必须承重"
    assert by_field["vv"]["value_type"] == "str"
    assert by_field["bei"]["row_index"] == 2
    m = report["fields"]["mbei"]
    assert m["present_true"] == 0 and m["present_other"] == 1
    assert m["present_true"] + m["present_false"] + m["present_null"] \
        + m["present_missing"] + m["present_other"] == 3, "五桶计数守恒"
    assert m["coverage"] == 0.0, "异常取值不可观测，不入分子但不破坏分母"


def test_bool_identity_strict_one_and_zero_are_not_true_false():
    rows = [_row("002633.SZ", bei=1), _row("002634.SZ", bei=0)]
    report = coverage_report(rows)
    f = report["fields"]["bei"]
    assert f["present_true"] == 0 and f["present_false"] == 0
    assert f["present_other"] == 2


def test_json_roundtrip_rows_match_real_pool_shape():
    rows = [json.loads(json.dumps(_row("002633.SZ", mbei=True))),
            json.loads(json.dumps(_row("002634.SZ", bei=False)))]
    before = deepcopy(rows)
    report = coverage_report(rows)
    assert rows == before, "输入不得被修改"
    assert report["status"] == "OK"
    assert report["fields"]["mbei"]["present_true"] == 1
    assert report["fields"]["bei"]["present_false"] == 1


def test_statistics_do_not_claim_gate_semantics():
    report = coverage_report([_row()])
    assert report["status"] in ("OK", "OK_WITH_ANOMALIES")
    assert report["status"] != "READY"
