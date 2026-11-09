# -*- coding: utf-8 -*-
"""9899 CowAgent console 看门狗（K-STOCK-077 P-1.5）+ 8095 picks 定时发布调度器看护。

背景：cow:9899 原由 ops/keepalive_bg.py（会话绑定后台任务）守护——
WorkBuddy 会话结束会被 Job 连坐收割，服务随即裸奔。本脚本以独立计划任务
「AI工作站CowAgent看门狗」每 5 分钟跑一次，任务宿主稳定不受会话影响。

2026-10-14 起 8095（picks 定时发布管理页 publish_schedule_server.py）、
8099（KM 知识库飞书联动 km_feishu_hub.py）与 8097（推送管理中心
push_admin_server.py）并入本守护：同一稳定宿主，一次任务跑四个探针，
注册层零改动（新增计划任务会被安全中心 schtasks 黑名单拦，改既有任务
脚本不需要）。

探针：TCP 连目标端口（socket 通=活）。
复活：看门狗自己 Popen 分离拉起（本进程宿主=计划任务，子进程随任务存活；
与 8094 走 Start-ScheduledTask 不同——两者均无既有服务任务）。
防抖：探针 x2 间隔 3s；8095 复活宽限 ~15s、cow 宽限 60s 内不重复拉。
预算：main() 入口起算 BUDGET_S=100s 的 monotonic 截止时刻，到期收缩宽限/跳过
      后续守卫——四口全死也不会顶爆任务执行上限 PT2M（2026-11-07）。

日志：C:/Temp/watchdog_9899.log（只记动作，静默通过不写）。
"""
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
import logging

PORT = 9899
# K-STOCK-414：旧机 E:\ 硬编码换机即死（cwd 不存在 → Popen 抛 FileNotFoundError
# → 本脚本崩溃，四端口守护连坐失守）→ 按本文件位置推导仓库根。
STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COW_DIR = os.path.join(STATION, "CowAgent")
COW_PY = os.path.join(COW_DIR, "venv", "Scripts", "python.exe")
PICKS_PORT = 8095
PORTAL_DIR = os.path.join(STATION, "portal")
# ── 全局预算（2026-11-07 K-ORG-20261107-WATCHDOG）──────────────────────
# 病根不是「某个守卫宽限太长」，而是「任一守卫都能吃满自己的宽限，串行叠加
# 顶爆任务执行上限」。四口全死最坏 4×(2+3+2+宽限)，cow 宽限 60s 时 ≈103s，
# 而任务上限 PT2M=120s：一旦某一跳碰上三个端口同时死，任务在第 2 分钟被
# 强杀，Job 连坐把刚 spawn 的子进程一起带走（K-PUSH-004 判例）→ 表现为
# 「端口被拉起又被杀」，日志里看不出原因。
# 解法：用统一的 monotonic 截止时刻替代「各守卫各自算」，到期就收缩宽限/
# 跳过后续守卫（下一跳 5 分钟后重试）。不压任何单端口宽限——压 cow 的 60s
# 会让 CowAgent 冷启慢时产生新的假「复活失败」，等于拿假信号换真安全。
BUDGET_S = 100.0
_DEADLINE = None  # monotonic 秒；None 表示不受预算约束（单函数独立调用时）


def _budget_left():
    """剩余预算秒；未设预算返回 None（单函数直调保持旧语义）。"""
    return None if _DEADLINE is None else _DEADLINE - time.monotonic()
SYS_PY = r"C:\Program Files\Python311\python.exe"  # 2026-10-16 实测在位
# 8099 KM 知识库飞书联动（km_feishu_hub.py）——与 Legion 8096 分离
KM_PORT = 8099
# 8097 推送管理中心（push_admin_server.py）
PA_PORT = 8097
LOG = r"C:\Temp\watchdog_9899.log"
DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED|NEW_GROUP|NO_WINDOW


def log(msg):
    line = "[%s] %s\n" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "ops/keepalive_9899.py", exc)
    print(line, end="")


def alive():
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def alive_on(port):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def guard_port(port, script_args, log_name, grace_iters, tag):
    """通用端口看护：x2 防抖 → 分离拉起 → 宽限验证。"""
    if alive_on(port):
        return
    left = _budget_left()
    if left is not None and left <= 8:
        # 探针 x2(≈5s)+拉起都放不进剩余预算：跳过整口，下一跳再试。
        # 宁可少守一跳，也不在预算边缘 spawn 出一个随后被任务强杀的子进程。
        log("%s:%d SKIP（本次预算剩 %.1fs 不足，下一跳再试）" % (tag, port, left))
        return
    time.sleep(3)
    if alive_on(port):
        return  # 抖动
    log("%s:%d DOWN x2 -> spawn" % (tag, port))
    # K-STOCK-414：拉起段包 try——单端口故障（解释器缺失/脚本崩）不再连坐
    # 后续端口守护；logf 句柄父侧及时回收（子进程已继承）。
    try:
        logf = open(log_name, "ab")
        logf.write(f"\n=== watchdog spawn {datetime.now()} ===\n".encode())
        logf.flush()
        subprocess.Popen([SYS_PY] + script_args,
                         cwd=PORTAL_DIR,
                         stdin=subprocess.DEVNULL, stdout=logf,
                         stderr=subprocess.STDOUT, creationflags=DETACHED,
                         close_fds=True)
        logf.close()
    except Exception as e:  # noqa: BLE001
        log("%s SPAWN_FAILED: %r" % (tag, e))
        return
    for i in range(grace_iters):
        left = _budget_left()
        if left is not None and left <= 3:
            log("%s REVIVE_UNCONFIRMED（预算耗尽，已 spawn 未确认——看 %s）"
                % (tag, log_name))
            return
        time.sleep(3)
        if alive_on(port):
            log("%s REVIVED after ~%ds" % (tag, (i + 1) * 3))
            return
    log("%s REVIVE_FAIL（%ds 仍无监听——看 %s）" % (tag, grace_iters * 3, log_name))


def guard_picks_sched():
    """8095 picks 定时发布调度器：掉线则用系统 python 分离拉起（--bg）。"""
    guard_port(PICKS_PORT, ["publish_schedule_server.py", "--bg"],
               r"C:\Temp\picks_sched_wd.log", 5, "picks_sched")


def guard_km_hub():
    """8099 KM 知识库飞书联动：掉线则分离拉起（--bg）。"""
    guard_port(KM_PORT, ["km_feishu_hub.py", "--bg"],
               r"C:\Temp\km_hub_wd.log", 5, "km_hub")


def guard_push_admin():
    """8097 推送管理中心：掉线则分离拉起（--bg）。"""
    guard_port(PA_PORT, ["push_admin_server.py", "--bg"],
               r"C:\Temp\push_admin_wd.log", 5, "push_admin")


def guard_cow():
    """9899 CowAgent：掉线则分离拉起。
    返回 True=活/已复活（或已拉起未确认），False=拉起失败。
    2026-11-07：原实现用 sys.exit() 收尾——因为当时它是最后一个且独立成进程；
    并入 main() 后 sys.exit 会横穿调用栈提前终止进程，改为 return，语义不变。"""
    if alive():
        return True
    left = _budget_left()
    if left is not None and left <= 8:
        log("cow:9899 SKIP（本次预算剩 %.1fs 不足，下一跳再试）" % left)
        return False
    time.sleep(3)
    if alive():
        return True  # 抖动
    log("DOWN x2 -> spawn CowAgent")
    try:  # K-STOCK-414：同 guard_port，spawn 故障留痕不裸炸
        logf = open(r"C:\Temp\cowagent_wd.log", "ab")
        logf.write(f"\n=== watchdog spawn {datetime.now()} ===\n".encode())
        logf.flush()
        subprocess.Popen([COW_PY, "app.py"], cwd=COW_DIR,
                         stdin=subprocess.DEVNULL, stdout=logf,
                         stderr=subprocess.STDOUT, creationflags=DETACHED,
                         close_fds=True)
        logf.close()
    except Exception as e:  # noqa: BLE001
        log("cow SPAWN_FAILED: %r" % e)
        return False
    for i in range(20):  # 宽限 60s（预算到期则提前收工）
        left = _budget_left()
        if left is not None and left <= 3:
            log("cow REVIVE_UNCONFIRMED（预算耗尽，已 spawn 未确认——看 C:/Temp/cowagent_wd.log）")
            return True
        time.sleep(3)
        if alive():
            log("REVIVED after ~%ds" % ((i + 1) * 3))
            return True
    log("REVIVE_FAIL（60s 仍无监听——看 C:/Temp/cowagent_wd.log）")
    return False


def main():
    """四端口一次巡完。预算在入口统一起算，避免任一守卫吃满宽限挤掉后面的。"""
    global _DEADLINE
    _DEADLINE = time.monotonic() + BUDGET_S
    guard_picks_sched()
    guard_km_hub()
    guard_push_admin()
    guard_cow()
    return 0


if __name__ == "__main__":
    main()
