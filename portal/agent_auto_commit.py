# -*- coding: utf-8 -*-
"""
agent_auto_commit.py — 各 Agent 代码定时强制入库（portal 统揽板块运维）
========================================================================
用途：monorepo（E:/老田的AI助手）下多个 Agent（scanner/integration/dashboard/news 等）
并行开发，本脚本由定时自动化周期调用，把工作区所有改动强制快照入库，
保证任一 Agent 的成果不因后续操作丢失（"强制"= 不挑改动，add -A 全收，
.gitignore 挡运行时产物）。

行为：
  1. git add -A
  2. 膨胀守卫：新增文件数/总字节/单文件超阈值 → 拒绝本次入库（unstage，
     工作区文件不动），状态记 BLOAT_GUARD，防止运行时产物击穿 .gitignore
  3. 无暂存改动 → 记"空跑"状态，退出 0
  4. 有改动 → git commit -m "auto: 各Agent代码定时入库快照 ..."（遇 index.lock 重试 3 次）
  5. 状态落 portal/logs/agent_commit_status.json（供自动化读取汇报），日志追加 portal/logs/auto_commit.log

退出码：0 = 成功/空跑/守卫拦截；2 = 提交失败（重试耗尽）
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(r"E:/老田的AI助手")
LOG_DIR = REPO / "portal" / "logs"
STATUS_FILE = LOG_DIR / "agent_commit_status.json"
LOG_FILE = LOG_DIR / "auto_commit.log"
LOCK_WAIT_RETRY, LOCK_WAIT_SEC = 3, 10

# ---- 膨胀守卫阈值（2026-10-08）：防大文件/大批量新文件击穿快照库 ----
BLOAT_MAX_NEW_FILES = 300   # 单次提交新增文件数上限
BLOAT_MAX_TOTAL_MB = 50     # 单次提交新增文件总字节上限
BLOAT_MAX_SINGLE_MB = 20    # 单个新增文件体积上限


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def commit_with_retry(msg: str) -> subprocess.CompletedProcess:
    last = None
    for i in range(LOCK_WAIT_RETRY):
        r = git("commit", "-m", msg, check=False)
        if r.returncode == 0:
            return r
        last = r
        err = (r.stderr or "")
        if "index.lock" in err:
            time.sleep(LOCK_WAIT_SEC)  # 另一 Agent 的 git 进程持锁，等待重试
            continue
        break
    return last


def staged_new_files() -> list:
    out = git("diff", "--cached", "--name-status").stdout
    return [line.split("\t")[-1] for line in out.splitlines() if line.startswith("A\t")]


def bloat_reason() -> str:
    """新增文件超过任一阈值则返回拦截原因，否则返回空串。"""
    new_files = staged_new_files()
    if not new_files:
        return ""
    sizes, total = [], 0
    for f in new_files:
        try:
            sz = (REPO / f).stat().st_size
        except OSError:
            sz = 0
        total += sz
        sizes.append((sz, f))
    sizes.sort(reverse=True)
    top = "; ".join(f"{f}({s / 1048576:.1f}MB)" for s, f in sizes[:5])
    if (len(new_files) > BLOAT_MAX_NEW_FILES
            or total > BLOAT_MAX_TOTAL_MB * 1048576
            or sizes[0][0] > BLOAT_MAX_SINGLE_MB * 1048576):
        return (f"新增文件数={len(new_files)} 总量={total / 1048576:.1f}MB "
                f"超阈值(>{BLOAT_MAX_NEW_FILES}个 或 总量>{BLOAT_MAX_TOTAL_MB}MB "
                f"或 单文件>{BLOAT_MAX_SINGLE_MB}MB) top5: {top}")
    return ""


def main() -> int:
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    status = {"time": ts, "ok": False, "committed": False, "detail": ""}

    try:
        # 忽略 autocrlf 状态差异：快照提交只关心"有无实质改动"
        st = git("status", "--porcelain").stdout
        mod = sum(1 for l in st.splitlines() if l.startswith(("M", " M", "MM")))
        new = sum(1 for l in st.splitlines() if l.startswith("??"))
        dele = sum(1 for l in st.splitlines() if l.startswith(("D", " D")))
        if not st.strip():
            status.update(ok=True, detail="工作区无改动，空跑")
            print(f"[{ts}] 空跑：无改动")
            return finish(status)

        git("add", "-A")  # 强制全收（.gitignore 已挡数据/日志/快照）

        bloat = bloat_reason()
        if bloat:
            git("reset", "-q")  # 退暂存；工作区文件不动，等人工甄别
            status.update(ok=True, committed=False, detail=f"BLOAT_GUARD: {bloat}")
            print(f"[{ts}] SKIP {status['detail']}")
            return finish(status)

        staged = git("diff", "--cached", "--stat", check=False).stdout.strip().splitlines()
        stat_line = staged[-1].strip() if staged else ""

        r = commit_with_retry(
            f"auto: 各Agent代码定时入库快照 {now.strftime('%m-%d %H:%M')}（改{mod} 新增{new} 删{dele}）"
        )
        if r is None or r.returncode != 0:
            status["detail"] = f"提交失败: {((r.stderr if r else '') or '').strip()[:200]}"
            print(f"[{ts}] FAIL {status['detail']}")
            return finish(status, code=2)

        head = git("log", "-1", "--format=%h %s").stdout.strip()
        status.update(ok=True, committed=True,
                      detail=f"{head} | diffstat: {stat_line}")
        print(f"[{ts}] OK {head}\n  改动: 改{mod} 新增{new} 删{dele} | {stat_line}")
        return finish(status)
    except Exception as e:  # noqa: BLE001
        status["detail"] = f"异常: {e}"
        print(f"[{ts}] ERROR {e}")
        return finish(status, code=2)


def finish(status: dict, code: int = 0) -> int:
    try:
        STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{status['time']} | {'OK' if status['ok'] else 'FAIL'} | {status['detail']}\n")
    except OSError:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
