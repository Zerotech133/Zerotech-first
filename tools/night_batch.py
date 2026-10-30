# -*- coding: utf-8 -*-
r"""night_batch.py — 夜间长跑批处理循环器（本地模型两条积压队列）。

为什么要有它
------------
2026-10-25 盘点：本地推理积压两条大队列——
  · news.db   20,016 条无 JEV 判定（近 7 天积压）
  · 雪球库    15,236 条未分类（33,182 - 17,946）
两条都用同一个本地 Ollama（9b），且服务端 NUM_PARALLEL=1 是**严格串行**的，
并行跑只会互相排队，所以这里串行轮转：一轮 JEV、一轮雪球，交替推进。

断点怎么来
----------
两个脚本本身就是增量的，不需要额外游标：
  · jev_backfill.py 取 `WHERE jev IS NULL` 的批，判完 UPDATE 写回 → 自动前进
  · xq_classify.py  取未分类行，写完 classified 表 → 自动前进
所以任何时刻被中断，重跑即可接着来，不会重复计费也不会漏。

安全边界（2026-10-25 EXEC-R13 修订 · 接管未落地的超时改造）
---------------------------------------------------------
  · 只调这两个既有脚本，不直接写库
  · **单实例锁** `locks/night_batch.lock`（pid/host/start + 心跳；2h 无心跳且
    pid 已死视为遗留锁可回收）。过渡期兜底：旧版实例没有锁，靠「日志最后是
    START 未 END 且 40 分钟内仍有活动」判断，防双实例（10:40/10:49 双 START 实证）。
  · 到点（默认 07:00，赶在晨报前）自动收工；也可放停止旗文件提前收
  · **每轮预算 + 主动收口**（旧版每轮都被外层硬超时 1620s/1800s 打死，rc=124）：
      JEV：子脚本 `--deadline` 自收口；外层硬时限 = deadline + 600s 宽限
           （一组 8 条 × 本地 judge 60s + 余量；旧宽限只有 120s，按点退出反被误杀）
      XQ ：单轮条数预算 + 软预算 1500s；到点 terminate（xq_classify 每条 commit，
           已落进度不回滚），下一轮按实测吞吐自适应下调 limit
  · **连续 rc=124 ≥2 次写 ALERT 旗** `jev-eval/alerts/BATCH_TIMEOUT_<ts>.flag`
    （alert_forward.ps1 扫 *.flag 转发飞书，n1_check.ps1 计入 pending）
  · 连续无推进 → 线性退避重试（默认 3 次、基数 300s），用尽仍无进展才退出
  · 每轮落一行日志（含两条队列剩余量/rc/耗时/吞吐），收工写汇总

用法：
    python -B tools/night_batch.py                    # 跑到 07:00
    python -B tools/night_batch.py --until 06:30
    python -B tools/night_batch.py --dry-run          # 只看队列与计划，不执行
    python -B tools/night_batch.py --batch-xq 300 --xq-budget 1500

白天调度参数（2026-10-26 EXEC-R27 · 计划 B；默认全关，显式传参才生效）
--------------------------------------------------------------------
  --xq-window HH:MM-HH:MM   只在该时段内跑 XQ 轮（支持跨午夜 22:00-06:00）；
                            不传 = 不限窗口（旧行为）
  --xq-min-jev N            JEV 待判积压 ≥N 时跳过 XQ 轮（计划 B 推荐 3000）；
                            不传 = 不按积压跳过（旧行为）
  --jev-only                完全跳过 XQ，只跑 JEV
  --pause-m3                运行期暂停 M3（写 C:\Temp\k266_m3.pause + 停 M3 进程），
                            收工/异常退出时恢复；用于"白天 JEV 独占窗口"（默认关）
  计划 B 接线：白天主进程用 `--xq-window 08:00-10:00 --xq-min-jev 3000`（XQ 只在
  早窗口且 JEV 清到 <3000 时才跑）；要彻底 JEV 优先则用 `--jev-only`。被跳过的
  轮次逐轮打印 `XQ-SKIP <原因>`，START/END 打印生效策略与跳过轮次数，便于次日核验。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import logging

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
NEWS_DB = ROOT / "news_collector" / "news.db"
XQ_DB = ROOT / "quant_backtest" / "data" / "xueqiu" / "xueqiu_info.db"
JEV = ROOT / "news_collector" / "jev_backfill.py"
# 2026-10-26 OS-FIX-JEVALERT：旧值 0 关闭 jev_backfill 的积压飞书告警（JEV 审计 §四-⑤）。
# 默认 100 与 jev_backfill 默认一致；JEV_ALERT_THRESHOLD 可覆盖（0 需显式设置才关闭）。
ALERT_THRESHOLD = os.environ.get("JEV_ALERT_THRESHOLD", "100")
XQ = ROOT / "quant_backtest" / "xq_classify.py"
DEFAULT_LOG = r"C:\Temp\night_batch.log"
DEFAULT_STOP = r"C:\Temp\night_batch.stop"
STALL_RETRIES_DEFAULT = 3      # 连续无推进的重试次数（超过才退出）
STALL_BACKOFF_DEFAULT = 300.0  # 退避基数秒数（按次数线性递增）
STALL_SLEEP_CHUNK = 30.0       # 退避分片，便于响应停止旗与收工时间

# --- 单实例锁（2026-10-25 EXEC-R13）----------------------------------------
LOCK_PATH = ROOT / "locks" / "night_batch.lock"
LOCK_STALE_S = 7200            # 锁 mtime 超 2h 且 pid 已死 → 视为遗留锁可回收
LOG_LIVE_WINDOW_S = 2400       # 旧版实例探测：日志 40 分钟内活跃且 START 未 END

# --- 每轮预算（2026-10-25 EXEC-R13）----------------------------------------
# 旧版：JEV 外层硬杀 = deadline(1500)+120=1620s；XQ 外层硬编码 1800s。
# 根因：JEV 子脚本按 1500s 自收口后被 120s 宽限误杀（一组最坏 >120s）；
#       XQ 子脚本只有 --limit 没有时间预算，600 条按实测 ~9.5s/条 需 ~5700s。
JEV_GROUP_WORST_S = 600        # 子脚本按 deadline 收口后，最坏一组 8 条还要多久
XQ_SOFT_BUDGET_S = 1500.0      # 雪球单轮软预算：到点主动收口，不等 1800s 硬杀
XQ_MIN_LIMIT = 30              # 自适应条数下限（宁可小步，不再打满超时）
XQ_HARD_TIMEOUT_S = 1800.0     # 软收口失效时的最终保险
BATCH_TIMEOUT_ALERT_STREAK = 2 # 连续 rc=124 达到该值写 ALERT 旗
ALERT_DIR = ROOT / "jev-eval" / "alerts"

# --- 2026-10-26 EXEC-R27（计划 B 白天调度；默认全关，显式传参才生效）--------
# 背景（_audit_20261025\exec\E6-throughput-assessment.md §4/§5）：白天槽被 XQ
# 分走约一半（JEV 日增仅 ~940 条），B 方案 = 白天优先 JEV、XQ 只在 08:00-10:00
# 独立窗口跑，或 JEV 积压 <N（推荐 3000）才跑。
# 注意：--xq-min-jev 的 argparse 默认值是 None 而不是 3000——当前 JEV 积压约
# 1.8 万条，若默认生效会把 XQ 静默全停（等于改变旧行为，违背「缺省不加参数时
# 与改造前一致」）。3000 作为计划 B 接线的**推荐值**，由启动命令显式传入。
XQ_MIN_JEV_RECOMMENDED = 3000


def stall_decision(stall_count: int, retries: int, backoff_s: float) -> tuple[bool, float]:
    """连续无推进时的处置：返回 (是否继续重试, 本次退避秒数)。

    2026-10-25（K-ORG-20261025-NIGHTBATCH）：原逻辑"连续 2 轮无推进即退出"，
    一次瞬时故障（云侧空返 + 与 M3 抢 ollama 锁）就让 2 万条积压停摆 1 小时。
    改为线性退避重试：第 n 次无推进等 n×base 秒再试，用尽上限才退出。
    """
    if stall_count < max(1, int(retries)):
        return True, max(0.0, float(backoff_s) * max(1, stall_count))
    return False, 0.0


# --- 2026-10-25 EXEC-R13 纯函数（便于单测；不碰 IO）----------------------
def hard_timeout_jev(deadline_s: float) -> float:
    """JEV 外层硬时限 = 子脚本自收口 deadline + 最坏一组宽限。

    旧值是 deadline+120：子脚本在 1500s 按点收工，但最后一批 8 条在 M3 抢
    Ollama 槽时最坏 8×60s+重试 > 120s，外层先杀 → 每轮 rc=124。
    """
    deadline = float(deadline_s)
    if deadline <= 0:
        return 3600.0
    return deadline + JEV_GROUP_WORST_S


def next_xq_limit(processed: int, elapsed_s: float,
                  budget_s: float = XQ_SOFT_BUDGET_S, cap: int = 600) -> int:
    """按本轮实测吞吐把下一轮条数收进软预算的 85%，最少 XQ_MIN_LIMIT 条。

    processed/elapsed 任一 <=0（无进度/读不到）→ 返回下限：小步试跑，绝不
    再拿 600 条去撞 1800s 硬超时。cap 保留调用方 --batch-xq 的显式上限。
    """
    if processed <= 0 or elapsed_s <= 0:
        return XQ_MIN_LIMIT
    per_item = float(elapsed_s) / float(processed)
    fit = int(max(0.0, float(budget_s)) * 0.85 / per_item)
    return max(XQ_MIN_LIMIT, min(int(cap), fit))


def timeout_alert_due(streak: int) -> bool:
    """连续 rc=124 是否达到告警阈值（默认 2 次）。"""
    return int(streak) >= BATCH_TIMEOUT_ALERT_STREAK


def log_looks_live(text: str) -> bool:
    """日志尾部是否有"START 之后没有 END"的未收工会话（旧版实例探测用）。"""
    last_start = last_end = -1
    for i, line in enumerate(text.splitlines()):
        if "] START " in line:
            last_start = i
        elif "] END " in line:
            last_end = i
    return last_start >= 0 and last_start > last_end


# --- 2026-10-26 EXEC-R27：白天调度纯函数（便于单测；不碰 IO）---------------
def parse_xq_window(text: str) -> tuple[int, int]:
    """解析 "HH:MM-HH:MM" → (起, 止) 的分钟数；支持跨午夜（22:00-06:00）。

    非法格式抛 ValueError（main 转 argparse error rc=2，不进入批处理循环）。
    起 == 止 视为全天窗口（24h）。
    """
    raw = (text or "").strip()
    if "-" not in raw:
        raise ValueError("缺少 '-'（应形如 08:00-10:00）：%r" % text)
    left, _, right = raw.partition("-")

    def _minutes(part: str) -> int:
        part = part.strip()
        hh, sep, mm = part.partition(":")
        if not sep or not hh.isdigit() or not mm.isdigit():
            raise ValueError("时刻应为 HH:MM：%r" % part)
        h, m = int(hh), int(mm)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError("时刻越界：%r" % part)
        return h * 60 + m

    return _minutes(left), _minutes(right)


def in_xq_window(now: datetime, start_min: int, end_min: int) -> bool:
    """当前时刻是否在 XQ 窗口内（起含、止不含；跨午夜按环绕处理）。"""
    cur = now.hour * 60 + now.minute
    start_min, end_min = int(start_min) % 1440, int(end_min) % 1440
    if start_min == end_min:
        return True
    if start_min < end_min:
        return start_min <= cur < end_min
    return cur >= start_min or cur < end_min


def xq_skip_reason(jev_only: bool, xq_window: tuple[int, int] | None,
                   min_jev: int | None, now: datetime,
                   jev_backlog: int) -> str | None:
    """本轮是否跳过 XQ：返回人话原因（None = 允许跑）。纯函数，优先级：

    ① `--jev-only` 全跳；② 不在 `--xq-window` 内；③ JEV 积压 ≥ `--xq-min-jev`。
    积压读不到（-1）时不按阈值跳（宁跑勿误停，避免 DB 抖动把 XQ 永久停掉）。
    """
    if jev_only:
        return "jev-only"
    if xq_window is not None and not in_xq_window(now, xq_window[0], xq_window[1]):
        return "outside-window(%02d:%02d-%02d:%02d)" % (
            xq_window[0] // 60, xq_window[0] % 60,
            xq_window[1] // 60, xq_window[1] % 60)
    if (min_jev is not None and min_jev >= 0 and jev_backlog >= 0
            and jev_backlog >= min_jev):
        return "jev-backlog %d >= xq-min-jev %d" % (jev_backlog, min_jev)
    return None


def xq_policy_text(jev_only: bool, xq_window: tuple[int, int] | None,
                   min_jev: int | None) -> str:
    """START 日志用：把生效的 XQ 策略压成一行（默认参数时显示旧行为）。"""
    parts = ["jev_only=%s" % ("on" if jev_only else "off")]
    if xq_window is None:
        parts.append("window=any")
    else:
        parts.append("window=%02d:%02d-%02d:%02d" % (
            xq_window[0] // 60, xq_window[0] % 60,
            xq_window[1] // 60, xq_window[1] % 60))
    parts.append("min_jev=%s" % ("off" if min_jev is None else str(min_jev)))
    return " ".join(parts)


# --- 单实例锁实现（pid/host/start + 心跳；异常退出可回收）-------------------
def _pid_alive(pid: int) -> bool:
    """Windows 安全的进程存活探测（勿用 os.kill——Windows 下会真杀进程）。"""
    if pid <= 0:
        return False
    try:
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            k32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259                   # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False      # 查询失败按死进程处理（宁可重抢，代价只是一轮并行）


def _lock_meta() -> dict:
    try:
        with open(LOCK_PATH, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def lock_is_stale(meta: dict, lock_path: Path = LOCK_PATH, now: float | None = None,
                  stale_s: float = LOCK_STALE_S) -> bool:
    """锁是否可回收：持锁 pid 已死，或心跳（mtime）超 stale_s。纯逻辑便于单测。"""
    now = time.time() if now is None else now
    pid = int((meta or {}).get("pid") or 0)
    if pid and pid != os.getpid() and not _pid_alive(pid):
        return True
    try:
        return (now - lock_path.stat().st_mtime) > stale_s
    except OSError:
        return True


def acquire_instance_lock() -> tuple[bool, dict]:
    """抢单实例锁：O_CREAT|O_EXCL 原子创建；遗留锁可回收。返回 (拿到?, 持锁者信息)。"""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            meta = _lock_meta()
            if not lock_is_stale(meta):
                return False, meta
            try:
                os.remove(LOCK_PATH)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/night_batch.py", exc)
            continue
        except OSError as exc:
            # 锁目录不可写等意外：不因护栏故障拖死批处理，降级为"无锁运行"并记录。
            return True, {"degraded": "锁不可用: %s" % exc}
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "pid": os.getpid(),
                "purpose": "night_batch",
                "host": os.environ.get("COMPUTERNAME") or "?",
                "start": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "start_ts": round(time.time(), 1),
            }, ensure_ascii=False))
        return True, {"pid": os.getpid()}
    return False, _lock_meta()


def touch_lock() -> None:
    """持锁心跳：仅 touch mtime。"""
    try:
        os.utime(LOCK_PATH, None)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/night_batch.py", exc)


def release_lock() -> None:
    """释放；锁已被他人抢占重建时不动别人的锁。"""
    meta = _lock_meta()
    if int(meta.get("pid") or 0) != os.getpid():
        return
    try:
        os.remove(LOCK_PATH)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/night_batch.py", exc)


def write_timeout_alert(log_path: str, rounds: int, jev: int, xq: int,
                        streak: int) -> Path:
    """连续 rc=124 告警旗：与 BACKUP_FAILED_*.flag 同目录同约定，alert_forward 会转发。"""
    ALERT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = ALERT_DIR / ("BATCH_TIMEOUT_%s.flag" % stamp)
    payload = {
        "type": "night_batch_timeout",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pid": os.getpid(),
        "host": os.environ.get("COMPUTERNAME") or "?",
        "rounds": rounds,
        "jev_backlog": jev,
        "xq_backlog": xq,
        "consecutive_rc124": streak,
        "log": str(log_path),
        "hint": "night_batch 子任务连续 rc=124：查看 log 尾部 ROUND/rc 行；"
                "若为 JEV 优先核对 --deadline 宽限，若为 XQ 核对 --xq-budget/limit",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return path


def _count(db: Path, sql: str) -> int:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True, timeout=10)
        try:
            return int(con.execute(sql).fetchone()[0])
        finally:
            con.close()
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return -1


def backlog() -> tuple[int, int]:
    """(待判 JEV 条数, 待分类雪球条数)。读不到返回 -1。"""
    jev = _count(NEWS_DB, "SELECT COUNT(*) FROM news WHERE (jev IS NULL OR jev='')")
    try:
        total = _count(XQ_DB, "SELECT COUNT(*) FROM xq_status")
        done = _count(XQ_DB, "SELECT COUNT(*) FROM xq_status_classified")
    except Exception:  # noqa: BLE001
        total = done = -1
    xq = (total - done) if (total >= 0 and done >= 0) else -1
    return jev, xq


class Logger:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg: str) -> None:
        line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/night_batch.py", exc)


def _cutoff(until: str) -> datetime:
    """今天的 HH:MM；若已过则该时刻视为明天。"""
    hh, mm = (int(x) for x in until.split(":"))
    now = datetime.now()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _jev_child_env() -> dict:
    """JEV 子进程环境：预筛默认 safe（2026-10-30 PREFILTERON，用户批准）。

    只对 JEV 子进程生效（XQ 等其他子进程不受影响）；父进程显式设置
    JEV_PREFILTER 时尊重父值（便于回滚/对照）。
    """
    env = dict(os.environ)
    env.setdefault("JEV_PREFILTER", "safe")
    return env


def _run(cmd: list[str], cwd: Path, timeout: float, env: dict | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                              timeout=timeout, check=False, env=env)
    except subprocess.TimeoutExpired:
        return 124, "HARD-KILL(%ss)" % int(timeout)
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    tail = [ln for ln in (out + "\n" + err).splitlines() if ln.strip()]
    return proc.returncode, (tail[-1].strip() if tail else "")


def _run_soft(cmd: list[str], cwd: Path, soft_budget_s: float,
              hard_timeout_s: float = XQ_HARD_TIMEOUT_S) -> tuple[int, str, float, str]:
    """带软预算的子进程运行器：到 soft_budget_s 主动 terminate 并落进度。

    返回 (rc, tail, elapsed_s, stop_kind)；stop_kind ∈ {done, soft-stop, hard-kill}。
    说明：xq_classify 每条 commit，terminate 最多丢在飞的一条，已落进度不回滚。
    """
    started = time.time()
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    out = b""
    stop_kind = "done"
    while True:
        try:
            out, _ = proc.communicate(timeout=5)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.time() - started
            if elapsed >= soft_budget_s:
                proc.terminate()
                stop_kind = "soft-stop"
                try:
                    out, _ = proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, _ = proc.communicate()
                break
            if elapsed >= hard_timeout_s:
                proc.kill()
                stop_kind = "hard-kill"
                out, _ = proc.communicate()
                break
    elapsed = time.time() - started
    rc = proc.returncode if proc.returncode is not None else 124
    text = (out or b"").decode("utf-8", "replace")
    tail = [ln for ln in text.splitlines() if ln.strip()]
    return rc, (tail[-1].strip() if tail else ""), elapsed, stop_kind


def _tail_text(path: Path, max_bytes: int = 65536) -> str:
    """读日志尾部（最多 max_bytes），用于旧版实例探测，避免整文件读入。"""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _stop_requested(stop: Path, cutoff: datetime) -> bool:
    return stop.exists() or datetime.now() >= cutoff


def build_parser() -> argparse.ArgumentParser:
    """CLI 定义（2026-10-26 EXEC-R27 抽成函数，便于单测解析/默认值）。"""
    ap = argparse.ArgumentParser(description="夜间长跑：JEV 回填 + 雪球分类 串行轮转")
    ap.add_argument("--until", default="07:00", help="收工时刻 HH:MM（默认 07:00）")
    ap.add_argument("--batch-jev", type=int, default=600)
    ap.add_argument("--batch-xq", type=int, default=150,
                    help="雪球单轮条数预算（2026-10-25 EXEC-R13：600→150，"
                         "按实测吞吐自适应下调，避免每轮撞 1800s 硬超时）")
    ap.add_argument("--deadline", type=int, default=1500,
                    help="每轮 JEV 子脚本自收口秒数（外层硬时限=该值+600s 宽限）")
    ap.add_argument("--xq-budget", type=float, default=XQ_SOFT_BUDGET_S,
                    help="雪球单轮软预算秒数（到点主动 terminate 收口）")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--stop-file", default=DEFAULT_STOP)
    ap.add_argument("--max-rounds", type=int, default=200)
    ap.add_argument("--stall-retries", type=int, default=STALL_RETRIES_DEFAULT,
                    help="连续无推进的重试次数（默认 3，用尽才退出）")
    ap.add_argument("--stall-backoff", type=float, default=STALL_BACKOFF_DEFAULT,
                    help="退避基数秒数，按次数线性递增（默认 300）")
    ap.add_argument("--dry-run", action="store_true")
    # ---- 2026-10-26 EXEC-R27（计划 B 白天调度；默认全关，不加参数=旧行为）----
    ap.add_argument("--xq-window", default="", metavar="HH:MM-HH:MM",
                    help="仅在此时段内跑 XQ 轮（如 08:00-10:00；支持跨午夜 "
                         "22:00-06:00）；不传=不限窗口（旧行为）")
    ap.add_argument("--xq-min-jev", type=int, default=None, metavar="N",
                    help="JEV 待判积压 ≥N 时跳过 XQ 轮（计划 B 推荐 %d）；"
                         "不传=不按积压跳过（旧行为）" % XQ_MIN_JEV_RECOMMENDED)
    ap.add_argument("--jev-only", action="store_true",
                    help="完全跳过 XQ，只跑 JEV（JEV 优先窗口/应急用）")
    ap.add_argument("--pause-m3", action="store_true",
                    help="运行期暂停 M3（写 k266_m3.pause+停 M3 进程），收工恢复；"
                         "用于白天 JEV 独占窗口，默认关")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    # EXEC-R27：新参数先校验，非法值 rc=2、不进循环/不读库/不写日志文件。
    xq_win = None
    if args.xq_window:
        try:
            xq_win = parse_xq_window(args.xq_window)
        except ValueError as exc:
            ap.error("--xq-window 非法（应形如 HH:MM-HH:MM，如 08:00-10:00）：%s" % exc)
    if args.xq_min_jev is not None and args.xq_min_jev < 0:
        ap.error("--xq-min-jev 必须 >=0（0=JEV>=0 时总跳过 XQ；不传=不按积压跳）")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    xq_policy = xq_policy_text(args.jev_only, xq_win, args.xq_min_jev)
    log = Logger(args.log)
    cutoff = _cutoff(args.until)
    jev0, xq0 = backlog()
    if args.dry_run:
        log("START 收工时间=%s | 起始积压 JEV=%s 雪球=%s | 批=%d/%d | XQ 策略: %s"
            % (cutoff.strftime("%m-%d %H:%M"), jev0, xq0, args.batch_jev,
               args.batch_xq, xq_policy))
        if xq0 <= 0:
            xq_now = "XQ 积压=0（无轮可跑）"
        else:
            _reason = xq_skip_reason(args.jev_only, xq_win, args.xq_min_jev,
                                     datetime.now(), jev0)
            xq_now = ("跳过（%s）" % _reason) if _reason else "允许跑"
        log("DRY-RUN 不执行；预计 JEV %.1f 小时、雪球 %.1f 小时（按 2s/1s 估）"
            " | XQ 当前判定: %s"
            % (max(jev0, 0) * 2 / 3600.0, max(xq0, 0) * 1 / 3600.0, xq_now))
        return 0

    # 过渡期兜底（2026-10-25 EXEC-R13）：旧版实例没有锁，靠日志近动 + 未收工 START 判断。
    log_path = Path(args.log)
    try:
        log_text = _tail_text(log_path)
        log_age = time.time() - log_path.stat().st_mtime
    except OSError:
        log_text, log_age = "", 1e9
    if log_looks_live(log_text) and log_age < LOG_LIVE_WINDOW_S:
        log("SKIP 检测到未收工实例（日志最后是 START 未 END 且 %.0f 分钟内有活动）；"
            "本实例退出，避免双实例" % (log_age / 60.0))
        return 0

    locked, meta = acquire_instance_lock()
    if not locked:
        log("SKIP 已有 night_batch 持锁 pid=%s host=%s start=%s（锁 %s）；本实例退出"
            % (meta.get("pid"), meta.get("host"), meta.get("start"), LOCK_PATH))
        return 0

    m3_paused = False
    try:
        if args.pause_m3:
            # 2026-10-26 OS-FIX-DAYBATCH：白天独占窗口——写暂停旗并停 M3 进程，
            # 让 JEV 拿到 ollama 锁；finally 恢复（异常/被杀留下旗由 k266 自愈）。
            try:
                import jev_night as _jn
                _jn.pause_m3(log, owner="night_batch")
                m3_paused = True
                log("M3 PAUSED（--pause-m3）收工自动恢复")
            except Exception as exc:  # noqa: BLE001
                log("ALERT M3 暂停失败（继续跑，可能仍被抢锁）：%s"
                    % str(exc)[:160])
        log("START 收工时间=%s | 起始积压 JEV=%s 雪球=%s | 批=%d/%d | lock pid=%s"
            " | XQ 策略: %s"
            % (cutoff.strftime("%m-%d %H:%M"), jev0, xq0, args.batch_jev,
               args.batch_xq, os.getpid(), xq_policy))
        stop = Path(args.stop_file)
        rounds = 0
        stall = 0
        timeout_streak = 0
        timeout_alerted = False
        xq_skipped = 0
        xq_limit = max(XQ_MIN_LIMIT, min(int(args.batch_xq), 600))
        last_jev, last_xq = jev0, xq0
        while datetime.now() < cutoff and rounds < args.max_rounds:
            if stop.exists():
                log("STOP 检测到停止旗 %s" % stop)
                break
            touch_lock()
            rounds += 1
            jev, xq = backlog()
            log("ROUND %d | 剩余 JEV=%s 雪球=%s | xq_limit=%d"
                % (rounds, jev, xq, xq_limit))
            if jev == 0 and xq == 0:
                log("DONE 两条队列都空了")
                break
            jev_elapsed = 0.0
            xq_elapsed = 0.0

            # ---- JEV：子脚本 --deadline 自收口；外层只加宽限防 hang ----
            if jev and jev > 0:
                t0 = time.time()
                hard_s = hard_timeout_jev(args.deadline)
                rc, tail = _run([PY, "-X", "utf8", "-B", str(JEV),
                                 "--batch", str(args.batch_jev),
                                 "--deadline", str(args.deadline),
                                 "--alert-threshold", ALERT_THRESHOLD],
                                ROOT / "news_collector", hard_s, env=_jev_child_env())
                elapsed = time.time() - t0
                jev_elapsed = elapsed
                if rc == 124:
                    timeout_streak += 1
                    log("  JEV rc=124 %.0fs HARD-KILL(deadline+%ss)：子脚本未按 "
                        "--deadline 收口，疑似 hang/排队" % (elapsed, JEV_GROUP_WORST_S))
                else:
                    timeout_streak = 0
                log("  JEV rc=%s %.0fs %s" % (rc, elapsed, tail[:120]))

            if _stop_requested(stop, cutoff):
                break

            # ---- XQ：单轮条数预算 + 软预算到点主动收口 ----
            # EXEC-R27（计划 B）：--jev-only / --xq-window / --xq-min-jev 任一命中
            # 则本轮跳过 XQ（JEV 已先行跑完），逐轮打印原因便于次日核验。
            xq_skip = (xq_skip_reason(args.jev_only, xq_win, args.xq_min_jev,
                                      datetime.now(), jev)
                       if (xq and xq > 0) else None)
            if xq and xq > 0 and xq_skip:
                xq_skipped += 1
                log("  XQ-SKIP %s | 剩余 JEV=%s 雪球=%s（按策略延后 XQ）"
                    % (xq_skip, jev, xq))
            elif xq and xq > 0:
                t0 = time.time()
                rc, tail, elapsed, stop_kind = _run_soft(
                    [PY, "-X", "utf8", "-B", str(XQ),
                     "--limit", str(xq_limit),
                     "--order", "asc", "--worker-id", "nightA",
                     "--segment", "B"],
                    ROOT / "quant_backtest", float(args.xq_budget))
                xq_elapsed = elapsed
                if stop_kind == "soft-stop":
                    log("  XQ SOFT-STOP %.0fs（预算 %ss，逐条 commit 已落进度）"
                        % (elapsed, int(args.xq_budget)))
                elif stop_kind == "hard-kill" or rc == 124:
                    timeout_streak += 1
                    log("  XQ rc=124 %.0fs HARD-KILL(%ss)：软收口失效"
                        % (elapsed, int(XQ_HARD_TIMEOUT_S)))
                else:
                    timeout_streak = 0
                log("  XQ  rc=%s %.0fs %s" % (rc, elapsed, tail[:120]))

            # ---- 本轮吞吐 / 自适应 / 告警 ----
            jev2, xq2 = backlog()
            moved_jev = (last_jev - jev2) if (last_jev >= 0 and jev2 >= 0) else 0
            moved_xq = (last_xq - xq2) if (last_xq >= 0 and xq2 >= 0) else 0
            jev_rate = (moved_jev / jev_elapsed * 60.0) if jev_elapsed > 0 else 0.0
            xq_rate = (moved_xq / xq_elapsed * 60.0) if xq_elapsed > 0 else 0.0
            log("  ROUND %d 吞吐 JEV=+%s(%.1f 条/分) XQ=+%s(%.1f 条/分) | 剩余 JEV=%s 雪球=%s"
                % (rounds, moved_jev, jev_rate, moved_xq, xq_rate, jev2, xq2))
            if moved_xq > 0 and xq_elapsed > 0:
                xq_limit = next_xq_limit(moved_xq, xq_elapsed,
                                         float(args.xq_budget),
                                         cap=int(args.batch_xq))

            if timeout_alert_due(timeout_streak) and not timeout_alerted:
                try:
                    flag = write_timeout_alert(args.log, rounds, jev2, xq2,
                                               timeout_streak)
                    log("ALERT 连续 %d 个子任务 rc=124 超时，已写告警旗 %s"
                        % (timeout_streak, flag))
                except OSError as exc:
                    log("ALERT 写告警旗失败：%s" % exc)
                timeout_alerted = True
            elif timeout_streak == 0:
                timeout_alerted = False

            moved = moved_jev + moved_xq
            if moved <= 0:
                stall += 1
                retry, wait_s = stall_decision(stall, args.stall_retries,
                                               args.stall_backoff)
                if not retry:
                    log("STALL 连续 %d 轮无推进（JEV=%s 雪球=%s），退避重试用尽，退出"
                        % (stall, jev2, xq2))
                    break
                log("STALL 第 %d 轮无推进（JEV=%s 雪球=%s），退避 %.0fs 后重试（上限 %d 次）"
                    % (stall, jev2, xq2, wait_s, args.stall_retries))
                end = time.time() + wait_s
                while time.time() < end:
                    if stop.exists() or datetime.now() >= cutoff:
                        break
                    touch_lock()
                    time.sleep(min(STALL_SLEEP_CHUNK, max(0.1, end - time.time())))
                if stop.exists() or datetime.now() >= cutoff:
                    log("STOP 退避期间收到停止旗或到达收工时间，退出")
                    break
            else:
                stall = 0
            last_jev, last_xq = jev2, xq2

        jev1, xq1 = backlog()
        log("END 轮数=%d | 剩余 JEV=%s 雪球=%s | 本轮完成 JEV=%s 雪球=%s"
            " | XQ 跳过=%d 轮（策略: %s）"
            % (rounds, jev1, xq1,
               (jev0 - jev1) if (jev0 >= 0 and jev1 >= 0) else "?",
               (xq0 - xq1) if (xq0 >= 0 and xq1 >= 0) else "?",
               xq_skipped, xq_policy))
        return 0
    finally:
        if m3_paused:
            try:
                import jev_night as _jn
                _jn.resume_m3(log, owner="night_batch")
                log("M3 RESUMED（--pause-m3 收工恢复）")
            except Exception as exc:  # noqa: BLE001
                log("ALERT M3 恢复失败（下轮 k266 tick 自愈）：%s"
                    % str(exc)[:160])
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
