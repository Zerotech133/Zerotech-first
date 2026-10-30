# -*- coding: utf-8 -*-
"""release_legion_ide：从 HEAD 构建/校验/指针回滚（不依赖生产）。"""
import json
import subprocess
import sys
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
