# -*- coding: utf-8 -*-
"""K-ORG-20261031-BUNDLEB5：bundle_bootstrap 幂等/失败可见/无写入测试。"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import bundle_bootstrap as B  # noqa: E402


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "portal").mkdir(parents=True)
    (r / "tools").mkdir()
    (r / "tools" / "build_agent_ide_bundle.py").write_text("# fake\n", encoding="utf-8")
    return r


def test_status_missing(tmp_path):
    r = _repo(tmp_path)
    st = B.status(r, deploy_path=tmp_path / "deploy.json")
    assert st["bundle"]["exists"] is False
    assert st["generator"]["exists"] is True


def test_present_is_idempotent_no_runner(tmp_path):
    r = _repo(tmp_path)
    (r / "portal" / "agent_ide_bundle.json").write_text('{"ok":1}', encoding="utf-8")
    called = []
    out = B.bootstrap(r, deploy=False, runner=lambda *a, **k: called.append(1))
    assert out["ok"] and out["action"] == "present"
    assert called == []
    assert out["generated"] is False


def test_missing_generates_and_deploys(tmp_path):
    r = _repo(tmp_path)
    dep = tmp_path / "deploy" / "bundle.json"

    def runner(cmd, **kw):
        (r / "portal" / "agent_ide_bundle.json").write_text('{"generated":1}', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="built", stderr="")

    out = B.bootstrap(r, deploy=True, deploy_path=dep, runner=runner)
    assert out["ok"] and out["generated"] and out["action"] == "generated"
    assert dep.is_file()
    assert json.loads(dep.read_text(encoding="utf-8")) == {"generated": 1}


def test_dry_run_does_not_write(tmp_path):
    r = _repo(tmp_path)
    out = B.bootstrap(r, dry_run=True, deploy=False)
    assert out["ok"] and out["action"] == "would_generate"
    assert not (r / "portal" / "agent_ide_bundle.json").exists()


def test_missing_generator_fails_visible(tmp_path):
    r = _repo(tmp_path)
    (r / "tools" / "build_agent_ide_bundle.py").unlink()
    out = B.bootstrap(r, deploy=False)
    assert out["ok"] is False
    assert out["action"] == "failed"
    assert out["error"].startswith("generator_missing")


def test_build_rc_nonzero_fails_visible(tmp_path):
    r = _repo(tmp_path)
    out = B.bootstrap(r, deploy=False,
                      runner=lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="boom"))
    assert out["ok"] is False
    assert "build_failed" in out["error"]


def test_check_exit_codes(tmp_path):
    r = _repo(tmp_path)
    assert B.main(["--check", "--repo", str(r), "--deploy-path", str(tmp_path / "d.json")]) == 3
    (r / "portal" / "agent_ide_bundle.json").write_text("{}", encoding="utf-8")
    assert B.main(["--check", "--repo", str(r), "--deploy-path", str(tmp_path / "d.json")]) == 0


def test_gate_bootstraps_before_tests():
    gate = (ROOT / "tools" / "test_gate.ps1").read_text(encoding="utf-8")
    assert "bundle_bootstrap.py" in gate
    assert "--check" in gate
    assert "bundle missing; generating before gate" in gate
    # 生成失败必须 fail-visible（exit 2），不能继续假绿
    assert "exit 2" in gate


def test_start_bootstraps_before_serve():
    start = (ROOT / "ops" / "start_legion_ide.ps1").read_text(encoding="utf-8")
    assert "bundle_bootstrap.py" in start
    assert "bundle bootstrap failed" in start
    assert "exit 1" in start
