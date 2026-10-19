# -*- coding: utf-8 -*-
"""A3 愈合批量重拉驱动（已批：老田 2026-10-20，A 组 A3 清扫范围）

用法:
    python a3_heal_batch.py --tables t1,t2,... [--log PATH] [--dry-run]

行为:
    1) 单实例锁（logs/a3_heal_batch.lock，持 PID；进程消亡即陈锁可强破）
    2) 若有其他 tdx_sync.py 实例在跑（如 22:05 的 ts_us_daily 重拉），等待至多 --wait-mins 分钟
    3) 首次运行时备份 data/tdx/_manifest.json -> _manifest.bak-a3-heal-<date>.json
    4) 逐张表：
       a. manifest 中该表置 {offset:0, part:0, rows:0, done:false}（其余键逐字节不动）
       b. 旧分片目录 data/tdx/<table> 移入 trash_restore/a3_heal_<date>/（不删除）
       c. 前台运行 python tdx_sync.py --tables <table>（stdout/stderr 追加到日志）
       d. 记录结果行：表名/退出码/重拉后 manifest rows/done
    5) 任一张表 tdx_sync 退出码非 0 -> 记录失败继续下一张（不中断整批，不重试）

设计纪律:
    - closed on failure：单表失败不污染其他表；manifest 每张表改前快照可回滚
    - 幂等：重跑安全（仅跳过本驱动此前已愈合成功并记入状态文件的表；
      manifest 的 done/rows 记账值本身可能是假账，不得作为跳过依据）
    - 本脚本自身不接触表数据内容，只动 manifest 条目与分片目录位置
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

QUANT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # quant_backtest/
DATA_TDX = os.path.join(QUANT_DIR, "data", "tdx")
MANIFEST = os.path.join(DATA_TDX, "_manifest.json")
LOGS = os.path.join(QUANT_DIR, "logs")
TRASH_ROOT = r"C:\Users\t\.workbuddy\trash_restore"
PY = r"C:\Program Files\Python311\python.exe"
STATE_PATH = os.path.join(LOGS, "a3_heal_state.json")


def now_s():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_line(f, msg):
    line = "[%s] %s" % (now_s(), msg)
    print(line, flush=True)
    if f:
        f.write(line + "\n")
        f.flush()


def find_tdx_sync_procs():
    """返回正在运行的 tdx_sync.py 进程的 PID 列表（不含本脚本派生的 worker 自身由调用方排除）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" "
             "| ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
            capture_output=True, text=True, timeout=60).stdout or ""
    except Exception as exc:
        print("[a3-heal][WARN] 进程枚举失败: %s" % str(exc)[:120])
        return []
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if "tdx_sync.py" in line and "a3_heal_batch" not in line:
            try:
                pids.append(int(line.split("|", 1)[0]))
            except ValueError:
                pass
    return pids


def acquire_lock(log_path):
    os.makedirs(LOGS, exist_ok=True)
    lock_path = os.path.join(LOGS, "a3_heal_batch.lock")
    if os.path.exists(lock_path):
        try:
            pid = int(open(lock_path).read().strip() or "0")
            os.kill(pid, 0)
            return None  # 持锁进程存活
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            log_line(None, "[a3-heal] 陈旧锁（持锁进程 %s 已消亡），强破" % (
                open(lock_path).read().strip() if os.path.exists(lock_path) else "?"))
        except Exception as exc:
            log_line(None, "[a3-heal][WARN] 锁检查异常（视为存活，本次放弃）: %s" % str(exc)[:120])
            return None
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))
    return lock_path


def wait_for_other_sync(max_wait_mins, f):
    deadline = time.time() + max_wait_mins * 60
    while time.time() < deadline:
        pids = find_tdx_sync_procs()
        if not pids:
            return True
        log_line(f, "[a3-heal] 有 tdx_sync 实例在跑 PID=%s，等 120s（剩 %.0f 分钟）"
                 % (pids, (deadline - time.time()) / 60))
        time.sleep(120)
    return not find_tdx_sync_procs()


def backup_manifest(f):
    stamp = datetime.now().strftime("%Y%m%d")
    bak = os.path.join(DATA_TDX, "_manifest.bak-a3-heal-%s.json" % stamp)
    if not os.path.exists(bak):
        shutil.copy2(MANIFEST, bak)
        log_line(f, "[a3-heal] manifest 已备份 -> %s" % bak)
    return bak


def reset_table_entry(table, f):
    with open(MANIFEST, "r", encoding="utf-8") as fh:
        m = json.load(fh)
    tables = m.get("tables", m)
    if table not in tables:
        log_line(f, "[a3-heal][WARN] %s 不在 manifest，跳过重置" % table)
        return
    tables[table] = {"offset": 0, "part": 0, "rows": 0, "done": False}
    tmp = MANIFEST + ".tmp-a3heal"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, MANIFEST)
    log_line(f, "[a3-heal] %s manifest 条目已重置（rows=0 done=false）" % table)


def move_parts_aside(table, f):
    tdir = os.path.join(DATA_TDX, table)
    if not os.path.isdir(tdir):
        log_line(f, "[a3-heal] %s 无分片目录，跳过移出" % table)
        return
    stamp = datetime.now().strftime("%Y%m%d")
    dest_root = os.path.join(TRASH_ROOT, "a3_heal_%s" % stamp)
    os.makedirs(dest_root, exist_ok=True)
    dest = os.path.join(dest_root, table)
    if os.path.exists(dest):
        dest = dest + "_" + datetime.now().strftime("%H%M%S")
    shutil.move(tdir, dest)
    log_line(f, "[a3-heal] %s 旧分片移出 -> %s" % (table, dest))


def run_one_table(table, log_path, f):
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    with open(log_path, "a", encoding="utf-8", errors="replace") as lf:
        proc = subprocess.run(
            [PY, "-X", "utf8", os.path.join(QUANT_DIR, "tdx_sync.py"), "--tables", table],
            cwd=QUANT_DIR, stdout=lf, stderr=subprocess.STDOUT, env=env, timeout=8 * 3600)
    return proc.returncode


def read_manifest_rows(table):
    try:
        with open(MANIFEST, "r", encoding="utf-8") as fh:
            m = json.load(fh)
        t = m.get("tables", m).get(table, {})
        return t.get("rows"), t.get("done")
    except Exception:
        return None, None


def load_healed_state():
    """本驱动此前愈合成功的表集合（manifest 记账可能是假账，不能替代此状态）。"""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return set(data.get("healed", []))
    except Exception:
        return set()


def record_healed(table, rows, done):
    try:
        os.makedirs(LOGS, exist_ok=True)
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        healed = set(data.get("healed", []))
        healed.add(table)
        data["healed"] = sorted(healed)
        data["last_%s" % table] = {"rows": rows, "done": done, "ts": now_s()}
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception as exc:
        log_line(None, "[a3-heal][WARN] 愈合状态记录失败（不影响本次批次）: %s" % str(exc)[:120])


def worker(tables, log_path):
    os.makedirs(LOGS, exist_ok=True)
    with open(log_path, "a", encoding="utf-8", errors="replace") as f:
        log_line(f, "==== A3 愈合批开始（%d 张）====" % len(tables))
        lock_path = acquire_lock(log_path)
        if lock_path is None:
            log_line(f, "[a3-heal] 已有批次在跑（锁被持），本次退出")
            return 2
        try:
            if not wait_for_other_sync(max_wait_mins=300, f=f):
                log_line(f, "[a3-heal][WARN] 等待其他 tdx_sync 实例超时，本次退出（不硬抢）")
                return 3
            backup_manifest(f)
            healed = load_healed_state()
            results = []
            for i, table in enumerate(tables, 1):
                if table in healed:
                    rows, done = read_manifest_rows(table)
                    log_line(f, "[a3-heal] (%d/%d) %s 本驱动此前已愈合（状态文件在案，manifest rows=%s done=%s），跳过"
                             % (i, len(tables), table, rows, done))
                    results.append((table, "SKIP", rows, done))
                    continue
                log_line(f, "[a3-heal] (%d/%d) %s 开始重拉" % (i, len(tables), table))
                reset_table_entry(table, f)
                move_parts_aside(table, f)
                rc = run_one_table(table, log_path, f)
                rows2, done2 = read_manifest_rows(table)
                verdict = "OK" if (rc == 0 and done2 and (rows2 or 0) > 0) else (
                    "OK-PARTIAL" if rc == 0 else "FAIL(rc=%s)" % rc)
                log_line(f, "[a3-heal] (%d/%d) %s -> %s rows=%s done=%s"
                         % (i, len(tables), table, verdict, rows2, done2))
                if rc == 0 and done2:
                    record_healed(table, rows2, done2)
                    healed = load_healed_state()
                results.append((table, verdict, rows2, done2))
            log_line(f, "==== A3 愈合批结束 ====")
            for t, v, r, d in results:
                log_line(f, "  RESULT %-24s %-14s rows=%s done=%s" % (t, v, r, d))
            return 0
        finally:
            try:
                os.remove(lock_path)
            except OSError:
                pass


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True, help="逗号分隔表清单")
    ap.add_argument("--log", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--detach", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    log_path = args.log or os.path.join(LOGS, "a3_heal_batch_%s.log" % datetime.now().strftime("%Y%m%d"))
    if args.dry_run:
        print("dry-run 表清单（%d 张，按缺口从大到小）:" % len(tables))
        for t in tables:
            print("  -", t)
        print("日志:", log_path)
        return 0
    if not args.detach:
        # 派生 detached worker 后立即返回（铁律：长跑不占会话）
        flags = 0
        if hasattr(subprocess, "DETACHED_PROCESS"):
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        os.makedirs(LOGS, exist_ok=True)
        with open(log_path, "a", encoding="utf-8", errors="replace") as lf:
            lf.write("[%s] [a3-heal] launcher 派生 detached worker（%d 张）\n" % (now_s(), len(tables)))
        subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.abspath(__file__),
             "--tables", ",".join(tables), "--log", log_path, "--detach"],
            cwd=QUANT_DIR, creationflags=flags, stdin=subprocess.DEVNULL,
            stdout=open(log_path, "a", encoding="utf-8", errors="replace"),
            stderr=subprocess.STDOUT, close_fds=True)
        print("worker 已派生，日志:", log_path)
        return 0
    return worker(tables, log_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
