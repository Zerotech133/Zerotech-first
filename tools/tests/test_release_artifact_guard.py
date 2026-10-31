# -*- coding: utf-8 -*-
"""K-ORG-20261031-ARTLOCK：release artifact guard 单测（tmp 假 artifact，不碰真实 _vault）。

覆盖：
- --check：一致 / 同尺寸内容篡改 / 缺文件 / 空 manifest / junction drift / 缺 current.json / 缺 root；
- --check 零写入（mtime/mode 不变）；
- --lock：只锁 artifact 目录内文件（含 manifest.json），不锁 current.json / junction；幂等；
- --unlock：只解除记录文件、不删文件、可幂等；sha 变化则保持锁定并报 changed；
- --lock 拒绝不一致 artifact（不写 lock manifest）；
- CLI `--json` / `--version` 契约与 exit code。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "release_artifact_guard.py"
FILES = {"app.js": b"A" * 1000, "app.css": b"B" * 500, "a0-mainchat.js": b"C" * 300}


def _load():
    spec = importlib.util.spec_from_file_location("artifact_guard_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr or r.stdout
    else:
        os.symlink(str(target), str(link), target_is_directory=True)


def _chmod_writable(p: Path) -> None:
    try:
        os.chmod(p, stat.S_IMODE(p.stat().st_mode) | 0o200)
    except OSError:
        pass


def _cleanup(root: Path) -> None:
    """把测试 root 下所有文件复原为可写（防 Windows 只读文件阻塞 pytest 清理）。"""
    for p in root.rglob("*"):
        if p.is_file():
            _chmod_writable(p)


def _make_artifact(parent: Path, name: str = "v1", files=None) -> tuple[Path, dict]:
    art = parent / name
    art.mkdir(parents=True)
    payload = dict(files or FILES)
    for fname, data in payload.items():
        (art / fname).write_bytes(data)
    manifest = {"schema": "legion-ide-release/v1", "version": name, "commit": "abc123",
                "files": {n: {"sha256": _sha(d), "bytes": len(d)} for n, d in payload.items()}}
    (art / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return art, manifest


def _make_root(tmp_path: Path) -> Path:
    rel = tmp_path / "release"
    art, _ = _make_artifact(rel)
    (rel / "current.json").write_text(json.dumps(
        {"version": "v1", "dir": str(art), "promoted_at": "2026-10-31T14:00:00+08:00"}),
        encoding="utf-8")
    _make_link(rel / "current", art)
    return rel


@pytest.fixture()
def rel(tmp_path):
    root = _make_root(tmp_path)
    yield root
    _cleanup(tmp_path)


# ── --check ────────────────────────────────────────────────────────────────

def test_check_ok(rel):
    m = _load()
    res = m.check(rel)
    assert res["ok"] is True and res["exit_code"] == 0
    assert res["junction"]["drift"] is False
    assert res["summary"]["files_total"] == len(FILES)
    assert res["summary"]["files_ok"] == len(FILES)
    assert all(v["match"] for v in res["files"].values())


def test_check_detects_same_size_content_tamper(rel):
    m = _load()
    (rel / "v1" / "app.js").write_bytes(b"Z" * 1000)   # 尺寸不变、内容变
    res = m.check(rel)
    assert res["exit_code"] == 1 and res["ok"] is False
    info = res["files"]["app.js"]
    assert info["size_match"] is True and info["match"] is False
    assert any("files_mismatch" in e for e in res["errors"])


def test_check_detects_missing_file(rel):
    m = _load()
    (rel / "v1" / "app.css").unlink()
    res = m.check(rel)
    assert res["exit_code"] == 1
    assert res["files"]["app.css"]["exists"] is False
    assert res["files"]["app.css"]["match"] is False


def test_check_detects_empty_manifest_files(rel):
    m = _load()
    (rel / "v1" / "manifest.json").write_text(json.dumps({"files": {}}), encoding="utf-8")
    res = m.check(rel)
    assert res["exit_code"] == 1
    assert any("manifest_files_empty" in e for e in res["errors"])


def test_check_detects_junction_drift(tmp_path):
    m = _load()
    rel = _make_root(tmp_path)
    try:
        # 新建另一个版本目录，junction 故意指向它，指针仍指 v1
        art2, _ = _make_artifact(rel, "v2")
        link = rel / "current"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)
        else:
            os.unlink(str(link))
        _make_link(link, art2)
        res = m.check(rel)
        assert res["exit_code"] == 1
        assert res["junction"]["drift"] is True
        assert any("junction_drift" in e for e in res["errors"])
    finally:
        _cleanup(tmp_path)


def test_check_missing_current_json_is_env_error(rel):
    m = _load()
    (rel / "current.json").unlink()
    res = m.check(rel)
    assert res["exit_code"] == 2
    assert any("current_json_missing" in e for e in res["errors"])


def test_check_missing_release_root_is_env_error(tmp_path):
    m = _load()
    res = m.check(tmp_path / "nope")
    assert res["exit_code"] == 2
    assert any("release_root_missing" in e for e in res["errors"])


def test_check_is_read_only(rel):
    m = _load()
    def snap():
        return {p.name: (p.stat().st_size, p.stat().st_mtime_ns, stat.S_IMODE(p.stat().st_mode))
                for p in (rel / "v1").iterdir()}
    before = snap()
    m.check(rel)
    assert snap() == before
    assert not (rel / m.LOCK_MANIFEST_NAME).exists()


# ── --lock / --unlock ──────────────────────────────────────────────────────

def _locked_files(rel: Path) -> list[Path]:
    return [rel / "v1" / n for n in ("manifest.json", *FILES.keys())]


def test_lock_sets_readonly_but_not_pointer_or_junction(rel):
    m = _load()
    res = m.lock(rel)
    assert res["ok"] is True and res["exit_code"] == 0, res
    for p in _locked_files(rel):
        assert not (stat.S_IMODE(p.stat().st_mode) & 0o200), "应只读: %s" % p
    # 指针与 junction 不动
    assert stat.S_IMODE((rel / "current.json").stat().st_mode) & 0o200
    assert m._link_target(rel / "current")
    # lock manifest 记录 + 自身可写
    lm = json.loads((rel / m.LOCK_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert lm["state"] == "locked" and lm["schema"].endswith("/lock_manifest")
    assert {r["name"] for r in lm["files"]} == {"manifest.json", *FILES.keys()}
    assert stat.S_IMODE((rel / m.LOCK_MANIFEST_NAME).stat().st_mode) & 0o200
    # 锁定后 check 仍一致（只读计数 >0）
    chk = m.check(rel)
    assert chk["exit_code"] == 0 and chk["summary"]["readonly_files"] >= len(FILES)


def test_lock_is_idempotent(rel):
    m = _load()
    first = m.lock(rel)
    second = m.lock(rel)
    assert first["exit_code"] == 0 and second["exit_code"] == 0
    assert second["summary"]["already_locked"] == second["summary"]["total"]
    assert second["summary"]["locked"] == 0


def test_lock_refuses_inconsistent_artifact(rel):
    m = _load()
    (rel / "v1" / "app.js").write_bytes(b"Z" * 1000)
    res = m.lock(rel)
    assert res["ok"] is False and res["exit_code"] == 1
    assert any("lock_refused" in e for e in res["errors"])
    assert not (rel / m.LOCK_MANIFEST_NAME).exists()
    # 未锁任何文件
    assert stat.S_IMODE((rel / "v1" / "app.css").stat().st_mode) & 0o200


def test_unlock_restores_recorded_files_only_and_no_delete(rel):
    m = _load()
    names_before = sorted(p.name for p in (rel / "v1").iterdir())
    m.lock(rel)
    res = m.unlock(rel)
    assert res["ok"] is True and res["exit_code"] == 0, res
    for p in _locked_files(rel):
        assert stat.S_IMODE(p.stat().st_mode) & 0o200, "应恢复可写: %s" % p
    assert (rel / "current.json").is_file()
    assert m._link_target(rel / "current")
    names_after = sorted(p.name for p in (rel / "v1").iterdir())
    assert names_after == names_before          # 不删任何文件
    lm = json.loads((rel / m.LOCK_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert lm["state"] == "unlocked" and "unlocked_at" in lm
    # 幂等
    again = m.unlock(rel)
    assert again["exit_code"] == 0


def test_unlock_missing_lock_manifest_is_noop(rel):
    m = _load()
    res = m.unlock(rel)
    assert res["exit_code"] == 0 and res.get("note") == "no_lock_manifest"


def test_unlock_keeps_changed_file_locked_and_reports(rel):
    m = _load()
    m.lock(rel)
    target = rel / "v1" / "app.js"
    _chmod_writable(target)                     # 模拟旁路进程强行改写
    target.write_bytes(b"Z" * 1000)
    res = m.unlock(rel)
    assert res["exit_code"] == 1
    assert "app.js" in res["changed"]
    assert res["summary"]["changed_kept_locked"] == 1
    lm = json.loads((rel / m.LOCK_MANIFEST_NAME).read_text(encoding="utf-8"))
    assert lm["state"] == "partially_locked"


def test_lock_unlock_cycle_no_file_content_change(rel):
    m = _load()
    before = {p.name: p.read_bytes() for p in (rel / "v1").iterdir()}
    m.lock(rel)
    m.unlock(rel)
    after = {p.name: p.read_bytes() for p in (rel / "v1").iterdir()}
    assert after == before


# ── CLI / 契约 ─────────────────────────────────────────────────────────────

def test_cli_version_contract(capsys):
    m = _load()
    rc = m.main(["--version"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == m.SCHEMA
    assert payload["tool"] == m.TOOL_NAME
    assert payload["modes"] == ["--check", "--lock", "--unlock"]


def test_cli_check_json_contract(rel, capsys):
    m = _load()
    rc = m.main(["--check", "--json", "--release-root", str(rel)])
    assert rc == 0
    res = json.loads(capsys.readouterr().out)
    assert res["schema"] == m.SCHEMA and res["mode"] == "check"
    assert res["ok"] is True and res["exit_code"] == 0
    for key in ("pointer", "junction", "manifest", "files", "summary"):
        assert key in res


def test_cli_check_failure_exit_code(tmp_path, capsys):
    m = _load()
    rel = _make_root(tmp_path)
    try:
        (rel / "v1" / "app.css").unlink()
        rc = m.main(["--check", "--json", "--release-root", str(rel)])
        assert rc == 1
        res = json.loads(capsys.readouterr().out)
        assert res["exit_code"] == 1 and res["summary"]["files_bad"] == 1
    finally:
        _cleanup(tmp_path)


def test_cli_lock_unlock_json_and_custom_manifest(rel, tmp_path, capsys):
    m = _load()
    custom = tmp_path / "custom_lock.json"
    rc = m.main(["--lock", "--json", "--release-root", str(rel), "--lock-manifest", str(custom)])
    assert rc == 0
    res = json.loads(capsys.readouterr().out)
    assert res["mode"] == "lock" and res["lock_manifest"] == str(custom)
    assert custom.is_file()
    rc = m.main(["--unlock", "--json", "--release-root", str(rel), "--lock-manifest", str(custom)])
    assert rc == 0
    res = json.loads(capsys.readouterr().out)
    assert res["mode"] == "unlock" and res["summary"]["unlocked"] == res["summary"]["total"]


def test_tool_never_writes_outside_release_root(rel, tmp_path):
    """check/lock/unlock 只应触碰 release root（含 lock manifest）与 artifact 权限。"""
    m = _load()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    outside_stat = outside.stat().st_mtime_ns
    m.check(rel)
    m.lock(rel)
    m.unlock(rel)
    assert outside.stat().st_mtime_ns == outside_stat
    # release root 之外没有新文件
    extras = [p for p in tmp_path.iterdir() if p.name not in ("release", "outside.txt")]
    assert extras == []
