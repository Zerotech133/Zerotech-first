# -*- coding: utf-8 -*-
"""P1 组合层工具测试：composition.py + kernel_guard.py。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


def _run(args, cwd=ROOT):
    p = subprocess.run([PY, "-X", "utf8", *args], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    return p.returncode, p.stdout or "", p.stderr or ""


def test_manifests_schema_pass():
    rc, out, err = _run(["tools/composition.py", "validate", "--all"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    assert "6 个组合" in out or "组合" in out


def test_templates_schema_pass():
    for name in ("legion", "product", "workflow", "data"):
        path = ROOT / "compositions" / "_templates" / f"template-{name}.yaml"
        assert path.exists(), f"missing {path}"
        rc, out, err = _run(["tools/composition.py", "validate", str(path)])
        assert rc == 0, f"{name}: rc={rc}\n{out}\n{err}"


def test_registry_consistent():
    reg = json.loads((ROOT / "compositions" / "registry.json").read_text(encoding="utf-8"))
    ids = {c["id"] for c in reg["compositions"]}
    dirs = {p.parent.name for p in (ROOT / "compositions").glob("*/manifest.yaml")}
    assert ids == dirs


def test_kernel_guard_blocks_kernel_without_adr():
    rc, out, err = _run(["tools/kernel_guard.py", "--files", "platform/kernel_v1.yaml"])
    assert rc == 1, f"rc={rc}\n{out}\n{err}"
    assert "BLOCK" in out


def test_kernel_guard_allows_kernel_with_adr():
    rc, out, err = _run(["tools/kernel_guard.py", "--files", "platform/kernel_v1.yaml", "--adr", "ADR-015"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
    assert "OK" in out


def test_kernel_guard_ignores_non_kernel():
    rc, out, err = _run(["tools/kernel_guard.py", "--files", "README.md"])
    assert rc == 0, f"rc={rc}\n{out}\n{err}"
