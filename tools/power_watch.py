# -*- coding: utf-8 -*-
"""电源/挂起心跳守护（独立脚本，零第三方依赖，Python 3.9+）。

为什么要有它
------------
本机 60+ 个计划任务都只在"醒来后"继续写日志，事后无法区分
「任务真的失败了」和「机器当时在睡觉」。本脚本没有任何业务逻辑，
唯一目的是把「什么时候睡的、睡了多久」变成可查询的数：每 30 秒写一行
JSONL，相邻两拍的「墙钟增量 - 不含睡眠的单调钟增量」> 5 秒即判为一次挂起。

实测结论（2026-10-24，本机 Windows，Python 3.11.9）
---------------------------------------------------
1. `time.monotonic()` **跨睡眠照走，不能用来检测挂起**。
   `time.get_clock_info("monotonic")` 实测 implementation="GetTickCount64()"；
   实测 monotonic=9776.8s，而「墙钟 - 开机时刻 13:29:05（Kernel-Boot 事件 27）」
   =9775s，两者一致 —— 说明它把当天 18.1min + 10.0min 两次睡眠都算进去了。
   若照抄"monotonic 跨睡眠停走"的假设，这台机器永远检测不到挂起。
2. 真正停走的是 `kernel32!QueryUnbiasedInterruptTime()`（无偏中断时间，100ns 单位，
   定义上不含睡眠/休眠）：同一时刻实测 unbiased=8091.6s、GetTickCount64=9776.8s，
   差 1685.2s ≈ 1085s(13:50:35→14:08:40) + 600s(15:32:45→15:42:45)，逐秒吻合。
   → 因此字段 `mono_s` 存的是这个无偏时钟（**不是** time.monotonic()），
     挂起判定也用它；只有在该 API 取不到时才退回 time.monotonic() 并在
     stderr 告警一次（此时跨睡眠只会漏报、不会误报）。
   （`time.perf_counter()` 基于 QPC，单点采样无法区分"计数起点偏移"和"是否跨睡眠
     走"，未做结论也不使用。）
3. `powercfg /lastwake` 中文系统输出「唤醒源计数 - N」，英文「Wake Source Count - N」，
   两种都解析；实测输出编码为 UTF-8（其他环境可能 CP936/UTF-16），按多编码兜底解码。

字段约定：取不到的写 null，绝不编造。
事件值：tick / suspend_resume / reboot / stop。
（reboot = mono_s 倒退，说明中途重启过；此时墙钟空档无法拆成睡眠，suspend_s 记 0。）

不碰：不启停服务、不读凭据、不写注册表、不改电源设置。
"""
import argparse
import ctypes
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
import logging

DEFAULT_INTERVAL_S = 30.0
DEFAULT_LOG = r"C:\Temp\power_watch.jsonl"
SUSPEND_THRESHOLD_S = 5.0   # 墙钟增量比单调钟增量多出这么多秒 => 判为一次挂起
LAST_WAKE_EVERY = 10        # 每 10 拍（默认 5 分钟）跑一次 powercfg /lastwake
ROTATE_EVERY = 20           # 每 20 拍检查一次日志大小
ROTATE_BYTES = 2 * 1024 * 1024
CREATE_NO_WINDOW = 0x08000000  # 守护进程调 powercfg 时不弹黑框
POWERCFG_TIMEOUT_S = 20

_STOP = False
_WARNED = set()


# ---------------------------------------------------------------- 基础设施
def setup_streams() -> None:
    """stdout/stderr 一律 UTF-8（启动器重定向到 .log 时也不受控制台代码页影响）。

    循 tools/verify_backups.py 判例：跨进程边界宁可显式编码，不赌终端本地代码页。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/power_watch.py", exc)


def _warn(msg: str, once_key=None) -> None:
    """告警打到 stderr（启动器会重定向到 err.log）；once_key 相同只喊一次。"""
    if once_key is not None:
        if once_key in _WARNED:
            return
        _WARNED.add(once_key)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    try:
        sys.stderr.write(f"[{stamp}] [power_watch] {msg}\n")
        sys.stderr.flush()
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/power_watch.py", exc)


class _ULong64(ctypes.Structure):
    _fields_ = [("QuadPart", ctypes.c_ulonglong)]


def _load_unbiased_clock():
    """取 QueryUnbiasedInterruptTime（不含睡眠的中断时间），失败返回 None。"""
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        func = kernel32.QueryUnbiasedInterruptTime
        func.argtypes = [ctypes.POINTER(_ULong64)]
        func.restype = ctypes.c_int

        def read() -> float:
            buf = _ULong64()
            if not func(ctypes.byref(buf)):
                raise OSError(ctypes.get_last_error(), "QueryUnbiasedInterruptTime 失败")
            return buf.QuadPart / 1e7  # 100ns -> 秒

        read()  # 探针：确认这版系统真能调
        return read
    except (AttributeError, OSError, ValueError):
        return None


_UNBIASED = _load_unbiased_clock()
CLOCK_SRC = "QueryUnbiasedInterruptTime" if _UNBIASED else "time.monotonic()"


def monotonic_no_sleep() -> float:
    """"不含睡眠"的单调钟（秒）。实测口径见模块 docstring。"""
    if _UNBIASED is not None:
        try:
            return _UNBIASED()
        except OSError:
            return time.monotonic()
    return time.monotonic()


def tick_uptime_s():
    """GetTickCount64 => 开机以来秒数（含睡眠，实测等于墙钟开机动量）。"""
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return kernel32.GetTickCount64() / 1000.0
    except (AttributeError, OSError, ValueError):
        return None


def boot_time_text():
    """开机时刻（本地时间字符串）。由 GetTickCount64 反推，取不到写 null。"""
    uptime = tick_uptime_s()
    if uptime is None:
        return None
    moment = datetime.now().astimezone() - timedelta(seconds=uptime)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_local(moment: datetime) -> str:
    """2026-10-24 16:05:00+08:00（%z 默认没有冒号，这里补上）。"""
    offset = moment.strftime("%z")
    if offset:
        offset = f"{offset[:3]}:{offset[3:]}"
    return f"{moment.strftime('%Y-%m-%d %H:%M:%S')}{offset}"


def _parse_local_ts(text):
    """反向解析 ts_local，失败返回 None。"""
    if not isinstance(text, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- 日志
def resolve_log_path(preferred: str) -> str:
    """首选路径写不进去就退到 %TEMP%，且只警告一次。"""
    preferred = os.path.abspath(preferred)
    candidates = [preferred]
    fallback = os.path.join(
        tempfile.gettempdir(), os.path.basename(preferred) or "power_watch.jsonl"
    )
    if os.path.normcase(fallback) != os.path.normcase(preferred):
        candidates.append(fallback)
    last_error = None
    for cand in candidates:
        try:
            os.makedirs(os.path.dirname(cand) or ".", exist_ok=True)
            with open(cand, "a", encoding="utf-8"):
                pass
            if os.path.normcase(cand) != os.path.normcase(preferred):
                _warn(f"日志目录不可写（{preferred}），已退到 {cand}")
            return cand
        except OSError as exc:
            last_error = exc
    raise SystemExit(f"[power_watch] 日志不可写，已试 {candidates}，最后错误：{last_error}")


def maybe_rotate(log_path: str) -> bool:
    """超过 2MB 就改名成 .1（只保留一份 .1）。"""
    try:
        if os.path.getsize(log_path) <= ROTATE_BYTES:
            return False
    except OSError:
        return False
    target = log_path + ".1"
    try:
        os.replace(log_path, target)
    except OSError as exc:
        _warn(f"日志轮转失败 {log_path} -> {target}：{exc}")
        return False
    _warn(f"日志已轮转 {log_path} -> {target}")
    return True


def read_last_record(log_path: str):
    """读最后一行合法 JSON，返回 (wall_ts, mono_s)；用于进程重启后仍能算出空档。"""
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size == 0:
                return None
            fh.seek(max(0, size - 16384))
            chunk = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(chunk.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        wall = _parse_local_ts(record.get("ts_local"))
        mono = record.get("mono_s")
        if wall is not None and isinstance(mono, (int, float)):
            return wall, float(mono)
    return None


def emit_record(record: dict, log_path: str, echo_json: bool) -> bool:
    """一行 JSONL。--json 时同时打到 stdout（--once 即恰好一行，供健康卡复用）。"""
    line = json.dumps(record, ensure_ascii=False)  # 落盘：UTF-8 中文可读
    if echo_json:
        # stdout：ensure_ascii=True 纯 ASCII，GBK 控制台/任意日志都不乱码
        # （循 tools/verify_backups.py 判例；解码后与原值等价）
        try:
            sys.stdout.write(json.dumps(record, ensure_ascii=True) + "\n")
            sys.stdout.flush()
        except (OSError, ValueError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/power_watch.py", exc)
    try:
        with open(log_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
        return True
    except OSError as exc:
        _warn(f"写日志失败 {log_path}：{exc}")
        return False


# ---------------------------------------------------------------- 唤醒源
_WAKE_SOURCE_RE = re.compile(
    r"(?:Wake\s+Source\s+Count|唤醒源计数)\s*[-:：]\s*(\d+)", re.IGNORECASE
)


def _decode_output(raw: bytes) -> str:
    """powercfg 输出编码不固定：BOM/UTF-8/CP936/UTF-16 依次兜底。"""
    if not raw:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/power_watch.py", exc)
    for encoding in ("utf-8-sig", "mbcs", "utf-16-le"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def read_last_wake():
    """跑一次 powercfg /lastwake，返回 {"count": 唤醒源计数, "text": 首行文本}。"""
    try:
        proc = subprocess.run(
            ["powercfg", "/lastwake"],
            capture_output=True,
            timeout=POWERCFG_TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn(f"powercfg /lastwake 调不起来：{exc}", once_key="powercfg_failed")
        return {"count": None, "text": None}
    if proc.returncode != 0:
        _warn(f"powercfg /lastwake 返回 {proc.returncode}", once_key="powercfg_rc")
        return {"count": None, "text": None}
    text = _decode_output(proc.stdout) or _decode_output(proc.stderr)
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    first = next((ln for ln in lines if ln), "")
    match = _WAKE_SOURCE_RE.search(text)
    count = int(match.group(1)) if match else None
    return {"count": count, "text": first or None}


# ---------------------------------------------------------------- 一拍
def build_record(prev, last_wake, boot_at):
    """采一拍：墙钟 + 无偏单调钟 => 判定 tick / suspend_resume / reboot。"""
    mono = monotonic_no_sleep()
    moment = datetime.now().astimezone()
    wall = moment.timestamp()

    wall_delta = None
    suspend_s = 0.0
    event = "tick"
    if prev is not None:
        prev_wall, prev_mono = prev
        wall_delta = wall - prev_wall
        mono_delta = mono - prev_mono
        if mono_delta < -1.0:
            # 单调钟倒退 = 中途重启过（无偏时钟开机从 0 开始）
            event = "reboot"
        elif wall_delta - mono_delta > SUSPEND_THRESHOLD_S:
            suspend_s = wall_delta - mono_delta
            event = "suspend_resume"

    uptime = tick_uptime_s()
    record = {
        "ts_utc": moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_local": _fmt_local(moment),
        "mono_s": round(mono, 1),
        "wall_delta_s": round(wall_delta, 1) if wall_delta is not None else None,
        "suspend_s": round(suspend_s, 1),
        "boot_time": boot_at,
        "uptime_s": int(round(uptime)) if uptime is not None else None,
        "last_wake": {"count": last_wake.get("count"), "text": last_wake.get("text")},
        "event": event,
    }
    return record, (wall, mono)


def _install_signal_handlers() -> None:
    def handler(_signum, _frame):
        global _STOP
        _STOP = True

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):  # Windows 上 SIGBREAK = CTRL_BREAK
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError, RuntimeError):
            continue


def _sleep_unless_stopped(seconds: float) -> None:
    """按 1 秒切片睡，好让 SIGTERM/CTRL_BREAK 能及时收尾。"""
    deadline = time.monotonic() + seconds
    while not _STOP:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(1.0, left))


def _write_pid_file(log_path: str, started: datetime):
    """给启动器做幂等判重（CIM/WMI 被拦时的兜底），退出时删掉。"""
    path = log_path + ".pid"
    payload = {
        "pid": os.getpid(),
        "started": started.strftime("%Y-%m-%dT%H:%M:%S"),
        "script": os.path.abspath(__file__),
        "log": log_path,
    }
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        return path
    except OSError as exc:
        _warn(f"写 pid 文件失败 {path}：{exc}")
        return None


# ---------------------------------------------------------------- 主流程
def run(args) -> int:
    if args.interval <= 0:
        _warn(f"--interval 必须 > 0，收到 {args.interval}")
        return 2

    log_path = resolve_log_path(args.log)
    prev = read_last_record(log_path)  # 先读基线再轮转：重启后仍能算出空档
    maybe_rotate(log_path)
    boot_at = boot_time_text()
    if _UNBIASED is None:
        _warn(
            "QueryUnbiasedInterruptTime 不可用，已退回 time.monotonic()——"
            "本机实测该时钟跨睡眠照走，将无法检测挂起",
            once_key="no_unbiased",
        )
    started = datetime.now().astimezone()
    pid_path = None if args.once else _write_pid_file(log_path, started)
    _warn(f"启动 pid={os.getpid()} 日志={log_path} 时钟={CLOCK_SRC} 间隔={args.interval}s")

    last_wake = {"count": None, "text": None}
    tick = 0
    try:
        while True:
            if tick and tick % ROTATE_EVERY == 0:
                maybe_rotate(log_path)
            if tick % LAST_WAKE_EVERY == 0:
                last_wake = read_last_wake()

            record, prev = build_record(prev, last_wake, boot_at)
            emit_record(record, log_path, args.json)
            if record["event"] == "suspend_resume":
                _warn(
                    f"检测到挂起：睡了 {record['suspend_s']}s，"
                    f"墙钟增量 {record['wall_delta_s']}s，恢复于 {record['ts_local']}"
                )
            tick += 1
            if args.once:
                return 0
            if _STOP:
                break
            _sleep_unless_stopped(args.interval)

        record, prev = build_record(prev, last_wake, boot_at)
        record["event"] = "stop"
        emit_record(record, log_path, args.json)
        _warn(f"收到停止信号，已写 stop 行，退出（共 {tick} 拍）")
        return 0
    except KeyboardInterrupt:
        record, prev = build_record(prev, last_wake, boot_at)
        record["event"] = "stop"
        emit_record(record, log_path, args.json)
        _warn(f"KeyboardInterrupt，已写 stop 行，退出（共 {tick} 拍）")
        return 0
    finally:
        if pid_path:
            try:
                os.remove(pid_path)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/power_watch.py", exc)


def main(argv=None) -> int:
    setup_streams()
    parser = argparse.ArgumentParser(
        prog="power_watch.py",
        description="电源/挂起心跳守护：每 30s 一行 JSONL，让'机器睡了多久'可查",
    )
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_S,
        help=f"每拍间隔秒数（默认 {DEFAULT_INTERVAL_S:g}）",
    )
    parser.add_argument(
        "--log", default=DEFAULT_LOG,
        help="JSONL 路径（默认 " + DEFAULT_LOG + "；不可写时退到系统临时目录 TEMP）",
    )
    parser.add_argument("--once", action="store_true", help="只写一拍就退出（测试/健康卡复用）")
    parser.add_argument("--json", action="store_true", help="把写出的行打到 stdout")
    args = parser.parse_args(argv)
    _install_signal_handlers()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
