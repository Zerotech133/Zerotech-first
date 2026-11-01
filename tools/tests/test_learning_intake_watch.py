# -*- coding: utf-8 -*-
"""learning_intake_watch：扫描/比对/dry-run 不写。"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("li", REPO / "tools/kb_corpus/learning_intake_watch.py")
li = importlib.util.module_from_spec(spec)
spec.loader.exec_module(li)


def test_known_sources():
    reg = {"projects": [{"source": ["C:/a.pdf"]}]}
    assert "c:\\a.pdf" in li.known_sources(reg)


def test_report_dry_run_no_write(tmp_path, monkeypatch):
    regp = tmp_path / "reg.json"
    regp.write_text('{"projects": []}', encoding="utf-8")
    monkeypatch.setattr(li, "REG", regp)
    monkeypatch.setattr(li, "scan", lambda: [{"path": "C:/x.pdf", "name": "x.pdf", "size": 1, "mtime": "t"}])
    out = li.report(dry_run=True)
    assert out["new"] and out["dry_run"] is True
    assert regp.read_text(encoding="utf-8") == '{"projects": []}'
