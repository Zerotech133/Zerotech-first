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


# ---------------------------------------------------------------------------
# 新增（task-137）：S3 元数据行过滤
# ---------------------------------------------------------------------------
def test_is_meta_claim_rules():
    meta = [
        "> 共 23 条 · 由 faster-whisper 转写，可能有个别同音字错误",
        "- 文件：`20261010_1835_缩量后放量 AI 把它翻译成公式.mp4`",
        "- 时长：1:16",
        "- 字数：405",
        "共 12 条",
        "由 faster-whisper 转写",
        "————————",
        "++++",
        "· · ·",
        "20261010_1835_缩量后放量.mp4",
    ]
    for s in meta:
        assert lo._is_meta_claim(s) is True, s
    keep = [
        "五天的成交量加起来还不如今天一天，缩量说明多空双方都不想动。",
        "今天的成交量要大于前五天里最大的那一根，窗口要往前挪一格。",
        "本视频仅演示AI量化工具搭建与代码编写，不构成任何投资建议。",
        "- 原理：先讲原理，再拆条件，最后才写代码。",
    ]
    for s in keep:
        assert lo._is_meta_claim(s) is False, s


def test_draft_units_filters_meta_and_stats(tmp_path):
    clean = tmp_path / "clean.md"
    clean.write_text("# 标题\n\n"
                     "五天的成交量加起来还不如今天一天，缩量说明多空双方都不想动。\n"
                     "- 文件：`x.mp4`\n"
                     "- 时长：1:16\n"
                     "- 字数：405\n"
                     "> 共 23 条 · 由 faster-whisper 转写，可能有个别同音字错误\n"
                     "今天的成交量要大于前五天里最大的那一根。\n"
                     "————————————————\n", encoding="utf-8")
    out = tmp_path / "units.jsonl"
    stats = {}
    kept = lo.draft_units("p_meta", [clean], out, stats=stats)
    assert kept == 2 and stats["meta_skipped"] == 5 and stats["units_raw"] == 7
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 2
    assert all("文件" not in u["claim"] and "时长" not in u["claim"] for u in lines)


def test_pipeline_dryrun_units_audit_no_rewrite(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    pid = "p_video"
    td = kb / "20_清洗" / pid / "text"
    td.mkdir(parents=True)
    (td / "a.md").write_text("正常知识行：成交量的窗口要往前挪一格。\n", encoding="utf-8")
    ud = kb / "30_知识单元"
    ud.mkdir(parents=True)
    units = ud / f"{pid}_units_draft.jsonl"
    units.write_text("\n".join([
        json.dumps({"claim": "正常知识：缩量后放量是一个特征"}, ensure_ascii=False),
        json.dumps({"claim": "- 文件：`x.mp4`"}, ensure_ascii=False),
        json.dumps({"claim": "- 时长：1:16"}, ensure_ascii=False),
    ]), encoding="utf-8")
    before = units.read_bytes()
    regp = tmp_path / "reg.json"
    regp.write_text(json.dumps({"projects": [{"project_id": pid, "status": "distilling"}]}),
                    encoding="utf-8")
    monkeypatch.setattr(lo, "REG", regp)
    monkeypatch.setattr(lo, "REPORTS", tmp_path / "reports")
    rep = lo.pipeline(kb, write=False)
    item = rep["projects"][0]
    assert item["action"] == "would_pipeline"
    assert item["units_audit"]["meta_skipped"] == 2
    assert item["units_audit"]["kept"] == 1
    assert item["units_audit"]["rewritten"] is False
    assert units.read_bytes() == before                    # 真实 units 未被重写
    payload = lo.meta_filter(pid, kb=kb, report=True)
    assert payload["units_rewritten"] is False and payload["audit"]["meta_skipped"] == 2
    assert (tmp_path / "reports" / f"learning_meta_filter_{payload['tag']}.md").exists()
