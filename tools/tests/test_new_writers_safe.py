# -*- coding: utf-8 -*-
"""tools/tests/test_new_writers_safe.py — 新增 3 个写盘脚本加固（wave32 / task-93）。

覆盖（任务验收 ≥5）：
  1. factor_daily.export_payload：写盘合法 + 二次写入生成写前备份 + 原子写无 tmp 残留；
  2. factor_daily：不可序列化 payload → SafeJsonError，目标文件字节不变（校验失败不覆盖）；
  3. s08_rebalance.export_static：备份 + 原子写 + 二次写入备份内容=旧版；
  4. sepa_scan_daily.export_payload：备份 + 原子写 + 门户 schema 字段保持；
  5. 复审：真实仓库扫描中 3 个脚本风险为低（非中/高）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_new_writers_safe.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
KBC = ROOT / "tools" / "kb_corpus"
sys.path.insert(0, str(KBC))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fd = _load("factor_daily_under_test", ROOT / "tools" / "research" / "factor_daily.py")
s08 = _load("s08_rebalance_under_test", ROOT / "tools" / "research" / "s08_rebalance.py")
ssd = _load("sepa_scan_daily_under_test", ROOT / "tools" / "research" / "sepa_scan_daily.py")


def _patch_backup(monkeypatch, mod, tmp_path):
    orig = mod.backup_then_atomic_write
    monkeypatch.setattr(
        mod, "backup_then_atomic_write",
        lambda path, payload, **kw: orig(path, payload, backup_dir=tmp_path / "backups", **kw))


def _no_tmp(path: Path) -> bool:
    return not list(path.parent.glob(f".{path.name}.*.tmp"))


# ------------------------------------------------------------------ 1. factor_daily
def test_factor_daily_backup_and_atomic(tmp_path, monkeypatch):
    _patch_backup(monkeypatch, fd, tmp_path)
    static = tmp_path / "static"
    first = fd.export_payload({"latest_date": "20261030", "factors": [1, 2]}, static)
    assert first["ok"] is True
    target = static / "factor_daily.json"
    old = target.read_bytes()
    fd.export_payload({"latest_date": "20261031", "factors": [3]}, static)
    backups = list((tmp_path / "backups").glob("factor_daily.*.bak.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == old
    assert json.loads(target.read_text(encoding="utf-8"))["latest_date"] == "20261031"
    assert _no_tmp(target)


# ------------------------------------------------------------------ 2. 校验失败不覆盖
def test_factor_daily_serialization_failure_keeps_target(tmp_path, monkeypatch):
    _patch_backup(monkeypatch, fd, tmp_path)
    static = tmp_path / "static"
    fd.export_payload({"ok": True}, static)
    target = static / "factor_daily.json"
    before = target.read_bytes()
    with pytest.raises(fd.SafeJsonError):
        fd.export_payload({"bad": object()}, static)
    assert target.read_bytes() == before
    assert _no_tmp(target)


# ------------------------------------------------------------------ 3. s08_rebalance
def test_s08_rebalance_backup_and_atomic(tmp_path, monkeypatch):
    _patch_backup(monkeypatch, s08, tmp_path)
    static = tmp_path / "static"
    first = s08.export_static({"strategy": "S08", "n": 50}, static)
    assert first["ok"] is True
    target = static / "s08_shadow.json"
    old = target.read_bytes()
    s08.export_static({"strategy": "S08", "n": 51}, static)
    backups = list((tmp_path / "backups").glob("s08_shadow.*.bak.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == old
    assert json.loads(target.read_text(encoding="utf-8"))["n"] == 51
    assert _no_tmp(target)


# ------------------------------------------------------------------ 4. sepa_scan_daily
def test_sepa_scan_daily_backup_and_atomic(tmp_path, monkeypatch):
    _patch_backup(monkeypatch, ssd, tmp_path)
    static = tmp_path / "static"
    payload = {"generated_at": "2026-11-01T17:40:00+08:00", "scan_date": "20261030",
               "strict": {"universe": 5571, "tt_pass": 12}, "wide": {"tt_pass": 80},
               "note": "研究扫描，不构成投资建议"}
    first = ssd.export_payload(payload, static)
    assert first["ok"] is True
    target = static / "sepa_scan_daily.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    for key in ("generated_at", "scan_date", "strict", "wide", "note"):
        assert key in data, f"门户 schema 缺字段 {key}"
    old = target.read_bytes()
    ssd.export_payload(dict(payload, scan_date="20261031"), static)
    backups = list((tmp_path / "backups").glob("sepa_scan_daily.*.bak.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == old
    assert json.loads(target.read_text(encoding="utf-8"))["scan_date"] == "20261031"
    assert _no_tmp(target)


# ------------------------------------------------------------------ 5. 复审降级
def test_audit_three_scripts_downgraded():
    import static_writer_audit as swa
    records = swa.scan()
    by = {r["script"].replace("\\", "/"): r for r in records}
    for script in ("tools/research/factor_daily.py",
                   "tools/research/s08_rebalance.py",
                   "tools/research/sepa_scan_daily.py"):
        rec = by.get(script)
        assert rec is not None, f"审计未识别：{script}"
        assert rec["risk"] == "low", (script, rec["risk"], rec["risk_reasons"])
        assert rec["flags"]["atomic_write"] is True
        assert rec["flags"]["backup"] is True
