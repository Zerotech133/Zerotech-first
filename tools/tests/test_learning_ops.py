# -*- coding: utf-8 -*-
"""learning_ops：pipeline 草稿/治理问题/标准化/安全白名单。"""
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lo", REPO / "tools/kb_corpus/learning_ops.py")
lo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lo)


def _setup(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    pid = "测试项目"
    d = kb / "20_清洗" / pid / "text"
    d.mkdir(parents=True)
    (d / "a.md").write_text("# 第一章\n\n创业要找到真实痛点。\n\n创业要找到真实痛点。\n\n表格行\n", encoding="utf-8")
    regp = tmp_path / "lp.json"
    regp.write_text(json.dumps({"projects": [{"project_id": pid, "status": "intake_pending"}]}), encoding="utf-8")
    monkeypatch.setattr(lo, "REG", regp)
    monkeypatch.setattr(lo, "REPORTS", tmp_path / "reports")
    (kb / "05_学习源件" / pid).mkdir(parents=True)
    (kb / "05_学习源件" / pid / "manifest.json").write_text(json.dumps({"files": []}), encoding="utf-8")
    (kb / "05_学习源件" / pid / "meta.json").write_text(json.dumps({"namespace": "learning"}), encoding="utf-8")
    return kb, pid, regp


def test_pipeline_drafts(tmp_path, monkeypatch):
    kb, pid, regp = _setup(tmp_path, monkeypatch)
    rep = lo.pipeline(kb, write=True)
    item = rep["projects"][0]
    assert item["action"] == "pipelined" and item["units"] >= 1
    assert (kb / "30_知识单元" / f"{pid}_units_draft.jsonl").exists()
    assert json.loads(regp.read_text(encoding="utf-8"))["projects"][0]["status"] == "distilling"


def test_governance_flags_missing_units(tmp_path, monkeypatch):
    kb, pid, regp = _setup(tmp_path, monkeypatch)
    rep = lo.governance(kb, write=True)
    codes = [i["code"] for i in rep["issues"]]
    assert "NO_DRAFT_UNITS" in codes


def test_standardize_checks(tmp_path, monkeypatch):
    kb, pid, _ = _setup(tmp_path, monkeypatch)
    rep = lo.standardize(kb, write=True)
    assert rep["summary"]["dirs"] >= 1 and not rep["issues"]


def test_safety_blocks_datawarehouse(tmp_path):
    kb = tmp_path / "kb"
    try:
        lo.safe(REPO / "tools/datawarehouse/dataset_registry.py", kb)
    except lo.SafetyError:
        return
    raise AssertionError("未拦截数据仓写入")
