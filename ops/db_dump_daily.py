# -*- coding: utf-8 -*-
"""db_dump_daily.py — Docker 数据库资产每日 dump 落宿主（K-STOCK-050 P0）

背景：全部核心库（菜鸟 MySQL / 3 套 PG / 2 套 Neo4j）数据都在 Docker Desktop
的 WSL2 虚拟磁盘（ext4.vhdx）命名卷里，宿主备份脚本盲区、vhdx 只增不减。
本脚本把 dump 落到 NTFS 宿主目录 backups/db/，纳入既有备份链。

安全设计：
- 零明文口令：dump 命令在容器内执行，直接引用容器 env（$MYSQL_ROOT_PASSWORD /
  $POSTGRES_USER / $POSTGRES_DB），宿主侧不出现任何凭据
- 长跑纪律：全部输出落日志文件（dump_latest.log），不用管道回传
- 删除纪律：14 天轮转删除走回收站（SHFileOperationW FOF_ALLOWUNDO），
  返回值不可信 → 删除后逐个 os.path.exists 复核
- 幂等：同日重跑覆盖当日目录

B4 修复（2026-11-05 首次季度恢复演练发现）：K-STOCK-414 的
`gzip.open(dst, "wb")` + `subprocess.run(cmd, stdout=f)` 在 Windows 上会让子进程
直接写未缓冲的底层 fd，GzipFile 头直到 close() 才落盘 → 产物是「明文 SQL + 尾部
空 gzip 头」，严格恢复（gzip -dc / ON_ERROR_STOP）报错。现改为
Popen(stdout=PIPE) + 后台线程流式 gzip.write，落盘先写 .part，校验 gzip 魔数/
可解压/非空后再原子替换；manifest 增加 sha256。

用法：python ops/db_dump_daily.py
      （无 CLI 参数：main() 不解析 sys.argv，轮转天数 KEEP_DAYS=14 是源码常量；
       旧 docstring 写过的 --keep-days 从未实现，传了会被静默忽略）
退出码：0=全部成功；1=部分失败；2=全部失败（供计划任务/automation 判 rc）

排班现状（2026-11-05 只读复核，勿按"每日"字面理解）：全库 492 个计划任务
**没有任何一个**引用本脚本；backups/db 的 10-09~10-15 是换机前旧机的日排班，
10-29 / 11-05 两次为人工执行（11-05 10:08 必保 3/3 rc=0）。
即脚本可用但**当前无自动排班**，恢复日排班需 L2 五要素批准。

排班前必读（轮转风险）：KEEP_DAYS=14 会让首次运行删掉 14 天前的
backups/db 目录（走回收站、可逆，但 10-09~10-15 / 10-29 这批换机前后
的留痕会被移走）。11-05 人工那次日志写的是「无超过 60 天的旧目录」，
与本文件常量不一致 → **轮转口径未收敛**，批排班时须一并裁定天数。
"""
import ctypes
import datetime
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_BASE = os.path.join(ROOT, "backups", "db")
LOG_PATH = os.path.join(OUT_BASE, "dump_latest.log")
KEEP_DAYS = 14

GZIP_MAGIC = b"\x1f\x8b"
CHUNK = 1024 * 1024
DUMP_RUNNER = ["docker", "exec"]

# 容器内执行的 dump 命令（引用容器 env，零明文）。
# K-STOCK-414：原 `... 2>/dev/null | gzip` 管道形态下 rc 是 gzip 的，
# dump 半途失败仍判 OK，且 stderr 被吞；且 postgres/neo4j 容器 sh=dash
# 不支持 pipefail。改为容器内纯 dump、宿主侧 gzip 收流（见 dump_one）。
DUMPS = [
    {"key": "cainiao_mysql", "container": "quant-cainiao-mysql",  # 2026-10-29 修正：旧名 cainiao-mysql-local 已不存在
     "cmd": 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction '
            '--all-databases --routines --triggers',
     "file": "cainiao_mysql_alldb.sql.gz", "must": True},
    {"key": "zhice_pg", "container": "zhice-db",
     "cmd": 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner',
     "file": "zhice_os_v2.sql.gz", "must": True},
    # 2026-10-29 修正：quantdinger-db / newquantsystem_v2-postgres-1 两个旧容器已不存在，
    # 现网 PG 为 quant-pg（承载 quant_meta 等库）→ 用 pg_dumpall 一次性覆盖全部库。
    {"key": "quant_pg_alldb", "container": "quant-pg",
     "cmd": 'pg_dumpall -U "$POSTGRES_USER"',
     "file": "quant_pg_alldb.sql.gz", "must": True},
    # Neo4j community 在线 dump 不可用（需停库）——先尝试，失败记 PENDING 不算硬失败
    {"key": "kg_neo4j", "container": "neo4j-kg",
     "cmd": 'neo4j-admin database dump neo4j --to-stdout',
     "file": "kg_neo4j.dump.gz", "must": False},
    {"key": "zerobound_neo4j", "container": "zerobound-neo4j-independent",
     "cmd": 'neo4j-admin database dump neo4j --to-stdout',
     "file": "zerobound_neo4j.dump.gz", "must": False},
]

LOG_FP = None


def log(msg):
    line = f"[{datetime.datetime.now():%F %T}] {msg}"
    print(line, flush=True)
    if LOG_FP:
        LOG_FP.write(line + "\n")
        LOG_FP.flush()


def _dump_command(item):
    """构造 docker exec 命令（测试可 monkeypatch 此处避开真实 docker）。"""
    return DUMP_RUNNER + [item["container"], "sh", "-c", item["cmd"]]


def _pump_stdout(proc, gz, digest, state):
    """后台线程：把子进程 stdout 流式写入 gzip（避免 Windows fd 直写回归）。"""
    try:
        while True:
            chunk = proc.stdout.read(CHUNK)
            if not chunk:
                break
            gz.write(chunk)
            digest.update(chunk)
            state["bytes"] += len(chunk)
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass


def _gzip_payload(cmd, dst, timeout=900):
    """执行 dump 命令并流式压缩写 dst。

    返回 (rc, written_bytes, sha256hex, stderr_bytes)；超时 rc=-1。
    stderr 落临时文件而非 PIPE，避免 8GB 级 dump 时管道写满死锁。
    """
    digest = hashlib.sha256()
    state = {"bytes": 0}
    stderr = b""
    timed_out = False
    with tempfile.TemporaryFile() as errf:
        with gzip.open(dst, "wb") as gz:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf)
            pump = threading.Thread(target=_pump_stdout,
                                    args=(proc, gz, digest, state), daemon=True)
            pump.start()
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                rc = proc.wait()
            pump.join()
        errf.seek(0)
        stderr = errf.read()
    if timed_out:
        rc = -1
    return rc, state["bytes"], digest.hexdigest(), stderr


def _validate_gzip(path):
    """校验产物：gzip 魔数 + 可解压首块 + 非空。返回 (ok, reason)。"""
    if not os.path.exists(path):
        return False, "missing"
    size = os.path.getsize(path)
    if size < 32:  # 空 gzip 流约 21B；真实 dump 远大于此
        return False, f"too_small({size}B)"
    try:
        with open(path, "rb") as f:
            if f.read(2) != GZIP_MAGIC:
                return False, "bad_magic"
    except OSError as e:  # noqa: BLE001
        return False, f"read_error:{e}"
    try:
        with gzip.open(path, "rb") as gz:
            first = gz.read(64)
        if not first:
            return False, "empty_payload"
    except (OSError, EOFError) as e:  # noqa: BLE001
        return False, f"gzip_read_error:{e}"
    return True, "ok"


def dump_one(item, out_dir, timeout=900):
    """单库 dump：容器内纯 dump → 宿主侧流式 gzip → 校验 → 原子替换。

    返回 (ok, compressed_size, sha256hex)。失败不留 .part/半成品。
    """
    dst = os.path.join(out_dir, item["file"])
    tmp = dst + ".part"
    t0 = time.time()
    cmd = _dump_command(item)
    try:
        rc, written, sha, stderr = _gzip_payload(cmd, tmp, timeout=timeout)
        valid, reason = _validate_gzip(tmp)
        size = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        ok = valid and rc == 0 and written > 0
        if not ok:
            if os.path.exists(tmp):
                os.remove(tmp)
            log(f"FAIL {item['key']} rc={rc} raw={written:,}B reason={reason} "
                f"用时{time.time()-t0:.0f}s")
            if stderr:
                log(f"     stderr: {stderr.decode(errors='replace')[:200]}")
            return False, 0, ""
        os.replace(tmp, dst)
        log(f"OK  {item['key']} rc=0 raw={written:,}B gz={size:,}B "
            f"sha256={sha[:16]} 用时{time.time()-t0:.0f}s")
        return True, size, sha
    except Exception as e:  # noqa: BLE001
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        log(f"FAIL {item['key']} 异常: {e}")
        return False, 0, ""


def recycle_delete(paths):
    """删除走回收站（可逆）；返回值不可信 → exists 逐个复核。

    K-STOCK-399：修复历史坏占位——原首行 ctypes.c_struct() 属性不存在，
    轮转触发即 AttributeError 崩溃，14 天轮转从未真正删过文件。
    """
    leftover = []
    # 双 NUL 结尾的多路径列表
    buf = "\0".join(paths) + "\0\0"
    class SHFILEOPSTRUCT(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint),
                    ("pFrom", ctypes.c_wchar_p), ("pTo", ctypes.c_wchar_p),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]
    fo = SHFILEOPSTRUCT()
    fo.hwnd = None
    fo.wFunc = 3  # FO_DELETE
    fo.pFrom = buf
    fo.pTo = None
    fo.fFlags = 0x40 | 0x10 | 0x4  # FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    ctypes.windll.shell32.SHFileOperationW(ctypes.byref(fo))
    time.sleep(0.5)
    for p in paths:
        if os.path.exists(p):
            leftover.append(p)
    return leftover


def rotate(keep_days):
    """14 天轮转：删除走回收站，复核失败则保留并告警（宁多留不误删）。"""
    cut = (datetime.date.today() - datetime.timedelta(days=keep_days)).isoformat()
    stale = []
    for name in os.listdir(OUT_BASE):
        full = os.path.join(OUT_BASE, name)
        if os.path.isdir(full) and len(name) == 10 and name < cut and name[:4] == "20":
            stale.append(full)
    if not stale:
        log(f"轮转：无超过 {keep_days} 天的旧目录")
        return
    leftover = recycle_delete(stale)
    log(f"轮转：回收 {len(stale)-len(leftover)}/{len(stale)} 个旧目录（≤{cut}）")
    for p in leftover:
        log(f"警告：回收站删除失败，已保留 {p}")


def main():
    global LOG_FP
    os.makedirs(OUT_BASE, exist_ok=True)
    LOG_FP = open(LOG_PATH, "a", encoding="utf-8")
    day = datetime.date.today().isoformat()
    out_dir = os.path.join(OUT_BASE, day)
    os.makedirs(out_dir, exist_ok=True)
    log(f"=== K-STOCK-050 DB dump 开始 → {out_dir} ===")

    manifest = {"date": day, "items": []}
    n_ok = n_must_fail = 0
    for item in DUMPS:
        ok, size, sha = dump_one(item, out_dir)
        manifest["items"].append({"key": item["key"], "ok": ok, "size": size,
                                  "sha256": sha, "file": item["file"],
                                  "must": item["must"]})
        n_ok += ok
        if not ok and item["must"]:
            n_must_fail += 1
        # 失败文件清掉避免空壳混入备份链
        if not ok and os.path.exists(os.path.join(out_dir, item["file"])):
            os.remove(os.path.join(out_dir, item["file"]))

    manifest["ok_count"] = n_ok
    manifest["must_fail"] = n_must_fail
    manifest["total"] = len(DUMPS)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    rotate(KEEP_DAYS)
    log(f"=== 结束：成功 {n_ok}/{len(DUMPS)}（必保失败 {n_must_fail}）rc={2 if n_ok==0 else (1 if n_must_fail else 0)} ===")
    LOG_FP.close()
    sys.exit(2 if n_ok == 0 else (1 if n_must_fail else 0))


if __name__ == "__main__":
    main()
