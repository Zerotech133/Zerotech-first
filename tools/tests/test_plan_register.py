# -*- coding: utf-8 -*-
"""plan_register 台账生成器单测（2026-10-30 P90）。"""
import importlib.util
import time
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2] / "tools" / "plan_register.py"


def _load():
    spec = importlib.util.spec_from_file_location("plan_register_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_classify_lifecycle():
    m = _load()
    assert m.classify("STATUS: superseded by v3") == "被取代"
    assert m.classify("# x\nSTATUS: frozen 只读") == "冻结/只读"
    assert m.classify("待批：此单待老田签字") == "待批/待执行"
    assert m.classify("本单已完工") == "完工"
    assert m.classify("普通正文") == "活跃(未标注)"


def test_kind_of():
    m = _load()
    assert m.kind_of("2026-10-01_路线图.md", "x") == "路线图"
    assert m.kind_of("2026-10-01_x.md", "设计方案 v1") == "方案"
    assert m.kind_of("2026-10-01_x.md", "无关键词标题") == "其他"


def test_scan_plan_uses_filename_date(tmp_path):
    m = _load()
    old = "2026-10-01"
    f = tmp_path / ("%s_测试计划.md" % old)
    f.write_text("# 测试计划\n\n正文\n", encoding="utf-8")
    row = m.scan_plan(f)
    assert row["date"] == old
    expect = round((time.time() - time.mktime(time.strptime(old, "%Y-%m-%d"))) / 86400.0, 1)
    assert abs(row["age_days"] - expect) < 1.0
    assert row["lifecycle"] == "活跃(未标注)"


def test_generated_plan_registers_excluded(tmp_path, monkeypatch):
    """2026-10-31 PLANREG：PLAN_REGISTER* 生成物不计入计划统计（不碰真实 docs/plans）。"""
    m = _load()
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "2026-10-01_真实计划.md").write_text("# 真实计划\n\n正文\n", encoding="utf-8")
    (plans / "PLAN_REGISTER_ALL_20261031.md").write_text("# 全量台账\n", encoding="utf-8")
    (plans / "PLAN_REGISTER_20261031.md").write_text("# 计划台账\n", encoding="utf-8")
    (plans / "INDEX_计划全集索引.md").write_text("# 索引\n", encoding="utf-8")
    (plans / "README.md").write_text("# 说明\n", encoding="utf-8")
    monkeypatch.setattr(m, "PLANS", plans)
    rep = m.build()
    assert rep["total"] == 1, "生成物/README/INDEX 都不得计入计划统计"
    assert [r["file"] for r in rep["rows"]] == ["2026-10-01_真实计划.md"]
    assert rep["by_lifecycle"] == {"活跃(未标注)": 1}
