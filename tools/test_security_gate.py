# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import security_gate as G  # noqa: E402


def test_scan_clean_and_findings(tmp_path):
    (tmp_path / "ok.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "bad.py").write_text(
        "import subprocess\neval('1+1')\nsubprocess.run('x', shell=True)\n"
        "KEY = 'sk-" + "abcdefghijklmnopqrstuvwxyz123456'\n", encoding="utf-8")  # security-gate:ignore
    res = G.scan(paths=[str(tmp_path)], all_files=True)
    assert res["files_scanned"] >= 2
    rules = {f["rule"] for f in res["findings"]}
    assert "danger_call:eval" in rules
    assert any("shell=True" in r for r in rules)
    assert "openai_sk" in rules
    assert res["ok"] is False and res["high"] >= 2


def test_scan_clean(tmp_path):
    (tmp_path / "ok.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    res = G.scan(paths=[str(tmp_path)], all_files=True)
    assert res["ok"] is True


def test_ignore_marker(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("KEY = 'sk-abcdefghijklmnopqrstuvwxyz123456'  # security-gate:ignore\n",
                 encoding="utf-8")
    res = G.scan(paths=[str(f)], all_files=True)
    assert res["ok"] is True and res["total"] == 0


def test_selftest_skipped(tmp_path):
    f = tmp_path / "_scan_secrets_selftest.py"
    f.write_text("KEY = 'sk-abcdefghijklmnopqrstuvwxyz123456'\n", encoding="utf-8")
    res = G.scan(paths=[str(f)], all_files=True)
    assert res["total"] == 0
