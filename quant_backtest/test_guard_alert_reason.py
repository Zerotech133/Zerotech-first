# -*- coding: utf-8 -*-
"""guard_alert 推送通道修复单测（A4 20261019，依据 strong_card_stale闸门
与push_fail诊断_20261019.md §五）。

覆盖：
  1. 缺陷①：push fail 日志带 reason（send_feishu 的 SEND_FAIL 原因落盘）
  2. reason ≤120 字符截断；底座未落原因时的 fallback
  3. 成功路径日志不变（既有 grep `push fail key=` / `dedup skip key=` 兼容）
  4. runner.LAST_PUSH_FAIL 透传：SEND_FAIL 落账、SEND_OK 清空
  5. 异常②：check-then-write 并发竞争——同 key 无重复 sent、
     异 key 无全量覆写丢 key（文件锁串行化）
  6. 超窗重发（预置 13h 前状态）/ 12h 去重边界（11:59:59 skip，12:00:00 发）
  7. 告警链自身故障只打点不反噬（_push 抛错不上抛）
  8. 状态原子写保留（无 .tmp 残留、结构不变）
  9. 文件锁：陈旧锁强破、等锁超时让步

零真实推送（_push/push_feishu 全打桩，不真发飞书）、零真实状态（tmp_path）。
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta

import pytest

HERE = sys.path[0]
import guard_alert as ga  # noqa: E402

T0 = datetime(2026, 10, 19, 6, 30, 0)
REASON = "个股锚 600519.SH=2026-10-12（zhice）≠ 板块锚 881019=2026-10-13"


@pytest.fixture
def state_fp(tmp_path):
    return str(tmp_path / "guard_alert_state.json")


@pytest.fixture(autouse=True)
def log_fp(tmp_path, monkeypatch):
    """日志重定向到 tmp，返回路径。

    WO-QT-20261020-059：改 autouse 全用例生效——原仅显式接收 log_fp
    的用例被隔离，runner 侧三例（test_guard_push_captures_runner_reason
    等）与并发竞争两例（guard_race/guard_edge）漏网，跑测试即向生产
    logs/guard_alert.log 写入（10-21 01:12/01:29 两轮实测复现：dedup
    skip + state lock timeout 行混入生产取证面）。"""
    fp = str(tmp_path / "guard_alert.log")
    monkeypatch.setattr(ga, "LOG_PATH", fp)
    return fp


def _read_log(fp):
    with open(fp, encoding="utf-8") as f:
        return f.read()


def _seed_state(fp, key, ts):
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({key: {"ts": ts.strftime("%Y-%m-%d %H:%M:%S"), "key": key}}, f)


# ── 缺陷①：失败原因落盘 ────────────────────────────────────────────


def test_push_fail_reason_logged(state_fp, log_fp, monkeypatch):
    """push 失败 → 日志 `push fail key=… reason=<SEND_FAIL 原因>`；
    既有 `push fail key=` 前缀保持（监控 grep 兼容）。"""

    def _p(text):
        ga._last_fail_reason = "SEND_FAIL openapi:token_missing no lark-cli"
        return False

    monkeypatch.setattr(ga, "_push", _p)
    ok, _ = ga.guard_alert("StrongEod", REASON, send=True, state_path=state_fp, now=T0)
    assert not ok
    log = _read_log(log_fp)
    line = [l for l in log.splitlines() if "push fail" in l][-1]
    body = line.split("] ", 1)[1]  # 去掉 [时间戳] 前缀
    assert body.startswith("push fail key=")  # 兼容既有 grep
    assert "reason=SEND_FAIL openapi:token_missing no lark-cli" in body
    # 失败不写状态（下轮重试）
    assert not os.path.exists(state_fp)


def test_push_fail_reason_truncated_120(state_fp, log_fp, monkeypatch):
    """超长原因（SEND_FAIL stdout 摘要）截断 ≤120 字符。"""
    long_reason = "SEND_FAIL " + "x" * 300

    def _p(text):
        ga._last_fail_reason = long_reason
        return False

    monkeypatch.setattr(ga, "_push", _p)
    ga.guard_alert("StrongEod", REASON, send=True, state_path=state_fp, now=T0)
    log = _read_log(log_fp)
    seg = log.split("reason=")[1].split("（")[0]
    assert len(seg) <= 120
    assert seg == long_reason[:120]


def test_push_fail_reason_fallback(state_fp, log_fp, monkeypatch):
    """底座未落原因（桩/旧版 runner）→ reason 落 fallback，不留空。"""
    monkeypatch.setattr(ga, "_push", lambda text: False)
    ga.guard_alert("StrongEod", REASON, send=True, state_path=state_fp, now=T0)
    assert "reason=" in _read_log(log_fp)


def test_success_path_log_unchanged(state_fp, log_fp, fake_push_calls, monkeypatch):
    """成功路径：日志只有正常痕迹，无 push fail 行；dedup skip 格式不变。"""
    monkeypatch.setattr(ga, "_push", lambda text: fake_push_calls.append(text) or True)
    ok1, _ = ga.guard_alert("StrongEod", REASON, send=True, state_path=state_fp, now=T0)
    assert ok1
    # 11h 后同因 → dedup skip（格式与既有一致）
    ga.guard_alert(
        "StrongEod",
        REASON,
        send=True,
        state_path=state_fp,
        now=T0 + timedelta(hours=11),
    )
    log = _read_log(log_fp)
    assert "push fail" not in log
    assert "dedup skip key=guard_" in log
    assert "(sent 2026-10-19 06:30:00)" in log


def test_guard_push_captures_runner_reason(monkeypatch):
    """_push 底座联动：runner.push_feishu False + LAST_PUSH_FAIL 落账
    → ga._push 取回原因；True → 原因清空。"""
    import runner as _runner

    def _fail(text):
        _runner.LAST_PUSH_FAIL = "SEND_FAIL openapi:appSecret not str"
        return False

    monkeypatch.setattr(_runner, "push_feishu", _fail)
    assert ga._push("t") is False
    assert ga._last_fail_reason == "SEND_FAIL openapi:appSecret not str"

    monkeypatch.setattr(_runner, "push_feishu", lambda text: True)
    assert ga._push("t") is True
    assert ga._last_fail_reason == ""


# ── runner 透传最小改动验证 ─────────────────────────────────────────


def test_runner_push_fail_captures_send_fail(monkeypatch):
    """runner.push_feishu：子进程 stdout=SEND_FAIL → 返回 False 且
    LAST_PUSH_FAIL 落 SEND_FAIL 原因行。"""
    import runner as _runner

    class _R:  # subprocess.CompletedProcess 桩
        stdout = "SEND_FAIL openapi:token_missing no lark-cli\n"
        stderr = ""

    monkeypatch.setattr(_runner.subprocess, "run", lambda *a, **k: _R())
    assert _runner.push_feishu("t") is False
    assert _runner.LAST_PUSH_FAIL.startswith("SEND_FAIL openapi:token_missing")


def test_runner_push_fail_stderr_fallback_and_ok_clears(monkeypatch):
    """stdout 空 → 取 stderr 摘要；两通道全空 → no output 兜底；
    SEND_OK → LAST_PUSH_FAIL 清空。"""
    import runner as _runner

    class _Rerr:
        stdout = ""
        stderr = "lark-cli exited 1\nboom"

    monkeypatch.setattr(_runner.subprocess, "run", lambda *a, **k: _Rerr())
    assert _runner.push_feishu("t") is False
    assert _runner.LAST_PUSH_FAIL == "lark-cli exited 1\nboom"

    class _Rempty:
        stdout = ""
        stderr = ""

    monkeypatch.setattr(_runner.subprocess, "run", lambda *a, **k: _Rempty())
    assert _runner.push_feishu("t") is False
    assert _runner.LAST_PUSH_FAIL == "send_feishu no output"

    class _Rok:
        stdout = "SEND_OK\n"
        stderr = ""

    monkeypatch.setattr(_runner.subprocess, "run", lambda *a, **k: _Rok())
    assert _runner.push_feishu("t") is True
    assert _runner.LAST_PUSH_FAIL == ""


# ── 异常②：并发竞争（文件锁） ──────────────────────────────────────


@pytest.fixture
def fake_push_calls():
    return []


def test_concurrent_same_key_no_duplicate_send(state_fp, fake_push_calls, monkeypatch):
    """两线程同 key 并发告警 → 锁内重读去重，只发 1 次（修复前会双发）。"""
    bar = threading.Barrier(2)

    def _p(text):
        time.sleep(0.25)  # 拉宽竞争窗口
        fake_push_calls.append(text)
        return True

    monkeypatch.setattr(ga, "_push", _p)

    def _worker():
        bar.wait()
        ga.alert_once("guard_race", "t", send=True, state_path=state_fp, now=T0)

    ts = [threading.Thread(target=_worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(fake_push_calls) == 1
    st = json.load(open(state_fp, encoding="utf-8"))
    assert list(st.keys()) == ["guard_race"]
    assert not os.path.exists(state_fp + ".lock")


def test_concurrent_diff_keys_no_clobber(state_fp, monkeypatch):
    """两线程异 key 并发 → 锁内重读合入，互不覆写（修复前旧快照
    全量覆写会丢 key——即 10-14 guard_1331… 键消失的最可能成因）。"""
    bar = threading.Barrier(2)

    def _p(text):
        time.sleep(0.2)
        return True

    monkeypatch.setattr(ga, "_push", _p)

    def _worker(k):
        bar.wait()
        ga.alert_once(k, "t", send=True, state_path=state_fp, now=T0)

    ts = [
        threading.Thread(target=_worker, args=(k,)) for k in ("guard_aaa", "guard_bbb")
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    st = json.load(open(state_fp, encoding="utf-8"))
    assert set(st.keys()) == {"guard_aaa", "guard_bbb"}


# ── 超窗 / 12h 边界 ────────────────────────────────────────────────


def test_overwindow_resend(state_fp, fake_push_calls, monkeypatch):
    """预置 13h 前已发状态 → 超窗正确重发并刷新 ts（10-14 19:01 场景
    在当前代码下的正确行为：重发而非 skip）。"""
    _seed_state(state_fp, "guard_ow", T0 - timedelta(hours=13))
    monkeypatch.setattr(ga, "_push", lambda text: fake_push_calls.append(text) or True)
    ok, _ = ga.alert_once("guard_ow", "t", send=True, state_path=state_fp, now=T0)
    assert ok and len(fake_push_calls) == 1
    st = json.load(open(state_fp, encoding="utf-8"))
    assert st["guard_ow"]["ts"] == "2026-10-19 06:30:00"


def test_dedup_boundary_12h(state_fp, fake_push_calls, monkeypatch):
    """12h 去重边界：11:59:59 仍 skip；12:00:00 整点起重发。"""
    _seed_state(state_fp, "guard_edge", T0)
    monkeypatch.setattr(ga, "_push", lambda text: fake_push_calls.append(text) or True)
    ok1, _ = ga.alert_once(
        "guard_edge",
        "t",
        send=True,
        state_path=state_fp,
        now=T0 + timedelta(hours=11, minutes=59, seconds=59),
    )
    assert not ok1 and len(fake_push_calls) == 0
    ok2, _ = ga.alert_once(
        "guard_edge", "t", send=True, state_path=state_fp, now=T0 + timedelta(hours=12)
    )
    assert ok2 and len(fake_push_calls) == 1


# ── 自身故障不反噬 / 原子写 / 文件锁行为 ────────────────────────────


def test_alert_channel_fault_no_backfire(state_fp, log_fp, monkeypatch):
    """告警链自身故障（_push 抛错）→ 只打点不反噬：不上抛、不写状态。"""

    def _boom(text):
        raise RuntimeError("send channel exploded")

    monkeypatch.setattr(ga, "_push", _boom)
    ok, _ = ga.guard_alert("StrongEod", REASON, send=True, state_path=state_fp, now=T0)
    assert not ok
    assert "alert_once error key=guard_" in _read_log(log_fp)
    assert not os.path.exists(state_fp)


def test_state_atomic_write_no_tmp_leftover(state_fp, fake_push_calls, monkeypatch):
    """成功写状态：无 .tmp 残留、结构（ts/key 字段）不变、可解析。"""
    monkeypatch.setattr(ga, "_push", lambda text: fake_push_calls.append(text) or True)
    ga.guard_alert("StrongEod", REASON, send=True, state_path=state_fp, now=T0)
    assert not os.path.exists(state_fp + ".tmp")
    st = json.load(open(state_fp, encoding="utf-8"))
    ent = list(st.values())[0]
    assert set(ent.keys()) == {"ts", "key"}


def test_lock_stale_broken(state_fp, fake_push_calls, monkeypatch):
    """陈旧锁（持锁进程消亡残留，mtime 超龄）→ 强破后正常发送，
    锁文件最终清理。"""
    lock_fp = state_fp + ".lock"
    with open(lock_fp, "w") as f:
        f.write("dead")
    old = time.time() - ga._LOCK_STALE_S - 5
    os.utime(lock_fp, (old, old))
    monkeypatch.setattr(ga, "_push", lambda text: fake_push_calls.append(text) or True)
    ok, _ = ga.alert_once("guard_stale", "t", send=True, state_path=state_fp, now=T0)
    assert ok and len(fake_push_calls) == 1
    assert not os.path.exists(lock_fp)


def test_lock_timeout_yields(state_fp):
    """等锁超时 → 让步（yield False），告警可用性优先、不永久阻塞。"""
    lock_fp = state_fp + ".lock"
    with open(lock_fp, "w") as f:
        f.write("held")
    with ga._locked(state_fp, timeout_s=0.3) as got:
        assert got is False
    os.remove(lock_fp)


def test_lock_exclusive(state_fp):
    """锁互斥：持锁期间第二把锁必须拿不到（O_EXCL 语义）。"""
    with ga._locked(state_fp, timeout_s=0.3) as first:
        assert first is True
        with ga._locked(state_fp, timeout_s=0.3) as second:
            assert second is False
    # 释放后可重新获取
    with ga._locked(state_fp, timeout_s=0.3) as again:
        assert again is True
