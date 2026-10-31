# -*- coding: utf-8 -*-
"""companies 质量门离线测试（只读，tmp DB/master）。"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "jev_companies_audit.py"


def _load():
    spec = importlib.util.spec_from_file_location("_companies_audit", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _setup(tmp_path):
    db = tmp_path / "news.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE jev_review_queue(
        id TEXT PRIMARY KEY, title TEXT, stocks TEXT, original_jev TEXT,
        status TEXT DEFAULT 'pending', bias TEXT, conf REAL, review_type TEXT DEFAULT '')""")
    rows = [
        ("a1", "宁德时代储能扩产", "[]",
         json.dumps({"companies": ["300750"], "reason": "宁德时代扩产", "impact": "medium"}), "bull", 0.6),
        ("a2", "某公司新闻", "[]",
         json.dumps({"companies": ["300,059"], "reason": "x", "impact": "medium"}), "neutral", 0.6),
        ("a3", "天阳科技新设子公司", "[]",
         json.dumps({"companies": ["300542"], "reason": "天阳科技", "impact": "medium"}), "neutral", 0.6),
        ("a4", "未知公司", "[]",
         json.dumps({"companies": ["999999"], "reason": "x", "impact": "medium"}), "neutral", 0.6),
        ("a5", "人民论坛网评", "[]",
         json.dumps({"companies": [], "reason": "评论", "impact": "medium"}), "bull", 0.6),
        ("a6", "重复标题", "[]",
         json.dumps({"companies": [], "reason": "x", "impact": "medium"}), "neutral", 0.6),
        ("a7", "重复标题", "[]",
         json.dumps({"companies": [], "reason": "y", "impact": "medium"}), "neutral", 0.6),
    ]
    con.executemany(
        "INSERT INTO jev_review_queue(id,title,stocks,original_jev,status,bias,conf) "
        "VALUES(?,?,?,?, 'pending',?,?)", rows)
    con.commit()
    con.close()
    master = tmp_path / "names.json"
    master.write_text(json.dumps({
        "300750.SZ": "宁德时代", "300542.SZ": "新晨科技", "300872.SZ": "天阳科技",
    }, ensure_ascii=False), encoding="utf-8")
    return db, master


def test_companies_audit_hard_weak_style_dupes(tmp_path):
    m = _load()
    db, master = _setup(tmp_path)
    rep = m.audit_db(db, master)
    codes = {(e.get("id"), e["error"]) for e in rep["hard_errors"]}
    assert ("a2", "company_code_format") in codes
    assert ("a3", "company_code_unknown") not in codes
    assert ("a4", "company_code_unknown") in codes
    assert not any(e["id"] == "a1" and e["error"] == "weak_link_name_not_in_text"
                   for e in rep["weak_links"])
    assert any(e.get("id") == "a3" and e.get("error") == "company_name_mismatch"
               for e in rep["hard_errors"])
    assert rep["duplicate_titles"]
    assert rep["zero_variance"]["unreliable"] is True
    assert rep["ok"] is False


def test_companies_audit_clean(tmp_path):
    m = _load()
    db, master = _setup(tmp_path)
    # 清空有问题的行，只保留一个强链
    con = sqlite3.connect(db)
    con.execute("DELETE FROM jev_review_queue WHERE id != 'a1'")
    con.commit(); con.close()
    rep = m.audit_db(db, master)
    assert rep["ok"] is True
    assert rep["counts"]["hard"] == 0
