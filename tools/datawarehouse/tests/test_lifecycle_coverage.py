# -*- coding: utf-8 -*-
"""lifecycle_coverage.py 回归测试（DWH-101，2026-11-02；仅测试，不改实现）。

被测：tools/datawarehouse/lifecycle_coverage.py 的 audit() / main()
口径（以现状实现为准，若未来口径调整需同步改对应用例）：
- 四态 active/planned/deprecated/archived 为显式有效值；缺 lifecycle 字段 = 隐式 active（missing）；
- covered 只计显式四态；effective = covered + implicit_active；
- 非法值（含大小写不一致）进 invalid 清单，同时计入 by_domain 的 missing；
- 非 dict 表条目经 _table_entry 归一为 {"_invalid": True}，现状按 missing/隐式 active 计。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import lifecycle_coverage as cov  # noqa: E402


def _catalog(tmp_path, tables, version=1, omit_tables=False):
    """写临时 catalog.json（只读工具用，测试夹具不碰真实数据仓）。"""
    payload = {"version": version}
    if not omit_tables:
        payload["tables"] = tables
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _audit(tmp_path, tables, **kwargs):
    return cov.audit(catalog_path=_catalog(tmp_path, tables), **kwargs)


def test_empty_catalog_no_division_by_zero(tmp_path):
    """用例 1｜空 catalog（无 tables 键 / tables={}）：total=0，比例 0.0，不抛异常。"""
    rep = cov.audit(catalog_path=_catalog(tmp_path, None, omit_tables=True))
    assert rep["tool"] == "lifecycle_coverage"
    assert rep["catalog_version"] == 1
    assert rep["total"] == 0
    assert rep["covered"] == 0
    assert rep["coverage_pct"] == 0.0
    assert rep["implicit_active"] == 0
    assert rep["effective_coverage_pct"] == 0.0
    assert rep["require_ok"] is True
    assert rep["distribution"] == {}
    assert rep["missing"] == []
    assert rep["invalid"] == []
    assert rep["by_domain"] == {}

    empty = _audit(tmp_path, {})
    assert empty["total"] == 0
    assert empty["coverage_pct"] == 0.0


def test_archived_is_explicit_covered(tmp_path):
    """用例 2｜含 archived：四态均计入显式覆盖（archived 不因“归档”被排除）。"""
    rep = _audit(tmp_path, {
        "alpha_active": {"lifecycle": "active"},
        "beta_planned": {"lifecycle": "planned"},
        "gamma_deprecated": {"lifecycle": "deprecated"},
        "delta_archived": {"lifecycle": "archived"},
        "epsilon_missing": {},
    })
    assert rep["total"] == 5
    assert rep["covered"] == 4
    assert rep["coverage_pct"] == 80.0
    assert rep["implicit_active"] == 1
    assert rep["effective_coverage_pct"] == 100.0
    assert rep["distribution"] == {
        "active": 1, "planned": 1, "deprecated": 1, "archived": 1, "missing": 1,
    }
    assert rep["missing"] == ["epsilon_missing"]
    assert rep["invalid"] == []
    assert rep["invalid_count"] == 0


def test_missing_lifecycle_field_is_implicit_active(tmp_path):
    """用例 3｜字段缺失边界：无 lifecycle / 空串 / None → 隐式 active，不算 invalid。"""
    rep = _audit(tmp_path, {
        "no_field": {"label": "x"},
        "empty_string": {"lifecycle": ""},
        "none_value": {"lifecycle": None},
    })
    assert rep["total"] == 3
    assert rep["covered"] == 0
    assert rep["coverage_pct"] == 0.0
    assert rep["implicit_active"] == 3
    assert rep["effective_coverage_pct"] == 100.0
    assert rep["invalid_count"] == 0
    assert rep["missing"] == ["empty_string", "no_field", "none_value"]


def test_invalid_lifecycle_value_reported(tmp_path):
    """用例 4｜非法值边界：非四态值进 invalid；大小写不归一（现状）；by_domain 计入 missing。"""
    rep = _audit(tmp_path, {
        "ok_table": {"lifecycle": "active", "source": "接口数据"},
        "bogus_table": {"lifecycle": "retired", "source": "接口数据"},
        "upper_table": {"lifecycle": "ARCHIVED", "source": "接口数据"},
    })
    assert rep["covered"] == 1
    assert rep["coverage_pct"] == 33.3
    assert rep["invalid_count"] == 2
    assert rep["invalid"] == ["bogus_table", "upper_table"]
    assert rep["distribution"]["invalid:retired"] == 1
    assert rep["distribution"]["invalid:ARCHIVED"] == 1
    assert rep["by_domain"]["接口数据"] == {"total": 3, "missing": 2}


def test_non_dict_entry_current_missing_behaviour(tmp_path):
    """用例 5｜边界：非 dict 表条目 → _table_entry 归一 {"_invalid": True}，现状按 missing/隐式 active 计。

    备注：_invalid 标记当前未上抛到 invalid 清单（audit 现状锁定；口径修正需同步改本用例）。
    """
    rep = _audit(tmp_path, {"str_entry": "oops", "list_entry": ["a"], "null_entry": None})
    assert rep["total"] == 3
    assert rep["invalid_count"] == 0
    assert rep["invalid"] == []
    assert rep["implicit_active"] == 3
    assert rep["effective_coverage_pct"] == 100.0
    assert rep["missing"] == ["list_entry", "null_entry", "str_entry"]


def test_by_domain_source_first_then_name_prefix(tmp_path):
    """用例 6｜分域口径：source 字段优先；缺失时用表名首段；首段为空归 "?"。"""
    rep = _audit(tmp_path, {
        "ts_daily": {"lifecycle": "active"},
        "ts_moneyflow": {},
        "xq_status": {"lifecycle": "planned", "source": "开源情报"},
    })
    assert rep["by_domain"] == {
        "ts": {"total": 2, "missing": 1},
        "开源情报": {"total": 1, "missing": 0},
    }

    rep2 = _audit(tmp_path, {"_leading": {"lifecycle": "active"}})
    assert rep2["by_domain"] == {"?": {"total": 1, "missing": 0}}


def test_require_explicit_threshold(tmp_path):
    """用例 7｜--require-explicit 阈值：显式 2/3=66.7%，要求 70% 时不达标。"""
    tables = {"p1": {"lifecycle": "active"}, "p2": {"lifecycle": "archived"}, "m1": {}}
    assert _audit(tmp_path, tables, require_explicit_pct=0)["require_ok"] is True
    assert _audit(tmp_path, tables, require_explicit_pct=60)["require_ok"] is True
    assert _audit(tmp_path, tables, require_explicit_pct=66.7)["require_ok"] is True
    assert _audit(tmp_path, tables, require_explicit_pct=70)["require_ok"] is False
    # 空 catalog 在 100% 要求下不得判通过（分母=0 按 0.0% 处理）
    empty_rep = cov.audit(
        catalog_path=_catalog(tmp_path, None, omit_tables=True),
        require_explicit_pct=100,
    )
    assert empty_rep["require_ok"] is False


def test_tables_list_form_supported(tmp_path):
    """用例 8｜字段边界：tables 为 [name, entry] 对列表时 dict() 归一后同样可审计。"""
    rep = _audit(tmp_path, [["la", {"lifecycle": "active"}], ["lb", {}]])
    assert rep["total"] == 2
    assert rep["covered"] == 1
    assert rep["implicit_active"] == 1
    assert rep["missing"] == ["lb"]


def test_main_check_exit_code(tmp_path, capsys):
    """用例 9｜CLI：--check 非法值 → rc=1；干净 → rc=0；--json 输出可解析。"""
    bad = _catalog(tmp_path, {"bad": {"lifecycle": "bogus"}})
    assert cov.main(["--catalog", str(bad), "--check", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["invalid_count"] == 1

    good = tmp_path / "good.json"
    good.write_text(
        json.dumps({"version": 2, "tables": {"a": {"lifecycle": "active"},
                                            "b": {"lifecycle": "archived"}}}),
        encoding="utf-8",
    )
    assert cov.main(["--catalog", str(good), "--check"]) == 0
    assert cov.main(["--catalog", str(good), "--require-explicit", "50", "--check"]) == 0

    mixed = _catalog(tmp_path, {"a": {"lifecycle": "active"}, "b": {}})
    assert cov.main(["--catalog", str(mixed), "--require-explicit", "100", "--check"]) == 1
