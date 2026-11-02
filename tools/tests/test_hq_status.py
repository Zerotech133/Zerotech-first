# -*- coding: utf-8 -*-
"""指挥部只读状态视图：真实仓库数据可聚合、可 JSON、不写盘。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_status  # noqa: E402


def test_status_shape():
    st = hq_status.build_status()
    assert st["chief"]["agent_id"] == "A0"
    assert st["org"]["agents"] >= 50
    assert len(st["compositions"]) >= 6
    assert st["task_board"]["total"] >= 1
    assert st["channels"]["pages"]["legion"] is True


def test_status_json_serializable():
    st = hq_status.build_status()
    raw = json.dumps(st, ensure_ascii=False)
    back = json.loads(raw)
    assert back["host"] == "lingjie"
    assert "generated_at" in back


def test_markdown_contains_sections():
    md = hq_status.render_markdown(hq_status.build_status())
    assert "零界指挥部" in md
    assert "军团账本" in md
    assert "任务板" in md
    assert "指挥通道" in md


def test_cli_json_stdout_pure(capsys):
    rc = hq_status.main(["--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out)["chief"]["agent_id"] == "A0"
