# -*- coding: utf-8 -*-
"""W4 SOP 执行器单测：dry-run 7 步全绿、无生产副作用、live 缺角色 FAIL、CLI。"""

import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_sop_runner as sop  # noqa: E402


def _h(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_dry_run_has_seven_steps_all_pass():
    steps = sop.dry_run("zb.demo.echo", "零界·演练")
    assert len(steps) == 7
    assert [s["step"] for s in steps] == list(range(1, 8))
    failed = [s for s in steps if not s["ok"]]
    assert not failed, failed


def test_dry_run_no_side_effects_on_production_files():
    before_reg = _h(sop.REGISTRY)
    before_routes = _h(sop.ROUTES)
    sop.dry_run()
    assert _h(sop.REGISTRY) == before_reg
    assert _h(sop.ROUTES) == before_routes


def test_dry_run_temp_dirs_cleaned():
    before = set()
    if os.path.isdir(sop.WORK_TMP):
        before = set(os.listdir(sop.WORK_TMP))
    sop.dry_run()
    after = set()
    if os.path.isdir(sop.WORK_TMP):
        after = set(os.listdir(sop.WORK_TMP))
    assert after - before == set(), "dry-run 应清理临时目录"


def test_live_check_unknown_role_fails():
    steps = sop.live_check("zb.no.such.role")
    assert len(steps) == 1 and steps[0]["ok"] is False


def test_summarize_verdict():
    ok = sop.summarize("r", [sop._step(1, "a", True, "")])
    assert ok["verdict"] == "PASS"
    bad = sop.summarize("r", [sop._step(1, "a", False, "x")])
    assert bad["verdict"] == "FAIL"


def test_render_contains_steps():
    md = sop.render(sop.summarize("r", [sop._step(1, "registry 登记角色", True, "ok")]))
    assert "7 步 SOP" in md and "registry 登记角色" in md


def test_cli_dry_run_json():
    script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "feishu_sop_runner.py"
    )
    r = subprocess.run(
        [sys.executable, script, "--dry-run", "--json"],
        capture_output=True,
        timeout=120,
    )
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    assert data["total"] == 7 and data["verdict"] == "PASS"
