# -*- coding: utf-8 -*-
"""xq_collect_1000 --cursor-file 参数契约测试（双实例游标分片防互踩）。

全部 tmp_path 隔离，不触生产游标 C:/aios/.../seed_cursor.json；
main 全程 mock（FakeCollector + db_count），绝不真跑采集。
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import xq_collect_1000 as M  # noqa: E402

DEFAULT_CURSOR = M.SEED_CURSOR_PATH


@pytest.fixture
def fake_collect(monkeypatch):
    """mock main 采集流程，记录 Collector 构造时的游标档路径。"""
    monkeypatch.setattr(M, "SEED_CURSOR_PATH", DEFAULT_CURSOR)  # 测试后还原全局
    monkeypatch.setattr(M, "db_count", lambda table: 0)
    recorded = {}

    class FakeCollector:
        new = 0
        requests = 0
        rotations = 0

        def __init__(self, target, fresh=False, seed=None):
            recorded["target"] = target
            recorded["seed"] = seed
            recorded["cursor_path_at_init"] = M.SEED_CURSOR_PATH

        def run(self):
            pass

    monkeypatch.setattr(M, "Collector", FakeCollector)
    return recorded


# ── argparse / 路径解析 ──

def test_default_cursor_file_is_default_path(fake_collect, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["xq_collect_1000.py"])
    M.main()
    assert fake_collect["cursor_path_at_init"] == DEFAULT_CURSOR
    assert DEFAULT_CURSOR.endswith("seed_cursor.json")
    # 未传参时全局常量原样（向后兼容：单实例行为不变）
    assert M.SEED_CURSOR_PATH == DEFAULT_CURSOR


def test_explicit_cursor_file_overrides(fake_collect, monkeypatch, tmp_path):
    custom = str(tmp_path / "seed_cursor_A.json")
    monkeypatch.setattr(sys, "argv",
                        ["xq_collect_1000.py", "--cursor-file", custom])
    M.main()
    assert fake_collect["cursor_path_at_init"] == custom
    assert M.SEED_CURSOR_PATH == custom


# ── save_seed_cursor 落档行为 ──

def test_save_seed_cursor_to_custom_path_content(monkeypatch, tmp_path):
    p = tmp_path / "seed_cursor_A.json"
    monkeypatch.setattr(M, "SEED_CURSOR_PATH", str(p))
    M.save_seed_cursor(20000, 102, {"max_id": "123456", "exhausted": False})
    M.save_seed_cursor(20000, 104, {"max_id": None, "exhausted": True})
    data = json.loads(p.read_text(encoding="utf-8"))
    b = data["20000"]
    assert b["102"]["max_id"] == "123456"
    assert b["102"]["exhausted"] is False
    assert b["104"]["max_id"] is None
    assert b["104"]["exhausted"] is True
    assert b["102"]["updated_at"] and b["104"]["updated_at"]
    # 原子落盘无残留 tmp
    assert not (tmp_path / "seed_cursor_A.json.tmp").exists()


def test_default_path_merge_preserves_other_cats(monkeypatch, tmp_path):
    # 模拟默认档已有在途断点，再存不覆盖他桶（默认行为不变）
    p = tmp_path / "seed_cursor.json"
    p.write_text(json.dumps(
        {"30000": {"105": {"max_id": "legacy", "exhausted": False}}}),
        encoding="utf-8")
    monkeypatch.setattr(M, "SEED_CURSOR_PATH", str(p))
    M.save_seed_cursor(30000, 102, {"max_id": "new", "exhausted": False})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["30000"]["105"]["max_id"] == "legacy"   # 旧档保留
    assert data["30000"]["102"]["max_id"] == "new"


def test_load_seed_cursors_custom_path(monkeypatch, tmp_path):
    p = tmp_path / "seed_cursor_B.json"
    p.write_text(json.dumps(
        {"45000": {"102": {"max_id": "abc", "exhausted": False}}}),
        encoding="utf-8")
    monkeypatch.setattr(M, "SEED_CURSOR_PATH", str(p))
    assert M.load_seed_cursors(45000) == {
        "102": {"max_id": "abc", "exhausted": False}}
    assert M.load_seed_cursors(99999) == {}            # 他 seed 不串桶
    # 缺档安全空返回
    monkeypatch.setattr(M, "SEED_CURSOR_PATH",
                        str(tmp_path / "absent.json"))
    assert M.load_seed_cursors(45000) == {}
