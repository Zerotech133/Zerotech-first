"""待确认队列的生命周期测试（统计 / 告警 / 归档）。

## 这份测试守的是什么

v1.5 落地队列时只做了"写入"，没有"关闭"。后果不是报错，而是**数字说谎**：
裁决过的行仍留在队列里、``status`` 恒为 ``pending``，任何积压统计都会报出
一个只增不减的待办量；演练一次之后，运维就不会再信这个告警了——这比没有
告警更糟。

所以这里守四条契约：

1. **统计必须用台账切分**——"队列里有多少行"和"还有多少条等人看"是两回事；
2. **阈值必须分两个维度**——量（跟不上产出）与滞留（根本没在看）是两类故障，
   只判一个必然漏另一个；
3. **归档必须幂等且不丢证据**——已裁决行搬进 ``*.decided.jsonl``，LLM 当时
   的理由只能从这里找到（台账只存布尔）；
4. **归档后队列仍可追加**——写回时漏掉结尾换行，下一轮 append 会把两行粘成
   一行非法 JSON，整条记录静默消失。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from knowledge import borderline_review as br

NOW = datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)


def _row(left: str, right: str, *, days_ago: float | None = 0.0, **extra):
    payload = {
        "left": left,
        "right": right,
        "similarity": 0.71,
        "rationale": f"llm:progress | {left} vs {right}",
        **extra,
    }
    if days_ago is not None:
        stamp = NOW - timedelta(days=days_ago)
        payload["queued_at"] = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    payload.setdefault("status", "pending")
    return payload


def _write_queue(path, rows):
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------- 时间戳解析 --


class TestParseQueueTs:
    def test_z_suffix_is_utc(self):
        parsed = br.parse_queue_ts("2026-11-04T14:34:53Z")
        assert parsed == datetime(2026, 11, 4, 14, 34, 53, tzinfo=timezone.utc)

    def test_offset_is_respected(self):
        """带偏移的串必须换算到 UTC，不能当成"就是 UTC"直接用。"""
        parsed = br.parse_queue_ts("2026-11-04T22:34:53+08:00")
        assert parsed == datetime(2026, 11, 4, 14, 34, 53, tzinfo=timezone.utc)

    def test_naive_is_rejected(self):
        """无时区的串一律返回 None，**不猜本地时区**。

        猜的话，UTC+8 的机器算出的积压天数会凭空少 8 小时，14 天阈值永远
        差一点才触发——这种"阈值永不触发"最难查。
        """
        assert br.parse_queue_ts("2026-11-04T14:34:53") is None

    @pytest.mark.parametrize("bad", ["", None, "   ", "昨天", "2026-13-40T99:99:99Z", 123])
    def test_garbage_is_none(self, bad):
        assert br.parse_queue_ts(bad) is None


# ------------------------------------------------------------- 统计 --


class TestQueueStats:
    def test_missing_file_is_zero_not_error(self, tmp_path):
        stats = br.queue_stats(tmp_path / "nope.jsonl", now=NOW)
        assert stats == br.QueueStats()
        assert stats.as_dict()["oldest_days"] is None

    def test_all_pending(self, tmp_path):
        path = _write_queue(tmp_path / "q.jsonl", [_row("a", "b"), _row("c", "d")])
        stats = br.queue_stats(path, now=NOW)
        assert (stats.total, stats.pending, stats.decided) == (2, 2, 0)
        assert stats.oldest_days == pytest.approx(0.0, abs=1e-6)

    def test_overrides_split_pending_from_decided(self, tmp_path):
        """同一份队列，有台账时"待办"必须变小——这正是僵尸行被剔除的地方。"""
        path = _write_queue(
            tmp_path / "q.jsonl", [_row("a", "b"), _row("c", "d"), _row("e", "f")]
        )
        stats = br.queue_stats(path, overrides={("a", "b"): False}, now=NOW)
        assert (stats.total, stats.pending, stats.decided) == (3, 2, 1)

    def test_oldest_days_uses_oldest_pending(self, tmp_path):
        path = _write_queue(
            tmp_path / "q.jsonl",
            [_row("a", "b", days_ago=2), _row("c", "d", days_ago=30)],
        )
        stats = br.queue_stats(path, now=NOW)
        assert stats.oldest_days == pytest.approx(30.0, abs=1e-6)

    def test_decided_rows_do_not_raise_oldest_age(self, tmp_path):
        """已裁决的老记录不能把滞留天数顶上去，否则一归档就"报警消失"，
        反而掩盖了新的积压。"""
        path = _write_queue(
            tmp_path / "q.jsonl",
            [_row("a", "b", days_ago=90), _row("c", "d", days_ago=1)],
        )
        stats = br.queue_stats(path, overrides={("a", "b"): True}, now=NOW)
        assert stats.pending == 1
        assert stats.oldest_days == pytest.approx(1.0, abs=1e-6)

    def test_duplicate_rows_counted_once(self, tmp_path):
        """历史队列可能带重复（v1.5 之前不去重），重复计入会让积压虚高。"""
        path = _write_queue(
            tmp_path / "q.jsonl", [_row("a", "b"), _row("b", "a"), _row("c", "d")]
        )
        stats = br.queue_stats(path, now=NOW)
        assert stats.total == 2 and stats.pending == 2

    def test_missing_timestamp_flagged_not_silently_zero(self, tmp_path):
        path = _write_queue(
            tmp_path / "q.jsonl",
            [_row("a", "b", days_ago=None), _row("c", "d", days_ago=3)],
        )
        stats = br.queue_stats(path, now=NOW)
        assert stats.pending == 2
        assert stats.unknown_age == 1
        assert stats.oldest_days == pytest.approx(3.0, abs=1e-6)

    def test_future_timestamp_flagged(self, tmp_path):
        """时钟回拨会算出负数天数；静默丢掉等于把异常藏起来。"""
        path = _write_queue(tmp_path / "q.jsonl", [_row("a", "b", days_ago=-2)])
        stats = br.queue_stats(path, now=NOW)
        assert stats.unknown_age == 1
        assert stats.oldest_days is None


# ------------------------------------------------------------- 告警 --


class TestBacklogAlert:
    def _stats(self, pending=0, oldest=None, unknown=0):
        return br.QueueStats(
            total=pending, pending=pending, decided=0,
            oldest_days=oldest, unknown_age=unknown,
        )

    def test_quiet_below_thresholds(self):
        assert br.backlog_alert(
            self._stats(pending=3, oldest=1.0), max_pending=20, max_age_days=14
        ) is None

    def test_count_threshold(self):
        msg = br.backlog_alert(
            self._stats(pending=20, oldest=1.0), max_pending=20, max_age_days=14
        )
        assert msg is not None and "待确认 20 条" in msg

    def test_age_threshold(self):
        msg = br.backlog_alert(
            self._stats(pending=1, oldest=14.0), max_pending=20, max_age_days=14
        )
        assert msg is not None and "已压" in msg

    def test_both_dimensions_reported_together(self):
        """两个维度同时越界时必须都报：只说一个会让人修完一个就以为好了。"""
        msg = br.backlog_alert(
            self._stats(pending=50, oldest=30.0), max_pending=20, max_age_days=14
        )
        assert msg is not None and "待确认 50 条" in msg and "已压" in msg

    def test_unknown_age_always_reported(self):
        msg = br.backlog_alert(
            self._stats(pending=1, oldest=None, unknown=2), max_pending=20, max_age_days=14
        )
        assert msg is not None and "不可解析" in msg

    def test_zero_disables_dimension(self):
        """0 = 关闭该维度。若把 0 当成"阈值为 0，永远超限"，关不掉反而刷屏。"""
        assert br.backlog_alert(
            self._stats(pending=999, oldest=999.0), max_pending=0, max_age_days=0
        ) is None

    def test_no_pending_no_alert(self):
        assert br.backlog_alert(
            br.QueueStats(), max_pending=1, max_age_days=1
        ) is None


# ----------------------------------------------- append 的僵尸防护 --


class TestAppendSkipsClosed:
    def test_closed_pair_not_requeued(self, tmp_path):
        """已裁决的配对不再入队——否则队列里长出一条永远没人处理的僵尸。"""
        path = tmp_path / "q.jsonl"
        added = br.append_pending(
            [{"left": "a", "right": "b"}, {"left": "c", "right": "d"}],
            path,
            overrides={("a", "b"): False},
        )
        assert added == 1
        keys = {br.pair_id(r["left"], r["right"]) for r in br.load_pending(path)}
        assert keys == {"c||d"}

    def test_without_overrides_still_appends(self, tmp_path):
        path = tmp_path / "q.jsonl"
        assert br.append_pending([{"left": "a", "right": "b"}], path) == 1

    def test_lost_overrides_requeues(self, tmp_path):
        """台账丢了（空台账）时该对必须重新入队。

        这是"跳过已裁决"最容易做错的地方：若按"曾经裁决过"判断，台账一丢
        人就成了瞎子——机器再次提升了一个已被否决的配对，而队列里没有它。
        """
        path = _write_queue(tmp_path / "q.jsonl", [_row("a", "b")])
        # 队列里已有该对，先清空模拟"队列被归档但台账也丢了"的极端情况
        path.write_text("", encoding="utf-8")
        assert br.append_pending([{"left": "a", "right": "b"}], path, overrides={}) == 1


# ------------------------------------------------------------- 归档 --


class TestPruneDecided:
    def test_moves_decided_and_keeps_pending(self, tmp_path):
        path = _write_queue(
            tmp_path / "q.jsonl", [_row("a", "b"), _row("c", "d"), _row("e", "f")]
        )
        moved, kept = br.prune_decided(path, overrides={("a", "b"): True, ("c", "d"): False})
        assert (moved, kept) == (2, 1)
        assert [br.pair_id(r["left"], r["right"]) for r in br.load_pending(path)] == ["e||f"]

    def test_archive_preserves_rationale(self, tmp_path):
        """归档不是删除：台账只有布尔，LLM 当时的理由只在这里。"""
        path = _write_queue(tmp_path / "q.jsonl", [_row("a", "b")])
        br.prune_decided(path, overrides={("a", "b"): False})

        archive = path.with_name(path.name + br.ARCHIVE_SUFFIX)
        rows = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines()]
        assert rows[0]["rationale"] == "llm:progress | a vs b"
        assert rows[0]["left"] == "a"

    def test_idempotent(self, tmp_path):
        path = _write_queue(tmp_path / "q.jsonl", [_row("a", "b")])
        assert br.prune_decided(path, overrides={("a", "b"): True}) == (1, 0)
        assert br.prune_decided(path, overrides={("a", "b"): True}) == (0, 0)

    def test_empty_and_missing_file(self, tmp_path):
        missing = tmp_path / "nope.jsonl"
        assert br.prune_decided(missing, overrides={}) == (0, 0)
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        assert br.prune_decided(empty, overrides={}) == (0, 0)

    def test_queue_is_appendable_after_prune(self, tmp_path):
        """归档后必须还能追加。

        写回时漏掉结尾换行，下一轮 ``append_pending`` 会把新记录粘到最后一行
        尾部，两行并成一行非法 JSON，整条记录静默消失——这类 bug 只表现为
        "新配对怎么也不进队列"，查起来极慢。
        """
        path = _write_queue(tmp_path / "q.jsonl", [_row("a", "b"), _row("c", "d")])
        br.prune_decided(path, overrides={("a", "b"): True})
        assert path.read_text(encoding="utf-8").endswith("\n")

        br.append_pending([{"left": "e", "right": "f"}], path)
        rows = br.load_pending(path)
        assert len(rows) == 2
        assert {br.pair_id(r["left"], r["right"]) for r in rows} == {"c||d", "e||f"}

    def test_prune_all_decided_leaves_empty_file(self, tmp_path):
        path = _write_queue(tmp_path / "q.jsonl", [_row("a", "b")])
        assert br.prune_decided(path, overrides={("a", "b"): True}) == (1, 0)
        assert path.read_text(encoding="utf-8") == ""
        assert br.load_pending(path) == []


# --------------------------------------------------------- 原子写重试 --


class TestAtomicWriteRetry:
    """``os.replace`` 在 Windows 上会被瞬时文件锁顶掉，重试是唯一可行的处理。

    2026-11-05 真机实测：``cp`` 复制队列后立即 ``compact``，首次报
    ``WinError 5 拒绝访问``，原样重跑即成功。这类失败没有日志特征，只能靠
    原语内重试消化；但**必须仍然会抛**，否则真权限问题会被伪装成慢。
    """

    def test_transient_permission_error_is_retried(self, tmp_path, monkeypatch):
        from pathlib import Path

        from common import atomic_io

        monkeypatch.setattr(atomic_io.time, "sleep", lambda *_: None)
        real_replace = Path.replace
        attempts = {"n": 0}

        def flaky(self, target):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise PermissionError(13, "拒绝访问")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", flaky)
        path = tmp_path / "o.json"
        br.save_overrides({("a", "b"): True}, path)

        assert attempts["n"] == 3
        assert br.load_overrides(path) == {("a", "b"): True}
        assert list(tmp_path.glob("*.tmp")) == []

    def test_permanent_permission_error_still_raises(self, tmp_path, monkeypatch):
        from pathlib import Path

        from common import atomic_io

        monkeypatch.setattr(atomic_io.time, "sleep", lambda *_: None)
        monkeypatch.setattr(
            Path, "replace", lambda self, target: (_ for _ in ()).throw(PermissionError(13, "x"))
        )
        path = tmp_path / "o.json"
        with pytest.raises(PermissionError):
            br.save_overrides({("a", "b"): True}, path)
        # 临时文件保留：里面是这次要写的内容，删了连事后取证都没了
        assert (tmp_path / "o.json.tmp").exists()


# --------------------------------------------------------------- CLI --

def _run_cli(monkeypatch, argv):
    """按真实命令行调用，验证"人会不会用错"这一层。

    模块级函数再全也不能替代 CLI：``compact`` 能不能找到队列/台账、输出
    有没有告诉人归档去了哪里，都只在命令行这一层暴露。
    """
    import sys

    from scripts import borderline_review as cli

    monkeypatch.setattr(sys, "argv", ["borderline_review.py", *argv])
    return cli.main()


class TestCompactCli:
    def test_compact_archives_and_reports(self, tmp_path, monkeypatch, capsys):
        queue = _write_queue(
            tmp_path / "q.jsonl", [_row("a", "b"), _row("c", "d")]
        )
        overrides = tmp_path / "o.json"
        overrides.write_text(
            json.dumps({"a||b": {"decision": "keep_separate"}}), encoding="utf-8"
        )

        code = _run_cli(
            monkeypatch,
            ["--queue", str(queue), "--overrides", str(overrides), "compact"],
        )
        out = capsys.readouterr().out

        assert code == 0
        assert "归档 1 条" in out
        assert "queue 保留 1 条" in out or "保留 1 条" in out
        assert [br.pair_id(r["left"], r["right"]) for r in br.load_pending(queue)] == ["c||d"]

    def test_compact_is_idempotent_in_output(self, tmp_path, monkeypatch, capsys):
        queue = _write_queue(tmp_path / "q.jsonl", [_row("a", "b")])
        overrides = tmp_path / "o.json"
        overrides.write_text(
            json.dumps({"a||b": {"decision": "merge"}}), encoding="utf-8"
        )
        _run_cli(monkeypatch, ["--queue", str(queue), "--overrides", str(overrides), "compact"])
        capsys.readouterr()
        _run_cli(monkeypatch, ["--queue", str(queue), "--overrides", str(overrides), "compact"])
        out = capsys.readouterr().out
        assert "归档 0 条" in out

    def test_list_pending_only_hides_decided(self, tmp_path, monkeypatch, capsys):
        queue = _write_queue(tmp_path / "q.jsonl", [_row("aaa", "bbb"), _row("ccc", "ddd")])
        overrides = tmp_path / "o.json"
        overrides.write_text(
            json.dumps({"aaa||bbb": {"decision": "merge"}}), encoding="utf-8"
        )
        _run_cli(
            monkeypatch,
            ["--queue", str(queue), "--overrides", str(overrides), "list", "--pending-only"],
        )
        out = capsys.readouterr().out
        assert "ccc" in out and "aaa" not in out
