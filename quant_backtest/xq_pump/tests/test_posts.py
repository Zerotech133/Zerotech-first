# -*- coding: utf-8 -*-
"""posts 增量抽取测试（FakeSession，沙箱无网仅测纯逻辑）。

验证：游标推进(max_id/next_max_id)、seen 去重、类目到底(exhausted)、
max_new 早停、断点续跑(二次运行 0 新增)。
"""
import os
import tempfile

from xq_pump.core import FakeSession
from xq_pump.cursor import CursorStore
from xq_pump.store import Store
from xq_pump.extractors.posts import collect_posts


def _item(i, created=1700000000000):
    return {"id": i, "created_at": created, "title": "t%d" % i,
            "description": "d%d" % i, "user_id": "u", "target": "",
            "comment_count": 0, "view_count": 1, "retweet_count": 0,
            "like_count": 0, "reward_count": 0, "source": "web"}


def _pages(*lists_with_next):
    return [{"list": lst, "next_max_id": nxt} for lst, nxt in lists_with_next]


def _env():
    d = tempfile.mkdtemp()
    store = Store(os.path.join(d, "t.db"))
    cursor = CursorStore(os.path.join(d, "c"))
    return store, cursor, d


def test_first_run_pulls_new_and_advances_cursor():
    store, cursor, _ = _env()
    pages = _pages(
        ([_item(1), _item(2), _item(3)], "100"),
        ([_item(4), _item(5)], "200"),
        ([], None),  # 空页 → 到底
    )
    sess = FakeSession(pages=pages)
    res = collect_posts(sess, store, cursor, categories=[102], max_pages_per_cat=10)
    assert res["new"] == 5
    assert res["requests"] == 3
    st = cursor.load_type("posts")["102"]
    assert st["exhausted"] is True  # 续翻到空页 → 挖尽
    store.close()


def test_resume_run_pulls_zero_when_exhausted():
    store, cursor, _ = _env()
    pages = _pages(([_item(1), _item(2), _item(3)], "100"), ([], None))
    sess = FakeSession(pages=pages)
    collect_posts(sess, store, cursor, categories=[102], max_pages_per_cat=10)
    # 第二次：类目已 exhausted → 跳过
    sess2 = FakeSession(pages=_pages(([_item(9)], "999"), ([], None)))
    res2 = collect_posts(sess2, store, cursor, categories=[102], max_pages_per_cat=10)
    assert res2["new"] == 0
    store.close()


def test_max_new_stops_early_and_saves_cursor():
    store, cursor, _ = _env()
    pages = _pages(
        ([_item(1), _item(2), _item(3)], "100"),
        ([_item(4), _item(5)], "200"),
    )
    sess = FakeSession(pages=pages)
    res = collect_posts(sess, store, cursor, categories=[102],
                        max_new=3, max_pages_per_cat=10)
    assert res["new"] == 3
    st = cursor.load_type("posts")["102"]
    assert st["exhausted"] is False
    assert st["max_id"] == "100"  # 断点已存，下次从 100 续翻
    store.close()


def test_dedup_within_run():
    store, cursor, _ = _env()
    # 两页含重复 id=1（模拟接口偶发回灌）
    pages = _pages(
        ([_item(1), _item(2)], "100"),
        ([_item(1), _item(3)], "200"),
        ([], None),
    )
    sess = FakeSession(pages=pages)
    res = collect_posts(sess, store, cursor, categories=[102], max_pages_per_cat=10)
    assert res["new"] == 3  # id 1 只算一次
    assert store.count("xq_status") == 3
    store.close()


def test_multiple_categories_independent_cursors():
    store, cursor, _ = _env()
    # 两类目各两页，第二页空；两类目用不同 id（真实雪球不同类目 ID 互斥）
    def mk_a():
        return FakeSession(pages=_pages(([_item(1)], "10"), ([], None)))
    def mk_b():
        return FakeSession(pages=_pages(([_item(2)], "20"), ([], None)))
    sess = {"102": mk_a(), "104": mk_b()}
    res = collect_posts(sess["102"], store, cursor, categories=[102], max_pages_per_cat=10)
    res = collect_posts(sess["104"], store, cursor, categories=[104], max_pages_per_cat=10)
    assert store.count("xq_status") == 2
    st = cursor.load_type("posts")
    assert st["102"]["exhausted"] and st["104"]["exhausted"]
    store.close()
