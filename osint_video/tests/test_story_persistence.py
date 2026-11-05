"""事件写库的幂等性：删成员边重算归属不得崩在唯一约束上。

## 这个文件为什么必须存在

``osint_story.story_key`` 有唯一约束（``osint_story_story_key_key``），而 key
由种子事实唯一确定。正常增量运行时 ``NOT EXISTS`` 会把已归属事实排除，同一条
种子永远不会再来一次——所以这个坑在单测与首轮真跑里都看不见。

但只要发生"重算归属"，它就必然触发：

- 修误合并：删掉错误的成员边，让那条事实重新参与聚类；
- 调阈值后重跑（2026-11-04 把 ``RECHECK_SIMILARITY`` 从 0.85 改到 0.75）；
- 人工重置某条事实的归属。

2026-11-04 真机验证跨窗口召回时就撞在这里：清空 ``osint_story_member`` 后
重跑，``INSERT INTO osint_story`` 直接以唯一约束冲突崩掉，前一条已写入的
成员边留在库里，形成半截状态。修法是 ``ON CONFLICT (story_key) DO UPDATE``，
并用 ``xmax = 0`` 区分"新建"与"复用"，避免 ``stories_created`` 虚报。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from common import db as db_module
from knowledge import story_group as sg
from tests.fakes import FakeDB

_NOW = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)


class FakeEmbedder:
    """固定向量；本轮只有一条事实，不需要区分相似度。"""

    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = list(vectors)

    def embed_texts(self, texts):
        return self.vectors[: len(texts)]


@pytest.fixture
def patch_db(monkeypatch):
    def _install(fake: FakeDB) -> FakeDB:
        monkeypatch.setattr(db_module, "fetch_all", fake.fetch_all)
        monkeypatch.setattr(db_module, "insert_returning", fake.insert_returning)
        monkeypatch.setattr(db_module, "execute", fake.execute)
        return fake

    return _install


def _unit(key: str, title: str) -> dict:
    return {
        "unit_key": key,
        "unit_version": 1,
        "title": title,
        "content": "",
        "source_url": "https://example.com/a",
        "source_link": None,
        "blogger_key": None,
        "resource_key": f"res-{key}",
        "entities": [],
        "topics": [],
        "review_status": "reviewed",
        "created_at": _NOW,
    }


def test_reseed_reuses_existing_story_id(patch_db):
    """同一 seed 事实第二次建事件必须复用既有 story_id，而不是抛唯一约束。"""
    fake = patch_db(FakeDB([_unit("u1", "宁德时代发布麒麟电池")]))

    first = sg.StoryGrouper(embedder=FakeEmbedder([[1.0, 0.0]])).run()
    second = sg.StoryGrouper(embedder=FakeEmbedder([[1.0, 0.0]])).run()

    assert first.stories_created == 1
    assert second.stories_created == 0
    assert second.stories_reused == 1
    # 只真正插入过一次，第二次走的是 ON CONFLICT 分支
    assert len(fake.story_inserts) == 1
    assert fake.story_inserts[0]["story_id"] == 1


def test_insert_sql_declares_conflict_target():
    """SQL 必须带 ON CONFLICT (story_key)：桩模拟得再好也不能替代真语句。"""
    assert "ON CONFLICT (story_key) DO UPDATE" in sg._INSERT_STORY
    assert "xmax = 0" in sg._INSERT_STORY


def test_story_key_is_stable_across_runs():
    """key 由 (source_id, unit_key) 决定，同输入必然同 key——这是复用的前提。"""
    assert sg._story_key("cls.cn", "u1") == sg._story_key("cls.cn", "u1")
    assert sg._story_key("cls.cn", "u1") != sg._story_key("eastmoney.com", "u1")
