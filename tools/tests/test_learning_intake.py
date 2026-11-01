# -*- coding: utf-8 -*-
"""learning_intake：docx 抽取/幂等/安全白名单/不碰数据仓。"""
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("li2", REPO / "tools/kb_corpus/learning_intake.py")
li = importlib.util.module_from_spec(spec)
spec.loader.exec_module(li)


def _mk_docx(path: Path):
    import docx
    d = docx.Document()
    d.add_heading("测试章节", level=1)
    d.add_paragraph("这是老木创业笔记的测试段落。")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "A"
    t.rows[0].cells[1].text = "B"
    d.save(str(path))


def test_ingest_docx(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    inbox = kb / "05_学习源件" / "_inbox"
    inbox.mkdir(parents=True)
    _mk_docx(inbox / "示例_Word.docx")
    monkeypatch.setattr(li, "REG", tmp_path / "learning_projects.json")
    monkeypatch.setattr(li, "REPORTS", tmp_path / "reports")
    files = li.scan(kb)
    assert len(files) == 1
    rep = li.ingest(files, kb, project="测试项目", write=True)
    item = rep["items"][0]
    assert item["action"] == "ingested" and item["copy"] is True
    assert item["extract"]["paragraphs"] >= 1 and item["extract"]["tables"] == 1
    md = Path(item["text_md"])
    assert md.exists() and "老木创业笔记" in md.read_text(encoding="utf-8")
    # 幂等
    rep2 = li.ingest(li.scan(kb), kb, project="测试项目", write=True)
    assert rep2["items"][0]["action"] == "skip_duplicate"


def test_safety_blocks_datawarehouse(tmp_path):
    kb = tmp_path / "kb"
    try:
        li.assert_safe(REPO / "tools/datawarehouse/dataset_registry.py", kb)
    except li.SafetyError:
        return
    raise AssertionError("安全白名单未拦截数据仓写入")


def test_pdf_no_copy_over_40mb(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    inbox = kb / "05_学习源件" / "_inbox"
    inbox.mkdir(parents=True)
    big = inbox / "大文件.pdf"
    big.write_bytes(b"x" * 1024)
    monkeypatch.setattr(li, "REG", tmp_path / "lp.json")
    monkeypatch.setattr(li, "REPORTS", tmp_path / "reports")
    # 直接构造超限记录避免真造 40MB
    files = li.scan(kb)
    files[0]["size"] = 50 * 1024 * 1024
    rep = li.ingest(files, kb, project="PDF项目", write=True)
    assert rep["items"][0]["copy"] is False
