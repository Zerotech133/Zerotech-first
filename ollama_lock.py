# -*- coding: utf-8 -*-
"""Ollama 批处理错峰锁（跨域共享：quant_backtest M3 / news_collector JEV 回填）。

背景（2026-10-08 事故）：M3 结构化与 JEV 回填同为本地 Ollama 重批处理，重叠运行
互相抢占推理槽，M3 单条从 ~20s 恶化到数小时（慢流滴答重置 requests 超时，详见
k266_m3_structure.ask_ollama 看门狗注释）。本锁让两个批处理互斥：抢不到就在
wait_s 内轮询，超时本轮让位，靠各自调度周期自愈追平。

用法：
    import ollama_lock
    if not ollama_lock.acquire("k266_m3", wait_s=1900):
        return                      # 本轮让位
    try:
        for item in items:
            ...                     # 每处理一条
            ollama_lock.heartbeat()
    finally:
        ollama_lock.release()

语义：
- 原子创建（O_CREAT|O_EXCL）抢锁；锁文件内容 {"pid","purpose","ts"}
- 持锁方须周期 heartbeat（仅 touch mtime，原子无撕裂读风险）
- 心跳超过 STALE_S（20 分钟）或持锁进程已死 → 视为遗留锁可抢占回收；
  M3 看门狗单条上限 600s < STALE_S，正常持锁不会被误抢
- **租约制（2026-10-25 K-ORG-20261025-LOCK）**：holder 每握满 LEASE_S（300s）须在
  处理完一条/一组后调用 tick()——release → 冷却 YIELD_S（20s）→ 重新抢锁。
  背景：M3 长跑连续独占数小时且全程 heartbeat，STALE_S 永远不触发，JEV 回填
  2026-10-25 00:34–06:00 连续 9 轮让位、零推进。租约制让两个批处理轮流持有。
- **等待上限（2026-10-26 OS-FIX-JEVLOCK，JEV 审计 §四-③）**：tick() 重新抢锁原实现
  最坏 6×(TICK_WAIT_S 900 + 5)+20 ≈ 5450s，远超子任务 hard timeout（JEV 夜间 deadline
  1500 / 小时轮 1800，外层 +300/+600）→ 被硬杀即丢组+留锁。现 tick() 支持
  `deadline_ts`（子任务自报截止时刻，单次等待/冷却都不得越过它），未传时用总预算
  TICK_TOTAL_BUDGET_S 兜底；超时打印 `OLLAMA_LOCK tick timeout ...` 字段日志后返回
  False（调用方按既有"本轮收工"分支处理），锁可用时行为与吞吐不变。
- 仅供长跑批处理使用；交互式/单次调用（门户问答等）不加锁，排队几秒可容忍
"""
import contextlib
import ctypes
import json
import os
import time
import logging

_ROOT = os.path.dirname(os.path.abspath(__file__))
LOCK_PATH = os.path.join(_ROOT, "locks", "ollama_batch.lock")
STALE_S = 1200            # 无心跳超此秒数视为遗留死锁
LEASE_S = 300             # 单次持锁租约：握满即让位一次（防长跑独占到底）
YIELD_S = 20              # 让位冷却：交出后空出这么久给对方抢
TICK_WAIT_S = 900         # 让位后重新抢锁的单次等待上限
_POLL_S = 5
# 2026-10-26 OS-FIX-JEVLOCK：tick 总等待上限（未传 deadline_ts 时的兜底）。
# 旧最坏 = max_tries 6 × (900+5) + 20 ≈ 5450s；任何子任务 hard timeout 都 < 2400s。
# 1200s 既保留"让位后仍可抢回"的正常语义，又保证在 1500/1800 的硬超时前必然返回。
TICK_TOTAL_BUDGET_S = 1200
TICK_DEADLINE_MARGIN_S = _POLL_S   # 提前一个轮询周期返回，防 acquire 内最后一次 sleep 越界


def _alive(pid):
    """Windows 安全的进程存活探测（勿用 os.kill——Windows 下会真杀进程）。"""
    if pid <= 0:
        return False
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == 259                  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False      # 查询失败按死进程处理（宁可重抢，代价只是一轮并行）


def _try_create(purpose):
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        now = time.time()          # since 存浮点：round 会丢亚秒精度，
        f.write(json.dumps({"pid": os.getpid(), "purpose": purpose,
                            "ts": round(now), "since": now}))  # 令刚持锁时算出 0
    return True


def _stealable():
    """锁是否可抢占：持锁进程已死，或心跳超时。"""
    try:
        with open(LOCK_PATH, encoding="utf-8") as f:
            pid = int((json.load(f) or {}).get("pid") or 0)
    except Exception:
        return True                                    # 无锁/内容损坏
    if pid != os.getpid() and not _alive(pid):
        return True
    try:
        return (time.time() - os.path.getmtime(LOCK_PATH)) > STALE_S
    except OSError:
        return True


def acquire(purpose, wait_s=0):
    """抢锁；wait_s 内每 5s 重试，抢不到返回 False（调用方本轮让位）。"""
    deadline = time.time() + max(0, wait_s)
    while True:
        if _try_create(purpose):
            return True
        if _stealable():
            try:
                os.remove(LOCK_PATH)                   # 回收遗留锁后重试
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "ollama_lock.py", exc)
            if _try_create(purpose):
                return True
        if time.time() >= deadline:
            return False
        time.sleep(_POLL_S)


def _owns_lock():
    """当前进程是否仍持有锁（读 pid 校验）。"""
    d = _read_lock()
    return int(d.get("pid") or 0) == os.getpid()


def heartbeat():
    """持锁方周期续命：仅 touch mtime（不动内容，避免撕裂读）。

    2026-10-30 BATCH2：先校验锁仍归本进程 —— 旧实现无条件 utime，
    锁被他人接管后会给"窃锁者"续命，且持有方误以为自己仍持锁。
    返回 True=续命成功 / False=已丢失（调用方应停止判读）。
    """
    if not _owns_lock():
        logging.getLogger(__name__).warning(
            "ollama_lock: heartbeat 发现锁不属于本进程（pid=%s），停止续命",
            _read_lock().get("pid"))
        return False
    try:
        os.utime(LOCK_PATH, None)
        return True
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "ollama_lock.py", exc)
        return False


def release():
    """释放；锁已被他人抢占重建时不动别人的锁。"""
    try:
        with open(LOCK_PATH, encoding="utf-8") as f:
            pid = int((json.load(f) or {}).get("pid") or 0)
        if pid != os.getpid():
            return
    except Exception as exc:
        # 2026-10-30 BATCH2：读不到/损坏时**不得**盲删（旧实现会删掉别人的锁）。
        logging.getLogger(__name__).warning(
            "ollama_lock: release 读锁失败，保守不删除（%s）", exc)
        return
    try:
        os.remove(LOCK_PATH)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "ollama_lock.py", exc)


def _read_lock():
    try:
        with open(LOCK_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def holder_info():
    """只读探测当前持锁者（不抢锁）：{purpose,pid,ts,alive}。"""
    d = _read_lock()
    if not isinstance(d, dict):
        return {}
    try:
        pid = int(d.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return {"purpose": str(d.get("purpose") or ""), "pid": pid,
            "ts": d.get("ts"), "alive": bool(pid and _alive(pid))}


def held_seconds():
    """本进程已连续持锁秒数；没持锁返回 0。"""
    d = _read_lock()
    if int(d.get("pid") or 0) != os.getpid():
        return 0.0
    try:
        since = float(d.get("since") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, time.time() - since) if since > 0 else 0.0


def should_yield(lease_s=None):
    """是否已握满租约。调用方应在每处理完一条/一组后检查。"""
    return held_seconds() >= (LEASE_S if lease_s is None else lease_s)


def tick(purpose, lease_s=None, yield_s=None, wait_s=None, max_tries=6,
         deadline_ts=None):
    """租约检查：握满就交还一手，冷却后抢回来。返回 True 表示仍持锁。

    抢锁失败会重试 max_tries 次（每次 wait_s，默认 900s）；仍失败返回 False，
    由调用方决定告警策略——绝不能"以为还持锁"地继续跑。

    2026-10-26 OS-FIX-JEVLOCK（JEV 审计 §四-③）：整次 tick 的等待/冷却受
    `deadline_ts` 约束（epoch 秒）——单次 acquire 等待与 YIELD_S 冷却都不得超过
    截止时刻（再提前 TICK_DEADLINE_MARGIN_S 返回）；未传 deadline_ts 时用
    TICK_TOTAL_BUDGET_S 兜底。超时打印 `OLLAMA_LOCK tick timeout purpose=..
    waited=..s tries=..s deadline_left=..s` 后返回 False。锁空闲时（should_yield
    为假或首抢即中）路径与旧实现完全一致，不影响正常吞吐。
    """
    started = time.time()
    if not _owns_lock():
        # 2026-10-30 BATCH2：锁已丢失（被接管/文件被删）——重新抢，不能假装持有。
        holder = str((_read_lock() or {}).get("purpose") or "")
        logging.getLogger(__name__).warning(
            "ollama_lock: tick 发现锁所有权丢失（holder=%s），purpose=%s", holder or "?", purpose)
        # 2026-10-30 LOCKYIELD：白天 M3 优先级更高——持锁方是 M3 时直接让位，
        # 不要反复重抢（14:00 轮实测：3 次重抢把 judged 从 232 拖到 110）。
        if holder.startswith("k266") or "m3" in holder.lower():
            logging.getLogger(__name__).info(
                "ollama_lock: 让位给 %s（本次轮次收工）", holder)
            return False
        wait = TICK_WAIT_S if wait_s is None else max(0.0, wait_s)
        budget = _tick_budget_s(deadline_ts, started)
        if budget <= 0:
            return False
        return bool(acquire(purpose, wait_s=min(wait, budget)))
    if not should_yield(lease_s):
        return True
    release()
    budget = _tick_budget_s(deadline_ts, started)
    if budget <= 0:
        _log_tick_timeout(purpose, time.time() - started, 0, deadline_ts)
        return False
    time.sleep(min(YIELD_S if yield_s is None else max(0.0, yield_s), budget))
    wait = TICK_WAIT_S if wait_s is None else max(0.0, wait_s)
    tries = 0
    for _ in range(max(1, max_tries)):
        budget = _tick_budget_s(deadline_ts, started)
        if budget <= 0:
            break
        tries += 1
        if acquire(purpose, wait_s=min(wait, budget)):
            return True
        budget = _tick_budget_s(deadline_ts, started)
        if budget <= 0:
            break
        time.sleep(min(_POLL_S, budget))
    _log_tick_timeout(purpose, time.time() - started, tries, deadline_ts)
    return False


def _tick_budget_s(deadline_ts, started, now=None):
    """本次 tick 剩余可等待秒数：优先 deadline_ts，缺省用 TICK_TOTAL_BUDGET_S。"""
    now = time.time() if now is None else now
    if deadline_ts is not None:
        try:
            return max(0.0, float(deadline_ts) - now - TICK_DEADLINE_MARGIN_S)
        except (TypeError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "ollama_lock.py", exc)
    return max(0.0, TICK_TOTAL_BUDGET_S - (now - started))


def _log_tick_timeout(purpose, waited_s, tries, deadline_ts):
    """超时日志（字段化，便于夜批日志/审计对账）；不抛异常、不影响返回值。"""
    left = "?"
    if deadline_ts is not None:
        try:
            left = "%.1f" % max(0.0, float(deadline_ts) - time.time())
        except (TypeError, ValueError):
            left = "?"
    print("OLLAMA_LOCK tick timeout purpose=%s waited=%.1fs tries=%d "
          "deadline_left=%ss budget=%s" % (
              purpose, waited_s, tries, left,
              "deadline" if deadline_ts is not None
              else "%ss" % TICK_TOTAL_BUDGET_S),
          flush=True)


@contextlib.contextmanager
def batch_lock(purpose, wait_s=0):
    """上下文管理器形态：yield 是否抢到。"""
    yield acquire(purpose, wait_s)
    release()
