# -*- coding: utf-8 -*-
"""analysis/tests 公共 fixture。

intraday_watch.run_once(dry_run=False) 会写全局 HEARTBEAT_PATH；
测试里必须重定向到临时目录，避免污染真实 analysis_work 心跳（曾导致
heartbeat.json 被测试覆盖、guard 误判）。
"""
import pytest


@pytest.fixture(autouse=True)
def _hermetic_intraday_heartbeat(monkeypatch, tmp_path):
    try:
        import analysis.intraday_watch as iw
    except Exception:  # pragma: no cover - 模块不可导入时跳过
        return
    if hasattr(iw, "HEARTBEAT_PATH"):
        monkeypatch.setattr(iw, "HEARTBEAT_PATH", str(tmp_path / "heartbeat.json"))
