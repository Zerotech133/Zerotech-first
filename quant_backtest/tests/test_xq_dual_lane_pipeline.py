# -*- coding: utf-8 -*-
"""WO-QT-20261020-076 双 lane 离线流水线的特性契约测试。

全部输入均为合成页 fixture（内存对象），不触网、不依赖任何采集器、
不写生产数据库。覆盖五条特性：
1. 追新优先/回溯双 lane 配额调度且互不阻塞；
2. 页级断档失败关闭（禁止静默跳过继续）；
3. ID 去重保留多股（多 cashtag）关系；
4. fetched_at/available_at 双时点证据，缺任一不得进入"新鲜"判定；
5. 单写者有界缓冲，满则丢弃计数暴露，不静默丢。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import xq_dual_lane_pipeline as pipeline


NOW = 1_700_000_100_000
WINDOW = 100_000


def entry(post_id, cashtags, created_at, fetched=None, available=None):
    """构造一条合成条目；fetched/available 为双时点证据（毫秒）。"""
    return pipeline.Entry(
        post_id=post_id,
        created_at_ms=created_at,
        cashtags=tuple(cashtags),
        fetched_at_ms=fetched,
        available_at_ms=available,
        payload={"text": "post %s" % post_id},
    )


def page(lane, segment_id, page_no, entries, source_key="SH600000"):
    """构造一张合成页。"""
    return pipeline.SyntheticPage(
        source_key=source_key,
        lane=lane,
        segment_id=segment_id,
        page_no=page_no,
        entries=tuple(entries),
    )


# ---------------------------------------------------------------------------
# 特性 1：追新优先 / 回溯双 lane，配额槽位，互不阻塞
# ---------------------------------------------------------------------------

def test_new_lane_priority_and_quota_scheduling():
    """配额 new=2:history=1 时按轮次先追新后回溯，处理顺序可证。"""
    p = pipeline.DualLanePipeline(lane_slots={"new": 2, "history": 1})
    p.submit_page(page("new", "n-batch", 1, [entry("n1", ("A",), 100, 5, 6)]))
    p.submit_page(page("new", "n-batch", 2, [entry("n2", ("A",), 200, 5, 6)]))
    p.submit_page(page("history", "h-batch", 1, [entry("h1", ("B",), 300, 5, 6)]))
    p.submit_page(page("history", "h-batch", 2, [entry("h2", ("B",), 400, 5, 6)]))
    summary = p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    assert [rec.post_id for rec in summary.processed_order] == ["n1", "n2", "h1", "h2"]
    assert summary.pages_done == 4
    assert summary.pages_failed_closed == 0


def test_empty_new_lane_does_not_block_history_lane():
    """追新 lane 为空时回溯 lane 独立推进，互不阻塞。"""
    p = pipeline.DualLanePipeline(lane_slots={"new": 2, "history": 1})
    for no in (1, 2, 3):
        outcome = p.submit_page(
            page("history", "h", no, [entry("h%d" % no, ("B",), no * 10, 5, 6)]))
        assert outcome.outcome == "queued"
    summary = p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    assert [rec.post_id for rec in summary.processed_order] == ["h1", "h2", "h3"]
    assert summary.pages_done == 3


# ---------------------------------------------------------------------------
# 特性 2：页级断档失败关闭
# ---------------------------------------------------------------------------

def test_page_gap_fail_closes_whole_segment_and_rejects_continuation():
    """页码跳号 -> 整段任务置 failed_closed，后续提交被拒绝，无条目被处理。"""
    p = pipeline.DualLanePipeline(lane_slots={"new": 1, "history": 2})
    assert p.submit_page(page("history", "h", 1,
                              [entry("a1", ("B",), 10, 5, 6)])).outcome == "queued"
    assert p.submit_page(page("history", "h", 2,
                              [entry("a2", ("B",), 20, 5, 6)])).outcome == "queued"
    # 跳过第 3 页直接提交第 4 页 -> 断档 -> 该段失败关闭
    gap = p.submit_page(page("history", "h", 4, [entry("late", ("B",), 40, 5, 6)]))
    assert gap.outcome == "failed_closed"
    assert "3" in gap.detail
    # 段内全部待处理任务置 failed_closed，绝不静默跳过继续
    tasks = p.page_tasks()
    assert len(tasks) == 2
    assert all(t.state == "failed_closed" for t in tasks)
    summary = p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    assert summary.processed_order == ()
    assert summary.pages_failed_closed == 2
    assert summary.segments_failed_closed == (("history", "SH600000", "h"),)
    # 断档后同段补交缺失页也被拒绝（段已封闭）
    assert p.submit_page(page("history", "h", 3, [entry("a3", ("B",), 30, 5, 6)])
                         ).outcome == "segment_closed"


def test_segment_close_is_scoped_to_its_own_segment():
    """失败关闭只作用于断档段本身，其他段不受牵连。"""
    p = pipeline.DualLanePipeline(lane_slots={"new": 1, "history": 2})
    p.submit_page(page("history", "h-bad", 1, [entry("b1", ("B",), 10, 5, 6)]))
    p.submit_page(page("history", "h-bad", 3, [entry("b3", ("B",), 30, 5, 6)]))
    p.submit_page(page("history", "h-good", 1, [entry("g1", ("B",), 10, 5, 6)]))
    p.submit_page(page("new", "n-good", 1, [entry("g2", ("A",), 10, 5, 6)]))
    summary = p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    states = {(t.page.segment_id, t.state) for t in p.page_tasks()}
    assert ("h-bad", "failed_closed") in states
    assert ("h-good", "done") in states and ("n-good", "done") in states
    assert [rec.post_id for rec in summary.processed_order] == ["g2", "g1"]


# ---------------------------------------------------------------------------
# 特性 3：ID 去重保留多股关系
# ---------------------------------------------------------------------------

def test_dedup_processes_once_and_preserves_all_cashtag_relations():
    """同一 post_id 重复出现只处理一次，但多 cashtag 关系并集全量保留。"""
    p = pipeline.DualLanePipeline(lane_slots={"new": 2, "history": 2})
    p.submit_page(page("new", "a", 1,
                       [entry("X", ("SH600000", "SZ000001"), 100, 5, 6)]))
    p.submit_page(page("new", "b", 1,
                       [entry("X", ("SH600000", "SZ300750"), 100, 5, 6)]))
    p.submit_page(page("new", "c", 1, [entry("Y", ("SH600519",), 200, 5, 6)]))
    summary = p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    assert summary.dedup_hits == 1
    # 只处理一次：输出侧 X 只出现一次
    assert [rec.post_id for rec in summary.processed_order] == ["X", "Y"]
    # 去重不丢关系：三条多股关系全部保留
    record = p.entry_record("X")
    assert record.cashtags == {"SH600000", "SZ000001", "SZ300750"}
    assert record.seen_count == 2
    assert p.entry_record("Y").cashtags == {"SH600519"}
    assert len(summary.relation_updates) == 1
    assert summary.relation_updates[0].post_id == "X"
    assert summary.relation_updates[0].added_cashtags == ("SZ300750",)
    delivered = []
    with p.buffer.single_writer():
        p.buffer.drain(delivered.append)
    assert [type(item) for item in delivered] == [
        pipeline.OutputRecord, pipeline.RelationUpdate, pipeline.OutputRecord]


def test_late_relation_is_delivered_after_first_output_was_drained():
    p = pipeline.DualLanePipeline()
    p.submit_page(page("new", "same", 1, [entry("X", ("A",), 100, 5, 6)]))
    first = p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    delivered = []
    with p.buffer.single_writer():
        p.buffer.drain(delivered.append)
    assert [item.post_id for item in delivered] == ["X"]
    assert first.relation_updates == ()

    p.submit_page(page("new", "same", 2, [entry("X", ("A", "B"), 100, 5, 6)]))
    second = p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    with p.buffer.single_writer():
        p.buffer.drain(delivered.append)
    assert second.processed_order == ()
    assert [(item.post_id, item.added_cashtags) for item in second.relation_updates] == [
        ("X", ("B",))]
    assert [type(item) for item in delivered] == [
        pipeline.OutputRecord, pipeline.RelationUpdate]
    assert p.entry_record("X").cashtags == {"A", "B"}


def test_full_buffer_counts_undelivered_relation_update():
    buf = pipeline.BoundedOutputBuffer(maxsize=1, overflow_policy="drop_count")
    p = pipeline.DualLanePipeline(output_buffer=buf)
    p.submit_page(page("new", "same", 1, [entry("X", ("A",), 100, 5, 6)]))
    p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    p.submit_page(page("new", "same", 2, [entry("X", ("B",), 100, 5, 6)]))
    second = p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    assert second.relation_updates == ()
    assert second.buffer_dropped == 1
    assert buf.stats()["dropped"] == 1
    assert p.entry_record("X").cashtags == {"A", "B"}
    drained = []
    with buf.single_writer():
        buf.drain(drained.append)
    retry = p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    with buf.single_writer():
        buf.drain(drained.append)
    assert retry.relation_updates == (pipeline.RelationUpdate("X", ("B",)),)
    assert [type(item) for item in drained] == [
        pipeline.OutputRecord, pipeline.RelationUpdate]


def test_dropped_original_is_retried_before_any_relation_update():
    buf = pipeline.BoundedOutputBuffer(maxsize=1, overflow_policy="drop_count")
    assert buf.offer("occupied")
    p = pipeline.DualLanePipeline(output_buffer=buf)
    p.submit_page(page("new", "same", 1, [entry("X", ("A",), 100, 5, 6)]))
    first = p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    assert first.buffer_dropped == 1
    p.submit_page(page("new", "same", 2, [entry("X", ("B",), 100, 5, 6)]))
    p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    drained = []
    with buf.single_writer():
        buf.drain(drained.append)
    assert drained == ["occupied"]
    p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    with buf.single_writer():
        buf.drain(drained.append)
    assert [type(item) for item in drained] == [str, pipeline.OutputRecord]
    assert drained[1].cashtags == ("A", "B")


# ---------------------------------------------------------------------------
# 特性 4：fetched_at / available_at 双时点证据
# ---------------------------------------------------------------------------

def test_missing_evidence_never_fresh_and_stale_is_detected():
    """缺任一证据 -> unverified 不得进入新鲜判定；双证齐但超窗 -> stale。"""
    p = pipeline.DualLanePipeline(lane_slots={"new": 1, "history": 1})
    entries = [
        entry("ok", ("A",), 100, fetched=NOW - 1_000, available=NOW - 2_000),
        entry("no_fetch", ("A",), 200, fetched=None, available=NOW - 1_000),
        entry("no_avail", ("A",), 300, fetched=NOW - 1_000, available=None),
        entry("stale", ("A",), 400,
              fetched=NOW - WINDOW * 10, available=NOW - WINDOW * 10),
    ]
    p.submit_page(page("new", "ev", 1, entries))
    summary = p.run_all(judged_at_ms=NOW, freshness_window_ms=WINDOW)
    assert list(summary.fresh_ids) == ["ok"]
    assert p.entry_record("no_fetch").freshness == "unverified"
    assert p.entry_record("no_avail").freshness == "unverified"
    assert p.entry_record("stale").freshness == "stale"
    assert p.entry_record("ok").freshness == "fresh"


def test_entry_input_validation_rejects_bad_evidence_types():
    """证据字段只接受非负整数或 None，非法类型在入口即拒绝。"""
    with pytest.raises(ValueError):
        entry("bad", ("A",), 100, fetched="now", available=NOW)
    with pytest.raises(ValueError):
        entry("bad", ("A",), -1, fetched=NOW, available=NOW)
    with pytest.raises(ValueError):
        entry("", ("A",), 100, fetched=NOW, available=NOW)


# ---------------------------------------------------------------------------
# 特性 5：单写者有界缓冲
# ---------------------------------------------------------------------------

def test_bounded_output_drop_count_exposed_and_single_writer_exclusive():
    buf = pipeline.BoundedOutputBuffer(maxsize=2, overflow_policy="drop_count")
    assert buf.offer({"post_id": "a"}) is True
    assert buf.offer({"post_id": "b"}) is True
    assert buf.offer({"post_id": "c"}) is False  # 满：丢弃但计数暴露
    stats = buf.stats()
    assert stats["published"] == 2
    assert stats["dropped"] == 1
    assert stats["capacity"] == 2
    assert stats["size"] == 2
    drained = []
    with buf.single_writer():
        buf.drain(drained.append)
        # 单写者互斥：写者会话内第二个写者被拒绝
        with pytest.raises(RuntimeError):
            with buf.single_writer():
                pass
    assert [r["post_id"] for r in drained] == ["a", "b"]
    assert buf.stats()["size"] == 0
    # 非 write 会话内 drain 被拒绝
    with pytest.raises(RuntimeError):
        buf.drain(drained.append)


def test_failed_writer_callback_retains_item_and_can_inspect_stats():
    buf = pipeline.BoundedOutputBuffer(maxsize=1)
    buf.offer("keep")

    def fail(item):
        assert buf.stats()["size"] == 1
        raise ValueError("write failed")

    with buf.single_writer():
        with pytest.raises(ValueError, match="write failed"):
            buf.drain(fail)
        received = []
        buf.drain(received.append)
    assert received == ["keep"]
    assert buf.stats()["size"] == 0


def test_writer_session_cannot_be_borrowed_by_another_thread():
    import threading
    buf = pipeline.BoundedOutputBuffer(maxsize=1)
    buf.offer("keep")
    errors = []
    with buf.single_writer():
        thread = threading.Thread(target=lambda: _capture_drain_error(buf, errors))
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert errors == ["RuntimeError"]
        assert buf.stats()["size"] == 1


def _capture_drain_error(buf, errors):
    try:
        buf.drain(lambda item: None)
    except RuntimeError as exc:
        errors.append(type(exc).__name__)


def test_buffer_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        pipeline.BoundedOutputBuffer(maxsize=0)
    with pytest.raises(ValueError):
        pipeline.BoundedOutputBuffer(maxsize=4, overflow_policy="silently_drop")


def test_pipeline_rejects_unknown_lane_and_bad_quota():
    with pytest.raises(ValueError):
        pipeline.SyntheticPage(source_key="S", lane="hot", segment_id="s",
                               page_no=1, entries=())
    with pytest.raises(ValueError):
        pipeline.DualLanePipeline(lane_slots={"new": 1})
    with pytest.raises(ValueError):
        pipeline.DualLanePipeline(lane_slots={"new": 0, "history": 1})
