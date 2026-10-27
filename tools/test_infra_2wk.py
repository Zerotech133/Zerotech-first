# -*- coding: utf-8 -*-
"""工程基建 2 周项单测：日志轮转 + 测试基线工具。"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

_TOOLS = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------- 日志轮转 ----------------
def test_rotate_skips_small(tmp_path):
    lr = _load("log_rotate")
    f = tmp_path / "small.log"
    f.write_text("x", encoding="utf-8")
    rep = lr.rotate_dir(tmp_path, min_mb=20, dry_run=True)
    assert rep["ok"] is True and not rep["rotated"]


def test_rotate_dry_run_big(tmp_path):
    lr = _load("log_rotate")
    f = tmp_path / "big.log"
    f.write_bytes(b"x" * (2 * 1024 * 1024))
    rep = lr.rotate_dir(tmp_path, min_mb=1, dry_run=True)
    assert len(rep["rotated"]) == 1
    assert rep["rotated"][0]["dry_run"] is True
    assert f.exists() and f.stat().st_size == 2 * 1024 * 1024


def test_rotate_apply_archives_and_truncates(tmp_path):
    lr = _load("log_rotate")
    f = tmp_path / "big.log"
    f.write_bytes(b"y" * (2 * 1024 * 1024))
    rep = lr.rotate_dir(tmp_path, min_mb=1, dry_run=False)
    assert len(rep["rotated"]) == 1 and rep["rotated"][0]["truncated"] is True
    assert f.stat().st_size == 0
    arch = list((tmp_path / "_log_archive").glob("*.gz"))
    assert arch and arch[0].stat().st_size > 0


def test_rotate_no_delete_policy(tmp_path):
    lr = _load("log_rotate")
    f = tmp_path / "big.log"
    f.write_bytes(b"z" * (2 * 1024 * 1024))
    rep = lr.rotate_dir(tmp_path, min_mb=1, dry_run=False, purge_expired=True)
    assert "不删除" in rep["note"]
    assert f.exists()


# ---------------- 测试基线 ----------------
def test_build_cmd_has_timeout_and_deselect():
    tb = _load("test_baseline")
    cmd = tb.build_cmd("python", ["agent_platform/tests"])
    assert "--timeout=90" in cmd
    assert "--deselect" in cmd and len(tb.DESELECT) >= 4


def test_parse_summary():
    tb = _load("test_baseline")
    s = tb.parse_summary("2095 passed, 2 skipped, 1 failed, 0 error in 250s")
    assert s["passed"] == 2095 and s["failed"] == 1 and s["skipped"] == 2


def test_run_baseline_writes_and_delta(tmp_path):
    tb = _load("test_baseline")
    fake = lambda cmd, **kw: SimpleNamespace(
        returncode=0, stdout="2095 passed, 2 skipped in 250s", stderr="")
    rec = tb.run_baseline(py="python", out_dir=tmp_path, runner=fake)
    assert rec["ok"] is True and (tmp_path / "latest.json").exists()
    assert (tmp_path / "latest.md").exists()
    rec2 = tb.run_baseline(py="python", out_dir=tmp_path, runner=fake)
    assert rec2["delta"]["passed"] == 0


def test_collect_only_parser():
    tb = _load("test_baseline")
    fake = lambda cmd, **kw: SimpleNamespace(
        returncode=0,
        stdout="a/b/test_x.py::test_a\na/b/test_x.py::test_b\n", stderr="")
    rep = tb.collect_only(runner=fake)
    assert rep["collected"] == 2 and rep["sample"]
