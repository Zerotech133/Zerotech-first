# -*- coding: utf-8 -*-
"""breadth_parallel 对照采集器测试（打桩不触网）。"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import breadth_parallel as bp


def _mk(tmp, name, obj):
    fp = os.path.join(tmp, name)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return fp


def _rps(latest="20261014", breadth=0.233):
    return {
        "latest": latest,
        "envs": [
            {"label": "A股全市场(5555只)", "breadth": breadth},
            {"label": "美股全市场", "breadth": 0.39},
        ],
    }


def _series(dates, brs):
    return {"dates": dates, "breadth": brs}


def test_collect_pair_basic():
    with tempfile.TemporaryDirectory() as tmp:
        r = bp.collect_pair(
            _mk(tmp, "r.json", _rps()),
            _mk(tmp, "s.json", _series(["20261012", "20261013"], [0.20, None])),
        )
        assert r["date"] == "20261012"  # 主锚=湖最新【有效】锚（null 跳过）
        assert r["lake_breadth"] == 0.20  # null 已跳过
        assert r["qpzm_breadth"] == 0.233
        assert abs(r["diff"] - (-0.033)) < 1e-9
        assert r["anchor_match"] is False  # 20261013 != 20261014


def test_collect_pair_missing_side():
    with tempfile.TemporaryDirectory() as tmp:
        r = bp.collect_pair(
            _mk(tmp, "r.json", {"envs": []}),  # 无 A股环境
            _mk(tmp, "s.json", _series(["20261013"], [0.5])),
        )
        assert r["qpzm_breadth"] is None and r["diff"] is None
        assert r["lake_breadth"] == 0.5
    with tempfile.TemporaryDirectory() as tmp:
        r = bp.collect_pair(
            os.path.join(tmp, "nope.json"), os.path.join(tmp, "nope2.json")
        )
        assert r["date"] is None  # 两产物全缺


def test_run_idempotent_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        rfp = _mk(tmp, "r.json", _rps())
        sfp = _mk(tmp, "s.json", _series(["20261013"], [0.3264]))
        ofp = os.path.join(tmp, "p.jsonl")
        assert bp.run(rfp, sfp, ofp)
        # 同日改值重跑 → 覆盖不追加
        _mk(tmp, "r2.json", _rps(breadth=0.30))
        bp.run(rfp, os.path.join(tmp, "s.json"), ofp)  # 同 rfp/sfp
        bp.run(rfp, sfp, ofp)
        lines = [json.loads(l) for l in open(ofp, encoding="utf-8")]
        assert len(lines) == 1 and lines[0]["lake_breadth"] == 0.3264


def test_run_all_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        assert (
            bp.run(
                os.path.join(tmp, "a.json"),
                os.path.join(tmp, "b.json"),
                os.path.join(tmp, "p.jsonl"),
            )
            is None
        )
        assert not os.path.exists(os.path.join(tmp, "p.jsonl"))
