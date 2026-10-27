# -*- coding: utf-8 -*-
"""nav.js 生成物语法烟测（Node 可用时执行；避免浏览器整栏静默失效）。"""
import shutil
import subprocess
from pathlib import Path

import pytest

NAV = Path(__file__).resolve().parent.parent / "portal" / "static" / "nav.js"


def test_nav_js_node_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过 JS 语法检查")
    r = subprocess.run([node, "--check", str(NAV)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
