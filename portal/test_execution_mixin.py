# -*- coding: utf-8 -*-
"""R1 执行健康总线单测（联动层一期 T1，对齐 test_capability_mixin.py 判例）。

判例红线：PS 轮询在测试中一律 monkeypatch，绝不真调 Get-ScheduledTask
（CI 无 Windows 计划任务上下文）。manifest 路径 monkeypatch 注入 tmp 仓。

运行：python -m pytest portal/test_execution_mixin.py -q
"""

import json
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import api_execution  # noqa: E402


# ----------------------------------------------------------------------
# 夹具与桩
# ----------------------------------------------------------------------
def _stub_handler():
    """最小 Handler 桩：只补 mixin 依赖的 _json（多继承主 Handler 兜底）。"""

    class _Stub(api_execution.Handler):
        def __init__(self):
            self.payload = None
            self.status = 200

        def _json(self, obj, status=200):
            self.payload = obj
            self.status = status

    return _Stub()


def _cfg(**kw):
    """manifest 条目基准构造器。"""
    base = {"name": "T1Probe", "cadence_minutes": 30, "grace_minutes": 30,
            "enabled": True}
    base.update(kw)
    return base


def _row(found=True, last_run="2026-10-18 09:00:00", last_result=0):
    return {"found": found, "last_run": last_run, "last_result": last_result}


@pytest.fixture()
def tmp_manifest(tmp_path, monkeypatch):
    """tmp manifest 注入：绝不读真实 execution_manifest.json。"""
    p = tmp_path / "execution_manifest.json"
    monkeypatch.setattr(api_execution, "MANIFEST_PATH", p)
    # 顺手清缓存，防跨用例串味
    monkeypatch.setattr(api_execution, "_CACHE", {"ts": 0.0, "data": None})
    return p


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """每个用例独立缓存（monkeypatch.setattr 还原，不污染真实模块态）。"""
    monkeypatch.setattr(api_execution, "_CACHE", {"ts": 0.0, "data": None})


NOW = datetime(2026, 10, 18, 10, 0, 0)


# ----------------------------------------------------------------------
# classify 状态机
# ----------------------------------------------------------------------
class TestClassify:
    def test_fail_on_bad_rc(self):
        """last_result=1 → fail（良性码表外的码必报 bad）。"""
        e = api_execution.classify(_cfg(), _row(last_result=1), NOW)
        assert e["status"] == "fail"
        assert e["result_ok"] is False

    def test_fail_on_4294967295(self):
        """0xFFFFFFFF 必报 bad（共享约定 §8.3）。"""
        e = api_execution.classify(_cfg(), _row(last_result=4294967295), NOW)
        assert e["status"] == "fail"

    def test_never_on_267011_sentinel(self):
        """267011 + 1999 哨兵 → never（从未运行，灰色不是 fail）。"""
        e = api_execution.classify(
            _cfg(), _row(last_run="1999-11-30 00:00:00", last_result=267011),
            NOW)
        assert e["status"] == "never"
        assert e["result_ok"] is True

    def test_267011_fresh_run_is_ok(self):
        """267011 但 LastRunTime 是近期（任务在跑只是 rc 停留）→ 不判 never。"""
        e = api_execution.classify(
            _cfg(), _row(last_run="2026-10-18 09:50:00", last_result=267011),
            NOW)
        assert e["status"] == "ok"

    def test_stale_boundary_exact_is_ok(self):
        """stale 边界：now-last_run == cadence+grace 恰好不判 stale。"""
        last = NOW - timedelta(minutes=60)  # 30+60 恰好边界
        e = api_execution.classify(_cfg(cadence_minutes=30, grace_minutes=30),
                                   _row(last_run=last.strftime("%Y-%m-%d %H:%M:%S")),
                                   NOW)
        assert e["stale_min"] == 60
        assert e["status"] == "ok"

    def test_stale_boundary_plus_one_is_stale(self):
        """stale 边界 +1 分钟 → stale。"""
        last = NOW - timedelta(minutes=61)
        e = api_execution.classify(_cfg(), _row(
            last_run=last.strftime("%Y-%m-%d %H:%M:%S")), NOW)
        assert e["status"] == "stale"

    def test_disabled_first_priority(self):
        """enabled=false → disabled（最高优先级，灰态不入 summary 红绿）。"""
        e = api_execution.classify(_cfg(enabled=False),
                                   _row(last_result=1), NOW)  # rc 坏也只灰
        assert e["status"] == "disabled"

    def test_missing_task_is_never(self):
        """manifest 内任务在系统中不存在 → ps_ok:true 但 status=never（宁报勿瞒）。"""
        e = api_execution.classify(_cfg(), _row(found=False), NOW)
        assert e["status"] == "never"
        assert e["in_system"] is False
        assert e["last_run"] is None

    def test_no_ps_row_is_never(self):
        """ps_row=None（PS 没返回该任务）→ never，不抛异常。"""
        e = api_execution.classify(_cfg(), None, NOW)
        assert e["status"] == "never"

    def test_future_last_run_no_negative_stale(self):
        """时钟漂移：last_run 在未来 → stale_min 不为负，判 ok。"""
        last = NOW + timedelta(minutes=5)
        e = api_execution.classify(_cfg(), _row(
            last_run=last.strftime("%Y-%m-%d %H:%M:%S")), NOW)
        assert e["status"] == "ok"
        assert e["stale_min"] == 0

    def test_fail_scan_hit_reported_not_status(self):
        """fail_scan 命中不改变 status（rc 面归 rc），单独 fail_scan_hit 输出。"""
        e = api_execution.classify(_cfg(), _row(), NOW)
        assert e["status"] == "ok"
        # 命中与否由 _log_state 决定，此处验证字段存在且布尔
        assert isinstance(e["fail_scan_hit"], bool)


# ----------------------------------------------------------------------
# fail_scan / 日志
# ----------------------------------------------------------------------
class TestFailScan:
    def test_fail_scan_hit(self, tmp_path):
        log = tmp_path / "aw-stack.log"
        log.write_text("ok line\n2026-10-18 FAILED: drill\n", encoding="utf-8")
        cfg = _cfg(fail_scan={"log": str(log), "pattern": "FAILED:",
                              "window_hours": 24})
        _, hit = api_execution._log_state(cfg)
        assert hit is True

    def test_fail_scan_window_expiry(self, tmp_path):
        """日志超 window_hours → 不命中（陈旧命中不算在警）。"""
        import os
        log = tmp_path / "aw-stack.log"
        log.write_text("FAILED: old\n", encoding="utf-8")
        old = (datetime.now() - timedelta(hours=48)).timestamp()
        os.utime(log, (old, old))
        cfg = _cfg(fail_scan={"log": str(log), "pattern": "FAILED:",
                              "window_hours": 24})
        _, hit = api_execution._log_state(cfg)
        assert hit is False

    def test_fail_scan_bad_bytes_replaced(self, tmp_path):
        """GBK 脏字节日志用 errors=replace 读，不炸（§8.4⑤ 判例）。"""
        log = tmp_path / "aw-stack.log"
        log.write_bytes(b"FAILED: \xd6\xd0\xce\xc4\n")  # GBK 中文
        cfg = _cfg(fail_scan={"log": str(log), "pattern": "FAILED:",
                              "window_hours": 24})
        _, hit = api_execution._log_state(cfg)
        assert hit is True

    def test_log_mtime_missing_file(self, tmp_path):
        cfg = _cfg(log_path=str(tmp_path / "no_such.log"))
        mtime, hit = api_execution._log_state(cfg)
        assert mtime is None
        assert hit is False


# ----------------------------------------------------------------------
# manifest 加载
# ----------------------------------------------------------------------
class TestManifest:
    def test_missing_manifest_fail_visible(self, tmp_manifest):
        tmp_manifest.parent.mkdir(parents=True, exist_ok=True)
        tasks, ok, err = api_execution.load_manifest()
        assert tasks == [] and ok is False and err

    def test_bad_json_fail_visible(self, tmp_manifest):
        tmp_manifest.write_text("{broken json", encoding="utf-8")
        tasks, ok, err = api_execution.load_manifest()
        assert tasks == [] and ok is False and err

    def test_bad_rows_skipped(self, tmp_manifest):
        """坏行（缺 name）跳过不拖垮整份清单（宁少勿假）。"""
        tmp_manifest.write_text(json.dumps({
            "version": 1, "tasks": [
                {"name": "A", "cadence_minutes": 5},
                {"cadence_minutes": 5},           # 缺 name → 跳过
                "not-a-dict",                     # 非对象 → 跳过
            ]}), encoding="utf-8")
        tasks, ok, _ = api_execution.load_manifest()
        assert ok is True and [t["name"] for t in tasks] == ["A"]


# ----------------------------------------------------------------------
# build_execution_health 聚合
# ----------------------------------------------------------------------
class TestBuildHealth:
    def test_ps_failure_fail_visible(self, tmp_manifest, monkeypatch):
        """PS 子进程注入失败 → ps_ok:false 不编造数据，HTTP 仍 200 语义。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")

        def _boom(names, force=False):
            return {}, False, "TimeoutExpired: ps", 5

        monkeypatch.setattr(api_execution, "poll_tasks", _boom)
        d = api_execution.build_execution_health(force=True)
        assert d["ps_ok"] is False and d["ps_error"]
        # ps 挂时拿不到行 → 该任务按 never 宁报勿瞒（而非编造 ok）
        assert d["tasks"][0]["status"] == "never"

    def test_ps_failure_rows_empty(self, tmp_manifest, monkeypatch):
        """ps_ok:false 时绝不带编造的任务行。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        monkeypatch.setattr(
            api_execution, "poll_tasks",
            lambda names, force=False: ({}, False, "err", 1))
        d = api_execution.build_execution_health(force=True)
        assert d["ps_ok"] is False
        # ps 挂时 rows={} → classify 拿不到行 → never（宁报勿瞒而非编造 ok）
        assert d["tasks"][0]["status"] == "never"

    def test_manifest_broken_no_poll(self, tmp_manifest, monkeypatch):
        """manifest 坏 JSON → manifest_ok:false，不发起 PS 轮询。"""
        tmp_manifest.write_text("{broken", encoding="utf-8")
        called = {"n": 0}

        def _spy(names, force=False):
            called["n"] += 1
            return {}, True, None, 0

        monkeypatch.setattr(api_execution, "poll_tasks", _spy)
        d = api_execution.build_execution_health(force=True)
        assert d["manifest_ok"] is False and called["n"] == 0
        assert d["summary"]["worst"] is None

    def test_summary_and_worst(self, tmp_manifest, monkeypatch):
        """worst 优先级 fail > stale > never（红>黄>灰）。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg(name="A"), _cfg(name="B"), _cfg(name="C")]}),
            encoding="utf-8")

        def _fake(names, force=False):
            rows = {
                "A": _row(last_result=1),                              # fail
                "B": _row(last_run=(NOW - timedelta(minutes=999)).strftime(
                    "%Y-%m-%d %H:%M:%S")),                             # stale
                "C": _row(last_run="1999-11-30 00:00:00",
                          last_result=267011),                         # never
            }
            return {n: rows.get(n, _row()) for n in names}, True, None, 7

        monkeypatch.setattr(api_execution, "poll_tasks", _fake)
        d = api_execution.build_execution_health(force=True)
        s = d["summary"]
        assert (s["fail"], s["stale"], s["never"]) == (1, 1, 1)
        assert s["worst"] == "fail"

        # 摘掉 fail 行 → worst 降为 stale
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg(name="B"), _cfg(name="C")]}), encoding="utf-8")
        d = api_execution.build_execution_health(force=True)
        assert d["summary"]["worst"] == "stale"

    def test_cache_hit_within_ttl(self, tmp_manifest, monkeypatch):
        """缓存 30s 生效：两次调用 ps 只跑一次。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        called = {"n": 0}

        def _fake(names, force=False):
            called["n"] += 1
            return {names[0]: _row()}, True, None, 3

        monkeypatch.setattr(api_execution, "poll_tasks", _fake)
        api_execution.build_execution_health()
        api_execution.build_execution_health()
        assert called["n"] == 1
        api_execution.build_execution_health(force=True)  # force 绕缓存
        assert called["n"] == 2

    def test_cache_expiry_after_ttl(self, tmp_manifest, monkeypatch):
        """TTL 过期后重新轮询。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        called = {"n": 0}

        def _fake(names, force=False):
            called["n"] += 1
            return {names[0]: _row()}, True, None, 3

        monkeypatch.setattr(api_execution, "poll_tasks", _fake)
        api_execution.build_execution_health()
        api_execution._CACHE["ts"] -= 31  # 模拟时间流逝
        api_execution.build_execution_health()
        assert called["n"] == 2

    def test_concurrent_no_race(self, tmp_manifest, monkeypatch):
        """并发 Lock 无竞态：PS 只跑一次，全部拿到一致载荷。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        called = {"n": 0}
        lk = threading.Lock()

        def _slow(names, force=False):
            with lk:
                called["n"] += 1
            threading.Event().wait(0.05)
            return {names[0]: _row()}, True, None, 50

        monkeypatch.setattr(api_execution, "poll_tasks", _slow)
        outs = []

        def worker():
            outs.append(api_execution.build_execution_health())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert called["n"] == 1
        assert all(o["tasks"][0]["name"] == "T1Probe" for o in outs)


# ----------------------------------------------------------------------
# Handler mixin
# ----------------------------------------------------------------------
class TestHandlerMixin:
    def test_mixin_returns_200_on_ps_failure(self, tmp_manifest, monkeypatch):
        """总线挂 ≠ 执行面挂：ps_ok:false 时仍走 _json（HTTP 200 语义）。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        monkeypatch.setattr(
            api_execution, "poll_tasks",
            lambda names, force=False: ({}, False, "boom", 1))
        h = _stub_handler()
        h._execution_health("")
        assert h.status == 200
        assert h.payload["ps_ok"] is False
        assert h.payload["read_only"] is True

    def test_mixin_force_flag(self, tmp_manifest, monkeypatch):
        """?force=1 透传给 build_execution_health。"""
        seen = {"force": None}

        def _fake(force=False):
            seen["force"] = force
            return {"read_only": True, "summary": {}}

        monkeypatch.setattr(api_execution, "build_execution_health", _fake)
        h = _stub_handler()
        h._execution_health("caller=x&force=1")
        assert seen["force"] is True

    def test_mixin_catches_unexpected(self, tmp_manifest, monkeypatch):
        """未捕获异常兜底 fail-visible：返回 manifest_ok:false 载荷不炸。"""
        def _boom(force=False):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(api_execution, "build_execution_health", _boom)
        h = _stub_handler()
        h._execution_health("")
        assert h.status == 200
        assert h.payload["manifest_ok"] is False
