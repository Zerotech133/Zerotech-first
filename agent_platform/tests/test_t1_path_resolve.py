# -*- coding: utf-8 -*-
"""T1 尾巴收口测试：模块级路径常量 `_resolve(root)` 化（M3 总验收遗留）。

范围：personal_wiring（SCHED_FILE→_sched_file）与 pitch_wiring
（DECKS_FILE→_decks_file）——五处尾巴中仅这两处读写面无参数注入；
其余三处（TASKS_BASE/APPROVALS_DB/EVIDENCE_DB）消费点已是
`store or Default` 形态，天然可注入，无需改造。

验收门：①缺省行为与常量完全一致（零行为变化）；②tmp root 落盘隔离
（save→load 往返 + 不触真仓）；③原子替换语义保持。
"""
import sys
import tempfile
from pathlib import Path

AP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AP))
sys.path.insert(0, str(AP / "domains"))

from personal_wiring import SCHED_FILE, _sched_file, load_schedules, save_schedules  # noqa: E402
from pitch_wiring import DECKS_FILE, _decks_file, load_decks, save_decks  # noqa: E402


def test_sched_default_matches_constant():
    assert _sched_file() == SCHED_FILE


def test_decks_default_matches_constant():
    assert _decks_file() == DECKS_FILE


def test_sched_tmp_root_roundtrip(tmp_path):
    rows = [{"agent": "A0", "at": "2026-10-19 09:00", "action": "晨检"}]
    save_schedules(rows, root=tmp_path)
    assert load_schedules(root=tmp_path) == rows
    assert (tmp_path / "data" / "schedules.json").exists()
    # tmp 落盘不触真仓
    assert load_schedules() != rows or load_schedules() == []


def test_decks_tmp_root_roundtrip(tmp_path):
    rows = [{"deck_id": "d1", "stage": "outline"}]
    save_decks(rows, root=tmp_path)
    assert load_decks(root=tmp_path) == rows
    assert (tmp_path / "data" / "pitch" / "decks.json").exists()


def test_sched_atomic_replace_semantics(tmp_path):
    # .tmp 中间文件被替换消化，目录内只剩目标文件
    save_schedules([{"a": 1}], root=tmp_path)
    save_schedules([{"a": 2}], root=tmp_path)  # 二次写不残留 .tmp
    leftovers = [p.name for p in (tmp_path / "data").glob("*.tmp")]
    assert leftovers == []
    assert load_schedules(root=tmp_path) == [{"a": 2}]


def test_sched_missing_file_returns_empty(tmp_path):
    assert load_schedules(root=tmp_path / "nonexistent") == []


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
