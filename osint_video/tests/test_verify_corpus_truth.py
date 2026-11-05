"""验收语料的真值自洽性（2026-11-04 solo-18 真值复核后新增）。

## 为什么要有这个文件

``tests/fixtures/verify_corpus.json`` 是全部聚类质量评估的**唯一真值来源**。
它一旦被误改，``eval_story_quality.py`` 打出的拆散/误合并数字就全是假的，
而这类错误不会让任何测试失败——数字照样"看起来合理"。

2026-11-04 的复核确认：原先标为独立事件的 ``solo-18``（光伏行业协会发布
行业自律倡议书）实际属于 ``S11``。判断依据是同语料内已有的标注口径，而不是
"改完算法就对了"：

============================  =======  ==========  ============================
已标注 story                  跨度     成员关系    说明
============================  =======  ==========  ============================
S02                           19 天    立案→处罚   同一案件的不同程序节点
S07                           22 天    提价→再提价 同一动作的重复发生
S12                           33 天    读数→新读数 同一指标的不同时点
S14                           29 天    决定→落地   同一政策的不同阶段
============================  =======  ==========  ============================

即语料接受"同一主体 + 同一事项的连续进展"跨月成组。按此口径，``S11`` 的
"呼吁自律 → 召集座谈 → 发布倡议书"是同一治理链条的三个动作，必须同组；
原标注与上表自相矛盾。

## 本文件锁什么

1. 语料结构常量（防止迁移动到别处的编号）；
2. **防止回退**：断言该条已在 ``S11`` 内、且不再以 ``solo-18`` 存在；
3. 反向不变式：其余 singleton 确实都是"同主体的另一类事项"，不是漏归。

第 3 条无法自动判定语义，故退化为可检验的形式——断言除 ``S11`` 外没有
任何 singleton 与所属 story 共享"动作链标题词"。这是范围收窄的近似，只
拦截最明显的一类回归（把同一件事的后续报道又标成独立事件）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import seed_verify_corpus as sv  # noqa: E402


@pytest.fixture(scope="module")
def corpus() -> dict:
    return json.loads(sv.CORPUS_PATH.read_text(encoding="utf-8"))


def _story(corpus: dict, story_id: str) -> dict:
    for story in corpus["stories"]:
        if story["story_id"] == story_id:
            return story
    raise AssertionError(f"语料中不存在 {story_id}")


# ---------------------------------------------------------------- 结构常量 --


def test_corpus_shape_is_stable(corpus):
    """事实总数与多事实故事 / 独事实条数不变。

    迁移只改变归属，不增删事实。这四条数字同时变动说明改错了地方。
    """
    assert len(corpus["stories"]) == 14
    assert len(corpus["singletons"]) == 17
    total = sum(len(s["units"]) for s in corpus["stories"]) + len(corpus["singletons"])
    assert total == 50


def test_truth_summary_matches_corpus(corpus):
    """``truth_summary`` 报出的期望事件数必须与实际结构一致。"""
    rows = sv.build_rows(corpus)
    summary = sv.truth_summary(rows)
    assert summary["units"] == 50
    assert summary["stories_with_multiple_facts"] == 14
    assert summary["singleton_units"] == 17
    assert summary["expected_stories"] == 31


def test_unit_keys_are_contiguous(corpus):
    """singleton 编号必须连续，story 内编号从 01 起。

    迁移把 singletons 的最后一条移进 ``S11``，因此 ``solo-01..17`` 应无断层。
    若将来有人在中间删条目，编号会跳号——那会让库里残留一条指向不存在事实的
    成员边，且入库是 ``ON CONFLICT DO NOTHING``，不会报错。
    """
    rows = sv.build_rows(corpus)
    solo_keys = sorted(r["unit_key"] for r in rows if "solo" in r["unit_key"])
    assert solo_keys == [f"{sv.SEED_PREFIX}solo-{i:02d}" for i in range(1, 18)]

    for story in corpus["stories"]:
        sid = story["story_id"].lower()
        keys = sorted(
            r["unit_key"] for r in rows if f"-{sid}-" in r["unit_key"]
        )
        expected = [
            f"{sv.SEED_PREFIX}{sid}-{i:02d}" for i in range(1, len(story["units"]) + 1)
        ]
        assert keys == expected, f"{story['story_id']} 编号不连续：{keys}"


# ------------------------------------------------------------ 防止回退 --


def test_solo18_is_now_part_of_s11(corpus):
    """原 ``solo-18``（行业自律倡议书）必须在 ``S11`` 内。

    这条是复核结论的锚。它失败意味着有人把真值改回去了——而"改回去"会让
    ``eval_story_quality`` 把一次**正确的合并**报成误合并，进而诱导下一轮把
    阈值调高，把 4 个真实事件从日报里抹掉。
    """
    s11 = _story(corpus, "S11")
    titles = [u["title"] for u in s11["units"]]
    assert "光伏行业协会发布行业自律倡议书" in titles
    assert len(s11["units"]) == 3


def test_solo18_key_no_longer_exists(corpus):
    """``verify-solo-18`` 这个 key 不该再出现（已迁为 ``verify-s11-03``）。"""
    rows = sv.build_rows(corpus)
    keys = {r["unit_key"] for r in rows}
    assert "verify-solo-18" not in keys
    assert "verify-s11-03" in keys


def test_solo18_keeps_its_original_payload(corpus):
    """迁移只改归属，正文/来源/时间一个字段都不能动。

    真值复核的理由是"归属判错"，不是"这条事实本身有问题"。若连内容也改了，
    复核结论就失去可比性——新旧两次评估比的将不再是同一条事实。
    """
    s11 = _story(corpus, "S11")
    migrated = [u for u in s11["units"] if u["title"].startswith("光伏行业协会发布")]
    assert len(migrated) == 1
    unit = migrated[0]
    assert unit["content"] == (
        "中国光伏行业协会发布倡议书，呼吁企业避免低于成本价投标，维护行业秩序。"
    )
    assert unit["domain"] == "solarbe.com"
    assert unit["day_offset"] == -3
    assert unit["entities"] == ["中国光伏行业协会"]


def test_migration_is_documented_in_corpus(corpus):
    """迁移痕迹必须留在语料里，便于溯源。

    真值变更若不留痕，半年后没人说得清"为什么 S11 是 3 条而文档写 2 条"。
    """
    s11 = _story(corpus, "S11")
    migrated = [u for u in s11["units"] if "_migrated_from" in u]
    assert len(migrated) == 1
    assert "solo-18" in migrated[0]["_migrated_from"]


# ------------------------------------------------------- 反向不变式 --


#: "动作链"标题词。命中任一组的 singleton，若又与某 story 同主体，说明它很
#: 可能是同一件事的后续治理动作被误标为独立事件——正是 solo-18 的形态。
#:
#: 注意判据**只看 singleton 一侧**。第一版要求两侧标题都命中同一组词，结果
#: solo-18 那条（story 侧标题只有"呼吁自律"、没有"倡议"）反而不会被报警，
#: 护栏在最该拦的场景下是失效的。动作词表明"这是一次动作"，而"是否与既有
#: story 同一件事"由同主体 + 人工复核确认，两侧同时命中不是必要条件。
_CHAIN_MARKERS = [
    ("倡议", "自律"),
    ("座谈", "价格"),
    ("落地", "决定"),
]


def test_no_other_singleton_looks_like_a_chain_step(corpus):
    """除了已修正的 S11，其余 singleton 都不该像"同一件事的后续动作"。

    这是范围内最明显一类回归的护栏，不是完整的语义判别。它拦不住"换个说法
    描述同一件事"，但能拦住"把后续动作标成独立事件"这种最省事的偷懒标注——
    而那恰好会让 ``eval_story_quality`` 把正确合并报成误合并。
    """
    story_entities: dict[str, set[str]] = {}
    for story in corpus["stories"]:
        ents: set[str] = set()
        for unit in story["units"]:
            for e in unit.get("entities") or []:
                ents.add(e["name"] if isinstance(e, dict) else e)
        story_entities[story["story_id"]] = ents

    offenders = []
    for index, singleton in enumerate(corpus["singletons"], start=1):
        ents = {
            e["name"] if isinstance(e, dict) else e
            for e in singleton.get("entities") or []
        }
        if not any(ents & story_ents for story_ents in story_entities.values()):
            continue
        for left, right in _CHAIN_MARKERS:
            if left in singleton["title"] and right in singleton["title"]:
                shared = [
                    sid for sid, se in story_entities.items() if ents & se
                ]
                offenders.append((index, singleton["title"], shared))
                break

    assert offenders == [], (
        "以下 singleton 标题含动作链词且与既有 story 同主体，疑似同一件事被标成"
        f"独立事件，请人工复核：{offenders}"
    )
