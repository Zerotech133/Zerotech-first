# -*- coding: utf-8 -*-
"""seat_iteration 测试（11 席迭代配置）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import seat_iteration as SI  # noqa: E402


def test_init_and_check(tmp_path):
    made = SI.init(tmp_path)
    assert len(made) == 11
    assert not SI.check(tmp_path)
    items = SI.load_all(tmp_path)
    assert len(items) == 11
    seats = {d["seat"] for d in items}
    for s in ("ARCH1", "DEV1", "DEV5", "DEV8", "OPS1", "DOC1"):
        assert s in seats
    # 再 init 不覆盖
    assert SI.init(tmp_path) == []


def test_render_md(tmp_path):
    SI.init(tmp_path)
    md = SI.render_md(tmp_path)
    for k in ("能力迭代", "本轮目标能力", "导师", "DEV7"):
        assert k in md


def test_validate_requires_evidence(tmp_path):
    SI.init(tmp_path)
    d = SI.load_all(tmp_path)[0]
    d["iteration"]["assessment"]["status"] = "passed"
    d["iteration"]["assessment"]["evidence"] = ""
    errs = SI.validate(d)
    assert any("evidence" in e for e in errs)
