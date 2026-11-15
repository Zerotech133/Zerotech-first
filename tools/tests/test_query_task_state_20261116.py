# -*- coding: utf-8 -*-
"""query_task 三态：found / not_found / unmeasurable（2026-11-16）。

判例
----
沙箱与部分受限环境里 ``schtasks.exe`` 被安全策略拦（Program Blacklist），
``query_task`` 拿不到输出 → 旧实现统一返回 ``found=False`` 并附一句
「未解析到 Last Run Time/Last Result（任务可能不存在）」，巡检再把它记成
**「计划任务未找到」**。

实测后果：一次巡检 7 个任务全红，全是「计划任务未找到」，而任务实际
一个不少 —— 真实差异全部来自执行环境。**「不可测」被写成「不存在」，
比漏报更危险：它让人去修不存在的问题。**

因此这里钉死三态，并规定 issue 文案必须能区分：
  found        查到了
  not_found    schtasks 明确回答「没有这个任务」
  unmeasurable schtasks 本身跑不起来 / 被拦 / 输出无法解析 —— **不等于不存在**

运行：python -m pytest -q tools/tests/test_query_task_state_20261116.py
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

_NOW = datetime.now().astimezone()


def _load():
    p = ROOT / "tools" / "research" / "daily_tasks_health.py"
    spec = importlib.util.spec_from_file_location("daily_tasks_health_qs_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Proc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


_OK_OUT = ("任务名                          下次运行时间           状态\n"
           "Last Run Time: 11/16/2026 4:33:15\n"
           "Last Result: 0\n").encode("utf-8")


def _runner(proc=None, exc=None):
    def run(cmd, **kw):
        if exc is not None:
            raise exc
        return proc
    return run


# ---------------------------------------------------------------------------
# ① 三态本身
# ---------------------------------------------------------------------------
def test_found_when_output_parses():
    mod = _load()
    st = mod.query_task("T", runner=_runner(_Proc(0, _OK_OUT, b"")))
    assert st["found"] is True
    assert st["query_state"] == "found"
    assert st["last_result"] == 0


def test_not_found_only_when_schtasks_says_so():
    """schtasks 明确回「任务不存在」→ not_found。"""
    mod = _load()
    err = b'ERROR: The specified task name "T" does not exist in the system.\r\n'
    st = mod.query_task("T", runner=_runner(_Proc(1, b"", err)))
    assert st["found"] is False
    assert st["query_state"] == "not_found"
    assert "不存在" in st["error"] or "does not exist" in st["error"]


def test_unmeasurable_when_program_cannot_start():
    """schtasks 被安全策略拦 / 不存在 → unmeasurable，绝不能说成「任务未找到」。"""
    mod = _load()
    st = mod.query_task("T", runner=_runner(exc=FileNotFoundError("blocked by policy")))
    assert st["found"] is False
    assert st["query_state"] == "unmeasurable"
    assert "不可测" in st["error"] or "无法" in st["error"]


def test_unmeasurable_on_unrecognized_failure():
    """非零退出但不是「任务不存在」（如拒绝访问）→ unmeasurable，不猜成不存在。"""
    mod = _load()
    err = b"ERROR: Access is denied.\r\n"
    st = mod.query_task("T", runner=_runner(_Proc(1, b"", err)))
    assert st["query_state"] == "unmeasurable"
    assert "Access" in st["error"] or "denied" in st["error"]


def test_unmeasurable_when_output_unparsable():
    """rc=0 但输出没有可解析字段 → 不可测（输出形态变了），不是「不存在」。"""
    mod = _load()
    st = mod.query_task("T", runner=_runner(_Proc(0, b"some unexpected banner\n", b"")))
    assert st["query_state"] == "unmeasurable"


# ---------------------------------------------------------------------------
# ② 消费面文案必须跟着三态走
# ---------------------------------------------------------------------------
def _cfg():
    return {"name": "T", "label": "t", "stale_hours": 36, "gate": False}


def _log_ok():
    return {"exists": True, "timestamp": None, "rc": 0, "rcs": None,
            "line": "rc=0", "age_hours": 0.5}


def _out_ok():
    return {"path": "p", "exists": True, "fresh": True, "age_hours": 0.5, "spec": {}}


def test_issue_text_distinguishes_unmeasurable():
    mod = _load()
    task = mod.check_task(
        _cfg(), _NOW,
        info={"found": False, "query_state": "unmeasurable",
              "error": "不可测：schtasks 被安全策略拦截", "last_result": None},
        log=_log_ok(), outputs=[_out_ok()])

    joined = " | ".join(task["issues"])
    assert "计划任务未找到" not in joined, f"把不可测说成了不存在：{joined}"
    assert "不可测" in joined


def test_issue_text_keeps_not_found_when_really_missing():
    mod = _load()
    task = mod.check_task(
        _cfg(), _NOW,
        info={"found": False, "query_state": "not_found", "error": "does not exist",
              "last_result": None},
        log=_log_ok(), outputs=[_out_ok()])

    joined = " | ".join(task["issues"])
    assert "计划任务未找到" in joined


def test_backward_compatible_when_state_absent():
    """旧调用方只传 found=False 的 dict（无 query_state）→ 仍按「未找到」处理。"""
    mod = _load()
    task = mod.check_task(
        _cfg(), _NOW,
        info={"found": False, "last_run_time": None, "last_result": None, "status": None},
        log=_log_ok(), outputs=[_out_ok()])

    joined = " | ".join(task["issues"])
    assert "计划任务未找到" in joined


# ---------------------------------------------------------------------------
# ③ 环境级判定：一次探测决定整份报告的口径
# ---------------------------------------------------------------------------
def test_environment_flag_reported_when_task_query_unmeasurable():
    """全部任务都不可测时，报告要能一眼看出「这次巡检没测到任务」，而不是 7 个任务都坏了。"""
    mod = _load()
    tasks = []
    for i in range(3):
        tasks.append(mod.check_task(
            {**_cfg(), "name": f"T{i}"}, _NOW,
            info={"found": False, "query_state": "unmeasurable", "error": "不可测：schtasks 不可用",
                  "last_result": None},
            log=_log_ok(), outputs=[_out_ok()]))
    rep = mod.build_report(tasks, _NOW, tag="t")

    assert rep["task_query"]["available"] is False
    assert "不可测" in rep["task_query"]["note"]
    # 折叠后只剩一条，且不谎称为「未找到」
    assert len(rep["alerts"]) == 1
    assert "计划任务未找到" not in rep["alert_text"]
