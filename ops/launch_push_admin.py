# -*- coding: utf-8 -*-
"""8097 推送中台幂等守护拉起器（计划任务宿主用，K-ORG-113 配套）。

由计划任务「AI工作站推送中台守护8097」每 5 分钟触发：
  - 幂等：8097 已监听则零副作用退出（socket 探测）
  - 单层分离：DETACHED+DEVNULL 直接跑 server（不走 --bg 双层 spawn）
  - 环境以系统 PATH 为底重建（start_hermes_bg.py 同款判例配方）

2026-10-16 实证矩阵（新机）：从交互终端 spawn 的分离进程
（DETACHED/CREATE_NO_WINDOW）全部 <160s 静默死；前台活；旧机计划任务
环境 spawn 的同款进程活几小时（watchdog_9899.log 实证）——故本脚本
必须由计划任务宿主运行，勿在交互终端直接跑。
"""
import os
import socket
import subprocess
import sys
import time

# ── 一次性强重置（K-PUSH-003 判例 2026-10-17）─────────────────
# 动机：8097 宿主是 SYSTEM 计划任务，用户终端无权 taskkill 旧进程，
# 码更新后旧进程因 /health 正常永远不会被守护换血。用法：
#   touch C:\Temp\push_admin_restart.marker
# 本任务下一跳（≤5min）以 SYSTEM 杀掉 8097 监听进程，再按磁盘新码
# 重拉；marker 用后即删，恢复原幂等语义（一次性自毁）。
MARKER = r"C:\Temp\push_admin_restart.marker"
if os.path.exists(MARKER):
    try:
        os.remove(MARKER)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "ops/launch_push_admin.py", exc)
    _ns = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                         capture_output=True).stdout.decode("gbk", "replace")
    for _ln in _ns.splitlines():
        if ":8097" in _ln and "LISTENING" in _ln:
            _pid = _ln.split()[-1]
            if _pid.isdigit():
                subprocess.run(["taskkill", "/F", "/PID", _pid],
                               capture_output=True)
                print("[restart] killed listener pid %s (marker consumed)"
                      % _pid)

# ── bot 看护（K-PUSH-004 2026-10-17：看护链断裂修复+marker 换血）──
# 背景：原 FeishuBotWatchdog 计划任务在本机不存在（Get-ScheduledTask
# 全量无引用 start_feishu_bot），bot 断看护链；bot 宿主 SYSTEM，用户
# 终端无权 taskkill（同 8097 判例）。本任务（SYSTEM 宿主）兼任 bot 守护：
#   a) 幂等确保：无逻辑 bot 实例 → 拉起 start_feishu_bot.py（自身幂等）
#   b) marker 强重置：touch C:\Temp\feishu_bot_restart.marker
#      → SYSTEM 杀全部 feishu_bot_ws 进程后重拉（码更新换血通道，用后即删）
#   c) 去重：>1 逻辑实例只留最新（树根判据，见 _roots_of 注释）
#   d) 诊断：touch C:\Temp\proc_dump.marker → SYSTEM 视角全量 python
#      命令行落 C:\Temp\proc_dump.txt（用户终端读不到 SYSTEM CL，排查用）
#
# 判例（K-PUSH-004 2026-10-17 上午）：本任务有 2 分钟执行上限
# （Stop Task If Runs 00:02:00），早期版本最坏 4 次 CIM×60s=240s，
# 任务在 bot 段被强杀（Job 连坐杀掉刚 Popen 的子进程），永远走不到
# 8097 拉起（Last Result=1、8097/bot 双灭实证）。故本段必须：
#   1) 单次 CIM 快查（timeout 25s）供 marker/dedup/ensure 共用
#   2) 整段 try/except 兜底——bot 守护失败不阻断 8097 主职
import re as _re
import logging

BOT_MARKER = r"C:\Temp\feishu_bot_restart.marker"
DUMP_MARKER = r"C:\Temp\proc_dump.marker"
# D1 冷却窗口（DD-7 2026-10-19，窗口值 300s）：实证依据 DNS 抖动期
# 13:05-13:22 bot 单日 19 次重启大战——距上次强重置 <300s 且无逻辑
# bot 实例时跳过拉起，抑制「拉起→崩溃→再拉起」的连续重启。重置时刻
# 取 BOT_MARKER 的 mtime，消耗后落 sidecar 供下跳判定（marker 用后
# 即删的一次性语义不变）；窗口值可调。
BOT_COOLDOWN_SEC = 300
BOT_RESET_TS = r"C:\Temp\feishu_bot_reset_ts.txt"
_VENV_PY = r"c:\项目\老田的AI助手\CowAgent\venv\Scripts\python.exe"
_BOT_LAUNCHER = r"c:\项目\老田的AI助手\portal\start_feishu_bot.py"


def _bot_snapshot():
    """一次 CIM 查询 bot 进程表 [(pid, ppid, created)]（SYSTEM 视角，
    交互终端读不到 SYSTEM 进程命令行）。失败/超时返回 []，调用方按
    「查不到=不动作」处理，宁可漏拉不可误杀。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\")"
             "|Where-Object{$_.CommandLine -like '*feishu_bot_ws*'}"
             "|Select-Object ProcessId,ParentProcessId,CreationDate"
             "|ConvertTo-Json -Compress"],
            capture_output=True, timeout=25)
    except Exception:
        return []
    out = (r.stdout or b"").decode("gbk", "replace")
    return [(int(m.group(1)), int(m.group(2)), m.group(3))
            for m in _re.finditer(
                r'"ProcessId":\s*(\d+),"ParentProcessId":\s*(\d+),'
                r'"CreationDate":\s*"([^"]+)"', out)]


def _roots_of(procs):
    """逻辑 bot 实例（进程树根）[(pid, created)]，旧→新。

    判例（K-PUSH-004 2026-10-17 上午实测）：CowAgent venv 的 python
    垫片会以同命令行再 spawn 真实进程（+20ms 父子对，昨夜稳定运行的
    50540+56868 即一对）——按进程数去重会把单实例误杀垫片，引发
    ~74s 重启大战。逻辑实例数 =「父进程不在 bot 集合内」的树根数；
    杀旧一律 taskkill /T 连树清（垫片+真实进程一起走）。
    """
    pidset = {p for p, _, _ in procs}
    roots = [(p, c) for p, pp, c in procs if pp not in pidset]
    roots.sort(key=lambda x: x[1])
    return roots


def _read_reset_ts():
    """读 sidecar 记录的上次强重置 epoch 秒（BOT_MARKER mtime 派生）。
    文件缺失/解析失败返回 None（无重置记录=不冷却，保持原拉起语义）。"""
    try:
        with open(BOT_RESET_TS, encoding="ascii") as f:
            return float(f.read().strip())
    except Exception:
        return None


def _write_reset_ts(ts):
    """marker 强重置后落 sidecar（D1 冷却判据）；写失败静默跳过，
    不影响 bot 看护主链与下方 8097 主职（2min 任务上限判例）。"""
    try:
        with open(BOT_RESET_TS, "w", encoding="ascii") as f:
            f.write(repr(float(ts)))
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "ops/launch_push_admin.py", exc)


try:
    if os.path.exists(DUMP_MARKER):
        try:
            os.remove(DUMP_MARKER)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "ops/launch_push_admin.py", exc)
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\""
                 "|Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine"
                 "|ConvertTo-Json -Compress"],
                capture_output=True, timeout=45)
            with open(r"C:\Temp\proc_dump.txt", "wb") as fo:
                fo.write((r.stdout or b"").decode("gbk", "replace").encode("utf-8"))
            print("[dump] python CL table -> C:\\Temp\\proc_dump.txt")
        except Exception as e:
            print("[dump] failed: %r" % e)

    _procs = _bot_snapshot()
    if os.path.exists(BOT_MARKER):
        try:  # D1: 先取 mtime（强重置时刻）再消耗，落 sidecar 供冷却判定
            _reset_ts = os.path.getmtime(BOT_MARKER)
        except OSError:
            _reset_ts = time.time()
        try:
            os.remove(BOT_MARKER)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "ops/launch_push_admin.py", exc)
        for _bp, _, _ in _procs:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(_bp)],
                           capture_output=True)
            print("[restart] killed bot pid %s (marker consumed)" % _bp)
        time.sleep(3)  # 等 WS 断开释放后再确保拉新
        _procs = []  # 已全杀，走 ensure 重拉（D1: 距本次重置 <300s 则冷却跳过）
        _write_reset_ts(_reset_ts)  # D1: 强重置时刻存档，下跳冷却判定用
    else:
        _roots = _roots_of(_procs)
        if len(_roots) > 1:  # 去重：多逻辑实例抢消息，只留最新树
            for _bp, _ in _roots[:-1]:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(_bp)],
                               capture_output=True)
                print("[dedup] killed extra bot tree root pid %s" % _bp)
            time.sleep(2)
            _procs = _bot_snapshot()

    if not _procs:  # 无 bot 即拉起（CIM 失败也返回[]会照拉一次，
    # 由 start_feishu_bot 幂等 + 下跳 dedup 收敛，不影响正确性）
        # D1 冷却窗口（DD-7 300s）：距上次强重置 <300s 且无逻辑 bot 实例
        # → 跳过拉起，打印 cooldown skip 后正常走完 8097 主职（DNS 抖动
        # 期重启大战实证）；副作用仅推迟重拉，不阻断任何主职。
        _last_reset = _read_reset_ts()
        if (_last_reset is not None
                and 0 <= time.time() - _last_reset < BOT_COOLDOWN_SEC):
            print("[cooldown] skip bot relaunch: %.0fs since last strong "
                  "reset (<%ds window, DD-7) -> 8097 main duty continues"
                  % (time.time() - _last_reset, BOT_COOLDOWN_SEC))
        else:
            subprocess.Popen(
                [_VENV_PY, _BOT_LAUNCHER],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            print("[launched] bot ensure -> start_feishu_bot.py (idempotent)")
except Exception as _e:
    # 兜底：bot 守护任何异常不得阻断下方 8097 主职（2min 任务上限判例）
    print("[bot-guard] failed: %r" % _e)

# ── 幂等探测：已监听即退出 ──────────────────────────────────
s = socket.socket()
s.settimeout(1.5)
try:
    s.connect(("127.0.0.1", 8097))
    s.close()
    print("[skip] 8097 already listening")
    sys.exit(0)
except OSError as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "ops/launch_push_admin.py", exc)
finally:
    try:
        s.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "ops/launch_push_admin.py", exc)

_sysroot = os.environ.get("SystemRoot", r"C:\Windows")
env = os.environ.copy()
env["PATH"] = ";".join([
    r"C:\Program Files\Python311\Scripts",
    r"C:\Program Files\Python311",
    _sysroot,
    os.path.join(_sysroot, "System32"),
    os.path.join(_sysroot, "System32", "Wbem"),
    os.path.join(_sysroot, "System32", "WindowsPowerShell", "v1.0"),
])
env["PYTHONUTF8"] = "1"
env["NO_PROXY"] = "localhost,127.0.0.1"
env["SYSTEMROOT"] = _sysroot

subprocess.Popen(
    [r"C:\Program Files\Python311\python.exe",
     r"c:\项目\老田的AI助手\portal\push_admin_server.py", "--port", "8097"],
    cwd=r"c:\项目\老田的AI助手\portal",
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL, env=env,
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
print("[launched] push_admin_server detached (scheduled-task host env)")
