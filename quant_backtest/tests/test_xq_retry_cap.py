# -*- coding: utf-8 -*-
"""刀 A 契约测试（2026-10-18 预备工位产出，同日晚 v1.4.3 转正）。

契约：classify_one 对同一 status_id 重复 INSERT OR REPLACE 后，
retry_count 必须保留旧值——REPLACE 删旧插新不得抹掉 degraded 计次。

实现（xq_classify.py classify_one，v1.4.3 刀 A）：INSERT 前查旧
retry_count，显式带进 REPLACE 列清单；归零语义由 retry_degraded 的
upgraded 分支显式 UPDATE 0（docstring 承诺「升级归零/仍截断累加」不变）。

跑法：pytest tests/test_xq_retry_cap.py -q
LLM 全程 monkeypatch（xc.safe_urlopen），绝不真调 Ollama；
DB 全程隔离（内存库/tmp_path），绝不碰生产库 C:/aios/ 任何文件。

2026-11-08 修接缝（同 test_xq_classify_retry_degraded 的 P3-5 根因）：
xq_classify 经 safe_urlopen 出网，loopback 走 _NO_PROXY_OPENER，不经
urllib.request.urlopen；旧替身失效 → 真调本机 Ollama（本轮实测本文件
直接卡到 200s 超时），契约断言失去意义。
"""
import json
import socket
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xq_classify as xc

BAD = '{"event_type":"行情观'   # 模拟 LLM 输出截断 → 不可解析 → 保持 degraded


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def patch_llm(monkeypatch, answer: str) -> None:
    """Ollama 响应换成固定截断内容（照抄 test_xq_classify_retry_degraded 约定）。"""
    def fake_safe_urlopen(req, *args, **kwargs):
        return _FakeResp(json.dumps({"message": {"content": answer}}).encode("utf-8"))
    monkeypatch.setattr(xc, "safe_urlopen", fake_safe_urlopen)


@pytest.fixture(autouse=True)
def _hermetic_llm(monkeypatch):
    """关租约 + 真实网络即断言（fail-visible，接缝再断开时不会静默打真服务）。"""
    monkeypatch.setenv("XQ_OLLAMA_LOCK", "0")

    def _no_connect(self, addr, *a, **kw):
        raise AssertionError(
            "测试禁止真实网络连接：%r —— LLM 替身接缝已断，"
            "patch 目标应为 xc.safe_urlopen" % (addr,))

    monkeypatch.setattr(socket.socket, "connect", _no_connect)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离 DB 与状态日志：LOG_DIR/STATUS_LOG 指 tmp_path，
    严防 log() 落笔生产 C:/aios/automation_runs/。"""
    monkeypatch.setattr(xc, "DB_PATH", tmp_path / "xueqiu_info.db")
    monkeypatch.setattr(xc, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(xc, "STATUS_LOG", tmp_path / "logs" / "status.log")
    return tmp_path


def seed_degraded(con: sqlite3.Connection, status_id: str, retry_count: int) -> None:
    """手工落一条带计次的 degraded 行，模拟历史截断产物。"""
    xc.ensure_table(con)
    con.execute(
        """INSERT INTO xq_status_classified
           (status_id, parse_ok, model, prompt_version, labeled_at, retry_count)
           VALUES (?,?,?,?,?,?)""",
        (status_id, 0, xc.MODEL, xc.PROMPT_VERSION,
         "2026-10-18 09:00:00", retry_count))
    con.commit()


def fetch_retry(con: sqlite3.Connection, status_id: str) -> int:
    return con.execute(
        "SELECT retry_count FROM xq_status_classified WHERE status_id=?",
        (status_id,)).fetchone()[0]


ROW = ("s1", "茅台要涨", "正文内容", "stock")


# ── 契约 1：classify_one 单次 REPLACE 保留 retry_count 旧值 ─────────

def test_replace_preserves_retry_count(env, monkeypatch):
    patch_llm(monkeypatch, BAD)          # 仍截断 → 保持 degraded（parse_ok=0）
    con = sqlite3.connect(":memory:")
    seed_degraded(con, "s1", retry_count=2)
    assert xc.classify_one(con, ROW) is True   # degraded 落账也返回 True
    assert fetch_retry(con, "s1") == 2         # 现状 bug：REPLACE 清零 → 0
    con.close()


# ── 契约 2：同一 id 连续多轮 REPLACE，计次不丢（封顶语义的根基） ────

def test_repeated_replace_preserves_retry_count(env, monkeypatch):
    patch_llm(monkeypatch, BAD)
    con = sqlite3.connect(":memory:")
    seed_degraded(con, "s1", retry_count=1)
    for _ in range(3):
        assert xc.classify_one(con, ROW) is True
        assert fetch_retry(con, "s1") == 1     # 现状 bug：首轮即清零
    con.close()


# ── 附注（预备工位实证，2026-10-18）：实施清单刀 A 节建议的三段式集成用例
# （旧行 retry_count=2 → retry_degraded → 断言 =3 且不再入选）在当前实现下
# 就已通过（XPASS）：retry_degraded 的 UPDATE（xq_classify.py:466-468）用
# 轮首 SELECT（:431）预读的 old_retry 覆写 (old+1)，恰好补偿 REPLACE 清零。
# 故刀 A 的可 xfail 契约只有 classify_one 单体层（契约 1/2）；集成口径
# 不能进本 xfail 文件（strict 下会报 XPASS）。修复合入后集成行为不变
# （UPDATE 仍覆写 old+1），明晨验证以单体契约 + 既有
# test_xq_classify_retry_degraded.py 回归为准。
