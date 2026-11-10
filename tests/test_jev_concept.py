# -*- coding: utf-8 -*-
"""题材成分采集回归（tools/jev_concept_fetch.py）—— 2026-11-08

不联网：`XtCloud` 全部走替身。守三件事，每件都对着一个真会静默出错的点：

1. **日期纯净性**：这张表的 start_date/end_date 偶尔失效（判例 #27 同型），
   请求 20261106 却返回 20240305 时**必须抛**，不能把别的日子盖到今天的文件名上。
   开发期就是这条校验先挡住了 `_d8()` 返回带横杠格式的 bug。
2. **同名成员合并**：同一概念里同一只票可能出现多行（不同 desc），
   desc 要保留首个非空、hot 要取最大，不能后写覆盖前写。
3. **幂等**：已有快照且未 --force 时不重拉、不重写（mtime 不变）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (HERE, os.path.join(HERE, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import jev_concept_fetch as jcf  # noqa: E402


class _FakeXt:
    """按预设页返回，模拟 xtcloud 的 {columns, rows, total} 结构。"""

    def __init__(self, pages, cols=None):
        self.pages = list(pages)
        self.cols = cols or ["ts_code", "name", "con_name", "con_code",
                             "trade_date", "desc", "hot_num"]
        self.calls = 0

    def table(self, tbl, code=None, start_date=None, end_date=None,
              limit=100, offset=0):
        self.calls += 1
        if not self.pages:
            return {"columns": self.cols, "rows": [], "total": 0}
        return {"columns": self.cols, "rows": self.pages.pop(0),
                "total": 999999}          # total 故意给假值，翻页不许看它


def _row(con, name, code, sc, day, desc="", hot=None):
    return [con, name, code, sc, day, desc, hot]


@pytest.fixture()
def snapdir(tmp_path, monkeypatch):
    monkeypatch.setattr(jcf, "SNAP_DIR", str(tmp_path / "_jev_concept"))
    return str(tmp_path / "_jev_concept")


def test_date_purity_rejects_foreign_day(monkeypatch):
    """请求 20261106 却返回 20240305 → 抛，且不落盘。"""
    fake = _FakeXt([[_row("000001.KP", "电力", "长江电力", "600900.SH", "20240305")]])
    monkeypatch.setattr(jcf, "XtCloud", lambda *a, **k: fake)
    with pytest.raises(jcf.XtCloudError) as ei:
        jcf.fetch("20261106")
    assert "日期过滤失效" in str(ei.value)


def test_merge_keeps_first_desc_and_max_hot(monkeypatch):
    """同票多行：desc 取首个非空，hot 取最大。"""
    rows = [
        _row("000001.KP", "电力", "川能动力", "000155.SZ", "20261106", "", 10),
        _row("000001.KP", "电力", "川能动力", "000155.SZ", "20261106", "建风电", None),
        _row("000001.KP", "电力", "川能动力", "000155.SZ", "20261106", "后来的", 99),
    ]
    monkeypatch.setattr(jcf, "XtCloud", lambda *a, **k: _FakeXt([rows]))
    concepts, n, warn = jcf.fetch("20261106")
    assert n == 3 and not warn
    m = concepts["电力"]["members"]["000155.SZ"]
    assert m["desc"] == "建风电" and m["hot"] == 99.0
    assert len(concepts["电力"]["members"]) == 1


def test_paging_stops_on_short_page(monkeypatch):
    """total 是假值（999999），翻页必须按「本页不满即止」。"""
    p1 = [_row("1.KP", "A", "甲", "000001.SZ", "20261106")] * jcf.PAGE
    p2 = [_row("2.KP", "B", "乙", "000002.SZ", "20261106")]
    fake = _FakeXt([p1, p2])
    monkeypatch.setattr(jcf, "XtCloud", lambda *a, **k: fake)
    concepts, n, _ = jcf.fetch("20261106")
    assert n == jcf.PAGE + 1 and fake.calls == 2
    assert set(concepts) == {"A", "B"}


def test_write_load_roundtrip_and_day_mismatch(snapdir):
    concepts = {"电力": {"code": "000001.KP",
                        "members": {"000155.SZ": {"name": "川能动力",
                                                  "desc": "", "hot": None}}}}
    p = jcf.write("20261106", concepts, 1)
    assert os.path.exists(p)
    obj = jcf.load("20261106")
    assert obj and obj["n_concept"] == 1 and "电力" in obj["concepts"]
    # 文件名与内容不符 → 当没有，别让调用方拿着错日子往下算
    bad = jcf.snap_path("20261105")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"day": "20261106", "concepts": {}}, f)
    assert jcf.load("20261105") is None


def test_main_is_idempotent(monkeypatch, snapdir, capsys):
    """已有快照且未 --force：不重拉（XtCloud 不被实例化）。"""
    jcf.write("20261106", {"电力": {"code": "1.KP", "members": {}}}, 0)

    def boom(*a, **k):
        raise AssertionError("不该重拉")

    monkeypatch.setattr(jcf, "XtCloud", boom)
    assert jcf.main(["--day", "20261106"]) == 0
    assert "已有快照" in capsys.readouterr().out


def test_empty_day_raises(monkeypatch):
    monkeypatch.setattr(jcf, "XtCloud", lambda *a, **k: _FakeXt([]))
    with pytest.raises(jcf.XtCloudError):
        jcf.fetch("20261106")
