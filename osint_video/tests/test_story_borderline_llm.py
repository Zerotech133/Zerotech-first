"""临界带 LLM 复核（``decided_by='llm_recheck'``）的测试。

## 这份测试守的是什么

临界带 ``[门槛-0.05, 门槛)`` 是**故意开的一个口子**：让判定器越过余弦门槛，
把"差一点没合上"的配对强行判为同一事件。它能救回真同事件（真机
``s11-01``/``s11-02``，cos=0.7133），也能造出误合并——而误合并会污染
``independent_sources``（热度分母）且事后极难发现。所以这条链路上每个
"不做"的边界都比"做"的边界重要：

1. **只对临界带生效**——``sim >= 门槛`` 不碰（已由余弦直判），
   ``sim < 门槛-0.05`` 不碰（证据太弱）。放开到全域就等于取消门槛。
2. **人工台账压过 LLM**——人拍过板的配对，机器不许再覆盖。
3. **失败不伪装成无关**——复核器挂了必须回落规则 + 计 degraded，
   否则外部表现是"事件突然变多/变少"，与嵌入降级同形，运维分不清。
4. **同一对只复核一次**——双向评估会让调用量翻倍、计数虚高。

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


class SpyJudge:
    """记录每次调用、可指定结论或故障的判定器替身。

    ``calls`` 必须是**属性且返回 int**：``StoryGrouper.run`` 末尾会做
    ``int(getattr(judge, "calls", 0))``，把它写成 list 会直接 TypeError。
    """

    name = "spy"

    def __init__(self, relation: str = "same_story", *, failed: bool = False) -> None:
        self.relation = relation
        self.failed = failed
        self.seen: list[tuple[str, str, float]] = []

    @property
    def calls(self) -> int:
        return len(self.seen)

    def judge(self, unit, target, similarity):
        self.seen.append(
            (str(unit.get("unit_key")), str(target.get("unit_key")), similarity)
        )
        if self.failed:
            return sg.Verdict(
                "unrelated", "llm", "llm_failed: backend down", failed=True
            )
        return sg.Verdict(self.relation, "llm", "spy-relation=%s" % self.relation)


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


def _run(
    patch_db,
    similarity: float,
    *,
    borderline_judge=None,
    overrides=None,
    identical_text: bool = False,
):
    """两条共用实体的事实、余弦恰为 ``similarity``，返回 dry-run 的 GroupResult。

    共用实体是必需的：候选池要求 topic 或实体有交集才准入，实体不同的话这对
    压根进不了判定环节，测出来的"未合并"是召回失败，不是临界判定。

    ``identical_text=True`` 让两条标题完全相同（文本 Jaccard=1.0），用于把
    "回落规则判定"与"直接当无关"区分开——前者会合并，后者不会。这两者在
    默认语料上结论相同，测不出差别，必须专门构造。
    """
    right_title = "组件价格跌破成本线" if identical_text else "协会召集头部企业座谈"
    fake = FakeDB(
        [
            _unit("a", "组件价格跌破成本线", entities=["光伏行业协会"]),
            _unit("b", right_title, entities=["光伏行业协会"]),
        ]
    )
    patch_db(fake)
    grouper = sg.StoryGrouper(
        embedder=FakeEmbedder([[1.0, 0.0], _vec(similarity)]),
        borderline_judge=borderline_judge,
        borderline_overrides=overrides,
    )
    return grouper.run(window_days=14, only_reviewed=True, dry_run=True)


# ------------------------------------------------------- 作用域边界 --


class TestScope:
    def test_rechecks_inside_borderline(self, patch_db):
        """cos=0.72 落在 [0.70, 0.75) → 复核器被调用且结论生效。"""
        spy = SpyJudge("same_story")
        result = _run(patch_db, 0.72, borderline_judge=spy)

        assert result.borderline_rechecked == 1
        assert result.borderline_promoted == 1
        assert result.stories_created == 1  # 两条合成一个事件
        assert len(spy.seen) == 1

    def test_skips_pairs_below_borderline(self, patch_db):
        """cos=0.65 低于门槛-0.05 → 不碰复核器（证据太弱，碰了就是取消门槛）。"""
        spy = SpyJudge("same_story")
        result = _run(patch_db, 0.65, borderline_judge=spy)

        assert spy.seen == []
        assert result.borderline_rechecked == 0
        assert result.stories_created == 2

    def test_skips_pairs_above_threshold(self, patch_db):
        """cos=0.80 已达直判门槛 → 走余弦直判，不花复核预算。"""
        spy = SpyJudge("unrelated")
        result = _run(patch_db, 0.80, borderline_judge=spy)

        assert spy.seen == []
        assert result.stories_created == 1
        assert result.borderline_rechecked == 0

    def test_skips_pairs_below_recall_gate(self, patch_db):
        """cos=0.58 低于召回门 0.60 → 根本不进判定环节。"""
        spy = SpyJudge("same_story")
        result = _run(patch_db, 0.58, borderline_judge=spy)

        assert spy.seen == []
        assert result.borderline_rechecked == 0

    def test_default_is_byte_identical_to_before(self, patch_db):
        """不配复核器时，critical 字段全 0 —— 默认行为与 P1 之前一致。"""
        result = _run(patch_db, 0.72, borderline_judge=None)

        assert result.borderline_rechecked == 0
        assert result.borderline_promoted == 0
        assert result.borderline_promoted_samples == []
        assert result.borderline == 1  # 仍然记"差一点没合"
        assert result.stories_created == 2


# ----------------------------------------------------------- 结论 --


class TestVerdict:
    def test_promoted_pair_is_recorded_for_review(self, patch_db):
        """判同并合并后，必须留下可供人复核的明细——否则误合并无从发现。"""
        spy = SpyJudge("same_story")
        result = _run(patch_db, 0.7133, borderline_judge=spy)

        assert len(result.borderline_promoted_samples) == 1
        sample = result.borderline_promoted_samples[0]
        assert sample["decided_by"] == sg.BORDERLINE_RECHECK_DECIDED_BY
        assert sample["similarity"] == pytest.approx(0.7133, abs=1e-3)
        assert sample["threshold"] == pytest.approx(0.75, abs=1e-3)
        assert sample["gap"] == pytest.approx(0.0367, abs=1e-3)
        assert "borderline_llm" in sample["rationale"]
        assert sample["left_title"] and sample["right_title"]

    def test_unrelated_verdict_still_counts_as_rechecked(self, patch_db):
        """判异时不合并，但"复核过"要计数——否则看不出临界带真实规模。"""
        spy = SpyJudge("unrelated")
        result = _run(patch_db, 0.72, borderline_judge=spy)

        assert result.borderline_rechecked == 1
        assert result.borderline_promoted == 0
        assert result.stories_created == 2
        assert result.borderline == 1

    def test_recheck_happens_once_per_pair(self, patch_db):
        """同一对在双向各被评估一次，但只该调复核器一次。

        不去重的话：调用量翻倍（生产机每对多付 20-30 秒 CPU），
        ``borderline_rechecked`` 也会记成 2，让人误判临界带规模。
        """
        spy = SpyJudge("unrelated")
        result = _run(patch_db, 0.72, borderline_judge=spy)

        assert len(spy.seen) == 1
        assert result.borderline_rechecked == 1
        assert result.judge_calls == 1


# ----------------------------------------------------------- 故障 --


class TestFailure:
    def test_failure_falls_back_and_marks_degraded(self, patch_db):
        """复核器故障 → 回落规则判定 + degraded，**绝不**当成"判为无关"。"""
        spy = SpyJudge(failed=True)
        result = _run(patch_db, 0.72, borderline_judge=spy)

        assert result.degraded is True
        assert "judge_failed" in (result.degrade_reason or "")
        assert result.judge_failures == 1
        assert result.borderline_promoted == 0
        # 回落规则后仍判不同（两条文本无重叠），故各自成事件——这是规则判定
        # 的结论，不是"故障当无关"。
        assert result.stories_created == 2

    def test_failure_is_not_retried_for_same_pair(self, patch_db):
        """故障对不再重试：内部熔断已让重试变成毫秒级空转，只会刷高计数。"""
        spy = SpyJudge(failed=True)
        result = _run(patch_db, 0.72, borderline_judge=spy)

        assert len(spy.seen) == 1
        assert result.borderline_rechecked == 1

    def test_failure_falls_back_to_rule_not_unrelated(self, patch_db):
        """故障必须**回落规则判定**，而不是直接当"判为无关"。

        这是本模块最核心的一条契约：``Verdict.failed`` 是故障信号，不是结论。
        若把故障当 unrelated，外部表现就是"事件突然变少"，与嵌入降级完全同形，
        运维分不清"今天没新闻"和"判定器挂了"。

        构造方式：两条标题**完全相同**（文本 Jaccard=1.0 ≥ RULE_JACCARD），
        但余弦只有 0.72 落在临界带。此时：

        - 正确行为（回落 RuleJudge）→ 文本证据够硬 → 判同 → 合并；
        - 错误行为（当 unrelated）→ 不合并（第一路分开，第二路也分开）。

        注意本用例**不足以单独证明契约**：第二次评估会走 ``_recheck_failed``
        短路分支，那里也回落规则，会把首次的错误掩盖掉。真正锁死契约的是
        :meth:`test_failure_returns_rule_verdict_on_first_call`。
        """
        spy = SpyJudge(failed=True)
        result = _run(
            patch_db, 0.72, borderline_judge=spy, identical_text=True
        )

        assert result.stories_created == 1  # 规则判定救回了这次合并
        assert result.borderline_promoted == 0  # 但不是 LLM 提升的
        assert result.degraded is True  # 故障依然显式可见

    def test_failure_returns_rule_verdict_on_first_call(self, patch_db):
        """**首次**失败就必须返回规则判定，decided_by 是 ``rule`` 不是 ``llm``。

        这是 :meth:`test_failure_falls_back_to_rule_not_unrelated` 守不住的那
        半条：集成路径上第二次评估会走 ``_recheck_failed`` 短路分支并回落规则，
        于是"首次把故障当无关"这个错误会被后续兜底掩盖，结果看起来仍然正确。

        直接调 ``_decide_borderline`` 单次判定的返回，就绕开了这层掩盖——
        变异测试实测：把回落换成 ``Verdict("unrelated", "llm", ...)`` 后本用例
        立刻失败，而集成用例仍然通过。
        """
        judge = SpyJudge(failed=True)
        grouper = sg.StoryGrouper(borderline_judge=judge)

        class _Ref:
            """只提供 ``key`` / ``row`` 的最小替身（_decide_borderline 只用这两个）。"""

            def __init__(self, key: str, row: dict) -> None:
                self.key = key
                self.row = row

        left = _Ref("a", _unit("a", "组件价格跌破成本线"))
        right = _Ref("b", _unit("b", "组件价格跌破成本线"))

        verdict = grouper._decide_borderline(left, right, 0.72, 0.75, "vector")

        assert verdict is not None
        assert verdict.decided_by == "rule"  # 回落规则判定，不是伪装成 llm
        assert verdict.relation == "same_story"  # 文本 Jaccard=1.0，规则判同
        assert verdict.failed is False
        assert judge.calls == 1


# ----------------------------------------------------------- 台账 --


class TestManualOverrides:
    def test_approve_merges_with_manual_tag(self, patch_db):
        """台账裁 merge → 合并，且 decided_by='manual'，不再问 LLM。"""
        spy = SpyJudge("unrelated")
        result = _run(
            patch_db, 0.72, borderline_judge=spy, overrides={("a", "b"): True}
        )

        assert result.stories_created == 1
        assert result.manual_overrides == 1
        assert spy.seen == []  # 人的裁决在 LLM 之前
        assert result.borderline_promoted == 0  # 不是 LLM 提升的

    def test_reject_keeps_separate_with_manual_tag(self, patch_db):
        """台账裁 keep_separate → 不合并，并且不再问 LLM。"""
        spy = SpyJudge("same_story")
        result = _run(
            patch_db, 0.72, borderline_judge=spy, overrides={("a", "b"): False}
        )

        assert result.stories_created == 2
        assert result.manual_overrides == 1
        assert spy.seen == []
        assert result.borderline_promoted == 0

    def test_override_key_is_order_insensitive(self, patch_db):
        """台账按无序对匹配：``("b","a")`` 必须命中 ``("a","b")``。

        同一对在两个方向的 ``unit_key`` 顺序取决于 pivot 选择，用有向对做键
        会让台账只在一半情况下命中——表现为"人工改了但时灵时不灵"。
        """
        spy = SpyJudge("same_story")
        result = _run(
            patch_db, 0.72, borderline_judge=spy, overrides={("b", "a"): False}
        )

        assert result.stories_created == 2
        assert result.manual_overrides == 1

    def test_override_does_not_apply_above_threshold(self, patch_db):
        """台账**只对临界带生效**：cos=0.80 的台账 reject 必须被忽略。

        放开到全域意味着人一个笔误就能把明显无关的两条事实并起来（或反过来
        拆散一个明显同一事件），而这类错误事后极难发现。
        """
        result = _run(
            patch_db, 0.80, borderline_judge=None, overrides={("a", "b"): False}
        )

        assert result.stories_created == 1  # 余弦直判同，台账没影响
        assert result.manual_overrides == 0

    def test_override_does_not_apply_far_below(self, patch_db):
        """台账对 ``sim < 门槛-0.05`` 同样不生效（证据太弱，不是人该管的地方）。"""
        result = _run(
            patch_db, 0.65, borderline_judge=None, overrides={("a", "b"): True}
        )

        assert result.stories_created == 2
        assert result.manual_overrides == 0


# ------------------------------------------------------- 纯函数 --


class TestUnorderedPair:
    def test_is_order_insensitive_and_stable(self):
        assert sg.unordered_pair("b", "a") == sg.unordered_pair("a", "b") == ("a", "b")

    def test_handles_none_and_empty(self):
        assert sg.unordered_pair(None, "a") == ("", "a")
        assert sg.unordered_pair("", "") == ("", "")

    def test_note_promoted_counts_beyond_sample_limit(self):
        """计数全量、样本受限：只看样本条数会系统性低估临界规模。"""
        result = sg.GroupResult()
        total = sg.BORDERLINE_SAMPLE_LIMIT + 5
        for i in range(total):
            result.note_promoted(
                unit={"unit_key": f"u{i}", "title": f"左{i}"},
                target={"unit_key": f"t{i}", "title": f"右{i}"},
                similarity=0.73,
                threshold=0.75,
                mode="vector",
                rationale="borderline_llm:x",
            )

        assert result.borderline_promoted == total
        assert len(result.borderline_promoted_samples) == sg.BORDERLINE_SAMPLE_LIMIT

    def test_note_promoted_dedupes_unordered_pair(self):
        """同一对重复记录只算一次（双向评估会来两次）。"""
        result = sg.GroupResult()
        for left, right in (("a", "b"), ("b", "a")):
            result.note_promoted(
                unit={"unit_key": left},
                target={"unit_key": right},
                similarity=0.73,
                threshold=0.75,
                mode="vector",
                rationale="borderline_llm:x",
            )

        assert result.borderline_promoted == 1
        assert len(result.borderline_promoted_samples) == 1

    def test_as_dict_exposes_new_fields(self):
        result = sg.GroupResult(borderline_rechecked=2, borderline_promoted=1)
        payload = result.as_dict()
        assert payload["borderline_rechecked"] == 2
        assert payload["borderline_promoted"] == 1
        assert payload["borderline_promoted_samples"] == []
        assert payload["manual_overrides"] == 0
