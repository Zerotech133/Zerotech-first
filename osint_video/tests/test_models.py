"""models.py 单元测试（第 2 层）：状态机 / 退避 / URL 规范化。

覆盖（任务 #4 要求）：
- 状态机：9 状态全集、合法流转通过、非法流转拒绝、终态无出边；
- 指数退避序列：5min/30min/2h（RETRY_BACKOFF_SECONDS 常量 + repository 侧
  _backoff_deadline 的索引计算一并验证）；
- URL 规范化：同链接不同跟踪参数 → 同一 hash（投递幂等去重的基础）；
- row_to_task 反序列化默认值。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from queue_link.models import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    ALL_ROUTES,
    ALL_STATUSES,
    DEFAULT_MAX_RETRY,
    RETRY_BACKOFF_SECONDS,
    LinkStatus,
    LinkTask,
    Route,
    compute_url_hash,
    isoformat_z,
    normalize_url,
    row_to_task,
)


def _task(status: str, **kw) -> LinkTask:
    """构造指定状态的 LinkTask。"""
    defaults = dict(sph_url="https://example.chanxuezhong.com/s/x", url_hash="h" * 40)
    defaults.update(kw)
    return LinkTask(status=status, **defaults)


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------
class TestStateMachine:
    def test_nine_statuses_defined(self):
        """架构 §2.1 共 9 个状态。"""
        assert ALL_STATUSES == {
            "pending",
            "parsed",
            "downloaded",
            "decrypted",
            "extracted",
            "done",
            "failed",
            "dead",
            "waiting_cookie",
        }
        assert len(ALL_STATUSES) == 9

    def test_main_happy_path_allowed(self):
        """主链路 pending→parsed→…→done 全部合法。"""
        chain = [
            LinkStatus.PENDING,
            LinkStatus.PARSED,
            LinkStatus.DOWNLOADED,
            LinkStatus.DECRYPTED,
            LinkStatus.EXTRACTED,
            LinkStatus.DONE,
        ]
        for cur, nxt in zip(chain, chain[1:]):
            assert _task(cur).can_transition_to(nxt), f"{cur} → {nxt} 应合法"

    def test_any_processing_to_failed_allowed(self):
        """任意处理态 → failed 合法（可重试）。"""
        for status in (
            LinkStatus.PENDING,
            LinkStatus.PARSED,
            LinkStatus.DOWNLOADED,
            LinkStatus.DECRYPTED,
            LinkStatus.EXTRACTED,
        ):
            assert _task(status).can_transition_to(LinkStatus.FAILED), status

    def test_failed_to_pending_and_dead(self):
        """failed → pending（退避到期）与 failed → dead（超限）合法。"""
        t = _task(LinkStatus.FAILED)
        assert t.can_transition_to(LinkStatus.PENDING)
        assert t.can_transition_to(LinkStatus.DEAD)

    def test_waiting_cookie_only_back_to_pending(self):
        """waiting_cookie 仅能回 pending（人工更新 Cookie 后）。"""
        t = _task(LinkStatus.WAITING_COOKIE)
        assert t.can_transition_to(LinkStatus.PENDING)
        assert not t.can_transition_to(LinkStatus.PARSED)
        assert not t.can_transition_to(LinkStatus.FAILED)
        # pending → waiting_cookie 合法（Cookie 过期挂起）
        assert _task(LinkStatus.PENDING).can_transition_to(LinkStatus.WAITING_COOKIE)

    @pytest.mark.parametrize(
        "bad_transition",
        [
            (LinkStatus.PENDING, LinkStatus.DONE),  # 跳过全流程
            (LinkStatus.PENDING, LinkStatus.DOWNLOADED),  # 跳过解析
            (LinkStatus.PARSED, LinkStatus.EXTRACTED),  # 跳过下载/解密
            (LinkStatus.DONE, LinkStatus.PENDING),  # 终态复活
            (LinkStatus.DEAD, LinkStatus.PENDING),  # 死信复活
            (LinkStatus.EXTRACTED, LinkStatus.PARSED),  # 回退
        ],
    )
    def test_illegal_transitions_rejected(self, bad_transition):
        """非法流转一律拒绝。"""
        cur, nxt = bad_transition
        assert not _task(cur).can_transition_to(nxt), f"{cur} → {nxt} 应被拒绝"

    def test_terminal_states_have_no_outgoing(self):
        """done/dead 终态无任何出边。"""
        assert ALLOWED_TRANSITIONS[LinkStatus.DONE] == frozenset()
        assert ALLOWED_TRANSITIONS[LinkStatus.DEAD] == frozenset()

    def test_is_terminal_property(self):
        assert _task(LinkStatus.DONE).is_terminal
        assert _task(LinkStatus.DEAD).is_terminal
        assert not _task(LinkStatus.FAILED).is_terminal
        assert not _task(LinkStatus.WAITING_COOKIE).is_terminal

    def test_unknown_status_has_no_transitions(self):
        """未知状态（脏数据）不匹配任何流转 → can_transition_to 恒 False。"""
        assert not _task("hacked").can_transition_to(LinkStatus.PENDING)


# ---------------------------------------------------------------------------
# 指数退避
# ---------------------------------------------------------------------------
class TestRetryBackoff:
    def test_backoff_sequence(self):
        """架构 §2.1：5min / 30min / 2h。"""
        assert RETRY_BACKOFF_SECONDS == (300, 1800, 7200)

    def test_default_max_retry(self):
        assert DEFAULT_MAX_RETRY == 3

    def test_backoff_deadline_indexing(self):
        """repository._backoff_deadline：第 N 次重试取表中第 N 项，越界封顶 2h。"""
        from queue_link.repository import _backoff_deadline

        before = datetime.now(timezone.utc)
        for retry, expect_seconds in [
            (1, 300),
            (2, 1800),
            (3, 7200),
            (4, 7200),
            (99, 7200),
        ]:
            deadline = _backoff_deadline(retry)
            delta: timedelta = deadline - before
            assert (
                timedelta(seconds=expect_seconds - 5)
                <= delta
                <= timedelta(seconds=expect_seconds + 5)
            ), f"retry={retry} 期望约 {expect_seconds}s，实际 {delta}"

    def test_backoff_deadline_invalid_input(self):
        """retry_count ≤ 0 兜底取第一档（min/max 夹逼）。"""
        from queue_link.repository import _backoff_deadline

        d = _backoff_deadline(0)
        assert (d - datetime.now(timezone.utc)).total_seconds() <= 305


# ---------------------------------------------------------------------------
# URL 规范化与哈希（幂等去重基础）
# ---------------------------------------------------------------------------
class TestUrlNormalization:
    def test_same_url_with_tracking_params_same_hash(self):
        """同链接不同跟踪参数 → 同一 SHA1（幂等去重核心）。"""
        base = "https://example.chanxuezhong.com/s/AbCdEf123"
        variants = [
            base,
            base + "?scene=1",
            base + "?scene=2&ch_id=xx",
            base + "?from=timeline",
            base + "?share_type=1&scene=9",
            "  " + base + "  ",
        ]
        hashes = {compute_url_hash(v) for v in variants}
        assert len(hashes) == 1, f"应归一为同一 hash，实际 {hashes}"

    def test_fragment_rd_variants_consistent(self):
        """#rd 尾巴：带/不带跟踪参数的 #rd 形态应彼此归一（当前实现的实际口径）。"""
        base = "https://example.chanxuezhong.com/s/AbCdEf123"
        rd_variants = [base + "#rd", base + "?scene=3#rd"]
        hashes = {compute_url_hash(v) for v in rd_variants}
        assert len(hashes) == 1, f"#rd 形态应彼此归一，实际 {hashes}"
        # 已知边界：#rd 与裸链接不同 hash（rstrip 不删 #rd 文本）——记录现状
        assert compute_url_hash(base + "#rd") != compute_url_hash(base)

    def test_different_urls_different_hash(self):
        a = compute_url_hash("https://example.chanxuezhong.com/s/AbCdEf123")
        b = compute_url_hash("https://example.chanxuezhong.com/s/XyZ987")
        assert a != b

    def test_hash_is_sha1_hex_40(self):
        h = compute_url_hash("https://example.chanxuezhong.com/s/x")
        assert len(h) == 40
        int(h, 16)  # 合法十六进制

    def test_normalize_strips_trailing_junk(self):
        assert normalize_url("https://e.com/s/x?") == "https://e.com/s/x"
        assert normalize_url("https://e.com/s/x#") == "https://e.com/s/x"
        assert normalize_url("https://e.com/s/x&") == "https://e.com/s/x"

    def test_non_tracking_params_preserved(self):
        """非跟踪参数（如 exporter）不删除——保守策略只删白名单参数。"""
        url = "https://e.com/s/x?exporter=abc"
        assert "exporter=abc" in normalize_url(url)
        assert compute_url_hash(url) != compute_url_hash("https://e.com/s/x")


# ---------------------------------------------------------------------------
# 其他工具与模型
# ---------------------------------------------------------------------------
class TestUtilities:
    def test_isoformat_z(self):
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert isoformat_z(dt) == "2025-06-01T12:00:00Z"
        assert isoformat_z(None) is None
        # naive datetime 视作 UTC
        naive = datetime(2025, 6, 1, 12, 0, 0)
        assert isoformat_z(naive) == "2025-06-01T12:00:00Z"

    def test_routes(self):
        assert ALL_ROUTES == {"D", "B", "AUTO"}
        assert Route.D == "D"

    def test_row_to_task_defaults(self):
        """最小行 dict → 默认值兜底（route=D / retry=0 / max_retry=3 / priority=5）。"""
        row = {"id": 1, "sph_url": "u", "url_hash": "h", "status": "pending"}
        task = row_to_task(row)
        assert task.route == "D"
        assert task.retry_count == 0
        assert task.max_retry == DEFAULT_MAX_RETRY
        assert task.priority == 5
        assert task.created_by == "feishu"
        assert task.parse_result is None
