# -*- coding: utf-8 -*-
"""W4 验收脚本单测：检查聚合口径、Markdown 渲染、CLI 可执行、真实检查项可跑。"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_acceptance as acc  # noqa: E402


def test_summarize_all_green():
    rep = acc.summarize([
        acc._result("a", True, "ok"),
        acc._result("b", True, "ok", critical=False)])
    assert rep["verdict"] == "PASS" and rep["passed"] == 2


def test_summarize_critical_failure():
    rep = acc.summarize([acc._result("a", False, "bad")])
    assert rep["verdict"] == "FAIL" and rep["critical_failed"] == 1


def test_summarize_warning_only_is_pass():
    rep = acc.summarize([acc._result("warn", False, "hist", critical=False)])
    assert rep["verdict"] == "PASS"


def test_render_markdown_contains_all_checks():
    rep = acc.summarize([acc._result("注册表/路由校验", True, "角色 5"),
                         acc._result("自动化测试", True, "80 passed")])
    md = acc.render_markdown(rep)
    assert "验收报告" in md and "注册表/路由校验" in md
    assert "PASS" in md and "80 passed" in md


def test_real_checks_run_offline():
    # 不跑 pytest、不触网：本地静态项应可执行且返回结构化结果
    for fn in (acc.check_registry, acc.check_secrets, acc.check_targets,
               acc.check_heartbeat, acc.check_ports, acc.check_sop_dry_run,
               acc.check_rollback_materials):
        r = fn()
        assert set(r) >= {"name", "ok", "detail", "critical"}


def test_sop_and_rollback_checks_green():
    sop = acc.check_sop_dry_run()
    rb = acc.check_rollback_materials()
    assert sop["ok"] is True, sop
    assert rb["ok"] is True, rb


def test_cli_json_runs():
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "feishu_acceptance.py")
    r = subprocess.run([sys.executable, script, "--json", "--skip-tests"],
                       capture_output=True, timeout=300)
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    assert data["total"] >= 6
    assert data["verdict"] in ("PASS", "FAIL")
