# -*- coding: utf-8 -*-
"""影子运行日志/账本追加打开：有界重试+退避测试（2026-10-19 判例修复）。

背景：周一 17:10 首跑死于 open(last_run.log, "a") 的 PermissionError
（瞬时独占锁；文件无只读属性、当前未锁、锁定者不可追认，嫌疑
watch-daemon/提权进程/杀毒软件毫秒级握持）→ 5 标的零落账（未落账≠假绿）。
修复：run_shadow._open_log_append 对 OSError 系（PermissionError 为子类）
有界重试（5 次，0.5/1/2/4s），耗尽 fail-visible（_LogOpenError → main
退出码 3），禁静默跳过；无锁时行为零变化。

覆盖契约：
  1. 无锁零差异：首次即成功、追加语义保持、零 sleep；
  2. 常量契约：5 次尝试 / 0.5,1,2,4 退避（模块级常量，便于运维调参）；
  3. 瞬时锁恢复：失败 N 次后成功，尝试次数与退避序列精确匹配；
  4. 重试耗尽 fail-visible：抛 _LogOpenError（OSError 子类），诊断含
     路径/尝试次数/末次 errno；
  5. 仅 OSError 系重试：PermissionError 重试；ValueError 等非 OSError
     原样立即上抛（重试无意义，不白等 7.5s）；
  6. 主链 fail-visible：last_run.log 打开耗尽 → main() 退 3 且 stderr 留
     [FATAL] 诊断（禁静默跳过=禁零落账假绿，正是 10-19 事故形态）。

纪律：全部 tmp 隔离（本测试组不落任何 agent_platform/data/ 文件）；
sleep 注入零等待；builtins.open 打桩经 monkeypatch 自动还原。
账本 append 调用点（篮子循环内）与 last_run.log 调用点共用同一 helper，
其 FATAL 接线由用例 6 同构覆盖（账本点直跑需真 LLM/行情，不在单测面）。
"""
import builtins
import io
import sys
from pathlib import Path

import pytest

PLATFORM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM))
sys.path.insert(0, str(PLATFORM / "engine"))

import run_shadow  # noqa: E402


class _FakeStream(io.TextIOBase):
    """isatty=False 的最小流（桩掉「计划任务无终端」面，不依赖 pytest 捕获模式）。"""

    def __init__(self):
        self.buf = []

    def write(self, s):
        self.buf.append(s)
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


def _stub_open(fail_times, exc):
    """前 fail_times 次抛 exc、之后委托真 open 的 builtins.open 桩。"""
    orig = builtins.open
    st = {"n": 0}

    def stub(path, mode="r", *a, **k):
        st["n"] += 1
        if st["n"] <= fail_times:
            raise exc
        return orig(path, mode, *a, **k)

    return stub, st


@pytest.fixture
def log_file(tmp_path):
    p = tmp_path / "shadow" / "last_run.log"
    p.parent.mkdir(parents=True)
    p.write_text("===== 旧 run =====\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------- 无锁零差异

def test_no_lock_first_attempt_succeeds_zero_behavior_change(log_file):
    """契约 1：无锁时首次即成功——返回真文件句柄、追加语义保持、零 sleep
    （与裸 open(path, "a", encoding="utf-8") 行为零差异）。"""
    slept = []
    f = run_shadow._open_log_append(log_file, sleep=lambda s: slept.append(s))
    try:
        f.write("new line\n")
    finally:
        f.close()
    assert slept == []
    text = log_file.read_text(encoding="utf-8")
    assert "===== 旧 run =====" in text and "new line" in text


def test_constants_contract():
    """契约 2：生产默认 5 次尝试 / 0.5,1,2,4 退避（模块级常量可 patch）。"""
    assert run_shadow.LOG_OPEN_MAX_ATTEMPTS == 5
    assert run_shadow.LOG_OPEN_BACKOFF_S == (0.5, 1.0, 2.0, 4.0)


# ---------------------------------------------------------------- 瞬时锁恢复

def test_transient_lock_recovers_with_bounded_backoff(monkeypatch, log_file):
    """契约 3：前 2 次 PermissionError 后成功——共 3 次尝试、退避精确
    [0.5, 1.0]（第 3 次成功不再睡）；锁释放即恢复，不无谓耗尽。"""
    stub, st = _stub_open(2, PermissionError(13, "Permission denied"))
    monkeypatch.setattr(builtins, "open", stub)
    slept = []
    f = run_shadow._open_log_append(log_file, sleep=lambda s: slept.append(s))
    f.close()
    assert st["n"] == 3
    assert slept == [0.5, 1.0]


def test_permission_error_is_retried_as_oserror_subclass(monkeypatch, log_file):
    """契约 5a：PermissionError 是 OSError 子类——10-19 事故形态必须被
    重试（前 3 次失败第 4 次成功，退避 0.5/1/2）。"""
    stub, st = _stub_open(3, PermissionError(13, "Permission denied"))
    monkeypatch.setattr(builtins, "open", stub)
    slept = []
    f = run_shadow._open_log_append(log_file, sleep=lambda s: slept.append(s))
    f.close()
    assert st["n"] == 4
    assert slept == [0.5, 1.0, 2.0]


# ---------------------------------------------------------------- 耗尽 fail-visible

def test_exhausted_raises_log_open_error_with_diagnostics(monkeypatch, log_file):
    """契约 4：重试耗尽抛 _LogOpenError（OSError 子类）；尝试=5、退避全额
    [0.5,1,2,4]（总等待 7.5s 有界）；诊断含路径/尝试次数/末次 errno——
    fail-visible 可排障，禁裸 traceback 无诊断。"""
    stub, st = _stub_open(99, PermissionError(13, "Permission denied"))
    monkeypatch.setattr(builtins, "open", stub)
    slept = []
    with pytest.raises(run_shadow._LogOpenError) as ei:
        run_shadow._open_log_append(log_file, sleep=lambda s: slept.append(s))
    assert st["n"] == 5
    assert slept == [0.5, 1.0, 2.0, 4.0]
    msg = str(ei.value)
    assert str(log_file) in msg and "5 次" in msg and "13" in msg
    assert isinstance(ei.value, OSError)


def test_non_oserror_propagates_immediately_no_retry(monkeypatch, log_file):
    """契约 5b：非 OSError（如 ValueError）原样立即上抛——重试无意义，
    1 次尝试零 sleep（不为注定失败的打开白等 7.5s）。"""
    stub, st = _stub_open(99, ValueError("bad encoding"))
    monkeypatch.setattr(builtins, "open", stub)
    slept = []
    with pytest.raises(ValueError):
        run_shadow._open_log_append(log_file, sleep=lambda s: slept.append(s))
    assert st["n"] == 1
    assert slept == []


# ---------------------------------------------------------------- 主链 fail-visible

def test_main_fatal_exit_3_when_log_open_exhausted(monkeypatch, tmp_path):
    """契约 6：last_run.log 打开耗尽 → main() 返回 3（非零=计划任务历史
    可见，经 shadow_automation.ps1 exit $LASTEXITCODE 透传）且 stderr 留
    [FATAL] 诊断——禁静默跳过（跳过=零落账假绿，正是 10-19 事故形态）。"""
    ledger = tmp_path / "shadow" / "shadow_ledger.jsonl"
    monkeypatch.setattr(run_shadow, "LEDGER", ledger)
    monkeypatch.setattr(run_shadow, "REHEARSAL", True)  # 隔离阀：不碰真仓 data/

    def boom(*a, **k):
        raise run_shadow._LogOpenError("stub: locked after 5 attempts")

    monkeypatch.setattr(run_shadow, "_open_log_append", boom)
    err_buf = _FakeStream()
    monkeypatch.setattr(sys, "stdout", _FakeStream())  # isatty=False → 重定向分支
    monkeypatch.setattr(sys, "stderr", err_buf)
    rc = run_shadow.main()
    assert rc == 3
    out = "".join(err_buf.buf)
    assert "[FATAL]" in out and "last_run.log" in out and "非假绿" in out
