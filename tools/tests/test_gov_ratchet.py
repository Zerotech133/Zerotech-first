# -*- coding: utf-8 -*-
"""P1 修正版 D0（ADR-025）：棘轮 cap 纯函数与状态读写回归。

覆盖：
  ① compute_cap 初始/下降跟随/不随增长上调/target 兜底；
  ② enforce 守卫“永不冻结”（cap >= current+1）；
  ③ load_state 缺文件/坏 JSON/坏字段回落默认；
  ④ save_state 原子落盘 + roundtrip；
  ⑤ apply_write off 档展示值；
  ⑥ count_worktrees 只读返回非负数。
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

_SPEC = importlib.util.spec_from_file_location("gov_ratchet", TOOLS / "gov_ratchet.py")
gov = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gov)


def _state(tmp_path, **kw):
    base = {"mode": "off", "cap": None, "target": 15, "ttl": None}
    base.update(kw)
    return base


def test_compute_cap_initial_is_current_plus_one():
    assert gov.compute_cap(61, None, 15) == 62
    assert gov.compute_cap(0, None, 15) == 15  # target 兜底


def test_compute_cap_follows_down_only():
    # 62 -> 当前 50：min(62, 51) = 51
    assert gov.compute_cap(50, 62, 15) == 51
    # 当前反弹到 65：不追涨，保持 56
    assert gov.compute_cap(65, 56, 15) == 56
    # target 托底
    assert gov.compute_cap(10, 12, 15) == 15


def test_enforce_never_freezes():
    st = _state(None, mode="enforce", cap=56)
    assert gov.next_cap(st, 65, "enforce") == 66
    # shadow 档按公式（不追涨）
    assert gov.next_cap(st, 65, "shadow") == 56


def test_load_state_defaults_when_missing_or_bad(tmp_path):
    p = tmp_path / "gov_state.json"
    assert gov.load_state(str(p)) == gov.default_state()
    p.write_text("{bad json", encoding="utf-8")
    assert gov.load_state(str(p)) == gov.default_state()
    p.write_text(json.dumps({"mode": "bogus", "cap": -1, "target": 0, "ttl": "x"}),
                 encoding="utf-8")
    assert gov.load_state(str(p)) == gov.default_state()


def test_load_state_parses_valid(tmp_path):
    p = tmp_path / "gov_state.json"
    p.write_text(json.dumps({"mode": "shadow", "cap": 62, "target": 15, "ttl": 72}),
                 encoding="utf-8")
    st = gov.load_state(str(p))
    assert st["mode"] == "shadow"
    assert st["cap"] == 62
    assert st["ttl"] == 72.0


def test_save_state_roundtrip(tmp_path):
    p = tmp_path / "sub" / "gov_state.json"
    st = _state(None, mode="warn", cap=42, ttl=48)
    out = gov.save_state(st, str(p))
    assert out == str(p)
    assert json.loads(p.read_text(encoding="utf-8"))["cap"] == 42
    assert gov.load_state(str(p))["mode"] == "warn"
    # 无临时文件残留
    assert not [x for x in os.listdir(p.parent) if x.startswith(".gov_state.")]


def test_apply_write_off_keeps_mode_and_sets_display_cap(tmp_path):
    p = tmp_path / "gov_state.json"
    st, _ = gov.apply_write(_state(None), 61, str(p))
    assert st["mode"] == "off"
    assert st["cap"] == 62
    assert gov.load_state(str(p))["cap"] == 62


def test_count_worktrees_readonly_nonnegative():
    n = gov.count_worktrees()
    assert isinstance(n, int) and n >= 0
