# -*- coding: utf-8 -*-
"""learning_gate：schema 校验/各阶段门/归档门/失败阻断。"""
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lg", REPO / "tools/kb_corpus/learning_gate.py")
lg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lg)


def test_validate_required_and_types():
    sch = lg.load_schema("learning_unit.schema.json")
    errs = lg.validate({"unit_id": "u1"}, sch)
    assert any("schema_version" in e for e in errs)
    ok = {"schema_version": "learning_unit.v1", "unit_id": "u1", "project_id": "p",
          "claim": "这是一条足够长的知识断言", "evidence": {"file": "a.md", "line": 3},
          "status": "draft", "created_at": "2026-11-02"}
    assert lg.validate(ok, sch) == []


def test_check_intake_manifest(tmp_path):
    kb = tmp_path / "kb"
    pid = "proj1"
    d = kb / "05_学习源件" / pid
    (d / "source").mkdir(parents=True)
    (d / "source" / "a.md").write_text("x", encoding="utf-8")
    import hashlib
    sha = hashlib.sha256(b"x").hexdigest()
    (d / "manifest.json").write_text(json.dumps({
        "project_id": pid, "generated_at": "2026-11-02",
        "files": [{"name": "a.md", "sha256": sha, "size": 1, "origin": "x", "copied": True}]}),
        encoding="utf-8")
    assert lg.check_intake(kb, pid) == []


def test_check_units_traceability(tmp_path):
    kb = tmp_path / "kb"
    pid = "proj1"
    td = kb / "20_清洗" / pid / "text"
    td.mkdir(parents=True)
    (td / "a.md").write_text("内容", encoding="utf-8")
    ud = kb / "30_知识单元"
    ud.mkdir(parents=True)
    lines = [json.dumps({"unit_id": "u1", "project_id": pid, "claim": "这是一条足够长的知识断言",
                         "evidence": {"file": "a.md", "line": 1}, "status": "draft", "created_at": "2026-11-02"},
                        ensure_ascii=False)]
    (ud / f"{pid}_units_draft.jsonl").write_text("\n".join(lines), encoding="utf-8")
    assert lg.check_units(kb, pid) == []


def test_run_archived_only_archive(tmp_path, monkeypatch):
    reg = {"projects": [{"project_id": "p_arch", "status": "archived",
                         "archive": str(tmp_path / "arch")}]}
    regp = tmp_path / "reg.json"
    regp.write_text(json.dumps(reg), encoding="utf-8")
    monkeypatch.setattr(lg, "REG", regp)
    (tmp_path / "arch").mkdir()
    (tmp_path / "arch" / "ENTRY.md").write_text("# e", encoding="utf-8")
    (tmp_path / "arch" / "manifest.json").write_text("{}", encoding="utf-8")
    rep = lg.run(tmp_path / "kb", stage="all")
    assert rep["ok"] is True
    (tmp_path / "arch" / "ENTRY.md").unlink()
    rep2 = lg.run(tmp_path / "kb", stage="all")
    assert rep2["ok"] is False
