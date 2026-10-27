# -*- coding: utf-8 -*-
"""breadth_crossing_monitor 单元测试（复盘落地 2026-10-14 breadth-mon）。

覆盖：45% 上穿、5 日内跌回 40% 假穿越、满 5 日确认保持、播种模式不告警、
同锚幂等、dry 不写状态、多锚一次推进、穿越后同窗再上穿不重复触发。
运行：python -m pytest test_breadth_crossing_monitor.py -q
"""
import json
import os
import tempfile

import breadth_crossing_monitor as bcm

HERE = os.path.dirname(os.path.abspath(__file__))


def _mk(tmp, name, obj):
    fp = os.path.join(tmp, name)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return fp


def _series_fp(tmp, pairs):
    return _mk(tmp, "series.json", {
        "dates": [d for d, _ in pairs], "breadth": [b for _, b in pairs]})


def test_cross_up_and_confirm(tmp_path=None):
    """先上穿 45，随后 5 日均 ≥40 → CROSS_UP + CONFIRM_HOLD。"""
    pairs = [("20260101", 0.40), ("20260102", 0.46),   # D1 上穿
             ("20260105", 0.42), ("20260106", 0.41),
             ("20260107", 0.44), ("20260108", 0.43),   # D2..D4
             ("20260109", 0.45)]                        # D5 满 5 日
    with tempfile.TemporaryDirectory() as tmp:
        sfp = _series_fp(tmp, pairs)
        ev1, st = bcm.advance(pairs[1:2], prev_br=pairs[0][1])  # 基准锚宽度
        kinds = [e["kind"] for e in ev1]
        assert kinds == ["CROSS_UP"], kinds
        assert st["pending"] == {"cross_date": "20260102", "days_since": 0}
        ev2, st2 = bcm.advance(pairs[2:], st, prev_br=pairs[1][1])
        kinds2 = [e["kind"] for e in ev2]
        assert kinds2 == ["CONFIRM_HOLD"], kinds2
        assert st2["pending"] is None
        assert st2["last_date"] == "20260109"


def test_fake_exit_within_5_days():
    """上穿后第 3 日跌回 40 以下 → FAKE_EXIT，观察态清空。"""
    pairs = [("20260101", 0.44), ("20260102", 0.47),   # 上穿
             ("20260105", 0.43), ("20260106", 0.42),
             ("20260107", 0.38)]                        # D3 跌破 40
    ev1, st = bcm.advance(pairs[1:2], prev_br=pairs[0][1])
    assert [e["kind"] for e in ev1] == ["CROSS_UP"]
    ev2, st2 = bcm.advance(pairs[2:], st, prev_br=pairs[1][1])
    kinds = [e["kind"] for e in ev2]
    assert kinds == ["FAKE_EXIT"], kinds
    assert ev2[0]["detail"]["days_since"] == 3
    assert ev2[0]["detail"]["cross_date"] == "20260102"
    assert st2["pending"] is None


def test_seed_mode_no_events_and_idempotent(tmp_path=None):
    """monitor_run 首跑播种：不产生事件；再跑同锚幂等零事件。"""
    pairs = [("20260101", 0.30), ("20260102", 0.50),   # 历史上穿也要被播种吃掉
             ("20260105", 0.48)]
    with tempfile.TemporaryDirectory() as tmp:
        sfp = _series_fp(tmp, pairs)
        stfp = os.path.join(tmp, "state.json")
        evfp = os.path.join(tmp, "events.jsonl")
        assert bcm.monitor_run(series_fp=sfp, state_fp=stfp,
                               events_fp=evfp, send=False) == []
        st = json.load(open(stfp, encoding="utf-8"))
        assert st["last_date"] == "20260105"
        assert not os.path.exists(evfp)
        # 再跑：无新锚 → 零事件，状态不变
        assert bcm.monitor_run(series_fp=sfp, state_fp=stfp,
                               events_fp=evfp, send=False) == []
        assert json.load(open(stfp, encoding="utf-8"))["last_date"] == "20260105"


def test_incremental_after_seed(tmp_path=None):
    """播种（穿越发生前）→ 新锚先上穿后假穿越 → 两事件落 jsonl。"""
    pairs = [("20260101", 0.30), ("20260102", 0.52), ("20260105", 0.36)]
    with tempfile.TemporaryDirectory() as tmp:
        sfp = _series_fp(tmp, pairs[:1])               # 播种在穿越发生前
        stfp = os.path.join(tmp, "state.json")
        evfp = os.path.join(tmp, "events.jsonl")
        bcm.monitor_run(series_fp=sfp, state_fp=stfp, events_fp=evfp,
                        send=False)
        # 追加新锚（上穿 + 假穿越）
        _mk(tmp, "series.json", {"dates": [d for d, _ in pairs],
                                 "breadth": [b for _, b in pairs]})
        evs = bcm.monitor_run(series_fp=sfp, state_fp=stfp, events_fp=evfp,
                              send=False)
        assert [e["kind"] for e in evs] == ["CROSS_UP", "FAKE_EXIT"], evs
        lines = open(evfp, encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 2 and "FAKE_EXIT" in lines[1]
        st = json.load(open(stfp, encoding="utf-8"))
        assert st["last_date"] == "20260105" and st["pending"] is None


def test_dry_mode_no_state_write():
    """dry：播种先行落状态，dry 推进新锚（穿越+假穿越）不写状态。"""
    pairs = [("20260101", 0.30), ("20260102", 0.50), ("20260105", 0.36)]
    with tempfile.TemporaryDirectory() as tmp:
        sfp = _series_fp(tmp, pairs[:1])
        stfp = os.path.join(tmp, "state.json")
        evfp = os.path.join(tmp, "e.jsonl")
        bcm.monitor_run(series_fp=sfp, state_fp=stfp, events_fp=evfp,
                        send=False)                    # 播种 → 落状态
        assert os.path.exists(stfp)
        before = open(stfp, encoding="utf-8").read()
        _mk(tmp, "series.json", {"dates": [d for d, _ in pairs],
                                 "breadth": [b for _, b in pairs]})
        evs = bcm.monitor_run(series_fp=sfp, state_fp=stfp, events_fp=evfp,
                              send=False, dry=True)    # dry：可推演不落
        assert [e["kind"] for e in evs] == ["CROSS_UP", "FAKE_EXIT"]
        assert open(stfp, encoding="utf-8").read() == before


def test_missing_series_fail_open():
    """序列文件缺失 → monitor_run 不抛、返回空（fail-open）。"""
    with tempfile.TemporaryDirectory() as tmp:
        assert bcm.monitor_run(
            series_fp=os.path.join(tmp, "nope.json"),
            state_fp=os.path.join(tmp, "state.json"),
            events_fp=os.path.join(tmp, "e.jsonl"), send=False) == []


def test_re_cross_after_fake_exit():
    """假穿越撤回后再上穿 → 允许新 CROSS_UP（撤回可翻案）。"""
    seq = [("d0", 0.44), ("d1", 0.47), ("d2", 0.43), ("d3", 0.39),
           ("d4", 0.46), ("d5", 0.41)]
    ev1, st = bcm.advance(seq[1:2], prev_br=seq[0][1])
    assert [e["kind"] for e in ev1] == ["CROSS_UP"]
    ev2, st2 = bcm.advance(seq[2:4], st, prev_br=seq[1][1])
    assert [e["kind"] for e in ev2] == ["FAKE_EXIT"]
    ev3, st3 = bcm.advance(seq[4:5], st2, prev_br=seq[3][1])
    assert [e["kind"] for e in ev3] == ["CROSS_UP"], ev3
    assert st3["pending"]["cross_date"] == "d4"


def test_alert_texts_contain_action():
    """三类事件文本均含行动建议与出处（告警自解释）。"""
    for kind, det in [
        ("CROSS_UP", {"prev_txt": "43.00%", "breadth_txt": "46.00%",
                      "th": 0.45}),
        ("FAKE_EXIT", {"cross_date": "d1", "days_since": 3,
                       "breadth_txt": "38.00%"}),
        ("CONFIRM_HOLD", {"cross_date": "d1", "breadth_txt": "44.00%"})]:
        t = bcm._alert_text({"kind": kind, "date": "dX", "detail": det})
        assert "行动" in t and ("撤回" in t or "评估" in t or "维持" in t), t


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
