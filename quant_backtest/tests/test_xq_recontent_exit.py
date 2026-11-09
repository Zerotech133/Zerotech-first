# -*- coding: utf-8 -*-
"""刀 B 契约测试（2026-10-18 预备工位产出，同日晚 v1.2.1 转正）。

契约：recontent_run() 在硬失败（模型/网络异常）时必须返回非 0。

实现（recontent_run 收尾，v1.2.1 刀 B）：rc = 1 if hard > 0 else 0
——口径=hard>0（2026-10-18 主会话裁决：recontent 是手动通道，硬失败
全量可见；run() 的 consec>=8 属长跑自动通道让路语义，不对齐）。

跑法：pytest tests/test_xq_recontent_exit.py -q
LLM 全程 monkeypatch（xc.safe_urlopen），绝不真调 Ollama；
DB 全程隔离到 tmp_path，绝不碰生产库 C:/aios/ 任何文件。

2026-11-08 修接缝（同 test_xq_classify_retry_degraded 的 P3-5 根因）：
xq_classify 经 safe_urlopen 出网，loopback 走 _NO_PROXY_OPENER，不经
urllib.request.urlopen；旧替身完全失效 → 真调本机 Ollama，硬失败模拟不出来，
本文件 2 项长期假红。
"""
import socket
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xq_classify as xc


def _boom_urlopen(req, *args, **kwargs):
    raise OSError("mock network down")


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
    """隔离 DB 与状态日志（照抄 test_xq_classify_retry_degraded 约定），
    建源表 xq_status。"""
    monkeypatch.setattr(xc, "DB_PATH", tmp_path / "xueqiu_info.db")
    monkeypatch.setattr(xc, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(xc, "STATUS_LOG", tmp_path / "logs" / "status.log")
    con = sqlite3.connect(xc.DB_PATH)
    con.execute("""CREATE TABLE xq_status (
                     status_id TEXT PRIMARY KEY, title TEXT, desc TEXT,
                     category TEXT, created_at TEXT)""")
    con.commit()
    con.close()
    return tmp_path


def seed_recontent_target(db, status_id):
    """落一条 parse_ok=1 且 content_type IS NULL 的待补标行。"""
    con = sqlite3.connect(db)
    con.execute("INSERT INTO xq_status VALUES (?,?,?,?,?)",
                (status_id, "标题", "正文", "stock", "2026-10-18 10:00:00"))
    xc.ensure_table(con)
    con.execute(
        """INSERT INTO xq_status_classified
           (status_id, parse_ok, model, prompt_version, labeled_at)
           VALUES (?,?,?,?,?)""",
        (status_id, 1, xc.MODEL, xc.PROMPT_VERSION, "2026-10-18 09:00:00"))
    con.commit()
    con.close()


# ── 契约 1：单次硬失败（hard>0）→ 返回非 0 ──────────────────────────

def test_hard_fail_returns_nonzero(env, monkeypatch):
    seed_recontent_target(xc.DB_PATH, "s1")
    monkeypatch.setattr(xc, "safe_urlopen", _boom_urlopen)
    rc = xc.recontent_run(10)
    assert rc != 0          # 现状 bug：恒 return 0


# ── 契约 2：连续 8 次硬失败触发 RE-ABORT 熔断 → 返回非 0 ────────────
# 此路径在两种候选修法（consec>=8 口径 / hard>0 口径）下都必须非 0。

def test_consecutive_abort_returns_nonzero(env, monkeypatch):
    for i in range(8):
        seed_recontent_target(xc.DB_PATH, "s%d" % i)
    monkeypatch.setattr(xc, "safe_urlopen", _boom_urlopen)
    rc = xc.recontent_run(50)
    assert rc != 0          # 现状 bug：熔断 break 后仍恒 return 0
