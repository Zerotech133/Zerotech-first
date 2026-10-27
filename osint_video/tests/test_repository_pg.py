"""PG 集成测试（第 2.5 层，超任务范围的加分验证）：repository 真实 SQL 路径。

本机环境发现：Docker 容器 zhice-db（postgres:15，宿主端口 5446）可用，
QA 已建独立库 osint_qa_test 并跑通 scripts/init_db.py（12 语句 / 5 表）。
故 repository 层（原计划跳过）做真实 PG 验证：

- submit_links：入队 + url_hash 幂等去重（同链接不同跟踪参数只入一条）；
- claim_next：认领即 pending→parsed 原子流转；
- mark_failed：常规失败计次+退避排期 / COOKIE_EXPIRED 不计次挂起 /
  超限死信 / AUTO 路由超限转 B；
- promote_due_failures / resume_waiting_cookie：批量恢复；
- transition：非法流转拒绝返回 None；
- get_stats / list_dead / save_credential。

前置：环境变量 PG_HOST=127.0.0.1 PG_PORT=5446 PG_USER=zhice
PG_PASSWORD=zhice_pass PG_DB=osint_qa_test（conftest 或外部注入）。
PG 不可达时整文件跳过（不失败）。
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _pg_ready() -> bool:
    """目标 PG 是否可达（独立短连接探测，不动全局单例）。"""
    try:
        import psycopg
    except ImportError:
        return False
    dsn = (
        f"host={os.environ.get('PG_HOST', '127.0.0.1')} "
        f"port={os.environ.get('PG_PORT', '5446')} "
        f"dbname={os.environ.get('PG_DB', 'osint_qa_test')} "
        f"user={os.environ.get('PG_USER', 'zhice')} "
        f"password={os.environ.get('PG_PASSWORD', '')} connect_timeout=3"
    )
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM osint_link_task LIMIT 1")
                cur.fetchone()
        return True
    except Exception:
        return False


pg_required = pytest.mark.skipif(
    not _pg_ready(), reason="QA PG(osint_qa_test@5446) 不可达，跳过集成测试"
)

pytestmark = pg_required


@pytest.fixture(autouse=True)
def _fresh_table():
    """每个测试清空 osint_link_task / osint_credential，互不污染。"""
    from common import db

    db.execute("DELETE FROM osint_link_task")
    db.execute("DELETE FROM osint_credential")
    db.reset_engine()  # 让 pool_pre_ping 重新建连
    yield
    db.reset_engine()


def _urls(*, tag: str | None = None) -> list[str]:
    tag = tag or uuid.uuid4().hex[:10]
    return [f"https://example.chanxuezhong.com/s/{tag}"]


# ---------------------------------------------------------------------------
# 入队 / 幂等
# ---------------------------------------------------------------------------
class TestSubmitLinks:
    def test_submit_and_idempotent_resubmit(self):
        from queue_link import repository as repo

        base = f"https://example.chanxuezhong.com/s/{uuid.uuid4().hex[:10]}"
        r1 = repo.submit_links([base + "?scene=1"])
        assert r1["accepted"] == 1 and r1["duplicated"] == 0
        # 同链接不同跟踪参数 → url_hash 命中 → duplicated
        r2 = repo.submit_links([base + "?scene=2&ch_id=x"], created_by="api")
        assert r2["accepted"] == 0 and r2["duplicated"] == 1
        # 返回的 tasks 是既有任务
        assert r2["tasks"][0].id == r1["tasks"][0].id

    def test_submit_batch_mixed(self):
        from queue_link import repository as repo

        tag = uuid.uuid4().hex[:8]
        u1 = f"https://example.chanxuezhong.com/s/{tag}a"
        u2 = f"https://example.chanxuezhong.com/s/{tag}b"
        repo.submit_links([u1])
        r = repo.submit_links([u1, u2, "  "])  # 空串跳过
        assert r["accepted"] == 1 and r["duplicated"] == 1 and len(r["tasks"]) == 2

    def test_get_by_hash_and_id(self):
        from queue_link import repository as repo
        from queue_link.models import compute_url_hash

        url = _urls()[0]
        repo.submit_links([url])
        by_hash = repo.get_by_hash(compute_url_hash(url))
        assert by_hash is not None and by_hash.sph_url == url
        by_id = repo.get_by_id(by_hash.id)
        assert by_id is not None and by_id.id == by_hash.id
        assert repo.get_by_hash("not-exist-hash") is None
        assert repo.get_by_id(999999) is None


class TestClaimAndTransition:
    def test_claim_next_pending_to_parsed(self):
        from queue_link import repository as repo
        from queue_link.models import LinkStatus

        url = _urls()[0]
        repo.submit_links([url], priority=1)
        task = repo.claim_next()
        assert task is not None
        assert task.status == LinkStatus.PARSED
        # 再认领：队列空（parsed 不再是 pending）
        assert repo.claim_next() is None

    def test_claim_respects_backoff_window(self):
        from queue_link import repository as repo
        from common import db

        repo.submit_links(_urls())
        created = repo.list_tasks()["items"][0]
        # 置为 failed 且退避时间在未来 → 不应被认领
        from common import db

        db.execute(
            """
            UPDATE osint_link_task
            SET status='failed', next_retry_at = now() + interval '1 hour'
            WHERE id = :id
            """,
            {"id": created.id},
        )
        assert repo.claim_next() is None

    def test_transition_legal_and_illegal(self):
        from queue_link import repository as repo

        url = _urls()[0]
        repo.submit_links([url])
        task = repo.claim_next()  # → parsed
        ok = repo.transition(task.id, "downloaded", file_md5="a" * 32, raw_path="/x/y.mp4")
        assert ok is not None and ok.status == "downloaded"
        assert ok.file_md5 == "a" * 32
        # 非法：downloaded → parsed（回退）
        assert repo.transition(task.id, "parsed") is None
        # 非法：不存在的任务
        assert repo.transition(999999, "pending") is None


class TestMarkFailed:
    def _task_id(self) -> int:
        from queue_link import repository as repo

        repo.submit_links(_urls())
        t = repo.list_tasks()["items"][0]
        return t.id

    def test_normal_failure_counts_and_backoff(self):
        from queue_link import repository as repo

        tid = self._task_id()
        t = repo.mark_failed(tid, "PARSE_FAIL", "解析超时")
        assert t.status == "failed"
        assert t.retry_count == 1
        assert t.next_retry_at is not None
        # 第 1 次退避 ≈ now+5min（允许 ±10s 误差）
        from queue_link.models import utc_now

        delta: timedelta = t.next_retry_at - utc_now()
        assert timedelta(seconds=290) <= delta <= timedelta(seconds=310)

    def test_cookie_expired_no_count_suspends(self):
        from queue_link import repository as repo

        tid = self._task_id()
        t = repo.mark_failed(tid, "COOKIE_EXPIRED", "401")
        assert t.status == "waiting_cookie"
        assert t.retry_count == 0  # 不计次
        assert t.next_retry_at is None

    def test_exceed_max_retry_to_dead(self):
        from queue_link import repository as repo

        tid = self._task_id()
        for _ in range(3):
            t = repo.mark_failed(tid, "PARSE_FAIL", "x")
            assert t.status == "failed"
            # 手动推回 pending 模拟退避到期
            from common import db

            db.execute("UPDATE osint_link_task SET status='pending' WHERE id=:i", {"i": tid})
        t = repo.mark_failed(tid, "PARSE_FAIL", "第 4 次")
        assert t.status == "dead"
        assert t.retry_count == 4

    def test_auto_route_over_limit_switches_to_b(self):
        from queue_link import repository as repo
        from common import db

        url = _urls()[0]
        repo.submit_links([url], route="AUTO")
        tid = repo.list_tasks()["items"][0].id
        for _ in range(4):
            repo.mark_failed(tid, "PARSE_FAIL", "x")
            db.execute(
                "UPDATE osint_link_task SET status='pending', next_retry_at=NULL WHERE id=:i",
                {"i": tid},
            )
        t = repo.get_by_id(tid)
        assert t.status == "pending" and t.route == "B" and t.retry_count == 0


class TestRecovery:
    def test_promote_due_failures(self):
        from queue_link import repository as repo
        from common import db

        url = _urls()[0]
        repo.submit_links([url])
        tid = repo.list_tasks()["items"][0].id
        repo.mark_failed(tid, "PARSE_FAIL", "x")
        # 未到期 → 0 条恢复
        assert repo.promote_due_failures() == 0
        # 置为已到期 → 1 条恢复
        db.execute(
            "UPDATE osint_link_task SET next_retry_at = now() - interval '1 min' WHERE id=:i",
            {"i": tid},
        )
        assert repo.promote_due_failures() == 1
        assert repo.get_by_id(tid).status == "pending"

    def test_resume_waiting_cookie(self):
        from queue_link import repository as repo

        for _ in range(2):
            repo.submit_links(_urls())
        ids = [t.id for t in repo.list_tasks()["items"]]
        for tid in ids:
            repo.mark_failed(tid, "COOKIE_EXPIRED", "401")
        assert repo.resume_waiting_cookie() == 2
        for tid in ids:
            t = repo.get_by_id(tid)
            assert t.status == "pending"
            assert t.error_code is None


class TestStatsAndCredential:
    def test_get_stats_shape(self):
        from queue_link import repository as repo

        for _ in range(2):
            repo.submit_links(_urls())
        stats = repo.get_stats()
        assert stats.get("pending") == 2
        assert stats["total"] == 2

    def test_list_dead_after_dead(self):
        from queue_link import repository as repo
        from common import db

        repo.submit_links(_urls())
        tid = repo.list_tasks()["items"][0].id
        # 前 3 次失败后推回 pending（模拟退避到期重试）；第 4 次失败自然转 dead，
        # 不再推回——否则 dead 会被本测试自己的推回语句覆盖（Round 1 测试代码笔误）
        for _ in range(3):
            repo.mark_failed(tid, "PARSE_FAIL", "boom")
            db.execute("UPDATE osint_link_task SET status='pending' WHERE id=:i", {"i": tid})
        repo.mark_failed(tid, "PARSE_FAIL", "boom")
        dead = repo.list_dead(5)
        assert len(dead) == 1 and dead[0].id == tid
        assert dead[0].status == "dead" and dead[0].error_code == "PARSE_FAIL"

    def test_save_credential_upsert(self):
        from queue_link import repository as repo

        repo.save_credential("yuanbao_cookie", "gAAAA-enc-1", "qa")
        repo.save_credential("yuanbao_cookie", "gAAAA-enc-2", "qa2")
        cred = repo.get_credential("yuanbao_cookie")
        assert cred is not None
        assert cred["updated_by"] == "qa2"
        assert "value_enc" not in cred  # 不回明文/密文
