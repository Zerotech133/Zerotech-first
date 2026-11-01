# -*- coding: utf-8 -*-
import json
import tempfile
import os

from xq_pump.cursor import CursorStore


def test_roundtrip_and_exhausted():
    d = tempfile.mkdtemp()
    c = CursorStore(d)
    st = {"102": {"max_id": "100", "exhausted": False},
          "104": {"max_id": None, "exhausted": True}}
    c.save_type("posts", st)
    got = c.load_type("posts")
    assert got["102"]["max_id"] == "100"
    assert got["102"]["exhausted"] is False
    assert got["104"]["exhausted"] is True
    # 文件确实落地
    p = os.path.join(d, "posts_cursor.json")
    assert os.path.exists(p)
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["102"]["max_id"] == "100"


def test_mark_exhausted():
    d = tempfile.mkdtemp()
    c = CursorStore(d)
    c.save_type("posts", {"102": {"max_id": "50", "exhausted": False}})
    c.mark_exhausted("posts", "102")
    got = c.load_type("posts")
    assert got["102"]["exhausted"] is True
    assert got["102"]["max_id"] == "50"


def test_missing_file_returns_empty():
    d = tempfile.mkdtemp()
    c = CursorStore(d)
    assert c.load_type("nope") == {}
