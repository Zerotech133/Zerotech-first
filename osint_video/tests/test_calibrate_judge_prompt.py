"""判定提示词标定脚本的纯函数测试（不调模型、不连库）。

守的是**标定数据本身可信**：探针集有问题时，脚本必须报错而不是给出一张
看起来合理的表。漏一条样本不会报错，只会让 ``false_merge`` 的分子分母同时
少一项，结论反而更漂亮——这是最危险的一种"通过"。
"""

from __future__ import annotations

import pytest

from scripts import calibrate_judge_prompt as cal


def _row(
    cid: str,
    expected_merge: bool,
    merged: bool,
    category: str = "parallel_false",
    failed: bool = False,
) -> dict:
    return {
        "id": cid,
        "category": category,
        "expected_merge": expected_merge,
        "merged": merged,
        "correct": expected_merge == merged,
        "relation": "same_story" if merged else "unrelated",
        "failed": failed,
        "rationale": "",
        "why": "",
    }


# ------------------------------------------------------------ 统计 --


class TestSummarize:
    def test_confusion_counts(self):
        rows = [
            _row("p1", True, True, "same_true"),
            _row("p2", True, False, "progress_true"),  # 漏合
            _row("n1", False, False),
            _row("n2", False, True),  # 误合并
            _row("n3", False, True),  # 误合并
        ]
        s = cal.summarize(rows)
        assert s["should_merge"] == 2
        assert s["merged_ok"] == 1
        assert s["missed_merge"] == 1
        assert s["should_not_merge"] == 3
        assert s["kept_apart_ok"] == 1
        assert s["false_merge"] == 2
        assert s["correct"] == 2
        assert s["accuracy"] == pytest.approx(0.4)
        assert s["missed_ids"] == ["p2"]
        assert sorted(s["false_merge_ids"]) == ["n2", "n3"]

    def test_perfect_run(self):
        rows = [_row("p", True, True, "same_true"), _row("n", False, False)]
        s = cal.summarize(rows)
        assert s["false_merge"] == 0
        assert s["missed_merge"] == 0
        assert s["accuracy"] == pytest.approx(1.0)

    def test_label_name_does_not_matter(self):
        """只看合并决策、不看标签名：progress 与 same_story 对来源数影响相同。"""
        rows = [_row("p", True, True)]
        rows[0]["relation"] = "progress"
        assert cal.summarize(rows)["merged_ok"] == 1

    def test_failed_calls_counted_separately(self):
        """故障调用单独计数：把故障混进"判对"会让一轮挂掉的运行看起来很准。"""
        rows = [_row("n", False, False, failed=True)]
        s = cal.summarize(rows)
        assert s["failed_calls"] == 1
        assert s["correct"] == 1

    def test_by_category(self):
        rows = [
            _row("a", True, True, "same_true"),
            _row("b", True, False, "progress_true"),
            _row("c", False, True, "parallel_false"),
        ]
        cats = cal.summarize(rows)["by_category"]
        assert cats["same_true"] == {"total": 1, "correct": 1}
        assert cats["progress_true"] == {"total": 1, "correct": 0}
        assert cats["parallel_false"] == {"total": 1, "correct": 0}

    def test_empty(self):
        s = cal.summarize([])
        assert s["total"] == 0
        assert s["accuracy"] == 0.0


# ------------------------------------------------------- 探针集校验 --


def _probe(cases: list[dict]) -> dict:
    return {"cases": cases}


def _case(cid="c1", left="a", right="b", expected=True) -> dict:
    return {"id": cid, "left": left, "right": right, "expected_merge": expected}


class TestVerifyProbe:
    def test_clean_probe_passes(self):
        probe = _probe([_case("c1", "a", "b"), _case("c2", "a", "c", False)])
        assert cal.verify_probe(probe, {"a": {}, "b": {}, "c": {}}) == []

    def test_empty_probe_rejected(self):
        assert cal.verify_probe(_probe([]), {}) == ["探针集为空"]

    def test_missing_unit_reported(self):
        problems = cal.verify_probe(_probe([_case()]), {"a": {}})
        assert any("找不到 b" in p for p in problems)

    def test_self_pair_rejected(self):
        """自配对必然判同，等于白送一个真阳性。"""
        problems = cal.verify_probe(_probe([_case("c1", "a", "a")]), {"a": {}})
        assert any("同一条事实" in p for p in problems)

    def test_duplicate_pair_rejected(self):
        probe = _probe([_case("c1", "a", "b"), _case("c2", "b", "a")])
        problems = cal.verify_probe(probe, {"a": {}, "b": {}})
        assert any("配对重复" in p for p in problems)

    def test_duplicate_id_rejected(self):
        probe = _probe([_case("c1", "a", "b"), _case("c1", "a", "c")])
        problems = cal.verify_probe(probe, {"a": {}, "b": {}, "c": {}})
        assert any("id 重复" in p for p in problems)

    def test_non_bool_label_rejected(self):
        """``expected_merge`` 写成字符串 "false" 会被 ``bool()`` 读成 True，
        整份统计的符号翻转，且不报任何错。"""
        probe = _probe([{"id": "c1", "left": "a", "right": "b", "expected_merge": "false"}])
        problems = cal.verify_probe(probe, {"a": {}, "b": {}})
        assert any("expected_merge" in p for p in problems)


# ------------------------------------------------------- 真实探针集 --


class TestRealFixture:
    def test_fixture_is_loadable_and_consistent(self):
        """仓库内探针集必须自洽：键齐全、无自配对、无重复对。"""
        probe = cal.load_probe()
        cases = probe["cases"]
        assert len(cases) >= 20
        units = {k: {} for c in cases for k in (c["left"], c["right"])}
        assert cal.verify_probe(probe, units) == []

    def test_fixture_has_both_labels(self):
        """只有正例或只有负例的探针集，量不出任何有意义的差。"""
        cases = cal.load_probe()["cases"]
        assert any(c["expected_merge"] for c in cases)
        assert any(not c["expected_merge"] for c in cases)

    def test_fixture_covers_the_false_merge_mode(self):
        """必须包含"同一主体两项并列事项"这一类——技术债 #16 的唯一来源。"""
        cases = cal.load_probe()["cases"]
        cats = {c.get("category") for c in cases}
        assert "parallel_false" in cats
        assert "progress_true" in cats

    def test_prompt_negative_example_is_not_in_probe(self):
        """提示词里的反例若出现在探针集，标定就是背答案。

        这条守的是一条容易被"顺手优化"破坏的性质：为了让 v2 表现更好而把
        难样本写进提示词，指标立刻漂亮，实际泛化能力毫无变化。
        """
        from knowledge import llm_judge as lj

        probe_text = " ".join(
            "%s %s" % (c.get("left", ""), c.get("why", ""))
            for c in cal.load_probe()["cases"]
        )
        # 提示词反例的关键词只应存在于提示词，不应落在探针集的任何说明里
        assert "某省发布年度经济数据" not in probe_text
        assert "某省发布年度经济数据" in lj.PROMPT_VARIANTS["v2"]


# ------------------------------------------------------------ 渲染 --


def test_format_table_marks_false_merge():
    s = cal.summarize([_row("n", False, True)])
    table = cal.format_table({"v1": s})
    assert "**1**" in table
    assert "v1" in table


def test_format_detail_lists_misses():
    rows = [_row("p", True, False, "progress_true")]
    text = cal.format_detail(rows, "v1")
    assert "漏合" in text
    assert "p" in text


def test_format_detail_all_correct():
    assert "全部判对" in cal.format_detail([_row("p", True, True)], "v2")
