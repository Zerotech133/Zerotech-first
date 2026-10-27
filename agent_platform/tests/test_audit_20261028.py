# -*- coding: utf-8 -*-
"""2026-10-28 审计修复回归测试（WO-AUDIT-20261028-FIX）。"""
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, str(REPO / rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_memory_add_idempotent(tmp_path, monkeypatch):
    MS = _load("agent_platform/memory/store.py", "mem_audit")
    db = tmp_path / "m.sqlite"
    monkeypatch.setattr(MS, "DB", db)
    monkeypatch.setattr(MS, "EVAL_OUT", tmp_path / "eval.json")
    r1 = MS.add("semantic", "cue", "content", mid="semantic:x", db=db)
    r2 = MS.add("semantic", "cue", "content", mid="semantic:x", db=db)
    assert r1["version"] == 1
    assert r2.get("unchanged") is True and r2["version"] == 1
    assert MS.history("semantic:x", db=db)["history"] == []
    # 内容变化仍应新增版本
    r3 = MS.add("semantic", "cue", "content2", mid="semantic:x", db=db)
    assert r3["version"] == 2
    assert len(MS.history("semantic:x", db=db)["history"]) == 1


def test_data_lineage_dir_fingerprint_bounded(tmp_path):
    DL = _load("agent_platform/tools/data_lineage.py", "lineage_audit")
    d = tmp_path / "data"
    d.mkdir()
    (d / "a.bin").write_bytes(b"a" * 100_000)
    (d / "b.txt").write_text("hello", encoding="utf-8")
    h1 = DL._sha256_path(d)
    h2 = DL._sha256_path(d)
    assert h1 == h2 and len(h1) == 64
    (d / "b.txt").write_text("hello2", encoding="utf-8")
    assert DL._sha256_path(d) != h1


def test_seat_realness_help_ok():
    tool = REPO / "agent_platform" / "tools" / "seat_realness.py"
    r = subprocess.run([sys.executable, "-X", "utf8", str(tool), "--help"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "usage" in (r.stdout + r.stderr).lower()
    assert "80%" in r.stdout

def test_memory_package_compat_under_engine_collision():
    """engine 名被其它模块占用时，from memory import MemoryStore 仍须可用。"""
    code = (
        "import sys,types; "
        "sys.modules['engine']=types.ModuleType('engine'); "
        "sys.path.insert(0, r'%s'); "
        "from memory import MemoryStore, PermissionDenied; print(MemoryStore.__name__)"
        % (REPO / "agent_platform")
    )
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, cwd=str(REPO))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "MemoryStore" in r.stdout
