# -*- coding: utf-8 -*-
"""shadow 项 collect_shadow 回归单测（2026-10-18 雪球采集复盘审查修复）。

病根：粗筛原为 ``day not in line[:64]``——真仓 ledger.jsonl 键序为
action/data_version/mode/reason 在前、ts 键在行尾，行首 64 字符只含
``"data_version": "2026-10-08-user-approved"`` 等固定串，**当日日期
永不在窗内** → 当日行全部被跳过 → n_today 恒 0 → 19:10 日批即使
正常落账，守望 shadow 项也永远 UNKNOWN（判据空转，假 UNKNOWN）。

修复：粗筛改整行 ``day not in line``（ts[:10] 精判仍在，data_version
等固定日期串不会误命中其他日期；reason 内容含当日日期被粗筛放行的，
由 ts 精判兜底）。本文件三用例锁定：真仓键序行 PASS / 缺账本 UNKNOWN /
无行+exit=1 FAIL。
"""
import json
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


def _ledger_row(day: str, hhmm: str, sec: str = "00700") -> str:
    """按真仓键序构造一行（action/data_version 在前、ts 在最后）——
    键序即 bug 的根源形态，测试行必须保真。"""
    row = {"action": "pass", "data_version": "2026-10-08-user-approved",
           "mode": "shadow", "reason": "bear事件风险", "ref_px": 0.0,
           "rule_id": "rule_r1", "score": 0.5, "security": sec,
           "ts": "%s %s:01" % (day, hhmm)}
    return json.dumps(row, ensure_ascii=False)


@pytest.fixture
def shadow_env(tmp_path, monkeypatch):
    """隔离 SHADOW_LEDGER / SHADOW_LOG 到 tmp_path（零真实文件读写）。"""
    led = tmp_path / "ledger.jsonl"
    log = tmp_path / "shadow_daily.log"
    monkeypatch.setattr(xtw, "SHADOW_LEDGER", str(led))
    monkeypatch.setattr(xtw, "SHADOW_LOG", str(log))
    return str(led), str(log)


def test_shadow_pass_with_today_rows_despite_key_order(shadow_env):
    """核心回归：当日行 ts 在行尾、行首 64 字符无当日日期 → 仍必须
    计入并 PASS（修复前 line[:64] 粗筛把整行滤掉，永远 UNKNOWN）。"""
    led, _log = shadow_env
    with open(led, "w", encoding="utf-8") as f:
        f.write(_ledger_row(DAY, "19:10") + "\n")
        f.write(_ledger_row(DAY, "19:10", sec="601127") + "\n")
        # 混入昨日行：ts 精判应排除，不计入 n_today
        f.write(_ledger_row("2026-10-17", "19:10") + "\n")
    item = xtw.collect_shadow(DAY)
    assert item["status"] == "PASS", item["detail"]
    assert "当日新行=2" in item["detail"]
    assert "当日新码=2" in item["detail"]


def test_shadow_unknown_when_ledger_absent(shadow_env):
    """账本不存在 → UNKNOWN（日批未跑或部署路径漂移），绝不误判。"""
    led, _log = shadow_env
    assert not os.path.exists(led)
    item = xtw.collect_shadow(DAY)
    assert item["status"] == "UNKNOWN"


def test_shadow_fail_when_no_rows_and_exit1(shadow_env):
    """当日无新行 + 日批日志 exit=1 → FAIL（真失败告警路径保留）。"""
    led, log = shadow_env
    with open(led, "w", encoding="utf-8") as f:
        f.write(_ledger_row("2026-10-17", "19:10") + "\n")   # 仅昨日行
    with open(log, "w", encoding="ascii") as f:
        f.write("%s 19:10:00 shadow_daily exit=1\n" % DAY)
    item = xtw.collect_shadow(DAY)
    assert item["status"] == "FAIL", item["detail"]
    assert "exit=1" in item["detail"]


def test_shadow_unknown_when_no_rows_and_no_exit_line(shadow_env):
    """当日无新行、日志无今日 exit 行（崩了没写/未到点）→ UNKNOWN 不误报。"""
    led, log = shadow_env
    with open(led, "w", encoding="utf-8") as f:
        f.write(_ledger_row("2026-10-17", "19:10") + "\n")
    item = xtw.collect_shadow(DAY)
    assert item["status"] == "UNKNOWN"
