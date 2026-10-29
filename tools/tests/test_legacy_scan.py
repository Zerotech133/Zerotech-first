# -*- coding: utf-8 -*-
"""legacy_scan 引用链扫描测试（P0-2）：0 引用 → L3，仅文档 → L2，代码引用 → referenced。"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import legacy_scan as ls  # noqa: E402


def _mk(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_level_classification(tmp_path):
    # 目标目录三份文件：一个被代码引用、一个只被文档引用、一个无引用
    _mk(tmp_path, "old/a.py", "def a(): pass\n")
    _mk(tmp_path, "old/b.py", "def b(): pass\n")
    _mk(tmp_path, "old/c.py", "def c(): pass\n")
    _mk(tmp_path, "mod/use_a.py", "import old.a as a  # old/a.py\n")
    _mk(tmp_path, "docs/ref_b.md", "见 old/b.py 说明\n")
    res = ls.scan_paths(["old"], root=tmp_path)
    r = res["old"]
    assert r["kind"] == "dir"
    assert r["files"] == 3
    assert r["code_hits"] >= 1
    # 目录整体因 a 被代码引用 → referenced
    assert r["level_hint"] == "referenced"

    # 单独扫 b：仅文档引用 → L2 候选
    res_b = ls.scan_paths(["old/b.py"], root=tmp_path)
    assert res_b["old/b.py"]["level_hint"] == "L2_candidate_doc_only"
    # 单独扫 c：0 引用 → L3 候选
    res_c = ls.scan_paths(["old/c.py"], root=tmp_path)
    assert res_c["old/c.py"]["level_hint"] == "L3_candidate"


def test_missing_path_reports_error(tmp_path):
    res = ls.scan_paths(["nope/missing.py"], root=tmp_path)
    assert res["nope/missing.py"]["error"] == "path_not_found"
