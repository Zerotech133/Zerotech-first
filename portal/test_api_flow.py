# -*- coding: utf-8 -*-
"""R4 rerun 扩容单测（仿 test_api_evolution.py 判例）。

红线：tmp 注入，绝不读真实 agent_platform/data/（shadow_ledger / 三个日志 /
observe 账本全部 monkeypatch 到 tmp_path）；绝不真触发计划任务——
subprocess.run 打桩为哨兵，任何 schtasks 调用记录进 calls 断言，不落真任务。
运行：python -X utf8 -m pytest portal/test_api_flow.py -q
"""

import json
import os
import subprocess  # 仅用 CompletedProcess 造桩返回值，不真跑
import sys
import time
import datetime as _dt
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import api_flow  # noqa: E402

NEW_TASKS = ("AgentShadowDaily", "AgentChiefPush", "AgentShadowEvalWeekly")
LEGACY_TASKS = ("TodaysPicks", "StrongEod", "RpsDaily", "JevFeed-Daily", "NewsAI")


# ── 桩与夹具 ──────────────────────────────────────────────────────────────


class _Stub:
    """最小 Handler 桩：只接 _json 落点（api_flow.Handler 是独立 mixin 类）。"""

    def __init__(self):
        self.resp = None
        self.code = 200

    def _json(self, data, code=200):
        self.resp = data
        self.code = code


class _SchtasksSentry:
    """subprocess.run 哨兵：记录调用、返回成功桩，不真跑计划任务。"""

    def __init__(self, rc=0):
        self.calls = []
        self.rc = rc

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        r = subprocess.CompletedProcess(cmd, self.rc)
        r.stdout = b'SUCCESS: the scheduled task "X" has been run'
        return r


class _SchtasksBoom:
    """subprocess.run 异常桩：走 500 分支。"""

    def __call__(self, cmd, **kw):
        raise RuntimeError("schtasks boom")


def _fixed_now(monkeypatch, y, m, d):
    """固定 api_flow 的 _dt.datetime.now()（monkeypatch 自动还原）。

    注意 _dt 是共享模块对象，这里 patch 的是 api_flow 可见同名属性，
    测试结束 monkeypatch 还原，不外溢。
    """

    class _Fixed(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(y, m, d, 12, 0, 0)

    monkeypatch.setattr(api_flow._dt, "datetime", _Fixed)


def _rerun(task, caller=None):
    """构造桩 Handler 调 _flow_rerun，返回 (stub, sentry 由夹具外置)。"""
    h = _Stub()
    body = {"task": task}
    if caller is not None:
        body["caller"] = caller
    api_flow.Handler._flow_rerun(h, body)
    return h


def _last_audit_rec():
    lines = Path(api_flow.OBSERVE_AUDIT).read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """全部数据面注入 tmp + subprocess 打桩。返回哨兵。"""
    sentry = _SchtasksSentry()
    monkeypatch.setattr(api_flow.subprocess, "run", sentry)
    monkeypatch.setattr(
        api_flow, "SHADOW_LEDGER", str(tmp_path / "shadow_ledger.jsonl")
    )
    monkeypatch.setattr(api_flow, "CHIEF_LOG", str(tmp_path / "chief_last_run.log"))
    monkeypatch.setattr(api_flow, "EVAL_LOG", str(tmp_path / "eval_last_run.log"))
    monkeypatch.setattr(
        api_flow, "OBSERVE_AUDIT", str(tmp_path / "llm_tool_audit.jsonl")
    )
    return sentry


# ── 1. 白名单扩容 ─────────────────────────────────────────────────────────


def test_whitelist_expanded():
    for t in NEW_TASKS:
        assert t in api_flow.RERUN_OK
    # 既有 17 任务不丢（抽代表）
    for t in LEGACY_TASKS:
        assert t in api_flow.RERUN_OK
    # 复盘档只读红线：AgentMondayCheckpoint 明确不进白名单
    assert "AgentMondayCheckpoint" not in api_flow.RERUN_OK


def test_outside_whitelist_refused_400(env):
    h = _rerun("AgentMondayCheckpoint")
    assert h.code == 400
    assert h.resp["error"] == "task not in whitelist"
    assert "AgentShadowDaily" in h.resp["whitelist"]
    assert "AgentMondayCheckpoint" not in h.resp["whitelist"]
    assert env.calls == []  # 白名单外绝不触发任何任务


def test_legacy_tasks_keep_no_valve_semantics(env):
    """既有 17 任务无阀语义不变：数据面全空 → 直接放行。"""
    h = _rerun("TodaysPicks")
    assert h.code == 200 and h.resp["ok"] is True
    assert env.calls == [["schtasks", "/Run", "/TN", "TodaysPicks"]]


# ── 2. 放行路径 + caller 归因 ─────────────────────────────────────────────


def test_shadow_daily_pass_monday_and_attribution(env, monkeypatch):
    _fixed_now(monkeypatch, 2026, 10, 19)  # 周一；tmp 账本不存在=无产出
    h = _rerun("AgentShadowDaily", caller="hermes-console")
    assert h.code == 200
    # 既有字段向后兼容（ok/task/out 一个不少）
    for k in ("ok", "task", "out"):
        assert k in h.resp
    assert h.resp["ok"] is True and h.resp["task"] == "AgentShadowDaily"
    assert h.resp["caller"] == "hermes-console"
    assert env.calls == [["schtasks", "/Run", "/TN", "AgentShadowDaily"]]
    # R4 验收③：归因入 observe 账本
    rec = _last_audit_rec()
    assert rec["caller"] == "hermes-console"
    assert rec["task"] == "AgentShadowDaily"
    assert rec["endpoint"] == "/api/flow/rerun" and rec["method"] == "POST"
    assert rec["ok"] is True


def test_default_caller_portal_console(env, monkeypatch):
    _fixed_now(monkeypatch, 2026, 10, 19)
    h = _rerun("AgentShadowDaily")  # 不带 caller
    assert h.resp["caller"] == "portal-console"
    assert _last_audit_rec()["caller"] == "portal-console"


def test_chief_push_pass_when_no_send_ok(env):
    h = _rerun("AgentChiefPush")
    assert h.resp["ok"] is True
    assert env.calls == [["schtasks", "/Run", "/TN", "AgentChiefPush"]]


def test_eval_weekly_pass_when_no_output(env):
    h = _rerun("AgentShadowEvalWeekly")
    assert h.resp["ok"] is True
    assert env.calls == [["schtasks", "/Run", "/TN", "AgentShadowEvalWeekly"]]


# ── 3. 幂等阀三种拦截 ─────────────────────────────────────────────────────


def test_shadow_daily_blocked_weekend(env, monkeypatch):
    _fixed_now(monkeypatch, 2026, 10, 18)  # 周日；账本不存在也拦（防污染账本）
    h = _rerun("AgentShadowDaily")
    assert h.code == 200
    assert h.resp["ok"] is False
    assert "blocked" in h.resp and "周一" in h.resp["blocked"]
    assert "error" not in h.resp  # 拦截≠异常，不与 500 分支混流
    assert env.calls == []  # 绝不真触发


def test_shadow_daily_blocked_same_day_ledger(env, monkeypatch):
    _fixed_now(monkeypatch, 2026, 10, 19)  # 周一
    Path(api_flow.SHADOW_LEDGER).write_text(
        json.dumps(
            {"date": "2026-10-19", "status": "done", "symbol": "600519"},
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {"date": "2026-10-16", "status": "done", "symbol": "000858"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    h = _rerun("AgentShadowDaily")
    assert h.resp["ok"] is False
    assert "当日" in h.resp["blocked"] and "账本" in h.resp["blocked"]
    assert env.calls == []
    # 拦截也留归因痕（ok=False + blocked 原因）
    rec = _last_audit_rec()
    assert rec["ok"] is False and "blocked" in rec
    assert rec["task"] == "AgentShadowDaily"


def test_chief_push_blocked_same_day_send_ok(env):
    p = Path(api_flow.CHIEF_LOG)
    p.write_text("[A0] 日报已生成\n[A0] 飞书推送：SEND_OK\n", encoding="utf-8")
    os.utime(p, (time.time(), time.time()))  # mtime=今日
    h = _rerun("AgentChiefPush")
    assert h.resp["ok"] is False and "SEND_OK" in h.resp["blocked"]
    assert env.calls == []


def test_chief_push_not_blocked_by_send_fail_today(env):
    """当日日志但推送失败（无 SEND_OK）→ 不拦，允许重跑补推。"""
    p = Path(api_flow.CHIEF_LOG)
    p.write_text("[A0] 日报已生成\n[A0] 飞书推送：SEND_FAIL\n", encoding="utf-8")
    os.utime(p, (time.time(), time.time()))
    h = _rerun("AgentChiefPush")
    assert h.resp["ok"] is True


def test_chief_push_not_blocked_by_stale_send_ok(env):
    """历史日志（非当日 mtime）含 SEND_OK → 不拦（只看当日口径）。"""
    p = Path(api_flow.CHIEF_LOG)
    p.write_text("SEND_OK\n", encoding="utf-8")
    old = time.time() - 3 * 86400
    os.utime(p, (old, old))
    h = _rerun("AgentChiefPush")
    assert h.resp["ok"] is True


def test_eval_weekly_blocked_same_day_output(env):
    p = Path(api_flow.EVAL_LOG)
    p.write_text("J1 PASS\nJ2 rho=0.42\n", encoding="utf-8")
    os.utime(p, (time.time(), time.time()))
    h = _rerun("AgentShadowEvalWeekly")
    assert h.resp["ok"] is False and "当日" in h.resp["blocked"]
    assert env.calls == []


def test_eval_weekly_not_blocked_by_stale_log(env):
    p = Path(api_flow.EVAL_LOG)
    p.write_text("J1 PASS\n", encoding="utf-8")
    old = time.time() - 8 * 86400
    os.utime(p, (old, old))
    h = _rerun("AgentShadowEvalWeekly")
    assert h.resp["ok"] is True


# ── 4. 响应契约向后兼容 + 异常路径 ─────────────────────────────────────────


def test_error_path_backward_compat(env, monkeypatch):
    """异常路径：既有 ok/task/error 字段不变，仅新增 caller。"""
    monkeypatch.setattr(api_flow.subprocess, "run", _SchtasksBoom())
    h = _rerun("TodaysPicks")
    assert h.code == 500
    for k in ("ok", "task", "error"):
        assert k in h.resp
    assert h.resp["ok"] is False
    assert h.resp["caller"] == "portal-console"


def test_refusal_path_backward_compat(env):
    """白名单拒绝路径：既有 error/whitelist 字段不变，仅新增 caller。"""
    h = _rerun("NoSuchTask")
    assert h.code == 400
    assert set(("error", "whitelist")) <= set(h.resp)
    assert h.resp["caller"] == "portal-console"


# ── 5. 阀函数直测（口径表单点验证） ────────────────────────────────────────


def test_valve_none_for_legacy_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(api_flow, "SHADOW_LEDGER", str(tmp_path / "x.jsonl"))
    monkeypatch.setattr(api_flow, "CHIEF_LOG", str(tmp_path / "x.log"))
    monkeypatch.setattr(api_flow, "EVAL_LOG", str(tmp_path / "y.log"))
    assert api_flow._rerun_blocked("TodaysPicks") is None
    assert api_flow._rerun_blocked("StrongEod") is None


def test_valve_weekday_saturday(tmp_path, monkeypatch):
    monkeypatch.setattr(api_flow, "SHADOW_LEDGER", str(tmp_path / "x.jsonl"))
    assert (
        api_flow._rerun_blocked(
            "AgentShadowDaily", now=_dt.datetime(2026, 10, 17, 12, 0)
        )
        is not None
    )  # 周六
    assert (
        api_flow._rerun_blocked(
            "AgentShadowDaily", now=_dt.datetime(2026, 10, 19, 12, 0)
        )
        is None
    )  # 周一


# ── 6. observe 账本 fail-open（写失败不影响 rerun 主流程） ─────────────────


def test_observe_fail_open(env, tmp_path, monkeypatch):
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("i am a file", encoding="utf-8")
    # OBSERVE_AUDIT 落在「文件路径之下」→ makedirs 必失败
    monkeypatch.setattr(
        api_flow, "OBSERVE_AUDIT", str(blocker / "llm_tool_audit.jsonl")
    )
    _fixed_now(monkeypatch, 2026, 10, 19)
    h = _rerun("AgentShadowDaily")  # 归因写失败不得影响放行
    assert h.code == 200 and h.resp["ok"] is True
    assert len(env.calls) == 1
