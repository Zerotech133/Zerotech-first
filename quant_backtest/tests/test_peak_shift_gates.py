# -*- coding: utf-8 -*-
"""错峰双闸门单测（K-INTEL-20261020-PEAKSHIFT 备稿，2026-10-20）。

闸门 1（xq_classify.peak_shift_gate）：XQ_PEAK_SHIFT=1 时 round 前探测让位，
busy 留痕 occupant PID，让位超时本轮跳过；默认关闭行为零变化。
闸门 2（k266_m3_structure.yield_to_classify_shifts）：K266_PEAK_SHIFT=1 时
避开雪球分类整点班窗口（08/09/10 ±20m），分片睡眠+心跳；默认关闭直通。
闸门 3（k266_m3_structure.probe_yield_gate，2026-11-04）：K266_PROBE_YIELD=1 时
每 60s 至多探一次延迟，busy（>6s，人类交互抢槽）→ 交还锁+睡 60s 重探，
累计让位超 1800s 或抢不回锁 → 本轮收工；默认关闭直通。

全部 mock 时间/busy 场景与 subprocess，不真调 Ollama、不碰真库、不真 sleep。
跑法：python -m pytest quant_backtest/tests/test_peak_shift_gates.py -q
"""
import json
import sys
import time as _time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xq_classify as xc
import k266_m3_structure as k266


# ── 闸门 1：xq_classify.peak_shift_gate ─────────────────────────────────

@pytest.fixture()
def logs(monkeypatch):
    lines = []
    monkeypatch.setattr(xc, "log", lines.append)
    return lines


def test_gate1_off_zero_change(monkeypatch, logs):
    """开关关闭（默认态）：直通放行，探测一次都不发，日志零新增。"""
    monkeypatch.delenv("XQ_PEAK_SHIFT", raising=False)

    def _no_probe(*a, **kw):
        raise AssertionError("gate off must not probe")

    assert xc.peak_shift_gate(probe=_no_probe) is True
    assert logs == []


def test_gate1_busy_yield_then_free(monkeypatch, logs):
    """开启 + busy：让位等待重试，JSON 留痕 occupant PID，空闲后放行。"""
    monkeypatch.setenv("XQ_PEAK_SHIFT", "1")
    probes = [(True, 40.0), (True, 35.2), (False, 0.8)]
    monkeypatch.setattr(xc, "ollama_probe_busy",
                        lambda thresh: probes.pop(0) if probes else (False, 0.8))
    monkeypatch.setattr(xc, "ollama_occupants",
                        lambda: ["k266_m3_structure.py pid=8200"])
    sleeps = []
    assert xc.peak_shift_gate(sleep=sleeps.append) is True
    assert len(sleeps) == 2
    js = [json.loads(l) for l in logs if l.startswith("{")]
    assert len(js) == 2
    assert js[0]["gate"] == "xq_peak_shift"
    assert js[0]["event"] == "busy_yield"
    assert js[0]["occupant"] == ["k266_m3_structure.py pid=8200"]
    assert js[0]["latency_s"] == 40.0
    assert js[0]["wait_left_s"] > 0


def test_gate1_wait_expired_skips_round(monkeypatch, logs):
    """开启 + busy 持续到让位时长耗尽：本轮跳过（返回 False），留 skip 决策。"""
    monkeypatch.setenv("XQ_PEAK_SHIFT", "1")
    monkeypatch.setenv("XQ_PEAK_SHIFT_WAIT_S", "600")
    monkeypatch.setenv("XQ_PEAK_SHIFT_POLL_S", "500")
    monkeypatch.setattr(xc, "ollama_probe_busy", lambda thresh: (True, 50.0))
    clock = {"t": 1000.0}

    def fake_now():
        return clock["t"]

    def fake_sleep(s):
        clock["t"] += s      # 每睡一次推进 500s，第三次即越过 deadline

    assert xc.peak_shift_gate(now=fake_now, sleep=fake_sleep) is False
    assert any("wait expired" in l for l in logs)
    assert any("skip round" in l for l in logs)


def test_gate1_run_short_circuits_when_gate_blocks(monkeypatch):
    """run() 集成：闸门拦截时不连库直接 rc=0（下一轮调度自愈）。"""
    monkeypatch.setenv("XQ_PEAK_SHIFT", "1")
    # 审计修复（2026-10-31）：本用例只验证闸门短路，不应依赖全局 ollama 锁；
    # 并行会话持锁时 acquire 会等满 120s，触发 pytest-timeout -> suite rc=1。
    monkeypatch.setenv("XQ_OLLAMA_LOCK", "0")
    monkeypatch.setattr(xc, "peak_shift_gate", lambda **kw: False)

    def _no_db(*a, **kw):
        raise AssertionError("blocked round must not touch DB")

    monkeypatch.setattr(xc.sqlite3, "connect", _no_db)
    assert xc.run(limit=10) == 0


def test_gate1_conf_invalid_env_falls_back(monkeypatch, logs):
    """非法 env 值回落默认（stderr 告警，不炸轮）。"""
    monkeypatch.setenv("XQ_PEAK_SHIFT", "1")
    monkeypatch.setenv("XQ_PEAK_SHIFT_THRESH_S", "abc")
    monkeypatch.setenv("XQ_PEAK_SHIFT_WAIT_S", "not-int")
    on, thresh, wait_s, poll_s = xc._peak_shift_conf()
    assert on is True
    assert (thresh, wait_s, poll_s) == (xc.PEAK_SHIFT_THRESH_S,
                                        xc.PEAK_SHIFT_WAIT_S,
                                        xc.PEAK_SHIFT_POLL_S)


def test_gate1_occupants_parse(monkeypatch):
    """netstat+tasklist 解析：只收客户端侧建连（Foreign=11434），排除 serve。"""
    netstat = "\n".join([
        "  Proto  Local Address        Foreign Address      State           PID",
        "  TCP    127.0.0.1:11434      127.0.0.1:52011      ESTABLISHED     10392",
        "  TCP    127.0.0.1:52011      127.0.0.1:11434      ESTABLISHED     8200",
        "  TCP    127.0.0.1:52012      127.0.0.1:11434      ESTABLISHED     36668",
        "  TCP    127.0.0.1:52013      127.0.0.1:11434      TIME_WAIT       99999",
    ])
    tasklist = "\n".join([
        '"ollama.exe","10392","Console","1","123,456 K"',
        '"python.exe","8200","Console","1","50,000 K"',
        '"python.exe","36668","Console","1","40,000 K"',
    ])

    def fake_run(cmd, **kw):
        out = netstat if cmd[0] == "netstat" else tasklist

        class R:
            stdout = out
        return R()

    monkeypatch.setattr(xc.subprocess, "run", fake_run)
    occ = xc.ollama_occupants()
    assert sorted(occ) == ["python.exe pid=36668", "python.exe pid=8200"]
    assert not any("ollama.exe" in o for o in occ)   # serve accept 侧被排除


def test_gate1_occupants_degrades_to_empty(monkeypatch):
    """netstat 失败/未安装：返回 [] 不阻塞让位决策。"""
    def boom(*a, **kw):
        raise OSError("no netstat")

    monkeypatch.setattr(xc.subprocess, "run", boom)
    assert xc.ollama_occupants() == []


# ── 闸门 2：k266_m3_structure.yield_to_classify_shifts ──────────────────

def _st(h, m, s=0):
    return _time.struct_time((2026, 10, 20, h, m, s, 1, 293, 0))


@pytest.mark.parametrize("tm,expect", [
    (_st(7, 39, 59), None),          # 缓冲前 1 秒：不在窗口
    (_st(7, 40, 0), 40 * 60),        # 缓冲沿（含）：进入 07:40-08:20 窗
    (_st(8, 0, 0), 20 * 60),         # 整点：剩 20 分钟
    (_st(8, 19, 59), 1.0),           # 窗尾前 1 秒
    (_st(8, 20, 0), None),           # 窗尾沿（半开）：已出窗
    (_st(9, 0, 0), 20 * 60),         # 第二班
    (_st(10, 5, 0), 15 * 60),        # 第三班
    (_st(11, 0, 0), None),           # 班外：直通
])
def test_gate2_window_boundaries(tm, expect):
    got = k266.classify_shift_wait_end(tm)
    if expect is None:
        assert got is None
    else:
        assert got == pytest.approx(expect, abs=1.0)


def test_gate2_off_zero_change(monkeypatch):
    """开关关闭（默认态）：直通，不睡不留痕。"""
    monkeypatch.delenv("K266_PEAK_SHIFT", raising=False)
    sleeps, beats = [], []
    k266.yield_to_classify_shifts(sleep=sleeps.append, heartbeat=lambda: beats.append(1))
    assert sleeps == [] and beats == []


def test_gate2_yield_in_window(monkeypatch):
    """开启 + 落窗：暂歇到窗尾（分片 ≤300s），每片心跳，JSON 留痕一次。"""
    monkeypatch.setenv("K266_PEAK_SHIFT", "1")
    clock = {"t": 7 * 3600 + 40 * 60.0}          # 07:40:00 起跑

    def fake_localtime():
        secs = int(clock["t"])
        return _st(secs // 3600, secs % 3600 // 60, secs % 60)

    sleeps, beats, lines = [], [], []
    k266.yield_to_classify_shifts(log=lines.append, heartbeat=lambda: beats.append(1),
                                  sleep=lambda s: clock.__setitem__(
                                      "t", clock["t"] + s) or sleeps.append(s),
                                  now=lambda: clock["t"], localtime=fake_localtime)
    assert all(s <= k266.PEAK_SHIFT_CHUNK_S for s in sleeps)   # 分片纪律
    assert len(beats) == len(sleeps)                           # 每片心跳
    assert fake_localtime().tm_hour == 8 and fake_localtime().tm_min >= 20  # 睡到窗尾
    js = [json.loads(l) for l in lines if l.startswith("{")]
    assert len(js) == 1
    assert js[0]["gate"] == "k266_peak_shift"
    assert js[0]["event"] == "yield_window"
    assert js[0]["window_hours"] == [8, 9, 10]
    assert js[0]["buffer_m"] == 20


def test_gate2_outside_window_direct(monkeypatch):
    """开启但班外：直通零睡眠。"""
    monkeypatch.setenv("K266_PEAK_SHIFT", "1")
    sleeps = []
    k266.yield_to_classify_shifts(sleep=sleeps.append,
                                  localtime=lambda: _st(11, 30, 0),
                                  now=lambda: 0.0)
    assert sleeps == []


def test_gate2_invalid_buffer_falls_back(monkeypatch):
    """非法缓冲 env 回落默认 20 分钟。"""
    monkeypatch.setenv("K266_PEAK_SHIFT", "1")
    monkeypatch.setenv("K266_PEAK_SHIFT_BUFFER_M", "abc")
    assert k266.classify_shift_wait_end(_st(8, 0, 0)) == pytest.approx(20 * 60, abs=1.0)


# ── 闸门 3：k266_m3_structure.probe_yield_gate（2026-11-04 探测让位）──────

class _FakeLock:
    """假锁：记录交还与重抢，可配重抢失败。"""

    def __init__(self, acquire_ok=True):
        self.released = 0
        self.acquired = []
        self.acquire_ok = acquire_ok

    def release(self):
        self.released += 1

    def acquire(self, purpose, wait_s=0):
        self.acquired.append((purpose, wait_s))
        return self.acquire_ok


def _fresh_state():
    return {"last": -1e9, "used": 0.0}


def test_gate3_off_zero_change(monkeypatch):
    """开关关闭（默认态）：直通放行，探测一次都不发。"""
    monkeypatch.delenv("K266_PROBE_YIELD", raising=False)

    def _no_probe(*a, **kw):
        raise AssertionError("gate3 off must not probe")

    assert k266.probe_yield_gate(probe=_no_probe, state=_fresh_state()) is True


def test_gate3_throttle_skips_recent(monkeypatch):
    """节流：距上次探测不足 EVERY_S 直接放行（探针开销摊薄）。"""
    monkeypatch.setenv("K266_PROBE_YIELD", "1")
    monkeypatch.setenv("K266_PROBE_YIELD_EVERY_S", "60")
    st = {"last": 1000.0, "used": 0.0}

    def _no_probe(*a, **kw):
        raise AssertionError("within throttle window must not probe")

    assert k266.probe_yield_gate(probe=_no_probe, now=lambda: 1030.0, state=st) is True


def test_gate3_busy_yield_release_lock_then_free(monkeypatch):
    """busy：交还锁 → 睡 POLL → 重抢锁 → 重探空闲放行，JSON 留痕。"""
    monkeypatch.setenv("K266_PROBE_YIELD", "1")
    monkeypatch.setenv("K266_PROBE_YIELD_POLL_S", "60")
    lock, sleeps, lines = _FakeLock(), [], []
    probes = [(True, 40.0), (False, 0.4)]
    clock = {"t": 1000.0}

    def fake_now():
        return clock["t"]

    def fake_sleep(s):
        clock["t"] += s
        sleeps.append(s)

    ok = k266.probe_yield_gate(
        lock=lock, probe=lambda th: probes.pop(0), sleep=fake_sleep, now=fake_now,
        log=lines.append, state=_fresh_state())
    assert ok is True
    assert sleeps == [60]
    assert lock.released == 1
    assert lock.acquired == [("k266_m3", 600)]
    js = [json.loads(l) for l in lines if l.startswith("{")]
    assert len(js) == 1
    assert js[0]["gate"] == "k266_probe_yield"
    assert js[0]["event"] == "busy_yield"
    assert js[0]["latency_s"] == 40.0
    assert js[0]["thresh_s"] == k266.PROBE_YIELD_THRESH_S
    assert js[0]["budget_left_s"] > 0


def test_gate3_budget_exhausted_stops_round(monkeypatch):
    """busy 持续到让位预算耗尽：返回 False（本轮收工），留 wait_expired。"""
    monkeypatch.setenv("K266_PROBE_YIELD", "1")
    monkeypatch.setenv("K266_PROBE_YIELD_POLL_S", "60")
    monkeypatch.setenv("K266_PROBE_YIELD_BUDGET_S", "120")
    lock, sleeps, lines = _FakeLock(), [], []
    clock = {"t": 1000.0}

    def fake_now():
        return clock["t"]

    def fake_sleep(s):
        clock["t"] += s
        sleeps.append(s)

    ok = k266.probe_yield_gate(
        lock=lock, probe=lambda th: (True, 50.0), sleep=fake_sleep, now=fake_now,
        log=lines.append, state=_fresh_state())
    assert ok is False
    assert sum(sleeps) == 120                      # 让位不超预算
    assert any("wait_expired" in l for l in lines)


def test_gate3_lock_lost_stops_round(monkeypatch):
    """让位期间锁被别人（xq/JEV）抢走且 600s 抢不回：本轮收工，留 lock_lost。"""
    monkeypatch.setenv("K266_PROBE_YIELD", "1")
    lock, lines = _FakeLock(acquire_ok=False), []
    ok = k266.probe_yield_gate(
        lock=lock, probe=lambda th: (True, 30.0), sleep=lambda s: None,
        now=lambda: 1000.0, log=lines.append, state=_fresh_state())
    assert ok is False
    assert any("lock_lost" in l for l in lines)


def test_gate3_invalid_env_falls_back(monkeypatch):
    """非法 env 回落默认阈值/间隔，不抛异常。"""
    monkeypatch.setenv("K266_PROBE_YIELD", "1")
    monkeypatch.setenv("K266_PROBE_YIELD_THRESH_S", "abc")
    on, thresh, poll_s, budget_s, every_s = k266._probe_conf()
    assert on is True
    assert thresh == k266.PROBE_YIELD_THRESH_S
    assert poll_s == k266.PROBE_YIELD_POLL_S
    assert budget_s == k266.PROBE_YIELD_BUDGET_S
    assert every_s == k266.PROBE_YIELD_EVERY_S


def test_probe_payload_shares_num_ctx(monkeypatch):
    """探测与主调用同 num_ctx（16384）+ num_predict=1：否则探针本身触发模型重载。"""
    seen = {}

    class _Resp:
        def read(self):
            return b"{}"

    class _Opener:
        def open(self, req, timeout=None):
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

    monkeypatch.setattr(k266.urllib.request, "build_opener", lambda *a, **kw: _Opener())
    busy, latency = k266.ollama_probe_busy(6.0)
    assert busy is False
    assert seen["body"]["options"]["num_ctx"] == k266.NUM_CTX == 16384
    assert seen["body"]["options"]["num_predict"] == 1
    assert seen["body"]["prompt"] == "ping"


def test_probe_exception_failsafe_busy(monkeypatch):
    """探测异常（超时/拒连）：按 busy 计，不硬挤。"""
    class _Opener:
        def open(self, req, timeout=None):
            raise TimeoutError("probe timeout")

    monkeypatch.setattr(k266.urllib.request, "build_opener", lambda *a, **kw: _Opener())
    busy, latency = k266.ollama_probe_busy(6.0, timeout_s=10)
    assert busy is True
    assert latency <= 10

