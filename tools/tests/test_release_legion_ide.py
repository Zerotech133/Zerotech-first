# -*- coding: utf-8 -*-
"""release_legion_ide：从 HEAD 构建/校验/指针回滚（不依赖生产）。"""
import json
import subprocess
from pathlib import Path

import importlib.util

TOOL = Path(__file__).resolve().parents[1] / "release_legion_ide.py"


def _load():
    spec = importlib.util.spec_from_file_location("rel_ide_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "Hermes/extensions/legion-ide").mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "Hermes/extensions/legion-ide/app.js").write_text("console.log('v1');\n", encoding="utf-8")
    (repo / "Hermes/extensions/legion-ide/app.css").write_text(".a{}\n", encoding="utf-8")
    (repo / "Hermes/extensions/legion-ide/a0-mainchat.js").write_text("// a0\n", encoding="utf-8")
    (repo / "Hermes/extensions/legion-ide/README.md").write_text("# ide\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "v1")
    return repo


def test_build_from_head_not_dirty(tmp_path):
    m = _load()
    repo = _repo(tmp_path)
    # 工作树制造未提交 WIP
    (repo / "Hermes/extensions/legion-ide/app.js").write_text("console.log('WIP');\n", encoding="utf-8")
    res = m.build(repo=repo, release_root=tmp_path / "out", version="v1")
    art = Path(res["dir"]) / "app.js"
    assert art.read_text(encoding="utf-8").strip() == "console.log('v1');"
    assert json.loads((Path(res["dir"]) / "manifest.json").read_text(encoding="utf-8"))["commit"]


def test_verify_tamper(tmp_path):
    m = _load()
    repo = _repo(tmp_path)
    res = m.build(repo=repo, release_root=tmp_path / "out", version="v1")
    d = Path(res["dir"])
    (d / "app.css").write_text(".tampered{}\n", encoding="utf-8")
    try:
        m.verify_dir(d)
        raise AssertionError("should fail")
    except RuntimeError:
        pass


def test_promote_rollback(tmp_path):
    m = _load()
    repo = _repo(tmp_path)
    root = tmp_path / "out"
    v1 = m.build(repo=repo, release_root=root, version="v1")["manifest"]["version"]
    (repo / "Hermes/extensions/legion-ide/app.js").write_text("console.log('v2');\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "v2")
    v2 = m.build(repo=repo, release_root=root, version="v2")["manifest"]["version"]
    m.promote(root, v1)
    m.promote(root, v2)
    assert m.status(root)["current"]["version"] == "v2"
    m.rollback_to(root, v1)
    assert m.status(root)["current"]["version"] == "v1"


def _nested(repo):
    h = repo / "Hermes/hermes-webui-next"
    h.mkdir(parents=True)
    _git(h, "init")
    _git(h, "config", "user.email", "t@t")
    _git(h, "config", "user.name", "t")
    (h / "ok.py").write_text("x = 1\n", encoding="utf-8")
    _git(h, "add", "-A")
    _git(h, "commit", "-m", "base")
    return h


def test_build_blocks_dirty_baseline(tmp_path):
    m = _load()
    repo = _repo(tmp_path)
    h = _nested(repo)
    (h / "dirty.py").write_text("y = 2\n", encoding="utf-8")
    st = m.baseline_status(repo)
    assert st["ok"] is False and st["dirty"] is True
    try:
        m.build(repo=repo, release_root=tmp_path / "out")
        raise AssertionError("should block dirty baseline")
    except RuntimeError:
        pass
    res = m.build(repo=repo, release_root=tmp_path / "out", version="v-allow",
                  allow_dirty_baseline=True)
    assert "hermes_baseline" in res["manifest"]


def test_baseline_absent_is_ok(tmp_path):
    m = _load()
    repo = _repo(tmp_path)
    st = m.baseline_status(repo)
    assert st["ok"] is True and st["exists"] is False


# ── W1（2026-10-31）：promote 必须能同时切 current junction ─────────────────


def _artifact(root, ver, content):
    """最小合法 artifact：app.js + manifest.json（供 verify_dir 校验）。"""
    import hashlib
    d = Path(root) / ver
    d.mkdir(parents=True)
    (d / "app.js").write_bytes(content)
    manifest = {
        "schema": "legion-ide-release/v1", "version": ver, "commit": "test-commit",
        "files": {"app.js": {"sha256": hashlib.sha256(content).hexdigest(),
                             "bytes": len(content)}},
    }
    (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def test_promote_pointer_only_keeps_junction(tmp_path):
    """向后兼容：不传 switch_current 时只写指针，不切 junction（旧调用方行为）。"""
    m = _load()
    root = tmp_path / "rel"
    _artifact(root, "v1", b"one")
    _artifact(root, "v2", b"two")
    m.promote(root, "v1", switch_current=True)
    before = m.status(root)
    assert before["drift"] is False and before["junction"]["version"] == "v1"

    m.promote(root, "v2")  # 旧式调用：只动指针
    st = m.status(root)
    assert st["current"]["version"] == "v2" and st["pointer_version"] == "v2"
    assert st["junction"]["version"] == "v1"  # junction 未动
    assert st["drift"] is True  # 显式暴露「指针变了、目录没切」


def test_promote_switch_current_swaps_pointer_and_junction(tmp_path):
    m = _load()
    root = tmp_path / "rel"
    _artifact(root, "v1", b"one")
    _artifact(root, "v2", b"two")
    res = m.promote(root, "v1", switch_current=True)
    assert res["junction_switch"]["switched"] is True
    res = m.promote(root, "v2", switch_current=True)

    st = m.status(root)
    assert st["pointer_version"] == "v2"
    assert st["junction"]["version"] == "v2"
    assert st["junction"]["target_exists"] is True
    assert st["drift"] is False
    # 旧 artifact 保留为回滚位，不删除
    assert m._path_present(root / "current_prev_v2") is True
    assert Path(res["junction_switch"]["previous_target"]).name == "v1"
    # 生产读取方：current/app.js 能读到新内容
    assert (root / "current" / "app.js").read_bytes() == b"two"


def test_promote_switch_blocks_bad_artifact(tmp_path):
    """artifact 校验失败 → 指针与 junction 一律不动（fail-closed）。"""
    m = _load()
    root = tmp_path / "rel"
    _artifact(root, "v1", b"one")
    m.promote(root, "v1", switch_current=True)
    bad = root / "bad"
    bad.mkdir()
    (bad / "manifest.json").write_text("{}", encoding="utf-8")
    try:
        m.promote(root, "bad", switch_current=True)
        raise AssertionError("should fail on unverifiable artifact")
    except RuntimeError:
        pass
    st = m.status(root)
    assert st["pointer_version"] == "v1"
    assert st["junction"]["version"] == "v1"
    assert st["drift"] is False


def test_switch_dry_run_touches_nothing(tmp_path):
    m = _load()
    root = tmp_path / "rel"
    _artifact(root, "v1", b"one")
    _artifact(root, "v2", b"two")
    m.promote(root, "v1", switch_current=True)
    plan = m.promote(root, "v2", switch_current=True, dry_run=True)
    assert plan["dry_run"] is True and plan["switch"]["planned"] is True
    st = m.status(root)
    assert st["pointer_version"] == "v1"
    assert st["junction"]["version"] == "v1"
    assert st["drift"] is False


def test_status_detects_drift(tmp_path):
    m = _load()
    root = tmp_path / "rel"
    _artifact(root, "v1", b"one")
    _artifact(root, "v2", b"two")
    m.promote(root, "v1", switch_current=True)
    # 模拟 drift：直接把指针改成 v2，junction 仍指 v1
    m._write_current_json(root, {"version": "v2", "dir": str(root / "v2"),
                                 "promoted_at": "x", "note": "drift-test"})
    st = m.status(root)
    assert st["pointer_version"] == "v2"
    assert st["junction"]["version"] == "v1"
    assert st["drift"] is True


def test_rollback_with_switch_current(tmp_path):
    m = _load()
    root = tmp_path / "rel"
    _artifact(root, "v1", b"one")
    _artifact(root, "v2", b"two")
    m.promote(root, "v1", switch_current=True)
    m.promote(root, "v2", switch_current=True)
    m.rollback_to(root, "v1", switch_current=True)
    st = m.status(root)
    assert st["pointer_version"] == "v1"
    assert st["junction"]["version"] == "v1"
    assert st["drift"] is False
    assert (root / "current" / "app.js").read_bytes() == b"one"


def test_switch_rejects_target_outside_root(tmp_path):
    m = _load()
    root = tmp_path / "rel"
    _artifact(root, "v1", b"one")
    try:
        m.switch_current_link(root, "../outside")
        raise AssertionError("should reject path escape")
    except RuntimeError:
        pass
    assert not (root / "current").exists()
