"""Ollama 关系判定器（第二层防错）的单元测试。

覆盖四类：
1. 提示词构造（四分类齐全、不泄露余弦值）
2. 回复解析（容错代码块/夹带文字，非法标签必须抛异常而非猜）
3. 传输层（熔断、预算、HTTP 异常、缺字段、health）
4. 与算子的降级联动（判定器故障必须置 degraded，且回落规则判定）

所有用例不依赖 PostgreSQL / Ollama，通过 fake client、fake DB 与端口注入。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from common import db as db_module
from knowledge import llm_judge as lj
from knowledge import story_group as sg
from tests.fakes import FakeDB


# ------------------------------------------------------------ 桩件 --


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    """记录入参的最小 HTTP 客户端；responses 逐个消费。"""

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if not self.responses:
            raise AssertionError("客户端被多调用了一次（熔断/预算失效）")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _chat_ok(relation: str, reason: str = "主体与事件均一致") -> FakeResponse:
    return FakeResponse(
        {"message": {"content": '{"relation": "%s", "reason": "%s"}' % (relation, reason)}}
    )


def _unit(key: str, title: str, url: str, content: str = "") -> dict:
    return {
        "unit_key": key,
        "unit_version": 1,
        "title": title,
        "content": content,
        "source_url": url,
        "source_link": None,
        "blogger_key": None,
        "resource_key": f"res-{key}",
        "entities": [],
        "topics": [],
        "review_status": "reviewed",
        "created_at": datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc),
    }


class FakeEmbedder:
    """按调用顺序返回预设向量，用于把余弦精确落在复核区间。"""

    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors
        self.calls = 0

    def embed_texts(self, texts):
        self.calls += 1
        return self.vectors[: len(texts)]


@pytest.fixture()
def patch_db(monkeypatch):
    def _install(fake: FakeDB) -> FakeDB:
        monkeypatch.setattr(db_module, "fetch_all", fake.fetch_all)
        monkeypatch.setattr(db_module, "insert_returning", fake.insert_returning)
        monkeypatch.setattr(db_module, "execute", fake.execute)
        return fake

    return _install


def _judge(client, **kwargs) -> lj.OllamaRelationJudge:
    return lj.OllamaRelationJudge(client=client, **kwargs)


#: 与 [1, 0] 余弦恰为 0.70 / 0.90 的两个单位向量。
_VEC_070 = [0.70, math.sqrt(1 - 0.49)]
_VEC_090 = [0.90, math.sqrt(1 - 0.81)]


# -------------------------------------------------------- 提示词构造 --


class TestBuildPrompt:
    def test_contains_both_sides_and_sources(self):
        prompt = lj.build_judge_prompt(
            _unit("u1", "宁德时代发布麒麟电池", "https://cls.cn/a/1"),
            _unit("u2", "宁德时代麒麟电池量产", "https://eastmoney.com/b/2"),
        )
        assert "宁德时代发布麒麟电池" in prompt
        assert "宁德时代麒麟电池量产" in prompt
        assert "cls.cn" in prompt
        assert "eastmoney.com" in prompt

    def test_lists_all_four_relations(self):
        prompt = lj.build_judge_prompt(_unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"))
        for relation in lj.JUDGE_RELATIONS:
            assert relation in prompt

    def test_does_not_leak_similarity_score(self):
        # 余弦值会变成模型的答案锚点，反而放大阈值本身的偏差
        prompt = lj.build_judge_prompt(_unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"))
        assert "cosine" not in prompt
        assert "0.72" not in prompt
        assert "相似度" not in prompt

    def test_truncates_long_body(self):
        prompt = lj.build_judge_prompt(
            _unit("u1", "标题", "https://a.cn", content="正" * 5000),
            _unit("u2", "标题", "https://b.cn"),
        )
        assert len(prompt) < 3000

    def test_missing_fields_fall_back_to_placeholder(self):
        prompt = lj.build_judge_prompt({}, {})
        assert "（无标题）" in prompt
        assert "未知来源" in prompt


# -------------------------------------------------------- 回复解析 --


class TestParseReply:
    def test_plain_json(self):
        assert lj.parse_judge_reply('{"relation":"progress","reason":"事态推进"}') == (
            "progress",
            "事态推进",
        )

    def test_fenced_json(self):
        raw = '```json\n{"relation":"same_story","reason":"同一事件"}\n```'
        assert lj.parse_judge_reply(raw)[0] == "same_story"

    def test_prose_around_json(self):
        raw = '我认为如下：{"relation":"roundup","reason":"综述"} 以上。'
        assert lj.parse_judge_reply(raw)[0] == "roundup"

    def test_uppercase_relation_tolerated(self):
        assert lj.parse_judge_reply('{"relation":"SAME_STORY"}')[0] == "same_story"

    def test_invalid_relation_raises(self):
        # 非法标签绝不能当成 unrelated —— 那会静默丢掉真合并
        with pytest.raises(lj.JudgeUnavailableError):
            lj.parse_judge_reply('{"relation":"yes","reason":"看着像"}')

    def test_empty_raises(self):
        with pytest.raises(lj.JudgeUnavailableError):
            lj.parse_judge_reply("")

    def test_no_json_raises(self):
        with pytest.raises(lj.JudgeUnavailableError):
            lj.parse_judge_reply("这两条看起来是一件事")

    def test_broken_json_raises(self):
        with pytest.raises(lj.JudgeUnavailableError):
            lj.parse_judge_reply('{"relation": "same_story", ')

    def test_missing_reason_gets_placeholder(self):
        relation, reason = lj.parse_judge_reply('{"relation":"unrelated"}')
        assert relation == "unrelated"
        assert reason

    def test_reason_truncated(self):
        _, reason = lj.parse_judge_reply(
            '{"relation":"progress","reason":"%s"}' % ("长" * 500)
        )
        assert len(reason) <= lj.REASON_CHARS


# -------------------------------------------------------- 传输层 --


class TestTransport:
    def test_happy_path_returns_llm_verdict(self):
        client = FakeClient([_chat_ok("same_story")])
        verdict = _judge(client).judge(
            _unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.72
        )
        assert verdict.relation == "same_story"
        assert verdict.decided_by == "llm"
        assert verdict.failed is False
        assert "same_story" in verdict.rationale

    def test_request_shape(self):
        client = FakeClient([_chat_ok("progress")])
        _judge(client).judge(_unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7)
        sent = client.calls[0]
        assert sent["url"].endswith("/api/chat")
        assert sent["json"]["stream"] is False
        assert sent["json"]["format"] == "json"
        assert sent["json"]["options"]["temperature"] == 0
        assert len(sent["json"]["messages"]) == 2

    def test_thinking_disabled(self):
        """qwen3.5:9b 是思考型模型：不关思考时 content 恒为空、耗时 48 秒。"""
        client = FakeClient([_chat_ok("same_story")])
        _judge(client).judge(_unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7)
        assert client.calls[0]["json"]["think"] is False

    def test_keep_alive_pinned(self):
        """冷加载 50 秒 vs 计算 1.5 秒——必须由客户端钉住驻留。"""
        client = FakeClient([_chat_ok("same_story")])
        _judge(client).judge(_unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7)
        assert client.calls[0]["json"]["keep_alive"] == lj.KEEP_ALIVE

    def test_thinking_fallback_when_content_empty(self):
        """某些 Ollama 版本不认 think=false，结果只落在 thinking 段。"""
        client = FakeClient(
            [
                FakeResponse(
                    {
                        "message": {
                            "content": "",
                            "thinking": '思考如下：{"relation":"progress","reason":"阶段推进"}',
                        }
                    }
                )
            ]
        )
        verdict = _judge(client).judge(
            _unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7
        )
        assert verdict.failed is False
        assert verdict.relation == "progress"

    def test_connection_error_returns_failed_not_unrelated(self):
        client = FakeClient([ConnectionError("connection refused")])
        verdict = _judge(client).judge(
            _unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.72
        )
        assert verdict.failed is True
        # relation 字段此时无意义，调用方必须看 failed
        assert "llm_failed" in verdict.rationale

    def test_circuit_breaker_after_transport_error(self):
        client = FakeClient([ConnectionError("boom"), _chat_ok("same_story")])
        judge = _judge(client)
        first = judge.judge(_unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7)
        second = judge.judge(_unit("u3", "C", "https://c.cn"), _unit("u4", "D", "https://d.cn"), 0.7)

        assert first.failed and second.failed
        # 熔断后不再发起请求，避免每次都白等一个超时
        assert len(client.calls) == 1

    def test_budget_exhausted(self):
        client = FakeClient([_chat_ok("same_story"), _chat_ok("same_story")])
        judge = _judge(client, max_calls=1)
        judge.judge(_unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7)
        verdict = judge.judge(_unit("u3", "C", "https://c.cn"), _unit("u4", "D", "https://d.cn"), 0.7)

        assert verdict.failed is True
        assert "预算" in verdict.rationale
        assert len(client.calls) == 1

    def test_http_error(self):
        client = FakeClient([FakeResponse({"error": "model not found"}, status_code=404)])
        verdict = _judge(client).judge(
            _unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7
        )
        assert verdict.failed is True
        assert "404" in verdict.rationale

    def test_missing_content_field(self):
        client = FakeClient([FakeResponse({"message": {}})])
        verdict = _judge(client).judge(
            _unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7
        )
        assert verdict.failed is True

    def test_generate_style_response_accepted(self):
        client = FakeClient([FakeResponse({"response": '{"relation":"progress"}'})])
        verdict = _judge(client).judge(
            _unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7
        )
        assert verdict.relation == "progress"

    def test_calls_counter(self):
        judge = _judge(FakeClient([_chat_ok("same_story")]))
        assert judge.calls == 0
        judge.judge(_unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7)
        assert judge.calls == 1

    def test_reset_budget_clears_breaker(self):
        client = FakeClient([ConnectionError("boom"), _chat_ok("same_story")])
        judge = _judge(client)
        assert judge.judge(_unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7).failed
        judge.reset_budget()
        assert judge.calls == 0
        verdict = judge.judge(_unit("u3", "C", "https://c.cn"), _unit("u4", "D", "https://d.cn"), 0.7)
        assert verdict.failed is False

    def test_health_true_and_false(self):
        assert _judge(FakeClient([_chat_ok("unrelated")])).health() is True
        assert _judge(FakeClient([ConnectionError("boom")])).health() is False

    def test_health_does_not_poison_state(self):
        """探活失败不得让后续真实调用短路，否则运维探一次就废掉整轮。"""
        judge = _judge(FakeClient([ConnectionError("boom"), _chat_ok("same_story")]))
        assert judge.health() is False
        verdict = judge.judge(
            _unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7
        )
        assert verdict.failed is False
        assert judge.calls == 1

    def test_warmup_absorbs_cold_load_and_resets(self):
        client = FakeClient([_chat_ok("unrelated"), _chat_ok("same_story")])
        judge = _judge(client)
        assert judge.warmup() is True
        # 预热不计入预算
        assert judge.calls == 0
        verdict = judge.judge(
            _unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7
        )
        assert verdict.failed is False
        assert judge.calls == 1

    def test_warmup_failure_clears_breaker(self):
        """预热失败后仍允许真实调用再试一次（连接被拒本身是毫秒级）。"""
        client = FakeClient([ConnectionError("boom"), _chat_ok("same_story")])
        judge = _judge(client)
        assert judge.warmup() is False
        verdict = judge.judge(
            _unit("u1", "A", "https://a.cn"), _unit("u2", "B", "https://b.cn"), 0.7
        )
        assert verdict.failed is False


# ------------------------------------------------ 与算子的降级联动 --


class _FailingJudge:
    """永远失败的判定器，用来验证降级通道。"""

    name = "failing-v1"

    def __init__(self) -> None:
        self.calls = 0

    def judge(self, unit, target, similarity):
        self.calls += 1
        return sg.Verdict("unrelated", "llm", "llm_failed: test", failed=True)

    def reset_budget(self) -> None:
        self.calls = 0


class _SpyJudge:
    """固定判 same_story，用来验证正常路径不被降级。"""

    name = "spy-v1"

    def __init__(self, relation: str = "same_story") -> None:
        self.calls = 0
        self.relation = relation

    def judge(self, unit, target, similarity):
        self.calls += 1
        return sg.Verdict(self.relation, "llm", "spy")

    def reset_budget(self) -> None:
        pass


def _two_units():
    return [
        _unit("u1", "宁德时代发布麒麟电池", "https://cls.cn/a/1"),
        _unit("u2", "宁德时代麒麟电池量产", "https://eastmoney.com/b/2"),
    ]


class TestDegradePropagation:
    def test_failing_judge_marks_run_degraded(self, patch_db):
        patch_db(FakeDB(_two_units()))
        result = sg.StoryGrouper(
            embedder=FakeEmbedder([[1.0, 0.0], _VEC_070]), judge=_FailingJudge()
        ).run()

        assert result.judge_failures == 1
        assert result.degraded is True
        assert "judge_failed" in (result.degrade_reason or "")

    def test_failing_judge_falls_back_to_rules(self, patch_db):
        patch_db(FakeDB(_two_units()))
        spy = _SpyJudge(relation="same_story")
        result = sg.StoryGrouper(
            embedder=FakeEmbedder([[1.0, 0.0], _VEC_070]),
            judge=_FailingJudge(),
            fallback_judge=spy,
        ).run()

        # 回落判定器必须被调用，否则等于把故障当无关
        assert spy.calls == 1
        assert result.degraded is True

    def test_healthy_judge_not_degraded(self, patch_db):
        patch_db(FakeDB(_two_units()))
        spy = _SpyJudge()
        result = sg.StoryGrouper(
            embedder=FakeEmbedder([[1.0, 0.0], _VEC_070]), judge=spy
        ).run()

        assert spy.calls == 1
        assert result.judge_failures == 0
        assert result.degraded is False
        assert result.degrade_reason is None

    def test_rule_judge_alone_stays_healthy(self, patch_db):
        """缺省（纯规则）路径不得因新增字段而误报降级。"""
        patch_db(FakeDB(_two_units()))
        result = sg.StoryGrouper(embedder=FakeEmbedder([[1.0, 0.0], _VEC_070])).run()

        assert result.judge_failures == 0
        assert result.degraded is False
        assert result.judge_calls == 0

    def test_high_similarity_skips_judge_entirely(self, patch_db):
        patch_db(FakeDB(_two_units()))
        spy = _SpyJudge()
        result = sg.StoryGrouper(
            embedder=FakeEmbedder([[1.0, 0.0], _VEC_090]), judge=spy
        ).run()

        assert spy.calls == 0
        assert result.members_added >= 2

    def test_degrade_reason_merges_with_embed_degrade(self, patch_db):
        """嵌入降级与判定降级同时发生时，两条原因都要留痕。

        嵌入降级后相似度退回字符二元组 Jaccard，因此这对文本的 Jaccard 必须
        落在 ``[RECALL_SIMILARITY, RECHECK_SIMILARITY)`` 内才会进入判定环节
        （实测 0.786）。
        """

        class _BrokenEmbedder:
            def embed_texts(self, texts):
                raise RuntimeError("ollama down")

        patch_db(
            FakeDB(
                [
                    _unit(
                        "u1",
                        "宁德时代发布麒麟电池，续航突破一千公里，计划明年量产装车",
                        "https://cls.cn/a/1",
                    ),
                    _unit(
                        "u2",
                        "宁德时代发布麒麟电池，续航突破一千公里，将于明年量产装车",
                        "https://eastmoney.com/b/2",
                    ),
                ]
            )
        )
        result = sg.StoryGrouper(
            embedder=_BrokenEmbedder(), judge=_FailingJudge()
        ).run()

        reason = result.degrade_reason or ""
        assert "embed_failed" in reason
        assert "judge_failed" in reason
        assert result.judge_failures == 1


# ------------------------------------------------------ 实体别名接线 --


class TestAliasWiring:
    """别名表必须真的改变聚类结果，否则它只是写在盘上的代码。

    这对事实文本相似（Jaccard ≈ 0.786）但实体写法不同：一条写"宁德时代"，
    一条写"CATL"。算子的候选召回按实体交集过滤（``_candidates``），
    别名未归一时两边被判断为不同实体、压根互不成候选。
    """

    _TEXT_A = "宁德时代发布麒麟电池，续航突破一千公里，计划明年量产装车"
    _TEXT_B = "宁德时代发布麒麟电池，续航突破一千公里，将于明年量产装车"

    def _units(self):
        left = _unit("u1", self._TEXT_A, "https://cls.cn/a/1")
        right = _unit("u2", self._TEXT_B, "https://eastmoney.com/b/2")
        left["entities"] = ["宁德时代"]
        right["entities"] = ["CATL"]
        return [left, right]

    def test_without_alias_entities_do_not_match(self, patch_db):
        patch_db(FakeDB(self._units()))
        result = sg.StoryGrouper().run()

        # 实体写法不同 → 互不成候选 → 各自独立成事件
        assert result.stories_created == 2

    def test_with_alias_map_merges_into_one_story(self, patch_db):
        patch_db(FakeDB(self._units()))
        result = sg.StoryGrouper(
            alias_map={"宁德时代": "catl", "CATL": "catl"}
        ).run()

        assert result.stories_created == 1

    def test_alias_map_loaded_from_db(self, patch_db, monkeypatch):
        """不显式传 alias_map 时，应从 osint_entity_alias 惰性装载。"""
        fake = patch_db(FakeDB(self._units()))
        # 让 fake 对别名查询返回两行
        original = fake.fetch_all

        def fetch_all(sql, params=None):
            if "osint_entity_alias" in sql:
                return [
                    {"alias": "宁德时代", "canonical_id": "catl"},
                    {"alias": "CATL", "canonical_id": "catl"},
                ]
            return original(sql, params)

        monkeypatch.setattr(db_module, "fetch_all", fetch_all)

        result = sg.StoryGrouper().run()
        assert result.stories_created == 1

    def test_alias_load_failure_does_not_break_run(self, patch_db, monkeypatch):
        """别名表读失败只降级为原文实体，不得中断聚类。"""
        fake = patch_db(FakeDB(self._units()))
        original = fake.fetch_all

        def fetch_all(sql, params=None):
            if "osint_entity_alias" in sql:
                raise RuntimeError("relation osint_entity_alias does not exist")
            return original(sql, params)

        monkeypatch.setattr(db_module, "fetch_all", fetch_all)

        result = sg.StoryGrouper(embedder=FakeEmbedder([[1.0, 0.0], _VEC_070])).run()
        # 未崩溃，且走的是原文实体口径
        assert result.units_scanned == 2
        assert result.degraded is False
