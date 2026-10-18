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
                          无条件打印）；done 计数 ≥1 = PASS，跑过但全 failed = FAIL
    monday_checkpoint.log 末行含 EXIT=0（追加式日志，只看末行防旧 run 假绿）
    chief_last_run.log    须含 SEND_OK——只有「日报已生成」而无 SEND_OK =
                          推送失败（门3检查 7.2 判例）
  日志缺失 = UNKNOWN 中性：AgentShadowDaily / AgentChiefPush LastTaskResult=
  267011 从未跑过，首跑（周一 17:10/17:35）前缺失属预期形态。
- daemon 留痕取证：start_agent_platform.ps1（zhice-os-v2 仓 deploy/）产出的
  watch-daemon/ack-serve **无 pid 文件**，留痕 = Win32_Process CommandLine
  匹配 'agent_platform\\.cli (watch-daemon|ack-serve)'（start_agent_platform.ps1:27-30）
  + 拉起日志 code/logs/aw-stack.log。本链真正的硬依赖是 Ollama LLM 服务
  （run_shadow.py:341 OllamaLLM；门3检查项 7）：socket 探 11434 端口，
  无监听 = FAIL，其他异常 = UNKNOWN。watch-daemon 留痕只作证据行不翻转状态
  （AgentWatchdog 已知损坏待管理员修复，M6 判例 7.4，非本链阻塞项）。
- 幂等根：run_shadow 真仓数据根 = <agent_platform>/data（run_shadow.py:64），
  存在且可读 = PASS；缺失 = FAIL（17:10 无处落账）。

## 检查项（每项 PASS/FAIL/UNKNOWN + 一行证据）

  a) 任务注册面：三个任务 XML 直读解析触发时间/命令行锚点/周一触发/Enabled；
     读不到文件 = UNKNOWN（不武断 FAIL——可能任务名不同，输出实际探测过的名字）
  b) 上次运行结果：三个日志按各自成功标记判读（见上）
  c) daemon 存活：Ollama 11434 socket 探测（决定性）+ watch-daemon 留痕（信息性）
  d) 幂等根就绪：真仓 data/ 存在且可读（只读检查，不 mkdir）

## 退出码

  2 = 存在 FAIL（开跑前先查清）
  0 = 无 FAIL 且有 PASS（可放行）
  1 = 全 UNKNOWN（中性——探测面全不可达，非失败）

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
from pathlib import Path
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# 模块级常量（--print-config 打印，现场核对用）
# ---------------------------------------------------------------------------

# 计划任务 XML 直读目录（工作记忆判例：直读文件，不走 Get-ScheduledTask）
SYSTEM_TASKS_DIR = Path(r"C:\Windows\System32\Tasks")

# 三个任务的期望形态（锚点/触发时间取证自 2026-10-18 实测 XML）
TASK_SPECS = (
    {"name": "AgentShadowDaily", "hhmm": "17:10",
     "anchors": ("shadow_automation.ps1", "-Mode run"),
     "log": "last_run.log"},
    {"name": "AgentMondayCheckpoint", "hhmm": "17:25",
     "anchors": ("monday_checkpoint.py",),
     "log": "monday_checkpoint.log"},
    {"name": "AgentChiefPush", "hhmm": "17:35",
     "anchors": ("shadow_automation.ps1", "-Mode push"),
     "log": "chief_last_run.log"},
)

# 日志目录（相对 agent_platform 根；shadow_automation.ps1:24/42 重定向目标）
SHADOW_LOG_DIR_REL = ("data", "shadow")

# daemon：本链硬依赖 = Ollama LLM 服务（run_shadow.py:341；门3检查项 7）
OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434
OLLAMA_TIMEOUT_S = 2.0

# daemon：watch-daemon/ack-serve 留痕（start_agent_platform.ps1，zhice-os-v2 仓）
AW_STACK_LOG = Path(r"C:\项目\zhice-os-v2-windows-20260920\code\logs\aw-stack.log")
PROCESS_PROBE_CMD = (
    "powershell", "-NoProfile", "-Command",
    "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\").CommandLine",
)
PROCESS_PROBE_TIMEOUT_S = 15.0
DAEMON_PROCESS_PATTERNS = ("agent_platform.cli watch-daemon", "agent_platform.cli ack-serve")

UNKNOWN, PASS, FAIL = "UNKNOWN", "PASS", "FAIL"

_DAY_CN = {"Monday": "一", "Tuesday": "二", "Wednesday": "三",
           "Thursday": "四", "Friday": "五", "Saturday": "六", "Sunday": "日"}


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
    """汇总退出码：FAIL>0→2；有 PASS 无 FAIL→0；全 UNKNOWN→1。"""
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

    解析失败抛 ValueError（调用方转 UNKNOWN）。
    """
    root = ET.fromstring(text)
    info = {"startboundary": None, "hhmm": None, "days": [],
            "command": None, "arguments": "", "enabled_false": False}
    for el in root.iter():
        tag = _local_tag(el.tag)
        if tag == "StartBoundary" and info["startboundary"] is None and el.text:
            info["startboundary"] = el.text.strip()
            m = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})", el.text.strip())
            if m:
                info["hhmm"] = m.group(1)
        elif tag == "DaysOfWeek":
            info["days"] = [_local_tag(c.tag) for c in el if _local_tag(c.tag) in _DAY_CN]
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
        # 不武断 FAIL：任务名可能不同，输出实际探测过的名字
        return UNKNOWN, "%s：读不到任务 XML（已探测 %s）" % (spec["name"], path)
    try:
        info = parse_task_xml(text)
    except Exception as exc:  # noqa: BLE001
        return UNKNOWN, "%s：XML 解析失败（%s）：%s" % (spec["name"], str(exc)[:60], path)
    if not info["hhmm"] or not info["command"]:
        return UNKNOWN, "%s：XML 可读但缺触发时间或 Command（形态未知）" % spec["name"]
    cmdline = info["command"] + " " + info["arguments"]
    missing = [a for a in spec["anchors"] if a not in cmdline]
    if missing:
        return FAIL, ("%s：命令行锚点缺失 %s（实测 Command=%s Arguments=%s）"
                      % (spec["name"], missing, _clip(info["command"], 60),
                         _clip(info["arguments"], 80)))
    if info["hhmm"] != spec["hhmm"]:
        return FAIL, "%s：触发时间 %s ≠ 期望 %s" % (spec["name"], info["hhmm"], spec["hhmm"])
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
    return UNKNOWN, detail


# ---------------------------------------------------------------------------
# 检查 b：上次运行结果（三日志各自成功标记）
# ---------------------------------------------------------------------------

def _judge_shadow_log(text: str) -> Tuple[str, str]:
    """last_run.log：收尾行 `shadow day <日期> done: {...}`，done 计数 ≥1 = PASS。"""
    for line in reversed(text.splitlines()):
        if line.startswith("shadow day "):
            tail = line.split("done:", 1)[-1] if "done:" in line else ""
            if re.search(r"'done':\s*[1-9]", tail):
                return PASS, _clip(line.strip(), 110)
            return FAIL, "上次影子跑零 done（%s）" % _clip(line.strip(), 90)
    return FAIL, "last_run.log 无 shadow day 收尾行（跑过但未正常收尾）"


def _judge_checkpoint_log(text: str) -> Tuple[str, str]:
    """monday_checkpoint.log：追加式日志，只看末行防旧 run 假绿。"""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return FAIL, "monday_checkpoint.log 存在但为空"
    last = lines[-1].strip()
    if "EXIT=0" in last:
        return PASS, "末行 %s" % _clip(last, 100)
    return FAIL, "末行无 EXIT=0：%s" % _clip(last, 90)


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
    log_dir = Path(repo_root).joinpath(*SHADOW_LOG_DIR_REL)
    results = []
    for spec in TASK_SPECS:
        log_name = spec["log"]
        path = log_dir / log_name
        text = _read_text_safe(path, encoding="utf-8")
        if text is None:
            results.append((UNKNOWN, "%s：%s 不存在（267011 从未跑过，首跑前中性）"
                            % (spec["name"], path)))
            continue
        st, ev = _LOG_JUDGES[log_name](text)
        results.append((st, "%s[%s] %s" % (spec["name"], st, ev)))
    statuses = [st for st, _ in results]
    detail = "；".join(ev for _, ev in results)
    if FAIL in statuses:
        return FAIL, detail
    if all(st == PASS for st in statuses):
        return PASS, detail
    return UNKNOWN, detail


# ---------------------------------------------------------------------------
# 检查 c：daemon 存活（Ollama 决定性 + watch-daemon 留痕信息性）
# ---------------------------------------------------------------------------

def _default_connect(host: str, port: int) -> None:
    sock = socket.create_connection((host, port), timeout=OLLAMA_TIMEOUT_S)
    sock.close()


def _default_process_probe() -> Optional[List[str]]:
    """按 start_agent_platform.ps1:27-30 同款进程特征探测；失败返回 None。"""
    try:
        proc = subprocess.run(PROCESS_PROBE_CMD, capture_output=True,
                              timeout=PROCESS_PROBE_TIMEOUT_S)
        return [l.strip() for l in proc.stdout.decode("utf-8", "replace").splitlines()
                if l.strip()]
    except Exception:  # noqa: BLE001
        return None


def _watchdog_trace(process_probe, aw_stack_log: Optional[Path]) -> str:
    """watch-daemon/ack-serve 留痕（信息性，不翻转检查 c 状态）。"""
    try:
        lines = process_probe() if process_probe else None
        if lines:
            hits = [l for l in lines
                    if any(p in l.replace("\\", " ").replace('"', " ")
                           for p in DAEMON_PROCESS_PATTERNS)]
            if hits:
                return "watch-daemon/ack-serve 进程特征在跑（%d 个）" % len(hits)
        if aw_stack_log is not None:
            text = _read_text_safe(aw_stack_log, encoding="utf-8")
            if text:
                pulls = [l for l in text.splitlines() if "pulled up" in l]
                if pulls:
                    return "watch-daemon 拉起留痕：%s" % _clip(pulls[-1].strip(), 60)
            return "watch-daemon 无留痕（AgentWatchdog 已知损坏待修，非本链阻塞——M6 判例 7.4）"
    except Exception:  # noqa: BLE001
        pass
    return "watch-daemon 留痕不可探"


def check_c_daemon(connect_fn=None, process_probe=None,
                   aw_stack_log: Optional[Path] = None) -> Tuple[str, str]:
    cf = connect_fn if connect_fn is not None else _default_connect
    try:
        cf(OLLAMA_HOST, OLLAMA_PORT)
        status, ev = PASS, "Ollama %s:%d 可连（run_shadow LLM 硬依赖，run_shadow.py:341）" % (
            OLLAMA_HOST, OLLAMA_PORT)
    except ConnectionRefusedError:
        status, ev = FAIL, ("Ollama %s:%d 无监听（拒绝连接）——17:10 run_shadow 必挂，"
                            "先拉起 Ollama 服务" % (OLLAMA_HOST, OLLAMA_PORT))
    except Exception as exc:  # noqa: BLE001
        status, ev = UNKNOWN, "Ollama %s:%d 探测异常（%s）" % (
            OLLAMA_HOST, OLLAMA_PORT, str(exc)[:80])
    trace = _watchdog_trace(process_probe,
                            Path(aw_stack_log) if aw_stack_log else AW_STACK_LOG)
    return status, "%s；%s" % (ev, trace)


# ---------------------------------------------------------------------------
# 检查 d：幂等根就绪（真仓 data/ 存在且可读，只读不 mkdir）
# ---------------------------------------------------------------------------

def check_d_data_root(repo_root: Path) -> Tuple[str, str]:
    data = Path(repo_root) / "data"
    if not data.exists():
        return FAIL, "真仓数据根不存在：%s（17:10 run 无处落账，run_shadow.py:64）" % data
    try:
        entries = os.listdir(data)
    except Exception as exc:  # noqa: BLE001
        return UNKNOWN, "数据根不可读（%s）：%s" % (data, str(exc)[:80])
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
# 汇总
# ---------------------------------------------------------------------------

CHECKS = (
    ("a_task_registration", lambda root: check_a_tasks()),
    ("b_last_run_results", check_b_last_runs),
    ("c_daemon_alive", lambda root: check_c_daemon()),
    ("d_data_root_ready", check_d_data_root),
)


def run_checks(repo_root, tasks_dir=None, connect_fn=None,
               process_probe=None, aw_stack_log=None) -> dict:
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
    return {"root": str(root), "items": items,
            "pass": sum(1 for s in statuses if s == PASS),
            "unknown": sum(1 for s in statuses if s == UNKNOWN),
            "fail": sum(1 for s in statuses if s == FAIL),
            "exit_code": aggregate(statuses)}


def print_config() -> None:
    print("=" * 62)
    print("check_trigger_chain 配置面（现场核对用）")
    print("=" * 62)
    print("SYSTEM_TASKS_DIR = %s" % SYSTEM_TASKS_DIR)
    for spec in TASK_SPECS:
        print("任务 %-22s 期望触发 %s  锚点 %s  结果日志 %s" % (
            spec["name"], spec["hhmm"], " + ".join(spec["anchors"]), spec["log"]))
    print("日志目录 = <repo-root>/%s" % "/".join(SHADOW_LOG_DIR_REL))
    print("成功标记：last_run.log=shadow day 行 done≥1；"
          "monday_checkpoint.log=末行 EXIT=0；chief_last_run.log=SEND_OK")
    print("Ollama = %s:%d（timeout %.0fs）" % (OLLAMA_HOST, OLLAMA_PORT, OLLAMA_TIMEOUT_S))
    print("watch-daemon 留痕 = %s" % AW_STACK_LOG)
    print("进程探测 = %s" % " ".join(PROCESS_PROBE_CMD))


def _print_report(rep: dict) -> None:
    print("=" * 62)
    print("周一外部触发链前置自检 %s" % rep["root"])
    print("=" * 62)
    for it in rep["items"]:
        print("  [%s] %s —— %s" % (it["status"], it["name"], it["evidence"]))
    print("汇总: PASS %d / UNKNOWN %d / FAIL %d" % (rep["pass"], rep["unknown"], rep["fail"]))
    verdict = {0: "无 FAIL 且有 PASS——链路就绪，可放行（退出码 0）",
               1: "全 UNKNOWN——中性：探测面不可达，人工核对 --print-config（退出码 1）",
               2: "存在 FAIL——17:10 前先查清（退出码 2）"}[rep["exit_code"]]
    print("结论: %s" % verdict)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="周一外部触发链前置自检（只读，零写面，fail-visible）")
    ap.add_argument("--repo-root", required=True,
                    help="agent_platform 根（显式必填——显式 root 纪律）")
    ap.add_argument("--print-config", action="store_true",
                    help="只打印任务名/日志路径等配置常量后退出，不做检查")
    args = ap.parse_args(argv)
    if args.print_config:
        print_config()
        return 0
    rep = run_checks(args.repo_root)
    _print_report(rep)
    return rep["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
