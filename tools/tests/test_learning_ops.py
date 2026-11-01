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
    regp.write_text(json.dumps({"projects": [{"project_id": pid, "status": "cleaning"}]}), encoding="utf-8")
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


# ---------------------------------------------------------------------------
# 新增（task-128）：PDF OCR 阶段 + 结项归档
# ---------------------------------------------------------------------------
def _project_with_pdf(tmp_path):
    kb = tmp_path / "kb"
    pid = "p_pdf"
    src = kb / "05_学习源件" / pid / "source"
    src.mkdir(parents=True)
    pdf = src / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake pdf")
    return kb, pid, pdf


def test_ocr_stage_idempotent(tmp_path):
    kb, pid, pdf = _project_with_pdf(tmp_path)
    calls = []

    def fake_runner(cmd, capture_output=True, text=True, timeout=None):
        calls.append(cmd)
        out = Path(cmd[cmd.index("--out-dir") + 1]) / pdf.stem
        out.mkdir(parents=True, exist_ok=True)
        (out / "ocr.txt").write_text("OCR 文本", encoding="utf-8")

        class P:
            returncode = 0
            stderr = ""
        return P()

    project = {"project_id": pid}
    first = lo.ocr_stage(kb, project, write=True, runner=fake_runner)
    assert first["stage"] == "ocr_done" and first["ran"] == 1 and first["failed"] == 0
    man = json.loads((kb / "05_学习源件" / pid / "manifest.json").read_text(encoding="utf-8"))
    assert man["stage"] == "ocr_done" and man["files"][0]["stage"] == "ocr_done"
    assert man["files"][0]["sha256"]
    second = lo.ocr_stage(kb, project, write=True, runner=fake_runner)
    assert second["ran"] == 0 and len(calls) == 1        # 幂等：不重复 OCR


def test_ocr_stage_no_pdf_skips(tmp_path):
    kb = tmp_path / "kb"
    (kb / "05_学习源件" / "empty_p").mkdir(parents=True)
    res = lo.ocr_stage(kb, {"project_id": "empty_p"}, write=True)
    assert res["stage"] == "skip_no_pdf" and res["pdfs"] == 0


def test_archive_dry_run_and_write(tmp_path, monkeypatch):
    kb, pid, pdf = _project_with_pdf(tmp_path)
    regp = tmp_path / "reg.json"
    regp.write_text(json.dumps({"projects": [
        {"project_id": pid, "name": "测试项目A", "status": "distilling",
         "source": [str(pdf)]}]}), encoding="utf-8")
    monkeypatch.setattr(lo, "REG", regp)
    monkeypatch.setattr(lo, "ARCHIVE_ROOT", tmp_path / "archive")
    monkeypatch.setattr(lo, "REPORTS", tmp_path / "reports")
    dry = lo.archive(kb, pid, write=False, date_tag="2026-11-02")
    assert dry["mode"] == "dry_run" and not (tmp_path / "archive").exists()
    assert dry["files"] and dry["status_after"] == "archived"
    out = lo.archive(kb, pid, write=True, date_tag="2026-11-02")
    assert out["mode"] == "archived"
    target = tmp_path / "archive" / "2026-11-02_测试项目A"
    assert (target / "ENTRY.md").exists() and (target / "manifest.json").exists()
    man = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert man["schema_version"] == "project_archive.v1" and man["status"] == "archived"
    assert man["files"] and len(man["files"][0]["sha256"]) == 64
    assert "结论" in (target / "ENTRY.md").read_text(encoding="utf-8")
    assert pdf.exists()                                  # 复制索引，不移动源文件
    assert json.loads(regp.read_text(encoding="utf-8"))["projects"][0]["status"] == "archived"
    again = lo.archive(kb, pid, write=True, date_tag="2026-11-02")
    assert again["mode"] == "already_archived"           # 幂等：已归档 no-op
