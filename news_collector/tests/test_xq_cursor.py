# -*- coding: utf-8 -*-
"""雪球断点游标验收（判例：2026-11-12 两轮均被强杀，队列尾部 136 只 52.8h 未刷新）。

要锁死的语义：
  1) 旋转从「上次结束处的下一个」开始，且是**无缝环**——尾部跑完接回头部，不漏不重；
  2) 上次止于队尾时 wrapped=True，轮次 +1，下一轮从 0 开始；
  3) 名单变了（last_code 不在队列）就退回原序，不猜；
  4) 游标原子写：写坏也只会留 tmp，不会留半截 JSON。

不启浏览器、不触网（rotate/save 都是纯函数级）。
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "news_collector") not in sys.path:
    sys.path.insert(0, str(REPO / "news_collector"))

import xq_stock_timeline as m  # noqa: E402


CODES = ["600000", "600001", "000001", "300001", "920001", "920002"]


def test_rotate_middle_resumes_after_last():
    ordered, wrapped, idx = m.rotate_from(CODES, "000001")
    assert idx == 2
    assert ordered == ["300001", "920001", "920002", "600000", "600001", "000001"]
    assert wrapped is False


def test_rotate_is_lossless_and_duplicate_free():
    """任何起点旋转后，集合与长度都必须与队列一致——尾部饥饿治理不能靠丢票换。"""
    for start in CODES:
        ordered, _, _ = m.rotate_from(CODES, start)
        assert sorted(ordered) == sorted(CODES)
        assert len(ordered) == len(CODES)


def test_rotate_at_tail_wraps_to_new_round():
    ordered, wrapped, idx = m.rotate_from(CODES, "920002")
    assert idx == len(CODES) - 1
    assert wrapped is True
    assert ordered == CODES


def test_rotate_unknown_code_falls_back_to_original_order():
    """名单变更/首次运行：last_code 不在队列 → 原序、start_idx=-1、不递增轮次。"""
    ordered, wrapped, idx = m.rotate_from(CODES, "999999")
    assert ordered == CODES
    assert (wrapped, idx) == (False, -1)


def test_rotate_empty_cursor_is_noop():
    assert m.rotate_from(CODES, "")[0] == CODES
    assert m.rotate_from(CODES, None)[0] == CODES


def test_cursor_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "_cursor.json"
    monkeypatch.setattr(m, "CURSOR_F", str(f))
    m._save_cursor("920001", "all", "", 6, 2, 40, 3)
    d = m._load_cursor()
    assert d["last_code"] == "920001" and d["round"] == 2
    assert d["scope"] == "all" and d["queue_len"] == 6 and d["quota"] == 40
    assert d["done_this_run"] == 3
    # 原子写：不许留 .tmp 残骸
    assert not os.path.exists(str(f) + ".tmp")


def test_cursor_missing_or_corrupt_returns_empty(tmp_path, monkeypatch):
    f = tmp_path / "_cursor.json"
    monkeypatch.setattr(m, "CURSOR_F", str(f))
    assert m._load_cursor() == {}
    f.write_text("{not json", encoding="utf-8")
    assert m._load_cursor() == {}


def test_cursor_survives_restart_simulation(tmp_path, monkeypatch):
    """模拟「跑到第 3 只被强杀」：游标已落盘，下一轮从第 4 只起。"""
    f = tmp_path / "_cursor.json"
    monkeypatch.setattr(m, "CURSOR_F", str(f))
    for i, code in enumerate(CODES[:3], start=1):
        m._save_cursor(code, "all", "", len(CODES), 1, 0, i)
    cur = m._load_cursor()
    ordered, wrapped, idx = m.rotate_from(CODES, cur["last_code"])
    assert cur["last_code"] == "000001"
    assert ordered[0] == "300001" and wrapped is False
