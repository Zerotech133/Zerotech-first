# -*- coding: utf-8 -*-
"""R1 执行健康总线单测（联动层一期 T1，对齐 test_capability_mixin.py 判例）。

判例红线：PS 轮询在测试中一律 monkeypatch，绝不真调 Get-ScheduledTask
（CI 无 Windows 计划任务上下文）。manifest 路径 monkeypatch 注入 tmp 仓。

运行：python -m pytest portal/test_execution_mixin.py -q
"""

import json
import sys
import threading
import time
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


def _row(found=True, last_run=None, last_result=0):
    # 默认 last_run 取真实墙钟：build_execution_health 内部用 time 墙钟判
    # stale（cadence+grace=60min），硬编码日期会成时间炸弹（10-18 实证：
    # 09:00 硬编码 11 点跑全量变 stale 误红）。classify 直测显式传 NOW。
    if last_run is None:
        last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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


@pytest.fixture(autouse=True)
def _reset_warmer(monkeypatch):
    """预热线程隔离（D1 处置配套）：

    ① 默认 env 闸关闭——任何用例都不会误启真预热线程（判例：测试不读
    真仓也不启真线程）； warmer 专项用例自行 monkeypatch.setenv 开闸。
    ② 用例结束停掉可能被启起的线程并复位模块态，防跨用例串味。
    """
    monkeypatch.setenv(api_execution._WARMER_ENV, "0")
    yield
    th = api_execution._WARMER_THREAD
    if th is not None and th.is_alive():
        api_execution._WARMER_STOP.set()
        th.join(2.0)
    api_execution._WARMER_THREAD = None
    api_execution._WARMER_STOP = threading.Event()


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

    def test_upstream_untrusted_rc3_not_fail(self):
        """rc=3（上游不可信→主动拒跑）→ upstream_untrusted；不染红但确实没执行。"""
        e = api_execution.classify(_cfg(), _row(last_result=3), NOW)
        assert e["status"] == "upstream_untrusted"
        assert e["result_ok"] is False

    def test_cancelled_0xc000013a_not_fail(self):
        """0xC000013A（被取消/强杀）→ cancelled，不进 fail。"""
        e = api_execution.classify(_cfg(), _row(last_result=3221225786), NOW)
        assert e["status"] == "cancelled"
        assert e["result_ok"] is False

    def test_launcher_refused_0x800710e0_not_fail(self):
        """0x800710E0（启动器已在运行/请求被拒）→ launcher_refused，不进 fail。"""
        e = api_execution.classify(_cfg(), _row(last_result=2147946720), NOW)
        assert e["status"] == "launcher_refused"
        assert e["result_ok"] is False

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

    def test_summary_non_failure_codes_not_worst(self, tmp_manifest, monkeypatch):
        """非失败单列码（rc=3 / 0xC000013A / 0x800710E0）计数但不进 worst。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg(name="Up"), _cfg(name="Cancel"), _cfg(name="Launcher")]}),
            encoding="utf-8")

        def _fake(names, force=False):
            rows = {"Up": _row(last_result=3),
                    "Cancel": _row(last_result=3221225786),
                    "Launcher": _row(last_result=2147946720)}
            return {n: rows[n] for n in names}, True, None, 7

        monkeypatch.setattr(api_execution, "poll_tasks", _fake)
        s = api_execution.build_execution_health(force=True)["summary"]
        assert (s["upstream_untrusted"], s["cancelled"],
                s["launcher_refused"]) == (1, 1, 1)
        assert s["fail"] == 0
        assert s["worst"] is None

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

    def test_cache_expiry_swr_stale_then_refresh(self, tmp_manifest,
                                                 monkeypatch):
        """TTL 过期（SWR，T-P2）：前台立即返回旧值带 stale 标记不阻塞，
        后台单飞异步刷新，刷新完成后恢复新鲜（无 stale 标记）。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        called = {"n": 0}

        def _fake(names, force=False):
            called["n"] += 1
            return {names[0]: _row(last_result=267009 if called["n"] > 1
                                   else 0)}, True, None, 3

        monkeypatch.setattr(api_execution, "poll_tasks", _fake)
        api_execution.build_execution_health()
        api_execution._CACHE["ts"] -= 31  # 模拟时间流逝（缓存过期）
        d = api_execution.build_execution_health()
        assert d["stale"] is True                      # 前台零阻塞返回旧值
        assert d["cache_age_sec"] >= 31
        assert d["tasks"][0]["last_result"] == 0       # 是旧值（第一轮数据）
        # 后台单飞刷新异步完成（事件轮询等待，无固定 sleep）
        deadline = time.monotonic() + 5.0
        while called["n"] < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert called["n"] == 2
        # 缓存写入在 poll_tasks 返回之后，轮询等后台刷新落缓存再取（防竞态）
        d2 = api_execution.build_execution_health()
        deadline2 = time.monotonic() + 5.0
        while "stale" in d2 and time.monotonic() < deadline2:
            time.sleep(0.02)
            d2 = api_execution.build_execution_health()
        assert "stale" not in d2                       # 刷新后恢复新鲜
        assert d2["tasks"][0]["last_result"] == 267009

    def test_swr_background_refresh_single_flight(self, tmp_manifest,
                                                  monkeypatch):
        """SWR 单飞：并发多次过期命中只触发一次后台构建（省 PS 开销）。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        called = {"n": 0}

        def _slow(names, force=False):
            called["n"] += 1
            threading.Event().wait(0.15)
            return {names[0]: _row()}, True, None, 3

        monkeypatch.setattr(api_execution, "poll_tasks", _slow)
        api_execution.build_execution_health()
        api_execution._CACHE["ts"] -= 31
        api_execution.build_execution_health()   # 触发后台刷新 #1
        api_execution.build_execution_health()   # 单飞在跑 → 静默让位
        api_execution.build_execution_health()
        deadline = time.monotonic() + 5.0
        while called["n"] < 1 + 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        threading.Event().wait(0.05)             # 给让位线程留退出时间
        assert called["n"] == 2, "并发过期命中应单飞成一次后台构建: %d" % called["n"]

    def test_fresh_hit_has_no_stale_marker(self, tmp_manifest, monkeypatch):
        """TTL 内温缓存命中不带 stale 标记（契约只对过期命中附加）。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        monkeypatch.setattr(
            api_execution, "poll_tasks",
            lambda names, force=False: ({names[0]: _row()}, True, None, 3))
        first = api_execution.build_execution_health()
        assert "stale" not in first
        second = api_execution.build_execution_health()
        assert "stale" not in second

    def test_swr_bg_failure_keeps_stale_serving(self, tmp_manifest,
                                                monkeypatch, capsys):
        """SWR fail-open：后台刷新失败吞掉记 stderr，旧缓存仍可服务。"""
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        called = {"n": 0}

        def _boom(names, force=False):
            called["n"] += 1
            if called["n"] == 1:      # 首轮成功建缓存（生产 poll_tasks 不抛）
                return {names[0]: _row()}, True, None, 3
            raise RuntimeError("swr exploded")

        monkeypatch.setattr(api_execution, "poll_tasks", _boom)
        api_execution.build_execution_health()   # 冷启同步轮询 n=1（成功）
        assert api_execution._CACHE["data"] is not None
        api_execution._CACHE["ts"] -= 31
        d = api_execution.build_execution_health()   # 过期→返回旧值+触发后台
        assert d["stale"] is True
        # 轮询等后台刷新失败记 stderr（capsys 跨线程捕获 sys.stderr 全局替换）。
        # 等待期间保持缓存过期重踢后台刷新：单飞闸若被上一用例的残留线程
        # 短暂持有，kick 静默让位后需要下一次过期命中补位（幂等，无副作用）。
        deadline = time.monotonic() + 5.0
        err = ""
        while time.monotonic() < deadline:
            err += capsys.readouterr().err
            if "swr exploded" in err:
                break
            api_execution._CACHE["ts"] -= 31
            api_execution.build_execution_health()
            time.sleep(0.02)
        assert "swr exploded" in err, "后台异常未记 stderr: %r" % err
        assert api_execution._CACHE["data"] is not None  # 失败不毁旧缓存

    def test_return_deepcopy_no_cache_pollution(self, tmp_manifest,
                                                monkeypatch):
        """审查收口 2026-10-18：返回值必须与缓存深隔离。

        R3 watchdog 的 _apply_injections 会原地改返回载荷的 tasks 条目；
        若返回与缓存共享嵌套对象（历史版 dict() 浅拷贝），注入会污染
        30s TTL 内的下一次缓存命中。回归：改返回值后缓存命中不受影响。
        """
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        monkeypatch.setattr(
            api_execution, "poll_tasks",
            lambda names, force=False: ({names[0]: _row()}, True, None, 3))
        first = api_execution.build_execution_health()
        first["tasks"][0]["status"] = "fail"       # 模拟演练注入改写
        first["summary"]["fail"] = 99
        second = api_execution.build_execution_health()  # TTL 内走缓存
        assert second["tasks"][0]["status"] == "ok"
        assert second["summary"]["fail"] == 0

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
        # 兜底 summary 必须与新口径同 key（非失败单列码同样有计数位）
        assert h.payload["summary"]["upstream_untrusted"] == 0
        assert h.payload["summary"]["cancelled"] == 0
        assert h.payload["summary"]["launcher_refused"] == 0


# ----------------------------------------------------------------------
# 后台预热线程（D1 处置 2026-10-18）
# ----------------------------------------------------------------------
class TestWarmer:
    """预热线程懒启动/防重入/fail-open/温缓存命中。

    时序判例：monkeypatch CACHE_TTL 小值 → _warm_interval() 落到 0.05s
    下限，全部用事件/条件等待同步，无固定 sleep，总时长 <5s。
    """

    def test_lazy_start_only_once(self, tmp_manifest, monkeypatch):
        """懒启动：首个 build_execution_health 调用 spawn 一次，重复调用不重复。"""
        monkeypatch.setenv(api_execution._WARMER_ENV, "1")
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        monkeypatch.setattr(
            api_execution, "poll_tasks",
            lambda names, force=False: ({names[0]: _row()}, True, None, 1))
        api_execution.build_execution_health()
        t1 = api_execution._WARMER_THREAD
        assert t1 is not None and t1.is_alive() and t1.daemon
        api_execution.build_execution_health()  # 第二次调用不得再 spawn
        assert api_execution._WARMER_THREAD is t1

    def test_disabled_by_env_gate(self, tmp_manifest, monkeypatch):
        """env 闸关闭（默认测试态）→ 不 spawn 线程。"""
        monkeypatch.delenv(api_execution._WARMER_ENV, raising=False)
        monkeypatch.setenv(api_execution._WARMER_ENV, "0")
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        monkeypatch.setattr(
            api_execution, "poll_tasks",
            lambda names, force=False: ({names[0]: _row()}, True, None, 1))
        api_execution.build_execution_health()
        assert api_execution._WARMER_THREAD is None

    def test_background_refresh_serves_warm_cache(self, tmp_manifest,
                                                  monkeypatch):
        """温缓存命中：后台刷新更新 _CACHE 后，前台调用不触发同步 poll。

        后台轮次的行带 last_result=267009（良性运行中码）作指纹，
        前台命中即返回后台数据 → 证明前台消费的是后台预热结果。
        """
        monkeypatch.setenv(api_execution._WARMER_ENV, "1")
        monkeypatch.setattr(api_execution, "CACHE_TTL", 0.4)  # → 间隔 0.05s
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        called = {"n": 0}
        bg_refreshed = threading.Event()

        def _fake(names, force=False):
            called["n"] += 1
            rc = 0 if called["n"] == 1 else 267009  # 第 2 次=后台轮次指纹
            if called["n"] >= 2:
                bg_refreshed.set()
            return {names[0]: _row(last_result=rc)}, True, None, 1

        monkeypatch.setattr(api_execution, "poll_tasks", _fake)
        first = api_execution.build_execution_health()   # 冷启同步轮询 n=1
        assert first["tasks"][0]["last_result"] == 0
        assert bg_refreshed.wait(5.0)                    # 后台完成一轮 n=2
        # 停线程消除竞态：后续不再有后台刷新动 counter/缓存
        api_execution._WARMER_STOP.set()
        api_execution._WARMER_THREAD.join(2.0)
        # 保底新鲜（后台刷新时刻到此刻 < TTL=0.4s，此处显式刷新防调度抖动）
        api_execution._CACHE["ts"] = time.monotonic()
        called["n"] = 0
        d = api_execution.build_execution_health()       # 必须温缓存命中
        assert called["n"] == 0                          # 未触发同步 poll
        assert d["tasks"][0]["last_result"] == 267009    # 返回后台刷新数据

    def test_warmer_exception_swallowed(self, tmp_manifest, monkeypatch,
                                        capsys):
        """fail-open：后台刷新异常吞掉记 stderr，线程存活不冒泡。"""
        monkeypatch.setenv(api_execution._WARMER_ENV, "1")
        monkeypatch.setattr(api_execution, "CACHE_TTL", 0.2)  # → 间隔 0.05s
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")

        def _boom(names, force=False):
            raise RuntimeError("ps exploded")

        monkeypatch.setattr(api_execution, "poll_tasks", _boom)
        api_execution._ensure_warmer_started()  # 直接触发懒启动（绕开前台）
        th = api_execution._WARMER_THREAD
        assert th is not None and th.is_alive()
        # 轮询等 stderr 出现诊断（capsys 跨线程捕获 sys.stderr 全局替换）
        deadline = time.monotonic() + 5.0
        err = ""
        while time.monotonic() < deadline:
            err += capsys.readouterr().err
            if "ps exploded" in err:
                break
            time.sleep(0.02)
        assert "ps exploded" in err, "后台异常未记 stderr: %r" % err
        assert th.is_alive(), "后台异常不得终止预热线程"
        assert api_execution._CACHE["data"] is None  # 失败轮次不写坏缓存

    def test_warmer_stops_on_stop_event(self, tmp_manifest, monkeypatch):
        """stop 事件置位 → 线程优雅退出（夹具清理/未来停机依赖此语义）。"""
        monkeypatch.setenv(api_execution._WARMER_ENV, "1")
        tmp_manifest.write_text(json.dumps({"version": 1, "tasks": [
            _cfg()]}), encoding="utf-8")
        monkeypatch.setattr(
            api_execution, "poll_tasks",
            lambda names, force=False: ({names[0]: _row()}, True, None, 1))
        api_execution.build_execution_health()
        th = api_execution._WARMER_THREAD
        assert th is not None
        api_execution._WARMER_STOP.set()
        th.join(2.0)
        assert not th.is_alive()


# ----------------------------------------------------------------------
# _ps_query 批量查询（T-P2 治本：COM Schedule.Service 单连接）
# ----------------------------------------------------------------------
class TestPsQueryBatch:
    """_ps_query 批量查询逻辑验证——mock subprocess.run，绝不真调
    PowerShell（判例红线：测试不真跑 COM/计划任务查询，22 任务轮询
    不进测试路径）。"""

    def _patch_run(self, monkeypatch, stdout=b"", stderr=b"", returncode=0,
                   cmd_spy=None):
        class _Proc:
            def __init__(self):
                self.stdout = stdout
                self.stderr = stderr
                self.returncode = returncode

        def _fake_run(cmd, **kw):
            if cmd_spy is not None:
                cmd_spy(cmd)
            return _Proc()

        monkeypatch.setattr(api_execution.subprocess, "run", _fake_run)

    def test_command_is_single_process_batch(self, monkeypatch):
        """PS 命令必须是单进程批量查询：COM 单连接，不再逐任务 CIM 往返。"""
        out = json.dumps([
            {"name": "A", "found": True, "last_run": "", "result": 0},
            {"name": "B'x", "found": False, "last_run": "", "result": 0},
        ]).encode("utf-8")
        seen = {}
        self._patch_run(monkeypatch, stdout=out,
                        cmd_spy=lambda cmd: seen.update(cmd=cmd))
        api_execution._ps_query(["A", "B'x"])
        ps_cmd = seen["cmd"][4]  # [ps, -NoProfile, -NonInteractive, -Command, ps]
        assert "Schedule.Service" in ps_cmd, "缺 COM 单连接入口"
        assert "Get-ScheduledTask" not in ps_cmd, "仍残留逐任务 CIM 查询"
        assert "'A'=$true" in ps_cmd and "'B''x'=$true" in ps_cmd, "单引号未转义"
        assert seen["cmd"][0] == api_execution._PS and len(seen["cmd"]) == 5

    def test_parse_found_and_missing(self, monkeypatch):
        """found 行解析；missing 行 found=False 且 last_run=None（宁报勿瞒）。"""
        out = json.dumps([
            {"name": "A", "found": True,
             "last_run": "2026-10-19 09:00:00", "result": 0},
            {"name": "B", "found": False, "last_run": "", "result": 0},
        ]).encode("utf-8")
        self._patch_run(monkeypatch, stdout=out)
        rows = api_execution._ps_query(["A", "B"])
        assert rows["A"] == {"found": True,
                             "last_run": "2026-10-19 09:00:00",
                             "last_result": 0}
        assert rows["B"]["found"] is False
        assert rows["B"]["last_run"] is None

    def test_parse_single_dict_row(self, monkeypatch):
        """单任务时 ConvertTo-Json 输出对象而非数组 → 归一为单元素列表。"""
        out = json.dumps({"name": "A", "found": True,
                          "last_run": "2026-10-19 09:00:00",
                          "result": 267009}).encode("utf-8")
        self._patch_run(monkeypatch, stdout=out)
        rows = api_execution._ps_query(["A"])
        assert rows["A"]["last_result"] == 267009

    def test_negative_int32_result_normalized(self, monkeypatch):
        """COM LastTaskResult 是 int32：0xFFFFFFFF 以 -1 到达，须归一
        4294967295（良性码表外必报 fail，不能因负值解析炸掉）。"""
        out = json.dumps([{"name": "A", "found": True,
                           "last_run": "", "result": -1}]).encode("utf-8")
        self._patch_run(monkeypatch, stdout=out)
        rows = api_execution._ps_query(["A"])
        assert rows["A"]["last_result"] == 4294967295

    def test_nonzero_exit_raises(self, monkeypatch):
        """PS 非零退出 → RuntimeError（poll_tasks 统一转 ps_ok:false）。"""
        self._patch_run(monkeypatch, stderr=b"boom", returncode=1)
        with pytest.raises(RuntimeError):
            api_execution._ps_query(["A"])
