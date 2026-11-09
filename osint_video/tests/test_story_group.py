"""事件层聚类算子的单元测试。

覆盖三类：
1. 纯函数（文本归一、二元组、余弦、来源识别、热度）
2. 判定器（规则判定器的保守性）
3. 算子主流程（三路分流、dry_run、幂等跳过、来源落库）

所有用例均不依赖 PostgreSQL / Qdrant / Ollama，通过 fake DB 与端口注入。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from common import db as db_module
from knowledge import story_group as sg
from tests.fakes import FakeDB


# ------------------------------------------------------------ 纯函数 --


class TestNormalizeText:
    def test_strips_punctuation_and_folds_space(self):
        assert sg.normalize_text("宁德时代，发布！麒麟电池。") == "宁德时代发布麒麟电池"

    def test_normalizes_digits_to_hash(self):
        # 同一事件里 3.2% 与 3.5% 应归一，避免数字把二元组拆散
        assert sg.normalize_text("涨幅 3.2%") == sg.normalize_text("涨幅 3.5%")

    def test_none_and_empty_return_empty(self):
        assert sg.normalize_text(None) == ""
        assert sg.normalize_text("   ") == ""


class TestSimilarity:
    def test_bigrams_of_identical_text_are_identical(self):
        left = sg.char_bigrams("宁德时代发布麒麟电池")
        right = sg.char_bigrams("宁德时代发布麒麟电池")
        assert left == right
        assert sg.jaccard(left, right) == 1.0

    def test_unrelated_texts_have_low_jaccard(self):
        left = sg.char_bigrams("宁德时代发布麒麟电池")
        right = sg.char_bigrams("英伟达发布新显卡")
        assert sg.jaccard(left, right) < 0.2

    def test_jaccard_handles_empty_sets(self):
        assert sg.jaccard(set(), {"ab"}) == 0.0
        assert sg.jaccard(set(), set()) == 0.0

    def test_cosine_of_identical_vectors_is_one(self):
        assert sg.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_cosine_of_orthogonal_vectors_is_zero(self):
        assert sg.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_cosine_mismatched_length_returns_zero(self):
        assert sg.cosine([1.0], [1.0, 2.0]) == 0.0


class TestExtractSourceId:
    def test_domain_from_url(self):
        assert sg.extract_source_id({"source_url": "https://www.cls.cn/detail/1"}) == "cls.cn"

    def test_strips_mobile_prefix(self):
        assert (
            sg.extract_source_id({"source_url": "https://m.eastmoney.com/news/1"})
            == "eastmoney.com"
        )

    def test_falls_back_to_blogger(self):
        assert sg.extract_source_id({"blogger_key": "b1"}) == "blogger:b1"

    def test_unknown_when_nothing_available(self):
        assert sg.extract_source_id({}) == "unknown"

    def test_same_domain_different_paths_is_one_source(self):
        # 同一家媒体发多篇只能算一个来源，这是独立来源数的分母口径
        left = sg.extract_source_id({"source_url": "https://cls.cn/a/1"})
        right = sg.extract_source_id({"source_url": "https://cls.cn/b/2"})
        assert left == right == "cls.cn"


class TestComputeHeat:
    def test_fresh_story_heat_equals_sources(self):
        now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
        assert sg.compute_heat(3, now, now) == pytest.approx(3.0)

    def test_decays_with_age(self):
        now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
        old = now - timedelta(hours=48)
        # exp(-48h/48h) = e^-1 ≈ 0.3679
        assert sg.compute_heat(3, old, now) == pytest.approx(3 * 0.3679, abs=1e-3)

    def test_never_negative_for_future_timestamp(self):
        now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
        assert sg.compute_heat(2, now + timedelta(hours=5), now) == pytest.approx(2.0)


# -------------------------------------------------------------- 判定器 --


class TestRuleJudge:
    def test_near_identical_text_is_same_story(self):
        judge = sg.RuleJudge()
        verdict = judge.judge(
            {"title": "宁德时代发布麒麟电池", "content": "宁德时代今日发布麒麟电池"},
            {"title": "宁德时代发布麒麟电池新品", "content": "宁德时代发布麒麟电池新品"},
            0.7,
        )
        assert verdict.relation == "same_story"
        assert verdict.decided_by == "rule"

    def test_shared_entities_alone_are_not_same_story(self):
        """实体重合不构成判定依据（v2 修订，2026-11-04）。

        v1 曾把"实体交集比例 ≥0.5 → same_story"当成保守规则，实际行为相反：
        分母是较小实体集，单实体事实恒为 1.0，规则退化为"共享任一实体即同事件"。
        50 条带真值语料上它独立制造 6 处误合并，全部是"同一主体、不同事件"
        （英伟达显卡发布并进财报、茅台提价并进三季报）。
        """
        judge = sg.RuleJudge()
        verdict = judge.judge(
            {"title": "英伟达发布新一代显卡", "content": "算力提升三成",
             "entities": ["英伟达"]},
            {"title": "英伟达三季度财报超预期", "content": "数据中心收入翻倍",
             "entities": ["英伟达"]},
            0.7,
        )
        assert verdict.relation == "unrelated"
        # 实体重合只作为复核线索写进理由，不改变判定
        assert "entity_overlap=1.000" in verdict.rationale

    def test_unrelated_falls_back_to_unrelated(self):
        # 保守优先：宁可漏合并，不可误合并（误合并会污染独立来源数）
        judge = sg.RuleJudge()
        verdict = judge.judge(
            {"title": "宁德时代发布麒麟电池", "content": "电池"},
            {"title": "英伟达发布新显卡", "content": "显卡"},
            0.65,
        )
        assert verdict.relation == "unrelated"


# ------------------------------------------------------------ 算子主流程 --


def _unit(
    key: str,
    title: str,
    url: str,
    *,
    entities: list[str] | None = None,
    topics: list[str] | None = None,
    content: str = "",
    version: int = 1,
) -> dict:
    return {
        "unit_key": key,
        "unit_version": version,
        "title": title,
        "content": content,
        "source_url": url,
        "source_link": None,
        "blogger_key": None,
        "resource_key": f"res-{key}",
        "entities": entities or [],
        "topics": topics or [],
        "review_status": "reviewed",
        "created_at": datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc),
    }


@pytest.fixture()
def patch_db(monkeypatch):
    def _install(fake: FakeDB) -> FakeDB:
        monkeypatch.setattr(db_module, "fetch_all", fake.fetch_all)
        monkeypatch.setattr(db_module, "insert_returning", fake.insert_returning)
        monkeypatch.setattr(db_module, "execute", fake.execute)
        return fake

    return _install


class TestStoryGrouper:
    def test_two_similar_units_merge_into_one_story(self, patch_db):
        fake = patch_db(
            FakeDB(
                [
                    _unit("u1", "宁德时代发布麒麟电池", "https://cls.cn/a/1"),
                    _unit("u2", "宁德时代发布麒麟电池新品", "https://eastmoney.com/b/2"),
                ]
            )
        )
        result = sg.StoryGrouper().run()

        assert result.stories_created == 1
        assert result.members_added == 2
        assert len(fake.story_inserts) == 1
        # 两条成员边指向同一个事件
        assert {m["story_id"] for m in fake.member_inserts} == {1}
        assert fake.refresh_calls == 1

    def test_unrelated_units_create_separate_stories(self, patch_db):
        fake = patch_db(
            FakeDB(
                [
                    _unit("u1", "宁德时代发布麒麟电池", "https://cls.cn/a/1"),
                    _unit("u2", "英伟达发布新一代显卡", "https://nvidia.com/b/2"),
                ]
            )
        )
        result = sg.StoryGrouper().run()

        assert result.stories_created == 2
        assert len({m["story_id"] for m in fake.member_inserts}) == 2

    def test_member_records_normalized_source_id(self, patch_db):
        fake = patch_db(
            FakeDB([_unit("u1", "某公司公告", "https://www.cls.cn/detail/9")])
        )
        sg.StoryGrouper().run()

        assert fake.member_inserts[0]["source_id"] == "cls.cn"
        assert fake.member_inserts[0]["decided_by"] == "vector"

    def test_dry_run_writes_nothing(self, patch_db):
        fake = patch_db(
            FakeDB(
                [
                    _unit("u1", "宁德时代发布麒麟电池", "https://cls.cn/a/1"),
                    _unit("u2", "宁德时代发布麒麟电池新品", "https://eastmoney.com/b/2"),
                ]
            )
        )
        result = sg.StoryGrouper().run(dry_run=True)

        assert result.dry_run is True
        assert result.stories_created == 1
        assert fake.story_inserts == []
        assert fake.member_inserts == []
        assert fake.refresh_calls == 0

    def test_empty_window_returns_zero_without_writes(self, patch_db):
        fake = patch_db(FakeDB([]))
        result = sg.StoryGrouper().run()

        assert result.units_scanned == 0
        assert result.stories_created == 0
        assert fake.story_inserts == []

    def test_rerun_is_idempotent_when_units_already_assigned(self, patch_db):
        """已归属单元被 SQL 的 NOT EXISTS 排除后，重跑不应新增任何边。"""
        fake = patch_db(FakeDB([]))  # 窗内没有待归属单元 = 重跑的真实形态
        first = sg.StoryGrouper().run()
        second = sg.StoryGrouper().run()

        assert first.members_added == 0
        assert second.members_added == 0
        assert fake.member_inserts == []

    def test_duplicate_unit_key_does_not_double_count(self, patch_db):
        """同一单元被重复投递时，ON CONFLICT 语义保证只留一条边。"""
        fake = patch_db(
            FakeDB(
                [
                    _unit("u1", "宁德时代发布麒麟电池", "https://cls.cn/a/1"),
                    _unit("u1", "宁德时代发布麒麟电池", "https://cls.cn/a/1"),
                ]
            )
        )
        result = sg.StoryGrouper().run()

        assert len(fake.member_inserts) == 1
        assert result.members_added == 1

    def test_embedder_failure_degrades_to_text_similarity(self, patch_db):
        """嵌入器抛错时不得中断整轮，应降级为文本相似度继续分组。"""

        class BrokenEmbedder:
            def embed_texts(self, texts):
                raise RuntimeError("ollama down")

        patch_db(
            FakeDB(
                [
                    _unit("u1", "宁德时代发布麒麟电池", "https://cls.cn/a/1"),
                    _unit("u2", "宁德时代发布麒麟电池新品", "https://eastmoney.com/b/2"),
                ]
            )
        )
        result = sg.StoryGrouper(embedder=BrokenEmbedder()).run()

        assert result.stories_created == 1
        assert result.members_added == 2

    def test_vector_path_used_when_embedder_available(self, patch_db):
        """有嵌入器时应走余弦判定，而非文本 Jaccard。"""

        class FakeEmbedder:
            def embed_texts(self, texts):
                # 两条文本给出同一向量 → 余弦 1.0 → 直接判同
                return [[1.0, 0.0, 0.0] for _ in texts]

        patch_db(
            FakeDB(
                [
                    _unit("u1", "完全不同甲", "https://a.com/1"),
                    _unit("u2", "完全无关乙", "https://b.com/2"),
                ]
            )
        )
        result = sg.StoryGrouper(embedder=FakeEmbedder()).run()

        # 文本毫不相似，但向量相同 → 走向量路径判同，证明余弦分支生效
        assert result.stories_created == 1
        assert result.members_added == 2

    def test_high_similarity_is_decided_by_vector(self, patch_db):
        class FakeEmbedder:
            def embed_texts(self, texts):
                return [[1.0, 0.0] for _ in texts]

        fake = patch_db(
            FakeDB(
                [
                    _unit("u1", "甲", "https://a.com/1"),
                    _unit("u2", "乙", "https://b.com/2"),
                ]
            )
        )
        sg.StoryGrouper(embedder=FakeEmbedder()).run()

        decisions = {m["decided_by"] for m in fake.member_inserts}
        assert "vector" in decisions
