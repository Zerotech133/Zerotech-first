# -*- coding: utf-8 -*-
"""doc_sync_probe 测试（2026-11-16 WO-DOC-SYNC-PROBE）。

用 tmp 内的小文档 + 小生成脚本，验证探针能正确判定三种破坏并守住原件。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "doc_sync_probe_t", REPO / "tools" / "doc_sync_probe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dp = _load()
BLOCK = "| a | 1 |"
TABLE = "| a | 1 |\n| b | 2 |\n"


def _setup(tmp_path):
    gen = tmp_path / "gen.py"
    gen.write_text("print('| a | 1 |')\nprint('| b | 2 |')\n", encoding="utf-8")
    doc = tmp_path / "d.md"
    doc.write_text("前言\n" + TABLE + "后记\n", encoding="utf-8")
    cmd = '"%s" -X utf8 "%s"' % (sys.executable, gen)
    return doc, cmd


def test_probe_passes_and_protects_source(tmp_path, capsys):
    doc, cmd = _setup(tmp_path)
    before = doc.read_bytes()
    rc = dp.main(["--doc", str(doc), "--block", BLOCK, "--lines", "2", "--cmd", cmd])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PROBE PASS" in out and "原件 sha256 未变 : True" in out
    assert doc.read_bytes() == before  # 原件一字节未动


def test_probe_flags_missing_block(tmp_path, capsys):
    """起始行在文档里不存在 → A 场景就该 FAIL → 探针整体 FAIL。"""
    doc, cmd = _setup(tmp_path)
    doc.write_text("没有任何表格\n", encoding="utf-8")
    rc = dp.main(["--doc", str(doc), "--block", BLOCK, "--lines", "2", "--cmd", cmd])
    assert rc == 1
    out = capsys.readouterr().out
    assert "PROBE FAIL" in out


def test_probe_flags_missing_doc(tmp_path, capsys):
    rc = dp.main(["--doc", str(tmp_path / "nope.md"), "--block", BLOCK,
                  "--lines", "2", "--cmd", "python -V"])
    assert rc == 1
    assert "DOC_MISSING" in capsys.readouterr().out


def test_probe_json_shape(tmp_path, capsys):
    import json
    doc, cmd = _setup(tmp_path)
    rc = dp.main(["--doc", str(doc), "--block", BLOCK, "--lines", "2", "--cmd", cmd, "--json"])
    assert rc == 0
    doc_json = json.loads(capsys.readouterr().out)
    assert doc_json["ok"] is True and doc_json["source_unchanged"] is True
    assert [s["name"] for s in doc_json["scenarios"]] == [
        "A 原样", "B 手改一字", "C 起始行重复", "D 块被截断", "E 命令失败"]
    assert all(s["ok"] for s in doc_json["scenarios"])
    assert doc_json["sha256_before"] == doc_json["sha256_after"]
