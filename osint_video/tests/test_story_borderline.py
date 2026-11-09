"""临界未合并（borderline）观测的测试。

## 这份测试守的是什么

``borderline`` 是调阈值**唯一**靠得住的依据：它记录"差一点就合上、但最终没合"
的配对。它必须满足三个性质，缺一个这个数就会误导人：

1. **只记未合并的**——已合的不叫临界，记进去会让临界带看起来比实际危险；
2. **按口径取门槛**——向量门槛 0.75、文本门槛 0.85 不可比，用同一个数会把
   文本口径的带宽算错；
3. **计数与样本分开**——样本有上限，只看样本条数会系统性低估临界规模。

真机实测（2026-11-04，清空成员边重跑）：``borderline=2``，恰好是
``s11-01``/``s11-02``（cos=0.7133，真同事件、被拆散）与 ``s04-01``/``solo-05``
（cos=0.7203，不同事件、正确未合）。两对同带且只差 0.007——这是"单一阈值
分不开同话题延续与同事件"的直接证据，也是本观测存在的理由。

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


def _vec(cos_theta: float) -> list[float]:
    """构造与 ``[1, 0]`` 余弦恰为 ``cos_theta`` 的单位向量。"""
    return [cos_theta, math.sqrt(max(0.0, 1.0 - cos_theta**2))]


class FakeEmbedder:
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


def _unit(key: str, title: str, entities: list[str] | None = None) -> dict:
    return {
        "unit_key": key,
        "unit_version": 1,
        "title": title,
        "content": "",
        "source_url": "https://example.com/a",
        "source_link": None,
        "blogger_key": None,
        "resource_key": f"res-{key}",
        "entities": entities or [],
        "topics": [],
        "review_status": "reviewed",
        "created_at": _NOW,
    }


def _run_with_similarity(patch_db, similarity: float) -> object:
    """两条事实、余弦恰为 ``similarity``，返回 run 的 GroupResult。

    两条**共用同一实体**：候选池要求 topic 或实体有交集才准入，实体不同的话
    这对压根进不了判定环节，测出来的"未合并"是召回失败而非临界判定。真机里
    ``s11-01``/``s11-02`` 也同属光伏行业协会，此处口径一致。
    """
    fake = FakeDB(
        [
            _unit("a", "组件价格跌破成本线", entities=["光伏行业协会"]),
            _unit("b", "协会召集头部企业座谈", entities=["光伏行业协会"]),
        ]
    )
    patch_db(fake)
    grouper = sg.StoryGrouper(embedder=FakeEmbedder([[1.0, 0.0], _vec(similarity)]))
    return grouper.run(window_days=14, only_reviewed=True, dry_run=True)


# ------------------------------------------------------------ 计数 --


class TestBorderlineCounting:
    def test_records_pair_just_below_threshold(self, patch_db):
        """cos=0.72 落在 [0.70, 0.75) → 记 1 条临界，且两条事实仍各自成事件。

        关键在"1 条"：同一对会在双向各被评估一次（a 作 pivot、b 作 pivot），
        不去重会得到 2。真机当年靠候选池较大才没暴露，属侥幸。
        """
        result = _run_with_similarity(patch_db, 0.72)

        assert result.borderline == 1
        assert len(result.borderline_samples) == 1
        assert result.stories_created == 2  # 未合并：各自独立
        assert result.borderline_samples[0]["similarity"] == pytest.approx(0.72, abs=1e-3)

    def test_ignores_pairs_far_below_threshold(self, patch_db):
        """cos=0.65 低于 0.75-0.05 → 不算临界（差得太远，看也没用）。"""
        result = _run_with_similarity(patch_db, 0.65)
        assert result.borderline == 0

    def test_ignores_pairs_below_recall_gate(self, patch_db):
        """cos=0.58 低于召回门 0.60 → 根本不进判定，自然不记临界。"""
        result = _run_with_similarity(patch_db, 0.58)
        assert result.borderline == 0

    def test_ignores_merged_pairs(self, patch_db):
        """cos=0.80 直判同 → 已合并不叫临界。"""
        result = _run_with_similarity(patch_db, 0.80)
        assert result.borderline == 0


# ------------------------------------------------------- 门槛按口径 --


class TestThresholdByMode:
    def test_vector_threshold_is_configured_recheck(self):
        """向量口径用 ``RECHECK_SIMILARITY`` 的解析值（当前 0.75）。"""
        grouper = sg.StoryGrouper()
        assert grouper._direct_threshold("vector") == pytest.approx(
            sg.resolve_recheck_similarity()
        )

    def test_text_threshold_is_rule_direct(self):
        """文本口径用 ``RULE_DIRECT_SIMILARITY``（0.85），与向量口径不可混用。

        这条是 2026-11-04 下调阈值时被现有用例逼出来的真缺陷的回归测试：
        统一用 0.75 会让一对 Jaccard=0.786 的文本被直接判同、跳过判定器，
        ``judge_failures`` 从 1 变 0，降级原因随之丢失。
        """
        grouper = sg.StoryGrouper()
        assert grouper._direct_threshold("text") == pytest.approx(
            sg.RULE_DIRECT_SIMILARITY
        )


# ------------------------------------------------------------ 样本 --


class TestSampleLimit:
    def test_counts_all_but_caps_samples(self):
        """计数不设上限，样本受 ``BORDERLINE_SAMPLE_LIMIT`` 约束。

        两者必须分开：只看样本条数会系统性低估临界规模，而全量保留又会让
        十万级语料的 dry-run 把内存撑爆。
        """
        result = sg.GroupResult()
        total = sg.BORDERLINE_SAMPLE_LIMIT + 5
        for i in range(total):
            result.note_borderline(
                unit={"unit_key": f"u{i}", "title": f"左{i}"},
                target={"unit_key": f"t{i}", "title": f"右{i}"},
                similarity=0.73,
                threshold=0.75,
                mode="vector",
                decided_by="rule",
                rationale="test",
            )

        assert result.borderline == total
        assert len(result.borderline_samples) == sg.BORDERLINE_SAMPLE_LIMIT

    def test_sample_carries_both_titles_and_reason(self):
        """样本必须带两侧标题与判定来源——看清单的人要判断"该不该合"。"""
        result = sg.GroupResult()
        result.note_borderline(
            unit={"unit_key": "u1", "title": "组件价格跌破成本线"},
            target={"unit_key": "t1", "title": "协会召集企业座谈"},
            similarity=0.7133,
            threshold=0.75,
            mode="vector",
            decided_by="rule",
            rationale="jaccard=0.12<0.35",
        )

        sample = result.borderline_samples[0]
        assert sample["left_title"] == "组件价格跌破成本线"
        assert sample["right_title"] == "协会召集企业座谈"
        assert sample["decided_by"] == "rule"
        assert sample["gap"] == pytest.approx(0.0367, abs=1e-3)
        assert sample["rationale"] == "jaccard=0.12<0.35"

    def test_as_dict_exposes_borderline(self):
        """``as_dict`` 必须带出临界字段，否则 CLI 的 dry-run 报不出来。"""
        result = sg.GroupResult()
        result.note_borderline(
            unit={"unit_key": "u"},
            target={"unit_key": "t"},
            similarity=0.73,
            threshold=0.75,
            mode="vector",
            decided_by="rule",
            rationale="test",
        )
        payload = result.as_dict()
        assert payload["borderline"] == 1
        assert len(payload["borderline_samples"]) == 1
