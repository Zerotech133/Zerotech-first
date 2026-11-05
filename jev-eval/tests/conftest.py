# -*- coding: utf-8 -*-
"""jev-eval 测试公共夹具（JEV-DSN-20261105）。

两件事，都只影响测试进程：

1. 默认放行 docker 降级（`JEV_DB_DOCKER_FALLBACK=1`）。
   生产默认已改为 0（降级 opt-in），但 `test_jev_common_fallback.py` 的用例
   描述的是「降级通道本身的机制」（占位符转换/异常包装/行解析），必须在
   降级放行状态下才有意义。要验证默认关闭的用例，自己 `monkeypatch.delenv`
   或显式设 0——新增的 test_jev_db_channel.py 即如此写。
2. 每个用例前清空进程级状态（WARN 限流窗口 + 通道计数/最近失效归因）。
   限流是进程内共享状态，不清会跨用例串联（前一条 WARN 把后一条压掉）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

JEV_DIR = Path(__file__).resolve().parents[1]
if str(JEV_DIR) not in sys.path:
    sys.path.insert(0, str(JEV_DIR))

import jev_common as jc  # noqa: E402


@pytest.fixture(autouse=True)
def _jev_db_channel_test_env(monkeypatch):
    monkeypatch.setenv("JEV_DB_DOCKER_FALLBACK", "1")
    jc._warn_seen.clear()
    jc._STATE.update(
        {"channel": "", "degraded_total": 0, "denied_total": 0, "last_failure": None}
    )
    jc._conn.update({"c": None, "mode": ""})
    yield
    jc._warn_seen.clear()
    jc._STATE.update(
        {"channel": "", "degraded_total": 0, "denied_total": 0, "last_failure": None}
    )
    jc._conn.update({"c": None, "mode": ""})
