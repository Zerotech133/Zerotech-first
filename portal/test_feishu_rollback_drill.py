# -*- coding: utf-8 -*-
"""W4 回滚演练单测：材料核验结构、回滚清单完整、结论口径、CLI。"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feishu_rollback_drill as rd  # noqa: E402


def test_artifact_checks_structure():
    checks = rd.artifact_checks()
    assert len(checks) >= 6
    names = [c["name"] for c in checks]
    assert any("canonical" in n for n in names)
    assert any("拉起器" in n for n in names)
    assert any("备份" in n for n in names)
    assert any("计划任务" in n for n in names)
    for c in checks:
        assert set(c) >= {"name", "ok", "critical", "detail"}


def test_canonical_and_backup_present():
    checks = {c["name"]: c for c in rd.artifact_checks()}
    # 本轮已建 canonical 快照 + 旧 bot 备份，应被识别为存在
    assert checks["canonical 凭据快照"]["ok"] is True
    assert checks["旧 bot 代码备份"]["ok"] is True


def test_rollback_plan_complete():
    plan = rd.rollback_plan()
    assert len(plan) >= 5
    text = json.dumps(plan, ensure_ascii=False)
    for kw in ("schtasks", "8097", "canonical", "Copy-Item", "acceptance"):
        assert kw in text, "回滚清单缺：%s" % kw


def test_summarize_verdict():
    ready = rd.summarize([rd._check("x", True)])
    assert ready["verdict"] == "READY"
    notready = rd.summarize([rd._check("x", False, "missing")])
    assert notready["verdict"] == "NOT-READY"


def test_render_contains_plan():
    md = rd.render(rd.summarize([rd._check("canonical 凭据快照", True, "ok")]))
    assert "回滚演练" in md and "回滚步骤" in md


def test_cli_json_runs():
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "feishu_rollback_drill.py")
    r = subprocess.run([sys.executable, script, "--json"],
                       capture_output=True, timeout=120)
    data = json.loads(r.stdout.decode("utf-8", "replace"))
    assert data["verdict"] in ("READY", "NOT-READY")
    assert data["plan"]
