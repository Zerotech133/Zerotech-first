# -*- coding: utf-8 -*-
"""G9 融合失败可观测性测试：mock PG 断连 → E1 告警链留痕与状态流转。

覆盖：
  · PG 断连注入（_pg_connect 抛异常 + docker exec 兜底 mock 失败）
    → outbox pending→.sent 流转 + alert_send.log 追加（result=OK）
  · 发送失败 → pending 保持不动（kept）+ alert_send.log result=FAIL
  · persist_combined 返回 False 分支同样触发告警
  · 告警链自身故障不反噬聚合主流程（禁 fail-open 假绿：内存结果照常返回）
  · 消息内容可诊断（trade_date / 根因 / rows）

红线（对齐 news_collector/test_alert_fail.py 惯例）：全程 mock + 临时目录，
不触网、不读写真实 outbox、不连真实 PG、不执行任何 DDL。
"""

import os
import re
import sys
import types
from unittest import mock

import pytest

QUANT_BT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if QUANT_BT not in sys.path:
    sys.path.insert(0, QUANT_BT)

from strategy_layer import aggregator, alert_bridge  # noqa: E402
import alert_fail_wrap as _wrap  # noqa: E402

TS_LINE_RE = (
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} \| task=\S+ "
    r"\| channel=\S+ \| result=(OK|FAIL) \| reason=.+$"
)


# 假 psycopg2（本 venv 无真包）：仅供 _persist_psycopg2 顶部
# `import psycopg2.extras` 通过；真实连接/执行入口一律 assert 拒止
# （本测试链只走 _pg_connect mock 断连，禁连真实 PG 5442）。
def _fake_pg_connect(*args, **kwargs):
    raise AssertionError("假 psycopg2 不得连接（测试禁连真实 PG）")


def _fake_pg_execute_values(*args, **kwargs):
    raise AssertionError("假 psycopg2 不得执行 SQL（测试禁连真实 PG）")


_fake_pg_extras = types.ModuleType("psycopg2.extras")
_fake_pg_extras.execute_values = _fake_pg_execute_values
_fake_pg = types.ModuleType("psycopg2")
_fake_pg.extras = _fake_pg_extras
_fake_pg.connect = _fake_pg_connect
_fake_pg.Error = Exception
_FAKE_PSYCOPG2 = {"psycopg2": _fake_pg, "psycopg2.extras": _fake_pg_extras}


class _StubAdapter:
    """最小 duck-type Adapter 桩（aggregator 按属性访问）。"""

    strategy_id = "stub"

    def score(self, trade_date):
        return [
            {
                "symbol": "600000.SH",
                "action": "buy",
                "score": 88.0,
                "confidence": 0.9,
                "rationale": "stub",
                "trade_date": trade_date,
            }
        ]


@pytest.fixture()
def sandbox(tmp_path):
    """E1 沙箱：outbox 与 alert_send.log 全部指向临时目录。"""
    outbox = str(tmp_path / "outbox")
    send_log = str(tmp_path / "logs" / "alert_send.log")
    with (
        mock.patch.object(_wrap, "OUTBOX", outbox),
        mock.patch.object(_wrap, "SEND_LOG", send_log),
    ):
        yield {"outbox": outbox, "send_log": send_log}


def _inject_pg_down():
    """写库全链失败注入：psycopg2 连接断 + docker exec 兜底不可用。

    本 venv 无 psycopg2：_persist_psycopg2 函数体首行
    `import psycopg2.extras`（延迟 import）先于 _pg_connect() 调用，
    缺模块时错误链首级退化为 "No module named 'psycopg2'"，_pg_connect
    的 mock 断连语义根本不会触发。生产环境装有 psycopg2，线上走的
    正是 _pg_connect 断连路径。故注入期以 sys.modules 补位假
    psycopg2（extras 桩）让 import 通过，把失败形态还原为与线上
    逐字一致的 "mock PG 断连 5442"——断言不变，只是还原被测路径；
    假模块的真实连接/执行入口一律 assert 拒止（禁连真实 PG）。
    """
    return [
        mock.patch.object(
            aggregator, "_pg_connect", side_effect=ConnectionError("mock PG 断连 5442")
        ),
        mock.patch.object(
            aggregator.subprocess,
            "run",
            side_effect=RuntimeError("docker 不可用 (mock)"),
        ),
        mock.patch.dict(sys.modules, _FAKE_PSYCOPG2),
    ]


def _run_agg(trade_date="20261019"):
    return aggregator.SignalAggregator().run(trade_date, [_StubAdapter()])


def _pending(sandbox):
    return os.path.join(sandbox["outbox"], "pending_alert_%s.txt" % alert_bridge.TASK)


def _sent(sandbox):
    return _pending(sandbox) + ".sent"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _log_lines(sandbox):
    with open(sandbox["send_log"], "r", encoding="ascii") as f:
        return f.read().splitlines()


class TestPgDownAlertChain:
    """PG 断连注入 → E1 告警链全流程。"""

    def test_pg_down_sent_flow(self, sandbox):
        """全链断连 + 发送成功：内存结果照常返回、pending→.sent、log=OK。"""
        patches = _inject_pg_down()
        with (
            patches[0],
            patches[1],
            patches[2],
            mock.patch.object(
                alert_bridge, "_send_feishu", return_value=(True, "SEND_OK")
            ) as send_fn,
        ):
            result = _run_agg()
        # 聚合判定不受影响（禁 fail-open 假绿：内存结果照常返回）
        assert len(result.combined) == 1
        assert result.combined[0]["symbol"] == "600000.SH"
        # 发送复用 send_feishu：入参为 pending 落点
        assert send_fn.call_count == 1
        assert send_fn.call_args[0][0] == _pending(sandbox)
        # 状态流转：pending 已原子改名为 .sent
        assert not os.path.exists(_pending(sandbox))
        assert os.path.isfile(_sent(sandbox))
        # 落痕可诊断：trade_date 与根因都在消息里
        body = _read(_sent(sandbox))
        assert "trade_date=20261019" in body
        assert "rows=1" in body
        assert "mock PG 断连 5442" in body
        assert "signals_combined 写库失败" in body
        # alert_send.log 单行 ASCII 追加，E1 同格式
        lines = _log_lines(sandbox)
        assert len(lines) == 1
        assert re.match(TS_LINE_RE, lines[0])
        assert "task=%s" % alert_bridge.TASK in lines[0]
        assert "result=OK" in lines[0]
        assert "reason=SEND_OK" in lines[0]

    def test_send_fail_pending_kept(self, sandbox):
        """发送失败：pending 原地保留（kept），log 记 FAIL，不流转。"""
        patches = _inject_pg_down()
        with (
            patches[0],
            patches[1],
            patches[2],
            mock.patch.object(
                alert_bridge, "_send_feishu", return_value=(False, "SEND_FAIL network")
            ),
        ):
            result = _run_agg()
        assert len(result.combined) == 1  # 主流程不中断
        assert os.path.isfile(_pending(sandbox))  # pending 原地保留
        assert not os.path.exists(_sent(sandbox))  # 不误标 .sent
        assert "mock PG 断连 5442" in _read(_pending(sandbox))
        lines = _log_lines(sandbox)
        assert len(lines) == 1
        assert "result=FAIL" in lines[0]
        assert "reason=SEND_FAIL network" in lines[0]

    def test_persist_false_branch_alerts(self, sandbox):
        """persist_combined 返回 False 分支同样触发 E1 告警。"""
        with (
            mock.patch.object(aggregator, "persist_combined", return_value=False),
            mock.patch.object(
                alert_bridge, "_send_feishu", return_value=(True, "SEND_OK")
            ),
        ):
            result = _run_agg()
        assert len(result.combined) == 1
        assert os.path.isfile(_sent(sandbox))
        assert "result=OK" in _log_lines(sandbox)[0]


class TestNoBackfire:
    """告警观测层不能反噬聚合主流程。"""

    def test_alert_bridge_broken_no_raise(self, sandbox):
        """alert_bridge 自身抛错 → 聚合照常返回结果、不外抛。"""
        patches = _inject_pg_down()
        with (
            patches[0],
            patches[1],
            patches[2],
            mock.patch.object(
                alert_bridge,
                "alert_persist_failure",
                side_effect=RuntimeError("bridge boom"),
            ),
        ):
            result = _run_agg()  # 不应抛异常
        assert len(result.combined) == 1

    def test_alert_bridge_never_raises(self, sandbox):
        """alert_persist_failure 自身对任意故障都不外抛（直调验证）。"""
        with (
            mock.patch.object(
                _wrap, "handle_send_result", side_effect=OSError("disk boom")
            ),
            mock.patch.object(
                alert_bridge, "_send_feishu", return_value=(True, "SEND_OK")
            ),
        ):
            # 不抛即通过（outbox 目录不可写等场景同理由外层 try 吞掉）
            alert_bridge.alert_persist_failure("20261019", 3, "boom")


class TestMessage:
    """消息内容可诊断。"""

    def test_build_message_fields(self):
        msg = alert_bridge.build_message("20261019", 7, RuntimeError("pg down"))
        assert "task=%s" % alert_bridge.TASK in msg
        assert "trade_date=20261019" in msg
        assert "rows=7" in msg
        assert "pg down" in msg
        assert "run_daily" in msg  # 可定位重跑入口

    def test_build_message_error_truncated(self):
        msg = alert_bridge.build_message("20261019", 1, "x" * 5000)
        assert len(msg) < 1000  # 根因截断控体积，不撑爆消息
