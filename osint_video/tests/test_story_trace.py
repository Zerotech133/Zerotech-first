"""配对级观测（``trace_pairs``）与临界带宽配置化的测试。

## 这两件事为什么放在一起测

它们服务于同一个目的：**让"带宽该定多少"成为一个可测量的经验问题，而不是
拍脑袋的常量**。观测提供数据，配置提供落点。任何一边坏掉，标定结论就不成立：

- 观测坏了（漏记配对、双向重复计数、跨轮累加）→ 标定脚本会算出"某带宽下
  真该合 0 对"，据此把带宽收窄，真事件从此消失；
- 配置坏了（只对一处链路生效、越界不回落）→ 配了 0.10 实际还是 0.05，
  外部表现是"改了配置没反应"，且没有任何报错。

默认行为必须逐字节不变：``trace_pairs`` 关闭时 ``pairs_observed`` 恒空，
带宽不显式传值时走常量 0.05。这两条都有专门用例钉住。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from common import db as db_module
from knowledge import story_group as sg
from tests.fakes import FakeDB

_NOW = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
_ENTITY = "光伏行业协会"


def _vec(cos_theta: float) -> list[float]:
    return [cos_theta, math.sqrt(max(0.0, 1.0 - cos_theta**2))]


class FakeEmbedder:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = list(vectors)

    def embed_texts(self, texts):
        return self.vectors[: len(texts)]


class SpyJudge:
    name = "spy"

    def __init__(self, relation: str = "same_story") -> None:
        self.relation = relation
        self.seen: list[tuple[str, str, float]] = []

    @property
    def calls(self) -> int:
        return len(self.seen)

    def judge(self, unit, target, similarity):
        self.seen.append(
            (str(unit.get("unit_key")), str(target.get("unit_key")), similarity)
        )
        return sg.Verdict(self.relation, "llm", "spy")


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
        "content": title,
        "source_url": f"https://example.com/{key}",
        "source_link": None,
        "blogger_key": None,
        "resource_key": f"res-{key}",
        "entities": entities if entities is not None else [_ENTITY],
        "topics": [],
        "review_status": "reviewed",
        "created_at": _NOW,
    }


def _run(patch_db, similarity: float, **kwargs):
    """两条共用实体的事实、余弦恰为 ``similarity``，返回 dry-run 结果。"""
    fake = FakeDB(
        [
            _unit("a", "组件价格跌破成本线"),
            _unit("b", "协会召集头部企业座谈"),
        ]
    )
    patch_db(fake)
    grouper = sg.StoryGrouper(
        embedder=FakeEmbedder([[1.0, 0.0], _vec(similarity)]), **kwargs
    )
    return grouper, grouper.run(window_days=14, only_reviewed=True, dry_run=True)


# --------------------------------------------------------------- 观测 --


def test_trace_off_by_default(patch_db):
    """默认不观测：``pairs_observed`` 必须为空。

    不是"省点内存"的优化，而是默认路径不产生任何额外对象的硬约束——
    十万级语料下每轮多存几万条 dict 会把常驻内存推高一个量级。
    """
    _g, result = _run(patch_db, 0.72)
    assert result.pairs_observed == []
    assert result.pairs_observed_truncated is False


def test_trace_records_pair(patch_db):
    """开启后记录相似度、门槛、口径与判定来源。"""
    _g, result = _run(patch_db, 0.72, trace_pairs=True)
    rows = result.pairs_observed
    # 双向各评估一次：pivot=a 见 b，pivot=b 见 a。
    assert len(rows) == 2
    first = rows[0]
    assert {first["left"], first["right"]} == {"a", "b"}
    assert first["similarity"] == pytest.approx(0.72, abs=1e-4)
    assert first["threshold"] == pytest.approx(0.75, abs=1e-4)
    assert first["mode"] == "vector"
    assert first["relation"] in {"same_story", "unrelated"}
    assert first["decided_by"]
    # 标题必须带上：标定明细里只有 key 的话，看到"某对 0.68 真该合"无法判读。
    assert first["left_title"] and first["right_title"]


def test_trace_keys_are_unordered(patch_db):
    """两个方向的记录必须共享同一个无序键，否则标定会把一对算成两对。

    断言 ``left <= right`` 而不是"归一后键相同"：后者掩盖了存有向键的可能——
    ``unordered_pair`` 会把 ``("b","a")`` 归一成 ``("a","b")``，用归一后的键
    去比永远相等，变异成有向存储也测不出来。
    """
    _g, result = _run(patch_db, 0.72, trace_pairs=True)
    assert result.pairs_observed
    assert all(r["left"] <= r["right"] for r in result.pairs_observed)
    keys = {sg.unordered_pair(r["left"], r["right"]) for r in result.pairs_observed}
    assert len(keys) == 1


def test_trace_truncation_is_flagged(patch_db, monkeypatch):
    """超过上限必须置位 ``pairs_observed_truncated``，绝不静默少给数据。

    标定脚本据此拒绝下结论——截断样本上算出的"带内真该合 0 对"会直接导致
    把带宽收窄、真事件消失。
    """
    monkeypatch.setattr(sg, "PAIR_TRACE_LIMIT", 1)
    _g, result = _run(patch_db, 0.72, trace_pairs=True)
    assert len(result.pairs_observed) == 1
    assert result.pairs_observed_truncated is True


def test_trace_cleared_between_runs(patch_db):
    """跨轮必须清空：不清会累加，标定按"本轮"统计就全错了。"""
    fake = FakeDB(
        [
            _unit("a", "组件价格跌破成本线"),
            _unit("b", "协会召集头部企业座谈"),
        ]
    )
    patch_db(fake)
    grouper = sg.StoryGrouper(
        embedder=FakeEmbedder([[1.0, 0.0], _vec(0.72)]), trace_pairs=True
    )
    first = grouper.run(window_days=14, only_reviewed=True, dry_run=True)
    second = grouper.run(window_days=14, only_reviewed=True, dry_run=True)
    assert len(first.pairs_observed) == len(second.pairs_observed)


# --------------------------------------------------------- 带宽配置化 --


def test_margin_defaults_to_constant(monkeypatch):
    """无配置时回落模块常量 0.05（标定结论：漏合归零的最小带宽）。"""
    from config.settings import get_settings

    monkeypatch.delenv("STORY_BORDERLINE_MARGIN", raising=False)
    get_settings.cache_clear()
    try:
        assert sg.resolve_borderline_margin() == pytest.approx(
            sg.BORDERLINE_MARGIN, abs=1e-9
        )
    finally:
        get_settings.cache_clear()


def test_margin_env_override(monkeypatch):
    from config.settings import get_settings

    monkeypatch.setenv("STORY_BORDERLINE_MARGIN", "0.12")
    get_settings.cache_clear()
    try:
        assert sg.resolve_borderline_margin() == pytest.approx(0.12)
    finally:
        get_settings.cache_clear()


def test_margin_out_of_range_falls_back(monkeypatch):
    """越界值回落常量而不是崩溃：聚类不该因为一个带宽配置项停摆。

    0.9 会被 pydantic 的 ``le=0.5`` 拦下抛 ValidationError，这里验证的是
    "抛了就降级"，不是"抛了就让进程起不来"。
    """
    from config.settings import get_settings

    monkeypatch.setenv("STORY_BORDERLINE_MARGIN", "0.9")
    get_settings.cache_clear()
    try:
        assert sg.resolve_borderline_margin() == pytest.approx(
            sg.BORDERLINE_MARGIN, abs=1e-9
        )
    finally:
        get_settings.cache_clear()


def test_margin_out_of_range_when_settings_bypassed(monkeypatch):
    """绕过 pydantic 校验时（测试替身 / 直接构造 Settings），越界值仍要回落常量。

    这是**第二道兜底**，与上一条走的不是同一条路径：上一条的 0.9 被 pydantic
    在构造期就拦掉了，根本到不了这里。只测上一条的话，越界分支是不可达代码，
    删掉它测试照样全绿——真出现"配置层被绕过"的部署形态时就没有保护了。
    """
    import config.settings as settings_mod

    class _BypassedSettings:
        story_borderline_margin = 0.9

    monkeypatch.setattr(settings_mod, "get_settings", lambda: _BypassedSettings())
    assert sg.resolve_borderline_margin() == pytest.approx(
        sg.BORDERLINE_MARGIN, abs=1e-9
    )


def test_margin_beyond_reachable_is_kept_not_clamped(monkeypatch, caplog):
    """超过可达上限时**保留用户值**并 warn，不 clamp。

    clamp 会让人以为"配了 0.20 就覆盖到 0.55"，而实际下限由召回门卡在 0.60。
    保留原值 + 明确告警，才能让人看到"你配的数有一部分根本不会生效"。
    """
    from config.settings import get_settings

    monkeypatch.setenv("STORY_BORDERLINE_MARGIN", "0.30")
    get_settings.cache_clear()
    try:
        with caplog.at_level("WARNING"):
            assert sg.resolve_borderline_margin() == pytest.approx(0.30)
        assert any("超出" in r.message or "上限" in r.message for r in caplog.records)
    finally:
        get_settings.cache_clear()


def test_explicit_margin_wins(patch_db):
    """显式传值优先于配置：标定脚本靠这个按不同带宽跑同一套语料。"""
    grouper = sg.StoryGrouper(embedder=FakeEmbedder([[1.0, 0.0]]), borderline_margin=0.08)
    assert grouper._margin() == pytest.approx(0.08)


def test_wider_margin_reaches_lower_similarity(patch_db):
    """带宽必须作用于判定链路，否则配了等于没配。

    cos=0.66：默认带宽 0.05 时下限是 0.70 → 不进复核；带宽 0.10 时下限 0.65
    → 进复核。
    """
    spy_narrow = SpyJudge()
    _g, narrow = _run(patch_db, 0.66, borderline_judge=spy_narrow)
    assert spy_narrow.seen == []
    assert narrow.borderline_rechecked == 0

    spy_wide = SpyJudge()
    _g, wide = _run(
        patch_db, 0.66, borderline_judge=spy_wide, borderline_margin=0.10
    )
    assert len(spy_wide.seen) == 1
    assert wide.borderline_rechecked == 1


def test_borderline_observation_uses_same_margin(patch_db):
    """观测带宽必须与判定带宽同源，否则 ``borderline`` 与复核量对不上。

    这条守的是第二处带宽判断（``_note_borderline``）：它决定"记不记这条临界
    明细"。那里若仍读常量 0.05，带宽配到 0.10 后会出现"复核了、判了、但不记账"
    ——运营看到的 ``borderline`` 仍是旧的 2 对，进而误判"带宽没生效"，
    反复放宽配置而不去找真正的原因。
    """
    spy = SpyJudge(relation="unrelated")
    _g, result = _run(patch_db, 0.66, borderline_judge=spy, borderline_margin=0.10)
    assert result.borderline_rechecked == 1
    assert result.borderline == 1
