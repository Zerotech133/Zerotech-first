# -*- coding: utf-8 -*-
"""R3 失败必响巡检单测（联动层一期 T3）。

判例红线：PS 轮询一律 monkeypatch（绝不真调 Get-ScheduledTask）；
TaskStore 临时库注入（run_shadow 测试法）；飞书推送 monkeypatch 不真发。

运行：python -m pytest tools/test_execution_watchdog.py -q
"""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import execution_watchdog as exwd  # noqa: E402


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
@pytest.fixture()
def tmp_state(tmp_path, monkeypatch):
    """节流状态目录注入 tmp（不写真实 automation_runs）。"""
    d = tmp_path / "automation_runs" / "execution_watchdog"
    monkeypatch.setattr(exwd, "STATE_DIR", d)
    return d


@pytest.fixture()
def tmp_store(tmp_path, monkeypatch):
    """TaskStore 临时库注入：open_ticket 读 exwd._REPO 反推路径，
    这里 monkeypatch _REPO 指向 tmp 仓并搭出 engine/tasks.py 同款环境。
    真 TaskStore(agent_platform/engine/tasks.py) 无第三方依赖，直接复用。"""
    fake_repo = tmp_path / "repo"
    (fake_repo / "agent_platform" / "data" / "org" / "tasks").mkdir(
        parents=True)
    # engine path 注入（与 run_shadow 同法）：真 engine/tasks.py 直接用
    engine = Path(exwd.__file__).resolve().parent.parent / "agent_platform" / "engine"
    monkeypatch.setattr(exwd, "_REPO", fake_repo)
    monkeypatch.syspath_prepend(str(engine))
    return fake_repo


def _entry(name="T1Probe", status="fail", last_result=1, stale_min=None,
           fail_scan_hit=False):
    return {"name": name, "status": status, "last_result": last_result,
            "stale_min": stale_min, "fail_scan_hit": fail_scan_hit,
            "last_run": "2026-10-18 09:00:00",
            "expected_cadence": "每 30 分钟", "log_mtime": None}


def _payload(entries):
    return {"ps_ok": True, "manifest_ok": True, "tasks": entries}


# ----------------------------------------------------------------------
# 节流
# ----------------------------------------------------------------------
class TestThrottle:
    def test_no_state_not_throttled(self, tmp_state):
        assert exwd.throttled("A", 1) is False

    def test_same_code_throttled(self, tmp_state):
        exwd._mark_alerted("A", 1, "T-1")
        assert exwd.throttled("A", 1) is True

    def test_code_change_realerts(self, tmp_state):
        """error_code 变化 → 立即再响。"""
        exwd._mark_alerted("A", 1, "T-1")
        assert exwd.throttled("A", 2) is False

    def test_expired_window(self, tmp_state):
        """超 24h 窗口 → 不再节流。"""
        from datetime import datetime, timedelta
        exwd._mark_alerted("A", 1, "T-1",
                           now=datetime.now() - timedelta(hours=25))
        assert exwd.throttled("A", 1) is False

    def test_bad_state_json_not_throttled(self, tmp_state):
        """坏 JSON 状态 = 未节流（宁重报勿漏报）。"""
        tmp_state.mkdir(parents=True, exist_ok=True)
        (tmp_state / "A.json").write_text("{broken", encoding="utf-8")
        assert exwd.throttled("A", 1) is False


# ----------------------------------------------------------------------
# 异常归并与措辞
# ----------------------------------------------------------------------
class TestAnomalies:
    def test_fail_stale_failscan_selected(self):
        payload = _payload([
            _entry(name="A", status="fail"),
            _entry(name="B", status="stale", last_result=0, stale_min=999),
            _entry(name="C", status="ok", fail_scan_hit=True),
            _entry(name="D", status="ok"),
            _entry(name="E", status="never", last_result=267011),
            _entry(name="F", status="disabled"),
        ])
        names = [e["name"] for e in exwd._anomalies(payload)]
        assert names == ["A", "B", "C"]

    def test_error_code_mapping(self):
        assert exwd._error_code_of(_entry(status="fail", last_result=7)) == 7
        assert exwd._error_code_of(_entry(status="stale")) == "STALE"
        assert exwd._error_code_of(_entry(status="ok",
                                          fail_scan_hit=True)) == "FAIL_SCAN"

    def test_fmt_alert_template(self):
        text = exwd._fmt_alert(_entry(name="X", status="fail",
                                      last_result=1), "已开单：T-1")
        assert "🚨 [P0] 执行失败必响：X" in text
        assert "LastTaskResult=1 非0" in text
        assert "已开单：T-1" in text
        assert "24h 内不重复推送" in text


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
class TestMain:
    def _run(self, monkeypatch, payload, push=None, **kw):
        monkeypatch.setattr(exwd.api_execution, "build_execution_health",
                            lambda force=False: payload)
        pushed = {"n": 0, "texts": []}

        def _fake_push(text):
            pushed["n"] += 1
            pushed["texts"].append(text)
            return push if push is not None else True

        monkeypatch.setattr(exwd, "push_feishu", _fake_push)
        code = exwd.main(kw.get("argv", []))
        return code, pushed

    def test_fail_alerts_once_and_opens_ticket(self, tmp_state, tmp_store,
                                               monkeypatch):
        """fail+未节流 → 推送+开单各一次，工单落 dev 部门 P0 doing。"""
        code, pushed = self._run(
            monkeypatch, _payload([_entry(name="ProbeA")]))
        assert code == 0
        assert pushed["n"] == 1
        lr = json.loads((tmp_state / "last_run.json").read_text(
            encoding="utf-8"))
        assert lr["exit"] == 0 and lr["alerted"][0]["ticket"]
        # 工单真实落账（org tasks jsonl）
        files = list((tmp_store / "agent_platform" / "data" / "org" /
                      "tasks").glob("tasks_*.jsonl"))
        assert files and "计划任务失败必响：ProbeA" in files[0].read_text(
            encoding="utf-8")

    def test_same_code_second_run_skipped(self, tmp_state, tmp_store,
                                          monkeypatch):
        """同任务同码 24h 内第二次 → 节流跳过，不推送不开单。"""
        payload = _payload([_entry(name="ProbeA")])
        self._run(monkeypatch, payload)
        code, pushed = self._run(monkeypatch, payload)
        assert code == 0 and pushed["n"] == 0
        lr = json.loads((tmp_state / "last_run.json").read_text(
            encoding="utf-8"))
        assert lr["skipped_throttle"] and lr["alerted"] == []

    def test_error_code_change_realerts(self, tmp_state, tmp_store,
                                        monkeypatch):
        """error_code 变化（rc 1→2）→ 立即再响。"""
        self._run(monkeypatch, _payload([_entry(name="ProbeA",
                                                last_result=1)]))
        code, pushed = self._run(
            monkeypatch, _payload([_entry(name="ProbeA", last_result=2)]))
        assert code == 0 and pushed["n"] == 1

    def test_stale_and_failscan_alert(self, tmp_state, tmp_store,
                                      monkeypatch):
        payload = _payload([
            _entry(name="S", status="stale", last_result=0, stale_min=999),
            _entry(name="F", status="ok", fail_scan_hit=True),
        ])
        code, pushed = self._run(monkeypatch, payload)
        assert code == 0 and pushed["n"] == 2
        assert "超期未跑" in pushed["texts"][0]
        # 断点7 收口 2026-10-18：fail_scan 措辞去「自愈 FAILED: 良性」专属化
        assert "fail_scan 日志命中失败标记" in pushed["texts"][1]

    def test_taskstore_failure_not_blocking_push(self, tmp_state, tmp_store,
                                                 monkeypatch):
        """TaskStore 异常不中断推送（开单 fail-open 判例）。"""
        import tasks as tasks_mod
        def _boom(*a, **kw):
            raise RuntimeError("store down")
        monkeypatch.setattr(tasks_mod.TaskStore, "open",
                            lambda self, *a, **kw: _boom())
        code, pushed = self._run(monkeypatch, _payload([_entry(name="X")]))
        assert pushed["n"] == 1  # 推送照发

    def test_send_feishu_failure_exit1_no_throttle_write(self, tmp_state,
                                                        tmp_store,
                                                        monkeypatch):
        """推送失败 → exit 1（fail-visible）且不写节流（下次重试响）。"""
        code, pushed = self._run(monkeypatch,
                                 _payload([_entry(name="X")]), push=False)
        assert code == 1
        assert not (tmp_state / "X.json").exists()

    def test_dry_run_no_side_effects(self, tmp_state, tmp_store,
                                     monkeypatch):
        """--dry-run 零写副作用：不写 last_run.json/节流状态，不开单不推送。"""
        code, pushed = self._run(
            monkeypatch, _payload([_entry(name="X")]),
            argv=["--dry-run"])
        assert code == 0 and pushed["n"] == 0
        assert list(tmp_state.glob("*.json")) == []  # 含 last_run.json
        assert not list((tmp_store / "agent_platform" / "data" / "org" /
                         "tasks").glob("tasks_*.jsonl"))

    def test_dry_run_ps_failure_writes_nothing(self, tmp_state, monkeypatch):
        """QA 风险①收口：dry-run + 总线挂 也不写 last_run.json（不喂假心跳）。"""
        code, pushed = self._run(
            monkeypatch, {"ps_ok": False, "ps_error": "boom", "tasks": []},
            argv=["--dry-run"])
        assert code == 1 and pushed["n"] == 0
        assert list(tmp_state.glob("*.json")) == []

    def test_ps_failure_exit1(self, tmp_state, monkeypatch):
        """总线轮询失败 → exit 1 且 last_run.json 照写（fail-visible）。"""
        code, pushed = self._run(
            monkeypatch, {"ps_ok": False, "ps_error": "boom", "tasks": []})
        assert code == 1 and pushed["n"] == 0
        lr = json.loads((tmp_state / "last_run.json").read_text(
            encoding="utf-8"))
        assert lr["exit"] == 1

    def test_bad_manifest_exit1(self, tmp_state, monkeypatch):
        code, _ = self._run(
            monkeypatch, {"ps_ok": True, "manifest_ok": False,
                          "manifest_error": "bad", "tasks": []})
        assert code == 1

    def test_inject_overrides_status(self, tmp_state, tmp_store,
                                     monkeypatch):
        """--inject 构造 fail 场景：rc=1 注入后按 fail 响。"""
        payload = _payload([_entry(name="DrillProbe", status="ok",
                                   last_result=0)])
        code, pushed = self._run(
            monkeypatch, payload,
            argv=["--inject", "DrillProbe", "1"])
        assert code == 0 and pushed["n"] == 1
        assert "DrillProbe" in pushed["texts"][0]

    def test_inject_benign_no_alert(self, tmp_state, tmp_store,
                                    monkeypatch):
        """--inject 良性 rc（0）→ 不响（解除验证）。"""
        payload = _payload([_entry(name="DrillProbe", status="fail",
                                   last_result=1)])
        code, pushed = self._run(
            monkeypatch, payload,
            argv=["--inject", "DrillProbe", "0"])
        assert code == 0 and pushed["n"] == 0

    def test_inject_benign_clears_fail_scan_hit(self, tmp_state, tmp_store,
                                                monkeypatch):
        """审查收口 2026-10-18：注入良性 rc 后 fail_scan_hit 必须清零、
        result_ok 随 rc 重算——否则 status=ok 但残留 fail_scan_hit 仍会被
        _anomalies 当 FAIL_SCAN 误报（演练通道与真实哑火混流）。"""
        payload = _payload([_entry(name="DrillProbe", status="ok",
                                   last_result=0, fail_scan_hit=True)])
        code, pushed = self._run(
            monkeypatch, payload,
            argv=["--inject", "DrillProbe", "0"])
        assert code == 0 and pushed["n"] == 0
        entry = payload["tasks"][0]
        assert entry["fail_scan_hit"] is False
        assert entry["result_ok"] is True
        assert entry["status"] == "ok"

    def test_never_never_alerts(self, tmp_state, tmp_store, monkeypatch):
        """never（尚未首跑，影子链常态）不是异常，不推送。"""
        code, pushed = self._run(
            monkeypatch,
            _payload([_entry(name="Shadow", status="never",
                             last_result=267011)]))
        assert code == 0 and pushed["n"] == 0


# ----------------------------------------------------------------------
# 断点7 收口（2026-10-18）：manifest fail_scan 契约 + 聚合路径（tmp 注入）
# ----------------------------------------------------------------------
class TestManifestFailScan:
    """portal/execution_manifest.json fail_scan 统一口径契约。

    判例（研究报告 L157）：断点7 = 分散日志统一入总线心跳——有明确成败
    日志语义的任务必须配 fail_scan={log, pattern, window_hours}，纯触发型
    任务不配。这里直读真实 manifest 做契约验证（解析容错：坏类型/缺键
    一律 FAIL 而非静默通过）。
    """

    @staticmethod
    def _load_manifest():
        mp = Path(exwd._REPO) / "portal" / "execution_manifest.json"
        return json.loads(mp.read_text(encoding="utf-8"))

    #: 断点7 收口后应配 fail_scan 的任务全集（名称 → 期望 pattern）
    EXPECTED_FAIL_SCAN = {
        "AgentWatchdog": "FAILED:",
        "AgentShadowDaily": "Traceback (most recent call last)",
        "AgentMondayCheckpoint": "EXIT=1",
        "AgentChiefPush": "SEND_FAIL",
        "AgentShadowEvalWeekly": "影子评估结论：FAIL",
        "AgentRetrospectBrief": '"exit": 1',
    }

    def test_manifest_loads_with_unique_names(self):
        m = self._load_manifest()
        names = [t["name"] for t in m["tasks"]]
        assert len(names) == len(set(names)) >= 21

    def test_fail_scan_coverage_matches_breakpoint7_scope(self):
        """有成败日志语义的任务与 fail_scan 覆盖面一一对应（不多不少）。"""
        m = self._load_manifest()
        with_fs = {t["name"] for t in m["tasks"] if t.get("fail_scan")}
        assert with_fs == set(self.EXPECTED_FAIL_SCAN)

    def test_fail_scan_schema_tolerant(self):
        """fail_scan 三键齐全且类型合法（log/pattern 非空串、window>0 数值）。"""
        m = self._load_manifest()
        for t in m["tasks"]:
            fs = t.get("fail_scan")
            if not fs:
                continue
            assert isinstance(fs.get("log"), str) and fs["log"], t["name"]
            assert isinstance(fs.get("pattern"), str) and fs["pattern"], t["name"]
            wh = fs.get("window_hours")
            assert isinstance(wh, (int, float)) and wh > 0, t["name"]

    def test_fail_scan_pattern_matches_semantics(self):
        """pattern 与任务成败语义对齐（断点7 口径表固化）。"""
        m = self._load_manifest()
        got = {t["name"]: t["fail_scan"]["pattern"]
               for t in m["tasks"] if t.get("fail_scan")}
        for name, pattern in self.EXPECTED_FAIL_SCAN.items():
            assert got.get(name) == pattern, name

    def test_fail_scan_tasks_have_log_path(self):
        """断点7 新配 fail_scan 的任务必须有 log_path（mtime 面同源留痕）；
        AgentWatchdog 为 2026-10-18 前在档特例（只扫 aw-stack 绝对路径，
        无 log_path），豁免。"""
        m = self._load_manifest()
        for t in m["tasks"]:
            if t.get("fail_scan") and t["name"] != "AgentWatchdog":
                assert t.get("log_path"), t["name"]

    def test_window_hours_derived_from_cadence_grace(self):
        """window_hours=(cadence+grace)/60 推导（±1h 容差），自愈类 24h 特例。"""
        m = self._load_manifest()
        for t in m["tasks"]:
            fs = t.get("fail_scan")
            if not fs or t["name"] == "AgentWatchdog":
                continue  # 自愈日志单点沿用 2026-10-18 原值 24h
            expect = (int(t["cadence_minutes"]) +
                      int(t["grace_minutes"])) / 60.0
            assert abs(float(fs["window_hours"]) - expect) <= 1.0, t["name"]


class TestFailScanAggregationTmp:
    """watchdog 聚合路径补齐：真实 _log_state 扫 tmp 注入日志 → 全链推送。"""

    def _cfg(self, tmp_path, pattern):
        log = tmp_path / "probe_fail.log"
        log.write_text("line1 ok\n%s\nline3 ok\n" % pattern, encoding="utf-8")
        return {"name": "ChiefProbe", "cadence_minutes": 1440,
                "grace_minutes": 4320, "log_path": str(log),
                "fail_scan": {"log": str(log), "pattern": pattern,
                              "window_hours": 24}}

    def test_log_state_hits_fresh_fail_pattern(self, tmp_path):
        """api_execution._log_state：新鲜日志命中 pattern → fail_scan_hit。"""
        log_mtime, hit = exwd.api_execution._log_state(
            self._cfg(tmp_path, "SEND_FAIL"))
        assert hit is True and log_mtime is not None

    def test_log_state_stale_window_no_hit(self, tmp_path):
        """window_hours 外（mtime 过期）不扫——陈旧失败不误报。"""
        import os
        cfg = self._cfg(tmp_path, "SEND_FAIL")
        old = (exwd.api_execution.time.time() - 48 * 3600)
        os.utime(cfg["fail_scan"]["log"], (old, old))
        _, hit = exwd.api_execution._log_state(cfg)
        assert hit is False

    def test_log_state_clean_log_no_hit(self, tmp_path):
        """日志只有成功标记 → 不命中（SEND_OK 与 SEND_FAIL 子串区分）。"""
        log = tmp_path / "clean.log"
        log.write_text("SEND_OK all good\npushed=1\n", encoding="utf-8")
        cfg = {"name": "ChiefProbe", "cadence_minutes": 1440,
               "grace_minutes": 4320, "log_path": str(log),
               "fail_scan": {"log": str(log), "pattern": "SEND_FAIL",
                             "window_hours": 24}}
        _, hit = exwd.api_execution._log_state(cfg)
        assert hit is False

    def test_fail_scan_hit_end_to_end_alert(self, tmp_state, tmp_store,
                                            monkeypatch, tmp_path):
        """tmp 注入日志 → _log_state 命中 → 总线 ok+fail_scan_hit →
        watchdog 推 FAIL_SCAN P0（断点7 聚合全链路）。"""
        cfg = self._cfg(tmp_path, "SEND_FAIL")
        log_mtime, hit = exwd.api_execution._log_state(cfg)
        assert hit
        entry = {"name": cfg["name"], "status": "ok", "last_result": 0,
                 "result_ok": True, "log_mtime": log_mtime,
                 "stale_min": None, "in_system": True,
                 "fail_scan_hit": hit, "last_run": "2026-10-18 17:35:00",
                 "expected_cadence": "每 1440 分钟"}
        monkeypatch.setattr(
            exwd.api_execution, "build_execution_health",
            lambda force=False: {"ps_ok": True, "manifest_ok": True,
                                 "tasks": [entry]})
        texts = []
        monkeypatch.setattr(exwd, "push_feishu",
                            lambda text: texts.append(text) or True)
        code = exwd.main([])
        assert code == 0
        lr = json.loads((tmp_state / "last_run.json").read_text(
            encoding="utf-8"))
        assert lr["alerted"][0]["error_code"] == "FAIL_SCAN"
        assert lr["exit"] == 0
        # 措辞 = 断点7 收口后的通用口径（非「自愈 FAILED: 良性」专属化）
        assert texts and "fail_scan 日志命中失败标记" in texts[0]
        assert "ChiefProbe" in texts[0]
