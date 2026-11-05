# -*- coding: utf-8 -*-
r"""B4 备份恢复演练（季度）—— 把 2026-11-05 那次人工演练固化成可调度脚本。

为什么要有这个脚本
    2026-11-05 的首次季度演练是拿 C:\Temp\b4_*.py 一堆临时脚本拼出来的：
    结论行进了 kg_sync_task.log，但**脚本本身没固化**，C:\Temp 一清就等于没演练过。
    演练的价值全在"每个月都跑一次"，跑不了第二次的演练只是一次性表演。

它查什么（对应 2026-11-05 回执里的两个 P1）
    P1-a 备份压缩回归：dump 头必须是 gzip 魔数 1f8b，且能被 gzip 流式解开。
        2026-10-29 那批是 `gzip.open(dst,"wb")` + `subprocess.run(stdout=f)` 的产物——
        Windows 下子进程直接写原 fd，明文数据先落盘、空 gzip 头在 close() 时才追加到尾部，
        文件头是 `2d 2d`（"--"）。旧演练又把这个 .gz 直接喂 `psql -f`，于是报
        `invalid byte sequence for encoding "UTF8": 0x8b`。本脚本两处都做对：
        先验魔数，再在宿主侧解压成流喂 psql stdin（不像旧演练那样把 .gz 当 SQL 读）。
    P1-b 备份早已过期：backups\db 最新目录超过 --max-age-days 直接判 P1。
        没有这条，dump 调度停了半年也不会有人知道，因为"演练成功"看的是老文件。

怎么跑
    python -X utf8 b4_restore_drill.py                 # 演练最新一份
    python -X utf8 b4_restore_drill.py --date 2026-11-05
    python -X utf8 b4_restore_drill.py --json
    python -X utf8 b4_restore_drill.py --keep-container # 排障时保留容器

退出码
    0 全部通过 / 1 有 finding（P1/P2，结论行 rc=1，会进 kg_log_aux_warnings）/ 2 环境不可用
"""

import argparse
import datetime
import gzip
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKUP_ROOT = Path(r"C:\项目\老田的AI助手\backups\db")
KG_LOG = Path(r"C:\Temp\kg_sync_task.log")
TASK_LOG = Path(r"C:\Temp\b4_restore_drill_task.log")
SNAP_DIR = Path(r"C:\Temp\b4_drill")
IMAGE = "postgres:15"
DB = "quant_meta"
TABLE = "news.ai_reviews"
MAX_AGE_DAYS = 8


def say(msg):
    print(msg, flush=True)


DOCKER_CANDIDATES = [
    os.environ.get("DOCKER_EXE"),
    r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
    r"C:\ProgramData\DockerDesktop\version-bin\docker.exe",
]


def find_docker():
    """计划任务里的 PATH 不一定是用户交互 shell 的那份，别指望 `docker` 能被解析到。"""
    for c in DOCKER_CANDIDATES:
        if c and os.path.isfile(c):
            return c
    return shutil.which("docker")


DOCKER = find_docker()


def docker(*args, **kw):
    if not DOCKER:
        class _R:
            returncode = 127
            stdout = ""
            stderr = "docker 不可用"
        return _R()
    return subprocess.run([DOCKER, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=kw.pop("timeout", 300), **kw)


def list_backups():
    """返回 [(date_str, sql_gz_path)]，按日期倒序。"""
    out = []
    if not BACKUP_ROOT.is_dir():
        return out
    for d in BACKUP_ROOT.iterdir():
        if not d.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name):
            continue
        p = d / "quant_pg_alldb.sql.gz"
        if p.is_file():
            out.append((d.name, p))
    return sorted(out, key=lambda x: x[0], reverse=True)


def gzip_magic(path):
    with open(path, "rb") as f:
        return f.read(2)


def gzip_stream_ok(path, probe=8 << 20):
    """真解压前 8MB，确认不是"头是 gzip、内容坏掉"。"""
    try:
        with gzip.open(path, "rb") as g:
            g.read(probe)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def count_copy_rows(sql_gz):
    """数备份里 COPY <TABLE> 段的行数；找不到返回 None。"""
    term = "\\."
    total = 0
    started = False
    try:
        with gzip.open(sql_gz, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not started:
                    if line.startswith("COPY %s " % TABLE):
                        started = True
                    continue
                if line.rstrip("\r\n") == term:
                    return total
                total += 1
    except Exception:  # noqa: BLE001
        return None
    return total if started else None


def wait_ready(name, timeout=90):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = docker("exec", name, "pg_isready", "-U", "postgres", timeout=20)
        if r.returncode == 0 and "accepting connections" in (r.stdout or ""):
            return True
        time.sleep(1.0)
    return False


def restore(sql_gz, name):
    """宿主侧解压 → psql stdin。ON_ERROR_STOP=1，任何一条错都要暴露。"""
    cmd = ["docker", "exec", "-i", name, "psql", "-U", "postgres", "-d", "postgres",
           "-v", "ON_ERROR_STOP=1", "-q"]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    try:
        with gzip.open(sql_gz, "rb") as g:
            shutil.copyfileobj(g, p.stdin, 1 << 20)
        p.stdin.close()
    except Exception as exc:  # noqa: BLE001
        try:
            p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        p.kill()
        return 1, "", "解压/写入失败: %s" % str(exc)[:200]
    out = (p.stdout.read() or b"").decode("utf-8", "replace")
    err = (p.stderr.read() or b"").decode("utf-8", "replace")
    return p.wait(), out, err


def psql_scalar(name, sql):
    r = docker("exec", name, "psql", "-U", "postgres", "-d", DB, "-tAc", sql, timeout=600)
    return (r.stdout or "").strip(), (r.stderr or "").strip()


def snapshot(name, dst):
    """pg_dump 自定义格式快照，留作证据（可 pg_restore -l 校验）。"""
    with open(dst, "wb") as f:
        p = subprocess.run(
            ["docker", "exec", name, "pg_dump", "-U", "postgres", "-d", DB, "-Fc"],
            stdout=f, stderr=subprocess.PIPE, timeout=1800)
    return p.returncode == 0, (p.stderr or b"").decode("utf-8", "replace")[:200]


def sha256_of(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main():
    global TABLE
    ap = argparse.ArgumentParser(description="B4 备份恢复演练")
    ap.add_argument("--date", help="备份日期 YYYY-MM-DD，缺省取最新")
    ap.add_argument("--table", default=TABLE)
    ap.add_argument("--max-age-days", type=int, default=MAX_AGE_DAYS)
    ap.add_argument("--keep-container", action="store_true")
    ap.add_argument("--no-snapshot", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    TABLE = args.table

    res = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "backup": "", "rows_backup": None, "rows_restored": None,
           "md5": "", "snapshot": "", "findings": [], "rc": 1}

    if not DOCKER:
        res["findings"].append("P0-docker-missing")
        return finish(res, args, 2)

    backups = list_backups()
    if not backups:
        res["findings"].append("P0-no-backup-dir")
        return finish(res, args, 2)

    if args.date:
        match = [b for b in backups if b[0] == args.date]
        if not match:
            res["findings"].append("P0-backup-not-found:%s" % args.date)
            return finish(res, args, 2)
        date_str, sql_gz = match[0]
    else:
        date_str, sql_gz = backups[0]

    res["backup"] = "%s/quant_pg_alldb.sql.gz" % date_str
    say("[1/6] 备份=%s size=%.1fMB" % (res["backup"], sql_gz.stat().st_size / 1048576))

    # 时效：最新备份太旧 = dump 调度停了（P1-b 的自动化版）
    try:
        bdate = datetime.date.fromisoformat(date_str)
        age = (datetime.date.today() - bdate).days
        res["age_days"] = age
        if age > args.max_age_days:
            res["findings"].append("P1-stale-backup:%dd" % age)
    except ValueError:
        pass

    # P1-a：魔数 + 真的能解压
    magic = gzip_magic(sql_gz)
    res["magic"] = magic.hex()
    if magic != b"\x1f\x8b":
        res["findings"].append("P1-gzip-regression:magic=%s" % magic.hex())
        return finish(res, args, 1)
    ok, why = gzip_stream_ok(sql_gz)
    if not ok:
        res["findings"].append("P1-gzip-stream-broken")
        res["gzip_err"] = why
        return finish(res, args, 1)
    res["rows_backup"] = count_copy_rows(sql_gz)
    say("[2/6] gzip 魔数/解压 OK，备份侧 %s 行=%s" % (TABLE, res["rows_backup"]))

    name = "drill-b4-%d-%04d" % (os.getpid(), random.randint(0, 9999))
    docker("rm", "-f", name, timeout=60)
    r = docker("run", "-d", "--name", name, "-e", "POSTGRES_PASSWORD=drill",
               "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
               "-v", "%s:/backup:ro" % sql_gz.parent, IMAGE, timeout=300)
    if r.returncode != 0:
        res["findings"].append("P0-container-start-failed")
        res["docker_err"] = (r.stderr or "").strip()[:200]
        return finish(res, args, 2)
    say("[3/6] 演练容器 %s 已起" % name)

    try:
        if not wait_ready(name):
            res["findings"].append("P0-container-not-ready")
            return finish(res, args, 2)
        say("[4/6] 恢复中（宿主侧解压 → psql stdin）…")
        rc, out, err = restore(sql_gz, name)
        res["restore_rc"] = rc
        if rc != 0:
            res["findings"].append("P1-restore-failed:rc=%d" % rc)
            res["restore_err"] = (err or out or "").strip()[:400]
            return finish(res, args, 1)
        say("[5/6] 恢复完成，校验行数与 md5…")
        cnt, cerr = psql_scalar(name, "select count(*) from %s" % TABLE)
        if cerr:
            res["findings"].append("P1-table-missing")
            res["psql_err"] = cerr[:300]
            return finish(res, args, 1)
        res["rows_restored"] = int(cnt) if cnt.isdigit() else None
        md5, _ = psql_scalar(
            name, "select md5(string_agg(t::text, E'\\n' order by t::text)) from %s t" % TABLE)
        res["md5"] = md5
        if res["rows_backup"] is not None and res["rows_restored"] != res["rows_backup"]:
            res["findings"].append(
                "P1-rowcount-mismatch:%s!=%s" % (res["rows_backup"], res["rows_restored"]))

        if not args.no_snapshot:
            SNAP_DIR.mkdir(parents=True, exist_ok=True)
            dst = SNAP_DIR / ("%s_%s.dump" % (DB, date_str.replace("-", "")))
            ok, serr = snapshot(name, dst)
            if ok:
                res["snapshot"] = str(dst)
                res["snapshot_sha256"] = sha256_of(dst)
                res["snapshot_size"] = dst.stat().st_size
            else:
                res["findings"].append("P2-snapshot-failed")
                res["snapshot_err"] = serr
    finally:
        if args.keep_container:
            say("      容器保留：%s" % name)
            res["container"] = name
        else:
            docker("rm", "-f", name, timeout=120)

    say("[6/6] 行数 备份=%s 恢复=%s md5=%s" %
        (res["rows_backup"], res["rows_restored"], res["md5"]))
    return finish(res, args, 1 if res["findings"] else 0)


def finish(res, args, rc):
    res["rc"] = rc
    findings = ",".join(res["findings"]) if res["findings"] else "none"
    line = ("b4_restore_drill date=%s backup=%s db=%s table=%s rows=%s md5=%s snapshot=%s "
            "findings=%s rc=%d" % (
                res["ts"], res["backup"] or "-", DB, TABLE,
                "%s/%s" % (res["rows_backup"], res["rows_restored"]),
                res["md5"] or "-", res["snapshot"] or "-", findings, rc))
    try:
        with KG_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:  # noqa: BLE001
        say("结论行写入失败: %s" % exc)
    # 计划任务的 Exec 不解析 shell 重定向（`>>` 会被当参数喂给 python），
    # 所以运行日志由脚本自己落盘，别指望在参数里写 ">> xxx.log"。
    try:
        with TASK_LOG.open("a", encoding="utf-8") as f:
            f.write("[%s] rc=%d findings=%s\n" % (res["ts"], rc, findings))
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        say("任务日志写入失败: %s" % exc)
    if args.json:
        say(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        say("结论: rc=%d findings=%s" % (rc, findings))
    return rc


if __name__ == "__main__":
    sys.exit(main())
