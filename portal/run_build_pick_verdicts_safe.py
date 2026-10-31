# -*- coding: utf-8 -*-
"""build_pick_verdicts.py 安全运行器（P2-1）：固定 py311 + 超时 + 分片 + 进度日志 + 独立输出。

背景（2026-10-31 P2-1 结论）
- 全量 14 档在 Python311 下约数小时，历史实现只在「每 5 档」打一行，长跑像挂死；
- py312 曾出 0xC0000005（WER 实证 python312.dll，错误偏移 0x27e990）。复现收敛：
  只有 `faulthandler.dump_traceback_later` 定时栈 dump 恰好命中 pandas/pyarrow
  读链时才崩（py311 同条件稳定）；业务读链本身不开定时 dump 可跑通。结论= 固定
  Python311 + 不把定时 dump 当生产诊断手段，而不是改业务逻辑。
本文件不复制业务逻辑，只负责安全拉起 build_pick_verdicts.py：
- 固定/校验 Python311（--python 可覆盖；非 3.11 仅告警，便于 py312 对照诊断）；
- 可配置超时（--timeout，默认 3600s；超时 taskkill /T /F 进程树，退出码 124）；
- 分片：--max-archives N（尾部 N 档）/ --archive-from/--archive-to（信号日区间）；
- 进度日志：子进程 stdout 逐行带时间戳同时落盘，30s 心跳；日志默认 C:\\Temp；
- 独立输出：默认 C:\\Temp\\pick_verdicts_safe\\pick_verdicts_<ts>.json；默认拒绝
  覆盖生产 portal/data/pick_verdicts.json（含 C:\\aios、C:\\项目\\portal 别名）；
  确需生产写必须显式 --allow-prod，且运行前自动备份现有 JSON。
退出码：0=成功；2=配置/预检失败；4=产物校验失败；124=超时；130=Ctrl-C；
        其余=build_pick_verdicts.py 原样退出码（Windows 崩溃为负值）。
用法示例
  "C:/Program Files/Python311/python.exe" portal/run_build_pick_verdicts_safe.py --help
  ".../python.exe" portal/run_build_pick_verdicts_safe.py --list-archives
  ".../python.exe" portal/run_build_pick_verdicts_safe.py --max-archives 1 --timeout 1800
  ".../python.exe" portal/run_build_pick_verdicts_safe.py --archive-from 20261019 --archive-to 20261019
  生产（人工确认后）：加 --allow-prod --out portal/data/pick_verdicts.json
"""

import argparse
import glob
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
TARGET = os.path.join(HERE, "build_pick_verdicts.py")
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import build_pick_verdicts as bpv  # noqa: E402

HIST_DIR = bpv.HIST_DIR
PROD_OUT = bpv.PROD_OUT
DEFAULT_PY311 = r"C:\Program Files\Python311\python.exe"
DEFAULT_BASE_DIR = r"C:\Temp\pick_verdicts_safe"
DEFAULT_TIMEOUT = 3600
HEARTBEAT_SEC = 30
EXIT_CONFIG = 2
EXIT_VALIDATE = 4
EXIT_TIMEOUT = 124
EXIT_INTERRUPT = 130

_PROD_ALIASES = (
    PROD_OUT,
    os.path.join(REPO_ROOT, "portal", "data", "pick_verdicts.json"),
    r"C:\aios\portal\data\pick_verdicts.json",
    r"C:\项目\portal\data\pick_verdicts.json",
)

_LOG_FH = None


class ConfigError(Exception):
    """配置/预检失败（退出码 2）。"""


def _log(msg):
    """同时打控制台 + 日志文件（带时间戳；日志失败不阻断主流程）。"""
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001
        pass
    fh = _LOG_FH
    if fh is not None:
        try:
            fh.write(line + "\n")
            fh.flush()
        except Exception:  # noqa: BLE001
            pass


def _norm_day(value):
    """YYYY-MM-DD / YYYYMMDD → YYYYMMDD（只留数字前 8 位）。"""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8]


def archive_day(fp):
    """归档文件名里的信号日（build_todays_picks 以 scan_run_date 命名）。"""
    return _norm_day(os.path.basename(fp))


def prod_out_paths():
    """生产 verdicts 路径集合（realpath 归一，覆盖 C:\\aios 等 junction 别名）。"""
    return {
        os.path.normcase(os.path.realpath(os.path.abspath(p))) for p in _PROD_ALIASES
    }


def check_out_path(out_path, allow_prod=False):
    """规范化输出路径；默认拒绝生产 verdicts（防测试/回归覆盖生产 14 档/2298 票）。"""
    if not out_path:
        raise ConfigError("--out 为空")
    real = os.path.realpath(os.path.abspath(out_path))
    if os.path.normcase(real) in prod_out_paths() and not allow_prod:
        raise ConfigError(
            "拒绝写生产文件 %s（测试/回归必须写 C:\\Temp 或独立目录；"
            "确需生产写加 --allow-prod，运行前会自动备份）" % real
        )
    if not real.lower().endswith(".json"):
        raise ConfigError("输出必须是 .json：%s" % real)
    return real


def make_child_env(base_env, out_real, max_archives=0, archive_from="", archive_to="",
                   progress_every=25):
    """拼子进程环境：UTF-8 + 独立输出 + 分片/进度变量（纯函数，便于测试）。"""
    env = dict(base_env)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["VERDICTS_OUT"] = out_real
    if max_archives and int(max_archives) > 0:
        env["VERDICTS_MAX_ARCHIVES"] = str(int(max_archives))
    else:
        env.pop("VERDICTS_MAX_ARCHIVES", None)
    d_from = _norm_day(archive_from)
    d_to = _norm_day(archive_to)
    if d_from:
        env["VERDICTS_ARCHIVE_FROM"] = d_from
    else:
        env.pop("VERDICTS_ARCHIVE_FROM", None)
    if d_to:
        env["VERDICTS_ARCHIVE_TO"] = d_to
    else:
        env.pop("VERDICTS_ARCHIVE_TO", None)
    env["VERDICTS_PROGRESS_EVERY"] = str(int(progress_every))
    return env


def version_tuple(text):
    """'3.11.9' → (3, 11)；不可解析返回 ()。"""
    parts = (text or "").strip().split(".")
    try:
        return tuple(int(p) for p in parts[:2])
    except (TypeError, ValueError):
        return ()


def python_version(py):
    """探测解释器版本字符串（不可执行/超时返回 None）。"""
    try:
        proc = subprocess.run(
            [py, "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    lines = (proc.stdout or "").strip().splitlines()
    return lines[-1].strip() if lines else None


def resolve_python(explicit=None):
    """定 Python：显式 --python > VERDICTS_PYTHON > 固定 Python311 > PATH python。"""
    candidates = []
    if explicit:
        candidates.append(explicit)
    else:
        env_py = os.environ.get("VERDICTS_PYTHON")
        if env_py:
            candidates.append(env_py)
        candidates.append(DEFAULT_PY311)
        which = shutil.which("python")
        if which:
            candidates.append(which)
    tried = []
    for cand in candidates:
        cand = os.path.expandvars(os.path.expanduser(cand))
        if not (os.path.isfile(cand) or shutil.which(cand)):
            tried.append("%s（不存在）" % cand)
            continue
        ver = python_version(cand)
        if not ver:
            tried.append("%s（无法执行）" % cand)
            continue
        tried.append("%s（%s）" % (cand, ver))
        if explicit or os.environ.get("VERDICTS_PYTHON"):
            return cand, ver, tried
        if version_tuple(ver) == (3, 11):
            return cand, ver, tried
    raise ConfigError("未找到可用 Python311；候选：%s" % "；".join(tried))


def build_command(py):
    """子进程命令行（纯函数，便于 --dry-run 显示/测试）。"""
    return [py, "-X", "utf8", TARGET]


def list_archives():
    """现有归档列表 [(basename, 信号日, size)]，便于分片规划。"""
    rows = []
    for fp in sorted(glob.glob(os.path.join(HIST_DIR, "todays_*.json"))):
        rows.append((os.path.basename(fp), archive_day(fp), os.path.getsize(fp)))
    return rows


def validate_output(out_real):
    """校验产物 JSON 结构并返回摘要（缺字段/坏 JSON 抛 ValueError）。"""
    with open(out_real, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ValueError("顶层不是 JSON 对象")
    summary = doc.get("summary")
    per_code = doc.get("per_code")
    if not isinstance(summary, dict) or not isinstance(per_code, dict):
        raise ValueError("缺 summary/per_code")
    windows = summary.get("windows") or {}
    if not isinstance(windows, dict) or not windows:
        raise ValueError("summary.windows 为空")
    return {
        "generated_at": doc.get("generated_at"),
        "asof": summary.get("asof"),
        "archives": summary.get("archives"),
        "codes": len(per_code),
        "windows": {
            k: {"n": (v or {}).get("n"), "hit": (v or {}).get("hit")}
            for k, v in windows.items()
        },
        "select": doc.get("select"),
    }


def kill_tree(pid):
    """终止子进程树（Windows taskkill /T /F；POSIX killpg）。"""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), 9)
        except OSError:
            pass


def backup_prod(out_real):
    """--allow-prod 前备份现有生产 JSON（返回备份路径，不存在则 None）。"""
    if not os.path.exists(out_real):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = out_real + ".bak-%s" % stamp
    shutil.copy2(out_real, bak)
    return bak


def _run_logged(cmd, env, out_real, timeout_sec):
    """拉起子进程，逐行转发输出 + 30s 心跳 + 超时/中断杀进程树。"""
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=(os.name != "nt"),
        )
    except OSError as ex:
        raise ConfigError("子进程启动失败：%s" % ex)
    _log("[safe] 子进程 pid=%d" % proc.pid)
    lines = queue.Queue()

    def _reader():
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    lines.put(line.rstrip("\r\n"))
        except Exception:  # noqa: BLE001
            pass
        finally:
            lines.put(None)

    threading.Thread(target=_reader, daemon=True, name="safe-reader").start()
    empty = object()
    started = time.time()
    last_hb = started
    timed_out = False
    interrupted = False
    try:
        while True:
            try:
                line = lines.get(timeout=1.0)
            except queue.Empty:
                line = empty
            if line is None:
                break
            if line is not empty and line:
                _log(line)
            now = time.time()
            if timeout_sec and (now - started) > timeout_sec:
                timed_out = True
                _log("[safe] TIMEOUT 超过 %ss，终止进程树 pid=%d" % (timeout_sec, proc.pid))
                kill_tree(proc.pid)
                break
            if now - last_hb >= HEARTBEAT_SEC:
                last_hb = now
                _log(
                    "[safe] 心跳：已运行 %.0fs（%s）"
                    % (now - started, ("超时 %ss" % timeout_sec) if timeout_sec else "不限")
                )
    except KeyboardInterrupt:
        interrupted = True
        _log("[safe] Ctrl-C，终止进程树 pid=%d" % proc.pid)
        kill_tree(proc.pid)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            fail_msg = "子进程未能在 20s 内退出 pid=%d" % proc.pid
            raise ConfigError(fail_msg)
    elapsed = time.time() - started
    rc = proc.returncode
    _log("[safe] 子进程退出码 %s | 耗时 %.1fs" % (rc, elapsed))
    if interrupted:
        return EXIT_INTERRUPT
    if timed_out:
        return EXIT_TIMEOUT
    return rc if rc is not None else 1


def build_parser():
    ap = argparse.ArgumentParser(
        description="build_pick_verdicts.py 安全运行器：固定 py311 + 超时 + 分片 + 进度 + 独立输出",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python portal/run_build_pick_verdicts_safe.py --max-archives 1 --timeout 1800\n"
            "  python portal/run_build_pick_verdicts_safe.py --archive-from 20261019 "
            "--archive-to 20261019\n"
            "  python portal/run_build_pick_verdicts_safe.py --list-archives\n"
            "生产写（人工确认后）：--allow-prod --out portal/data/pick_verdicts.json"
        ),
    )
    ap.add_argument("--python", default=None, help="Python 解释器（默认固定 Python311）")
    ap.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help="超时秒数（0=不限；默认 3600）"
    )
    ap.add_argument(
        "--max-archives", type=int, default=0, help="只算尾部 N 份归档（0=全量）"
    )
    ap.add_argument("--archive-from", default="", help="只算信号日 >= YYYYMMDD 的归档")
    ap.add_argument("--archive-to", default="", help="只算信号日 <= YYYYMMDD 的归档")
    ap.add_argument(
        "--progress-every", type=int, default=25, help="档内每 N 只票一行进度（0=关闭）"
    )
    ap.add_argument("--out", default="", help="输出 JSON（默认 C:\\Temp 下时间戳文件）")
    ap.add_argument(
        "--log", default="", help="日志路径（默认 C:\\Temp\\pick_verdicts_safe\\run_<ts>.log）"
    )
    ap.add_argument(
        "--allow-prod", action="store_true", help="允许写生产 pick_verdicts.json（先备份）"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="只打印将要执行的命令/环境，不执行"
    )
    ap.add_argument(
        "--list-archives", action="store_true", help="只列出归档（含当前筛选结果）后退出"
    )
    return ap


def _print_archives(args):
    files = sorted(glob.glob(os.path.join(HIST_DIR, "todays_*.json")))
    if not files:
        _log("[safe] 无归档：%s" % HIST_DIR)
        return 0
    selected = set(
        bpv.select_archives(files, args.max_archives, args.archive_from, args.archive_to)
    )
    _log("[safe] 归档 %d 份 | 当前筛选命中 %d 份（* 标记）" % (len(files), len(selected)))
    for fp in files:
        mark = "*" if fp in selected else " "
        _log("  %s %s %8d  %s" % (mark, archive_day(fp), os.path.getsize(fp),
                                  os.path.basename(fp)))
    return 0


def main(argv=None):
    global _LOG_FH
    args = build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")  # Windows 控制台编码兜底
    except Exception:  # noqa: BLE001
        pass
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base_dir = DEFAULT_BASE_DIR
    log_path = os.path.abspath(
        args.log or os.path.join(base_dir, "run_%s.log" % stamp)
    )
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        _LOG_FH = open(log_path, "w", encoding="utf-8")
    except OSError as ex:
        print("[safe] 日志不可写 %s：%s" % (log_path, ex), flush=True)
        return EXIT_CONFIG
    try:
        _log("[safe] P2-1 安全运行器 | 主机 %s | 日志 %s" % (os.environ.get("COMPUTERNAME", "?"), log_path))
        if args.list_archives:
            return _print_archives(args)
        if os.path.isfile(TARGET) is False:
            raise ConfigError("目标脚本不存在：%s" % TARGET)
        if args.timeout < 0:
            raise ConfigError("--timeout 不能为负（0=不限）")
        if args.max_archives < 0:
            raise ConfigError("--max-archives 不能为负")
        if args.progress_every < 0:
            raise ConfigError("--progress-every 不能为负")
        d_from = _norm_day(args.archive_from)
        d_to = _norm_day(args.archive_to)
        for name, raw, day in (
            ("--archive-from", args.archive_from, d_from),
            ("--archive-to", args.archive_to, d_to),
        ):
            if raw and len(day) != 8:
                raise ConfigError("%s 需 YYYYMMDD/YYYY-MM-DD：%r" % (name, raw))
        if d_from and d_to and d_from > d_to:
            raise ConfigError("--archive-from %s 晚于 --archive-to %s" % (d_from, d_to))
        out_path = os.path.abspath(
            args.out or os.path.join(base_dir, "pick_verdicts_%s.json" % stamp)
        )
        out_real = check_out_path(out_path, allow_prod=args.allow_prod)
        py, ver, tried = resolve_python(args.python)
        _log("[safe] Python: %s" % py)
        _log("[safe] 版本: %s%s" % (ver, "" if version_tuple(ver) == (3, 11) else "（WARN 非 3.11，仅建议诊断；生产固定 3.11）"))
        if version_tuple(ver) != (3, 11):
            _log("[safe] 候选链：%s" % "；".join(tried))
        env = make_child_env(
            os.environ,
            out_real,
            max_archives=args.max_archives,
            archive_from=d_from,
            archive_to=d_to,
            progress_every=args.progress_every,
        )
        cmd = build_command(py)
        _log("[safe] 命令: %s" % subprocess.list2cmdline(cmd))
        _log("[safe] 输出: %s" % out_real)
        _log(
            "[safe] 超时: %s | 分片: %s | 进度: 每 %s 票"
            % (
                ("%ss" % args.timeout) if args.timeout else "不限",
                _describe_select(args.max_archives, d_from, d_to),
                args.progress_every,
            )
        )
        if args.dry_run:
            _log("[safe] dry-run：不执行（环境变量见上）")
            return 0
        if os.path.normcase(out_real) in prod_out_paths():
            bak = backup_prod(out_real)
            if bak:
                _log("[safe] 生产写前备份: %s" % bak)
            else:
                _log("[safe] 生产写：现有文件不存在，首次生成")
        rc = _run_logged(cmd, env, out_real, args.timeout)
        if rc != 0:
            _log("[safe] FAIL rc=%s（详情见 %s）" % (rc, log_path))
            return rc
        try:
            info = validate_output(out_real)
        except (OSError, ValueError) as ex:
            _log("[safe] 产物校验失败：%s" % ex)
            return EXIT_VALIDATE
        _log(
            "[safe] OK 产物 generated_at=%s asof=%s archives=%s codes=%s"
            % (info["generated_at"], info["asof"], info["archives"], info["codes"])
        )
        for w in sorted(info["windows"], key=lambda x: int(x)):
            blk = info["windows"][w]
            _log("  execT+%s: n=%s 命中率=%s%%" % (w, blk.get("n"), blk.get("hit")))
        if info.get("select"):
            _log("[safe] 分片自证：%s" % json.dumps(info["select"], ensure_ascii=False))
        _log("[safe] 产物路径: %s" % out_real)
        return 0
    except ConfigError as ex:
        _log("[safe] 配置/预检失败：%s" % ex)
        return EXIT_CONFIG
    finally:
        try:
            _LOG_FH.close()
        except Exception:  # noqa: BLE001
            pass
        _LOG_FH = None


def _describe_select(max_archives, d_from, d_to):
    parts = []
    if max_archives:
        parts.append("尾部 %s 档" % max_archives)
    if d_from or d_to:
        parts.append("%s~%s" % (d_from or "最早", d_to or "最新"))
    return " / ".join(parts) if parts else "全量"


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(EXIT_INTERRUPT)
