"""判定提示词版本机制的单元测试。

覆盖四类，全部离线（不依赖 Ollama / PostgreSQL）：

1. **v1 逐字冻结** —— v1 是当前生产默认，任何一字改动都会让历史
   ``osint_story.model_version`` 失去可比性，必须有一个"改了就红"的用例；
2. v2 的差异点确实落地（progress 三条判据 + stage 字段）；
3. 版本解析的容错边界（未知/空/大小写），且**降级必须留痕**；
4. 版本号进 ``name``，进而进 ``model_version``——这是事后能分辨某批事件
   由哪套判据产出的唯一依据。
"""

from __future__ import annotations

import logging

import pytest

from knowledge import llm_judge as lj


# ------------------------------------------------------------ 桩件 --


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.responses.pop(0)


def _reply(**fields) -> FakeResponse:
    import json

    return FakeResponse({"message": {"content": json.dumps(fields, ensure_ascii=False)}})


def _unit(title: str = "标题", body: str = "正文", source: str = "a.cn") -> dict:
    return {
        "unit_key": "u-" + title,
        "title": title,
        "content": body,
        "source_name": source,
        "created_at": "2026-11-04T08:00:00+00:00",
    }


#: v1 渲染结果的金标准。**不要因为"看着一样"就更新它**：这个用例的价值
#: 恰恰在于拦住无意的措辞改动——v1 一旦变了，历史上所有用 v1 判定的事件
#: 都不再可比，必须新建版本号而不是原地改。
_GOLDEN_V1 = """两条事实已被向量检索筛为"可能相关"，请判断它们的关系。

【事实 A】来源：x.cn；发布：2026-11-04 08:00
标题：A标题
正文：A正文

【事实 B】来源：y.cn；发布：2026-11-04 09:00
标题：B标题
正文：B正文

在下列四个选项中选一个，必须选，不许回避：
- same_story：同一件事的同一阶段。两条在讲同一起事件、同一进展，只是报道方不同。
- progress：同一件事的不同阶段。主体相同、事态向前推进了（如"立案"→"起诉"→"判决"）。
- roundup：综述/聚合报道。一条是清单式汇总，另一条是其中某个具体事件。
- unrelated：不相关。主体不同，或主体相同但确实是两件不同的事。

判定须知：
1. 不要因为"证据不足"或"不确定"就选 unrelated。只有在你能指出两条事实的
   主体或事件确实不同时，才选 unrelated。
2. 正文可能被截断，以标题和可见部分为准，不要因为残缺就选 unrelated。
3. 同一主体（公司/机构/人物）出现在两件事里，不等于同一事件。要看事件本身。
4. 时间相隔较远但事态连续（如一个月前的立案与今天的判决），选 progress。

只输出一个 JSON 对象，格式严格如下，不要加代码块标记：
{"relation": "same_story|progress|roundup|unrelated", "reason": "不超过 60 字的中文理由"}"""


def _render(variant: str | None = None) -> str:
    return lj.build_judge_prompt(
        {
            "title": "A标题",
            "content": "A正文",
            "source_name": "x.cn",
            "created_at": "2026-11-04T08:00:00+00:00",
        },
        {
            "title": "B标题",
            "content": "B正文",
            "source_name": "y.cn",
            "created_at": "2026-11-04T09:00:00+00:00",
        },
        variant=variant,
    )


# ------------------------------------------------------- v1 冻结 --


def test_v1_rendering_is_frozen():
    """v1 是生产默认，渲染结果必须逐字不变。"""
    assert _render("v1") == _GOLDEN_V1


def test_default_variant_matches_declared_default():
    """不传版本必须等于声明的默认版本。

    这里刻意**不**断言"默认是 v1"：默认值会随标定结论变（v1→v2 就是这么来的），
    把版本号钉在用例里，翻默认时就得改成例，等于给了自己一个"改测试让它绿"的
    借口。真正该钉住的是"默认值只有一个来源"。
    """
    assert _render() == _render(lj.DEFAULT_PROMPT_VARIANT)


def test_default_is_v2_and_that_decision_is_evidenced():
    """默认版本被钉在 v2。

    这**不是**"钉一个数字图安心"，而是一条决策记录：v1 在 16 对"同一主体、
    不同事项"的硬负例上误合并 4 对（25%，2 轮零翻转），v2 为 0 对且漏合 0 对
    （8 对正例全中）。要改回去，请先重跑 ``scripts/calibrate_judge_prompt.py``
    并在这里写下新的数字——没有数字就翻默认，等于把已知缺陷重新装回去。
    """
    assert lj.DEFAULT_PROMPT_VARIANT == "v2"


def test_v1_is_still_available_after_default_flip():
    """翻默认不能删旧版本：历史事件要能凭 model_version 复现当时的判据。"""
    assert "v1" in lj.PROMPT_VARIANTS
    assert _render("v1") == _GOLDEN_V1


# ------------------------------------------------------- v2 差异 --


class TestV2:
    def test_progress_requires_single_matter(self):
        """v2 必须把"只有一个具体事项在推进"写进定义里。

        这是 v1 那四处误合并的共同缺口：v1 只说"主体相同、事态推进"，
        模型据此把"同一主体的两件并列事项"也读成了推进。
        """
        prompt = _render("v2")
        assert "只有一个具体事项" in prompt

    def test_progress_has_three_conditions(self):
        prompt = _render("v2")
        assert "progress 必须同时满足三条" in prompt
        assert "两件并列事项" in prompt

    def test_negative_example_is_out_of_domain(self):
        """反例必须是域外结构同形样本。

        若把真机语料里的央行那对写进提示词，探针集上就会变成背答案，
        标定结论随之失效——这条用例守的是标定的有效性，不是措辞。
        """
        prompt = _render("v2")
        assert "某省发布年度经济数据" in prompt
        assert "货币政策执行报告" not in prompt
        assert "中期借贷便利" not in prompt

    def test_requires_stage_field(self):
        prompt = _render("v2")
        assert '"stage"' in prompt
        assert "写不出具体阶段" in prompt

    def test_stage_is_in_the_output_format_line(self):
        """输出格式行必须**显式带** stage 的字段名与含义。

        只断言 ``'"stage"' in prompt`` 是不够的：判据段里也有
        ``必须写出 "stage" 字段``，把格式行里的 stage 删掉，模型仍然读到
        "要写 stage"却看不到字段该怎么写——真机表现是偶发漏写/写错，
        事后完全无法归因。这里钉住的是格式行本身，不是"出现过 stage 字样"。
        """
        prompt = _render("v2")
        assert '"stage": "仅 progress 填写前后阶段，否则留空"' in prompt

    def test_still_lists_all_relations(self):
        prompt = _render("v2")
        for relation in lj.JUDGE_RELATIONS:
            assert relation in prompt

    def test_does_not_leak_similarity(self):
        """v2 新增了大段判据，仍不得把余弦值或"相似度"字样带进去。"""
        prompt = _render("v2")
        assert "相似度" not in prompt
        assert "cosine" not in prompt

    def test_stays_within_budget(self):
        """提示词长度仍要留得住正文：截断后总长必须远小于上下文预算。"""
        prompt = lj.build_judge_prompt(
            _unit("标题", "正" * 5000), _unit("标题2", "正" * 5000), variant="v2"
        )
        assert len(prompt) < 3000


# --------------------------------------------------- 版本解析容错 --


class TestResolveVariant:
    def test_none_falls_back_to_default(self):
        assert lj.resolve_prompt_variant(None) == lj.DEFAULT_PROMPT_VARIANT

    def test_empty_falls_back_to_default(self):
        assert lj.resolve_prompt_variant("   ") == lj.DEFAULT_PROMPT_VARIANT

    def test_case_insensitive(self):
        assert lj.resolve_prompt_variant("V2") == "v2"

    def test_unknown_warns_and_falls_back(self, caplog):
        """未知版本要回落**并告警**：静默回落会让"配了 v3 没生效"变成幽灵问题。"""
        with caplog.at_level(logging.WARNING):
            assert lj.resolve_prompt_variant("v9") == lj.DEFAULT_PROMPT_VARIANT
        assert "v9" in caplog.text

    def test_unknown_variant_still_renders(self):
        """拼错版本不能让聚类停摆：必须照常产出一个可用提示词。"""
        assert _render("v9") == _render(lj.DEFAULT_PROMPT_VARIANT)


# ------------------------------------------------- 版本进 name --


class TestJudgeName:
    def test_default_name_carries_v1(self):
        judge = lj.OllamaRelationJudge(client=FakeClient([]), prompt_variant="v1")
        assert judge.name == "llm-ollama-v1"

    def test_explicit_variant_carries_into_name(self):
        judge = lj.OllamaRelationJudge(client=FakeClient([]), prompt_variant="v2")
        assert judge.name == "llm-ollama-v2"

    def test_name_is_not_shared_across_instances(self):
        """``name`` 是实例属性：两个版本并存时不能互相覆盖（曾写成类属性）。"""
        a = lj.OllamaRelationJudge(client=FakeClient([]), prompt_variant="v1")
        b = lj.OllamaRelationJudge(client=FakeClient([]), prompt_variant="v2")
        assert a.name != b.name

    def test_name_reaches_model_version(self):
        """``name`` 是 ``osint_story.model_version`` 的来源，必须带版本。"""
        judge = lj.OllamaRelationJudge(client=FakeClient([]), prompt_variant="v2")
        # story_group 用 self.judge.name 写 model_version，这里直接验证接口契约
        assert judge.name.startswith("llm-ollama-")
        assert judge.name.endswith("v2")


# ------------------------------------------------- 实际发出去的 --


def test_variant_reaches_the_wire():
    """版本必须真的改变发出去的请求体，而不是只改了日志里的名字。"""
    client = FakeClient([_reply(relation="unrelated", reason="x")])
    lj.OllamaRelationJudge(client=client, prompt_variant="v2").judge(
        _unit("甲"), _unit("乙"), 0.7
    )
    user_content = client.calls[0]["json"]["messages"][1]["content"]
    assert "只有一个具体事项" in user_content

    client_v1 = FakeClient([_reply(relation="unrelated", reason="x")])
    lj.OllamaRelationJudge(client=client_v1, prompt_variant="v1").judge(
        _unit("甲"), _unit("乙"), 0.7
    )
    user_v1 = client_v1.calls[0]["json"]["messages"][1]["content"]
    assert "只有一个具体事项" not in user_v1


# ------------------------------------------------------ stage 解析 --


class TestStageParsing:
    def test_stage_extracted(self):
        relation, reason, stage = lj.parse_judge_reply_ex(
            '{"relation":"progress","reason":"推进","stage":"A：立案 → B：处罚"}'
        )
        assert relation == "progress"
        assert reason == "推进"
        assert stage == "A：立案 → B：处罚"

    def test_missing_stage_is_empty_not_error(self):
        """v1 回复里没有 stage，不能因此判失败——那会让 v1 全线降级。"""
        _, _, stage = lj.parse_judge_reply_ex('{"relation":"same_story","reason":"r"}')
        assert stage == ""

    def test_two_tuple_still_works(self):
        """旧调用方依赖二元组，不能因为加了 stage 就破坏签名。"""
        assert lj.parse_judge_reply('{"relation":"unrelated","reason":"r"}') == (
            "unrelated",
            "r",
        )

    def test_invalid_relation_still_raises(self):
        with pytest.raises(lj.JudgeUnavailableError):
            lj.parse_judge_reply_ex('{"relation":"maybe","reason":"r"}')


def test_stage_appended_to_rationale():
    """stage 要进 ``rationale``：日后发现某次 progress 判错，才知道错在哪一步。"""
    client = FakeClient([_reply(relation="progress", reason="推进", stage="A：立案 → B：处罚")])
    verdict = lj.OllamaRelationJudge(client=client, prompt_variant="v2").judge(
        _unit("甲"), _unit("乙"), 0.7
    )
    assert verdict.relation == "progress"
    assert "stage:A：立案 → B：处罚" in verdict.rationale
