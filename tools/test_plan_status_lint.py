# -*- coding: utf-8 -*-
"""test_plan_status_lint.py — 计划 STATUS lint 测试（tmp 假计划目录，不写真实 docs/plans）。

覆盖：扫描口径（顶层生成物/README 排除、子目录 README 计入）、6000 字符边界、
状态分类映射、CLI JSON/人类输出/退出码、只读零写、与 daily_three_ledger 同源（不另立第二套账）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import plan_status_lint as lint_mod


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _plan(name: str, status: str | None = None, body: str = "# 假计划\n") -> str:
    line = ("> STATUS: %s\n\n" % status) if status else ""
    return body + "\n" + line + "正文。\n"


class TestScanScope:
    def test_exclusions_and_counts(self, tmp_path):
        plans = tmp_path / "plans"
        _write(plans / "2026-11-01_good.md", _plan("good", "active（已生效）"))
        _write(plans / "2026-11-01_bad.md", _plan("bad"))
        _write(plans / "README.md", _plan("readme"))  # 顶层 README 排除
        _write(plans / "PLAN_REGISTER_ALL_20261031.md", _plan("reg"))  # 顶层生成台账排除
        _write(plans / "sub/README.md", _plan("sub-readme"))  # 子目录 README 计入
        _write(plans / "sub/2026-11-01_ok.md", _plan("ok", "frozen（只读）"))

        rep = lint_mod.lint(plans)
        assert rep["files_total"] == 6
        assert rep["plan_docs"] == 4
        assert rep["with_status"] == 2
        assert rep["unspecified"] == 2
        assert sorted(rep["unspecified_files"]) == ["2026-11-01_bad.md", "sub/README.md"]
        assert rep["coverage_pct"] == 50.0
        assert rep["status"] == "fail"
        assert rep["schema"] == lint_mod.SCHEMA

    def test_status_beyond_6000_chars_is_unspecified(self, tmp_path):
        plans = tmp_path / "plans"
        body = "# 超长前言计划\n\n" + ("填充行。\n" * 1200)  # >6000 字符后才出现 STATUS
        _write(plans / "2026-11-01_late.md", body + "\n> STATUS: active（太晚）\n")
        rep = lint_mod.lint(plans)
        assert rep["plan_docs"] == 1
        assert rep["unspecified"] == 1

    def test_status_mapping(self, tmp_path):
        plans = tmp_path / "plans"
        _write(plans / "a.md", _plan("a", "active（生效）"))
        _write(plans / "b.md", _plan("b", "declared（active：生效）"))
        _write(plans / "c.md", _plan("c", "pending_approval（待批）"))
        _write(plans / "d.md", _plan("d", "frozen（只读）"))
        _write(plans / "e.md", _plan("e", "superseded（被取代）"))
        _write(plans / "f.md", _plan("f", "done（完工）"))
        rep = lint_mod.lint(plans)
        assert rep["unspecified"] == 0
        assert rep["with_status"] == 6
        assert rep["by_status"] == {"declared": 2, "pending_approval": 1,
                                    "frozen": 1, "superseded": 1, "done": 1}


class TestReadOnly:
    def test_zero_write(self, tmp_path):
        plans = tmp_path / "plans"
        _write(plans / "2026-11-01_good.md", _plan("good", "active（x）"))
        _write(plans / "2026-11-01_bad.md", _plan("bad"))

        def snapshot():
            return {str(p.relative_to(plans)): (hashlib.sha256(p.read_bytes()).hexdigest(),
                                                p.stat().st_mtime_ns)
                    for p in sorted(plans.rglob("*")) if p.is_file()}

        before = snapshot()
        lint_mod.lint(plans)
        assert snapshot() == before


class TestCLI:
    def test_json_and_exit_codes(self, tmp_path, capsys):
        plans = tmp_path / "plans"
        _write(plans / "2026-11-01_bad.md", _plan("bad"))
        rc = lint_mod.main(["--plans-dir", str(plans), "--json"])
        out = capsys.readouterr().out
        rep = json.loads(out)
        assert rc == 1
        assert rep["schema"] == lint_mod.SCHEMA
        assert rep["unspecified"] == 1
        assert rep["unspecified_files"] == ["2026-11-01_bad.md"]
        assert rep["status"] == "fail"

    def test_all_marked_exit_0(self, tmp_path, capsys):
        plans = tmp_path / "plans"
        _write(plans / "2026-11-01_good.md", _plan("good", "active（x）"))
        assert lint_mod.main(["--plans-dir", str(plans), "--json"]) == 0
        rep = json.loads(capsys.readouterr().out)
        assert rep["status"] == "pass" and rep["unspecified"] == 0

    def test_missing_dir_exit_2(self, tmp_path, capsys):
        rc = lint_mod.main(["--plans-dir", str(tmp_path / "nope")])
        assert rc == 2
        assert "计划目录不存在" in capsys.readouterr().out

    def test_usage_error_exit_2(self):
        with pytest.raises(SystemExit) as exc:
            lint_mod.main(["--bogus"])
        assert exc.value.code == 2

    def test_human_output(self, tmp_path, capsys):
        plans = tmp_path / "plans"
        _write(plans / "2026-11-01_bad.md", _plan("bad"))
        _write(plans / "2026-11-01_good.md", _plan("good", "active（x）"))
        rc = lint_mod.main(["--plans-dir", str(plans)])
        human = capsys.readouterr().out
        assert rc == 1
        assert "计划 STATUS lint" in human
        assert "2026-11-01_bad.md" in human
        assert "FAIL" in human
        assert "不写文件" in human


class TestSingleSource:
    def test_reuses_daily_three_ledger_scan(self):
        """口径唯一：直接复用 daily_three_ledger.scan_plans，不另立第二套扫描。"""
        import daily_three_ledger

        assert lint_mod.scan_plans is daily_three_ledger.scan_plans

    def test_default_plans_dir_points_to_repo_plans(self):
        assert lint_mod.DEFAULT_PLANS_DIR.name == "plans"
        assert lint_mod.DEFAULT_PLANS_DIR.parent.name == "docs"
        assert lint_mod.DEFAULT_PLANS_DIR.is_dir()
