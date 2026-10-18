# -*- coding: utf-8 -*-
"""Hermes8787-KillSvc Gate 0 前置逻辑层（R7，纯逻辑，零注册零执行零杀进程）。

设计定稿（逐条对齐，不自创口径）：
    docs/runbooks/2026-10-18_UAC提权操作盘点与高权限自愈方案.md

本模块只做「可测逻辑」：
  - check()        七条白名单校验器：request -> Verdict（deny 必带规则号+拒因）
  - tri_state()    三态判定纯函数：normal_user_session | zombie_system_session | not_found
  - d1..d6         防抖六条逐条落成可测谓词（§3.2）

本模块不做任何真实探测、不杀任何进程、不注册任何任务。
未来 Gate 0 后 SYSTEM 侧白名单脚本（hermes_kill_8787.ps1）只做纯转调，
判定逻辑全部收口在这里（import 本模块的常量与谓词）。

七条白名单与 runbook 对照：
    W1 请求者身份白名单       <- §2.3 转调方式 + §2.1 权限边界（作者账户 Start 权限）
    W2 目标进程特征三重匹配   <- §2.2.2 按端口找 PID(8787) + §2.2.3 CommandLine 匹配
                                 server.py（进程名 python/pythonw + 命令行特征 + 端口归属三重，
                                 进程宿主覆盖 §1 实测 python.exe 与 pythonw.exe 两种形态）
    W3 请求频率防抖冷却窗     <- §3.2.4 冷却 10min + §3.2.6 Guard30 30min 天然错开
    W4 参数面=零参数强制      <- §2.2.1 无 param 块 + §2.1 Action 不携带任何参数
                                 （请求面同样只允许固定 schema，多一个键即拒）
    W5 时间窗                 <- §2.1 ExecutionTimeLimit=PT5M + §3.2.5 任务自身兜底
                                 （请求 issued_at 与 now 偏差须 <= 300s，陈旧请求拒收）
    W6 指纹/nonce 防重放      <- 验收③ 白名单化（§2.2.1 不读外部控制字）的请求面收敛：
                                 每次转调须带唯一 nonce，seen_nonces 中出现过即重放拒绝
    W7 审计落账要求           <- §2.2.7 固定日志路径 C:\\aios\\Hermes\\kill_svc.log 每步落盘
                                 （请求须携带 reason 观测依据 + 固定 audit_log_path）
"""

import re
import time
from dataclasses import dataclass
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# 白名单常量（集中一处，供未来 SYSTEM 侧脚本 import）
# ---------------------------------------------------------------------------

#: KillSvc 监听的目标端口（§2.2.2）
PORT = 8787

#: 固定审计日志路径（§2.2.7，请求方不得自定）
KILL_SVC_LOG_PATH = r"C:\aios\Hermes\kill_svc.log"

#: 转调方白名单（§3.3 转调方实施载体二选一 + §2.1 管理员手动触发）
REQUESTERS = frozenset({
    "guard30_chain",   # 轻量版：Guard30 拉活链 start_hermes_bg.py 前置探测处
    "n1_selfheal",     # 独立版：N1 晨检/联动层自愈任务内嵌判定转调
    "admin_manual",    # 管理员手动触发（§2.1 触发方式）
})

#: 8787 真身进程宿主白名单（§1 实测 Guard30 拉起为 python.exe；覆盖 pythonw.exe 形态）
PROCESS_NAMES = frozenset({"python.exe", "pythonw.exe"})

#: 命令行特征（§2.2.3）：匹配 hermes-webui-next\server.py（或回滚基 hermes-webui\server.py）
CMDLINE_PATTERN = re.compile(r"hermes-webui(?:-next)?[\\/]server\.py", re.IGNORECASE)

#: 冷却窗：10 分钟内不重复触发（§3.2.4）
COOLDOWN_SECONDS = 600

#: 时间窗：PT5M（§2.1 ExecutionTimeLimit），请求 issued_at 新鲜度同窗口（§3.2.5）
TIME_WINDOW_SECONDS = 300

#: Guard30 伴生周期：30min（§3.2.6，触发频率上限 = 每 30min 一次）
GUARD30_CADENCE_SECONDS = 1800

#: HTTP 真探连续失败阈值（§3.2.1：连续 3 次失败）
PROBE_FAILURE_THRESHOLD = 3

#: W4 零参数面：request 只允许这六个键，多一个键即拒（§2.2.1 / §2.1）
REQUEST_KEYS = frozenset({
    "requester",      # 转调方身份（W1）
    "nonce",          # 防重放指纹（W6）
    "issued_at",      # 请求签发时刻 epoch 秒（W5）
    "target",         # 目标进程探测结果 dict（W2）
    "reason",         # 触发依据：观测态描述（W7）
    "audit_log_path", # 审计日志固定路径（W7）
})

# 三态判定取值（§2.1「单任务覆盖三态」：用户态实例 / SYSTEM 僵尸实例 / 无实例）
TRI_NORMAL = "normal_user_session"        # 用户态实例（用户态可处置，KillSvc 零动作）
TRI_ZOMBIE = "zombie_system_session"      # session-0 SYSTEM 旧实例（唯一需 KillSvc 场景）
TRI_NOTFOUND = "not_found"                # 无监听/无实例（拉活归 Guard30，幂等 exit 0）


@dataclass(frozen=True)
class Verdict:
    """check() 的判定结果。deny 时 rule 必为 W1..W7 之一，reason 必非空。"""

    allow: bool
    rule: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# 目标进程身份核对（三重：进程名 + 命令行特征 + 端口归属）
# ---------------------------------------------------------------------------

def identity_match(target: dict) -> bool:
    """8787 真身三重核对（§2.2.2 + §2.2.3，防误杀最后一道闸的请求侧镜像）。

    target 为进程探测结果 dict（不做真实探测），字段：
        pid            监听端口的 OwningProcess
        process_name   进程名（须在 PROCESS_NAMES）
        command_line   完整命令行（须匹配 CMDLINE_PATTERN）
        port           监听端口（须 == PORT）
        listening      是否 LISTENING（须 True）
    """
    if not isinstance(target, dict):
        return False
    name = str(target.get("process_name", "")).lower()
    cmdline = str(target.get("command_line", ""))
    if name not in PROCESS_NAMES:
        return False
    if not CMDLINE_PATTERN.search(cmdline):
        return False
    if target.get("port") != PORT or not target.get("listening"):
        return False
    return target.get("pid") is not None


# ---------------------------------------------------------------------------
# 七条白名单校验器
# ---------------------------------------------------------------------------

def check(
    request: dict,
    now: Optional[float] = None,
    last_trigger_ts: Optional[float] = None,
    seen_nonces: Iterable[str] = (),
) -> Verdict:
    """七条白名单逐条校验，按 W1..W7 固定顺序，命中即拒（deny 带规则号+拒因）。"""
    if not isinstance(request, dict):
        return Verdict(False, "W4", "request 不是 dict：参数面非法（W4 零参数强制）")
    if now is None:
        now = time.time()
    seen = set(seen_nonces)

    # W1 请求者身份白名单（§2.3 / §2.1 权限边界）
    requester = request.get("requester")
    if requester not in REQUESTERS:
        return Verdict(False, "W1", "requester 不在转调方白名单 %s: %r" % (sorted(REQUESTERS), requester))

    # W2 目标进程特征=8787 真身三重匹配（§2.2.2/§2.2.3）
    if not identity_match(request.get("target")):
        return Verdict(False, "W2", "目标进程三重核对不过（进程名/命令行特征/端口归属 8787）：拒杀")

    # W3 请求频率防抖冷却窗（§3.2.4：10min 内不重复触发）
    if last_trigger_ts is not None and (now - last_trigger_ts) < COOLDOWN_SECONDS:
        return Verdict(
            False, "W3",
            "冷却窗内重复触发（距上次 %.0fs < %ds）" % (now - last_trigger_ts, COOLDOWN_SECONDS),
        )

    # W4 参数面=零参数强制（§2.2.1 无 param 块；请求面只允许固定 schema 六键）
    extras = set(request.keys()) - REQUEST_KEYS
    if extras:
        return Verdict(False, "W4", "请求面出现 schema 外字段 %s：零参数强制（不提供任意命令执行）" % sorted(extras))

    # W5 时间窗（§2.1 PT5M / §3.2.5：陈旧请求拒收，窗口同 ExecutionTimeLimit=300s）
    issued_at = request.get("issued_at")
    if not isinstance(issued_at, (int, float)):
        return Verdict(False, "W5", "issued_at 缺失或非数值")
    if abs(now - issued_at) > TIME_WINDOW_SECONDS:
        return Verdict(False, "W5", "请求超出 PT5M 时间窗（偏差 %.0fs > %ds）：疑似陈旧/重放" % (abs(now - issued_at), TIME_WINDOW_SECONDS))

    # W6 指纹/nonce 防重放（验收③：请求面唯一性核验）
    nonce = request.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        return Verdict(False, "W6", "nonce 缺失或为空")
    if nonce in seen:
        return Verdict(False, "W6", "nonce 已出现过：判定为重放请求，拒绝")

    # W7 审计落账要求（§2.2.7：固定日志路径 + 触发依据）
    if not str(request.get("reason", "")).strip():
        return Verdict(False, "W7", "reason 缺失：无观测依据不触发")
    if request.get("audit_log_path") != KILL_SVC_LOG_PATH:
        return Verdict(False, "W7", "audit_log_path 非固定路径 %s：审计面不合规" % KILL_SVC_LOG_PATH)

    return Verdict(True, "ALLOW", "七条白名单全通过：允许转调 Start-ScheduledTask Hermes8787-KillSvc")


# ---------------------------------------------------------------------------
# 三态判定（§2.1：单任务覆盖三态——用户态实例 / SYSTEM 僵尸实例 / 无实例）
# ---------------------------------------------------------------------------

def tri_state(target: dict) -> str:
    """纯函数三态判定，输入为进程探测结果 dict（不做真实探测）。

    target 字段：listening、pid、session_id、owner（probe_failures 供防抖 d1 用，
    不参与本判定——「是否真僵死」由 §3.2 防抖双条件谓词另行把关）。
    """
    if not isinstance(target, dict):
        return TRI_NOTFOUND
    if not target.get("listening") or target.get("pid") is None:
        return TRI_NOTFOUND
    session_id = target.get("session_id")
    owner = str(target.get("owner", "")).upper()
    is_system_session = (session_id == 0) or ("SYSTEM" in owner and owner.rstrip().endswith("SYSTEM"))
    if is_system_session:
        return TRI_ZOMBIE
    return TRI_NORMAL


# ---------------------------------------------------------------------------
# 防抖六条（§3.2 逐条落成可测谓词）
# ---------------------------------------------------------------------------

def d1_zombie_declared(target: dict) -> bool:
    """防抖1（§3.2.1）：双条件齐备才判僵死——端口 LISTENING 且 HTTP 真探连续失败 >= 3 次。"""
    has_process = bool(target.get("listening")) and target.get("pid") is not None
    probes_dead = target.get("probe_failures", 0) >= PROBE_FAILURE_THRESHOLD
    return has_process and probes_dead


def d2_no_kill_single_condition(target: dict) -> bool:
    """防抖2（§3.2.2）：单条件不动作。

    返回 True 表示当前只满足单条件（无监听=态A走 Guard30；探活正常=健康零动作），
    不得触发 KillSvc；双条件齐备时返回 False（进入 d1 放行区）。
    """
    return not d1_zombie_declared(target)


def d3_pre_kill_identity_ok(target: dict) -> bool:
    """防抖3（§3.2.3）：杀前二次核对——CommandLine 匹配 server.py 的最后一道闸。"""
    return identity_match(target)


def d4_cooldown_ok(now: float, last_trigger_ts: Optional[float]) -> bool:
    """防抖4（§3.2.4）：冷却窗 10 分钟内不重复触发。"""
    return last_trigger_ts is None or (now - last_trigger_ts) >= COOLDOWN_SECONDS


def d5_task_bounded(declared_seconds: float, concurrent_instances: int = 1) -> bool:
    """防抖5（§3.2.5）：ExecutionTimeLimit=PT5M + IgnoreNew 兜底。

    声明耗时 <= 300s 且无并发实例堆积才放行。
    """
    return declared_seconds <= TIME_WINDOW_SECONDS and concurrent_instances <= 1


def d6_guard30_cadence_ok(now: float, last_guard30_ts: Optional[float]) -> bool:
    """防抖6（§3.2.6）：Guard30 30min 伴生周期错开冷却窗，触发频率上限每 30min 一次。"""
    return last_guard30_ts is None or (now - last_guard30_ts) >= GUARD30_CADENCE_SECONDS
