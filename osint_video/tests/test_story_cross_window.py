"""跨窗口召回（P1-C）的护栏、接线与阈值口径测试。

本文件钉三件事：

1. **护栏真的挡得住**：余弦 0.70 以下不跨窗、无共同主体不跨窗、文本口径不跨窗；
2. **接线真的通**：开启后窗内事实能挂到窗口外已建事件的 ``story_id`` 上，
   并把 ``cross_window_merged`` 回报出来（否则"开了没效果"无从区分）；
3. **两个相似度口径的阈值没有混用**：向量余弦用 0.75，字符 Jaccard 用 0.85。
   这条是 2026-11-04 下调阈值时被现有用例逼出来的真缺陷——统一降到 0.75
   后，一对 Jaccard=0.786 的文本被直接判同、跳过判定器，``judge_failures``
   从 1 变 0，降级原因随之丢失。

全部用例不依赖 PostgreSQL / Ollama。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from common import db as db_module
from knowledge import story_group as sg
from tests.fakes import FakeDB

_NOW = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------ 桩件 --


def _vec(cos_theta: float) -> list[float]:
    """构造与 ``[1, 0]`` 余弦恰为 ``cos_theta`` 的单位向量。"""
    return [cos_theta, math.sqrt(max(0.0, 1.0 - cos_theta**2))]


class FakeEmbedder:
    """按调用顺序返回预设向量（``refs + anchors`` 的拼接顺序）。"""

    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = list(vectors)
        self.calls = 0

    def embed_texts(self, texts):
        self.calls += 1
        return self.vectors[: len(texts)]


class _SpyJudge:
    """记录调用次数的判定器；恒判同，便于隔离"是否进入判定环节"。"""

    name = "spy"

    def __init__(self) -> None:
        self.calls = 0

    def judge(self, unit_row, target_row, similarity):
        self.calls += 1
        return sg.Verdict("same_story", "llm", "spy")


@pytest.fixture
def patch_db(monkeypatch):
    def _install(fake: FakeDB) -> FakeDB:
        monkeypatch.setattr(db_module, "fetch_all", fake.fetch_all)
        monkeypatch.setattr(db_module, "insert_returning", fake.insert_returning)
        monkeypatch.setattr(db_module, "execute", fake.execute)
        return fake

    return _install


def _unit(
    key: str,
    title: str,
    *,
    entities: list[str] | None = None,
    url: str = "https://example.com/a",
) -> dict:
    return {
        "unit_key": key,
        "unit_version": 1,
        "title": title,
        "content": "",
        "source_url": url,
        "source_link": None,
        "blogger_key": None,
        "resource_key": f"res-{key}",
        "entities": entities or [],
        "topics": [],
        "review_status": "reviewed",
        "created_at": _NOW,
    }


def _anchor(
    key: str, title: str, *, story_id: int, entities: list[str] | None = None
) -> dict:
    row = _unit(key, title, entities=entities, url="https://example.com/old")
    row["story_id"] = story_id
    return row


# ------------------------------------------------------ 护栏与接线 --


class TestCrossWindowGuard:
    """三道护栏（实体交集 / 余弦 0.70 / 仅向量口径）逐条验证。"""

    def test_merges_into_existing_story_when_guards_pass(self, patch_db):
        """共同主体 + 余弦 0.95 → 必须挂到窗外既有事件，而不是新建事件。"""
        patch_db(
            FakeDB(
                [_unit("new", "中芯国际产能利用率回升至九成", entities=["中芯国际"])],
                anchors=[
                    _anchor(
                        "old",
                        "中芯国际产能利用率超90%，成熟制程订单回暖",
                        story_id=77,
                        entities=["中芯国际"],
                    )
                ],
            )
        )
        result = sg.StoryGrouper(
            embedder=FakeEmbedder([[1.0, 0.0], _vec(0.95)]), cross_window=True
        ).run()

        assert result.anchors_loaded == 1
        assert result.cross_window_merged == 1
        assert result.stories_created == 0
        # 回报字段必须进 as_dict，否则运维在日志里看不到"跨窗合并了几条"
        assert result.as_dict()["cross_window_merged"] == 1
        assert result.as_dict()["anchors_loaded"] == 1

    def test_member_points_to_anchor_story(self, patch_db):
        """落库的成员边必须指向锚点所属的 story_id，且留痕可追。"""
        fake = patch_db(
            FakeDB(
                [_unit("new", "中芯国际产能利用率回升至九成", entities=["中芯国际"])],
                anchors=[
                    _anchor("old", "中芯国际产能利用率超90%", story_id=77,
                            entities=["中芯国际"])
                ],
            )
        )
        sg.StoryGrouper(
            embedder=FakeEmbedder([[1.0, 0.0], _vec(0.95)]), cross_window=True
        ).run()

        assert len(fake.member_inserts) == 1
        member = fake.member_inserts[0]
        assert member["story_id"] == 77
        assert "cross_window" in member["rationale"]
        assert "中芯国际" in member["rationale"]

    def test_skips_when_no_shared_entity(self, patch_db):
        """无共同主体 → 不跨窗，退回独立事件（余弦 0.95 也不合）。

        2026-11-04 曾为这条加过"余弦 >= 0.90 免实体"的通道，理由是误读库里
        的原始 entities；真机日志证明别名归一已把两侧对齐，放宽前提不成立，
        通道已删。这条用例就是防它被重新加回来。
        """
        patch_db(
            FakeDB(
                [_unit("new", "中芯国际产能利用率超90%", entities=["SMIC"])],
                anchors=[_anchor("old", "台积电产能利用率回升至九成", story_id=77,
                                 entities=["中芯国际"])],
            )
        )
        result = sg.StoryGrouper(
            embedder=FakeEmbedder([[1.0, 0.0], _vec(0.95)]), cross_window=True
        ).run()

        assert result.anchors_loaded == 1
        assert result.cross_window_merged == 0
        assert result.stories_created == 1

    def test_skips_when_below_guard(self, patch_db):
        """余弦 0.65 < 0.70 → 不跨窗。0.642 是本语料实测的那对假阳。"""
        patch_db(
            FakeDB(
                [_unit("new", "隆基绿能公告董事长增持公司股份", entities=["隆基绿能"])],
                anchors=[_anchor("old", "隆基绿能上调硅片报价", story_id=77,
                                 entities=["隆基绿能"])],
            )
        )
        result = sg.StoryGrouper(
            embedder=FakeEmbedder([[1.0, 0.0], _vec(0.65)]), cross_window=True
        ).run()

        assert result.cross_window_merged == 0
        assert result.stories_created == 1

    def test_text_mode_never_crosses(self, patch_db):
        """嵌入不可用（文本口径）时不跨窗：0.70 是为余弦标定的，Jaccard 不可比。"""
        class _BrokenEmbedder:
            def embed_texts(self, texts):
                raise RuntimeError("ollama down")

        patch_db(
            FakeDB(
                [_unit("new", "中芯国际产能利用率回升至九成", entities=["中芯国际"])],
                anchors=[_anchor("old", "中芯国际产能利用率回升至九成", story_id=77,
                                 entities=["中芯国际"])],
            )
        )
        result = sg.StoryGrouper(embedder=_BrokenEmbedder(), cross_window=True).run()

        assert result.degraded is True
        assert result.cross_window_merged == 0

    def test_disabled_by_default(self, patch_db):
        """默认关闭：不查锚点、不设 anchors_loaded，行为与 P0 完全一致。"""
        fake = patch_db(
            FakeDB(
                [_unit("new", "中芯国际产能利用率回升至九成", entities=["中芯国际"])],
                anchors=[_anchor("old", "中芯国际产能利用率超90%", story_id=77,
                                 entities=["中芯国际"])],
            )
        )
        result = sg.StoryGrouper(embedder=FakeEmbedder([[1.0, 0.0]])).run()

        assert result.anchors_loaded == 0
        assert result.cross_window_merged == 0
        assert result.stories_created == 1
        # 开关关着就不该发起锚点查询（省掉十万级表的一次索引扫描）
        assert fake.anchors, "锚点确实备着"
        assert fake.anchor_queries == 0, "开关关闭时不应查询锚点"


# ------------------------------------------------------ 准入判定纯函数 --


class TestCrossWindowAdmit:
    """两条通道与文本口径拒绝，逐条钉死（门槛数值即本语料的实测结论）。"""

    def test_entity_channel(self):
        ok, channel = sg.cross_window_admit({"a"}, 0.71, "vector")
        assert ok is True
        assert channel == "entity+similarity"

    def test_entity_below_minimum(self):
        ok, channel = sg.cross_window_admit({"a"}, 0.69, "vector")
        assert ok is False
        assert channel == "below_min_similarity"

    def test_no_shared_entity_rejected_even_at_high_similarity(self):
        """无共同主体时余弦 0.99 也不准入（曾经误放宽，已撤回）。"""
        ok, channel = sg.cross_window_admit(set(), 0.99, "vector")
        assert ok is False
        assert channel == "no_shared_entity"

    def test_known_false_positive_rejected(self):
        """隆基绿能"增持"vs"涨价"：有实体、余弦 0.642，必须被挡。"""
        ok, _ = sg.cross_window_admit({"隆基绿能"}, 0.642, "vector")
        assert ok is False

    def test_text_mode_never_admitted(self):
        """文本口径即使余弦等价位置也不会出现，Jaccard 0.99 也不准入。"""
        ok, channel = sg.cross_window_admit({"a"}, 0.99, "text")
        assert ok is False
        assert channel == "text_mode_not_allowed"


# ------------------------------------------------------ 阈值口径分离 --


class TestThresholdSeparation:
    """向量余弦与字符 Jaccard 的阈值必须分开，且各自可被配置覆盖。"""

    def test_vector_and_text_thresholds_differ(self):
        assert sg.RECHECK_SIMILARITY == 0.75
        assert sg.RULE_DIRECT_SIMILARITY == 0.85
        assert sg.RULE_DIRECT_SIMILARITY > sg.RECHECK_SIMILARITY

    def test_recall_gates_differ(self):
        """文本召回门必须低于向量召回门，否则嵌入降级那轮合并率断崖下跌。"""
        assert sg.gate_for("vector") == sg.RECALL_SIMILARITY
        assert sg.gate_for("text") == sg.RULE_JACCARD
        assert sg.gate_for("text") < sg.gate_for("vector")

    def test_text_similarity_0786_still_reaches_judge(self):
        """Jaccard=0.786 不得被直判同短路——这正是 0.75 统一阈值时踩的坑。"""
        spy = _SpyJudge()
        grouper = sg.StoryGrouper(embedder=FakeEmbedder([]), judge=spy)
        unit = sg._UnitRef(key="u1", version=1, row={}, source_id="a")
        target = sg._UnitRef(key="u2", version=1, row={}, source_id="b")

        grouper._decide(unit, target, 0.786, "text")

        assert spy.calls == 1

    def test_vector_similarity_0786_short_circuits(self):
        """同一条 0.786 走向量口径时应当直判同（0.75 已达标）。"""
        spy = _SpyJudge()
        grouper = sg.StoryGrouper(embedder=FakeEmbedder([]), judge=spy)
        unit = sg._UnitRef(key="u1", version=1, row={}, source_id="a")
        target = sg._UnitRef(key="u2", version=1, row={}, source_id="b")

        verdict = grouper._decide(unit, target, 0.786, "vector")

        assert spy.calls == 0
        assert verdict.decided_by == "vector"
        assert verdict.relation == "same_story"

    def test_config_override_reaches_grouper(self, monkeypatch):
        """STORY_RECHECK_SIMILARITY 必须真的改变判定行为（可灰度回退到 0.85）。"""
        from config.settings import get_settings

        monkeypatch.setenv("STORY_RECHECK_SIMILARITY", "0.85")
        get_settings.cache_clear()
        try:
            assert sg.resolve_recheck_similarity() == 0.85
            spy = _SpyJudge()
            grouper = sg.StoryGrouper(embedder=FakeEmbedder([]), judge=spy)
            unit = sg._UnitRef(key="u1", version=1, row={}, source_id="a")
            target = sg._UnitRef(key="u2", version=1, row={}, source_id="b")

            grouper._decide(unit, target, 0.786, "vector")

            assert spy.calls == 1  # 0.786 < 0.85，回退后重新进入判定环节
        finally:
            monkeypatch.delenv("STORY_RECHECK_SIMILARITY", raising=False)
            get_settings.cache_clear()
