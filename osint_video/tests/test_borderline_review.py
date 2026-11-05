"""人工确认台账与待确认队列（``knowledge/borderline_review.py``）的测试。

## 这份测试守的是什么

台账是**人工裁决的唯一载体**：每条记录背后是一次人工判断，丢了无法重建
（重跑聚类只能得到机器的意见）。所以它的失败模式比一般配置文件更严苛：

1. **损坏必须报错，不能静默当空台账**——否则"人工改的规则突然不生效"，
   而日志里什么都没说，这种故障比直接崩掉难查得多；
2. **不存在的文件和损坏的文件必须区别对待**——前者是"还没配"，后者是事故；
3. **一条写坏不能连坐全份**——人手改 JSON 时一个笔误抹掉全部裁决，代价
   远大于漏一条；
4. **写入必须原子**——非原子写被 cron kill 会留下半截 JSON，而加载器对
   损坏文件是拒绝运行的，等于一次意外停掉整条聚类链路；
5. **队列必须按配对去重**——聚类幂等可重跑，不去重的话同一对每轮多一份，
   真正新出现的那几条反而看不见。
"""

from __future__ import annotations

import json

import pytest

from knowledge import borderline_review as br


# --------------------------------------------------------------- 键 --


class TestPairId:
    def test_roundtrip_and_order_insensitive(self):
        assert br.pair_id("b", "a") == br.pair_id("a", "b") == "a||b"
        assert br.parse_pair_id("a||b") == ("a", "b")

    def test_parse_normalizes_order(self):
        """反向键也要拆成有序对——否则台账匹配会漏一半。"""
        assert br.parse_pair_id("b||a") == ("a", "b")

    def test_parse_rejects_malformed(self):
        for bad in ("", "abc", "||b", "a||", "a:b"):
            with pytest.raises(ValueError):
                br.parse_pair_id(bad)

    def test_separator_does_not_collide_with_unit_key_colon(self):
        """``unit_key`` 形如 ``src:xxx``，用 ``||`` 才不会被键内冒号切错。"""
        key = br.pair_id("src:abc", "blogger:def")
        assert br.parse_pair_id(key) == ("blogger:def", "src:abc")


# ------------------------------------------------------------- 台账 --


class TestLoadOverrides:
    def test_missing_file_means_not_configured(self, tmp_path):
        """文件不存在 = 还没配，合法，返回空。"""
        assert br.load_overrides(tmp_path / "nope.json") == {}

    def test_empty_file_is_not_corruption(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("  \n", encoding="utf-8")
        assert br.load_overrides(path) == {}

    def test_corrupt_file_raises(self, tmp_path):
        """损坏必须抛异常：静默当空台账会让全部人工裁决凭空失效。"""
        path = tmp_path / "bad.json"
        path.write_text('{"a||b": {"decision": ', encoding="utf-8")
        with pytest.raises(br.OverrideError) as exc:
            br.load_overrides(path)
        assert "解析失败" in str(exc.value)

    def test_top_level_must_be_object(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text('["a||b"]', encoding="utf-8")
        with pytest.raises(br.OverrideError):
            br.load_overrides(path)

    def test_accepts_three_notations(self, tmp_path):
        """bool 简写、字符串、对象三种写法都要认——手写时怎么顺手怎么来。"""
        path = tmp_path / "mix.json"
        path.write_text(
            json.dumps(
                {
                    "a||b": True,
                    "c||d": "keep_separate",
                    "e||f": {"decision": "merge", "note": "同一倡议的后续"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        overrides = br.load_overrides(path)

        assert overrides[("a", "b")] is True
        assert overrides[("c", "d")] is False
        assert overrides[("e", "f")] is True

    def test_underscore_keys_are_comments(self, tmp_path):
        path = tmp_path / "meta.json"
        path.write_text(
            json.dumps({"_comment": "x", "_updated_at": "y", "a||b": True}),
            encoding="utf-8",
        )
        assert br.load_overrides(path) == {("a", "b"): True}

    def test_bad_entry_does_not_kill_others(self, tmp_path):
        """一条写坏只失效这一条：一次笔误不该抹掉全部人工成果。"""
        path = tmp_path / "partial.json"
        path.write_text(
            json.dumps(
                {
                    "a||b": True,
                    "bad-key": True,
                    "c||d": "???",
                    "e||f": False,
                }
            ),
            encoding="utf-8",
        )
        overrides = br.load_overrides(path)

        assert overrides == {("a", "b"): True, ("e", "f"): False}

    def test_ambiguous_value_is_rejected(self, tmp_path):
        """含糊值（``yes``/``1``）不许当 True 处理——台账含糊等于没写。"""
        path = tmp_path / "vague.json"
        path.write_text(json.dumps({"a||b": "yes"}), encoding="utf-8")
        assert br.load_overrides(path) == {}


class TestWriteOverrides:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "o.json"
        br.save_overrides({("a", "b"): True, ("c", "d"): False}, path)
        assert br.load_overrides(path) == {("a", "b"): True, ("c", "d"): False}

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        """写完不能残留 ``.tmp``：残留文件会被下一次写入覆盖，但人看到会困惑。"""
        path = tmp_path / "o.json"
        br.save_overrides({("a", "b"): True}, path)
        assert list(tmp_path.glob("*.tmp")) == []

    def test_record_override_preserves_other_entries_and_notes(self, tmp_path):
        """写一条不能把别人删掉——CLI 每次只关心一条。"""
        path = tmp_path / "o.json"
        br.record_override("a", "b", merge=True, note="甲的备注", path=path)
        br.record_override("c", "d", merge=False, path=path)

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["a||b"]["decision"] == "merge"
        assert payload["a||b"]["note"] == "甲的备注"
        assert payload["c||d"]["decision"] == "keep_separate"
        assert br.load_overrides(path) == {("a", "b"): True, ("c", "d"): False}

    def test_record_override_updates_in_place(self, tmp_path):
        """改判：同一对从 merge 改成 keep_separate，文件里只能有一条。"""
        path = tmp_path / "o.json"
        br.record_override("a", "b", merge=True, path=path)
        br.record_override("b", "a", merge=False, path=path)

        payload = json.loads(path.read_text(encoding="utf-8"))
        pairs = [k for k in payload if not k.startswith("_")]
        assert pairs == ["a||b"]
        assert payload["a||b"]["decision"] == "keep_separate"


# ------------------------------------------------------------- 队列 --


class TestPendingQueue:
    def test_missing_queue_is_empty(self, tmp_path):
        assert br.load_pending(tmp_path / "q.jsonl") == []

    def test_append_and_load(self, tmp_path):
        path = tmp_path / "q.jsonl"
        added = br.append_pending(
            [
                {"left": "a", "right": "b", "similarity": 0.7133},
                {"left": "c", "right": "d", "similarity": 0.72},
            ],
            path,
        )
        assert added == 2
        rows = br.load_pending(path)
        assert len(rows) == 2
        assert rows[0]["status"] == "pending"
        assert rows[0]["queued_at"]

    def test_dedupes_across_runs(self, tmp_path):
        """重跑一轮不能把同一对再写一遍：否则人手翻清单时被刷屏。"""
        path = tmp_path / "q.jsonl"
        item = {"left": "a", "right": "b"}
        assert br.append_pending([item], path) == 1
        assert br.append_pending([item], path) == 0
        # 反向顺序也算同一对
        assert br.append_pending([{"left": "b", "right": "a"}], path) == 0
        assert len(br.load_pending(path)) == 1

    def test_bad_line_is_skipped(self, tmp_path):
        """队列是只追加日志，一行写坏不该让整份清单读不出来。"""
        path = tmp_path / "q.jsonl"
        path.write_text(
            '{"left":"a","right":"b"}\nnot-json\n{"left":"c","right":"d"}\n',
            encoding="utf-8",
        )
        rows = br.load_pending(path)
        assert len(rows) == 2


# ------------------------------------------------------------- 路径 --


class TestPathAnchor:
    def test_relative_path_anchors_to_project_root(self):
        """相对路径必须锚到项目根：cron 从 ``/`` 启动与从项目目录启动若读到
        两份不同台账，表现就是"人工改的规则时灵时不灵"。"""
        resolved = br.resolve_path("data/x.json", br.DEFAULT_OVERRIDES_PATH)
        assert resolved.is_absolute()
        assert resolved.parent.parent == br.PROJECT_ROOT

    def test_absolute_path_is_kept(self, tmp_path):
        target = tmp_path / "a.json"
        assert br.resolve_path(target, br.DEFAULT_OVERRIDES_PATH) == target

    def test_none_takes_default(self):
        resolved = br.resolve_path(None, br.DEFAULT_QUEUE_PATH)
        assert resolved == br.PROJECT_ROOT / br.DEFAULT_QUEUE_PATH
