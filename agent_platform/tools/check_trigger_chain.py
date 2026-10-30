# -*- coding: utf-8 -*-
"""check_trigger_chain：周一外部触发链前置自检（只读，fail-visible）。

## 背景

2026-10-19（周一）外部触发链 = 17:10 AgentShadowDaily → 17:25
AgentMondayCheckpoint → 17:35 AgentChiefPush。tools/check_observation_point.py
（cac9ed6e）覆盖 17:25 之后的观察判读，但触发**前**（如 16:50）没有工具验证
链路就绪。本工具补「开跑前体检」：任务注册面、上次运行结果、daemon 存活、
幂等根就绪。

## 取证依据（2026-10-18 实证，判例出处）

- 任务 XML 可直读 `C:\\Windows\\System32\\Tasks\\<任务名>`（UTF-16 编码，
  open(encoding='utf-16') 自动剥 BOM）；沙箱常拦
  Get-ScheduledTask/Export-ScheduledTask 与目录 listdir，直读文件更稳。
- 三个任务的真实锚点（实测 XML）：
    AgentShadowDaily      17:10 周一~周五  powershell ... shadow_automation.ps1 -Mode run
    AgentMondayCheckpoint 17:25 仅周一     python -X utf8 ... monday_checkpoint.py
    AgentChiefPush        17:35 周一~周五  powershell ... shadow_automation.ps1 -Mode push
- 上次运行结果：任务 XML **不含** LastTaskResult 字段，改查任务对应日志
  （shadow_automation.ps1:24/42 重定向取证）：
    last_run.log          收尾行 `shadow day <日期> done: {...}`（run_shadow.py:455
                         无条件打印）；done 计数 ≥1 = PASS，跑过但全 failed = FAIL；
                         收尾行按**日期最大**者取（日志跨日累积，见 WO-TF-04），
                         且所取日期早于今日（模块级 TODAY）时不得判 PASS——
                         历史成功不得冒充今日通过（今日崩了=FAIL / 今日未跑=WARN）
    monday_checkpoint.log 末个 run 块含 EXIT=0（追加式日志按块判读防旧 run
                          假绿；末块未收尾的 [wait] 残留/进行中 = WARN 不计
                          FAIL，2026-10-19 误报修复见 _judge_checkpoint_log）
    chief_last_run.log    须含 SEND_OK——只有「日报已生成」而无 SEND_OK =
                          推送失败（门3检查 7.2 判例）
  日志缺失 = WARN 显式预期：AgentShadowDaily / AgentChiefPush LastTaskResult=
  267011 从未跑过，首跑（周一 17:10/17:35）前缺失属**确定预期形态**（2026-10-18
  实跑判例：这曾是整项 UNKNOWN 的唯一来源——检测器明知中性却仍标 UNKNOWN，
  属检测器状态建模不足，已收口为显式 WARN + 复查指引，不再产出含糊 UNKNOWN）。
- daemon 留痕取证：start_agent_platform.ps1（zhice-os-v2 仓 deploy/）产出的
  watch-daemon/ack-serve **无 pid 文件**，留痕 = Win32_Process CommandLine
  匹配 'agent_platform\\.cli (watch-daemon|ack-serve)'（start_agent_platform.ps1:27-30）
  + 拉起日志 code/logs/aw-stack.log。本链真正的硬依赖是 Ollama LLM 服务
  （run_shadow.py:341 OllamaLLM；门3检查项 7）：socket 探 11434 端口，
  无监听 = FAIL，其他异常 = WARN（带手动复核指引）。watch-daemon 留痕只作证据行不翻转状态
  （AgentWatchdog 已知损坏待管理员修复，M6 判例 7.4，非本链阻塞项）。
- 幂等根：run_shadow 真仓数据根 = <agent_platform>/data（run_shadow.py:64），
  存在且可读 = PASS；缺失 = FAIL（17:10 无处落账）。

## 检查项（每项 PASS/FAIL/WARN + 一行证据；WARN 必带「→」处置指引）

  a) 任务注册面：三个任务 XML 直读解析触发时间/命令行锚点/周一触发/Enabled；
     读不到文件 = WARN（不武断 FAIL——可能任务名不同，输出实际探测过的名字
     + 处置指引）
  b) 上次运行结果：三个日志按各自成功标记判读（见上）；日志缺失在首跑前 =
     WARN 显式预期（带复查时点指引）
  c) daemon 存活：Ollama 11434 socket 探测（决定性）+ watch-daemon 留痕（信息性）；
     探测异常（非拒绝连接）= WARN + 手动复核指引
  d) 幂等根就绪：真仓 data/ 存在且可读（只读检查，不 mkdir）；不可读 = WARN

## 状态与退出码

  PASS = 有正向证据；FAIL = 反向证据（先查清）；WARN = 显式中性/预期形态，
  每条 WARN 证据必含「→」指向的处置动作，不再出现含糊 UNKNOWN
  （2026-10-18 收口判例：原 UNKNOWN 全部定性——chief 首跑前日志缺失为
  确定预期 → WARN；XML 读不到/解析失败、Ollama 探测异常、数据根不可读
  为探测面受限 → WARN + 人工复核指引）。

  2 = 存在 FAIL（开跑前先查清）
  0 = 无 FAIL 且有 PASS（可放行；WARN 不阻塞放行）
  1 = 无 FAIL 且无 PASS（全 WARN——探测面不可达，人工核对 --print-config）

## 红线

零写面：不创建目录、不写日志、不连网（仅本机 socket 探测 Ollama 端口）。

用法：

    python tools/check_trigger_chain.py --repo-root <agent_platform 根>
    python tools/check_trigger_chain.py --repo-root <agent_platform 根> --print-config
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple
import logging

#: 模块级日期注入点（2026-10-20，WO-TF-04）：判「今日」用此值，默认运行当天。
#: 沿用仓内既有约定 agent_platform/observe_point_review.py:26 的同名 TODAY，
#: 可被测试 rebind（monkeypatch.setattr(ctc, "TODAY", ...)）——既有夹具写死的
#: `shadow day <日期>` 由此可与判据对齐，无需把断言语义改成非 PASS。
TODAY = date.today().isoformat()

# ---------------------------------------------------------------------------
# 模块级常量（--print-config 打印，现场核对用）
# ---------------------------------------------------------------------------

# 计划任务 XML 直读目录（工作记忆判例：直读文件，不走 Get-ScheduledTask）
SYSTEM_TASKS_DIR = Path(r"C:\Windows\System32\Tasks")

# 三个任务的期望形态（锚点/触发时间取证自 2026-10-18 实测 XML）
TASK_SPECS = (
    {
        "name": "AgentShadowDaily",
        "hhmm": "17:10",
        "anchors": ("shadow_automation.ps1", "-Mode run"),
        "log": "last_run.log",
    },
    {
        "name": "AgentMondayCheckpoint",
        "hhmm": "17:25",
        "anchors": ("monday_checkpoint.py",),
        "log": "monday_checkpoint.log",
    },
    {
        "name": "AgentChiefPush",
        "hhmm": "17:35",
        "anchors": ("shadow_automation.ps1", "-Mode push"),
        "log": "chief_last_run.log",
    },
)

# 日志目录（相对 agent_platform 根；shadow_automation.ps1:24/42 重定向目标）
SHADOW_LOG_DIR_REL = ("data", "shadow")
# 2026-10-26 K-ORG-20261026-CHAINPATH：真仓脚本/数据都在 agent_platform/ 下
# （任务动作 = C:\aios\agent_platform\monday_checkpoint.py，run_shadow 数据根
# = <agent_platform>/data，见 run_shadow.py:64）；检查器此前按「仓库根」找，
# 导致 2026-10-25 16:40 误报「脚本缺失」。改为优先 agent_platform/，回退仓库根
# （保留单测夹具的旧布局兼容）。
_AP_DIR = "agent_platform"


def _first_existing(root: Path, *rels):
    """按 (agent_platform/..., ...) 顺序找第一个存在的路径；都不存在返回 None。"""
    for rel in rels:
        cand = Path(root).joinpath(*rel)
        if cand.exists():
            return cand
    return None


# daemon：本链硬依赖 = Ollama LLM 服务（run_shadow.py:341；门3检查项 7）
OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434
OLLAMA_TIMEOUT_S = 2.0

# daemon：watch-daemon/ack-serve 留痕（start_agent_platform.ps1，zhice-os-v2 仓）
# 机器绑定路径：换机后此探测静默失效，但不影响状态判定（仅信息性留痕，
# 见 _watchdog_trace）；--print-config 可核对实际取值。
AW_STACK_LOG = Path(r"C:\项目\zhice-os-v2-windows-20260920\code\logs\aw-stack.log")
PROCESS_PROBE_CMD = (
    "powershell",
    "-NoProfile",
    "-Command",
    "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\").CommandLine",
)
PROCESS_PROBE_TIMEOUT_S = 15.0
DAEMON_PROCESS_PATTERNS = (
    "agent_platform.cli watch-daemon",
    "agent_platform.cli ack-serve",
)

WARN, PASS, FAIL = "WARN", "PASS", "FAIL"

_DAY_CN = {
    "Monday": "一",
    "Tuesday": "二",
    "Wednesday": "三",
    "Thursday": "四",
    "Friday": "五",
    "Saturday": "六",
    "Sunday": "日",
}


# ---------------------------------------------------------------------------
# 通用小件
# ---------------------------------------------------------------------------


def _read_text_safe(path: Path, encoding: str = "utf-16") -> Optional[str]:
    """只读全文；不存在/不可读返回 None（fail-visible 由判据层决定状态）。"""
    try:
        if not path.exists():
            return None
        return path.read_text(encoding=encoding, errors="replace")
    except Exception:  # noqa: BLE001
        return None


def _clip(text: str, n: int = 100) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def aggregate(statuses) -> int:
    """汇总退出码：FAIL>0→2；有 PASS 无 FAIL→0；全 WARN（无正向证据）→1。"""
    if FAIL in statuses:
        return 2
    if PASS in statuses:
        return 0
    return 1


# ---------------------------------------------------------------------------
# 检查 a：任务注册面（任务 XML 直读解析）
# ---------------------------------------------------------------------------


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_task_xml(text: str) -> dict:
    """解析任务 XML，提取触发时间/天/命令行/Enabled（命名空间无关）。

    解析失败抛 ValueError（调用方转 WARN）。
    """
    root = ET.fromstring(text)  # nosec B314 - 仅解析本仓生成的任务链 XML
    info = {
        "startboundary": None,
        "hhmm": None,
        "days": [],
        "command": None,
        "arguments": "",
        "enabled_false": False,
    }
    for el in root.iter():
        tag = _local_tag(el.tag)
        if tag == "StartBoundary" and info["startboundary"] is None and el.text:
            info["startboundary"] = el.text.strip()
            m = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})", el.text.strip())
            if m:
                info["hhmm"] = m.group(1)
        elif tag == "DaysOfWeek":
            info["days"] = [
                _local_tag(c.tag) for c in el if _local_tag(c.tag) in _DAY_CN
            ]
        elif tag == "Command" and info["command"] is None and el.text:
            info["command"] = el.text.strip()
        elif tag == "Arguments" and el.text:
            info["arguments"] = el.text.strip()
        elif tag == "Enabled" and el.text and el.text.strip().lower() == "false":
            info["enabled_false"] = True
    return info


def _check_one_task(tasks_dir: Path, spec: dict) -> Tuple[str, str]:
    path = tasks_dir / spec["name"]
    text = _read_text_safe(path, encoding="utf-16")
    if text is None:
        # 不武断 FAIL：任务名可能不同，输出实际探测过的名字 + 处置指引
        return WARN, (
            "%s：读不到任务 XML（已探测 %s）→ 核对任务名"
            "（--print-config 列期望名）；确未注册→SOP 异常矩阵 #4，"
            "重注册须老田拍板" % (spec["name"], path)
        )
    try:
        info = parse_task_xml(text)
    except Exception as exc:  # noqa: BLE001
        return WARN, (
            "%s：XML 解析失败（%s）→ 人工核对 %s"
            "（任务计划程序 GUI 或 Get-ScheduledTask，异常矩阵 #4）"
            % (spec["name"], str(exc)[:60], path)
        )
    if not info["hhmm"] or not info["command"]:
        return WARN, (
            "%s：XML 可读但缺触发时间或 Command（形态未知）"
            "→ 人工核对任务实际形态（SOP 异常矩阵 #4）" % spec["name"]
        )
    cmdline = info["command"] + " " + info["arguments"]
    missing = [a for a in spec["anchors"] if a not in cmdline]
    if missing:
        return FAIL, (
            "%s：命令行锚点缺失 %s（实测 Command=%s Arguments=%s）"
            % (
                spec["name"],
                missing,
                _clip(info["command"], 60),
                _clip(info["arguments"], 80),
            )
        )
    if info["hhmm"] != spec["hhmm"]:
        return FAIL, "%s：触发时间 %s ≠ 期望 %s" % (
            spec["name"],
            info["hhmm"],
            spec["hhmm"],
        )
    if "Monday" not in info["days"]:
        return FAIL, "%s：触发日无周一（%s）——周一链必断" % (spec["name"], info["days"])
    if info["enabled_false"]:
        return FAIL, "%s：任务或触发器已禁用（Enabled=false）" % spec["name"]
    days = "周" + "".join(_DAY_CN.get(d, "?") for d in info["days"])
    return PASS, "%s %s %s 锚点齐" % (spec["name"], info["hhmm"], days)


def check_a_tasks(tasks_dir: Optional[Path] = None) -> Tuple[str, str]:
    tdir = Path(tasks_dir) if tasks_dir is not None else SYSTEM_TASKS_DIR
    results = [_check_one_task(tdir, spec) for spec in TASK_SPECS]
    statuses = [st for st, _ in results]
    detail = "；".join(ev for _, ev in results)
    if FAIL in statuses:
        return FAIL, detail
    if all(st == PASS for st in statuses):
        return PASS, detail
    return WARN, detail


# ---------------------------------------------------------------------------
# 检查 b：上次运行结果（三日志各自成功标记）
# ---------------------------------------------------------------------------


def _judge_shadow_log(text: str) -> Tuple[str, str]:
    """last_run.log：收尾行 `shadow day <日期> done: {...}`，done 计数 ≥1 = PASS。

    WO-TF-04（2026-10-20）：日志已由「每日覆盖」改为「跨日累积」（WO-TF-02 把
    包装器外层重定向改指 last_run_wrapper.log，last_run.log 转由 Python 侧单独
    持有），故「最后一条 shadow day 行」≠「最近一次跑的收尾行」——今日跑崩在
    中途（今日无收尾行）而历史存在成功行时，旧口径会读到**历史**那条并判 PASS，
    即假绿（改前每日覆盖时今日崩溃判 FAIL）。

    口径（**按日期**取最近一次，而非按出现顺序取最后一条）：
      1. 收尾行 = `shadow day <YYYY-MM-DD> done: {...}`；取**日期最大**的那条
         （同一日期多条时取最后一条），done 计数 ≥1 = PASS，零 done = FAIL。
      2. **日期门控（要求 2，防历史成功冒充今日通过）**：所取收尾行的日期**早于
         今日**（模块级 TODAY）⇒ 不得判 PASS——历史成功行不能被读成今日通过。
         文案须显式写出所依据那行的日期，使「今日未跑/今日崩了」看得见。
      3. 门控的严重度按本文件既有语义分层（真失败=FAIL，预期形态=WARN 带「→」
         处置指引）：日志里有**今日**的跑动头行 `===== shadow run start <ts> =====
         （run_shadow.py:443 无条件打印）⇒ 今日跑已开始却没写收尾行 = 真失败
         ⇒ FAIL；没有今日头行 ⇒ 今日尚未开跑（如 16:40 预检早于 17:10 跑批，
         属「首跑前属预期形态」）⇒ WARN，不误报成失败。

    **同日多次跑的补漏（2026-10-20 lead 独立复核发现，工位实现未覆盖）**：
    上面的「日期最大」口径在**同一日跑了多次**时仍会假绿——日志形如
    「早跑成功收尾行 + 晚跑崩溃（只有头行、无收尾行）」时，日期最大的收尾行
    仍是早跑那条（done≥1）⇒ 判 PASS，而**当晚那次跑其实崩了**。
    生产上同一日可多次跑（17:10 定时跑 + 手工补跑 + 演练跑），不是构造场景。

    故在日期门控**之前**加一道「今日最后一次跑是否崩在中途」的**否决判定**：
    取今日**最后一条跑动头行**（今日最后一次跑的起点），若该头行之后**没有**
    今日收尾行 ⇒ 说明今日最后一次跑没写收尾行（崩在中途）⇒ FAIL。
    这样「早成功 + 晚崩溃」判 FAIL，「早崩溃 + 晚成功」判 PASS，都以**今日最后
    一次跑**的真实结果为准，与 observe_point_review.py:49-63 的块级判读口径一致
    （那边也是按「今日 17: 块」判，块内无收尾行 = RED）。

    **该否决判定只判「崩在中途」，不重新选择所依据的收尾行**（这是刻意的，
    lead 变异测试实证）：若它同时把 chosen 改写为今日收尾行，就会**旁路**上面
    第 1 步的「按日期取」语义——工单判据 4（多日累积 + 今日成功行不在最后一条
    ⇒ 仍按日期取到今日那条并判 PASS）将永远为绿，「改按出现顺序取最后一条」
    这一回退**不可检出**（变异实测：改坏仍 45 passed）。故两条职责严格分离：
    第 1 步决定「所依据哪一行」，本步只做「是否崩在中途」的否决。
    """
    close_re = re.compile(r"^shadow day (\d{4}-\d{2}-\d{2}) done:")
    start_re = re.compile(r"^===== shadow run start (\d{4}-\d{2}-\d{2}) ")

    dated = []  # (date, idx, line)：全部带日期的收尾行
    starts = []  # 跑动头行的 (date, idx)
    for idx, line in enumerate(text.splitlines()):
        m = close_re.match(line)
        if m:
            dated.append((m.group(1), idx, line))
            continue
        m = start_re.match(line)
        if m:
            starts.append((m.group(1), idx))

    # 今日最后一次跑（今日最后一条跑动头行）的起点
    today_starts = [i for d, i in starts if d == TODAY]
    last_today_start = max(today_starts) if today_starts else None

    # 1) 按**日期**取最近一次跑的收尾行（不是按出现顺序取最后一条出现）。
    #    同一日期多条时取最后出现的那条——即该日**最后一次跑**的结果。
    if dated:
        chosen_date, chosen_idx, chosen_line = max(dated, key=lambda t: (t[0], t[1]))
    else:
        legacy = [l for l in text.splitlines() if l.startswith("shadow day ")]
        if not legacy:
            if last_today_start is not None:
                return FAIL, (
                    "last_run.log 无 shadow day 收尾行——今日 %s 的跑"
                    "已开始却未收尾（跑崩在中途）" % TODAY
                )
            return FAIL, "last_run.log 无 shadow day 收尾行（跑过但未正常收尾）"
        chosen_date, chosen_idx, chosen_line = "", -1, legacy[-1]

    # 2) 判据链（各段职责单一、互斥，不得互相旁路）：
    #    2a) **今日最后一次跑崩在中途**（同日多次跑补漏，lead 独立复核发现，
    #        工位实现未覆盖）：今日最后一条头行在所取收尾行**之后** ⇒ 今日最后
    #        一次跑没写收尾行 ⇒ FAIL（今日更早那次成功的收尾行不得冒充今日
    #        最后一次跑的结果）。
    #        ⚠ 本段**只做否决、不改写所依据的行**——若在此改写 chosen，会旁路
    #        第 1 步的「按日期取」语义，使工单判据 4（多日累积 + 今日成功行
    #        不在最后一条 ⇒ 仍按日期取到今日那条并判 PASS）永远为绿，「退回
    #        按出现顺序取最后一条」这一回退**不可检出**（变异实测：改坏仍全绿）。
    #    2b) **日期门控**（工单要求 2）：所取日期早于今日 ⇒ 不得判 PASS
    #        （历史成功不得冒充今日通过）；今日有头行 ⇒ 真失败 FAIL；今日无
    #        头行 ⇒ 今日尚未开跑（如 16:40 预检早于 17:10 跑批，属预期形态）
    #        ⇒ WARN 带「→」处置指引，不误报成失败。
    if chosen_date == TODAY:
        if last_today_start is not None and chosen_idx < last_today_start:
            return FAIL, (
                "今日 %s 最后一次影子跑（头行之后）无收尾行——跑崩在中途"
                "（今日更早那次成功不得冒充今日最后一次跑的结果）" % TODAY
            )
    elif chosen_date and chosen_date < TODAY:
        if last_today_start is not None:
            return FAIL, (
                "最近一次影子跑为 %s，但今日 %s 的跑已开始却无收尾行"
                "（历史成功不得冒充今日通过）" % (chosen_date, TODAY)
            )
        return WARN, (
            "最近一次影子跑为 %s，今日 %s 尚无跑动痕迹"
            "（今日未跑；历史成功不等于今日通过）→ 17:10 跑后复查该日志"
            % (chosen_date, TODAY)
        )

    # 3) 按 done 计数判（沿用既有语义）：done ≥1 = PASS，零 done = FAIL。
    tail = chosen_line.split("done:", 1)[-1] if "done:" in chosen_line else ""
    if re.search(r"'done':\s*[1-9]", tail):
        return PASS, _clip(chosen_line.strip(), 110)
    return FAIL, "上次影子跑零 done（%s）" % _clip(chosen_line.strip(), 90)


# ---------------------------------------------------------------------------
# 检查 b 辅助：monday_checkpoint.log 块级判读（2026-10-19 误报修复）
# ---------------------------------------------------------------------------
#: 巡检 run 块头行前缀（与 monday_checkpoint.py main() 头打印同源；改任一侧
#: 必须同步——块级判读靠它切分「一次运行一节」，判例：首跑写面地图 #8）
_CHECKPOINT_RUN_HEAD = "M5 刀4 周一观察点护航巡检 "
#: [wait] 行前缀：设计内 90s 等待循环的进度输出（SOP 手册：[wait] 行=在
#: 推进属正常；17:25 巡检必撞上首跑未完成，最长等 180 分钟）
_CHECKPOINT_WAIT_PREFIX = "[wait]"


def _split_checkpoint_blocks(text: str) -> list:
    """把 monday_checkpoint.log 切成 run 块（一次运行一节）。

    每块以 `M5 刀4 周一观察点护航巡检 <ts>` 头行起始、下一头行前收尾；
    返回 [{"head": <头行>, "lines": [体行...]}]。无头行（旧格式/手工
    摘要）返回 []——调用方回退旧末行口径。
    """
    blocks = []
    cur = None
    for line in text.splitlines():
        if line.startswith(_CHECKPOINT_RUN_HEAD):
            if cur is not None:
                blocks.append(cur)
            cur = {"head": line.strip(), "lines": []}
        elif cur is not None:
            cur["lines"].append(line)
    if cur is not None:
        blocks.append(cur)
    return blocks


def _checkpoint_block_ts(head: str) -> str:
    """块头行 → 运行时间戳（剥前缀，证据行用）。"""
    return head[len(_CHECKPOINT_RUN_HEAD) :].strip() or head


def _checkpoint_block_verdict(block: dict):
    """单块判读 → (status, evidence)；status=None = 未收尾（无结论/EXIT 行）。

    收尾块以 EXIT 行为准（EXIT=0 → PASS；EXIT≠0 → 真实 FAIL）；EXIT 行
    缺失但有结论行时以结论判（进程死于两行连续 print 之间的极端残迹，
    结论行仍透露真实失败——fail-visible 不因残迹降级）。
    """
    concl = exit_line = None
    for raw in block["lines"]:
        s = raw.strip()
        if s.startswith("结论:"):
            concl = s
        elif s.startswith("EXIT="):
            exit_line = s
    if exit_line is None and concl is None:
        return None, ""
    m = re.search(r"EXIT=(\d+)", exit_line or "")
    failed = bool(m and m.group(1) != "0") or (m is None and "FAIL" in (concl or ""))
    if concl and exit_line:
        # 证据带结论明细（哪几门未过）——排障面不止「失败了」三个字
        ev = "%s；%s" % (_clip(concl, 110), _clip(exit_line, 60))
    else:
        ev = _clip(exit_line or concl or "", 100)
    return (FAIL if failed else PASS), ev


def _judge_checkpoint_log(text: str) -> Tuple[str, str]:
    """monday_checkpoint.log：块级判读（2026-10-19 预期性误报修复）。

    旧口径「只看末行有无 EXIT=0」把两类**非失败**形态误判 FAIL：
      · 设计内 [wait] 等待循环进行中——17:25 巡检必撞上首跑未完成，每 90s
        重试最长 180 分钟（SOP 手册：[wait] 行=在推进，属正常）；
      · 预演/手跑被中断留下的 [wait] 残留块（无结论/EXIT 收尾行）。
    二者同构（末块无收尾行），故按 run 块判：
      · 末块已收尾 → 按其 EXIT/结论判：EXIT≠0 = 真实 checkpoint 失败
        （FAIL，带结论明细，fail-visible）；
      · 末块未收尾 → WARN（[wait] 残留/进行中不计 FAIL——预期性误报）；
        但若最近完整块是未被更新完整块取代的真实失败，仍报 FAIL
        （残留块不得掩盖未处置的失败）；
      · 无 run 头（旧格式/手工摘要）→ 回退旧末行口径。
    """
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return FAIL, "monday_checkpoint.log 存在但为空"
    blocks = _split_checkpoint_blocks(text)
    if not blocks:
        # 无 run 块头（历史格式/手工摘要）：维持旧末行口径
        last = lines[-1].strip()
        if "EXIT=0" in last:
            return PASS, "末行 %s" % _clip(last, 100)
        return FAIL, "末行无 EXIT=0：%s" % _clip(last, 90)
    last_blk = blocks[-1]
    st, ev = _checkpoint_block_verdict(last_blk)
    if st is not None:
        head = _checkpoint_block_ts(last_blk["head"])
        if st == PASS:
            return PASS, "末块 %s：%s" % (head, ev)
        return FAIL, (
            "末块 %s：%s——真实 checkpoint 失败（观察点门未过），"
            "按结论明细处置" % (head, ev)
        )
    # 末块未收尾：[wait] 设计内等待/中断残留 → 不判 FAIL（预期性误报修复）
    wait_n = sum(
        1 for l in last_blk["lines"] if l.strip().startswith(_CHECKPOINT_WAIT_PREFIX)
    )
    tail = (
        "末块 %s 未收尾（无结论/EXIT 行；[wait] 行 ×%d = 设计内 90s 等待循环"
        "或预演/手跑中断残留，非真实失败）"
        % (_checkpoint_block_ts(last_blk["head"]), wait_n)
    )
    for blk in reversed(blocks[:-1]):
        pst, pev = _checkpoint_block_verdict(blk)
        if pst is None:
            continue  # 更早的未收尾块：跳过，找最近完整块
        head = _checkpoint_block_ts(blk["head"])
        if pst == FAIL:
            return FAIL, (
                "%s；但最近完整块 %s 为真实失败且未被更新的完整块"
                "取代：%s" % (tail, head, pev)
            )
        return WARN, (
            "%s；最近完整块 %s：%s → 周一 17:25 首跑后复查末块结论行，"
            "届时末块无 EXIT=0（或 EXIT=1）→ SOP 异常矩阵 #2" % (tail, head, pev)
        )
    return WARN, (
        "%s；且无历史完整块可判读 → 周一 17:25 首跑后复查该日志，"
        "届时末块仍无 EXIT=0 → SOP 异常矩阵 #2" % tail
    )


def _judge_chief_log(text: str) -> Tuple[str, str]:
    """chief_last_run.log：须含 SEND_OK（仅有日报生成 ≠ 推送成功，门3 7.2）。"""
    if "SEND_OK" in text:
        return PASS, "含 SEND_OK（飞书推送成功标记）"
    return FAIL, "无 SEND_OK（日报生成≠推送成功，判例门3 7.2）"


_LOG_JUDGES = {
    "last_run.log": _judge_shadow_log,
    "monday_checkpoint.log": _judge_checkpoint_log,
    "chief_last_run.log": _judge_chief_log,
}


def check_b_last_runs(repo_root: Path) -> Tuple[str, str]:
    log_dir = _first_existing(
        repo_root, (_AP_DIR,) + SHADOW_LOG_DIR_REL, SHADOW_LOG_DIR_REL
    ) or Path(repo_root).joinpath(*SHADOW_LOG_DIR_REL)
    results = []
    for spec in TASK_SPECS:
        log_name = spec["log"]
        path = log_dir / log_name
        text = _read_text_safe(path, encoding="utf-8")
        if text is None:
            # 首跑（周 17:10/17:35 调度）前缺失是确定预期形态，显式 WARN + 复查指引
            results.append(
                (
                    WARN,
                    "%s：%s 不存在（LastTaskResult=267011 从未跑过，"
                    "首跑 %s 前缺失属预期）→ 周一 %s 首跑后复查该日志，"
                    "届时缺失/无标记→SOP 异常矩阵对应项"
                    % (spec["name"], path, spec["hhmm"], spec["hhmm"]),
                )
            )
            continue
        st, ev = _LOG_JUDGES[log_name](text)
        results.append((st, "%s[%s] %s" % (spec["name"], st, ev)))
    statuses = [st for st, _ in results]
    detail = "；".join(ev for _, ev in results)
    if FAIL in statuses:
        return FAIL, detail
    if all(st == PASS for st in statuses):
        return PASS, detail
    return WARN, detail


# ---------------------------------------------------------------------------
# 检查 c：daemon 存活（Ollama 决定性 + watch-daemon 留痕信息性）
# ---------------------------------------------------------------------------


def _default_connect(host: str, port: int) -> None:
    sock = socket.create_connection((host, port), timeout=OLLAMA_TIMEOUT_S)
    sock.close()


def _default_process_probe() -> Optional[List[str]]:
    """按 start_agent_platform.ps1:27-30 同款进程特征探测；失败返回 None。"""
    try:
        proc = subprocess.run(
            PROCESS_PROBE_CMD, capture_output=True, timeout=PROCESS_PROBE_TIMEOUT_S
        )
        return [
            l.strip()
            for l in proc.stdout.decode("utf-8", "replace").splitlines()
            if l.strip()
        ]
    except Exception:  # noqa: BLE001
        return None


def _watchdog_trace(process_probe, aw_stack_log: Optional[Path]) -> str:
    """watch-daemon/ack-serve 留痕（信息性，不翻转检查 c 状态）。"""
    try:
        lines = process_probe() if process_probe else None
        if lines:
            hits = [
                l
                for l in lines
                if any(
                    p in l.replace("\\", " ").replace('"', " ")
                    for p in DAEMON_PROCESS_PATTERNS
                )
            ]
            if hits:
                return "watch-daemon/ack-serve 进程特征在跑（%d 个）" % len(hits)
        if aw_stack_log is not None:
            text = _read_text_safe(aw_stack_log, encoding="utf-8")
            if text:
                pulls = [l for l in text.splitlines() if "pulled up" in l]
                if pulls:
                    return "watch-daemon 拉起留痕：%s" % _clip(pulls[-1].strip(), 60)
            return "watch-daemon 无留痕（AgentWatchdog 已知损坏待修，非本链阻塞——M6 判例 7.4）"
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/tools/check_trigger_chain.py", exc)
    return "watch-daemon 留痕不可探"


def check_c_daemon(
    connect_fn=None, process_probe=None, aw_stack_log: Optional[Path] = None
) -> Tuple[str, str]:
    cf = connect_fn if connect_fn is not None else _default_connect
    try:
        cf(OLLAMA_HOST, OLLAMA_PORT)
        status, ev = (
            PASS,
            "Ollama %s:%d 可连（run_shadow LLM 硬依赖，run_shadow.py:341）"
            % (OLLAMA_HOST, OLLAMA_PORT),
        )
    except ConnectionRefusedError:
        status, ev = (
            FAIL,
            (
                "Ollama %s:%d 无监听（拒绝连接）——17:10 run_shadow 必挂，"
                "先拉起 Ollama 服务" % (OLLAMA_HOST, OLLAMA_PORT)
            ),
        )
    except Exception as exc:  # noqa: BLE001
        status, ev = (
            WARN,
            (
                "Ollama %s:%d 探测异常（%s，非拒绝连接——拒绝=明确 FAIL）"
                "→ 手动复核 curl -s --max-time 3 "
                "http://127.0.0.1:11434/api/ps，17:10 前必须确认可连"
                "（SOP 异常矩阵 #3）" % (OLLAMA_HOST, OLLAMA_PORT, str(exc)[:80])
            ),
        )
    trace = _watchdog_trace(
        process_probe, Path(aw_stack_log) if aw_stack_log else AW_STACK_LOG
    )
    return status, "%s；%s" % (ev, trace)


# ---------------------------------------------------------------------------
# 检查 d：幂等根就绪（真仓 data/ 存在且可读，只读不 mkdir）
# ---------------------------------------------------------------------------


def check_d_data_root(repo_root: Path) -> Tuple[str, str]:
    data = _first_existing(Path(repo_root), (_AP_DIR, "data"), ("data",)) or (
        Path(repo_root) / "data"
    )
    if not data.exists():
        return (
            FAIL,
            "真仓数据根不存在：%s（17:10 run 无处落账，run_shadow.py:64）" % data,
        )
    try:
        entries = os.listdir(data)
    except Exception as exc:  # noqa: BLE001
        return WARN, (
            "数据根不可读（%s：%s）→ 检查目录权限/被占用；不排除则 "
            "17:10 run 无处落账（run_shadow.py:64），报 lead 定夺"
            % (data, str(exc)[:80])
        )
    parts = ["数据根可读（%d 项）" % len(entries)]
    shadow = data / "shadow"
    if shadow.is_dir():
        parts.append("shadow/ 存在（%d 项）" % len(os.listdir(shadow)))
    else:
        parts.append("shadow/ 不存在（首跑 17:10 由 run_shadow 落建）")
    idem = data / "idempotency"
    if idem.is_dir():
        parts.append("idempotency/ %d 项（空=首次运行属预期）" % len(os.listdir(idem)))
    return PASS, "；".join(parts)


# ---------------------------------------------------------------------------
# e 代码可导入性（T-30min 补判据，2026-10-18）
# ---------------------------------------------------------------------------
#: 只做语法编译的目标（顶层无 __main__ 守卫风险，不做真导入）
CODE_COMPILE_ONLY = ("monday_checkpoint.py", "a0_chief.py")
#: 做真导入的目标 —— 已实证顶层有 `if __name__ == "__main__"` 守卫
#: （run_shadow.py:451），导入零副作用、不写账本、不触发 LLM。
CODE_IMPORT_PROBE = ("run_shadow.py", "import run_shadow")
#: 导入子进程超时（秒）：需加载 pandas/numpy，留足余量
CODE_IMPORT_TIMEOUT_S = 60


def check_e_code_importable(repo_root: Path) -> Tuple[str, str]:
    """E 代码可导入性 —— 明天真正要跑的脚本是否还能被拉起。

    存在理由（2026-10-18 T-21h 补）：a~d 四条判据覆盖「任务注册 / 历史结果 /
    进程存活 / 数据根」，对**代码本身被改坏**全盲。首跑前夜 run_shadow.py 恰有
    在途重构（超时包裹收敛到 engine/strategy_runtime），若导入链断，17:10 首跑
    直接 ImportError，且当日无补救窗口（下次触发要等周二）。
    故补语法编译 + 顶层导入两段，把「代码坏了」前移到 16:40 暴露。

    口径：语法失败=FAIL（必挂）；导入失败=FAIL（必挂）；均为 fail-visible，
    不降级为 WARN —— 这类问题到 17:10 就是硬失败，没有「到点复查」的余地。
    """
    root = Path(repo_root).resolve()  # 必须 resolve：下面 subprocess 传 cwd=str(root)，
    # 若 root 为相对路径，子进程会把它与 cwd 二次拼接
    # （agent_platform/agent_platform/x.py）导致假 FAIL。
    py = sys.executable
    parts = []
    fails = []

    # 1) 语法编译（三件）：优先 agent_platform/，回退仓库根
    for name in (CODE_IMPORT_PROBE[0],) + CODE_COMPILE_ONLY:
        src = _first_existing(root, (_AP_DIR, name), (name,))
        if src is None or not src.is_file():
            fails.append("%s 缺失（首跑必挂）" % name)
            continue
        proc = subprocess.run(
            [py, "-X", "utf8", "-m", "py_compile", str(src)],
            capture_output=True,
            timeout=CODE_IMPORT_TIMEOUT_S,
            cwd=str(root),
        )
        if proc.returncode != 0:
            fails.append(
                "%s 语法编译失败：%s"
                % (name, proc.stderr.decode("utf-8", errors="replace")[-160:].strip())
            )
        else:
            parts.append("%s 语法通过" % name)

    # 2) 真导入（run_shadow，零副作用）
    name, stmt = CODE_IMPORT_PROBE
    if not fails:
        try:
            _probe = _first_existing(root, (_AP_DIR, name), (name,))
            _cwd = str(_probe.parent) if _probe is not None else str(root)
            # 宿主可能启用 PYTHONSAFEPATH=1（-c 不把 cwd 加入 sys.path），
            # 因此显式注入探测目录，避免把环境策略误判成代码 ImportError。
            probe_stmt = ("import sys; sys.path.insert(0, %r); " % _cwd) + stmt
            proc = subprocess.run(
                [py, "-X", "utf8", "-c", probe_stmt],
                capture_output=True,
                timeout=CODE_IMPORT_TIMEOUT_S,
                cwd=_cwd,
            )
            if proc.returncode != 0:
                fails.append(
                    "%s 顶层导入失败（17:10 首跑直接 ImportError）：%s"
                    % (
                        name,
                        proc.stderr.decode("utf-8", errors="replace")[-300:].strip(),
                    )
                )
            else:
                parts.append("%s 顶层导入通过（__main__ 守卫下零副作用）" % name)
        except subprocess.TimeoutExpired:
            fails.append(
                "%s 顶层导入超时 %ds（疑模块级阻塞）" % (name, CODE_IMPORT_TIMEOUT_S)
            )
        except Exception as exc:  # noqa: BLE001
            fails.append("%s 导入探测自身异常：%r" % (name, exc))

    if fails:
        return FAIL, "；".join(fails) + " → 17:10 前必须修好，否则影子首跑当日空跑"
    return PASS, "；".join(parts)


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

CHECKS = (
    ("a_task_registration", lambda root: check_a_tasks()),
    ("b_last_run_results", check_b_last_runs),
    ("c_daemon_alive", lambda root: check_c_daemon()),
    ("d_data_root_ready", check_d_data_root),
    ("e_code_importable", check_e_code_importable),
)


def run_checks(
    repo_root, tasks_dir=None, connect_fn=None, process_probe=None, aw_stack_log=None
) -> dict:
    """前置自检主入口（只读）。注入参数供测试用（不读真系统）。"""
    root = Path(repo_root)
    st, ev = check_a_tasks(tasks_dir)
    items = [{"name": CHECKS[0][0], "status": st, "evidence": ev}]
    for name, fn in CHECKS[1:]:
        if name == "c_daemon_alive":
            st, ev = check_c_daemon(connect_fn, process_probe, aw_stack_log)
        else:
            st, ev = fn(root)
        items.append({"name": name, "status": st, "evidence": ev})
    statuses = [it["status"] for it in items]
    return {
        "root": str(root),
        "items": items,
        "pass": sum(1 for s in statuses if s == PASS),
        "warn": sum(1 for s in statuses if s == WARN),
        "fail": sum(1 for s in statuses if s == FAIL),
        "exit_code": aggregate(statuses),
    }


def print_config() -> None:
    print("=" * 62)
    print("check_trigger_chain 配置面（现场核对用）")
    print("=" * 62)
    print("SYSTEM_TASKS_DIR = %s" % SYSTEM_TASKS_DIR)
    for spec in TASK_SPECS:
        print(
            "任务 %-22s 期望触发 %s  锚点 %s  结果日志 %s"
            % (spec["name"], spec["hhmm"], " + ".join(spec["anchors"]), spec["log"])
        )
    print("日志目录 = <repo-root>/%s" % "/".join(SHADOW_LOG_DIR_REL))
    print(
        "成功标记：last_run.log=shadow day 行 done≥1；"
        "monday_checkpoint.log=末个 run 块 EXIT=0（[wait] 残留/进行中块未收尾"
        "=WARN 不计 FAIL）；chief_last_run.log=SEND_OK"
    )
    print(
        "Ollama = %s:%d（timeout %.0fs）" % (OLLAMA_HOST, OLLAMA_PORT, OLLAMA_TIMEOUT_S)
    )
    print("watch-daemon 留痕 = %s" % AW_STACK_LOG)
    print("进程探测 = %s" % " ".join(PROCESS_PROBE_CMD))


def _print_report(rep: dict) -> None:
    print("=" * 62)
    print("周一外部触发链前置自检 %s" % rep["root"])
    print("=" * 62)
    for it in rep["items"]:
        print("  [%s] %s —— %s" % (it["status"], it["name"], it["evidence"]))
    print("汇总: PASS %d / WARN %d / FAIL %d" % (rep["pass"], rep["warn"], rep["fail"]))
    verdict = {
        0: "无 FAIL 且有 PASS——链路就绪，可放行（WARN 项按其指引到点复查；退出码 0）",
        1: "全 WARN 无正向 PASS——探测面不可达，人工核对 --print-config（退出码 1）",
        2: "存在 FAIL——17:10 前先查清（退出码 2）",
    }[rep["exit_code"]]
    print("结论: %s" % verdict)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="周一外部触发链前置自检（只读，零写面，fail-visible）"
    )
    ap.add_argument(
        "--repo-root",
        required=True,
        help="agent_platform 根（显式必填——显式 root 纪律）",
    )
    ap.add_argument(
        "--print-config",
        action="store_true",
        help="只打印任务名/日志路径等配置常量后退出，不做检查",
    )
    args = ap.parse_args(argv)
    if args.print_config:
        print_config()
        return 0
    rep = run_checks(args.repo_root)
    _print_report(rep)
    return rep["exit_code"]


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
