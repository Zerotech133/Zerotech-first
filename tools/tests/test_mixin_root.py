# -*- coding: utf-8 -*-
"""分支感知 Mixin 门单测（2026-10-30 P103）。

验证 `_mixin_integrity.py --check --root <tmp>` 在临时仓库结构上能：
1. 与基线一致 → rc=0；
2. 多出 Handler 方法（基线未含）→ rc=1 且提示"新增"。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "_mixin_integrity.py"


def _mkroot(tmp_path: Path, source: str, handlers, funcs=None) -> Path:
    portal = tmp_path / "portal"
    portal.mkdir(parents=True, exist_ok=True)
    (portal / "portal_api.py").write_text(source, encoding="utf-8")
    (portal / "mixin_baseline.json").write_text(
        json.dumps({"handlers": handlers, "funcs": funcs or []}, ensure_ascii=False),
        encoding="utf-8")
    return tmp_path


def _run(root: Path):
    return subprocess.run([sys.executable, str(TOOL), "--check", "--root", str(root)],
                          capture_output=True, text=True, encoding="utf-8")


def test_clean_root(tmp_path):
    src = "class H:\n    def a(self):\n        pass\n"
    root = _mkroot(tmp_path, src, ["a"])
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "INTEGRITY OK" in r.stdout


def test_added_method_detected(tmp_path):
    src = "class H:\n    def a(self):\n        pass\n    def b(self):\n        pass\n"
    root = _mkroot(tmp_path, src, ["a"])
    r = _run(root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "新增" in (r.stdout + r.stderr)


def test_root_isolated_from_repo(tmp_path):
    """--root 指向临时目录时不得读取主仓基线（防"扫描 master 树"回归）。"""
    src = "class H:\n    def only_here(self):\n        pass\n"
    root = _mkroot(tmp_path, src, ["only_here"])
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr
