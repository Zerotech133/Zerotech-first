# -*- coding: utf-8 -*-
"""alert 项 mtime 新鲜度判定单测（watch-alert-dev，2026-10-18）。

口径：verify_alert.log 的 mtime >= 当日 19:00 才视为今晚 19:05 生产验收
产物；早于 19:00 的 FAIL 行为历史残留（如 14:07 人工冒烟），判 UNKNOWN
防假红（验收成功时本工具不落盘故 mtime 不更新）。判定序：
不存在=UNKNOWN 偏 PASS → 残留=UNKNOWN → 新鲜+FAIL=FAIL → 新鲜无 FAIL=PASS。
"""
import os
import sys
from datetime import datetime

import pytest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import xq_timeline_watch as xtw  # noqa: E402

DAY = "2026-10-18"


def _ts(day: str, hhmm: str) -> float:
    return datetime.strptime("%s %s:00" % (day, hhmm),
                             "%Y-%m-%d %H:%M:%S").timestamp()


@pytest.fixture
def alert_env(tmp_path, monkeypatch):
    """隔离 VERIFY_ALERT_LOG 到 tmp_path（零真实文件写入），返回日志路径。"""
    log = tmp_path / "verify_alert.log"
    monkeypatch.setattr(xtw, "VERIFY_ALERT_LOG", str(log))
    return str(log)


def test_alert_threshold_boundary(alert_env):
    """19:00 整点（含）即视为新鲜（与 report 的 18:50 阈值语义不同）。"""
    assert xtw.alert_is_fresh(_ts(DAY, "19:00"), DAY) is True
    assert xtw.alert_is_fresh(_ts(DAY, "18:59"), DAY) is False


def test_alert_unknown_when_absent(alert_env):
    """文件不存在=验收从未失败 → UNKNOWN 偏 PASS。"""
    assert not os.path.exists(alert_env)
    item = xtw.collect_alert(DAY)
    assert item["status"] == "UNKNOWN"


def test_alert_unknown_when_stale_fail(alert_env):
    """14:07 冒烟残留 FAIL → UNKNOWN 防假红（今晚 19:35 误报源）。"""
    with open(alert_env, "w", encoding="ascii") as f:
        f.write("2026-10-18 14:07:00 FAIL freshness: daily json stale\n")
    os.utime(alert_env, (_ts(DAY, "14:07"),) * 2)
    item = xtw.collect_alert(DAY)
    assert item["status"] == "UNKNOWN"
    assert "残留" in item["detail"]


def test_alert_fail_when_fresh_fail(alert_env):
    """19:05 验收失败落盘（mtime>=19:00）→ FAIL 摘录。"""
    with open(alert_env, "w", encoding="ascii") as f:
        f.write("2026-10-18 19:06:12 FAIL payload: missing key anomaly_signals\n")
    os.utime(alert_env, (_ts(DAY, "19:06"),) * 2)
    item = xtw.collect_alert(DAY)
    assert item["status"] == "FAIL"
    assert "FAIL" in item["detail"]


def test_alert_pass_when_fresh_no_fail(alert_env):
    """mtime 新鲜且无 FAIL 行 → PASS。"""
    with open(alert_env, "w", encoding="ascii") as f:
        f.write("2026-10-18 19:06:12 all checks passed\n")
    os.utime(alert_env, (_ts(DAY, "19:06"),) * 2)
    item = xtw.collect_alert(DAY)
    assert item["status"] == "PASS"


def test_alert_unknown_when_stale_no_fail(alert_env):
    """残留文件即使无 FAIL 行也不得 PASS（mtime<19:00 一律 UNKNOWN）。"""
    with open(alert_env, "w", encoding="ascii") as f:
        f.write("2026-10-18 14:07:00 all checks passed\n")
    os.utime(alert_env, (_ts(DAY, "14:07"),) * 2)
    item = xtw.collect_alert(DAY)
    assert item["status"] == "UNKNOWN"
