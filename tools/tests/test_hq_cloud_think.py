# -*- coding: utf-8 -*-
"""部门主管云端思考调度：白名单/提示契约/调用/留证。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_cloud_think as c  # noqa: E402


def test_cloud_leads_are_department_heads():
    assert len(c.CLOUD_LEADS) == 13
    for lead in ("DATA1", "PM1", "QA1", "ARCH1", "D1"):
        assert lead in c.CLOUD_LEADS


def test_prompt_uses_role_and_no_fabrication():
    p = c.build_prompt("DATA1", "数据仓血缘缺口怎么补？", "已知 data-warehouse 100%")
    assert p["agent"] == "DATA1"
    assert p["purpose"] == "dept_think"
    assert "张松" in p["system"] and "数据资源主官" in p["system"]
    assert "不得编造" in p["system"]
    assert "数据仓血缘缺口" in p["user"]


def test_unknown_lead_rejected(capsys):
    assert c.main(["--lead", "A0", "--question", "x"]) == 2
    assert "unknown_or_not_cloud_lead" in capsys.readouterr().err


def test_success_output_and_out(monkeypatch, tmp_path):
    def fake(payload, base, timeout):
        return {"schema": "hq/cloud_think/v1", "lead": payload["agent"],
                "name": "张松", "dept": "数据资源主官",
                "question": payload["user"], "text": '{"conclusion":"ok"}',
                "binding": "glm/glm-5.3", "degraded": False, "latency_ms": 1}
    monkeypatch.setattr(c, "call_cloud", fake)
    out = tmp_path / "think.json"
    assert c.main(["--lead", "DATA1", "--question", "测试", "--out", str(out)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["lead"] == "DATA1" and d["binding"].startswith("glm/")


def test_call_failure_is_fail_visible(monkeypatch, capsys):
    def boom(payload, base, timeout):
        raise RuntimeError("cloud_think_failed: timeout")
    monkeypatch.setattr(c, "call_cloud", boom)
    assert c.main(["--lead", "QA1", "--question", "测试"]) == 2
    assert "cloud_think_error" in capsys.readouterr().err
