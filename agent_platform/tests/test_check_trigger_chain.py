# -*- coding: utf-8 -*-
"""check_trigger_chain 周一触发链前置自检测试。

tmp_path 构造假任务 XML / 假日志（不读真系统），覆盖：
  1. 全 PASS：三任务 XML 合规 + 三日志含成功标记 + daemon 可连 → 退出码 0；
  2. 缺日志 UNKNOWN：chief_last_run.log 不存在 → 首跑前中性，不拉 FAIL；
  3. 日志无成功标记 FAIL：chief 无 SEND_OK / shadow 零 done / checkpoint 末行
     非 EXIT=0 → 退出码 2；
  4. 任务 XML 缺失 UNKNOWN：读不到不武断 FAIL，证据行含实际探测路径；
  5. 退出码矩阵 aggregate()：全 UNKNOWN→1 / 有 FAIL→2 / 有 PASS 无 FAIL→0；
  6. XML 解析：锚点缺失 FAIL / 触发时间漂移 FAIL / 无周一 FAIL / 禁用 FAIL /
     坏 XML UNKNOWN；--print-config 可打印。
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

AP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AP))
sys.path.insert(0, str(AP / "tools"))

import check_trigger_chain as ctc  # noqa: E402

TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"

PS_CMD = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
PS_ARGS_RUN = r"-NoProfile -ExecutionPolicy Bypass -File C:\aios\agent_platform\shadow_automation.ps1 -Mode run"
PS_ARGS_PUSH = r"-NoProfile -ExecutionPolicy Bypass -File C:\aios\agent_platform\shadow_automation.ps1 -Mode push"
PY_CMD = r"C:\Program Files\Python311\python.exe"
PY_ARGS = r"-X utf8 C:\aios\agent_platform\monday_checkpoint.py"


def make_task_xml(hhmm="17:10", days=("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"),
                  command=PS_CMD, arguments=PS_ARGS_RUN, enabled=True):
    days_xml = "".join("<%s />" % d for d in days)
    en = "true" if enabled else "false"
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.3" xmlns="%s">\n'
        "  <Triggers><CalendarTrigger>\n"
        "    <StartBoundary>2026-10-17T%s:00+08:00</StartBoundary>\n"
        "    <Enabled>%s</Enabled>\n"
        "    <ScheduleByWeek><DaysOfWeek>%s</DaysOfWeek><WeeksInterval>1</WeeksInterval>"
        "</ScheduleByWeek>\n"
        "  </CalendarTrigger></Triggers>\n"
        "  <Settings><Enabled>%s</Enabled></Settings>\n"
        '  <Actions Context="Author"><Exec>\n'
        "    <Command>%s</Command>\n"
        "    <Arguments>%s</Arguments>\n"
        "  </Exec></Actions>\n"
        "</Task>" % (TASK_NS, hhmm, en, days_xml, en, command, arguments)
    )


TASK_BODIES = {
    "AgentShadowDaily": make_task_xml(),
    "AgentMondayCheckpoint": make_task_xml(hhmm="17:25", days=("Monday",),
                                           command=PY_CMD, arguments=PY_ARGS),
    "AgentChiefPush": make_task_xml(hhmm="17:35", arguments=PS_ARGS_PUSH),
}


class TriggerChainBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="ctc_test_")
        self.root = Path(self._tmp)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def make_tasks_dir(self, bodies=None):
        tdir = self.root / "Tasks"
        tdir.mkdir(parents=True, exist_ok=True)
        for name, xml in (bodies if bodies is not None else TASK_BODIES).items():
            (tdir / name).write_text(xml, encoding="utf-16")
        return tdir

    def make_repo(self, logs=None):
        """构造 repo_root/data/shadow 假日志面（None=不写该日志）。"""
        log_dir = self.root / "data" / "shadow"
        log_dir.mkdir(parents=True, exist_ok=True)
        (self.root / "data" / "idempotency").mkdir(exist_ok=True)
        defaults = {
            "last_run.log": "a\nshadow day 2026-10-16 done: {'done': 5}\n",
            "monday_checkpoint.log": "x\n结论: 7/7 PASS——观察点门全过\n"
                                     "EXIT=0 日志=C:\\aios\\agent_platform\\data\\shadow\\monday_checkpoint.log\n",
            "chief_last_run.log": "[A0] 日报已生成\nSEND_OK ou_xxx\n",
        }
        for name, text in (logs if logs is not None else defaults).items():
            if text is not None:
                (log_dir / name).write_text(text, encoding="utf-8")
        return self.root

    @staticmethod
    def ok_connect(host, port):
        return None

    @staticmethod
    def ok_probe():
        return ["python -m agent_platform.cli watch-daemon --market CRYPTO"]


class TestAllPass(TriggerChainBase):
    def test_green_run_exit_zero(self):
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(),
                             connect_fn=self.ok_connect, process_probe=self.ok_probe,
                             aw_stack_log=self.root / "nonexistent_aw.log")
        self.assertEqual(rep["exit_code"], 0)
        self.assertEqual(rep["fail"], 0)
        self.assertEqual([it["status"] for it in rep["items"]],
                         ["PASS", "PASS", "PASS", "PASS"])
        a = rep["items"][0]["evidence"]
        self.assertIn("AgentShadowDaily 17:10", a)
        self.assertIn("AgentMondayCheckpoint 17:25 周一", a)
        self.assertIn("AgentChiefPush 17:35", a)
        b = rep["items"][1]["evidence"]
        self.assertIn("SEND_OK", b)
        self.assertIn("EXIT=0", b)
        self.assertIn("shadow day", b)
        c = rep["items"][2]["evidence"]
        self.assertIn("Ollama", c)
        self.assertIn("进程特征在跑", c)
        d = rep["items"][3]["evidence"]
        self.assertIn("idempotency/ 0 项", d)


class TestLastRunLogs(TriggerChainBase):
    def test_missing_chief_log_is_unknown_not_fail(self):
        logs = {
            "last_run.log": "shadow day 2026-10-16 done: {'done': 5}\n",
            "monday_checkpoint.log": "EXIT=0 日志=x\n",
            "chief_last_run.log": None,  # 从未跑过（267011）
        }
        rep = ctc.run_checks(self.make_repo(logs), tasks_dir=self.make_tasks_dir(),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        b = rep["items"][1]
        self.assertEqual(b["status"], "UNKNOWN")
        self.assertIn("chief_last_run.log 不存在", b["evidence"])
        self.assertIn("首跑前中性", b["evidence"])
        self.assertEqual(rep["exit_code"], 0)  # 有 PASS 无 FAIL

    def test_chief_log_without_send_ok_is_fail(self):
        logs = {
            "last_run.log": "shadow day 2026-10-16 done: {'done': 5}\n",
            "monday_checkpoint.log": "EXIT=0 日志=x\n",
            "chief_last_run.log": "[A0] 日报已生成\n",  # 日报生成≠推送成功
        }
        rep = ctc.run_checks(self.make_repo(logs), tasks_dir=self.make_tasks_dir(),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][1]["status"], "FAIL")
        self.assertIn("无 SEND_OK", rep["items"][1]["evidence"])
        self.assertEqual(rep["exit_code"], 2)

    def test_shadow_zero_done_is_fail(self):
        logs = {
            "last_run.log": "shadow day 2026-10-16 done: {'failed': 5}\n",
            "monday_checkpoint.log": "EXIT=0 日志=x\n",
            "chief_last_run.log": "SEND_OK\n",
        }
        rep = ctc.run_checks(self.make_repo(logs), tasks_dir=self.make_tasks_dir(),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][1]["status"], "FAIL")
        self.assertIn("零 done", rep["items"][1]["evidence"])

    def test_checkpoint_last_line_not_exit0_is_fail(self):
        # 追加式日志：旧 run 的 EXIT=0 在中间，末行是新 FAIL——防旧 run 假绿
        logs = {
            "last_run.log": "shadow day 2026-10-16 done: {'done': 5}\n",
            "monday_checkpoint.log": "EXIT=0 日志=x\n结论: 有 FAIL——先查清\n",
            "chief_last_run.log": "SEND_OK\n",
        }
        rep = ctc.run_checks(self.make_repo(logs), tasks_dir=self.make_tasks_dir(),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][1]["status"], "FAIL")
        self.assertIn("末行无 EXIT=0", rep["items"][1]["evidence"])

    def test_checkpoint_exit0_must_be_last_line(self):
        logs = {
            "last_run.log": "shadow day 2026-10-16 done: {'done': 5}\n",
            # EXIT=0 在中间（旧 run），末行无标记 → FAIL
            "monday_checkpoint.log": "EXIT=0 日志=x\nxxx\n",
            "chief_last_run.log": "SEND_OK\n",
        }
        rep = ctc.run_checks(self.make_repo(logs), tasks_dir=self.make_tasks_dir(),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][1]["status"], "FAIL")


class TestTaskRegistration(TriggerChainBase):
    def test_missing_task_xml_is_unknown_with_probed_name(self):
        bodies = dict(TASK_BODIES)
        del bodies["AgentChiefPush"]
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(bodies),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        a = rep["items"][0]
        self.assertEqual(a["status"], "UNKNOWN")
        self.assertIn("AgentChiefPush", a["evidence"])
        self.assertIn("Tasks", a["evidence"])  # 输出实际探测过的路径
        self.assertEqual(rep["exit_code"], 0)  # UNKNOWN 不拉 FAIL

    def test_anchor_mismatch_is_fail(self):
        bodies = {"AgentShadowDaily": make_task_xml(arguments="-Mode eval"),  # 锚点错
                  "AgentMondayCheckpoint": TASK_BODIES["AgentMondayCheckpoint"],
                  "AgentChiefPush": TASK_BODIES["AgentChiefPush"]}
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(bodies),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][0]["status"], "FAIL")
        self.assertIn("锚点缺失", rep["items"][0]["evidence"])
        self.assertEqual(rep["exit_code"], 2)

    def test_time_drift_is_fail(self):
        bodies = {"AgentShadowDaily": make_task_xml(hhmm="09:00"),
                  "AgentMondayCheckpoint": TASK_BODIES["AgentMondayCheckpoint"],
                  "AgentChiefPush": TASK_BODIES["AgentChiefPush"]}
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(bodies),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][0]["status"], "FAIL")
        self.assertIn("触发时间 09:00 ≠ 期望 17:10", rep["items"][0]["evidence"])

    def test_no_monday_is_fail(self):
        bodies = {"AgentShadowDaily": make_task_xml(days=("Tuesday", "Wednesday")),
                  "AgentMondayCheckpoint": TASK_BODIES["AgentMondayCheckpoint"],
                  "AgentChiefPush": TASK_BODIES["AgentChiefPush"]}
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(bodies),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][0]["status"], "FAIL")
        self.assertIn("无周一", rep["items"][0]["evidence"])

    def test_disabled_task_is_fail(self):
        bodies = {"AgentShadowDaily": make_task_xml(enabled=False),
                  "AgentMondayCheckpoint": TASK_BODIES["AgentMondayCheckpoint"],
                  "AgentChiefPush": TASK_BODIES["AgentChiefPush"]}
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(bodies),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][0]["status"], "FAIL")
        self.assertIn("禁用", rep["items"][0]["evidence"])

    def test_malformed_xml_is_unknown(self):
        tdir = self.make_tasks_dir()
        (tdir / "AgentShadowDaily").write_text("<not-xml", encoding="utf-16")
        rep = ctc.run_checks(self.make_repo(), tasks_dir=tdir,
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][0]["status"], "UNKNOWN")
        self.assertIn("XML 解析失败", rep["items"][0]["evidence"])


class TestDaemon(TriggerChainBase):
    def test_ollama_refused_is_fail(self):
        def refused(host, port):
            raise ConnectionRefusedError()
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(),
                             connect_fn=refused, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][2]["status"], "FAIL")
        self.assertIn("无监听", rep["items"][2]["evidence"])

    def test_ollama_other_error_is_unknown(self):
        def boom(host, port):
            raise OSError("sandbox denied")
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(),
                             connect_fn=boom, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][2]["status"], "UNKNOWN")
        self.assertIn("探测异常", rep["items"][2]["evidence"])

    def test_watchdog_stack_log_trace_visible(self):
        aw = self.root / "aw-stack.log"
        aw.write_text("[2026-10-18 10:00:00] pulled up: watch-daemon,ack-serve\n",
                      encoding="utf-8")
        _, ev = ctc.check_c_daemon(connect_fn=self.ok_connect,
                                   process_probe=lambda: None, aw_stack_log=aw)
        self.assertIn("拉起留痕", ev)


class TestDataRoot(TriggerChainBase):
    def test_missing_data_root_is_fail(self):
        empty = self.root / "empty_repo"
        empty.mkdir()
        st, ev = ctc.check_d_data_root(empty)
        self.assertEqual(st, "FAIL")
        self.assertIn("不存在", ev)

    def test_present_data_root_pass(self):
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][3]["status"], "PASS")


class TestExitMatrix(TriggerChainBase):
    def test_aggregate_matrix(self):
        self.assertEqual(ctc.aggregate(["UNKNOWN"] * 4), 1)
        self.assertEqual(ctc.aggregate(["PASS", "UNKNOWN"]), 0)
        self.assertEqual(ctc.aggregate(["PASS", "FAIL"]), 2)
        self.assertEqual(ctc.aggregate([]), 1)

    def test_all_unknown_run_exit_one(self):
        # 任务全缺 + 日志全缺 + Ollama 探测异常 + 数据根可读（真 PASS 不在矩阵内，
        # 用不存在的 repo 让 d 也 FAIL 之外的场景不可达——此处验 d=PASS 时全 UNKNOWN
        # 的 b/a/c 与 1 值聚合口径：aggregate 输入排除 d）
        statuses = ["UNKNOWN", "UNKNOWN", "UNKNOWN"]
        self.assertEqual(ctc.aggregate(statuses), 1)

    def test_fail_run_exit_two(self):
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(),
                             connect_fn=lambda h, p: (_ for _ in ()).throw(
                                 ConnectionRefusedError()),
                             process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["exit_code"], 2)


class TestCli(TriggerChainBase):
    def test_print_config(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ctc.main(["--repo-root", str(self.root), "--print-config"])
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("AgentShadowDaily", out)
        self.assertIn("17:25", out)
        self.assertIn("chief_last_run.log", out)
        self.assertIn("11434", out)

    def test_main_report_and_exit_code(self):
        import io
        from contextlib import redirect_stdout
        rep_root = self.make_repo()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ctc.main(["--repo-root", str(rep_root)])
        # 未注入任务目录 → 走真系统路径（沙箱读不到）→ a UNKNOWN；日志面全绿 → 0
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("周一外部触发链前置自检", out)
        self.assertIn("汇总: PASS", out)


class TestRealSystemReadOnly(TriggerChainBase):
    """真仓实跑（只读）：验证可执行性与 fail-visible，不断言具体状态。"""

    def test_real_repo_run_is_read_only_and_visible(self):
        real_root = AP
        rep = ctc.run_checks(real_root)
        self.assertEqual(len(rep["items"]), 4)
        for it in rep["items"]:
            self.assertIn(it["status"], ("PASS", "FAIL", "UNKNOWN"))
            self.assertTrue(it["evidence"])
        self.assertIn(rep["exit_code"], (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
