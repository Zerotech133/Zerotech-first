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
    (inbox / "README.txt").write_text("skip me", encoding="utf-8")
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


PII_MD = (
    "# 测试\n\n联系邮箱 teacher@example.com，手机 13800138000，"
    "网址 https://example.com/path?q=1 ，微信 @laomu1688。\n"
)


def test_ingest_md_pii_cleaned_and_receipted(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    inbox = kb / "05_学习源件" / "_inbox"
    inbox.mkdir(parents=True)
    (inbox / "pii_样例.md").write_text(PII_MD, encoding="utf-8")
    monkeypatch.setattr(li, "REG", tmp_path / "lp.json")
    monkeypatch.setattr(li, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(li, "_PII_MODULE", None)
    files = li.scan(kb)
    rep = li.ingest(files, kb, project="PII项目", write=True)
    item = rep["items"][0]
    text = Path(item["text_md"]).read_text(encoding="utf-8")
    assert "teacher@example.com" not in text
    assert "13800138000" not in text
    assert "https://example.com" not in text
    assert "[EMAIL]" in text and "[PHONE]" in text and "[URL]" in text
    assert item["pii"]["changed"] is True and item["pii"]["hits"]
    assert rep["pii"]["enabled"] is True and rep["pii"]["files_changed"] == 1
    assert rep["pii"]["policy_version"] == "pii-policy.v1.1"
    manifest = json.loads((kb / "05_学习源件" / "PII项目" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"][0]["pii"]["changed"] is True


def test_missing_pii_policy_fails_closed(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    inbox = kb / "05_学习源件" / "_inbox"
    inbox.mkdir(parents=True)
    (inbox / "普通.md").write_text("正常内容", encoding="utf-8")
    monkeypatch.setattr(li, "REG", tmp_path / "lp.json")
    monkeypatch.setattr(li, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(li, "_PII_MODULE", None)
    monkeypatch.setattr(li, "_find_intel_kg_root", lambda: None)
    files = li.scan(kb)
    try:
        li.ingest(files, kb, project="缺策略", write=True)
    except li.SafetyError as exc:
        assert "PII 清洗策略不可用" in str(exc)
        return
    raise AssertionError("策略缺失时应 fail-closed")


def test_no_pii_clean_escape_keeps_text(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    inbox = kb / "05_学习源件" / "_inbox"
    inbox.mkdir(parents=True)
    (inbox / "pii_逃生.md").write_text(PII_MD, encoding="utf-8")
    monkeypatch.setattr(li, "REG", tmp_path / "lp.json")
    monkeypatch.setattr(li, "REPORTS", tmp_path / "reports")
    files = li.scan(kb)
    rep = li.ingest(files, kb, project="逃生项目", write=True, no_pii_clean=True)
    text = Path(rep["items"][0]["text_md"]).read_text(encoding="utf-8")
    assert "teacher@example.com" in text
    assert rep["pii"]["enabled"] is False and rep["pii"]["disabled"] == "flag"

