# -*- coding: utf-8 -*-
"""voltarget_ab 采集位测试（全打桩，不触真实数据）。"""

import json
import os
import sys
import tempfile


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import voltarget_ab as va


def _mk(tmp, name, obj):
    fp = os.path.join(tmp, name)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return fp


def _series(date="20261020", br=0.50):
    return {"dates": ["20261019", date], "breadth": [0.44, br]}


def _events(confirmed=False):
    if not confirmed:
        return []
    return [{"kind": "CROSS_UP", "date": "20261019"},
            {"kind": "CONFIRM_HOLD", "date": "20261026"}]


def _write_events(tmp, evs):
    fp = os.path.join(tmp, "ev.jsonl")
    with open(fp, "w", encoding="utf-8") as f:
        for e in evs:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    va.BC_EVENTS_FP = fp
    return fp


def test_waiting_phase_and_config_freeze():
    with tempfile.TemporaryDirectory() as tmp:
        _write_events(tmp, _events(False))          # 无确认 → waiting
        row = va.advance(_mk(tmp, "s.json", _series(br=0.32)),
                         os.path.join(tmp, "st.json"),
                         os.path.join(tmp, "out.jsonl"),
                         os.path.join(tmp, "cfg.json"))
        assert row["phase"] == "waiting_for_cross"
        assert row.get("event") != "PHASE_TRACKING"
        # 预注册判据已冻结且带 sha
        cfg = json.load(open(os.path.join(tmp, "cfg.json"),
                             encoding="utf-8"))
        assert cfg["pre_registration"]["eval_days"] == 60
        assert len(cfg["config_sha256"]) == 16


def test_phase_flip_on_confirm_hold_is_latched():
    with tempfile.TemporaryDirectory() as tmp:
        _write_events(tmp, _events(True))           # 已有确认事件
        sfp = _mk(tmp, "s.json", _series())
        stfp = os.path.join(tmp, "st.json")
        ofp = os.path.join(tmp, "out.jsonl")
        row = va.advance(sfp, stfp, ofp, os.path.join(tmp, "cfg.json"))
        assert row["phase"] == "tracking" and row["event"] == "PHASE_TRACKING"
        # 次日推进：闩锁保持 tracking（即使事件文件被清也不回退）
        _write_events(tmp, [])
        _mk(tmp, "s2.json", _series(date="20261021", br=0.51))
        row2 = va.advance(os.path.join(tmp, "s2.json"), stfp, ofp,
                          os.path.join(tmp, "cfg.json"))
        assert row2["phase"] == "tracking" and "event" not in row2


def test_tamper_detected_by_sha():
    with tempfile.TemporaryDirectory() as tmp:
        _write_events(tmp, _events(False))
        cfp = os.path.join(tmp, "cfg.json")
        va.advance(_mk(tmp, "s.json", _series()), os.path.join(tmp, "st.json"),
                   os.path.join(tmp, "out.jsonl"), cfp)
        cfg = json.load(open(cfp, encoding="utf-8"))
        cfg["pre_registration"]["eval_days"] = 5    # 篡改判据
        with open(cfp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        _, intact, _ = va.ensure_config(cfp)
        assert intact is False                      # sha 校验抓到篡改


def test_idempotent_same_day():
    with tempfile.TemporaryDirectory() as tmp:
        _write_events(tmp, _events(False))
        sfp = _mk(tmp, "s.json", _series())
        ofp = os.path.join(tmp, "out.jsonl")
        args = (sfp, os.path.join(tmp, "st.json"), ofp,
                os.path.join(tmp, "cfg.json"))
        va.advance(*args)
        va.advance(*args)
        rows = [json.loads(l) for l in open(ofp, encoding="utf-8")]
        assert len(rows) == 1                       # 同日不重复
