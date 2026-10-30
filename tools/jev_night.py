# -*- coding: utf-8 -*-
"""jev_night.py —— 夜间 JEV 独占跑批（22:00 暂停 M3 → 07:00 恢复）。

为什么要它
----------
2026-10-25 实测：news.db 有约 2 万条待 JEV 判定。云端 typesafe 通道需要海外代理
（127.0.0.1:7890），本机没有代理进程 → 全部回落本地 9b，与 M3 结构化抢同一张
GPU（Ollama NUM_PARALLEL=1 强制串行）。实测 JEV 仅 191 条/小时 → 要 4.3 天。

独占后（M3 停、无锁竞争）预期 ~845 条/小时，一夜 9 小时约 7.6k，3 夜清完。

怎么保证 M3 夜里真的不回来
--------------------------
`k266_services.ps1` 每 5 分钟跑一次，扫到新文档（输出含 "OK d"）就会异步拉起 M3。
所以本脚本先写暂停旗 `C:\\Temp\\k266_m3.pause`，并在 k266_services.ps1 里加了闸：
旗在 → 本轮不拉 M3。收工时删旗，下一 tick 自然恢复。

纪律
----
  · 单实例锁（2026-10-26 EXEC-R30）：`locks/jev_night.lock`（JSON pid/started_at），
    存活实例在跑时重复启动会打印 SKIP 并 exit 0；陈旧锁（pid 死/2h 无心跳）自动接管；
    长跑每轮 touch 心跳；`--dry-run` 不拿锁；`JEV_NIGHT_LOCK_PATH` 可改锁路径（测试/演练）。
  · 只停命令行含 `k266_m3_structure` 的 python 进程，绝不按名字一把杀
  · 全程落日志 C:\\Temp\\jev_night.log
  · 支持停止旗 C:\\Temp\\jev_night.stop 与 --dry-run

用法：
    python -B tools/jev_night.py --start 22:00 --until 07:00
    python -B tools/jev_night.py --dry-run
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import logging

ROOT = Path(__file__).resolve().parents[1]
JEV = ROOT / "news_collector" / "jev_backfill.py"
NEWS_DB = ROOT / "news_collector" / "news.db"
PAUSE_FILE = Path(r"C:\Temp\k266_m3.pause")
DEFAULT_LOG = r"C:\Temp\jev_night.log"
DEFAULT_STOP = r"C:\Temp\jev_night.stop"
PY = sys.executable

# 2026-10-26 OS-FIX-JEVALERT：夜间批处理不再把 --alert-threshold 固定为 0。
# 旧值 0 会关闭 jev_backfill 的"积压超过阈值"飞书告警（JEV 审计 §四-⑤：夜间唯一告警被关掉）。
# 默认 100 与 jev_backfill 自身默认一致；JEV_ALERT_THRESHOLD 可覆盖（0 仍表示关闭，显式才生效）。
ALERT_THRESHOLD = os.environ.get("JEV_ALERT_THRESHOLD", "100")

# ---------------------------------------------------------------------------
# 单实例锁（2026-10-26 EXEC-R30；复用 tools/night_batch.py 的锁语义/命名风格）
# ---------------------------------------------------------------------------
# 背景：EXEC-R24 发现 22:45 实例未写 END、23:59 又起一个 START（旧版无锁 → 双实例
# 互等 ollama_lock，抵消吞吐治理收益）。锁文件 JSON: pid/purpose/host/started_at/start_ts；
# 存活 → SKIP；陈旧（pid 死 或 mtime>2h 无心跳）→ 清理后接管；finally 释放。
# 兼容：--dry-run 不拿锁；JEV_NIGHT_LOCK_PATH 可把锁挪到临时路径（测试/演练用）。
# 过渡期兜底：旧版无锁实例仍活时，用日志「最后 START 未 END 且 40min 内活跃」判 SKIP。
LOCK_PATH = Path(os.environ.get(
    "JEV_NIGHT_LOCK_PATH", str(ROOT / "locks" / "jev_night.lock")))
# 主锁目录不可写时（例如从受限沙箱会话手工拉起）退化到工作区级锁；生产环境主路径可写，
# 正常不会用到 fallback。可用 JEV_NIGHT_LOCK_FALLBACK 覆盖。
LOCK_FALLBACK_PATH = Path(os.environ.get(
    "JEV_NIGHT_LOCK_FALLBACK", r"C:\项目\.locks\jev_night.lock"))
LOCK_STALE_S = 7200            # 锁 mtime 超 2h（且 pid 已死）→ 遗留锁可回收
LOG_LIVE_WINDOW_S = 2400       # 旧版实例探测窗口：40 分钟（>单轮 deadline 1500s 的余量）
_ACTIVE_LOCK_PATH: Path | None = None   # 本次进程实际生效的锁路径（可能是 fallback）


def _lock_path(lock_path: Path | None = None) -> Path:
    """本次进程实际使用的锁路径：显式参数 > 已生效锁 > 主路径。"""
    if lock_path is not None:
        return Path(lock_path)
    return _ACTIVE_LOCK_PATH if _ACTIVE_LOCK_PATH is not None else LOCK_PATH


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


def _lock_meta(lock_path: Path | None = None) -> dict:
    try:
        with open(_lock_path(lock_path), encoding="utf-8") as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def lock_is_stale(meta: dict, lock_path: Path | None = None,
                  now: float | None = None, stale_s: float = LOCK_STALE_S) -> bool:
    """锁是否可回收：持锁 pid 已死，或心跳（mtime）超过 stale_s。纯逻辑便于单测。"""
    path = _lock_path(lock_path)
    now = time.time() if now is None else now
    pid = int((meta or {}).get("pid") or 0)
    if pid and pid != os.getpid() and not _pid_alive(pid):
        return True
    try:
        return (now - path.stat().st_mtime) > stale_s
    except OSError:
        return True


def _try_acquire(path: Path) -> tuple[bool, dict]:
    """在指定路径尝试抢锁（O_CREAT|O_EXCL；遗留锁回收）。返回 (拿到?, meta)。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # 父目录不可建（如父路径是文件/权限不足）→ 交由调用方退回 fallback 或降级。
        return True, {"degraded": "锁目录不可用: %s" % exc}
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            meta = _lock_meta(path)
            if not lock_is_stale(meta, path):
                return False, meta
            try:
                os.remove(path)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/jev_night.py", exc)
            continue
        except OSError as exc:
            # 锁目录不可写等意外：不因护栏故障拖死夜跑，降级为"无锁运行"并记录。
            return True, {"degraded": "锁不可用: %s" % exc}
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "pid": os.getpid(),
                "purpose": "jev_night",
                "host": os.environ.get("COMPUTERNAME") or "?",
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "start_ts": round(time.time(), 1),
            }, ensure_ascii=False))
        return True, {"pid": os.getpid(), "lock_path": str(path)}
    return False, _lock_meta(path)


def acquire_instance_lock(lock_path: Path | None = None) -> tuple[bool, dict]:
    """抢单实例锁：主路径不可写时退回工作区级 fallback；都不可写则降级无锁运行。

    返回 (拿到?, meta)。meta 含 pid/lock_path；degraded 表示锁不可用（无锁运行）。
    已存在且持锁 pid 存活 → (False, meta)，调用方应打印 SKIP 并 exit 0。
    """
    global _ACTIVE_LOCK_PATH
    if lock_path is not None:
        ok, meta = _try_acquire(Path(lock_path))
        if ok and not meta.get("degraded"):
            _ACTIVE_LOCK_PATH = Path(lock_path)
        return ok, meta
    ok, meta = _try_acquire(LOCK_PATH)
    if not meta.get("degraded"):
        if ok:
            _ACTIVE_LOCK_PATH = LOCK_PATH
        return ok, meta
    # 主锁路径不可写（受限会话）：退回工作区级 fallback，避免"无锁静默运行"。
    fb_ok, fb_meta = _try_acquire(LOCK_FALLBACK_PATH)
    if not fb_meta.get("degraded"):
        if fb_ok:
            _ACTIVE_LOCK_PATH = LOCK_FALLBACK_PATH
        return fb_ok, fb_meta
    return True, {"degraded": "主锁与 fallback 均不可写: %s；%s"
                              % (meta.get("degraded"), fb_meta.get("degraded"))}


def touch_lock(lock_path: Path | None = None) -> None:
    """持锁心跳：仅 touch mtime（等待分片/每轮前后调用）。"""
    try:
        os.utime(_lock_path(lock_path), None)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/jev_night.py", exc)


def release_lock(lock_path: Path | None = None) -> None:
    """释放；锁已被他人抢占重建时不动别人的锁。"""
    path = _lock_path(lock_path)
    meta = _lock_meta(path)
    if int(meta.get("pid") or 0) != os.getpid():
        return
    try:
        os.remove(path)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/jev_night.py", exc)


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


def log_looks_live(text: str) -> bool:
    """日志尾部是否有「START 之后没有 END」的未收工会话（旧版实例探测用）。"""
    last_start = last_end = -1
    for i, line in enumerate(text.splitlines()):
        if "] START " in line:
            last_start = i
        elif "] END " in line:
            last_end = i
    return last_start >= 0 and last_start > last_end

PS_LIST_M3 = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Where-Object { $_.CommandLine -match 'k266_m3_structure' } | "
    "Select-Object -ExpandProperty ProcessId"
)


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
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/jev_night.py", exc)


def _hm(text: str) -> tuple[int, int]:
    hh, mm = text.split(":")
    return int(hh), int(mm)


def window(start: str, until: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """返回 (开始, 结束)。若当前已在窗口内，从当前时刻起算（不回溯等待）。

    2026-10-30 BATCH2 修复：旧实现只在「今天 start 已过」时把 begin 设为 now；
    跨午夜窗口（21:50-07:00）若在 00:00-06:59 重启，begin 会被算成「今天 21:50」，
    导致等待 15.8-20.8 小时（还被任务 10h 时限先杀）——夜跑实际无崩溃恢复。
    现同时检查「昨晚 start → 今天 until」这个候选窗口。
    """
    now = now or datetime.now()
    sh, sm = _hm(start)
    uh, um = _hm(until)
    begin_today = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end_today = begin_today.replace(hour=uh, minute=um, second=0, microsecond=0)
    if end_today <= begin_today:
        end_today += timedelta(days=1)
    # 候选 A：昨晚开启、今天凌晨仍在窗口内（重启恢复场景）
    begin_y = begin_today - timedelta(days=1)
    end_y = end_today - timedelta(days=1)
    if begin_y <= now < end_y:
        return now, end_y
    # 候选 B：今天窗口
    if begin_today <= now < end_today:
        return now, end_today
    # 尚未开始：等到今天窗口开始
    return begin_today, end_today


def backlog() -> int:
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True, timeout=10)
        try:
            return int(con.execute(
                "SELECT COUNT(*) FROM news WHERE jev IS NULL OR jev=''").fetchone()[0])
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return -1


def m3_pids() -> list[int]:
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", PS_LIST_M3],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            out.append(int(line))
    return out


def pause_m3(log: Logger, dry_run: bool = False, owner: str = "jev_night") -> int:
    """写暂停旗（带 owner/pid）并停掉 M3，返回被停的进程数。

    2026-10-30 BATCH2：DayBatch 与 JevNight 共用同一暂停旗，原实现无 owner，
    先收工者会无条件删旗导致另一方仍在跑时 M3 被恢复；现写入 owner，恢复时校验。
    """
    if dry_run:
        pids = m3_pids()
        log("DRY-RUN 将写暂停旗 %s 并停止 M3 进程 %s" % (PAUSE_FILE, pids or "（无）"))
        return len(pids)
    PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAUSE_FILE.write_text(
        json.dumps({"owner": owner, "pid": os.getpid(),
                    "at": datetime.now().isoformat()}, ensure_ascii=False),
        encoding="utf-8")
    killed = 0
    for pid in m3_pids():
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Stop-Process -Id %d -Force" % pid],
                           capture_output=True, timeout=60)
            killed += 1
            log("  已停 M3 pid=%d" % pid)
        except (OSError, subprocess.TimeoutExpired):
            log("  停 M3 pid=%d 失败" % pid)
    log("M3 暂停完成（停 %d 个），暂停旗已写" % killed)
    return killed


def resume_m3(log: Logger, owner: str | None = None) -> None:
    """删暂停旗；owner 匹配（或旧版纯文本旗）才删，避免误删另一任务的旗。"""
    try:
        raw = PAUSE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        log("暂停旗本就不存在")
        return
    except OSError as exc:
        log("读暂停旗失败：%s" % exc)
        return
    file_owner = ""
    try:
        file_owner = str((json.loads(raw) or {}).get("owner") or "")
    except (ValueError, AttributeError):
        file_owner = "legacy"
    if owner and file_owner and file_owner not in (owner, "legacy"):
        log("暂停旗 owner=%s 非本任务(%s)，不删除" % (file_owner, owner))
        return
    try:
        PAUSE_FILE.unlink()
        log("暂停旗已删除，M3 可被 k266_services 下一 tick 恢复")
    except OSError as exc:
        log("删暂停旗失败：%s" % exc)


def run_jev(batch: int, deadline: int, log: Logger) -> tuple[int, str]:
    t0 = time.time()
    try:
        proc = subprocess.run(
            [PY, "-X", "utf8", "-B", str(JEV), "--batch", str(batch),
             "--deadline", str(deadline), "--alert-threshold", ALERT_THRESHOLD],
            cwd=str(ROOT / "news_collector"), capture_output=True,
            timeout=deadline + 300)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT(%ss)" % (deadline + 300)
    tail_lines = ((proc.stdout or b"") + (proc.stderr or b"")).decode(
        "utf-8", "replace").strip().splitlines()
    tail = tail_lines[-1] if tail_lines else ""
    log("  JEV rc=%d %.0fs %s" % (proc.returncode, time.time() - t0, tail[:140]))
    return proc.returncode, tail


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="夜间 JEV 独占跑批（暂停 M3）")
    ap.add_argument("--start", default="22:00")
    ap.add_argument("--until", default="07:00")
    ap.add_argument("--batch", type=int, default=600)
    ap.add_argument("--deadline", type=int, default=1500)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--stop-file", default=DEFAULT_STOP)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    log = Logger(args.log)
    begin, end = window(args.start, args.until)
    left0 = backlog()

    if args.dry_run:
        # 2026-10-26 EXEC-R30：dry-run 只读预演、不拿单实例锁（允许与运行实例并存）。
        log("START 计划窗口 %s → %s | 待判 JEV=%s | 批=%d 截止=%ds"
            % (begin.strftime("%m-%d %H:%M"), end.strftime("%m-%d %H:%M"),
               left0, args.batch, args.deadline))
        log("DRY-RUN 不执行。将：① 等到开始时刻 ②写暂停旗+停 M3 ③循环 JEV 到结束 ④删旗恢复")
        return 0

    # 过渡期兜底（2026-10-26 EXEC-R30）：旧版 jev_night 无锁，若其 START 未 END 且
    # 日志近期仍活跃，视为仍在跑 → SKIP，避免新实例与旧实例并行互等 ollama_lock。
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

    # 单实例锁：拿不到就 SKIP exit 0（绝不抢正在跑的实例）；主锁不可写时自动退回 fallback。
    locked, meta = acquire_instance_lock()
    if not locked:
        log("SKIP 已有 jev_night 持锁 pid=%s started_at=%s（锁 %s）；本实例退出"
            % (meta.get("pid"), meta.get("started_at"), _lock_path()))
        return 0
    if meta.get("degraded"):
        log("WARN 单实例锁不可用，降级为无锁运行：%s" % meta.get("degraded"))

    try:
        log("START 计划窗口 %s → %s | 待判 JEV=%s | 批=%d 截止=%ds | lock pid=%s path=%s"
            % (begin.strftime("%m-%d %H:%M"), end.strftime("%m-%d %H:%M"),
               left0, args.batch, args.deadline, os.getpid(), _lock_path()))
        stop = Path(args.stop_file)
        if stop.exists():
            log("WARN 启动时检测到遗留停止旗，清理后继续")
            stop.unlink(missing_ok=True)

        # ① 等到开始时刻（分片睡眠，可被停止旗打断）
        while datetime.now() < begin:
            if stop.exists():
                log("STOP 等待期检测到停止旗，退出")
                stop.unlink(missing_ok=True)
                resume_m3(log, owner="jev_night")
                log("END 轮数=0 | 等待期停止（停止旗已清理）")
                return 0
            remain = (begin - datetime.now()).total_seconds()
            touch_lock()
            time.sleep(min(300.0, max(1.0, remain)))
            if datetime.now() < begin:
                log("等待中... 距开始 %.0f 分钟" % ((begin - datetime.now()).total_seconds() / 60))

        # ② 暂停 M3
        pause_m3(log, owner="jev_night")
        touch_lock()

        # ③ 循环 JEV 直到结束/清空/停止旗
        # 2026-10-28 JEVSTOP：清零后若剩极少量"毒丸"（判读永远失败），旧逻辑会
        # 27s/轮空转到 07:00；加「连续无进展」收工闸，避免整夜空耗 GPU。
        rounds = 0
        stall = 0
        STALL_MAX_LEFT = 5
        STALL_ROUNDS = 3
        try:
            while datetime.now() < end:
                if stop.exists():
                    log("STOP 检测到停止旗")
                    stop.unlink(missing_ok=True)
                    log("STOP 已清理停止旗")
                    break
                left = backlog()
                if left == 0:
                    log("DONE 队列已清空")
                    break
                rounds += 1
                log("ROUND %d | 剩余 JEV=%s" % (rounds, left))
                touch_lock()
                rc, _ = run_jev(args.batch, args.deadline, log)
                if rc != 0:
                    time.sleep(60)
                touch_lock()
                new_left = backlog()
                if new_left is not None and new_left <= STALL_MAX_LEFT and new_left >= left:
                    stall += 1
                    if stall >= STALL_ROUNDS:
                        log("STALL 剩余 %s 条连续 %d 轮无进展（疑似毒丸），提前收工"
                            % (new_left, stall))
                        break
                else:
                    stall = 0
        finally:
            left1 = backlog()
            resume_m3(log, owner="jev_night")
            log("END 轮数=%d | 剩余 JEV=%s | 本轮完成=%s"
                % (rounds, left1, (left0 - left1) if (left0 >= 0 and left1 >= 0) else "?"))
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
