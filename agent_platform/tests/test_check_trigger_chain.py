# -*- coding: utf-8 -*-
"""check_trigger_chain 周一触发链前置自检测试。

tmp_path 构造假任务 XML / 假日志（不读真系统），覆盖：
  1. 全 PASS：三任务 XML 合规 + 三日志含成功标记 + daemon 可连 → 退出码 0；
  2. 缺日志 WARN：chief_last_run.log 不存在 → 首跑前显式预期（带复查指引），
     不拉 FAIL，证据含「→」处置指引；
  3. 日志无成功标记 FAIL：chief 无 SEND_OK / shadow 零 done / checkpoint 末行
     非 EXIT=0 → 退出码 2；
  4. 任务 XML 缺失 WARN：读不到不武断 FAIL，证据行含实际探测路径 + 处置指引；
  5. 退出码矩阵 aggregate()：全 WARN→1 / 有 FAIL→2 / 有 PASS 无 FAIL→0；
  6. XML 解析：锚点缺失 FAIL / 触发时间漂移 FAIL / 无周一 FAIL / 禁用 FAIL /
     坏 XML WARN；--print-config 可打印；
  7. WARN 收口判例：任何 WARN 证据必含「→」处置指引（不再产出含糊 UNKNOWN）；
  8. [wait] 残留不误报（2026-10-19 修复）：monday_checkpoint.log 块级判读——
     预演/手跑中断残留与实时 [wait] 等待循环计 WARN 不计 FAIL，收尾块
     EXIT≠0 仍 FAIL（fail-visible），残留块不掩盖未处置失败，无 run 头
     旧格式回退旧末行口径。
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

#: 影子链收尾行的夹具日期（WO-TF-04）：判据 _judge_shadow_log 按**日期**取最近
#: 一次收尾行，且所取日期早于 TODAY（模块级注入点）时不得判 PASS。既有夹具把
#: `shadow day 2026-10-16` 写死，故把夹具日期抽成本常量，并在 setUp 里把
#: ctc.TODAY 绑到同一日期——断言语义零改动（原口径下这些行即"最近一次收尾"，
#: 仍 PASS），同时夹具日期与判据口径永远同步，不会随日历漂移。
LOG_DAY = "2026-10-16"


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
        # WO-TF-04：把判据的模块级 TODAY 注入点绑到夹具日期（LOG_DAY）——
        # 判据按日期取最近一次收尾行，夹具日期与判据口径须同源，故一并绑定。
        # 用 monkeypatch 语义之外的显式绑定 + 还原，保证跨用例不串味。
        self._orig_today = ctc.TODAY
        ctc.TODAY = LOG_DAY
        self.addCleanup(setattr, ctc, "TODAY", self._orig_today)

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
        # e_code_importable 判据夹具（2026-10-18 补）：该判据会编译三个脚本并对
        # run_shadow.py 做真导入。非真实仓下必须提供合法桩件，否则判据恒 FAIL、
        # 把「夹具缺件」误报成「代码坏了」——这正是假绿的镜像面（假红），同样有害。
        # 桩件取最小合法 Python：空模块可导入、语法必过，e 判据在夹具面 PASS。
        for stub in ("run_shadow.py", "monday_checkpoint.py", "a0_chief.py"):
            stub_path = self.root / stub
            if not stub_path.exists():
                stub_path.write_text(
                    "# test stub —— e_code_importable 夹具占位（空模块可导入）\n",
                    encoding="utf-8")
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
                         ["PASS", "PASS", "PASS", "PASS", "PASS"])  # e_code_importable 见夹具桩件
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
    def test_missing_chief_log_is_warn_with_guidance_not_fail(self):
        logs = {
            "last_run.log": "shadow day 2026-10-16 done: {'done': 5}\n",
            "monday_checkpoint.log": "EXIT=0 日志=x\n",
            "chief_last_run.log": None,  # 从未跑过（267011）
        }
        rep = ctc.run_checks(self.make_repo(logs), tasks_dir=self.make_tasks_dir(),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        b = rep["items"][1]
        self.assertEqual(b["status"], "WARN")
        self.assertIn("chief_last_run.log 不存在", b["evidence"])
        self.assertIn("首跑 17:35 前缺失属预期", b["evidence"])
        self.assertIn("→", b["evidence"])  # 处置指引
        self.assertIn("17:35 首跑后复查", b["evidence"])
        self.assertEqual(rep["exit_code"], 0)  # 有 PASS 无 FAIL，WARN 不阻塞放行

    def test_missing_shadow_log_warn_carries_first_run_time(self):
        logs = {
            "last_run.log": None,
            "monday_checkpoint.log": "EXIT=0 日志=x\n",
            "chief_last_run.log": "SEND_OK\n",
        }
        rep = ctc.run_checks(self.make_repo(logs), tasks_dir=self.make_tasks_dir(),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        b = rep["items"][1]
        self.assertEqual(b["status"], "WARN")
        self.assertIn("首跑 17:10 前缺失属预期", b["evidence"])

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
    def test_missing_task_xml_is_warn_with_probed_name_and_guidance(self):
        bodies = dict(TASK_BODIES)
        del bodies["AgentChiefPush"]
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(bodies),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        a = rep["items"][0]
        self.assertEqual(a["status"], "WARN")
        self.assertIn("AgentChiefPush", a["evidence"])
        self.assertIn("Tasks", a["evidence"])  # 输出实际探测过的路径
        self.assertIn("→", a["evidence"])  # 处置指引
        self.assertIn("异常矩阵 #4", a["evidence"])
        self.assertEqual(rep["exit_code"], 0)  # WARN 不拉 FAIL

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

    def test_malformed_xml_is_warn_with_guidance(self):
        tdir = self.make_tasks_dir()
        (tdir / "AgentShadowDaily").write_text("<not-xml", encoding="utf-16")
        rep = ctc.run_checks(self.make_repo(), tasks_dir=tdir,
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][0]["status"], "WARN")
        self.assertIn("XML 解析失败", rep["items"][0]["evidence"])
        self.assertIn("→", rep["items"][0]["evidence"])


class TestDaemon(TriggerChainBase):
    def test_ollama_refused_is_fail(self):
        def refused(host, port):
            raise ConnectionRefusedError()
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(),
                             connect_fn=refused, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][2]["status"], "FAIL")
        self.assertIn("无监听", rep["items"][2]["evidence"])

    def test_ollama_other_error_is_warn_with_guidance(self):
        def boom(host, port):
            raise OSError("sandbox denied")
        rep = ctc.run_checks(self.make_repo(), tasks_dir=self.make_tasks_dir(),
                             connect_fn=boom, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["items"][2]["status"], "WARN")
        self.assertIn("探测异常", rep["items"][2]["evidence"])
        self.assertIn("curl", rep["items"][2]["evidence"])  # 手动复核指引

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
        self.assertEqual(ctc.aggregate(["WARN"] * 4), 1)
        self.assertEqual(ctc.aggregate(["PASS", "WARN"]), 0)
        self.assertEqual(ctc.aggregate(["WARN", "FAIL"]), 2)
        self.assertEqual(ctc.aggregate([]), 1)

    def test_all_warn_run_exit_one(self):
        # 全 WARN（探测面不可达）与原全 UNKNOWN 同口径：无正向 PASS 证据 → 1
        statuses = ["WARN", "WARN", "WARN"]
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
        # 未注入任务目录 → 走真系统路径（沙箱读不到）→ a WARN；日志面全绿 → 0
        self.assertEqual(code, 0)
        out = buf.getvalue()
        self.assertIn("周一外部触发链前置自检", out)
        self.assertIn("汇总: PASS", out)


class TestRealSystemReadOnly(TriggerChainBase):
    """真仓实跑（只读）：验证可执行性与 fail-visible，不断言具体状态。"""

    def test_real_repo_run_is_read_only_and_visible(self):
        real_root = AP
        rep = ctc.run_checks(real_root)
        # 5 条：a_task_registration / b_last_run_results / c_daemon_alive /
        # d_data_root_ready / e_code_importable（2026-10-18 新增代码可导入性）
        self.assertEqual(len(rep["items"]), 5)
        for it in rep["items"]:
            self.assertIn(it["status"], ("PASS", "FAIL", "WARN"))
            self.assertTrue(it["evidence"])
        self.assertIn(rep["exit_code"], (0, 1, 2))

    def test_real_checkpoint_log_judge_is_read_only_and_visible(self):
        """真仓 monday_checkpoint.log 块级判读只读、不断言具体状态：实况随
        17:25 块收尾/未收尾在 WARN/FAIL 间演化（等满上限收尾 EXIT=1 是
        真实失败），锁死「不抛+状态合法+证据非空」的 fail-visible 契约。"""
        real_log = AP / "data" / "shadow" / "monday_checkpoint.log"
        if not real_log.exists():
            self.skipTest("真仓巡检日志不存在（周一 17:25 首跑前预期）")
        st, ev = ctc._judge_checkpoint_log(real_log.read_text(encoding="utf-8"))
        self.assertIn(st, ("PASS", "WARN", "FAIL"))
        self.assertTrue(ev)


class TestWarnClosure(TriggerChainBase):
    """WARN 收口判例（2026-10-18）：输出不再出现含糊 UNKNOWN，WARN 必带指引。"""

    def test_no_vague_unknown_in_statuses_and_report(self):
        import io
        from contextlib import redirect_stdout
        # 混合场景：任务 XML 缺失 + chief 日志缺失 → a/b 双 WARN
        bodies = dict(TASK_BODIES)
        del bodies["AgentChiefPush"]
        logs = {
            "last_run.log": "shadow day 2026-10-16 done: {'done': 5}\n",
            "monday_checkpoint.log": "EXIT=0 日志=x\n",
            "chief_last_run.log": None,
        }
        rep = ctc.run_checks(self.make_repo(logs), tasks_dir=self.make_tasks_dir(bodies),
                             connect_fn=self.ok_connect, process_probe=lambda: None,
                             aw_stack_log=self.root / "no.log")
        self.assertEqual(rep["warn"], 2)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ctc._print_report(rep)
        out = buf.getvalue()
        self.assertNotIn("UNKNOWN", out)  # 报告面不再出现 UNKNOWN 字样
        # 每条 WARN 证据必含「→」处置指引
        for it in rep["items"]:
            if it["status"] == "WARN":
                self.assertIn("→", it["evidence"])
        self.assertEqual(rep["exit_code"], 0)  # 有 PASS 无 FAIL → 可放行


class TestCheckpointLogWaitResidual(TriggerChainBase):
    """判据 b 修复（2026-10-19 预期性误报）：monday_checkpoint.log 块级判读。

    事故复刻：17:25 AgentMondayCheckpoint 进入设计内 [wait] 90s 等待循环
    （17:10 首跑零落账 → 未完成），旧「只看末行 EXIT=0」口径把 [wait] 行
    误判 FAIL → 触发链预检假警报。新口径按 run 块判：末块未收尾（[wait]
    残留/进行中）= WARN 不计 FAIL；末块收尾且 EXIT≠0 = 真实 FAIL
    （fail-visible）；残留块不得掩盖未被子块取代的真实失败。
    """

    @staticmethod
    def _blk(ts, body):
        return ("=" * 56 + "\nM5 刀4 周一观察点护航巡检 %s\n" % ts + "=" * 56
                + "\n" + body)

    WAIT = "[wait] 首跑未完成（尚未落账，第 1/120 次重试前等 90s）\n"
    OK_BODY = ("结论: 7/7 PASS——观察点门全过\n"
               "EXIT=0 日志=C:\\aios\\agent_platform\\data\\shadow\\monday_checkpoint.log\n")
    FAIL_BODY = ("结论: 4/7 PASS（FAIL: 影子首跑已发生（最新交易日 > 2026-10-14））\n"
                 "EXIT=1 日志=x\n")

    def _run_b(self, checkpoint_text):
        """只让 monday_checkpoint.log 变化（其余两日志恒 PASS），返回 run_checks 报告。"""
        logs = {
            "last_run.log": "shadow day 2026-10-16 done: {'done': 5}\n",
            "monday_checkpoint.log": checkpoint_text,
            "chief_last_run.log": "SEND_OK\n",
        }
        return ctc.run_checks(self.make_repo(logs), tasks_dir=self.make_tasks_dir(),
                              connect_fn=self.ok_connect, process_probe=lambda: None,
                              aw_stack_log=self.root / "no.log")

    def test_wait_residual_block_is_warn_not_fail(self):
        """10-19 16:40 实况复刻：旧块 EXIT=0 + 末块 [wait] 残留（04:11 预演/
        手跑中断，无收尾行）→ WARN 不计 FAIL；不告警、不阻塞放行（exit 0）。"""
        rep = self._run_b(self._blk("2026-10-18 05:55:58", self.OK_BODY)
                          + self._blk("2026-10-19 04:11:43", self.WAIT + self.WAIT))
        b = rep["items"][1]
        self.assertEqual(b["status"], "WARN")
        self.assertIn("[wait] 行 ×2", b["evidence"])
        self.assertIn("非真实失败", b["evidence"])
        self.assertIn("→", b["evidence"])  # WARN 必带处置指引
        self.assertEqual(rep["fail"], 0)
        self.assertEqual(rep["exit_code"], 0)  # 有 PASS 无 FAIL → 可放行

    def test_live_wait_loop_in_progress_is_warn_not_fail(self):
        """实时等待循环（17:25 块 [wait] ×36 进行中、未收尾）→ WARN：
        设计内形态（SOP 手册：[wait] 行=在推进属正常），不误报 FAIL。"""
        rep = self._run_b(self._blk("2026-10-18 05:55:58", self.OK_BODY)
                          + self._blk("2026-10-19 17:25:00", self.WAIT * 36))
        b = rep["items"][1]
        self.assertEqual(b["status"], "WARN")
        self.assertIn("[wait] 行 ×36", b["evidence"])

    def test_concluded_exit1_block_is_real_fail(self):
        """末块收尾 EXIT=1（等满上限后观察点门未过）→ 真实 FAIL，带结论
        明细；exit 2（触发链预检告警面照旧响）。"""
        rep = self._run_b(self._blk("2026-10-18 05:55:58", self.OK_BODY)
                          + self._blk("2026-10-19 17:25:00",
                                      self.WAIT * 3 + self.FAIL_BODY))
        b = rep["items"][1]
        self.assertEqual(b["status"], "FAIL")
        self.assertIn("真实 checkpoint 失败", b["evidence"])
        self.assertIn("影子首跑已发生", b["evidence"])  # 结论明细在证据里
        self.assertEqual(rep["exit_code"], 2)

    def test_residual_block_does_not_mask_unresolved_fail(self):
        """残留块不得掩盖未处置失败：最近完整块 EXIT=1 + 末块 [wait] 残留
        → 仍 FAIL（fail-visible：失败未被更新的完整块取代）。"""
        rep = self._run_b(self._blk("2026-10-19 17:25:00",
                                    self.WAIT * 3 + self.FAIL_BODY)
                          + self._blk("2026-10-19 18:00:00", self.WAIT + self.WAIT))
        b = rep["items"][1]
        self.assertEqual(b["status"], "FAIL")
        self.assertIn("未被更新的完整块取代", b["evidence"])

    def test_concluded_exit0_block_is_pass(self):
        """末块收尾 EXIT=0（[wait] 后首跑落账、门全过）→ PASS。"""
        rep = self._run_b(self._blk("2026-10-19 17:25:00",
                                    self.WAIT * 3 + self.OK_BODY))
        self.assertEqual(rep["items"][1]["status"], "PASS")

    def test_empty_log_still_fail(self):
        """空日志保持 FAIL（存在但无任何证据，fail-visible 不降级）。"""
        rep = self._run_b("   \n\n")
        b = rep["items"][1]
        self.assertEqual(b["status"], "FAIL")
        self.assertIn("为空", b["evidence"])

    def test_header_only_block_is_warn_with_guidance(self):
        """仅头行无体（拉起即被杀）且无历史完整块 → WARN + 复查指引
        （无证据不判失败，也不冒充通过）。"""
        rep = self._run_b(self._blk("2026-10-19 17:25:00", ""))
        b = rep["items"][1]
        self.assertEqual(b["status"], "WARN")
        self.assertIn("无历史完整块", b["evidence"])
        self.assertIn("→", b["evidence"])

    def test_headerless_legacy_fallback_preserved(self):
        """无 run 头（旧格式/手工摘要）→ 回退旧末行口径：末行 EXIT=0=PASS；
        末行无 EXIT=0=FAIL（存量夹具行为不回归）。"""
        ok = self._run_b("x\n结论: 7/7 PASS——观察点门全过\nEXIT=0 日志=x\n")
        self.assertEqual(ok["items"][1]["status"], "PASS")
        bad = self._run_b("EXIT=0 日志=x\n结论: 有 FAIL——先查清\n")
        self.assertEqual(bad["items"][1]["status"], "FAIL")
        self.assertIn("末行无 EXIT=0", bad["items"][1]["evidence"])


class TestShadowLogDateGate(TriggerChainBase):
    """影子日志判据的日期感知回归（WO-TF-04，2026-10-20）。

    背景：WO-TF-02 把 ``last_run.log`` 由「每日覆盖」改为「跨日累积」后，
    「最后一条 shadow day 行」≠「最近一次跑的收尾行」——历史成功行会冒充
    今日通过（假绿）。本类把 WO-TF-04 工单 §三 的 5 条判据 + lead 独立复核
    补出的 2 条同日多次跑场景，**固化为可回归断言**（原工位实现未新增任何
    用例，判据无回归护栏 ⇒ 下次改动会静默回退假绿）。

    夹具日期统一用 ``LOG_DAY``（= ``ctc.TODAY``，基类 setUp 已绑定），
    今日/昨日用 ``DAY_TODAY`` / ``DAY_YEST`` 相对生成，日历漂移不敏感。
    """

    DAY_TODAY = LOG_DAY
    DAY_YEST = "2026-10-15"

    @staticmethod
    def _start(day, hhmm="17:10:00"):
        return "===== shadow run start %s %s =====\n" % (day, hhmm)

    @staticmethod
    def _close(day, done=5):
        return "shadow day %s done: {'done': %d}\n" % (day, done)

    def _judge(self, text):
        return ctc._judge_shadow_log(text)

    # ---------- 工单 §三 判据 1-5 ----------

    def test_c1_today_crashed_with_history_success_is_not_pass(self):
        """判据1：历史有成功行（D-1）+ 今日有头行但无收尾行 ⇒ 不得判 PASS
        （改前会读到历史那条判 PASS = 假绿）。"""
        text = (self._close(self.DAY_YEST)
                + self._start(self.DAY_TODAY)
                + "[strategy] xxx\n")
        status, msg = self._judge(text)
        self.assertNotEqual(status, ctc.PASS)
        self.assertEqual(status, ctc.FAIL)
        self.assertIn(self.DAY_YEST, msg)  # 文案须写出所依据那行的日期
        self.assertIn(self.DAY_TODAY, msg)

    def test_c2_today_success_is_pass_with_today_date(self):
        """判据2：含今日收尾行 done≥1 ⇒ PASS 且说明含今日日期。"""
        text = (self._close(self.DAY_YEST)
                + self._start(self.DAY_TODAY)
                + self._close(self.DAY_TODAY, done=3))
        status, msg = self._judge(text)
        self.assertEqual(status, ctc.PASS)
        self.assertIn(self.DAY_TODAY, msg)

    def test_c3_only_history_success_is_not_pass(self):
        """判据3：仅历史成功（无今日行）⇒ 非 PASS 且文案含该历史日期。"""
        status, msg = self._judge(self._close(self.DAY_YEST))
        self.assertNotEqual(status, ctc.PASS)
        self.assertIn(self.DAY_YEST, msg)
        self.assertIn("→", msg)  # WARN 必带处置指引（本仓既有纪律）

    def test_c4_multiday_today_success_in_middle_is_pass(self):
        """判据4（本次修复核心）：多日累积，今日成功行**不在最后一条**
        ⇒ 仍按**日期**取到今日那条并判 PASS（不能只取「最后一条」）。"""
        text = (self._close("2026-10-13")
                + self._start(self.DAY_TODAY)
                + self._close(self.DAY_TODAY, done=3)
                + self._close("2026-10-14"))  # 今日行之后还有别的日期行
        status, msg = self._judge(text)
        self.assertEqual(status, ctc.PASS)
        self.assertIn(self.DAY_TODAY, msg)

    def test_c5_today_zero_done_is_fail(self):
        """判据5：今日跑过但 done=0 ⇒ 判非 PASS（沿用「零 done」语义）。"""
        status, msg = self._judge(self._close(self.DAY_TODAY, done=0))
        self.assertEqual(status, ctc.FAIL)
        self.assertIn("零 done", msg)

    # ---------- lead 独立复核补出的同日多次跑场景 ----------

    def test_same_day_early_success_then_late_crash_is_fail(self):
        """补充场景（lead 独立复核发现，工位实现未覆盖）：同一日跑两次——
        早跑成功收尾、晚跑崩溃（只有头行无收尾行）⇒ 判 FAIL。
        改前按「日期最大」取到早跑那条成功行 ⇒ 假绿。生产上同日可多次跑
        （17:10 定时跑 + 手工补跑 + 演练跑），非构造场景。"""
        text = (self._start(self.DAY_TODAY, "03:47:59")
                + self._close(self.DAY_TODAY, done=5)
                + self._start(self.DAY_TODAY, "17:10:00")
                + "[strategy] xxx\n")
        status, msg = self._judge(text)
        self.assertEqual(status, ctc.FAIL)
        self.assertIn(self.DAY_TODAY, msg)
        self.assertIn("无收尾行", msg)

    def test_same_day_early_crash_then_late_success_is_pass(self):
        """补充场景反向：同日早跑崩溃、晚跑成功 ⇒ 判 PASS（取今日**最后
        一次跑**的真实结果，不被早跑的崩溃拖累）。"""
        text = (self._start(self.DAY_TODAY, "03:47:59")
                + "[strategy] xxx\n"
                + self._start(self.DAY_TODAY, "17:10:00")
                + self._close(self.DAY_TODAY, done=5))
        status, msg = self._judge(text)
        self.assertEqual(status, ctc.PASS)
        self.assertIn(self.DAY_TODAY, msg)

    # ---------- 既有行为不回归 ----------

    def test_no_close_line_at_all_is_fail(self):
        """无任何收尾行（跑过但未正常收尾）⇒ FAIL（既有语义不回归）。"""
        status, msg = self._judge(self._start(self.DAY_TODAY) + "[strategy] x\n")
        self.assertEqual(status, ctc.FAIL)

    def test_empty_log_is_fail(self):
        """空日志 ⇒ FAIL（存在但无正向证据，fail-visible 不降级）。"""
        status, _ = self._judge("")
        self.assertEqual(status, ctc.FAIL)

    def test_today_not_run_yet_is_warn_not_fail(self):
        """今日尚未开跑（预检早于 17:10 跑批）⇒ WARN 不误报失败；
        且不得判 PASS（历史成功 ≠ 今日通过）。"""
        status, msg = self._judge(self._close(self.DAY_YEST))
        self.assertEqual(status, ctc.WARN)
        self.assertIn("→", msg)

    def test_today_start_only_no_close_is_fail(self):
        """今日有头行、无今日收尾行、且无历史行 ⇒ FAIL（真失败可见）。"""
        status, msg = self._judge(self._start(self.DAY_TODAY) + "[strategy] x\n")
        self.assertEqual(status, ctc.FAIL)
        self.assertIn(self.DAY_TODAY, msg)


if __name__ == "__main__":
    unittest.main()
