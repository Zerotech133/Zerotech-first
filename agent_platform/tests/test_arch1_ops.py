# -*- coding: utf-8 -*-
"""ARCH1 架构/技术债周巡检测试（WO-LEGION-ARCH1-PATROL）。"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "agent_platform" / "tools" / "arch1_ops.py"


def _mkroot(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir(parents=True)
    (tmp_path / "tools" / "_mixin_integrity.py").write_text(
        "# stub\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "ADR-001-test.md").write_text(
        "# ADR 001\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "plan-test.md").write_text(
        "# plan\n", encoding="utf-8"
    )
    return tmp_path


def _run(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(TOOL),
            "weekly",
            "--root",
            str(root),
            "--no-emit",
            "--skip-gates",
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _latest(root: Path) -> Path:
    files = sorted(
        (root / "agent_platform" / "data" / "dev" / "arch1").glob("*_patrol.md")
    )
    assert len(files) == 1
    return files[0]


def test_weekly_brief(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root)
    assert proc.returncode == 0, proc.stderr
    text = _latest(root).read_text(encoding="utf-8")
    assert "架构/技术债巡检" in text
    assert "ADR 数量：**1**" in text
    assert "ADR-001-test.md" in text
    assert "质量门" in text


def test_dry_run_zero_write(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["dry_run"] is True
    assert not (root / "agent_platform" / "data" / "dev").exists()


def test_invalid_root_exit_2(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(TOOL),
            "weekly",
            "--root",
            str(tmp_path),
            "--no-emit",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 2
    assert "repo root invalid" in proc.stdout


def test_adr_stale_flag(tmp_path):
    root = _mkroot(tmp_path)
    # 把 ADR mtime 改到 30 天前 → 断供
    import os
    import time

    old = time.time() - 30 * 86400
    os.utime(root / "docs" / "adr" / "ADR-001-test.md", (old, old))
    proc = _run(root)
    assert proc.returncode == 0, proc.stderr
    text = _latest(root).read_text(encoding="utf-8")
    assert "断供" in text
