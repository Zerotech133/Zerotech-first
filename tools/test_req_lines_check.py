# -*- coding: utf-8 -*-
"""O8 P2 分线 lock 校验器单测。"""
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("req_lines_check", HERE / "req_lines_check.py")
rl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl)


def test_check_file_good(tmp_path):
    p = tmp_path / "a.lock"
    p.write_text("# c\nfoo==1.2.3\nBar==2.0\n", encoding="utf-8")
    r = rl.check_file(str(p))
    assert r["ok"] and r["packages"] == 2


def test_check_file_bad_format(tmp_path):
    p = tmp_path / "b.lock"
    p.write_text("foo==1.2.3\nnot a req\n", encoding="utf-8")
    r = rl.check_file(str(p))
    assert not r["ok"] and any("格式非法" in e for e in r["errors"])


def test_check_file_duplicate(tmp_path):
    p = tmp_path / "c.lock"
    p.write_text("foo==1.0\nFoo==2.0\n", encoding="utf-8")
    r = rl.check_file(str(p))
    assert not r["ok"] and any("重复包" in e for e in r["errors"])


def test_check_file_empty(tmp_path):
    p = tmp_path / "d.lock"
    p.write_text("# only comments\n", encoding="utf-8")
    r = rl.check_file(str(p))
    assert not r["ok"] and any("无有效包行" in e for e in r["errors"])


def test_check_dir(tmp_path):
    (tmp_path / "a.lock").write_text("foo==1.0\n", encoding="utf-8")
    (tmp_path / "b.lock").write_text("bar==2.0\n", encoding="utf-8")
    r = rl.check_dir(str(tmp_path))
    assert r["ok"] and r["files"] == 2 and r["total_packages"] == 2
